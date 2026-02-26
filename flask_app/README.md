# Flask Schwinn Dashboard

Flask replacement for `read_file.py` that:
- reads `AARON.DAT`
- parses workout JSON blocks using the same workout calculations
- merges imported workouts into `Workout_History.csv`
- lets you pick date range and fields to graph dynamically
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
cd flask_app
make install
make test
make run
```

Then open `http://localhost:8080`.

## Operational endpoints

- Health check: `GET /healthz`
- Metrics: `GET /metrics`
- Container healthcheck uses `/healthz`

## Logging

- App logs: `flask_app/logs/app.log` (or `/app/logs/app.log` in container)
- Rotation: `100MB` max file size, `5` historical log files

## Run as Docker container (AMD)

```bash
cd flask_app
make build
docker run --rm \
  --platform linux/amd64 \
  -e PORT=8080 \
  -e DATA_DIR=/app/data \
  -p 8080:8080 \
  -v "$(pwd)/data:/app/data" \
  schwinn-dashboard:latest
```

Put your bike export file at `flask_app/data/AARON.DAT`.
`DATA_DIR` controls where uploaded files and `Workout_History.csv` are stored.
`PORT` controls the listen port inside the container.

## Docker compose

```bash
cd flask_app
docker compose up --build
```

You can override runtime values:

```bash
PORT=9090 DATA_DIR=/app/custom-data docker compose up --build
```

## UI workflow

1. Upload `AARON.DAT` (or place file at `/app/data/AARON.DAT`).
2. Choose start/end date.
3. Select fields to include.
4. Optionally set table-specific date filters.
5. Click **Refresh Dashboard**.

Historical data is persisted in `/app/data/Workout_History.csv`.
