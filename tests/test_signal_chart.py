import json
import os
import unittest
from dataclasses import replace
from datetime import date, timedelta
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from app.automation.service import Worker
from app.automation.targets import calculate_targets
from app.core.types import iso
from app.dashboard.api import create_app
from app.dashboard.signal_chart import candidate_setup
from app.core.config import Settings
from app.storage.db import set_state
from tests.helpers import Fixture, at
from tests.test_price_action import candles


class SignalChartTests(unittest.TestCase):
    def setUp(self):
        self.f = Fixture()
        self.now = at("2026-09-07T10:30:00")
        self.f.db.change_config({"strategy_model": "price_action", "execution_mode": "intraday"}, self.now)
        self.worker = Worker(self.f.db, self.f.calendar, Mock())
        recent = candles()
        first = date.fromisoformat(recent[0].day)
        older = [replace(recent[0], day=(first - timedelta(days=i)).isoformat()) for i in range(160, 0, -1)]
        self.history = older + recent
        self.worker.ingest("history:sh510300", {"raw": self.history, "qfq": self.history}, self.now)
        self.f.quote(self.now, price="1.007")
        self.plan = calculate_targets(self.f.db, "2026-09-04", self.now)
        self.worker.ingest("live_targets", self.plan, self.now)
        with self.f.db.transaction() as conn:
            set_state(conn, "worker_heartbeat", {"at": iso(self.now)})
        with patch.dict(os.environ, {"NIUNO3_ADMIN_PASSWORD": "niuno3-test-chart-password"}):
            self.client = TestClient(create_app(self.f.db, self.f.calendar, clock=lambda: self.now))
        self.url = "/api/v1/signals/sh510300/chart"

    def tearDown(self):
        self.client.close()
        self.worker.close()
        self.f.close()

    def test_public_chart_matches_strategy_window_excludes_future_and_does_not_write_trades(self):
        future = replace(self.history[-1], day="2026-09-07", close="1.31")
        with self.f.db.transaction() as conn:
            conn.execute(
                "INSERT INTO bars VALUES(?,?,?,?,?,?)",
                ("sh510300", "qfq", future.day, json.dumps(future.to_dict()), "test", iso(self.now)),
            )
            set_state(conn, "pa_position:sh510300", {"stop": 940000})
        original_bars = self.f.rows("bars")
        result = self.client.get(self.url, params={"mode": "live", "signal_id": self.plan["id"]})
        self.assertEqual(result.status_code, 200)
        body = result.json()
        self.assertEqual((body["as_of"], len(body["bars"])), ("2026-09-04", 250))
        self.assertEqual(body["bars"][0]["day"], self.history[-250].day)
        self.assertEqual(body["history_count"], 250)
        self.assertEqual(body["row"]["pa"]["structure_bars"], 60)
        self.assertEqual(body["row"]["pa"]["structure_start"], self.history[-60].day)
        self.assertEqual(body["bars"][-1]["day"], "2026-09-04")
        self.assertTrue(body["matched"])
        self.assertIsNone(body["candidate_setup"])
        self.assertEqual(body["row"]["pa"]["entry"], 1.311)
        self.assertEqual(body["position_reference"]["stop"], 0.94)
        reference = body["intraday_reference"]
        self.assertEqual(reference["session_day"], "2026-09-07")
        self.assertEqual(reference["levels"]["entry"], 1.005)
        self.assertEqual(reference["levels"], {key: value / 1_000_000 for key, value in self.plan["rows"][0]["pa"]["raw"].items() if value > 0})
        self.assertEqual(self.f.rows("orders"), [])
        self.assertEqual(self.f.rows("bars"), original_bars)

    def test_revised_history_reports_mismatch_instead_of_silently_overlaying_old_levels(self):
        revised = replace(self.history[-1], low="1.23")
        with self.f.db.transaction() as conn:
            conn.execute(
                "UPDATE bars SET payload=? WHERE symbol='sh510300' AND adjustment='qfq' AND day=?",
                (json.dumps(revised.to_dict()), revised.day),
            )
        body = self.client.get(self.url).json()
        self.assertFalse(body["matched"])
        self.assertIsNone(body["candidate_setup"])
        self.assertIn("暂不叠加", body["warning"])
        self.assertEqual(body["intraday_reference"]["levels"], {})

    def test_shorter_background_reports_actual_coverage_and_respects_eligibility_minimum(self):
        with self.f.db.transaction() as conn:
            conn.execute("DELETE FROM bars WHERE day<?", (self.history[-140].day,))
        self.plan = calculate_targets(self.f.db, "2026-09-04", self.now)
        self.worker.ingest("live_targets", self.plan, self.now)
        body = self.client.get(self.url).json()
        self.assertEqual(len(body["bars"]), 140)
        self.assertEqual(body["history_count"], 250)
        self.assertEqual(body["row"]["pa"]["history_bars"], 140)
        self.assertTrue(body["matched"])
        self.assertTrue(body["row"]["pa"]["ready"])
        self.f.db.change_config({"minimum_bars": 250}, self.now)
        plan = calculate_targets(self.f.db, "2026-09-04", self.now)
        self.assertFalse(plan["rows"][0]["pa"]["ready"])
        self.assertEqual(plan["targets"], {})

    def test_old_signal_removed_selection_and_new_configuration_are_not_returned_as_current(self):
        self.assertEqual(self.client.get(self.url, params={"signal_id": "old-id"}).status_code, 409)
        self.assertEqual(self.client.get("/api/v1/signals/sh510500/chart").status_code, 404)
        self.f.db.change_config({"pa_min_rr": "1.8"}, self.now)
        self.assertEqual(self.client.get(self.url).status_code, 409)

    def test_daily_reference_uses_its_basis_and_does_not_show_live_account_protection(self):
        with self.f.db.transaction() as conn:
            conn.execute(
                "INSERT INTO plans(as_of,execute_day,config_id,at,payload) VALUES(?,?,?,?,?)",
                ("2026-09-04", "2026-09-07", self.plan["config_id"], iso(self.now), json.dumps(self.plan)),
            )
            set_state(conn, "pa_position:sh510300", {"stop": 940000})
        body = self.client.get(self.url, params={"mode": "daily"}).json()
        self.assertTrue(body["matched"])
        self.assertEqual(body["position_reference"], {})
        self.assertEqual(body["bars"][-1]["day"], "2026-09-04")

    def test_candidate_chart_is_read_only_and_remains_aligned_with_its_signal(self):
        self.plan["targets"] = {}
        self.plan["rows"][0]["selected"] = False
        self.plan["rows"][0]["target_weight"] = "0"
        self.worker.ingest("live_targets", self.plan, self.now)
        original = {table: self.f.rows(table) for table in ("orders", "bars", "configs")}
        body = self.client.get(self.url).json()
        self.assertFalse(body["row"]["selected"])
        self.assertEqual(len(body["bars"]), 250)
        self.assertTrue(body["matched"])
        self.assertEqual({table: self.f.rows(table) for table in original}, original)
        self.plan["rows"][0].pop("pa")
        self.worker.ingest("live_targets", self.plan, self.now)
        body = self.client.get(self.url).json()
        self.assertFalse(body["matched"])
        self.assertIn("暂无有效策略结构", body["warning"])
        self.assertEqual(body["intraday_reference"]["levels"], {})


class CandidateSetupTests(unittest.TestCase):
    def setUp(self):
        self.plan = {"mode": "post_close", "as_of": "2026-09-10", "execute_day": "2026-09-11"}
        self.row = {
            "post_close_candidate": True,
            "quote": {"stale": True},
            "pa": {
                "ready": True, "as_of": "2026-09-10", "signal_day": "2026-09-10",
                "setup": "看涨 Pin Bar", "trend": "震荡结构", "entry": 1.116,
                "entry_stop": 1.1, "target": 1.136, "entry_ceiling": 1.124,
                "evidence": {"bull": {"high": 1.115, "low": 1.101, "context": "关键位附近"},
                             "target": {"price": 1.136}},
            },
        }

    def test_new_game_setup_can_be_explained_without_a_past_trigger_or_fresh_quote(self):
        result = candidate_setup(self.plan, self.row, Settings(pa_rr_enabled=False), 0.001)
        self.assertEqual(result["signal_day"], "2026-09-10")
        self.assertEqual(result["execute_day"], "2026-09-11")
        self.assertAlmostEqual(result["reward_risk"], 1.25)
        self.assertAlmostEqual(result["entry_ceiling"], 1.124)
        self.assertFalse(result["rr_enabled"])
        self.assertIn("关键位附近", result["reasons"][0])
        self.assertIn("过滤已关闭", result["reasons"][3])

    def test_enabled_filter_caps_displayed_entry_price_and_rejects_failed_candidates(self):
        self.assertIsNone(candidate_setup(self.plan, self.row, Settings(), 0.001))
        result = candidate_setup(self.plan, self.row, Settings(pa_min_rr="1"), 0.001)
        self.assertTrue(result["rr_enabled"])
        self.assertAlmostEqual(result["entry_ceiling"], 1.118)
        self.assertIn("达到最低 1", result["reasons"][3])

    def test_observation_live_and_unready_rows_do_not_gain_candidate_annotations(self):
        config = Settings(pa_rr_enabled=False)
        self.assertIsNone(candidate_setup({**self.plan, "mode": "live"}, self.row, config, 0.001))
        self.assertIsNone(candidate_setup(self.plan, {**self.row, "post_close_candidate": False}, config, 0.001))
        self.row["pa"]["ready"] = False
        self.assertIsNone(candidate_setup(self.plan, self.row, config, 0.001))
