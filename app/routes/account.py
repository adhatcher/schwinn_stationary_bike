"""Account routes."""

from __future__ import annotations

from fastapi import APIRouter, File, Form, Request, Response, UploadFile
from fastapi.responses import HTMLResponse
from werkzeug.security import check_password_hash

from app import config
from app.logging import audit_auth_event
from app.services.auth import current_user, password_is_valid
from app.services.avatar import parse_avatar_size, process_avatar_upload, user_has_avatar
from app.services.users import normalize_name, update_user_avatar, update_user_password, update_user_profile
from app.web import redirect_to, render, route_url

router = APIRouter()


@router.get("/account/avatar", name="account_avatar")
def account_avatar(request: Request):
    """Serve the current user avatar image."""
    user = current_user(request)
    if user is None or not user_has_avatar(user):
        return Response(status_code=404)
    return Response(
        bytes(user["avatar_data"]),
        media_type=str(user["avatar_mime"] or config.AVATAR_MIME_TYPE),
        headers={"Cache-Control": "private, max-age=300"},
    )


def account_context(message: str = "", message_type: str = "", avatar_size: int = config.AVATAR_SIZE_DEFAULT_PX) -> dict[str, object]:
    """Build template context for account forms."""
    return {
        "message": message,
        "message_type": message_type,
        "avatar_size": avatar_size,
        "avatar_size_min": config.AVATAR_SIZE_MIN_PX,
        "avatar_size_max": config.AVATAR_SIZE_MAX_PX,
    }


@router.get("/account", response_class=HTMLResponse, name="account")
def account_get(request: Request):
    """Render the account settings page."""
    return render(
        request,
        "account.html",
        account_context(request.query_params.get("message", ""), "success" if request.query_params.get("message", "") else ""),
    )


@router.post("/account", response_class=HTMLResponse, name="account_post")
async def account_post(
    request: Request,
    action: str = Form(""),
    name: str = Form(""),
    current_password: str = Form(""),
    new_password: str = Form(""),
    confirm_password: str = Form(""),
    avatar_size: str = Form(""),
    avatar: UploadFile | None = File(None),
):
    """Apply account profile, password, or avatar updates."""
    user = current_user(request)
    if user is None:
        return redirect_to(route_url(request, "login"))
    message = ""
    message_type = "error"
    parsed_avatar_size = config.AVATAR_SIZE_DEFAULT_PX
    if action == "profile":
        normalized_name = normalize_name(name)
        if not normalized_name:
            message = "Enter your name."
        else:
            update_user_profile(int(user["id"]), name=normalized_name)
            audit_auth_event("profile_update", str(user["email"]), "success", details="name updated")
            return redirect_to(route_url(request, "account", message="Profile updated."))
    elif action == "password":
        if not check_password_hash(str(user["password_hash"]), current_password):
            audit_auth_event("password_change", str(user["email"]), "failure", details="current password mismatch")
            message = "Current password is incorrect."
        elif not password_is_valid(new_password):
            audit_auth_event("password_change", str(user["email"]), "failure", details="password too short")
            message = "Choose a new password with at least 8 characters."
        elif new_password != confirm_password:
            audit_auth_event("password_change", str(user["email"]), "failure", details="password confirmation mismatch")
            message = "Passwords did not match. Please try again."
        else:
            update_user_password(int(user["id"]), new_password)
            audit_auth_event("password_change", str(user["email"]), "success", details="password updated")
            return redirect_to(route_url(request, "account", message="Password updated."))
    elif action == "avatar":
        parsed_avatar_size = parse_avatar_size(avatar_size)
        try:
            avatar_data = await process_avatar_upload(avatar, requested_size=parsed_avatar_size)
            update_user_avatar(int(user["id"]), avatar_data)
            audit_auth_event("profile_update", str(user["email"]), "success", details="avatar updated")
            return redirect_to(route_url(request, "account", message="Profile image updated."))
        except ValueError as exc:
            audit_auth_event("profile_update", str(user["email"]), "failure", details="avatar update failed")
            message = str(exc)
    else:
        message = "Choose an account action."
    return render(request, "account.html", account_context(message, message_type, parsed_avatar_size))
