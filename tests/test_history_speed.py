"""Public-source speed changes must still reject provisional and mismatched closes."""

import json
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import Mock, patch


from app.core.types import Instrument
from app.market_data.providers import DataError, PublicProvider, closing_price
from tests.helpers import at


class HistorySpeedTests(unittest.TestCase):
    def setUp(self):
        self.provider = PublicProvider()
        self.instrument = Instrument("sh510300", "沪深300ETF")
        self.clock = patch("app.market_data.providers.now_cn", return_value=at("2026-09-07T17:30:00"))
        self.clock.start()
        self.parts = [""] * 60
        for index, value in {
            2: "510300",
            3: "4.634",
            5: "4.635",
            6: "9930783",
            30: "20260907161443",
            33: "4.649",
            34: "4.612",
        }.items():
            self.parts[index] = value
        self.today = {
            "1": "20260907",
            "7": "4.635",
            "8": "4.649",
            "9": "4.612",
            "11": "4.634",
            "13": 993078300,
            "19": "4602371500.000",
            "1968584": "4.248",
            "open": 0,
            "dt": "1727",
        }
        self.old_turnover = "20260904,4.637,4.672,4.599,4.616,841465540,3905673000.000"
        self.current_turnover = ""
        self.calls = []
        self.provider.get = self.get

    def tearDown(self):
        self.clock.stop()
        self.provider.close()

    def get(self, url, params=None):
        self.calls.append((url, params))
        if "fqkline" in url:
            qfq = params["param"].endswith(",qfq")
            key = "qfqday" if qfq else "day"
            # Last qfq price is rounded; the raw history row is still from midday.
            last = (
                ["2026-09-07", "4.64", "4.63", "4.65", "4.61", "9930783"]
                if qfq
                else ["2026-09-07", "4.635", "4.621", "4.648", "4.612", "6098969.8"]
            )
            return Mock(
                json=lambda: {
                    "data": {
                        "sh510300": {
                            key: [["2026-09-04", "4.637", "4.616", "4.672", "4.599", "8414655"], last],
                            "qt": {"sh510300": self.parts},
                        }
                    }
                }
            )
        if url.endswith("today.js"):
            payload = {"hs_510300": self.today}
        else:
            payload = {"today": "20260907", "data": self.old_turnover + self.current_turnover}
        return Mock(text="callback(" + json.dumps(payload) + ")")

    def download(self, **kwargs):
        return self.provider.fallback_history(
            self.instrument, "2026-09-07", start="2026-09-04", limit=8, **kwargs
        )


    def test_closing_supplement_only_replaces_missing_or_provisional_rows(self):
        cases = (
            ("", 993078300, True),
            (";20260907,4.635,4.649,4.612,4.634,983078300,4562371500,1.25", 993078300, True),
            (";20260907,,,,4.634,0,,0.000,,,0", 993078300, True),
            (";20260907,,,,4.634,0,1,0.000,,,0", 993078300, True),
            (";20260907,4.635,4.649,4.612,4.634,993078340,4602371500.000", 993078340, False),
        )
        for row, volume, supplemented in cases:
            with self.subTest(row=row):
                self.current_turnover = row
                self.calls.clear()
                result = self.download()
                self.assertEqual(len(self.calls), 4 if supplemented else 3)
                self.assertTrue(self.calls[2][0].endswith("last8.js"))
                for adjustment in ("raw", "qfq"):
                    bar = result[adjustment][-1]
                    self.assertEqual((bar.open, bar.close, bar.high, bar.low), ("4.635", "4.634", "4.649", "4.612"))
                    self.assertEqual((bar.amount, bar.volume), ("4602371500.000", volume))
                    self.assertEqual(bar.source, "tencent-price+ths-amount")
                    if supplemented:
                        self.assertEqual(bar.turnover_rate, "0.04248")
                self.assertEqual(result["qfq"][0].close, "4.616")
        self.old_turnover = "20260904,,,,4.616,0,,0.000,,,0"
        with self.assertRaises(DataError):
            self.download()

    def test_bad_current_snapshot_never_completes_the_history(self):
        for field, value in [
            ("1", "20260904"),
            ("1", "20260908"),
            ("dt", "1459"),
            ("dt", "1800"),
            ("open", 1),
            ("open", None),
            ("11", "4.635"),
            ("13", 983078300),
            ("19", "--"),
            ("19", "NaN"),
            ("19", "0"),
        ]:
            original = self.today[field]
            with self.subTest(field=field, value=value):
                self.today[field] = value
                with self.assertRaises(DataError):
                    self.download()
            self.today[field] = original

    def test_bad_quote_does_not_unlock_current_day_supplement(self):
        for index, value in [
            (2, "159915"),
            (30, "20260907145959"),
            (30, "20260908160000"),
            (3, "--"),
            (34, "5"),
        ]:
            old = self.parts[index]
            self.parts[index] = value
            self.calls.clear()
            with self.assertRaises(DataError):
                self.download()
            self.assertFalse(any(url.endswith("today.js") for url, _ in self.calls))
            self.parts[index] = old

    def test_quote_cannot_be_used_before_decision_cutoff_or_for_a_different_target(self):
        data = {"qt": {"sh510300": self.parts}}
        self.parts[30] = "20260907150000"
        self.assertIsNone(closing_price(data, "sh510300", "2026-09-07", at("2026-09-07T15:29:59")))
        self.assertIsNone(closing_price(data, "sh510300", "2026-09-04", at("2026-09-07T17:30:00")))

    def test_stale_primary_falls_back_immediately(self):
        self.provider.get = Mock(
            return_value=Mock(
                json=lambda: {
                    "data": {
                        "code": "510300",
                        "klines": ["2026-09-04,2,2,2,2,1000,200000"],
                    }
                }
            )
        )
        self.provider.fallback_history = Mock(return_value={"raw": [], "qfq": []})
        self.provider.history(self.instrument, "2026-09-07")
        self.provider.fallback_history.assert_called_once()
        self.assertGreater(self.provider.history_primary_retry_at, time.monotonic())

    def test_failed_primary_probe_is_single_flight_and_other_jobs_continue(self):
        entered, release = Event(), Event()

        def primary(*args, **kwargs):
            entered.set()
            if not release.wait(3):
                raise AssertionError("probe test did not release worker")
            raise DataError("primary unavailable")

        self.provider.eastmoney_history = Mock(side_effect=primary)
        self.provider.fallback_history = Mock(return_value={"raw": [], "qfq": []})
        with ThreadPoolExecutor(max_workers=1) as pool:
            job = pool.submit(self.provider.history, self.instrument, "2026-09-07")
            try:
                self.assertTrue(entered.wait(1))
                self.provider.history(self.instrument, "2026-09-07")
                self.assertEqual(self.provider.eastmoney_history.call_count, 1)
                self.assertEqual(self.provider.fallback_history.call_count, 1)
            finally:
                release.set()
            job.result(timeout=2)
        self.provider.history(self.instrument, "2026-09-07")
        self.assertEqual(self.provider.eastmoney_history.call_count, 1)
        self.assertEqual(self.provider.fallback_history.call_count, 3)
        self.assertGreater(self.provider.history_primary_retry_at, time.monotonic())

if __name__ == "__main__":
    unittest.main()
