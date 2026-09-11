import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta

from app.core.types import iso, units
from app.storage.db import Database, get_state, put_instrument, set_state
from app.trading.account import reconcile, snapshot
from app.trading.actions import apply_actions, ingest_actions
from app.trading.engine import Engine
from tests.helpers import Fixture, at


class TradingTests(unittest.TestCase):
    def setUp(self):
        self.f = Fixture()

    def tearDown(self):
        self.f.close()

    def test_buy_uses_next_quote_fee_slippage_and_no_duplicate_after_restart(self):
        self.f.quote(at() - timedelta(seconds=10))
        self.f.order()
        self.f.engine.match(at())
        self.assertEqual(len(self.f.rows("fills")), 0)
        self.f.quote(at() + timedelta(seconds=30), volume=2_000_000)
        self.f.engine.match(at() + timedelta(seconds=30))
        fill = self.f.rows("fills")[0]
        self.assertEqual(fill["price"], units("1.001"))
        self.assertEqual(fill["fee"], units(".10"))
        self.assertEqual(fill["quantity"], 1000)
        Engine(self.f.db, self.f.calendar).match(at() + timedelta(seconds=35))
        self.assertEqual(len(self.f.rows("fills")), 1)
        with self.f.db.connect() as conn:
            self.assertEqual(reconcile(conn), [])
            self.assertEqual(
                snapshot(conn, at())["cash_units"], units("100000") - fill["gross"] - fill["fee"]
            )

    def test_concurrent_matching_exactly_once(self):
        self.f.quote(at() - timedelta(seconds=10))
        self.f.order()
        self.f.quote(at() + timedelta(seconds=30), volume=2_000_000)
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(
                pool.map(
                    lambda _: Engine(self.f.db, self.f.calendar).match(at() + timedelta(seconds=30)), range(2)
                )
            )
        self.assertEqual(len(self.f.rows("fills")), 1)

    def test_partial_fill_volume_and_minimum_commission_per_order(self):
        self.f.db.change_config({"minimum_commission": 5}, at() - timedelta(minutes=1))
        self.f.quote(at() - timedelta(seconds=10))
        self.f.order(quantity=500)
        self.f.quote(at() + timedelta(seconds=30), volume=1_010_000)
        self.f.engine.match(at() + timedelta(seconds=30))
        self.f.quote(at() + timedelta(seconds=60), volume=1_020_000)
        self.f.engine.match(at() + timedelta(seconds=60))
        fills = self.f.rows("fills")
        self.assertEqual([f["quantity"] for f in fills], [100, 100])
        self.assertEqual(sum(f["fee"] for f in fills), units(5))
        self.assertEqual(self.f.rows("orders")[0]["status"], "partial")

    def test_t_plus_one_holiday_and_risk_intent_survives(self):
        start = at("2026-09-24T09:35:00")
        with self.f.db.transaction() as conn:
            set_state(conn, "actions:sh510300", {"at": iso(start)})
        self.f.buy(start)
        self.assertEqual(self.f.rows("lots")[0]["available_day"], "2026-09-28")
        risk_at = start + timedelta(minutes=1)
        self.f.quote(risk_at, price=".940", volume=3_000_000)
        self.f.engine.risk_check(risk_at)
        self.f.quote(risk_at + timedelta(seconds=30), price=".940", volume=4_000_000)
        self.f.engine.match(risk_at + timedelta(seconds=30))
        self.assertEqual(len(self.f.rows("fills")), 1)
        self.f.engine.expire(at("2026-09-24T15:00:00"))
        self.assertEqual(self.f.rows("orders")[-1]["status"], "pending")
        next_day = at("2026-09-28T09:35:00")
        with self.f.db.transaction() as conn:
            set_state(conn, "actions:sh510300", {"at": iso(next_day)})
        self.f.quote(next_day - timedelta(seconds=10), price=".940", volume=1_000_000)
        self.f.quote(next_day + timedelta(seconds=30), price=".940", volume=2_000_000)
        self.f.engine.match(next_day + timedelta(seconds=30))
        self.assertEqual(self.f.rows("fills")[-1]["side"], "SELL")
        self.assertEqual(self.f.rows("lots")[0]["quantity"], 0)
        self.assertTrue(self.f.rows("cooldown"))
        with self.f.db.connect() as conn:
            self.assertEqual(reconcile(conn), [])

    def test_t_plus_zero_etfs_can_buy_and_sell_same_day_without_next_calendar_day(self):
        start = at("2026-12-31T09:35:00")
        for category, region in [
            ("cross_border", "OVERSEAS"),
            ("commodity", "CN"),
            ("bond", "CN"),
            ("money", "CN"),
        ]:
            with self.subTest(category=category):
                f = Fixture()
                self.addCleanup(f.close)
                with f.db.transaction() as conn:
                    put_instrument(
                        conn, replace(f.instrument, category=category, region=region, settlement=0)
                    )
                    set_state(conn, "actions:sh510300", {"at": iso(start)})
                f.buy(start)
                self.assertEqual(len(f.rows("fills")), 1)
                self.assertEqual(f.rows("lots")[0]["available_day"], "2026-12-31")
                f.order("SELL", when=start + timedelta(minutes=1))
                f.quote(start + timedelta(seconds=90), volume=3_000_000)
                f.engine.match(start + timedelta(seconds=90))
                self.assertEqual([row["side"] for row in f.rows("fills")], ["BUY", "SELL"])
                self.assertEqual(f.rows("lots")[0]["quantity"], 0)
                with f.db.connect() as conn:
                    self.assertEqual(reconcile(conn), [])

    def test_stale_unknown_suspended_and_price_limit_fail_closed(self):
        scenarios = [
            {"status": "unknown"},
            {"status": "suspended"},
            {"ask": units("1.1")},
            {"upper": 0},
            {"bid": 0},
        ]
        for index, kwargs in enumerate(scenarios):
            with self.subTest(kwargs=kwargs):
                when = at() + timedelta(minutes=index * 2)
                self.f.quote(when - timedelta(seconds=10), volume=(index + 1) * 1_000_000)
                self.f.order(when=when, key=f"bad:{index}")
                self.f.quote(when + timedelta(seconds=30), volume=(index + 2) * 1_000_000, **kwargs)
                self.f.engine.match(when + timedelta(seconds=30))
                self.assertEqual(len(self.f.rows("fills")), 0)
        self.f.engine.match(at() + timedelta(minutes=15))
        self.assertEqual(len(self.f.rows("fills")), 0)

    def test_source_switch_requires_new_volume_baseline(self):
        self.f.quote(at() - timedelta(seconds=10))
        self.f.order()
        self.f.quote(at() + timedelta(seconds=30), volume=4_000_000, source="other")
        self.f.engine.match(at() + timedelta(seconds=30))
        self.assertEqual(len(self.f.rows("fills")), 0)
        self.f.quote(at() + timedelta(seconds=60), volume=5_000_000, source="other")
        self.f.engine.match(at() + timedelta(seconds=60))
        self.assertEqual(len(self.f.rows("fills")), 1)

    def test_unchanged_or_reset_volume_has_no_liquidity(self):
        self.f.quote(at() - timedelta(seconds=10))
        self.f.order()
        for seconds, volume in ((30, 1_000_000), (60, 50)):
            with self.subTest(volume=volume):
                now = at() + timedelta(seconds=seconds)
                self.f.quote(now, volume=volume)
                self.f.engine.match(now)
                self.assertEqual(self.f.rows("fills"), [])
                self.assertIn("新增成交量", self.f.rows("orders")[0]["blocked_reason"])

    def test_simultaneous_source_switch_cannot_reuse_older_volume(self):
        self.f.quote(at() - timedelta(seconds=10))
        self.f.order(quantity=500)
        self.f.quote(at() + timedelta(seconds=30), volume=2_000_000, source="other")
        self.f.quote(at() + timedelta(seconds=30), volume=2_000_000)
        self.f.engine.match(at() + timedelta(seconds=30))
        self.assertEqual(self.f.rows("fills"), [])
        self.f.quote(at() + timedelta(seconds=60), volume=2_010_000)
        self.f.engine.match(at() + timedelta(seconds=60))
        self.assertEqual(self.f.rows("fills")[0]["quantity"], 100)

    def test_cost_projection_corruption_blocks_further_matching(self):
        self.f.buy()
        with self.f.db.transaction() as conn:
            conn.execute("UPDATE lots SET cost=cost+1")
        with self.f.db.connect() as conn:
            self.assertIn("持仓成本与成交不一致：sh510300", reconcile(conn))
        self.f.order(quantity=500, when=at() + timedelta(seconds=40))
        self.f.quote(at() + timedelta(seconds=60), volume=3_000_000)
        self.f.engine.match(at() + timedelta(seconds=60))
        self.assertEqual(len(self.f.rows("fills")), 1)

    def test_unverified_etf_still_blocked_at_fill(self):
        with self.f.db.transaction() as conn:
            put_instrument(conn, replace(self.f.instrument, category="bond", verified=False))
        self.f.buy()
        self.assertEqual(len(self.f.rows("fills")), 0)

    def test_cash_and_weight_caps(self):
        self.f.buy(quantity=100_000)
        fill = self.f.rows("fills")[0]
        self.assertLessEqual(fill["gross"], units(20000))
        with self.f.db.connect() as conn:
            self.assertGreater(snapshot(conn, at())["cash_units"], 0)

    def test_buy_requires_actions_and_readiness(self):
        with self.f.db.transaction() as conn:
            set_state(conn, "buy_ready", False)
        self.f.buy()
        self.assertEqual(len(self.f.rows("fills")), 0)
        with self.f.db.transaction() as conn:
            set_state(conn, "buy_ready", True)
            set_state(conn, "actions:sh510300", {})
        self.f.quote(at() + timedelta(seconds=60), volume=3_000_000)
        self.f.engine.match(at() + timedelta(seconds=60))
        self.assertEqual(len(self.f.rows("fills")), 0)


    def test_retiring_pause_preserves_account_config_orders_and_control_history(self):
        self.f.buy()
        self.f.order(when=at() + timedelta(minutes=1))
        with self.f.db.transaction() as conn:
            set_state(conn, "paused", True)
            conn.execute("INSERT INTO runs(task,at,status,detail) VALUES('control',?,'ok','pause')", (iso(at()),))
            conn.execute("INSERT INTO risk_intents VALUES('sh510300','historical risk',?)", (iso(at()),))
            conn.execute("UPDATE orders SET status='cancelled' WHERE status='pending'")
        tables = ("configs", "instruments", "cash_ledger", "lots", "orders", "fills", "runs", "risk_intents")
        before = {table: self.f.rows(table) for table in tables}
        Database(self.f.db.path)
        Database(self.f.db.path)
        self.assertEqual({table: self.f.rows(table) for table in tables}, before)
        with self.f.db.connect() as conn:
            self.assertIsNone(get_state(conn, "paused"))
            self.assertNotIn("paused", snapshot(conn, at()))
            self.assertEqual(reconcile(conn), [])

    def test_expiry_and_lunch_no_matching(self):
        self.f.order()
        self.f.engine.match(at("2026-09-07T12:00:00"))
        self.f.engine.expire(at("2026-09-07T15:00:00"))
        self.assertEqual(self.f.rows("orders")[0]["status"], "expired")
        self.assertEqual(len(self.f.rows("fills")), 0)

    def test_immutable_history_and_reconciliation(self):
        self.f.buy()
        with self.assertRaises(Exception):
            with self.f.db.transaction() as conn:
                conn.execute("UPDATE fills SET quantity=999")
        with self.f.db.transaction() as conn:
            conn.execute("UPDATE lots SET quantity=99")
        with self.f.db.connect() as conn:
            self.assertTrue(reconcile(conn))

    def test_initial_cash_locked_after_orders(self):
        self.f.order()
        with self.assertRaises(ValueError):
            self.f.db.change_config({"initial_cash": 200000}, at())

    def test_dividend_entitlement_survives_sale_and_is_idempotent(self):
        self.f.buy()
        action = {
            "id": "div1",
            "symbol": "sh510300",
            "kind": "dividend",
            "record_day": "2026-09-07",
            "ex_day": "2026-09-08",
            "pay_day": "2026-09-09",
            "value": "0.1",
            "verified": True,
            "source": "fixture",
        }
        with self.f.db.transaction() as conn:
            ingest_actions(conn, [action])
            apply_actions(conn, at("2026-09-07T15:01:00"))
            apply_actions(conn, at("2026-09-08T09:00:00"))
        self.assertEqual(self.f.rows("actions")[0]["receivable"], units(100))
        self.assertEqual(self.f.rows("lots")[0]["high"], units(".901"))
        day2 = at("2026-09-08T09:35:00")
        with self.f.db.transaction() as conn:
            set_state(conn, "actions:sh510300", {"at": iso(day2)})
        self.f.quote(day2 - timedelta(seconds=10), price=".930", volume=1_000_000)
        self.f.order(side="SELL", when=day2)
        self.f.quote(day2 + timedelta(seconds=30), price=".930", volume=2_000_000)
        self.f.engine.match(day2 + timedelta(seconds=30))
        self.assertEqual(self.f.rows("lots")[0]["quantity"], 0)
        for _ in range(2):
            with self.f.db.transaction() as conn:
                apply_actions(conn, at("2026-09-09T09:00:00"))
        self.assertEqual(self.f.rows("actions")[0]["status"], "paid")
        self.assertEqual(len([r for r in self.f.rows("cash_ledger") if r["kind"] == "dividend"]), 1)
        with self.f.db.connect() as conn:
            self.assertEqual(reconcile(conn), [])

    def test_split_adjusts_quantity_and_high_without_changing_cost(self):
        self.f.buy()
        cost = self.f.rows("lots")[0]["cost"]
        action = {
            "id": "split1",
            "symbol": "sh510300",
            "kind": "split",
            "record_day": "2026-09-08",
            "ex_day": "2026-09-08",
            "pay_day": "2026-09-08",
            "value": "2",
            "verified": True,
            "source": "fixture",
        }
        for _ in range(2):
            with self.f.db.transaction() as conn:
                ingest_actions(conn, [action])
                apply_actions(conn, at("2026-09-08T09:00:00"))
        lot = self.f.rows("lots")[0]
        self.assertEqual(lot["quantity"], 2000)
        self.assertEqual(lot["cost"], cost)
        self.assertEqual(lot["high"], units(".5005"))
        with self.f.db.connect() as conn:
            self.assertEqual(reconcile(conn), [])

    def test_fractional_split_blocks_instead_of_dropping_shares(self):
        self.f.buy()
        action = {
            "id": "split1",
            "symbol": "sh510300",
            "kind": "split",
            "record_day": "2026-09-08",
            "ex_day": "2026-09-08",
            "pay_day": "2026-09-08",
            "value": "1.3333",
            "verified": True,
            "source": "fixture",
        }
        with self.f.db.transaction() as conn:
            ingest_actions(conn, [action])
            apply_actions(conn, at("2026-09-08T09:00:00"))
        self.assertEqual(self.f.rows("lots")[0]["quantity"], 1000)
        self.assertIn("不足一份", self.f.rows("instruments")[0]["payload"])

    def test_invalid_mark_cannot_trigger_false_stop_or_drawdown(self):
        self.f.buy()
        self.f.quote(at() + timedelta(seconds=60), price="0", status="unknown", volume=3_000_000)
        self.f.engine.risk_check(at() + timedelta(seconds=60))
        self.assertEqual(self.f.rows("risk_intents"), [])
        with self.f.db.connect() as conn:
            self.assertTrue(snapshot(conn, at() + timedelta(seconds=60))["stale"])
            self.assertFalse(get_state(conn, "drawdown_latched", False))


if __name__ == "__main__":
    unittest.main()
