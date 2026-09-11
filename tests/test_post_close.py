import json
import os
import unittest
from dataclasses import replace
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from app.automation.service import Worker
from app.automation.targets import calculate_targets
from app.core.types import iso
from app.dashboard.api import create_app
from app.storage.db import get_state, set_state, settings
from app.strategies.buy_review import calculate_review, review_request
from app.trading.intraday import live_problem
from tests.helpers import Fixture, at
from tests.test_price_action import candles


class PostCloseTests(unittest.TestCase):
    def setUp(self):
        self.f = Fixture()
        self.now = at("2026-09-04T16:00:00")
        self.f.db.change_config(
            {"execution_mode": "intraday", "strategy_model": "price_action", "pa_rr_enabled": False}, self.now
        )
        self.worker = Worker(self.f.db, self.f.calendar, Mock())
        self.history = candles()
        self.ingest_history()
        with self.f.db.transaction() as conn:
            set_state(conn, "profile:sh510300", {"at": iso(self.now)})
        self.worker.tick(self.now, network=False)
        self.assertEqual(len(self.f.rows("plans")), 1)
        with patch.dict(os.environ, {"NIUNO3_ADMIN_PASSWORD": "post-close-test-password"}):
            self.client = TestClient(create_app(self.f.db, self.f.calendar, clock=lambda: self.now))

    def tearDown(self):
        self.client.close()
        self.worker.close()
        self.f.close()

    def ingest_history(self):
        self.worker.ingest("history:sh510300", {"qfq": self.history, "raw": self.history}, self.now)

    def signal(self):
        response = self.client.get("/api/v1/signals")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_completed_plan_is_automatically_visible_with_latest_chart_and_review_without_writes(self):
        with self.f.db.connect() as conn:
            request = review_request(conn, self.f.instrument, "2026-09-04", self.f.calendar)
        self.worker.ingest("buy_review:sh510300", calculate_review(request, self.now), self.now)
        before = {
            t: self.f.rows(t)
            for t in ("plans", "plan_inputs", "orders", "fills", "state", "bars", "configs", "buy_reviews")
        }
        body = self.signal()
        self.assertEqual(body["mode"], "post_close")
        self.assertEqual(body["as_of"], "2026-09-04")
        self.assertEqual(body["execute_day"], "2026-09-07")
        self.assertEqual(body["candidate_count"], 1)
        self.assertTrue(body["rows"][0]["post_close_candidate"])
        self.assertTrue(body["preview_only"])
        self.assertFalse(body["stale"])
        self.assertFalse(body["rows"][0]["eligible"])
        self.assertFalse(body["rows"][0]["selected"])
        self.assertEqual(body["targets"], {})
        chart = self.client.get(
            "/api/v1/signals/sh510300/chart", params={"mode": "post_close", "signal_id": body["id"]}
        )
        self.assertEqual(chart.status_code, 200, chart.text)
        data = chart.json()
        self.assertTrue(data["matched"])
        self.assertEqual(data["bars"][-1]["day"], body["as_of"])
        self.assertEqual(data["buy_review"]["as_of"], body["as_of"])
        self.assertEqual(data["intraday_reference"]["levels"], {})
        self.assertEqual(data["position_reference"], {})
        candidate = data["candidate_setup"]
        self.assertEqual(candidate["as_of"], body["as_of"])
        self.assertEqual(candidate["execute_day"], body["execute_day"])
        self.assertEqual(candidate["signal_day"], body["rows"][0]["pa"]["signal_day"])
        self.assertEqual(candidate["entry"], body["rows"][0]["pa"]["entry"])
        self.assertFalse(candidate["rr_enabled"])
        self.assertTrue(candidate["reasons"])
        self.assertEqual({t: self.f.rows(t) for t in before}, before)
        with self.f.db.connect() as conn:
            version, config = settings(conn)
            self.assertIsNone(get_state(conn, "live_targets"))
            self.assertIn("盘后参考", live_problem(body, version, config, self.f.calendar, self.now))

    def test_weekend_preopen_open_and_lunch_switch_use_the_correct_basis(self):
        preview = self.signal()
        for stamp in ("2026-09-05T12:00:00", "2026-09-07T09:29:59"):
            self.now = at(stamp)
            self.assertEqual(self.signal()["id"], preview["id"])
        self.now = at("2026-09-07T09:30:00")
        opening = self.signal()
        self.assertEqual(opening["mode"], "live")
        self.assertIsNone(opening["id"])
        self.assertEqual(
            self.client.get(
                "/api/v1/signals/sh510300/chart", params={"mode": "post_close", "signal_id": preview["id"]}
            ).status_code,
            409,
        )
        self.f.quote(self.now, price="1.007")
        live = calculate_targets(self.f.db, "2026-09-04", self.now)
        self.worker.ingest("live_targets", live, self.now)
        self.assertEqual(self.signal()["id"], live["id"])
        self.now = at("2026-09-07T12:00:00")
        lunch = self.signal()
        self.assertEqual(lunch["mode"], "live")
        self.assertEqual(lunch["id"], live["id"])
        self.assertTrue(lunch["session_snapshot"])

    def test_before_completed_bar_cutoff_and_pending_sync_preserve_the_last_live_snapshot(self):
        self.now = at("2026-09-04T14:59:00")
        self.f.quote(self.now, price="1.007")
        live = calculate_targets(self.f.db, "2026-09-03", self.now)
        self.worker.ingest("live_targets", live, self.now)
        for stamp in ("2026-09-04T15:00:00", "2026-09-04T15:29:59", "2026-09-04T15:30:00"):
            self.now = at(stamp)
            body = self.signal()
            self.assertEqual(body["id"], live["id"])
            self.assertTrue(body["session_snapshot"])
        self.assertTrue(body["post_close_pending"])
        self.now = at("2026-09-04T16:00:00")
        self.assertEqual(self.signal()["mode"], "post_close")
        self.assertEqual(self.client.get("/api/v1/signals?mode=live").json()["id"], live["id"])

    def test_rr_settings_invalidate_old_preview_and_control_next_session_candidates(self):
        previous = self.signal()
        self.f.db.change_config({"pa_rr_enabled": True, "pa_min_rr": 5}, self.now)
        waiting = self.signal()
        self.assertIsNone(waiting["id"])
        self.assertTrue(waiting["post_close_pending"])
        self.worker.tick(self.now, network=False)
        filtered = self.signal()
        self.assertEqual(filtered["mode"], "post_close")
        self.assertEqual(filtered["candidate_count"], 0)
        self.assertIn("潜在盈亏比不足", "；".join(filtered["rows"][0]["reasons"]))
        self.assertNotEqual(previous["id"], filtered["id"])
        self.f.db.change_config({"pa_rr_enabled": False}, self.now)
        self.worker.tick(self.now, network=False)
        self.assertEqual(self.signal()["candidate_count"], 1)

    def test_revised_bars_refresh_preview_without_rewriting_frozen_plan_and_future_bars_are_ignored(self):
        first = self.signal()
        frozen = self.f.rows("plans")
        self.history[-1] = replace(self.history[-1], high="1.33", close="1.325")
        self.ingest_history()
        updated = self.signal()
        self.assertNotEqual(first["id"], updated["id"])
        self.assertNotEqual(first["input_sha256"], updated["input_sha256"])
        self.assertNotEqual(first["rows"][0]["pa"]["input_sha256"], updated["rows"][0]["pa"]["input_sha256"])
        self.assertEqual(self.f.rows("plans"), frozen)
        chart = self.client.get("/api/v1/signals/sh510300/chart", params={"signal_id": updated["id"]}).json()
        self.assertTrue(chart["matched"])
        future = replace(self.history[-1], day="2026-09-07", high="99")
        with self.f.db.transaction() as conn:
            conn.execute(
                "INSERT INTO bars VALUES(?,?,?,?,?,?)",
                ("sh510300", "qfq", future.day, json.dumps(future.to_dict()), "test", iso(self.now)),
            )
        self.assertEqual(self.signal()["id"], updated["id"])
        self.assertEqual(self.f.rows("orders"), [])

    def test_missing_or_invalid_completed_history_is_pending_instead_of_a_current_preview(self):
        with self.f.db.transaction() as conn:
            conn.execute("DELETE FROM bars WHERE day='2026-09-04'")
        self.assertIsNone(self.signal()["id"])
        self.history[-1] = replace(self.history[-1], high="NaN")
        self.ingest_history()
        self.assertIsNone(self.signal()["id"])

    def test_explicit_daily_mode_and_daily_execution_keep_their_frozen_plan(self):
        daily = self.client.get("/api/v1/signals?mode=daily").json()
        self.assertEqual(daily["mode"], "daily")
        self.assertEqual(daily["id"], self.f.rows("plans")[0]["id"])
        self.f.db.change_config({"strategy_model": "momentum", "execution_mode": "daily"}, self.now)
        self.worker.tick(self.now, network=False)
        self.assertEqual(self.signal()["mode"], "daily")
