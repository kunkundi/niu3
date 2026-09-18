"""Offline OHLC scenario backtest; never writes to the live account/database.

Signals are frozen after the preceding completed day. OHLC/OLHC are alternative
assumed paths, not reconstructed quotes. Qfq-equivalent units normalize corporate
actions; this is not a reconstruction of historical raw-share cash ledgers.
"""

import argparse
import hashlib
import json
import math
from collections import Counter
from pathlib import Path

from app.core.config import Settings
from app.core.types import units
from app.strategies.price_action import price_decision
from app.trading.engine import fee_for


def fee(gross, config):
    return fee_for(units(gross), config) / 1_000_000


def execution_price(price, factor, tick, side):
    raw = price * factor
    steps = math.ceil(raw / tick - 1e-9) if side == "BUY" else math.floor(raw / tick + 1e-9)
    return steps * tick / factor


def buy_size(cash, price, factor, lot, config):
    quantity = int(cash / (price * factor) / lot) * lot
    while quantity and quantity * factor * price + fee(quantity * factor * price, config) > cash + 1e-8:
        quantity -= lot
    return quantity * factor


def simulate(bars, plans, instrument, config, path="OHLC", benchmark=False, profit_targets=True):
    cash = float(config.initial_cash)
    initial = cash
    position = None
    trades, equity = [], []
    counters = Counter()
    peak, drawdown, fees = cash, 0.0, 0.0
    settlement = instrument["settlement"]
    tick = instrument["tick"] / 1_000_000
    for index, bar in enumerate(bars):
        day, factor = bar["day"], bar.get("factor", 1.0)
        pa = plans.get(day, {})
        if pa:
            assert pa["as_of"] < day, "Future or same-day analysis cannot trade today"
        if position and pa.get("ready"):
            position["stop"] = max(position["stop"], pa.get("structural_stop") or 0)
        attempted = False
        exited = False

        def sell(price, reason):
            nonlocal cash, position, fees, exited
            fill = execution_price(price, factor, tick, "SELL")
            gross = position["quantity"] * fill
            commission = fee(gross, config)
            cash += gross - commission
            fees += commission
            trades.append({
                **position,
                "exit_day": day, "exit_price": fill, "exit_reason": reason,
                "exit_fee": commission, "net_pnl": gross - commission - position["cost"],
                "return_pct": (gross - commission) / position["cost"] * 100 - 100,
                "holding_days": index - position["index"],
            })
            position = None
            exited = True

        def risk(price, reason):
            if position["index"] + settlement <= index:
                sell(price, reason)
            elif not position["pending_exit"]:
                position["pending_exit"] = reason
                counters["t1_deferred"] += 1

        def stop_price():
            return max(position["stop"], pa.get("exit") or 0)

        def enter(price):
            nonlocal attempted, position, cash, fees
            attempted = True
            if exited or not pa.get("ready") or pa.get("data_gap"):
                return
            counters["first_touches"] += 1
            if not benchmark and price_decision(pa, price, config.pa_min_rr, config.pa_rr_enabled)["action"] != "buy":
                counters["rejected_reference"] += 1
                return
            fill = execution_price(price, factor, tick, "BUY")
            if not benchmark and price_decision(pa, fill, config.pa_min_rr, config.pa_rr_enabled)["action"] != "buy":
                counters["rejected_after_cost"] += 1
                return
            quantity = buy_size(cash, fill, factor, instrument["lot_size"], config)
            if quantity <= 0:
                counters["insufficient_cash"] += 1
                return
            gross = quantity * fill
            commission = fee(gross, config)
            cost = gross + commission
            cash -= cost
            fees += commission
            position = {
                "entry_day": day, "index": index, "known_through": pa["as_of"],
                "signal_day": pa.get("signal_day"), "setup": pa.get("setup", "买入持有"),
                "entry_price": fill, "quantity": quantity, "cost": cost,
                "entry_fee": commission,
                "stop": max(pa.get("entry_stop") or 0, pa.get("structural_stop") or 0),
                "target": (pa.get("target") or math.inf) if profit_targets else math.inf, "pending_exit": None,
            }
            counters["entries"] += 1

        def point(price):
            if position and not benchmark:
                if position["pending_exit"]:
                    if position["index"] + settlement <= index:
                        sell(price, "T+1延期：" + position["pending_exit"])
                elif price <= stop_price() + 1e-10:
                    risk(price, "止损／结构退出")
                elif price >= position["target"] - 1e-10:
                    risk(price, "冻结目标止盈")
            if not position and not attempted and not exited and pa.get("entry") and price >= pa["entry"] - 1e-10:
                enter(price)

        def segment(left, right):
            # Event order is determined by the chosen path and crossing price,
            # never by which order produces a more favorable backtest outcome.
            if position and not benchmark and not position["pending_exit"]:
                boundary = position["target"] if right > left else stop_price()
                crossed = left < boundary <= right if right > left else right <= boundary < left
                if crossed:
                    risk(boundary, "冻结目标止盈" if right > left else "止损／结构退出")
            elif not position and not attempted and not exited and pa.get("entry"):
                trigger = pa["entry"]
                if left < trigger <= right:
                    enter(trigger)
                    if position and not benchmark:
                        segment(trigger, right)
                        return
            point(right)

        if benchmark:
            if not position and not trades and pa.get("ready") and not pa.get("data_gap"):
                enter(bar["open"])
        else:
            point(bar["open"])
            prices = [bar[key] for key in ("open", "high", "low", "close")]
            if path == "OLHC":
                prices = [bar[key] for key in ("open", "low", "high", "close")]
            for left, right in zip(prices, prices[1:]):
                segment(left, right)
        nav = cash + (position["quantity"] * bar["close"] if position else 0)
        assert cash >= -1e-7 and math.isfinite(nav)
        peak = max(peak, nav)
        drawdown = max(drawdown, 1 - nav / peak)
        equity.append({"day": day, "nav": nav, "cash": cash, "holding": bool(position)})
    final = equity[-1]["nav"] if equity else initial
    wins = sum(trade["net_pnl"] > 0 for trade in trades)
    gross_profit = sum(max(0, trade["net_pnl"]) for trade in trades)
    gross_loss = -sum(min(0, trade["net_pnl"]) for trade in trades)
    return {
        "return_pct": (final / initial - 1) * 100, "max_drawdown_pct": drawdown * 100,
        "ending_nav": final, "closed_trades": len(trades), "entries": counters["entries"],
        "win_rate_pct": wins / len(trades) * 100 if trades else None,
        "profit_factor": gross_profit / gross_loss if gross_loss else None,
        "fees": fees, "open_position": bool(position),
        "unrealized_pnl": position["quantity"] * bars[-1]["close"] - position["cost"] if position else 0,
        "exposure_pct": sum(row["holding"] for row in equity) / len(equity) * 100 if equity else 0,
        "counters": dict(counters), "trades": trades, "equity": equity,
    }


def validate_input(input_data):
    all_days = sorted({bar["day"] for item in input_data["etfs"] for bar in item["qfq"]})
    checks = []
    for item in input_data["etfs"]:
        bars = item["qfq"]
        days = [bar["day"] for bar in bars]
        errors = []
        if days != sorted(set(days)):
            errors.append("duplicate_or_unordered_days")
        for bar in bars:
            values = [float(bar[key]) for key in ("open", "high", "low", "close")]
            if not all(math.isfinite(value) and value > 0 for value in values):
                errors.append("invalid_prices:" + bar["day"])
            if values[2] > min(values[0], values[3]) or values[1] < max(values[0], values[3]):
                errors.append("invalid_ohlc:" + bar["day"])
        missing = [day for day in all_days if days[0] < day < days[-1] and day not in days] if days else []
        checks.append({
            "symbol": item["instrument"]["symbol"], "bars": len(bars), "errors": errors,
            "start": days[0] if days else None, "end": days[-1] if days else None,
            "missing_relative_to_peers": missing,
            "sources": sorted({bar["stored_source"] for bar in bars}),
        })
    return all_days, checks


def run(input_path, levels_path, output_path):
    source = Path(input_path).read_bytes()
    data = json.loads(source)
    levels = json.loads(Path(levels_path).read_text())
    assert hashlib.sha256(source).hexdigest() == levels["input_sha256"]
    config = Settings.model_validate(data["config"])
    calendar, checks = validate_input(data)
    planned = {row["symbol"]: row["days"] for row in levels["rows"]}
    results = []
    global_start = min(row["day"] for rows in planned.values() for row in rows)
    for item, quality in zip(data["etfs"], checks):
        instrument = dict(item["instrument"])
        symbol = instrument["symbol"]
        overrides = []
        if symbol == "sh560390":
            instrument["settlement"] = 1
            overrides.append("560390 跟踪境内A股电网设备，回测按T+1；原列表跨境/T+0分类有误")
        if quality["errors"] or not planned[symbol]:
            results.append({"symbol": symbol, "name": instrument["name"], "status": "unavailable", "quality": quality})
            continue
        raw = {bar["day"]: bar for bar in item["raw"]}
        bars = [{
            "day": bar["day"],
            **{key: float(bar[key]) for key in ("open", "high", "low", "close")},
            # Opening raw/qfq ratio is observable at the open, not derived from
            # today's close. Qfq units represent reinvested adjustment equivalents.
            "factor": float(raw[bar["day"]]["open"]) / float(bar["open"]),
        } for bar in item["qfq"] if bar["day"] >= global_start]
        runs = {}
        for mode in ("legacy", "current"):
            plans = {}
            for row in planned[symbol]:
                pa = dict(row[mode])
                assert pa["as_of"] == row["known_through"] < row["day"]
                previous = calendar[calendar.index(row["day"]) - 1]
                pa["data_gap"] = row["known_through"] != previous
                plans[row["day"]] = pa
            for path in ("OHLC", "OLHC"):
                runs[f"{mode}_{path}"] = simulate(bars, plans, instrument, config, path)
            if mode == "legacy":
                runs["buy_hold"] = simulate(bars, plans, instrument, config, benchmark=True)
        results.append({
            "symbol": symbol, "name": instrument["name"], "status": "evaluated",
            "eligible_start": planned[symbol][0]["day"], "eligible_days": len(planned[symbol]),
            "settlement": instrument["settlement"], "overrides": overrides,
            "quality": quality, "runs": runs,
        })
        print(symbol, instrument["name"], "current", round(runs["current_OHLC"]["return_pct"], 3), flush=True)
    hashes = {path: hashlib.sha256(Path(path).read_bytes()).hexdigest() for path in (
        "web/src/price-action/engine.js", "web/src/price-action/direction-context.js",
        "web/src/price-action/strategy.js", "scripts/backtest_levels.mjs", "scripts/backtest_etfs.py",
    )}
    report = {
        "as_of": data["as_of"], "snapshot_at": data["exported_at"],
        "input_sha256": levels["input_sha256"], "code_sha256": hashes,
        "config": data["config"], "global_start": global_start,
        "calendar_days": [day for day in calendar if day >= global_start], "results": results,
        "model": {
            "capital_per_etf": float(config.initial_cash), "allocation": "100% independent capital, no pyramiding",
            "paths": ["OHLC", "OLHC"], "no_same_day_reentry": True,
            "price_basis": "qfq equivalent units; opening raw/qfq factor for lot/tick conversion",
            "execution": "conditional fills on first touch, tick alignment and RR recheck; no added slippage",
            "excluded_live_controls": ["intraday quote confirmations", "depth/partial fills", "intraday T cycles", "portfolio position/exposure caps"],
            "unliquidated_end": "open holdings marked at last close; not included in closed-trade win rate",
        },
    }
    Path(output_path).write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("input")
    parser.add_argument("levels")
    parser.add_argument("output")
    args = parser.parse_args()
    run(args.input, args.levels, args.output)
