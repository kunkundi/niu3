import json
import unittest
import zlib
from datetime import timedelta
from unittest.mock import Mock

from app.automation.service import Worker
from app.automation.targets import calculate_targets
from app.core.types import iso
from app.storage.db import set_state
from app.storage.maintenance import retain_evidence
from app.trading.account import reconcile
from app.trading.engine import Engine
from tests.helpers import Fixture, at, candles


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.f = Fixture()
        self.worker = Worker(self.f.db, self.f.calendar, Mock())

    def tearDown(self):
        self.worker.close()
        self.f.close()

    def prepare(self, now):
        # Bar prices intentionally differ from live prices: orders must use live execution inputs.
        history = candles()
        self.worker.ingest("history:sh510300", {"raw": history, "qfq": history}, now)
        with self.f.db.transaction() as conn:
            set_state(conn, "profile:sh510300", {"at": iso(now)})
            set_state(conn, "catalog", {"at": iso(now), "total": 1})
            set_state(conn, "actions:sh510300", {"at": iso(now)})
        self.f.quote(now - timedelta(seconds=10), price="1.007")

    def test_two_day_buy_hold_exit_restart_and_frozen_inputs(self):
        now = at()
        self.prepare(now)
        for seconds in (0, 60):
            observed = now + timedelta(seconds=seconds)
            self.f.quote(observed, price="1.007")
            self.worker.ingest("live_targets", calculate_targets(self.f.db, "2026-09-04", observed), observed)
            self.worker.tick(observed, network=False)
        self.assertEqual(len(self.f.rows("plans")), 1)
        self.assertEqual([o["kind"] for o in self.f.rows("orders")], ["intraday"])
        self.f.quote(now + timedelta(seconds=90), price="1.007", volume=4_000_000)
        self.worker.tick(now + timedelta(seconds=90), network=False)
        self.assertEqual(len(self.f.rows("fills")), 1)
        self.assertEqual(self.f.rows("fills")[0]["side"], "BUY")
        self.worker.tick(now + timedelta(seconds=95), network=False)
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

    def test_daily_snapshot_never_trades_without_confirmed_live_signal(self):
        now = at("2026-09-07T10:01:00")
        self.prepare(now)
        self.worker.tick(now, network=False)
        self.assertEqual(len(self.f.rows("plans")), 1)
        self.assertEqual(self.f.rows("orders"), [])

    def test_data_delay_retries_with_fresh_confirmations_after_restart(self):
        from app.trading.intraday import IntradayTrader
        now = at()
        self.prepare(now)
        with self.f.db.transaction() as conn:
            set_state(conn, "buy_ready", False)
        self.worker.ingest("live_targets", calculate_targets(self.f.db, "2026-09-04", now), now)
        self.worker.intraday.tick(now)
        self.assertEqual(self.f.rows("orders"), [])
        restarted = IntradayTrader(Engine(self.f.db, self.f.calendar))
        with self.f.db.transaction() as conn:
            set_state(conn, "buy_ready", True)
        for seconds in (60, 120):
            later = now + timedelta(seconds=seconds)
            self.f.quote(later, price="1.007")
            self.worker.ingest("live_targets", calculate_targets(self.f.db, "2026-09-04", later), later)
            restarted.tick(later)
        restarted.tick(later + timedelta(seconds=1))
        self.assertEqual(len(self.f.rows("orders")), 1)

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
