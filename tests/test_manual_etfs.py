"""Manual membership, safe migration and background collection without an automatic universe."""

import os
import unittest
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from app.automation.service import Worker, readiness
from app.core.config import Settings, REMOVED_POOL_FIELDS
from app.core.types import Instrument, iso
from app.dashboard.api import create_app
from app.market_data.providers import DataError, PublicProvider, parse_profile
from app.storage.db import (
    Database,
    get_state,
    instruments,
    tracked_instruments,
    put_instrument,
    set_state,
    settings,
)
from app.storage.focus import focus_snapshot
from app.strategies.focus import select_focus
from app.strategies.price_action import build_plan
from app.trading.account import reconcile
from tests.helpers import Fixture, at, bars, candles


class ManualPoolTests(unittest.TestCase):
    def setUp(self):
        self.f = Fixture()
        self.addCleanup(self.f.close)
        self.a = self.f.instrument
        self.b = replace(self.a, symbol="sh510310", name="同方向 ETF")

    def test_after_hours_addition_can_receive_real_closing_quote(self):
        provider = PublicProvider()
        self.addCleanup(provider.close)
        quote = self.f.quote(at("2026-09-07T15:00:00"))
        for source in ("eastmoney", "tencent", "sina", "ths"):
            setattr(provider, source + "_quotes", Mock(return_value=[replace(quote, source=source)]))
        results = provider.quotes({self.a.symbol: self.a}, at("2026-09-07T17:00:00"))
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].source, "eastmoney")
        self.assertFalse(results[0].fresh(at("2026-09-07T17:00:00")))

    def test_membership_ignores_every_legacy_filter_and_same_group_ranking(self):
        config = Settings(
            minimum_amount="999999999999", minimum_turnover="1", focus_categories=[], focus_markets=[]
        )
        history = {self.a.symbol: [replace(b, amount="100") for b in candles()], self.b.symbol: [replace(b, amount="10") for b in candles()]}
        plan = build_plan([self.a, self.b], history, set(), "2026-09-04", "2026-09-07", config, live_prices={s: Decimal(h[-1].close)*Decimal("1.007") for s,h in history.items()})
        self.assertEqual(set(plan["targets"]), {self.a.symbol, self.b.symbol})
        self.assertTrue(all(r["representative"] for r in plan["rows"]))
        self.assertTrue(all(r["turnover20"] is None for r in plan["rows"]))
        self.assertEqual([Decimal(r["amount20"]) for r in plan["rows"]], [100, 10])


    def test_manual_membership_is_independent_of_data_readiness(self):
        cases = (
            (replace(self.b, verified=False, active=False), {}, set()),
            (self.b, {self.a.symbol: bars(count=10)}, set()),
            (self.b, {self.a.symbol: candles()}, {self.a.symbol}),
            (replace(self.b, watched=False), {self.a.symbol: candles(), self.b.symbol: candles()}, {self.a.symbol}),
        )
        for other, history, targets in cases:
            with self.subTest(other=other, history=list(history)):
                rows = select_focus([self.a, other], history, "2026-09-04")
                self.assertTrue(rows[self.a.symbol]["representative"])
                self.assertEqual(rows[other.symbol]["representative"], other.watched)
                plan = build_plan([self.a, other], history, set(), "2026-09-04", "2026-09-07", Settings(), live_prices={s: Decimal(h[-1].close)*Decimal("1.007") for s,h in history.items()})
                self.assertEqual(set(plan["targets"]), targets)
                self.assertTrue(all(row["reasons"] for row in plan["rows"] if row["symbol"] not in targets))

    def test_migration_is_idempotent_preserves_holdings_and_stops_old_orders(self):
        self.f.buy()
        self.f.order(when=at() + timedelta(minutes=1), key="old-pending")
        before = {t: self.f.rows(t) for t in ("fills", "cash_ledger", "lots", "position_ledger")}
        with self.f.db.transaction() as conn:
            put_instrument(conn, self.b)
            conn.execute("DELETE FROM state WHERE key='etf_pool_mode'")
            conn.execute("UPDATE instruments SET payload=json_remove(payload,'$.watched')")
        migrated = Database(self.f.db.path)
        Database(self.f.db.path)
        for table, rows in before.items():
            self.assertEqual(self.f.rows(table), rows)
        with migrated.connect() as conn:
            self.assertFalse(any(i.watched for i in instruments(conn).values()))
            self.assertEqual(set(tracked_instruments(conn)), {self.a.symbol})
            self.assertEqual(focus_snapshot(conn, "2026-09-04")["representative_count"], 0)
            self.assertEqual(settings(conn)[0], 2)
            self.assertFalse(get_state(conn, "buy_ready"))
            self.assertEqual(reconcile(conn), [])
        self.assertEqual(self.f.rows("orders")[-1]["status"], "cancelled")
        self.assertTrue(migrated.add_etf(self.b, at()))
        Database(self.f.db.path)
        with migrated.connect() as conn:
            self.assertTrue(instruments(conn)[self.b.symbol].watched)

    def test_remove_requires_positions_and_pending_orders_to_be_resolved(self):
        self.f.order()
        with self.assertRaisesRegex(ValueError, "待成交"):
            self.f.db.remove_etf(self.a.symbol, at())
        self.assertEqual(len(self.f.rows("configs")), 1)
        with self.f.db.transaction() as conn:
            conn.execute("UPDATE orders SET status='cancelled'")
        self.f.buy(when=at() + timedelta(minutes=2))
        with self.assertRaisesRegex(ValueError, "持仓"):
            self.f.db.remove_etf(self.a.symbol, at())
        self.assertEqual(len(self.f.rows("configs")), 1)

    def test_scheduler_collects_only_manual_and_held_etfs(self):
        with self.f.db.transaction() as conn:
            put_instrument(conn, replace(self.b, watched=False))
        worker = Worker(self.f.db, self.f.calendar, Mock())
        self.addCleanup(worker.close)
        worker.submit = Mock(return_value=False)
        worker.schedule_histories = Mock()
        worker.schedule(at())
        self.assertNotIn("market", [call.args[0] for call in worker.submit.call_args_list])
        quote_call = next(call for call in worker.submit.call_args_list if call.args[0] == "fallback")
        self.assertEqual(set(quote_call.args[2]), {self.a.symbol})
        self.assertEqual(set(worker.schedule_histories.call_args.args[0]), {self.a.symbol})
        self.assertNotIn("profile:" + self.b.symbol, [call.args[0] for call in worker.submit.call_args_list])

    def test_removed_symbol_inflight_profile_and_quotes_cannot_restore_membership(self):
        worker = Worker(self.f.db, self.f.calendar, Mock())
        self.addCleanup(worker.close)
        quote = self.f.quote()
        self.f.db.remove_etf(self.a.symbol, at())
        worker.ingest("profile:" + self.a.symbol, self.a, at())
        worker.ingest("history:" + self.a.symbol, {"raw": bars(), "qfq": bars()}, at())
        worker.ingest("quotes", [replace(quote, at=iso(at() + timedelta(seconds=10)))], at())
        with self.f.db.connect() as conn:
            self.assertFalse(instruments(conn)[self.a.symbol].watched)
        self.assertEqual(self.f.rows("bars"), [])
        self.assertEqual(len(self.f.rows("quotes")), 1)

    def test_unverified_profile_retries_after_five_minutes_and_survives_restart(self):
        now = at()
        unknown = replace(self.a, region="unknown", verified=False)
        with self.f.db.transaction() as conn:
            put_instrument(conn, unknown)
            set_state(conn, "profile:" + self.a.symbol, {"at": iso(now)})
        for elapsed, expected in [(299, False), (300, True)]:
            worker = Worker(self.f.db, self.f.calendar, Mock())
            worker.submit = Mock(return_value=False)
            worker.schedule_histories = Mock()
            worker.schedule(now + timedelta(seconds=elapsed))
            keys = [call.args[0] for call in worker.submit.call_args_list]
            self.assertEqual("profile:" + self.a.symbol in keys, expected)
            worker.close()

    def test_history_progress_includes_unverified_manual_etf(self):
        unknown = replace(self.a, region="unknown", verified=False)
        with self.f.db.transaction() as conn:
            put_instrument(conn, unknown)
            set_state(conn, "profile:" + self.a.symbol, {"at": iso(at())})
            set_state(conn, "worker_heartbeat", {"at": iso(at())})
        worker = Worker(self.f.db, self.f.calendar, Mock())
        self.addCleanup(worker.close)
        worker.ingest("history:" + self.a.symbol, {"raw": bars(), "qfq": bars()}, at())
        with self.f.db.connect() as conn:
            status = readiness(conn, self.f.calendar, at())
        self.assertEqual(status["history_progress"]["total"], 1)
        self.assertEqual(status["history_progress"]["completed"], 1)
        self.assertEqual(status["tradable_count"], 0)
        self.assertIn("ETF 交易属性待核验", status["reasons"])

    def test_readiness_ignores_old_market_coverage_and_explains_empty_pool(self):
        with self.f.db.transaction() as conn:
            put_instrument(conn, replace(self.b, watched=False))
            set_state(conn, "profile:" + self.a.symbol, {"at": iso(at())})
            set_state(conn, "catalog", {"at": "2000-01-01T00:00:00+08:00", "total": 1500})
            set_state(conn, "worker_heartbeat", {"at": iso(at())})
        self.f.quote()
        worker = Worker(self.f.db, self.f.calendar, Mock())
        self.addCleanup(worker.close)
        worker.ingest("history:" + self.a.symbol, {"raw": bars(), "qfq": bars()}, at())
        with self.f.db.connect() as conn:
            status = readiness(conn, self.f.calendar, at())
            self.assertTrue(status["ready"], status["reasons"])
            self.assertEqual(status["history_progress"]["total"], 1)
        self.f.db.remove_etf(self.a.symbol, at())
        with self.f.db.connect() as conn:
            status = readiness(conn, self.f.calendar, at())
            self.assertEqual(status["catalog_count"], 0)
            self.assertIn("尚未手动添加 ETF", status["reasons"])


class ManualEtfApiTests(unittest.TestCase):
    def setUp(self):
        self.f = Fixture()
        self.provider = Mock()
        self.provider.profile.side_effect = lambda instrument, now: replace(
            instrument,
            name="新添加 ETF",
            category="equity",
            region="CN",
            index_id="沪深300",
            verified=True,
            source="eastmoney:fundf10:" + instrument.symbol[2:],
            updated_at=iso(now),
        )
        with patch.dict(os.environ, {"NIUNO3_ADMIN_PASSWORD": "manual-list-test"}):
            self.client = TestClient(
                create_app(self.f.db, self.f.calendar, clock=at, etf_provider=self.provider)
            )
        self.headers = {"X-NiuNo3-Request": "1"}

    def tearDown(self):
        self.client.close()
        self.f.close()

    def login(self):
        self.assertEqual(
            self.client.post(
                "/api/v1/auth/login", json={"password": "manual-list-test"}, headers=self.headers
            ).status_code,
            200,
        )


    def test_invalid_code_or_source_failure_never_changes_pool(self):
        self.login()
        for code in ("600000", "000001", "sz510300", "sh000001", "510300;"):
            with self.subTest(code=code):
                response = self.client.post("/api/v1/etfs", json={"code": code}, headers=self.headers)
                self.assertEqual(response.status_code, 422)
        self.provider.profile.assert_not_called()
        for message, status in (
            ("profile is not an ETF", 422),
            ("fund profile identity mismatch", 422),
            ("source down", 503),
        ):
            with self.subTest(message=message):
                self.provider.profile.side_effect = DataError(message)
                response = self.client.post("/api/v1/etfs", json={"code": "510500"}, headers=self.headers)
                self.assertEqual(response.status_code, status)
        self.assertEqual(self.client.get("/api/v1/etfs").json()["total"], 1)
        self.assertEqual(len(self.f.rows("configs")), 1)

    def test_authenticated_add_duplicate_remove_restart_and_cached_metadata(self):
        self.login()
        payload = {"code": "SH510500"}
        result = self.client.post("/api/v1/etfs", json=payload, headers=self.headers)
        self.assertEqual(result.status_code, 200)
        self.assertTrue(result.json()["added"])
        self.assertEqual(self.client.get("/api/v1/etfs").json()["total"], 2)
        self.assertIsNone(self.client.get("/api/v1/etfs/sh510500").json()["quote"])
        self.assertFalse(self.client.post("/api/v1/etfs", json=payload, headers=self.headers).json()["added"])
        self.assertEqual(len(self.f.rows("configs")), 2)
        self.assertEqual(self.provider.profile.call_count, 1)
        self.assertTrue(self.client.delete("/api/v1/etfs/sh510500", headers=self.headers).json()["removed"])
        self.assertEqual(self.client.get("/api/v1/etfs/sh510500").status_code, 404)
        for query in ("focus_only=false", "focus_only=true", "q=510500"):
            listed = self.client.get("/api/v1/etfs?" + query).json()["items"]
            self.assertNotIn("sh510500", [item["symbol"] for item in listed])
        Database(self.f.db.path)
        self.assertEqual(self.client.get("/api/v1/etfs").json()["total"], 1)
        self.assertTrue(self.client.post("/api/v1/etfs", json=payload, headers=self.headers).json()["added"])
        self.assertEqual(self.provider.profile.call_count, 1)
        self.assertEqual([r["task"] for r in self.f.rows("runs")], ["watchlist"] * 3)

    def test_deleted_filters_not_exposed_and_cannot_change_config(self):
        self.login()
        config = self.client.get("/api/v1/config").json()
        self.assertFalse(REMOVED_POOL_FIELDS.intersection(config["values"]))
        self.assertNotIn("focus_category_options", config)
        for field in REMOVED_POOL_FIELDS:
            self.assertEqual(
                self.client.patch("/api/v1/config", json={field: 0}, headers=self.headers).status_code, 422
            )
        self.assertEqual(len(self.f.rows("configs")), 1)

    def test_adding_cached_unverified_etf_refreshes_its_profile(self):
        self.login()
        unknown = replace(
            self.f.instrument,
            symbol="sh516120",
            region="unknown",
            verified=False,
            watched=False,
            source="eastmoney:fundf10:516120",
        )
        with self.f.db.transaction() as conn:
            put_instrument(conn, unknown)
        response = self.client.post("/api/v1/etfs", json={"code": "516120"}, headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.provider.profile.assert_called_once()
        with self.f.db.connect() as conn:
            self.assertTrue(instruments(conn)[unknown.symbol].tradable)

    def test_etf_list_detail_and_status_include_all_verified_types_and_markets(self):
        variants = [("cross_border", "OVERSEAS"), ("commodity", "CN"), ("bond", "CN"), ("money", "CN")]
        with self.f.db.transaction() as conn:
            for n, (category, region) in enumerate(variants):
                put_instrument(
                    conn,
                    replace(
                        self.f.instrument,
                        symbol=f"sh51100{n}",
                        category=category,
                        region=region,
                        settlement=0,
                    ),
                )
        result = self.client.get("/api/v1/etfs?tradable=true").json()
        self.assertEqual(result["total"], 5)
        self.assertTrue(all(row["tradable"] for row in result["items"]))
        for row in result["items"]:
            self.assertTrue(self.client.get(f"/api/v1/etfs/{row['symbol']}").json()["tradable"])
        with self.f.db.connect() as conn:
            status = readiness(conn, self.f.calendar, at())
        self.assertEqual(status["watched_tradable_count"], 5)
        self.assertEqual(status["history_progress"]["total"], 5)

    def test_profile_rejects_etf_feeder_and_uses_verified_fund_name(self):
        html = "<table><tr><th>基金代码</th><td>510300</td><th>基金全称</th><td>沪深300交易型开放式指数证券投资基金</td></tr><tr><th>基金简称</th><td>沪深300ETF</td><th>基金类型</th><td>股票型</td></tr><tr><th>跟踪标的</th><td>沪深300指数</td></tr></table>"
        parsed = parse_profile(Instrument("sh510300", "SH510300"), html, at())
        self.assertEqual(parsed.name, "沪深300ETF")
        with self.assertRaisesRegex(DataError, "not an ETF"):
            parse_profile(Instrument("sh510300", "SH510300"), html.replace("交易型开放式", "ETF联接"), at())
