import copy
import json
import unittest
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock

from app.automation.service import Worker
from app.automation.targets import calculate_targets
from app.core.config import Settings
from app.core.types import Bar, iso, units
from app.storage.db import get_state, put_instrument, set_state
from app.strategies.causal_review import causal_review
from app.strategies.price_action import build_plan
from app.trading.account import reconcile
from app.trading.intraday import IntradayTrader
from tests.helpers import Fixture, at


def grain_history():
    fixture = json.loads((Path(__file__).parent / "fixtures/sz159587-causal.json").read_text())
    return [Bar(**b, amount=None) for b in fixture["bars"]]


def request(history):
    return {
        "symbol": "sz159587",
        "minimum_bars": 120,
        "minimum_rr": 1.5,
        "tick": 0.001,
        "as_of": history[-1].day,
        "bars": [
            {"date": b.day, **{k: float(getattr(b, k)) for k in ("open", "high", "low", "close")}}
            for b in history
        ],
    }


class CausalStrategyTests(unittest.TestCase):
    def test_rr_switch_applies_to_review_and_keeps_the_chase_limit(self):
        data = {**request(grain_history()), "minimum_rr": 5}
        enabled = causal_review(data)
        self.assertTrue(enabled["rr_enabled"])
        self.assertFalse(any(m["day"] == "2026-08-27" for m in enabled["markers"]))
        data["rr_enabled"] = False
        disabled = causal_review(data)
        marker = next(m for m in disabled["markers"] if m["day"] == "2026-08-27")
        self.assertFalse(disabled["rr_enabled"])
        self.assertLess(marker["reward_risk"], 5)
        self.assertAlmostEqual(marker["entry_ceiling"], 1.248)
        data["bars"][-1].update(open=1.26, low=1.25, high=1.27, close=1.26)
        self.assertFalse(any(m["day"] == "2026-08-27" for m in causal_review(data)["markers"]))

    def test_rr_switch_reaches_live_targets_orders_and_matching(self):
        for enabled in (True, False):
            with self.subTest(enabled=enabled):
                f = Fixture()
                worker = Worker(f.db, f.calendar, Mock())
                try:
                    now = at("2026-08-27T10:00:00")
                    f.db.change_config({
                        "execution_mode": "intraday", "strategy_model": "price_action",
                        "pa_min_rr": 5, "pa_rr_enabled": enabled,
                    }, now)
                    history = grain_history()[:-1]
                    worker.ingest("history:sh510300", {"raw": history, "qfq": history}, now)
                    trader = IntradayTrader(f.engine)
                    with f.db.transaction() as conn:
                        set_state(conn, "actions:sh510300", {"at": iso(now)})
                    for offset in (0, 60):
                        when = now + timedelta(seconds=offset)
                        f.quote(when, price="1.218", previous_close=units("1.199"),
                                upper=units("1.4"), lower=units("1.0"))
                        plan = calculate_targets(f.db, "2026-08-26", when)
                        worker.ingest("live_targets", plan, when)
                        trader.tick(when)
                    self.assertEqual(bool(plan["targets"]), not enabled)
                    self.assertEqual(bool(f.rows("orders")), not enabled)
                    fill_at = now + timedelta(seconds=90)
                    f.quote(fill_at, price="1.218", volume=5000000, previous_close=units("1.199"),
                            upper=units("1.4"), lower=units("1.0"))
                    f.engine.match(fill_at)
                    self.assertEqual(bool(f.rows("fills")), not enabled)
                    with f.db.connect() as conn:
                        self.assertEqual(reconcile(conn), [])
                finally:
                    worker.close()
                    f.close()

    def test_missing_duplicate_invalid_and_unfinished_history_is_not_reported_as_zero_buys(self):
        valid = request(grain_history())
        invalid = [
            {**valid, "bars": valid["bars"][-9:]},
            {**valid, "bars": [*valid["bars"], valid["bars"][-1]]},
            {**valid, "bars": valid["bars"][:-1]},
            {**valid, "window_days": ["2026-08-01"]},
            *[
                {**valid, "bars": [*valid["bars"][:-1], {**valid["bars"][-1], "low": low}]}
                for low in (None, float("nan"), 0, 10)
            ],
            {**valid, "bars": [*valid["bars"][:-1], {**valid["bars"][-1], "closed": False}]},
        ]
        for data in invalid:
            with self.subTest(data=data["bars"][-1]):
                result = causal_review(data)
                self.assertFalse(result["ready"])
                self.assertEqual(result["markers"], [])
        quiet = {
            **valid,
            "bars": [{**b, "open": 1, "close": 1, "high": 1.001, "low": 0.999} for b in valid["bars"]],
        }
        self.assertTrue(causal_review(quiet)["ready"])
        self.assertEqual(causal_review(quiet)["markers"], [])

    def test_grain_trigger_is_known_the_day_before_and_future_changes_cannot_filter_it(self):
        data = request(grain_history())
        before = copy.deepcopy(data)
        result = causal_review(data)
        marker = next(m for m in result["markers"] if m["day"] == "2026-08-27")
        self.assertEqual(marker["known_through"], "2026-08-26")
        self.assertEqual(marker["setup"], "Inside Bar")
        self.assertAlmostEqual(marker["trigger"], 1.217)
        self.assertAlmostEqual(marker["stop"], 1.154)
        self.assertLess(marker["entry_ceiling"], 1.229)
        self.assertFalse(marker["execution_verified"])
        self.assertEqual(data, before)
        data["bars"].append({"date": "2026-08-28", "open": 0, "high": 1000, "low": -1, "close": None})
        self.assertEqual(causal_review(data), result)
        # A later crash is evaluated later, but cannot erase the 27th's candidate.
        data["as_of"] = "2026-08-28"
        data["bars"][-1] = {"date": "2026-08-28", "open": 1.24, "high": 1.25, "low": 1.0, "close": 1.01}
        later = causal_review(data)
        self.assertEqual(next(m for m in later["markers"] if m["day"] == marker["day"]), marker)

    def test_no_close_confirmation_and_no_optimistic_intraday_path(self):
        data = request(grain_history())
        data["bars"][-1].update(close=1.205)
        marker = next(m for m in causal_review(data)["markers"] if m["day"] == "2026-08-27")
        self.assertFalse(marker["path_ambiguous"])
        data["bars"][-1].update(low=1.14, close=1.15)
        ambiguous = next(m for m in causal_review(data)["markers"] if m["day"] == "2026-08-27")
        self.assertTrue(ambiguous["path_ambiguous"])
        self.assertFalse(ambiguous["execution_verified"])
        self.assertEqual(ambiguous["entry"], marker["entry"])
        data["bars"][-1].update(open=1.25, low=1.24, close=1.26, high=1.27)
        self.assertFalse(any(m["day"] == "2026-08-27" for m in causal_review(data)["markers"]))

    def test_real_grain_history_to_synthetic_quotes_to_buy_fill_and_frozen_target_exit(self):
        f = Fixture()
        worker = Worker(f.db, f.calendar, Mock())
        try:
            instrument = replace(f.instrument, symbol="sz159587", name="粮食ETF广发")
            now = at("2026-08-27T10:00:00")
            config = Settings(execution_mode="intraday", strategy_model="price_action")
            f.db.change_config({"execution_mode": "intraday", "strategy_model": "price_action"}, now)
            with f.db.transaction() as conn:
                put_instrument(conn, replace(f.instrument, watched=False))
                put_instrument(conn, instrument)
                set_state(conn, "actions:sz159587", {"at": iso(now)})
            history = grain_history()
            # No 27th candle (nor anything later) is present in the trading database.
            worker.ingest("history:sz159587", {"raw": history[:-1], "qfq": history[:-1]}, now)
            trader = IntradayTrader(f.engine)

            def quote(when, price, volume=1000000):
                return f.quote(
                    when,
                    price=price,
                    volume=volume,
                    symbol=instrument.symbol,
                    previous_close=units("1.199"),
                    upper=units("1.4"),
                    lower=units("1.0"),
                )

            def run(when, price):
                quote(when, price)
                plan = calculate_targets(f.db, "2026-08-26", when)
                worker.ingest("live_targets", plan, when)
                trader.tick(when)
                return plan

            run(now, "1.201")
            self.assertEqual(f.rows("orders"), [])
            run(now + timedelta(seconds=60), "1.218")
            self.assertEqual(f.rows("orders"), [])
            plan = run(now + timedelta(seconds=120), "1.218")
            self.assertEqual(plan["targets"], {"sz159587": "0.20"})
            self.assertEqual(len(f.rows("orders")), 1)
            self.assertEqual(f.rows("orders")[0]["side"], "BUY")
            fill_at = now + timedelta(seconds=150)
            quote(fill_at, "1.218", 5000000)
            f.engine.match(fill_at)
            self.assertTrue(f.rows("fills"))
            self.assertEqual({r["day"] for r in f.rows("bars")} & {"2026-08-27"}, set())
            with f.db.connect() as conn:
                reference = get_state(conn, "pa_position:sz159587")
                self.assertEqual(reference["stop"], units("1.154"))
                self.assertEqual(reference["target"], units("1.340"))
            # Trade decisions also ignore an explicitly supplied future candle.
            past = build_plan(
                [instrument],
                {instrument.symbol: history[:-1]},
                set(),
                "2026-08-26",
                "",
                config,
                live_prices={instrument.symbol: Decimal("1.218")},
            )
            future = build_plan(
                [instrument],
                {instrument.symbol: history},
                set(),
                "2026-08-26",
                "",
                config,
                live_prices={instrument.symbol: Decimal("1.218")},
            )
            self.assertEqual(past, future)
            too_late = build_plan(
                [instrument],
                {instrument.symbol: history[:-1]},
                set(),
                "2026-08-26",
                "",
                config,
                live_prices={instrument.symbol: Decimal("1.244")},
            )
            self.assertEqual(too_late["targets"], {})
            # Synthetic next-day target touch uses the entry's frozen target, no future K.
            tomorrow = at("2026-08-28T10:00:00")
            with f.db.transaction() as conn:
                set_state(conn, "actions:sz159587", {"at": iso(tomorrow)})
            quote(tomorrow, "1.341", 1000000)
            f.engine.risk_check(tomorrow)
            self.assertIn("到达入场时确定", f.rows("risk_intents")[0]["reason"])
            quote(tomorrow + timedelta(seconds=30), "1.341", 5000000)
            f.engine.match(tomorrow + timedelta(seconds=30))
            self.assertTrue(any(r["side"] == "SELL" for r in f.rows("fills")))
            with f.db.connect() as conn:
                self.assertEqual(reconcile(conn), [])
        finally:
            worker.close()
            f.close()
