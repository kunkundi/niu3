import os
import json
import unittest
from dataclasses import replace
from datetime import timedelta
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from app.automation.service import Worker
from app.automation.status import automation_status
from app.core.types import iso, units
from app.dashboard.api import create_app
from app.storage.db import Database, get_state, put_instrument, set_state, settings
from app.trading.account import snapshot
from app.trading.engine import Engine
from tests.helpers import Fixture, at, bars


class AutoRunTests(unittest.TestCase):
    def setUp(self):
        self.f = Fixture()
        self.addCleanup(self.f.close)

    def status(self, now, ready=True):
        with self.f.db.connect() as conn:
            return automation_status(
                conn, self.f.calendar, now,
                {"ready": ready, "reasons": [] if ready else ["历史日 K 覆盖率不足"]},
            )

    def heartbeat(self, now):
        with self.f.db.transaction() as conn:
            set_state(conn, "worker_heartbeat", {"at": iso(now)})

    def test_continuous_operation_survives_restart_and_worker_trades_after_open(self):
        self.f.buy()
        overnight = at("2026-09-08T02:00:00")
        self.heartbeat(overnight)
        self.assertEqual(self.status(overnight)["state"], "waiting_session")
        with self.f.db.connect() as conn:
            self.assertFalse(get_state(conn, "paused"))
            self.assertTrue(snapshot(conn, overnight)["stale"])
            self.assertEqual(get_state(conn, "peak_nav"), units(100000))

        db = Database(self.f.db.path)
        worker = Worker(db, self.f.calendar, Mock())
        self.addCleanup(worker.close)
        opening = at("2026-09-08T09:35:00")
        history = bars(end="2026-09-07")
        worker.ingest("history:sh510300", {"raw": history, "qfq": history}, opening)
        with db.transaction() as conn:
            set_state(conn, "profile:sh510300", {"at": iso(opening)})
            set_state(conn, "actions:sh510300", {"at": iso(opening)})
            set_state(conn, "pa_position:sh510300", {"stop": units(".95")})
        worker.tick(opening, network=False)
        self.assertEqual(len(self.f.rows("fills")), 1)
        self.f.quote(opening + timedelta(seconds=10), price=".94")
        worker.tick(opening + timedelta(seconds=10), network=False)
        self.f.quote(opening + timedelta(seconds=30), price=".94", volume=4_000_000)
        worker.tick(opening + timedelta(seconds=30), network=False)
        self.assertEqual(len(self.f.rows("fills")), 2)
        self.assertEqual(self.status(opening + timedelta(seconds=30))["state"], "running")

    def test_high_drawdown_and_legacy_latch_do_not_block_orders_or_reset_statistics(self):
        self.f.buy(quantity=10000)
        with self.f.db.transaction() as conn:
            set_state(conn, "peak_nav", units(200000))
            set_state(conn, "drawdown_latched", True)
        now = at() + timedelta(minutes=1)
        self.f.order(when=now, quantity=1000)
        later = now + timedelta(seconds=30)
        self.f.quote(later, volume=4_000_000)
        self.f.engine.risk_check(later)
        self.f.engine.match(later)
        self.assertEqual(len(self.f.rows("fills")), 2)
        with self.f.db.connect() as conn:
            self.assertFalse(get_state(conn, "paused"))
            self.assertEqual(get_state(conn, "peak_nav"), units(200000))
            self.assertGreater(snapshot(conn, later)["drawdown"], .49)
            self.assertNotIn("drawdown_latched", snapshot(conn, later))

    def test_old_config_and_orders_remain_readable_without_retired_risk_setting(self):
        self.f.order()
        with self.f.db.transaction() as conn:
            version, config = settings(conn)
            legacy = json.dumps({**config.model_dump(mode="json"), "drawdown_stop": "0.10"})
            conn.execute("UPDATE configs SET payload=? WHERE id=?", (legacy, version))
            set_state(conn, "drawdown_latched", True)
        db = Database(self.f.db.path)
        with db.connect() as conn:
            self.assertIsNone(get_state(conn, "drawdown_latched"))
            self.assertNotIn("drawdown_stop", settings(conn)[1].model_dump())
            self.assertEqual(conn.execute("SELECT payload FROM configs WHERE id=?", (version,)).fetchone()[0], legacy)
        self.f.quote(at() - timedelta(seconds=10))
        self.f.quote(at() + timedelta(seconds=30), volume=4_000_000)
        Engine(db, self.f.calendar).match(at() + timedelta(seconds=30))
        self.assertEqual(len(self.f.rows("fills")), 1)

    def test_auto_run_does_not_trade_on_invalid_valuation_or_ledger(self):
        for problem in ("stale", "future", "missing", "zero", "suspended", "unknown", "accounting", "ledger"):
            with self.subTest(problem=problem):
                f = Fixture()
                try:
                    f.buy()
                    now = at() + timedelta(minutes=5)
                    if problem != "stale":
                        f.quote(
                            now + timedelta(seconds=10) if problem == "future" else now,
                            price="0" if problem == "zero" else "1",
                            status=problem if problem in {"suspended", "unknown"} else "trading",
                        )
                    with f.db.transaction() as conn:
                        if problem == "missing":
                            conn.execute("UPDATE quotes SET symbol='unavailable'")
                        elif problem == "accounting":
                            put_instrument(conn, replace(f.instrument, accounting_block="分红待核验"))
                        elif problem == "ledger":
                            conn.execute("UPDATE lots SET quantity=quantity+1")
                    f.order(when=now - timedelta(seconds=1))
                    f.engine.risk_check(now)
                    f.engine.match(now)
                    self.assertEqual(len(f.rows("fills")), 1)
                    with f.db.connect() as conn:
                        self.assertFalse(get_state(conn, "paused"))
                finally:
                    f.close()

    def test_valuation_uses_configured_freshness_for_waiting(self):
        self.f.buy()
        self.f.db.change_config({"quote_max_age": 10}, at())
        now = at() + timedelta(seconds=41)
        self.f.engine.risk_check(now)
        self.heartbeat(now)
        with self.f.db.connect() as conn:
            self.assertTrue(snapshot(conn, now)["stale"])
        self.f.quote(now)
        self.f.engine.risk_check(now)
        with self.f.db.connect() as conn:
            self.assertFalse(snapshot(conn, now)["stale"])

    def test_status_distinguishes_automatic_waits_from_offline(self):
        self.f.buy()
        now = at() + timedelta(minutes=5)
        self.assertEqual(self.status(now)["state"], "offline")
        self.heartbeat(now)
        self.assertEqual(self.status(now, ready=False)["state"], "waiting_data")
        self.assertEqual(self.status(now)["state"], "waiting_valuation")
        self.assertTrue(self.status(now)["enabled"])
        self.f.quote(now)
        self.assertEqual(self.status(now)["state"], "running")

    def test_retired_control_endpoints_cannot_change_state_or_orders(self):
        self.f.buy()
        self.f.order(when=at() + timedelta(minutes=1))
        now = at("2026-09-08T02:00:00")
        self.heartbeat(now)
        with patch.dict(os.environ, {"NIUNO3_ADMIN_PASSWORD": "test-password"}):
            app = create_app(self.f.db, self.f.calendar, clock=lambda: now)
        headers = {"X-NiuNo3-Request": "1"}
        with TestClient(app) as client:
            for authenticated in (False, True):
                if authenticated:
                    client.post("/api/v1/auth/login", json={"password": "test-password"}, headers=headers)
                tables = ("state", "orders", "fills", "lots", "cash_ledger", "configs", "runs")
                before = {table: self.f.rows(table) for table in tables}
                for action in ("pause", "resume", "start", "stop"):
                    with self.subTest(authenticated=authenticated, action=action):
                        response = client.post(f"/api/v1/automation/{action}", headers=headers)
                        self.assertEqual(response.status_code, 404)
                        self.assertEqual({table: self.f.rows(table) for table in tables}, before)
            status = client.get("/api/v1/status").json()
            self.assertNotIn("paused", status)
            self.assertNotIn("paused", client.get("/api/v1/account").json())
            self.assertTrue(status["automation"]["enabled"])
            self.assertEqual(status["automation"]["state"], "waiting_session")
            config = client.get("/api/v1/config").json()
            self.assertNotIn("drawdown_stop", config["values"])
            self.assertNotIn("drawdown_stop", config["labels"])
            rejected = client.patch("/api/v1/config", json={"drawdown_stop": ".1"}, headers=headers)
            self.assertEqual(rejected.status_code, 422)

    def test_legacy_pause_cannot_block_continuous_status_or_matching(self):
        with self.f.db.transaction() as conn:
            set_state(conn, "paused", True)
        self.f.buy()
        self.assertEqual(len(self.f.rows("fills")), 1)
        now = at() + timedelta(seconds=30)
        self.heartbeat(now)
        self.assertTrue(self.status(now)["enabled"])
        self.assertEqual(self.status(now)["state"], "running")
