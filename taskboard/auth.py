"""Registration, login, and the `login_required` decorator.

Passwords are stored as salted hashes (werkzeug's scrypt/pbkdf2), never in plain text.
Login returns a signed, expiring token; clients send it as `Authorization: Bearer <token>`.
"""
import sqlite3
import time
from datetime import datetime, timezone
from functools import wraps

from flask import Blueprint, current_app, g, jsonify, request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from werkzeug.security import check_password_hash, generate_password_hash

from .db import get_db
from .errors import ApiError
from .validation import clean_credentials

bp = Blueprint("auth", __name__, url_prefix="/api")

# A throwaway hash, checked when the username doesn't exist, so a login attempt takes about the
# same time whether or not the account is real (otherwise response time reveals valid usernames).
_DUMMY_HASH = generate_password_hash("not-a-real-password")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt="taskboard-auth")


def make_token(user_id: int) -> str:
    return _serializer().dumps({"uid": user_id})


def read_token(token: str):
    try:
        return _serializer().loads(token, max_age=current_app.config["TOKEN_MAX_AGE"])["uid"]
    except (BadSignature, SignatureExpired, KeyError, TypeError):
        return None


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        header = request.headers.get("Authorization", "")
        scheme, _, token = header.partition(" ")
        if scheme.lower() != "bearer" or not token:
            raise ApiError(401, "unauthorized", "Send an 'Authorization: Bearer <token>' header.",
                           headers={"WWW-Authenticate": "Bearer"})
        user_id = read_token(token.strip())
        user = None if user_id is None else get_db().execute(
            "SELECT id, username, created_at FROM users WHERE id = ?", (user_id,)).fetchone()
        if user is None:
            raise ApiError(401, "invalid_token", "Token is invalid or expired. Log in again.",
                           headers={"WWW-Authenticate": "Bearer"})
        g.user = user
        return view(*args, **kwargs)

    return wrapped


# --- brute-force protection: too many failed logins lock that username out for a while ---------
# Kept in memory, so it is per server process (documented in the README).
def _failures() -> dict:
    return current_app.extensions.setdefault("login_failures", {})


def _check_not_locked(username: str) -> None:
    config = current_app.config
    count, first = _failures().get(username.lower(), (0, 0.0))
    if count >= config["MAX_LOGIN_FAILURES"]:
        wait = config["LOCKOUT_SECONDS"] - (time.monotonic() - first)
        if wait > 0:
            raise ApiError(429, "too_many_attempts", "Too many failed logins. Try again later.",
                           headers={"Retry-After": str(int(wait) + 1)})
        _failures().pop(username.lower(), None)


def _record_failure(username: str) -> None:
    count, first = _failures().get(username.lower(), (0, time.monotonic()))
    _failures()[username.lower()] = (count + 1, first if count else time.monotonic())


@bp.post("/register")
def register():
    username, password = clean_credentials(request.get_json(silent=True))
    db = get_db()
    try:
        cursor = db.execute("INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)",
                            (username, generate_password_hash(password), now_iso()))
        db.commit()
    except sqlite3.IntegrityError:
        raise ApiError(409, "username_taken", "That username is already registered.")
    return jsonify({"id": cursor.lastrowid, "username": username}), 201


@bp.post("/login")
def login():
    username, password = clean_credentials(request.get_json(silent=True))
    _check_not_locked(username)
    user = get_db().execute("SELECT id, password_hash FROM users WHERE username = ?", (username,)).fetchone()
    valid = check_password_hash(user["password_hash"] if user else _DUMMY_HASH, password)
    if not user or not valid:
        _record_failure(username)
        raise ApiError(401, "invalid_credentials", "Wrong username or password.")
    _failures().pop(username.lower(), None)
    return jsonify({"token": make_token(user["id"]), "token_type": "Bearer",
                    "expires_in": current_app.config["TOKEN_MAX_AGE"]})


@bp.get("/me")
@login_required
def me():
    return jsonify({"id": g.user["id"], "username": g.user["username"], "created_at": g.user["created_at"]})
