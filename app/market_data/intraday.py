"""On-demand display-only minute data; isolated from strategy and execution prices."""

from collections import OrderedDict
import json
from datetime import datetime, time as clock_time, timedelta
from threading import Lock, BoundedSemaphore
import time

from app.core.types import TZ, dec, iso, dt
from app.market_data.providers import DataError, PublicProvider, jsonp, sina_quote_rows
from app.market_data.sources import SOURCE_PRIORITY
from app.storage.db import dump

ACTIVE_REFRESH_SECONDS = 10
IDLE_REFRESH_SECONDS = 60


def trading_minute(value: str) -> int | None:
    if len(value) != 4 or not value.isdigit():
        raise DataError("invalid minute time")
    hour, minute = int(value[:2]), int(value[2:])
    if hour > 23 or minute > 59:
        raise DataError("invalid minute time")
    minutes = hour * 60 + minute
    if 570 <= minutes <= 690:
        return minutes - 570
    if 780 <= minutes <= 900:
        return minutes - 660
    return None


def minute_series(symbol, day, previous_close, rows, now, source):
    """Retain actual regular-session observations, without filling gaps or stretching time."""
    try:
        date = datetime.strptime(day, "%Y%m%d").date()
        previous = dec(previous_close)
        if date > now.astimezone(TZ).date() or previous <= 0 or len(rows) > 1000:
            raise ValueError("invalid minute metadata")
        points, seen = [], set()
        last = ""
        for row in rows:
            if len(row) < 2:
                raise ValueError("minute columns missing")
            stamp = row[0]
            position = trading_minute(stamp)
            # Opening auctions and after-hours fixed-price trades are outside this chart's axis.
            if position is None:
                continue
            at = datetime.combine(date, clock_time(int(stamp[:2]), int(stamp[2:])), TZ)
            price = dec(row[1])
            if stamp in seen or stamp < last or at > now or price <= 0:
                raise ValueError("invalid minute row")
            seen.add(stamp)
            last = stamp
            points.append({"time": f"{stamp[:2]}:{stamp[2:]}", "minute": position, "price": str(price)})
        if not points:
            raise ValueError("no regular-session minute points")
        return {
            "symbol": symbol,
            "day": date.isoformat(),
            "previous_close": str(previous),
            "points": points,
            "as_of": iso(datetime.combine(date, clock_time.fromisoformat(points[-1]["time"]), TZ)),
            "fetched_at": iso(now),
            "source": source,
        }
    except (ValueError, ArithmeticError, TypeError, KeyError) as exc:
        raise DataError("minute data invalid or unavailable") from exc


def tencent_minutes(payload, symbol, now):
    try:
        data = payload["data"][symbol]
        quote = data["qt"][symbol]
        day = data["data"]["date"]
        # The previous close must belong to the same instrument and session as the minute series.
        if payload.get("code") != 0 or quote[2] != symbol[2:] or quote[30][:8] != day:
            raise DataError("minute identity/date mismatch")
        return minute_series(
            symbol, day, quote[4], [row.split() for row in data["data"]["data"]], now, "tencent"
        )
    except (KeyError, IndexError, TypeError, AttributeError) as exc:
        raise DataError("minute response malformed") from exc


def ths_minutes(payload, symbol, now):
    try:
        data = payload[f"hs_{symbol[2:]}"]
        return minute_series(
            symbol,
            data["date"],
            data["pre"],
            [row.split(",") for row in data["data"].split(";") if row],
            now,
            "ths",
        )
    except (KeyError, TypeError, AttributeError) as exc:
        raise DataError("minute response malformed") from exc


def eastmoney_minutes(payload, symbol, now):
    try:
        data = payload["data"]
        market = 1 if symbol.startswith("sh") else 0
        if str(data["code"]) != symbol[2:] or int(data["market"]) != market:
            raise DataError("minute identity mismatch")
        rows = [row.split(",") for row in data["trends"]]
        dates = {row[0][:10] for row in rows}
        if len(dates) != 1:
            raise DataError("minute dates mixed")
        day = next(iter(dates)).replace("-", "")
        return minute_series(
            symbol,
            day,
            data["preClose"],
            [[row[0][11:16].replace(":", ""), row[2]] for row in rows],
            now,
            "eastmoney",
        )
    except (KeyError, IndexError, TypeError, AttributeError) as exc:
        raise DataError("minute response malformed") from exc


def sina_minutes(rows, quote_text, symbol, now):
    try:
        quote = sina_quote_rows(quote_text)[symbol]
        if len(quote) < 33 or not isinstance(rows, list):
            raise DataError("Sina minute metadata missing")
        day = quote[30]
        # Use actual one-minute closes and the same session's previous close.
        points = [
            [row["day"][11:16].replace(":", ""), row["close"]] for row in rows if row["day"][:10] == day
        ]
        return minute_series(symbol, day.replace("-", ""), quote[2], points, now, "sina")
    except (KeyError, IndexError, TypeError, AttributeError) as exc:
        raise DataError("Sina minute response malformed") from exc


def expected_day(calendar, now):
    local = now.astimezone(TZ)
    if not calendar.known(local.date()):
        return None
    if calendar.is_open(local.date()) and local.time() >= clock_time(9, 30):
        return local.date().isoformat()
    try:
        return calendar.previous(local.date())
    except ValueError:
        return None


def refresh_interval(calendar, now, failed=False, interval=ACTIVE_REFRESH_SECONDS):
    if not calendar.session(now):
        return 0
    return IDLE_REFRESH_SECONDS if failed else interval


def snapshot_key(calendar, now):
    target = expected_day(calendar, now)
    if target is None or calendar.session(now):
        return None
    local = now.astimezone(TZ)
    cutoff = "11:30" if target == local.date().isoformat() and local.time() < clock_time(13) else "15:00"
    return f"{target}T{cutoff}:00+08:00"


def snapshot_complete(data, phase):
    return bool(data and phase and data["day"] == phase[:10] and data["as_of"] >= phase)


def polling_schedule(calendar, now, interval=ACTIVE_REFRESH_SECONDS):
    local = now.astimezone(TZ)
    days = [local.date()] if calendar.is_open(local.date()) else []
    try:
        days.append(datetime.fromisoformat(calendar.next(local.date())).date())
    except ValueError:
        pass
    windows = [
        {"start": iso(datetime.combine(day, start, TZ)), "end": iso(datetime.combine(day, end, TZ))}
        for day in days
        for start, end in ((clock_time(9, 30), clock_time(11, 30)), (clock_time(13), clock_time(15)))
    ]
    return {
        "server_at": local.isoformat(timespec="milliseconds"),
        "interval_seconds": interval,
        "windows": windows,
        "snapshot_key": snapshot_key(calendar, now),
    }


def display_state(data, calendar, now, error="", interval=ACTIVE_REFRESH_SECONDS):
    target = expected_day(calendar, now)
    result = {**(data or {}), "points": (data or {}).get("points", []), "expected_day": target}
    result.update(
        {
            "refresh_seconds": refresh_interval(calendar, now, bool(error), interval),
            "polling": polling_schedule(calendar, now, interval),
            "stale": bool(error),
            "message": error,
            "status": "unavailable",
            "snapshot_key": None,
            "snapshot_complete": False,
        }
    )
    if not data:
        result["message"] = error or ("暂无分时数据" if calendar.session(now) else "暂无对应交易日分时")
        return result
    local = now.astimezone(TZ)
    current_session = calendar.is_open(local.date()) and local.time() >= clock_time(9, 30)
    latest = data["points"][-1]["time"]
    if target is None:
        result.update(status="delayed", stale=True, message="日历范围待核验，显示来源日期的分时")
    elif data["day"] != target:
        result.update(status="delayed", stale=True, message=f"尚无 {target} 分时，显示 {data['day']} 数据")
    elif error:
        result.update(status="delayed", stale=True)
    elif not current_session:
        if latest < "14:57":
            result.update(status="delayed", stale=True, message="最近交易日分时未更新至收盘")
        else:
            result.update(status="closed", message="最近交易日分时")
    else:
        minute = local.hour * 60 + local.minute
        cutoff = min(minute, 690) if minute < 780 else min(minute, 900)
        last_minute = int(latest[:2]) * 60 + int(latest[3:])
        if last_minute < cutoff - 3:
            result.update(status="delayed", stale=True, message="分时更新滞后")
        elif minute >= 900:
            result.update(status="closed", message="已收盘")
        elif 690 <= minute < 780:
            result.update(status="break", message="午间休市")
        else:
            result.update(status="live", message=f"盘中 · 每 {interval} 秒检查")
    return result


class IntradayService:
    def __init__(self, calendar, provider=None, monotonic=time.monotonic, db=None):
        self.calendar, self.provider, self.monotonic = calendar, provider, monotonic
        self.db = db
        self.owns_provider = provider is None
        self.cache = OrderedDict()
        self.snapshot_loads = OrderedDict()
        self.lock = Lock()
        self.guards = [Lock() for _ in range(64)]
        self.capacity = BoundedSemaphore(3)

    def close(self):
        if self.owns_provider and self.provider:
            self.provider.close()

    def saved(self, symbol):
        if self.db is None:
            return None
        with self.db.connect() as conn:
            row = conn.execute("SELECT payload FROM intraday_snapshots WHERE symbol=?", (symbol,)).fetchone()
        return json.loads(row[0]) if row else None

    def save(self, symbol, data):
        if self.db is None:
            return
        with self.db.transaction() as conn:
            conn.execute(
                "INSERT INTO intraday_snapshots VALUES(?,?,?) ON CONFLICT(symbol) DO UPDATE SET at=excluded.at,payload=excluded.payload",
                (symbol, data["fetched_at"], dump(data)),
            )
            conn.execute(
                "DELETE FROM intraday_snapshots WHERE symbol NOT IN (SELECT symbol FROM intraday_snapshots ORDER BY at DESC,symbol LIMIT 256)"
            )

    def fetch(self, symbol, now, *, snapshot=None):
        started = self.monotonic()

        def allowed():
            current = now + timedelta(seconds=max(0, self.monotonic() - started))
            return (
                snapshot_key(self.calendar, current) == snapshot
                if snapshot
                else self.calendar.session(current)
            )

        if not allowed():
            raise DataError("minute request window closed")
        with self.lock:
            if self.provider is None:
                self.provider = PublicProvider()
        errors = []
        for source in SOURCE_PRIORITY:
            if not allowed():
                raise DataError("minute request window closed")
            # THS hs_000001 is exchange-ambiguous; the other adapters retain sh000001.
            if source == "ths" and symbol == "sh000001":
                continue
            try:
                if source == "eastmoney":
                    payload = self.provider.get(
                        "https://push2his.eastmoney.com/api/qt/stock/trends2/get",
                        {
                            "secid": ("1." if symbol.startswith("sh") else "0.") + symbol[2:],
                            "fields1": "f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11",
                            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58",
                            "ndays": 1,
                            "iscr": 0,
                        },
                        allowed=allowed,
                    ).json()
                    result = eastmoney_minutes(payload, symbol, now)
                elif source == "tencent":
                    payload = self.provider.get(
                        "https://web.ifzq.gtimg.cn/appstock/app/minute/query",
                        {"code": symbol},
                        allowed=allowed,
                    ).json()
                    result = tencent_minutes(payload, symbol, now)
                elif source == "sina":
                    rows = self.provider.get(
                        "https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService.getKLineData",
                        {"symbol": symbol, "scale": 1, "ma": "no", "datalen": 500},
                        allowed=allowed,
                    ).json()
                    quote = self.provider.get("https://hq.sinajs.cn/list=" + symbol, allowed=allowed)
                    result = sina_minutes(rows, quote.content.decode("gb18030"), symbol, now)
                else:
                    payload = jsonp(
                        self.provider.get(
                            f"https://d.10jqka.com.cn/v6/time/hs_{symbol[2:]}/last.js", allowed=allowed
                        ).text
                    )
                    result = ths_minutes(payload, symbol, now)
                if result["day"] != expected_day(self.calendar, now):
                    raise DataError("minute session not current")
                return result
            except (DataError, ValueError, TypeError, ArithmeticError) as exc:
                errors.append(f"{source}: {exc}")
        raise DataError("minute sources unavailable: " + "; ".join(errors))

    def claim_snapshot(self, symbol, phase, now, retry):
        if self.db:
            with self.db.transaction() as conn:
                row = conn.execute(
                    "SELECT at,error FROM intraday_snapshot_loads WHERE symbol=? AND phase=?", (symbol, phase)
                ).fetchone()
                if row and (not retry or (now - dt(row["at"])).total_seconds() < 60):
                    return False, row["error"]
                conn.execute(
                    "INSERT INTO intraday_snapshot_loads VALUES(?,?,?,?) ON CONFLICT(symbol,phase) DO UPDATE SET at=excluded.at,error=excluded.error",
                    (symbol, phase, iso(now), "休市分时正在补齐"),
                )
                conn.execute(
                    "DELETE FROM intraday_snapshot_loads WHERE rowid NOT IN (SELECT rowid FROM intraday_snapshot_loads ORDER BY at DESC,rowid DESC LIMIT 1024)"
                )
        else:
            with self.lock:
                row = self.snapshot_loads.get((symbol, phase))
                if row and (not retry or (now - dt(row["at"])).total_seconds() < 60):
                    return False, row["error"]
                self.snapshot_loads[symbol, phase] = {"at": iso(now), "error": "休市分时正在补齐"}
                while len(self.snapshot_loads) > 1024:
                    self.snapshot_loads.popitem(last=False)
        return True, ""

    def finish_snapshot(self, symbol, phase, error):
        if self.db:
            with self.db.transaction() as conn:
                conn.execute(
                    "UPDATE intraday_snapshot_loads SET error=? WHERE symbol=? AND phase=?",
                    (error, symbol, phase),
                )
        else:
            with self.lock:
                if (symbol, phase) in self.snapshot_loads:
                    self.snapshot_loads[symbol, phase]["error"] = error

    def closed_snapshot(self, symbol, data, now, interval, retry, current_time):
        phase = snapshot_key(self.calendar, now)
        error = ""
        if phase and not snapshot_complete(data, phase):
            claimed, error = self.claim_snapshot(symbol, phase, now, retry)
            if claimed:
                try:
                    with self.capacity:
                        if snapshot_key(self.calendar, current_time()) != phase:
                            raise DataError("snapshot session changed")
                        fresh = self.fetch(symbol, current_time(), snapshot=phase)
                    if (
                        fresh["symbol"] != symbol
                        or fresh["day"] != phase[:10]
                        or (data and fresh["as_of"] < data["as_of"])
                    ):
                        raise DataError("snapshot identity/date mismatch or moved backwards")
                    data = fresh
                    self.save(symbol, data)
                    with self.lock:
                        self.cache[symbol] = {
                            "data": data,
                            "error": "",
                            "expires": 0,
                            "target": phase[:10],
                            "active_session": False,
                            "interval": interval,
                            "window": False,
                        }
                        self.cache.move_to_end(symbol)
                        while len(self.cache) > 256:
                            self.cache.popitem(last=False)
                    if not snapshot_complete(data, phase):
                        error = f"来源尚未更新至 {phase[11:16]}，显示已获取分时"
                except (DataError, ValueError, TypeError):
                    error = "休市分时补齐失败，保留已有数据；可稍后手动重试"
                self.finish_snapshot(symbol, phase, error)
        result = display_state(data, self.calendar, current_time(), error, interval)
        result.update(snapshot_key=phase, snapshot_complete=snapshot_complete(data, phase))
        if result["snapshot_complete"]:
            today = now.astimezone(TZ).date().isoformat()
            result["message"] = (
                "当日分时 · 固定展示" if data["day"] == today else f"最近交易日 {data['day']} 分时 · 固定展示"
            )
        return result

    def get(self, instrument, now, interval=ACTIVE_REFRESH_SECONDS, snapshot_retry=False):
        started = self.monotonic()

        def current_time():
            return now + timedelta(seconds=max(0, self.monotonic() - started))

        symbol, target = instrument.symbol, expected_day(self.calendar, now)
        active_session = self.calendar.session(now)
        with self.guards[hash(symbol) % len(self.guards)]:
            with self.lock:
                cached = self.cache.get(symbol)
            data = cached["data"] if cached else self.saved(symbol)
            # Rest periods load one frozen snapshot per symbol/session endpoint, never a polling loop.
            if not active_session:
                return self.closed_snapshot(symbol, data, now, interval, snapshot_retry, current_time)
            with self.lock:
                if (
                    cached
                    and cached["expires"] > self.monotonic()
                    and cached["target"] == target
                    and cached["active_session"] == active_session
                    and cached["interval"] == interval
                    and cached["window"] == (now.astimezone(TZ).hour < 12)
                ):
                    self.cache.move_to_end(symbol)
                    return display_state(cached["data"], self.calendar, now, cached["error"], interval)
            error = ""
            try:
                with self.capacity:
                    # A queued request can reach the provider after the session has ended.
                    if not self.calendar.session(current_time()):
                        return display_state(data, self.calendar, current_time(), interval=interval)
                    fresh = self.fetch(symbol, current_time())
                # An old fallback response cannot replace a newer cached session.
                if data and fresh["as_of"] < data["as_of"]:
                    raise DataError("minute data moved backwards")
                data = fresh
                self.save(symbol, data)
            except (DataError, ValueError, TypeError):
                error = "分时刷新失败，显示上次缓存" if data else "分时数据暂不可用，稍后自动重试"
            ttl = refresh_interval(self.calendar, now, bool(error), interval)
            with self.lock:
                self.cache[symbol] = {
                    "data": data,
                    "error": error,
                    "target": target,
                    "active_session": active_session,
                    "interval": interval,
                    "window": now.astimezone(TZ).hour < 12,
                    "expires": self.monotonic() + ttl,
                }
                self.cache.move_to_end(symbol)
                while len(self.cache) > 256:
                    self.cache.popitem(last=False)
            return display_state(data, self.calendar, current_time(), error, interval)
