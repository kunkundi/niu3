"""Read-only daily profit attribution from share and trade ledgers, in integer yuan units."""

import json
from datetime import date

from app.core.types import Quote, iso, units, yuan
from app.dashboard.quote_display import quote_display_state
from app.market_data.intraday import expected_day
from app.storage.db import instruments


def previous_close(conn, symbol, day, quote, has_action):
    # Corporate-action days require the actual unadjusted prior close. A quote's
    # previous_close can be an ex-dividend / ex-split reference price instead.
    row = conn.execute(
        "SELECT payload FROM bars WHERE symbol=? AND adjustment='raw' AND day=?", (symbol, day)
    ).fetchone()
    if row:
        try:
            price = units(json.loads(row[0])["close"])
            if price > 0:
                return price
        except (ValueError, TypeError, ArithmeticError, KeyError):
            pass
    row = conn.execute(
        "SELECT payload FROM quotes WHERE symbol=? AND at>=? AND at<=? ORDER BY at DESC,id DESC LIMIT 1",
        (symbol, day + "T14:58:30+08:00", day + "T23:59:59+08:00"),
    ).fetchone()
    if row:
        price = Quote(**json.loads(row[0])).last
        if price > 0:
            return price
    if not has_action and quote and quote.previous_close > 0:
        return quote.previous_close
    return None


def daily_returns(conn, calendar, now, account):
    day = expected_day(calendar, now)
    result = {"day": day, "is_today": day == iso(now)[:10], "pnl": None, "closed_pnl": None, "warning": ""}
    for position in account["positions"]:
        position.update(daily_pnl=None, daily_pnl_warning="收益数据待齐")
    try:
        prior_day = calendar.previous(date.fromisoformat(day)) if day else None
    except ValueError:
        prior_day = None
    if not prior_day:
        result["warning"] = "交易日历待核验"
        return result
    start = day + "T00:00:00+08:00"
    end = min(iso(now), day + "T23:59:59+08:00")
    shares = {
        row["symbol"]: dict(row)
        for row in conn.execute(
            "SELECT symbol,SUM(CASE WHEN at<? THEN delta ELSE 0 END) opening,SUM(delta) closing "
            "FROM position_ledger WHERE at<=? GROUP BY symbol",
            (start, end),
        )
    }
    flows = dict(conn.execute(
        "SELECT symbol,SUM(CASE WHEN side='SELL' THEN gross-fee ELSE -gross-fee END) "
        "FROM fills WHERE at>=? AND at<=? GROUP BY symbol", (start, end)
    ))
    actions = {}
    for row in conn.execute("SELECT * FROM actions WHERE ex_day=?", (day,)):
        actions.setdefault(row["symbol"], []).append(dict(row))
    universe = instruments(conn)
    holdings = {p["symbol"]: p for p in account["positions"]}
    totals, closed, missing, delayed = 0, 0, 0, False
    symbols = set(shares) | set(flows) | set(actions) | set(holdings)
    for symbol in sorted(symbols):
        opening = shares.get(symbol, {}).get("opening", 0)
        closing = shares.get(symbol, {}).get("closing", 0)
        events = actions.get(symbol, [])
        dividend = sum(a["entitlement"] for a in events if a["kind"] == "dividend"
                       and a["verified"] and a["status"] in ("entitled", "paid"))
        if not (opening or closing or symbol in flows or dividend or symbol in holdings):
            continue
        row = conn.execute(
            "SELECT payload FROM quotes WHERE symbol=? AND at>=? AND at<=? "
            "ORDER BY at DESC,id DESC LIMIT 1",
            (symbol, day + "T09:30:00+08:00", end),
        ).fetchone()
        quote = Quote(**json.loads(row[0])) if row else None
        baseline = previous_close(conn, symbol, prior_day, quote, bool(events)) if opening else 0
        warning = ""
        if opening < 0 or closing < 0 or (
            result["is_today"] and closing != holdings.get(symbol, {}).get("quantity", 0)
        ):
            warning = "持仓账本待核验"
        elif symbol in universe and universe[symbol].accounting_block:
            warning = universe[symbol].accounting_block
        elif (opening or dividend) and any(
            not a["verified"] or a["status"] not in ("entitled", "paid", "applied") for a in events
        ):
            warning = "分红／折算待核算"
        elif baseline is None:
            warning = "昨收数据待齐"
        elif closing and (not quote or quote.last <= 0):
            warning = "当日行情待更新"
        pnl = None
        if not warning:
            # Closing value + net sale/buy cash flows + newly accrued dividends
            # minus opening value. This also covers partial sales and round trips.
            pnl = closing * (quote.last if quote else 0) + flows.get(symbol, 0) + dividend - opening * baseline
            totals += pnl
            if symbol not in holdings:
                closed += pnl
            if closing and quote_display_state(quote, calendar, now)["warning"]:
                warning = "行情待更新"
                delayed = True
        else:
            missing += 1
        if symbol in holdings:
            holdings[symbol].update(daily_pnl=yuan(pnl) if pnl is not None else None, daily_pnl_warning=warning)
    result.update(
        pnl=None if missing else yuan(totals),
        closed_pnl=None if missing else yuan(closed),
        warning=f"{missing} 只标的收益数据待齐" if missing else "部分行情待更新" if delayed else "",
    )
    return result
