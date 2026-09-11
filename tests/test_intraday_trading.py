import json
import unittest
from datetime import timedelta

from app.core.types import iso, units
from app.storage.db import get_state, set_state, settings
from app.strategies.focus import FOCUS_POLICY
from app.trading.account import reconcile
from app.trading.engine import Engine
from app.trading.intraday import IntradayTrader
from tests.helpers import Fixture, at


class IntradayTradingTests(unittest.TestCase):
    def setUp(self):
        self.f = Fixture()
        self.f.db.change_config({"execution_mode": "intraday"}, at())
        self.engine = self.f.engine
        self.trader = IntradayTrader(self.engine)
        self.now = at("2026-09-07T10:30:00")

    def tearDown(self):
        with self.f.db.connect() as conn:
            self.assertEqual(reconcile(conn), [])
        self.f.close()

    def signal(self, when, selected=True, price="1.000", **updates):
        self.f.quote(when, price=price)
        with self.f.db.transaction() as conn:
            version, _ = settings(conn)
            plan = {
                "id": f"live:{version}:{iso(when)}",
                "created_at": iso(when),
                "config_id": version,
                "focus_policy": FOCUS_POLICY,
                "as_of": self.f.calendar.previous(when.date()),
                "targets": {"sh510300": "0.2"} if selected else {},
                "missing_quotes": 0,
                "rows": [
                    {
                        "symbol": "sh510300",
                        "score": "0.1",
                        "evaluated_quote_at": iso(when),
                        "focus_status": "representative",
                    }
                ],
                **updates,
            }
            set_state(conn, "live_targets", plan)
            set_state(conn, "actions:sh510300", {"at": iso(when)})
        return plan

    def confirmed(self, now, selected=True, price="1.000"):
        self.signal(now - timedelta(seconds=60), selected, price)
        self.trader.tick(now - timedelta(seconds=60))
        self.signal(now, selected, price)
        self.trader.tick(now)

    def fill(self, when, price="1.000", volume=5_000_000):
        self.f.quote(when, price=price, volume=volume)
        self.engine.match(when)

    def inventory(self):
        start = at("2026-09-07T14:00:00")
        self.f.buy(when=start, quantity=19000)
        self.f.quote(start + timedelta(seconds=60), volume=4_000_000)
        self.engine.match(start + timedelta(seconds=60))
        self.now = at("2026-09-08T10:30:00")

    def test_buys_after_morning_window_only_after_two_distinct_snapshots_and_restart_is_idempotent(self):
        with self.f.db.transaction() as conn:
            set_state(conn, "peak_nav", units(200000))
            set_state(conn, "drawdown_latched", True)
        self.signal(self.now)
        self.trader.tick(self.now)
        self.trader.tick(self.now + timedelta(seconds=1))
        self.assertEqual(self.f.rows("orders"), [])
        later = self.now + timedelta(seconds=60)
        self.signal(later)
        self.trader.tick(later)
        self.assertEqual(len(self.f.rows("orders")), 1)
        self.assertEqual(self.f.rows("orders")[0]["kind"], "intraday")
        IntradayTrader(Engine(self.f.db, self.f.calendar)).tick(later + timedelta(seconds=1))
        self.assertEqual(len(self.f.rows("orders")), 1)
        self.fill(later + timedelta(seconds=30))
        self.assertGreater(self.f.rows("orders")[0]["filled"], 0)
        evidence = self.f.rows("intraday_decisions")
        self.assertEqual(len(evidence), 1)
        self.assertEqual(json.loads(evidence[0]["payload"])["signal"]["targets"], {"sh510300": "0.2"})

    def test_newly_bought_t_plus_one_inventory_waits_and_exits_next_day(self):
        self.confirmed(self.now)
        self.fill(self.now + timedelta(seconds=30))
        later = self.now + timedelta(minutes=10)
        self.confirmed(later, selected=False)
        self.assertFalse(any(o["side"] == "SELL" for o in self.f.rows("orders")))
        next_day = at("2026-09-08T10:30:00")
        self.confirmed(next_day, selected=False)
        sells = [o for o in self.f.rows("orders") if o["side"] == "SELL"]
        self.assertEqual(len(sells), 1)
        self.fill(next_day + timedelta(seconds=30))
        self.assertEqual(sum(lot["quantity"] for lot in self.f.rows("lots")), 0)

    def test_sell_then_buyback_uses_actual_inventory_and_does_not_trigger_immediate_refill(self):
        self.inventory()
        self.confirmed(self.now, price="1.020")
        sell = self.f.rows("orders")[-1]
        self.assertEqual((sell["kind"], sell["side"], sell["quantity"]), ("t_sell", "SELL", 4700))
        self.fill(self.now + timedelta(seconds=30), price="1.020")
        self.trader.tick(self.now + timedelta(seconds=35))
        self.assertEqual(self.f.rows("t_cycles")[0]["status"], "waiting_buy")
        later = self.now + timedelta(minutes=6)
        self.signal(later, price="1.019")
        IntradayTrader(Engine(self.f.db, self.f.calendar)).tick(later)
        self.assertEqual(len(self.f.rows("orders")), 2)  # Original inventory plus T sell.
        later += timedelta(minutes=1)
        self.signal(later, price="1.000")
        self.trader.tick(later)
        buy = self.f.rows("orders")[-1]
        self.assertEqual((buy["kind"], buy["quantity"]), ("t_buy", sell["quantity"]))
        self.fill(later + timedelta(seconds=30))
        self.trader.tick(later + timedelta(seconds=35))
        self.assertEqual(self.f.rows("t_cycles")[0]["status"], "complete")
        lots = self.f.rows("lots")
        self.assertEqual(sum(lot["quantity"] for lot in lots), 19000)
        self.assertEqual(lots[-1]["available_day"], "2026-09-09")

    def test_t_price_reversal_before_fill_does_not_execute_at_an_unwanted_price(self):
        self.inventory()
        self.confirmed(self.now, price="1.020")
        self.fill(self.now + timedelta(seconds=30), price="1.000")
        self.assertEqual(self.f.rows("orders")[-1]["filled"], 0)
        self.assertIn("卖出门槛", self.f.rows("orders")[-1]["blocked_reason"])
        self.fill(self.now + timedelta(seconds=60), price="1.020", volume=6_000_000)
        later = self.now + timedelta(minutes=7)
        self.signal(later)
        self.trader.tick(later)
        self.assertEqual(self.f.rows("orders")[-1]["kind"], "t_buy")
        self.fill(later + timedelta(seconds=30), price="1.020")
        self.assertEqual(self.f.rows("orders")[-1]["filled"], 0)
        self.assertIn("买回门槛", self.f.rows("orders")[-1]["blocked_reason"])

    def test_partial_t_sale_only_buys_back_the_filled_quantity(self):
        self.inventory()
        self.confirmed(self.now, price="1.020")
        self.fill(self.now + timedelta(seconds=30), price="1.020", volume=1_010_000)
        self.assertEqual(self.f.rows("orders")[-1]["filled"], 100)
        self.engine.expire(self.now + timedelta(minutes=5))
        later = self.now + timedelta(minutes=11)
        self.signal(later)
        self.trader.tick(later)
        buy = self.f.rows("orders")[-1]
        self.assertEqual((buy["kind"], buy["quantity"]), ("t_buy", 100))

    def test_expired_partial_buyback_keeps_waiting_and_retries_only_remaining_quantity(self):
        self.inventory()
        self.confirmed(self.now, price="1.020")
        self.fill(self.now + timedelta(seconds=30), price="1.020")
        buy_at = self.now + timedelta(minutes=7)
        self.signal(buy_at)
        self.trader.tick(buy_at)
        self.fill(buy_at + timedelta(seconds=30), volume=1_010_000)
        self.assertEqual(self.f.rows("orders")[-1]["filled"], 100)
        self.engine.expire(buy_at + timedelta(minutes=5))
        wait_at = buy_at + timedelta(minutes=6)
        self.signal(wait_at, price="1.020")
        self.trader.tick(wait_at)
        self.assertEqual(self.f.rows("t_cycles")[0]["status"], "waiting_buy")
        self.assertEqual(len(self.f.rows("orders")), 3)
        retry_at = buy_at + timedelta(minutes=11)
        self.signal(retry_at)
        self.trader.tick(retry_at)
        self.assertEqual(self.f.rows("orders")[-1]["quantity"], 4600)
        self.fill(retry_at + timedelta(seconds=30))
        self.trader.tick(retry_at + timedelta(seconds=35))
        self.assertEqual(self.f.rows("t_cycles")[0]["status"], "complete")
        self.assertEqual(sum(o["filled"] for o in self.f.rows("orders") if o["kind"] == "t_buy"), 4700)

    def test_t_cycle_limit_and_late_session_prevent_new_sell_cycles(self):
        self.f.db.change_config({"intraday_t_cycles": 1}, self.now)
        self.inventory()
        self.confirmed(self.now, price="1.020")
        self.fill(self.now + timedelta(seconds=30), price="1.020")
        buy_at = self.now + timedelta(minutes=7)
        self.signal(buy_at)
        self.trader.tick(buy_at)
        self.fill(buy_at + timedelta(seconds=30))
        self.trader.tick(buy_at + timedelta(seconds=35))
        later = buy_at + timedelta(minutes=7)
        self.signal(later, price="1.020")
        self.trader.tick(later)
        self.assertEqual(len(self.f.rows("t_cycles")), 1)
        self.f.db.change_config({"intraday_t_cycles": 2}, later)
        self.confirmed(at("2026-09-08T14:46:00"), price="1.020")
        self.assertEqual(len(self.f.rows("t_cycles")), 1)

    def test_stale_missing_quotes_and_old_config_never_create_or_fill_orders(self):
        self.confirmed(self.now)
        for delta, updates in [
            (30, {"missing_quotes": 1}),
            (40, {"config_id": 999}),
            (100, {"created_at": iso(self.now)}),
        ]:
            when = self.now + timedelta(seconds=delta)
            self.signal(when, **updates)
            self.trader.tick(when)
            self.fill(when + timedelta(seconds=1))
            self.assertEqual(self.f.rows("fills"), [])
            self.assertEqual(len(self.f.rows("orders")), 1)

    def test_removed_target_cancels_old_buy_and_pending_history_does_not_liquidate(self):
        self.confirmed(self.now)
        later = self.now + timedelta(seconds=60)
        self.signal(later, selected=False)
        self.trader.tick(later)
        self.assertEqual(self.f.rows("orders")[0]["status"], "cancelled")
        self.inventory()
        self.confirmed(self.now, selected=False)
        # Cancel the valid exit to inspect the data-gap behavior separately.
        with self.f.db.transaction() as conn:
            conn.execute("UPDATE orders SET status='cancelled' WHERE status IN ('pending','partial')")
        later = self.now + timedelta(minutes=10)
        for when in (later - timedelta(seconds=60), later):
            self.signal(when, selected=False, rows=[{"symbol": "sh510300", "focus_status": "pending"}])
            self.trader.tick(when)
        self.assertFalse(any(o["status"] == "pending" for o in self.f.rows("orders")))

    def test_risk_exit_cancels_t_and_prevents_buyback(self):
        self.inventory()
        self.confirmed(self.now, price="1.020")
        later = self.now + timedelta(seconds=30)
        self.signal(later, price="0.940")
        self.engine.risk_check(later)
        self.trader.tick(later)
        self.assertEqual(self.f.rows("t_cycles")[0]["status"], "abandoned")
        self.assertTrue(any(o["kind"] == "risk" for o in self.f.rows("orders")))
        self.assertEqual(
            next(o for o in self.f.rows("orders") if o["kind"] == "t_sell")["status"], "cancelled"
        )

    def test_configuration_switch_cancels_dynamic_orders_and_restarts_confirmation(self):
        self.confirmed(self.now)
        self.f.db.change_config({"execution_mode": "daily"}, self.now)
        self.assertEqual(self.f.rows("orders")[0]["status"], "cancelled")
        self.assertEqual(len(self.f.rows("intraday_decisions")), 1)
        with self.f.db.connect() as conn:
            self.assertIsNone(get_state(conn, "intraday_confirmation"))
        self.signal(self.now + timedelta(minutes=1))
        self.trader.tick(self.now + timedelta(minutes=1))
        self.assertEqual(len(self.f.rows("orders")), 1)

    def test_lunch_close_and_daily_mode_never_submit_dynamic_orders(self):
        self.confirmed(self.now)
        self.engine.expire(self.now + timedelta(minutes=5))
        self.assertEqual(self.f.rows("orders")[0]["status"], "expired")
        for when in (at("2026-09-07T12:00:00"), at("2026-09-07T15:00:00")):
            self.signal(when)
            self.trader.tick(when)
        self.assertEqual(len(self.f.rows("orders")), 1)

    def test_order_budget_and_minimum_interval_prevent_repeated_orders(self):
        self.f.db.change_config({"intraday_max_orders": 2}, self.now)
        self.confirmed(self.now)
        self.engine.expire(self.now + timedelta(minutes=5))
        self.signal(self.now + timedelta(minutes=6))
        self.trader.tick(self.now + timedelta(minutes=6))
        self.assertEqual(len(self.f.rows("orders")), 1)
        self.signal(self.now + timedelta(minutes=11))
        self.trader.tick(self.now + timedelta(minutes=11))
        self.assertEqual(len(self.f.rows("orders")), 2)
        self.engine.expire(self.now + timedelta(minutes=16))
        self.signal(self.now + timedelta(minutes=22))
        self.trader.tick(self.now + timedelta(minutes=22))
        self.assertEqual(len(self.f.rows("orders")), 2)


if __name__ == "__main__":
    unittest.main()
