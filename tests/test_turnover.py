import json
import unittest
from dataclasses import replace
from decimal import Decimal
from unittest.mock import Mock


from app.market_data.providers import PublicProvider, turnover_ratio
from app.strategies.focus import liquidity, turnover_liquidity
from tests.helpers import Fixture, bars


def rated(rate="0.02", **kwargs):
    return [replace(b, turnover_rate=rate, turnover_source="eastmoney:f61") for b in bars(**kwargs)]


class TurnoverMetricTests(unittest.TestCase):
    def test_last_twenty_days_are_averaged_without_future_data_or_zero_filling(self):
        amounts = bars(amount="200000000")
        amounts[-20:] = [replace(b, amount="1000000") for b in amounts[-20:]]
        amounts.append(replace(amounts[-1], day="2026-09-07", amount="999999999"))
        self.assertEqual(liquidity(amounts, "2026-09-04"), (Decimal("1000000"), ""))
        history = rated("0.50")
        history[-20:] = [replace(b, turnover_rate="0.01") for b in history[-20:]]
        history[-1] = replace(history[-1], turnover_rate="0.21")
        history.append(replace(history[-1], day="2026-09-07", turnover_rate="100"))
        self.assertEqual(turnover_liquidity(history, "2026-09-04"), (Decimal("0.02"), ""))
        history[-2] = replace(history[-2], turnover_rate=None)
        self.assertIsNone(turnover_liquidity(history, "2026-09-04")[0])
        self.assertEqual(turnover_liquidity(rated("0"), "2026-09-04")[0], 0)
        self.assertEqual(turnover_liquidity(rated("1.25"), "2026-09-04")[0], Decimal("1.25"))

    def test_bad_or_mixed_sources_stale_duplicate_and_short_history_are_unavailable(self):
        for rate in ("-1", "NaN", "Infinity", "bad"):
            self.assertIsNone(turnover_liquidity(rated(rate), "2026-09-04")[0])
        for history in [rated(count=19), rated(end="2026-09-03"), rated() + rated()[-1:]]:
            self.assertIsNone(turnover_liquidity(history, "2026-09-04")[0])
        history = rated()
        for source in ("", "ths:1968584"):
            history[-1] = replace(history[-1], turnover_source=source)
            self.assertIsNone(turnover_liquidity(history, "2026-09-04")[0])


class TurnoverDataTests(unittest.TestCase):
    def setUp(self):
        self.f = Fixture()
        self.provider = PublicProvider()

    def tearDown(self):
        self.provider.close()
        self.f.close()

    def test_primary_requests_reported_field_and_converts_percent_to_ratio(self):
        for value, expected in [
            ("1.25", "0.0125"),
            ("0", "0"),
            ("125", "1.25"),
            ("-", None),
            ("NaN", None),
            ("-1", None),
        ]:
            with self.subTest(value=value):
                self.provider.get = Mock(
                    return_value=Mock(
                        json=lambda: {
                            "data": {"code": "510300", "klines": [f"2026-09-04,2,2,2,2,1000,200000,{value}"]}
                        }
                    )
                )
                result = self.provider.eastmoney_history(self.f.instrument, "2026-09-04")
                self.assertTrue(self.provider.get.call_args.args[1]["fields2"].endswith(",f61"))
                for rows in result.values():
                    self.assertEqual(rows[-1].turnover_rate, expected)
                    self.assertEqual(rows[-1].turnover_source, "eastmoney:f61")
        self.assertIsNone(turnover_ratio(None))

    def test_fallback_uses_daily_reported_rate_and_does_not_infer_missing_rate(self):
        for suffix, expected in [(",3.600", "0.036"), ("", None), (",", None)]:

            def get(url, params=None):
                if "fqkline" in url:
                    key = "qfqday" if params["param"].endswith(",qfq") else "day"
                    return Mock(
                        json=lambda: {
                            "data": {"sh510300": {key: [["2026-09-04", "2", "2", "2", "2", "1000"]]}}
                        }
                    )
                return Mock(
                    text="callback(" + json.dumps({"data": "20260904,2,2,2,2,100000,200000" + suffix}) + ")"
                )

            self.provider.get = get
            result = self.provider.fallback_history(self.f.instrument, "2026-09-04")
            self.assertEqual(result["qfq"][-1].turnover_rate, expected)
            self.assertEqual(result["qfq"][-1].turnover_source, "ths:1968584")

if __name__ == "__main__":
    unittest.main()
