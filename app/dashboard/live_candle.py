"""Read-only current-session candle on the preceding completed qfq price basis."""

import json
from datetime import datetime, time, timedelta

from app.core.types import TZ, dec, dt, iso, yuan
from app.storage.db import latest_quote


def live_candle_payload(conn, symbol, calendar, now):
    local = now.astimezone(TZ)
    result = {"symbol": symbol, "server_at": iso(local), "bar": None, "message": ""}
    if not calendar.is_open(local.date()) or local.time() < time(9, 30):
        return result
    latest = latest_quote(conn, symbol)
    if not latest:
        return {**result, "message": "等待当日行情"}
    quote = latest[1]
    stamp = dt(quote.at)
    if stamp.date() != local.date() or stamp > local or stamp.time() < time(9, 30):
        return {**result, "message": "等待当日行情"}
    if not (0 < quote.low <= min(quote.open, quote.last) <= max(quote.open, quote.last) <= quote.high
            and quote.previous_close > 0 and quote.volume > 0):
        return {**result, "message": "当日开高低收待齐"}
    previous_day = calendar.previous(local.date())
    previous = conn.execute(
        "SELECT payload FROM bars WHERE symbol=? AND adjustment='qfq' AND day=?",
        (symbol, previous_day),
    ).fetchone()
    if not previous:
        return {**result, "message": "等待上一交易日日 K，以对齐价格口径"}
    try:
        close = dec(json.loads(previous[0])["close"])
    except (ValueError, TypeError, ArithmeticError, KeyError):
        return {**result, "message": "上一交易日收盘价待核验"}
    if close <= 0:
        return {**result, "message": "上一交易日收盘价待核验"}
    # Same bridge as portfolio display returns: qfq yesterday × raw price / ex-reference close.
    factor = close / quote.previous_close
    cutoff = local
    if time(11, 30) <= local.time() < time(13):
        cutoff = datetime.combine(local.date(), time(11, 30), TZ)
    elif local.time() >= time(15):
        cutoff = datetime.combine(local.date(), time(15), TZ)
    result["bar"] = {
        "day": local.date().isoformat(),
        "open": float(dec(quote.open) * factor),
        "high": float(dec(quote.high) * factor),
        "low": float(dec(quote.low) * factor),
        "close": float(dec(quote.last) * factor),
        "volume": quote.volume,
        "amount": yuan(quote.amount) if quote.amount >= 0 else None,
        "source": quote.source,
        "at": quote.at,
        "basis_day": previous_day,
        "basis_close": float(close),
        "stale": stamp < cutoff - timedelta(seconds=90),
    }
    return result
