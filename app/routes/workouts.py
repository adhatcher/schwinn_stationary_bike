from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from app import app as handlers

router = APIRouter()
router.add_api_route("/download-history", handlers.download_history, methods=["GET"], name="download_history")
router.add_api_route("/", handlers.welcome, methods=["GET"], response_class=HTMLResponse, name="welcome")
router.add_api_route(
    "/workout-performance",
    handlers.workout_performance,
    methods=["GET"],
    response_class=HTMLResponse,
    name="workout_performance",
)
router.add_api_route(
    "/upload-workout",
    handlers.upload_workout_get,
    methods=["GET"],
    response_class=HTMLResponse,
    name="upload_workout",
)
router.add_api_route(
    "/upload-workout",
    handlers.upload_workout_post,
    methods=["POST"],
    response_class=HTMLResponse,
    name="upload_workout_post",
)
router.add_api_route(
    "/upload-history",
    handlers.upload_history_get,
    methods=["GET"],
    response_class=HTMLResponse,
    name="upload_history",
)
router.add_api_route(
    "/upload-history",
    handlers.upload_history_post,
    methods=["POST"],
    response_class=HTMLResponse,
    name="upload_history_post",
)
