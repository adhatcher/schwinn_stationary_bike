from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from app import app as handlers

router = APIRouter()
router.add_api_route("/login", handlers.login_get, methods=["GET"], response_class=HTMLResponse, name="login")
router.add_api_route("/login", handlers.login_post, methods=["POST"], response_class=HTMLResponse, name="login_post")
router.add_api_route("/register", handlers.register_get, methods=["GET"], response_class=HTMLResponse, name="register")
router.add_api_route("/register", handlers.register_post, methods=["POST"], response_class=HTMLResponse, name="register_post")
router.add_api_route(
    "/forgot-password",
    handlers.forgot_password_get,
    methods=["GET"],
    response_class=HTMLResponse,
    name="forgot_password",
)
router.add_api_route(
    "/forgot-password",
    handlers.forgot_password_post,
    methods=["POST"],
    response_class=HTMLResponse,
    name="forgot_password_post",
)
router.add_api_route(
    "/reset-password/{token}",
    handlers.reset_password_get,
    methods=["GET"],
    response_class=HTMLResponse,
    name="reset_password",
)
router.add_api_route(
    "/reset-password/{token}",
    handlers.reset_password_post,
    methods=["POST"],
    response_class=HTMLResponse,
    name="reset_password_post",
)
router.add_api_route("/logout", handlers.logout, methods=["GET"], name="logout")
