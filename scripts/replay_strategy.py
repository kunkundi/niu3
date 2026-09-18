"""Offline quote replay through the production planner, risk engine and matcher.

Minute publication time is assumed, NOT reconstructed: source bars have overwritten
fetch timestamps. Results are conditional simulations, never imported into an account.
"""

import argparse
import hashlib
import json
import sqlite3
import sys
from collections import Counter, defaultdict
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path

from app.automation.targets import calculate_targets
from app.core.calendar import Calendar
from app.core.config import ROOT, Settings
from app.core.types import Instrument, Quote, dt, iso, units
from app.storage.db import Database, put_instrument, put_quote, set_state
from app.trading.account import reconcile, snapshot
from app.trading.engine import Engine
from app.trading.intraday import IntradayTrader


class ReplayDatabase(Database):
    """Use the real schema in a new private memory DB, without production disk IO."""

    def __init__(self):
        self.connection = sqlite3.connect(":memory:", isolation_level=None)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys=ON")
        super().__init__(":memory:")

    @contextmanager
    def connect(self):
        try:
            yield self.connection
        finally:
            if self.connection.in_transaction:
                self.connection.rollback()


def input_quality(data, symbols, days):
    minute_counts = defaultdict(Counter)
    quote_counts = defaultdict(Counter)
    for row in data["minutes"]:
        minute_counts[row["symbol"]][row["at"][:10]] += 1
    for row in data["quotes"]:
        quote_counts[row["symbol"]][row["at"][:10]] += 1
    return [{"symbol": s, "minute_counts": {d: minute_counts[s][d] for d in days},
             "quote_counts": {d: quote_counts[s][d] for d in days}} for s in sorted(symbols)]


def run(data, symbols, delay=5, phase=0, start=None, end=None):
    start, end = start or data["start"], end or data["end"]
    if delay < 5 or not 0 <= phase < 60:
        raise ValueError("Publication guard >=5 seconds; target phase in [0,60).")
    calendar = Calendar()
    days = [d for d in calendar.days if start <= d <= end]
    if any(a["symbol"] in symbols for a in data["actions"]):
        raise ValueError("Corporate action in replay window requires an explicit historical event feed")
    config = Settings.model_validate(data["config"])
    db = ReplayDatabase()
    engine = Engine(db, calendar)
    trader = IntradayTrader(engine)
    with db.transaction() as conn:
        conn.execute("INSERT INTO configs(at,payload) VALUES(?,?)", (start, config.model_dump_json()))
        if config.initial_cash != 100000:
            raise ValueError("Replay fixture currently requires the account's 100000 initial capital")
        for instrument in data["instruments"]:
            if instrument["symbol"] in symbols:
                put_instrument(conn, Instrument(**instrument))
        conn.executemany("INSERT INTO bars VALUES(?,?,?,?,?,?)", [
            (r["symbol"], r["adjustment"], r["day"], r["payload"], r["source"], r["fetched_at"])
            for r in data["bars"] if r["symbol"] in symbols
        ])
        set_state(conn, "buy_ready", True)
    quotes = sorted((max(dt(p["at"]), dt(p["fetched_at"])), r["id"], p)
                    for r in data["quotes"] if r["symbol"] in symbols
                    for p in [json.loads(r["payload"])])
    # Choose one provider per symbol/day. Never merge two providers' minute series.
    available_sources = defaultdict(set)
    for r in data["minutes"]:
        available_sources[(r["symbol"], r["at"][:10])].add(r["source"])
    sources = {key: "tencent" if "tencent" in values else sorted(values)[0]
               for key, values in available_sources.items()}
    minutes = sorted((dt(r["at"]) + timedelta(seconds=delay), r["symbol"], r["source"],
                      json.loads(r["payload"])) for r in data["minutes"]
                     if r["symbol"] in symbols and r["source"] == sources[(r["symbol"], r["at"][:10])])
    qi = mi = 0
    minute_history = defaultdict(list)
    equity, peak, max_dd = [], units(config.initial_cash), 0.0
    evaluated_steps = 0
    for day in days:
        beginning = dt(day + "T09:30:00+08:00")
        ending = dt(day + "T15:00:00+08:00")
        minute_history.clear()
        with db.transaction() as conn:
            for symbol in symbols:
                set_state(conn, f"actions:{symbol}", {"at": iso(beginning)})
        now = beginning
        last_target = None
        while now <= ending:
            with db.transaction() as conn:
                while qi < len(quotes) and quotes[qi][0] <= now:
                    put_quote(conn, Quote(**quotes[qi][2]))
                    qi += 1
                while mi < len(minutes) and minutes[mi][0] <= now:
                    _, symbol, source, bar = minutes[mi]
                    if bar["at"][:10] == day:
                        minute_history[symbol].append(bar)
                    mi += 1
                # Model a successful refresh every 30 seconds, using only published bars.
                if now.second % 30 == 0:
                    for symbol, bars in minute_history.items():
                        set_state(conn, f"minute5:{symbol}", {"symbol": symbol,
                                  "source": sources[(symbol, day)], "fetched_at": iso(now),
                                  "as_of": bars[-1]["at"], "bars": bars[-13:]})
            if calendar.session(now):
                elapsed = int((now - beginning).total_seconds())
                if elapsed >= phase and (last_target is None or (now - last_target).total_seconds() >= 60):
                    plan = calculate_targets(db, calendar.previous(now.date()), now)
                    with db.transaction() as conn:
                        set_state(conn, "live_targets", plan)
                    last_target = now
                engine.expire(now)
                engine.risk_check(now)
                trader.tick(now)
                engine.match(now)
                evaluated_steps += 1
            if now.second == 0:
                with db.connect() as conn:
                    account = snapshot(conn, now)
                if not account["stale"]:
                    nav = account["nav_units"]
                    peak = max(peak, nav)
                    max_dd = max(max_dd, 1 - nav / peak)
                    equity.append({"at": iso(now), "nav": nav / 1e6})
            now += timedelta(seconds=5)
        print(day, "nav", round(account["nav_units"] / 1e6, 2), file=sys.stderr, flush=True)
    with db.connect() as conn:
        errors = reconcile(conn)
        if errors:
            raise AssertionError(errors)
        fills = [dict(r) for r in conn.execute("SELECT f.*,o.reason,o.kind FROM fills f JOIN orders o ON o.id=f.order_id")]
        orders = [dict(r) for r in conn.execute("SELECT * FROM orders")]
        account = snapshot(conn, ending)
    db.connection.close()
    return {
        "start": start, "end": end, "symbols": sorted(symbols), "publication_delay": delay,
        "target_phase": phase, "quality": input_quality(data, symbols, days), "steps": evaluated_steps,
        "return_pct": (account["nav_units"] / units(config.initial_cash) - 1) * 100,
        "max_drawdown_pct": max_dd * 100, "ending_nav": account["nav_units"] / 1e6,
        "fees": sum(f["fee"] for f in fills) / 1e6, "fill_count": len(fills), "order_count": len(orders),
        "realized": sum(f["realized"] for f in fills) / 1e6,
        "positions": account["positions"], "fills": fills, "orders": orders, "equity": equity,
        "ledger_errors": errors,
        "limitations": ["Current watchlist: selection/survivorship bias, not the historical full universe",
                        "Minute and daily prices may contain later provider revisions",
                        "Assumed minute publication/refresh and historical company-action readiness",
                        "Actual retained bid/ask quotes, simulated execution, no exchange queue model",
                        "Minute drawdown sampled on fresh marks; stale intervals excluded",
                        "Initial cash account; this is not a replay of the restated production account"],
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input")
    parser.add_argument("output")
    parser.add_argument("--symbols", required=True)
    parser.add_argument("--delay", type=int, default=5)
    parser.add_argument("--phase", type=int, default=0)
    parser.add_argument("--start")
    parser.add_argument("--end")
    args = parser.parse_args()
    raw = Path(args.input).read_bytes()
    result = run(json.loads(raw), set(args.symbols.split(",")), args.delay, args.phase, args.start, args.end)
    result["input_sha256"] = hashlib.sha256(raw).hexdigest()
    result["code_sha256"] = {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in (
        "app/strategies/profit.py", "app/strategies/price_action.py", "app/trading/engine.py",
        "web/src/price-action/strategy.js", "web/src/price-action/entry-quality.js", "scripts/replay_strategy.py")}
    Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps({k: result[k] for k in ("return_pct", "max_drawdown_pct", "fill_count", "fees")}))
