#!/usr/bin/env python3
from __future__ import annotations

import json
import logging
import os
from io import BytesIO, StringIO
from logging.handlers import RotatingFileHandler
from pathlib import Path
from time import perf_counter
from typing import Iterable

import pandas as pd
import plotly.graph_objects as go
from flask import Flask, Response, g, jsonify, render_template, request
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024  # 5 MB upload cap

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("DATA_DIR", str(BASE_DIR / "data"))).resolve()
DAT_FILE = Path(os.getenv("DAT_FILE", str(DATA_DIR / "AARON.DAT"))).resolve()
HISTORY_FILE = Path(os.getenv("HISTORY_FILE", str(DATA_DIR / "Workout_History.csv"))).resolve()
LOG_DIR = Path(os.getenv("LOG_DIR", str(BASE_DIR / "logs"))).resolve()
LOG_FILE = Path(os.getenv("LOG_FILE", str(LOG_DIR / "app.log"))).resolve()
PORT = int(os.getenv("PORT", "8080"))

COLUMN_NAMES = [
    "Workout_Date",
    "Distance",
    "Avg_Speed",
    "Workout_Time",
    "Total_Calories",
    "Heart_Rate",
    "RPM",
    "Level",
]
GRAPHABLE_FIELDS = [name for name in COLUMN_NAMES if name != "Workout_Date"]

REQUEST_COUNT = Counter(
    "schwinn_http_requests_total",
    "Total HTTP requests",
    ["method", "endpoint", "status_code"],
)
REQUEST_LATENCY = Histogram(
    "schwinn_http_request_duration_seconds",
    "HTTP request latency in seconds",
    ["method", "endpoint"],
)
CHARTS_GENERATED = Counter(
    "schwinn_charts_generated_total",
    "Total charts generated",
)
CHART_GENERATION_LATENCY = Histogram(
    "schwinn_chart_generation_duration_seconds",
    "Time spent generating chart HTML",
)
FILE_IMPORT_LATENCY = Histogram(
    "schwinn_file_import_duration_seconds",
    "Time spent importing DAT files",
    ["source"],
)


def configure_logging() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = RotatingFileHandler(
        LOG_FILE,
        maxBytes=100 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)

    app.logger.setLevel(logging.INFO)
    app.logger.handlers = []
    app.logger.addHandler(file_handler)
    app.logger.addHandler(stream_handler)
    app.logger.propagate = False

    werkzeug_logger = logging.getLogger("werkzeug")
    werkzeug_logger.setLevel(logging.INFO)
    werkzeug_logger.handlers = [file_handler, stream_handler]
    werkzeug_logger.propagate = False


configure_logging()


def extract_json_objects(payload: str) -> list[dict]:
    decoder = json.JSONDecoder()
    workouts: list[dict] = []
    cursor = 0

    while True:
        start = payload.find("{", cursor)
        if start == -1:
            break
        try:
            obj, consumed = decoder.raw_decode(payload[start:])
        except json.JSONDecodeError:
            cursor = start + 1
            continue
        if isinstance(obj, dict):
            workouts.append(obj)
        cursor = start + consumed

    return workouts


def parse_dat_payload(raw_text: str) -> list[dict]:
    payload = "\n".join(raw_text.splitlines()[8:])
    workouts = extract_json_objects(payload)
    if not workouts:
        raise ValueError("No workout objects found in DAT file.")
    return workouts


def load_workout_data(workout_json: Iterable[dict]) -> pd.DataFrame:
    rows = []
    for workout_dict in workout_json:
        workout_date = (
            f"{workout_dict['workoutDate']['Month']}/"
            f"{workout_dict['workoutDate']['Day']}/"
            f"{workout_dict['workoutDate']['Year']}"
        )
        total_minutes = (
            int(workout_dict["totalWorkoutTime"]["Hours"]) * 60
            + int(workout_dict["totalWorkoutTime"]["Minutes"])
        )
        rows.append(
            [
                workout_date,
                workout_dict["distance"],
                workout_dict["averageSpeed"],
                total_minutes,
                workout_dict["totalCalories"],
                workout_dict["avgHeartRate"],
                workout_dict["avgRpm"],
                workout_dict["avgLevel"],
            ]
        )

    df_table = pd.DataFrame(rows, columns=COLUMN_NAMES)
    df_table["Workout_Date"] = pd.to_datetime(df_table["Workout_Date"], errors="coerce")
    return df_table.dropna(subset=["Workout_Date"]).reset_index(drop=True)


def load_history_file(history_file: Path) -> pd.DataFrame:
    if not history_file.exists() or history_file.stat().st_size == 0:
        return pd.DataFrame(columns=COLUMN_NAMES)

    history_df = pd.read_csv(history_file)
    if history_df.empty:
        return pd.DataFrame(columns=COLUMN_NAMES)

    history_df["Workout_Date"] = pd.to_datetime(history_df["Workout_Date"], errors="coerce")
    history_df = history_df.dropna(subset=["Workout_Date"])

    for field in GRAPHABLE_FIELDS:
        if field in history_df.columns:
            history_df[field] = pd.to_numeric(history_df[field], errors="coerce")

    return history_df[COLUMN_NAMES].sort_values(by=["Workout_Date"]).reset_index(drop=True)


def merge_data(new_data: pd.DataFrame, historical_data: pd.DataFrame) -> pd.DataFrame:
    if historical_data.empty:
        return new_data.sort_values(by=["Workout_Date"]).reset_index(drop=True)
    if new_data.empty:
        return historical_data.sort_values(by=["Workout_Date"]).reset_index(drop=True)

    combined_file = pd.concat([historical_data, new_data], ignore_index=True)
    sorted_file = combined_file.sort_values(by=["Workout_Date"]).reset_index(drop=True)
    merged = sorted_file.drop_duplicates(subset=["Workout_Date", "Workout_Time"], keep="last")
    return merged.reset_index(drop=True)


def save_history(data: pd.DataFrame, history_file: Path) -> None:
    history_file.parent.mkdir(parents=True, exist_ok=True)
    data.to_csv(history_file, index=False)


def read_dat_from_disk(dat_file: Path) -> pd.DataFrame:
    raw_text = dat_file.read_text(encoding="utf-8", errors="ignore")
    return load_workout_data(parse_dat_payload(raw_text))


def read_dat_from_upload(file_storage) -> pd.DataFrame:
    raw_bytes = file_storage.read()
    raw_text = raw_bytes.decode("utf-8", errors="ignore")
    return load_workout_data(parse_dat_payload(raw_text))


def read_history_csv_from_upload(file_storage) -> pd.DataFrame:
    upload_df = pd.read_csv(BytesIO(file_storage.read()))
    missing_columns = [col for col in COLUMN_NAMES if col not in upload_df.columns]
    if missing_columns:
        raise ValueError(f"Missing required columns in historical CSV: {', '.join(missing_columns)}")

    history_df = upload_df[COLUMN_NAMES].copy()
    history_df["Workout_Date"] = pd.to_datetime(history_df["Workout_Date"], errors="coerce")
    history_df = history_df.dropna(subset=["Workout_Date"])

    for field in GRAPHABLE_FIELDS:
        history_df[field] = pd.to_numeric(history_df[field], errors="coerce")

    return history_df.sort_values(by=["Workout_Date"]).reset_index(drop=True)


def filter_data(df: pd.DataFrame, start_date: str, end_date: str) -> pd.DataFrame:
    filtered = df.copy()

    if start_date:
        filtered = filtered[filtered["Workout_Date"] >= pd.to_datetime(start_date)]
    if end_date:
        filtered = filtered[filtered["Workout_Date"] <= pd.to_datetime(end_date)]

    return filtered.sort_values(by=["Workout_Date"]).reset_index(drop=True)


def build_chart(df: pd.DataFrame, fields: list[str]) -> str | None:
    if df.empty or not fields:
        return None

    start = perf_counter()
    fig = go.Figure()
    for field in fields:
        fig.add_trace(
            go.Scatter(
                x=df["Workout_Date"],
                y=df[field],
                mode="lines+markers",
                name=field,
            )
        )

    fig.update_layout(
        title="Schwinn Workout Performance",
        xaxis_title="Workout Date",
        yaxis_title="Value",
        height=692,
        hovermode="x unified",
        template="plotly_white",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )

    chart_html = fig.to_html(full_html=False, include_plotlyjs="cdn")
    CHARTS_GENERATED.inc()
    CHART_GENERATION_LATENCY.observe(perf_counter() - start)
    return chart_html


@app.before_request
def start_timer() -> None:
    g.request_start = perf_counter()


@app.after_request
def observe_request(response):
    endpoint = request.endpoint or request.path
    REQUEST_COUNT.labels(request.method, endpoint, str(response.status_code)).inc()
    if hasattr(g, "request_start"):
        REQUEST_LATENCY.labels(request.method, endpoint).observe(perf_counter() - g.request_start)
    return response


@app.route("/healthz", methods=["GET"])
def healthz():
    return jsonify(status="ok")


@app.route("/metrics", methods=["GET"])
def metrics():
    return Response(generate_latest(), mimetype=CONTENT_TYPE_LATEST)


@app.route("/download-history", methods=["GET"])
def download_history():
    history_df = load_history_file(HISTORY_FILE)
    buffer = StringIO()
    history_df.to_csv(buffer, index=False)
    return Response(
        buffer.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=Workout_History.csv"},
    )


@app.route("/", methods=["GET", "POST"])
def index():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    message = ""

    historical_data = load_history_file(HISTORY_FILE)

    if request.method == "POST":
        try:
            history_upload = request.files.get("history_csv_file")
            dat_upload = request.files.get("dat_file")

            if history_upload and history_upload.filename:
                start = perf_counter()
                uploaded_history = read_history_csv_from_upload(history_upload)
                FILE_IMPORT_LATENCY.labels("history_csv").observe(perf_counter() - start)
                historical_data = merge_data(uploaded_history, historical_data)
                save_history(historical_data, HISTORY_FILE)
                message = f"Uploaded {history_upload.filename} and merged {len(uploaded_history)} historical rows."
                app.logger.info("History CSV merged: file=%s rows=%s", history_upload.filename, len(uploaded_history))
            elif dat_upload and dat_upload.filename:
                start = perf_counter()
                new_data = read_dat_from_upload(dat_upload)
                FILE_IMPORT_LATENCY.labels("upload").observe(perf_counter() - start)
                historical_data = merge_data(new_data, historical_data)
                save_history(historical_data, HISTORY_FILE)
                message = f"Uploaded {dat_upload.filename} and merged {len(new_data)} workouts."
                app.logger.info("DAT upload merged: file=%s rows=%s", dat_upload.filename, len(new_data))
            elif DAT_FILE.exists():
                start = perf_counter()
                new_data = read_dat_from_disk(DAT_FILE)
                FILE_IMPORT_LATENCY.labels("disk").observe(perf_counter() - start)
                historical_data = merge_data(new_data, historical_data)
                save_history(historical_data, HISTORY_FILE)
                message = f"Loaded {DAT_FILE.name} from disk and merged {len(new_data)} workouts."
                app.logger.info("Disk import merged: file=%s rows=%s", DAT_FILE, len(new_data))
            else:
                message = "No upload provided and no DAT file found on disk."
                app.logger.warning("Import attempted without DAT file available")
        except Exception as exc:
            message = f"Unable to parse DAT file: {exc}"
            app.logger.exception("DAT parse/import failed")

    start_date = request.values.get("start_date", "")
    end_date = request.values.get("end_date", "")

    selected_fields = [field for field in request.values.getlist("fields") if field in GRAPHABLE_FIELDS]
    if not selected_fields:
        selected_fields = ["Distance", "Avg_Speed", "Workout_Time"]

    filtered_data = filter_data(historical_data, start_date, end_date)
    chart_html = build_chart(filtered_data, selected_fields)

    min_date = ""
    max_date = ""
    if not historical_data.empty:
        min_date = historical_data["Workout_Date"].min().date().isoformat()
        max_date = historical_data["Workout_Date"].max().date().isoformat()

    historical_table_data = historical_data.sort_values(by=["Workout_Date"], ascending=False).reset_index(drop=True)
    table_html = (
        historical_table_data.to_html(classes="table table-striped", index=False)
        if not historical_table_data.empty
        else ""
    )

    return render_template(
        "index.html",
        message=message,
        fields=GRAPHABLE_FIELDS,
        selected_fields=selected_fields,
        start_date=start_date,
        end_date=end_date,
        min_date=min_date,
        max_date=max_date,
        chart_html=chart_html,
        table_html=table_html,
        record_count=len(filtered_data),
        historical_count=len(historical_data),
        history_file=HISTORY_FILE,
        dat_file=DAT_FILE,
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT, debug=True)
