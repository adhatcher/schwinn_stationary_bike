"""Health and metrics route registrations."""

from __future__ import annotations

from fastapi import APIRouter

from app import app as handlers

router = APIRouter()
router.add_api_route("/healthz", handlers.healthz, methods=["GET"], name="healthz")
router.add_api_route("/metrics", handlers.metrics, methods=["GET"], name="metrics")
