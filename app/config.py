"""Configuration and constants for the Schwinn workout tracker."""

from __future__ import annotations

import os
import secrets
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"


def load_dotenv_file(dotenv_path: Path) -> None:
    """Load environment variables from a dotenv file without overwriting existing values."""
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


def env_first(*names: str, default: str = "") -> str:
    """Return the first non-blank environment value from the provided names."""
    for name in names:
        value = os.getenv(name)
        if value is not None and value.strip():
            return value.strip()
    return default


load_dotenv_file(ROOT_DIR / ".env")

SESSION_COOKIE_SECURE = os.getenv("SESSION_COOKIE_SECURE", "").lower() in {"1", "true", "yes"}
secret_key = os.getenv("SECRET_KEY") or secrets.token_urlsafe(64)

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

ADMIN_ROLE = "admin"
USER_ROLE = "user"
VALID_ROLES = {ADMIN_ROLE, USER_ROLE}

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
WORKOUT_DETAIL_PERIODS = {
    "1_week": "1 week",
    "2_weeks": "2 weeks",
    "1_month": "1 month",
    "begin_month": "begin of month",
    "3_months": "3 months",
    "5_months": "5 months",
    "current_year": "current year",
    "last_1_year": "last 1 year",
    "all": "all",
}
METRIC_AGGREGATIONS = {
    "Distance": "sum",
    "Avg_Speed": "mean",
    "Workout_Time": "sum",
    "Total_Calories": "sum",
    "Heart_Rate": "mean",
    "RPM": "mean",
    "Level": "mean",
}
DAT_IMPORT_ERROR_MESSAGE = "Unable to parse the uploaded DAT file. Check the file contents and try again."
HISTORY_IMPORT_ERROR_MESSAGE = "Unable to import the historical CSV file. Verify the file format and try again."
