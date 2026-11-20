import os
import tempfile
import time
import unittest

from taskboard import create_app


class ApiTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = create_app({"DATABASE": os.path.join(self.tmp.name, "t.sqlite"), "SECRET_KEY": "test", "TESTING": True})
        self.client = self.app.test_client()

    def tearDown(self):
        self.tmp.cleanup()

    def register(self, name="alice", password="correct-horse"):
        return self.client.post("/api/register", json={"username": name, "password": password})

    def login(self, name="alice", password="correct-horse"):
        return self.client.post("/api/login", json={"username": name, "password": password})

    def auth(self, name="alice"):
        self.register(name)
        token = self.login(name).get_json()["token"]
        return {"Authorization": f"Bearer {token}"}

    def make_task(self, headers, **fields):
        fields.setdefault("title", "Write tests")
        return self.client.post("/api/tasks", json=fields, headers=headers)


class AuthTests(ApiTestCase):
    def test_register_login_me(self):
        self.assertEqual(self.register().status_code, 201)
        token = self.login().get_json()["token"]
        me = self.client.get("/api/me", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(me.get_json()["username"], "alice")
        self.assertNotIn("password", str(me.get_json()))

    def test_password_is_hashed_in_database(self):
        self.register()
        with self.app.app_context():
            from taskboard.db import get_db
            stored = get_db().execute("SELECT password_hash FROM users").fetchone()[0]
        self.assertNotIn("correct-horse", stored)

    def test_duplicate_username_is_case_insensitive(self):
        self.register("alice")
        self.assertEqual(self.register("ALICE").status_code, 409)

    def test_validation_reports_all_fields(self):
        r = self.client.post("/api/register", json={"username": "a!", "password": "short"})
        self.assertEqual(r.status_code, 422)
        self.assertEqual(set(r.get_json()["error"]["fields"]), {"username", "password"})

    def test_wrong_password_and_unknown_user_look_identical(self):
        self.register()
        a = self.login(password="wrong-password")
        b = self.login(name="nobody")
        self.assertEqual((a.status_code, a.get_json()), (b.status_code, b.get_json()))
        self.assertEqual(a.status_code, 401)

    def test_lockout_after_repeated_failures(self):
        self.register()
        for _ in range(5):
            self.assertEqual(self.login(password="wrong-password").status_code, 401)
        locked = self.login()  # even the right password is refused while locked
        self.assertEqual(locked.status_code, 429)
        self.assertIn("Retry-After", locked.headers)

    def test_lockout_expires(self):
        self.app.config["LOCKOUT_SECONDS"] = 0.2
        self.register()
        for _ in range(5):
            self.login(password="wrong-password")
        time.sleep(0.3)
        self.assertEqual(self.login().status_code, 200)

    def test_protected_routes_need_a_valid_token(self):
        self.assertEqual(self.client.get("/api/tasks").status_code, 401)
        bad = self.client.get("/api/tasks", headers={"Authorization": "Bearer nonsense"})
        self.assertEqual(bad.get_json()["error"]["code"], "invalid_token")
        self.assertEqual(self.client.get("/api/tasks", headers={"Authorization": "Basic abc"}).status_code, 401)

    def test_expired_token_rejected(self):
        self.app.config["TOKEN_MAX_AGE"] = 0
        headers = self.auth()
        time.sleep(1.1)
        self.assertEqual(self.client.get("/api/me", headers=headers).status_code, 401)

    def test_token_signed_with_other_key_rejected(self):
        headers = self.auth()
        other = create_app({"DATABASE": os.path.join(self.tmp.name, "t.sqlite"), "SECRET_KEY": "different"}).test_client()
        self.assertEqual(other.get("/api/me", headers=headers).status_code, 401)


class TaskTests(ApiTestCase):
    def test_create_defaults_and_fetch(self):
        h = self.auth()
        r = self.make_task(h)
        self.assertEqual(r.status_code, 201)
        task = r.get_json()
        self.assertEqual((task["status"], task["priority"], task["due_date"], task["description"]), ("todo", "medium", None, ""))
        self.assertEqual(r.headers["Location"], f"/api/tasks/{task['id']}")
        self.assertEqual(self.client.get(f"/api/tasks/{task['id']}", headers=h).get_json(), task)

    def test_title_is_trimmed(self):
        h = self.auth()
        self.assertEqual(self.make_task(h, title="  padded  ").get_json()["title"], "padded")

    def test_validation_errors(self):
        h = self.auth()
        r = self.make_task(h, title="", status="finished", priority="urgent", due_date="31/12/2026", extra=1)
        fields = r.get_json()["error"]["fields"]
        self.assertEqual(r.status_code, 422)
        self.assertEqual(set(fields), {"title", "status", "priority", "due_date", "extra"})
        self.assertEqual(self.make_task(h, title="x" * 201).status_code, 422)
        self.assertEqual(self.make_task(h, due_date="2026-02-30").status_code, 422)  # not a real date
        self.assertEqual(self.client.post("/api/tasks", json={}, headers=h).status_code, 422)

    def test_non_json_and_wrong_shape_bodies(self):
        h = self.auth()
        self.assertEqual(self.client.post("/api/tasks", data="not json", headers=h).status_code, 400)
        self.assertEqual(self.client.post("/api/tasks", json=[1, 2], headers=h).status_code, 400)

    def test_oversized_body_rejected(self):
        h = self.auth()
        r = self.client.post("/api/tasks", json={"title": "a", "description": "x" * 70000}, headers=h)
        self.assertEqual(r.status_code, 413)

    def test_patch_changes_only_given_fields_and_bumps_updated_at(self):
        h = self.auth()
        task = self.make_task(h, priority="high", due_date="2026-12-31").get_json()
        time.sleep(1.1)
        r = self.client.patch(f"/api/tasks/{task['id']}", json={"status": "done"}, headers=h).get_json()
        self.assertEqual((r["status"], r["priority"], r["due_date"]), ("done", "high", "2026-12-31"))
        self.assertGreater(r["updated_at"], task["updated_at"])
        self.assertEqual(r["created_at"], task["created_at"])
        cleared = self.client.patch(f"/api/tasks/{task['id']}", json={"due_date": None}, headers=h).get_json()
        self.assertIsNone(cleared["due_date"])

    def test_patch_validation(self):
        h = self.auth()
        tid = self.make_task(h).get_json()["id"]
        self.assertEqual(self.client.patch(f"/api/tasks/{tid}", json={}, headers=h).status_code, 422)
        self.assertEqual(self.client.patch(f"/api/tasks/{tid}", json={"status": "x"}, headers=h).status_code, 422)
        self.assertEqual(self.client.patch(f"/api/tasks/{tid}", json={"id": 99}, headers=h).status_code, 422)

    def test_delete(self):
        h = self.auth()
        tid = self.make_task(h).get_json()["id"]
        self.assertEqual(self.client.delete(f"/api/tasks/{tid}", headers=h).status_code, 204)
        self.assertEqual(self.client.get(f"/api/tasks/{tid}", headers=h).status_code, 404)
        self.assertEqual(self.client.delete(f"/api/tasks/{tid}", headers=h).status_code, 404)

    def test_users_cannot_touch_each_others_tasks(self):
        alice, bob = self.auth("alice"), self.auth("bobby")
        tid = self.make_task(alice).get_json()["id"]
        url = f"/api/tasks/{tid}"
        self.assertEqual(self.client.get(url, headers=bob).status_code, 404)
        self.assertEqual(self.client.patch(url, json={"title": "hacked"}, headers=bob).status_code, 404)
        self.assertEqual(self.client.delete(url, headers=bob).status_code, 404)
        self.assertEqual(self.client.get("/api/tasks", headers=bob).get_json()["total"], 0)
        self.assertEqual(self.client.get(url, headers=alice).get_json()["title"], "Write tests")

    def test_filter_search_and_sort(self):
        h = self.auth()
        self.make_task(h, title="Buy milk", priority="low", status="done", due_date="2026-03-01")
        self.make_task(h, title="Ship release", priority="high", due_date="2026-01-01")
        self.make_task(h, title="Plan trip", description="book 100% refundable", priority="medium")
        get = lambda q: self.client.get(f"/api/tasks?{q}", headers=h).get_json()
        self.assertEqual([t["title"] for t in get("status=done")["items"]], ["Buy milk"])
        self.assertEqual([t["title"] for t in get("priority=high")["items"]], ["Ship release"])
        self.assertEqual([t["title"] for t in get("q=TRIP")["items"]], ["Plan trip"])
        self.assertEqual(get("q=100%25")["total"], 1)  # % is searched literally
        self.assertEqual(get("q=%25")["total"], 1)
        self.assertEqual([t["title"] for t in get("sort=priority")["items"]], ["Ship release", "Plan trip", "Buy milk"])
        self.assertEqual([t["title"] for t in get("sort=due")["items"]], ["Ship release", "Buy milk", "Plan trip"])
        self.assertEqual([t["title"] for t in get("sort=title")["items"]], ["Buy milk", "Plan trip", "Ship release"])

    def test_bad_query_params(self):
        h = self.auth()
        for q in ("status=nope", "sort=title;DROP TABLE tasks", "page=0", "page=abc", "per_page=1000"):
            self.assertEqual(self.client.get(f"/api/tasks?{q}", headers=h).status_code, 422, q)

    def test_sql_injection_attempts_are_inert(self):
        h = self.auth()
        self.make_task(h, title="'; DROP TABLE tasks; --")
        r = self.client.get("/api/tasks?q=' OR '1'='1", headers=h).get_json()
        self.assertEqual(r["total"], 0)
        self.assertEqual(self.client.get("/api/tasks", headers=h).get_json()["total"], 1)

    def test_pagination(self):
        h = self.auth()
        for i in range(25):
            self.make_task(h, title=f"task {i:02d}")
        page = lambda n: self.client.get(f"/api/tasks?sort=created&per_page=10&page={n}", headers=h).get_json()
        p1, p3, p4 = page(1), page(3), page(4)
        self.assertEqual((p1["total"], p1["pages"], len(p1["items"])), (25, 3, 10))
        self.assertEqual(p1["items"][0]["title"], "task 00")
        self.assertEqual([t["title"] for t in p3["items"]], [f"task {i}" for i in range(20, 25)])
        self.assertEqual(p4["items"], [])

    def test_default_order_is_newest_first(self):
        h = self.auth()
        for title in ("first", "second", "third"):
            self.make_task(h, title=title)
        titles = [t["title"] for t in self.client.get("/api/tasks", headers=h).get_json()["items"]]
        self.assertEqual(titles, ["third", "second", "first"])

    def test_summary(self):
        h = self.auth()
        self.make_task(h, status="todo", due_date="2000-01-01")
        self.make_task(h, status="done", due_date="2000-01-01")
        self.make_task(h, status="doing")
        s = self.client.get("/api/tasks/summary", headers=h).get_json()
        self.assertEqual(s, {"by_status": {"todo": 1, "doing": 1, "done": 1}, "total": 3, "overdue": 1})


class MiscTests(ApiTestCase):
    def test_health_and_json_errors(self):
        self.assertEqual(self.client.get("/api/health").get_json(), {"status": "ok"})
        r = self.client.get("/api/nope")
        self.assertEqual((r.status_code, r.get_json()["error"]["code"]), (404, "not_found"))
        self.assertEqual(self.client.put("/api/health").status_code, 405)

    def test_deleting_user_cascades_to_tasks(self):
        h = self.auth()
        self.make_task(h)
        with self.app.app_context():
            from taskboard.db import get_db
            db = get_db()
            db.execute("DELETE FROM users")
            db.commit()
            self.assertEqual(db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
