"""Workout page and import routes."""

from __future__ import annotations

from io import StringIO
from time import perf_counter

import pandas as pd
from fastapi import APIRouter, File, Request, Response, UploadFile
from fastapi.responses import HTMLResponse

from app import config
from app.bootstrap.metrics import FILE_IMPORT_LATENCY
from app.state import get_logger
from app.web import redirect_to, render, route_url
from app.workouts.analytics import (
    build_page_context,
    build_workout_details_context,
    filter_data,
    normalize_workout_detail_period,
    parse_graph_selection,
)
from app.workouts.charts import build_chart
from app.workouts.io import (
    load_history_file,
    merge_data,
    read_dat_from_disk,
    read_dat_from_upload,
    read_history_csv_from_upload,
    save_history,
)

router = APIRouter()


@router.get("/download-history", name="download_history")
def download_history():
    """Return workout history as a CSV download."""
    history_df = load_history_file(config.HISTORY_FILE)
    buffer = StringIO()
    history_df.to_csv(buffer, index=False)
    return Response(
        buffer.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=Workout_History.csv"},
    )


@router.get("/", response_class=HTMLResponse, name="welcome")
def welcome(request: Request):
    """Render the welcome dashboard."""
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    historical_data = load_history_file(config.HISTORY_FILE)
    return render(request, "welcome.html", build_page_context(historical_data))


@router.get("/workout-performance", response_class=HTMLResponse, name="workout_performance")
def workout_performance(request: Request):
    """Render workout charts and history table."""
    from app.workouts.analytics import current_day

    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    message = request.query_params.get("message", "")
    historical_data = load_history_file(config.HISTORY_FILE)
    default_start_date = (current_day() - pd.DateOffset(years=1)).date().isoformat()
    start_date = request.query_params.get("start_date", default_start_date)
    end_date = request.query_params.get("end_date", "")

    selected_fields = [field for field in request.query_params.getlist("fields") if field in config.GRAPHABLE_FIELDS]
    if not selected_fields:
        selected_fields = ["Distance", "Avg_Speed", "Workout_Time"]

    filtered_data = filter_data(historical_data, start_date, end_date)
    chart_html = build_chart(filtered_data, selected_fields)
    historical_table_data = filtered_data.sort_values(by=["Workout_Date"], ascending=False).reset_index(drop=True)
    table_html = historical_table_data.to_html(classes="table table-striped", index=False) if not historical_table_data.empty else ""

    return render(
        request,
        "workout_performance.html",
        {
            "message": message,
            "fields": config.GRAPHABLE_FIELDS,
            "selected_fields": selected_fields,
            "start_date": start_date,
            "end_date": end_date,
            "chart_html": chart_html,
            "table_html": table_html,
            "record_count": len(filtered_data),
            **build_page_context(historical_data),
        },
    )


@router.get("/workout-details", response_class=HTMLResponse, name="workout_details")
def workout_details(request: Request):
    """Render responsive Workout Details stats charts."""
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    historical_data = load_history_file(config.HISTORY_FILE)
    selected_period = normalize_workout_detail_period(request.query_params.get("period", ""))
    selected_graphs = parse_graph_selection(request.query_params)
    return render(
        request,
        "workout_details.html",
        {
            **build_workout_details_context(historical_data, selected_period, selected_graphs),
            **build_page_context(historical_data),
        },
    )


@router.get("/upload-workout", response_class=HTMLResponse, name="upload_workout")
def upload_workout_get(request: Request):
    """Render the DAT workout import form."""
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    historical_data = load_history_file(config.HISTORY_FILE)
    return render(request, "upload_workout.html", {"message": "", **build_page_context(historical_data)})


@router.post("/upload-workout", response_class=HTMLResponse, name="upload_workout_post")
async def upload_workout_post(request: Request, dat_file: UploadFile | None = File(None)):
    """Import workouts from an uploaded or disk DAT file."""
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    message = ""
    historical_data = load_history_file(config.HISTORY_FILE)
    try:
        if dat_file and dat_file.filename:
            start = perf_counter()
            new_data = await read_dat_from_upload(dat_file)
            FILE_IMPORT_LATENCY.labels("upload").observe(perf_counter() - start)
            historical_data = merge_data(new_data, historical_data)
            save_history(historical_data, config.HISTORY_FILE)
            get_logger().info("DAT upload merged: file=%s rows=%s", dat_file.filename, len(new_data))
            return redirect_to(route_url(request, "workout_performance", message=f"Uploaded {dat_file.filename} and merged {len(new_data)} workouts."))
        if config.DAT_FILE.exists():
            start = perf_counter()
            new_data = read_dat_from_disk(config.DAT_FILE)
            FILE_IMPORT_LATENCY.labels("disk").observe(perf_counter() - start)
            historical_data = merge_data(new_data, historical_data)
            save_history(historical_data, config.HISTORY_FILE)
            get_logger().info("Disk import merged: file=%s rows=%s", config.DAT_FILE, len(new_data))
            return redirect_to(route_url(request, "workout_performance", message=f"Loaded {config.DAT_FILE.name} from disk and merged {len(new_data)} workouts."))
        message = "No upload provided and no DAT file found on disk."
        get_logger().warning("Workout import attempted without DAT file available")
    except Exception:
        message = config.DAT_IMPORT_ERROR_MESSAGE
        get_logger().warning("DAT parse/import failed")
    historical_data = load_history_file(config.HISTORY_FILE)
    return render(request, "upload_workout.html", {"message": message, **build_page_context(historical_data)})


@router.get("/upload-history", response_class=HTMLResponse, name="upload_history")
def upload_history_get(request: Request):
    """Render the historical CSV import form."""
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    historical_data = load_history_file(config.HISTORY_FILE)
    return render(request, "upload_history.html", {"message": "", **build_page_context(historical_data)})


@router.post("/upload-history", response_class=HTMLResponse, name="upload_history_post")
async def upload_history_post(request: Request, history_csv_file: UploadFile | None = File(None)):
    """Import workouts from an uploaded history CSV."""
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    message = ""
    historical_data = load_history_file(config.HISTORY_FILE)
    try:
        if not history_csv_file or not history_csv_file.filename:
            message = "Please choose a historical CSV file to import."
        else:
            start = perf_counter()
            uploaded_history = await read_history_csv_from_upload(history_csv_file)
            FILE_IMPORT_LATENCY.labels("history_csv").observe(perf_counter() - start)
            historical_data = merge_data(uploaded_history, historical_data)
            save_history(historical_data, config.HISTORY_FILE)
            get_logger().info("History CSV merged: file=%s rows=%s", history_csv_file.filename, len(uploaded_history))
            return redirect_to(route_url(request, "workout_performance", message=f"Uploaded {history_csv_file.filename} and merged {len(uploaded_history)} historical rows."))
    except Exception:
        message = config.HISTORY_IMPORT_ERROR_MESSAGE
        get_logger().warning("History CSV import failed")
    historical_data = load_history_file(config.HISTORY_FILE)
    return render(request, "upload_history.html", {"message": message, **build_page_context(historical_data)})
