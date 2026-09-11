import json
import unittest
from dataclasses import replace
from unittest.mock import Mock

from app.automation.service import Worker, stored_history
from app.core.types import iso
from app.market_data.history import request_window, signature
from app.market_data.providers import DataError, PublicProvider
from app.storage.db import get_state
from tests.helpers import Fixture, at, bars


def series(count=20, end="2026-09-04", source="eastmoney"):
    rows = [
        replace(
            b,
            close="2.000",
            open="2.000",
            high="2.100",
            low="1.900",
            amount="200000",
            volume=100000,
            source=source,
        )
        for b in bars(count=count, end=end)
    ]
    return {"raw": rows[:], "qfq": rows[:]}


def delta(cached, days=("2026-09-07",)):
    return {key: rows[-5:] + [replace(rows[-1], day=d) for d in days] for key, rows in cached.items()}


class IncrementalHistoryTests(unittest.TestCase):
    def setUp(self):
        self.f = Fixture()
        self.provider = PublicProvider()
        self.worker = Worker(self.f.db, self.f.calendar, self.provider)
        self.cached = series()

    def tearDown(self):
        self.worker.close()
        self.f.close()

    def update(self, cached=None, target="2026-09-07", full_day="2026-09-04"):
        return self.provider.update_history(
            self.f.instrument, target, self.cached if cached is None else cached, self.f.calendar, full_day
        )


    def test_history_window_policy_handles_cache_gaps_holidays_and_periodic_refresh(self):
        holiday = series(end="2026-09-24")
        cases = (
            ("same_target", self.cached, "2026-09-04", "2026-09-04", None, "cached", 0),
            ("one_day", self.cached, "2026-09-07", "2026-09-04", delta(self.cached), "incremental", 8),
            ("holiday", holiday, "2026-09-28", "2026-09-24", delta(holiday, ("2026-09-28",)), "incremental", 8),
            ("empty", {}, "2026-09-07", "", series(end="2026-09-07"), "full", 320),
            ("short", series(count=4), "2026-09-07", "", series(end="2026-09-07"), "full", 320),
            ("long_gap", self.cached, "2026-11-30", "2026-09-04", series(end="2026-11-30"), "full", 320),
            ("periodic", self.cached, "2026-09-07", "2026-08-01", series(end="2026-09-07"), "full", 320),
        )
        for name, cached, target, full_day, fresh, mode, limit in cases:
            with self.subTest(case=name):
                self.provider.history = Mock(return_value=fresh)
                result = self.update(cached, target, full_day)
                self.assertEqual((result.mode, result.requested_bars), (mode, limit))
                if mode == "cached":
                    self.provider.history.assert_not_called()
                    self.assertEqual(result.series, cached)
                elif mode == "full":
                    self.provider.history.assert_called_once_with(self.f.instrument, target)
                    self.assertEqual(result.series, fresh)
                else:
                    self.provider.history.assert_called_once_with(
                        self.f.instrument, target, start=cached["raw"][-5].day, limit=8, source="eastmoney"
                    )
                    self.assertEqual(len(result.series["qfq"]), len(cached["qfq"]) + 1)
                    self.assertEqual(result.series["qfq"][:-6], cached["qfq"][:-5])
                    self.assertEqual(result.series["qfq"][-1].day, target)
        self.assertEqual(request_window(self.cached, "2027-01-04", self.f.calendar)[0], "")

    def test_adjustment_change_refreshes_both_full_series(self):
        changed = delta(self.cached)
        changed["qfq"] = [replace(b, close="1.950") for b in changed["qfq"]]
        full = series(end="2026-09-07")
        full["qfq"] = [replace(b, close="1.950") for b in full["qfq"]]
        self.provider.history = Mock(side_effect=[changed, full])
        result = self.update()
        self.assertEqual(result.mode, "full")
        self.assertEqual(result.series, full)
        self.assertIn("复权", result.reason)
        self.assertEqual(self.provider.history.call_count, 2)

    def test_source_switch_and_missing_anchor_require_full_refresh(self):
        changed = delta(self.cached)
        for rows in changed.values():
            rows[:] = [replace(b, source="tencent-price+ths-amount") for b in rows]
        missing = {key: rows[1:] for key, rows in delta(self.cached).items()}
        for fresh in (changed, missing):
            with self.subTest(source=fresh["raw"][0].source, length=len(fresh["raw"])):
                self.provider.history = Mock(side_effect=[fresh, series(end="2026-09-07")])
                self.assertEqual(self.update().mode, "full")
                self.assertEqual(self.provider.history.call_count, 2)


    def test_missing_or_invalid_delta_preserves_cache_without_full_refetch(self):
        duplicate = delta(self.cached)
        duplicate["raw"].append(duplicate["raw"][-1])
        for name, fresh, target in (
            ("missing_latest", delta(self.cached, days=()), "2026-09-08"),
            ("missing_interior", delta(self.cached, days=("2026-09-08",)), "2026-09-08"),
            ("duplicate", duplicate, "2026-09-07"),
        ):
            with self.subTest(case=name):
                original = signature(self.cached)
                self.provider.history = Mock(return_value=fresh)
                with self.assertRaises(ValueError):
                    self.update(target=target)
                self.assertEqual(self.provider.history.call_count, 1)
                self.assertEqual(signature(self.cached), original)

    def test_full_refresh_failure_preserves_database(self):
        self.worker.ingest("history:sh510300", self.cached, at())
        changed = delta(self.cached)
        changed["qfq"][0] = replace(changed["qfq"][0], close="1.950")
        self.provider.history = Mock(side_effect=[changed, DataError("unavailable")])
        before = self.f.rows("bars")
        with self.assertRaises(DataError):
            self.worker.download_history(self.f.instrument, "2026-09-07")
        self.assertEqual(self.f.rows("bars"), before)

    def test_storage_upserts_overlap_preserves_old_evidence_and_bounds_window(self):
        cached = series(count=320)
        self.worker.ingest("history:sh510300", cached, at())
        self.provider.history = Mock(return_value=delta(cached))
        result = self.worker.download_history(self.f.instrument, "2026-09-07")
        self.worker.ingest("history:sh510300", result, at("2026-09-07T16:00:00"))
        with self.f.db.connect() as conn:
            actual = stored_history(conn, "sh510300")
            state = get_state(conn, "history:sh510300")
        self.assertEqual(len(actual["raw"]), 320)
        self.assertEqual(actual["raw"][0].day, cached["raw"][1].day)
        self.assertEqual(
            (state["mode"], state["updated_bars"], state["requested_bars"]), ("incremental", 6, 8)
        )
        self.assertEqual(state["full_refreshed_day"], "2026-09-04")
        old = next(r for r in self.f.rows("bars") if r["day"] == cached["raw"][1].day)
        self.assertEqual(old["fetched_at"], iso(at()))
        with self.assertRaises(DataError):
            self.worker.ingest("history:sh510300", result, at("2026-09-07T16:01:00"))
        # A new worker reads the updated cache and makes no repeat request for that target.
        self.worker.close()
        self.worker = Worker(self.f.db, self.f.calendar, self.provider)
        self.provider.history.reset_mock()
        self.assertEqual(self.worker.download_history(self.f.instrument, "2026-09-07").mode, "cached")
        self.provider.history.assert_not_called()

    def test_both_adjustments_roll_back_together_on_write_failure(self):
        self.worker.ingest("history:sh510300", self.cached, at())
        self.provider.history = Mock(return_value=delta(self.cached))
        result = self.worker.download_history(self.f.instrument, "2026-09-07")
        before = self.f.rows("bars")
        with self.f.db.transaction() as conn:
            conn.execute(
                "CREATE TRIGGER reject_qfq BEFORE INSERT ON bars WHEN NEW.adjustment='qfq' "
                "BEGIN SELECT RAISE(ABORT,'fixture write failure'); END"
            )
        with self.assertRaises(Exception):
            self.worker.ingest("history:sh510300", result, at("2026-09-07T16:00:00"))
        self.assertEqual(self.f.rows("bars"), before)

    def test_eastmoney_incremental_request_has_bounded_dates_and_limit(self):
        self.provider.get = Mock(
            return_value=Mock(
                json=lambda: {
                    "data": {
                        "code": "510300",
                        "klines": [
                            "2026-09-04,2,2,2.1,1.9,1000,200000",
                            "2026-09-07,2,2,2.1,1.9,1000,200000",
                        ],
                    }
                }
            )
        )
        result = self.provider.eastmoney_history(self.f.instrument, "2026-09-07", start="2026-09-04", limit=8)
        self.assertEqual(len(result["raw"]), 2)
        for call in self.provider.get.call_args_list:
            self.assertEqual(
                (call.args[1]["beg"], call.args[1]["end"], call.args[1]["lmt"]), ("20260904", "20260907", 8)
            )

    def test_fallback_incremental_uses_recent_turnover_only_and_rejects_bad_volume(self):
        calls = []
        bad = False

        def get(url, params=None):
            calls.append((url, params))
            if "fqkline" in url:
                key = "qfqday" if params["param"].endswith(",qfq") else "day"
                return Mock(
                    json=lambda: {
                        "data": {
                            "sh510300": {
                                key: [
                                    ["2026-09-04", "2", "2", "2.1", "1.9", "1000"],
                                    ["2026-09-07", "2", "2", "2.1", "1.9", "1000"],
                                    ["2026-09-08", "2", "2", "2.1", "1.9", "1000"],
                                ]
                            }
                        }
                    }
                )
            volume = "200000" if bad else "100000"
            return Mock(
                text="callback("
                + json.dumps(
                    {"data": f"20260904,2,2.1,1.9,2,{volume},200000;20260907,2,2.1,1.9,2,100000,200000"}
                )
                + ");"
            )

        self.provider.get = get
        result = self.provider.fallback_history(self.f.instrument, "2026-09-07", start="2026-09-04", limit=8)
        self.assertEqual(len(calls), 3)
        self.assertTrue(calls[2][0].endswith("/last8.js"))
        self.assertIn(",8,", calls[1][1]["param"])
        self.assertEqual([b.day for b in result["raw"]], ["2026-09-04", "2026-09-07"])
        calls.clear()
        self.provider.fallback_history(self.f.instrument, "2026-09-04")
        self.assertEqual(len(calls), 3)
        self.assertTrue(calls[2][0].endswith("/last320.js"))
        bad = True
        with self.assertRaises(DataError):
            self.provider.fallback_history(self.f.instrument, "2026-09-07", start="2026-09-04", limit=8)

if __name__ == "__main__":
    unittest.main()
