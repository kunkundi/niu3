import unittest
from concurrent.futures import Future
from dataclasses import replace
from datetime import timedelta
from unittest.mock import Mock

from app.automation.service import Worker
from app.core.types import dt, iso
from app.market_data.providers import DataError
from app.storage.db import get_state, set_state
from tests.helpers import Fixture, at, bars


class HistoryQueueTests(unittest.TestCase):
    def setUp(self):
        self.f = Fixture()
        self.worker = self.make_worker()
        self.universe = {
            f"sh{510300 + i}": replace(self.f.instrument, symbol=f"sh{510300 + i}") for i in range(9)
        }

    def make_worker(self):
        worker = Worker(self.f.db, self.f.calendar, Mock())
        worker.pool.shutdown()
        worker.pool = Mock()
        worker.pool.submit.side_effect = lambda *args: Future()
        return worker

    def tearDown(self):
        self.worker.close()
        self.f.close()

    def fail_running(self, now):
        for future in self.worker.futures.values():
            future.set_exception(DataError("source date incomplete"))
        self.worker.collect(now)

    def test_oldest_attempt_first_prevents_repeated_failures_starving_untouched_codes(self):
        self.worker.schedule_histories(self.universe, set(), at())
        self.assertEqual(list(self.worker.futures), [f"history:sh{510300 + i}" for i in range(3)])
        self.fail_running(at())
        # Even after the first batch's retry delay expires, untouched codes must get a turn.
        self.worker.schedule_histories(self.universe, set(), at() + timedelta(minutes=6))
        self.assertEqual(list(self.worker.futures), [f"history:sh{510300 + i}" for i in range(3, 6)])
        self.fail_running(at() + timedelta(minutes=6))
        self.worker.schedule_histories(self.universe, set(), at() + timedelta(minutes=12))
        self.assertEqual(list(self.worker.futures), [f"history:sh{510300 + i}" for i in range(6, 9)])

    def test_failures_back_off_exponentially_with_cap_and_survive_restart(self):
        universe = {self.f.instrument.symbol: self.f.instrument}
        now = at()
        for expected in (300, 600, 1200, 2400, 3600, 3600):
            self.worker.schedule_histories(universe, set(), now)
            self.assertEqual(len(self.worker.futures), 1)
            self.fail_running(now)
            with self.f.db.connect() as conn:
                request = get_state(conn, "history_request:sh510300")
            self.assertEqual((dt(request["retry_at"]) - now).total_seconds(), expected)
            self.worker.close()
            self.worker = self.make_worker()
            self.worker.schedule_histories(universe, set(), now + timedelta(seconds=expected - 1))
            self.assertEqual(self.worker.futures, {})
            now += timedelta(seconds=expected)

    def test_priority_scope_and_three_job_limit_are_preserved(self):
        with self.f.db.transaction() as conn:
            set_state(conn, "history_request:sh510308", {"at": iso(at()), "target": "2026-09-04"})
        self.universe["sh510301"] = replace(self.universe["sh510301"], active=False)
        self.universe["sh510300"] = replace(self.universe["sh510300"], region="unknown", verified=False)
        self.worker.schedule_histories(self.universe, {"sh510308"}, at())
        self.assertEqual(list(self.worker.futures), ["history:sh510308", "history:sh510302", "history:sh510303"])
        self.assertFalse(self.universe["sh510300"].tradable)
        self.worker.schedule_histories(self.universe, {"sh510308"}, at())
        self.assertEqual(len(self.worker.futures), 3)
        self.assertEqual(self.worker.pool.submit.call_count, 3)
        self.worker.close()
        self.worker = self.make_worker()
        self.worker.schedule_histories({key: self.universe[key] for key in ("sh510300", "sh510301")}, set(), at())
        self.assertEqual(list(self.worker.futures), ["history:sh510300"])

    def test_inflight_restart_waits_and_new_target_resets_backoff(self):
        universe = {self.f.instrument.symbol: self.f.instrument}
        now = at("2026-09-07T15:29:00")
        self.worker.schedule_histories(universe, set(), now)
        self.worker.close()
        self.worker = self.make_worker()
        self.worker.schedule_histories(universe, set(), now + timedelta(seconds=1))
        self.assertEqual(self.worker.futures, {})
        self.worker.schedule_histories(universe, set(), now + timedelta(minutes=2))
        self.assertEqual(len(self.worker.futures), 1)
        with self.f.db.connect() as conn:
            request = get_state(conn, "history_request:sh510300")
            self.assertEqual((request["target"], request["failures"]), ("2026-09-07", 0))

    def test_success_clears_retry_state_and_never_refetches_complete_target(self):
        universe = {self.f.instrument.symbol: self.f.instrument}
        self.worker.schedule_histories(universe, set(), at())
        self.fail_running(at())
        now = at() + timedelta(minutes=6)
        self.worker.schedule_histories(universe, set(), now)
        self.worker.futures["history:sh510300"].set_result({"qfq": bars(), "raw": bars()})
        self.worker.collect(now)
        self.worker.schedule_histories(universe, set(), now)
        self.assertEqual(self.worker.futures, {})
        with self.f.db.connect() as conn:
            self.assertIsNone(get_state(conn, "history_request:sh510300"))
            self.assertIsNone(get_state(conn, "error:history:sh510300"))

    def test_adapter_upgrade_releases_old_backoff_once_and_restart_keeps_new_backoff(self):
        now = at()
        with self.f.db.transaction() as conn:
            set_state(
                conn,
                "history_request:sh510300",
                {
                    "at": iso(now),
                    "target": "2026-09-04",
                    "failures": 6,
                    "retry_at": iso(now + timedelta(hours=1)),
                },
            )
        universe = {self.f.instrument.symbol: self.f.instrument}
        self.worker.schedule_histories(universe, set(), now)
        self.assertEqual(len(self.worker.futures), 1)
        self.fail_running(now)
        with self.f.db.connect() as conn:
            self.assertEqual(get_state(conn, "history_request:sh510300")["failures"], 1)
        self.worker.close()
        self.worker = self.make_worker()
        self.worker.schedule_histories(universe, set(), now + timedelta(seconds=1))
        self.assertEqual(self.worker.futures, {})

    def test_stale_source_result_preserves_history_and_backs_off(self):
        old = {"qfq": bars(end="2026-09-03"), "raw": bars(end="2026-09-03")}
        self.worker.ingest("history:sh510300", old, at())
        self.worker.schedule_histories({self.f.instrument.symbol: self.f.instrument}, set(), at())
        self.worker.futures["history:sh510300"].set_result(old)
        self.worker.collect(at())
        with self.f.db.connect() as conn:
            self.assertEqual(get_state(conn, "history:sh510300")["target"], "2026-09-03")
            self.assertEqual(get_state(conn, "history_request:sh510300")["failures"], 1)
        self.assertEqual(len(self.f.rows("bars")), 280)


if __name__ == "__main__":
    unittest.main()
