"""Validate and clean JSON input. Every problem is collected, so the client sees them all at once."""
from datetime import date

from .errors import ApiError

STATUSES = ("todo", "doing", "done")
PRIORITIES = ("low", "medium", "high")
TASK_FIELDS = {"title", "description", "status", "priority", "due_date"}


def _check_title(value, problems):
    if not isinstance(value, str) or not value.strip():
        problems["title"] = "must be a non-empty string"
        return None
    value = value.strip()
    if len(value) > 200:
        problems["title"] = "must be 200 characters or fewer"
        return None
    return value


def _check_description(value, problems):
    if not isinstance(value, str):
        problems["description"] = "must be a string"
        return None
    if len(value) > 5000:
        problems["description"] = "must be 5000 characters or fewer"
        return None
    return value


def _check_choice(name, choices):
    def check(value, problems):
        if value not in choices:
            problems[name] = f"must be one of: {', '.join(choices)}"
            return None
        return value

    return check


def _check_due_date(value, problems):
    if value is None:
        return None
    try:
        if not isinstance(value, str):
            raise ValueError
        return date.fromisoformat(value).isoformat()  # strict YYYY-MM-DD
    except ValueError:
        problems["due_date"] = "must be a date like 2026-12-31, or null"
        return None


_CHECKS = {
    "title": _check_title,
    "description": _check_description,
    "status": _check_choice("status", STATUSES),
    "priority": _check_choice("priority", PRIORITIES),
    "due_date": _check_due_date,
}


def clean_task(data, partial: bool) -> dict:
    """Return only the valid task fields. `partial=True` is for PATCH, where every field is optional."""
    if not isinstance(data, dict):
        raise ApiError(400, "invalid_json", "Request body must be a JSON object.")
    problems = {name: "unknown field" for name in data if name not in TASK_FIELDS}
    if not partial and "title" not in data:
        problems["title"] = "is required"
    cleaned = {}
    for name, value in data.items():
        if name in _CHECKS:
            result = _CHECKS[name](value, problems)
            if name not in problems:
                cleaned[name] = result
    if partial and not data:
        problems["body"] = "provide at least one field to change"
    if problems:
        raise ApiError(422, "validation_failed", "Some fields are invalid.", problems)
    return cleaned


def clean_credentials(data) -> tuple[str, str]:
    if not isinstance(data, dict):
        raise ApiError(400, "invalid_json", "Request body must be a JSON object.")
    problems = {}
    username, password = data.get("username"), data.get("password")
    if not isinstance(username, str) or not (3 <= len(username.strip()) <= 32) or not username.strip().replace("_", "").replace("-", "").isalnum():
        problems["username"] = "must be 3-32 characters: letters, numbers, - and _"
    if not isinstance(password, str) or not (8 <= len(password) <= 128):
        problems["password"] = "must be 8-128 characters"
    if problems:
        raise ApiError(422, "validation_failed", "Some fields are invalid.", problems)
    return username.strip(), password
