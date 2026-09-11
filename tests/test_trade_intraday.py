import unittest
from datetime import date
from unittest.mock import Mock

from fastapi.testclient import TestClient

from app.dashboard.api import create_app
from app.dashboard.trade_intraday import recorded_intraday
from app.core.types import iso, units
from tests.helpers import Fixture, at


class RecordedIntradayTests(unittest.TestCase):
    def setUp(self):
        self.f = Fixture()
        self.addCleanup(self.f.close)

    def read(self, day="2026-09-07", now="2026-09-08T10:00:00"):
        with self.f.db.connect() as conn:
            return recorded_intraday(conn, "sh510300", date.fromisoformat(day), self.f.calendar, at(now))

    def test_actual_minute_samples_use_last_valid_price_and_preserve_gaps(self):
        for stamp, price in [("09:30:05", "1.01"), ("09:30:50", "1.02"), ("09:35:10", "1.03"), ("11:30:00", "1.04"), ("13:00:01", "1.05")]:
            self.f.quote(at("2026-09-07T" + stamp), price)
        result = self.read()
        self.assertEqual([p["minute"] for p in result["points"]], [0, 5, 120, 120])
        self.assertEqual([p["price"] for p in result["points"]], [1.02, 1.03, 1.04, 1.05])
        self.assertEqual(result["points"][0]["at"], "2026-09-07T09:30:50+08:00")
        self.assertEqual(result["previous_close"], 1)

    def test_wrong_day_future_stale_unknown_and_non_session_quotes_are_excluded(self):
        for stamp in ["09:29:59", "11:30:01", "12:00:00", "15:00:01"]:
            self.f.quote(at("2026-09-07T" + stamp))
        self.f.quote(at("2026-09-06T09:30:00"))
        self.f.quote(at("2026-09-07T10:05:00"))
        self.f.quote(at("2026-09-07T09:31:00"), status="unknown")
        self.f.quote(at("2026-09-07T09:32:00"), previous_close=0)
        self.f.quote(at("2026-09-07T09:33:00"), fetched_at=iso(at("2026-09-07T10:00:00")))
        self.f.quote(at("2026-09-07T09:34:00"), "1.04")
        result = self.read(now="2026-09-07T10:00:00")
        self.assertEqual([p["time"] for p in result["points"]], ["09:34"])
        self.assertEqual(self.read("2026-09-06")["points"], [])

    def test_conflicting_previous_close_does_not_mix_price_bases(self):
        self.f.quote(at("2026-09-07T09:30:00"), previous_close=units("9"))
        self.f.quote(at("2026-09-07T09:31:00"))
        self.assertEqual(len(self.read()["points"]), 1)
        self.assertEqual(self.read()["previous_close"], 1)

    def test_day_query_is_read_only_does_not_fetch_current_session_or_invent_missing_history(self):
        self.f.quote(at("2026-09-07T09:30:00"), "1.02")
        service = Mock()
        with TestClient(create_app(self.f.db, self.f.calendar, clock=lambda: at("2026-09-08T10:00:00"), intraday_service=service)) as client:
            tables = ["quotes", "orders", "fills", "cash_ledger", "position_ledger", "intraday_snapshots"]
            before = {table: self.f.rows(table) for table in tables}
            response = client.get("/api/v1/etfs/sh510300/intraday?day=2026-09-07")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["day"], "2026-09-07")
            self.assertEqual(response.json()["points"][0]["price"], 1.02)
            self.assertEqual(client.get("/api/v1/etfs/sh510300/intraday?day=2026-09-04").json()["points"], [])
            for day in ["2026-09-09", "2026-02-30", "bad"]:
                self.assertEqual(client.get(f"/api/v1/etfs/sh510300/intraday?day={day}").status_code, 422)
            self.assertEqual(client.get("/api/v1/etfs/sh999999/intraday?day=2026-09-07").status_code, 404)
            self.assertEqual(before, {table: self.f.rows(table) for table in tables})
        service.get.assert_not_called()
