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
import threading
from email.message import EmailMessage

logger = logging.getLogger(__name__)

_lock = threading.Lock()


def _env(name, default=""):
    return (os.environ.get(name) or default).strip()


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
    """Send synchronously in a worker thread; never raise, never log secrets."""
    if not smtp_configured():
        logger.info("Email skipped (SMTP not configured): subject=%r to=%r",
                    message["Subject"], message["To"])
        return False
    host = _env("SMTP_HOST")
    port = int(_env("SMTP_PORT", "587") or 587)
    user = _env("SMTP_USER")
    password = _env("SMTP_PASSWORD")
    use_ssl = port == 465

    def _deliver():
        try:
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
                        pass
                if user:
                    server.login(user, password)
                server.send_message(message)
            logger.info("Email sent: subject=%r to=%r", message["Subject"], message["To"])
        except Exception:
            logger.exception("Email delivery failed (subject=%r to=%r)",
                             message["Subject"], message["To"])

    # Fire-and-forget so login/signup responses never block on SMTP.
    threading.Thread(target=_deliver, daemon=True).start()
    return True


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
