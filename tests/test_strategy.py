import unittest
from dataclasses import replace
from decimal import Decimal

from app.core.config import Settings
from app.core.types import Bar, Instrument, symbol_for
from app.strategies.momentum import build_plan, evaluate
from tests.helpers import bars


class StrategyTests(unittest.TestCase):
    def setUp(self):
        self.i = Instrument("sh510300", "300ETF", "equity", "CN", "沪深300", verified=True)
        self.config = Settings()
        self.bars = bars()

    def test_exchange_and_three_digit_fund_codes(self):
        self.assertEqual(symbol_for("510300"), "sh510300")
        self.assertEqual(symbol_for("159915"), "sz159915")
        self.assertEqual(symbol_for("588000", "sh"), "sh588000")
        with self.assertRaises(ValueError):
            symbol_for("510300", "sz")
        with self.assertRaises(ValueError):
            symbol_for("600000")

    def test_causal_score_excludes_future_and_requires_complete_day(self):
        future = self.bars + [Bar("2026-09-07", "9999", "999999999")]
        expected = evaluate(self.i, self.bars, "2026-09-04", self.config)
        actual = evaluate(self.i, future, "2026-09-04", self.config)
        self.assertEqual(expected, actual)
        self.assertTrue(actual["eligible"])
        closes = [Decimal(b.close) for b in self.bars]
        score = Decimal(".6") * (closes[-1] / closes[-21] - 1) + Decimal(".4") * (
            closes[-1] / closes[-61] - 1
        )
        self.assertEqual(Decimal(actual["score"]), score)
        self.assertFalse(evaluate(self.i, self.bars[:-1], "2026-09-04", self.config)["eligible"])

    def test_short_or_falling_history_cannot_enter_targets(self):
        for history in (self.bars[:80], bars(growth=-0.001)):
            with self.subTest(count=len(history), latest_close=history[-1].close):
                self.assertFalse(evaluate(self.i, history, "2026-09-04", self.config)["eligible"])

    def test_all_etf_types_and_markets_can_enter_momentum_targets(self):
        for category, region in [
            ("equity", "CN"),
            ("equity", "OVERSEAS"),
            ("cross_border", "OVERSEAS"),
            ("commodity", "CN"),
            ("bond", "CN"),
            ("money", "CN"),
        ]:
            with self.subTest(category=category, region=region):
                instrument = replace(self.i, category=category, region=region)
                plan = build_plan(
                    [instrument],
                    {instrument.symbol: self.bars},
                    set(),
                    "2026-09-04",
                    "2026-09-07",
                    self.config,
                )
                self.assertTrue(instrument.tradable)
                self.assertEqual(plan["targets"], {instrument.symbol: "0.20"})
                for changed in (
                    {"verified": False},
                    {"active": False},
                    {"index_id": ""},
                    {"accounting_block": "分红／折算事件待核验"},
                ):
                    blocked = replace(instrument, **changed)
                    self.assertFalse(blocked.tradable)
                    self.assertFalse(evaluate(blocked, self.bars, "2026-09-04", self.config)["eligible"])

    def test_keep_top_eight_and_fill_five_equal_weights(self):
        universe = [replace(self.i, symbol=f"sh5100{n:02d}", index_id=f"index{n}") for n in range(10)]
        histories = {i.symbol: bars(growth=0.01 - n * 0.0005) for n, i in enumerate(universe)}
        plan = build_plan(
            universe,
            histories,
            {universe[7].symbol, universe[9].symbol},
            "2026-09-04",
            "2026-09-07",
            self.config,
        )
        self.assertEqual(len(plan["targets"]), 5)
        self.assertIn(universe[7].symbol, plan["targets"])
        self.assertNotIn(universe[9].symbol, plan["targets"])
        self.assertTrue(all(Decimal(v) == Decimal(".16") for v in plan["targets"].values()))

    def test_tie_order_and_cooldown(self):
        other = replace(self.i, symbol="sh510310", index_id="中证500")
        plan = build_plan(
            [other, self.i],
            {self.i.symbol: self.bars, other.symbol: self.bars},
            set(),
            "2026-09-04",
            "2026-09-07",
            self.config,
            {self.i.symbol},
        )
        self.assertEqual(plan["rows"][0]["symbol"], self.i.symbol)
        self.assertNotIn(self.i.symbol, plan["targets"])

    def test_configuration_rejects_invalid_limits(self):
        with self.assertRaises(ValueError):
            Settings(max_weight=".9", max_exposure=".8")
        with self.assertRaises(ValueError):
            Settings(participation=".02")


if __name__ == "__main__":
    unittest.main()
