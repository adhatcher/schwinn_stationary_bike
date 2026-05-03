"""Grafana API routes."""

from __future__ import annotations

import pandas as pd
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app import config
from app.workouts.analytics import filter_data, parse_field_selection
from app.workouts.io import load_history_file

router = APIRouter()


@router.get("/api/grafana/workouts", name="grafana_workouts")
def grafana_workouts(request: Request):
    """Return workout history as Grafana-friendly time series points."""
    start_date = request.query_params.get("from", "") or request.query_params.get("start_date", "")
    end_date = request.query_params.get("to", "") or request.query_params.get("end_date", "")
    try:
        selected_fields = parse_field_selection(request.query_params)
    except ValueError:
        return JSONResponse({"error": "Unsupported field selection.", "allowed_fields": config.GRAPHABLE_FIELDS}, status_code=400)

    filtered_data = filter_data(load_history_file(config.HISTORY_FILE), start_date, end_date)
    points: list[dict] = []
    for _, row in filtered_data.iterrows():
        timestamp = row["Workout_Date"].strftime("%Y-%m-%dT%H:%M:%SZ")
        for field in selected_fields:
            value = row[field]
            if not pd.isna(value):
                points.append({"time": timestamp, "field": field, "value": float(value)})
    return points


@router.get("/api/grafana/summary", name="grafana_summary")
def grafana_summary(request: Request):
    """Return aggregate workout summaries for Grafana."""
    start_date = request.query_params.get("from", "") or request.query_params.get("start_date", "")
    end_date = request.query_params.get("to", "") or request.query_params.get("end_date", "")
    try:
        selected_fields = parse_field_selection(request.query_params)
    except ValueError:
        return JSONResponse({"error": "Unsupported field selection.", "allowed_fields": config.GRAPHABLE_FIELDS}, status_code=400)

    filtered_data = filter_data(load_history_file(config.HISTORY_FILE), start_date, end_date)
    summary: dict[str, object] = {
        "workout_count": int(len(filtered_data)),
        "from": start_date,
        "to": end_date,
        "fields": selected_fields,
        "averages": {},
        "minimums": {},
        "maximums": {},
    }
    for field in selected_fields:
        field_series = pd.to_numeric(filtered_data[field], errors="coerce").dropna()
        summary["averages"][field] = None if field_series.empty else float(field_series.mean())
        summary["minimums"][field] = None if field_series.empty else float(field_series.min())
        summary["maximums"][field] = None if field_series.empty else float(field_series.max())
    return summary
