"""
Traffic Intelligence - authentication, authorization and account security.

Session-cookie based, backed by a SQLite users table. Passwords are hashed
with werkzeug's pbkdf2 implementation - never stored in plain text and never
returned by any API.

Key properties:
  * Email-based accounts with email verification (6-digit OTP).
  * Public signup ALWAYS creates a standard user - role can never be chosen
    or spoofed from the frontend.
  * The privileged admin is provisioned securely via environment variables
    (TI_ADMIN_EMAIL / TI_ADMIN_PASSWORD) - never hard-coded.
  * Admin-privileged accounts must use the admin email domain.
  * OTPs are cryptographically random, single-use, short-lived, stored only
    as salted hashes and protected by rate limits.
  * An audit log records security-relevant events (never secrets).
"""

import hashlib
import logging
import os
import re
import secrets
import sqlite3
import threading
import time

from flask import Blueprint, jsonify, request, session
from werkzeug.security import check_password_hash, generate_password_hash

import email_service
import session_store

logger = logging.getLogger(__name__)

bp = Blueprint("auth", __name__)

_USERNAME_MIN = 3
_PASSWORD_MIN = 8
_NAME_MIN = 2

# Role hierarchy. Each role inherits the permissions of the roles below it.
# ADMIN > MANAGER > ADVANCED > STANDARD. "standard" is the normal end user.
ROLES = ("admin", "manager", "advanced", "standard")
ROLE_LABELS = {
    "admin": "ADMIN",
    "manager": "MANAGER",
    "advanced": "ADVANCED USER",
    "standard": "STANDARD USER",
}
_DEFAULT_ROLE = "standard"

# Privileged (admin) accounts must belong to this email domain. Configurable
# via TI_ADMIN_EMAIL_DOMAIN for self-hosted deployments.
ADMIN_EMAIL_DOMAIN = (os.environ.get("TI_ADMIN_EMAIL_DOMAIN")
                      or "traffic-intelligence.com").lstrip("@").lower()
ADMIN_EMAIL = (os.environ.get("TI_ADMIN_EMAIL")
               or ("admin@" + ADMIN_EMAIL_DOMAIN)).strip().lower()

# OTP configuration (env-overridable).
OTP_LENGTH = int(os.environ.get("TI_OTP_LENGTH", "6") or 6)
OTP_TTL_SECONDS = int(os.environ.get("TI_OTP_TTL_SECONDS", "600") or 600)
OTP_MAX_ATTEMPTS = int(os.environ.get("TI_OTP_MAX_ATTEMPTS", "5") or 5)
OTP_RESEND_COOLDOWN = int(os.environ.get("TI_OTP_RESEND_COOLDOWN", "60") or 60)
OTP_MAX_SENDS_PER_HOUR = int(os.environ.get("TI_OTP_MAX_SENDS_PER_HOUR", "5") or 5)

# Login brute-force protection (in-memory, per identifier).
_LOGIN_WINDOW = 900          # 15 minutes
_LOGIN_MAX_FAILURES = 10
_login_failures = {}         # identifier -> [timestamps]
_login_lock = threading.Lock()

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _role_rank(role):
    return ROLES.index(role) if role in ROLES else -1


def _now():
    return time.time()


def _is_admin_domain(email):
    return bool(email) and email.lower().endswith("@" + ADMIN_EMAIL_DOMAIN)


# ---------------------------------------------------------------------------
# Schema (migration-safe: existing users are preserved)
# ---------------------------------------------------------------------------
_SCHEMA = """
CREATE TABLE IF NOT EXISTS ti_users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL DEFAULT 'standard',
    created_at    REAL DEFAULT (strftime('%s','now'))
);
CREATE TABLE IF NOT EXISTS ti_otp_codes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    identifier  TEXT NOT NULL,
    purpose     TEXT NOT NULL,
    salt        TEXT NOT NULL,
    code_hash   TEXT NOT NULL,
    expires_at  REAL NOT NULL,
    max_attempts INTEGER NOT NULL DEFAULT 5,
    attempts    INTEGER NOT NULL DEFAULT 0,
    consumed_at REAL,
    created_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ti_otp_ident ON ti_otp_codes(identifier, purpose);
CREATE TABLE IF NOT EXISTS ti_audit_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    actor      TEXT,
    action     TEXT NOT NULL,
    target     TEXT,
    detail     TEXT,
    created_at REAL DEFAULT (strftime('%s','now'))
);
"""

# Columns added to ti_users for pre-existing databases (each applied
# defensively - OperationalError means it already exists).
_USER_MIGRATIONS = (
    "ALTER TABLE ti_users ADD COLUMN name TEXT",
    "ALTER TABLE ti_users ADD COLUMN email TEXT",
    "ALTER TABLE ti_users ADD COLUMN email_verified INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE ti_users ADD COLUMN account_status TEXT NOT NULL DEFAULT 'active'",
    "ALTER TABLE ti_users ADD COLUMN last_login_at REAL",
    "ALTER TABLE ti_users ADD COLUMN updated_at REAL",
)


def _init_tables():
    conn = session_store.get_connection()
    try:
        conn.executescript(_SCHEMA)
        for alter in _USER_MIGRATIONS:
            try:
                conn.execute(alter)
            except sqlite3.OperationalError:
                pass  # column already exists
        # Unique email index (legacy rows without an email are unaffected).
        try:
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_ti_users_email "
                "ON ti_users(email) WHERE email IS NOT NULL")
            conn.commit()
        except sqlite3.OperationalError:
            pass
        conn.commit()
    finally:
        conn.close()


_init_tables()


def _ensure_admin_account():
    """Provision the initial administrator from environment variables.

    Never hard-codes a password: if TI_ADMIN_PASSWORD is not set, no admin is
    created and a warning is logged. Existing accounts are never overwritten.
    """
    password = os.environ.get("TI_ADMIN_PASSWORD")
    if not password:
        logger.warning(
            "TI_ADMIN_PASSWORD is not set - the admin account (%s) has not "
            "been provisioned. Set TI_ADMIN_EMAIL and TI_ADMIN_PASSWORD in "
            "the environment to bootstrap the administrator.", ADMIN_EMAIL)
        return
    if not _is_admin_domain(ADMIN_EMAIL):
        logger.error("Refusing to provision admin: %s is not on the required "
                     "admin domain @%s", ADMIN_EMAIL, ADMIN_EMAIL_DOMAIN)
        return
    conn = session_store.get_connection()
    try:
        row = conn.execute(
            "SELECT id FROM ti_users WHERE username = ? OR email = ?",
            (ADMIN_EMAIL, ADMIN_EMAIL)).fetchone()
        if row:
            return  # already provisioned - never overwrite
        conn.execute(
            """INSERT INTO ti_users
                   (username, password_hash, role, name, email,
                    email_verified, account_status, created_at, updated_at)
               VALUES (?, ?, 'admin', ?, ?, 1, 'active', ?, ?)""",
            (ADMIN_EMAIL, generate_password_hash(password),
             "Administrator", ADMIN_EMAIL, _now(), _now()))
        conn.commit()
        _audit("system", "admin_provisioned", ADMIN_EMAIL, None)
        logger.info("Admin account provisioned for %s", ADMIN_EMAIL)
    finally:
        conn.close()


_init_audit_placeholder = True  # admin provisioning runs after _audit is defined


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------
def _audit(actor, action, target=None, detail=None):
    try:
        conn = session_store.get_connection()
        try:
            conn.execute(
                "INSERT INTO ti_audit_log (actor, action, target, detail) "
                "VALUES (?, ?, ?, ?)", (actor, action, target, detail))
            conn.commit()
        finally:
            conn.close()
    except sqlite3.Error:
        logger.exception("Failed to write audit record")


def list_audit_logs(limit=200):
    conn = session_store.get_connection()
    try:
        rows = conn.execute(
            "SELECT actor, action, target, detail, created_at "
            "FROM ti_audit_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


_ensure_admin_account()


# ---------------------------------------------------------------------------
# Session / identity helpers
# ---------------------------------------------------------------------------
def current_username():
    return session.get("username")


def current_user():
    """Authenticated user info sourced from the signed-in session.

    The role is stored in the (server-signed) session at login time, so it
    cannot be tampered with from the frontend. Role changes take effect on
    the next login.
    """
    username = current_username()
    if not username:
        return None
    role = session.get("role") or _DEFAULT_ROLE
    return {
        "username": username,
        "name": session.get("name") or username,
        "email": session.get("email"),
        "role": role,
        "role_label": ROLE_LABELS.get(role, role.upper()),
        "email_verified": bool(session.get("email_verified", True)),
    }


def current_role():
    return session.get("role") or _DEFAULT_ROLE


def has_role(*roles):
    """True if the signed-in user is at least as privileged as one of the
    supplied roles. Rank decreases with privilege (admin=0 < manager=1 <
    advanced=2 < standard=3)."""
    role = current_role()
    user_rank = _role_rank(role)
    return any(user_rank <= _role_rank(r) for r in roles)


def require_auth(view):
    """Decorator: reject with 401 unless a session is signed in."""
    from functools import wraps

    @wraps(view)
    def wrapper(*args, **kwargs):
        if not current_username():
            return jsonify({"success": False, "error": "Sign in required."}), 401
        return view(*args, **kwargs)
    return wrapper


def require_role(*roles):
    """Decorator: reject the request with 403 unless the user holds a role
    at least as privileged as one of the supplied roles."""
    def decorator(view):
        from functools import wraps

        @wraps(view)
        def wrapper(*args, **kwargs):
            if not has_role(*roles):
                return jsonify({"success": False,
                                "error": "You do not have permission to perform this action."}), 403
            return view(*args, **kwargs)
        return wrapper
    return decorator


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------
def _valid_password(password):
    """At least 8 characters with at least one letter and one digit."""
    return (len(password) >= _PASSWORD_MIN
            and re.search(r"[A-Za-z]", password)
            and re.search(r"\d", password))


def _normalize_email(raw):
    email = str(raw or "").strip().lower()
    return email if _EMAIL_RE.match(email) else None


# ---------------------------------------------------------------------------
# OTP engine
# ---------------------------------------------------------------------------
def _hash_code(salt, code):
    return hashlib.sha256((salt + code).encode("utf-8")).hexdigest()


def issue_otp(identifier, purpose):
    """Create a new single-use OTP for identifier/purpose.

    Invalidates any previous outstanding code, enforces a resend cooldown and
    an hourly send cap. Returns (ok, result) where result is the plaintext
    code on success (email body only) or an error message.
    """
    now = _now()
    conn = session_store.get_connection()
    try:
        recent = conn.execute(
            """SELECT created_at FROM ti_otp_codes
               WHERE identifier = ? AND purpose = ?
               ORDER BY id DESC LIMIT ?""",
            (identifier, purpose, OTP_MAX_SENDS_PER_HOUR)).fetchall()
        if recent:
            sent_last_hour = sum(1 for r in recent if now - r["created_at"] < 3600)
            if sent_last_hour >= OTP_MAX_SENDS_PER_HOUR:
                return False, "Too many code requests. Please try again later."
            newest_age = now - recent[0]["created_at"]
            if newest_age < OTP_RESEND_COOLDOWN:
                return False, ("A code was just sent. Please wait %d seconds "
                               "before requesting another."
                               % int(OTP_RESEND_COOLDOWN - newest_age))
        # Invalidate any outstanding code for this identifier/purpose.
        conn.execute(
            """UPDATE ti_otp_codes SET consumed_at = ?
               WHERE identifier = ? AND purpose = ? AND consumed_at IS NULL""",
            (now, identifier, purpose))
        salt = secrets.token_hex(16)
        code = "".join(str(secrets.randbelow(10)) for _ in range(OTP_LENGTH))
        conn.execute(
            """INSERT INTO ti_otp_codes
                   (identifier, purpose, salt, code_hash, expires_at,
                    max_attempts, attempts, created_at)
               VALUES (?, ?, ?, ?, ?, ?, 0, ?)""",
            (identifier, purpose, salt, _hash_code(salt, code),
             now + OTP_TTL_SECONDS, OTP_MAX_ATTEMPTS, now))
        conn.commit()
        logger.info("otp_stored purpose=%s", purpose)
        _audit(identifier, "otp_issued", purpose, None)
        # The plaintext code exists only in this return path (email body).
        return True, code
    finally:
        conn.close()


def verify_otp(identifier, purpose, code):
    """Verify a single-use OTP. Returns (ok, error_message).

    Messages stay generic so nothing about the implementation leaks.
    """
    code = str(code or "").strip()
    if not code:
        return False, "Invalid verification code."
    now = _now()
    conn = session_store.get_connection()
    try:
        row = conn.execute(
            """SELECT * FROM ti_otp_codes
               WHERE identifier = ? AND purpose = ? AND consumed_at IS NULL
               ORDER BY id DESC LIMIT 1""",
            (identifier, purpose)).fetchone()
        if row is None:
            return False, "Invalid verification code."
        if row["expires_at"] < now:
            conn.execute("UPDATE ti_otp_codes SET consumed_at = ? WHERE id = ?",
                         (now, row["id"]))
            conn.commit()
            return False, "Verification code expired. Please request a new code."
        if row["attempts"] >= row["max_attempts"]:
            conn.execute("UPDATE ti_otp_codes SET consumed_at = ? WHERE id = ?",
                         (now, row["id"]))
            conn.commit()
            return False, "Too many incorrect attempts. Please request a new code."
        conn.execute("UPDATE ti_otp_codes SET attempts = attempts + 1 WHERE id = ?",
                     (row["id"],))
        if not secrets.compare_digest(row["code_hash"], _hash_code(row["salt"], code)):
            conn.commit()
            return False, "Invalid verification code."
        # Success: consume immediately (single use).
        conn.execute("UPDATE ti_otp_codes SET consumed_at = ? WHERE id = ?",
                     (now, row["id"]))
        conn.commit()
        _audit(identifier, "otp_verified", purpose, None)
        return True, None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# User storage helpers
# ---------------------------------------------------------------------------
def _safe_user_row(row):
    """Project a ti_users row to a safe dict - NEVER includes password_hash."""
    return {
        "id": row["id"],
        "username": row["username"],
        "name": row["name"] or row["username"],
        "email": row["email"],
        "role": row["role"] or _DEFAULT_ROLE,
        "role_label": ROLE_LABELS.get(row["role"], "STANDARD USER"),
        "email_verified": bool(row["email_verified"]),
        "account_status": row["account_status"] or "active",
        "created_at": row["created_at"],
        "last_login_at": row["last_login_at"],
    }


def get_user_by_identifier(identifier):
    """Look up a user by email or (legacy) username."""
    identifier = str(identifier or "").strip()
    if not identifier:
        return None
    conn = session_store.get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM ti_users WHERE email = ? OR username = ?",
            (identifier.lower(), identifier)).fetchone()
        return row
    finally:
        conn.close()


def get_user_by_id(user_id):
    conn = session_store.get_connection()
    try:
        return conn.execute("SELECT * FROM ti_users WHERE id = ?",
                            (user_id,)).fetchone()
    finally:
        conn.close()


def list_users():
    """Admin-facing user list (safe fields only - no password hashes)."""
    conn = session_store.get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM ti_users ORDER BY created_at ASC").fetchall()
        return [_safe_user_row(r) for r in rows]
    finally:
        conn.close()


def get_user_role(username):
    conn = session_store.get_connection()
    try:
        row = conn.execute("SELECT role FROM ti_users WHERE username = ?",
                           (username,)).fetchone()
        return row["role"] if row else None
    finally:
        conn.close()


def set_user_role(username, role):
    """Assign a role. Granting the admin role requires the admin email domain."""
    if role not in ROLES:
        return False
    conn = session_store.get_connection()
    try:
        row = conn.execute("SELECT email FROM ti_users WHERE username = ?",
                           (username,)).fetchone()
        if row is None:
            return False
        if role == "admin" and not _is_admin_domain(row["email"]):
            logger.warning("Blocked admin-role grant to %s: email domain not "
                           "allowed for privileged accounts.", username)
            return False
        cur = conn.execute(
            "UPDATE ti_users SET role = ?, updated_at = ? WHERE username = ?",
            (role, _now(), username))
        conn.commit()
        if cur.rowcount > 0:
            _audit(current_username() or "system", "role_change", username, role)
        return cur.rowcount > 0
    finally:
        conn.close()


def set_account_status(username, status):
    if status not in ("active", "disabled"):
        return False
    conn = session_store.get_connection()
    try:
        cur = conn.execute(
            "UPDATE ti_users SET account_status = ?, updated_at = ? "
            "WHERE username = ?", (status, _now(), username))
        conn.commit()
        if cur.rowcount > 0:
            _audit(current_username() or "system", "account_status",
                   username, status)
        return cur.rowcount > 0
    finally:
        conn.close()


def update_password(user_id, new_password):
    """Replace a password hash. Never touches identity or session data."""
    conn = session_store.get_connection()
    try:
        cur = conn.execute(
            "UPDATE ti_users SET password_hash = ?, updated_at = ? WHERE id = ?",
            (generate_password_hash(new_password), _now(), user_id))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def mark_email_verified(user_id):
    conn = session_store.get_connection()
    try:
        conn.execute(
            "UPDATE ti_users SET email_verified = 1, updated_at = ? WHERE id = ?",
            (_now(), user_id))
        conn.commit()
    finally:
        conn.close()


def touch_last_login(username):
    conn = session_store.get_connection()
    try:
        conn.execute("UPDATE ti_users SET last_login_at = ? WHERE username = ?",
                     (_now(), username))
        conn.commit()
    finally:
        conn.close()


def _register_login_failure(identifier):
    now = _now()
    with _login_lock:
        stamps = [t for t in _login_failures.get(identifier, [])
                  if now - t < _LOGIN_WINDOW]
        stamps.append(now)
        _login_failures[identifier] = stamps
        return len(stamps) >= _LOGIN_MAX_FAILURES


def _clear_login_failures(identifier):
    with _login_lock:
        _login_failures.pop(identifier, None)


def _login_throttled(identifier):
    now = _now()
    with _login_lock:
        stamps = [t for t in _login_failures.get(identifier, [])
                  if now - t < _LOGIN_WINDOW]
        return len(stamps) >= _LOGIN_MAX_FAILURES


# ---------------------------------------------------------------------------
# Routes: signup / verification
# ---------------------------------------------------------------------------
@bp.route("/signup", methods=["POST"])
def signup():
    payload = request.get_json(silent=True) or {}
    name = str(payload.get("name") or "").strip()
    email = _normalize_email(payload.get("email"))
    password = str(payload.get("password") or "")
    confirm = str(payload.get("confirm_password") or "")

    if len(name) < _NAME_MIN:
        return jsonify({"success": False,
                        "error": "Please enter your full name."}), 400
    if not email:
        return jsonify({"success": False,
                        "error": "Please enter a valid email address."}), 400
    if not _valid_password(password):
        return jsonify({"success": False,
                        "error": ("Password must be at least 8 characters and "
                                  "include a letter and a number.")}), 400
    if password != confirm:
        return jsonify({"success": False,
                        "error": "Passwords do not match."}), 400

    # Roles can never be chosen by the signup caller - this prevents
    # privilege escalation. Admins are provisioned only via environment.
    conn = session_store.get_connection()
    try:
        exists = conn.execute(
            "SELECT 1 FROM ti_users WHERE email = ? OR username = ?",
            (email, email)).fetchone()
        if exists:
            return jsonify({"success": False,
                            "error": "That email is already registered."}), 409
        conn.execute(
            """INSERT INTO ti_users
                   (username, password_hash, role, name, email,
                    email_verified, account_status, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, 0, 'active', ?, ?)""",
            (email, generate_password_hash(password), _DEFAULT_ROLE,
             name, email, _now(), _now()))
        conn.commit()
    except sqlite3.Error:
        logger.exception("Signup failed")
        return jsonify({"success": False, "error": "Sign up failed."}), 500
    finally:
        conn.close()

    _audit(email, "signup", None, None)
    logger.info("verification_request_received domain=%s", email.rsplit("@", 1)[1])
    ok, result = issue_otp(email, "verify_email")
    if not ok:
        # No OTP could be issued (e.g. hourly cap reached). Do NOT present a
        # green "verification sent" step - nothing was emailed or stored.
        _audit(email, "signup_otp_failed", None, result)
        return jsonify({"success": False, "error": result}), 429

    delivered = email_service.send_otp_email(email, name, result, "verify_email")
    if not delivered and email_service.delivery_configured():
        # Email delivery is expected in this deployment but the transport
        # (HTTP provider or SMTP) failed - report the real status instead of
        # a false success so the UI never shows a green verify step when no
        # email was actually accepted.
        logger.warning("email_delivery_failed signup email=%s", email.rsplit("@", 1)[1])
        return jsonify({
            "success": False,
            "error": "Account created, but the verification email could not be sent. Please try again later.",
        }), 502

    # No session is created here: the user must verify their email and then
    # sign in manually.
    message = ("Account created. We've sent a verification code to your email."
               if delivered else
               "Account created, but email delivery is not configured so no "
               "verification code was emailed. Use 'Resend verification code' "
               "once email is enabled.")
    return jsonify({"success": True, "message": message,
                    "data": {"email": email}})


@bp.route("/verify-email", methods=["POST"])
def verify_email():
    payload = request.get_json(silent=True) or {}
    email = _normalize_email(payload.get("email"))
    code = payload.get("code")
    if not email:
        return jsonify({"success": False,
                        "error": "Please enter a valid email address."}), 400
    user = get_user_by_identifier(email)
    if user is None or not user["email"]:
        return jsonify({"success": False, "error": "Invalid verification code."}), 400
    ok, err = verify_otp(user["email"], "verify_email", code)
    if not ok:
        return jsonify({"success": False, "error": err}), 400
    mark_email_verified(user["id"])
    _audit(user["email"], "email_verified", None, None)
    return jsonify({
        "success": True,
        "message": "Email verified successfully. Please sign in.",
    })


@bp.route("/resend-verification", methods=["POST"])
def resend_verification():
    payload = request.get_json(silent=True) or {}
    email = _normalize_email(payload.get("email"))
    if email:
        user = get_user_by_identifier(email)
        if user is not None and user["email"] and not user["email_verified"]:
            ok, result = issue_otp(user["email"], "verify_email")
            if ok:
                delivered = email_service.send_otp_email(
                    user["email"], user["name"], result, "verify_email")
                if not delivered:
                    return jsonify({
                        "success": False,
                        "error": "The verification email could not be sent. Please try again later.",
                    }), 502
            else:
                return jsonify({"success": False, "error": result}), 429
    return jsonify({
        "success": True,
        "message": "If a verification code can be sent, it is on its way.",
    })


# ---------------------------------------------------------------------------
# Routes: login / logout / me
# ---------------------------------------------------------------------------
@bp.route("/login", methods=["POST"])
def login():
    payload = request.get_json(silent=True) or {}
    identifier = str(payload.get("email") or payload.get("username") or "").strip()
    password = str(payload.get("password") or "")
    if not identifier or not password:
        return jsonify({"success": False,
                        "error": "Incorrect email or password."}), 401

    key = identifier.lower()
    if _login_throttled(key):
        return jsonify({"success": False,
                        "error": "Too many sign-in attempts. Please try again "
                                 "in a few minutes."}), 429

    row = get_user_by_identifier(identifier)
    if row is None or not check_password_hash(row["password_hash"], password):
        _register_login_failure(key)
        _audit(key, "login_failed", None, None)
        return jsonify({"success": False,
                        "error": "Incorrect email or password."}), 401

    if (row["account_status"] or "active") == "disabled":
        return jsonify({"success": False,
                        "error": "This account has been disabled. Please "
                                 "contact the administrator."}), 403

    if not row["email_verified"]:
        # Do NOT create an authenticated session.
        return jsonify({
            "success": False,
            "error": "Please verify your email before signing in.",
            "code": "email_unverified",
            "data": {"email": row["email"]},
        }), 403

    _clear_login_failures(key)
    session.clear()
    session["username"] = row["username"]
    session["role"] = row["role"] or _DEFAULT_ROLE
    session["name"] = row["name"] or row["username"]
    session["email"] = row["email"]
    session["email_verified"] = True
    touch_last_login(row["username"])
    _audit(row["username"], "login", None, None)

    # Optional greeting email - best-effort, threaded, never blocks login.
    if row["email"]:
        try:
            email_service.send_login_greeting(row["name"], row["email"])
        except Exception:
            logger.exception("Login greeting email could not be queued")

    return jsonify({"success": True, "data": current_user()})


@bp.route("/logout", methods=["POST"])
def logout():
    actor = current_username()
    session.clear()
    if actor:
        _audit(actor, "logout", None, None)
    resp = jsonify({"success": True})
    resp.headers["Cache-Control"] = "no-store"
    return resp


@bp.route("/me")
def me():
    user = current_user()
    if not user:
        return jsonify({"success": False, "error": "Not signed in"}), 401
    return jsonify({"success": True, "data": user})


# ---------------------------------------------------------------------------
# Routes: password change / reset
# ---------------------------------------------------------------------------
@bp.route("/change-password", methods=["POST"])
@require_auth
def change_password():
    payload = request.get_json(silent=True) or {}
    current_password = str(payload.get("current_password") or "")
    new_password = str(payload.get("new_password") or "")
    confirm = str(payload.get("confirm_password") or "")

    row = get_user_by_identifier(current_username())
    if row is None or not check_password_hash(row["password_hash"], current_password):
        return jsonify({"success": False,
                        "error": "Your current password is incorrect."}), 401
    if not _valid_password(new_password):
        return jsonify({"success": False,
                        "error": ("New password must be at least 8 characters "
                                  "and include a letter and a number.")}), 400
    if new_password != confirm:
        return jsonify({"success": False,
                        "error": "New passwords do not match."}), 400

    update_password(row["id"], new_password)
    _audit(row["username"], "password_changed", None, None)
    if row["email"]:
        email_service.send_password_changed_notice(row["name"], row["email"])
    # The session stays valid on purpose: the signed-in user changed their own
    # password and the session cookie is server-signed - no stale state.
    return jsonify({"success": True, "message": "Password updated."})


@bp.route("/forgot-password", methods=["POST"])
def forgot_password():
    """Always respond generically so accounts cannot be enumerated."""
    payload = request.get_json(silent=True) or {}
    email = _normalize_email(payload.get("email"))
    if email:
        user = get_user_by_identifier(email)
        if user is not None and user["email"]:
            ok, result = issue_otp(user["email"], "password_reset")
            if ok:
                email_service.send_otp_email(user["email"], user["name"],
                                             result, "password_reset")
            else:
                return jsonify({"success": False, "error": result}), 429
    return jsonify({
        "success": True,
        "message": ("If an account exists for that email, a password reset "
                    "code has been sent."),
    })


@bp.route("/reset-password", methods=["POST"])
def reset_password():
    payload = request.get_json(silent=True) or {}
    email = _normalize_email(payload.get("email"))
    code = payload.get("code")
    new_password = str(payload.get("new_password") or "")
    confirm = str(payload.get("confirm_password") or "")

    if not email:
        return jsonify({"success": False,
                        "error": "Please enter a valid email address."}), 400
    if not _valid_password(new_password):
        return jsonify({"success": False,
                        "error": ("New password must be at least 8 characters "
                                  "and include a letter and a number.")}), 400
    if new_password != confirm:
        return jsonify({"success": False,
                        "error": "New passwords do not match."}), 400

    user = get_user_by_identifier(email)
    if user is None or not user["email"]:
        return jsonify({"success": False, "error": "Invalid verification code."}), 400
    ok, err = verify_otp(user["email"], "password_reset", code)
    if not ok:
        return jsonify({"success": False, "error": err}), 400
    update_password(user["id"], new_password)
    _audit(user["username"], "password_reset", None, None)
    if user["email"]:
        email_service.send_password_changed_notice(user["name"], user["email"])
    return jsonify({
        "success": True,
        "message": "Password updated. Please sign in with your new password.",
    })





