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


def _init_users_table():
    conn = session_store.get_connection()
    try:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS ti_users (
                   id       INTEGER PRIMARY KEY AUTOINCREMENT,
                   username TEXT UNIQUE NOT NULL,
                   password_hash TEXT NOT NULL,
                   created_at REAL DEFAULT (strftime('%s','now'))
               )"""
        )
        conn.commit()
    finally:
        conn.close()


_init_users_table()


def current_username():
    return session.get("username")


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
        conn.execute(
            "INSERT INTO ti_users (username, password_hash) VALUES (?, ?)",
            (username, generate_password_hash(password)),
        )
        conn.commit()
    except sqlite3.Error:
        logger.exception("Signup failed for %s", username)
        return jsonify({"success": False, "error": "Sign up failed."}), 500
    finally:
        conn.close()

    session.clear()
    session["username"] = username
    return jsonify({"success": True, "data": {"username": username}})


@bp.route("/login", methods=["POST"])
def login():
    payload = request.get_json(silent=True) or {}
    username = str(payload.get("username") or "").strip()
    password = str(payload.get("password") or "")

    conn = session_store.get_connection()
    try:
        row = conn.execute(
            "SELECT username, password_hash FROM ti_users WHERE username = ?",
            (username,),
        ).fetchone()
    finally:
        conn.close()

    if row is None or not check_password_hash(row["password_hash"], password):
        return jsonify({"success": False,
                        "error": "Incorrect username or password."}), 401

    session.clear()
    session["username"] = row["username"]
    return jsonify({"success": True, "data": {"username": row["username"]}})


@bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify({"success": True})


@bp.route("/me")
def me():
    username = current_username()
    if not username:
        return jsonify({"success": False, "error": "Not signed in"}), 401
    return jsonify({"success": True, "data": {"username": username}})