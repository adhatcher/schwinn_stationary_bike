#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
import sqlite3
import smtplib
from contextlib import contextmanager
from email.message import EmailMessage
from io import BytesIO, StringIO
from logging.handlers import RotatingFileHandler
from pathlib import Path
from time import perf_counter
from typing import Iterable
from urllib.parse import urlencode

import pandas as pd
import plotly.graph_objects as go
from fastapi import File, Form, Request, Response, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from PIL import Image, UnidentifiedImageError
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from werkzeug.security import check_password_hash, generate_password_hash

ROOT_DIR = Path(__file__).resolve().parents[1]


def load_dotenv_file(dotenv_path: Path) -> None:
    if not dotenv_path.exists():
        return

    for raw_line in dotenv_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key or key in os.environ:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        os.environ[key] = value


load_dotenv_file(ROOT_DIR / ".env")

SESSION_COOKIE_SECURE = os.getenv("SESSION_COOKIE_SECURE", "").lower() in {"1", "true", "yes"}
secret_key = os.getenv("SECRET_KEY")
if not secret_key:
    secret_key = secrets.token_urlsafe(64)


def env_first(*names: str, default: str = "") -> str:
    for name in names:
        value = os.getenv(name)
        if value is not None and value.strip():
            return value.strip()
    return default

BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
DATA_DIR = Path(os.getenv("DATA_DIR", str(BASE_DIR / "data"))).resolve()
DAT_FILE = Path(os.getenv("DAT_FILE", str(DATA_DIR / "AARON.DAT"))).resolve()
HISTORY_FILE = Path(os.getenv("HISTORY_FILE", str(DATA_DIR / "Workout_History.csv"))).resolve()
AUTH_DB_FILE = Path(os.getenv("AUTH_DB_FILE", str(DATA_DIR / "users.db"))).resolve()
LOG_DIR = Path(os.getenv("LOG_DIR", str(BASE_DIR / "logs"))).resolve()
LOG_FILE = Path(os.getenv("LOG_FILE", str(LOG_DIR / "app.log"))).resolve()
PORT = int(os.getenv("PORT", "8080"))
HOST = os.getenv("HOST", "127.0.0.1")
PUBLIC_BASE_URL = env_first("PUBLIC_BASE_URL").rstrip("/")
SMTP_SECURE = env_first("SMTP_SECURE").lower()
MAIL_SERVER = env_first("MAIL_SERVER", "SMTP_HOST")
MAIL_PORT = int(env_first("MAIL_PORT", "SMTP_PORT", default="587"))
MAIL_USERNAME = env_first("MAIL_USERNAME", "SMTP_NAME")
MAIL_PASSWORD = env_first("MAIL_PASSWORD", "SMTP_PASSWORD")
MAIL_USE_TLS = env_first("MAIL_USE_TLS").lower() in {"1", "true", "yes"} or SMTP_SECURE == "tls"
MAIL_USE_SSL = env_first("MAIL_USE_SSL").lower() in {"1", "true", "yes"} or SMTP_SECURE in {"ssl", "smtps"}
MAIL_FROM = env_first("MAIL_FROM", "MAIL_FROM_ADDRESS", default=MAIL_USERNAME or "no-reply@schwinn.local")
PASSWORD_RESET_SALT = env_first("PASSWORD_RESET_SALT", default="schwinn-password-reset-salt")
PASSWORD_RESET_MAX_AGE_SECONDS = int(os.getenv("PASSWORD_RESET_MAX_AGE_SECONDS", "3600"))
USER_SESSION_KEY = "user_id"
AVATAR_SIZE_MIN_PX = 48
AVATAR_SIZE_DEFAULT_PX = 96
AVATAR_SIZE_MAX_PX = 256
AVATAR_UPLOAD_MAX_BYTES = 2 * 1024 * 1024
AVATAR_MIME_TYPE = "image/webp"
PUBLIC_ENDPOINTS = {
    "healthz",
    "metrics",
    "setup_admin",
    "login",
    "register",
    "forgot_password",
    "reset_password",
    "logout",
    "static",
}
BOOTSTRAP_ALLOWED_ENDPOINTS = {"healthz", "metrics", "setup_admin", "static"}
ADMIN_ROLE = "admin"
USER_ROLE = "user"
VALID_ROLES = {ADMIN_ROLE, USER_ROLE}



class PasswordResetTokenError(RuntimeError):
    pass

COLUMN_NAMES = [
    "Workout_Date",
    "Distance",
    "Avg_Speed",
    "Workout_Time",
    "Total_Calories",
    "Heart_Rate",
    "RPM",
    "Level",
]
GRAPHABLE_FIELDS = [name for name in COLUMN_NAMES if name != "Workout_Date"]
DAT_IMPORT_ERROR_MESSAGE = "Unable to parse the uploaded DAT file. Check the file contents and try again."
HISTORY_IMPORT_ERROR_MESSAGE = "Unable to import the historical CSV file. Verify the file format and try again."

from app.bootstrap.metrics import (
    AUTH_EVENT_COUNT,
    CHART_GENERATION_LATENCY,
    CHARTS_GENERATED,
    FILE_IMPORT_LATENCY,
    REQUEST_COUNT,
    REQUEST_LATENCY,
)


def configure_logging() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = RotatingFileHandler(
        LOG_FILE,
        maxBytes=100 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)

    app.logger.setLevel(logging.INFO)
    app.logger.handlers = []
    app.logger.addHandler(file_handler)
    app.logger.addHandler(stream_handler)
    app.logger.propagate = False

    werkzeug_logger = logging.getLogger("werkzeug")
    werkzeug_logger.setLevel(logging.INFO)
    werkzeug_logger.handlers = [file_handler, stream_handler]
    werkzeug_logger.propagate = False



def audit_auth_event(action: str, email: str, result: str, *, actor_email: str = "", details: str = "") -> None:
    AUTH_EVENT_COUNT.labels(action, result).inc()
    subject_id = email_audit_id(email)
    actor_id = email_audit_id(actor_email)
    log_method = app.logger.info if result == "success" else app.logger.warning
    log_method(
        "auth_event action=%s result=%s subject_id=%s actor_id=%s details=%s",
        action,
        result,
        subject_id,
        actor_id,
        details,
    )


@contextmanager
def get_db_connection():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(AUTH_DB_FILE)
    connection.row_factory = sqlite3.Row
    try:
        yield connection
    finally:
        connection.close()


def init_auth_db() -> None:
    with get_db_connection() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL DEFAULT '',
                first_name TEXT NOT NULL DEFAULT '',
                last_name TEXT NOT NULL DEFAULT '',
                email TEXT NOT NULL UNIQUE,
                email_verified INTEGER NOT NULL DEFAULT 0,
                password_hash TEXT NOT NULL,
                avatar_data BLOB,
                avatar_mime TEXT NOT NULL DEFAULT '',
                role TEXT NOT NULL DEFAULT 'user',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        existing_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(users)").fetchall()
        }
        if "name" not in existing_columns:
            connection.execute("ALTER TABLE users ADD COLUMN name TEXT NOT NULL DEFAULT ''")
        if "first_name" not in existing_columns:
            connection.execute("ALTER TABLE users ADD COLUMN first_name TEXT NOT NULL DEFAULT ''")
        if "last_name" not in existing_columns:
            connection.execute("ALTER TABLE users ADD COLUMN last_name TEXT NOT NULL DEFAULT ''")
        if "email_verified" not in existing_columns:
            connection.execute("ALTER TABLE users ADD COLUMN email_verified INTEGER NOT NULL DEFAULT 0")
        if "avatar_data" not in existing_columns:
            connection.execute("ALTER TABLE users ADD COLUMN avatar_data BLOB")
        if "avatar_mime" not in existing_columns:
            connection.execute("ALTER TABLE users ADD COLUMN avatar_mime TEXT NOT NULL DEFAULT ''")
        if "role" not in existing_columns:
            connection.execute("ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'user'")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        connection.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
            ("registration_enabled", "false"),
        )
        connection.commit()


def normalize_email(email: str | None) -> str:
    if email is None:
        return ""
    return str(email).strip().lower()


def email_is_valid(email: str | None) -> bool:
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
    return " ".join(name.strip().split())


def compose_full_name(first_name: str, last_name: str) -> str:
    return normalize_name(f"{normalize_name(first_name)} {normalize_name(last_name)}")


def display_user_name(user) -> str:
    if user is None:
        return ""
    first_name = normalize_name(str(user["first_name"])) if "first_name" in user.keys() else ""
    last_name = normalize_name(str(user["last_name"])) if "last_name" in user.keys() else ""
    full_name = compose_full_name(first_name, last_name)
    if full_name:
        return full_name
    name = normalize_name(str(user["name"])) if "name" in user.keys() else ""
    return name or str(user["email"])


def user_initials(user) -> str:
    display_name = display_user_name(user)
    words = [word for word in display_name.replace("@", " ").replace(".", " ").split() if word]
    if not words:
        return "U"
    if len(words) == 1:
        return words[0][:2].upper()
    return f"{words[0][0]}{words[-1][0]}".upper()


def user_has_avatar(user) -> bool:
    return bool(user is not None and "avatar_data" in user.keys() and user["avatar_data"])


def parse_avatar_size(raw_size: str) -> int:
    try:
        requested_size = int(raw_size)
    except (TypeError, ValueError):
        requested_size = AVATAR_SIZE_DEFAULT_PX
    return max(AVATAR_SIZE_MIN_PX, min(requested_size, AVATAR_SIZE_MAX_PX))


async def process_avatar_upload(upload_file: UploadFile | None, *, requested_size: int = AVATAR_SIZE_DEFAULT_PX) -> bytes:
    if not upload_file or not upload_file.filename:
        raise ValueError("Choose an image file to upload.")
    upload_bytes = await upload_file.read()
    if not upload_bytes:
        raise ValueError("Choose an image file to upload.")
    if len(upload_bytes) > AVATAR_UPLOAD_MAX_BYTES:
        raise ValueError("Profile images must be 2 MB or smaller.")

    size = parse_avatar_size(str(requested_size))
    try:
        with Image.open(BytesIO(upload_bytes)) as image:
            image = image.convert("RGB")
            image.thumbnail((size, size), Image.Resampling.LANCZOS)
            canvas = Image.new("RGB", (size, size), (255, 255, 255))
            left = (size - image.width) // 2
            top = (size - image.height) // 2
            canvas.paste(image, (left, top))
            output = BytesIO()
            canvas.save(output, format="WEBP", quality=78, method=6)
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError("Upload a PNG, JPEG, GIF, or WebP image.") from exc

    return output.getvalue()


def mask_email(email: str) -> str:
    normalized = normalize_email(email)
    if not normalized or "@" not in normalized:
        return ""
    local_part, domain = normalized.split("@", 1)
    if not local_part:
        return f"***@{domain}"
    if len(local_part) == 1:
        masked_local = "*"
    elif len(local_part) == 2:
        masked_local = f"{local_part[0]}*"
    else:
        masked_local = f"{local_part[0]}***{local_part[-1]}"
    return f"{masked_local}@{domain}"


def email_audit_id(value: str) -> str:
    normalized = normalize_email(value)
    if not normalized:
        return ""
    audit_salt = f"{app.secret_key}:{PASSWORD_RESET_SALT}".encode("utf-8")
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        normalized.encode("utf-8"),
        audit_salt,
        600_000,
    )
    return digest.hex()[:12]


def get_user_by_email(email: str) -> sqlite3.Row | None:
    normalized = normalize_email(email)
    if not normalized:
        return None
    with get_db_connection() as connection:
        return connection.execute("SELECT * FROM users WHERE email = ?", (normalized,)).fetchone()


def get_user_by_id(user_id: int | None) -> sqlite3.Row | None:
    if not user_id:
        return None
    with get_db_connection() as connection:
        return connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


def create_user(
    email: str,
    password: str,
    *,
    role: str = USER_ROLE,
    name: str = "",
    first_name: str = "",
    last_name: str = "",
    email_verified: bool = False,
) -> sqlite3.Row:
    normalized = normalize_email(email)
    normalized_first_name = normalize_name(first_name)
    normalized_last_name = normalize_name(last_name)
    normalized_name = normalize_name(name) or compose_full_name(normalized_first_name, normalized_last_name)
    if role not in VALID_ROLES:
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
    password_hash = generate_password_hash(password)
    with get_db_connection() as connection:
        connection.execute("UPDATE users SET password_hash = ? WHERE id = ?", (password_hash, user_id))
        connection.commit()


def update_user_profile(user_id: int, *, name: str) -> None:
    with get_db_connection() as connection:
        connection.execute("UPDATE users SET name = ? WHERE id = ?", (normalize_name(name), user_id))
        connection.commit()


def update_admin_identity(user_id: int, *, first_name: str, last_name: str, email: str, email_verified: bool) -> None:
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


def update_user_avatar(user_id: int, avatar_data: bytes, *, avatar_mime: str = AVATAR_MIME_TYPE) -> None:
    with get_db_connection() as connection:
        connection.execute(
            "UPDATE users SET avatar_data = ?, avatar_mime = ? WHERE id = ?",
            (avatar_data, avatar_mime, user_id),
        )
        connection.commit()


def update_user_role(user_id: int, role: str) -> None:
    if role not in VALID_ROLES:
        raise ValueError(f"Unsupported role: {role}")
    with get_db_connection() as connection:
        connection.execute("UPDATE users SET role = ? WHERE id = ?", (role, user_id))
        connection.commit()


def delete_user(user_id: int) -> None:
    with get_db_connection() as connection:
        connection.execute("DELETE FROM users WHERE id = ?", (user_id,))
        connection.commit()


def list_users() -> list[sqlite3.Row]:
    with get_db_connection() as connection:
        return connection.execute("SELECT * FROM users ORDER BY COALESCE(NULLIF(name, ''), email) ASC").fetchall()


def get_setting(key: str, default: str = "") -> str:
    with get_db_connection() as connection:
        row = connection.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    if row is None:
        return default
    return str(row["value"])


def set_setting(key: str, value: str) -> bool:
    try:
        with get_db_connection() as connection:
            connection.execute(
                """
                INSERT INTO settings (key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (key, value),
            )
            connection.commit()
    except sqlite3.Error:
        app.logger.warning("Settings update failed: key=%s", key)
        return False
    return True


def is_registration_enabled() -> bool:
    return get_setting("registration_enabled", "false").lower() == "true"


def set_registration_enabled(enabled: bool) -> bool:
    return set_setting("registration_enabled", "true" if enabled else "false")


def admin_count() -> int:
    with get_db_connection() as connection:
        row = connection.execute("SELECT COUNT(*) AS count FROM users WHERE role = ?", (ADMIN_ROLE,)).fetchone()
    return int(row["count"]) if row else 0


def admin_exists() -> bool:
    return admin_count() > 0


def password_reset_serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(app.secret_key)


def generate_password_reset_token(email: str) -> str:
    try:
        return password_reset_serializer().dumps(normalize_email(email), salt=PASSWORD_RESET_SALT)
    except Exception as exc:
        raise PasswordResetTokenError("Unable to generate password reset token.") from exc


def verify_password_reset_token(token: str, *, max_age: int | None = None) -> sqlite3.Row | None:
    try:
        email = password_reset_serializer().loads(
            token,
            salt=PASSWORD_RESET_SALT,
            max_age=max_age or PASSWORD_RESET_MAX_AGE_SECONDS,
        )
    except (BadSignature, SignatureExpired, Exception):
        return None
    return get_user_by_email(str(email))


def current_user(request: Request) -> sqlite3.Row | None:
    return get_user_by_id(request.session.get(USER_SESSION_KEY))


def current_user_is_admin(request: Request) -> bool:
    user = current_user(request)
    return bool(user and str(user["role"]) == ADMIN_ROLE)


def admin_email_is_verified(user) -> bool:
    return bool(user and str(user["role"]) == ADMIN_ROLE and int(user["email_verified"] or 0) == 1)


def login_user(request: Request, user: sqlite3.Row) -> None:
    request.session.clear()
    request.session[USER_SESSION_KEY] = int(user["id"])


def logout_current_user(request: Request) -> None:
    request.session.clear()


def password_is_valid(password: str) -> bool:
    return len(password) >= 8


def generate_temporary_password() -> str:
    return secrets.token_urlsafe(18)


def send_password_reset_email(email: str, reset_link: str) -> None:
    message = EmailMessage()
    message["Subject"] = "Reset your Schwinn password"
    message["From"] = MAIL_FROM
    message["To"] = email
    message.set_content(
        "\n".join(
            [
                "A password reset was requested for your Schwinn account.",
                "",
                f"Reset your password using this link: {reset_link}",
                "",
                f"This link expires in {PASSWORD_RESET_MAX_AGE_SECONDS // 60} minutes.",
                "If you did not request a reset, you can ignore this email.",
            ]
        )
    )

    if not MAIL_SERVER:
        app.logger.info(
            "Password reset email not sent because MAIL_SERVER is not configured: recipient_id=%s",
            email_audit_id(email),
        )
        return

    if MAIL_USE_SSL:
        smtp = smtplib.SMTP_SSL(MAIL_SERVER, MAIL_PORT, timeout=10)
    else:
        smtp = smtplib.SMTP(MAIL_SERVER, MAIL_PORT, timeout=10)

    with smtp:
        if MAIL_USE_TLS and not MAIL_USE_SSL:
            smtp.starttls()
        if MAIL_USERNAME:
            smtp.login(MAIL_USERNAME, MAIL_PASSWORD)
        smtp.send_message(message)


def build_reset_link(request: Request | str, token: str | None = None) -> str:
    if token is None:
        token = str(request)
        if PUBLIC_BASE_URL:
            return f"{PUBLIC_BASE_URL}/reset-password/{token}"
        raise PasswordResetTokenError("Request is required when PUBLIC_BASE_URL is not configured.")
    reset_path = request.url_for("reset_password", token=token).path
    if PUBLIC_BASE_URL:
        return f"{PUBLIC_BASE_URL}{reset_path}"
    return str(request.url_for("reset_password", token=token))


def render(
    request: Request,
    template_name: str,
    context: dict[str, object] | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    page_context = dict(context or {})
    page_context.update(
        {
            "request": request,
            "current_user": current_user(request),
            "display_user_name": display_user_name,
            "user_has_avatar": user_has_avatar,
            "user_initials": user_initials,
            "registration_enabled": is_registration_enabled(),
            "admin_exists": admin_exists(),
        }
    )
    return templates.TemplateResponse(request, template_name, page_context, status_code=status_code)


def redirect_to(url: str, status_code: int = 302) -> RedirectResponse:
    return RedirectResponse(url=url, status_code=status_code)


def route_url(request: Request, name: str, **params: object) -> str:
    path = request.url_for(name).path
    clean_params = {key: value for key, value in params.items() if value is not None}
    return f"{path}?{urlencode(clean_params, safe='@/')}" if clean_params else path


def extract_json_objects(payload: str) -> list[dict]:
    decoder = json.JSONDecoder()
    workouts: list[dict] = []
    cursor = 0

    while True:
        start = payload.find("{", cursor)
        if start == -1:
            break
        try:
            obj, consumed = decoder.raw_decode(payload[start:])
        except json.JSONDecodeError:
            cursor = start + 1
            continue
        if isinstance(obj, dict):
            workouts.append(obj)
        cursor = start + consumed

    return workouts


def parse_dat_payload(raw_text: str) -> list[dict]:
    payload = "\n".join(raw_text.splitlines()[8:])
    workouts = extract_json_objects(payload)
    if not workouts:
        raise ValueError("No workout objects found in DAT file.")
    return workouts


def load_workout_data(workout_json: Iterable[dict]) -> pd.DataFrame:
    rows = []
    for workout_dict in workout_json:
        workout_date = (
            f"{workout_dict['workoutDate']['Month']}/"
            f"{workout_dict['workoutDate']['Day']}/"
            f"{workout_dict['workoutDate']['Year']}"
        )
        total_minutes = (
            int(workout_dict["totalWorkoutTime"]["Hours"]) * 60
            + int(workout_dict["totalWorkoutTime"]["Minutes"])
        )
        rows.append(
            [
                workout_date,
                workout_dict["distance"],
                workout_dict["averageSpeed"],
                total_minutes,
                workout_dict["totalCalories"],
                workout_dict["avgHeartRate"],
                workout_dict["avgRpm"],
                workout_dict["avgLevel"],
            ]
        )

    df_table = pd.DataFrame(rows, columns=COLUMN_NAMES)
    df_table["Workout_Date"] = pd.to_datetime(df_table["Workout_Date"], errors="coerce")
    return df_table.dropna(subset=["Workout_Date"]).reset_index(drop=True)


def load_history_file(history_file: Path) -> pd.DataFrame:
    if not history_file.exists() or history_file.stat().st_size == 0:
        return pd.DataFrame(columns=COLUMN_NAMES)

    history_df = pd.read_csv(history_file)
    if history_df.empty:
        return pd.DataFrame(columns=COLUMN_NAMES)

    history_df["Workout_Date"] = pd.to_datetime(history_df["Workout_Date"], errors="coerce")
    history_df = history_df.dropna(subset=["Workout_Date"])

    for field in GRAPHABLE_FIELDS:
        if field in history_df.columns:
            history_df[field] = pd.to_numeric(history_df[field], errors="coerce")

    return history_df[COLUMN_NAMES].sort_values(by=["Workout_Date"]).reset_index(drop=True)


def merge_data(new_data: pd.DataFrame, historical_data: pd.DataFrame) -> pd.DataFrame:
    if historical_data.empty:
        return new_data.sort_values(by=["Workout_Date"]).reset_index(drop=True)
    if new_data.empty:
        return historical_data.sort_values(by=["Workout_Date"]).reset_index(drop=True)

    combined_file = pd.concat([historical_data, new_data], ignore_index=True)
    sorted_file = combined_file.sort_values(by=["Workout_Date"]).reset_index(drop=True)
    merged = sorted_file.drop_duplicates(subset=["Workout_Date", "Workout_Time"], keep="last")
    return merged.reset_index(drop=True)


def save_history(data: pd.DataFrame, history_file: Path) -> None:
    history_file.parent.mkdir(parents=True, exist_ok=True)
    data.to_csv(history_file, index=False)


def read_dat_from_disk(dat_file: Path) -> pd.DataFrame:
    raw_text = dat_file.read_text(encoding="utf-8", errors="ignore")
    return load_workout_data(parse_dat_payload(raw_text))


async def read_dat_from_upload(upload_file: UploadFile) -> pd.DataFrame:
    raw_bytes = await upload_file.read()
    raw_text = raw_bytes.decode("utf-8", errors="ignore")
    return load_workout_data(parse_dat_payload(raw_text))


async def read_history_csv_from_upload(upload_file: UploadFile) -> pd.DataFrame:
    upload_df = pd.read_csv(BytesIO(await upload_file.read()))
    missing_columns = [col for col in COLUMN_NAMES if col not in upload_df.columns]
    if missing_columns:
        raise ValueError(f"Missing required columns in historical CSV: {', '.join(missing_columns)}")

    history_df = upload_df[COLUMN_NAMES].copy()
    history_df["Workout_Date"] = pd.to_datetime(history_df["Workout_Date"], errors="coerce")
    history_df = history_df.dropna(subset=["Workout_Date"])

    for field in GRAPHABLE_FIELDS:
        history_df[field] = pd.to_numeric(history_df[field], errors="coerce")

    return history_df.sort_values(by=["Workout_Date"]).reset_index(drop=True)


def filter_data(df: pd.DataFrame, start_date: str, end_date: str) -> pd.DataFrame:
    filtered = df.copy()

    if start_date:
        filtered = filtered[filtered["Workout_Date"] >= pd.to_datetime(start_date)]
    if end_date:
        filtered = filtered[filtered["Workout_Date"] <= pd.to_datetime(end_date)]

    return filtered.sort_values(by=["Workout_Date"]).reset_index(drop=True)


def current_day() -> pd.Timestamp:
    return pd.Timestamp.now().normalize()


def summarize_window(df: pd.DataFrame, *, days: int, today: pd.Timestamp | None = None) -> dict[str, float | int]:
    if today is None:
        today = current_day()
    else:
        today = pd.to_datetime(today).normalize()

    if df.empty:
        return {"workout_count": 0, "distance": 0.0, "workout_time": 0}

    window_start = today - pd.Timedelta(days=max(days - 1, 0))
    window_df = df[(df["Workout_Date"] >= window_start) & (df["Workout_Date"] <= today)]
    return {
        "workout_count": int(len(window_df)),
        "distance": float(pd.to_numeric(window_df["Distance"], errors="coerce").fillna(0).sum()),
        "workout_time": int(pd.to_numeric(window_df["Workout_Time"], errors="coerce").fillna(0).sum()),
    }


def format_distance(value: float) -> str:
    formatted = f"{value:.1f}"
    if "." in formatted:
        formatted = formatted.rstrip("0").rstrip(".")
    return formatted


def format_minutes(total_minutes: int) -> str:
    hours, minutes = divmod(int(total_minutes), 60)
    if hours and minutes:
        return f"{hours}h {minutes}m"
    if hours:
        return f"{hours}h"
    return f"{minutes}m"


def build_summary_cards(df: pd.DataFrame) -> dict[str, dict[str, str]]:
    today = current_day()
    last_30 = summarize_window(df, days=30, today=today)
    last_year = summarize_window(df, days=365, today=today)

    return {
        "last_30_days": {
            "workouts": str(last_30["workout_count"]),
            "distance": format_distance(last_30["distance"]),
            "time": format_minutes(last_30["workout_time"]),
        },
        "last_year": {
            "workouts": str(last_year["workout_count"]),
            "distance": format_distance(last_year["distance"]),
            "time": format_minutes(last_year["workout_time"]),
        },
    }


def build_page_context(historical_data: pd.DataFrame) -> dict[str, object]:
    min_date = ""
    max_date = ""
    last_workout_date = ""
    days_since_last_workout = None

    if not historical_data.empty:
        min_ts = historical_data["Workout_Date"].min()
        max_ts = historical_data["Workout_Date"].max()
        min_date = min_ts.date().isoformat()
        max_date = max_ts.date().isoformat()
        last_workout_date = max_ts.date().isoformat()
        days_since_last_workout = int((current_day() - max_ts.normalize()).days)

    return {
        "history_file": HISTORY_FILE,
        "dat_file": DAT_FILE,
        "historical_count": len(historical_data),
        "min_date": min_date,
        "max_date": max_date,
        "last_workout_date": last_workout_date,
        "days_since_last_workout": days_since_last_workout,
        "summary_cards": build_summary_cards(historical_data),
    }


def parse_field_selection(args) -> list[str]:
    requested_fields: list[str] = []

    for field in args.getlist("field"):
        if field:
            requested_fields.append(field.strip())

    for field in args.getlist("field[]"):
        if field:
            requested_fields.append(field.strip())

    comma_fields = args.get("fields", "")
    if comma_fields:
        for field in comma_fields.split(","):
            cleaned = field.strip()
            if cleaned:
                requested_fields.append(cleaned)

    if not requested_fields:
        return list(GRAPHABLE_FIELDS)

    deduped_fields: list[str] = []
    for field in requested_fields:
        cleaned = field.strip().strip("'\"")
        if cleaned and cleaned not in deduped_fields:
            deduped_fields.append(cleaned)

    invalid_fields = [field for field in deduped_fields if field not in GRAPHABLE_FIELDS]
    if invalid_fields:
        raise ValueError(f"Unsupported field(s): {', '.join(invalid_fields)}")

    return deduped_fields


def build_chart(df: pd.DataFrame, fields: list[str]) -> str | None:
    if df.empty or not fields:
        return None

    start = perf_counter()
    fig = go.Figure()
    for field in fields:
        fig.add_trace(
            go.Scatter(
                x=df["Workout_Date"],
                y=df[field],
                mode="lines+markers",
                name=field,
            )
        )

    fig.update_layout(
        title="Schwinn Workout Performance",
        xaxis_title="Workout Date",
        yaxis_title="Value",
        height=692,
        hovermode="x unified",
        template="plotly_white",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )

    chart_html = fig.to_html(full_html=False, include_plotlyjs="cdn")
    CHARTS_GENERATED.inc()
    CHART_GENERATION_LATENCY.observe(perf_counter() - start)
    return chart_html


PUBLIC_PATHS = {
    "/healthz",
    "/metrics",
    "/setup-admin",
    "/login",
    "/register",
    "/forgot-password",
    "/logout",
}
BOOTSTRAP_ALLOWED_PATHS = {"/healthz", "/metrics", "/setup-admin"}


async def auth_and_metrics_middleware(request: Request, call_next):
    request.state.request_start = perf_counter()
    if "PYTEST_CURRENT_TEST" in os.environ and "x-test-user-id" in request.headers:
        request.session[USER_SESSION_KEY] = int(request.headers["x-test-user-id"])

    init_auth_db()
    path = request.url.path

    if not admin_exists() and path not in BOOTSTRAP_ALLOWED_PATHS and not path.startswith("/static"):
        response = redirect_to(route_url(request, "setup_admin"))
    elif path.startswith("/static") or path in PUBLIC_PATHS or path.startswith("/reset-password/"):
        response = await call_next(request)
    elif current_user(request) is not None:
        response = await call_next(request)
    elif path.startswith("/api/"):
        response = JSONResponse({"error": "Authentication required."}, status_code=401)
    else:
        next_path = path
        if request.url.query:
            next_path = f"{next_path}?{request.url.query}"
        response = redirect_to(route_url(request, "login", next=next_path))

    endpoint = request.scope.get("endpoint")
    endpoint_name = getattr(endpoint, "__name__", path)
    REQUEST_COUNT.labels(request.method, endpoint_name, str(response.status_code)).inc()
    REQUEST_LATENCY.labels(request.method, endpoint_name).observe(perf_counter() - request.state.request_start)
    return response



def healthz():
    return {"status": "ok"}


def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


def login_get(request: Request):
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


def login_post(request: Request, email: str = Form(""), password: str = Form("")):
    if not admin_exists():
        return redirect_to(route_url(request, "setup_admin"))
    if current_user(request) is not None:
        return redirect_to(route_url(request, "welcome"))

    normalized_email = normalize_email(email)
    user = get_user_by_email(normalized_email)
    if user and check_password_hash(str(user["password_hash"]), password):
        login_user(request, user)
        audit_auth_event("login", normalized_email, "success", details="user signed in")
        if str(user["role"]) == ADMIN_ROLE and not admin_email_is_verified(user):
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


def register_get(request: Request):
    if not admin_exists():
        return redirect_to(route_url(request, "setup_admin"))
    if current_user(request) is not None:
        return redirect_to(route_url(request, "welcome"))
    if not is_registration_enabled():
        return render(request, "register.html", {"message": "New user registration is currently disabled.", "email": "", "name": ""}, status_code=403)
    return render(request, "register.html", {"message": "", "email": "", "name": ""})


def register_post(
    request: Request,
    name: str = Form(""),
    email: str = Form(""),
    password: str = Form(""),
    confirm_password: str = Form(""),
):
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
        user = create_user(normalized_email, password, role=USER_ROLE, name=normalized_name)
        login_user(request, user)
        return redirect_to(route_url(request, "welcome"))
    return render(request, "register.html", {"message": message, "email": normalized_email, "name": normalized_name})


def forgot_password_get(request: Request):
    if not admin_exists():
        return redirect_to(route_url(request, "setup_admin"))
    return render(request, "forgot_password.html", {"message": "", "email": ""})


def forgot_password_post(request: Request, email: str = Form("")):
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


def reset_password_get(request: Request, token: str):
    if not admin_exists():
        return redirect_to(route_url(request, "setup_admin"))
    user = verify_password_reset_token(token)
    if user is None:
        audit_auth_event("password_reset", "", "failure", details="invalid or expired reset token")
        return render(request, "reset_password.html", {"message": "That password reset link is invalid or has expired.", "token": token, "reset_link_valid": False})
    return render(request, "reset_password.html", {"message": "", "token": token, "reset_link_valid": True, "reset_email": str(user["email"])})


def reset_password_post(
    request: Request,
    token: str,
    password: str = Form(""),
    confirm_password: str = Form(""),
):
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


def logout(request: Request):
    user = current_user(request)
    logout_current_user(request)
    if user is not None:
        audit_auth_event("logout", str(user["email"]), "success", details="user signed out")
    return redirect_to(route_url(request, "setup_admin" if not admin_exists() else "login", message="You have been signed out."))


def account_avatar(request: Request):
    user = current_user(request)
    if user is None or not user_has_avatar(user):
        return Response(status_code=404)
    return Response(
        bytes(user["avatar_data"]),
        media_type=str(user["avatar_mime"] or AVATAR_MIME_TYPE),
        headers={"Cache-Control": "private, max-age=300"},
    )


def account_context(message: str = "", message_type: str = "", avatar_size: int = AVATAR_SIZE_DEFAULT_PX) -> dict[str, object]:
    return {
        "message": message,
        "message_type": message_type,
        "avatar_size": avatar_size,
        "avatar_size_min": AVATAR_SIZE_MIN_PX,
        "avatar_size_max": AVATAR_SIZE_MAX_PX,
    }


def account_get(request: Request):
    return render(
        request,
        "account.html",
        account_context(request.query_params.get("message", ""), "success" if request.query_params.get("message", "") else ""),
    )


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
    user = current_user(request)
    if user is None:
        return redirect_to(route_url(request, "login"))
    message = ""
    message_type = "error"
    parsed_avatar_size = AVATAR_SIZE_DEFAULT_PX
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


def setup_admin_context(message: str, email: str, first_name: str, last_name: str, email_verified: bool, existing_admin: bool) -> dict[str, object]:
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


def setup_admin_get(request: Request):
    current = current_user(request)
    if admin_exists():
        if current is not None and str(current["role"]) == ADMIN_ROLE and not admin_email_is_verified(current):
            return update_admin_setup(request, current)
        if current is not None:
            return redirect_to(route_url(request, "welcome"))
        return redirect_to(route_url(request, "login"))
    return render(request, "setup_admin.html", setup_admin_context("", "", "", "", False, False))


def setup_admin_post(
    request: Request,
    first_name: str = Form(""),
    last_name: str = Form(""),
    email: str = Form(""),
    email_verified: str = Form(""),
    password: str = Form(""),
    confirm_password: str = Form(""),
):
    current = current_user(request)
    verified = email_verified == "true"
    if admin_exists():
        if current is not None and str(current["role"]) == ADMIN_ROLE and not admin_email_is_verified(current):
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
            role=ADMIN_ROLE,
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
    if not current_user_is_admin(request):
        return redirect_to(route_url(request, "welcome"))
    return None


def admin_dashboard(request: Request):
    guard = admin_required(request)
    if guard is not None:
        return guard
    return render(request, "admin.html", {"message": request.query_params.get("message", "")})


def admin_dashboard_post(request: Request, registration_enabled: str = Form("")):
    guard = admin_required(request)
    if guard is not None:
        return guard
    message = "Registration settings updated." if set_registration_enabled(registration_enabled == "true") else "We couldn't update registration settings right now."
    return render(request, "admin.html", {"message": message})


def admin_users_get(request: Request):
    guard = admin_required(request)
    if guard is not None:
        return guard
    return render(request, "admin_users.html", {"message": request.query_params.get("message", ""), "users": list_users(), "roles": sorted(VALID_ROLES)})


def admin_users_post(
    request: Request,
    action: str = Form(""),
    name: str = Form(""),
    email: str = Form(""),
    role: str = Form(USER_ROLE),
    user_id: str = Form(""),
):
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
        elif target_role not in VALID_ROLES:
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
        if str(target_user["role"]) == ADMIN_ROLE and admin_count() == 1:
            audit_auth_event("user_delete", str(target_user["email"]), "failure", actor_email=actor_email, details="cannot delete last admin")
            message = "You cannot delete the last admin account."
        else:
            delete_user(int(target_user["id"]))
            audit_auth_event("user_delete", str(target_user["email"]), "success", actor_email=actor_email, details="user deleted")
            message = f"Deleted {target_user['email']}."
    elif action == "update_role" and target_user is not None:
        if target_role not in VALID_ROLES:
            audit_auth_event("user_role_update", str(target_user["email"]), "failure", actor_email=actor_email, details="invalid role")
            message = "Choose a valid user role."
        elif str(target_user["role"]) == ADMIN_ROLE and target_role != ADMIN_ROLE and admin_count() == 1:
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

    return render(request, "admin_users.html", {"message": message, "users": list_users(), "roles": sorted(VALID_ROLES)})


def download_history():
    history_df = load_history_file(HISTORY_FILE)
    buffer = StringIO()
    history_df.to_csv(buffer, index=False)
    return Response(
        buffer.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=Workout_History.csv"},
    )


def grafana_workouts(request: Request):
    start_date = request.query_params.get("from", "") or request.query_params.get("start_date", "")
    end_date = request.query_params.get("to", "") or request.query_params.get("end_date", "")
    try:
        selected_fields = parse_field_selection(request.query_params)
    except ValueError:
        return JSONResponse({"error": "Unsupported field selection.", "allowed_fields": GRAPHABLE_FIELDS}, status_code=400)

    filtered_data = filter_data(load_history_file(HISTORY_FILE), start_date, end_date)
    points: list[dict] = []
    for _, row in filtered_data.iterrows():
        timestamp = row["Workout_Date"].strftime("%Y-%m-%dT%H:%M:%SZ")
        for field in selected_fields:
            value = row[field]
            if not pd.isna(value):
                points.append({"time": timestamp, "field": field, "value": float(value)})
    return points


def grafana_summary(request: Request):
    start_date = request.query_params.get("from", "") or request.query_params.get("start_date", "")
    end_date = request.query_params.get("to", "") or request.query_params.get("end_date", "")
    try:
        selected_fields = parse_field_selection(request.query_params)
    except ValueError:
        return JSONResponse({"error": "Unsupported field selection.", "allowed_fields": GRAPHABLE_FIELDS}, status_code=400)

    filtered_data = filter_data(load_history_file(HISTORY_FILE), start_date, end_date)
    summary: dict[str, object] = {
        "workout_count": int(len(filtered_data)),
        "from": start_date,
        "to": end_date,
        "fields": selected_fields,
        "averages": {},
        "minimums": {},
        "maximums": {},
    }
    for field in selected_fields:
        field_series = pd.to_numeric(filtered_data[field], errors="coerce").dropna()
        summary["averages"][field] = None if field_series.empty else float(field_series.mean())
        summary["minimums"][field] = None if field_series.empty else float(field_series.min())
        summary["maximums"][field] = None if field_series.empty else float(field_series.max())
    return summary


def welcome(request: Request):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    historical_data = load_history_file(HISTORY_FILE)
    return render(request, "welcome.html", build_page_context(historical_data))


def workout_performance(request: Request):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    message = request.query_params.get("message", "")
    historical_data = load_history_file(HISTORY_FILE)
    default_start_date = (current_day() - pd.DateOffset(years=1)).date().isoformat()
    start_date = request.query_params.get("start_date", default_start_date)
    end_date = request.query_params.get("end_date", "")

    selected_fields = [field for field in request.query_params.getlist("fields") if field in GRAPHABLE_FIELDS]
    if not selected_fields:
        selected_fields = ["Distance", "Avg_Speed", "Workout_Time"]

    filtered_data = filter_data(historical_data, start_date, end_date)
    chart_html = build_chart(filtered_data, selected_fields)
    historical_table_data = filtered_data.sort_values(by=["Workout_Date"], ascending=False).reset_index(drop=True)
    table_html = historical_table_data.to_html(classes="table table-striped", index=False) if not historical_table_data.empty else ""

    return render(
        request,
        "workout_performance.html",
        {
            "message": message,
            "fields": GRAPHABLE_FIELDS,
            "selected_fields": selected_fields,
            "start_date": start_date,
            "end_date": end_date,
            "chart_html": chart_html,
            "table_html": table_html,
            "record_count": len(filtered_data),
            **build_page_context(historical_data),
        },
    )


def upload_workout_get(request: Request):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    historical_data = load_history_file(HISTORY_FILE)
    return render(request, "upload_workout.html", {"message": "", **build_page_context(historical_data)})


async def upload_workout_post(request: Request, dat_file: UploadFile | None = File(None)):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    message = ""
    historical_data = load_history_file(HISTORY_FILE)
    try:
        if dat_file and dat_file.filename:
            start = perf_counter()
            new_data = await read_dat_from_upload(dat_file)
            FILE_IMPORT_LATENCY.labels("upload").observe(perf_counter() - start)
            historical_data = merge_data(new_data, historical_data)
            save_history(historical_data, HISTORY_FILE)
            app.logger.info("DAT upload merged: file=%s rows=%s", dat_file.filename, len(new_data))
            return redirect_to(route_url(request, "workout_performance", message=f"Uploaded {dat_file.filename} and merged {len(new_data)} workouts."))
        if DAT_FILE.exists():
            start = perf_counter()
            new_data = read_dat_from_disk(DAT_FILE)
            FILE_IMPORT_LATENCY.labels("disk").observe(perf_counter() - start)
            historical_data = merge_data(new_data, historical_data)
            save_history(historical_data, HISTORY_FILE)
            app.logger.info("Disk import merged: file=%s rows=%s", DAT_FILE, len(new_data))
            return redirect_to(route_url(request, "workout_performance", message=f"Loaded {DAT_FILE.name} from disk and merged {len(new_data)} workouts."))
        message = "No upload provided and no DAT file found on disk."
        app.logger.warning("Workout import attempted without DAT file available")
    except Exception:
        message = DAT_IMPORT_ERROR_MESSAGE
        app.logger.warning("DAT parse/import failed")
    historical_data = load_history_file(HISTORY_FILE)
    return render(request, "upload_workout.html", {"message": message, **build_page_context(historical_data)})


def upload_history_get(request: Request):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    historical_data = load_history_file(HISTORY_FILE)
    return render(request, "upload_history.html", {"message": "", **build_page_context(historical_data)})


async def upload_history_post(request: Request, history_csv_file: UploadFile | None = File(None)):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    message = ""
    historical_data = load_history_file(HISTORY_FILE)
    try:
        if not history_csv_file or not history_csv_file.filename:
            message = "Please choose a historical CSV file to import."
        else:
            start = perf_counter()
            uploaded_history = await read_history_csv_from_upload(history_csv_file)
            FILE_IMPORT_LATENCY.labels("history_csv").observe(perf_counter() - start)
            historical_data = merge_data(uploaded_history, historical_data)
            save_history(historical_data, HISTORY_FILE)
            app.logger.info("History CSV merged: file=%s rows=%s", history_csv_file.filename, len(uploaded_history))
            return redirect_to(route_url(request, "workout_performance", message=f"Uploaded {history_csv_file.filename} and merged {len(uploaded_history)} historical rows."))
    except Exception:
        message = HISTORY_IMPORT_ERROR_MESSAGE
        app.logger.warning("History CSV import failed")
    historical_data = load_history_file(HISTORY_FILE)
    return render(request, "upload_history.html", {"message": message, **build_page_context(historical_data)})


from app.bootstrap.factory import create_app
from app.bootstrap.testing import install_test_client


app = create_app()
install_test_client(app)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.app:app", host=HOST, port=PORT, reload=False)
