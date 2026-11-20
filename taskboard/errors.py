"""One error shape for the whole API:  {"error": {"code": "...", "message": "...", "fields": {...}}}"""
from flask import jsonify
from werkzeug.exceptions import HTTPException


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, fields: dict | None = None, headers: dict | None = None):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message
        self.fields, self.headers = fields, headers or {}


def error_response(status: int, code: str, message: str, fields=None, headers=None):
    body = {"error": {"code": code, "message": message}}
    if fields:
        body["error"]["fields"] = fields
    response = jsonify(body)
    response.status_code = status
    for name, value in (headers or {}).items():
        response.headers[name] = value
    return response


def register_error_handlers(app) -> None:
    @app.errorhandler(ApiError)
    def api_error(exc):
        return error_response(exc.status, exc.code, exc.message, exc.fields, exc.headers)

    @app.errorhandler(HTTPException)
    def http_error(exc):  # 404 for unknown URLs, 405 for wrong method, and so on
        code = exc.name.lower().replace(" ", "_")
        return error_response(exc.code, code, exc.description)

    @app.errorhandler(Exception)
    def unexpected(exc):
        app.logger.exception("unhandled error")
        return error_response(500, "internal_error", "Something went wrong on our side.")
