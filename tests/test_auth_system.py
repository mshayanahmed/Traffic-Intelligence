"""
Traffic Intelligence - authentication & authorization test suite.

Covers the auth test plan: signup/verification/OTP, login gating, logout,
route protection, admin security, /api/config and the heatmap regression.
Run with:  python -m pytest tests/test_auth_system.py
"""

import os
import sqlite3
import tempfile
import time

import pytest

# ---------------------------------------------------------------------------
# Test environment - MUST be configured before any backend module is imported.
# ---------------------------------------------------------------------------
_TMP = tempfile.mkdtemp(prefix="ti_auth_test_")
os.environ["DATABASE_PATH"] = os.path.join(_TMP, "test_auth.db")
os.environ["CORS_ORIGINS"] = "http://localhost:5000"
os.environ["TI_ADMIN_EMAIL"] = "admin@traffic-intelligence.com"
os.environ["TI_ADMIN_PASSWORD"] = "TestAdmin-Initial1"
os.environ["TI_OTP_RESEND_COOLDOWN"] = "0"
os.environ["TI_OTP_MAX_SENDS_PER_HOUR"] = "50"
os.environ["TI_OTP_TTL_SECONDS"] = "600"

import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "backend"))

import app as app_module          # noqa: E402  (backend package root)
import auth as auth_module        # noqa: E402
import email_service              # noqa: E402

USER_PASSWORD = "Passw0rd123"
ADMIN_EMAIL = "admin@traffic-intelligence.com"
ADMIN_PASSWORD = "TestAdmin-Initial1"


@pytest.fixture()
def client():
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


def _issue_code(email, purpose="verify_email"):
    ok, result = auth_module.issue_otp(email, purpose)
    assert ok, result
    return result


def _signup(client, email, password=USER_PASSWORD, name="Shayan"):
    return client.post("/api/auth/signup", json={
        "name": name, "email": email, "password": password,
        "confirm_password": password,
    })


def _signup_and_verify(client, email, password=USER_PASSWORD):
    resp = _signup(client, email=email, password=password)
    assert resp.status_code == 200
    code = _issue_code(email)
    resp = client.post("/api/auth/verify-email", json={"email": email, "code": code})
    assert resp.status_code == 200


def _expire_otps(email, purpose="verify_email"):
    conn = sqlite3.connect(os.environ["DATABASE_PATH"])
    try:
        conn.execute("UPDATE ti_otp_codes SET expires_at = ? "
                     "WHERE identifier = ? AND purpose = ?",
                     (time.time() - 10, email, purpose))
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# A. Signup
# ---------------------------------------------------------------------------
def test_signup_creates_unverified_standard_user_no_session(client):
    resp = _signup(client, "shayan@example.com")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["success"] is True
    assert "verification code" in body["message"].lower()
    # No auto-login: the session must NOT be authenticated.
    assert client.get("/api/auth/me").status_code == 401


def test_signup_role_cannot_be_self_selected(client):
    _signup(client, "escalate@example.com")
    conn = sqlite3.connect(os.environ["DATABASE_PATH"])
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT role FROM ti_users WHERE email = ?",
                           ("escalate@example.com",)).fetchone()
        assert row["role"] == "standard"
    finally:
        conn.close()


def test_signup_rejects_duplicate_email(client):
    _signup(client, "dup@example.com")
    assert _signup(client, "dup@example.com").status_code == 409


def test_signup_password_rules(client):
    assert _signup(client, "weak@example.com", password="short1").status_code == 400
    assert _signup(client, "weak2@example.com", password="onlyletters").status_code == 400


# ---------------------------------------------------------------------------
# B/C. OTP behaviour
# ---------------------------------------------------------------------------
def test_verify_wrong_then_correct_otp(client):
    _signup(client, "otp@example.com")
    resp = client.post("/api/auth/verify-email",
                       json={"email": "otp@example.com", "code": "000000"})
    assert resp.status_code == 400
    assert "invalid" in resp.get_json()["error"].lower()
    code = _issue_code("otp@example.com")
    resp = client.post("/api/auth/verify-email",
                       json={"email": "otp@example.com", "code": code})
    assert resp.status_code == 200


def test_otp_single_use(client):
    _signup(client, "reuse@example.com")
    code = _issue_code("reuse@example.com")
    assert client.post("/api/auth/verify-email",
                       json={"email": "reuse@example.com", "code": code}).status_code == 200
    assert client.post("/api/auth/verify-email",
                       json={"email": "reuse@example.com", "code": code}).status_code == 400


def test_expired_otp_rejected_and_resend_works(client):
    _signup(client, "expire@example.com")
    code = _issue_code("expire@example.com")
    _expire_otps("expire@example.com")
    resp = client.post("/api/auth/verify-email",
                       json={"email": "expire@example.com", "code": code})
    assert resp.status_code == 400
    assert "expired" in resp.get_json()["error"].lower()
    new_code = _issue_code("expire@example.com")
    assert client.post("/api/auth/verify-email",
                       json={"email": "expire@example.com", "code": new_code}).status_code == 200


def test_resend_endpoint_is_generic(client):
    resp = client.post("/api/auth/resend-verification",
                       json={"email": "ghost@example.com"})
    assert resp.status_code == 200  # never reveals account existence


# ---------------------------------------------------------------------------
# D/E. Login gating
# ---------------------------------------------------------------------------
def test_unverified_login_blocked(client):
    _signup(client, "unver@example.com")
    resp = client.post("/api/auth/login",
                       json={"email": "unver@example.com",
                             "password": USER_PASSWORD})
    assert resp.status_code == 403
    body = resp.get_json()
    assert body["code"] == "email_unverified"
    assert "verify your email" in body["error"].lower()
    assert client.get("/api/auth/me").status_code == 401


def test_verified_user_login_and_dashboard_access(client):
    _signup_and_verify(client, "login@example.com")
    resp = client.post("/api/auth/login",
                       json={"email": "login@example.com",
                             "password": USER_PASSWORD})
    assert resp.status_code == 200
    data = resp.get_json()["data"]
    assert data["role"] == "standard"
    assert "password" not in resp.get_data(as_text=True).lower()
    assert client.get("/api/auth/me").status_code == 200
    assert client.get("/api/sessions").status_code == 200


def test_login_wrong_password(client):
    _signup_and_verify(client, "wrongpw@example.com")
    assert client.post("/api/auth/login",
                       json={"email": "wrongpw@example.com",
                             "password": "Nope0Nope"}).status_code == 401


# ---------------------------------------------------------------------------
# F. Logout
# ---------------------------------------------------------------------------
def test_logout_clears_session_and_relogin_works(client):
    _signup_and_verify(client, "logout@example.com")
    assert client.post("/api/auth/login",
                       json={"email": "logout@example.com",
                             "password": USER_PASSWORD}).status_code == 200
    assert client.get("/api/auth/me").status_code == 200
    assert client.post("/api/auth/logout").status_code == 200
    assert client.get("/api/auth/me").status_code == 401
    assert client.get("/api/sessions").status_code == 401
    resp = client.post("/api/auth/login",
                       json={"email": "logout@example.com",
                             "password": USER_PASSWORD})
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# G. Route protection
# ---------------------------------------------------------------------------
def test_logged_out_routes_redirect_to_login(client):
    resp = client.get("/")
    assert resp.status_code == 302
    assert "/login.html" in resp.headers["Location"]


def test_health_stays_public(client):
    assert client.get("/api/health").status_code == 200


def test_normal_user_cannot_access_admin_api(client):
    _signup_and_verify(client, "norm@example.com")
    client.post("/api/auth/login",
                json={"email": "norm@example.com", "password": USER_PASSWORD})
    for path in ("/api/admin/users", "/api/admin/overview",
                 "/api/admin/audit-logs"):
        assert client.get(path).status_code == 403, path
    assert client.put("/api/admin/users/x/status",
                      json={"status": "disabled"}).status_code == 403


def test_admin_can_access_admin_api(client):
    assert client.post("/api/auth/login",
                       json={"email": ADMIN_EMAIL,
                             "password": ADMIN_PASSWORD}).status_code == 200
    assert client.get("/api/admin/users").status_code == 200
    assert client.get("/api/admin/overview").status_code == 200
    assert client.get("/api/admin/audit-logs").status_code == 200


# ---------------------------------------------------------------------------
# H. Admin security
# ---------------------------------------------------------------------------
def test_admin_bootstrap_account_exists_and_is_admin(client):
    assert client.post("/api/auth/login",
                       json={"email": ADMIN_EMAIL,
                             "password": ADMIN_PASSWORD}).status_code == 200
    me = client.get("/api/auth/me").get_json()["data"]
    assert me["role"] == "admin"
    client.post("/api/auth/logout")


def test_admin_user_list_has_no_password_data(client):
    _signup_and_verify(client, "safe@example.com")
    client.post("/api/auth/login",
                json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    body = client.get("/api/admin/users").get_data(as_text=True).lower()
    assert "password_hash" not in body
    assert "pbkdf2" not in body
    client.post("/api/auth/logout")


def test_admin_password_change(client):
    assert client.post("/api/auth/login",
                       json={"email": ADMIN_EMAIL,
                             "password": ADMIN_PASSWORD}).status_code == 200
    new_password = "BrandNew-Admin2"
    resp = client.post("/api/auth/change-password", json={
        "current_password": ADMIN_PASSWORD,
        "new_password": new_password,
        "confirm_password": new_password,
    })
    assert resp.status_code == 200
    client.post("/api/auth/logout")
    assert client.post("/api/auth/login",
                       json={"email": ADMIN_EMAIL,
                             "password": ADMIN_PASSWORD}).status_code == 401
    assert client.post("/api/auth/login",
                       json={"email": ADMIN_EMAIL,
                             "password": new_password}).status_code == 200
    assert client.get("/api/auth/me").status_code == 200
    # restore the original password so other tests using ADMIN_PASSWORD pass
    resp = client.post("/api/auth/change-password", json={
        "current_password": new_password,
        "new_password": ADMIN_PASSWORD,
        "confirm_password": ADMIN_PASSWORD,
    })
    assert resp.status_code == 200
    client.post("/api/auth/logout")


def test_admin_role_requires_admin_email_domain(client):
    client.post("/api/auth/login",
                json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    resp = client.put("/api/admin/users/safe@example.com/role",
                      json={"role": "admin"})
    assert resp.status_code == 400
    conn = sqlite3.connect(os.environ["DATABASE_PATH"])
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT role FROM ti_users WHERE email = ?",
                           ("safe@example.com",)).fetchone()
        assert row["role"] == "standard"
    finally:
        conn.close()
    client.post("/api/auth/logout")


def test_admin_can_disable_and_enable_user(client):
    _signup_and_verify(client, "toggle@example.com")
    client.post("/api/auth/login",
                json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert client.put("/api/admin/users/toggle@example.com/status",
                      json={"status": "disabled"}).status_code == 200
    client.post("/api/auth/logout")
    assert client.post("/api/auth/login",
                       json={"email": "toggle@example.com",
                             "password": USER_PASSWORD}).status_code == 403
    client.post("/api/auth/login",
                json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert client.put("/api/admin/users/toggle@example.com/status",
                      json={"status": "active"}).status_code == 200
    client.post("/api/auth/logout")
    assert client.post("/api/auth/login",
                       json={"email": "toggle@example.com",
                             "password": USER_PASSWORD}).status_code == 200
    client.post("/api/auth/logout")


# ---------------------------------------------------------------------------
# I. /api/config
# ---------------------------------------------------------------------------
def test_config_requires_authentication(client):
    assert client.get("/api/config").status_code == 401


def test_config_get_ok_for_normal_user_no_sensitive_keys(client):
    _signup_and_verify(client, "cfg@example.com")
    client.post("/api/auth/login",
                json={"email": "cfg@example.com", "password": USER_PASSWORD})
    resp = client.get("/api/config")
    assert resp.status_code == 200
    text = resp.get_data(as_text=True).lower()
    for secret in ("secret", "password", "smtp", "api_key", "token"):
        assert secret not in text, secret
    client.post("/api/auth/logout")


def test_config_put_still_restricted(client):
    _signup_and_verify(client, "cfgput@example.com")
    client.post("/api/auth/login",
                json={"email": "cfgput@example.com", "password": USER_PASSWORD})
    assert client.put("/api/config",
                      json={"confidence_threshold": 0.3}).status_code == 403
    client.post("/api/auth/logout")
    client.post("/api/auth/login",
                json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert client.put("/api/config",
                      json={"confidence_threshold": 0.3}).status_code == 200
    client.post("/api/auth/logout")


# ---------------------------------------------------------------------------
# J. Heatmap regression
# ---------------------------------------------------------------------------
def test_dashboard_js_has_no_loadheatmap_reference():
    import io
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(here, "..", "frontend", "js", "dashboard.js")
    with io.open(path, "r", encoding="utf-8") as fh:
        src = fh.read()
    assert "loadHeatmap" not in src
    assert "function fetchHeatmap" in src
    assert "fetchHeatmap();" in src
    assert 'if (route === "admin")' in src


def test_heatmap_endpoint_works(client):
    _signup_and_verify(client, "heat@example.com")
    client.post("/api/auth/login",
                json={"email": "heat@example.com", "password": USER_PASSWORD})
    assert client.get("/api/sessions/live/heatmap").status_code == 200
    client.post("/api/auth/logout")


# ---------------------------------------------------------------------------
# Email service / password reset
# ---------------------------------------------------------------------------
def test_email_service_disabled_without_smtp():
    assert email_service.smtp_configured() is False


def test_forgot_password_generic_response(client):
    resp = client.post("/api/auth/forgot-password",
                       json={"email": "nobody@example.com"})
    assert resp.status_code == 200
    assert "if an account exists" in resp.get_json()["message"].lower()


def test_password_reset_flow(client):
    _signup_and_verify(client, "reset@example.com")
    code = _issue_code("reset@example.com", purpose="password_reset")
    resp = client.post("/api/auth/reset-password", json={
        "email": "reset@example.com", "code": code,
        "new_password": "FreshPass9", "confirm_password": "FreshPass9",
    })
    assert resp.status_code == 200
    # old password rejected, new one accepted
    assert client.post("/api/auth/login",
                       json={"email": "reset@example.com",
                             "password": USER_PASSWORD}).status_code == 401
    assert client.post("/api/auth/login",
                       json={"email": "reset@example.com",
                             "password": "FreshPass9"}).status_code == 200
    client.post("/api/auth/logout")




