"""Taskboard: a small REST API for personal task lists, built with Flask and SQLite."""
import os
import secrets

from flask import Flask, jsonify

from . import auth, db, tasks
from .errors import register_error_handlers


def create_app(config: dict | None = None) -> Flask:
    app = Flask(__name__)
    app.config.update(
        DATABASE=os.environ.get("TASKBOARD_DB", os.path.join(os.getcwd(), "taskboard.sqlite")),
        # Set TASKBOARD_SECRET in real use. The random fallback is fine for a demo, but every
        # restart then signs tokens with a new key and everyone has to log in again.
        SECRET_KEY=os.environ.get("TASKBOARD_SECRET") or secrets.token_hex(32),
        TOKEN_MAX_AGE=60 * 60 * 24,  # tokens last 24 hours
        MAX_LOGIN_FAILURES=5,
        LOCKOUT_SECONDS=60,
        MAX_CONTENT_LENGTH=64 * 1024,  # reject request bodies over 64 KB
        JSON_SORT_KEYS=False,
    )
    if config:
        app.config.update(config)
    app.json.sort_keys = False

    app.teardown_appcontext(db.close_db)
    register_error_handlers(app)
    app.register_blueprint(auth.bp)
    app.register_blueprint(tasks.bp)

    @app.get("/api/health")
    def health():
        return jsonify({"status": "ok"})

    with app.app_context():
        db.init_db()
    return app
