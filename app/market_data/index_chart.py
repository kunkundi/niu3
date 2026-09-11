"""Display-only Shanghai Composite candles; never part of the ETF trading universe."""

import time
from datetime import date
from threading import Lock

from app.core.types import dec, iso
from app.market_data.providers import DataError, PublicProvider
from app.market_data.sources import SOURCE_PRIORITY, SOURCE_LABELS

SYMBOL = "sh000001"


def index_bars(payload, as_of):
    data = payload.get("data", {}).get(SYMBOL)
    if not data or not isinstance(data.get("day"), list):
        raise DataError("上证指数日 K 数据缺失")
    result, previous = [], ""
    for row in data["day"]:
        if len(row) < 6:
            raise DataError("上证指数日 K 字段不完整")
        try:
            day = date.fromisoformat(row[0]).isoformat()
        except (ValueError, TypeError) as exc:
            raise DataError("上证指数日 K 日期异常") from exc
        if day > as_of:
            continue
        try:
            o, c, h, low, volume = map(dec, row[1:6])
        except (ValueError, TypeError, ArithmeticError) as exc:
            raise DataError("上证指数日 K 价格异常") from exc
        if (
            day <= previous
            or not all(value.is_finite() for value in (o, c, h, low, volume))
            or min(o, c, h, low) <= 0
            or not low <= min(o, c) <= max(o, c) <= h
            or volume < 0
        ):
            raise DataError("上证指数日 K 日期或价格异常")
        result.append(
            {
                "day": day,
                "open": str(o),
                "close": str(c),
                "high": str(h),
                "low": str(low),
                "volume": float(volume),
                "amount": None,
            }
        )
        previous = day
    if not result:
        raise DataError("暂无已完成的上证指数日 K")
    return result[-250:]


def fetch_index_bars(provider, source, as_of):
    if source == "eastmoney":
        data = (
            provider.get(
                "https://push2his.eastmoney.com/api/qt/stock/kline/get",
                {
                    "secid": "1.000001",
                    "klt": 101,
                    "fqt": 0,
                    "beg": "19900101",
                    "end": as_of.replace("-", ""),
                    "lmt": 260,
                    "fields1": "f1,f2,f3,f4,f5,f6",
                    "fields2": "f51,f52,f53,f54,f55,f56,f57",
                },
            )
            .json()
            .get("data")
            or {}
        )
        if str(data.get("code")) != "000001" or int(data.get("market", -1)) != 1:
            raise DataError("上证指数身份不匹配")
        values = [line.split(",") for line in data.get("klines", [])]
        if any(len(row) < 7 for row in values):
            raise DataError("上证指数日 K 字段不完整")
        bars = index_bars({"data": {SYMBOL: {"day": [row[:6] for row in values]}}}, as_of)
        amounts = {row[0]: row[6] for row in values}
        for bar in bars:
            amount = dec(amounts[bar["day"]])
            if amount < 0:
                raise DataError("上证指数成交额异常")
            bar["amount"] = float(amount)
        return bars
    if source == "tencent":
        return index_bars(
            provider.get(
                "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get", {"param": f"{SYMBOL},day,,,260,"}
            ).json(),
            as_of,
        )
    data = provider.get(
        "https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService.getKLineData",
        {"symbol": SYMBOL, "scale": 240, "ma": "no", "datalen": 260},
    ).json()
    if not isinstance(data, list):
        raise DataError("上证指数日 K 数据缺失")
    rows = [[r["day"], r["open"], r["close"], r["high"], r["low"], str(dec(r["volume"]) / 100)] for r in data]
    return index_bars({"data": {SYMBOL: {"day": rows}}}, as_of)


class IndexChartService:
    def __init__(self, provider=None, monotonic=time.monotonic):
        self.provider = provider
        self.owns_provider = provider is None
        self.monotonic = monotonic
        self.lock = Lock()
        self.cached = None
        self.expires = 0
        self.target = None

    def close(self):
        if self.owns_provider and self.provider:
            self.provider.close()

    def get(self, as_of, now):
        with self.lock:
            if self.cached and self.expires > self.monotonic() and as_of == self.target:
                return self.cached
            previous = self.cached or {}
            bars, warning = previous.get("bars", []), ""
            fetched_at = previous.get("fetched_at")
            source = previous.get("source", "")
            try:
                if not as_of:
                    raise DataError("交易日历未就绪")
                if self.provider is None:
                    self.provider = PublicProvider()
                fresh, selected = None, None
                for candidate in SOURCE_PRIORITY:
                    # THS's unqualified hs_000001 can identify a Shenzhen stock.
                    if candidate == "ths":
                        continue
                    try:
                        candidate_bars = fetch_index_bars(self.provider, candidate, as_of)
                        if fresh is None or candidate_bars[-1]["day"] > fresh[-1]["day"]:
                            fresh, selected = candidate_bars, candidate
                        if candidate_bars[-1]["day"] == as_of:
                            break
                    except (DataError, ValueError, TypeError, KeyError, ArithmeticError):
                        continue
                if fresh is None:
                    raise DataError("上证指数数据源暂不可用")
                if bars and fresh[-1]["day"] < bars[-1]["day"]:
                    raise DataError("指数历史日期回退")
                bars, fetched_at, source = fresh, iso(now), SOURCE_LABELS[selected]
                if bars[-1]["day"] != as_of:
                    warning = "上证指数最新完整日 K 尚未同步，暂显示已有历史。"
            except (DataError, ValueError, TypeError, KeyError, ArithmeticError):
                warning = (
                    "上证指数刷新失败，暂显示缓存并自动重试。"
                    if bars
                    else "上证指数日 K 暂不可用，将自动重试。"
                )
            self.cached = {
                "symbol": SYMBOL,
                "name": "上证指数",
                "kind": "index",
                "bars": bars,
                "as_of": bars[-1]["day"] if bars else None,
                "expected_as_of": as_of,
                "adjustment": "none",
                "volume_unit": "手",
                "tick": 0.01,
                "history_count": 250,
                "source": source,
                "fetched_at": fetched_at,
                "stale": bool(warning),
                "warning": warning,
                "refresh_seconds": 60,
            }
            self.target, self.expires = as_of, self.monotonic() + 60
            return self.cached
