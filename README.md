# Flask Schwinn Dashboard

![schwinn-fitness](app/static/schwinn-logo.png)

Flask replacement for `read_file.py` that:

- reads `<user>.DAT`
- parses workout JSON blocks using the same workout calculations
- merges imported workouts into `Workout_History.csv`
- lets you pick date range and fields to graph dynamically
- requires account login before users can view or modify dashboard data
- supports email/password registration and password reset by email
- exposes health and Prometheus metrics endpoints
- logs to rotating files (`100MB`, `5` backups)

## Data columns

The parsed workout columns are:

- `Workout_Date`
- `Distance`
- `Avg_Speed`
- `Workout_Time` (minutes)
- `Total_Calories`
- `Heart_Rate`
- `RPM`
- `Level`

## Poetry + Make (recommended)

```bash
make install
make test
make run
```

Then open `http://localhost:8080`.

## Local secrets

Keep real credentials in a local `.env` file that is not committed. A safe workflow is:

```bash
cp .env.example .env
```

Then edit `.env` with your real values. The app loads `.env` automatically at startup, so you do not need to export each variable manually.

## Authentication setup

The dashboard now protects all pages, imports, downloads, and Grafana APIs behind user login. Public routes remain:

- `GET /healthz`
- `GET /metrics`
- `GET|POST /login`
- `GET|POST /register`
- `GET|POST /forgot-password`
- `GET|POST /reset-password/<token>`

Set a strong secret key before running outside local development:

```bash
export SECRET_KEY="replace-with-a-long-random-secret"
```

If you are using `.env`, put that value there instead.

User accounts are stored in a local SQLite file at `app/data/users.db` by default. You can override that path with:

```bash
export AUTH_DB_FILE=/custom/path/users.db
```

When the app is behind a reverse proxy like SWAG, set the public HTTPS URL so password reset emails contain the correct external link:

```bash
export PUBLIC_BASE_URL="https://schwinn.aaronhatcher.com"
```

## Password reset email

If SMTP is configured, the app sends password reset emails. Otherwise it logs the reset link to the app log for local development.

Supported mail settings:

```bash
export MAIL_SERVER="smtp.example.com"
export MAIL_PORT="587"
export MAIL_USERNAME="smtp-user"
export MAIL_PASSWORD="smtp-password"
export MAIL_FROM="no-reply@example.com"
export MAIL_USE_TLS="true"
export MAIL_USE_SSL="false"
```

The app also accepts these alias names if you prefer them:

```bash
export SMTP_HOST="smtp.example.com"
export SMTP_PORT="587"
export SMTP_NAME="smtp-user@example.com"
export SMTP_PASSWORD="smtp-password"
export SMTP_SECURE="tls"
export MAIL_FROM_ADDRESS="no-reply@example.com"
```

For this repo, the easiest path is to keep those values in your local `.env` and commit only [.env.example](/Users/aaron/Documents/Development/app_code/PythonCode/schwinn/.env.example).

Optional reset settings:

```bash
export PASSWORD_RESET_MAX_AGE_SECONDS="3600"
export SESSION_COOKIE_SECURE="true"
```

## Operational endpoints

- Health check: `GET /healthz`
- Metrics: `GET /metrics`
- Grafana workouts API: `GET /api/grafana/workouts?field=Distance&from=2026-01-01&to=2026-01-31` (login required)
- Grafana summary API: `GET /api/grafana/summary?field=Distance&from=2026-01-01&to=2026-01-31` (login required)
- Container healthcheck uses `/healthz`

## Logging

- App logs: `app/logs/app.log` (or `/app/logs/app.log` in container)
- Rotation: `100MB` max file size, `5` historical log files

## Run as Docker container (AMD)

```bash
make build
docker run --rm \
  --platform linux/amd64 \
  -e PORT=8080 \
  -e DATA_DIR=/app/data \
  -p 8080:8080 \
  -v "$(pwd)/app/data:/app/data" \
  schwinn-dashboard:latest
```

Put your bike export file at `app/data/<user>.DAT`.
`DATA_DIR` controls where uploaded files and `Workout_History.csv` are stored.
`PORT` controls the listen port inside the container.

## Docker compose

```bash
docker compose up --build
```

You can override runtime values:

```bash
PORT=9090 DATA_DIR=/app/custom-data docker compose up --build
```

## UI workflow

1. Upload `<user>.DAT` (or place file at `/app/data/AARON.DAT`).
2. Choose start/end date.
3. Select fields to include.
4. Use table header dropdown filters (date filter supports checkbox multi-select).
5. Click **Refresh Dashboard**.

Historical data is persisted in `/app/data/Workout_History.csv`.
