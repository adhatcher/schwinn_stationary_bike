from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from app import app as handlers

router = APIRouter()
router.add_api_route("/setup-admin", handlers.setup_admin_get, methods=["GET"], response_class=HTMLResponse, name="setup_admin")
router.add_api_route(
    "/setup-admin",
    handlers.setup_admin_post,
    methods=["POST"],
    response_class=HTMLResponse,
    name="setup_admin_post",
)
router.add_api_route("/admin", handlers.admin_dashboard, methods=["GET"], response_class=HTMLResponse, name="admin_dashboard")
router.add_api_route(
    "/admin",
    handlers.admin_dashboard_post,
    methods=["POST"],
    response_class=HTMLResponse,
    name="admin_dashboard_post",
)
router.add_api_route("/admin/users", handlers.admin_users_get, methods=["GET"], response_class=HTMLResponse, name="admin_users")
router.add_api_route(
    "/admin/users",
    handlers.admin_users_post,
    methods=["POST"],
    response_class=HTMLResponse,
    name="admin_users_post",
)
