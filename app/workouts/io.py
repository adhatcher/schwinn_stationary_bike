"""Workout import, parsing, and persistence helpers."""

from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path
from typing import Iterable

import pandas as pd
from fastapi import UploadFile

from app import config


def extract_json_objects(payload: str) -> list[dict]:
    """Extract JSON objects embedded in arbitrary text."""
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
    """Parse Schwinn DAT text into workout objects."""
    payload = "\n".join(raw_text.splitlines()[8:])
    workouts = extract_json_objects(payload)
    if not workouts:
        raise ValueError("No workout objects found in DAT file.")
    return workouts


def load_workout_data(workout_json: Iterable[dict]) -> pd.DataFrame:
    """Convert workout objects into a normalized DataFrame."""
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

    df_table = pd.DataFrame(rows, columns=config.COLUMN_NAMES)
    df_table["Workout_Date"] = pd.to_datetime(df_table["Workout_Date"], errors="coerce")
    return df_table.dropna(subset=["Workout_Date"]).reset_index(drop=True)


def load_history_file(history_file: Path) -> pd.DataFrame:
    """Load the workout history CSV into a normalized DataFrame."""
    if not history_file.exists() or history_file.stat().st_size == 0:
        return pd.DataFrame(columns=config.COLUMN_NAMES)

    history_df = pd.read_csv(history_file)
    if history_df.empty:
        return pd.DataFrame(columns=config.COLUMN_NAMES)

    history_df["Workout_Date"] = pd.to_datetime(history_df["Workout_Date"], errors="coerce")
    history_df = history_df.dropna(subset=["Workout_Date"])

    for field in config.GRAPHABLE_FIELDS:
        if field in history_df.columns:
            history_df[field] = pd.to_numeric(history_df[field], errors="coerce")

    return history_df[config.COLUMN_NAMES].sort_values(by=["Workout_Date"]).reset_index(drop=True)


def merge_data(new_data: pd.DataFrame, historical_data: pd.DataFrame) -> pd.DataFrame:
    """Merge imported workouts into existing history."""
    if historical_data.empty:
        return new_data.sort_values(by=["Workout_Date"]).reset_index(drop=True)
    if new_data.empty:
        return historical_data.sort_values(by=["Workout_Date"]).reset_index(drop=True)

    combined_file = pd.concat([historical_data, new_data], ignore_index=True)
    sorted_file = combined_file.sort_values(by=["Workout_Date"]).reset_index(drop=True)
    merged = sorted_file.drop_duplicates(subset=["Workout_Date", "Workout_Time"], keep="last")
    return merged.reset_index(drop=True)


def save_history(data: pd.DataFrame, history_file: Path) -> None:
    """Persist workout history to CSV."""
    history_file.parent.mkdir(parents=True, exist_ok=True)
    data.to_csv(history_file, index=False)


def read_dat_from_disk(dat_file: Path) -> pd.DataFrame:
    """Read and parse a DAT file from disk."""
    raw_text = dat_file.read_text(encoding="utf-8", errors="ignore")
    return load_workout_data(parse_dat_payload(raw_text))


async def read_dat_from_upload(upload_file: UploadFile) -> pd.DataFrame:
    """Read and parse an uploaded DAT file."""
    raw_bytes = await upload_file.read()
    raw_text = raw_bytes.decode("utf-8", errors="ignore")
    return load_workout_data(parse_dat_payload(raw_text))


async def read_history_csv_from_upload(upload_file: UploadFile) -> pd.DataFrame:
    """Read and validate an uploaded history CSV."""
    upload_df = pd.read_csv(BytesIO(await upload_file.read()))
    missing_columns = [col for col in config.COLUMN_NAMES if col not in upload_df.columns]
    if missing_columns:
        raise ValueError(f"Missing required columns in historical CSV: {', '.join(missing_columns)}")

    history_df = upload_df[config.COLUMN_NAMES].copy()
    history_df["Workout_Date"] = pd.to_datetime(history_df["Workout_Date"], errors="coerce")
    history_df = history_df.dropna(subset=["Workout_Date"])

    for field in config.GRAPHABLE_FIELDS:
        history_df[field] = pd.to_numeric(history_df[field], errors="coerce")

    return history_df.sort_values(by=["Workout_Date"]).reset_index(drop=True)
