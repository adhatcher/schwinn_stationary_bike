"""User account persistence and identity helpers."""

from __future__ import annotations

import sqlite3

from werkzeug.security import generate_password_hash

from app import config
from app.db import get_db_connection, get_setting, set_setting


def normalize_email(email: str | None) -> str:
    """Normalize an email address for storage and lookup."""
    if email is None:
        return ""
    return str(email).strip().lower()


def email_is_valid(email: str | None) -> bool:
    """Validate the email syntax accepted by account forms."""
    normalized = normalize_email(email)
    if not normalized or len(normalized) > 254:
        return False

    if normalized.count("@") != 1:
        return False

    local_part, domain = normalized.split("@", 1)
    if not local_part or not domain:
        return False
    if any(ch.isspace() for ch in normalized):
        return False
    if local_part.startswith(".") or local_part.endswith("."):
        return False
    if domain.startswith(".") or domain.endswith("."):
        return False
    if ".." in normalized:
        return False
    if "." not in domain:
        return False

    return True


def normalize_name(name: str) -> str:
    """Collapse whitespace around a display name."""
    return " ".join(name.strip().split())


def compose_full_name(first_name: str, last_name: str) -> str:
    """Build a normalized full name from first and last names."""
    return normalize_name(f"{normalize_name(first_name)} {normalize_name(last_name)}")


def display_user_name(user) -> str:
    """Return the best display name for a user row."""
    if user is None:
        return ""
    first_name = normalize_name(str(user["first_name"])) if "first_name" in user.keys() else ""
    last_name = normalize_name(str(user["last_name"])) if "last_name" in user.keys() else ""
    full_name = compose_full_name(first_name, last_name)
    if full_name:
        return full_name
    name = normalize_name(str(user["name"])) if "name" in user.keys() else ""
    return name or str(user["email"])


def get_user_by_email(email: str) -> sqlite3.Row | None:
    """Load a user row by normalized email address."""
    normalized = normalize_email(email)
    if not normalized:
        return None
    with get_db_connection() as connection:
        return connection.execute("SELECT * FROM users WHERE email = ?", (normalized,)).fetchone()


def get_user_by_id(user_id: int | None) -> sqlite3.Row | None:
    """Load a user row by numeric id."""
    if not user_id:
        return None
    with get_db_connection() as connection:
        return connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


def create_user(
    email: str,
    password: str,
    *,
    role: str = config.USER_ROLE,
    name: str = "",
    first_name: str = "",
    last_name: str = "",
    email_verified: bool = False,
) -> sqlite3.Row:
    """Create a user account and return the inserted row."""
    normalized = normalize_email(email)
    normalized_first_name = normalize_name(first_name)
    normalized_last_name = normalize_name(last_name)
    normalized_name = normalize_name(name) or compose_full_name(normalized_first_name, normalized_last_name)
    if role not in config.VALID_ROLES:
        raise ValueError(f"Unsupported role: {role}")
    password_hash = generate_password_hash(password)
    with get_db_connection() as connection:
        cursor = connection.execute(
            """
            INSERT INTO users (name, first_name, last_name, email, email_verified, password_hash, role)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                normalized_name,
                normalized_first_name,
                normalized_last_name,
                normalized,
                1 if email_verified else 0,
                password_hash,
                role,
            ),
        )
        connection.commit()
        user_id = int(cursor.lastrowid)
    user = get_user_by_id(user_id)
    if user is None:
        raise RuntimeError("Created user could not be loaded.")
    return user


def update_user_password(user_id: int, password: str) -> None:
    """Replace the stored password hash for a user."""
    password_hash = generate_password_hash(password)
    with get_db_connection() as connection:
        connection.execute("UPDATE users SET password_hash = ? WHERE id = ?", (password_hash, user_id))
        connection.commit()


def update_user_profile(user_id: int, *, name: str) -> None:
    """Update a user display name."""
    with get_db_connection() as connection:
        connection.execute("UPDATE users SET name = ? WHERE id = ?", (normalize_name(name), user_id))
        connection.commit()


def update_admin_identity(user_id: int, *, first_name: str, last_name: str, email: str, email_verified: bool) -> None:
    """Update the verified identity fields for an admin user."""
    normalized_first_name = normalize_name(first_name)
    normalized_last_name = normalize_name(last_name)
    full_name = compose_full_name(normalized_first_name, normalized_last_name)
    with get_db_connection() as connection:
        connection.execute(
            """
            UPDATE users
            SET first_name = ?, last_name = ?, name = ?, email = ?, email_verified = ?
            WHERE id = ?
            """,
            (normalized_first_name, normalized_last_name, full_name, normalize_email(email), 1 if email_verified else 0, user_id),
        )
        connection.commit()


def update_user_avatar(user_id: int, avatar_data: bytes, *, avatar_mime: str = config.AVATAR_MIME_TYPE) -> None:
    """Store resized avatar bytes for a user."""
    with get_db_connection() as connection:
        connection.execute(
            "UPDATE users SET avatar_data = ?, avatar_mime = ? WHERE id = ?",
            (avatar_data, avatar_mime, user_id),
        )
        connection.commit()


def update_user_role(user_id: int, role: str) -> None:
    """Change a user role after validation."""
    if role not in config.VALID_ROLES:
        raise ValueError(f"Unsupported role: {role}")
    with get_db_connection() as connection:
        connection.execute("UPDATE users SET role = ? WHERE id = ?", (role, user_id))
        connection.commit()


def delete_user(user_id: int) -> None:
    """Delete a user account by id."""
    with get_db_connection() as connection:
        connection.execute("DELETE FROM users WHERE id = ?", (user_id,))
        connection.commit()


def list_users() -> list[sqlite3.Row]:
    """List users sorted by display name or email."""
    with get_db_connection() as connection:
        return connection.execute("SELECT * FROM users ORDER BY COALESCE(NULLIF(name, ''), email) ASC").fetchall()


def is_registration_enabled() -> bool:
    """Return whether self-service registration is enabled."""
    return get_setting("registration_enabled", "false").lower() == "true"


def set_registration_enabled(enabled: bool) -> bool:
    """Persist the self-service registration setting."""
    return set_setting("registration_enabled", "true" if enabled else "false")


def admin_count() -> int:
    """Count currently configured admin users."""
    with get_db_connection() as connection:
        row = connection.execute("SELECT COUNT(*) AS count FROM users WHERE role = ?", (config.ADMIN_ROLE,)).fetchone()
    return int(row["count"]) if row else 0


def admin_exists() -> bool:
    """Return whether at least one admin user exists."""
    return admin_count() > 0
