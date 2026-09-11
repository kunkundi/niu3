import os
import unittest
from dataclasses import replace
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from app.dashboard.api import create_app
from app.market_data.etf_search import local_suggestions, parse_suggestions
from app.market_data.providers import PublicProvider, DataError
from app.storage.db import put_instrument
from tests.helpers import Fixture, at


def row(code="510300", name="沪深300ETF华泰柏瑞", market="1", **overrides):
    return {
        "Classify": "Fund",
        "Code": code,
        "Name": name,
        "MktNum": market,
        "QuoteID": f"{market}.{code}",
        **overrides,
    }


def payload(rows):
    return {"QuotationCodeTable": {"Status": 0, "Data": rows}}


class EtfSearchTests(unittest.TestCase):
    def setUp(self):
        self.f = Fixture()
        self.addCleanup(self.f.close)
        self.a = replace(self.f.instrument, source="eastmoney:fundf10:510300")

    def test_code_name_index_whitespace_and_exact_match_rank(self):
        other = replace(self.a, symbol="sh510301", name="沪深300ETF二号", watched=False)
        matches = local_suggestions({other.symbol: other, self.a.symbol: self.a}, " ＳＨ ５１０３００ ")
        self.assertEqual([r["symbol"] for r in matches], [self.a.symbol])
        for query in ("沪深 300", "5103"):
            rows = local_suggestions({other.symbol: other, self.a.symbol: self.a}, query)
            self.assertEqual(len(rows), 2)
            self.assertEqual([r["watched"] for r in rows], [True, False])
        self.assertEqual(local_suggestions({self.a.symbol: self.a}, " ")[0:], [])

    def test_lookup_is_bounded_and_ignores_inactive_or_unverified_identity(self):
        universe = {f"sh5103{n:02d}": replace(self.a, symbol=f"sh5103{n:02d}") for n in range(30)}
        self.assertEqual(len(local_suggestions(universe, "5103")), 10)
        self.assertEqual(
            local_suggestions(
                {"inactive": replace(self.a, active=False), "unknown": replace(self.a, source="")}, "5103"
            ),
            [],
        )

    def test_remote_filters_non_etfs_and_checks_exchange_identity(self):
        rows = [
            row(),
            row(),
            row("159915", "创业板ETF", "0"),
            row("160123", "沪深300LOF", "0"),
            row("159999", "ETF联接基金", "0"),
            row("600000", "浦发ETF股票", Classify="AStock"),
            row("510301", "跨所ETF", "0"),
            row("510302", "身份错误ETF", QuoteID="0.510302"),
            row("000300", "沪深300", Classify="Index"),
            None,
        ]
        result = parse_suggestions(payload(rows), "ETF")
        self.assertEqual([r["symbol"] for r in result], ["sh510300", "sz159915"])
        self.assertEqual([r["symbol"] for r in parse_suggestions(payload(rows), "sz159")], ["sz159915"])
        self.assertEqual(parse_suggestions(payload([]), "未找到"), [])
        with self.assertRaises(DataError):
            parse_suggestions({"QuotationCodeTable": {"Status": 1, "Data": []}}, "ETF")

    def test_provider_uses_bounded_query_and_cache_without_changing_cached_result(self):
        provider = PublicProvider()
        self.addCleanup(provider.close)
        response = Mock()
        response.json.return_value = payload([row()])
        provider.get = Mock(return_value=response)
        first = provider.suggest_etfs("SH510300")
        first[0]["name"] = "modified"
        second = provider.suggest_etfs(" sh510300 ")
        self.assertEqual(second[0]["name"], "沪深300ETF华泰柏瑞")
        self.assertEqual(provider.get.call_count, 1)
        self.assertEqual(provider.get.call_args.args[1], {"input": "510300", "type": 14, "count": 30})
        self.assertEqual(provider.suggest_etfs(""), [])

    def test_api_reads_archive_without_adding_or_scheduling_any_symbol(self):
        provider = Mock()
        with self.f.db.transaction() as conn:
            put_instrument(conn, replace(self.a, watched=False))
        with patch.dict(os.environ, {"NIUNO3_ADMIN_PASSWORD": "suggestions-test"}):
            client = TestClient(create_app(self.f.db, self.f.calendar, clock=at, etf_provider=provider))
        self.addCleanup(client.close)
        before = {t: self.f.rows(t) for t in ("configs", "instruments", "orders", "runs", "bars")}
        response = client.get("/api/v1/etfs/suggestions", params={"q": "SH510300"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["source"], "local")
        self.assertEqual(response.json()["items"][0]["symbol"], self.a.symbol)
        self.assertFalse(response.json()["items"][0]["watched"])
        provider.suggest_etfs.assert_not_called()
        self.assertEqual(client.get("/api/v1/etfs").json()["total"], 0)
        provider.suggest_etfs.return_value = [{"symbol": "sz159915", "name": "创业板ETF"}]
        self.assertEqual(
            client.get("/api/v1/etfs/suggestions?q=创业板").json()["items"][0]["symbol"], "sz159915"
        )
        provider.suggest_etfs.return_value = [
            {"symbol": "sh510300", "name": "重复名称"},
            {"symbol": "sz159919", "name": "沪深300ETF嘉实"},
        ]
        merged = client.get("/api/v1/etfs/suggestions?q=沪深300").json()["items"]
        self.assertEqual([r["symbol"] for r in merged], ["sh510300", "sz159919"])
        provider.suggest_etfs.side_effect = DataError("temporary failure")
        self.assertEqual(client.get("/api/v1/etfs/suggestions?q=新基金").status_code, 503)
        self.assertEqual(
            client.get("/api/v1/etfs/suggestions?q=沪深300").json()["items"][0]["symbol"], self.a.symbol
        )
        self.assertEqual(client.get("/api/v1/etfs/suggestions?q=").json()["items"], [])
        self.assertEqual(client.get("/api/v1/etfs/suggestions", params={"q": "a" * 61}).status_code, 422)
        for table, rows in before.items():
            self.assertEqual(self.f.rows(table), rows)
