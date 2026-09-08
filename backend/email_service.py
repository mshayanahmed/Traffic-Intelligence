"""
Traffic Intelligence - email service.

All configuration comes from environment variables - no credentials are ever
hard-coded. When SMTP is not configured the service degrades gracefully:
emails are skipped (a single line is written to the server log) and callers
treat delivery as best-effort so authentication never fails because mail is
down.

Environment variables:
    SMTP_HOST       SMTP server hostname (empty => email disabled)
    SMTP_PORT       SMTP port (default 587)
    SMTP_USER       SMTP username (optional; empty => no SMTP auth)
    SMTP_PASSWORD   SMTP password (never logged, never returned)
    MAIL_FROM       From address, e.g. "Traffic Intelligence <no-reply@...>"
    APP_URL         Public application URL used in email copy
    EMAIL_ENABLED   "true"/"1" forces enable, "false"/"0" forces disable
"""

import logging
import os
import smtplib
from email.message import EmailMessage

logger = logging.getLogger(__name__)

def _env(name, default=""):
    return (os.environ.get(name) or default).strip()


def _masked_recipient(value):
    local, separator, domain = (value or "").partition("@")
    if not separator:
        return "<invalid>"
    return (local[:1] + "***@" + domain) if local else "***@" + domain


def smtp_configured():
    """True when an SMTP relay is configured and email is not force-disabled."""
    forced = _env("EMAIL_ENABLED").lower()
    if forced in ("0", "false", "no", "off"):
        return False
    if forced in ("1", "true", "yes", "on"):
        return True
    return bool(_env("SMTP_HOST"))


def _from_address():
    return _env("MAIL_FROM") or "Traffic Intelligence <no-reply@traffic-intelligence.com>"


def _app_url():
    return _env("APP_URL").rstrip("/") or "https://traffic-intelligence-web.vercel.app"


def _send(message):
    """Send and report whether the SMTP server accepted every recipient."""
    recipient = _masked_recipient(message["To"])
    if not smtp_configured():
        logger.warning("email_send_failed stage=smtp_not_configured to=%s", recipient)
        return False
    host = _env("SMTP_HOST")
    try:
        port = int(_env("SMTP_PORT", "587") or 587)
    except ValueError:
        logger.error("email_send_failed stage=invalid_smtp_port")
        return False
    user = _env("SMTP_USER")
    password = _env("SMTP_PASSWORD")
    use_ssl = port == 465

    logger.info("email_send_started to=%s", recipient)
    try:
        logger.info("smtp_connection_started host=%s port=%s", host, port)
        if use_ssl:
            server = smtplib.SMTP_SSL(host, port, timeout=20)
        else:
            server = smtplib.SMTP(host, port, timeout=20)
        with server:
            server.ehlo()
            if not use_ssl:
                try:
                    server.starttls()
                    server.ehlo()
                except smtplib.SMTPNotSupportedError:
                    logger.warning("smtp_starttls_unavailable host=%s port=%s", host, port)
            if user:
                server.login(user, password)
            refused = server.send_message(message)
        if refused:
            logger.error("email_send_failed stage=smtp_recipient_rejected to=%s", recipient)
            return False
        logger.info("smtp_send_completed to=%s", recipient)
        return True
    except (OSError, smtplib.SMTPException) as exc:
        # Log only the exception CLASS and stage - never credentials, OTPs,
        # or full tracebacks containing environment details.
        logger.error(
            "email_send_failed stage=smtp_delivery to=%s error_type=%s",
            recipient, type(exc).__name__)
        logger.debug("email_send_failed detail: %s", exc)
        return False


def _message(to, subject, body):
    msg = EmailMessage()
    msg["From"] = _from_address()
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    return msg


def send_otp_email(email, name, code, purpose="verify_email"):
    """Deliver a one-time verification code. The code is never logged."""
    if purpose == "password_reset":
        subject = "Your Traffic Intelligence password reset code"
        body = (
            f"Hi {name or 'there'},\n\n"
            "We received a request to reset the password for your Traffic\n"
            "Intelligence account. Use the verification code below to continue:\n\n"
            f"    {code}\n\n"
            "This code expires shortly and can be used only once. If you did\n"
            "not request a password reset you can safely ignore this email -\n"
            "your current password remains valid.\n\n"
            "Regards,\nTraffic Intelligence"
        )
    else:
        subject = "Your Traffic Intelligence verification code"
        body = (
            f"Hi {name or 'there'},\n\n"
            "Welcome to Traffic Intelligence! Use the verification code below\n"
            "to confirm your email address:\n\n"
            f"    {code}\n\n"
            "This code expires shortly and can be used only once.\n\n"
            "Regards,\nTraffic Intelligence"
        )
    return _send(_message(email, subject, body))


def send_login_greeting(name, email):
    """Optional post-login notification. Best-effort; never blocks login."""
    display = name or "there"
    subject = f"Welcome back to Traffic Intelligence, {display}"
    body = (
        f"Hi {display},\n\n"
        "Welcome back to Traffic Intelligence.\n\n"
        "Your account was successfully signed in. If this was not you,\n"
        "please reset your password immediately.\n\n"
        "Regards,\nTraffic Intelligence"
    )
    return _send(_message(email, subject, body))


def send_password_changed_notice(name, email):
    display = name or "there"
    subject = "Your Traffic Intelligence password was changed"
    body = (
        f"Hi {display},\n\n"
        "The password for your Traffic Intelligence account was just\n"
        "changed. If you did not make this change, please reset your\n"
        "password immediately and contact the administrator.\n\n"
        "Regards,\nTraffic Intelligence"
    )
    return _send(_message(email, subject, body))
