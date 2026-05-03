"""Workout filtering, formatting, and summary helpers."""

from __future__ import annotations

import pandas as pd

from app import config


def filter_data(df: pd.DataFrame, start_date: str, end_date: str) -> pd.DataFrame:
    """Filter workout history by inclusive date bounds."""
    filtered = df.copy()

    if start_date:
        filtered = filtered[filtered["Workout_Date"] >= pd.to_datetime(start_date)]
    if end_date:
        filtered = filtered[filtered["Workout_Date"] <= pd.to_datetime(end_date)]

    return filtered.sort_values(by=["Workout_Date"]).reset_index(drop=True)


def current_day() -> pd.Timestamp:
    """Return today as a normalized pandas timestamp."""
    return pd.Timestamp.now().normalize()


def summarize_window(df: pd.DataFrame, *, days: int, today: pd.Timestamp | None = None) -> dict[str, float | int]:
    """Summarize workout totals for a rolling day window."""
    if today is None:
        today = current_day()
    else:
        today = pd.to_datetime(today).normalize()

    if df.empty:
        return {"workout_count": 0, "distance": 0.0, "workout_time": 0}

    window_start = today - pd.Timedelta(days=max(days - 1, 0))
    window_df = df[(df["Workout_Date"] >= window_start) & (df["Workout_Date"] <= today)]
    return {
        "workout_count": int(len(window_df)),
        "distance": float(pd.to_numeric(window_df["Distance"], errors="coerce").fillna(0).sum()),
        "workout_time": int(pd.to_numeric(window_df["Workout_Time"], errors="coerce").fillna(0).sum()),
    }


def format_distance(value: float) -> str:
    """Format a distance value without redundant decimals."""
    formatted = f"{value:.1f}"
    if "." in formatted:
        formatted = formatted.rstrip("0").rstrip(".")
    return formatted


def format_minutes(total_minutes: int) -> str:
    """Format total minutes as hours and minutes."""
    hours, minutes = divmod(int(total_minutes), 60)
    if hours and minutes:
        return f"{hours}h {minutes}m"
    if hours:
        return f"{hours}h"
    return f"{minutes}m"


def build_summary_cards(df: pd.DataFrame) -> dict[str, dict[str, str]]:
    """Build dashboard summary card values from history."""
    today = current_day()
    last_30 = summarize_window(df, days=30, today=today)
    last_year = summarize_window(df, days=365, today=today)

    return {
        "last_30_days": {
            "workouts": str(last_30["workout_count"]),
            "distance": format_distance(last_30["distance"]),
            "time": format_minutes(last_30["workout_time"]),
        },
        "last_year": {
            "workouts": str(last_year["workout_count"]),
            "distance": format_distance(last_year["distance"]),
            "time": format_minutes(last_year["workout_time"]),
        },
    }


def build_last_30_day_workouts(df: pd.DataFrame, *, today: pd.Timestamp | None = None) -> list[dict[str, str]]:
    """Build display rows for workouts included in the last 30-day summary."""
    if df.empty:
        return []

    if today is None:
        today = current_day()
    else:
        today = pd.to_datetime(today).normalize()

    window_start = today - pd.Timedelta(days=29)
    recent_df = df[(df["Workout_Date"] >= window_start) & (df["Workout_Date"] <= today)]
    recent_df = recent_df.sort_values(by=["Workout_Date"], ascending=False)
    rows = []
    for _, workout in recent_df.iterrows():
        rows.append(
            {
                "date": pd.to_datetime(workout["Workout_Date"]).date().isoformat(),
                "time": format_minutes(int(pd.to_numeric(workout["Workout_Time"], errors="coerce"))),
                "distance": format_distance(float(pd.to_numeric(workout["Distance"], errors="coerce"))),
                "average_speed": format_distance(float(pd.to_numeric(workout["Avg_Speed"], errors="coerce"))),
                "total_calories": format_distance(float(pd.to_numeric(workout["Total_Calories"], errors="coerce"))),
            }
        )
    return rows


def build_page_context(historical_data: pd.DataFrame) -> dict[str, object]:
    """Build shared page context from workout history."""
    min_date = ""
    max_date = ""
    last_workout_date = ""
    days_since_last_workout = None

    if not historical_data.empty:
        min_ts = historical_data["Workout_Date"].min()
        max_ts = historical_data["Workout_Date"].max()
        min_date = min_ts.date().isoformat()
        max_date = max_ts.date().isoformat()
        last_workout_date = max_ts.date().isoformat()
        days_since_last_workout = int((current_day() - max_ts.normalize()).days)

    return {
        "history_file": config.HISTORY_FILE,
        "dat_file": config.DAT_FILE,
        "historical_count": len(historical_data),
        "min_date": min_date,
        "max_date": max_date,
        "last_workout_date": last_workout_date,
        "days_since_last_workout": days_since_last_workout,
        "summary_cards": build_summary_cards(historical_data),
        "last_30_day_workouts": build_last_30_day_workouts(historical_data),
    }


def parse_field_selection(args) -> list[str]:
    """Parse and validate selected graph fields from query parameters."""
    requested_fields: list[str] = []

    for field in args.getlist("field"):
        if field:
            requested_fields.append(field.strip())

    for field in args.getlist("field[]"):
        if field:
            requested_fields.append(field.strip())

    comma_fields = args.get("fields", "")
    if comma_fields:
        for field in comma_fields.split(","):
            cleaned = field.strip()
            if cleaned:
                requested_fields.append(cleaned)

    if not requested_fields:
        return list(config.GRAPHABLE_FIELDS)

    deduped_fields: list[str] = []
    for field in requested_fields:
        cleaned = field.strip().strip("'\"")
        if cleaned and cleaned not in deduped_fields:
            deduped_fields.append(cleaned)

    invalid_fields = [field for field in deduped_fields if field not in config.GRAPHABLE_FIELDS]
    if invalid_fields:
        raise ValueError(f"Unsupported field(s): {', '.join(invalid_fields)}")

    return deduped_fields


def normalize_workout_detail_period(period: str | None) -> str:
    """Return a supported workout detail period."""
    normalized = (period or "last_1_year").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "1week": "1_week",
        "2weeks": "2_weeks",
        "1month": "1_month",
        "begin_of_month": "begin_month",
        "beginning_of_month": "begin_month",
        "3months": "3_months",
        "5months": "5_months",
        "last_year": "last_1_year",
        "all_time": "all",
    }
    normalized = aliases.get(normalized, normalized)
    return normalized if normalized in config.WORKOUT_DETAIL_PERIODS else "last_1_year"


def workout_detail_period_bounds(period: str, historical_data: pd.DataFrame) -> tuple[str, str]:
    """Calculate inclusive date bounds for the selected workout detail period."""
    if historical_data.empty:
        return "", ""

    if period == "all":
        return (
            historical_data["Workout_Date"].min().date().isoformat(),
            historical_data["Workout_Date"].max().date().isoformat(),
        )

    today = current_day()
    if period == "1_week":
        start = today - pd.Timedelta(days=6)
    elif period == "2_weeks":
        start = today - pd.Timedelta(days=13)
    elif period == "1_month":
        start = today - pd.Timedelta(days=29)
    elif period == "begin_month":
        start = today.replace(day=1)
    elif period == "3_months":
        start = today - pd.Timedelta(days=89)
    elif period == "5_months":
        start = today - pd.Timedelta(days=149)
    elif period == "current_year":
        start = today.replace(month=1, day=1)
    else:
        start = today - pd.Timedelta(days=364)

    return start.date().isoformat(), today.date().isoformat()


def parse_graph_selection(args) -> list[str]:
    """Parse selected Workout Details graph fields from query parameters."""
    requested_graphs: list[str] = []
    for graph in args.getlist("graphs"):
        if graph:
            requested_graphs.append(graph.strip())

    comma_graphs = args.get("graph_fields", "")
    if comma_graphs:
        requested_graphs.extend(graph.strip() for graph in comma_graphs.split(",") if graph.strip())

    if not requested_graphs:
        return [] if args.get("graphs_submitted") == "1" else list(config.GRAPHABLE_FIELDS)

    selected_graphs: list[str] = []
    for graph in requested_graphs:
        cleaned = graph.strip().strip("'\"")
        if cleaned in config.GRAPHABLE_FIELDS and cleaned not in selected_graphs:
            selected_graphs.append(cleaned)

    return selected_graphs


def summarize_stats(df: pd.DataFrame) -> dict[str, object]:
    """Build MapMy-style summary totals for a filtered workout set."""
    if df.empty:
        return {
            "workout_count": 0,
            "distance": "0",
            "duration": "0m",
            "calories": "0",
            "average_speed": "0",
        }

    distance = float(pd.to_numeric(df["Distance"], errors="coerce").fillna(0).sum())
    duration = int(pd.to_numeric(df["Workout_Time"], errors="coerce").fillna(0).sum())
    calories = float(pd.to_numeric(df["Total_Calories"], errors="coerce").fillna(0).sum())
    average_speed_series = pd.to_numeric(df["Avg_Speed"], errors="coerce").dropna()
    average_speed = 0.0 if average_speed_series.empty else float(average_speed_series.mean())
    return {
        "workout_count": int(len(df)),
        "distance": format_distance(distance),
        "duration": format_minutes(duration),
        "calories": format_distance(calories),
        "average_speed": format_distance(average_speed),
    }


def build_lifetime_stats(df: pd.DataFrame) -> dict[str, object]:
    """Summarize all-time workout history."""
    summary = summarize_stats(df)
    if df.empty:
        summary.update({"first_workout": "", "latest_workout": "", "best_distance": "0", "longest_duration": "0m"})
        return summary

    distances = pd.to_numeric(df["Distance"], errors="coerce").fillna(0)
    durations = pd.to_numeric(df["Workout_Time"], errors="coerce").fillna(0)
    summary.update(
        {
            "first_workout": df["Workout_Date"].min().date().isoformat(),
            "latest_workout": df["Workout_Date"].max().date().isoformat(),
            "best_distance": format_distance(float(distances.max())),
            "longest_duration": format_minutes(int(durations.max())),
        }
    )
    return summary


def field_label(field: str) -> str:
    """Return a readable label for a workout field."""
    labels = {
        "Avg_Speed": "Avg Speed",
        "Workout_Time": "Workout Time",
        "Total_Calories": "Total Calories",
        "Heart_Rate": "Heart Rate",
    }
    return labels.get(field, field.replace("_", " "))


def workout_detail_bucket_granularity(period: str) -> str:
    """Return the grouping granularity used for Workout Details charts."""
    if period in {"1_week", "2_weeks", "1_month", "begin_month"}:
        return "daily"
    if period == "all":
        return "monthly"
    return "weekly"


def format_bucket_label(bucket_date: pd.Timestamp, granularity: str) -> str:
    """Format chart bucket labels."""
    bucket = pd.to_datetime(bucket_date)
    if granularity == "monthly":
        return bucket.strftime("%b %Y")
    if granularity == "weekly":
        end = bucket + pd.Timedelta(days=6)
        return f"{bucket.strftime('%b')} {bucket.day} - {end.strftime('%b')} {end.day}"
    return f"{bucket.strftime('%b')} {bucket.day}"


def add_workout_detail_buckets(df: pd.DataFrame, period: str) -> pd.DataFrame:
    """Attach daily, Sunday-weekly, or monthly bucket dates to workout rows."""
    bucketed = df.copy()
    workout_dates = pd.to_datetime(bucketed["Workout_Date"], errors="coerce").dt.normalize()
    granularity = workout_detail_bucket_granularity(period)
    if granularity == "monthly":
        bucketed["Bucket_Date"] = workout_dates.dt.to_period("M").dt.to_timestamp()
    elif granularity == "weekly":
        days_since_sunday = (workout_dates.dt.weekday + 1) % 7
        bucketed["Bucket_Date"] = workout_dates - pd.to_timedelta(days_since_sunday, unit="D")
    else:
        bucketed["Bucket_Date"] = workout_dates
    return bucketed.dropna(subset=["Bucket_Date"])


def build_metric_breakdown(df: pd.DataFrame, metric: str, period: str) -> list[dict[str, object]]:
    """Build chart rows for one Workout Details metric."""
    if df.empty or metric not in config.GRAPHABLE_FIELDS:
        return []

    metric_df = add_workout_detail_buckets(df, period)
    metric_df["Metric_Value"] = pd.to_numeric(metric_df[metric], errors="coerce")
    aggregation = config.METRIC_AGGREGATIONS.get(metric, "mean")
    if aggregation == "sum":
        metric_df["Metric_Value"] = metric_df["Metric_Value"].fillna(0)

    grouped = (
        metric_df.groupby("Bucket_Date", as_index=False)
        .agg(value=("Metric_Value", aggregation), workouts=("Metric_Value", "count"))
        .dropna(subset=["value"])
        .sort_values(by=["Bucket_Date"])
    )
    granularity = workout_detail_bucket_granularity(period)
    return [
        {
            "label": format_bucket_label(row["Bucket_Date"], granularity),
            "value": float(row["value"]),
            "workouts": int(row["workouts"]),
        }
        for _, row in grouped.iterrows()
    ]


def build_workout_details_context(historical_data: pd.DataFrame, period: str, selected_graphs: list[str]) -> dict[str, object]:
    """Build template context for the Workout Details page."""
    from app.workouts.charts import build_workout_detail_charts

    start_date, end_date = workout_detail_period_bounds(period, historical_data)
    filtered_data = filter_data(historical_data, start_date, end_date)
    return {
        "workout_detail_periods": config.WORKOUT_DETAIL_PERIODS,
        "selected_period": period,
        "selected_period_label": config.WORKOUT_DETAIL_PERIODS[period],
        "selected_graphs": selected_graphs,
        "graph_fields": config.GRAPHABLE_FIELDS,
        "field_label": field_label,
        "detail_start_date": start_date,
        "detail_end_date": end_date,
        "detail_summary": summarize_stats(filtered_data),
        "lifetime_stats": build_lifetime_stats(historical_data),
        "detail_record_count": len(filtered_data),
        "detail_charts": build_workout_detail_charts(filtered_data, period, selected_graphs),
    }
