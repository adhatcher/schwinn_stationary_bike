from __future__ import annotations

import importlib
import os
from io import BytesIO
import textwrap
from pathlib import Path

import pandas as pd
from flask import request
from werkzeug.datastructures import FileStorage

app = importlib.import_module("app.app")


def _sample_workout(month: int, day: int, year: int, hours: int, minutes: int, distance: float = 5.5) -> dict:
    return {
        "workoutDate": {"Month": month, "Day": day, "Year": year},
        "distance": distance,
        "averageSpeed": 14.2,
        "totalWorkoutTime": {"Hours": hours, "Minutes": minutes},
        "totalCalories": 250,
        "avgHeartRate": 132,
        "avgRpm": 78,
        "avgLevel": 6,
    }


def _sample_dat_text() -> str:
    header = "\n".join(["header"] * 8)
    body = textwrap.dedent(
        """\
        {"workoutDate":{"Month":1,"Day":2,"Year":2026},"distance":3.2,"averageSpeed":14.2,"totalWorkoutTime":{"Hours":0,"Minutes":40},"totalCalories":200,"avgHeartRate":130,"avgRpm":75,"avgLevel":5}
        """
    ).strip()
    return f"{header}\n{body}"


def _configure_auth(monkeypatch, tmp_path) -> None:
    auth_db = tmp_path / "users.db"
    monkeypatch.setattr(app, "AUTH_DB_FILE", auth_db)
    monkeypatch.setattr(app, "DATA_DIR", tmp_path)
    monkeypatch.setattr(app, "DAT_FILE", tmp_path / "AARON.DAT")
    monkeypatch.setattr(app, "HISTORY_FILE", tmp_path / "Workout_History.csv")
    app.app.secret_key = "test-secret-key"
    app.init_auth_db()


def _create_user(
    email: str = "athlete@example.com",
    password: str = "password123",
    *,
    role: str = "user",
):
    return app.create_user(email, password, role=role)


def _create_admin(email: str = "admin@example.com", password: str = "password123"):
    return _create_user(email, password, role="admin")


def _log_in(client, user=None) -> None:
    auth_user = user or _create_admin()
    with client.session_transaction() as flask_session:
        flask_session[app.USER_SESSION_KEY] = int(auth_user["id"])


class _DummySMTP:
    def __init__(self, *_args, **_kwargs):
        self.started_tls = False
        self.logged_in = None
        self.sent_message = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def starttls(self):
        self.started_tls = True

    def login(self, username, password):
        self.logged_in = (username, password)

    def send_message(self, message):
        self.sent_message = message


def test_extract_json_objects_parses_multiple_objects() -> None:
    payload = "noise {\"a\":1} trailing text {\"b\":2}"
    objs = app.extract_json_objects(payload)
    assert objs == [{"a": 1}, {"b": 2}]


def test_extract_json_objects_skips_malformed_json_prefix() -> None:
    payload = "junk {not-json} and valid {\"ok\":1}"
    objs = app.extract_json_objects(payload)
    assert objs == [{"ok": 1}]


def test_parse_dat_payload_ignores_header_lines() -> None:
    header = "\n".join(["header"] * 8)
    body = '{"workoutDate":{"Month":1,"Day":2,"Year":2026},"distance":1,"averageSpeed":2,' \
           '"totalWorkoutTime":{"Hours":0,"Minutes":30},"totalCalories":100,"avgHeartRate":120,' \
           '"avgRpm":70,"avgLevel":3}'
    workouts = app.parse_dat_payload(f"{header}\n{body}")
    assert len(workouts) == 1
    assert workouts[0]["distance"] == 1


def test_parse_dat_payload_raises_when_no_workouts_found() -> None:
    header_only = "\n".join(["header"] * 12)
    try:
        app.parse_dat_payload(header_only)
    except ValueError as exc:
        assert "No workout objects found" in str(exc)
    else:
        raise AssertionError("Expected ValueError for empty DAT payload")


def test_load_workout_data_calculates_minutes() -> None:
    df = app.load_workout_data([_sample_workout(1, 2, 2026, 1, 15)])
    assert list(df.columns) == app.COLUMN_NAMES
    assert int(df.loc[0, "Workout_Time"]) == 75


def test_merge_data_deduplicates_date_and_workout_time() -> None:
    old = app.load_workout_data([_sample_workout(1, 2, 2026, 0, 30, distance=1.0)])
    new = app.load_workout_data([_sample_workout(1, 2, 2026, 0, 30, distance=2.0)])
    merged = app.merge_data(new, old)
    assert len(merged) == 1
    assert float(merged.loc[0, "Distance"]) == 2.0


def test_merge_data_returns_historical_when_new_is_empty() -> None:
    old = app.load_workout_data([_sample_workout(1, 2, 2026, 0, 30, distance=1.0)])
    new_empty = pd.DataFrame(columns=app.COLUMN_NAMES)
    merged = app.merge_data(new_empty, old)
    assert len(merged) == 1
    assert float(merged.loc[0, "Distance"]) == 1.0


def test_filter_data_respects_inclusive_date_range() -> None:
    df = app.load_workout_data(
        [
            _sample_workout(1, 1, 2026, 0, 20),
            _sample_workout(1, 15, 2026, 0, 20),
            _sample_workout(2, 1, 2026, 0, 20),
        ]
    )
    filtered = app.filter_data(df, "2026-01-15", "2026-02-01")
    assert len(filtered) == 2
    assert filtered["Workout_Date"].min() == pd.Timestamp("2026-01-15")
    assert filtered["Workout_Date"].max() == pd.Timestamp("2026-02-01")


def test_build_chart_returns_html_when_fields_selected() -> None:
    df = app.load_workout_data([_sample_workout(1, 2, 2026, 0, 45)])
    html = app.build_chart(df, ["Distance", "Avg_Speed"])
    assert html is not None
    assert "plotly" in html.lower()


def test_build_chart_returns_none_when_no_data() -> None:
    empty_df = pd.DataFrame(columns=app.COLUMN_NAMES)
    assert app.build_chart(empty_df, ["Distance"]) is None


def test_read_dat_from_upload_parses_file_storage() -> None:
    header = "\n".join(["header"] * 8)
    body = '{"workoutDate":{"Month":1,"Day":2,"Year":2026},"distance":3.2,"averageSpeed":14.2,' \
           '"totalWorkoutTime":{"Hours":0,"Minutes":40},"totalCalories":200,"avgHeartRate":130,' \
           '"avgRpm":75,"avgLevel":5}'
    upload = FileStorage(stream=BytesIO(f"{header}\n{body}".encode("utf-8")), filename="AARON.DAT")
    df = app.read_dat_from_upload(upload)
    assert len(df) == 1
    assert float(df.loc[0, "Distance"]) == 3.2


def test_read_dat_from_disk_parses_file(tmp_path) -> None:
    dat_file = tmp_path / "AARON.DAT"
    dat_file.write_text(_sample_dat_text(), encoding="utf-8")
    df = app.read_dat_from_disk(dat_file)
    assert len(df) == 1
    assert float(df.loc[0, "Distance"]) == 3.2


def test_load_history_file_returns_empty_for_header_only_csv(tmp_path) -> None:
    history_file = tmp_path / "Workout_History.csv"
    history_file.write_text(",".join(app.COLUMN_NAMES) + "\n", encoding="utf-8")
    df = app.load_history_file(history_file)
    assert df.empty


def test_read_history_csv_from_upload_raises_on_missing_columns() -> None:
    csv_text = "Workout_Date,Distance\n2026-01-01,2.0\n"
    upload = FileStorage(stream=BytesIO(csv_text.encode("utf-8")), filename="bad.csv")
    try:
        app.read_history_csv_from_upload(upload)
    except ValueError as exc:
        assert "Missing required columns" in str(exc)
    else:
        raise AssertionError("Expected ValueError for missing history columns")


def test_upload_workout_does_not_expose_exception_details(monkeypatch, tmp_path) -> None:
    def fail_read_dat_from_upload(_upload):
        raise ValueError("secret parse failure details")

    monkeypatch.setattr(app, "read_dat_from_upload", fail_read_dat_from_upload)

    _configure_auth(monkeypatch, tmp_path)
    client = app.app.test_client()
    _log_in(client)
    response = client.post(
        "/upload-workout",
        data={"dat_file": (BytesIO(b"bad dat payload"), "AARON.DAT")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    text = response.get_data(as_text=True)
    assert app.DAT_IMPORT_ERROR_MESSAGE in text
    assert "secret parse failure details" not in text


def test_upload_history_does_not_expose_exception_details(monkeypatch, tmp_path) -> None:
    def fail_read_history_csv_from_upload(_upload):
        raise ValueError("secret csv failure details")

    monkeypatch.setattr(app, "read_history_csv_from_upload", fail_read_history_csv_from_upload)

    _configure_auth(monkeypatch, tmp_path)
    client = app.app.test_client()
    _log_in(client)
    response = client.post(
        "/upload-history",
        data={"history_csv_file": (BytesIO(b"bad,csv"), "history.csv")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    text = response.get_data(as_text=True)
    assert app.HISTORY_IMPORT_ERROR_MESSAGE in text
    assert "secret csv failure details" not in text


def test_healthz_endpoint_returns_ok() -> None:
    client = app.app.test_client()
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.get_json() == {"status": "ok"}


def test_metrics_endpoint_exposes_prometheus_output() -> None:
    client = app.app.test_client()
    client.get("/healthz")
    response = client.get("/metrics")
    assert response.status_code == 200
    text = response.get_data(as_text=True)
    assert "schwinn_http_requests_total" in text
    assert "schwinn_http_request_duration_seconds" in text


def test_download_history_returns_csv(monkeypatch, tmp_path) -> None:
    _configure_auth(monkeypatch, tmp_path)
    history_file = tmp_path / "Workout_History.csv"
    sample_df = app.load_workout_data([_sample_workout(1, 2, 2026, 0, 30)])
    sample_df.to_csv(history_file, index=False)
    monkeypatch.setattr(app, "HISTORY_FILE", history_file)

    client = app.app.test_client()
    _log_in(client)
    response = client.get("/download-history")

    assert response.status_code == 200
    assert "text/csv" in response.headers["Content-Type"]
    assert "Workout_Date" in response.get_data(as_text=True)


def test_welcome_page_shows_days_since_last_workout(monkeypatch, tmp_path) -> None:
    _configure_auth(monkeypatch, tmp_path)
    history_file = tmp_path / "Workout_History.csv"
    df = app.load_workout_data([_sample_workout(3, 10, 2026, 0, 30)])
    df.to_csv(history_file, index=False)
    monkeypatch.setattr(app, "HISTORY_FILE", history_file)
    monkeypatch.setattr(app, "current_day", lambda: pd.Timestamp("2026-03-17"))

    client = app.app.test_client()
    _log_in(client)
    response = client.get("/")

    assert response.status_code == 200
    text = response.get_data(as_text=True)
    assert "Welcome Back!" in text
    assert "It's been 7 days since your last workout." in text


def test_summarize_window_includes_current_day_in_last_30_days() -> None:
    df = app.load_workout_data(
        [
            _sample_workout(2, 15, 2026, 0, 20, distance=1.0),
            _sample_workout(2, 17, 2026, 0, 25, distance=2.0),
            _sample_workout(3, 17, 2026, 0, 30, distance=3.0),
        ]
    )

    summary = app.summarize_window(df, days=30, today=pd.Timestamp("2026-03-17"))

    assert summary["workout_count"] == 2
    assert summary["distance"] == 5.0
    assert summary["workout_time"] == 55


def test_grafana_workouts_api_returns_timeseries_points(monkeypatch, tmp_path) -> None:
    _configure_auth(monkeypatch, tmp_path)
    history_file = tmp_path / "Workout_History.csv"
    df = app.load_workout_data(
        [
            _sample_workout(1, 2, 2026, 0, 30, distance=3.2),
            _sample_workout(1, 3, 2026, 0, 45, distance=4.4),
        ]
    )
    df.to_csv(history_file, index=False)
    monkeypatch.setattr(app, "HISTORY_FILE", history_file)

    client = app.app.test_client()
    _log_in(client)
    response = client.get("/api/grafana/workouts?field=Distance")

    assert response.status_code == 200
    payload = response.get_json()
    assert len(payload) == 2
    assert payload[0]["field"] == "Distance"
    assert payload[0]["time"] == "2026-01-02T00:00:00Z"
    assert payload[0]["value"] == 3.2


def test_grafana_workouts_api_defaults_to_all_fields(monkeypatch, tmp_path) -> None:
    _configure_auth(monkeypatch, tmp_path)
    history_file = tmp_path / "Workout_History.csv"
    df = app.load_workout_data([_sample_workout(1, 2, 2026, 0, 30, distance=3.2)])
    df.to_csv(history_file, index=False)
    monkeypatch.setattr(app, "HISTORY_FILE", history_file)

    client = app.app.test_client()
    _log_in(client)
    response = client.get("/api/grafana/workouts")

    assert response.status_code == 200
    payload = response.get_json()
    returned_fields = {row["field"] for row in payload}
    assert returned_fields == set(app.GRAPHABLE_FIELDS)


def test_grafana_workouts_api_accepts_quoted_csv_fields(monkeypatch, tmp_path) -> None:
    _configure_auth(monkeypatch, tmp_path)
    history_file = tmp_path / "Workout_History.csv"
    df = app.load_workout_data([_sample_workout(1, 2, 2026, 0, 30, distance=3.2)])
    df.to_csv(history_file, index=False)
    monkeypatch.setattr(app, "HISTORY_FILE", history_file)

    client = app.app.test_client()
    _log_in(client)
    response = client.get("/api/grafana/workouts?fields='Distance','Avg_Speed'")

    assert response.status_code == 200
    payload = response.get_json()
    returned_fields = {row["field"] for row in payload}
    assert returned_fields == {"Distance", "Avg_Speed"}


def test_grafana_workouts_api_rejects_invalid_field(monkeypatch, tmp_path) -> None:
    _configure_auth(monkeypatch, tmp_path)
    history_file = tmp_path / "Workout_History.csv"
    app.load_workout_data([_sample_workout(1, 2, 2026, 0, 30)]).to_csv(history_file, index=False)
    monkeypatch.setattr(app, "HISTORY_FILE", history_file)

    client = app.app.test_client()
    _log_in(client)
    response = client.get("/api/grafana/workouts?field=NotAField")

    assert response.status_code == 400
    payload = response.get_json()
    assert "Unsupported field" in payload["error"]
    assert "allowed_fields" in payload


def test_grafana_summary_api_returns_aggregates(monkeypatch, tmp_path) -> None:
    _configure_auth(monkeypatch, tmp_path)
    history_file = tmp_path / "Workout_History.csv"
    df = app.load_workout_data(
        [
            _sample_workout(1, 1, 2026, 0, 30, distance=2.0),
            _sample_workout(1, 2, 2026, 0, 45, distance=4.0),
            _sample_workout(1, 3, 2026, 1, 0, distance=6.0),
        ]
    )
    df.to_csv(history_file, index=False)
    monkeypatch.setattr(app, "HISTORY_FILE", history_file)

    client = app.app.test_client()
    _log_in(client)
    response = client.get("/api/grafana/summary?field=Distance&from=2026-01-02&to=2026-01-03")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["workout_count"] == 2
    assert payload["fields"] == ["Distance"]
    assert payload["minimums"]["Distance"] == 4.0
    assert payload["maximums"]["Distance"] == 6.0
    assert payload["averages"]["Distance"] == 5.0


def test_upload_history_csv_merges_into_history(monkeypatch, tmp_path) -> None:
    _configure_auth(monkeypatch, tmp_path)
    history_file = tmp_path / "Workout_History.csv"

    csv_text = (
        "Workout_Date,Distance,Avg_Speed,Workout_Time,Total_Calories,Heart_Rate,RPM,Level\n"
        "2026-01-04,3.5,12.2,30,210,128,72,4\n"
    )

    client = app.app.test_client()
    _log_in(client)
    response = client.post(
        "/upload-history",
        data={"history_csv_file": (BytesIO(csv_text.encode("utf-8")), "history.csv")},
        content_type="multipart/form-data",
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert history_file.exists()
    history_df = pd.read_csv(history_file)
    assert len(history_df) == 1
    assert float(history_df.loc[0, "Distance"]) == 3.5


def test_upload_workout_post_dat_upload_merges_data(monkeypatch, tmp_path) -> None:
    _configure_auth(monkeypatch, tmp_path)
    history_file = tmp_path / "Workout_History.csv"

    client = app.app.test_client()
    _log_in(client)
    response = client.post(
        "/upload-workout",
        data={"dat_file": (BytesIO(_sample_dat_text().encode("utf-8")), "AARON.DAT")},
        content_type="multipart/form-data",
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert history_file.exists()
    history_df = pd.read_csv(history_file)
    assert len(history_df) == 1


def test_upload_workout_post_uses_disk_dat_when_present(monkeypatch, tmp_path) -> None:
    _configure_auth(monkeypatch, tmp_path)
    history_file = tmp_path / "Workout_History.csv"
    dat_file = tmp_path / "AARON.DAT"
    dat_file.write_text(_sample_dat_text(), encoding="utf-8")
    monkeypatch.setattr(app, "DAT_FILE", dat_file)

    client = app.app.test_client()
    _log_in(client)
    response = client.post("/upload-workout", data={}, follow_redirects=False)
    assert response.status_code == 302
    assert "/workout-performance" in response.headers["Location"]


def test_upload_workout_post_shows_no_file_message_when_no_upload_and_no_disk_file(monkeypatch, tmp_path) -> None:
    _configure_auth(monkeypatch, tmp_path)

    client = app.app.test_client()
    _log_in(client)
    response = client.post("/upload-workout", data={})
    assert response.status_code == 200
    assert "No upload provided and no DAT file found on disk." in response.get_data(as_text=True)


def test_upload_workout_post_shows_parse_error_message_on_bad_dat_upload(monkeypatch, tmp_path) -> None:
    _configure_auth(monkeypatch, tmp_path)

    client = app.app.test_client()
    _log_in(client)
    response = client.post(
        "/upload-workout",
        data={"dat_file": (BytesIO("not a valid dat payload".encode("utf-8")), "AARON.DAT")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 200
    assert app.DAT_IMPORT_ERROR_MESSAGE in response.get_data(as_text=True)


def test_table_header_filter_ui_renders(monkeypatch, tmp_path) -> None:
    _configure_auth(monkeypatch, tmp_path)
    history_file = tmp_path / "Workout_History.csv"
    df = app.load_workout_data(
        [
            _sample_workout(1, 1, 2026, 0, 20),
            _sample_workout(1, 15, 2026, 0, 25),
            _sample_workout(2, 1, 2026, 0, 30),
        ]
    )
    df.to_csv(history_file, index=False)

    monkeypatch.setattr(app, "HISTORY_FILE", history_file)

    client = app.app.test_client()
    _log_in(client)
    response = client.get("/workout-performance")

    assert response.status_code == 200
    text = response.get_data(as_text=True)
    assert "Workout_Date</code> supports checkbox selection in a dropdown" in text
    assert "dropdown-filter-toggle" in text
    assert "date-checkbox-dropdown" in text


def test_workout_performance_defaults_start_date_to_one_year_ago(monkeypatch, tmp_path) -> None:
    _configure_auth(monkeypatch, tmp_path)
    history_file = tmp_path / "Workout_History.csv"
    df = app.load_workout_data([_sample_workout(3, 1, 2026, 0, 30)])
    df.to_csv(history_file, index=False)

    monkeypatch.setattr(app, "HISTORY_FILE", history_file)
    monkeypatch.setattr(app, "current_day", lambda: pd.Timestamp("2026-03-17"))

    client = app.app.test_client()
    _log_in(client)
    response = client.get("/workout-performance")

    assert response.status_code == 200
    assert 'name="start_date" value="2025-03-17"' in response.get_data(as_text=True)


def test_workout_performance_table_respects_selected_date_range(monkeypatch, tmp_path) -> None:
    _configure_auth(monkeypatch, tmp_path)
    history_file = tmp_path / "Workout_History.csv"
    df = app.load_workout_data(
        [
            _sample_workout(1, 1, 2026, 0, 20, distance=1.0),
            _sample_workout(1, 15, 2026, 0, 25, distance=2.0),
            _sample_workout(2, 1, 2026, 0, 30, distance=3.0),
        ]
    )
    df.to_csv(history_file, index=False)

    monkeypatch.setattr(app, "HISTORY_FILE", history_file)

    client = app.app.test_client()
    _log_in(client)
    response = client.get("/workout-performance?start_date=2026-01-15&end_date=2026-01-15")

    assert response.status_code == 200
    text = response.get_data(as_text=True)
    assert "2026-01-15" in text
    assert ">2.0<" in text
    assert ">1.0<" not in text
    assert ">3.0<" not in text
    assert "table-container-fixed" in text


def test_login_required_redirects_to_login(monkeypatch, tmp_path) -> None:
    _configure_auth(monkeypatch, tmp_path)
    _create_admin()
    client = app.app.test_client()
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["Location"].startswith("/login")


def test_api_requires_authentication(monkeypatch, tmp_path) -> None:
    _configure_auth(monkeypatch, tmp_path)
    _create_admin()
    client = app.app.test_client()
    response = client.get("/api/grafana/workouts")
    assert response.status_code == 401
    assert response.get_json() == {"error": "Authentication required."}


def test_register_creates_user_and_logs_in(monkeypatch, tmp_path) -> None:
    _configure_auth(monkeypatch, tmp_path)
    _create_admin()
    app.set_registration_enabled(True)
    client = app.app.test_client()
    response = client.post(
        "/register",
        data={
            "email": "athlete@example.com",
            "password": "password123",
            "confirm_password": "password123",
        },
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/")
    user = app.get_user_by_email("athlete@example.com")
    assert user is not None


def test_login_accepts_registered_user(monkeypatch, tmp_path) -> None:
    _configure_auth(monkeypatch, tmp_path)
    _create_admin()
    _create_user()
    client = app.app.test_client()
    response = client.post(
        "/login",
        data={"email": "athlete@example.com", "password": "password123"},
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/")


def test_auth_events_log_successes_and_failures(monkeypatch, tmp_path) -> None:
    _configure_auth(monkeypatch, tmp_path)
    admin = _create_admin()
    _create_user()
    logged: list[tuple[str, str]] = []

    monkeypatch.setattr(
        app.app.logger,
        "info",
        lambda message, *args: logged.append(("info", message % args)),
    )
    monkeypatch.setattr(
        app.app.logger,
        "warning",
        lambda message, *args: logged.append(("warning", message % args)),
    )
    client = app.app.test_client()

    login_success = client.post(
        "/login",
        data={"email": "athlete@example.com", "password": "password123"},
        follow_redirects=False,
    )
    assert login_success.status_code == 302

    client.get("/logout")

    login_failure = client.post(
        "/login",
        data={"email": "athlete@example.com", "password": "wrong-password"},
        follow_redirects=False,
    )
    assert login_failure.status_code == 200

    with client.session_transaction() as flask_session:
        flask_session[app.USER_SESSION_KEY] = int(admin["id"])

    sent = {}

    def fake_send(email: str, reset_link: str) -> None:
        sent["email"] = email
        sent["reset_link"] = reset_link

    monkeypatch.setattr(app, "send_password_reset_email", fake_send)
    create_response = client.post(
        "/admin/users",
        data={"action": "create_user", "email": "newuser@example.com", "role": "user"},
    )
    assert create_response.status_code == 200
    token = str(sent["reset_link"]).rsplit("/reset-password/", 1)[1]

    reset_response = client.post(
        f"/reset-password/{token}",
        data={"password": "newpassword123", "confirm_password": "newpassword123"},
        follow_redirects=False,
    )
    assert reset_response.status_code == 302

    entries = "\n".join(message for _level, message in logged)
    athlete_id = app.email_audit_id("athlete@example.com")
    admin_id = app.email_audit_id("admin@example.com")
    new_user_id = app.email_audit_id("newuser@example.com")
    assert f"action=login result=success subject_id={athlete_id}" in entries
    assert f"action=login result=failure subject_id={athlete_id}" in entries
    assert f"action=logout result=success subject_id={athlete_id}" in entries
    assert f"action=user_create result=success subject_id={new_user_id} actor_id={admin_id}" in entries
    assert f"action=password_reset_email result=success subject_id={new_user_id} actor_id={admin_id}" in entries
    assert f"action=password_reset result=success subject_id={new_user_id}" in entries


def test_mask_email_redacts_sensitive_values() -> None:
    assert app.mask_email("athlete@example.com") == "a***e@example.com"
    assert app.mask_email("ab@example.com") == "a*@example.com"
    assert app.mask_email("a@example.com") == "*@example.com"
    assert app.mask_email("") == ""


def test_email_audit_id_is_stable_and_non_empty() -> None:
    assert app.email_audit_id("Athlete@example.com") == app.email_audit_id(" athlete@example.com ")
    assert app.email_audit_id("athlete@example.com")
    assert app.email_audit_id("") == ""


def test_forgot_password_sends_reset_email(monkeypatch, tmp_path) -> None:
    _configure_auth(monkeypatch, tmp_path)
    _create_admin()
    _create_user()
    sent = {}

    def fake_send(email: str, reset_link: str) -> None:
        sent["email"] = email
        sent["reset_link"] = reset_link

    monkeypatch.setattr(app, "send_password_reset_email", fake_send)
    client = app.app.test_client()
    response = client.post("/forgot-password", data={"email": "athlete@example.com"})
    text = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "password reset link has been sent" in text
    assert sent["email"] == "athlete@example.com"
    assert "/reset-password/" in sent["reset_link"]


def test_auth_metrics_track_login_and_reset_events(monkeypatch, tmp_path) -> None:
    _configure_auth(monkeypatch, tmp_path)
    admin = _create_admin()
    _create_user()
    sent = {}

    def fake_send(email: str, reset_link: str) -> None:
        sent["email"] = email
        sent["reset_link"] = reset_link

    monkeypatch.setattr(app, "send_password_reset_email", fake_send)
    client = app.app.test_client()

    client.post("/login", data={"email": "athlete@example.com", "password": "password123"})
    client.get("/logout")
    client.post("/login", data={"email": "athlete@example.com", "password": "wrong-password"})

    with client.session_transaction() as flask_session:
        flask_session[app.USER_SESSION_KEY] = int(admin["id"])

    client.post("/admin/users", data={"action": "create_user", "email": "newuser@example.com", "role": "user"})
    token = str(sent["reset_link"]).rsplit("/reset-password/", 1)[1]
    client.post(
        f"/reset-password/{token}",
        data={"password": "newpassword123", "confirm_password": "newpassword123"},
    )

    metrics_response = client.get("/metrics")
    metrics_text = metrics_response.get_data(as_text=True)

    assert 'schwinn_auth_events_total{action="login",result="success"}' in metrics_text
    assert 'schwinn_auth_events_total{action="login",result="failure"}' in metrics_text
    assert 'schwinn_auth_events_total{action="logout",result="success"}' in metrics_text
    assert 'schwinn_auth_events_total{action="user_create",result="success"}' in metrics_text
    assert 'schwinn_auth_events_total{action="password_reset_email",result="success"}' in metrics_text
    assert 'schwinn_auth_events_total{action="password_reset",result="success"}' in metrics_text


def test_reset_password_updates_stored_password(monkeypatch, tmp_path) -> None:
    _configure_auth(monkeypatch, tmp_path)
    _create_admin()
    user = _create_user()
    token = app.generate_password_reset_token(str(user["email"]))
    client = app.app.test_client()
    response = client.post(
        f"/reset-password/{token}",
        data={"password": "newpassword123", "confirm_password": "newpassword123"},
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]
    assert "email=athlete@example.com" in response.headers["Location"]
    updated = app.get_user_by_email("athlete@example.com")
    assert updated is not None
    assert app.check_password_hash(str(updated["password_hash"]), "newpassword123")


def test_admin_created_user_can_reset_password_and_log_in(monkeypatch, tmp_path) -> None:
    _configure_auth(monkeypatch, tmp_path)
    admin = _create_admin()
    sent = {}

    def fake_send(email: str, reset_link: str) -> None:
        sent["email"] = email
        sent["reset_link"] = reset_link

    monkeypatch.setattr(app, "send_password_reset_email", fake_send)
    client = app.app.test_client()
    _log_in(client, admin)

    create_response = client.post(
        "/admin/users",
        data={"action": "create_user", "email": "newuser@example.com", "role": "user"},
    )

    assert create_response.status_code == 200
    assert sent["email"] == "newuser@example.com"
    token = str(sent["reset_link"]).rsplit("/reset-password/", 1)[1]

    reset_response = client.post(
        f"/reset-password/{token}",
        data={"password": "newpassword123", "confirm_password": "newpassword123"},
        follow_redirects=False,
    )

    assert reset_response.status_code == 302
    assert "/login" in reset_response.headers["Location"]
    with client.session_transaction() as flask_session:
        assert app.USER_SESSION_KEY not in flask_session

    login_response = client.post(
        "/login",
        data={"email": "newuser@example.com", "password": "newpassword123"},
        follow_redirects=False,
    )

    assert login_response.status_code == 302
    assert login_response.headers["Location"].endswith("/")


def test_login_prefills_email_from_query_string(monkeypatch, tmp_path) -> None:
    _configure_auth(monkeypatch, tmp_path)
    _create_admin()
    client = app.app.test_client()

    response = client.get("/login?email=newuser@example.com")

    text = response.get_data(as_text=True)
    assert response.status_code == 200
    assert 'value="newuser@example.com"' in text


def test_invalid_reset_token_shows_error(monkeypatch, tmp_path) -> None:
    _configure_auth(monkeypatch, tmp_path)
    _create_admin()
    client = app.app.test_client()
    response = client.get("/reset-password/bad-token")
    assert response.status_code == 200
    assert "invalid or has expired" in response.get_data(as_text=True)


def test_load_dotenv_file_loads_quoted_values_and_skips_existing(monkeypatch, tmp_path) -> None:
    dotenv_file = tmp_path / ".env"
    dotenv_file.write_text(
        "\n".join(
            [
                "# comment",
                "KEEP_ME=from-file",
                'QUOTED="quoted value"',
                "SPACED = spaced-value",
                "INVALID_LINE",
                "",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("KEEP_ME", "already-set")
    monkeypatch.delenv("QUOTED", raising=False)
    monkeypatch.delenv("SPACED", raising=False)

    app.load_dotenv_file(dotenv_file)

    assert os.environ["KEEP_ME"] == "already-set"
    assert os.environ["QUOTED"] == "quoted value"
    assert os.environ["SPACED"] == "spaced-value"


def test_env_first_returns_default_when_no_values_set(monkeypatch) -> None:
    monkeypatch.delenv("FIRST_OPTION", raising=False)
    monkeypatch.delenv("SECOND_OPTION", raising=False)
    assert app.env_first("FIRST_OPTION", "SECOND_OPTION", default="fallback") == "fallback"


def test_env_first_returns_first_non_blank_value(monkeypatch) -> None:
    monkeypatch.setenv("FIRST_OPTION", "   ")
    monkeypatch.setenv("SECOND_OPTION", " chosen ")
    assert app.env_first("FIRST_OPTION", "SECOND_OPTION", default="fallback") == "chosen"


def test_get_user_helpers_return_none_for_missing_inputs(monkeypatch, tmp_path) -> None:
    _configure_auth(monkeypatch, tmp_path)
    assert app.get_user_by_email("") is None
    assert app.get_user_by_id(None) is None


def test_create_user_raises_when_created_user_cannot_be_loaded(monkeypatch, tmp_path) -> None:
    _configure_auth(monkeypatch, tmp_path)
    original_get_user_by_id = app.get_user_by_id
    monkeypatch.setattr(app, "get_user_by_id", lambda _user_id: None)
    try:
        try:
            app.create_user("missing@example.com", "password123")
        except RuntimeError as exc:
            assert "could not be loaded" in str(exc)
        else:
            raise AssertionError("Expected RuntimeError when created user cannot be reloaded")
    finally:
        monkeypatch.setattr(app, "get_user_by_id", original_get_user_by_id)


def test_logout_current_user_clears_session(monkeypatch, tmp_path) -> None:
    _configure_auth(monkeypatch, tmp_path)
    client = app.app.test_client()
    _log_in(client)
    with client.session_transaction() as flask_session:
        assert app.USER_SESSION_KEY in flask_session
    client.get("/logout")
    with client.session_transaction() as flask_session:
        assert app.USER_SESSION_KEY not in flask_session


def test_password_is_valid_rejects_short_passwords() -> None:
    assert app.password_is_valid("short") is False


def test_send_password_reset_email_logs_when_mail_server_not_configured(monkeypatch) -> None:
    logged = {}
    monkeypatch.setattr(app, "MAIL_SERVER", "")
    monkeypatch.setattr(
        app.app.logger,
        "info",
        lambda message, recipient_id: logged.update({"message": message, "recipient_id": recipient_id}),
    )
    app.send_password_reset_email("athlete@example.com", "https://example.com/reset")
    assert "MAIL_SERVER is not configured" in logged["message"]
    assert logged["recipient_id"] == app.email_audit_id("athlete@example.com")


def test_send_password_reset_email_uses_tls_and_login(monkeypatch) -> None:
    smtp = _DummySMTP()
    monkeypatch.setattr(app, "MAIL_SERVER", "smtp.example.com")
    monkeypatch.setattr(app, "MAIL_PORT", 587)
    monkeypatch.setattr(app, "MAIL_USERNAME", "user@example.com")
    monkeypatch.setattr(app, "MAIL_PASSWORD", "secret")
    monkeypatch.setattr(app, "MAIL_USE_TLS", True)
    monkeypatch.setattr(app, "MAIL_USE_SSL", False)
    monkeypatch.setattr(app.smtplib, "SMTP", lambda *args, **kwargs: smtp)

    app.send_password_reset_email("athlete@example.com", "https://example.com/reset")

    assert smtp.started_tls is True
    assert smtp.logged_in == ("user@example.com", "secret")
    assert smtp.sent_message["To"] == "athlete@example.com"


def test_send_password_reset_email_uses_ssl_without_starttls(monkeypatch) -> None:
    smtp = _DummySMTP()
    monkeypatch.setattr(app, "MAIL_SERVER", "smtp.example.com")
    monkeypatch.setattr(app, "MAIL_PORT", 465)
    monkeypatch.setattr(app, "MAIL_USERNAME", "")
    monkeypatch.setattr(app, "MAIL_USE_TLS", True)
    monkeypatch.setattr(app, "MAIL_USE_SSL", True)
    monkeypatch.setattr(app.smtplib, "SMTP_SSL", lambda *args, **kwargs: smtp)

    app.send_password_reset_email("athlete@example.com", "https://example.com/reset")

    assert smtp.started_tls is False
    assert smtp.logged_in is None
    assert smtp.sent_message["To"] == "athlete@example.com"


def test_build_reset_link_uses_public_base_url(monkeypatch) -> None:
    monkeypatch.setattr(app, "PUBLIC_BASE_URL", "https://schwinn.example.com")
    with app.app.test_request_context():
        assert app.build_reset_link("token-123") == "https://schwinn.example.com/reset-password/token-123"


def test_summarize_window_defaults_today_when_not_provided(monkeypatch) -> None:
    monkeypatch.setattr(app, "current_day", lambda: pd.Timestamp("2026-03-17"))
    df = app.load_workout_data([_sample_workout(3, 17, 2026, 0, 30, distance=4.2)])
    summary = app.summarize_window(df, days=1)
    assert summary["workout_count"] == 1
    assert summary["distance"] == 4.2


def test_format_minutes_formats_hour_and_minutes_variants() -> None:
    assert app.format_minutes(60) == "1h"
    assert app.format_minutes(45) == "45m"


def test_parse_field_selection_accepts_field_array_variant() -> None:
    with app.app.test_request_context("/?field[]=Distance&field[]=Avg_Speed"):
        assert app.parse_field_selection(request.args) == ["Distance", "Avg_Speed"]


def test_login_redirects_authenticated_user(monkeypatch, tmp_path) -> None:
    _configure_auth(monkeypatch, tmp_path)
    client = app.app.test_client()
    _log_in(client)
    response = client.get("/login", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/")


def test_login_rejects_invalid_credentials(monkeypatch, tmp_path) -> None:
    _configure_auth(monkeypatch, tmp_path)
    _create_admin()
    _create_user()
    client = app.app.test_client()
    response = client.post("/login", data={"email": "athlete@example.com", "password": "wrong-password"})
    assert response.status_code == 200
    assert "We couldn&#39;t sign you in with that email and password." in response.get_data(as_text=True)


def test_login_redirects_to_next_path_when_safe(monkeypatch, tmp_path) -> None:
    _configure_auth(monkeypatch, tmp_path)
    _create_admin()
    _create_user()
    client = app.app.test_client()
    response = client.post(
        "/login",
        data={"email": "athlete@example.com", "password": "password123", "next": "/workout-performance"},
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/workout-performance")


def test_login_ignores_unsafe_next_url(monkeypatch, tmp_path) -> None:
    _configure_auth(monkeypatch, tmp_path)
    _create_admin()
    _create_user()
    client = app.app.test_client()
    response = client.post(
        "/login",
        data={"email": "athlete@example.com", "password": "password123", "next": "https://evil.example"},
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/")


def test_login_ignores_unsafe_next_url_with_protocol_relative_target(monkeypatch, tmp_path) -> None:
    _configure_auth(monkeypatch, tmp_path)
    _create_admin()
    _create_user()
    client = app.app.test_client()
    response = client.post(
        "/login",
        data={"email": "athlete@example.com", "password": "password123", "next": "//evil.example"},
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/")


def test_login_ignores_unsafe_next_url_with_backslash(monkeypatch, tmp_path) -> None:
    _configure_auth(monkeypatch, tmp_path)
    _create_admin()
    _create_user()
    client = app.app.test_client()
    response = client.post(
        "/login",
        data={"email": "athlete@example.com", "password": "password123", "next": "/\\evil"},
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/")


def test_register_redirects_authenticated_user(monkeypatch, tmp_path) -> None:
    _configure_auth(monkeypatch, tmp_path)
    client = app.app.test_client()
    _log_in(client)
    response = client.get("/register", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/")


def test_register_validation_messages(monkeypatch, tmp_path) -> None:
    _configure_auth(monkeypatch, tmp_path)
    _create_admin()
    app.set_registration_enabled(True)
    client = app.app.test_client()
    cases = [
        ({"email": "", "password": "password123", "confirm_password": "password123"}, "Enter an email address"),
        ({"email": "bad-email", "password": "password123", "confirm_password": "password123"}, "Enter a valid email"),
        ({"email": "athlete@example.com", "password": "short", "confirm_password": "short"}, "at least 8 characters"),
        ({"email": "athlete@example.com", "password": "password123", "confirm_password": "different"}, "Passwords did not match"),
    ]
    for payload, message in cases:
        response = client.post("/register", data=payload)
        assert response.status_code == 200
        assert message in response.get_data(as_text=True)


def test_register_rejects_duplicate_email(monkeypatch, tmp_path) -> None:
    _configure_auth(monkeypatch, tmp_path)
    _create_admin()
    app.set_registration_enabled(True)
    _create_user()
    client = app.app.test_client()
    response = client.post(
        "/register",
        data={"email": "athlete@example.com", "password": "password123", "confirm_password": "password123"},
    )
    assert response.status_code == 200
    assert "already registered" in response.get_data(as_text=True)


def test_forgot_password_get_renders_page(monkeypatch, tmp_path) -> None:
    _configure_auth(monkeypatch, tmp_path)
    _create_admin()
    client = app.app.test_client()
    response = client.get("/forgot-password")
    assert response.status_code == 200
    assert "Recover Password" in response.get_data(as_text=True)


def test_forgot_password_handles_email_delivery_failure(monkeypatch, tmp_path) -> None:
    _configure_auth(monkeypatch, tmp_path)
    _create_admin()
    _create_user()
    monkeypatch.setattr(app, "send_password_reset_email", lambda _email, _link: (_ for _ in ()).throw(RuntimeError("smtp down")))
    client = app.app.test_client()
    response = client.post("/forgot-password", data={"email": "athlete@example.com"})
    assert response.status_code == 200
    assert "We couldn&#39;t send the password reset email right now. Please try again." in response.get_data(as_text=True)


def test_reset_password_get_renders_valid_form(monkeypatch, tmp_path) -> None:
    _configure_auth(monkeypatch, tmp_path)
    _create_admin()
    user = _create_user()
    token = app.generate_password_reset_token(str(user["email"]))
    client = app.app.test_client()
    response = client.get(f"/reset-password/{token}")
    assert response.status_code == 200
    assert "Set a new password" in response.get_data(as_text=True)


def test_reset_password_rejects_short_password(monkeypatch, tmp_path) -> None:
    _configure_auth(monkeypatch, tmp_path)
    _create_admin()
    user = _create_user()
    token = app.generate_password_reset_token(str(user["email"]))
    client = app.app.test_client()
    response = client.post(f"/reset-password/{token}", data={"password": "short", "confirm_password": "short"})
    assert response.status_code == 200
    assert "at least 8 characters" in response.get_data(as_text=True)


def test_reset_password_rejects_mismatch(monkeypatch, tmp_path) -> None:
    _configure_auth(monkeypatch, tmp_path)
    _create_admin()
    user = _create_user()
    token = app.generate_password_reset_token(str(user["email"]))
    client = app.app.test_client()
    response = client.post(
        f"/reset-password/{token}",
        data={"password": "newpassword123", "confirm_password": "different"},
    )
    assert response.status_code == 200
    assert "Passwords did not match" in response.get_data(as_text=True)


def test_grafana_workouts_skips_nan_values(monkeypatch, tmp_path) -> None:
    _configure_auth(monkeypatch, tmp_path)
    history_file = tmp_path / "Workout_History.csv"
    history_file.write_text(
        "Workout_Date,Distance,Avg_Speed,Workout_Time,Total_Calories,Heart_Rate,RPM,Level\n"
        "2026-01-02,,14.2,30,250,132,78,6\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(app, "HISTORY_FILE", history_file)
    client = app.app.test_client()
    _log_in(client)
    response = client.get("/api/grafana/workouts?field=Distance&field=Avg_Speed")
    payload = response.get_json()
    assert response.status_code == 200
    assert len(payload) == 1
    assert payload[0]["field"] == "Avg_Speed"


def test_grafana_summary_rejects_invalid_field(monkeypatch, tmp_path) -> None:
    _configure_auth(monkeypatch, tmp_path)
    client = app.app.test_client()
    _log_in(client)
    response = client.get("/api/grafana/summary?field=Nope")
    assert response.status_code == 400
    assert "Unsupported field" in response.get_json()["error"]


def test_grafana_summary_returns_none_for_empty_numeric_series(monkeypatch, tmp_path) -> None:
    _configure_auth(monkeypatch, tmp_path)
    history_file = tmp_path / "Workout_History.csv"
    history_file.write_text(
        "Workout_Date,Distance,Avg_Speed,Workout_Time,Total_Calories,Heart_Rate,RPM,Level\n"
        "2026-01-02,abc,14.2,30,250,132,78,6\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(app, "HISTORY_FILE", history_file)
    client = app.app.test_client()
    _log_in(client)
    response = client.get("/api/grafana/summary?field=Distance")
    payload = response.get_json()
    assert response.status_code == 200
    assert payload["averages"]["Distance"] is None
    assert payload["minimums"]["Distance"] is None
    assert payload["maximums"]["Distance"] is None


def test_upload_history_requires_file_selection(monkeypatch, tmp_path) -> None:
    _configure_auth(monkeypatch, tmp_path)
    client = app.app.test_client()
    _log_in(client)
    response = client.post("/upload-history", data={})
    assert response.status_code == 200
    assert "Please choose a historical CSV file to import." in response.get_data(as_text=True)


def test_upload_history_get_renders_form(monkeypatch, tmp_path) -> None:
    _configure_auth(monkeypatch, tmp_path)
    client = app.app.test_client()
    _log_in(client)
    response = client.get("/upload-history")
    assert response.status_code == 200
    assert "Load Historical Data" in response.get_data(as_text=True)


def test_bootstrap_redirects_to_setup_admin_when_no_admin_exists(monkeypatch, tmp_path) -> None:
    _configure_auth(monkeypatch, tmp_path)
    client = app.app.test_client()
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/setup-admin")


def test_setup_admin_creates_first_admin_and_disables_registration(monkeypatch, tmp_path) -> None:
    _configure_auth(monkeypatch, tmp_path)
    client = app.app.test_client()
    response = client.post(
        "/setup-admin",
        data={
            "email": "owner@example.com",
            "password": "password123",
            "confirm_password": "password123",
        },
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/admin?message=Admin+account+created.")
    user = app.get_user_by_email("owner@example.com")
    assert user is not None
    assert user["role"] == "admin"
    assert app.is_registration_enabled() is False


def test_setup_admin_redirects_to_login_once_admin_exists(monkeypatch, tmp_path) -> None:
    _configure_auth(monkeypatch, tmp_path)
    _create_admin()
    client = app.app.test_client()
    response = client.get("/setup-admin", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/login")


def test_register_is_disabled_by_default(monkeypatch, tmp_path) -> None:
    _configure_auth(monkeypatch, tmp_path)
    _create_admin()
    client = app.app.test_client()
    response = client.get("/register")
    assert response.status_code == 403
    assert "registration is currently disabled" in response.get_data(as_text=True)


def test_admin_dashboard_updates_registration_setting(monkeypatch, tmp_path) -> None:
    _configure_auth(monkeypatch, tmp_path)
    client = app.app.test_client()
    _log_in(client, _create_admin())
    response = client.post("/admin", data={"registration_enabled": "true"})
    assert response.status_code == 200
    assert app.is_registration_enabled() is True
    assert "Registration settings updated." in response.get_data(as_text=True)


def test_non_admin_cannot_access_admin_pages(monkeypatch, tmp_path) -> None:
    _configure_auth(monkeypatch, tmp_path)
    _create_admin()
    user = _create_user("user@example.com")
    client = app.app.test_client()
    _log_in(client, user)
    response = client.get("/admin", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/")


def test_admin_can_create_user_and_send_setup_email(monkeypatch, tmp_path) -> None:
    _configure_auth(monkeypatch, tmp_path)
    sent = {}

    def fake_send(email: str, reset_link: str) -> None:
        sent["email"] = email
        sent["reset_link"] = reset_link

    monkeypatch.setattr(app, "send_password_reset_email", fake_send)
    client = app.app.test_client()
    _log_in(client, _create_admin())
    response = client.post(
        "/admin/users",
        data={"action": "create_user", "email": "newuser@example.com", "role": "user"},
    )
    assert response.status_code == 200
    created = app.get_user_by_email("newuser@example.com")
    assert created is not None
    assert created["role"] == "user"
    assert sent["email"] == "newuser@example.com"
    assert "password setup email" in response.get_data(as_text=True)


def test_admin_can_update_user_role(monkeypatch, tmp_path) -> None:
    _configure_auth(monkeypatch, tmp_path)
    admin_user = _create_admin()
    managed_user = _create_user("member@example.com")
    client = app.app.test_client()
    _log_in(client, admin_user)
    response = client.post(
        "/admin/users",
        data={"action": "update_role", "user_id": str(managed_user["id"]), "role": "admin"},
    )
    assert response.status_code == 200
    updated = app.get_user_by_email("member@example.com")
    assert updated is not None
    assert updated["role"] == "admin"


def test_admin_can_delete_user(monkeypatch, tmp_path) -> None:
    _configure_auth(monkeypatch, tmp_path)
    admin_user = _create_admin()
    managed_user = _create_user("member@example.com")
    client = app.app.test_client()
    _log_in(client, admin_user)
    response = client.post("/admin/users", data={"action": "delete_user", "user_id": str(managed_user["id"])})
    assert response.status_code == 200
    assert app.get_user_by_email("member@example.com") is None


def test_last_admin_cannot_be_deleted(monkeypatch, tmp_path) -> None:
    _configure_auth(monkeypatch, tmp_path)
    admin_user = _create_admin()
    client = app.app.test_client()
    _log_in(client, admin_user)
    response = client.post("/admin/users", data={"action": "delete_user", "user_id": str(admin_user["id"])})
    assert response.status_code == 200
    assert "last admin account" in response.get_data(as_text=True)
    assert app.get_user_by_email("admin@example.com") is not None


def test_last_admin_cannot_be_demoted(monkeypatch, tmp_path) -> None:
    _configure_auth(monkeypatch, tmp_path)
    admin_user = _create_admin()
    client = app.app.test_client()
    _log_in(client, admin_user)
    response = client.post(
        "/admin/users",
        data={"action": "update_role", "user_id": str(admin_user["id"]), "role": "user"},
    )
    assert response.status_code == 200
    assert "last admin account" in response.get_data(as_text=True)
    assert app.get_user_by_email("admin@example.com")["role"] == "admin"


def test_admin_can_send_reset_email_for_existing_user(monkeypatch, tmp_path) -> None:
    _configure_auth(monkeypatch, tmp_path)
    admin_user = _create_admin()
    managed_user = _create_user("member@example.com")
    sent = {}

    def fake_send(email: str, reset_link: str) -> None:
        sent["email"] = email
        sent["reset_link"] = reset_link

    monkeypatch.setattr(app, "send_password_reset_email", fake_send)
    client = app.app.test_client()
    _log_in(client, admin_user)
    response = client.post("/admin/users", data={"action": "send_reset", "user_id": str(managed_user["id"])})
    assert response.status_code == 200
    assert sent["email"] == "member@example.com"
    assert "/reset-password/" in sent["reset_link"]
