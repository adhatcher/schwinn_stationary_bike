from __future__ import annotations

import importlib
from io import BytesIO
import textwrap

import pandas as pd
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
    history_file = tmp_path / "Workout_History.csv"
    sample_df = app.load_workout_data([_sample_workout(1, 2, 2026, 0, 30)])
    sample_df.to_csv(history_file, index=False)
    monkeypatch.setattr(app, "HISTORY_FILE", history_file)

    client = app.app.test_client()
    response = client.get("/download-history")

    assert response.status_code == 200
    assert "text/csv" in response.headers["Content-Type"]
    assert "Workout_Date" in response.get_data(as_text=True)


def test_grafana_workouts_api_returns_timeseries_points(monkeypatch, tmp_path) -> None:
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
    response = client.get("/api/grafana/workouts?field=Distance")

    assert response.status_code == 200
    payload = response.get_json()
    assert len(payload) == 2
    assert payload[0]["field"] == "Distance"
    assert payload[0]["time"] == "2026-01-02T00:00:00Z"
    assert payload[0]["value"] == 3.2


def test_grafana_workouts_api_defaults_to_all_fields(monkeypatch, tmp_path) -> None:
    history_file = tmp_path / "Workout_History.csv"
    df = app.load_workout_data([_sample_workout(1, 2, 2026, 0, 30, distance=3.2)])
    df.to_csv(history_file, index=False)
    monkeypatch.setattr(app, "HISTORY_FILE", history_file)

    client = app.app.test_client()
    response = client.get("/api/grafana/workouts")

    assert response.status_code == 200
    payload = response.get_json()
    returned_fields = {row["field"] for row in payload}
    assert returned_fields == set(app.GRAPHABLE_FIELDS)


def test_grafana_workouts_api_accepts_quoted_csv_fields(monkeypatch, tmp_path) -> None:
    history_file = tmp_path / "Workout_History.csv"
    df = app.load_workout_data([_sample_workout(1, 2, 2026, 0, 30, distance=3.2)])
    df.to_csv(history_file, index=False)
    monkeypatch.setattr(app, "HISTORY_FILE", history_file)

    client = app.app.test_client()
    response = client.get("/api/grafana/workouts?fields='Distance','Avg_Speed'")

    assert response.status_code == 200
    payload = response.get_json()
    returned_fields = {row["field"] for row in payload}
    assert returned_fields == {"Distance", "Avg_Speed"}


def test_grafana_workouts_api_rejects_invalid_field(monkeypatch, tmp_path) -> None:
    history_file = tmp_path / "Workout_History.csv"
    app.load_workout_data([_sample_workout(1, 2, 2026, 0, 30)]).to_csv(history_file, index=False)
    monkeypatch.setattr(app, "HISTORY_FILE", history_file)

    client = app.app.test_client()
    response = client.get("/api/grafana/workouts?field=NotAField")

    assert response.status_code == 400
    payload = response.get_json()
    assert "Unsupported field" in payload["error"]
    assert "allowed_fields" in payload


def test_grafana_summary_api_returns_aggregates(monkeypatch, tmp_path) -> None:
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
    response = client.get("/api/grafana/summary?field=Distance&from=2026-01-02&to=2026-01-03")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["workout_count"] == 2
    assert payload["fields"] == ["Distance"]
    assert payload["minimums"]["Distance"] == 4.0
    assert payload["maximums"]["Distance"] == 6.0
    assert payload["averages"]["Distance"] == 5.0


def test_upload_history_csv_merges_into_history(monkeypatch, tmp_path) -> None:
    history_file = tmp_path / "Workout_History.csv"
    monkeypatch.setattr(app, "HISTORY_FILE", history_file)
    monkeypatch.setattr(app, "DATA_DIR", tmp_path)
    monkeypatch.setattr(app, "DAT_FILE", tmp_path / "AARON.DAT")

    csv_text = (
        "Workout_Date,Distance,Avg_Speed,Workout_Time,Total_Calories,Heart_Rate,RPM,Level\n"
        "2026-01-04,3.5,12.2,30,210,128,72,4\n"
    )

    client = app.app.test_client()
    response = client.post(
        "/",
        data={"history_csv_file": (BytesIO(csv_text.encode("utf-8")), "history.csv")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    assert history_file.exists()
    history_df = pd.read_csv(history_file)
    assert len(history_df) == 1
    assert float(history_df.loc[0, "Distance"]) == 3.5


def test_index_post_dat_upload_merges_data(monkeypatch, tmp_path) -> None:
    history_file = tmp_path / "Workout_History.csv"
    monkeypatch.setattr(app, "HISTORY_FILE", history_file)
    monkeypatch.setattr(app, "DATA_DIR", tmp_path)
    monkeypatch.setattr(app, "DAT_FILE", tmp_path / "AARON.DAT")

    client = app.app.test_client()
    response = client.post(
        "/",
        data={"dat_file": (BytesIO(_sample_dat_text().encode("utf-8")), "AARON.DAT")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 200
    assert history_file.exists()
    history_df = pd.read_csv(history_file)
    assert len(history_df) == 1


def test_index_post_uses_disk_dat_when_present(monkeypatch, tmp_path) -> None:
    history_file = tmp_path / "Workout_History.csv"
    dat_file = tmp_path / "AARON.DAT"
    dat_file.write_text(_sample_dat_text(), encoding="utf-8")
    monkeypatch.setattr(app, "HISTORY_FILE", history_file)
    monkeypatch.setattr(app, "DATA_DIR", tmp_path)
    monkeypatch.setattr(app, "DAT_FILE", dat_file)

    client = app.app.test_client()
    response = client.post("/", data={})
    assert response.status_code == 200
    text = response.get_data(as_text=True)
    assert "Loaded AARON.DAT from disk" in text


def test_index_post_shows_no_file_message_when_no_upload_and_no_disk_file(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(app, "HISTORY_FILE", tmp_path / "Workout_History.csv")
    monkeypatch.setattr(app, "DATA_DIR", tmp_path)
    monkeypatch.setattr(app, "DAT_FILE", tmp_path / "AARON.DAT")

    client = app.app.test_client()
    response = client.post("/", data={})
    assert response.status_code == 200
    assert "No upload provided and no DAT file found on disk." in response.get_data(as_text=True)


def test_index_post_shows_parse_error_message_on_bad_dat_upload(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(app, "HISTORY_FILE", tmp_path / "Workout_History.csv")
    monkeypatch.setattr(app, "DATA_DIR", tmp_path)
    monkeypatch.setattr(app, "DAT_FILE", tmp_path / "AARON.DAT")

    client = app.app.test_client()
    response = client.post(
        "/",
        data={"dat_file": (BytesIO("not a valid dat payload".encode("utf-8")), "AARON.DAT")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 200
    assert "Unable to parse DAT file:" in response.get_data(as_text=True)


def test_table_header_filter_ui_renders(monkeypatch, tmp_path) -> None:
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
    monkeypatch.setattr(app, "DATA_DIR", tmp_path)
    monkeypatch.setattr(app, "DAT_FILE", tmp_path / "AARON.DAT")

    client = app.app.test_client()
    response = client.get("/")

    assert response.status_code == 200
    text = response.get_data(as_text=True)
    assert "Workout_Date</code> supports checkbox selection in a dropdown" in text
    assert "dropdown-filter-toggle" in text
    assert "date-checkbox-dropdown" in text
