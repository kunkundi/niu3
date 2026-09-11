import unittest
from datetime import date, timedelta
from unittest.mock import Mock

from fastapi.testclient import TestClient

from app.dashboard.api import create_app
from app.market_data.index_chart import IndexChartService, index_bars
from app.market_data.providers import DataError
from tests.helpers import Fixture, at


def index_payload():
    end = date(2026, 9, 7)
    rows = [
        [(end - timedelta(days=i)).isoformat(), "3000", "3010", "3020", "2990", "1000000"]
        for i in range(300, -1, -1)
    ]
    return {"data": {"sh000001": {"day": rows}}}


class IndexChartTests(unittest.TestCase):
    def test_completed_window_preserves_ohlcv_and_never_invents_amount(self):
        bars = index_bars(index_payload(), "2026-09-04")
        self.assertEqual(len(bars), 250)
        self.assertEqual(bars[-1]["day"], "2026-09-04")
        self.assertEqual(bars[-1]["close"], "3010")
        self.assertEqual(bars[-1]["volume"], 1000000)
        self.assertIsNone(bars[-1]["amount"])

    def test_wrong_identity_duplicate_dates_and_invalid_prices_are_rejected(self):
        with self.assertRaises(DataError):
            index_bars({"data": {"sz000001": {"day": []}}}, "2026-09-07")
        for column, value in [(1, "NaN"), (2, "Infinity"), (3, "2980"), (4, "3050"), (5, "-1")]:
            payload = index_payload()
            payload["data"]["sh000001"]["day"][-1][column] = value
            with self.assertRaises((DataError, ArithmeticError)):
                index_bars(payload, "2026-09-07")
        payload = index_payload()
        payload["data"]["sh000001"]["day"].append(payload["data"]["sh000001"]["day"][-1])
        with self.assertRaises(DataError):
            index_bars(payload, "2026-09-07")

    def test_cache_coalesces_refresh_and_reports_failure_without_fabricating_candles(self):
        provider = Mock()
        provider.get.return_value.json.return_value = index_payload()
        elapsed = [0]
        service = IndexChartService(provider, monotonic=lambda: elapsed[0])
        now = at("2026-09-07T10:30:00")
        fresh = service.get("2026-09-04", now)
        self.assertFalse(fresh["stale"])
        self.assertEqual(service.get("2026-09-04", now), fresh)
        self.assertEqual(provider.get.call_count, 2)  # EM unavailable, then Tencent; cache does not refetch.
        provider.get.side_effect = DataError("offline")
        elapsed[0] = 61
        stale = service.get("2026-09-04", now)
        self.assertTrue(stale["stale"])
        self.assertEqual(stale["bars"], fresh["bars"])
        self.assertEqual(stale["fetched_at"], fresh["fetched_at"])
        self.assertIn("缓存", stale["warning"])
        empty = IndexChartService(provider).get("2026-09-04", now)
        self.assertEqual(empty["bars"], [])
        self.assertIsNone(empty["as_of"])

    def test_public_index_endpoint_is_isolated_from_fund_universe_and_trading(self):
        f = Fixture()
        provider = Mock()
        provider.get.return_value.json.return_value = index_payload()
        try:
            original = {
                table: f.rows(table) for table in ("instruments", "bars", "quotes", "orders", "configs")
            }
            with TestClient(
                create_app(f.db, f.calendar, clock=at, index_chart_service=IndexChartService(provider))
            ) as client:
                response = client.get("/api/v1/indices/sh000001/chart")
                self.assertEqual(response.status_code, 200)
                body = response.json()
                self.assertEqual(body["symbol"], "sh000001")
                self.assertEqual(body["kind"], "index")
                self.assertEqual(body["volume_unit"], "手")
                self.assertEqual(body["as_of"], "2026-09-04")
                self.assertEqual(client.get("/api/v1/indices/sh600000/chart").status_code, 404)
            self.assertEqual({table: f.rows(table) for table in original}, original)
        finally:
            f.close()
