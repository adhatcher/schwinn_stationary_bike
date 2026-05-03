"""Plotly chart builders for workout pages."""

from __future__ import annotations

from time import perf_counter

import pandas as pd
import plotly.graph_objects as go

from app.bootstrap.metrics import CHART_GENERATION_LATENCY, CHARTS_GENERATED
from app.workouts.analytics import build_metric_breakdown, field_label


def build_chart(df: pd.DataFrame, fields: list[str]) -> str | None:
    """Render the selected workout fields as Plotly HTML."""
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
        autosize=True,
        hovermode="x unified",
        template="plotly_white",
        margin=dict(l=48, r=24, t=58, b=48),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )

    chart_html = fig.to_html(
        full_html=False,
        include_plotlyjs="cdn",
        default_width="100%",
        default_height="100%",
        config={"responsive": True, "displaylogo": False, "displayModeBar": False},
    )
    CHARTS_GENERATED.inc()
    CHART_GENERATION_LATENCY.observe(perf_counter() - start)
    return chart_html


def metric_axis_title(metric: str) -> str:
    """Return a concise y-axis title for a workout metric chart."""
    if metric == "Workout_Time":
        return "Minutes"
    if metric == "Total_Calories":
        return "Calories"
    if metric == "Heart_Rate":
        return "BPM"
    return field_label(metric)


def build_metric_bar_chart(metric: str, rows: list[dict[str, object]], *, include_plotlyjs: str | bool) -> str | None:
    """Render one Workout Details metric as a responsive Plotly bar chart."""
    if not rows:
        return None

    label = field_label(metric)
    fig = go.Figure()
    fig.add_trace(
        go.Bar(
            x=[row["label"] for row in rows],
            y=[row["value"] for row in rows],
            name=label,
            marker_color="#c8102e",
            hovertemplate="%{x}<br>%{y:.2f}<extra></extra>",
        )
    )
    fig.update_layout(
        title=label,
        xaxis_title="Period",
        yaxis_title=metric_axis_title(metric),
        autosize=True,
        template="plotly_white",
        margin=dict(l=44, r=18, t=54, b=56),
        showlegend=False,
    )
    return fig.to_html(
        full_html=False,
        include_plotlyjs=include_plotlyjs,
        default_width="100%",
        default_height="100%",
        config={"responsive": True, "displaylogo": False, "displayModeBar": False},
    )


def build_workout_detail_charts(df: pd.DataFrame, period: str, selected_graphs: list[str]) -> list[dict[str, object]]:
    """Build all selected Workout Details charts."""
    charts = []
    include_plotlyjs: str | bool = "cdn"
    for metric in selected_graphs:
        rows = build_metric_breakdown(df, metric, period)
        chart_html = build_metric_bar_chart(metric, rows, include_plotlyjs=include_plotlyjs)
        if chart_html:
            charts.append({"field": metric, "label": field_label(metric), "html": chart_html})
            include_plotlyjs = False
    return charts
