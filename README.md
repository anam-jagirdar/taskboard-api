# taskboard-api

A REST API for personal task lists, built with Flask and SQLite. Users register, log in to get a token, and manage their own tasks with filtering, search, sorting and pagination.

The focus is on the parts that make an API trustworthy: authentication, input validation, per-user data isolation, and consistent error responses.

## Quick start

```bash
pip install -r requirements.txt
python run.py                      # http://127.0.0.1:5001
```

```bash
# create an account and log in
curl -X POST localhost:5001/api/register -H 'Content-Type: application/json' \
     -d '{"username": "anam", "password": "a-long-password"}'
curl -X POST localhost:5001/api/login -H 'Content-Type: application/json' \
     -d '{"username": "anam", "password": "a-long-password"}'
# -> {"token": "...", "token_type": "Bearer", "expires_in": 86400}

# use the token
TOKEN=...
curl -X POST localhost:5001/api/tasks -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
     -d '{"title": "Ship the portfolio", "priority": "high", "due_date": "2026-12-01"}'
curl "localhost:5001/api/tasks?priority=high&sort=due" -H "Authorization: Bearer $TOKEN"
```

Environment variables: `TASKBOARD_SECRET` (token signing key, set it in real use), `TASKBOARD_DB` (database file path).

## Endpoints

| Method | Path | Description |
|---|---|---|
| POST | `/api/register` | Create an account |
| POST | `/api/login` | Exchange username and password for a token |
| GET | `/api/me` | The logged-in user |
| GET | `/api/tasks` | List tasks (query options below) |
| POST | `/api/tasks` | Create a task. Returns `201` and a `Location` header |
| GET | `/api/tasks/<id>` | One task |
| PATCH | `/api/tasks/<id>` | Change any subset of fields |
| DELETE | `/api/tasks/<id>` | Delete (`204`) |
| GET | `/api/tasks/summary` | Counts by status, plus overdue count |
| GET | `/api/health` | Liveness check |

A task has `title` (required, up to 200 characters), `description`, `status` (`todo` / `doing` / `done`), `priority` (`low` / `medium` / `high`) and `due_date` (`YYYY-MM-DD` or `null`).

**List options:** `status`, `priority`, `q` (searches title and description), `sort` (`-created` default, `created`, `due`, `priority`, `title`), `page`, `per_page` (1 to 100, default 20). The response is `{"items": [...], "page", "per_page", "total", "pages"}`.

**Errors** always have the same shape, and validation errors list every bad field at once:

```json
{"error": {"code": "validation_failed", "message": "Some fields are invalid.",
           "fields": {"status": "must be one of: todo, doing, done", "due_date": "must be a date like 2026-12-31, or null"}}}
```

## Design decisions

- **Passwords** are stored only as salted hashes (Werkzeug). A login for a username that doesn't exist still does a hash check, so response time doesn't reveal which usernames are registered, and both failures return the identical message.
- **Tokens** are signed and expire after 24 hours (`itsdangerous`). They are verified on every request, and a deleted user's token stops working immediately.
- **Data isolation:** every task query includes the logged-in user's id. Asking for someone else's task returns `404`, the same as a task that doesn't exist, so IDs can't be probed.
- **No SQL injection:** all values are bound parameters. The few dynamic parts (sort order, columns to update) come from fixed allow-lists, never from request text. Search treats `%` and `_` literally. Tests cover these cases.
- **Brute-force protection:** 5 failed logins for a username locks it for 60 seconds (`429` with `Retry-After`).
- **Limits:** request bodies over 64 KB are rejected (`413`); strict date validation rejects dates like `2026-02-30`.
- **Database:** SQLite with `CHECK` constraints for status and priority, foreign keys with cascade delete, and an index on `(user_id, status)`.

## Tests

```bash
python -m unittest discover -s tests -v
```

27 tests cover authentication (including lockout, expiry and tokens signed with another key), validation, the PATCH semantics, user isolation, filtering/search/sorting, pagination, and injection attempts. They take about 6 seconds because password hashing is deliberately slow.

I also ran the real server and exercised register, login, create, list and an unauthenticated request with `curl`.

## Limitations

- The login lockout is kept in memory, so it is per process and resets on restart. With several server processes it would need a shared store such as Redis.
- Without `TASKBOARD_SECRET`, a random key is generated at startup, so tokens stop working when the server restarts.
- Flask's built-in server is for development. For deployment, run it behind a production server such as gunicorn, over HTTPS.
- Tokens can't be revoked individually (there is no logout endpoint); they simply expire.
- No password reset or email verification.
