import json
import unittest
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from unittest.mock import Mock

from app.automation.service import Worker
from app.automation.targets import calculate_targets
from app.core.config import Settings
from app.core.types import iso
from app.storage.db import Database, dump, get_state, latest_quote, set_state, settings
from app.strategies.focus import FOCUS_POLICY
from app.strategies.price_action import STRATEGY, build_plan, price_decision, trigger_problem, select_targets
from app.trading.account import reconcile
from app.trading.actions import adjust_pa_reference
from app.trading.intraday import IntradayTrader, live_problem
from app.trading.engine import Engine
from app.trading.entry_retry import PRICE_CANCEL, INVALIDATED
from tests.helpers import Fixture, at, bars, candles




class PriceActionTests(unittest.TestCase):
    def test_invalidated_entry_explanation_does_not_suppress_hard_exit(self):
        pa = {"ready": True, "entry": None, "trend": "上升结构", "structural_stop": .9,
              "entry_rejections": [{"reason": "旧信号已失效"}, {"reason": "旧信号已失效"}]}
        self.assertEqual(price_decision(pa, 1, 1.5), {"action": "hold", "reason": "旧信号已失效"})
        self.assertEqual(price_decision(pa, .8, 1.5)["action"], "exit")

    def test_full_position_limit_explanation_clears_when_a_slot_becomes_available(self):
        rows = [
            {"symbol": "held", "eligible": False, "reasons": [], "pa": {"action": "hold"}},
            {"symbol": "new", "eligible": True, "reasons": [], "pa": {"action": "buy", "reward_risk": 3}},
        ]
        config = self.config.model_copy(update={"max_positions": 1})
        self.assertNotIn("new", select_targets(rows, {"held"}, config))
        self.assertEqual(rows[1]["reasons"], ["持仓名额已满，等待既有持仓结构退出"])
        self.assertIn("new", select_targets(rows, set(), config))
        self.assertEqual(rows[1]["reasons"], [])

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

    def cancel_unfilled_entry(self):
        self.confirmed()
        when = self.now + timedelta(minutes=1)
        self.signal(when, action="hold", selected=False, price="1.050")
        self.trader.tick(when)
        order = self.f.rows("orders")[-1]
        self.assertEqual((order["status"], order["filled"], order["blocked_reason"]),
                         ("cancelled", 0, PRICE_CANCEL))
        return when

    def test_unfilled_entry_retries_after_new_confirmation_without_fixed_wait_then_fills_once(self):
        cancelled = self.cancel_unfilled_entry()
        original_order = self.f.rows("orders")[0]
        original_decision = self.f.rows("intraday_decisions")[0]
        # Two fresh confirmations can restore a zero-fill intent before five minutes.
        retry_at = cancelled + timedelta(minutes=2)
        self.confirmed(retry_at)
        orders = self.f.rows("orders")
        self.assertEqual(len(orders), 2)
        self.assertEqual(orders[0], original_order)
        self.assertEqual(self.f.rows("intraday_decisions")[0], original_decision)
        evidence = json.loads(self.f.rows("intraday_decisions")[1]["payload"])
        self.assertEqual(evidence["entry_attempt"]["previous_order_id"], orders[0]["id"])
        self.assertEqual(evidence["entry_attempt"]["attempt"], 2)
        self.assertEqual(evidence["entry_attempt"]["intent_key"], orders[0]["key"])
        self.assertNotEqual(orders[0]["key"], orders[1]["key"])
        # A process restart and repeated same-quote ticks cannot duplicate attempts.
        restarted = Database(self.f.db.path)
        IntradayTrader(Engine(restarted, self.f.calendar)).tick(retry_at + timedelta(seconds=1))
        self.assertEqual(len(self.f.rows("orders")), 2)
        self.fill(retry_at + timedelta(seconds=30))
        self.assertGreater(self.f.rows("orders")[1]["filled"], 0)
        self.assertTrue(all(f["order_id"] == orders[1]["id"] for f in self.f.rows("fills")))
        self.confirmed(retry_at + timedelta(minutes=6))
        self.assertEqual(len(self.f.rows("orders")), 2)

    def test_timeout_requires_confirmations_strictly_after_expiration(self):
        self.confirmed()
        expired = self.now + timedelta(minutes=5)
        self.signal(expired)
        self.f.engine.expire(expired)
        self.trader.tick(expired)
        with self.f.db.connect() as conn:
            self.assertEqual(get_state(conn, "intraday_confirmation")["symbols"]["sh510300"]["count"], 0)
        retry = expired + timedelta(seconds=30)
        self.signal(retry)
        self.trader.tick(retry)
        self.trader.tick(retry + timedelta(seconds=10))
        self.assertEqual(len(self.f.rows("orders")), 1)
        self.signal(retry + timedelta(minutes=1))
        self.trader.tick(retry + timedelta(minutes=1))
        self.assertEqual(len(self.f.rows("orders")), 2)
        self.assertEqual(self.f.rows("orders")[0]["status"], "expired")

    def test_cooldown_starts_at_last_actual_fill_and_cancel_does_not_extend_it(self):
        self.confirmed()
        filled_at = self.now + timedelta(seconds=30)
        self.fill(filled_at, volume=1_010_000)  # A real partial fill also starts cooling.
        order = self.f.rows("orders")[0]
        self.assertGreater(order["filled"], 0)
        self.assertLess(order["filled"], order["quantity"])
        cancel_at = filled_at + timedelta(minutes=4)
        with self.f.db.transaction() as conn:
            self.trader._cancel(conn, order["id"], cancel_at, "测试撤销余单")
            _, config = settings(conn)
            self.assertIn("还需 60 秒", self.trader._limit(conn, "sh510300", config, cancel_at))
            self.assertEqual(self.trader._limit(conn, "sh510300", config,
                                               filled_at + timedelta(minutes=5)), "")

    def test_new_quote_waits_for_its_own_evaluation_before_new_order(self):
        self.signal(self.now)
        self.trader.tick(self.now)
        later = self.now + timedelta(seconds=30)
        self.signal(later)
        self.f.quote(later + timedelta(seconds=1), price="1.050")
        self.trader.tick(later + timedelta(seconds=1))
        self.assertEqual(self.f.rows("orders"), [])
        with self.f.db.connect() as conn:
            self.assertIn("新行情已到", str(get_state(conn, "intraday_execution")))
        # The latest observation removes the buy condition; a timer cannot restore it.
        self.signal(later + timedelta(seconds=2), action="hold", selected=False, price="1.050")
        self.trader.tick(later + timedelta(seconds=2))
        self.trader.tick(later + timedelta(minutes=5))
        self.assertEqual(self.f.rows("orders"), [])

    def test_structure_exit_bypasses_recent_fill_cooldown(self):
        with self.f.db.transaction() as conn:
            from app.storage.db import put_instrument

            put_instrument(conn, replace(self.f.instrument, settlement=0))
        self.confirmed()
        self.fill(self.now + timedelta(seconds=30))
        later = self.now + timedelta(seconds=45)
        self.signal(later, action="exit", selected=False, price=".940")
        self.f.engine.risk_check(later)
        self.assertTrue(any(o["side"] == "SELL" and o["kind"] == "risk" for o in self.f.rows("orders")))

    def test_same_timestamp_other_source_requires_its_own_evaluation(self):
        self.signal(self.now)
        self.trader.tick(self.now)
        later = self.now + timedelta(seconds=30)
        plan = self.signal(later)
        with self.f.db.transaction() as conn:
            plan["rows"][0]["evaluated_quote_id"] = latest_quote(conn, "sh510300")[0]
            set_state(conn, "live_targets", plan)
        self.f.quote(later, price="1.050", source="other")
        self.trader.tick(later)
        self.assertEqual(self.f.rows("orders"), [])

    def test_brief_price_excursion_resets_confirmation_even_while_recalculation_is_pending(self):
        self.signal(self.now)
        self.trader.tick(self.now)
        # No new target plan yet: observe the out-of-range quote directly.
        self.f.quote(self.now + timedelta(seconds=10), price="1.050")
        self.trader.tick(self.now + timedelta(seconds=10))
        with self.f.db.connect() as conn:
            self.assertEqual(get_state(conn, "intraday_confirmation")["symbols"]["sh510300"]["count"], 0)
        self.signal(self.now + timedelta(seconds=20))
        self.trader.tick(self.now + timedelta(seconds=20))
        self.assertEqual(self.f.rows("orders"), [])
        self.signal(self.now + timedelta(seconds=30))
        self.trader.tick(self.now + timedelta(seconds=30))
        self.assertEqual(len(self.f.rows("orders")), 1)

    def test_retry_attempts_share_the_existing_daily_order_budget(self):
        self.f.db.change_config({"intraday_max_orders": 2}, self.now - timedelta(minutes=2))
        cancelled = self.cancel_unfilled_entry()
        retry = cancelled + timedelta(minutes=5)
        self.confirmed(retry)
        self.assertEqual(len(self.f.rows("orders")), 2)
        end = retry + timedelta(minutes=1)
        self.signal(end, action="hold", selected=False, price="1.050")
        self.trader.tick(end)
        self.confirmed(end + timedelta(minutes=5))
        self.assertEqual(len(self.f.rows("orders")), 2)
        with self.f.db.connect() as conn:
            self.assertIn("达到本日订单上限", str(get_state(conn, "intraday_execution")))

    def test_retry_checks_executable_ask_before_creating_an_attempt(self):
        cancelled = self.cancel_unfilled_entry()
        retry = cancelled + timedelta(minutes=5)
        for when in (retry - timedelta(minutes=1), retry):
            # Last trade enters the range, but the executable ask is outside it.
            self.f.quote(when, price="1.000", ask=1050000)
            self.signal(when)
            self.trader.tick(when)
        self.assertEqual(len(self.f.rows("orders")), 1)
        self.signal(retry + timedelta(minutes=1))
        self.trader.tick(retry + timedelta(minutes=1))
        self.assertEqual(len(self.f.rows("orders")), 2)

    def test_flat_account_remembers_invalidation_after_price_cancel_across_restart(self):
        cancelled = self.cancel_unfilled_entry()
        broken = cancelled + timedelta(minutes=1)
        # Entry stop is .950; the daily structural/exit levels are lower. Even
        # a hold action with no selected target must invalidate this old entry.
        self.signal(broken, action="hold", selected=False, price=".945")
        self.trader.tick(broken)
        with self.f.db.connect() as conn:
            blocks = get_state(conn, "entry_retry_blocks")["intents"]
            self.assertIn(self.f.rows("orders")[0]["key"], blocks)
        restarted = Database(self.f.db.path)
        self.trader = IntradayTrader(Engine(restarted, self.f.calendar))
        self.confirmed(broken + timedelta(minutes=6))
        self.assertEqual(len(self.f.rows("orders")), 1)
        self.assertEqual(self.f.rows("orders")[0]["blocked_reason"], PRICE_CANCEL)

    def test_entry_stop_cancels_pending_unfilled_entry_without_waiting_for_a_position(self):
        self.confirmed()
        broken = self.now + timedelta(minutes=1)
        self.signal(broken, action="hold", selected=False, price=".945")
        self.trader.tick(broken)
        self.assertEqual(self.f.rows("orders")[0]["blocked_reason"], INVALIDATED)
        self.confirmed(broken + timedelta(minutes=6))
        self.assertEqual(len(self.f.rows("orders")), 1)

    def test_partial_entry_cancellation_does_not_create_another_full_entry(self):
        self.confirmed()
        self.fill(self.now + timedelta(seconds=30), volume=1010000)
        self.assertEqual(self.f.rows("orders")[0]["filled"], 100)
        cancelled = self.now + timedelta(minutes=1)
        self.signal(cancelled, action="hold", selected=False, price="1.050")
        self.trader.tick(cancelled)
        self.assertNotEqual(self.f.rows("orders")[0]["blocked_reason"], PRICE_CANCEL)
        self.confirmed(cancelled + timedelta(minutes=6))
        self.assertEqual(len(self.f.rows("orders")), 1)
        self.assertEqual(sum(r["quantity"] for r in self.f.rows("lots")), 100)

    def test_risk_cancel_is_not_retried_even_if_risk_record_is_later_cleared(self):
        self.confirmed()
        cancelled = self.now + timedelta(minutes=1)
        with self.f.db.transaction() as conn:
            conn.execute("INSERT INTO risk_intents VALUES(?,?,?)", ("sh510300", "结构失效", iso(cancelled)))
        self.signal(cancelled)
        self.trader.tick(cancelled)
        self.assertEqual(self.f.rows("orders")[0]["status"], "cancelled")
        with self.f.db.transaction() as conn:
            conn.execute("DELETE FROM risk_intents")
        self.confirmed(cancelled + timedelta(minutes=6))
        self.assertEqual(len(self.f.rows("orders")), 1)

    def test_changed_structure_cannot_reuse_the_cancelled_entry_intent(self):
        cancelled = self.cancel_unfilled_entry()
        self.confirmed(cancelled + timedelta(minutes=6), input_sha256="revised-history")
        self.assertEqual(len(self.f.rows("orders")), 1)

    def test_upgrade_does_not_rearm_historical_expired_entries(self):
        self.confirmed()
        with self.f.db.transaction() as conn:
            row = conn.execute("SELECT * FROM intraday_decisions").fetchone()
            evidence = json.loads(row["payload"])
            evidence.pop("entry_attempt")
            conn.execute("UPDATE intraday_decisions SET payload=? WHERE order_id=?", (dump(evidence), row["order_id"]))
        self.f.engine.expire(self.now + timedelta(minutes=5))
        self.confirmed(self.now + timedelta(minutes=11))
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
