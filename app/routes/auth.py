"""Authentication routes."""

from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse
from werkzeug.security import check_password_hash

from app import config
from app.logging import audit_auth_event
from app.services.auth import (
    admin_email_is_verified,
    current_user,
    generate_password_reset_token,
    login_user,
    logout_current_user,
    password_is_valid,
    verify_password_reset_token,
)
from app.services.email import build_reset_link, send_password_reset_email
from app.services.users import admin_exists, create_user, get_user_by_email, is_registration_enabled, normalize_email, normalize_name
from app.web import redirect_to, render, route_url

router = APIRouter()


@router.get("/login", response_class=HTMLResponse, name="login")
def login_get(request: Request):
    """Render the login form."""
    if not admin_exists():
        return redirect_to(route_url(request, "setup_admin"))
    if current_user(request) is not None:
        return redirect_to(route_url(request, "welcome"))
    return render(
        request,
        "login.html",
        {
            "message": request.query_params.get("message", ""),
            "email": normalize_email(request.query_params.get("email", "")),
            "registration_enabled": is_registration_enabled(),
        },
    )


@router.post("/login", response_class=HTMLResponse, name="login_post")
def login_post(request: Request, email: str = Form(""), password: str = Form("")):
    """Authenticate login credentials and start a session."""
    if not admin_exists():
        return redirect_to(route_url(request, "setup_admin"))
    if current_user(request) is not None:
        return redirect_to(route_url(request, "welcome"))

    normalized_email = normalize_email(email)
    user = get_user_by_email(normalized_email)
    if user and check_password_hash(str(user["password_hash"]), password):
        login_user(request, user)
        audit_auth_event("login", normalized_email, "success", details="user signed in")
        if str(user["role"]) == config.ADMIN_ROLE and not admin_email_is_verified(user):
            return redirect_to(route_url(request, "setup_admin", verify="email"))
        return redirect_to(route_url(request, "welcome"))
    audit_auth_event("login", normalized_email, "failure", details="invalid email or password")
    return render(
        request,
        "login.html",
        {
            "message": "We couldn't sign you in with that email and password.",
            "email": normalized_email,
            "registration_enabled": is_registration_enabled(),
        },
    )


@router.get("/register", response_class=HTMLResponse, name="register")
def register_get(request: Request):
    """Render the registration form when registration is enabled."""
    if not admin_exists():
        return redirect_to(route_url(request, "setup_admin"))
    if current_user(request) is not None:
        return redirect_to(route_url(request, "welcome"))
    if not is_registration_enabled():
        return render(request, "register.html", {"message": "New user registration is currently disabled.", "email": "", "name": ""}, status_code=403)
    return render(request, "register.html", {"message": "", "email": "", "name": ""})


@router.post("/register", response_class=HTMLResponse, name="register_post")
def register_post(
    request: Request,
    name: str = Form(""),
    email: str = Form(""),
    password: str = Form(""),
    confirm_password: str = Form(""),
):
    """Validate and create a self-service user account."""
    if not admin_exists():
        return redirect_to(route_url(request, "setup_admin"))
    if current_user(request) is not None:
        return redirect_to(route_url(request, "welcome"))
    if not is_registration_enabled():
        return render(request, "register.html", {"message": "New user registration is currently disabled.", "email": "", "name": ""}, status_code=403)

    normalized_name = normalize_name(name)
    normalized_email = normalize_email(email)
    message = ""
    if not normalized_name:
        message = "Enter your name to create your account."
    elif not normalized_email:
        message = "Enter an email address to create your account."
    elif "@" not in normalized_email:
        message = "Enter a valid email address."
    elif not password_is_valid(password):
        message = "Choose a password with at least 8 characters."
    elif password != confirm_password:
        message = "Passwords did not match. Please try again."
    elif get_user_by_email(normalized_email) is not None:
        message = "That email address is already registered."
    else:
        user = create_user(normalized_email, password, role=config.USER_ROLE, name=normalized_name)
        login_user(request, user)
        return redirect_to(route_url(request, "welcome"))
    return render(request, "register.html", {"message": message, "email": normalized_email, "name": normalized_name})


@router.get("/forgot-password", response_class=HTMLResponse, name="forgot_password")
def forgot_password_get(request: Request):
    """Render the forgot password form."""
    if not admin_exists():
        return redirect_to(route_url(request, "setup_admin"))
    return render(request, "forgot_password.html", {"message": "", "email": ""})


@router.post("/forgot-password", response_class=HTMLResponse, name="forgot_password_post")
def forgot_password_post(request: Request, email: str = Form("")):
    """Send a password reset email when the account exists."""
    if not admin_exists():
        return redirect_to(route_url(request, "setup_admin"))
    normalized_email = normalize_email(email)
    user = get_user_by_email(normalized_email)
    if user is not None:
        try:
            token = generate_password_reset_token(normalized_email)
            reset_link = build_reset_link(request, token)
            send_password_reset_email(normalized_email, reset_link)
            audit_auth_event("password_reset_email", normalized_email, "success", details="forgot password email sent")
        except Exception:
            audit_auth_event("password_reset_email", normalized_email, "failure", details="forgot password email send failed")
            return render(
                request,
                "forgot_password.html",
                {"message": "We couldn't send the password reset email right now. Please try again.", "email": normalized_email},
            )
    else:
        audit_auth_event("password_reset_email", normalized_email, "failure", details="forgot password requested for unknown email")
    return render(request, "forgot_password.html", {"message": "If that email is registered, a password reset link has been sent.", "email": ""})


@router.get("/reset-password/{token}", response_class=HTMLResponse, name="reset_password")
def reset_password_get(request: Request, token: str):
    """Render the password reset form for a valid token."""
    if not admin_exists():
        return redirect_to(route_url(request, "setup_admin"))
    user = verify_password_reset_token(token)
    if user is None:
        audit_auth_event("password_reset", "", "failure", details="invalid or expired reset token")
        return render(request, "reset_password.html", {"message": "That password reset link is invalid or has expired.", "token": token, "reset_link_valid": False})
    return render(request, "reset_password.html", {"message": "", "token": token, "reset_link_valid": True, "reset_email": str(user["email"])})


@router.post("/reset-password/{token}", response_class=HTMLResponse, name="reset_password_post")
def reset_password_post(
    request: Request,
    token: str,
    password: str = Form(""),
    confirm_password: str = Form(""),
):
    """Validate and apply a password reset submission."""
    if not admin_exists():
        return redirect_to(route_url(request, "setup_admin"))
    user = verify_password_reset_token(token)
    if user is None:
        audit_auth_event("password_reset", "", "failure", details="invalid or expired reset token")
        return render(request, "reset_password.html", {"message": "That password reset link is invalid or has expired.", "token": token, "reset_link_valid": False})
    message = ""
    if not password_is_valid(password):
        audit_auth_event("password_reset", str(user["email"]), "failure", details="password too short")
        message = "Choose a password with at least 8 characters."
    elif password != confirm_password:
        audit_auth_event("password_reset", str(user["email"]), "failure", details="password confirmation mismatch")
        message = "Passwords did not match. Please try again."
    else:
        from app.services.users import update_user_password

        update_user_password(int(user["id"]), password)
        logout_current_user(request)
        audit_auth_event("password_reset", str(user["email"]), "success", details="password updated")
        return redirect_to(
            route_url(
                request,
                "login",
                message="Your password has been reset. You can sign in now.",
                email=str(user["email"]),
            )
        )
    return render(request, "reset_password.html", {"message": message, "token": token, "reset_link_valid": True, "reset_email": str(user["email"])})


@router.get("/logout", name="logout")
def logout(request: Request):
    """End the current session and redirect to authentication."""
    user = current_user(request)
    logout_current_user(request)
    if user is not None:
        audit_auth_event("logout", str(user["email"]), "success", details="user signed out")
    return redirect_to(route_url(request, "setup_admin" if not admin_exists() else "login", message="You have been signed out."))
