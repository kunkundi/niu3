import unittest

from app.core.config import Settings
from scripts.backtest_etfs import simulate


class DailyScenarioBacktestTests(unittest.TestCase):
    def setUp(self):
        self.config = Settings(commission_rate=0, minimum_commission=0, slippage_bps=0)
        self.instrument = {"settlement": 0, "tick": 1000, "lot_size": 100}

    def bar(self, day, opening, high, low, close):
        return {"day": day, "open": opening, "high": high, "low": low, "close": close, "factor": 1}

    def plan(self, as_of="2026-01-01", target=12):
        return {"ready": True, "as_of": as_of, "entry": 10, "entry_stop": 9,
                "target": target, "entry_ceiling": 11, "setup": "测试形态", "trend": "上升结构"}

    def test_intraday_path_does_not_apply_a_pre_entry_low_as_a_later_stop(self):
        bars = [self.bar("2026-01-02", 9, 10.5, 8, 10.2)]
        plans = {"2026-01-02": self.plan()}
        high_first = simulate(bars, plans, self.instrument, self.config, "OHLC")
        low_first = simulate(bars, plans, self.instrument, self.config, "OLHC")
        self.assertEqual(high_first["closed_trades"], 1)
        self.assertAlmostEqual(high_first["trades"][0]["exit_price"], 9)
        self.assertTrue(low_first["open_position"])
        self.assertEqual(low_first["closed_trades"], 0)

    def test_t1_stop_latches_and_executes_at_next_open_even_after_price_recovers(self):
        bars = [self.bar("2026-01-02", 9, 10.5, 8, 10.2), self.bar("2026-01-05", 10.5, 11, 10, 10.8)]
        run = simulate(bars, {"2026-01-02": self.plan()}, {**self.instrument, "settlement": 1}, self.config)
        self.assertEqual(run["trades"][0]["exit_day"], "2026-01-05")
        self.assertAlmostEqual(run["trades"][0]["exit_price"], 10.5)
        self.assertEqual(run["counters"]["t1_deferred"], 1)

    def test_existing_position_target_and_stop_follow_assumed_path(self):
        bars = [self.bar("2026-01-02", 10, 10, 10, 10), self.bar("2026-01-05", 10, 13, 8, 10)]
        plans = {"2026-01-02": self.plan()}
        high_first = simulate(bars, plans, self.instrument, self.config, "OHLC")
        low_first = simulate(bars, plans, self.instrument, self.config, "OLHC")
        self.assertAlmostEqual(high_first["trades"][0]["exit_price"], 12)
        self.assertAlmostEqual(low_first["trades"][0]["exit_price"], 9)

    def test_gap_stop_uses_open_price_and_gap_above_entry_ceiling_does_not_reenter(self):
        bars = [self.bar("2026-01-02", 10, 10, 10, 10), self.bar("2026-01-05", 8, 11, 7, 10)]
        run = simulate(bars, {"2026-01-02": self.plan()}, self.instrument, self.config)
        self.assertAlmostEqual(run["trades"][0]["exit_price"], 8)
        gap = [self.bar("2026-01-02", 12, 12, 9, 10)]
        self.assertEqual(simulate(gap, {"2026-01-02": self.plan()}, self.instrument, self.config)["entries"], 0)

    def test_slippage_rechecks_reward_risk_and_fee_accounting_reconciles(self):
        bars = [self.bar("2026-01-02", 10, 10, 10, 10)]
        costs = Settings(commission_rate="0.0001", slippage_bps=5)
        rejected = simulate(bars, {"2026-01-02": self.plan(target=11.5)}, self.instrument, costs)
        self.assertEqual(rejected["entries"], 0)
        self.assertEqual(rejected["counters"]["rejected_after_cost"], 1)
        bars.append(self.bar("2026-01-05", 12, 12, 12, 12))
        run = simulate(bars, {"2026-01-02": self.plan()}, self.instrument, costs)
        self.assertAlmostEqual(run["ending_nav"] - 100000, sum(t["net_pnl"] for t in run["trades"]))
        self.assertGreater(run["fees"], 0)

    def test_future_prices_cannot_change_earlier_equity_and_same_day_signals_are_rejected(self):
        bars = [self.bar("2026-01-02", 10, 10.5, 9.5, 10.2)]
        plans = {"2026-01-02": self.plan()}
        before = simulate(bars, plans, self.instrument, self.config)
        after = simulate(bars + [self.bar("2026-01-05", 500, 501, 499, 500)], plans, self.instrument, self.config)
        self.assertEqual(before["equity"], after["equity"][:1])
        with self.assertRaises(AssertionError):
            simulate(bars, {"2026-01-02": self.plan("2026-01-02")}, self.instrument, self.config)


if __name__ == "__main__":
    unittest.main()
