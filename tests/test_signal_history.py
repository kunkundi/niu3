import json
import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.dashboard.api import create_app
from app.storage.db import get_state, set_state, settings
from tests.helpers import Fixture, at


class SignalHistoryTests(unittest.TestCase):
    def setUp(self):
        self.f = Fixture()
        with patch.dict(os.environ, {"NIUNO3_ADMIN_PASSWORD": "niuno3-test-history-password"}):
            self.client = TestClient(create_app(self.f.db, self.f.calendar, clock=at))

    def tearDown(self):
        self.client.close()
        self.f.close()

    def login(self):
        self.client.post(
            "/api/v1/auth/login",
            json={"password": "niuno3-test-history-password"},
            headers={"X-NiuNo3-Request": "1"},
        )

    def save_plan(self, day="2026-09-04"):
        payload = {
            "strategy": "price-action-v2",
            "targets": {"sh510300": "0.2"},
            "rows": [
                {
                    "symbol": "sh510300",
                    "name": "当时名称",
                    "representative": True,
                    "selected": True,
                    "target_weight": "0.2",
                    "reasons": ["当时筛选依据"],
                    "pa": {"entry": 1.31, "entry_stop": 1.24},
                }
            ],
            "input_sha256": "saved-evidence",
            "as_of": day,
        }
        with self.f.db.transaction() as conn:
            version, _ = settings(conn)
            return conn.execute(
                "INSERT INTO plans(as_of,execute_day,config_id,at,payload) VALUES(?,?,?,?,?)",
                (day, "2026-09-07", version, "2026-09-04T15:35:00+08:00", json.dumps(payload)),
            ).lastrowid

    def test_public_history_handles_empty_missing_and_invalid_requests(self):
        self.assertEqual(
            self.client.get("/api/v1/signals/history").json(), {"items": [], "next_before_id": None}
        )
        self.assertEqual(self.client.get("/api/v1/signals/history/999").status_code, 404)
        for query in ("limit=0", "limit=101", "before_id=0"):
            self.assertEqual(self.client.get("/api/v1/signals/history?" + query).status_code, 422)

    def test_cursor_pagination_includes_prior_configurations_without_duplicates(self):
        first = self.save_plan("2026-09-02")
        second = self.save_plan("2026-09-03")
        third = self.save_plan()
        self.f.db.change_config({"minimum_amount": "120000000"}, at())
        fourth = self.save_plan()
        result = self.client.get("/api/v1/signals/history?limit=2").json()
        self.assertEqual([row["id"] for row in result["items"]], [fourth, third])
        self.assertEqual([row["config_id"] for row in result["items"]], [2, 1])
        self.assertEqual(result["items"][0]["target_exposure"], "0.2")
        self.assertEqual(result["items"][0]["selected_count"], 1)
        self.assertNotIn("rows", result["items"][0])
        self.save_plan("2026-09-01")  # A newer insert cannot shift a previously issued cursor.
        result = self.client.get(
            f"/api/v1/signals/history?limit=2&before_id={result['next_before_id']}"
        ).json()
        self.assertEqual([row["id"] for row in result["items"]], [second, first])
        self.assertIsNone(result["next_before_id"])

    def test_detail_preserves_frozen_values_and_does_not_change_live_trading_state(self):
        plan_id = self.save_plan()
        self.f.db.change_config({"execution_mode": "intraday"}, at())
        self.f.quote(price="1.087")
        with self.f.db.transaction() as conn:
            set_state(conn, "live_targets", {"id": "live-current", "targets": {"sh510500": "0.3"}})
        self.login()
        original = {table: self.f.rows(table) for table in ("plans", "configs", "orders", "quotes")}
        result = self.client.get(f"/api/v1/signals/history/{plan_id}").json()
        self.assertEqual(result["mode"], "archive")
        self.assertEqual(result["config_id"], 1)
        self.assertEqual(result["targets"], {"sh510300": "0.2"})
        self.assertEqual(result["rows"][0]["name"], "当时名称")
        self.assertEqual(result["rows"][0]["pa"]["entry"], 1.31)
        self.assertNotIn("quote", result["rows"][0])
        self.assertEqual(result["input_sha256"], "saved-evidence")
        self.assertEqual({table: self.f.rows(table) for table in original}, original)
        with self.f.db.connect() as conn:
            self.assertEqual(get_state(conn, "live_targets")["id"], "live-current")
            self.assertEqual(settings(conn)[1].execution_mode, "intraday")
