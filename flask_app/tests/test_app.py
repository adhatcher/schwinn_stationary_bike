from __future__ import annotations

from io import BytesIO

import pandas as pd
from werkzeug.datastructures import FileStorage

import app


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


def test_extract_json_objects_parses_multiple_objects() -> None:
    payload = "noise {\"a\":1} trailing text {\"b\":2}"
    objs = app.extract_json_objects(payload)
    assert objs == [{"a": 1}, {"b": 2}]


def test_parse_dat_payload_ignores_header_lines() -> None:
    header = "\n".join(["header"] * 8)
    body = '{"workoutDate":{"Month":1,"Day":2,"Year":2026},"distance":1,"averageSpeed":2,' \
           '"totalWorkoutTime":{"Hours":0,"Minutes":30},"totalCalories":100,"avgHeartRate":120,' \
           '"avgRpm":70,"avgLevel":3}'
    workouts = app.parse_dat_payload(f"{header}\n{body}")
    assert len(workouts) == 1
    assert workouts[0]["distance"] == 1


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


def test_read_dat_from_upload_parses_file_storage() -> None:
    header = "\n".join(["header"] * 8)
    body = '{"workoutDate":{"Month":1,"Day":2,"Year":2026},"distance":3.2,"averageSpeed":14.2,' \
           '"totalWorkoutTime":{"Hours":0,"Minutes":40},"totalCalories":200,"avgHeartRate":130,' \
           '"avgRpm":75,"avgLevel":5}'
    upload = FileStorage(stream=BytesIO(f"{header}\n{body}".encode("utf-8")), filename="AARON.DAT")
    df = app.read_dat_from_upload(upload)
    assert len(df) == 1
    assert float(df.loc[0, "Distance"]) == 3.2


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
