from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware


@asynccontextmanager
async def lifespan(_app: FastAPI):
    from app import app as legacy

    legacy.init_auth_db()
    yield


def create_app() -> FastAPI:
    from app import app as legacy
    from app.routes import account, admin, auth, grafana, health, workouts

    fastapi_app = FastAPI(title="Schwinn", version="1.0.0", lifespan=lifespan)
    fastapi_app.secret_key = legacy.secret_key
    fastapi_app.logger = legacy.logging.getLogger("schwinn")

    legacy.app = fastapi_app
    legacy.configure_logging()

    if legacy.STATIC_DIR.exists():
        fastapi_app.mount("/static", StaticFiles(directory=str(legacy.STATIC_DIR)), name="static")

    fastapi_app.middleware("http")(legacy.auth_and_metrics_middleware)
    fastapi_app.add_middleware(
        SessionMiddleware,
        secret_key=legacy.secret_key,
        https_only=legacy.SESSION_COOKIE_SECURE,
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
