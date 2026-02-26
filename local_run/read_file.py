#!/usr/bin/env python3
"""Read Schwinn workout data and display trend charts."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from pprint import pprint

import pandas as pd
from matplotlib import pyplot as plt

DEFAULT_DATA_FILE = Path("/Volumes/AARON/AARON1.DAT")
DEFAULT_HISTORY_FILE = Path("Workout_History.csv")
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


def is_accessible(path: Path) -> bool:
    """Return True when the file exists and can be read."""
    return path.is_file() and os.access(path, os.R_OK)


def _extract_json_objects(payload: str) -> list[dict]:
    """Decode multiple JSON objects from a noisy text payload."""
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


def _read_dat_file(data_file: Path) -> list[dict]:
    """Parse the bike DAT file into workout dictionaries without shelling out."""
    raw_text = data_file.read_text(encoding="utf-8", errors="ignore")
    payload = "\n".join(raw_text.splitlines()[8:])
    workouts = _extract_json_objects(payload)

    if not workouts:
        raise ValueError(f"No workout data found in {data_file}")

    return workouts


def _load_workout_data(workout_json: list[dict], column_names: list[str]) -> pd.DataFrame:
    """Load workout data from parsed JSON dictionaries."""
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

    df_table = pd.DataFrame(rows, columns=column_names)
    df_table["Workout_Date"] = pd.to_datetime(df_table["Workout_Date"], errors="coerce")

    return df_table.dropna(subset=["Workout_Date"]).reset_index(drop=True)


def _load_history_file(history_file: Path, column_names: list[str]) -> pd.DataFrame:
    """Load the workout history file if present."""
    try:
        history_df = pd.read_csv(history_file)
        history_df["Workout_Date"] = pd.to_datetime(history_df["Workout_Date"], errors="coerce")
    except (FileNotFoundError, pd.errors.EmptyDataError):
        history_df = pd.DataFrame(columns=column_names)

    return history_df.dropna(subset=["Workout_Date"]).sort_values(by=["Workout_Date"]).reset_index(drop=True)


def _merge_data(new_file: pd.DataFrame, old_file: pd.DataFrame) -> pd.DataFrame:
    """Merge historical and newly imported workouts and deduplicate rows."""
    combined_file = pd.concat([old_file, new_file], ignore_index=True)
    sorted_file = combined_file.sort_values(by=["Workout_Date"]).reset_index(drop=True)
    unique_df = sorted_file.drop_duplicates(subset=["Workout_Date", "Workout_Time"]).reset_index(drop=True)

    return unique_df


def _write_new_history(data: pd.DataFrame, history_file: Path) -> None:
    """Write merged workout history to disk."""
    data.to_csv(history_file, index=False)


def _graph_progress(df: pd.DataFrame) -> None:
    """Graph historical workout trends."""
    graph_df = df.copy()
    graph_df["Workout_Date"] = graph_df["Workout_Date"].dt.date
    sorted_file = graph_df.sort_values(by="Workout_Date")

    plt.figure(1)
    ax = plt.gca()
    plt.title("Historical Distance")
    sorted_file.plot(kind="line", x="Workout_Date", y="Workout_Time", ax=ax)
    sorted_file.plot(kind="line", x="Workout_Date", y="Distance", ax=ax)
    sorted_file.plot(kind="line", x="Workout_Date", y="Avg_Speed", ax=ax)
    sorted_file.plot(kind="line", x="Workout_Date", y="Level", ax=ax)

    plt.figure(2)
    ax = plt.gca()
    plt.title("Historical Performance")
    sorted_file.plot(kind="line", x="Workout_Date", y="RPM", ax=ax)
    sorted_file.plot(kind="line", x="Workout_Date", y="Total_Calories", ax=ax)
    sorted_file.plot(kind="line", x="Workout_Date", y="Heart_Rate", ax=ax)

    sorted_file.plot.bar(stacked=True, x="Workout_Date")
    plt.show()


def _show_last_n_days(df: pd.DataFrame, days: int = 30) -> None:
    """Graph workout trends for the last N days."""
    graph_df = df.copy()
    graph_df["Workout_Date"] = pd.to_datetime(graph_df["Workout_Date"], errors="coerce")
    start_date = pd.Timestamp("now").floor("D") - pd.offsets.Day(days)

    last_n_days = graph_df[graph_df["Workout_Date"] >= start_date]
    sorted_file = last_n_days.sort_values(by="Workout_Date", ascending=False)

    plt.figure(1)
    ax = plt.gca()
    plt.title(f"Distance and Average Speed over the Last {days} Days")
    sorted_file.plot(kind="line", x="Workout_Date", y="Workout_Time", ax=ax)
    sorted_file.plot(kind="line", x="Workout_Date", y="Distance", ax=ax)
    sorted_file.plot(kind="line", x="Workout_Date", y="Avg_Speed", ax=ax)
    sorted_file.plot(kind="line", x="Workout_Date", y="Level", ax=ax)

    plt.figure(2)
    ax = plt.gca()
    plt.title(f"Calories and Heart Rate over the Last {days} Days")
    sorted_file.plot(kind="line", x="Workout_Date", y="RPM", ax=ax)
    sorted_file.plot(kind="line", x="Workout_Date", y="Total_Calories", ax=ax)
    sorted_file.plot(kind="line", x="Workout_Date", y="Heart_Rate", ax=ax)

    bar_df = last_n_days.copy()
    bar_df["Workout_Date"] = bar_df["Workout_Date"].dt.date
    bar_df = bar_df.sort_values(by="Workout_Date")
    bar_df.plot.bar(stacked=True, x="Workout_Date")
    plt.show()


def _build_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read Schwinn bike workout data and plot progress.")
    parser.add_argument("--data-file", type=Path, default=DEFAULT_DATA_FILE, help="Path to Schwinn DAT file.")
    parser.add_argument("--history-file", type=Path, default=DEFAULT_HISTORY_FILE, help="Path to CSV history file.")
    parser.add_argument("--days", type=int, default=30, help="Number of recent days to graph.")
    return parser.parse_args()


def main() -> int:
    args = _build_args()

    print("Loading historical data")
    historical_data = _load_history_file(args.history_file, COLUMN_NAMES)
    print("Historical data loaded")

    if is_accessible(args.data_file):
        print(f"Workout file found: {args.data_file}")
        workout_table = _load_workout_data(_read_dat_file(args.data_file), COLUMN_NAMES)
        combined_data = _merge_data(workout_table, historical_data)
        print("Saving updated history file")
        _write_new_history(combined_data, args.history_file)
    else:
        print(f"Workout file not found: {args.data_file}; using history only")
        combined_data = historical_data

    if combined_data.empty:
        print("No workout data available to graph")
        return 0

    _graph_progress(combined_data)
    _show_last_n_days(combined_data, args.days)

    pd.set_option("display.max_rows", combined_data.shape[0] + 1)
    pd.set_option("display.max_columns", None)
    pd.set_option("display.width", 120)
    pprint(combined_data)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
