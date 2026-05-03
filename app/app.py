#!/usr/bin/env python3
"""FastAPI application entrypoint and compatibility facade."""

from __future__ import annotations

import smtplib

from werkzeug.security import check_password_hash

from app import config
from app.bootstrap.factory import create_app
from app.config import *  # noqa: F403
from app.db import get_db_connection, get_setting, init_auth_db, set_setting
from app.logging import audit_auth_event, configure_logging
from app.middleware import auth_and_metrics_middleware
from app.routes.account import account_avatar, account_context, account_get, account_post
from app.routes.admin import (
    admin_dashboard,
    admin_dashboard_post,
    admin_required,
    admin_users_get,
    admin_users_post,
    setup_admin_context,
    setup_admin_get,
    setup_admin_post,
    update_admin_setup,
)
from app.routes.auth import (
    forgot_password_get,
    forgot_password_post,
    login_get,
    login_post,
    logout,
    register_get,
    register_post,
    reset_password_get,
    reset_password_post,
)
from app.routes.grafana import grafana_summary, grafana_workouts
from app.routes.health import healthz, metrics
from app.routes.workouts import (
    download_history,
    upload_history_get,
    upload_history_post,
    upload_workout_get,
    upload_workout_post,
    welcome,
    workout_details,
    workout_performance,
)
from app.services.auth import (
    PasswordResetTokenError,
    admin_email_is_verified,
    current_user,
    current_user_is_admin,
    email_audit_id,
    generate_password_reset_token,
    generate_temporary_password,
    login_user,
    logout_current_user,
    password_is_valid,
    password_reset_serializer,
    verify_password_reset_token,
)
from app.services.avatar import parse_avatar_size, process_avatar_upload, user_has_avatar, user_initials
from app.services.email import build_reset_link, send_password_reset_email
from app.services.users import (
    admin_count,
    admin_exists,
    compose_full_name,
    create_user,
    delete_user,
    display_user_name,
    email_is_valid,
    get_user_by_email,
    get_user_by_id,
    is_registration_enabled,
    list_users,
    normalize_email,
    normalize_name,
    set_registration_enabled,
    update_admin_identity,
    update_user_avatar,
    update_user_password,
    update_user_profile,
    update_user_role,
)
from app.web import redirect_to, render, route_url, template_url_context, template_url_for, templates
from app.workouts.analytics import (
    add_workout_detail_buckets,
    build_last_30_day_workouts,
    build_lifetime_stats,
    build_metric_breakdown,
    build_page_context,
    build_summary_cards,
    build_workout_details_context,
    current_day,
    field_label,
    filter_data,
    format_bucket_label,
    format_distance,
    format_minutes,
    normalize_workout_detail_period,
    parse_field_selection,
    parse_graph_selection,
    summarize_stats,
    summarize_window,
    workout_detail_bucket_granularity,
    workout_detail_period_bounds,
)
from app.workouts.charts import build_chart, build_metric_bar_chart, build_workout_detail_charts, metric_axis_title
from app.workouts.io import (
    extract_json_objects,
    load_history_file,
    load_workout_data,
    merge_data,
    parse_dat_payload,
    read_dat_from_disk,
    read_dat_from_upload,
    read_history_csv_from_upload,
    save_history,
)

app = create_app()


def mask_email(email: str) -> str:
    """Redact the local part of an email for display."""
    normalized = normalize_email(email)
    if not normalized or "@" not in normalized:
        return ""
    local_part, domain = normalized.split("@", 1)
    if not local_part:
        return f"***@{domain}"
    if len(local_part) == 1:
        masked_local = "*"
    elif len(local_part) == 2:
        masked_local = f"{local_part[0]}*"
    else:
        masked_local = f"{local_part[0]}***{local_part[-1]}"
    return f"{masked_local}@{domain}"


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.app:app", host=config.HOST, port=config.PORT, reload=False)
