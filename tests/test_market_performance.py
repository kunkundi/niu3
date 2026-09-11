import os
import unittest
from dataclasses import replace
from decimal import Decimal
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.core.types import units, iso
from app.dashboard.api import create_app
from app.dashboard.market_performance import calculate_performance, load_histories
from app.storage.db import dump, put_instrument
from tests.helpers import Fixture, at


class MarketPerformanceTests(unittest.TestCase):
    def setUp(self):
        self.f = Fixture()
        self.addCleanup(self.f.close)
        self.now = at("2026-09-07T10:00:00")
        self.day = self.now.date().isoformat()
        self.history = self.make_history(self.day)
        self.quote = self.f.quote(
            self.now, price="240", previous_close=units("238"), high=units("246"), low=units("230")
        )

    def make_history(self, day):
        index = self.f.calendar.days.index(day)
        self.days = self.f.calendar.days[index - 20 : index + 1]
        return {
            adjustment: {
                day: {
                    "day": day,
                    "close": str((100 + i) * factor),
                    "high": str((103 + i) * factor),
                    "low": str((95 + i) * factor),
                    "open": str((100 + i) * factor),
                    "amount": "100000",
                    "volume": 1000,
                }
                for i, day in enumerate(self.days)
            }
            for adjustment, factor in (("qfq", 1), ("raw", 2))
        }

    def calculate(self, quote=None, complete_day=None, now=None):
        return calculate_performance(
            quote or self.quote,
            self.history,
            self.f.calendar,
            now or self.now,
            complete_day or self.days[-2],
        )

    def assert_percent(self, result, key, numerator, denominator):
        self.assertAlmostEqual(float(result[key]), numerator / denominator - 1)


    def test_return_windows_use_adjusted_basis_and_exchange_calendar(self):
        for day in ("2026-09-07", "2026-09-28"):
            with self.subTest(day=day):
                self.history = self.make_history(day)
                now = at(day + "T10:00:00")
                self.history["raw"][self.days[-2]]["close"] = "300"
                result = self.calculate(replace(self.quote, at=iso(now)), now=now)
                self.assertEqual(result["as_of"], day)
                for field, price in (("change_pct", 240), ("low_change_pct", 230), ("high_change_pct", 246)):
                    self.assert_percent(result, field, price, 238)
                for period, baseline in ((5, 115), (10, 110), (20, 100)):
                    self.assert_percent(result, f"return{period}", 120, baseline)
                if day == "2026-09-28":
                    self.assertEqual(self.days[-2], "2026-09-24")
                    self.assertNotIn("2026-09-25", self.days)


    def test_missing_or_invalid_history_only_blocks_affected_returns(self):
        for missing in ([self.days[-9]], self.days[:-6]):
            with self.subTest(missing=missing):
                self.history = self.make_history(self.day)
                for day in missing:
                    del self.history["qfq"][day]
                result = self.calculate()
                self.assert_percent(result, "return5", 120, 115)
                self.assertIsNone(result["return10"])
                self.assertIsNone(result["return20"])
        baseline = self.history["qfq"][self.days[-6]]
        for close, expected in (("120", Decimal(0)), ("0", None), ("NaN", None), ("-1", None)):
            with self.subTest(close=close):
                baseline["close"] = close
                actual = self.calculate()["return5"]
                self.assertEqual(Decimal(actual) if actual is not None else None, expected)


    def test_extremes_use_completed_bars_only_and_never_price_limits(self):
        for low, high in ((0, 0), (units("245"), units("230")), (units("250"), units("260"))):
            with self.subTest(low=low, high=high):
                result = self.calculate(replace(self.quote, low=low, high=high))
                self.assertIsNone(result["low_change_pct"])
                self.assertIsNone(result["high_change_pct"])
                self.assertIsNotNone(result["change_pct"])
        quote = replace(self.quote, high=0, low=0)
        self.history["qfq"][self.day].update(close="9999", high="9999", low="0.01")
        live = self.calculate(quote)
        self.assert_percent(live, "return20", 120, 100)
        self.assertIsNone(live["high_change_pct"])
        self.history = self.make_history(self.day)
        now = at(self.day + "T16:00:00")
        closed = self.calculate(replace(quote, at=iso(now)), complete_day=self.day, now=now)
        self.assert_percent(closed, "low_change_pct", 230, 238)
        self.assert_percent(closed, "high_change_pct", 246, 238)

    def test_missing_previous_close_uses_only_matching_completed_daily_data(self):
        quote = replace(self.quote, previous_close=0, high=0, low=0, source="ths")
        live = self.calculate(quote)
        self.assertIsNone(live["change_pct"])
        self.assertIsNone(live["return5"])
        now = at("2026-09-07T19:00:00")
        result = self.calculate(replace(quote, at=iso(now)), complete_day=self.day, now=now)
        self.assert_percent(result, "change_pct", 120, 119)
        self.assert_percent(result, "high_change_pct", 123, 119)
        self.assert_percent(result, "return20", 120, 100)
        result = self.calculate(
            replace(quote, at=iso(now), last=units("241")), complete_day=self.day, now=now
        )
        self.assertIsNone(result["return20"])

    def test_weekend_quotes_keep_friday_date_and_future_quote_is_ignored(self):
        now = at("2026-09-12T12:00:00")
        self.history = self.make_history("2026-09-11")
        quote = replace(self.quote, at=iso(at("2026-09-11T15:00:00")))
        result = self.calculate(quote, complete_day="2026-09-11", now=now)
        self.assertEqual(result["as_of"], "2026-09-11")
        self.assert_percent(result, "return20", 120, 100)
        future = replace(quote, at=iso(at("2026-09-14T10:00:00")), last=units("9999"))
        for unavailable in (None, future):
            with self.subTest(quote=unavailable):
                result = calculate_performance(unavailable, self.history, self.f.calendar, now, "2026-09-11")
                self.assertEqual(result["as_of"], "2026-09-11")
                self.assert_percent(result, "return20", 120, 100)

    def test_api_sorting_paginates_all_metrics_and_keeps_missing_data_last(self):
        symbols = ["sh510300", "sh510500", "sh512000", "sh512001"]
        with self.f.db.transaction() as conn:
            for symbol in symbols:
                put_instrument(conn, replace(self.f.instrument, symbol=symbol))
                if symbol == symbols[-1]:
                    continue
                for adjustment, records in self.history.items():
                    for day, bar in records.items():
                        conn.execute(
                            "INSERT INTO bars VALUES(?,?,?,?,?,?)",
                            (symbol, adjustment, day, dump(bar), "fixture", iso(self.now)),
                        )
            loaded = load_histories(conn, symbols, self.days[-2])
            self.assertNotIn(self.day, loaded[symbols[0]]["qfq"])
        for symbol, value in zip(symbols[:3], ("240", "220", "230")):
            self.f.quote(
                self.now,
                symbol=symbol,
                price=value,
                previous_close=units("238"),
                high=units(Decimal(value) + 1),
                low=units(Decimal(value) - 1),
                volume=2_000_000,
            )
        with patch.dict(os.environ, {"NIUNO3_ADMIN_PASSWORD": "niuno3-test-password-2026"}):
            client = TestClient(create_app(self.f.db, self.f.calendar, clock=lambda: self.now))
        self.addCleanup(client.close)
        before = {table: self.f.rows(table) for table in ("bars", "quotes", "plans", "orders")}
        for field in ("change", "low_change_pct", "high_change_pct", "return5", "return10", "return20"):
            for ascending, expected in (
                (False, [symbols[0], symbols[2], symbols[1], symbols[3]]),
                (True, [symbols[1], symbols[2], symbols[0], symbols[3]]),
            ):
                sort = field + ("_asc" if ascending else "")
                with self.subTest(sort=sort):
                    rows = []
                    for offset in (0, 2):
                        response = client.get(f"/api/v1/etfs?sort={sort}&offset={offset}&limit=2")
                        self.assertEqual(response.status_code, 200)
                        rows.extend(response.json()["items"])
                    self.assertEqual([r["symbol"] for r in rows], expected)
        listing = client.get("/api/v1/etfs").json()["items"][0]
        detail = client.get("/api/v1/etfs/" + listing["symbol"]).json()
        self.assertEqual(listing["performance"], detail["performance"])
        self.assertEqual(before, {table: self.f.rows(table) for table in before})


if __name__ == "__main__":
    unittest.main()
