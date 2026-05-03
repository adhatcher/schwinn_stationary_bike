"""HTTP middleware for auth enforcement and request metrics."""

from __future__ import annotations

from time import perf_counter

from fastapi import Request
from fastapi.responses import JSONResponse

from app import config
from app.bootstrap.metrics import REQUEST_COUNT, REQUEST_LATENCY
from app.db import init_auth_db
from app.services.auth import current_user
from app.services.users import admin_exists
from app.web import redirect_to, route_url


async def auth_and_metrics_middleware(request: Request, call_next):
    """Enforce authentication and collect request metrics."""
    request.state.request_start = perf_counter()

    init_auth_db()
    path = request.url.path

    if not admin_exists() and path not in config.BOOTSTRAP_ALLOWED_PATHS and not path.startswith("/static"):
        response = redirect_to(route_url(request, "setup_admin"))
    elif path.startswith("/static") or path in config.PUBLIC_PATHS or path.startswith("/reset-password/"):
        response = await call_next(request)
    elif current_user(request) is not None:
        response = await call_next(request)
    elif path.startswith("/api/"):
        response = JSONResponse({"error": "Authentication required."}, status_code=401)
    else:
        next_path = path
        if request.url.query:
            next_path = f"{next_path}?{request.url.query}"
        response = redirect_to(route_url(request, "login", next=next_path))

    endpoint = request.scope.get("endpoint")
    endpoint_name = getattr(endpoint, "__name__", path)
    REQUEST_COUNT.labels(request.method, endpoint_name, str(response.status_code)).inc()
    REQUEST_LATENCY.labels(request.method, endpoint_name).observe(perf_counter() - request.state.request_start)
    return response
