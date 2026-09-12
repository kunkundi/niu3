import json
import unittest
from dataclasses import replace
from datetime import timedelta, timezone
from unittest.mock import Mock

from fastapi.testclient import TestClient

from app.automation.service import Worker
from app.core.config import Settings
from app.core.types import iso, units
from app.dashboard.api import create_app
from app.dashboard.minute_t import display_context
from app.market_data.minute_bars import (
    normalize,
    save_five_minute,
    tencent_bars,
    sina_bars,
    fetch_five_minute,
)
from app.market_data.providers import DataError
from app.storage.db import Database, get_state, set_state, settings, latest_quote, sold_today, put_instrument
from app.strategies.minute_t import context
from app.trading.account import reconcile
from app.trading.intraday import IntradayTrader
from tests.helpers import Fixture, at
from tests import test_price_action


def minute_rows(day="2026-09-08"):
    prices = [
        ("1.002", "1.004", "1.000", "1.003"),
        ("1.006", "1.010", "1.005", "1.009"),
        ("1.012", "1.016", "1.011", "1.015"),
        ("1.018", "1.022", "1.017", "1.021"),
        ("1.024", "1.030", "1.023", "1.029"),
        ("1.027", "1.030", "1.026", "1.029"),
        ("1.030", "1.031", "1.027", "1.028"),
    ]
    start = at(day + "T10:00:00")
    return [
        {
            "at": iso(start + timedelta(minutes=5 * i)),
            **dict(zip(("open", "high", "low", "close"), p)),
            "volume": "100000",
            "amount": "102000",
        }
        for i, p in enumerate(prices)
    ]


def buy_rows():
    return minute_rows() + [
        {
            "at": iso(at("2026-09-08T10:35:00")),
            "open": "1.020",
            "high": "1.021",
            "low": "1.010",
            "close": "1.011",
            "volume": "100000",
        },
        {
            "at": iso(at("2026-09-08T10:40:00")),
            "open": "1.000",
            "high": "1.003",
            "low": ".999",
            "close": "1.002",
            "volume": "100000",
        },
    ]


class MinuteBarTests(unittest.TestCase):
    def setUp(self):
        self.f = Fixture()
        self.now = at("2026-09-08T10:30:10")

    def tearDown(self):
        self.f.close()

    def save(self, rows=None, now=None, source="test"):
        result = normalize(rows or minute_rows(), "sh510300", now or self.now, source)
        with self.f.db.transaction() as conn:
            save_five_minute(conn, result)
        return result

    def test_real_ohlcv_precision_and_future_bar_are_handled_causally(self):
        rows = minute_rows()
        rows.append({"at": iso(self.now + timedelta(minutes=5)), "open": "NaN"})
        data = self.save(rows)
        self.assertEqual(len(data["bars"]), 7)
        self.assertEqual(data["bars"][-1]["high"], units("1.031"))
        with self.f.db.connect() as conn:
            self.assertTrue(context(conn, "sh510300", self.now, 1000)["ready"])
        self.assertEqual(
            Settings.model_validate_json('{"execution_mode":"intraday"}').intraday_t_model, "daily"
        )

    def test_rejects_malformed_completed_ohlcv_duplicates_and_old_session(self):
        for changes in [{"high": "0.2"}, {"volume": "-1"}, {"volume": "1.5"}, {"close": "NaN"}]:
            rows = minute_rows()
            rows[-1].update(changes)
            with self.subTest(changes=changes), self.assertRaises(DataError):
                self.save(rows)
        with self.assertRaises(DataError):
            self.save(minute_rows() + [minute_rows()[-1]])
        with self.assertRaises(DataError):
            self.save(minute_rows("2026-09-07"))

    def test_unfinished_publication_guard_gaps_and_staleness_block_signals(self):
        self.save(now=at("2026-09-08T10:30:03"))
        with self.f.db.connect() as conn:
            self.assertFalse(context(conn, "sh510300", at("2026-09-08T10:30:03"), 1000)["ready"])
        rows = minute_rows()
        rows.pop(3)
        # Keep enough rows to distinguish a gap from the warmup condition.
        rows.insert(0, {**rows[0], "at": iso(at("2026-09-08T09:55:00"))})
        self.save(rows)
        with self.f.db.connect() as conn:
            self.assertIn("缺口", context(conn, "sh510300", self.now, 1000)["message"])
            self.assertFalse(context(conn, "sh510300", self.now + timedelta(minutes=3), 1000)["ready"])
            self.assertFalse(context(conn, "sh510300", at("2026-09-09T10:30:10"), 1000)["ready"])

    def test_restart_preserves_bars_and_older_response_cannot_replace_snapshot(self):
        saved = self.save()
        self.save(minute_rows()[:-1], self.now - timedelta(minutes=5))
        restarted = Database(self.f.db.path)
        with restarted.connect() as conn:
            self.assertEqual(get_state(conn, "minute5:sh510300"), saved)
            self.assertTrue(context(conn, "sh510300", self.now, 1000)["ready"])
        self.assertEqual(len(self.f.rows("minute_bars")), 7)

    def test_source_parsers_use_end_timestamps_and_correct_volume_units(self):
        rows = minute_rows()
        qt = ["", "", "510300"]
        payload = {
            "code": 0,
            "data": {
                "sh510300": {
                    "qt": {"sh510300": qt},
                    "m5": [
                        [
                            r["at"][:16].replace("-", "").replace("T", "").replace(":", ""),
                            r["open"],
                            r["close"],
                            r["high"],
                            r["low"],
                            "1000",
                        ]
                        for r in rows
                    ],
                }
            },
        }
        result = tencent_bars(payload, "sh510300", self.now)
        self.assertEqual(result["bars"][-1]["volume"], 100000)
        self.assertIsNone(result["bars"][-1]["amount"])
        sina = [{**r, "day": r["at"][:19].replace("T", " ")} for r in rows]
        self.assertEqual(
            sina_bars(sina, "sh510300", self.now)["bars"],
            normalize(rows, "sh510300", self.now, "test")["bars"],
        )
        payload["data"]["sh510300"]["qt"]["sh510300"][2] = "510500"
        with self.assertRaises(DataError):
            tencent_bars(payload, "sh510300", self.now)
        provider = Mock()
        provider.get.side_effect = [DataError("unavailable"), Mock(json=lambda: sina)]
        self.assertEqual(fetch_five_minute(provider, "sh510300", self.now)["source"], "sina")


class MinuteTTradingTests(unittest.TestCase):
    signal = test_price_action.PriceActionTests.signal
    confirmed = test_price_action.PriceActionTests.confirmed
    fill = test_price_action.PriceActionTests.fill
    inventory = test_price_action.PriceActionTests.inventory

    def setUp(self):
        test_price_action.PriceActionTests.setUp(self)
        self.f.db.change_config({"intraday_t_model": "minute5"}, self.now)
        self.inventory()
        self.now = at("2026-09-08T10:30:10")

    def tearDown(self):
        test_price_action.PriceActionTests.tearDown(self)

    def minutes(self, rows=None, when=None):
        when = when or self.now
        with self.f.db.transaction() as conn:
            save_five_minute(conn, normalize(rows or minute_rows(), "sh510300", when, "test"))

    def close_position(self):
        self.signal(self.now - timedelta(seconds=60), action="exit", selected=False, price="1.028")
        quantity = sum(row["quantity"] for row in self.f.rows("lots"))
        self.f.order(side="SELL", quantity=quantity, when=self.now - timedelta(seconds=10))
        with self.f.db.connect() as conn:
            self.assertEqual(sold_today(conn, self.now), set())
        self.fill(self.now, price="1.028", volume=4000000)
        self.assertEqual(sum(row["quantity"] for row in self.f.rows("lots")), 0)

    def sell(self):
        self.minutes()
        self.confirmed(self.now, action="hold", price="1.028", t_allowed=False, trend="上升结构")
        sell = self.f.rows("orders")[-1]
        self.assertEqual(sell["kind"], "t_sell")
        return sell

    def complete_sale(self, volume=4000000):
        sell = self.sell()
        self.fill(self.now + timedelta(seconds=30), price="1.028", volume=volume)
        self.trader.tick(self.now + timedelta(seconds=35))
        return sell

    def buy_signal(self, rows=None, price="1.001"):
        later = at("2026-09-08T10:40:10")
        self.minutes(rows or buy_rows(), later)
        self.confirmed(later, action="hold", price=price, t_allowed=False, trend="上升结构")
        return later

    def test_round_trip_uses_minutes_even_without_daily_t_range_and_preserves_t_plus_one(self):
        sell = self.complete_sale()
        evidence = json.loads(self.f.rows("intraday_decisions")[-1]["payload"])["minute_t"]
        self.assertEqual(evidence["support"], units("1.000"))
        self.assertEqual(len(evidence["bars"]), 7)
        self.trader = IntradayTrader(self.f.engine)
        later = self.buy_signal()
        buy = self.f.rows("orders")[-1]
        self.assertEqual((buy["kind"], buy["quantity"]), ("t_buy", sell["quantity"]))
        self.fill(later + timedelta(seconds=30), price="1.001", volume=6000000)
        self.trader.tick(later + timedelta(seconds=35))
        self.assertEqual(self.f.rows("t_cycles")[-1]["status"], "complete")
        self.assertEqual(self.f.rows("lots")[-1]["available_day"], "2026-09-09")
        self.assertEqual(sum(x["quantity"] for x in self.f.rows("lots")), 19000)
        with self.f.db.connect() as conn:
            self.assertEqual(reconcile(conn), [])

    def test_only_actual_partial_sale_can_be_bought_back(self):
        self.complete_sale(volume=1010000)
        self.assertEqual(self.f.rows("orders")[-1]["filled"], 100)
        self.f.engine.expire(self.now + timedelta(minutes=5))
        self.buy_signal()
        self.assertEqual(
            (self.f.rows("orders")[-1]["kind"], self.f.rows("orders")[-1]["quantity"]), ("t_buy", 100)
        )

    def test_no_pattern_no_minutes_and_downtrend_do_not_sell(self):
        self.confirmed(self.now, action="hold", price="1.028")
        self.assertEqual(len(self.f.rows("orders")), 1)
        self.minutes()
        self.confirmed(self.now, action="hold", price="1.028", trend="下降结构")
        self.assertEqual(len(self.f.rows("orders")), 1)
        rows = minute_rows()
        rows[-1]["open"] = "1.027"
        self.minutes(rows)
        self.confirmed(self.now, action="hold", price="1.028", trend="上升结构")
        self.assertEqual(len(self.f.rows("orders")), 1)

    def test_support_breach_ends_cycle_even_if_latest_candle_recovers(self):
        self.complete_sale()
        rows = buy_rows()
        rows[-2]["low"] = ".990"
        self.buy_signal(rows)
        self.assertEqual(self.f.rows("t_cycles")[-1]["status"], "abandoned")
        self.assertFalse(any(o["kind"] == "t_buy" for o in self.f.rows("orders")))

    def test_frozen_support_is_used_instead_of_new_rolling_low(self):
        self.complete_sale()
        rows = buy_rows()
        rows[-2]["low"] = ".9995"
        later = self.buy_signal(rows)
        evidence = json.loads(self.f.rows("intraday_decisions")[-1]["payload"])["minute_t"]
        with self.f.db.connect() as conn:
            current = context(conn, "sh510300", later, 1000)
        self.assertEqual(evidence["support"], units("1.000"))
        self.assertTrue(current["ready"])
        self.assertNotEqual(current["support"], evidence["support"])
        self.assertEqual(evidence["sell_order_id"], self.f.rows("orders")[-2]["id"])

    def test_partial_buyback_retries_only_remainder_on_a_new_closed_bar(self):
        sell = self.complete_sale()
        later = self.buy_signal()
        self.fill(later + timedelta(seconds=30), price="1.001", volume=1010000)
        self.assertEqual(self.f.rows("orders")[-1]["filled"], 100)
        self.f.engine.expire(later + timedelta(minutes=5))
        rows = buy_rows()
        for minute in (45, 50):
            rows.append({**rows[-1], "at": iso(at(f"2026-09-08T10:{minute}:00"))})
        retry = at("2026-09-08T10:50:10")
        self.minutes(rows, retry)
        self.confirmed(retry, action="hold", price="1.001", t_allowed=False)
        self.assertEqual(self.f.rows("orders")[-1]["quantity"], sell["quantity"] - 100)
        self.fill(retry + timedelta(seconds=30), price="1.001", volume=4000000)
        self.trader.tick(retry + timedelta(seconds=35))
        self.assertEqual(self.f.rows("t_cycles")[-1]["status"], "complete")
        self.assertEqual(
            sum(o["filled"] for o in self.f.rows("orders") if o["kind"] == "t_buy"), sell["quantity"]
        )

    def test_today_t_plus_one_inventory_cannot_start_minute_sale(self):
        self.now = at("2026-09-07T10:30:10")
        self.minutes(minute_rows("2026-09-07"))
        self.confirmed(self.now, action="hold", price="1.028")
        self.assertEqual(len(self.f.rows("t_cycles")), 0)

    def test_t_plus_zero_buyback_is_available_today_and_keeps_daily_protection(self):
        from dataclasses import replace
        from app.storage.db import put_instrument

        with self.f.db.transaction() as conn:
            put_instrument(conn, replace(self.f.instrument, settlement=0))
            set_state(conn, "pa_position:sh510300", {"stop": units(".95")})
        self.complete_sale()
        later = self.buy_signal()
        self.fill(later + timedelta(seconds=30), price="1.001", volume=6000000)
        self.assertEqual(self.f.rows("lots")[-1]["available_day"], "2026-09-08")
        with self.f.db.connect() as conn:
            self.assertEqual(get_state(conn, "pa_position:sh510300")["stop"], units(".95"))

    def test_chasing_and_changed_minute_source_block_pending_fill(self):
        self.sell()
        self.fill(self.now + timedelta(seconds=30), price="1.010")
        self.assertEqual(self.f.rows("orders")[-1]["filled"], 0)
        self.assertIn("执行区间", self.f.rows("orders")[-1]["blocked_reason"])
        with self.f.db.transaction() as conn:
            data = get_state(conn, "minute5:sh510300")
            data["source"] = "other"
            set_state(conn, "minute5:sh510300", data)
        self.fill(self.now + timedelta(seconds=50), price="1.028")
        self.assertEqual(self.f.rows("orders")[-1]["filled"], 0)

    def test_same_closed_bar_is_not_reused_after_restart_or_quote_refresh(self):
        self.sell()
        self.trader = IntradayTrader(self.f.engine)
        self.trader.tick(self.now + timedelta(seconds=5))
        self.assertEqual(len(self.f.rows("t_cycles")), 1)
        self.assertEqual(len(self.f.rows("orders")), 2)
        with self.f.db.transaction() as conn:
            plan = get_state(conn, "live_targets")
            config_id, config = settings(conn)
            old = json.loads(
                conn.execute(
                    "SELECT payload FROM intraday_decisions ORDER BY order_id DESC LIMIT 1"
                ).fetchone()[0]
            )
            quote = latest_quote(conn, "sh510300")[1]
            repeat = self.trader._submit(
                conn,
                plan,
                old["account"],
                self.f.instrument,
                quote,
                "SELL",
                100,
                "t_sell",
                "test",
                self.now,
                config_id,
                config,
                minute_t=old["minute_t"],
            )
            self.assertIsNone(repeat)

    def test_risk_exit_cancels_minute_cycle_before_matching(self):
        self.sell()
        when = self.now + timedelta(seconds=30)
        self.signal(when, action="hold", price=".930")
        self.f.engine.risk_check(when)
        self.trader.tick(when)
        self.assertEqual(self.f.rows("t_cycles")[-1]["status"], "abandoned")
        self.assertEqual(self.f.rows("orders")[1]["status"], "cancelled")
        self.assertTrue(any(o["kind"] == "risk" for o in self.f.rows("orders")))

    def test_fee_filter_and_late_session_prevent_new_cycle(self):
        self.f.db.change_config({"minimum_commission": "100"}, self.now)
        self.minutes()
        self.confirmed(self.now, action="hold", price="1.028")
        self.assertEqual(len(self.f.rows("t_cycles")), 0)
        self.f.db.change_config({"minimum_commission": "0"}, self.now)
        later = at("2026-09-08T14:46:10")
        rows = minute_rows()
        for i, row in enumerate(rows):
            row["at"] = iso(at("2026-09-08T14:15:00") + timedelta(minutes=5 * i))
        self.minutes(rows, later)
        self.confirmed(later, action="hold", price="1.028")
        self.assertEqual(len(self.f.rows("t_cycles")), 0)

    def test_public_status_reads_minute_levels_without_creating_orders(self):
        self.minutes()
        self.signal(self.now, action="hold", price="1.028")
        with self.f.db.transaction() as conn:
            set_state(conn, "worker_heartbeat", {"at": iso(self.now)})
        count = len(self.f.rows("orders"))
        with TestClient(create_app(self.f.db, self.f.calendar, clock=lambda: self.now)) as client:
            response = client.get("/api/v1/t-strategy")
            self.assertEqual(response.status_code, 200)
            data = response.json()
            self.assertTrue(data["enabled"])
            self.assertEqual(data["items"][0]["support"], 1.0)
        self.assertEqual(len(self.f.rows("orders")), count)

    def test_worker_collects_and_persists_minutes_independently_of_browser(self):
        worker = Worker(self.f.db, self.f.calendar, Mock())
        self.worker = worker
        worker.submit = Mock(return_value=False)
        worker.schedule(self.now)
        keys = [call.args[0] for call in worker.submit.call_args_list]
        self.assertIn("minute5:sh510300", keys)
        data = normalize(minute_rows(), "sh510300", self.now, "test")
        worker.ingest("minute5:sh510300", data, self.now)
        with self.f.db.connect() as conn:
            self.assertTrue(context(conn, "sh510300", self.now, 1000)["ready"])

    def test_closed_today_keeps_reference_prices_without_changing_trading_state(self):
        self.close_position()
        self.minutes()
        with self.f.db.transaction() as conn:
            set_state(conn, "worker_heartbeat", {"at": iso(self.now)})
        tables = [
            "orders",
            "fills",
            "lots",
            "cash_ledger",
            "position_ledger",
            "t_cycles",
            "intraday_decisions",
        ]
        with TestClient(create_app(self.f.db, self.f.calendar, clock=lambda: self.now)) as client:
            before = {table: self.f.rows(table) for table in tables}
            response = client.get("/api/v1/t-strategy")
            self.assertEqual(response.status_code, 200)
            [item] = response.json()["items"]
            self.assertEqual(item["quantity"], 0)
            self.assertEqual(item["available"], 0)
            self.assertTrue(item["observation_only"])
            self.assertFalse(item["frozen"])
            self.assertEqual(item["support"], 1.0)
            self.assertEqual(item["resistance"], 1.03)
            self.assertLess(item["support_stop"], item["support"])
            self.assertIn("今日已清仓", item["message"])
            self.assertNotIn("T+1", item["message"])
            self.assertEqual(before, {table: self.f.rows(table) for table in tables})

    def test_closed_today_waits_for_real_minutes_and_disappears_after_day_rollover(self):
        self.close_position()
        with TestClient(create_app(self.f.db, self.f.calendar, clock=lambda: self.now)) as client:
            [item] = client.get("/api/v1/t-strategy").json()["items"]
            self.assertIsNone(item["support"])
            self.assertIsNone(item["resistance"])
            self.assertIsNone(item["support_stop"])
        self.minutes()
        with self.f.db.connect() as conn:
            self.assertEqual(sold_today(conn, self.now), {"sh510300"})
            self.assertEqual(sold_today(conn, self.now.astimezone(timezone.utc)), {"sh510300"})
            self.assertEqual(sold_today(conn, self.now - timedelta(seconds=1)), set())
            self.assertEqual(sold_today(conn, at("2026-09-09T00:00:00")), set())
        self.now += timedelta(days=1)
        with TestClient(create_app(self.f.db, self.f.calendar, clock=lambda: self.now)) as client:
            self.assertEqual(client.get("/api/v1/t-strategy").json()["items"], [])

    def test_worker_continues_minutes_for_closed_today_even_when_removed_from_watchlist(self):
        self.close_position()
        with self.f.db.transaction() as conn:
            put_instrument(conn, replace(self.f.instrument, watched=False))
        self.worker = Worker(self.f.db, self.f.calendar, Mock())
        self.worker.submit = Mock(return_value=False)
        self.worker.schedule(self.now)
        self.assertIn("minute5:sh510300", [call.args[0] for call in self.worker.submit.call_args_list])
        self.worker.submit.reset_mock()
        self.worker.schedule(self.now + timedelta(days=1))
        self.assertNotIn("minute5:sh510300", [call.args[0] for call in self.worker.submit.call_args_list])

    def test_weekend_preserves_sold_etfs_and_levels_until_the_next_open(self):
        self.now = at("2026-09-11T10:30:10")
        self.close_position()
        self.minutes(minute_rows("2026-09-11"))
        tables = ["quotes", "minute_bars", "state", "orders", "fills", "lots", "t_cycles", "cash_ledger"]
        with TestClient(create_app(self.f.db, self.f.calendar, clock=lambda: self.now)) as client:
            before = {table: self.f.rows(table) for table in tables}
            for stamp in (
                "2026-09-11T16:00:00", "2026-09-12T00:01:00", "2026-09-13T23:59:59",
                "2026-09-14T09:29:59",
            ):
                with self.subTest(stamp=stamp):
                    self.now = at(stamp).astimezone(timezone.utc)
                    data = client.get("/api/v1/t-strategy").json()
                    self.assertEqual(data["day"], "2026-09-11")
                    self.assertTrue(data["session_snapshot"])
                    self.assertFalse(data["running"])
                    [item] = data["items"]
                    self.assertTrue(item["observation_only"])
                    self.assertEqual((item["support"], item["resistance"]), (1.0, 1.03))
                    self.assertIn("2026-09-11", item["message"])
                    self.assertEqual(client.get("/api/v1/status").json()["display_day"], "2026-09-11")
                    with self.f.db.connect() as conn:
                        self.assertFalse(context(conn, "sh510300", self.now, 1000)["ready"])
            self.now = at("2026-09-14T09:30:00")
            data = client.get("/api/v1/t-strategy").json()
            self.assertEqual(data["day"], "2026-09-14")
            self.assertFalse(data["session_snapshot"])
            self.assertEqual(data["items"], [])
        self.assertEqual(before, {table: self.f.rows(table) for table in tables})

    def test_holiday_preserves_real_structure_but_rejects_wrong_dates_and_future_sources(self):
        saved_at = at("2026-02-13T14:59:50")
        rows = minute_rows("2026-02-13")
        for i, row in enumerate(rows):
            row["at"] = iso(at("2026-02-13T14:25:00") + timedelta(minutes=5 * i))
        self.minutes(rows, saved_at)
        self.now = at("2026-02-23T23:59:00")
        with self.f.db.connect() as conn:
            shown = display_context(conn, "sh510300", self.f.calendar, self.now, 1000)
            self.assertTrue(shown["ready"])
            self.assertEqual(shown["bar_at"], rows[-1]["at"])
            self.assertFalse(context(conn, "sh510300", self.now, 1000)["ready"])
            self.assertFalse(display_context(conn, "sh510300", self.f.calendar, at("2026-02-24T09:30:00"), 1000)["ready"])
        with self.f.db.transaction() as conn:
            source = get_state(conn, "minute5:sh510300")
            for changes in (
                {"as_of": "2026-02-12T14:55:00+08:00"},
                {"fetched_at": "2026-02-24T15:00:00+08:00"},
            ):
                with self.subTest(changes=changes):
                    set_state(conn, "minute5:sh510300", {**source, **changes})
                    self.assertFalse(display_context(conn, "sh510300", self.f.calendar, self.now, 1000)["ready"])

    def test_minute_download_slots_rotate_to_least_recent_attempts(self):
        with self.f.db.transaction() as conn:
            version, _ = settings(conn)
            for symbol in ["sh510500", "sh512800"]:
                put_instrument(conn, replace(self.f.instrument, symbol=symbol))
                self.f.engine._order(conn, symbol, symbol, "BUY", 100, "test", "rebalance", self.now, version)
        self.worker = Worker(self.f.db, self.f.calendar, Mock())
        self.worker.attempts.update({"minute5:sh510300": 100, "minute5:sh510500": 50})
        self.worker.submit = Mock(return_value=True)
        self.worker.schedule(self.now)
        keys = [
            call.args[0] for call in self.worker.submit.call_args_list if call.args[0].startswith("minute5:")
        ]
        self.assertEqual(keys, ["minute5:sh512800", "minute5:sh510500"])
