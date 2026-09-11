import unittest
from dataclasses import replace
from datetime import timedelta

from app.automation.service import readiness
from app.core.types import iso
from app.storage.db import dump, put_instrument, set_state
from tests.helpers import Fixture, at, bars


class HistoryProgressTests(unittest.TestCase):
    def setUp(self):
        self.f = Fixture()
        self.now = at()
        self.symbols = [f"sh{510300 + i}" for i in range(21)]
        with self.f.db.transaction() as conn:
            for symbol in self.symbols:
                put_instrument(conn, replace(self.f.instrument, symbol=symbol))
            set_state(conn, "worker_heartbeat", {"at": iso(self.now)})

    def tearDown(self):
        self.f.close()

    def complete(self, symbol, minutes=0, day="2026-09-04"):
        when = iso(self.now - timedelta(minutes=minutes))
        with self.f.db.transaction() as conn:
            for bar in bars(count=1, end=day):
                conn.execute(
                    "INSERT OR REPLACE INTO bars VALUES(?,?,?,?,?,?)",
                    (symbol, "qfq", bar.day, dump(bar.to_dict()), "fixture", when),
                )
            set_state(conn, f"history:{symbol}", {"at": when, "target": day})

    def progress(self, now=None):
        with self.f.db.connect() as conn:
            return readiness(conn, self.f.calendar, now or self.now)["history_progress"]

    def test_counts_use_current_scope_and_recent_successes_not_attempts(self):
        for i, symbol in enumerate(self.symbols[:19]):
            self.complete(symbol, minutes=1 if i < 2 else 6)
        with self.f.db.transaction() as conn:
            # Failed attempts for the same symbol count once; completed symbols never count as pending failures.
            for symbol in (self.symbols[0], self.symbols[-1]):
                set_state(conn, f"error:history:{symbol}", {"at": iso(self.now), "message": "HTTP 404"})
            put_instrument(conn, replace(self.f.instrument, symbol="sh511010", category="bond"))
            put_instrument(conn, replace(self.f.instrument, symbol="sh510999", active=False))
        p = self.progress()
        self.assertEqual((p["total"], p["completed"], p["pending"]), (22, 19, 3))
        self.assertAlmostEqual(p["coverage"], 19 / 22)
        self.assertEqual((p["required_count"], p["remaining_to_ready"]), (21, 2))
        self.assertEqual((p["recent_completed"], p["failed_count"]), (2, 1))
        self.assertEqual(p["last_success_at"], iso(self.now - timedelta(minutes=1)))
        self.assertEqual(p["state"], "syncing")
        self.assertFalse(p["coverage_met"])

    def test_coverage_threshold_does_not_claim_all_downloads_complete(self):
        for symbol in self.symbols[:20]:
            self.complete(symbol)
        p = self.progress()
        self.assertTrue(p["coverage_met"])
        self.assertEqual((p["pending"], p["remaining_to_ready"]), (1, 0))
        self.assertEqual(p["state"], "syncing")
        self.complete(self.symbols[-1])
        self.assertEqual(self.progress()["state"], "complete")

    def test_stale_data_and_missing_bars_do_not_count_after_target_rollover(self):
        self.complete(self.symbols[0])
        with self.f.db.transaction() as conn:
            set_state(conn, f"history:{self.symbols[1]}", {"at": iso(self.now), "target": "2026-09-04"})
        self.assertEqual(self.progress()["completed"], 1)
        p = self.progress(at("2026-09-07T15:31:00"))
        self.assertEqual(p["target"], "2026-09-07")
        self.assertEqual((p["completed"], p["recent_completed"]), (0, 0))
        self.assertEqual(p["cached_count"], 1)
        self.assertEqual(p["incremental_count"], 0)

    def test_cooldown_retry_and_offline_are_distinct(self):
        with self.f.db.transaction() as conn:
            set_state(
                conn,
                "history_source_cooldown",
                {"until": iso(self.now + timedelta(minutes=3)), "source": "example.test"},
            )
            set_state(conn, f"error:history:{self.symbols[0]}", {"message": "HTTP 429"})
        p = self.progress()
        self.assertEqual(p["state"], "cooling")
        self.assertEqual(p["cooldown_source"], "example.test")
        with self.f.db.transaction() as conn:
            set_state(conn, "history_source_cooldown", {"until": iso(self.now - timedelta(seconds=1))})
        self.assertEqual(self.progress()["state"], "retrying")
        self.assertIsNone(self.progress()["cooldown_until"])
        self.assertEqual(self.progress(self.now + timedelta(seconds=31))["state"], "offline")
        self.complete(self.symbols[1])
        self.assertEqual(self.progress()["state"], "syncing")
        with self.f.db.transaction() as conn:
            set_state(conn, "worker_heartbeat", {"at": iso(self.now + timedelta(seconds=1))})
        self.assertEqual(self.progress()["state"], "syncing")

    def test_empty_scope_and_unknown_calendar_never_claim_success(self):
        p = self.progress(at("2027-01-04T09:35:00"))
        self.assertEqual(p["state"], "waiting_calendar")
        with self.f.db.connect() as conn:
            status = readiness(conn, self.f.calendar, at("2027-01-04T09:35:00"))
        self.assertFalse(status["ready"])
        self.assertIn("交易日历超出已核验范围", status["reasons"])
        self.assertEqual(p["completed"], 0)
        with self.f.db.transaction() as conn:
            conn.execute("DELETE FROM instruments")
        p = self.progress()
        self.assertEqual(p["state"], "preparing")
        self.assertEqual((p["total"], p["coverage"]), (0, 0))
        self.assertFalse(p["coverage_met"])


if __name__ == "__main__":
    unittest.main()
