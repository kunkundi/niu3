import json
import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from threading import Barrier
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.dashboard.api import create_app
from app.dashboard.security import authenticate, authorized, change_password, initialize_auth, password_hash
from app.storage.db import get_state
from tests.helpers import Fixture, at

OLD = "fixture-original-key-2026"
NEW = "牛牛三号的新管理密钥-2026"


class AdminPasswordTests(unittest.TestCase):
    def setUp(self):
        self.f = Fixture()
        with patch.dict(os.environ, {"NIUNO3_ADMIN_PASSWORD": OLD}):
            self.app = create_app(self.f.db, self.f.calendar, clock=at)
        self.client = TestClient(self.app)
        self.headers = {"X-NiuNo3-Request": "1"}
        self.bootstrap = self.f.db.path.parent / "admin-token.txt"
        self.bootstrap.write_text(OLD + "\n", encoding="utf-8")

    def tearDown(self):
        self.client.close()
        self.f.close()

    def login(self, password=OLD):
        return self.client.post("/api/v1/auth/login", json={"password": password}, headers=self.headers)

    def rotate(self, **overrides):
        body = {"current_password": OLD, "new_password": NEW, "confirm_password": NEW} | overrides
        return self.client.post("/api/v1/auth/password", json=body, headers=self.headers)

    def credential(self):
        with self.f.db.connect() as conn:
            return get_state(conn, "admin_credential")

    def test_rotation_revokes_sessions_preserves_ledger_and_survives_restart(self):
        self.f.buy()
        tables = ("cash_ledger", "lots", "fills", "orders", "configs", "actions", "notification_deliveries")
        before = {table: self.f.rows(table) for table in tables}
        self.login()
        first = self.client.cookies.get("niuno3_session")
        self.login()
        second = self.client.cookies.get("niuno3_session")
        original = self.credential()
        response = self.rotate()
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["authenticated"])
        self.assertIn("Max-Age=0", response.headers["set-cookie"])
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertFalse(self.bootstrap.exists())
        self.assertNotEqual(self.credential()["salt"], original["salt"])
        self.assertEqual(self.f.rows("sessions"), [])
        with self.f.db.connect() as conn:
            self.assertFalse(authorized(conn, first, at()))
            self.assertFalse(authorized(conn, second, at()))
        self.assertFalse(self.client.get("/api/v1/auth/session").json()["authenticated"])
        self.assertEqual(self.client.get("/api/v1/config").status_code, 401)
        self.assertEqual(self.client.get("/api/v1/account").status_code, 200)
        self.assertEqual(self.login(OLD).status_code, 401)
        self.assertEqual(self.login(NEW).status_code, 200)
        self.assertEqual(self.client.get("/api/v1/account").status_code, 200)
        credential = self.credential()
        with patch.dict(os.environ, {"NIUNO3_ADMIN_PASSWORD": OLD}):
            initialize_auth(self.f.db)
        self.assertEqual(self.credential(), credential)
        self.assertEqual(self.login(OLD).status_code, 401)
        self.assertEqual(self.login(NEW).status_code, 200)
        self.assertEqual({table: self.f.rows(table) for table in tables}, before)
        audit = self.f.rows("runs")[-1]
        self.assertEqual(audit["task"], "auth:password")
        for value in (response.text, json.dumps(self.credential()), json.dumps(audit)):
            self.assertNotIn(OLD, value)
            self.assertNotIn(NEW, value)

    def test_invalid_new_keys_and_extra_fields_never_echo_credentials(self):
        self.login()
        original = self.credential()
        for overrides in [
            {"new_password": "", "confirm_password": ""},
            {"new_password": None, "confirm_password": None},
            {"new_password": OLD, "confirm_password": OLD},
            {"confirm_password": "fixture-not-matching-key"},
            {"current_password": ""},
            {"undeclared_secret": "fixture-hidden-input"},
        ]:
            with self.subTest(fields=list(overrides)):
                response = self.rotate(**overrides)
                self.assertEqual(response.status_code, 422)
                for secret in (OLD, NEW, "fixture-hidden-input", "X" * 257):
                    self.assertNotIn(secret, response.text)
                self.assertEqual(self.credential(), original)
        response = self.client.post("/api/v1/auth/login", json={"password": "X" * 257}, headers=self.headers)
        self.assertNotIn("X" * 257, response.text)
        self.assertTrue(self.bootstrap.exists())

    def test_passwords_have_no_length_or_character_combination_requirements(self):
        current = OLD
        for key in ("1", "123456", "a", "牛", " ", " padded ", "X" * 1024):
            with self.subTest(length=len(key)):
                self.assertEqual(self.login(current).status_code, 200)
                response = self.rotate(current_password=current, new_password=key, confirm_password=key)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(self.login(key).status_code, 200)
                self.assertEqual(self.client.get("/api/v1/config").status_code, 200)
                current = key
        # Long passwords must also be accepted as the current password when rotating again.
        self.assertEqual(self.rotate(current_password=current).status_code, 200)
        self.assertEqual(self.login(NEW).status_code, 200)

    def test_initial_password_accepts_short_long_and_whitespace_values(self):
        for source in ("environment", "file"):
            for key in ("1", "牛", " ", " padded ", "X" * 1024):
                with self.subTest(source=source, length=len(key)):
                    fixture = Fixture()
                    try:
                        environment = {"NIUNO3_ADMIN_PASSWORD": key if source == "environment" else ""}
                        if source == "file":
                            (fixture.db.path.parent / "admin-token.txt").write_text(key + "\n", encoding="utf-8")
                        with patch.dict(os.environ, environment):
                            initialize_auth(fixture.db)
                        token = authenticate(fixture.db, key, "fixture", at())
                        self.assertIsNotNone(token)
                    finally:
                        fixture.close()


    def test_wrong_current_key_preserves_credentials_and_shares_login_rate_limit(self):
        self.login()
        original = self.credential()
        for attempt in range(1, 11):
            response = self.rotate(current_password="wrong-current-key")
            self.assertEqual(response.status_code, 400)
            self.assertIn("当前管理密钥不正确", response.text)
            self.assertEqual(len(self.f.rows("login_attempts")), attempt)
        self.assertEqual(self.credential(), original)
        self.assertTrue(self.bootstrap.exists())
        self.assertTrue(self.client.get("/api/v1/auth/session").json()["authenticated"])
        self.assertEqual(self.rotate().status_code, 429)
        self.assertEqual(self.login().status_code, 429)
        self.assertIsNotNone(authenticate(self.f.db, OLD, "testclient", at() + timedelta(minutes=11)))

    def test_credential_sessions_and_audit_roll_back_together(self):
        self.login()
        original = self.credential()
        before_sessions = self.f.rows("sessions")
        with self.f.db.transaction() as conn:
            conn.execute(
                "CREATE TRIGGER fail_password_audit BEFORE INSERT ON runs WHEN NEW.task='auth:password' BEGIN SELECT RAISE(ABORT,'fixture failure'); END"
            )
        response = self.rotate()
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('fixture failure', response.text)
        self.assertEqual(self.credential(), original)
        self.assertEqual(self.f.rows("sessions"), before_sessions)
        self.assertTrue(self.bootstrap.exists())
        self.assertEqual(self.f.rows("runs"), [])

    def test_bootstrap_cleanup_failure_does_not_undo_committed_change(self):
        self.login()
        with patch.object(Path, "unlink", side_effect=PermissionError("fixture-private-path")):
            response = self.rotate()
        self.assertEqual(response.status_code, 200)
        self.assertIn("未能清理", response.json()["warning"])
        self.assertNotIn("fixture-private-path", response.text)
        self.assertTrue(self.bootstrap.exists())
        self.assertEqual(self.login(OLD).status_code, 401)
        self.assertEqual(self.login(NEW).status_code, 200)

    def test_expired_or_revoked_session_cannot_change_credentials(self):
        self.login()
        token = self.client.cookies.get("niuno3_session")
        with self.assertRaises(PermissionError):
            change_password(self.f.db, OLD, NEW, token, "testclient", at() + timedelta(hours=13))
        self.rotate()
        with self.assertRaises(PermissionError):
            change_password(self.f.db, NEW, OLD, token, "testclient", at())

    def test_concurrent_rotations_can_commit_only_once(self):
        self.login()
        token = self.client.cookies.get("niuno3_session")
        barrier = Barrier(2)

        def rotate(key):
            barrier.wait(timeout=3)
            try:
                change_password(self.f.db, OLD, key, token, "testclient", at())
                return key
            except PermissionError:
                return None

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(rotate, [NEW, NEW + "-second"]))
        successful = [key for key in results if key]
        self.assertEqual(len(successful), 1)
        self.assertEqual(len(self.f.rows("runs")), 1)
        credential = self.credential()
        self.assertEqual(credential["hash"], password_hash(successful[0], credential["salt"]))

if __name__ == "__main__":
    unittest.main()
