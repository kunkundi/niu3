import unittest
from datetime import date
from unittest.mock import Mock

from fastapi.testclient import TestClient

from app.dashboard.api import create_app
from app.dashboard.trade_intraday import recorded_intraday
from app.core.types import iso, units
from app.storage.maintenance import retain_evidence
from tests.helpers import Fixture, at


class RecordedIntradayTests(unittest.TestCase):
    def setUp(self):
        self.f = Fixture()
        self.addCleanup(self.f.close)

    def read(self, day="2026-09-07", now="2026-09-08T10:00:00"):
        with self.f.db.connect() as conn:
            return recorded_intraday(conn, "sh510300", date.fromisoformat(day), self.f.calendar, at(now))

    def test_all_source_samples_preserve_subminute_prices_seconds_and_gaps(self):
        for stamp, price in [("09:30:05", "1.01"), ("09:30:50", "1.02"), ("09:35:10", "1.03"), ("11:30:00", "1.04"), ("13:00:01", "1.05")]:
            self.f.quote(at("2026-09-07T" + stamp), price)
        result = self.read()
        self.assertEqual([p["minute"] for p in result["points"]], [0, 0, 5, 120, 120])
        self.assertEqual([p["price"] for p in result["points"]], [1.01, 1.02, 1.03, 1.04, 1.05])
        self.assertEqual([p["time"] for p in result["points"]], ["09:30:05", "09:30:50", "09:35:10", "11:30:00", "13:00:01"])
        self.assertEqual(result["points"][0]["at"], "2026-09-07T09:30:05+08:00")
        self.assertEqual(result["previous_close"], 1)

    def test_repeated_timestamps_use_last_valid_quote_without_removing_flat_prices(self):
        self.f.quote(at("2026-09-07T09:30:05"), "1.01", source="first")
        self.f.quote(at("2026-09-07T09:30:05"), "1.02", source="second")
        self.f.quote(at("2026-09-07T09:30:05"), "1.03", source="invalid", status="unknown")
        self.f.quote(at("2026-09-07T09:30:35"), "1.02")
        self.f.quote(at("2026-09-07T09:31:05"), "1.02")
        points = self.read()["points"]
        self.assertEqual([p["time"] for p in points], ["09:30:05", "09:30:35", "09:31:05"])
        self.assertEqual([p["price"] for p in points], [1.02, 1.02, 1.02])

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
        self.assertEqual([p["time"] for p in result["points"]], ["09:34:00"])
        self.assertEqual(self.read("2026-09-06")["points"], [])

    def test_conflicting_previous_close_does_not_mix_price_bases(self):
        self.f.quote(at("2026-09-07T09:30:00"), previous_close=units("9"))
        self.f.quote(at("2026-09-07T09:31:00"))
        self.assertEqual(len(self.read()["points"]), 1)
        self.assertEqual(self.read()["previous_close"], 1)

    def test_long_holiday_retains_the_previous_session_minutes_until_trading_resumes(self):
        self.f.quote(at("2026-02-12T09:30:00"))
        for stamp in ["09:30:00", "10:00:00", "11:00:00", "14:59:00", "15:00:00"]:
            self.f.quote(at("2026-02-13T" + stamp))
        before = self.read("2026-02-13", "2026-02-13T16:00:00")["points"]
        with self.f.db.transaction() as conn:
            retain_evidence(conn, at("2026-02-23T23:00:00"), self.f.calendar)
        self.assertEqual(self.read("2026-02-13", "2026-02-23T23:00:00")["points"], before)
        self.assertEqual(self.read("2026-02-12", "2026-02-23T23:00:00")["points"], [])
        with self.f.db.transaction() as conn:
            retain_evidence(conn, at("2026-02-24T09:29:59"), self.f.calendar)
        self.assertEqual(self.read("2026-02-13", "2026-02-24T09:29:59")["points"], before)
        with self.f.db.transaction() as conn:
            retain_evidence(conn, at("2026-02-25T16:00:00"), self.f.calendar)
        self.assertEqual(len(self.read("2026-02-13", "2026-02-25T16:00:00")["points"]), 2)

    def test_day_query_is_read_only_does_not_fetch_current_session_or_invent_missing_history(self):
        self.f.quote(at("2026-09-07T09:30:00"), "1.02")
        self.f.quote(at("2026-09-07T09:30:20"), "1.04")
        self.f.quote(at("2026-09-07T09:30:40"), "1.03")
        service = Mock()
        with TestClient(create_app(self.f.db, self.f.calendar, clock=lambda: at("2026-09-08T10:00:00"), intraday_service=service)) as client:
            tables = ["quotes", "orders", "fills", "cash_ledger", "position_ledger", "intraday_snapshots"]
            before = {table: self.f.rows(table) for table in tables}
            response = client.get("/api/v1/etfs/sh510300/intraday?day=2026-09-07")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["day"], "2026-09-07")
            self.assertEqual(response.json()["points"][0]["price"], 1.02)
            self.assertEqual([p["price"] for p in response.json()["points"]], [1.02, 1.04, 1.03])
            self.assertEqual(client.get("/api/v1/etfs/sh510300/intraday?day=2026-09-04").json()["points"], [])
            for day in ["2026-09-09", "2026-02-30", "bad"]:
                self.assertEqual(client.get(f"/api/v1/etfs/sh510300/intraday?day={day}").status_code, 422)
            self.assertEqual(client.get("/api/v1/etfs/sh999999/intraday?day=2026-09-07").status_code, 404)
            self.assertEqual(before, {table: self.f.rows(table) for table in tables})
        service.get.assert_not_called()
