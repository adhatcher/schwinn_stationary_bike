"""Admin routes."""

from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse

from app import config
from app.logging import audit_auth_event
from app.services.auth import (
    admin_email_is_verified,
    current_user,
    current_user_is_admin,
    generate_password_reset_token,
    generate_temporary_password,
    login_user,
    password_is_valid,
)
from app.services.email import build_reset_link, send_password_reset_email
from app.services.users import (
    admin_count,
    admin_exists,
    create_user,
    delete_user,
    display_user_name,
    email_is_valid,
    get_user_by_email,
    get_user_by_id,
    list_users,
    normalize_email,
    normalize_name,
    set_registration_enabled,
    update_admin_identity,
    update_user_role,
)
from app.web import redirect_to, render, route_url

router = APIRouter()


def setup_admin_context(message: str, email: str, first_name: str, last_name: str, email_verified: bool, existing_admin: bool) -> dict[str, object]:
    """Build template context for admin setup forms."""
    return {
        "message": message,
        "email": email,
        "first_name": first_name,
        "last_name": last_name,
        "email_verified": email_verified,
        "existing_admin": existing_admin,
    }


def update_admin_setup(
    request: Request,
    user,
    *,
    first_name: str | None = None,
    last_name: str | None = None,
    email: str | None = None,
    email_verified: bool = False,
    submitted: bool = False,
):
    """Validate and update an existing unverified admin identity."""
    message = "Verify or correct the admin email address before continuing."
    current_first_name = normalize_name(str(user["first_name"])) if "first_name" in user.keys() else ""
    current_last_name = normalize_name(str(user["last_name"])) if "last_name" in user.keys() else ""
    if not current_first_name and not current_last_name:
        name_parts = display_user_name(user).split()
        current_first_name = name_parts[0] if name_parts else ""
        current_last_name = " ".join(name_parts[1:]) if len(name_parts) > 1 else ""

    display_first_name = normalize_name(first_name if first_name is not None else current_first_name)
    display_last_name = normalize_name(last_name if last_name is not None else current_last_name)
    display_email = normalize_email(email if email is not None else str(user["email"]))

    if submitted:
        if not display_first_name:
            message = "Enter the admin first name."
        elif not display_last_name:
            message = "Enter the admin last name."
        elif not display_email:
            message = "Enter an email address for the admin account."
        elif not email_is_valid(display_email):
            message = "Enter a valid email address."
        elif not email_verified:
            message = "Confirm that the admin email address has been verified."
        else:
            update_admin_identity(
                int(user["id"]),
                first_name=display_first_name,
                last_name=display_last_name,
                email=display_email,
                email_verified=True,
            )
            audit_auth_event("admin_email_verify", display_email, "success", details="admin email verified")
            return redirect_to(route_url(request, "admin_dashboard", message="Admin email verified."))

    return render(
        request,
        "setup_admin.html",
        setup_admin_context(message, display_email, display_first_name, display_last_name, email_verified, True),
    )


@router.get("/setup-admin", response_class=HTMLResponse, name="setup_admin")
def setup_admin_get(request: Request):
    """Render or redirect the admin setup flow."""
    current = current_user(request)
    if admin_exists():
        if current is not None and str(current["role"]) == config.ADMIN_ROLE and not admin_email_is_verified(current):
            return update_admin_setup(request, current)
        if current is not None:
            return redirect_to(route_url(request, "welcome"))
        return redirect_to(route_url(request, "login"))
    return render(request, "setup_admin.html", setup_admin_context("", "", "", "", False, False))


@router.post("/setup-admin", response_class=HTMLResponse, name="setup_admin_post")
def setup_admin_post(
    request: Request,
    first_name: str = Form(""),
    last_name: str = Form(""),
    email: str = Form(""),
    email_verified: str = Form(""),
    password: str = Form(""),
    confirm_password: str = Form(""),
):
    """Create or verify the first admin account."""
    current = current_user(request)
    verified = email_verified == "true"
    if admin_exists():
        if current is not None and str(current["role"]) == config.ADMIN_ROLE and not admin_email_is_verified(current):
            return update_admin_setup(
                request,
                current,
                first_name=first_name,
                last_name=last_name,
                email=email,
                email_verified=verified,
                submitted=True,
            )
        if current is not None:
            return redirect_to(route_url(request, "welcome"))
        return redirect_to(route_url(request, "login"))

    normalized_first_name = normalize_name(first_name)
    normalized_last_name = normalize_name(last_name)
    normalized_email = normalize_email(email)
    message = ""
    if not normalized_first_name:
        message = "Enter the admin first name."
    elif not normalized_last_name:
        message = "Enter the admin last name."
    elif not normalized_email:
        message = "Enter an email address for the admin account."
    elif not email_is_valid(normalized_email):
        message = "Enter a valid email address."
    elif not password_is_valid(password):
        message = "Choose a password with at least 8 characters."
    elif password != confirm_password:
        message = "Passwords did not match. Please try again."
    else:
        user = create_user(
            normalized_email,
            password,
            role=config.ADMIN_ROLE,
            first_name=normalized_first_name,
            last_name=normalized_last_name,
            email_verified=verified,
        )
        if not set_registration_enabled(False):
            delete_user(int(user["id"]))
            message = "We couldn't finish admin setup right now. Please try again."
            return render(request, "setup_admin.html", setup_admin_context(message, normalized_email, normalized_first_name, normalized_last_name, verified, False))
        login_user(request, user)
        audit_auth_event("admin_bootstrap", normalized_email, "success", details="initial admin account created")
        return redirect_to(route_url(request, "admin_dashboard", message="Admin account created."))
    return render(request, "setup_admin.html", setup_admin_context(message, normalized_email, normalized_first_name, normalized_last_name, verified, False))


def admin_required(request: Request):
    """Redirect non-admin users away from admin routes."""
    if not current_user_is_admin(request):
        return redirect_to(route_url(request, "welcome"))
    return None


@router.get("/admin", response_class=HTMLResponse, name="admin_dashboard")
def admin_dashboard(request: Request):
    """Render admin registration settings."""
    guard = admin_required(request)
    if guard is not None:
        return guard
    return render(request, "admin.html", {"message": request.query_params.get("message", "")})


@router.post("/admin", response_class=HTMLResponse, name="admin_dashboard_post")
def admin_dashboard_post(request: Request, registration_enabled: str = Form("")):
    """Update admin registration settings."""
    guard = admin_required(request)
    if guard is not None:
        return guard
    message = "Registration settings updated." if set_registration_enabled(registration_enabled == "true") else "We couldn't update registration settings right now."
    return render(request, "admin.html", {"message": message})


@router.get("/admin/users", response_class=HTMLResponse, name="admin_users")
def admin_users_get(request: Request):
    """Render the admin user management page."""
    guard = admin_required(request)
    if guard is not None:
        return guard
    return render(request, "admin_users.html", {"message": request.query_params.get("message", ""), "users": list_users(), "roles": sorted(config.VALID_ROLES)})


@router.post("/admin/users", response_class=HTMLResponse, name="admin_users_post")
def admin_users_post(
    request: Request,
    action: str = Form(""),
    name: str = Form(""),
    email: str = Form(""),
    role: str = Form(config.USER_ROLE),
    user_id: str = Form(""),
):
    """Handle admin user creation, role, deletion, and reset actions."""
    guard = admin_required(request)
    if guard is not None:
        return guard

    target_name = normalize_name(name)
    target_email = normalize_email(email)
    target_role = role
    target_id = int(user_id) if user_id.isdigit() else None
    target_user = get_user_by_id(target_id)
    actor = current_user(request)
    actor_email = str(actor["email"]) if actor is not None else ""
    message = ""

    if action == "create_user":
        if not target_name:
            audit_auth_event("user_create", target_email, "failure", actor_email=actor_email, details="missing name")
            message = "Enter a name for the new user."
        elif not target_email:
            audit_auth_event("user_create", target_email, "failure", actor_email=actor_email, details="missing email")
            message = "Enter an email address for the new user."
        elif "@" not in target_email:
            audit_auth_event("user_create", target_email, "failure", actor_email=actor_email, details="invalid email")
            message = "Enter a valid email address."
        elif target_role not in config.VALID_ROLES:
            audit_auth_event("user_create", target_email, "failure", actor_email=actor_email, details="invalid role")
            message = "Choose a valid user role."
        elif get_user_by_email(target_email) is not None:
            audit_auth_event("user_create", target_email, "failure", actor_email=actor_email, details="email already exists")
            message = "That email address already belongs to an existing user."
        else:
            temporary_password = generate_temporary_password()
            create_user(target_email, temporary_password, role=target_role, name=target_name)
            audit_auth_event("user_create", target_email, "success", actor_email=actor_email, details=f"user created role={target_role}")
            try:
                reset_link = build_reset_link(request, generate_password_reset_token(target_email))
                send_password_reset_email(target_email, reset_link)
                audit_auth_event("password_reset_email", target_email, "success", actor_email=actor_email, details="new user setup email sent")
                message = f"Created {target_email} and sent a password setup email."
            except Exception:
                audit_auth_event("password_reset_email", target_email, "failure", actor_email=actor_email, details="new user setup email send failed")
                message = f"Created {target_email}, but the password setup email could not be sent."
    elif action == "delete_user" and target_user is not None:
        if str(target_user["role"]) == config.ADMIN_ROLE and admin_count() == 1:
            audit_auth_event("user_delete", str(target_user["email"]), "failure", actor_email=actor_email, details="cannot delete last admin")
            message = "You cannot delete the last admin account."
        else:
            delete_user(int(target_user["id"]))
            audit_auth_event("user_delete", str(target_user["email"]), "success", actor_email=actor_email, details="user deleted")
            message = f"Deleted {target_user['email']}."
    elif action == "update_role" and target_user is not None:
        if target_role not in config.VALID_ROLES:
            audit_auth_event("user_role_update", str(target_user["email"]), "failure", actor_email=actor_email, details="invalid role")
            message = "Choose a valid user role."
        elif str(target_user["role"]) == config.ADMIN_ROLE and target_role != config.ADMIN_ROLE and admin_count() == 1:
            audit_auth_event("user_role_update", str(target_user["email"]), "failure", actor_email=actor_email, details="cannot demote last admin")
            message = "You cannot demote the last admin account."
        else:
            update_user_role(int(target_user["id"]), target_role)
            audit_auth_event("user_role_update", str(target_user["email"]), "success", actor_email=actor_email, details=f"role updated to {target_role}")
            message = f"Updated {target_user['email']} to {target_role}."
    elif action == "send_reset" and target_user is not None:
        try:
            reset_link = build_reset_link(request, generate_password_reset_token(str(target_user["email"])))
            send_password_reset_email(str(target_user["email"]), reset_link)
            audit_auth_event("password_reset_email", str(target_user["email"]), "success", actor_email=actor_email, details="admin-triggered reset email sent")
            message = f"Sent a password reset email to {target_user['email']}."
        except Exception:
            audit_auth_event("password_reset_email", str(target_user["email"]), "failure", actor_email=actor_email, details="admin-triggered reset email send failed")
            message = f"Could not send a password reset email to {target_user['email']}."
    else:
        audit_auth_event("admin_user_action", target_email, "failure", actor_email=actor_email, details="invalid admin action")
        message = "The requested admin action could not be completed."

    return render(request, "admin_users.html", {"message": message, "users": list_users(), "roles": sorted(config.VALID_ROLES)})
