import unittest
import time
import httpx
from unittest.mock import Mock

from app.automation.service import Worker, readiness
from app.core.calendar import Calendar
from app.core.types import Bar, units, iso
from app.market_data.providers import (
    DataError,
    PublicProvider,
    normalized_bars,
    parse_actions,
    parse_profile,
    parse_tencent,
)
from app.storage.db import get_state, instruments, set_state
from tests.helpers import Fixture, at


class DataTests(unittest.TestCase):
    def setUp(self):
        self.f = Fixture()

    def tearDown(self):
        self.f.close()

    def test_calendar_holiday_weekend_lunch_and_unknown_year(self):
        calendar = Calendar()
        self.assertFalse(calendar.session(at("2026-09-25T10:00:00")))
        self.assertFalse(calendar.session(at("2026-09-20T10:00:00")))
        self.assertFalse(calendar.session(at("2026-09-07T12:30:00")))
        self.assertFalse(calendar.session(at("2026-09-07T15:00:00")))
        self.assertTrue(calendar.session(at("2026-09-07T13:30:00")))
        self.assertEqual(calendar.previous(at("2026-09-28").date()), "2026-09-24")
        self.assertFalse(calendar.known(at("2027-01-04").date()))
        with self.assertRaises(ValueError):
            calendar.next(at("2026-12-31").date())

    def profile(self, fund_type, index):
        return f"<table><tr><th>基金代码</th><td>510300（主代码）</td><th>基金全称</th><td>测试交易型开放式指数证券投资基金</td></tr><tr><th>基金类型</th><td>{fund_type}</td><th>跟踪标的</th><td>{index}</td></tr></table>"


    def test_profile_classification_uses_verified_type_and_benchmark(self):
        cases = (
            ("指数型-股票", "沪深300指数", "", "equity", "CN", True, "0.10"),
            ("指数型-股票", "某个新指数", "", "equity", "unknown", False, "0.10"),
            ("债券型", "中证国债指数", "", "bond", "CN", True, "0.10"),
            ("指数型-股票", "上证科创板50指数", "", "equity", "CN", True, "0.20"),
            ("指数型-股票", "有色金属", "中证申万有色金属指数", "equity", "CN", True, "0.10"),
            ("指数型-股票", "细分化工", "中证细分化工产业主题指数收益率", "equity", "CN", True, "0.10"),
            ("指数型-股票", "科创板50", "上证科创板50成份指数收益率", "equity", "CN", True, "0.20"),
            ("指数型-股票", "港股通", "中证港股通指数收益率", "cross_border", "OVERSEAS", True, "0.10"),
            *(("指数型-股票", index, "", "cross_border", "OVERSEAS", True, "0.10") for index in (
                "中证港股通指数", "中证沪港深500指数", "中证沪深港科技龙头指数", "国证深港通精选指数"
            )),
        )
        for fund_type, index, benchmark, category, region, tradable, limit in cases:
            with self.subTest(index=index):
                html = self.profile(fund_type, index).replace(
                    "</table>", f"<tr><th>业绩比较基准</th><td>{benchmark}</td></tr></table>"
                )
                result = parse_profile(self.f.instrument, html, at())
                self.assertEqual((result.category, result.region, result.tradable), (category, region, tradable))
                self.assertEqual(result.limit_ratio, limit)
                self.assertEqual(result.index_id, benchmark.removesuffix("收益率") if benchmark else index)

    def test_profile_rejects_wrong_identity_and_unrelated_or_composite_benchmarks(self):
        with self.assertRaises(DataError):
            parse_profile(self.f.instrument, self.profile("指数型-股票", "沪深300指数").replace("510300", "159915"), at())
        for benchmark in ("沪深300指数", "中证细分化工指数*95%+存款利率*5%", "中证细分化工指数+沪深300指数"):
            html = self.profile("指数型-股票", "细分化工").replace(
                "</table>", f"<tr><th>业绩比较基准</th><td>{benchmark}</td></tr></table>"
            )
            parsed = parse_profile(self.f.instrument, html, at())
            self.assertEqual(parsed.index_id, "细分化工")
            self.assertFalse(parsed.tradable)

    def test_hang_seng_a_share_index_is_domestic_t1(self):
        for fund_type, index, category, region, settlement in (
            ("指数型-股票", "恒生A股电网设备指数", "equity", "CN", 1),
            ("指数型-股票", "恒生 A 股专精特新50指数", "equity", "CN", 1),
            ("指数型-股票", "恒生指数", "cross_border", "OVERSEAS", 0),
            ("指数型-股票", "恒生中国企业指数", "cross_border", "OVERSEAS", 0),
            ("QDII-股票", "恒生A股电网设备指数", "cross_border", "OVERSEAS", 0),
        ):
            with self.subTest(index=index, fund_type=fund_type):
                result = parse_profile(self.f.instrument, self.profile(fund_type, index), at())
                self.assertEqual((result.category, result.region, result.settlement),
                                 (category, region, settlement))
                self.assertTrue(result.verified)

    def test_tencent_units_precision_timestamp_and_identity(self):
        parts = ["0"] * 60
        for key, value in {
            2: "510300",
            3: "1.234",
            4: "1.200",
            5: "1.210",
            6: "1234",
            9: "1.233",
            19: "1.235",
            30: "20260907093530",
            33: "1.250",
            34: "1.190",
            37: "234.56",
            47: "1.320",
            48: "1.080",
        }.items():
            parts[key] = value
        text = 'v_sh510300="' + "~".join(parts) + '";'
        quote = parse_tencent(text, {"sh510300": self.f.instrument}, at())[0]
        self.assertEqual(quote.volume, 123400)
        self.assertEqual(quote.amount, units("2345600"))
        self.assertEqual(quote.last, units("1.234"))
        self.assertEqual(quote.open, units("1.210"))
        self.assertEqual((quote.high, quote.low), (units("1.250"), units("1.190")))
        self.assertEqual(quote.status, "trading")
        self.assertEqual(parse_tencent(text, {}, at()), [])

    def test_eastmoney_scales_price_without_stock_rounding(self):
        raw = {
            "f2": 1.234,
            "f18": 1.20,
            "f15": 1.25,
            "f16": 1.19,
            "f17": 1.21,
            "f31": 1.233,
            "f32": 1.235,
            "f5": 1234,
            "f6": 500000,
            "f124": int(at().timestamp()),
        }
        quote = PublicProvider.market_quote(self.f.instrument, raw, at())
        self.assertEqual(quote.last, units("1.234"))
        self.assertEqual(quote.upper, units("1.320"))
        self.assertEqual(quote.open, units("1.210"))
        self.assertEqual((quote.high, quote.low), (units("1.250"), units("1.190")))
        self.assertEqual(quote.volume, 123400)

    def test_verified_non_equity_profiles_have_executable_quote_boundaries(self):
        raw = {
            "f2": 1.234,
            "f18": 1.20,
            "f31": 1.233,
            "f32": 1.235,
            "f5": 1234,
            "f6": 500000,
            "f124": int(at().timestamp()),
        }
        for fund_type, index in [
            ("QDII", "纳斯达克100指数"),
            ("商品型", "黄金现货价格"),
            ("债券型", "中证国债指数"),
            ("货币型", "活期存款利率"),
        ]:
            with self.subTest(fund_type=fund_type):
                instrument = parse_profile(self.f.instrument, self.profile(fund_type, index), at())
                quote = PublicProvider.market_quote(instrument, raw, at())
                self.assertTrue(instrument.tradable)
                self.assertEqual(instrument.settlement, 0)
                self.assertEqual(quote.status, "trading")
                self.assertEqual((quote.upper, quote.lower), (units("1.320"), units("1.080")))

    def test_history_rejects_duplicate_nonfinite_and_future_only(self):
        bar = Bar("2026-09-04", "1.00", "100")
        self.assertEqual(normalized_bars([bar, Bar("2026-09-07", "2", "100")], "2026-09-04"), [bar])
        for values in ([bar, bar], [Bar("2026-09-04", "NaN", "100")], [Bar("2026-09-07", "1", "100")]):
            with self.assertRaises((DataError, ValueError)):
                normalized_bars(values, "2026-09-04")

    def test_dividend_parser_requires_dates_and_cash_unit(self):
        html = "<table><tr><th>年份</th><th>权益登记日</th><th>除息日</th><th>每10份分红</th><th>分红发放日</th></tr><tr><td>2026年</td><td>2026-09-07</td><td>2026-09-08</td><td>每10份派现金1.2300元</td><td>2026-09-09</td></tr></table>"
        action = parse_actions("sh510300", html)[0]
        self.assertEqual(action["value"], "0.1230")
        self.assertTrue(action["verified"])
        self.assertFalse(parse_actions("sh510300", html.replace("2026-09-09", "--"))[0]["verified"])
        self.assertFalse(parse_actions("sh510300", html.replace("2026-09-09", "2026-09-01"))[0]["verified"])
        self.assertFalse(parse_actions("sh510300", html.replace("2026-09-09", "2026-99-99"))[0]["verified"])
        with self.assertRaises(DataError):
            parse_actions("sh510300", "<html>upstream maintenance</html>")

    def test_obsolete_catalog_response_cannot_repopulate_watchlist(self):
        provider = Mock()
        worker = Worker(self.f.db, self.f.calendar, provider)
        with self.f.db.transaction() as conn:
            set_state(conn, "catalog", {"at": iso(at()), "total": 1000})
        worker.ingest("market", {"sh510500": {"f14": "旧目录 ETF"}}, at())
        self.assertNotIn("sh510500", {row["symbol"] for row in self.f.rows("instruments")})
        with self.f.db.connect() as conn:
            self.assertEqual(get_state(conn, "catalog")["total"], 1000)
            self.assertIn("sh510300", instruments(conn))
        worker.close()

    def test_fallback_history_joins_reported_amount_and_preserves_adjustment(self):
        provider = PublicProvider()

        def get(url, params=None):
            if "fqkline" in url:
                qfq = params["param"].endswith(",qfq")
                key = "qfqday" if qfq else "day"
                close = "1.234" if qfq else "2.468"
                return Mock(
                    json=lambda: {
                        "data": {
                            "sh510300": {
                                key: [
                                    ["2026-09-04", close, close, close, close, "1000"],
                                ]
                            }
                        }
                    }
                )
            body = '{"data":"20260904,2,2,2,2,100000,253467.80"}'
            return Mock(text="callback(" + body + ");")

        provider.get = get
        try:
            result = provider.fallback_history(self.f.instrument, "2026-09-04")
            self.assertEqual(result["qfq"][0].close, "1.234")
            self.assertEqual(result["raw"][0].close, "2.468")
            self.assertEqual(result["raw"][0].volume, 100000)
            self.assertEqual(result["qfq"][0].amount, "253467.80")
            self.assertEqual(result["raw"][0].source, "tencent-price+ths-amount")
            with self.assertRaises(DataError):
                provider.fallback_history(self.f.instrument, "2026-09-07")
        finally:
            provider.close()

    def test_source_block_stops_repeated_requests_and_cools_scheduler(self):
        from concurrent.futures import Future
        from app.market_data.providers import SourceCooling

        provider = PublicProvider()
        request = httpx.Request("GET", "https://example.test/history")
        response = httpx.Response(501, request=request)
        provider.client.get = Mock(return_value=response)
        try:
            with self.assertRaises(SourceCooling) as first:
                provider.get(str(request.url))
            with self.assertRaises(SourceCooling):
                provider.get(str(request.url))
            self.assertEqual(provider.client.get.call_count, 1)
            worker = Worker(self.f.db, self.f.calendar, provider)
            future = Future()
            future.set_exception(first.exception)
            worker.futures["history:sh510300"] = future
            worker.collect(at())
            self.assertGreater(worker.history_cooldown_until, time.monotonic())
            with self.f.db.connect() as conn:
                self.assertEqual(get_state(conn, "history_source_cooldown")["source"], "example.test")
            worker.close()
        finally:
            provider.close()

    def test_held_history_must_be_ready_even_if_overall_coverage_passes(self):
        self.f.buy()
        with self.f.db.connect() as conn:
            status = readiness(conn, self.f.calendar, at())
            self.assertIn("持仓数据／核算未就绪：sh510300", status["reasons"])


if __name__ == "__main__":
    unittest.main()
