import math
import unittest
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from unittest.mock import Mock

from app.automation.service import Worker
from app.automation.targets import calculate_targets
from app.core.config import Settings
from app.core.types import iso
from app.storage.db import get_state, latest_quote, set_state, settings
from app.strategies.focus import FOCUS_POLICY
from app.strategies.price_action import STRATEGY, build_plan, price_decision, trigger_problem
from app.trading.account import reconcile
from app.trading.actions import adjust_pa_reference
from app.trading.intraday import IntradayTrader, live_problem
from tests.helpers import Fixture, at, bars


def candles():
    history = []
    for i, b in enumerate(bars()):
        p = 1 + i * 0.002 + math.sin(i * 0.6) * 0.025
        history.append(
            replace(b, open=str(p - 0.004), close=str(p + 0.004), high=str(p + 0.012), low=str(p - 0.012))
        )
    history[-1] = replace(history[-1], open="1.3", close="1.305", high="1.31", low="1.24")
    return history


class PriceActionTests(unittest.TestCase):
    def setUp(self):
        self.f = Fixture()
        self.now = at("2026-09-07T10:30:00")
        self.config = Settings(execution_mode="intraday", strategy_model="price_action")
        self.f.db.change_config({"execution_mode": "intraday", "strategy_model": "price_action"}, self.now)
        self.trader = IntradayTrader(self.f.engine)
        self.worker = None

    def tearDown(self):
        if self.worker:
            self.worker.close()
        with self.f.db.connect() as conn:
            self.assertEqual(reconcile(conn), [])
        self.f.close()

    def signal(self, when, action="buy", selected=True, price="1.000", **updates):
        self.f.quote(when, price=price)
        pa = {
            "ready": True,
            "action": action,
            "input_sha256": "same-bars",
            "signal_day": "2026-09-04",
            "as_of": self.f.calendar.previous(when.date()),
            "exit_setup": "看跌吞没",
            "t_allowed": True,
            "raw": {
                "entry": 995000,
                "entry_stop": 950000,
                "entry_ceiling": 1040000,
                "target": 1100000,
                "structural_stop": 940000,
                "exit": 930000,
                "support": 1000000,
                "resistance": 1030000,
                "t_stop": 980000,
            },
            **updates,
        }
        with self.f.db.transaction() as conn:
            version, _ = settings(conn)
            plan = {
                "id": f"live:{version}:{iso(when)}",
                "created_at": iso(when),
                "config_id": version,
                "strategy": STRATEGY,
                "focus_policy": FOCUS_POLICY,
                "as_of": pa["as_of"],
                "targets": {"sh510300": ".2"} if selected else {},
                "missing_quotes": 0,
                "rows": [
                    {
                        "symbol": "sh510300",
                        "pa": pa,
                        "reasons": ["裸 K 测试形态"],
                        "evaluated_quote_at": iso(when),
                        "focus_status": "representative",
                    }
                ],
            }
            set_state(conn, "live_targets", plan)
            set_state(conn, "actions:sh510300", {"at": iso(when)})
        return plan

    def confirmed(self, when=None, **kwargs):
        when = when or self.now
        for t in (when - timedelta(seconds=60), when):
            self.signal(t, **kwargs)
            self.trader.tick(t)

    def test_previous_strategy_revision_cannot_authorize_new_orders(self):
        plan = self.signal(self.now)
        with self.f.db.connect() as conn:
            version, config = settings(conn)
        self.assertEqual(live_problem(plan, version, config, self.f.calendar, self.now), "")
        plan["strategy"] = "price-action-v1"
        self.assertEqual(live_problem(plan, version, config, self.f.calendar, self.now), "等待裸 K 策略目标")

    def test_rr_filter_can_be_disabled_without_bypassing_price_or_exit_conditions(self):
        pa = {
            "ready": True, "entry": 1.0, "entry_stop": 0.95, "target": 1.06,
            "entry_ceiling": 1.025, "setup": "测试形态", "trend": "上升结构",
            "exit": 0.94, "exit_setup": "看跌吞没", "structural_stop": 0.93,
        }
        self.assertEqual(price_decision(pa, 1.0, 1.5)["action"], "hold")
        allowed = price_decision(pa, 1.0, 1.5, False)
        self.assertEqual(allowed["action"], "buy")
        self.assertAlmostEqual(allowed["reward_risk"], 1.2)
        for price, action in [(0.999, "hold"), (1.026, "hold"), (0.94, "exit")]:
            self.assertEqual(price_decision(pa, price, 1.5, False)["action"], action)
        self.assertEqual(price_decision({**pa, "entry": None}, 1.0, 1.5, False)["action"], "hold")
        plan = self.signal(self.now)
        raw_pa = plan["rows"][0]["pa"]
        raw_pa["raw"]["target"] = 1060000
        with self.f.db.connect() as conn:
            quote = latest_quote(conn, self.f.instrument.symbol)[1]
        self.assertTrue(trigger_problem(raw_pa, quote, self.config, "intraday", "BUY"))
        disabled = self.config.model_copy(update={"pa_rr_enabled": False})
        self.assertEqual(trigger_problem(raw_pa, quote, disabled, "intraday", "BUY"), "")
        for ask in (990000, 1050000):
            self.assertTrue(trigger_problem(raw_pa, replace(quote, ask=ask), disabled, "intraday", "BUY"))

    def fill(self, when, price="1.000", volume=3000000):
        self.f.quote(when, price=price, volume=volume)
        self.f.engine.match(when)

    def inventory(self, quantity=19000):
        self.f.buy(quantity=quantity)
        self.fill(at() + timedelta(seconds=60), volume=4000000)
        self.now = at("2026-09-08T10:30:00")

    def test_real_engine_to_quote_to_order_and_fill_preserves_candles(self):
        worker = Worker(self.f.db, self.f.calendar, Mock())
        self.worker = worker
        history = candles()
        worker.ingest("history:sh510300", {"raw": history, "qfq": history}, self.now)
        original = self.f.rows("bars")
        for offset in (0, 60):
            now = self.now + timedelta(seconds=offset)
            self.f.quote(now, price="1.007")
            plan = calculate_targets(self.f.db, "2026-09-04", now)
            self.assertEqual(plan["strategy"], STRATEGY)
            self.assertIn("sh510300", plan["targets"])
            self.assertEqual(plan["rows"][0]["pa"]["setup"], "看涨 Pin Bar")
            self.assertLess(plan["rows"][0]["pa"]["raw"]["entry"], 1007000)
            worker.ingest("live_targets", plan, now)
            self.trader.tick(now)
        self.fill(now + timedelta(seconds=30), price="1.007")
        self.assertTrue(self.f.rows("fills"))
        self.assertEqual(self.f.rows("bars"), original)
        with self.f.db.connect() as conn:
            self.assertGreater(get_state(conn, "pa_position:sh510300")["stop"], 0)

    def test_non_domestic_equity_etfs_can_trigger_price_action_entries(self):
        history = candles()
        for category, region in [
            ("cross_border", "OVERSEAS"),
            ("commodity", "CN"),
            ("bond", "CN"),
            ("money", "CN"),
        ]:
            with self.subTest(category=category, region=region):
                instrument = replace(self.f.instrument, category=category, region=region)
                plan = build_plan(
                    [instrument],
                    {instrument.symbol: history},
                    set(),
                    "2026-09-04",
                    "",
                    self.config,
                    live_prices={instrument.symbol: Decimal(history[-1].close) * Decimal("1.007")},
                )
                self.assertEqual(plan["rows"][0]["pa"]["action"], "buy")
                self.assertIn(instrument.symbol, plan["targets"])

    def test_no_setup_no_buy_and_missing_data_never_liquidates(self):
        self.confirmed(action="hold")
        self.assertEqual(self.f.rows("orders"), [])
        self.inventory(1000)
        self.confirmed(action="hold", selected=False, ready=False)
        self.assertFalse(any(o["side"] == "SELL" for o in self.f.rows("orders")))
        result = build_plan(
            [self.f.instrument],
            {"sh510300": bars()},
            {"sh510300"},
            "2026-09-04",
            "",
            self.config,
            live_prices={"sh510300": Decimal("1")},
        )
        self.assertIn("sh510300", result["targets"])
        self.assertFalse(result["rows"][0]["pa"]["ready"])

    def test_unverified_market_still_analyzes_structure_but_cannot_authorize_buy(self):
        history = candles()
        unknown = replace(self.f.instrument, verified=False, region="unknown")
        result = build_plan(
            [unknown],
            {unknown.symbol: history},
            set(),
            "2026-09-04",
            "",
            self.config,
            live_prices={unknown.symbol: Decimal(history[-1].close) * Decimal("1.007")},
        )
        row = result["rows"][0]
        self.assertTrue(row["pa"]["ready"])
        self.assertEqual(row["pa"]["action"], "buy")
        self.assertFalse(row["eligible"])
        self.assertEqual(result["targets"], {})
        self.assertIn("交易属性待核验，暂不自动买入", row["reasons"])

    def test_quote_reversal_and_execution_price_are_rechecked_before_entry(self):
        self.confirmed()
        self.fill(self.now + timedelta(seconds=30), price=".990")
        self.assertEqual(self.f.rows("orders")[0]["filled"], 0)
        self.fill(self.now + timedelta(seconds=60), price="1.040", volume=4000000)
        self.assertEqual(self.f.rows("orders")[0]["filled"], 0)
        self.assertIn("裸 K", self.f.rows("orders")[0]["blocked_reason"])

    def test_partial_entry_continues_but_refresh_cannot_repeat_same_setup(self):
        self.confirmed()
        self.fill(self.now + timedelta(seconds=30), volume=1010000)
        self.assertEqual(self.f.rows("orders")[0]["filled"], 100)
        self.trader.tick(self.now + timedelta(seconds=31))
        self.assertEqual(self.f.rows("orders")[0]["status"], "partial")
        self.fill(self.now + timedelta(seconds=60), volume=5000000)
        self.confirmed(self.now + timedelta(minutes=10))
        self.assertEqual(len(self.f.rows("orders")), 1)

    def test_structure_exit_persists_through_t_plus_one_without_percentage_stop(self):
        self.confirmed()
        self.fill(self.now + timedelta(seconds=30))
        later = self.now + timedelta(minutes=5)
        self.signal(later, price=".940")
        self.f.engine.risk_check(later)
        risk = self.f.rows("risk_intents")[0]
        self.assertIn("裸 K 结构失效", risk["reason"])
        self.fill(later + timedelta(seconds=30), price=".940", volume=5000000)
        self.assertFalse(any(f["side"] == "SELL" for f in self.f.rows("fills")))
        tomorrow = at("2026-09-08T10:30:00")
        self.signal(tomorrow, action="hold")
        self.f.engine.risk_check(tomorrow)
        self.fill(tomorrow + timedelta(seconds=30), volume=5000000)
        self.assertTrue(any(f["side"] == "SELL" for f in self.f.rows("fills")))

    def test_hold_does_not_rebalance_by_weight_or_trigger_percentage_t(self):
        self.inventory(1000)
        self.confirmed(action="hold", price="1.020")
        self.assertEqual(len(self.f.rows("orders")), 1)
        self.signal(self.now + timedelta(seconds=1), action="hold", price=".920")
        with self.f.db.transaction() as conn:
            plan = get_state(conn, "live_targets")
            plan["rows"][0]["pa"]["raw"].update(structural_stop=850000, exit=840000)
            set_state(conn, "live_targets", plan)
        self.f.engine.risk_check(self.now + timedelta(seconds=1))
        self.assertEqual(self.f.rows("risk_intents"), [])

    def test_t_uses_resistance_and_support_and_actual_sold_quantity(self):
        self.inventory()
        self.confirmed(action="hold", price="1.040")
        sell = self.f.rows("orders")[-1]
        self.assertEqual(sell["kind"], "t_sell")
        self.fill(self.now + timedelta(seconds=30), price="1.040", volume=5000000)
        later = self.now + timedelta(minutes=6)
        self.confirmed(later, action="hold", price="1.010")
        self.assertEqual(len(self.f.rows("orders")), 2)  # 1% fall alone is not the support.
        self.confirmed(later + timedelta(minutes=2), action="hold", price=".997")
        buy = self.f.rows("orders")[-1]
        self.assertEqual((buy["kind"], buy["quantity"]), ("t_buy", sell["filled"] or sell["quantity"]))
        self.fill(later + timedelta(minutes=2, seconds=30), price=".997", volume=5000000)
        self.trader.tick(later + timedelta(minutes=2, seconds=35))
        self.assertEqual(self.f.rows("t_cycles")[0]["status"], "complete")

    def test_stop_ratchets_only_up_and_adjusts_for_corporate_actions(self):
        self.inventory()
        self.signal(self.now, action="hold")
        self.f.engine.risk_check(self.now)
        later = self.now + timedelta(seconds=60)
        plan = self.signal(later, action="hold")
        plan["rows"][0]["pa"]["raw"]["structural_stop"] = 900000
        with self.f.db.transaction() as conn:
            set_state(conn, "live_targets", plan)
        self.f.engine.risk_check(later)
        with self.f.db.transaction() as conn:
            self.assertEqual(get_state(conn, "pa_position:sh510300")["stop"], 940000)
            adjust_pa_reference(conn, "sh510300", dividend=10000, ratio=Decimal("2"))
            self.assertEqual(get_state(conn, "pa_position:sh510300")["stop"], 465000)

    def test_missing_peer_quotes_do_not_disable_valid_holding_protection(self):
        self.inventory()
        plan = self.signal(self.now, action="hold", price=".935")
        plan["missing_quotes"] = 13
        with self.f.db.transaction() as conn:
            set_state(conn, "live_targets", plan)
        self.f.engine.risk_check(self.now)
        self.assertIn("裸 K 结构失效", self.f.rows("risk_intents")[0]["reason"])

    def test_invalidated_or_changed_structure_cannot_fill_pending_entry(self):
        self.confirmed()
        later = self.now + timedelta(seconds=15)
        self.signal(later, input_sha256="changed-bars")
        self.trader.tick(later)
        self.fill(later + timedelta(seconds=30))
        self.assertEqual(self.f.rows("fills"), [])

    def test_peer_signal_changes_do_not_reset_entry_or_block_its_fill(self):
        for i in range(3):
            now = self.now + timedelta(seconds=60 * i)
            plan = self.signal(now)
            plan["rows"].append({
                "symbol": "sz159999", "evaluated_quote_at": iso(now),
                "pa": {"ready": True, "input_sha256": "peer", "action": "buy" if i % 2 else "hold"},
            })
            with self.f.db.transaction() as conn:
                set_state(conn, "live_targets", plan)
            self.trader.tick(now)
            if i == 0:
                self.assertEqual(self.f.rows("orders"), [])
            else:
                self.assertEqual(len(self.f.rows("orders")), 1)
        self.fill(now + timedelta(seconds=30))
        self.assertTrue(self.f.rows("fills"))

    def test_reusing_a_quote_cannot_count_as_a_second_confirmation(self):
        self.signal(self.now)
        self.trader.tick(self.now)
        later = self.now + timedelta(seconds=60)
        plan = self.signal(later)
        plan["rows"][0]["evaluated_quote_at"] = iso(self.now)
        with self.f.db.transaction() as conn:
            set_state(conn, "live_targets", plan)
        self.trader.tick(later)
        self.assertEqual(self.f.rows("orders"), [])
        self.signal(later + timedelta(seconds=1))
        self.trader.tick(later + timedelta(seconds=1))
        self.assertEqual(len(self.f.rows("orders")), 1)

    def test_confirmation_survives_restart_but_resets_after_quote_outage(self):
        self.signal(self.now)
        self.trader.tick(self.now)
        self.trader = IntradayTrader(self.f.engine)
        later = self.now + timedelta(seconds=120)
        self.signal(later)
        self.trader.tick(later)
        self.assertEqual(self.f.rows("orders"), [])
        self.signal(later + timedelta(seconds=60))
        self.trader.tick(later + timedelta(seconds=60))
        self.assertEqual(len(self.f.rows("orders")), 1)
