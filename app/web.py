"""Template and response helpers."""

from __future__ import annotations

from urllib.parse import urlencode

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app import config
from app.services.auth import current_user
from app.services.avatar import user_has_avatar, user_initials
from app.services.users import admin_exists, display_user_name, is_registration_enabled


def template_url_for(request: Request, name: str, **path_params: object) -> str:
    """Build path-only URLs for templates."""
    return request.url_for(name, **path_params).path


def template_url_context(request: Request) -> dict[str, object]:
    """Expose URL helpers to Jinja templates."""
    return {"url_for": lambda name, **path_params: template_url_for(request, name, **path_params)}


templates = Jinja2Templates(directory=str(config.TEMPLATES_DIR), context_processors=[template_url_context])


def render(
    request: Request,
    template_name: str,
    context: dict[str, object] | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    """Render a template with shared application context."""
    page_context = dict(context or {})
    page_context.update(
        {
            "request": request,
            "current_user": current_user(request),
            "display_user_name": display_user_name,
            "user_has_avatar": user_has_avatar,
            "user_initials": user_initials,
            "registration_enabled": is_registration_enabled(),
            "admin_exists": admin_exists(),
        }
    )
    return templates.TemplateResponse(request, template_name, page_context, status_code=status_code)


def redirect_to(url: str, status_code: int = 302) -> RedirectResponse:
    """Build a redirect response."""
    return RedirectResponse(url=url, status_code=status_code)


def route_url(request: Request, name: str, **params: object) -> str:
    """Build a route path with optional query parameters."""
    path = request.url_for(name).path
    clean_params = {key: value for key, value in params.items() if value is not None}
    return f"{path}?{urlencode(clean_params, safe='@/')}" if clean_params else path
