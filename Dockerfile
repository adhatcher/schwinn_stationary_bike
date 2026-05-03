FROM python:3.14-alpine AS builder

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:0.11.7 /uv /uvx /bin/

COPY pyproject.toml uv.lock ./
RUN uv export --frozen --no-dev --format requirements-txt --output-file requirements.txt --no-hashes


FROM python:3.14-alpine

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080 \
    DATA_DIR=/app/data \
    LOG_DIR=/app/logs \
    DAT_FILE=/app/data/AARON.DAT \
    HISTORY_FILE=/app/data/Workout_History.csv

COPY --from=builder /app/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt && rm -f /tmp/requirements.txt

COPY app ./app

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD sh -c 'wget -q -O /dev/null "http://127.0.0.1:${PORT:-8080}/healthz" || exit 1'

CMD ["sh", "-c", "uvicorn app.app:app --host 0.0.0.0 --port ${PORT:-8080}"]
