import json
import unittest
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock, patch

from app.automation.service import Worker
from app.automation.targets import calculate_targets
from app.core.types import iso, units
from app.market_data.minute_bars import normalize, save_five_minute
from app.storage.db import Database, dump, get_state, latest_quote, set_state, settings
from app.strategies.profit import (
    LEGACY_PROFIT, PROFIT_REASON, cooling_symbols, latest_profit_sale,
    reentry_setup, profit_quantity,
)
from app.trading.account import reconcile
from app.trading.intraday import IntradayTrader
from tests.helpers import Fixture, at
from tests.test_minute_t import minute_rows
from tests.test_price_action import candles


class ProfitReentryTests(unittest.TestCase):
    def setUp(self):
        self.f = Fixture()
        self.f.buy(at("2026-09-07T09:35:00"))
        self.now = at("2026-09-08T10:30:10")
        self.f.db.change_config({"strategy_model": "price_action", "execution_mode": "intraday",
                                 "intraday_t_enabled": False}, self.now - timedelta(minutes=1))
        with self.f.db.transaction() as conn:
            self.version, self.config = settings(conn)
            set_state(conn, "actions:sh510300", {"at": iso(self.now)})
            set_state(conn, "pa_position:sh510300", {"stop": 940000, "target": 1028000})
        self.f.quote(self.now - timedelta(seconds=1), price="1.028", volume=3000000)
        self.minutes()

    def tearDown(self):
        with self.f.db.connect() as conn:
            self.assertEqual(reconcile(conn), [])
        self.f.close()

    def minutes(self, breakout=False, now=None):
        rows = minute_rows()
        if breakout:
            rows[-1].update(open="1.027", low="1.026", high="1.034", close="1.033")
        with self.f.db.transaction() as conn:
            save_five_minute(conn, normalize(rows, "sh510300", now or self.now, "test"))

    def legacy_order(self, fill=False, reason=LEGACY_PROFIT):
        created = at("2026-09-07T10:21:00")
        with self.f.db.transaction() as conn:
            self.f.engine._order(conn, "legacy-profit", "sh510300", "SELL", 1000,
                                 reason, "risk", created, self.version)
            order = conn.execute("SELECT * FROM orders WHERE key='legacy-profit'").fetchone()
            conn.execute("INSERT INTO intraday_decisions VALUES(?,?,?,?)", (
                order["id"], iso(created), self.version,
                dump({"position_reference": {"target": 1028000}, "quote": {"at": iso(created)}})))
            conn.execute("INSERT INTO risk_intents VALUES(?,?,?)", ("sh510300", reason, iso(created)))
            if fill:
                # Build an immutable legacy ledger as it existed before the fix.
                stamp = at("2026-09-08T09:31:02")
                quote_id = latest_quote(conn, "sh510300")[0]
                self.f.engine._fill(conn, order, quote_id, 1025000, 1000, units(".1"),
                                    stamp, self.f.instrument)
                conn.execute("INSERT OR IGNORE INTO cooldown VALUES(?,?)", ("sh510300", "2026-09-08"))
        return order["id"]

    def daily_pa(self):
        return {"ready": True, "trend": "震荡结构", "action": "hold", "input_sha256": "daily",
                "as_of": "2026-09-07", "entry": .995, "entry_stop": .95, "entry_ceiling": 1.01,
                "target": 1.1, "structural_stop": .94, "resistance": 1.1, "setup": "Pin Bar",
                "signal_day": "2026-09-04", "raw": {"entry": 995000, "entry_stop": 950000,
                "entry_ceiling": 1010000, "target": 1100000, "structural_stop": 940000,
                "resistance": 1100000}}

    def test_legacy_t_plus_one_profit_is_cancelled_and_cannot_match_at_open(self):
        self.legacy_order()
        self.f.engine.match(self.now)
        self.assertEqual(len(self.f.rows("fills")), 1)
        self.f.engine.risk_check(self.now)
        self.assertEqual(self.f.rows("orders")[1]["status"], "cancelled")
        self.assertNotIn("legacy-profit", [r["key"] for r in self.f.rows("orders") if r["status"] == "pending"])

    def test_target_touch_without_rejection_does_not_sell(self):
        self.minutes(breakout=True)
        self.f.quote(self.now + timedelta(seconds=1), price="1.033")
        self.f.engine.risk_check(self.now + timedelta(seconds=1))
        self.assertEqual(len(self.f.rows("orders")), 1)
        self.assertEqual(self.f.rows("cooldown"), [])

    def test_profit_rejection_fills_without_stop_cooldown(self):
        with self.f.db.transaction() as conn:
            set_state(conn, "pa_position:sh510300", {"stop": 940000, "target": 1000000})
        self.f.engine.risk_check(self.now)
        order = self.f.rows("orders")[-1]
        self.assertEqual(order["reason"], PROFIT_REASON)
        self.f.quote(self.now + timedelta(seconds=30), price="1.028", volume=4000000)
        self.f.engine.match(self.now + timedelta(seconds=30))
        self.assertEqual(self.f.rows("orders")[-1]["status"], "filled")
        self.assertEqual(self.f.rows("cooldown"), [])
        self.assertEqual(self.f.rows("risk_intents"), [])
        self.assertEqual(self.f.rows("fills")[-1]["quantity"], 200)
        self.assertEqual(sum(r["quantity"] for r in self.f.rows("lots")), 800)
        with self.f.db.connect() as conn:
            reference = get_state(conn, "pa_position:sh510300")
        self.assertEqual(reference["profit_budget"], 200)
        self.assertEqual(reference["profit_sold"], 200)
        self.assertEqual(reference["stop"], 1001000)

    def test_profit_budget_survives_restart_and_never_replenishes(self):
        self.test_profit_rejection_fills_without_stop_cooldown()
        Database(self.f.db.path)
        later = self.now + timedelta(seconds=60)
        self.f.quote(later, price="1.028", volume=5000000)
        self.f.engine.risk_check(later)
        self.assertEqual(len(self.f.rows("orders")), 2)
        # A subsequent true structural failure exits the remaining core.
        self.f.quote(later + timedelta(seconds=1), price=".99", volume=6000000)
        self.f.engine.risk_check(later + timedelta(seconds=1))
        order = self.f.rows("orders")[-1]
        self.assertEqual(order["quantity"], 800)
        self.assertIn("结构失效", order["reason"])

    def test_partial_profit_fill_cancel_and_new_candle_only_offer_remaining_budget(self):
        with self.f.db.transaction() as conn:
            set_state(conn, "pa_position:sh510300", {"stop": 940000, "target": 1000000})
        self.f.engine.risk_check(self.now)
        later = self.now + timedelta(seconds=30)
        self.f.quote(later, price="1.028", volume=3010000)
        self.f.engine.match(later)
        self.assertEqual(self.f.rows("fills")[-1]["quantity"], 100)
        with self.f.db.connect() as conn:
            reference = get_state(conn, "pa_position:sh510300")
        self.assertEqual(reference["profit_sold"], 100)
        self.assertEqual(profit_quantity(reference, 900, 900, 100), 100)
        Database(self.f.db.path)
        with self.f.db.transaction() as conn:
            conn.execute("UPDATE orders SET status='cancelled' WHERE id=?", (self.f.rows("orders")[-1]["id"],))
        with patch("app.trading.engine.profit_signal", return_value={"bar_at": "next-completed-candle"}):
            self.f.engine.risk_check(self.now + timedelta(minutes=6))
        # Refresh a current quote before evaluating that new candle.
        self.f.quote(self.now + timedelta(minutes=6), price="1.028", volume=4000000)
        with patch("app.trading.engine.profit_signal", return_value={"bar_at": "next-completed-candle"}):
            self.f.engine.risk_check(self.now + timedelta(minutes=6))
        self.assertEqual(self.f.rows("orders")[-1]["quantity"], 100)

    def test_small_inventory_is_not_liquidated_to_meet_a_round_lot(self):
        self.assertEqual(profit_quantity({}, 300, 300, 100), 0)
        self.assertEqual(profit_quantity({}, 1000, 0, 100), 0)
        self.assertEqual(profit_quantity({}, 300, 300, 100, preserve_core=False), 300)

    def test_rejection_at_unbroken_target_still_exits_all_available_inventory(self):
        self.f.engine.risk_check(self.now)
        self.assertEqual(self.f.rows("orders")[-1]["quantity"], 1000)

    def test_unfilled_cancelled_exit_can_be_reclassified_after_confirmed_breakout(self):
        self.f.engine.risk_check(self.now)
        self.assertEqual(self.f.rows("orders")[-1]["quantity"], 1000)
        for minutes in (5, 10):
            later = self.now + timedelta(minutes=minutes)
            rows = minute_rows()
            for row in rows:
                row["at"] = iso(at(row["at"]) + timedelta(minutes=minutes))
                for key in ("open", "high", "low", "close"):
                    row[key] = str(Decimal(row[key]) + Decimal(".05"))
            with self.f.db.transaction() as conn:
                save_five_minute(conn, normalize(rows, "sh510300", later, "test"))
            self.f.quote(later, price="1.078", volume=4000000)
            self.f.engine.risk_check(later)
        self.assertEqual(self.f.rows("orders")[-2]["status"], "cancelled")
        self.assertEqual(self.f.rows("orders")[-1]["quantity"], 200)

    def test_corporate_action_scales_frozen_trim_budget_and_preserves_spent_fraction(self):
        from app.trading.actions import adjust_pa_reference

        with self.f.db.transaction() as conn:
            set_state(conn, "pa_position:sh510300", {"stop": 1000000, "target": 1100000,
                                                    "profit_budget": 200, "profit_sold": 100})
            adjust_pa_reference(conn, "sh510300", ratio=Decimal(2))
            reference = get_state(conn, "pa_position:sh510300")
        self.assertEqual(reference, {"stop": 500000, "target": 550000,
                                     "profit_budget": 400, "profit_sold": 200})

    def test_breakout_after_submission_blocks_and_cancels_profit_order(self):
        self.f.engine.risk_check(self.now)
        later = self.now + timedelta(seconds=30)
        self.f.quote(later, price="1.035", volume=4000000)
        self.f.engine.match(later)
        self.assertEqual(len(self.f.rows("fills")), 1)
        self.f.engine.risk_check(later)
        self.assertEqual(self.f.rows("orders")[-1]["status"], "cancelled")

    def test_profit_order_expires_and_does_not_repeat_same_candle(self):
        self.f.engine.risk_check(self.now)
        later = self.now + timedelta(minutes=5)
        self.f.quote(later, price="1.028")
        self.f.engine.risk_check(later)
        self.assertEqual(self.f.rows("orders")[-1]["status"], "cancelled")
        self.assertEqual(len(self.f.rows("orders")), 2)

    def test_stop_overrides_profit_and_keeps_cooldown(self):
        self.f.engine.risk_check(self.now)
        later = self.now + timedelta(seconds=30)
        self.f.quote(later, price=".930", volume=4000000)
        self.f.engine.risk_check(later)
        self.assertEqual(self.f.rows("orders")[-2]["status"], "cancelled")
        self.assertIn("结构失效", self.f.rows("orders")[-1]["reason"])
        with self.f.db.connect() as conn:
            self.assertIn("sh510300", cooling_symbols(conn, "2026-09-08", self.config))

    def test_only_proven_profit_cooldown_is_ignored_without_mutating_history(self):
        self.legacy_order(fill=True)
        before = {t: self.f.rows(t) for t in ("fills", "cooldown", "cash_ledger", "orders")}
        with self.f.db.transaction() as conn:
            self.assertNotIn("sh510300", cooling_symbols(conn, "2026-09-08", self.config))

        self.assertEqual(before, {t: self.f.rows(t) for t in before})
        with self.f.db.transaction() as conn:
            conn.execute("INSERT INTO risk_intents VALUES(?,?,?)", ("sh510300", "结构止损", iso(self.now)))
            self.assertIn("sh510300", cooling_symbols(conn, "2026-09-08", self.config))

    def test_reentry_requires_breakout_and_rejects_chasing_stale_and_future_data(self):
        self.legacy_order(fill=True)
        quote = self.f.quote(self.now, price="1.032")
        with self.f.db.connect() as conn:
            sale = latest_profit_sale(conn, "sh510300", self.now)
            self.assertIsNone(reentry_setup(conn, self.f.instrument, self.daily_pa(), sale, quote,
                                           self.config, self.now, Decimal(1))[0])
        self.minutes(breakout=True)
        with self.f.db.connect() as conn:
            pa, _ = reentry_setup(conn, self.f.instrument, self.daily_pa(), sale, quote,
                                  self.config, self.now, Decimal(1))
            self.assertEqual(pa["raw"]["entry"], 1031000)
            self.assertEqual(pa["raw"]["entry_ceiling"], 1034000)
            for q, stamp in [(replace(quote, ask=1035000), self.now),
                             (quote, self.now + timedelta(minutes=3)),
                             (quote, at("2026-09-08T10:30:03"))]:
                self.assertIsNone(reentry_setup(conn, self.f.instrument, self.daily_pa(), sale, q,
                                               self.config, stamp, Decimal(1))[0])

    def test_rr_switch_applies_to_reentry(self):
        self.legacy_order(fill=True)
        self.minutes(breakout=True)
        quote = self.f.quote(self.now, price="1.032")
        pa = self.daily_pa()
        pa["raw"].update(target=1035000, resistance=1035000)
        with self.f.db.connect() as conn:
            sale = latest_profit_sale(conn, "sh510300", self.now)
            self.assertIsNone(reentry_setup(conn, self.f.instrument, pa, sale, quote,
                                           self.config, self.now, Decimal(1))[0])
            config = self.config.model_copy(update={"pa_rr_enabled": False})
            self.assertIsNotNone(reentry_setup(conn, self.f.instrument, pa, sale, quote,
                                              config, self.now, Decimal(1))[0])

    def test_worker_fetches_minutes_with_t_disabled(self):
        worker = Worker(self.f.db, self.f.calendar, Mock())
        try:
            worker.submit = Mock(return_value=False)
            worker.schedule(self.now)
            self.assertIn("minute5:sh510300", [c.args[0] for c in worker.submit.call_args_list])
        finally:
            worker.close()

    def test_588170_retained_bars_offer_bounded_reentry_after_original_ceiling(self):
        data = json.loads((Path(__file__).parent / "fixtures/sh588170-profit-reentry.json").read_text())
        from app.core.types import Quote
        from app.trading.intraday import confirm_price_action

        instrument = replace(self.f.instrument, symbol=data["symbol"])
        config = self.config.model_copy(update={"pa_rr_enabled": False})
        sale = {"order_id": 13, "at": "2026-09-16T09:31:02+08:00", "quantity": 21600,
                "target": data["prior_profit_target"]}
        previous = {}
        for raw_quote in data["quotes"]:
            quote = Quote(**raw_quote)
            now = at(quote.fetched_at)
            with self.f.db.transaction() as conn:
                set_state(conn, "minute5:" + instrument.symbol, {
                    "symbol": instrument.symbol, "source": "tencent", "bars": data["minutes"],
                    "as_of": data["minutes"][-1]["at"], "fetched_at": iso(now),
                })
                pa, _ = reentry_setup(conn, instrument, data["daily_pa"], sale, quote, config, now, Decimal(1))
            self.assertGreater(quote.ask, data["daily_pa"]["raw"]["entry_ceiling"])
            self.assertEqual((pa["raw"]["entry"], pa["raw"]["entry_ceiling"]), (951000, 956000))
            self.assertEqual(pa["raw"]["entry_stop"], 939000)
            plan = {"id": iso(now), "created_at": iso(now), "targets": {instrument.symbol: ".2"},
                    "rows": [{"symbol": instrument.symbol, "pa": pa, "evaluated_quote_at": quote.at}]}
            previous = confirm_price_action(plan, previous, 62, config, now)
        self.assertEqual(previous[instrument.symbol]["count"], 2)

    def test_same_day_locked_inventory_does_not_latch_profit(self):
        fresh = Fixture()
        try:
            fresh.buy(at("2026-09-08T09:35:00"))
            fresh.db.change_config({"strategy_model": "price_action", "execution_mode": "intraday"}, self.now)
            with self.f.db.connect() as conn:
                minute = get_state(conn, "minute5:sh510300")
            with fresh.db.transaction() as conn:
                set_state(conn, "minute5:sh510300", minute)
                set_state(conn, "pa_position:sh510300", {"stop": 940000, "target": 1028000})
            fresh.quote(self.now, price="1.028")
            fresh.engine.risk_check(self.now)
            self.assertEqual(fresh.rows("risk_intents"), [])
            self.assertEqual(len(fresh.rows("orders")), 1)
        finally:
            fresh.close()

    def test_reentry_targets_confirmation_partial_fills_restart_and_no_duplicate(self):
        sale_id = self.legacy_order(fill=True)
        self.minutes(breakout=True)
        history = [replace(b, day="2026-09-07") if i == 139 else b for i, b in enumerate(candles())]
        with self.f.db.transaction() as conn:
            conn.executemany("INSERT INTO bars VALUES(?,?,?,?,?,?)", [
                ("sh510300", "qfq", b.day, dump(b.to_dict()), "test", iso(self.now)) for b in history
            ])
        trader = IntradayTrader(self.f.engine)

        def refresh(stamp, volume):
            self.minutes(breakout=True, now=stamp)
            self.f.quote(stamp, price="1.032", previous_close=units(history[-1].close), volume=volume)
            with patch("app.strategies.price_action.analyze_many", return_value=[self.daily_pa()]):
                plan = calculate_targets(self.f.db, "2026-09-07", stamp)
            with self.f.db.transaction() as conn:
                set_state(conn, "live_targets", plan)
            trader.tick(stamp)
            return plan

        plan = refresh(self.now, 3000000)
        self.assertEqual(plan["rows"][0]["pa"]["action"], "buy")
        self.assertEqual(len(self.f.rows("orders")), 2)
        refresh(self.now + timedelta(seconds=60), 4000000)
        order = self.f.rows("orders")[-1]
        self.assertEqual(order["key"], f"intraday:reentry:sh510300:{sale_id}")
        self.assertEqual(order["quantity"], 1000)
        self.f.quote(self.now + timedelta(seconds=75), price="1.032", volume=4010000)
        self.f.engine.match(self.now + timedelta(seconds=75))
        self.assertEqual(self.f.rows("orders")[-1]["filled"], 100)
        Database(self.f.db.path)
        refresh(self.now + timedelta(seconds=90), 4100000)
        self.f.engine.match(self.now + timedelta(seconds=90))
        self.assertEqual(self.f.rows("orders")[-1]["filled"], 1000)
        refresh(self.now + timedelta(seconds=100), 4200000)
        self.assertEqual(len(self.f.rows("orders")), 3)
        self.assertEqual(self.f.rows("lots")[-1]["available_day"], "2026-09-09")
        with self.f.db.connect() as conn:
            self.assertEqual(get_state(conn, "pa_position:sh510300")["stop"], 1025000)
