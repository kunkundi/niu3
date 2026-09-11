"""Source-reported, unadjusted five-minute OHLCV, timestamped at interval END.

Never derive highs/lows from display points or sparse polling quotes. A five-second
publication guard excludes the currently forming candle, including future-labelled
Tencent bars. Each successful snapshot uses one source, never a mixed-source series.
"""

from datetime import datetime, timedelta

from app.core.types import TZ, dec, dt, iso, units
from app.market_data.providers import DataError
from app.storage.db import dump, get_state, set_state


def bar_number(stamp):
    minute = stamp.hour * 60 + stamp.minute
    if stamp.second or minute % 5:
        return None
    if 575 <= minute <= 690:
        return (minute - 570) // 5
    if 785 <= minute <= 900:
        return 24 + (minute - 780) // 5
    return None


def normalize(rows, symbol, now, source):
    result, seen = [], set()
    if not rows or len(rows) > 1000:
        raise DataError("5 分钟 K 数据为空或数量异常")
    try:
        for row in rows:
            stamp = dt(row["at"])
            # Discard unfinished/future data before inspecting its OHLC fields.
            if stamp > now - timedelta(seconds=5):
                continue
            if bar_number(stamp) is None or stamp.date() > now.date():
                raise ValueError("invalid bar interval")
            if row["at"] in seen:
                raise ValueError("duplicate bar")
            seen.add(row["at"])
            values = {key: units(row[key]) for key in ("open", "high", "low", "close")}
            if (
                not 0
                < values["low"]
                <= min(values["open"], values["close"])
                <= max(values["open"], values["close"])
                <= values["high"]
            ):
                raise ValueError("invalid OHLC")
            volume = dec(row["volume"])
            if volume < 0 or volume != int(volume):
                raise ValueError("invalid volume")
            amount = units(row["amount"]) if row.get("amount") is not None else None
            if amount is not None and amount < 0:
                raise ValueError("invalid amount")
            result.append({"at": iso(stamp), **values, "volume": int(volume), "amount": amount})
        result.sort(key=lambda b: b["at"])
        if not result or result[-1]["at"][:10] != now.date().isoformat():
            raise ValueError("current trading day missing")
        if (now - dt(result[-1]["at"])).total_seconds() > 360:
            raise ValueError("latest completed bar missing")
    except (ValueError, TypeError, KeyError, ArithmeticError) as exc:
        raise DataError("5 分钟 K 日期、时效或 OHLCV 校验失败") from exc
    return {
        "symbol": symbol,
        "source": source,
        "timeframe": "5m",
        "as_of": result[-1]["at"],
        "fetched_at": iso(now),
        "bars": result,
    }


def tencent_bars(payload, symbol, now):
    try:
        data = payload["data"][symbol]
        if payload.get("code") != 0 or data["qt"][symbol][2] != symbol[2:]:
            raise ValueError("identity mismatch")
        rows = [
            {
                "at": iso(datetime.strptime(r[0], "%Y%m%d%H%M").replace(tzinfo=TZ)),
                "open": r[1],
                "close": r[2],
                "high": r[3],
                "low": r[4],
                "volume": dec(r[5]) * 100,
            }
            for r in data["m5"]
            if datetime.strptime(r[0], "%Y%m%d%H%M").replace(tzinfo=TZ) <= now - timedelta(seconds=5)
        ]
        return normalize(rows, symbol, now, "tencent")
    except (ValueError, TypeError, KeyError, IndexError, ArithmeticError) as exc:
        raise DataError("腾讯 5 分钟 K 返回格式异常") from exc


def sina_bars(payload, symbol, now):
    try:
        rows = [
            {**r, "at": iso(datetime.strptime(r["day"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=TZ))}
            for r in payload
        ]
        return normalize(rows, symbol, now, "sina")
    except (ValueError, TypeError, KeyError) as exc:
        raise DataError("新浪 5 分钟 K 返回格式异常") from exc


def fetch_five_minute(provider, symbol, now):
    errors = []
    for source in ("tencent", "sina"):
        try:
            if source == "tencent":
                response = provider.get(
                    "https://ifzq.gtimg.cn/appstock/app/kline/mkline", {"param": f"{symbol},m5,,240"}
                ).json()
                return tencent_bars(response, symbol, now)
            response = provider.get(
                "https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService.getKLineData",
                {"symbol": symbol, "scale": 5, "ma": "no", "datalen": 240},
            ).json()
            return sina_bars(response, symbol, now)
        except (DataError, ValueError, TypeError) as exc:
            errors.append(f"{source}: {exc}")
    raise DataError("5 分钟 K 暂不可用；" + "；".join(errors))


def save_five_minute(conn, result):
    key = f"minute5:{result['symbol']}"
    old = get_state(conn, key, {})
    if old.get("as_of", "") > result["as_of"] or old.get("fetched_at", "") > result["fetched_at"]:
        return
    conn.executemany(
        "INSERT INTO minute_bars VALUES(?,?,?,?,?) ON CONFLICT(symbol,at,source) "
        "DO UPDATE SET fetched_at=excluded.fetched_at,payload=excluded.payload",
        [
            (result["symbol"], bar["at"], result["source"], result["fetched_at"], dump(bar))
            for bar in result["bars"]
        ],
    )
    set_state(conn, key, result)
