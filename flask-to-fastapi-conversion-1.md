# Flask to FastAPI Conversion Plan

File: `flask-to-fastapi-conversion-1.md`

Project: `schwinn`

Source app: `app/app.py`

Target runtime: FastAPI running under Uvicorn in Docker.

---

## 1. Goal

Convert the existing Flask-based Schwinn workout web app to FastAPI while preserving the current behavior, URLs, templates, authentication flow, SQLite schema, workout import logic, Grafana API endpoints, Prometheus metrics, Docker workflow, and tests.

This should be implemented as a compatibility migration first. Do not redesign the app, database schema, authentication model, or UI during the initial conversion.

---

## 2. Current application summary

The current `app/app.py` is a full Flask web application, not just a JSON API.

It currently includes:

- Server-rendered Jinja templates.
- Session-based authentication.
- Admin bootstrap flow.
- User registration/login/logout.
- Password reset email workflow.
- Admin user management.
- Profile and avatar upload support.
- SQLite database for users/settings.
- Workout DAT file parsing.
- Historical CSV upload/import.
- Pandas-based workout history processing.
- Plotly chart generation.
- Downloadable workout history CSV.
- Grafana-friendly JSON API endpoints.
- Prometheus metrics endpoint.
- Rotating file logs.
- Docker/Kubernetes-friendly health check.

The migration should preserve these behaviors.

---

## 3. Target stack

Use:

- `fastapi`
- `uvicorn[standard]`
- `starlette` session middleware
- `jinja2`
- `python-multipart`
- `pandas`
- `plotly`
- `pillow`
- `prometheus-client`
- `itsdangerous`
- `werkzeug`
- `pytest`
- `httpx`

Keep SQLite for this migration.

Do not introduce SQLAlchemy, Alembic, async SQLite drivers, OAuth, JWT auth, or a major frontend rewrite in this first conversion.

---

## 4. Recommended migration strategy

Use a phased approach.

### Phase 1: Compatibility migration

Preserve:

- Existing URL paths.
- Existing templates.
- Existing SQLite tables.
- Existing CSV/DAT behavior.
- Existing Prometheus metric names.
- Existing Docker port `8080`.
- Existing user/admin behavior.
- Existing session-based auth, even though cookie format will change.

Expected side effect:

- Existing browser sessions may be invalidated because Flask and Starlette session cookies are not compatible. Users may need to log in again after deployment.

### Phase 2: Refactor after parity

Only after the FastAPI version works and tests pass, split the monolithic `app/app.py` into routers/services/modules.

Do not perform Phase 2 in the initial migration unless explicitly requested.

---

## 5. Files that need updates

Expected files:

```text
app/app.py
app/__init__.py
app/templates/*.html
app/static/*
pyproject.toml or requirements.txt
poetry.lock or requirements lock file
Dockerfile
docker-compose.yml, if present
Makefile
README.md
.env.example
.dockerignore
tests/conftest.py
tests/test_*.py
```

---

# 6. `app/app.py` changes

## 6.1 Replace Flask imports

Current Flask import:

```python
from flask import Flask, Response, g, jsonify, redirect, render_template, request, session, url_for
```

Replace with FastAPI/Starlette imports:

```python
from fastapi import FastAPI, File, Form, Request, Response, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
```

Keep existing imports that are still needed:

```python
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

import pandas as pd
import plotly.graph_objects as go
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from PIL import Image, UnidentifiedImageError
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from werkzeug.security import check_password_hash, generate_password_hash
```

Optional direct-run import:

```python
import uvicorn
```

Only use this if keeping `python app/app.py` support.

---

## 6.2 Replace Flask app creation

Current:

```python
app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = os.getenv("SESSION_COOKIE_SECURE", "").lower() in {"1", "true", "yes"}
...
app.secret_key = secret_key
```

Target:

```python
app = FastAPI(title="Schwinn", version="1.0.0")

app.add_middleware(
    SessionMiddleware,
    secret_key=secret_key,
    https_only=SESSION_COOKIE_SECURE,
    same_site="lax",
    session_cookie="session",
)

templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

app.mount(
    "/static",
    StaticFiles(directory=str(BASE_DIR / "static")),
    name="static",
)
```

Notes:

- FastAPI does not use `app.config`.
- File size validation should be enforced manually in upload handlers.
- Starlette session cookies are signed but not the same format as Flask sessions.

---

## 6.3 Add constants for templates/static

Near existing path constants, define:

```python
BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"
```

Then configure:

```python
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
```

---

## 6.4 Replace Flask template context processor

Current:

```python
@app.context_processor
def inject_template_context() -> dict[str, object]:
    return {
        "current_user": current_user(),
        ...
    }
```

FastAPI does not have Flask-style context processors.

Create a render helper:

```python
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
    return templates.TemplateResponse(template_name, page_context, status_code=status_code)
```

All template responses must use this helper or must include `request` manually.

---

## 6.5 Add redirect helper

Create:

```python
def redirect_to(url: str, status_code: int = 303) -> RedirectResponse:
    return RedirectResponse(url=url, status_code=status_code)
```

Use `303` for redirects after POST requests.

---

## 6.6 Update session functions

Current functions access Flask global `session`.

Replace:

```python
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
```

With:

```python
def current_user(request: Request) -> sqlite3.Row | None:
    return get_user_by_id(request.session.get(USER_SESSION_KEY))


def current_user_is_admin(request: Request) -> bool:
    user = current_user(request)
    return bool(user and str(user["role"]) == ADMIN_ROLE)


def login_user(request: Request, user: sqlite3.Row) -> None:
    request.session.clear()
    request.session[USER_SESSION_KEY] = int(user["id"])


def logout_current_user(request: Request) -> None:
    request.session.clear()
```

Update all callers.

---

## 6.7 Replace Flask request lifecycle hooks

Current Flask hooks:

```python
@app.before_request
def start_timer() -> None:
    g.request_start = perf_counter()


@app.before_request
def require_login():
    ...


@app.after_request
def observe_request(response):
    ...
```

Replace with one FastAPI middleware:

```python
@app.middleware("http")
async def auth_and_metrics_middleware(request: Request, call_next):
    request.state.request_start = perf_counter()
    init_auth_db()

    path = request.url.path

    if not admin_exists() and path not in BOOTSTRAP_ALLOWED_PATHS and not path.startswith("/static"):
        response = redirect_to(str(request.url_for("setup_admin")))
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
        response = redirect_to(f"{request.url_for('login')}?next={next_path}")

    endpoint = request.scope.get("endpoint")
    endpoint_name = getattr(endpoint, "__name__", path)

    REQUEST_COUNT.labels(
        request.method,
        endpoint_name,
        str(response.status_code),
    ).inc()

    REQUEST_LATENCY.labels(
        request.method,
        endpoint_name,
    ).observe(perf_counter() - request.state.request_start)

    return response
```

Make sure the public path sets include:

```python
PUBLIC_PATHS = {
    "/healthz",
    "/metrics",
    "/setup-admin",
    "/login",
    "/register",
    "/forgot-password",
    "/logout",
}

BOOTSTRAP_ALLOWED_PATHS = {
    "/healthz",
    "/metrics",
    "/setup-admin",
}
```

The reset password path uses path prefix matching:

```python
path.startswith("/reset-password/")
```

---

## 6.8 Add FastAPI startup handler

Add:

```python
@app.on_event("startup")
def startup() -> None:
    init_auth_db()
```

Note: FastAPI now supports lifespan handlers as the newer pattern, but `@app.on_event("startup")` is acceptable for this migration unless the project already enforces the newer lifespan style.

---

## 6.9 Update route decorators

Convert every Flask route decorator.

Current:

```python
@app.route("/healthz", methods=["GET"])
def healthz():
```

Target:

```python
@app.get("/healthz", name="healthz")
def healthz():
```

Current mixed GET/POST route:

```python
@app.route("/login", methods=["GET", "POST"])
def login():
```

Target should split into two handlers:

```python
@app.get("/login", response_class=HTMLResponse, name="login")
def login_get(request: Request):
    ...


@app.post("/login", response_class=HTMLResponse, name="login_post")
def login_post(
    request: Request,
    email: str = Form(""),
    password: str = Form(""),
):
    ...
```

Important: Preserve route names used by templates. For routes referenced by templates, use explicit `name=` values.

---

## 6.10 Route conversion list

### Convert simple GET routes

Convert:

```text
/healthz
/metrics
/logout
/account/avatar
/download-history
/api/grafana/workouts
/api/grafana/summary
/
/workout-performance
```

### Split GET/POST routes

Split:

```text
/login
/register
/forgot-password
/reset-password/{token}
/account
/setup-admin
/admin
/admin/users
/upload-workout
/upload-history
```

---

## 6.11 Convert JSON responses

Current Flask:

```python
return jsonify(status="ok")
return jsonify(points)
return jsonify(error="Unsupported field selection.", allowed_fields=GRAPHABLE_FIELDS), 400
```

FastAPI target:

```python
return {"status": "ok"}
return points
return JSONResponse(
    {"error": "Unsupported field selection.", "allowed_fields": GRAPHABLE_FIELDS},
    status_code=400,
)
```

Update routes:

```text
/healthz
/api/grafana/workouts
/api/grafana/summary
middleware API auth failure
```

---

## 6.12 Convert metrics endpoint

Current:

```python
@app.route("/metrics", methods=["GET"])
def metrics():
    return Response(generate_latest(), mimetype=CONTENT_TYPE_LATEST)
```

Target:

```python
@app.get("/metrics", name="metrics")
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
```

Keep all existing metric names unchanged.

---

## 6.13 Convert redirects and `url_for`

Current Flask:

```python
return redirect(url_for("welcome"))
```

Target:

```python
return redirect_to(str(request.url_for("welcome")))
```

For redirects with query params, use strings:

```python
return redirect_to(f"{request.url_for('login')}?message=You have been signed out.")
```

---

## 6.14 Update password reset URL generation

Current:

```python
def build_reset_link(token: str) -> str:
    if PUBLIC_BASE_URL:
        return f"{PUBLIC_BASE_URL}{url_for('reset_password', token=token)}"
    return url_for("reset_password", token=token, _external=True)
```

Target:

```python
def build_reset_link(request: Request, token: str) -> str:
    reset_path = request.url_for("reset_password", token=token).path
    if PUBLIC_BASE_URL:
        return f"{PUBLIC_BASE_URL}{reset_path}"
    return str(request.url_for("reset_password", token=token))
```

Update all callers:

```python
reset_link = build_reset_link(request, token)
```

---

## 6.15 Convert file upload functions

Current Flask upload handlers use `file_storage.read()`.

Convert these functions to async and use `UploadFile`:

```python
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
```

Convert avatar upload:

```python
async def process_avatar_upload(
    upload_file: UploadFile | None,
    *,
    requested_size: int = AVATAR_SIZE_DEFAULT_PX,
) -> bytes:
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
```

---

## 6.16 Convert upload routes

Example target for workout upload:

```python
@app.post("/upload-workout", response_class=HTMLResponse, name="upload_workout_post")
async def upload_workout_post(
    request: Request,
    dat_file: UploadFile | None = File(None),
):
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
            logger.info("DAT upload merged: file=%s rows=%s", dat_file.filename, len(new_data))
            return redirect_to(
                f"{request.url_for('workout_performance')}?message=Uploaded {dat_file.filename} and merged {len(new_data)} workouts."
            )

        if DAT_FILE.exists():
            start = perf_counter()
            new_data = read_dat_from_disk(DAT_FILE)
            FILE_IMPORT_LATENCY.labels("disk").observe(perf_counter() - start)
            historical_data = merge_data(new_data, historical_data)
            save_history(historical_data, HISTORY_FILE)
            logger.info("Disk import merged: file=%s rows=%s", DAT_FILE, len(new_data))
            return redirect_to(
                f"{request.url_for('workout_performance')}?message=Loaded {DAT_FILE.name} from disk and merged {len(new_data)} workouts."
            )

        message = "No upload provided and no DAT file found on disk."
        logger.warning("Workout import attempted without DAT file available")
    except Exception:
        message = DAT_IMPORT_ERROR_MESSAGE
        logger.warning("DAT parse/import failed")

    historical_data = load_history_file(HISTORY_FILE)
    return render(request, "upload_workout.html", {"message": message, **build_page_context(historical_data)})
```

Make equivalent changes for `/upload-history`.

---

## 6.17 Convert query params and form data

Flask patterns to replace:

```python
request.args.get(...)
request.values.get(...)
request.values.getlist(...)
request.form.get(...)
request.files.get(...)
```

FastAPI equivalents:

- Query params:
  ```python
  request.query_params.get("from", "")
  request.query_params.getlist("field")
  ```

- Form fields:
  ```python
  email: str = Form("")
  password: str = Form("")
  ```

- File fields:
  ```python
  dat_file: UploadFile | None = File(None)
  ```

For routes with many optional form fields, explicit `Form("")` args are preferred for compatibility and testability.

---

## 6.18 Update `parse_field_selection`

Current code expects Flask request args.

Update to accept Starlette `QueryParams`, which supports `.get()` and `.getlist()`:

```python
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
        cleaned = field.strip().strip("'"")
        if cleaned and cleaned not in deduped_fields:
            deduped_fields.append(cleaned)

    invalid_fields = [field for field in deduped_fields if field not in GRAPHABLE_FIELDS]
    if invalid_fields:
        raise ValueError(f"Unsupported field(s): {', '.join(invalid_fields)}")

    return deduped_fields
```

---

## 6.19 Keep CPU-heavy routes sync unless upload requires async

Do not convert everything to `async def`.

Use normal `def` for routes doing:

- SQLite operations.
- Pandas processing.
- Plotly chart generation.
- SMTP sends.

Use `async def` for routes that call:

```python
await upload_file.read()
```

Examples:

```text
POST /account, because avatar upload may be present
POST /upload-workout
POST /upload-history
```

---

## 6.20 Update direct execution block

Current:

```python
if __name__ == "__main__":
    app.run(host=HOST, port=PORT, debug=False)
```

Target:

```python
if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.app:app", host=HOST, port=PORT, reload=False)
```

Docker should run Uvicorn directly and should not rely on this block.

---

# 7. Template changes

Inspect all templates in:

```text
app/templates/
```

Search for:

```text
url_for('static', filename=
url_for("static", filename=
get_flashed_messages
request.endpoint
session
```

## 7.1 Static URL updates

Flask:

```jinja2
{{ url_for('static', filename='app.css') }}
```

FastAPI/Starlette:

```jinja2
{{ url_for('static', path='/app.css') }}
```

For nested files:

```jinja2
{{ url_for('static', path='/css/app.css') }}
```

## 7.2 Route URL updates

Most route URLs should continue working if explicit route names are preserved:

```jinja2
{{ url_for('login') }}
{{ url_for('logout') }}
{{ url_for('account') }}
{{ url_for('admin_dashboard') }}
{{ url_for('workout_performance') }}
```

For reset-password path parameters:

```jinja2
{{ url_for('reset_password', token=token) }}
```

Ensure the FastAPI route has:

```python
@app.get("/reset-password/{token}", name="reset_password")
```

---

# 8. `app/__init__.py`

If missing, create:

```python
# Package marker for Schwinn app.
```

This makes imports stable:

```python
from app.app import app
```

---

# 9. Dependency file changes

Update whichever dependency management file the project uses.

## 9.1 If using Poetry: `pyproject.toml`

Remove or stop using:

```text
Flask
```

Add:

```toml
fastapi = "^0.115"
uvicorn = {extras = ["standard"], version = "^0.34"}
python-multipart = "^0.0.20"
jinja2 = "^3.1"
itsdangerous = "^2.2"
pandas = "^2.2"
plotly = "^5.24"
pillow = "^11.0"
prometheus-client = "^0.21"
werkzeug = "^3.1"
```

Test/dev dependencies:

```toml
pytest = "^8.0"
pytest-cov = "^5.0"
httpx = "^0.27"
ruff = "^0.8"
mypy = "^1.13"
```

Version numbers may be adjusted to match the project’s current Python version and lockfile constraints.

Run:

```bash
poetry lock
poetry install
```

## 9.2 If using `requirements.txt`

Remove:

```text
Flask
```

Add:

```text
fastapi
uvicorn[standard]
python-multipart
jinja2
itsdangerous
pandas
plotly
pillow
prometheus-client
werkzeug
httpx
pytest
pytest-cov
ruff
mypy
```

---

# 10. Dockerfile changes

Replace Flask-style execution with Uvicorn.

## 10.1 Required command change

Replace:

```dockerfile
CMD ["python", "app/app.py"]
```

or:

```dockerfile
CMD ["flask", "run", "--host=0.0.0.0"]
```

With:

```dockerfile
CMD ["uvicorn", "app.app:app", "--host", "0.0.0.0", "--port", "8080"]
```

## 10.2 Recommended Poetry-based Dockerfile

```dockerfile
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV HOST=0.0.0.0
ENV PORT=8080

WORKDIR /app

RUN apt-get update     && apt-get install -y --no-install-recommends build-essential curl     && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml poetry.lock* ./

RUN pip install --no-cache-dir poetry     && poetry config virtualenvs.create false     && poetry install --only main --no-interaction --no-ansi

COPY . .

RUN mkdir -p /app/app/data /app/app/logs

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3     CMD curl -fsS http://127.0.0.1:8080/healthz || exit 1

CMD ["uvicorn", "app.app:app", "--host", "0.0.0.0", "--port", "8080"]
```

## 10.3 Recommended requirements.txt Dockerfile

If the project uses `requirements.txt` instead of Poetry:

```dockerfile
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV HOST=0.0.0.0
ENV PORT=8080

WORKDIR /app

RUN apt-get update     && apt-get install -y --no-install-recommends build-essential curl     && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /app/app/data /app/app/logs

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3     CMD curl -fsS http://127.0.0.1:8080/healthz || exit 1

CMD ["uvicorn", "app.app:app", "--host", "0.0.0.0", "--port", "8080"]
```

---

# 11. `docker-compose.yml` changes, if present

Replace any Flask or Python direct startup command.

Target:

```yaml
services:
  schwinn:
    build: .
    ports:
      - "8080:8080"
    environment:
      HOST: 0.0.0.0
      PORT: 8080
      DATA_DIR: /app/app/data
      DAT_FILE: /app/app/data/AARON.DAT
      HISTORY_FILE: /app/app/data/Workout_History.csv
      AUTH_DB_FILE: /app/app/data/users.db
      LOG_DIR: /app/app/logs
      LOG_FILE: /app/app/logs/app.log
      SESSION_COOKIE_SECURE: "false"
    volumes:
      - ./app/data:/app/app/data
      - ./app/logs:/app/app/logs
    healthcheck:
      test: ["CMD", "curl", "-fsS", "http://127.0.0.1:8080/healthz"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 10s
```

Optional explicit command:

```yaml
command: uvicorn app.app:app --host 0.0.0.0 --port 8080
```

---

# 12. Makefile changes

Update any Flask-specific targets.

Recommended Makefile targets:

```makefile
APP_MODULE ?= app.app:app
HOST ?= 0.0.0.0
PORT ?= 8080
IMAGE ?= schwinn
TAG ?= local

.PHONY: install
install:
	poetry install

.PHONY: run
run:
	poetry run uvicorn $(APP_MODULE) --host $(HOST) --port $(PORT) --reload

.PHONY: run-prod
run-prod:
	poetry run uvicorn $(APP_MODULE) --host $(HOST) --port $(PORT)

.PHONY: test
test:
	poetry run pytest -q

.PHONY: test-cov
test-cov:
	poetry run pytest --cov=app --cov-report=term-missing

.PHONY: lint
lint:
	poetry run ruff check .

.PHONY: format
format:
	poetry run ruff format .

.PHONY: typecheck
typecheck:
	poetry run mypy app

.PHONY: docker-build
docker-build:
	docker build -t $(IMAGE):$(TAG) .

.PHONY: docker-run
docker-run:
	docker run --rm -p $(PORT):8080 		-e HOST=0.0.0.0 		-e PORT=8080 		$(IMAGE):$(TAG)

.PHONY: docker-test
docker-test:
	docker run --rm $(IMAGE):$(TAG) pytest -q
```

If not using Poetry, replace `poetry run` with direct commands.

---

# 13. `.env.example` changes

Update or create:

```env
HOST=0.0.0.0
PORT=8080
SECRET_KEY=change-me
SESSION_COOKIE_SECURE=false

DATA_DIR=/app/app/data
DAT_FILE=/app/app/data/AARON.DAT
HISTORY_FILE=/app/app/data/Workout_History.csv
AUTH_DB_FILE=/app/app/data/users.db

LOG_DIR=/app/app/logs
LOG_FILE=/app/app/logs/app.log

PUBLIC_BASE_URL=http://localhost:8080

MAIL_SERVER=
MAIL_PORT=587
MAIL_USERNAME=
MAIL_PASSWORD=
MAIL_FROM=no-reply@schwinn.local
MAIL_USE_TLS=true
MAIL_USE_SSL=false

PASSWORD_RESET_SALT=schwinn-password-reset-salt
PASSWORD_RESET_MAX_AGE_SECONDS=3600
```

For HTTPS reverse proxy deployments:

```env
SESSION_COOKIE_SECURE=true
PUBLIC_BASE_URL=https://schwinn.example.com
```

---

# 14. `.dockerignore` changes

Ensure local data and generated files are not copied into the container image:

```dockerignore
.git
.venv
__pycache__
.pytest_cache
.mypy_cache
.ruff_cache
.coverage
htmlcov
codeql-db
codeql-results.sarif
app/data
app/logs
*.pyc
.env
```

---

# 15. README changes

Replace Flask run instructions with FastAPI/Uvicorn instructions.

## 15.1 Local run

```bash
poetry install
poetry run uvicorn app.app:app --host 0.0.0.0 --port 8080 --reload
```

Or without Poetry:

```bash
pip install -r requirements.txt
uvicorn app.app:app --host 0.0.0.0 --port 8080 --reload
```

## 15.2 Docker build/run

```bash
docker build -t schwinn:local .

docker run --rm -p 8080:8080   -e HOST=0.0.0.0   -e PORT=8080   -v "$(pwd)/app/data:/app/app/data"   -v "$(pwd)/app/logs:/app/app/logs"   schwinn:local
```

## 15.3 Health and metrics

```bash
curl http://localhost:8080/healthz
curl http://localhost:8080/metrics
```

---

# 16. Test migration

All Flask `app.test_client()` tests must be converted to FastAPI `TestClient`.

## 16.1 Replace Flask test client

Current:

```python
from app.app import app

client = app.test_client()
response = client.get("/healthz")
```

Target:

```python
from fastapi.testclient import TestClient
from app.app import app

client = TestClient(app)
response = client.get("/healthz")
```

Dependency required:

```text
httpx
```

---

## 16.2 Update `tests/conftest.py`

Use isolated temp files for every test.

Important: Because `app/app.py` reads environment variables at import time, tests should set env vars before importing `app.app`.

Recommended fixture:

```python
import importlib
import sys

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def app_module(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    log_dir = tmp_path / "logs"
    data_dir.mkdir()
    log_dir.mkdir()

    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setenv("DAT_FILE", str(data_dir / "AARON.DAT"))
    monkeypatch.setenv("HISTORY_FILE", str(data_dir / "Workout_History.csv"))
    monkeypatch.setenv("AUTH_DB_FILE", str(data_dir / "users.db"))
    monkeypatch.setenv("LOG_DIR", str(log_dir))
    monkeypatch.setenv("LOG_FILE", str(log_dir / "app.log"))
    monkeypatch.setenv("SECRET_KEY", "test-secret-key")
    monkeypatch.setenv("SESSION_COOKIE_SECURE", "false")
    monkeypatch.setenv("PUBLIC_BASE_URL", "http://testserver")

    sys.modules.pop("app.app", None)
    module = importlib.import_module("app.app")
    module.init_auth_db()
    return module


@pytest.fixture()
def client(app_module):
    return TestClient(app_module.app)


def create_admin_and_login(client):
    response = client.post(
        "/setup-admin",
        data={
            "first_name": "Test",
            "last_name": "Admin",
            "email": "admin@example.com",
            "email_verified": "true",
            "password": "password123",
            "confirm_password": "password123",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    return response
```

---

## 16.3 Update redirect tests

FastAPI/Starlette redirects use status `307` by default if not specified.

Use explicit `303` in app code.

Tests should usually use:

```python
response = client.get("/", follow_redirects=False)
assert response.status_code in {302, 303, 307}
assert response.headers["location"].endswith("/setup-admin")
```

After standardizing `redirect_to(..., status_code=303)`, tests can assert:

```python
assert response.status_code == 303
```

---

## 16.4 Update file upload tests

Current Flask style:

```python
client.post(
    "/upload-history",
    data={"history_csv_file": (BytesIO(csv_bytes), "history.csv")},
    content_type="multipart/form-data",
)
```

Target FastAPI TestClient style:

```python
client.post(
    "/upload-history",
    files={
        "history_csv_file": ("history.csv", csv_bytes, "text/csv"),
    },
)
```

DAT upload:

```python
client.post(
    "/upload-workout",
    files={
        "dat_file": ("AARON.DAT", dat_bytes, "text/plain"),
    },
)
```

Avatar upload:

```python
client.post(
    "/account",
    data={"action": "avatar", "avatar_size": "96"},
    files={"avatar": ("avatar.png", image_bytes, "image/png")},
)
```

---

# 17. Test files to update or create

## 17.1 `tests/test_health.py`

```python
def test_healthz(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
```

---

## 17.2 `tests/test_metrics.py`

```python
def test_metrics(client):
    response = client.get("/metrics")
    assert response.status_code == 200
    assert "schwinn_http_requests_total" in response.text
```

---

## 17.3 `tests/test_auth.py`

Test cases:

```text
GET / redirects to /setup-admin when no admin exists.
GET /setup-admin returns 200.
POST /setup-admin creates admin user.
POST /login logs user in.
GET /logout clears session.
GET protected page redirects to /login when unauthenticated.
GET /api/grafana/workouts returns 401 when unauthenticated.
```

---

## 17.4 `tests/test_admin.py`

Test cases:

```text
Non-admin cannot access /admin.
Admin can access /admin.
Admin can enable registration.
Admin can disable registration.
Admin can create user.
Admin can update user role.
Admin cannot delete the last admin.
Admin can trigger password reset email.
```

Mock email sending for password reset cases.

---

## 17.5 `tests/test_account.py`

Test cases:

```text
GET /account redirects when unauthenticated.
GET /account works when authenticated.
POST /account action=profile updates profile.
POST /account action=password validates current password.
POST /account action=password updates password.
POST /account action=avatar accepts valid image.
POST /account action=avatar rejects invalid image.
GET /account/avatar returns 404 when no avatar.
GET /account/avatar returns image/webp when avatar exists.
```

---

## 17.6 `tests/test_password_reset.py`

Test cases:

```text
Forgot password does not reveal unknown email.
Forgot password sends email for known user.
Generated reset token validates.
Invalid token renders error page.
Password reset updates password.
New password can be used to log in.
```

Mock:

```python
send_password_reset_email
```

---

## 17.7 `tests/test_workouts.py`

Keep these mostly framework-independent.

Test:

```text
extract_json_objects()
parse_dat_payload()
load_workout_data()
load_history_file()
merge_data()
save_history()
filter_data()
summarize_window()
format_distance()
format_minutes()
build_summary_cards()
parse_field_selection()
```

---

## 17.8 `tests/test_uploads.py`

Test cases:

```text
GET /upload-workout works when authenticated.
POST /upload-workout with DAT file imports rows.
POST /upload-workout with no upload and no DAT shows message.
POST /upload-workout uses disk DAT file when present.
GET /upload-history works when authenticated.
POST /upload-history rejects missing file.
POST /upload-history imports valid CSV.
POST /upload-history rejects invalid CSV.
```

---

## 17.9 `tests/test_grafana_api.py`

Test cases:

```text
GET /api/grafana/workouts requires auth.
GET /api/grafana/summary requires auth.
GET /api/grafana/workouts returns list when authenticated.
GET /api/grafana/summary returns summary object when authenticated.
Invalid field selection returns 400.
Date filters work.
Field filters work with field, field[], and fields query params.
```

---

## 17.10 `tests/test_templates.py`

Add template smoke tests to catch broken `url_for()` calls.

Test pages:

```text
/login
/register when registration is enabled
/setup-admin
/account after login
/admin after admin login
/admin/users after admin login
/
/workout-performance
/upload-workout
/upload-history
```

---

# 18. Acceptance criteria

## 18.1 Local checks

These should pass:

```bash
make test
make lint
make docker-build
```

Run app locally:

```bash
make run
```

Verify:

```bash
curl http://localhost:8080/healthz
curl http://localhost:8080/metrics
```

Expected:

```json
{"status":"ok"}
```

Metrics should include:

```text
schwinn_http_requests_total
schwinn_http_request_duration_seconds
schwinn_charts_generated_total
schwinn_file_import_duration_seconds
schwinn_auth_events_total
```

---

## 18.2 Docker checks

Build:

```bash
docker build -t schwinn:fastapi .
```

Run:

```bash
docker run --rm -p 8080:8080   -e HOST=0.0.0.0   -e PORT=8080   -e SESSION_COOKIE_SECURE=false   -v "$(pwd)/app/data:/app/app/data"   -v "$(pwd)/app/logs:/app/app/logs"   schwinn:fastapi
```

Check:

```bash
curl http://localhost:8080/healthz
curl http://localhost:8080/metrics
```

Expected:

- Uvicorn starts successfully.
- App listens on `0.0.0.0:8080`.
- `/healthz` returns `200`.
- `/metrics` returns Prometheus metrics.
- Data persists in mounted `app/data`.
- Logs persist in mounted `app/logs`.

---

## 18.3 Browser flow checks

Verify manually:

```text
Open /
Redirects to /setup-admin when no admin exists.
Create admin.
Login.
View welcome page.
Upload history CSV.
Upload DAT file.
View workout performance chart.
Download history CSV.
Open /account.
Update profile name.
Upload avatar.
Open /admin.
Enable/disable registration.
Create user.
Send password reset.
Logout.
Login again.
```

---

## 18.4 API behavior checks

Unauthenticated:

```bash
curl -i http://localhost:8080/api/grafana/workouts
```

Expected:

```text
HTTP/1.1 401 Unauthorized
```

Authenticated through browser session:

```text
/api/grafana/workouts returns JSON list.
/api/grafana/summary returns JSON object.
Invalid field selection returns HTTP 400.
```

---

# 19. Suggested commit sequence

## Commit 1: Add FastAPI dependencies

Files:

```text
pyproject.toml or requirements.txt
poetry.lock or requirements lock file
```

Changes:

```text
Add fastapi.
Add uvicorn[standard].
Add python-multipart.
Add httpx for tests.
Keep pandas, plotly, pillow, prometheus-client, itsdangerous, werkzeug.
Remove Flask if no longer used.
```

---

## Commit 2: Convert app bootstrap and middleware

Files:

```text
app/app.py
app/__init__.py
```

Changes:

```text
Replace Flask app with FastAPI app.
Add SessionMiddleware.
Add Jinja2Templates.
Mount static files.
Add render helper.
Add redirect helper.
Replace before_request/after_request with FastAPI middleware.
Convert /healthz and /metrics.
```

---

## Commit 3: Convert auth and account routes

Files:

```text
app/app.py
app/templates/*.html, if static/url_for changes are needed
tests/test_auth.py
tests/test_account.py
tests/test_admin.py
tests/test_password_reset.py
```

Changes:

```text
Convert login/logout/register.
Convert setup-admin.
Convert account/avatar.
Convert admin/admin-users.
Convert forgot/reset password.
Update sessions to request.session.
Update redirects to RedirectResponse.
Update template rendering.
```

---

## Commit 4: Convert workout and API routes

Files:

```text
app/app.py
tests/test_workouts.py
tests/test_uploads.py
tests/test_grafana_api.py
```

Changes:

```text
Convert /.
Convert /workout-performance.
Convert /upload-workout.
Convert /upload-history.
Convert /download-history.
Convert /api/grafana/workouts.
Convert /api/grafana/summary.
Update file uploads to UploadFile.
```

---

## Commit 5: Update Docker and Makefile

Files:

```text
Dockerfile
docker-compose.yml, if present
Makefile
.dockerignore
.env.example
README.md
```

Changes:

```text
Run uvicorn instead of Flask.
Expose 8080.
Set HOST=0.0.0.0.
Update Docker healthcheck.
Update make run/test/docker targets.
Document new run commands.
```

---

## Commit 6: Test hardening and cleanup

Files:

```text
tests/conftest.py
tests/*
app/app.py
```

Changes:

```text
Ensure tests isolate DATA_DIR, AUTH_DB_FILE, HISTORY_FILE, LOG_FILE.
Mock SMTP in password tests.
Smoke test template rendering.
Verify unauthenticated /api/* returns 401.
Verify unauthenticated page routes redirect to /login.
Verify no-admin bootstrap redirects to /setup-admin.
```

---

# 20. Post-migration optional refactor

After the FastAPI migration is complete and tests pass, split the app into modules:

```text
app/
  __init__.py
  main.py
  core/
    config.py
    logging.py
    metrics.py
  auth/
    db.py
    service.py
    routes.py
  workouts/
    parsing.py
    history.py
    charts.py
    routes.py
  templates/
  static/
tests/
```

Recommended separation:

```text
core/config.py       Environment and path settings.
core/logging.py      Logging setup.
core/metrics.py      Prometheus counters/histograms.
auth/db.py           SQLite connection and init.
auth/service.py      Users, passwords, sessions, reset tokens.
auth/routes.py       Login/register/admin/account routes.
workouts/parsing.py  DAT and CSV parsing.
workouts/history.py  History load/save/merge/filter.
workouts/charts.py   Plotly chart generation.
workouts/routes.py   Workout pages and Grafana API routes.
main.py              FastAPI app creation and middleware.
```

Do not do this until the compatibility migration is working.

---

# 21. Important implementation notes

- Keep the SQLite schema unchanged.
- Keep existing metric names unchanged.
- Keep existing route paths unchanged.
- Preserve template route names with explicit `name=` in route decorators.
- Use `request.session` instead of Flask `session`.
- Use `request.url_for()` instead of Flask `url_for()`.
- Include `request` in every template context.
- Use `python-multipart` for forms and uploads.
- Use `UploadFile` for file uploads.
- Use `Response(..., media_type=...)` instead of Flask `Response(..., mimetype=...)`.
- Use `JSONResponse(..., status_code=...)` for custom error status JSON.
- Use `RedirectResponse(..., status_code=303)` for POST redirects.
- Prefer sync `def` routes unless `await upload_file.read()` is needed.
- Do not make major architecture changes in the first migration.
