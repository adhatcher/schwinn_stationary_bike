"""Account route registrations."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from app import app as handlers

router = APIRouter()
router.add_api_route("/account/avatar", handlers.account_avatar, methods=["GET"], name="account_avatar")
router.add_api_route("/account", handlers.account_get, methods=["GET"], response_class=HTMLResponse, name="account")
router.add_api_route("/account", handlers.account_post, methods=["POST"], response_class=HTMLResponse, name="account_post")
