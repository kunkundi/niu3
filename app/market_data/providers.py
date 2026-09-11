"""Bounded public-data adapters. Malformed data raises instead of becoming a zero price."""

from __future__ import annotations

import re
import json
import time
from collections import OrderedDict
from dataclasses import replace
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from threading import Lock

import httpx
from bs4 import BeautifulSoup

from app.core.types import Bar, Instrument, Quote, TZ, dec, iso, now_cn, symbol_for, units
from app.market_data.sources import SOURCE_PRIORITY
from app.market_data.history import (
    HistoryUpdate,
    FullRefreshRequired,
    request_window,
    merge_history,
    signature,
    validate_series,
)


class DataError(RuntimeError):
    pass


class SourceCooling(DataError):
    def __init__(self, host: str, until: float, status: int | None = None):
        self.host, self.until = host, until
        code = f"HTTP {status}；" if status else ""
        super().__init__(f"{host}：{code}数据源冷却中，稍后自动重试")


def number(value, default=0):
    try:
        return dec(value)
    except (ValueError, ArithmeticError):
        return Decimal(default)


def turnover_ratio(value) -> str | None:
    """Public feeds report percent points; unavailable/invalid is distinct from zero."""
    try:
        rate = dec(value)
        return str(rate / 100) if rate >= 0 else None
    except (ValueError, ArithmeticError, TypeError):
        return None


def optional_number(value, multiplier=1, *, integer=False):
    try:
        result = dec(value) * multiplier
        if result < 0:
            return None
        return int(result) if integer else str(result)
    except (ValueError, ArithmeticError, TypeError):
        return None


def normalized_bars(rows: list[Bar], as_of: str) -> list[Bar]:
    if not rows:
        raise DataError("empty history")
    days = set()
    result = []
    for bar in rows:
        datetime.strptime(bar.day, "%Y-%m-%d")
        if bar.day > as_of:
            continue
        if (
            bar.day in days
            or dec(bar.close) <= 0
            or (bar.amount is not None and dec(bar.amount) < 0)
            or (bar.volume is not None and bar.volume < 0)
        ):
            raise DataError("invalid history")
        days.add(bar.day)
        result.append(bar)
    if not result:
        raise DataError("no completed bars")
    return sorted(result, key=lambda b: b.day)


def jsonp(body: str) -> dict:
    match = re.search(r"^[^(]*\((\{.*\})\)\s*;?\s*$", body, re.S)
    if not match:
        raise DataError("fallback turnover response malformed")
    return json.loads(match[1])


def closing_price(data: dict, symbol: str, as_of: str, now: datetime) -> list | None:
    """Use the quote embedded in Tencent's response only after that session has closed."""
    parts = data.get("qt", {}).get(symbol, [])
    if len(parts) < 36 or parts[2] != symbol[2:]:
        return None
    try:
        at = datetime.strptime(parts[30], "%Y%m%d%H%M%S").replace(tzinfo=TZ)
        if (
            at.date().isoformat() != as_of
            or at.strftime("%H%M") < "1500"
            or at > now
            or now < at.replace(hour=15, minute=30, second=0)
        ):
            return None
        o, c, h, low = (dec(parts[i]) for i in (5, 3, 33, 34))
        volume = optional_number(parts[6])
        if min(o, c, h, low) <= 0 or not low <= min(o, c) <= max(o, c) <= h:
            return None
        return [as_of, str(o), str(c), str(h), str(low), volume]
    except (ValueError, ArithmeticError, TypeError):
        return None


def matching_close(row: list, turnover: list) -> bool:
    # THS reports shares; Tencent rounds cumulative volume to whole lots.
    try:
        return (
            all(dec(row[i]) == dec(turnover[j]) for i, j in ((1, 1), (2, 4), (3, 2), (4, 3)))
            and abs(dec(row[5]) * 100 - dec(turnover[5])) <= 100
            and dec(turnover[6]) > 0
        )
    except (ValueError, ArithmeticError, TypeError):
        return False


def parse_profile(instrument: Instrument, html: str, now: datetime) -> Instrument:
    soup = BeautifulSoup(html, "html.parser")
    pairs = {}
    for row in soup.select("table tr"):
        cells = row.find_all(["th", "td"])
        for i in range(0, len(cells) - 1, 2):
            pairs[cells[i].get_text(strip=True)] = cells[i + 1].get_text(" ", strip=True)
    if instrument.symbol[2:] not in pairs.get("基金代码", ""):
        raise DataError("fund profile identity mismatch")
    fund_type = pairs.get("基金类型", "")
    index = re.sub(r"\s+", "", pairs.get("跟踪标的", ""))
    if index in {"该基金无跟踪标的", "无跟踪标的", "--", "---", "不适用"}:
        index = ""
    # The profile may abbreviate the tracked index (e.g. 有色金属 / 细分化工).
    # Resolve it only against a matching, single-index benchmark from the same
    # verified fund page; do not infer trading rules from the ETF's display name.
    benchmark = re.sub(r"\s+", "", pairs.get("业绩比较基准", ""))
    benchmark = re.sub(r"(?:收益率|增长率|回报率)$", "", benchmark)
    if (
        index
        and index.removesuffix("指数") in benchmark
        and re.fullmatch(r"(?:沪深|中证|上证|深证|国证|创业|科创|中小)[^+＋*×%％]*指数", benchmark)
    ):
        index = benchmark
    full = pairs.get("基金全称", "") + " " + index + " " + fund_type
    if "联接" in full or (
        "交易型开放式" not in full and "交易型货币" not in full and "ETF" not in full.upper()
    ):
        raise DataError("profile is not an ETF")
    category, region = "unknown", "unknown"
    if "货币" in fund_type:
        category, region = "money", "CN"
    elif "债券" in fund_type or re.search("国债|信用债|可转债|政金债", index):
        category, region = "bond", "CN"
    elif re.search("黄金|白银|原油|豆粕|商品|期货", full):
        category, region = "commodity", "CN"
    elif "股票" in fund_type or "QDII" in fund_type:
        category = "equity"
        if re.search(
            "QDII|港股|沪港|深港|沪深港|A\\+H|恒生|香港|纳斯达克|标普|美国|日经|日本|德国|法国|越南|全球|海外|亚太|新兴市场",
            full,
        ):
            region = "OVERSEAS"
        elif re.match("^(沪深|中证|上证|深证|国证|创业|科创|中小)", index):
            region = "CN"
    category = "cross_border" if region == "OVERSEAS" else category
    limit = "0.20" if region == "CN" and category == "equity" and re.search("创业|科创", index) else "0.10"
    return replace(
        instrument,
        name=pairs.get("基金简称") or pairs.get("基金全称") or instrument.name,
        category=category,
        region=region,
        index_id=index,
        listed_on=pairs.get("上市日期", ""),
        verified=category != "unknown" and region != "unknown",
        settlement=1 if category == "equity" else 0,
        limit_ratio=limit,
        source=f"eastmoney:fundf10:{instrument.symbol[2:]}",
        updated_at=iso(now),
    )


def parse_actions(symbol: str, html: str) -> list[dict]:
    def valid_dates(*values):
        try:
            days = [datetime.strptime(value, "%Y-%m-%d").date() for value in values]
            return days == sorted(days)
        except ValueError:
            return False

    soup = BeautifulSoup(html, "html.parser")
    results = []
    recognized = False
    for table in soup.select("table"):
        title = table.get_text(" ", strip=True)
        dividend = "权益登记日" in title and "分红发放日" in title
        split = "拆分折算日" in title
        if not dividend and not split:
            continue
        recognized = True
        for row in table.select("tr"):
            cells = [td.get_text(" ", strip=True) for td in row.find_all("td")]
            if not cells or not re.match(r"20\d{2}年", cells[0]):
                continue
            if dividend and len(cells) >= 5:
                match = re.search(r"每10份派现金([\d.]+)元", cells[3])
                if not match:
                    raise DataError("unrecognized dividend amount")
                record, ex, pay = cells[1], cells[2], cells[4]
                verified = valid_dates(record, ex, pay)
                results.append(
                    {
                        "id": f"{symbol}:dividend:{record}",
                        "symbol": symbol,
                        "kind": "dividend",
                        "record_day": record,
                        "ex_day": ex,
                        "pay_day": pay,
                        "value": str(dec(match[1]) / 10),
                        "verified": bool(verified),
                        "source": f"https://fundf10.eastmoney.com/fhsp_{symbol[2:]}.html",
                    }
                )
            elif split and len(cells) >= 4:
                match = re.search(r"([\d.]+)[:：]([\d.]+)", cells[3])
                if not match:
                    raise DataError("unrecognized split ratio")
                results.append(
                    {
                        "id": f"{symbol}:split:{cells[1]}",
                        "symbol": symbol,
                        "kind": "split",
                        "record_day": cells[1],
                        "ex_day": cells[1],
                        "pay_day": cells[1],
                        "value": str(dec(match[2]) / dec(match[1])),
                        "verified": valid_dates(cells[1]),
                        "source": f"https://fundf10.eastmoney.com/fhsp_{symbol[2:]}.html",
                    }
                )
    if not recognized and "暂无分红信息" not in html and "暂无分红" not in html:
        raise DataError("corporate action page unavailable")
    return results


def parse_tencent(text: str, known: dict[str, Instrument], now: datetime) -> list[Quote]:
    results = []
    for symbol, body in re.findall(r'v_((?:sh|sz)\d{6})="([^"]*)"', text):
        if symbol not in known:
            continue
        parts = body.split("~")
        if len(parts) < 49 or not re.fullmatch(r"\d{14}", parts[30]):
            continue
        at = datetime.strptime(parts[30], "%Y%m%d%H%M%S").replace(tzinfo=TZ)
        last, prev, bid, ask = (units(number(parts[x])) for x in (3, 4, 9, 19))
        upper, lower = units(number(parts[47])), units(number(parts[48]))
        volume = int(number(parts[6]) * 100)  # Tencent ETF volume is in board lots, not shares.
        status = "trading" if min(last, prev, bid, ask, upper, lower) > 0 else "unknown"
        results.append(
            Quote(
                symbol,
                iso(at),
                iso(now),
                last,
                prev,
                bid,
                ask,
                volume,
                units(number(parts[37]) * 10000),
                upper,
                lower,
                status,
                "tencent",
                high=units(number(parts[33])),
                low=units(number(parts[34])),
                open=units(number(parts[5])),
            )
        )
    return results


def sina_quote_rows(text: str) -> dict[str, list[str]]:
    return {
        symbol: body.split(",")
        for symbol, body in re.findall(r'var hq_str_((?:sh|sz)\d{6})="([^"]*)";', text)
    }


def parse_sina(text: str, known: dict[str, Instrument], now: datetime) -> list[Quote]:
    result = []
    for symbol, parts in sina_quote_rows(text).items():
        if symbol not in known or len(parts) < 33:
            continue
        try:
            stamp = datetime.strptime(parts[30] + " " + parts[31], "%Y-%m-%d %H:%M:%S").replace(tzinfo=TZ)
            raw = dict(
                zip(
                    ("f2", "f18", "f31", "f32", "f6", "f15", "f16", "f17"),
                    (parts[3], parts[2], parts[6], parts[7], parts[9], parts[4], parts[5], parts[1]),
                )
            )
            raw.update(f5=str(dec(parts[8]) / 100), f124=int(stamp.timestamp()))
            quote = PublicProvider.market_quote(known[symbol], raw, now)
            if quote:
                result.append(
                    replace(quote, source="sina", status=quote.status if parts[32] == "00" else "unknown")
                )
        except (ValueError, ArithmeticError, TypeError):
            continue
    return result


class PublicProvider:
    def __init__(self):
        self.suggestion_cache = OrderedDict()
        self.suggestion_lock = Lock()
        self.history_primary_retry_at = 0.0
        self.history_primary_lock = Lock()
        self.next_request = {}
        self.cooldowns = {}
        self.rate_lock = Lock()
        self.client = httpx.Client(
            timeout=httpx.Timeout(10, connect=4),
            follow_redirects=True,
            limits=httpx.Limits(max_connections=8, max_keepalive_connections=8),
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://fundf10.eastmoney.com/"},
        )

    def close(self):
        self.client.close()

    def get(self, url, params=None, *, allowed=None):
        last = None
        host = httpx.URL(url).host
        for attempt in range(2):
            with self.rate_lock:
                if self.cooldowns.get(host, 0) > time.monotonic():
                    raise SourceCooling(host, self.cooldowns[host])
                slot = max(time.monotonic(), self.next_request.get(host, 0))
                self.next_request[host] = slot + 1.0
            time.sleep(max(0, slot - time.monotonic()))
            if allowed is not None and not allowed():
                raise DataError("request window closed")
            if self.cooldowns.get(host, 0) > time.monotonic():
                raise SourceCooling(host, self.cooldowns[host])
            try:
                options = {"params": params}
                if host.endswith("sina.cn") or host.endswith("sinajs.cn") or host.endswith("sina.com.cn"):
                    options["headers"] = {"Referer": "https://finance.sina.com.cn/"}
                response = self.client.get(url, **options)
                response.raise_for_status()
                return response
            except httpx.HTTPError as exc:
                last = exc
                if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in {403, 429, 501}:
                    retry_after = exc.response.headers.get("Retry-After", "300")
                    delay = max(300, int(retry_after)) if retry_after.isdigit() else 300
                    self.cooldowns[host] = time.monotonic() + delay
                    raise SourceCooling(host, self.cooldowns[host], exc.response.status_code) from exc
                if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in {400, 404}:
                    break
                if attempt == 0:
                    time.sleep(0.25)
        status = f" HTTP {last.response.status_code}" if isinstance(last, httpx.HTTPStatusError) else ""
        if isinstance(last, (httpx.TransportError,)) or (
            isinstance(last, httpx.HTTPStatusError) and last.response.status_code in {500, 502, 503, 504}
        ):
            # Keep a failed high-priority host from delaying every minute/quote request.
            # Its next eligible probe still precedes the lower-priority sources.
            with self.rate_lock:
                self.cooldowns[host] = time.monotonic() + 60
        raise DataError(f"{host}:{type(last).__name__}{status}") from last

    @staticmethod
    def market_quote(instrument: Instrument, raw: dict, now: datetime) -> Quote | None:
        if number(raw.get("f124")) <= 0:
            return None
        at = datetime.fromtimestamp(int(raw["f124"]), TZ)
        last, previous, bid, ask = (units(number(raw.get(k))) for k in ("f2", "f18", "f31", "f32"))
        # Use the verified ETF's price-limit convention across all types and markets.
        upper = lower = 0
        if instrument.tradable and previous > 0:
            ratio = dec(instrument.limit_ratio)
            upper = (
                int(
                    (Decimal(previous) * (1 + ratio) / instrument.tick).quantize(
                        Decimal(1), rounding=ROUND_HALF_UP
                    )
                )
                * instrument.tick
            )
            lower = (
                int(
                    (Decimal(previous) * (1 - ratio) / instrument.tick).quantize(
                        Decimal(1), rounding=ROUND_HALF_UP
                    )
                )
                * instrument.tick
            )
        status = "trading" if min(last, previous, bid, ask, upper, lower) > 0 else "unknown"
        return Quote(
            instrument.symbol,
            iso(at),
            iso(now),
            last,
            previous,
            bid,
            ask,
            int(number(raw.get("f5")) * 100),
            units(number(raw.get("f6"))),
            upper,
            lower,
            status,
            "eastmoney",
            high=units(number(raw.get("f15"))),
            low=units(number(raw.get("f16"))),
            open=units(number(raw.get("f17"))),
        )

    def profile(self, instrument: Instrument, now: datetime):
        html = self.get(f"https://fundf10.eastmoney.com/jbgk_{instrument.symbol[2:]}.html").text
        return parse_profile(instrument, html, now)

    def suggest_etfs(self, query: str):
        from app.market_data.etf_search import parse_suggestions, search_key

        key = search_key(query)
        if not key or len(key) > 60:
            return []
        with self.suggestion_lock:
            cached = self.suggestion_cache.get(key)
            if cached and time.monotonic() - cached[0] < 300:
                self.suggestion_cache.move_to_end(key)
                return [dict(row) for row in cached[1]]
        term = key[2:] if re.fullmatch(r"(?:sh|sz)\d+", key) else key
        payload = self.get(
            "https://searchapi.eastmoney.com/api/suggest/get",
            {"input": term, "type": 14, "count": 30},
        ).json()
        result = parse_suggestions(payload, key)
        with self.suggestion_lock:
            self.suggestion_cache[key] = (time.monotonic(), result)
            self.suggestion_cache.move_to_end(key)
            while len(self.suggestion_cache) > 128:
                self.suggestion_cache.popitem(last=False)
        return [dict(row) for row in result]

    def quotes(self, known: dict[str, Instrument], now: datetime):
        remaining, result, errors = dict(known), [], []
        display_only, stale_only = {}, {}
        for source in SOURCE_PRIORITY:
            if not remaining:
                break
            try:
                quotes = getattr(self, f"{source}_quotes")(dict(remaining), now)
                for quote in quotes:
                    if quote.symbol not in remaining or quote.last <= 0:
                        continue
                    age = (now - datetime.fromisoformat(quote.at)).total_seconds()
                    if age < 0:
                        continue
                    if age > 90:
                        # Keep a real closing quote available when a code is added after hours.
                        # Freshness checks in execution still forbid using it for a trade.
                        old = stale_only.get(quote.symbol)
                        if old is None or quote.at > old.at:
                            stale_only[quote.symbol] = quote
                        continue
                    if quote.status == "unknown":
                        display_only.setdefault(quote.symbol, quote)
                        continue
                    result.append(quote)
                    remaining.pop(quote.symbol)
            except (DataError, ValueError, KeyError, TypeError) as exc:
                errors.append(f"{source}: {exc}")
        result.extend(
            display_only.get(s) or stale_only[s] for s in remaining if s in display_only or s in stale_only
        )
        if known and not result:
            raise DataError("quote sources unavailable: " + "; ".join(errors))
        return result

    def eastmoney_quotes(self, known, now):
        result = []
        symbols = list(known)
        for offset in range(0, len(symbols), 50):
            batch = symbols[offset : offset + 50]
            data = (
                self.get(
                    "https://push2.eastmoney.com/api/qt/ulist.np/get",
                    {
                        "secids": ",".join(("1." if s.startswith("sh") else "0.") + s[2:] for s in batch),
                        "fltt": 2,
                        "invt": 2,
                        "fields": "f2,f5,f6,f12,f13,f15,f16,f17,f18,f31,f32,f124",
                    },
                )
                .json()
                .get("data")
                or {}
            )
            for raw in data.get("diff", []):
                try:
                    symbol = symbol_for(str(raw.get("f12", "")), "sh" if raw.get("f13") == 1 else "sz")
                except ValueError:
                    continue
                if symbol in known:
                    quote = self.market_quote(known[symbol], raw, now)
                    if quote:
                        result.append(quote)
        return result

    def tencent_quotes(self, known, now):
        result = []
        symbols = sorted(known)
        for i in range(0, len(symbols), 60):
            response = self.get("https://qt.gtimg.cn/q=" + ",".join(symbols[i : i + 60]))
            result.extend(parse_tencent(response.content.decode("gb18030"), known, now))
        if symbols and not result:
            raise DataError("no usable fallback quotes")
        return result

    def sina_quotes(self, known, now):
        result = []
        symbols = list(known)
        for offset in range(0, len(symbols), 60):
            response = self.get("https://hq.sinajs.cn/list=" + ",".join(symbols[offset : offset + 60]))
            result.extend(parse_sina(response.content.decode("gb18030"), known, now))
        return result

    def ths_quotes(self, known, now):
        result = []
        for symbol, instrument in known.items():
            if not re.fullmatch(r"(?:sh5|sz1)\d{5}", symbol):
                continue
            try:
                data = jsonp(self.get(f"https://d.10jqka.com.cn/v6/line/hs_{symbol[2:]}/01/today.js").text)[
                    f"hs_{symbol[2:]}"
                ]
                stamp = datetime.strptime(data["1"] + data["dt"], "%Y%m%d%H%M").replace(tzinfo=TZ)
                # This endpoint has no executable bid/ask. Never invent a tradable order book.
                result.append(
                    Quote(
                        symbol,
                        iso(stamp),
                        iso(now),
                        units(dec(data["11"])),
                        0,
                        0,
                        0,
                        int(dec(data["13"])),
                        units(dec(data["19"])),
                        0,
                        0,
                        "unknown",
                        "ths",
                    )
                )
            except (DataError, ValueError, KeyError, TypeError, ArithmeticError):
                continue
        return result

    def update_history(self, instrument, as_of, cached, calendar, full_day="", *, refresh_turnover=False):
        start, count, reason = request_window(cached, as_of, calendar, full_day)
        if refresh_turnover and start and cached.get("raw"):
            # Backfill only the observation window on upgrade, including an already-current cache.
            start = min(start, cached["raw"][-min(20, len(cached["raw"]))].day)
            count = max(count, len([d for d in calendar.days if start <= d <= as_of]) + 2)
            reason = "补齐最近 20 日换手率字段"
        base = signature(cached)
        if not count:
            return HistoryUpdate(cached, "cached", reason, base, 0)
        if start:
            source = cached["raw"][-1].source
            fresh = self.history(instrument, as_of, start=start, limit=count, source=source)
            try:
                merged = merge_history(cached, fresh, as_of, calendar)
                return HistoryUpdate(merged, "incremental", reason, base, count, fresh["raw"][0].day)
            except FullRefreshRequired as exc:
                reason = str(exc)
            # A changed adjustment baseline cannot be spliced into the cached series.
            fresh = self.history(instrument, as_of, source=fresh["raw"][-1].source)
        else:
            fresh = self.history(instrument, as_of)
        validate_series(fresh, as_of)
        return HistoryUpdate(fresh, "full", reason, base, 320)

    def history(self, instrument: Instrument, as_of: str, *, start=None, limit=320, source=None):
        options = {"start": start, "limit": limit} if start or limit != 320 else {}
        # Cached provenance must not promote a lower-priority source. Source changes
        # are handled by merge_history's full-window validation, never by splicing.
        primary = self.try_primary_history(instrument, as_of, options)
        if primary is not None:
            return primary
        return self.fallback_history(instrument, as_of, include_metrics=False, **options)

    def history_metrics(self, instrument, as_of, *, start=None, limit=320):
        """Independent, optional enrichment; failure never discards price history."""
        primary = self.try_primary_history(instrument, as_of, {"start": start, "limit": limit})
        if primary is not None:
            from app.market_data.history import missing_metrics

            if not missing_metrics(primary):
                return primary
            # Retain higher-priority fields while supplementing absent ones.
            try:
                supplement = self.fallback_history(instrument, as_of, start=start, limit=limit)
            except (DataError, ValueError, KeyError, TypeError, ArithmeticError):
                return primary
            from app.market_data.history import enrich_metrics

            return enrich_metrics(primary, supplement)
        return self.fallback_history(instrument, as_of, start=start, limit=limit)

    def try_primary_history(self, instrument, as_of, options):
        # One failed source probe must not occupy every history worker during an outage.
        if time.monotonic() < self.history_primary_retry_at or not self.history_primary_lock.acquire(False):
            return None
        try:
            if time.monotonic() < self.history_primary_retry_at:
                return None
            try:
                return self.eastmoney_history(instrument, as_of, **options)
            except (DataError, ValueError, KeyError):
                self.history_primary_retry_at = time.monotonic() + 600
                return None
        finally:
            self.history_primary_lock.release()

    def eastmoney_history(self, instrument: Instrument, as_of: str, *, start=None, limit=320):
        secid = ("1." if instrument.symbol.startswith("sh") else "0.") + instrument.symbol[2:]
        result = {}
        for adjustment, fqt in (("qfq", 1), ("raw", 0)):
            data = (
                self.get(
                    "https://push2his.eastmoney.com/api/qt/stock/kline/get",
                    {
                        "secid": secid,
                        "klt": 101,
                        "fqt": fqt,
                        "beg": start.replace("-", "") if start else "19900101",
                        "end": as_of.replace("-", ""),
                        "lmt": limit,
                        "fields1": "f1,f2,f3,f4,f5,f6",
                        "fields2": "f51,f52,f53,f54,f55,f56,f57,f61",
                    },
                )
                .json()
                .get("data")
            )
            if not data or str(data.get("code")) != instrument.symbol[2:]:
                raise DataError("history identity missing")
            bars = []
            for line in data.get("klines", []):
                values = line.split(",")
                if len(values) < 5:
                    raise DataError("history columns missing")
                o, c, h, low = map(dec, values[1:5])
                if not 0 < low <= min(o, c) <= max(o, c) <= h:
                    raise DataError("Eastmoney daily prices invalid")
                bars.append(
                    Bar(
                        values[0],
                        values[2],
                        optional_number(values[6] if len(values) > 6 else None),
                        values[1],
                        values[3],
                        values[4],
                        optional_number(values[5] if len(values) > 5 else None, 100, integer=True),
                        turnover_rate=turnover_ratio(values[7] if len(values) > 7 else None),
                        turnover_source="eastmoney:f61",
                        amount_source="eastmoney",
                    )
                )
            result[adjustment] = [b for b in normalized_bars(bars, as_of) if not start or b.day >= start][
                -limit:
            ]
        if {b.day for b in result["raw"]} != {b.day for b in result["qfq"]}:
            raise DataError("adjustment dates mismatch")
        if not result["raw"] or result["raw"][-1].day != as_of:
            raise DataError("primary history latest completed date missing")
        return result

    def tencent_history_prices(self, instrument, as_of, *, start=None, limit=320):
        price_series = {}
        closes = {}
        now = now_cn()
        for adjustment, suffix in (("qfq", "qfq"), ("raw", "")):
            payload = self.get(
                "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get",
                {
                    "param": f"{instrument.symbol},day,,,{limit},{suffix}",
                },
            ).json()
            data = payload.get("data", {}).get(instrument.symbol)
            if not data:
                raise DataError("fallback history identity missing")
            values = data.get("qfqday" if suffix else "day") or data.get("day", [])
            if any(len(row) < 5 for row in values) or len({row[0] for row in values}) != len(values):
                raise DataError("fallback price rows invalid or duplicated")
            for row in values:
                datetime.strptime(row[0], "%Y-%m-%d")
                o, c, h, low = map(dec, row[1:5])
                if not 0 < low <= min(o, c) <= max(o, c) <= h:
                    raise DataError("Tencent daily prices invalid")
            price_series[adjustment] = {
                r[0]: r for r in values if r[0] <= as_of and (not start or r[0] >= start)
            }
            if values and max(r[0] for r in values) == as_of:
                closes[adjustment] = closing_price(data, instrument.symbol, as_of, now)
        if any(not rows or max(rows) != as_of for rows in price_series.values()):
            raise DataError("Tencent latest completed price date missing")
        close = closes.get("raw")
        if close and close == closes.get("qfq"):
            # Forward-adjusted prices equal raw prices on the series' latest session. Tencent's
            # provisional last qfq row can have only two decimals; its closing quote has three.
            for series in price_series.values():
                series[as_of] = close
        else:
            close = None
        if as_of == now.date().isoformat() and not close:
            raise DataError("Tencent current-day closing snapshot not confirmed")
        return price_series, close

    def sina_daily_rows(self, symbol, as_of, *, start=None, limit=320):
        data = self.get(
            "https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService.getKLineData",
            {
                "symbol": symbol,
                "scale": 240,
                "ma": "no",
                "datalen": min(1970, limit),
            },
        ).json()
        if not isinstance(data, list) or not data:
            raise DataError("Sina daily price response missing")
        result = {}
        for row in data:
            day = datetime.strptime(row["day"], "%Y-%m-%d").date().isoformat()
            if day > as_of or (start and day < start):
                continue
            if day in result:
                raise DataError("Sina daily price dates duplicated")
            o, c, h, low = (dec(row[k]) for k in ("open", "close", "high", "low"))
            volume = optional_number(row.get("volume"))
            if not 0 < low <= min(o, c) <= max(o, c) <= h:
                raise DataError("Sina daily prices invalid")
            result[day] = [
                day,
                str(o),
                str(c),
                str(h),
                str(low),
                str(dec(volume) / 100) if volume is not None else None,
            ]
        if not result or max(result) != as_of:
            raise DataError("Sina latest completed price date missing")
        return result

    def ths_history_prices(self, instrument, as_of, *, start=None, limit=320, raw=None):
        result = {"raw": raw} if raw is not None else {}
        # Sina's bounded daily endpoint supplies unadjusted OHLC only. Forward-adjusted
        # prices continue to THS; raw prices are never relabelled as forward-adjusted.
        for adjustment, channel in (("qfq", "01"), ("raw", "00")):
            if adjustment in result:
                continue
            payload = jsonp(
                self.get(
                    f"https://d.10jqka.com.cn/v6/line/hs_{instrument.symbol[2:]}/{channel}/last{limit}.js"
                ).text
            )
            rows = {}
            for line in payload.get("data", "").split(";"):
                values = line.split(",")
                if len(values) < 5:
                    continue
                day = datetime.strptime(values[0], "%Y%m%d").date().isoformat()
                if day > as_of or (start and day < start):
                    continue
                if day in rows:
                    raise DataError("THS daily price dates duplicated")
                o, h, low, c = map(dec, values[1:5])
                volume = optional_number(values[5] if len(values) > 5 else None)
                if not 0 < low <= min(o, c) <= max(o, c) <= h:
                    raise DataError("THS daily prices invalid")
                rows[day] = [
                    day,
                    str(o),
                    str(c),
                    str(h),
                    str(low),
                    str(dec(volume) / 100) if volume is not None else None,
                ]
            if not rows or max(rows) != as_of:
                raise DataError("THS latest completed price date missing")
            result[adjustment] = rows
        return result

    def fallback_history(
        self, instrument: Instrument, as_of: str, *, start=None, limit=320, include_metrics=True
    ):
        """Prices: Tencent → Sina → THS; unsupported liquidity fields continue to THS."""
        options = {"start": start, "limit": limit}
        try:
            price_series, close = self.tencent_history_prices(instrument, as_of, **options)
            source = "tencent-price+ths-amount"
        except (DataError, ValueError, KeyError, TypeError, ArithmeticError):
            try:
                raw = self.sina_daily_rows(instrument.symbol, as_of, **options)
            except (DataError, ValueError, KeyError, TypeError, ArithmeticError):
                raw = None
            price_series = self.ths_history_prices(instrument, as_of, raw=raw, **options)
            source = "sina-raw+ths-qfq+ths-amount" if raw is not None else "ths"
            close = None
        if not include_metrics:
            # Publish validated price pairs before attempting any optional source.
            source = source.replace("tencent-price", "tencent").removesuffix("+ths-amount")
            result = {
                adjustment: [
                    Bar(
                        day,
                        row[2],
                        None,
                        row[1],
                        row[3],
                        row[4],
                        optional_number(row[5] if len(row) > 5 else None, 100, integer=True),
                        source,
                    )
                    for day, row in sorted(rows.items())
                ]
                for adjustment, rows in price_series.items()
            }
            validate_series(result, as_of)
            return result
        base_url = f"https://d.10jqka.com.cn/v6/line/hs_{instrument.symbol[2:]}/01"
        turnover_data = jsonp(self.get(f"{base_url}/last{limit}.js").text)
        amounts = {}
        rates = {}
        turnover_rows = {}
        try:
            for line in turnover_data.get("data", "").split(";"):
                values = line.split(",")
                if len(values) < 7:
                    continue
                day = datetime.strptime(values[0], "%Y%m%d").date().isoformat()
                if day <= as_of and (not start or day >= start):
                    if day in turnover_rows:
                        raise DataError("fallback turnover dates duplicated")
                    turnover_rows[day] = values
                    try:
                        amounts[day] = (dec(values[6]), int(dec(values[5])))
                        rates[day] = turnover_ratio(values[7] if len(values) > 7 else None)
                    except (ValueError, ArithmeticError):
                        if day != as_of or not close:
                            raise
                        # Some files append today's date with blank amount/OHLC. Keep it missing
                        # until the separate, fully validated closing snapshot can replace it.
            if close and (as_of not in amounts or not matching_close(close, turnover_rows[as_of])):
                # The historical file may omit today or still contain an intraday sample.
                # Fetch the tiny closing snapshot only when needed; never accept metadata.today
                # as a bar or accept a current quote for an earlier target date.
                today = jsonp(self.get(f"{base_url}/today.js").text).get(f"hs_{instrument.symbol[2:]}", {})
                stamp = datetime.strptime(today.get("1", "") + today.get("dt", ""), "%Y%m%d%H%M").replace(
                    tzinfo=TZ
                )
                if (
                    stamp.date().isoformat() != as_of
                    or stamp.strftime("%H%M") < "1500"
                    or stamp > now_cn()
                    or str(today.get("open")) != "0"
                ):
                    raise DataError("fallback closing turnover is not confirmed after market close")
                row = [today.get(k, "") for k in ("1", "7", "8", "9", "11", "13", "19")]
                if not matching_close(close, row):
                    raise DataError("fallback closing price/volume cross-check failed")
                amounts[as_of] = (dec(row[6]), int(dec(row[5])))
                rates[as_of] = turnover_ratio(today.get("1968584"))
        except (ValueError, ArithmeticError, TypeError) as exc:
            raise DataError("fallback turnover values invalid") from exc
        common = sorted(set(price_series["raw"]) & set(price_series["qfq"]) & set(amounts))
        if not common or common[-1] != as_of:
            raise DataError("fallback amount/price dates incomplete")
        expected = sorted(price_series["raw"])
        if not start:
            expected = expected[-min(250, len(expected)) :]
        if any(day not in common for day in expected):
            raise DataError("fallback history has missing daily turnover")
        result = {}
        for adjustment, series in price_series.items():
            rows = []
            for day in common[-320:]:
                values = series[day]
                amount, ths_volume = amounts[day]
                tx_volume = int(dec(price_series["raw"][day][5]) * 100)
                if (
                    max(ths_volume, tx_volume)
                    and abs(ths_volume - tx_volume) / max(ths_volume, tx_volume) > 0.02
                ):
                    raise DataError("fallback history volume cross-check failed")
                rows.append(
                    Bar(
                        day,
                        values[2],
                        str(amount),
                        values[1],
                        values[3],
                        values[4],
                        ths_volume,
                        source,
                        turnover_rate=rates.get(day),
                        turnover_source="ths:1968584",
                        amount_source="ths",
                    )
                )
            result[adjustment] = normalized_bars(rows, as_of)
        return result

    def actions(self, symbol: str):
        return parse_actions(symbol, self.get(f"https://fundf10.eastmoney.com/fhsp_{symbol[2:]}.html").text)
