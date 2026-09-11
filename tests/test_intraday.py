import copy
import json
import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import ANY, Mock, patch

from fastapi.testclient import TestClient

from app.core.calendar import Calendar
from app.core.types import Instrument
from app.dashboard.api import create_app
from app.market_data.intraday import (
    IntradayService,
    display_state,
    expected_day,
    refresh_interval,
    polling_schedule,
    snapshot_key,
    tencent_minutes,
    ths_minutes,
    trading_minute,
)
from app.market_data.providers import DataError
from tests.helpers import Fixture, at


def tencent_payload(day="20260907", rows=None, symbol="sh510300"):
    quote = [""] * 31
    quote[2], quote[4], quote[30] = symbol[2:], "4.600", day + "150000"
    return {
        "code": 0,
        "data": {
            symbol: {
                "data": {
                    "date": day,
                    "data": rows
                    or ["0930 4.61 10 4610", "1130 4.65 20 9250", "1300 4.64 30 13890", "1500 4.63 40 18520"],
                },
                "qt": {symbol: quote},
            }
        },
    }


def sample(now=None, day="20260907", rows=None):
    return tencent_minutes(tencent_payload(day, rows), "sh510300", now or at("2026-09-07T16:00:00"))


class MinuteParserTests(unittest.TestCase):
    def test_regular_session_axis_compresses_lunch_without_stretching_early_data(self):
        result = sample(
            rows=[
                "0925 4.60",
                "0930 4.61",
                "1000 4.62",
                "1130 4.63",
                "1200 4.63",
                "1300 4.62",
                "1500 4.64",
                "1505 4.64",
                "1530 4.64",
            ]
        )
        self.assertEqual([p["minute"] for p in result["points"]], [0, 30, 120, 120, 240])
        self.assertEqual(result["as_of"], "2026-09-07T15:00:00+08:00")
        self.assertEqual(result["previous_close"], "4.600")
        early = sample(at("2026-09-07T10:01:00"), rows=["0930 4.61", "1000 4.62"])
        self.assertEqual(early["points"][-1]["minute"], 30)
        self.assertEqual(len(early["points"]), 2)
        self.assertIsNone(trading_minute("1200"))

    def test_ths_uses_reported_prices_and_the_matching_symbol(self):
        payload = {
            "hs_510300": {
                "date": "20260907",
                "pre": "4.616",
                "data": "0930,4.635,32624375,4.635,7038700;1500,4.634,10000,4.640,10000;",
            }
        }
        result = ths_minutes(payload, "sh510300", at("2026-09-07T16:00:00"))
        self.assertEqual((result["source"], result["previous_close"]), ("ths", "4.616"))
        self.assertEqual(result["points"][-1]["price"], "4.634")
        with self.assertRaises(DataError):
            ths_minutes(payload, "sz159915", at())

    def test_rejects_identity_mismatch_previous_close_wrong_date_or_bad_metadata(self):
        base = tencent_payload()
        invalid = []
        for index, value in [(2, "510500"), (4, "0"), (4, "NaN"), (30, "20260908150000")]:
            payload = copy.deepcopy(base)
            payload["data"]["sh510300"]["qt"]["sh510300"][index] = value
            invalid.append(payload)
        invalid.extend(
            [{}, {"code": 1, "data": base["data"]}, tencent_payload("20260908"), tencent_payload("20260230")]
        )
        for payload in invalid:
            with self.subTest(payload=payload), self.assertRaises(DataError):
                tencent_minutes(payload, "sh510300", at("2026-09-07T16:00:00"))

    def test_rejects_bad_duplicate_out_of_order_and_future_points(self):
        for rows in [
            ["0930 0"],
            ["0930 -1"],
            ["0930 NaN"],
            ["0930 Infinity"],
            ["0930 bad"],
            ["0930 4.6", "0930 4.7"],
            ["1000 4.6", "0930 4.6"],
            ["0931 4.6"],
            ["2460 4.6"],
            ["0930"],
            ["1200 4.6"],
        ]:
            with self.subTest(rows=rows), self.assertRaises(DataError):
                sample(at(), rows=rows if rows != ["0931 4.6"] else ["0936 4.6"])
        single = sample(at(), rows=["0930 4.6"])
        self.assertEqual(len(single["points"]), 1)

    def test_calendar_status_marks_delays_and_previous_sessions(self):
        calendar = Calendar()
        self.assertEqual(expected_day(calendar, at("2026-09-07T09:29:00")), "2026-09-04")
        self.assertEqual(expected_day(calendar, at("2026-09-07T09:30:00")), "2026-09-07")
        self.assertIsNone(expected_day(calendar, at("2027-01-05T10:00:00")))
        cases = [
            ("2026-09-07T10:01:00", ["0930 4.6", "1000 4.7"], "live", False),
            ("2026-09-07T10:05:00", ["0930 4.6", "1000 4.7"], "delayed", True),
            ("2026-09-07T12:10:00", ["0930 4.6", "1130 4.7"], "break", False),
            ("2026-09-07T15:30:00", ["0930 4.6", "1500 4.7"], "closed", False),
        ]
        for stamp, rows, status, stale in cases:
            now = at(stamp)
            result = display_state(sample(now, rows=rows), calendar, now)
            self.assertEqual((result["status"], result["stale"]), (status, stale))
        previous = sample(day="20260904")
        weekend = display_state(previous, calendar, at("2026-09-06T11:00:00"))
        self.assertEqual((weekend["status"], weekend["stale"]), ("closed", False))
        monday = display_state(previous, calendar, at("2026-09-07T10:00:00"))
        self.assertTrue(monday["stale"])
        self.assertIn("2026-09-04", monday["message"])
        partial = sample(day="20260904", rows=["0930 4.6"])
        self.assertTrue(display_state(partial, calendar, at("2026-09-06T11:00:00"))["stale"])


def active_payload(day="20260907", rows=None, symbol="sh510300"):
    return tencent_payload(day, rows or ["0930 4.61", "1459 4.63"], symbol)


class IntradayServiceTests(unittest.TestCase):
    def setUp(self):
        self.provider = Mock()
        self.tick = 0
        self.service = IntradayService(Calendar(), self.provider, monotonic=lambda: self.tick)
        self.instrument = Instrument("sh510300", "沪深300ETF")
        self.now = at("2026-09-07T14:59:59")

    def test_configured_interval_updates_both_cache_and_schedule(self):
        self.now = at("2026-09-07T10:00:00")
        self.service.fetch = Mock(return_value=sample(self.now, rows=["1000 4.62"]))
        first = self.service.get(self.instrument, self.now, interval=30)
        self.assertEqual(first["refresh_seconds"], 30)
        self.tick = 5
        self.service.get(self.instrument, self.now, interval=30)
        self.service.fetch.assert_called_once()
        self.service.fetch.return_value = sample(self.now, rows=["1000 4.63"])
        updated = self.service.get(self.instrument, self.now, interval=5)
        self.assertEqual(updated["points"][-1]["price"], "4.63")
        self.assertEqual([p["time"] for p in updated["points"]], ["10:00"])
        self.assertEqual(updated["polling"]["interval_seconds"], 5)
        self.assertEqual(self.service.fetch.call_count, 2)
        self.tick = 9.9
        self.service.get(self.instrument, self.now, interval=5)
        self.assertEqual(self.service.fetch.call_count, 2)
        self.tick = 10
        self.service.get(self.instrument, self.now, interval=5)
        self.assertEqual(self.service.fetch.call_count, 3)


    def test_closing_snapshot_completes_missing_or_partial_cache_once(self):
        for partial in (False, True):
            with self.subTest(partial=partial):
                service = IntradayService(Calendar(), self.provider, monotonic=lambda: self.tick)
                service.fetch = Mock(return_value=sample(self.now, rows=["0930 4.61", "1459 4.63"]))
                if partial:
                    service.get(self.instrument, self.now)
                service.fetch.return_value = sample()
                before = service.fetch.call_count
                for stamp in ("2026-09-07T15:00:05", "2026-09-07T22:00:00", "2026-09-08T09:29:59"):
                    result = service.get(self.instrument, at(stamp))
                    self.assertEqual(result["refresh_seconds"], 0)
                    self.assertEqual(result["points"][-1]["time"], "15:00")
                    self.assertTrue(result["snapshot_complete"])
                    self.assertEqual(result["day"], "2026-09-07")
                self.assertIn("最近交易日", result["message"])
                service.get(self.instrument, at("2027-01-05T10:00:00"))
                self.assertEqual(service.fetch.call_count, before + 1)

    def test_incomplete_or_failed_snapshot_is_not_automatically_retried_even_after_restart(self):
        f = Fixture()
        self.addCleanup(f.close)
        service = IntradayService(f.calendar, self.provider, db=f.db)
        self.provider.get.return_value = Mock(json=lambda: active_payload())
        closed = at("2026-09-07T16:00:00")
        first = service.get(self.instrument, closed)
        self.assertFalse(first["snapshot_complete"])
        self.assertTrue(first["stale"])
        self.assertEqual(first["points"][-1]["time"], "14:59")
        restarted = IntradayService(f.calendar, self.provider, db=f.db)
        repeated = restarted.get(self.instrument, at("2026-09-07T16:05:00"))
        self.assertEqual(repeated["points"], first["points"])
        self.assertEqual(self.provider.get.call_count, 2)
        self.provider.get.return_value = Mock(json=lambda: tencent_payload())
        retried = restarted.get(self.instrument, at("2026-09-07T16:05:00"), snapshot_retry=True)
        self.assertTrue(retried["snapshot_complete"])
        self.assertEqual(self.provider.get.call_count, 4)
        self.provider.get.side_effect = DataError("offline")
        other = Instrument("sh000001", "上证指数")
        for stamp in ("2026-09-07T16:06:00", "2026-09-07T16:10:00"):
            self.assertEqual(restarted.get(other, at(stamp))["points"], [])
        self.assertEqual(self.provider.get.call_count, 7)  # EM/Tencent/Sina; no ambiguous THS index.
        restarted.get(other, at("2026-09-07T16:06:30"), snapshot_retry=True)
        self.assertEqual(self.provider.get.call_count, 7)  # Manual retries also have a 60-second cooldown.

    def test_snapshot_requires_matching_day_and_preserves_previous_day_as_stale(self):
        service = self.service
        service.fetch = Mock(return_value=sample(day="20260904"))
        result = service.get(self.instrument, at("2026-09-07T16:00:00"))
        self.assertEqual(result["points"], [])
        self.assertFalse(result["snapshot_complete"])
        service.fetch.assert_called_once()
        self.assertEqual(
            snapshot_key(self.service.calendar, at("2026-09-12T10:00:00")), "2026-09-11T15:00:00+08:00"
        )
        self.assertEqual(
            snapshot_key(self.service.calendar, at("2026-09-07T12:00:00")), "2026-09-07T11:30:00+08:00"
        )

    def test_saved_chart_survives_restart_without_off_hours_upstream_requests(self):
        f = Fixture()
        self.addCleanup(f.close)
        service = IntradayService(f.calendar, self.provider, db=f.db)
        service.fetch = Mock(return_value=sample())
        before = {table: f.rows(table) for table in ("quotes", "bars", "orders", "plans", "configs")}
        result = service.get(self.instrument, at("2026-09-07T16:00:00"))
        restarted = IntradayService(f.calendar, self.provider, db=f.db)
        restored = restarted.get(self.instrument, at("2026-09-07T16:00:00"))
        self.assertEqual(restored["points"], result["points"])
        self.provider.get.assert_not_called()
        self.assertEqual(before, {table: f.rows(table) for table in before})
        restarted.fetch = Mock(
            return_value=sample(at("2026-09-08T09:30:00"), day="20260908", rows=["0930 4.65"])
        )
        self.assertEqual(restarted.get(self.instrument, at("2026-09-08T09:30:00"))["day"], "2026-09-08")
        restarted.fetch.assert_called_once()

    def test_every_success_is_persisted_during_session_and_survives_restart_with_source_offline(self):
        from app.storage.db import Database

        f = Fixture()
        self.addCleanup(f.close)
        first_at = at("2026-09-07T10:00:20")
        service = IntradayService(f.calendar, self.provider, db=f.db, monotonic=lambda: self.tick)
        self.provider.get.return_value = Mock(json=lambda: tencent_payload(rows=["0930 4.61", "1000 4.62"]))
        first = service.get(self.instrument, first_at, interval=5)
        with f.db.connect() as conn:
            saved = json.loads(
                conn.execute(
                    "SELECT payload FROM intraday_snapshots WHERE symbol=?", (self.instrument.symbol,)
                ).fetchone()[0]
            )
        self.assertEqual(saved["points"], first["points"])
        self.assertEqual(saved["fetched_at"], "2026-09-07T10:00:20+08:00")

        # Reopen the database and construct a fresh service without a graceful service close.
        restarted = IntradayService(
            f.calendar, self.provider, db=Database(f.db.path), monotonic=lambda: self.tick
        )
        self.provider.get.side_effect = DataError("offline after restart")
        offline = restarted.get(self.instrument, at("2026-09-07T10:00:30"), interval=5)
        self.assertTrue(offline["stale"])
        self.assertEqual(offline["points"], first["points"])
        self.assertEqual(offline["fetched_at"], first["fetched_at"])
        self.tick = 60
        self.provider.get.side_effect = None
        self.provider.get.return_value = Mock(
            json=lambda: tencent_payload(rows=["0930 4.61", "1000 4.62", "1001 4.63"])
        )
        recovered = restarted.get(self.instrument, at("2026-09-07T10:01:30"), interval=5)
        self.assertFalse(recovered["stale"])
        self.assertEqual(recovered["points"][-1]["time"], "10:01")
        self.assertEqual(restarted.saved(self.instrument.symbol)["points"], recovered["points"])

    def test_queued_request_and_fallback_cannot_start_after_close(self):
        capacity = Mock()
        capacity.__enter__ = Mock(side_effect=lambda: setattr(self, "tick", 2))
        capacity.__exit__ = Mock(return_value=False)
        self.service.capacity = capacity
        self.service.get(self.instrument, self.now)
        self.provider.get.assert_not_called()
        self.tick = 0

        def late_failure(*args, **kwargs):
            self.tick = 2
            raise DataError("late failure")

        self.provider.get.side_effect = late_failure
        with self.assertRaises(DataError):
            self.service.fetch(self.instrument.symbol, self.now)
        self.provider.get.assert_called_once()  # No fallback starts at 15:00:01.

    def test_provider_checks_session_after_rate_limit_wait_and_before_retry(self):
        from app.market_data.providers import PublicProvider
        import httpx

        provider = PublicProvider()
        self.addCleanup(provider.close)
        provider.client.get = Mock(side_effect=httpx.ConnectError("offline"))
        allowed = Mock(side_effect=[True, False])
        with patch("app.market_data.providers.time.sleep"):
            with self.assertRaises(DataError):
                provider.get("https://example.test/minutes", allowed=allowed)
        provider.client.get.assert_called_once()

    def test_polling_calendar_boundaries_and_error_backoff(self):
        calendar = Calendar()
        for stamp, next_day in (("2026-09-11T16:00:00", "2026-09-14"), ("2026-09-30T16:00:00", "2026-10-08")):
            policy = polling_schedule(calendar, at(stamp))
            self.assertEqual(policy["windows"][-2]["start"], next_day + "T09:30:00+08:00")
        self.assertEqual(polling_schedule(calendar, at("2027-01-05T10:00:00"))["windows"], [])
        for stamp, seconds in (
            ("2026-09-07T10:00:00", 10),
            ("2026-09-07T12:00:00", 0),
            ("2026-09-07T16:00:00", 0),
            ("2026-09-06T10:00:00", 0),
            ("2026-09-07T09:29:55", 0),
            ("2026-09-07T12:59:55", 0),
            ("2026-09-07T13:00:00", 10),
        ):
            self.assertEqual(refresh_interval(calendar, at(stamp)), seconds, stamp)
        self.now = at("2026-09-07T10:00:20")
        self.provider.get.side_effect = DataError("offline")
        first = self.service.get(self.instrument, self.now)
        self.assertEqual(first["refresh_seconds"], 60)
        self.assertEqual((first["status"], first["points"]), ("unavailable", []))
        self.tick = 10
        self.assertEqual(self.service.get(self.instrument, self.now), first)
        self.assertEqual(self.provider.get.call_count, 4)  # All sources once, then cache.
        self.tick = 60
        self.provider.get.side_effect = None
        self.provider.get.return_value = Mock(json=lambda: active_payload(rows=["0930 4.61", "1000 4.62"]))
        recovered = self.service.get(self.instrument, self.now)
        self.assertFalse(recovered["stale"])
        self.assertEqual(recovered["refresh_seconds"], 10)

    def test_afternoon_opening_resumes_after_one_lunch_snapshot(self):
        self.service.fetch = Mock(
            return_value=sample(at("2026-09-07T11:29:59"), rows=["0930 4.61", "1129 4.62"])
        )
        self.service.get(self.instrument, at("2026-09-07T11:29:59"))
        idle = self.service.get(self.instrument, at("2026-09-07T12:59:50"))
        self.assertEqual(idle["refresh_seconds"], 0)
        self.assertEqual(self.service.fetch.call_count, 2)
        self.service.get(self.instrument, at("2026-09-07T12:59:55"))
        self.assertEqual(self.service.fetch.call_count, 2)
        self.service.fetch = Mock(
            return_value=sample(at("2026-09-07T13:00:00"), rows=["0930 4.61", "1300 4.63"])
        )
        active = self.service.get(self.instrument, at("2026-09-07T13:00:00"))
        self.service.fetch.assert_called_once()
        self.assertEqual(active["status"], "live")
        self.assertEqual(active["points"][-1]["time"], "13:00")

    def test_index_cache_keeps_index_identity_and_never_falls_back_to_stock_000001(self):
        index = Instrument("sh000001", "上证指数", category="index")
        payload = active_payload(symbol=index.symbol, rows=["0930 3901.5", "1459 3910.2"])
        payload["data"][index.symbol]["qt"][index.symbol][4] = "3900.50"
        self.provider.get.return_value = Mock(json=lambda: payload)
        first = self.service.get(index, self.now)
        self.assertEqual((first["symbol"], first["previous_close"]), ("sh000001", "3900.50"))
        self.assertEqual(first["points"][-1]["price"], "3910.2")
        self.assertFalse(first["stale"])
        self.assertEqual(self.service.get(index, self.now), first)
        self.assertEqual(self.provider.get.call_count, 2)
        self.provider.get.assert_any_call(
            "https://web.ifzq.gtimg.cn/appstock/app/minute/query", {"code": "sh000001"}, allowed=ANY
        )
        # A provider returning the identically numbered Shenzhen stock must be rejected.
        self.tick = 61
        self.provider.get.return_value = Mock(json=lambda: active_payload(symbol="sz000001"))
        cached = self.service.get(index, self.now)
        self.assertTrue(cached["stale"])
        self.assertEqual(cached["points"], first["points"])
        self.assertEqual(self.provider.get.call_count, 6)
        self.assertFalse(any("10jqka" in call.args[0] for call in self.provider.get.call_args_list))


    def test_failed_or_older_refresh_preserves_dated_cached_session(self):
        for stamp, refresh in (
            ("2026-09-07T14:59:59", sample(day="20260904")),
            ("2026-09-08T09:35:00", DataError("offline")),
        ):
            with self.subTest(stamp=stamp):
                service = IntradayService(Calendar(), self.provider, monotonic=lambda: self.tick)
                service.fetch = Mock(return_value=sample(self.now, rows=["0930 4.61", "1459 4.63"]))
                first = service.get(self.instrument, self.now)
                self.assertEqual(service.get(self.instrument, self.now), first)
                service.fetch.assert_called_once()
                if isinstance(refresh, Exception):
                    service.fetch.side_effect = refresh
                else:
                    service.fetch.return_value = refresh
                self.tick += 61
                result = service.get(self.instrument, at(stamp))
                self.assertEqual((result["day"], result["expected_day"]), ("2026-09-07", stamp[:10]))
                self.assertTrue(result["stale"])
                self.assertEqual(result["points"], first["points"])

    def test_same_symbol_concurrency_shares_one_fetch(self):
        entered, release = Event(), Event()

        def fetch(symbol, now):
            entered.set()
            if not release.wait(3):
                raise AssertionError("fixture did not release")
            return sample()

        self.service.fetch = Mock(side_effect=fetch)
        with ThreadPoolExecutor(max_workers=2) as pool:
            one = pool.submit(self.service.get, self.instrument, self.now)
            self.assertTrue(entered.wait(1))
            two = pool.submit(self.service.get, self.instrument, self.now)
            release.set()
            self.assertEqual(one.result(timeout=3), two.result(timeout=3))
        self.service.fetch.assert_called_once()

    def test_primary_old_day_tries_fallback_and_uses_fallback_source(self):
        fallback = {"hs_510300": {"date": "20260907", "pre": "4.6", "data": "0930,4.61;1459,4.63"}}
        self.provider.get.side_effect = [
            DataError("eastmoney offline"),
            Mock(json=lambda: active_payload("20260904")),
            DataError("sina offline"),
            Mock(text="callback(" + json.dumps(fallback) + ")"),
        ]
        result = self.service.get(self.instrument, self.now)
        self.assertEqual((result["source"], result["day"]), ("ths", "2026-09-07"))
        self.assertFalse(result["stale"])


class IntradayApiTests(unittest.TestCase):

    def test_public_minute_routes_preserve_identity_and_never_write_trading_data(self):
        f = Fixture()
        self.addCleanup(f.close)
        now = at("2026-09-07T16:00:00")
        for symbol, kind, name, unit, digits in (
            ("sh000001", "indices", "上证指数", "点", 2),
            ("sh510300", "etfs", "沪深300ETF", "元", 3),
        ):
            with self.subTest(symbol=symbol):
                payload = tencent_payload(symbol=symbol)
                service = Mock()
                service.get.return_value = display_state(tencent_minutes(payload, symbol, now), f.calendar, now)
                with TestClient(create_app(f.db, f.calendar, clock=lambda: now, intraday_service=service)) as client:
                    tables = ("instruments", "quotes", "bars", "configs", "orders", "fills", "cash_ledger", "plans")
                    before = {table: f.rows(table) for table in tables}
                    response = client.get(f"/api/v1/{kind}/{symbol}/intraday")
                    self.assertEqual(response.status_code, 200)
                    body = response.json()
                    self.assertEqual(body["symbol"], symbol)
                if kind == "indices":
                    self.assertEqual((body["name"], body["price_unit"], body["price_digits"]), (name, unit, digits))
                    self.assertEqual(body["points"][-1]["time"], "15:00")
                    for path in ("/indices/sz000001", "/etfs/sh000001", "/etfs/sh999999"):
                        self.assertEqual(client.get("/api/v1" + path + "/intraday").status_code, 404)
                    service.get.assert_called_once()
                    instrument, stamp = service.get.call_args.args
                    self.assertEqual((instrument.symbol, stamp), (symbol, now))
                    if kind == "indices":
                        self.assertEqual(instrument.category, "index")
                        self.assertFalse(instrument.tradable)
                    self.assertEqual(response.headers["cache-control"], "no-store")
                    self.assertEqual(before, {table: f.rows(table) for table in tables})
                service.close.assert_called_once()

if __name__ == "__main__":
    unittest.main()
