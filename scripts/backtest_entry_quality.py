"""Fixed-policy temporal diagnostic. This is not the live minute-strategy backtest."""

import argparse
import hashlib
import json
import math
from pathlib import Path

from app.core.config import Settings
from scripts.backtest_etfs import simulate


WINDOWS = {
    "train": ("1900-01-01", "2026-03-31"),
    "validation": ("2026-04-01", "2026-06-30"),
    "test": ("2026-07-01", "2026-09-17"),
    "full": ("1900-01-01", "2026-09-17"),
}


def win_interval(trades):
    """Wilson 95% binomial interval; dependence between trades is not removed."""
    n = len(trades)
    if not n:
        return None
    p, z = sum(t["net_pnl"] > 0 for t in trades) / n, 1.959963984540054
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return [100 * (centre - margin), 100 * (centre + margin)]


def run(data, levels):
    config = Settings.model_validate(data["config"])
    instruments = {r["symbol"]: r for r in data["instruments"]}
    calendar = sorted({r["day"] for r in data["bars"]})
    previous = dict(zip(calendar[1:], calendar))
    results, excluded = [], []
    for item in levels["rows"]:
        symbol = item["symbol"]
        stored = [r for r in data["bars"] if r["symbol"] == symbol]
        raw = {r["day"]: json.loads(r["payload"]) for r in stored if r["adjustment"] == "raw"}
        qfq = {r["day"]: json.loads(r["payload"]) for r in stored if r["adjustment"] == "qfq"}
        assert set(raw) == set(qfq), f"{symbol}: adjustment dates differ"
        bars, adjustment_errors = [], []
        for day in sorted(qfq):
            b, unadjusted = qfq[day], raw[day]
            assert 0 < float(b["low"]) <= min(float(b["open"]), float(b["close"])) <= max(
                float(b["open"]), float(b["close"])) <= float(b["high"])
            factor = float(unadjusted["open"]) / float(b["open"])
            if any(abs(float(unadjusted[k]) - float(b[k]) * factor) > .004 for k in ("close", "high", "low")):
                adjustment_errors.append(day)
            bars.append({"day": day, "factor": factor, **{k: float(b[k]) for k in ("open", "high", "low", "close")}})
        if adjustment_errors:
            excluded.append({"symbol": symbol, "days": adjustment_errors,
                             "reason": "Raw/qfq proportional conversion exceeds .004 price tolerance; split rounding or additive dividend adjustment requires a separate model"})
            continue
        windows = {}
        for name, (start, end) in WINDOWS.items():
            days = [r for r in item["days"] if start <= r["day"] <= end]
            if not days:
                continue
            selected = [b for b in bars if days[0]["day"] <= b["day"] <= end]
            runs = {}
            for policy in levels["policies"]:
                plans = {r["day"]: {**r["plans"][policy], "data_gap":
                         r["plans"][policy]["as_of"] != previous[r["day"]]} for r in days}
                for path in ("OHLC", "OLHC"):
                    result = simulate(selected, plans, instruments[symbol], config,
                                      path=path, profit_targets=False)
                    result["win_rate_wilson95"] = win_interval(result["trades"])
                    result["avg_trade_return_pct"] = (sum(t["return_pct"] for t in result["trades"]) /
                                                       len(result["trades"]) if result["trades"] else None)
                    runs[f"{policy}_{path}"] = result
            windows[name] = {"start": selected[0]["day"], "end": selected[-1]["day"],
                             "days": len(selected), "runs": runs}
        results.append({"symbol": symbol, "windows": windows})
    return {"results": results, "excluded": excluded, "windows": WINDOWS, "config": config.model_dump(mode="json"),
            "model": "Entry-only OHLC/OLHC scenarios, identical daily hard exits; minute profit/T/reentry disabled",
            "win_rate": "Net profitable complete round trips / all closed round trips; excludes open positions",
            "allocation": "Independent 100000 capital per symbol, 100% capacity; not the production portfolio",
            "calendar": "Union of supplied observed daily dates; missing prior market day blocks entry",
            "limitations": ["Previously observed historical temporal split, not prospective out-of-sample",
                            "Daily scenarios cannot validate full deployed strategy win rate",
                            "No spread/market impact; qfq-equivalent units, not historical cash/share ledger",
                            "Each window starts with cash; full-window positions may cross split boundaries",
                            "Current watchlist and revised historical prices introduce selection/revision bias"]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input")
    parser.add_argument("levels")
    parser.add_argument("output")
    args = parser.parse_args()
    raw = Path(args.input).read_bytes()
    levels = json.loads(Path(args.levels).read_text())
    assert hashlib.sha256(raw).hexdigest() == levels["input_sha256"]
    result = run(json.loads(raw), levels)
    result["input_sha256"] = levels["input_sha256"]
    result["levels_sha256"] = hashlib.sha256(Path(args.levels).read_bytes()).hexdigest()
    root = Path(__file__).resolve().parents[1]
    result["code_sha256"] = {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in (
        "scripts/backtest_entry_quality.py", "scripts/backtest_etfs.py", "scripts/research_pa_levels.mjs",
        "web/src/price-action/strategy.js", "web/src/price-action/entry-quality.js",
        "web/src/price-action/engine.js", "app/strategies/price_action.py")}
    Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2))
