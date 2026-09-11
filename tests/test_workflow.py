import json
import unittest
import zlib
from datetime import timedelta
from unittest.mock import Mock

from app.automation.service import Worker
from app.core.types import iso
from app.storage.db import set_state, dump
from app.storage.maintenance import retain_evidence
from app.trading.account import reconcile
from app.trading.engine import Engine
from tests.helpers import Fixture, at, bars


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.f = Fixture()
        self.worker = Worker(self.f.db, self.f.calendar, Mock())

    def tearDown(self):
        self.worker.close()
        self.f.close()

    def prepare(self, now):
        # Bar prices intentionally differ from live prices: orders must use live execution inputs.
        history = bars()
        self.worker.ingest("history:sh510300", {"raw": history, "qfq": history}, now)
        with self.f.db.transaction() as conn:
            set_state(conn, "profile:sh510300", {"at": iso(now)})
            set_state(conn, "catalog", {"at": iso(now), "total": 1})
            set_state(conn, "actions:sh510300", {"at": iso(now)})
        self.f.quote(now - timedelta(seconds=10))

    def test_two_day_buy_hold_exit_restart_and_frozen_inputs(self):
        now = at()
        self.prepare(now)
        self.worker.tick(now, network=False)
        plans = self.f.rows("plans")
        self.assertEqual(len(plans), 1)
        self.assertEqual(len(self.f.rows("orders")), 1)
        self.f.quote(now + timedelta(seconds=30), volume=4_000_000)
        self.worker.tick(now + timedelta(seconds=30), network=False)
        self.assertEqual(len(self.f.rows("fills")), 1)
        self.assertEqual(self.f.rows("fills")[0]["side"], "BUY")
        self.worker.tick(now + timedelta(seconds=35), network=False)
        self.assertEqual(len(self.f.rows("orders")), 1)
        with self.f.db.connect() as conn:
            inputs = json.loads(
                zlib.decompress(conn.execute("SELECT compressed FROM plan_inputs").fetchone()[0])
            )
            self.assertIn("sh510300", inputs["histories"])
            self.assertEqual(reconcile(conn), [])
        # New process objects use the same durable database, without resetting order or account state.
        self.f.engine = Engine(self.f.db, self.f.calendar)
        day2 = at("2026-09-08T09:35:00")
        with self.f.db.transaction() as conn:
            set_state(conn, "actions:sh510300", {"at": iso(day2)})
        self.f.quote(day2 - timedelta(seconds=10), price=".94", volume=1_000_000)
        self.f.engine.risk_check(day2)
        self.f.quote(day2 + timedelta(seconds=30), price=".94", volume=4_000_000)
        self.f.engine.match(day2 + timedelta(seconds=30))
        self.f.engine.record_equity(day2 + timedelta(seconds=30))
        self.assertEqual([r["side"] for r in self.f.rows("fills")], ["BUY", "SELL"])
        self.assertEqual(self.f.rows("lots")[0]["quantity"], 0)
        with self.f.db.connect() as conn:
            self.assertEqual(reconcile(conn), [])

    def test_missed_window_never_backfills_a_trade(self):
        now = at("2026-09-07T10:01:00")
        self.prepare(now)
        self.worker.tick(now, network=False)
        self.assertEqual(len(self.f.rows("plans")), 1)
        self.assertEqual(self.f.rows("orders"), [])

    def test_opening_data_delay_retries_automatically_after_restart(self):
        now = at()
        self.prepare(now)
        self.worker.tick(now, network=False)
        # Reproduce a fresh opening attempt with the market temporarily below coverage.
        with self.f.db.transaction() as conn:
            conn.execute("DELETE FROM orders")
            conn.execute("DELETE FROM slots")
            set_state(conn, "buy_ready", False)
        plan_id = self.f.rows("plans")[0]["id"]
        self.f.engine.rebalance(plan_id, now)
        self.assertEqual(self.f.rows("orders"), [])
        self.assertEqual(self.f.rows("slots")[0]["status"], "waiting")
        later = now + timedelta(minutes=1)
        self.f.quote(later)
        with self.f.db.transaction() as conn:
            set_state(conn, "buy_ready", True)
        restarted = Engine(self.f.db, self.f.calendar)
        restarted.rebalance(plan_id, later)
        restarted.rebalance(plan_id, later + timedelta(seconds=1))
        self.assertEqual(len(self.f.rows("orders")), 1)
        self.assertEqual(self.f.rows("slots")[0]["status"], "done")

    def test_deferred_buy_does_not_duplicate_sell_or_trade_after_window(self):
        from dataclasses import replace
        from app.storage.db import put_instrument
        from app.strategies.focus import FOCUS_POLICY

        self.f.buy()
        now = at("2026-09-08T09:35:00")
        other = replace(self.f.instrument, symbol="sh510500", index_id="中证500指数")
        with self.f.db.transaction() as conn:
            put_instrument(conn, other)
            set_state(conn, "buy_ready", False)
            cursor = conn.execute(
                "INSERT INTO plans(as_of,execute_day,config_id,at,payload) VALUES(?,?,1,?,?)",
                (
                    "2026-09-07",
                    "2026-09-08",
                    iso(now),
                    dump(
                        {
                            "targets": {other.symbol: "0.2"},
                            "focus_policy": FOCUS_POLICY,
                        }
                    ),
                ),
            )
            plan_id = cursor.lastrowid
        self.f.quote(now)
        self.f.quote(now, symbol=other.symbol)
        self.f.engine.rebalance(plan_id, now)
        self.f.engine.rebalance(plan_id, now + timedelta(seconds=1))
        sells = [o for o in self.f.rows("orders") if o["side"] == "SELL"]
        self.assertEqual(len(sells), 1)
        self.assertEqual(self.f.rows("slots")[0]["status"], "waiting")
        with self.f.db.transaction() as conn:
            set_state(conn, "buy_ready", True)
        self.f.engine.rebalance(plan_id, now.replace(hour=10))
        self.assertEqual(len(self.f.rows("orders")), 2)  # Original buy and one exit only.

    def test_only_one_worker_lease(self):
        other = Worker(self.f.db, self.f.calendar, Mock())
        try:
            self.assertTrue(self.worker.lease(at()))
            self.assertFalse(other.lease(at()))
            self.assertTrue(other.lease(at() + timedelta(seconds=31)))
        finally:
            other.close()

    def test_retention_keeps_fill_evidence_and_latest_baseline(self):
        self.f.quote(at() - timedelta(minutes=2))
        self.f.buy()
        self.f.quote(at() + timedelta(seconds=60), volume=3_000_000)
        self.f.quote(at() + timedelta(seconds=90), volume=4_000_000)
        with self.f.db.transaction() as conn:
            retain_evidence(conn, at("2026-09-21T16:00:00"))
            first_count = conn.execute("SELECT COUNT(*) FROM quotes").fetchone()[0]
            retain_evidence(conn, at("2026-09-21T16:01:00"))
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM quotes").fetchone()[0], first_count)
            self.assertEqual(first_count, 4)
            self.assertEqual(reconcile(conn), [])
            self.assertIsNotNone(
                conn.execute("SELECT q.id FROM fills f JOIN quotes q ON q.id=f.quote_id").fetchone()
            )


if __name__ == "__main__":
    unittest.main()
