"""Background intraday targets, separate from immutable daily plans."""

import json

from app.core.types import Bar, dec, iso
from app.storage.db import tracked_instruments, latest_quote, settings
from app.strategies.price_action import build_plan
from app.strategies.profit import cooling_symbols, latest_profit_sale, reentry_setup


def calculate_targets(db, as_of, now):
    with db.connect() as conn:
        conn.execute("BEGIN")
        config_id, config = settings(conn)
        universe = tracked_instruments(conn)
        histories = {}
        symbols = list(universe)
        for row in conn.execute(
            "SELECT symbol,payload FROM (SELECT symbol,payload,ROW_NUMBER() OVER "
            f"(PARTITION BY symbol ORDER BY day DESC) n FROM bars WHERE symbol IN ({','.join('?' for _ in symbols)}) AND adjustment='qfq' AND day<=?) "
            "WHERE n<=? ORDER BY symbol,n DESC",
            (*symbols, as_of, config.history_bars),
        ):
            if row["symbol"] in universe:
                histories.setdefault(row["symbol"], []).append(Bar(**json.loads(row["payload"])))
        held = {row[0] for row in conn.execute("SELECT DISTINCT symbol FROM lots WHERE quantity>0")}
        cooldown = cooling_symbols(conn, now.date().isoformat(), config)
        prices, quote_times, factors, quotes = {}, {}, {}, {}
        for symbol in universe:
            history = histories.get(symbol, [])
            latest = latest_quote(conn, symbol)
            if not history or history[-1].day != as_of or not latest:
                continue
            quote = latest[1]
            if (
                quote.fresh(now, config.quote_max_age)
                and quote.status == "trading"
                and min(quote.last, quote.previous_close) > 0
            ):
                prices[symbol] = dec(history[-1].close) * quote.last / quote.previous_close
                quote_times[symbol] = quote.at
                factors[symbol] = dec(quote.previous_close) / 1_000_000 / dec(history[-1].close)
                quotes[symbol] = quote
    plan = build_plan(list(universe.values()), histories, held, as_of, "", config, cooldown, prices)
    for row in plan["rows"]:
        row["evaluated_quote_at"] = quote_times.get(row["symbol"])
        if row.get("pa", {}).get("ready") and row["symbol"] in factors:
            from app.strategies.price_action import raw_levels

            row["pa"]["raw"] = raw_levels(row["pa"], factors[row["symbol"]], universe[row["symbol"]].tick)
    from app.strategies.price_action import select_targets

    with db.connect() as conn:
        for row in plan["rows"]:
            symbol = row["symbol"]
            if symbol in cooldown:
                row["reasons"].append("当日结构止损冷却，禁止再入场")
            if (symbol in cooldown or symbol not in quotes
                    or not universe[symbol].tradable or not row["representative"]):
                continue
            sale = latest_profit_sale(conn, symbol, now)
            if not sale or (symbol in held and not sale.get("pending")):
                continue
            pa, reason = reentry_setup(conn, universe[symbol], row.get("pa", {}), sale,
                                      quotes[symbol], config, now, factors[symbol])
            # A profit sale consumes the old setup; require a new intraday breakout.
            row["eligible"] = bool(pa)
            row["rank"] = None
            row["reasons"] = [reason]
            if pa:
                row["pa"] = pa
            elif row["pa"].get("action") != "exit":
                row["pa"]["action"] = "hold"
        plan["targets"] = select_targets(plan["rows"], held, config)
        plan["rows"].sort(key=lambda r: (r["rank"] or 100000, r["symbol"]))
    representatives = [
        row for row in plan["rows"] if row["representative"] and universe[row["symbol"]].tradable
    ]
    missing = sum(row["symbol"] not in prices for row in representatives)
    return {
        **plan,
        "id": f"live:{config_id}:{iso(now)}",
        "mode": "live",
        "created_at": iso(now),
        "config_id": config_id,
        "quote_count": len(representatives) - missing,
        "representative_count": len(representatives),
        "missing_quotes": missing,
        "refresh_seconds": config.market_interval,
        "message": "盘中目标自动重算，连续确认后按盘中交易规则执行。",
    }
