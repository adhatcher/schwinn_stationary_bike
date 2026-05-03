"""Shared FastAPI application state."""

from __future__ import annotations

import logging

from fastapi import FastAPI

_app: FastAPI | None = None


def set_app(app: FastAPI) -> None:
    """Store the active FastAPI application instance."""
    global _app
    _app = app


def get_app() -> FastAPI:
    """Return the active FastAPI application instance."""
    if _app is None:
        raise RuntimeError("Application has not been initialized.")
    return _app


def get_logger() -> logging.Logger:
    """Return the application logger or a named fallback logger."""
    if _app is not None and hasattr(_app, "logger"):
        return _app.logger
    return logging.getLogger("schwinn")
