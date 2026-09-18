"""Long-window entry-rule ablation, NOT the production minute strategy backtest."""

import argparse
import hashlib
import json
from datetime import date
from pathlib import Path

from app.core.calendar import Calendar
from app.core.config import Settings
from scripts.backtest_etfs import simulate


def run(data, levels, end):
    config = Settings.model_validate(data["config"])
    instruments = {r["symbol"]: r for r in data["instruments"]}
    calendar = Calendar()
    results = []
    for item in levels["rows"]:
        symbol = item["symbol"]
        stored = [r for r in data["bars"] if r["symbol"] == symbol]
        raw = {r["day"]: json.loads(r["payload"]) for r in stored if r["adjustment"] == "none"}
        # The actual store names the unadjusted series 'raw'.
        raw.update({r["day"]: json.loads(r["payload"]) for r in stored if r["adjustment"] == "raw"})
        qfq = sorted((json.loads(r["payload"]) for r in stored if r["adjustment"] == "qfq"),
                     key=lambda b: b["day"])
        eligible = [r for r in item["days"] if calendar.days[0] < r["day"] <= end]
        if not eligible:
            continue
        first = eligible[0]["day"]
        if any(b["day"] not in raw for b in qfq if first <= b["day"] <= end):
            raise ValueError(f"{symbol}: unadjusted bars missing")
        bars = [{"day": b["day"], **{k: float(b[k]) for k in ("open", "high", "low", "close")},
                 "factor": float(raw[b["day"]]["open"]) / float(b["open"])}
                for b in qfq if first <= b["day"] <= end]
        runs = {}
        for mode in ("baseline", "candidate"):
            plans = {r["day"]: {**r[mode], "data_gap":
                     r[mode]["as_of"] != calendar.previous(date.fromisoformat(r["day"]))}
                     for r in eligible}
            for path in ("OHLC", "OLHC"):
                runs[f"{mode}_{path}"] = simulate(bars, plans, instruments[symbol], config,
                                                  path=path, profit_targets=False)
        results.append({"symbol": symbol, "name": instruments[symbol]["name"],
                        "start": first, "end": bars[-1]["day"], "days": len(bars), "runs": runs})
    return {"end": end, "config": config.model_dump(mode="json"), "results": results,
            "model": "Entry-only diagnostic; both sides disable minute profit/T/reentry, retain daily hard stops",
            "allocation": "Independent 100000 capital per ETF, not a five-position portfolio",
            "limitations": ["OHLC and OLHC are assumed paths, not quotes; no spread or volume participation",
                            "Current watchlist selection and provider-adjustment revision bias",
                            "This earlier historical window is not forward out-of-sample evidence"]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input")
    parser.add_argument("levels")
    parser.add_argument("output")
    parser.add_argument("--end", default="2026-09-10")
    args = parser.parse_args()
    raw = Path(args.input).read_bytes()
    levels = json.loads(Path(args.levels).read_text())
    assert hashlib.sha256(raw).hexdigest() == levels["input_sha256"]
    result = run(json.loads(raw), levels, args.end)
    result["input_sha256"] = levels["input_sha256"]
    Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2))
