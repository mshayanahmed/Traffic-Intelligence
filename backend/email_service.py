"""
Traffic Intelligence - email service.

All configuration comes from environment variables - no credentials are ever
hard-coded. When SMTP is not configured the service degrades gracefully:
emails are skipped (a single line is written to the server log) and callers
treat delivery as best-effort so authentication never fails because mail is
down.

Environment variables:
    EMAIL_PROVIDER  auto|resend|sendgrid|postmark|brevo|mailgun|smtp (default auto)
    RESEND_API_KEY / SENDGRID_API_KEY / POSTMARK_SERVER_TOKEN /
    BREVO_API_KEY / MAILGUN_API_KEY + MAILGUN_DOMAIN
                    HTTP transactional providers (preferred on Render Free,
                    which blocks outbound SMTP ports 25/465/587)
    SMTP_HOST       SMTP server hostname (fallback transport; empty => no SMTP)
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

def _env(name, default=""):
    return (os.environ.get(name) or default).strip()


# ---------------------------------------------------------------------------
# HTTP transactional email providers (Render Free blocks outbound SMTP ports
# 25/465/587, so production uses an HTTP API transport instead).
#
# Provider selection (no credentials are ever hard-coded or logged):
#   EMAIL_PROVIDER   auto|resend|sendgrid|postmark|brevo|mailgun|smtp
#   RESEND_API_KEY   enables the Resend HTTP API
#   SENDGRID_API_KEY enables the SendGrid HTTP API
#   POSTMARK_SERVER_TOKEN enables Postmark
#   BREVO_API_KEY    enables Brevo
#   MAILGUN_API_KEY + MAILGUN_DOMAIN enable Mailgun
#   MAIL_FROM        sender address (used by every transport)
# With EMAIL_PROVIDER unset/"auto" the first configured HTTP provider wins;
# SMTP is used only when no HTTP provider is configured (optional fallback).
# ---------------------------------------------------------------------------
_HTTP_PROVIDERS = ("resend", "sendgrid", "postmark", "brevo", "mailgun")


def _active_http_provider():
    """Return the configured HTTP provider name, or '' when SMTP-only."""
    forced = _env("EMAIL_PROVIDER").lower()
    if forced == "smtp":
        return ""
    if forced in _HTTP_PROVIDERS:
        return forced
    # auto-detect from whichever provider credential is present
    if _env("RESEND_API_KEY"):
        return "resend"
    if _env("SENDGRID_API_KEY"):
        return "sendgrid"
    if _env("POSTMARK_SERVER_TOKEN"):
        return "postmark"
    if _env("BREVO_API_KEY"):
        return "brevo"
    if _env("MAILGUN_API_KEY") and _env("MAILGUN_DOMAIN"):
        return "mailgun"
    return ""


def delivery_configured():
    """True when ANY email transport (HTTP provider or SMTP) is configured.

    EMAIL_ENABLED=false/0 force-disables every transport; EMAIL_ENABLED=true/1
    force-enables (used when credentials are injected at runtime).
    """
    forced = _env("EMAIL_ENABLED").lower()
    if forced in ("0", "false", "no", "off"):
        return False
    if forced in ("1", "true", "yes", "on"):
        return True
    return bool(_env("SMTP_HOST")) or bool(_active_http_provider())


def _plain_text_body(message):
    body = message.get_content()
    if isinstance(body, bytes):
        body = body.decode(message.get_content_charset() or "utf-8", "replace")
    return body


def _send_http(message):
    """Deliver via the configured HTTP provider.

    Returns True only when the provider API accepted the request (HTTP 2xx).
    Never logs API keys, message bodies, OTP values, or full recipients.
    """
    provider = _active_http_provider()
    if not provider:
        return False
    recipient = _masked_recipient(message["To"])
    try:
        import requests
    except ImportError:  # pragma: no cover - requests is in requirements.txt
        logger.error("email_send_failed stage=missing_requests_dependency")
        return False

    from_email = _from_address()
    to_email = message["To"]
    subject = message["Subject"]
    text = _plain_text_body(message)

    endpoints = {
        "resend": ("https://api.resend.com/emails",
                   {"Authorization": "Bearer " + _env("RESEND_API_KEY")},
                   {"from": from_email, "to": [to_email],
                    "subject": subject, "text": text}),
        "sendgrid": ("https://api.sendgrid.com/v3/mail/send",
                     {"Authorization": "Bearer " + _env("SENDGRID_API_KEY")},
                     {"personalizations": [{"to": [{"email": to_email}]}],
                      "from": {"email": from_email}, "subject": subject,
                      "content": [{"type": "text/plain", "value": text}]}),
        "postmark": ("https://api.postmarkapp.com/email",
                     {"X-Postmark-Server-Token": _env("POSTMARK_SERVER_TOKEN"),
                      "Accept": "application/json"},
                     {"From": from_email, "To": to_email,
                      "Subject": subject, "TextBody": text}),
        "brevo": ("https://api.brevo.com/v3/smtp/email",
                  {"api-key": _env("BREVO_API_KEY"),
                   "Content-Type": "application/json"},
                  {"sender": {"email": from_email},
                   "to": [{"email": to_email}],
                   "subject": subject, "textContent": text}),
    }
    if provider == "mailgun":
        url = "https://api.mailgun.net/v3/%s/messages" % _env("MAILGUN_DOMAIN")
        headers = {}
        auth = ("api", _env("MAILGUN_API_KEY"))
        payload = {"from": from_email, "to": to_email,
                   "subject": subject, "text": text}
    else:
        url, headers, payload = endpoints[provider]
        auth = None

    logger.info("email_send_started transport=http provider=%s to=%s",
                provider, recipient)
    try:
        logger.info("http_send_started provider=%s to=%s", provider, recipient)
        resp = requests.post(url, json=payload if provider != "mailgun" else None,
                             data=payload if provider == "mailgun" else None,
                             headers=headers, auth=auth, timeout=20)
    except requests.RequestException as exc:
        logger.error("email_send_failed stage=http_network provider=%s "
                     "error_type=%s", provider, type(exc).__name__)
        return False
    if 200 <= resp.status_code < 300:
        logger.info("http_send_completed provider=%s to=%s", provider, recipient)
        return True
    # Status code only - the response body may echo payloads; never log it.
    logger.error("email_send_failed stage=http_rejected provider=%s "
                 "status=%s", provider, resp.status_code)
    return False

def _send(message):
    """Send and report whether the transport accepted every recipient."""
    recipient = _masked_recipient(message["To"])
    if not delivery_configured():
        logger.warning("email_send_failed stage=email_not_configured to=%s", recipient)
        return False
    # HTTP transactional provider takes priority (Render Free blocks SMTP
    # egress); SMTP remains available as an optional fallback transport.
    if _active_http_provider():
        return _send_http(message)
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
