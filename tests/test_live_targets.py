import os
import unittest
from datetime import timedelta
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from app.automation.service import Worker
from app.automation.targets import calculate_targets
from app.core.types import iso
from app.dashboard.api import create_app
from app.storage.db import get_state, set_state
from tests.helpers import Fixture, at, bars


class LiveTargetsTests(unittest.TestCase):
    def setUp(self):
        self.f = Fixture()
        self.worker = Worker(self.f.db, self.f.calendar, Mock())
        self.now = at()
        history = bars()
        self.worker.ingest("history:sh510300", {"raw": history, "qfq": history}, self.now)
        self.f.quote(self.now, price="1.020")
        with self.f.db.transaction() as conn:
            set_state(conn, "profile:sh510300", {"at": iso(self.now)})
            set_state(conn, "catalog", {"at": iso(self.now), "total": 1})
        self.worker.tick(self.now, network=False)

    def tearDown(self):
        self.worker.close()
        self.f.close()

    def test_new_quote_changes_targets_without_writing_bars_or_execution_plans(self):
        plans = self.f.rows("plans")
        stored_bars = self.f.rows("bars")
        orders = self.f.rows("orders")
        first = calculate_targets(self.f.db, "2026-09-04", self.now)
        self.assertIn("sh510300", first["targets"])
        later = self.now + timedelta(seconds=60)
        self.f.quote(later, price="0.940")
        second = calculate_targets(self.f.db, "2026-09-04", later)
        self.assertEqual(second["targets"], {})
        self.assertIn("趋势门槛未通过", second["rows"][0]["reasons"])
        self.assertNotEqual(first["rows"][0]["score"], second["rows"][0]["score"])
        self.assertEqual(first["rows"][0]["amount20"], second["rows"][0]["amount20"])
        self.assertEqual(self.f.rows("plans"), plans)
        self.assertEqual(self.f.rows("bars"), stored_bars)
        self.assertEqual(self.f.rows("orders"), orders)

    def test_stale_future_unknown_or_missing_baseline_cannot_be_selected(self):
        for kwargs in [
            {"when": self.now - timedelta(minutes=2)},
            {"when": self.now + timedelta(seconds=1)},
            {"status": "unknown"},
            {"previous_close": 0},
        ]:
            with self.subTest(kwargs=kwargs):
                with self.f.db.transaction() as conn:
                    conn.execute("DELETE FROM quotes")
                self.f.quote(**kwargs)
                result = calculate_targets(self.f.db, "2026-09-04", self.now)
                self.assertEqual(result["targets"], {})
                self.assertEqual(result["missing_quotes"], 1)

    def test_worker_automatically_executes_current_live_targets_in_intraday_mode(self):
        self.f.db.change_config({"execution_mode": "intraday"}, self.now)
        for seconds in (60, 120):
            now = self.now + timedelta(seconds=seconds)
            self.f.quote(now, price="1.020")
            self.worker.ingest("live_targets", calculate_targets(self.f.db, "2026-09-04", now), now)
            self.worker.tick(now, network=False)
        orders = self.f.rows("orders")
        self.assertEqual([o["kind"] for o in orders], ["rebalance", "intraday"])
        self.assertEqual(orders[0]["status"], "cancelled")
        self.assertEqual(orders[1]["status"], "pending")
        self.f.quote(self.now + timedelta(seconds=150), price="1.020", volume=5_000_000)
        self.worker.tick(self.now + timedelta(seconds=150), network=False)
        self.assertTrue(self.f.rows("fills"))

    def test_background_schedule_persists_and_config_change_invalidates_inflight_result(self):
        self.worker.schedule_targets(self.now)
        result = self.worker.futures["live_targets"].result(timeout=5)
        self.worker.collect(self.now)
        with self.f.db.connect() as conn:
            self.assertEqual(get_state(conn, "live_targets")["targets"], result["targets"])
        self.worker.schedule_targets(self.now + timedelta(seconds=5))
        self.assertNotIn("live_targets", self.worker.futures)
        self.f.db.change_config({"max_weight": "0.10"}, self.now)
        self.worker.ingest("live_targets", result, self.now)
        self.worker.schedule_targets(self.now)
        updated = self.worker.futures["live_targets"].result(timeout=5)
        self.assertEqual(updated["config_id"], 2)
        self.assertEqual(updated["targets"], {self.f.instrument.symbol: "0.10"})
        self.worker.collect(self.now)
        self.worker.schedule_targets(at("2026-09-07T12:00:00"))
        self.assertNotIn("live_targets", self.worker.futures)

    def test_api_labels_freshness_and_never_returns_previous_config_or_day_as_current(self):
        result = calculate_targets(self.f.db, "2026-09-04", self.now)
        self.worker.ingest("live_targets", result, self.now)
        with patch.dict(os.environ, {"NIUNO3_ADMIN_PASSWORD": "niuno3-test-password-2026"}):
            client = TestClient(create_app(self.f.db, self.f.calendar, clock=lambda: self.now))
        with client:
            path = "/api/v1/signals?mode=live"
            self.assertEqual(client.get(path).status_code, 200)
            current = client.get(path).json()
            self.assertEqual(current["update_state"], "live")
            self.assertFalse(current["stale"])
            self.assertEqual(client.get("/api/v1/signals").json()["mode"], "daily")
            self.now += timedelta(seconds=100)
            self.assertEqual(client.get(path).json()["update_state"], "offline")
            self.worker.lease(self.now)
            self.assertTrue(client.get(path).json()["stale"])
            self.assertEqual(client.get(path).json()["update_state"], "stale")
            self.f.db.change_config({"max_weight": "0.10"}, self.now)
            self.assertIsNone(client.get(path).json()["id"])
            refreshed = calculate_targets(self.f.db, "2026-09-04", self.now)
            self.worker.ingest("live_targets", refreshed, self.now)
            self.now = at("2026-09-08T09:35:00")
            client.post(
                "/api/v1/auth/login",
                json={"password": "niuno3-test-password-2026"},
                headers={"X-NiuNo3-Request": "1"},
            )
            self.assertEqual(client.get(path).json()["rows"], [])

    def test_after_close_and_preopen_keep_dated_session_snapshot_without_changing_execution_state(self):
        self.f.db.change_config({"execution_mode": "intraday"}, self.now)
        self.now = at("2026-09-07T14:59:00")
        self.f.quote(self.now, price="1.020")
        plan = calculate_targets(self.f.db, "2026-09-04", self.now)
        self.worker.ingest("live_targets", plan, self.now)
        with patch.dict(os.environ, {"NIUNO3_ADMIN_PASSWORD": "niuno3-test-password-2026"}):
            client = TestClient(create_app(self.f.db, self.f.calendar, clock=lambda: self.now))
        with client:
            for stamp in (
                "2026-09-07T15:29:59",
                "2026-09-07T15:30:00",
                "2026-09-07T20:00:00",
                "2026-09-08T09:29:59",
            ):
                self.now = at(stamp)
                self.worker.lease(self.now)
                client.post(
                    "/api/v1/auth/login",
                    json={"password": "niuno3-test-password-2026"},
                    headers={"X-NiuNo3-Request": "1"},
                )
                before = {
                    table: self.f.rows(table) for table in ("state", "orders", "fills", "plans", "configs")
                }
                body = client.get("/api/v1/signals").json()
                self.assertEqual(body["id"], plan["id"], stamp)
                self.assertEqual(body["targets"], plan["targets"])
                self.assertEqual(body["as_of"], "2026-09-04")
                self.assertEqual(body["focus"]["as_of"], body["as_of"])
                self.assertTrue(body["session_snapshot"])
                self.assertTrue(body["stale"])
                self.assertEqual(body["update_state"], "waiting_session")
                self.assertIn("09-07 14:59:00", body["message"])
                chart = client.get("/api/v1/signals/sh510300/chart", params={"signal_id": plan["id"]})
                self.assertEqual(chart.status_code, 200)
                self.assertEqual(chart.json()["as_of"], plan["as_of"])
                self.assertEqual(before, {table: self.f.rows(table) for table in before})
            # At the next opening the previous session must never masquerade as a new signal.
            self.now = at("2026-09-08T09:30:00")
            self.worker.lease(self.now)
            body = client.get("/api/v1/signals").json()
            self.assertIsNone(body["id"])
            self.assertFalse(body["session_snapshot"])
            self.assertEqual(body["rows"], [])
            from app.trading.intraday import live_problem
            from app.storage.db import settings

            with self.f.db.connect() as conn:
                config_id, config = settings(conn)
                self.assertTrue(live_problem(plan, config_id, config, self.f.calendar, self.now))

    def test_weekend_snapshot_retains_last_session_but_rejects_changed_config_and_future_time(self):
        self.now = at("2026-09-11T14:59:00")
        history = bars(end="2026-09-10")
        self.worker.ingest("history:sh510300", {"raw": history, "qfq": history}, self.now)
        self.f.quote(self.now, price="1.020")
        plan = calculate_targets(self.f.db, "2026-09-10", self.now)
        self.worker.ingest("live_targets", plan, self.now)
        self.now = at("2026-09-12T10:00:00")
        with patch.dict(os.environ, {"NIUNO3_ADMIN_PASSWORD": "niuno3-test-password-2026"}):
            client = TestClient(create_app(self.f.db, self.f.calendar, clock=lambda: self.now))
        with client:
            client.post(
                "/api/v1/auth/login",
                json={"password": "niuno3-test-password-2026"},
                headers={"X-NiuNo3-Request": "1"},
            )
            body = client.get("/api/v1/signals?mode=live").json()
            self.assertEqual(body["id"], plan["id"])
            self.assertTrue(body["session_snapshot"])
            self.f.db.change_config({"max_weight": "0.10"}, self.now)
            self.assertIsNone(client.get("/api/v1/signals?mode=live").json()["id"])
            plan["config_id"] += 1
            plan["created_at"] = "2026-09-14T14:59:00+08:00"
            with self.f.db.transaction() as conn:
                set_state(conn, "live_targets", plan)
            self.assertIsNone(client.get("/api/v1/signals?mode=live").json()["id"])


if __name__ == "__main__":
    unittest.main()
