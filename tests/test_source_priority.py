"""Source failover must preserve identity, freshness, units and adjustment provenance."""

import json
import unittest
import httpx
from dataclasses import replace
from unittest.mock import Mock, patch

from app.core.calendar import Calendar
from app.core.types import Bar, Instrument, Quote, iso, units
from app.market_data.providers import DataError, PublicProvider, SourceCooling, parse_sina
from app.market_data.intraday import IntradayService, eastmoney_minutes, sina_minutes
from app.market_data.index_chart import IndexChartService
from tests.helpers import at
from tests.test_intraday import active_payload


def quote():
    return Quote(
        "sh510300",
        iso(at()),
        iso(at()),
        units("2"),
        units("1.9"),
        units("2"),
        units("2.001"),
        100000,
        units("200000"),
        units("2.09"),
        units("1.71"),
        "trading",
        "test",
    )


def sina_text(symbol="sh510300", day="2026-09-07", stamp="09:35:00"):
    parts = ["0"] * 33
    for index, value in {
        0: "沪深300ETF",
        1: "2",
        2: "1.9",
        3: "2.001",
        4: "2.1",
        5: "1.9",
        6: "2.000",
        7: "2.002",
        8: "123450",
        9: "245678.90",
        30: day,
        31: stamp,
        32: "00",
    }.items():
        parts[index] = value
    return f'var hq_str_{symbol}="' + ",".join(parts) + '";'


def em_minutes(code="510300", market=1, day="2026-09-07"):
    return {
        "data": {
            "code": code,
            "market": market,
            "preClose": 1.9,
            "trends": [f"{day} 09:30,2,2.001,2.1,1.9,1000,200000,2"],
        }
    }


class SourcePriorityTests(unittest.TestCase):
    def setUp(self):
        self.provider = PublicProvider()
        self.addCleanup(self.provider.close)
        self.instrument = Instrument(
            "sh510300", "沪深300ETF", category="equity", region="CN", verified=True, index_id="沪深300"
        )


    def test_http_failure_retry_and_host_cooling_policy(self):
        for status, requests, cooled in ((404, 1, False), (501, 1, True), (502, 2, True)):
            with self.subTest(status=status):
                url = f"https://status-{status}.example.test/quotes"
                request = httpx.Request("GET", url)
                self.provider.client.get = Mock(return_value=httpx.Response(status, request=request))
                with patch("app.market_data.providers.time.sleep"):
                    with self.assertRaises(DataError):
                        self.provider.get(url)
                    self.assertEqual(self.provider.client.get.call_count, requests)
                    if cooled:
                        with self.assertRaises(SourceCooling):
                            self.provider.get(url)
                        self.assertEqual(self.provider.client.get.call_count, requests)

    def test_quotes_only_send_missing_symbols_to_lower_priority_sources(self):
        other = replace(self.instrument, symbol="sh510500")
        first = replace(quote(), source="eastmoney")
        second = replace(first, symbol=other.symbol, source="tencent")
        self.provider.eastmoney_quotes = Mock(return_value=[first])
        self.provider.tencent_quotes = Mock(return_value=[second])
        self.provider.sina_quotes = Mock()
        self.provider.ths_quotes = Mock()
        result = self.provider.quotes({self.instrument.symbol: self.instrument, other.symbol: other}, at())
        self.assertEqual([r.source for r in result], ["eastmoney", "tencent"])
        self.assertEqual(set(self.provider.tencent_quotes.call_args.args[0]), {other.symbol})
        self.provider.sina_quotes.assert_not_called()
        self.provider.ths_quotes.assert_not_called()


    def test_stale_missing_book_and_failed_quotes_fall_back_in_priority_order(self):
        for primary in (
            [replace(quote(), at="2026-09-04T15:00:00+08:00")],
            [replace(quote(), bid=0, status="unknown")],
            DataError("primary offline"),
        ):
            with self.subTest(primary=primary):
                self.provider.eastmoney_quotes = Mock()
                if isinstance(primary, Exception):
                    self.provider.eastmoney_quotes.side_effect = primary
                else:
                    self.provider.eastmoney_quotes.return_value = primary
                self.provider.tencent_quotes = Mock(side_effect=DataError("secondary offline"))
                self.provider.sina_quotes = Mock(return_value=[replace(quote(), source="sina")])
                self.provider.ths_quotes = Mock()
                result = self.provider.quotes({self.instrument.symbol: self.instrument}, at())
                self.assertEqual([q.source for q in result], ["sina"])
                self.provider.eastmoney_quotes.assert_called_once()
                self.provider.tencent_quotes.assert_called_once()
                self.provider.sina_quotes.assert_called_once()
                self.provider.ths_quotes.assert_not_called()

    def test_sina_quote_units_identity_and_status(self):
        result = parse_sina(sina_text(), {self.instrument.symbol: self.instrument}, at())
        self.assertEqual(
            (result[0].last, result[0].volume, result[0].amount), (units("2.001"), 123450, units("245678.90"))
        )
        self.assertEqual(result[0].status, "trading")
        self.assertEqual((result[0].high, result[0].low), (units("2.1"), units("1.9")))
        self.assertEqual(
            parse_sina(sina_text("sz000001"), {self.instrument.symbol: self.instrument}, at()), []
        )
        self.assertEqual(
            parse_sina(sina_text(day="2026-99-99"), {self.instrument.symbol: self.instrument}, at()), []
        )

    def test_eastmoney_batch_retains_exchange_identity_and_volume_units(self):
        self.provider.get = Mock(
            return_value=Mock(
                json=lambda: {
                    "data": {
                        "diff": [
                            {
                                "f12": "510300",
                                "f13": 1,
                                "f2": 2.001,
                                "f18": 1.9,
                                "f31": 2,
                                "f32": 2.002,
                                "f5": 1234,
                                "f6": 245678.9,
                                "f124": int(at().timestamp()),
                            },
                            {"f12": "510500", "f13": 0},
                        ]
                    }
                }
            )
        )
        result = self.provider.eastmoney_quotes({self.instrument.symbol: self.instrument}, at())
        self.assertEqual([(q.symbol, q.volume) for q in result], [("sh510300", 123400)])
        self.assertEqual(self.provider.get.call_args.args[1]["secids"], "1.510300")

    def test_cached_fallback_source_does_not_override_recovered_primary(self):
        expected = {"raw": [Bar("2026-09-04", "2", "200000")], "qfq": [Bar("2026-09-04", "2", "200000")]}
        self.provider.eastmoney_history = Mock(return_value=expected)
        self.provider.fallback_history = Mock(side_effect=AssertionError("primary was ignored"))
        self.assertEqual(
            self.provider.history(self.instrument, "2026-09-04", source="tencent-price+ths-amount"), expected
        )

    def test_history_tencent_failure_uses_sina_raw_and_ths_adjusted_without_relabelling(self):
        calls = []

        def get(url, params=None):
            calls.append(url)
            if "eastmoney" in url or "gtimg.cn" in url:
                raise DataError("offline")
            if "sina.cn" in url:
                return Mock(
                    json=lambda: [
                        {
                            "day": "2026-09-04",
                            "open": "2",
                            "close": "2",
                            "high": "2",
                            "low": "2",
                            "volume": "100000",
                        }
                    ]
                )
            if "/00/" in url:
                raise AssertionError("Sina raw price must take precedence")
            return Mock(
                text="callback(" + json.dumps({"data": "20260904,1.8,1.8,1.8,1.8,100000,200000,1.25"}) + ")"
            )

        self.provider.get = get
        result = self.provider.history(self.instrument, "2026-09-04")
        self.assertEqual(
            ["eastmoney" in calls[0], "gtimg.cn" in calls[1], "sina.cn" in calls[2], "10jqka" in calls[3]],
            [True] * 4,
        )
        self.assertEqual(result["raw"][-1].close, "2")
        self.assertEqual(result["qfq"][-1].close, "1.8")
        self.assertEqual(result["raw"][-1].source, "sina-raw+ths-qfq")
        self.assertIsNone(result["qfq"][-1].turnover_rate)
        self.assertIsNone(result["qfq"][-1].amount)
        self.assertEqual(len(calls), 4)  # No optional turnover request in the price path.

    def test_all_earlier_history_price_sources_unavailable_uses_ths_both_adjustments(self):
        self.provider.tencent_history_prices = Mock(side_effect=DataError("offline"))
        self.provider.sina_daily_rows = Mock(side_effect=DataError("offline"))
        calls = []

        def get(url, params=None):
            calls.append(url)
            price = "2" if "/00/" in url else "1.8"
            return Mock(
                text="callback("
                + json.dumps({"data": f"20260904,{price},{price},{price},{price},100000,200000,1.25"})
                + ")"
            )

        self.provider.get = get
        result = self.provider.fallback_history(self.instrument, "2026-09-04")
        self.assertEqual(result["raw"][-1].source, "ths")
        self.assertEqual((result["raw"][-1].close, result["qfq"][-1].close), ("2", "1.8"))
        self.assertTrue(any("/00/" in url for url in calls))

    def test_eastmoney_minutes_win_and_exchange_mismatch_is_rejected(self):
        self.provider.get = Mock(return_value=Mock(json=lambda: em_minutes()))
        service = IntradayService(Calendar(), self.provider)
        result = service.fetch(self.instrument.symbol, at())
        self.assertEqual(result["source"], "eastmoney")
        self.provider.get.assert_called_once()
        with self.assertRaises(DataError):
            eastmoney_minutes(em_minutes(market=0), self.instrument.symbol, at())

    def test_minute_errors_reach_sina_before_ths_and_preserve_previous_close(self):
        rows = [{"day": "2026-09-07 09:30:00", "close": "2.001"}]
        self.provider.get = Mock(
            side_effect=[
                DataError("eastmoney offline"),
                DataError("tencent offline"),
                Mock(json=lambda: rows),
                Mock(content=sina_text().encode("gb18030")),
            ]
        )
        result = IntradayService(Calendar(), self.provider).fetch(self.instrument.symbol, at())
        self.assertEqual((result["source"], result["previous_close"]), ("sina", "1.9"))
        self.assertFalse(any("10jqka" in c.args[0] for c in self.provider.get.call_args_list))
        with self.assertRaises(DataError):
            sina_minutes(rows, sina_text(day="2026-09-04"), self.instrument.symbol, at())

    def test_old_eastmoney_minutes_fall_back_to_current_tencent(self):
        self.provider.get = Mock(
            side_effect=[
                Mock(json=lambda: em_minutes(day="2026-09-04")),
                Mock(json=lambda: active_payload(rows=["0930 2.001"])),
            ]
        )
        result = IntradayService(Calendar(), self.provider).fetch(self.instrument.symbol, at())
        self.assertEqual(result["source"], "tencent")
        self.assertEqual(self.provider.get.call_count, 2)

    def test_index_fallback_to_sina_retains_index_and_converts_shares_to_lots(self):
        self.provider.get = Mock(
            side_effect=[
                DataError("eastmoney offline"),
                DataError("tencent offline"),
                Mock(
                    json=lambda: [
                        {
                            "day": "2026-09-04",
                            "open": "3000",
                            "close": "3010",
                            "high": "3020",
                            "low": "2990",
                            "volume": "100000000",
                        }
                    ]
                ),
            ]
        )
        result = IndexChartService(self.provider).get("2026-09-04", at())
        self.assertEqual(
            (result["symbol"], result["source"], result["stale"]), ("sh000001", "新浪财经", False)
        )
        self.assertEqual(result["bars"][-1]["volume"], 1000000)
        self.assertFalse(any("10jqka" in c.args[0] for c in self.provider.get.call_args_list))


if __name__ == "__main__":
    unittest.main()
