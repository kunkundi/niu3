"""Display-only ETF returns, aligned to exchange sessions and adjustment basis."""

import json
from bisect import bisect_left
from datetime import time

from app.core.types import TZ, dec, dt

WINDOWS = (5, 10, 20)
METRICS = ("change_pct", "low_change_pct", "high_change_pct", *(f"return{n}" for n in WINDOWS))


def load_histories(conn, symbols, through):
    if not symbols:
        return {}
    result = {}
    for row in conn.execute(
        "SELECT symbol,adjustment,payload FROM (SELECT symbol,adjustment,payload,"
        "ROW_NUMBER() OVER (PARTITION BY symbol,adjustment ORDER BY day DESC) n "
        f"FROM bars WHERE symbol IN ({','.join('?' for _ in symbols)}) AND day<=?) "
        "WHERE (adjustment='qfq' AND n<=21) OR (adjustment='raw' AND n<=1) ORDER BY symbol,n DESC",
        (*symbols, through or ""),
    ):
        bar = json.loads(row["payload"])
        result.setdefault(row["symbol"], {}).setdefault(row["adjustment"], {})[bar["day"]] = bar
    return result


def positive(value):
    try:
        number = dec(value)
        return number if number > 0 else None
    except (ValueError, TypeError, ArithmeticError):
        return None


def calculate_performance(quote, history, calendar, now, complete_day):
    result = {"as_of": None, **dict.fromkeys(METRICS), "reasons": {}}
    qfq = {d: b for d, b in history.get("qfq", {}).items() if complete_day and d <= complete_day}
    raw = {d: b for d, b in history.get("raw", {}).items() if complete_day and d <= complete_day}
    local = now.astimezone(TZ)
    quote_time = dt(quote.at).astimezone(TZ) if quote else None
    valid_quote = bool(
        quote
        and quote_time <= local
        and quote_time.time() >= time(9, 30)
        and calendar.is_open(quote_time.date())
        and quote.last > 0
    )
    # A valid stale quote keeps its own date; never combine it with a later day's close.
    day = quote_time.date().isoformat() if valid_quote else max(qfq, default=None)
    if not day:
        result["reasons"] = dict.fromkeys(METRICS, "暂无有效行情或完整日 K")
        return result
    result["as_of"] = day
    index = bisect_left(calendar.days, day)
    if index == len(calendar.days) or calendar.days[index] != day:
        result["reasons"] = dict.fromkeys(METRICS, "交易日历未覆盖指标日期")
        return result
    previous_day = calendar.days[index - 1] if index else None
    previous = positive(qfq.get(previous_day, {}).get("close"))
    closing = qfq.get(day, {}) if complete_day and day <= complete_day else {}
    raw_closing = raw.get(day, {})
    # Completed daily bars may fill missing display fields only after the close.
    completed = bool(closing and (not valid_quote or quote_time.time() >= time(15)))
    if completed and valid_quote:
        completed = positive(raw_closing.get("close")) == dec(quote.last) / 1_000_000
    closing = closing if completed else {}

    end = positive(closing.get("close"))
    change = None
    if valid_quote and quote.previous_close > 0:
        change = dec(quote.last) / quote.previous_close - 1
        # Bridge the live raw quote to the previous completed day's qfq price basis.
        if end is None and previous is not None:
            end = previous * (1 + change)
    elif end is not None and previous is not None:
        change = end / previous - 1
    if change is not None:
        result["change_pct"] = str(change)
    else:
        result["reasons"]["change_pct"] = "昨收或同日完整日 K 待齐"

    bounds = None
    if valid_quote and quote.previous_close > 0:
        if 0 < quote.low <= quote.last <= quote.high:
            bounds = (dec(quote.low), dec(quote.high), dec(quote.previous_close))
        elif completed:
            low, high = positive(raw_closing.get("low")), positive(raw_closing.get("high"))
            if low is not None and high is not None and low <= dec(quote.last) / 1_000_000 <= high:
                bounds = (low, high, dec(quote.previous_close) / 1_000_000)
    if bounds is None and end is not None and previous is not None and completed:
        low, high = positive(closing.get("low")), positive(closing.get("high"))
        if low is not None and high is not None and low <= end <= high:
            bounds = (low, high, previous)
    for field, value in zip(("low_change_pct", "high_change_pct"), bounds[:2] if bounds else (None, None)):
        if value is None:
            result["reasons"][field] = "当日最高／最低价或昨收待齐"
        else:
            result[field] = str(value / bounds[2] - 1)

    for window in WINDOWS:
        field = f"return{window}"
        dates = calendar.days[max(0, index - window) : index]
        closes = [positive(qfq.get(d, {}).get("close")) for d in dates]
        if len(dates) != window or any(value is None for value in closes):
            result["reasons"][field] = f"不足 {window} 个连续交易日的前复权收盘数据"
        elif end is None:
            result["reasons"][field] = "指标日期的完整日 K 或盘中昨收待齐"
        else:
            result[field] = str(end / closes[0] - 1)
    return result
