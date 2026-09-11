import json
import os
import unittest
from dataclasses import replace
from datetime import date
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from app.automation.service import Worker
from app.dashboard.api import create_app
from app.storage.db import put_instrument
from app.strategies.buy_review import (
    calculate_review,
    fingerprint,
    review_payload,
    review_request,
    save_review,
)
from tests.helpers import Fixture, at
from tests.test_price_action import candles


class BuyReviewTests(unittest.TestCase):
    def setUp(self):
        self.f = Fixture()
        self.history = candles()
        for i in range(9):
            day = self.f.calendar.next(date.fromisoformat(self.history[-1].day))
            delta = i * 0.003
            self.history.append(
                replace(
                    self.history[-1],
                    day=day,
                    open=str(1.312 + delta),
                    low=str(1.306 + delta),
                    close=str(1.317 + delta),
                    high=str(1.32 + delta),
                )
            )
        self.as_of = self.history[-1].day
        self.now = at(self.as_of + "T16:00:00")
        self.worker = Worker(self.f.db, self.f.calendar, Mock())
        self.worker.ingest("history:sh510300", {"qfq": self.history, "raw": self.history}, self.now)

    def tearDown(self):
        self.worker.close()
        self.f.close()

    def request(self):
        with self.f.db.connect() as conn:
            return review_request(conn, self.f.instrument, self.as_of, self.f.calendar)

    def calculate(self):
        result = calculate_review(self.request(), self.now)
        self.worker.ingest("buy_review:sh510300", result, self.now)
        return result

    def test_real_engine_persists_ten_sessions_without_touching_trading(self):
        before = {t: self.f.rows(t) for t in ("orders", "fills", "cash_ledger", "lots", "configs", "plans")}
        result = self.calculate()
        self.assertTrue(result["ready"])
        self.assertEqual(result["evaluated_days"], 10)
        self.assertEqual(len(result["checks"]), 10)
        self.assertTrue(any(m["setup"] == "看涨 Pin Bar" for m in result["markers"]))
        self.assertEqual(result["mode"], "causal")
        self.assertNotIn("causal", result)
        self.assertTrue(all(m["known_through"] < m["day"] for m in result["markers"]))
        self.assertEqual(result["input_sha256"], fingerprint(result["input"]))
        self.assertEqual({t: self.f.rows(t) for t in before}, before)
        with self.f.db.connect() as conn:
            payload = review_payload(conn, "sh510300", self.as_of, self.f.calendar)
        self.assertEqual(payload["markers"], result["markers"])
        self.assertEqual(payload["checks"], result["checks"])
        self.assertNotIn("input", payload)
        with patch.dict(os.environ, {"NIUNO3_ADMIN_PASSWORD": "buy-review-test-password"}):
            app = create_app(self.f.db, self.f.calendar, clock=lambda: self.now)
        tables = ("buy_reviews", "orders", "configs", "bars")
        before = {t: self.f.rows(t) for t in tables}
        with TestClient(app) as client:
            response = client.get("/api/v1/etfs/sh510300")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["buy_review"], payload)
            self.assertEqual(response.json()["bars"][-1]["day"], self.as_of)
        self.assertEqual({t: self.f.rows(t) for t in tables}, before)

    def test_scheduler_runs_after_sync_and_skips_unchanged_results_after_restart(self):
        self.worker.schedule_reviews(at(self.as_of + "T15:29:59"))
        self.assertEqual(self.worker.futures, {})
        self.worker.schedule_reviews(self.now)
        self.assertEqual(list(self.worker.futures), ["buy_review:sh510300"])
        self.worker.futures["buy_review:sh510300"].result(timeout=15)
        self.worker.collect(self.now)
        self.assertEqual(len(self.f.rows("buy_reviews")), 1)
        self.worker.close()
        self.worker = Worker(self.f.db, self.f.calendar, Mock())
        self.worker.schedule_reviews(self.now)
        self.assertEqual(self.worker.futures, {})

    def test_changed_prices_invalidate_old_overlay_and_inflight_result(self):
        result = self.calculate()
        revised = replace(self.history[-1], high="1.8")
        with self.f.db.transaction() as conn:
            conn.execute(
                "UPDATE bars SET payload=? WHERE symbol=? AND adjustment=? AND day=?",
                (json.dumps(revised.to_dict()), "sh510300", "qfq", self.as_of),
            )
            self.assertFalse(save_review(conn, result, self.f.calendar))
            payload = review_payload(conn, "sh510300", self.as_of, self.f.calendar)
        self.assertFalse(payload["ready"])
        self.assertEqual(payload["markers"], [])
        self.worker.schedule_reviews(self.now)
        self.assertIn("buy_review:sh510300", self.worker.futures)


    def test_review_cache_depends_on_prices_membership_and_relevant_settings(self):
        result = self.calculate()
        self.f.db.change_config({"max_positions": 4}, self.now)
        self.assertEqual(fingerprint(self.request()), result["input_sha256"])
        self.f.db.change_config({"pa_rr_enabled": False}, self.now)
        self.assertFalse(self.request()["rr_enabled"])
        with self.f.db.transaction() as conn:
            self.assertFalse(save_review(conn, result, self.f.calendar))
            self.assertFalse(review_payload(conn, "sh510300", self.as_of, self.f.calendar)["ready"])
        disabled = self.calculate()
        self.assertFalse(disabled["rr_enabled"])
        self.f.db.change_config({"pa_rr_enabled": True}, self.now)
        self.assertEqual(fingerprint(self.request()), result["input_sha256"])
        with self.f.db.transaction() as conn:
            self.assertFalse(save_review(conn, disabled, self.f.calendar))
        self.f.db.change_config({"pa_min_rr": 3}, self.now)
        self.assertNotEqual(fingerprint(self.request()), result["input_sha256"])
        with self.f.db.transaction() as conn:
            put_instrument(conn, replace(self.f.instrument, watched=False))
            self.assertFalse(save_review(conn, result, self.f.calendar))
            self.assertIsNone(review_payload(conn, "sh510300", self.as_of, self.f.calendar))

    def test_missing_session_is_not_reported_as_no_buys_and_future_bars_are_ignored(self):
        request = self.request()
        future = replace(self.history[-1], day="2026-12-01", high="100")
        with self.f.db.transaction() as conn:
            conn.execute(
                "INSERT INTO bars VALUES(?,?,?,?,?,?)",
                ("sh510300", "qfq", future.day, json.dumps(future.to_dict()), "test", self.now.isoformat()),
            )
        self.assertEqual(self.request(), request)
        with self.f.db.transaction() as conn:
            conn.execute("DELETE FROM bars WHERE symbol='sh510300' AND day=?", (self.history[-4].day,))
        result = self.calculate()
        self.assertFalse(result["ready"])
        self.assertIn("未齐全", result["reason"])
        self.assertEqual(result["markers"], [])

    def test_invalid_numeric_history_is_unavailable_without_crashing_scheduler(self):
        malformed = replace(self.history[-1], high="not-a-price", low="NaN")
        with self.f.db.transaction() as conn:
            conn.execute(
                "UPDATE bars SET payload=? WHERE symbol=? AND adjustment=? AND day=?",
                (json.dumps(malformed.to_dict()), "sh510300", "qfq", self.as_of),
            )
        self.worker.schedule_reviews(self.now)
        self.worker.futures["buy_review:sh510300"].result(timeout=15)
        self.worker.collect(self.now)
        payload = json.loads(self.f.rows("buy_reviews")[0]["payload"])
        self.assertFalse(payload["ready"])
        self.assertEqual(payload["markers"], [])

    def test_market_trigger_filter_combines_conditions_before_pagination_and_is_read_only(self):
        self.calculate()
        for symbol, quiet in (("sh510050", True), ("sh510500", False)):
            instrument = replace(
                self.f.instrument,
                symbol=symbol,
                name="未触发 ETF" if quiet else "债券触发 ETF",
                category="equity" if quiet else "bond",
                verified=quiet,
            )
            with self.f.db.transaction() as conn:
                put_instrument(conn, instrument)
            history = (
                [replace(b, open="1", close="1", high="1.01", low="0.99") for b in self.history]
                if quiet else self.history
            )
            self.worker.ingest(f"history:{symbol}", {"qfq": history, "raw": history}, self.now)
            with self.f.db.connect() as conn:
                request = review_request(conn, instrument, self.as_of, self.f.calendar)
            result = calculate_review(request, self.now)
            self.assertTrue(result["ready"])
            self.assertEqual(bool(result["markers"]), not quiet)
            self.worker.ingest(f"buy_review:{symbol}", result, self.now)
        with self.f.db.transaction() as conn:
            put_instrument(conn, replace(self.f.instrument, symbol="sz159985", name="待复盘 ETF"))
        with patch.dict(os.environ, {"NIUNO3_ADMIN_PASSWORD": "buy-review-test-password"}):
            app = create_app(self.f.db, self.f.calendar, clock=lambda: self.now)
        before = {t: self.f.rows(t) for t in ("buy_reviews", "orders", "configs", "bars")}
        with TestClient(app) as client:
            all_etfs = client.get("/api/v1/etfs?sort=symbol&limit=1").json()
            self.assertEqual(all_etfs["total"], 4)
            self.assertEqual(all_etfs["items"][0]["symbol"], "sh510050")
            summary = all_etfs["recent_triggers"]
            self.assertEqual(summary["matched_count"], 2)
            self.assertEqual(summary["pending_count"], 1)
            self.assertEqual(summary["as_of"], self.as_of)
            self.assertEqual(summary["window_start"], self.request()["window_days"][0])
            filtered = client.get(
                "/api/v1/etfs?recent_triggered=true&sort=symbol&limit=1&offset=1"
            ).json()
            self.assertEqual(filtered["total"], 2)
            self.assertEqual(filtered["items"][0]["symbol"], "sh510500")
            self.assertEqual(filtered["recent_triggers"], summary)
            for condition in ("q=510300", "category=equity", "tradable=true"):
                narrowed = client.get(f"/api/v1/etfs?recent_triggered=true&{condition}").json()
                self.assertEqual([i["symbol"] for i in narrowed["items"]], ["sh510300"])
                self.assertEqual(narrowed["recent_triggers"]["matched_count"], 1)
            quiet = client.get("/api/v1/etfs?recent_triggered=true&q=510050").json()
            self.assertEqual(quiet["total"], 0)
            self.assertEqual(quiet["recent_triggers"]["pending_count"], 0)
            self.assertEqual({t: self.f.rows(t) for t in before}, before)

    def test_market_trigger_filter_excludes_stale_settings_and_previous_review_windows(self):
        self.calculate()
        with patch.dict(os.environ, {"NIUNO3_ADMIN_PASSWORD": "buy-review-test-password"}):
            app = create_app(self.f.db, self.f.calendar, clock=lambda: self.now)
        with TestClient(app) as client:
            self.assertEqual(client.get("/api/v1/etfs?recent_triggered=true").json()["total"], 1)
            self.f.db.change_config({"pa_rr_enabled": False}, self.now)
            stale = client.get("/api/v1/etfs?recent_triggered=true").json()
            self.assertEqual(stale["total"], 0)
            self.assertEqual(stale["recent_triggers"]["pending_count"], 1)
            self.f.db.change_config({"pa_rr_enabled": True}, self.now)
            self.assertEqual(client.get("/api/v1/etfs?recent_triggered=true").json()["total"], 1)
            next_day = self.f.calendar.next(date.fromisoformat(self.as_of))
            self.now = at(next_day + "T16:00:00")
            outdated = client.get("/api/v1/etfs?recent_triggered=true").json()
            self.assertEqual(outdated["total"], 0)
            self.assertEqual(outdated["recent_triggers"]["pending_count"], 1)
            self.assertEqual(outdated["recent_triggers"]["as_of"], next_day)

    def test_legacy_causal_result_is_read_without_exposing_hindsight_or_writing_history(self):
        result = self.calculate()
        previous = {**result["input"], "version": "buy-review-v2"}
        previous.pop("strategy")
        causal = {
            key: result[key] for key in ("mode", "strategy", "markers", "checks", "evaluated_days", "reason")
        }
        legacy = {
            **result,
            "version": "buy-review-v2",
            "mode": "retrospective",
            "causal": causal,
            "input": previous,
            "input_sha256": fingerprint(previous),
            "markers": [{"id": "hindsight-only"}],
        }
        with self.f.db.transaction() as conn:
            conn.execute(
                "UPDATE buy_reviews SET payload=?, input_sha256=?",
                (json.dumps(legacy), fingerprint(previous)),
            )
        before = self.f.rows("buy_reviews")
        with self.f.db.connect() as conn:
            payload = review_payload(conn, "sh510300", self.as_of, self.f.calendar)
        self.assertEqual(payload["markers"], result["markers"])
        self.assertEqual(payload["mode"], "causal")
        self.assertNotIn("causal", payload)
        self.assertNotIn("input", payload)
        self.assertEqual(self.f.rows("buy_reviews"), before)
        self.f.db.change_config({"pa_min_rr": 3}, self.now)
        with self.f.db.connect() as conn:
            self.assertFalse(review_payload(conn, "sh510300", self.as_of, self.f.calendar)["ready"])
