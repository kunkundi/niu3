"""Read-only minute samples for the selected execution date, from recorded quotes."""

import json
from datetime import datetime, time, timedelta

from app.core.types import Quote, TZ, dt, iso, yuan
from app.market_data.intraday import polling_schedule, trading_minute


def recorded_intraday(conn, symbol, day, calendar, now):
    start = datetime.combine(day, time.min, TZ)
    end = start + timedelta(days=1)
    samples = {}
    if calendar.is_open(day):
        for row in conn.execute(
            "SELECT payload FROM quotes WHERE symbol=? AND at>=? AND at<? ORDER BY at,id",
            (symbol, iso(start), iso(end)),
        ):
            try:
                quote = Quote(**json.loads(row[0]))
                stamp = dt(quote.at).astimezone(TZ)
                minute = trading_minute(stamp.strftime("%H%M"))
                if (
                    quote.symbol != symbol or stamp.date() != day or stamp > now
                    or minute is None or stamp.time() > time(15)
                    or time(11, 30) < stamp.time() < time(13)
                    or quote.status != "trading" or quote.last <= 0 or quote.previous_close <= 0
                    or not quote.fresh(dt(quote.fetched_at))
                ):
                    continue
                samples[stamp.strftime("%H:%M")] = quote
            except (ValueError, TypeError, KeyError, ArithmeticError):
                continue
    quotes = list(samples.values())
    previous = quotes[-1].previous_close if quotes else None
    # A conflicting previous close cannot silently distort the selected day's scale.
    points = [
        {
            "time": dt(quote.at).strftime("%H:%M"),
            "minute": trading_minute(dt(quote.at).strftime("%H%M")),
            "price": yuan(quote.last),
            "at": quote.at,
        }
        for quote in quotes if quote.previous_close == previous
    ]
    return {
        "symbol": symbol,
        "day": day.isoformat(),
        "points": points,
        "previous_close": yuan(previous) if previous is not None else None,
        "as_of": points[-1]["at"] if points else None,
        "source": "recorded_quotes",
        "polling": polling_schedule(calendar, now),
        "message": "已采集行情 · 缺失时段留空" if points else "该成交日暂无保留的分时行情",
    }
