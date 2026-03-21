#!/usr/bin/env python3
from __future__ import annotations

import hmac
import json
import logging
import os
import secrets
import sqlite3
import smtplib
from contextlib import contextmanager
from io import BytesIO, StringIO
from email.message import EmailMessage
from logging.handlers import RotatingFileHandler
from pathlib import Path
from time import perf_counter
from typing import Iterable

import pandas as pd
import plotly.graph_objects as go
from flask import Flask, Response, g, jsonify, redirect, render_template, request, session, url_for
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
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

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024  # 5 MB upload cap
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = os.getenv("SESSION_COOKIE_SECURE", "").lower() in {"1", "true", "yes"}
app.secret_key = os.getenv("SECRET_KEY", "development-only-secret-change-me")


def env_first(*names: str, default: str = "") -> str:
    for name in names:
        value = os.getenv(name)
        if value is not None and value.strip():
            return value.strip()
    return default

BASE_DIR = Path(__file__).resolve().parent
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

REQUEST_COUNT = Counter(
    "schwinn_http_requests_total",
    "Total HTTP requests",
    ["method", "endpoint", "status_code"],
)
REQUEST_LATENCY = Histogram(
    "schwinn_http_request_duration_seconds",
    "HTTP request latency in seconds",
    ["method", "endpoint"],
)
CHARTS_GENERATED = Counter(
    "schwinn_charts_generated_total",
    "Total charts generated",
)
CHART_GENERATION_LATENCY = Histogram(
    "schwinn_chart_generation_duration_seconds",
    "Time spent generating chart HTML",
)
FILE_IMPORT_LATENCY = Histogram(
    "schwinn_file_import_duration_seconds",
    "Time spent importing DAT files",
    ["source"],
)
AUTH_EVENT_COUNT = Counter(
    "schwinn_auth_events_total",
    "Authentication and account management events",
    ["action", "result"],
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


configure_logging()


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
                email TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'user',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        existing_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(users)").fetchall()
        }
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


def normalize_email(email: str) -> str:
    return email.strip().lower()


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
    audit_key = f"{app.secret_key}:{PASSWORD_RESET_SALT}".encode("utf-8")
    digest = hmac.new(audit_key, normalized.encode("utf-8"), "sha256").hexdigest()
    return digest[:12]


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


def create_user(email: str, password: str, *, role: str = USER_ROLE) -> sqlite3.Row:
    normalized = normalize_email(email)
    if role not in VALID_ROLES:
        raise ValueError(f"Unsupported role: {role}")
    password_hash = generate_password_hash(password)
    with get_db_connection() as connection:
        cursor = connection.execute(
            "INSERT INTO users (email, password_hash, role) VALUES (?, ?, ?)",
            (normalized, password_hash, role),
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
        return connection.execute("SELECT * FROM users ORDER BY email ASC").fetchall()


def get_setting(key: str, default: str = "") -> str:
    with get_db_connection() as connection:
        row = connection.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    if row is None:
        return default
    return str(row["value"])


def set_setting(key: str, value: str) -> None:
    with get_db_connection() as connection:
        connection.execute(
            """
            INSERT INTO settings (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, value),
        )
        connection.commit()


def is_registration_enabled() -> bool:
    return get_setting("registration_enabled", "false").lower() == "true"


def set_registration_enabled(enabled: bool) -> None:
    set_setting("registration_enabled", "true" if enabled else "false")


def admin_count() -> int:
    with get_db_connection() as connection:
        row = connection.execute("SELECT COUNT(*) AS count FROM users WHERE role = ?", (ADMIN_ROLE,)).fetchone()
    return int(row["count"]) if row else 0


def admin_exists() -> bool:
    return admin_count() > 0


def password_reset_serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(app.secret_key)


def generate_password_reset_token(email: str) -> str:
    return password_reset_serializer().dumps(normalize_email(email), salt=PASSWORD_RESET_SALT)


def verify_password_reset_token(token: str, *, max_age: int | None = None) -> sqlite3.Row | None:
    try:
        email = password_reset_serializer().loads(
            token,
            salt=PASSWORD_RESET_SALT,
            max_age=max_age or PASSWORD_RESET_MAX_AGE_SECONDS,
        )
    except (BadSignature, SignatureExpired):
        return None
    return get_user_by_email(str(email))


def current_user():
    return get_user_by_id(session.get(USER_SESSION_KEY))


def current_user_is_admin() -> bool:
    user = current_user()
    return bool(user and str(user["role"]) == ADMIN_ROLE)


def login_user(user: sqlite3.Row) -> None:
    session.clear()
    session[USER_SESSION_KEY] = int(user["id"])


def logout_current_user() -> None:
    session.clear()


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


def build_reset_link(token: str) -> str:
    if PUBLIC_BASE_URL:
        return f"{PUBLIC_BASE_URL}{url_for('reset_password', token=token)}"
    return url_for("reset_password", token=token, _external=True)


@app.context_processor
def inject_template_context() -> dict[str, object]:
    return {
        "current_user": current_user(),
        "registration_enabled": is_registration_enabled(),
        "admin_exists": admin_exists(),
    }


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


def read_dat_from_upload(file_storage) -> pd.DataFrame:
    raw_bytes = file_storage.read()
    raw_text = raw_bytes.decode("utf-8", errors="ignore")
    return load_workout_data(parse_dat_payload(raw_text))


def read_history_csv_from_upload(file_storage) -> pd.DataFrame:
    upload_df = pd.read_csv(BytesIO(file_storage.read()))
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


@app.before_request
def start_timer() -> None:
    g.request_start = perf_counter()


@app.before_request
def require_login():
    init_auth_db()
    endpoint = request.endpoint or ""
    if not admin_exists() and endpoint not in BOOTSTRAP_ALLOWED_ENDPOINTS:
        return redirect(url_for("setup_admin"))
    if endpoint in PUBLIC_ENDPOINTS:
        return None
    if current_user() is not None:
        return None
    if request.path.startswith("/api/"):
        return jsonify(error="Authentication required."), 401
    return redirect(url_for("login"))


@app.after_request
def observe_request(response):
    endpoint = request.endpoint or request.path
    REQUEST_COUNT.labels(request.method, endpoint, str(response.status_code)).inc()
    if hasattr(g, "request_start"):
        REQUEST_LATENCY.labels(request.method, endpoint).observe(perf_counter() - g.request_start)
    return response


@app.route("/healthz", methods=["GET"])
def healthz():
    return jsonify(status="ok")


@app.route("/metrics", methods=["GET"])
def metrics():
    return Response(generate_latest(), mimetype=CONTENT_TYPE_LATEST)


@app.route("/login", methods=["GET", "POST"])
def login():
    if not admin_exists():
        return redirect(url_for("setup_admin"))
    if current_user() is not None:
        return redirect(url_for("welcome"))

    message = request.args.get("message", "")
    email = normalize_email(request.values.get("email", "") or request.args.get("email", ""))

    if request.method == "POST":
        email = normalize_email(request.form.get("email", ""))
        password = request.form.get("password", "")
        user = get_user_by_email(email)
        if user and check_password_hash(str(user["password_hash"]), password):
            login_user(user)
            audit_auth_event("login", email, "success", details="user signed in")
            return redirect(url_for("welcome"))
        audit_auth_event("login", email, "failure", details="invalid email or password")
        message = "We couldn't sign you in with that email and password."

    return render_template(
        "login.html",
        message=message,
        email=email,
        registration_enabled=is_registration_enabled(),
    )


@app.route("/register", methods=["GET", "POST"])
def register():
    if not admin_exists():
        return redirect(url_for("setup_admin"))
    if current_user() is not None:
        return redirect(url_for("welcome"))
    if not is_registration_enabled():
        return render_template("register.html", message="New user registration is currently disabled.", email=""), 403

    message = ""
    email = ""
    if request.method == "POST":
        email = normalize_email(request.form.get("email", ""))
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")

        if not email:
            message = "Enter an email address to create your account."
        elif "@" not in email:
            message = "Enter a valid email address."
        elif not password_is_valid(password):
            message = "Choose a password with at least 8 characters."
        elif password != confirm_password:
            message = "Passwords did not match. Please try again."
        elif get_user_by_email(email) is not None:
            message = "That email address is already registered."
        else:
            user = create_user(email, password, role=USER_ROLE)
            login_user(user)
            return redirect(url_for("welcome"))

    return render_template("register.html", message=message, email=email)


@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if not admin_exists():
        return redirect(url_for("setup_admin"))
    message = ""
    if request.method == "POST":
        email = normalize_email(request.form.get("email", ""))
        user = get_user_by_email(email)
        if user is not None:
            token = generate_password_reset_token(email)
            reset_link = build_reset_link(token)
            try:
                send_password_reset_email(email, reset_link)
                audit_auth_event("password_reset_email", email, "success", details="forgot password email sent")
            except Exception:
                audit_auth_event("password_reset_email", email, "failure", details="forgot password email send failed")
                message = "We couldn't send the password reset email right now. Please try again."
                return render_template("forgot_password.html", message=message, email=email)
        else:
            audit_auth_event("password_reset_email", email, "failure", details="forgot password requested for unknown email")
        message = "If that email is registered, a password reset link has been sent."
        return render_template("forgot_password.html", message=message, email="")

    return render_template("forgot_password.html", message=message, email="")


@app.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token: str):
    if not admin_exists():
        return redirect(url_for("setup_admin"))
    user = verify_password_reset_token(token)
    if user is None:
        audit_auth_event("password_reset", "", "failure", details="invalid or expired reset token")
        return render_template(
            "reset_password.html",
            message="That password reset link is invalid or has expired.",
            token=token,
            token_valid=False,
        )

    message = ""
    if request.method == "POST":
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")
        if not password_is_valid(password):
            audit_auth_event("password_reset", str(user["email"]), "failure", details="password too short")
            message = "Choose a password with at least 8 characters."
        elif password != confirm_password:
            audit_auth_event("password_reset", str(user["email"]), "failure", details="password confirmation mismatch")
            message = "Passwords did not match. Please try again."
        else:
            update_user_password(int(user["id"]), password)
            logout_current_user()
            audit_auth_event("password_reset", str(user["email"]), "success", details="password updated")
            return redirect(
                url_for(
                    "login",
                    message="Your password has been reset. You can sign in now.",
                    email=str(user["email"]),
                )
            )

    return render_template(
        "reset_password.html",
        message=message,
        token=token,
        token_valid=True,
        reset_email=str(user["email"]),
    )


@app.route("/logout", methods=["GET"])
def logout():
    user = current_user()
    logout_current_user()
    if user is not None:
        audit_auth_event("logout", str(user["email"]), "success", details="user signed out")
    return redirect(url_for("setup_admin" if not admin_exists() else "login", message="You have been signed out."))


@app.route("/setup-admin", methods=["GET", "POST"])
def setup_admin():
    if admin_exists():
        if current_user() is not None:
            return redirect(url_for("welcome"))
        return redirect(url_for("login"))

    message = ""
    email = ""
    if request.method == "POST":
        email = normalize_email(request.form.get("email", ""))
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")

        if not email:
            message = "Enter an email address for the admin account."
        elif "@" not in email:
            message = "Enter a valid email address."
        elif not password_is_valid(password):
            message = "Choose a password with at least 8 characters."
        elif password != confirm_password:
            message = "Passwords did not match. Please try again."
        else:
            user = create_user(email, password, role=ADMIN_ROLE)
            set_registration_enabled(False)
            login_user(user)
            audit_auth_event("admin_bootstrap", email, "success", details="initial admin account created")
            return redirect(url_for("admin_dashboard", message="Admin account created."))

    return render_template("setup_admin.html", message=message, email=email)


def admin_required():
    if not current_user_is_admin():
        return redirect(url_for("welcome"))
    return None


@app.route("/admin", methods=["GET", "POST"])
def admin_dashboard():
    guard = admin_required()
    if guard is not None:
        return guard

    message = request.args.get("message", "")
    if request.method == "POST":
        enabled = request.form.get("registration_enabled") == "true"
        set_registration_enabled(enabled)
        message = "Registration settings updated."

    return render_template("admin.html", message=message)


@app.route("/admin/users", methods=["GET", "POST"])
def admin_users():
    guard = admin_required()
    if guard is not None:
        return guard

    message = request.args.get("message", "")
    if request.method == "POST":
        action = request.form.get("action", "")
        target_email = normalize_email(request.form.get("email", ""))
        target_role = request.form.get("role", USER_ROLE)
        target_id_raw = request.form.get("user_id", "")
        target_id = int(target_id_raw) if target_id_raw.isdigit() else None
        target_user = get_user_by_id(target_id)
        actor = current_user()
        actor_email = str(actor["email"]) if actor is not None else ""

        if action == "create_user":
            if not target_email:
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
                create_user(target_email, temporary_password, role=target_role)
                audit_auth_event("user_create", target_email, "success", actor_email=actor_email, details=f"user created role={target_role}")
                reset_link = build_reset_link(generate_password_reset_token(target_email))
                try:
                    send_password_reset_email(target_email, reset_link)
                    audit_auth_event(
                        "password_reset_email",
                        target_email,
                        "success",
                        actor_email=actor_email,
                        details="new user setup email sent",
                    )
                    message = f"Created {target_email} and sent a password setup email."
                except Exception:
                    audit_auth_event(
                        "password_reset_email",
                        target_email,
                        "failure",
                        actor_email=actor_email,
                        details="new user setup email send failed",
                    )
                    message = f"Created {target_email}, but the password setup email could not be sent."
        elif action == "delete_user" and target_user is not None:
            if str(target_user["role"]) == ADMIN_ROLE and admin_count() == 1:
                audit_auth_event(
                    "user_delete",
                    str(target_user["email"]),
                    "failure",
                    actor_email=actor_email,
                    details="cannot delete last admin",
                )
                message = "You cannot delete the last admin account."
            else:
                delete_user(int(target_user["id"]))
                audit_auth_event(
                    "user_delete",
                    str(target_user["email"]),
                    "success",
                    actor_email=actor_email,
                    details="user deleted",
                )
                message = f"Deleted {target_user['email']}."
        elif action == "update_role" and target_user is not None:
            if target_role not in VALID_ROLES:
                audit_auth_event(
                    "user_role_update",
                    str(target_user["email"]),
                    "failure",
                    actor_email=actor_email,
                    details="invalid role",
                )
                message = "Choose a valid user role."
            elif (
                str(target_user["role"]) == ADMIN_ROLE
                and target_role != ADMIN_ROLE
                and admin_count() == 1
            ):
                audit_auth_event(
                    "user_role_update",
                    str(target_user["email"]),
                    "failure",
                    actor_email=actor_email,
                    details="cannot demote last admin",
                )
                message = "You cannot demote the last admin account."
            else:
                update_user_role(int(target_user["id"]), target_role)
                audit_auth_event(
                    "user_role_update",
                    str(target_user["email"]),
                    "success",
                    actor_email=actor_email,
                    details=f"role updated to {target_role}",
                )
                message = f"Updated {target_user['email']} to {target_role}."
        elif action == "send_reset" and target_user is not None:
            reset_link = build_reset_link(generate_password_reset_token(str(target_user["email"])))
            try:
                send_password_reset_email(str(target_user["email"]), reset_link)
                audit_auth_event(
                    "password_reset_email",
                    str(target_user["email"]),
                    "success",
                    actor_email=actor_email,
                    details="admin-triggered reset email sent",
                )
                message = f"Sent a password reset email to {target_user['email']}."
            except Exception:
                audit_auth_event(
                    "password_reset_email",
                    str(target_user["email"]),
                    "failure",
                    actor_email=actor_email,
                    details="admin-triggered reset email send failed",
                )
                message = f"Could not send a password reset email to {target_user['email']}."
        else:
            audit_auth_event("admin_user_action", target_email, "failure", actor_email=actor_email, details="invalid admin action")
            message = "The requested admin action could not be completed."

    return render_template("admin_users.html", message=message, users=list_users(), roles=sorted(VALID_ROLES))


@app.route("/download-history", methods=["GET"])
def download_history():
    history_df = load_history_file(HISTORY_FILE)
    buffer = StringIO()
    history_df.to_csv(buffer, index=False)
    return Response(
        buffer.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=Workout_History.csv"},
    )


@app.route("/api/grafana/workouts", methods=["GET"])
def grafana_workouts():
    start_date = request.args.get("from", "") or request.args.get("start_date", "")
    end_date = request.args.get("to", "") or request.args.get("end_date", "")

    try:
        selected_fields = parse_field_selection(request.args)
    except ValueError:
        return jsonify(error="Unsupported field selection.", allowed_fields=GRAPHABLE_FIELDS), 400

    historical_data = load_history_file(HISTORY_FILE)
    filtered_data = filter_data(historical_data, start_date, end_date)

    points: list[dict] = []
    for _, row in filtered_data.iterrows():
        timestamp = row["Workout_Date"].strftime("%Y-%m-%dT%H:%M:%SZ")
        for field in selected_fields:
            value = row[field]
            if pd.isna(value):
                continue
            points.append({"time": timestamp, "field": field, "value": float(value)})

    return jsonify(points)


@app.route("/api/grafana/summary", methods=["GET"])
def grafana_summary():
    start_date = request.args.get("from", "") or request.args.get("start_date", "")
    end_date = request.args.get("to", "") or request.args.get("end_date", "")

    try:
        selected_fields = parse_field_selection(request.args)
    except ValueError:
        return jsonify(error="Unsupported field selection.", allowed_fields=GRAPHABLE_FIELDS), 400

    historical_data = load_history_file(HISTORY_FILE)
    filtered_data = filter_data(historical_data, start_date, end_date)

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
        if field_series.empty:
            summary["averages"][field] = None
            summary["minimums"][field] = None
            summary["maximums"][field] = None
            continue
        summary["averages"][field] = float(field_series.mean())
        summary["minimums"][field] = float(field_series.min())
        summary["maximums"][field] = float(field_series.max())

    return jsonify(summary)


@app.route("/", methods=["GET"])
def welcome():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    historical_data = load_history_file(HISTORY_FILE)
    return render_template("welcome.html", **build_page_context(historical_data))


@app.route("/workout-performance", methods=["GET"])
def workout_performance():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    message = request.args.get("message", "")
    historical_data = load_history_file(HISTORY_FILE)
    default_start_date = (current_day() - pd.DateOffset(years=1)).date().isoformat()
    start_date = request.values.get("start_date", default_start_date)
    end_date = request.values.get("end_date", "")

    selected_fields = [field for field in request.values.getlist("fields") if field in GRAPHABLE_FIELDS]
    if not selected_fields:
        selected_fields = ["Distance", "Avg_Speed", "Workout_Time"]

    filtered_data = filter_data(historical_data, start_date, end_date)
    chart_html = build_chart(filtered_data, selected_fields)
    historical_table_data = filtered_data.sort_values(by=["Workout_Date"], ascending=False).reset_index(drop=True)
    table_html = (
        historical_table_data.to_html(classes="table table-striped", index=False)
        if not historical_table_data.empty
        else ""
    )

    return render_template(
        "workout_performance.html",
        message=message,
        fields=GRAPHABLE_FIELDS,
        selected_fields=selected_fields,
        start_date=start_date,
        end_date=end_date,
        chart_html=chart_html,
        table_html=table_html,
        record_count=len(filtered_data),
        **build_page_context(historical_data),
    )


@app.route("/upload-workout", methods=["GET", "POST"])
def upload_workout():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    message = ""

    if request.method == "POST":
        historical_data = load_history_file(HISTORY_FILE)
        try:
            dat_upload = request.files.get("dat_file")
            if dat_upload and dat_upload.filename:
                start = perf_counter()
                new_data = read_dat_from_upload(dat_upload)
                FILE_IMPORT_LATENCY.labels("upload").observe(perf_counter() - start)
                historical_data = merge_data(new_data, historical_data)
                save_history(historical_data, HISTORY_FILE)
                app.logger.info("DAT upload merged: file=%s rows=%s", dat_upload.filename, len(new_data))
                return redirect(
                    url_for(
                        "workout_performance",
                        message=f"Uploaded {dat_upload.filename} and merged {len(new_data)} workouts.",
                    )
                )

            if DAT_FILE.exists():
                start = perf_counter()
                new_data = read_dat_from_disk(DAT_FILE)
                FILE_IMPORT_LATENCY.labels("disk").observe(perf_counter() - start)
                historical_data = merge_data(new_data, historical_data)
                save_history(historical_data, HISTORY_FILE)
                app.logger.info("Disk import merged: file=%s rows=%s", DAT_FILE, len(new_data))
                return redirect(
                    url_for(
                        "workout_performance",
                        message=f"Loaded {DAT_FILE.name} from disk and merged {len(new_data)} workouts.",
                    )
                )

            message = "No upload provided and no DAT file found on disk."
            app.logger.warning("Workout import attempted without DAT file available")
        except Exception:
            message = DAT_IMPORT_ERROR_MESSAGE
            app.logger.warning("DAT parse/import failed")

    historical_data = load_history_file(HISTORY_FILE)
    return render_template("upload_workout.html", message=message, **build_page_context(historical_data))


@app.route("/upload-history", methods=["GET", "POST"])
def upload_history():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    message = ""

    if request.method == "POST":
        historical_data = load_history_file(HISTORY_FILE)
        try:
            history_upload = request.files.get("history_csv_file")
            if not history_upload or not history_upload.filename:
                message = "Please choose a historical CSV file to import."
            else:
                start = perf_counter()
                uploaded_history = read_history_csv_from_upload(history_upload)
                FILE_IMPORT_LATENCY.labels("history_csv").observe(perf_counter() - start)
                historical_data = merge_data(uploaded_history, historical_data)
                save_history(historical_data, HISTORY_FILE)
                app.logger.info("History CSV merged: file=%s rows=%s", history_upload.filename, len(uploaded_history))
                return redirect(
                    url_for(
                        "workout_performance",
                        message=f"Uploaded {history_upload.filename} and merged {len(uploaded_history)} historical rows.",
                    )
                )
        except Exception:
            message = HISTORY_IMPORT_ERROR_MESSAGE
            app.logger.warning("History CSV import failed")

    historical_data = load_history_file(HISTORY_FILE)
    return render_template("upload_history.html", message=message, **build_page_context(historical_data))


if __name__ == "__main__":
    debug_mode = os.environ.get("FLASK_DEBUG", "").lower() in {"1", "true", "yes"}
    app.run(host=HOST, port=PORT, debug=debug_mode)
