"""Health and metrics routes."""

from __future__ import annotations

from fastapi import APIRouter, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

router = APIRouter()


@router.get("/healthz", name="healthz")
def healthz():
    """Return application health status."""
    return {"status": "ok"}


@router.get("/metrics", name="metrics")
def metrics():
    """Return Prometheus metrics."""
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
