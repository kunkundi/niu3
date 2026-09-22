"""Retired execution cannot return through persisted settings or in-flight evidence."""
import unittest
from datetime import timedelta

from app.core.config import RETIRED_STRATEGY_FIELDS, Settings
from app.core.types import iso
from app.storage.db import Database, dump, get_state, set_state, settings
from app.strategies.price_action import STRATEGY
from app.trading.account import reconcile
from app.trading.intraday import live_problem
from tests.helpers import Fixture, at


class StrategyMigrationTests(unittest.TestCase):
    def setUp(self):
        self.f = Fixture()
        self.addCleanup(self.f.close)

    def test_new_instance_only_accepts_price_action_and_rejects_retired_parameters(self):
        with self.f.db.connect() as conn:
            _, config = settings(conn)
        self.assertEqual((config.strategy_model, config.execution_mode), ("price_action", "intraday"))
        self.assertEqual(config.history_bars, 250)
        self.assertFalse(RETIRED_STRATEGY_FIELDS.intersection(config.model_dump()))
        before = self.f.rows("configs")
        for payload in [{"strategy_model": "momentum"}, {"execution_mode": "daily"},
                        *({key: "0.05"} for key in RETIRED_STRATEGY_FIELDS)]:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                self.f.db.change_config(payload, at())
        self.assertEqual(self.f.rows("configs"), before)
        self.f.db.change_config({"max_positions": 20}, at())
        with self.f.db.connect() as conn:
            self.assertEqual(settings(conn)[1].max_positions, 20)

    def test_legacy_upgrade_preserves_account_and_evidence_and_is_idempotent(self):
        self.f.buy()
        self.f.order(when=at() + timedelta(minutes=1), key="legacy-pending", kind="intraday")
        self.f.order("SELL", when=at() + timedelta(minutes=1), key="legacy-risk", kind="risk")
        with self.f.db.transaction() as conn:
            version, config = settings(conn)
            legacy = dump({**config.model_dump(mode="json"), "strategy_model": "momentum",
                           "execution_mode": "daily", "stop_loss": ".05", "retain_rank": 8,
                           "intraday_t_model": "minute5", "max_weight": ".15"})
            conn.execute("UPDATE configs SET payload=? WHERE id=?", (legacy, version))
            conn.execute("UPDATE orders SET reason='成本止损' WHERE kind='risk'")
            conn.execute("INSERT INTO risk_intents VALUES('sh510300','成本止损',?)", (iso(at()),))
            conn.execute("INSERT INTO cooldown VALUES('sh510300','2026-09-07')")
            conn.execute("INSERT INTO t_cycles(day,symbol,config_id,sell_order_id,status,at,updated_at) "
                         "VALUES('2026-09-07','sh510300',?,2,'selling',?,?)", (version, iso(at()), iso(at())))
            set_state(conn, "live_targets", {"strategy": "trend-momentum-v1", "targets": {"sh510300": ".2"}})
            set_state(conn, "intraday_confirmation", {"count": 2})
            set_state(conn, "notification_settings", {"enabled": False, "channels": {"custom": "preserved"}})
        preserved = ("instruments", "fills", "cash_ledger", "position_ledger", "lots", "plans", "plan_inputs")
        before = {table: self.f.rows(table) for table in preserved}
        original_order = self.f.rows("orders")[0]
        Database(self.f.db.path)
        with self.f.db.connect() as conn:
            current_id, current = settings(conn)
            self.assertEqual(current_id, version + 1)
            self.assertEqual(str(current.max_weight), "0.15")
            self.assertEqual(current.intraday_t_model, "minute5")
            self.assertEqual(conn.execute("SELECT payload FROM configs WHERE id=?", (version,)).fetchone()[0], legacy)
            self.assertIsNone(get_state(conn, "live_targets"))
            self.assertIsNone(get_state(conn, "intraday_confirmation"))
            self.assertFalse(get_state(conn, "buy_ready"))
            self.assertEqual(get_state(conn, "notification_settings")["channels"], {"custom": "preserved"})
            self.assertEqual(reconcile(conn), [])
        self.assertEqual(before, {table: self.f.rows(table) for table in preserved})
        self.assertEqual(self.f.rows("orders")[0], original_order)
        self.assertTrue(all(o["status"] == "cancelled" for o in self.f.rows("orders")[1:]))
        self.assertEqual(self.f.rows("t_cycles")[0]["status"], "abandoned")
        self.assertEqual(self.f.rows("risk_intents"), [])
        state = {t: self.f.rows(t) for t in ("configs", "orders", "state", "runs", "t_cycles")}
        Database(self.f.db.path)
        self.assertEqual(state, {t: self.f.rows(t) for t in state})

    def test_existing_price_action_orders_and_protection_are_untouched(self):
        self.f.buy()
        self.f.order(kind="intraday", key="pa-pending", when=at() + timedelta(minutes=1))
        with self.f.db.transaction() as conn:
            set_state(conn, "pa_position:sh510300", {"stop": 950000, "target": 1100000})
        tables = ("configs", "orders", "fills", "lots", "cash_ledger", "state")
        before = {table: self.f.rows(table) for table in tables}
        Database(self.f.db.path)
        self.assertEqual(before, {table: self.f.rows(table) for table in tables})

    def test_historical_decoder_keeps_frozen_fees_without_restoring_old_strategy(self):
        config = Settings.from_record({"strategy_model": "momentum", "execution_mode": "daily",
                                      "stop_loss": ".05", "commission_rate": ".002"})
        self.assertEqual(str(config.commission_rate), "0.002")
        self.assertEqual(config.strategy_model, "price_action")
        self.assertNotIn("stop_loss", config.model_dump())

    def test_old_signal_cannot_authorize_new_orders(self):
        from app.strategies.focus import FOCUS_POLICY
        with self.f.db.connect() as conn:
            version, config = settings(conn)
        plan = {"config_id": version, "focus_policy": FOCUS_POLICY, "strategy": "trend-momentum-v1"}
        self.assertEqual(live_problem(plan, version, config, self.f.calendar, at()), "等待裸 K 策略目标")
        self.assertNotEqual(plan["strategy"], STRATEGY)
