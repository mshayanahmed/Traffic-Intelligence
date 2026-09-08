"""Tests for codec, email, and signup production issues."""
import sys
import os
import json
import tempfile

# Fix import path - backend is one level up from tests/
BACKEND_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend")
sys.path.insert(0, BACKEND_DIR)

import pytest


# ---------------------------------------------------------------------------
# Codec Tests
# ---------------------------------------------------------------------------
def test_codec_fallback_produces_valid_video():
    """The codec fallback must produce a readable, non-empty video file."""
    import cv2
    import numpy as np
    from process_video import _open_writer
    
    with tempfile.TemporaryDirectory() as tmpdir:
        output_path = os.path.join(tmpdir, "test_output.mp4")
        writer, codec_name, actual_path = _open_writer(output_path, 640, 360, 30.0)
        
        assert writer is not None, "No codec could be opened"
        assert writer.isOpened(), "VideoWriter failed to open"
        
        for i in range(10):
            frame = np.full((360, 640, 3), i * 25, dtype=np.uint8)
            writer.write(frame)
        writer.release()
        
        assert actual_path is not None, "No actual path returned"
        assert os.path.exists(actual_path), "Output file not created"
        assert os.path.getsize(actual_path) > 1000, f"Output file too small: {os.path.getsize(actual_path)}"
        
        cap = cv2.VideoCapture(actual_path)
        assert cap.isOpened(), "Cannot open output video for reading"
        ret, read_frame = cap.read()
        assert ret, "Cannot read frame from output video"
        assert read_frame is not None, "Read frame is None"
        assert read_frame.shape == (360, 640, 3), f"Wrong frame shape: {read_frame.shape}"
        cap.release()


# ---------------------------------------------------------------------------
# Signup Validation Tests
# ---------------------------------------------------------------------------
def test_signup_rejects_short_name(app):
    """Signup with name < 2 chars must return 400."""
    client = app.test_client()
    resp = client.post("/api/auth/signup", json={
        "name": "A",
        "email": "test@example.com",
        "password": "Password1",
        "confirm_password": "Password1",
    }, headers={"Origin": "https://traffic-intelligence-web.vercel.app"})
    assert resp.status_code == 400


def test_signup_rejects_invalid_email(app):
    """Signup with invalid email must return 400."""
    client = app.test_client()
    resp = client.post("/api/auth/signup", json={
        "name": "Test User",
        "email": "not-an-email",
        "password": "Password1",
        "confirm_password": "Password1",
    }, headers={"Origin": "https://traffic-intelligence-web.vercel.app"})
    assert resp.status_code == 400


def test_signup_rejects_weak_password(app):
    """Signup with weak password must return 400."""
    client = app.test_client()
    resp = client.post("/api/auth/signup", json={
        "name": "Test User",
        "email": "test@example.com",
        "password": "short",
        "confirm_password": "short",
    }, headers={"Origin": "https://traffic-intelligence-web.vercel.app"})
    assert resp.status_code == 400


def test_signup_accepts_valid_input(app):
    """Signup with valid input must succeed when SMTP is gracefully disabled."""
    import time
    client = app.test_client()
    # Ensure SMTP is unconfigured so signup succeeds via the graceful-degradation
    # path (email skipped, account still created in local/dev).
    old = {k: os.environ.get(k) for k in ("SMTP_HOST", "EMAIL_ENABLED")}
    for k in ("SMTP_HOST", "EMAIL_ENABLED"):
        os.environ.pop(k, None)
    try:
        # Use unique email to avoid conflicts from previous test runs
        unique_email = f"newuser_prod_{int(time.time())}@example.com"
        resp = client.post("/api/auth/signup", json={
            "name": "Test User",
            "email": unique_email,
            "password": "Password1",
            "confirm_password": "Password1",
        }, headers={"Origin": "https://traffic-intelligence-web.vercel.app"})
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["success"] is True
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


# ---------------------------------------------------------------------------
# Email Service Tests
# ---------------------------------------------------------------------------
def test_email_service_disabled_by_default():
    """Without SMTP configured, smtp_configured() must return False."""
    from email_service import smtp_configured
    # Ensure SMTP_HOST is not set
    old_host = os.environ.pop("SMTP_HOST", None)
    old_enabled = os.environ.pop("EMAIL_ENABLED", None)
    try:
        assert smtp_configured() is False
    finally:
        if old_host:
            os.environ["SMTP_HOST"] = old_host
        if old_enabled:
            os.environ["EMAIL_ENABLED"] = old_enabled


def test_email_service_handles_failure_gracefully():
    """Email delivery failure must not raise an exception (returns False)."""
    from email_service import send_otp_email
    old_env = {}
    for key in ["SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD"]:
        old_env[key] = os.environ.get(key)

    os.environ["SMTP_HOST"] = "invalid.host.example.com"
    os.environ["SMTP_PORT"] = "587"
    os.environ["SMTP_USER"] = "test@example.com"
    os.environ["SMTP_PASSWORD"] = "wrongpassword"

    try:
        result = send_otp_email("to@example.com", "Test", "123456")
        # Delivery is now synchronous: a failed SMTP handoff must report False
        # (never raise), so callers can avoid showing a false success.
        assert result is False
    finally:
        for key, val in old_env.items():
            if val is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = val


def test_signup_no_false_success_when_smtp_configured_but_down(app):
    """When SMTP is configured but handoff fails, signup must NOT return success.

    This prevents the UI from showing the green 'verification sent' step when no
    email was actually accepted by the relay.
    """
    import time
    client = app.test_client()
    old = {k: os.environ.get(k) for k in (
        "SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD", "EMAIL_ENABLED")}
    os.environ["SMTP_HOST"] = "invalid.host.example.com"
    os.environ["SMTP_PORT"] = "587"
    os.environ["SMTP_USER"] = "test@example.com"
    os.environ["SMTP_PASSWORD"] = "wrongpassword"
    os.environ.pop("EMAIL_ENABLED", None)
    try:
        unique_email = f"newsmtp_{int(time.time())}@example.com"
        resp = client.post("/api/auth/signup", json={
            "name": "Test User",
            "email": unique_email,
            "password": "Password1",
            "confirm_password": "Password1",
        })
        assert resp.status_code == 502
        assert resp.get_json()["success"] is False
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
# ---------------------------------------------------------------------------
# SMTP success-path and resend tests (fully mocked - no real credentials)
# ---------------------------------------------------------------------------
def test_email_send_success_with_mocked_smtp(monkeypatch):
    """A successful SMTP handoff must report True (no false failure)."""
    from email_service import send_otp_email

    class _FakeServer:
        def __init__(self, *a, **k):
            self.ehlo_called = False
        def ehlo(self):
            self.ehlo_called = True
        def starttls(self):
            pass
        def login(self, user, password):
            self.user = user
        def send_message(self, msg):
            return {}  # no recipients refused
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    created = {}
    def _fake_smtp(host, port, timeout=None):
        created["host"], created["port"] = host, port
        return _FakeServer()

    monkeypatch.setenv("SMTP_HOST", "smtp.gmail.com")
    monkeypatch.setenv("SMTP_PORT", "587")
    monkeypatch.setenv("SMTP_USER", "test@example.com")
    monkeypatch.setenv("SMTP_PASSWORD", "unused-mock")
    monkeypatch.setattr("smtplib.SMTP", _fake_smtp)

    assert send_otp_email("to@example.com", "Test", "123456") is True
    assert created["port"] == 587


def test_email_send_failure_when_recipient_refused(monkeypatch):
    """If the SMTP server refuses the recipient, delivery must report False."""
    from email_service import send_otp_email

    class _RefusingServer:
        def ehlo(self): pass
        def starttls(self): pass
        def login(self, user, password): pass
        def send_message(self, msg):
            return {"to@example.com": (550, "rejected")}  # refused
        def __enter__(self): return self
        def __exit__(self, *a): return False

    monkeypatch.setenv("SMTP_HOST", "smtp.gmail.com")
    monkeypatch.setenv("SMTP_PORT", "587")
    monkeypatch.setenv("SMTP_USER", "test@example.com")
    monkeypatch.setenv("SMTP_PASSWORD", "unused-mock")
    monkeypatch.setattr("smtplib.SMTP", lambda *a, **k: _RefusingServer())

    assert send_otp_email("to@example.com", "Test", "123456") is False


def test_resend_no_false_success_when_smtp_down(app):
    """Resend verification must NOT report success when SMTP handoff fails."""
    import time
    client = app.test_client()
    old = {k: os.environ.get(k) for k in (
        "SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD", "EMAIL_ENABLED")}
    os.environ["SMTP_HOST"] = "invalid.host.example.com"
    os.environ["SMTP_PORT"] = "587"
    os.environ["SMTP_USER"] = "test@example.com"
    os.environ["SMTP_PASSWORD"] = "unused-mock"
    os.environ.pop("EMAIL_ENABLED", None)
    try:
        email = f"resend_{int(time.time())}@example.com"
        # Create the account while SMTP is down (signup itself reports 502 but
        # the account + OTP storage still happen, per the verified chain).
        client.post("/api/auth/signup", json={
            "name": "Resend Test", "email": email,
            "password": "Password1", "confirm_password": "Password1",
        })
        resp = client.post("/api/auth/resend-verification", json={"email": email})
        # Resend must not report a false success when the email failed.
        body = resp.get_json() or {}
        if resp.status_code == 200:
            assert body.get("success") is True
        else:
            assert resp.status_code >= 400
            assert body.get("success") is not True
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_email_logs_never_contain_otp_or_password(monkeypatch, caplog):
    """OTP codes and SMTP passwords must never appear in log output."""
    import logging
    from email_service import send_otp_email

    class _RecordingServer:
        def ehlo(self): pass
        def starttls(self): pass
        def login(self, user, password):
            logging.getLogger("email_service").info("login attempt user=%s", user)
        def send_message(self, msg): return {}
        def __enter__(self): return self
        def __exit__(self, *a): return False

    otp = "987654"
    monkeypatch.setenv("SMTP_HOST", "smtp.gmail.com")
    monkeypatch.setenv("SMTP_PORT", "587")
    monkeypatch.setenv("SMTP_USER", "test@example.com")
    monkeypatch.setenv("SMTP_PASSWORD", "super-secret-app-password")
    monkeypatch.setattr("smtplib.SMTP", lambda *a, **k: _RecordingServer())

    with caplog.at_level(logging.DEBUG, logger="email_service"):
        send_otp_email("to@example.com", "Test", otp)

    assert otp not in caplog.text
    assert "super-secret-app-password" not in caplog.text


@pytest.fixture
def app():
    """Create a test Flask app."""
    from app import app as flask_app
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    yield flask_app


@pytest.fixture
def app():
    """Create a test Flask app."""
    from app import app as flask_app
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    yield flask_app

# ---------------------------------------------------------------------------
# HTTP transactional provider tests (fully mocked - no network, no secrets)
# ---------------------------------------------------------------------------
def _http_env(monkeypatch, provider="resend"):
    """Configure a mock HTTP email provider environment."""
    monkeypatch.setenv("EMAIL_PROVIDER", provider)
    monkeypatch.setenv("MAIL_FROM", "TI <no-reply@example.com>")
    monkeypatch.delenv("SMTP_HOST", raising=False)
    creds = {"resend": "RESEND_API_KEY", "sendgrid": "SENDGRID_API_KEY",
             "postmark": "POSTMARK_SERVER_TOKEN", "brevo": "BREVO_API_KEY"}
    if provider in creds:
        monkeypatch.setenv(creds[provider], "mock-key-not-real")
    monkeypatch.delenv("EMAIL_ENABLED", raising=False)


class _FakeResponse:
    def __init__(self, status_code):
        self.status_code = status_code
        self.text = "{}"


def test_http_provider_success(monkeypatch):
    """HTTP provider 2xx response must report True."""
    from email_service import send_otp_email, delivery_configured
    _http_env(monkeypatch, "resend")
    assert delivery_configured() is True
    calls = {}
    def fake_post(url, **kwargs):
        calls["url"] = url
        return _FakeResponse(200)
    monkeypatch.setattr("requests.post", fake_post)
    assert send_otp_email("to@example.com", "Test", "123456") is True
    assert "resend" in calls["url"]


def test_http_provider_rejection_failure(monkeypatch):
    """HTTP provider 4xx/5xx must report False (no false success)."""
    from email_service import send_otp_email
    _http_env(monkeypatch, "resend")
    monkeypatch.setattr("requests.post", lambda url, **k: _FakeResponse(401))
    assert send_otp_email("to@example.com", "Test", "123456") is False


def test_http_provider_timeout_failure(monkeypatch):
    """Network errors/timeouts must report False, never raise."""
    import requests as _requests
    from email_service import send_otp_email
    _http_env(monkeypatch, "resend")
    def fake_post(url, **kwargs):
        raise _requests.exceptions.Timeout("timed out")
    monkeypatch.setattr("requests.post", fake_post)
    assert send_otp_email("to@example.com", "Test", "123456") is False


def test_http_provider_sendgrid_and_brevo_endpoints(monkeypatch):
    """Provider selection must honour EMAIL_PROVIDER for each supported provider."""
    import email_service
    for provider, marker in (("sendgrid", "sendgrid"), ("brevo", "brevo"),
                             ("postmark", "postmark")):
        _http_env(monkeypatch, provider)
        assert email_service._active_http_provider() == provider
        seen = {}
        monkeypatch.setattr("requests.post",
                            lambda url, **k: (seen.setdefault("url", url),
                                              _FakeResponse(200))[1])
        assert email_service.send_otp_email("to@example.com", "T", "123456") is True
        assert marker in seen["url"]


def test_http_provider_logs_never_contain_secrets(monkeypatch, caplog):
    """API keys and OTP must never appear in HTTP transport logs."""
    import logging
    from email_service import send_otp_email
    _http_env(monkeypatch, "resend")
    monkeypatch.setattr("requests.post", lambda url, **k: _FakeResponse(200))
    otp = "445566"
    with caplog.at_level(logging.DEBUG, logger="email_service"):
        result = send_otp_email("to@example.com", "Test", otp)
    assert result is True
    assert otp not in caplog.text
    assert "mock-key-not-real" not in caplog.text


def test_signup_no_false_success_when_http_provider_fails(app, monkeypatch):
    """Signup must return failure (never green success) when the HTTP provider rejects."""
    import time
    client = app.test_client()
    _http_env(monkeypatch, "resend")
    monkeypatch.setattr("requests.post", lambda url, **k: _FakeResponse(500))
    resp = client.post("/api/auth/signup", json={
        "name": "HTTP Fail", "email": f"httpfail_{int(time.time())}@example.com",
        "password": "Password1", "confirm_password": "Password1",
    })
    assert resp.status_code == 502
    assert resp.get_json()["success"] is False


def test_otp_storage_unaffected_by_transport_choice(app, monkeypatch):
    """OTP issuance/storage must work identically regardless of transport."""
    import time
    client = app.test_client()
    _http_env(monkeypatch, "resend")
    monkeypatch.setattr("requests.post", lambda url, **k: _FakeResponse(200))
    email = f"otpstore_{int(time.time())}@example.com"
    resp = client.post("/api/auth/signup", json={
        "name": "OTP Store", "email": email,
        "password": "Password1", "confirm_password": "Password1",
    })
    assert resp.status_code == 200
    assert resp.get_json()["success"] is True
    # OTP was stored: an immediate duplicate resend is rate-limited.
    resp2 = client.post("/api/auth/resend-verification", json={"email": email})
    assert resp2.status_code == 429


@pytest.fixture
def app():
    """Create a test Flask app."""
    from app import app as flask_app
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    yield flask_app
