"""Price-first downloads and optional enrichment must remain causally independent."""

import time
import unittest
from concurrent.futures import Future
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from unittest.mock import Mock, patch

from app.automation.service import Worker, readiness, stored_history
from app.core.config import Settings
from app.core.types import Bar, iso
from app.market_data.history import enrich_metrics, merge_history, signature, validate_series
from app.market_data.providers import DataError, PublicProvider, SourceCooling
from app.storage.db import get_state, set_state
from app.strategies.price_action import analysis_request, build_plan
from tests.helpers import Fixture, at
from tests.test_price_action import candles


class OptionalHistoryTests(unittest.TestCase):
    def setUp(self):
        self.f = Fixture()
        self.addCleanup(self.f.close)
        self.provider = PublicProvider()
        self.addCleanup(self.provider.close)
        self.worker = Worker(self.f.db, self.f.calendar, self.provider)
        self.addCleanup(self.worker.close)
        self.rows = [replace(b, amount=None, volume=None, source="tencent") for b in candles()]
        self.series = {"raw": self.rows, "qfq": self.rows}

    def test_optional_outage_never_requested_by_price_download(self):
        self.provider.try_primary_history = Mock(return_value=None)
        row = ["2026-09-04", "2", "2.01", "2.02", "1.99", "1000"]
        self.provider.tencent_history_prices = Mock(
            return_value=({k: {row[0]: row} for k in ("raw", "qfq")}, None)
        )
        self.provider.get = Mock(side_effect=SourceCooling("d.10jqka.com.cn", time.monotonic() + 3600))
        result = self.provider.history(self.f.instrument, "2026-09-04")
        self.provider.get.assert_not_called()
        for rows in result.values():
            self.assertEqual((rows[-1].source, rows[-1].close, rows[-1].volume), ("tencent", "2.01", 100000))
            self.assertIsNone(rows[-1].amount)
            self.assertIsNone(rows[-1].turnover_rate)
        self.worker.ingest("history:sh510300", result, at())
        self.assertEqual(len(self.f.rows("bars")), 2)

    def test_primary_missing_optional_columns_keeps_valid_price(self):
        self.provider.get = Mock(
            return_value=Mock(
                json=lambda: {
                    "data": {
                        "code": "510300",
                        "klines": ["2026-09-04,2,2,2.1,1.9,--,--,--"],
                    }
                }
            )
        )
        result = self.provider.eastmoney_history(self.f.instrument, "2026-09-04")
        self.assertEqual(result["raw"][-1].close, "2")
        self.assertIsNone(result["raw"][-1].volume)
        self.assertIsNone(result["raw"][-1].amount)
        validate_series(result, "2026-09-04")

    def test_unconfirmed_current_day_prices_are_still_rejected(self):
        row = ["2026-09-07", "2", "2", "2.1", "1.9", "1000"]
        self.provider.get = Mock(return_value=Mock(json=lambda: {"data": {"sh510300": {"day": [row]}}}))
        with patch("app.market_data.providers.now_cn", return_value=at("2026-09-07T17:00:00")):
            with self.assertRaisesRegex(DataError, "closing snapshot"):
                self.provider.tencent_history_prices(self.f.instrument, "2026-09-07")

    def test_optional_failure_does_not_clear_history_or_readiness(self):
        self.worker.ingest("history:sh510300", self.series, at())
        self.f.quote()
        with self.f.db.transaction() as conn:
            set_state(conn, "profile:sh510300", {"at": iso(at())})
            set_state(conn, "worker_heartbeat", {"at": iso(at())})
        future = Future()
        future.set_exception(SourceCooling("d.10jqka.com.cn", time.monotonic() + 3600))
        self.worker.futures["metrics:sh510300"] = future
        self.worker.collect(at())
        with self.f.db.connect() as conn:
            status = readiness(conn, self.f.calendar, at())
            self.assertTrue(status["ready"], status["reasons"])
            self.assertEqual(status["history_progress"]["completed"], 1)
            self.assertEqual(status["history_progress"]["optional_failed"], 1)
            self.assertIsNone(get_state(conn, "history_source_cooldown"))
            self.assertIsNone(get_state(conn, "error:history:sh510300"))
            self.assertEqual(stored_history(conn, "sh510300"), self.series)
        self.assertEqual(self.worker.history_cooldown_until, 0)
        self.worker.schedule_histories({self.f.instrument.symbol: self.f.instrument}, set(), at())
        self.assertFalse(any(key.startswith("history:") for key in self.worker.futures))

    def test_optional_retry_survives_restart_and_reserves_only_one_job(self):
        self.worker.ingest("history:sh510300", self.series, at())
        with self.f.db.transaction() as conn:
            set_state(
                conn,
                "metrics_request:sh510300",
                {"target": "2026-09-04", "retry_at": iso(at() + timedelta(minutes=5))},
            )
        self.worker.submit = Mock(return_value=True)
        self.worker.schedule_metrics({"sh510300": self.f.instrument}, at())
        self.worker.submit.assert_not_called()
        self.worker.schedule_metrics({"sh510300": self.f.instrument}, at() + timedelta(minutes=5))
        self.worker.submit.assert_called_once()
        self.assertEqual(self.worker.submit.call_args.args[0], "metrics:sh510300")

    def test_enrichment_updates_only_verified_optional_fields(self):
        self.worker.ingest("history:sh510300", self.series, at())
        donors = [
            replace(
                b,
                amount="1200000",
                amount_source="ths",
                volume=100000,
                turnover_rate="0.01",
                turnover_source="ths",
            )
            for b in self.rows
        ]
        supplement = {"raw": donors, "qfq": donors}
        before = self.f.rows("bars")
        self.worker.ingest(
            "metrics:sh510300",
            {"base_signature": signature(self.series), "target": "2026-09-04", "series": supplement},
            at(),
        )
        with self.f.db.connect() as conn:
            enriched = stored_history(conn, "sh510300")
        self.assertEqual(enriched["raw"][-1].amount, "1200000")
        self.assertEqual(enriched["raw"][-1].amount_source, "ths")
        self.assertEqual(enriched["raw"][-1].source, "tencent")
        self.assertEqual([r["fetched_at"] for r in before], [r["fetched_at"] for r in self.f.rows("bars")])
        self.assertEqual(
            analysis_request(self.rows, "2026-09-04", 1000),
            analysis_request(enriched["qfq"], "2026-09-04", 1000),
        )

    def test_wrong_date_price_or_volume_cannot_enrich_history(self):
        base = Bar("2026-09-04", "2", None, "2", "2.1", "1.9", 100000, "tencent")
        for donor in [
            replace(base, day="2026-09-03", amount="100"),
            replace(base, close="2.01", amount="100"),
            replace(base, volume=300000, amount="100"),
            replace(base, amount="-1"),
        ]:
            cached = {"raw": [base], "qfq": [base]}
            self.assertEqual(enrich_metrics(cached, {"raw": [donor]}), cached)

    def test_stale_enrichment_is_rejected_after_new_price_commit(self):
        self.worker.ingest("history:sh510300", self.series, at())
        old_signature = signature(self.series)
        changed = {k: rows + [replace(rows[-1], day="2026-09-07")] for k, rows in self.series.items()}
        self.worker.ingest("history:sh510300", changed, at())
        with self.assertRaisesRegex(DataError, "价格历史已更新"):
            self.worker.ingest(
                "metrics:sh510300",
                {"base_signature": old_signature, "target": "2026-09-04", "series": self.series},
                at(),
            )

    def test_incremental_price_refresh_preserves_optional_values_and_price_source(self):
        old = {
            k: [
                replace(
                    b, amount="1000000", amount_source="ths", volume=100000, source="tencent-price+ths-amount"
                )
                for b in rows
            ]
            for k, rows in self.series.items()
        }
        fresh = {k: rows[-5:] + [replace(rows[-1], day="2026-09-07")] for k, rows in self.series.items()}
        merged = merge_history(old, fresh, "2026-09-07", self.f.calendar)
        self.assertEqual(merged["raw"][-2].amount, "1000000")
        self.assertEqual(merged["raw"][-2].volume, 100000)
        self.assertIsNone(merged["raw"][-1].amount)

    def test_missing_optional_fields_do_not_change_price_action_selection(self):
        config = Settings(strategy_model="price_action", execution_mode="intraday", max_positions=1)
        second = replace(self.f.instrument, symbol="sh510310")
        universe = [self.f.instrument, second]
        history = candles()
        prices = {i.symbol: Decimal(history[-1].close) * Decimal("1.007") for i in universe}
        histories = {i.symbol: [replace(b, amount=None, volume=None) for b in history] for i in universe}
        first = build_plan(universe, histories, set(), "2026-09-04", "", config, live_prices=prices)
        histories[second.symbol] = [replace(b, amount="99999999999", volume=99999) for b in history]
        second_plan = build_plan(universe, histories, set(), "2026-09-04", "", config, live_prices=prices)
        self.assertEqual(first["targets"], second_plan["targets"])
        self.assertEqual(list(first["targets"]), [self.f.instrument.symbol])
        self.assertTrue(first["rows"][0]["eligible"])



if __name__ == "__main__":
    unittest.main()
