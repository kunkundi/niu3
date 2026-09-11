import json
import unittest
from dataclasses import replace

from fastapi.testclient import TestClient

from app.core.types import units, Quote
from app.dashboard.api import create_app
from app.dashboard.live_candle import live_candle_payload
from app.market_data.providers import parse_sina
from app.storage.db import put_quote
from tests.helpers import Fixture, at


class LiveCandleTests(unittest.TestCase):
    def setUp(self):
        self.f = Fixture()
        self.now = at("2026-09-10T10:00:00")
        self.quote = self.f.quote(self.now, price="2.100", previous_close=units("2.000"),
                                  open=units("2.020"), high=units("2.200"), low=units("1.900"))
        with self.f.db.transaction() as conn:
            conn.execute("INSERT INTO bars VALUES(?,?,?,?,?,?)", (
                "sh510300", "qfq", "2026-09-09", json.dumps({"day": "2026-09-09", "close": "1.000"}), "test", self.now.isoformat()
            ))

    def tearDown(self):
        self.f.close()

    def get(self, now=None):
        with self.f.db.connect() as conn:
            return live_candle_payload(conn, "sh510300", self.f.calendar, now or self.now)

    def test_current_candle_uses_true_open_and_qfq_basis_without_writing_history(self):
        before = {table: self.f.rows(table) for table in ["bars", "quotes", "orders", "fills"]}
        with TestClient(create_app(self.f.db, self.f.calendar, clock=lambda: self.now)) as client:
            response = client.get("/api/v1/etfs/sh510300/live-candle")
            self.assertEqual(response.status_code, 200)
            bar = response.json()["bar"]
            self.assertEqual(client.get("/api/v1/etfs/sz159915/live-candle").status_code, 404)
        self.assertEqual((bar["open"], bar["high"], bar["low"], bar["close"]), (1.01, 1.1, .95, 1.05))
        self.assertEqual(bar["volume"], self.quote.volume)
        self.assertEqual((bar["day"], bar["basis_day"]), ("2026-09-10", "2026-09-09"))
        self.assertFalse(bar["stale"])
        self.assertEqual(before, {table: self.f.rows(table) for table in before})

    def test_missing_or_inconsistent_open_never_becomes_a_synthetic_candle(self):
        for changes in [{"open": 0}, {"open": units("3")}, {"low": 0}, {"previous_close": 0}, {"volume": 0}]:
            with self.f.db.transaction() as conn:
                conn.execute("DELETE FROM quotes")
                put_quote(conn, replace(self.quote, **changes))
            self.assertIsNone(self.get()["bar"])
        legacy = self.quote.to_dict()
        del legacy["open"]
        self.assertEqual(Quote(**legacy).open, 0)

    def test_old_future_premarket_and_non_session_dates_are_not_drawn_as_today(self):
        for stamp in ["2026-09-09T15:00:00", "2026-09-10T10:01:00", "2026-09-10T09:20:00"]:
            with self.f.db.transaction() as conn:
                conn.execute("DELETE FROM quotes")
                put_quote(conn, replace(self.quote, at=at(stamp).isoformat()))
            self.assertIsNone(self.get()["bar"])
        self.assertIsNone(self.get(at("2026-09-12T10:00:00"))["bar"])
        self.assertIsNone(self.get(at("2026-09-10T09:20:00"))["bar"])

    def test_stale_quote_keeps_actual_values_and_lunch_does_not_age_a_complete_snapshot(self):
        self.assertTrue(self.get(at("2026-09-10T10:02:00"))["bar"]["stale"])
        self.f.quote(at("2026-09-10T11:29:30"), price="2.1", previous_close=units("2"),
                     open=units("2.02"), high=units("2.2"), low=units("1.9"))
        self.assertFalse(self.get(at("2026-09-10T12:00:00"))["bar"]["stale"])
        with self.f.db.transaction() as conn:
            conn.execute("DELETE FROM bars")
        self.assertIsNone(self.get(at("2026-09-10T12:00:00"))["bar"])

    def test_sina_open_uses_reported_open_field(self):
        parts = ["0"] * 33
        for i, value in {1:"2.02", 2:"2", 3:"2.1", 4:"2.2", 5:"1.9", 6:"2.099", 7:"2.101", 8:"1000", 9:"2100", 30:"2026-09-10", 31:"10:00:00", 32:"00"}.items():
            parts[i] = value
        quote = parse_sina('var hq_str_sh510300="' + ','.join(parts) + '";', {"sh510300":self.f.instrument}, self.now)[0]
        self.assertEqual(quote.open, units("2.02"))
