from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware


@asynccontextmanager
async def lifespan(_app: FastAPI):
    from app import app as app_module

    app_module.init_auth_db()
    yield


def create_app() -> FastAPI:
    from app import app as app_module
    from app.routes import account, admin, auth, grafana, health, workouts

    fastapi_app = FastAPI(title="Schwinn", version="1.0.0", lifespan=lifespan)
    fastapi_app.secret_key = app_module.secret_key
    fastapi_app.logger = app_module.logging.getLogger("schwinn")

    app_module.app = fastapi_app
    app_module.configure_logging()

    if app_module.STATIC_DIR.exists():
        fastapi_app.mount("/static", StaticFiles(directory=str(app_module.STATIC_DIR)), name="static")

    fastapi_app.middleware("http")(app_module.auth_and_metrics_middleware)
    fastapi_app.add_middleware(
        SessionMiddleware,
        secret_key=app_module.secret_key,
        https_only=app_module.SESSION_COOKIE_SECURE,
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
