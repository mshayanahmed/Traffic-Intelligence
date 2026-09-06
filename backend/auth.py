"""
Traffic Intelligence - authentication (signup / login / logout).

Session-cookie based, backed by a SQLite users table. Passwords are hashed
with werkzeug's pbkdf2 implementation - never stored in plain text.
"""

import logging
import sqlite3

from flask import Blueprint, jsonify, request, session
from werkzeug.security import check_password_hash, generate_password_hash

import session_store

logger = logging.getLogger(__name__)

bp = Blueprint("auth", __name__)

_USERNAME_MIN = 3
_PASSWORD_MIN = 6

# Role hierarchy. Each role inherits the permissions of the roles below it.
# ADMIN > MANAGER > ADVANCED > STANDARD.
ROLES = ("admin", "manager", "advanced", "standard")
ROLE_LABELS = {
    "admin": "ADMIN",
    "manager": "MANAGER",
    "advanced": "ADVANCED USER",
    "standard": "STANDARD USER",
}
_DEFAULT_ROLE = "standard"


def _role_rank(role):
    return ROLES.index(role) if role in ROLES else -1


def _init_users_table():
    conn = session_store.get_connection()
    try:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS ti_users (
                   id       INTEGER PRIMARY KEY AUTOINCREMENT,
                   username TEXT UNIQUE NOT NULL,
                   password_hash TEXT NOT NULL,
                   role     TEXT NOT NULL DEFAULT 'standard',
                   created_at REAL DEFAULT (strftime('%s','now'))
               )"""
        )
        conn.commit()
        # Migration-safe: add the role column to pre-existing databases.
        try:
            conn.execute("ALTER TABLE ti_users ADD COLUMN role TEXT NOT NULL DEFAULT 'standard'")
            conn.commit()
        except sqlite3.OperationalError:
            pass  # column already exists
    finally:
        conn.close()


_init_users_table()


def current_username():
    return session.get("username")


def current_user():
    """Authenticated user info sourced from the signed-in session.

    The role is stored in the (server-signed) session at login/signup time,
    so it cannot be tampered with from the frontend. Role changes take effect
    on the next login.
    """
    username = current_username()
    if not username:
        return None
    role = session.get("role") or _DEFAULT_ROLE
    return {
        "username": username,
        "role": role,
        "role_label": ROLE_LABELS.get(role, role.upper()),
    }


def current_role():
    return session.get("role") or _DEFAULT_ROLE


def has_role(*roles):
    """True if the signed-in user is at least as privileged as one of the
    supplied roles. Rank decreases with privilege (admin=0 < manager=1 <
    advanced=2 < standard=3), so a user satisfies a requirement when their
    rank is <= the requirement's rank (the requirement allocates a minimum
    privilege, not an exact role)."""
    role = current_role()
    user_rank = _role_rank(role)
    return any(user_rank <= _role_rank(r) for r in roles)


def require_role(*roles):
    """Decorator: reject the request with 403 unless the user holds a role
    at least as privileged as one of the supplied roles."""
    def decorator(view):
        from functools import wraps

        @wraps(view)
        def wrapper(*args, **kwargs):
            if not has_role(*roles):
                return jsonify({"success": False,
                                "error": "You do not have permission to access this resource."}), 403
            return view(*args, **kwargs)
        return wrapper
    return decorator


def list_users():
    conn = session_store.get_connection()
    try:
        rows = conn.execute(
            "SELECT id, username, role, created_at FROM ti_users ORDER BY created_at ASC"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_user_role(username):
    conn = session_store.get_connection()
    try:
        row = conn.execute(
            "SELECT id, username, role FROM ti_users WHERE username = ?", (username,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def set_user_role(username, role):
    if role not in ROLES:
        return False
    conn = session_store.get_connection()
    try:
        cur = conn.execute("UPDATE ti_users SET role = ? WHERE username = ?", (role, username))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


@bp.route("/signup", methods=["POST"])
def signup():
    payload = request.get_json(silent=True) or {}
    username = str(payload.get("username") or "").strip()
    password = str(payload.get("password") or "")

    if len(username) < _USERNAME_MIN:
        return jsonify({"success": False,
                        "error": "Username must be at least 3 characters."}), 400
    if len(password) < _PASSWORD_MIN:
        return jsonify({"success": False,
                        "error": "Password must be at least 6 characters."}), 400

    conn = session_store.get_connection()
    try:
        exists = conn.execute(
            "SELECT 1 FROM ti_users WHERE username = ?", (username,)
        ).fetchone()
        if exists:
            return jsonify({"success": False,
                            "error": "That username is already taken."}), 409
        # The very first account in the system becomes an admin; every
        # subsequent signup is a standard user. Roles can never be chosen by
        # the signup caller - this prevents privilege escalation.
        count = conn.execute("SELECT COUNT(*) AS c FROM ti_users").fetchone()["c"]
        role = "admin" if count == 0 else _DEFAULT_ROLE
        conn.execute(
            "INSERT INTO ti_users (username, password_hash, role) VALUES (?, ?, ?)",
            (username, generate_password_hash(password), role),
        )
        conn.commit()
    except sqlite3.Error:
        logger.exception("Signup failed for %s", username)
        return jsonify({"success": False, "error": "Sign up failed."}), 500
    finally:
        conn.close()

    session.clear()
    session["username"] = username
    session["role"] = role
    user = current_user()
    return jsonify({"success": True, "data": user})


@bp.route("/login", methods=["POST"])
def login():
    payload = request.get_json(silent=True) or {}
    username = str(payload.get("username") or "").strip()
    password = str(payload.get("password") or "")

    conn = session_store.get_connection()
    try:
        row = conn.execute(
            "SELECT username, password_hash, role FROM ti_users WHERE username = ?",
            (username,),
        ).fetchone()
    finally:
        conn.close()

    if row is None or not check_password_hash(row["password_hash"], password):
        return jsonify({"success": False,
                        "error": "Incorrect username or password."}), 401

    session.clear()
    session["username"] = row["username"]
    session["role"] = row["role"] or _DEFAULT_ROLE
    user = current_user()
    return jsonify({"success": True, "data": user})


@bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify({"success": True})


@bp.route("/me")
def me():
    user = current_user()
    if not user:
        return jsonify({"success": False, "error": "Not signed in"}), 401
    return jsonify({"success": True, "data": user})