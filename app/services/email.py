"""Password reset email helpers."""

from __future__ import annotations

import smtplib
from email.message import EmailMessage

from fastapi import Request

from app import config
from app.services.auth import PasswordResetTokenError, email_audit_id
from app.state import get_logger


def send_password_reset_email(email: str, reset_link: str) -> None:
    """Send or log a password reset email."""
    message = EmailMessage()
    message["Subject"] = "Reset your Schwinn password"
    message["From"] = config.MAIL_FROM
    message["To"] = email
    message.set_content(
        "\n".join(
            [
                "A password reset was requested for your Schwinn account.",
                "",
                f"Reset your password using this link: {reset_link}",
                "",
                f"This link expires in {config.PASSWORD_RESET_MAX_AGE_SECONDS // 60} minutes.",
                "If you did not request a reset, you can ignore this email.",
            ]
        )
    )

    if not config.MAIL_SERVER:
        get_logger().info(
            "Password reset email not sent because MAIL_SERVER is not configured: recipient_id=%s",
            email_audit_id(email),
        )
        return

    if config.MAIL_USE_SSL:
        smtp = smtplib.SMTP_SSL(config.MAIL_SERVER, config.MAIL_PORT, timeout=10)
    else:
        smtp = smtplib.SMTP(config.MAIL_SERVER, config.MAIL_PORT, timeout=10)

    with smtp:
        if config.MAIL_USE_TLS and not config.MAIL_USE_SSL:
            smtp.starttls()
        if config.MAIL_USERNAME:
            smtp.login(config.MAIL_USERNAME, config.MAIL_PASSWORD)
        smtp.send_message(message)


def build_reset_link(request: Request | str, token: str | None = None) -> str:
    """Build an absolute or request-based password reset link."""
    if token is None:
        token = str(request)
        if config.PUBLIC_BASE_URL:
            return f"{config.PUBLIC_BASE_URL}/reset-password/{token}"
        raise PasswordResetTokenError("Request is required when PUBLIC_BASE_URL is not configured.")
    reset_path = request.url_for("reset_password", token=token).path
    if config.PUBLIC_BASE_URL:
        return f"{config.PUBLIC_BASE_URL}{reset_path}"
    return str(request.url_for("reset_password", token=token))
