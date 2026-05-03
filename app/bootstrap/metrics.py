"""Prometheus metric collector helpers for the Schwinn app."""

from __future__ import annotations

from prometheus_client import Counter, Histogram, REGISTRY


def _collector(name: str):
    """Return an existing Prometheus collector by registered name."""
    return REGISTRY._names_to_collectors.get(name)  # noqa: SLF001


def counter(name: str, documentation: str, labelnames: list[str] | None = None) -> Counter:
    """Create or reuse a Prometheus counter."""
    existing = _collector(name) or _collector(f"{name}_total")
    if existing is not None:
        return existing
    return Counter(name, documentation, labelnames or [])


def histogram(name: str, documentation: str, labelnames: list[str] | None = None) -> Histogram:
    """Create or reuse a Prometheus histogram."""
    existing = _collector(name)
    if existing is not None:
        return existing
    return Histogram(name, documentation, labelnames or [])


REQUEST_COUNT = counter(
    "schwinn_http_requests_total",
    "Total HTTP requests",
    ["method", "endpoint", "status_code"],
)
REQUEST_LATENCY = histogram(
    "schwinn_http_request_duration_seconds",
    "HTTP request latency in seconds",
    ["method", "endpoint"],
)
CHARTS_GENERATED = counter(
    "schwinn_charts_generated_total",
    "Total charts generated",
)
CHART_GENERATION_LATENCY = histogram(
    "schwinn_chart_generation_duration_seconds",
    "Time spent generating chart HTML",
)
FILE_IMPORT_LATENCY = histogram(
    "schwinn_file_import_duration_seconds",
    "Time spent importing DAT files",
    ["source"],
)
AUTH_EVENT_COUNT = counter(
    "schwinn_auth_events_total",
    "Authentication and account management events",
    ["action", "result"],
)
