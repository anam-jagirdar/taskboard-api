"""Task endpoints. Every query is scoped to the logged-in user, so one user can never see,
change, or even confirm the existence of another user's tasks (they get a plain 404)."""
from flask import Blueprint, g, jsonify, request

from .auth import login_required, now_iso
from .db import get_db
from .errors import ApiError
from .validation import PRIORITIES, STATUSES, clean_task

bp = Blueprint("tasks", __name__, url_prefix="/api/tasks")

# Sort choices map to fixed SQL fragments. User input never gets pasted into the query.
SORTS = {
    "created": "created_at ASC, id ASC",
    "-created": "created_at DESC, id DESC",
    "due": "due_date IS NULL, due_date ASC, id ASC",  # tasks without a due date go last
    "priority": "CASE priority WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END, id ASC",
    "title": "title COLLATE NOCASE ASC, id ASC",
}
COLUMNS = "id, title, description, status, priority, due_date, created_at, updated_at"


def _int_arg(name: str, default: int, low: int, high: int) -> int:
    raw = request.args.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ApiError(422, "validation_failed", "Invalid query parameter.", {name: "must be an integer"})
    if not low <= value <= high:
        raise ApiError(422, "validation_failed", "Invalid query parameter.", {name: f"must be between {low} and {high}"})
    return value


def _get_owned(task_id: int):
    row = get_db().execute(f"SELECT {COLUMNS} FROM tasks WHERE id = ? AND user_id = ?", (task_id, g.user["id"])).fetchone()
    if row is None:
        raise ApiError(404, "not_found", "Task not found.")
    return row


@bp.get("")
@login_required
def list_tasks():
    where, params = ["user_id = ?"], [g.user["id"]]
    problems = {}
    for name, choices in (("status", STATUSES), ("priority", PRIORITIES)):
        value = request.args.get(name)
        if value is not None:
            if value not in choices:
                problems[name] = f"must be one of: {', '.join(choices)}"
            else:
                where.append(f"{name} = ?")
                params.append(value)
    sort = request.args.get("sort", "-created")
    if sort not in SORTS:
        problems["sort"] = f"must be one of: {', '.join(SORTS)}"
    if problems:
        raise ApiError(422, "validation_failed", "Invalid query parameter.", problems)
    search = request.args.get("q", "").strip()
    if search:
        escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        where.append("(title LIKE ? ESCAPE '\\' OR description LIKE ? ESCAPE '\\')")
        params += [f"%{escaped}%"] * 2

    page = _int_arg("page", 1, 1, 1_000_000)
    per_page = _int_arg("per_page", 20, 1, 100)
    clause = " AND ".join(where)
    db = get_db()
    total = db.execute(f"SELECT COUNT(*) FROM tasks WHERE {clause}", params).fetchone()[0]
    rows = db.execute(f"SELECT {COLUMNS} FROM tasks WHERE {clause} ORDER BY {SORTS[sort]} LIMIT ? OFFSET ?",
                      params + [per_page, (page - 1) * per_page]).fetchall()
    return jsonify({
        "items": [dict(row) for row in rows],
        "page": page,
        "per_page": per_page,
        "total": total,
        "pages": -(-total // per_page),
    })


@bp.post("")
@login_required
def create_task():
    data = clean_task(request.get_json(silent=True), partial=False)
    now = now_iso()
    db = get_db()
    cursor = db.execute(
        "INSERT INTO tasks (user_id, title, description, status, priority, due_date, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (g.user["id"], data["title"], data.get("description", ""), data.get("status", "todo"),
         data.get("priority", "medium"), data.get("due_date"), now, now))
    db.commit()
    response = jsonify(dict(_get_owned(cursor.lastrowid)))
    response.status_code = 201
    response.headers["Location"] = f"/api/tasks/{cursor.lastrowid}"
    return response


@bp.get("/<int:task_id>")
@login_required
def get_task(task_id):
    return jsonify(dict(_get_owned(task_id)))


@bp.patch("/<int:task_id>")
@login_required
def update_task(task_id):
    _get_owned(task_id)
    data = clean_task(request.get_json(silent=True), partial=True)
    names = list(data)  # validated against a fixed allow-list, so safe to use as column names
    db = get_db()
    db.execute(f"UPDATE tasks SET {', '.join(f'{n} = ?' for n in names)}, updated_at = ? WHERE id = ? AND user_id = ?",
               [data[n] for n in names] + [now_iso(), task_id, g.user["id"]])
    db.commit()
    return jsonify(dict(_get_owned(task_id)))


@bp.delete("/<int:task_id>")
@login_required
def delete_task(task_id):
    _get_owned(task_id)
    db = get_db()
    db.execute("DELETE FROM tasks WHERE id = ? AND user_id = ?", (task_id, g.user["id"]))
    db.commit()
    return "", 204


@bp.get("/summary")
@login_required
def summary():
    rows = get_db().execute("SELECT status, COUNT(*) AS n FROM tasks WHERE user_id = ? GROUP BY status", (g.user["id"],)).fetchall()
    counts = {status: 0 for status in STATUSES}
    counts.update({row["status"]: row["n"] for row in rows})
    overdue = get_db().execute(
        "SELECT COUNT(*) FROM tasks WHERE user_id = ? AND status != 'done' AND due_date IS NOT NULL AND due_date < date('now')",
        (g.user["id"],)).fetchone()[0]
    return jsonify({"by_status": counts, "total": sum(counts.values()), "overdue": overdue})
