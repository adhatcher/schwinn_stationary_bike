"""FastAPI application factory and lifespan setup."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app import config
from app.db import init_auth_db
from app.logging import configure_logging
from app.middleware import auth_and_metrics_middleware
from app.state import set_app


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Initialize application state for the FastAPI lifespan."""
    init_auth_db()
    yield


def create_app() -> FastAPI:
    """Build and configure the Schwinn FastAPI application."""
    from app.routes import account, admin, auth, grafana, health, workouts

    fastapi_app = FastAPI(title="Schwinn", version="1.0.0", lifespan=lifespan)
    fastapi_app.secret_key = config.secret_key
    import logging

    fastapi_app.logger = logging.getLogger("schwinn")

    set_app(fastapi_app)
    configure_logging()

    if config.STATIC_DIR.exists():
        fastapi_app.mount("/static", StaticFiles(directory=str(config.STATIC_DIR)), name="static")

    fastapi_app.middleware("http")(auth_and_metrics_middleware)
    fastapi_app.add_middleware(
        SessionMiddleware,
        secret_key=config.secret_key,
        https_only=config.SESSION_COOKIE_SECURE,
        same_site="lax",
        session_cookie="session",
    )

    for router in (
        health.router,
        auth.router,
        account.router,
        admin.router,
        grafana.router,
        workouts.router,
    ):
        fastapi_app.include_router(router)

    return fastapi_app
