"""Application logging and audit helpers."""

from __future__ import annotations

import logging as std_logging
from logging.handlers import RotatingFileHandler

from app import config
from app.bootstrap.metrics import AUTH_EVENT_COUNT
from app.services.auth import email_audit_id
from app.state import get_app


def configure_logging() -> None:
    """Configure application and Uvicorn logging handlers."""
    config.LOG_DIR.mkdir(parents=True, exist_ok=True)

    formatter = std_logging.Formatter(
        "%(asctime)s %(levelname)s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = RotatingFileHandler(
        config.LOG_FILE,
        maxBytes=100 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)

    stream_handler = std_logging.StreamHandler()
    stream_handler.setFormatter(formatter)

    logger = get_app().logger
    logger.setLevel(std_logging.INFO)
    logger.handlers = []
    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)
    logger.propagate = False

    uvicorn_logger = std_logging.getLogger("uvicorn")
    uvicorn_logger.setLevel(std_logging.INFO)
    uvicorn_logger.handlers = [file_handler, stream_handler]
    uvicorn_logger.propagate = False


def audit_auth_event(action: str, email: str, result: str, *, actor_email: str = "", details: str = "") -> None:
    """Record authentication audit events in metrics and logs."""
    AUTH_EVENT_COUNT.labels(action, result).inc()
    subject_id = email_audit_id(email)
    actor_id = email_audit_id(actor_email)
    log_method = get_app().logger.info if result == "success" else get_app().logger.warning
    log_method(
        "auth_event action=%s result=%s subject_id=%s actor_id=%s details=%s",
        action,
        result,
        subject_id,
        actor_id,
        details,
    )
