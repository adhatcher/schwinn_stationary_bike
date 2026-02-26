FROM python:3.14-alpine AS builder

WORKDIR /app

ENV POETRY_VERSION=2.3.2

RUN pip install --no-cache-dir \
    "poetry==${POETRY_VERSION}" \
    "poetry-plugin-export>=1.8.0"

COPY pyproject.toml poetry.lock ./
RUN poetry export --only main --format requirements.txt --output requirements.txt --without-hashes


FROM python:3.14-alpine

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080 \
    DATA_DIR=/app/data \
    DAT_FILE=/app/data/AARON.DAT \
    HISTORY_FILE=/app/data/Workout_History.csv

COPY --from=builder /app/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt && rm -f /tmp/requirements.txt

COPY app/app.py ./app.py
COPY app/templates ./templates
COPY app/data ./data
COPY app/static ./static

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD sh -c 'wget -q -O /dev/null "http://127.0.0.1:${PORT:-8080}/healthz" || exit 1'

CMD ["sh", "-c", "gunicorn --bind 0.0.0.0:${PORT:-8080} --workers 2 app:app"]
