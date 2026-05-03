"""Authentication, session, and password reset token helpers."""

from __future__ import annotations

import hashlib
import secrets
import sqlite3

from fastapi import Request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app import config
from app.services.users import get_user_by_email, get_user_by_id, normalize_email
from app.state import get_app


class PasswordResetTokenError(RuntimeError):
    """Raised when password reset token operations fail."""


def email_audit_id(value: str) -> str:
    """Hash an email into a stable audit identifier."""
    normalized = normalize_email(value)
    if not normalized:
        return ""
    audit_salt = f"{get_app().secret_key}:{config.PASSWORD_RESET_SALT}".encode("utf-8")
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        normalized.encode("utf-8"),
        audit_salt,
        600_000,
    )
    return digest.hex()[:12]


def password_reset_serializer() -> URLSafeTimedSerializer:
    """Create the timed serializer for reset tokens."""
    return URLSafeTimedSerializer(get_app().secret_key)


def generate_password_reset_token(email: str) -> str:
    """Create a signed password reset token for an email."""
    try:
        return password_reset_serializer().dumps(normalize_email(email), salt=config.PASSWORD_RESET_SALT)
    except Exception as exc:
        raise PasswordResetTokenError("Unable to generate password reset token.") from exc


def verify_password_reset_token(token: str, *, max_age: int | None = None) -> sqlite3.Row | None:
    """Resolve a password reset token to a user row."""
    try:
        email = password_reset_serializer().loads(
            token,
            salt=config.PASSWORD_RESET_SALT,
            max_age=max_age or config.PASSWORD_RESET_MAX_AGE_SECONDS,
        )
    except (BadSignature, SignatureExpired, Exception):
        return None
    return get_user_by_email(str(email))


def current_user(request: Request) -> sqlite3.Row | None:
    """Load the user referenced by the request session."""
    return get_user_by_id(request.session.get(config.USER_SESSION_KEY))


def current_user_is_admin(request: Request) -> bool:
    """Return whether the request user is an admin."""
    user = current_user(request)
    return bool(user and str(user["role"]) == config.ADMIN_ROLE)


def admin_email_is_verified(user) -> bool:
    """Return whether an admin account has verified identity fields."""
    return bool(user and str(user["role"]) == config.ADMIN_ROLE and int(user["email_verified"] or 0) == 1)


def login_user(request: Request, user: sqlite3.Row) -> None:
    """Persist a user id in the request session."""
    request.session.clear()
    request.session[config.USER_SESSION_KEY] = int(user["id"])


def logout_current_user(request: Request) -> None:
    """Clear the request session."""
    request.session.clear()


def password_is_valid(password: str) -> bool:
    """Return whether a password satisfies local length rules."""
    return len(password) >= 8


def generate_temporary_password() -> str:
    """Generate a temporary password for account setup."""
    return secrets.token_urlsafe(18)
