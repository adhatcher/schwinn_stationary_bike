"""Grafana API route registrations."""

from __future__ import annotations

from fastapi import APIRouter

from app import app as handlers

router = APIRouter()
router.add_api_route("/api/grafana/workouts", handlers.grafana_workouts, methods=["GET"], name="grafana_workouts")
router.add_api_route("/api/grafana/summary", handlers.grafana_summary, methods=["GET"], name="grafana_summary")
