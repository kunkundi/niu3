"""Incremental history planning and validation, independent of network and storage."""

from dataclasses import dataclass, replace
from datetime import date
import hashlib
import json

from app.core.calendar import Calendar
from app.core.types import Bar, dec

WINDOW = 320
OVERLAP = 5
MAX_GAP = 30


class FullRefreshRequired(ValueError):
    pass


@dataclass(frozen=True)
class HistoryUpdate:
    series: dict[str, list[Bar]]
    mode: str
    reason: str
    base_signature: str
    requested_bars: int
    write_from: str = ""


def signature(series):
    payload = {key: [b.to_dict() for b in series.get(key, [])] for key in ("raw", "qfq")}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def price_source(bar):
    return bar.source.replace("tencent-price", "tencent").removesuffix("+ths-amount")


def missing_metrics(series):
    return any(
        bar.amount is None or bar.volume is None or bar.turnover_rate is None for bar in series.get("raw", [])
    )


def enrich_metrics(cached, supplement):
    """Fill optional fields only after matching raw price/volume; never rewrite prices."""
    donors = {b.day: b for b in supplement.get("raw", [])}
    accepted = {}
    for bar in cached.get("raw", []):
        donor = donors.get(bar.day)
        if not donor or any(
            dec(getattr(bar, k)) != dec(getattr(donor, k)) for k in ("open", "high", "low", "close")
        ):
            continue
        if (
            (donor.amount is not None and dec(donor.amount) < 0)
            or (donor.volume is not None and donor.volume < 0)
            or (donor.turnover_rate is not None and dec(donor.turnover_rate) < 0)
        ):
            continue
        if (
            bar.volume is not None
            and donor.volume is not None
            and (abs(bar.volume - donor.volume) > max(bar.volume, donor.volume) * 0.02)
        ):
            continue
        accepted[bar.day] = donor
    result = {}
    for adjustment, bars in cached.items():
        rows = []
        for bar in bars:
            donor = accepted.get(bar.day)
            updates = {}
            if donor:
                if bar.amount is None and donor.amount is not None:
                    updates.update(amount=donor.amount, amount_source=donor.amount_source or donor.source)
                if bar.volume is None and donor.volume is not None:
                    updates["volume"] = donor.volume
                if bar.turnover_rate is None and donor.turnover_rate is not None:
                    updates.update(turnover_rate=donor.turnover_rate, turnover_source=donor.turnover_source)
            rows.append(replace(bar, **updates) if updates else bar)
        result[adjustment] = rows
    return result


def validate_series(series, as_of):
    if set(series) != {"raw", "qfq"}:
        raise ValueError("history adjustment pair missing")
    dates = []
    for key in ("raw", "qfq"):
        rows = series[key]
        days = [b.day for b in rows]
        if not days or days != sorted(set(days)) or days[-1] != as_of:
            raise ValueError("history dates incomplete or duplicated")
        for bar in rows:
            date.fromisoformat(bar.day)
            if (
                dec(bar.close) <= 0
                or (bar.amount is not None and dec(bar.amount) < 0)
                or (bar.volume is not None and bar.volume < 0)
            ):
                raise ValueError("invalid history values")
            for field in (bar.open, bar.high, bar.low):
                if dec(field) < 0:
                    raise ValueError("invalid history price")
        dates.append(days)
    if dates[0] != dates[1]:
        raise ValueError("history adjustment dates mismatch")


def request_window(cached, as_of, calendar: Calendar, full_day=""):
    """Return (start, count, reason). Empty start means a full refresh is required."""
    try:
        last = cached["raw"][-1].day
        validate_series(cached, last)
    except (KeyError, IndexError, ValueError):
        return "", WINDOW, "首次下载或缓存不完整"
    if last == as_of:
        return last, 0, "缓存已更新到目标交易日"
    source = {price_source(bar) for rows in cached.values() for bar in rows}
    if len(source) != 1 or not source <= {
        "eastmoney",
        "tencent",
        "sina-raw+ths-qfq",
        "ths",
    }:
        return "", WINDOW, "缓存来源不一致"
    if len(cached["raw"]) < OVERLAP:
        return "", WINDOW, "历史过短，无法校验重叠窗口"
    start = cached["raw"][-OVERLAP].day
    if not calendar.known(date.fromisoformat(start)) or not calendar.known(date.fromisoformat(as_of)):
        return "", WINDOW, "增量范围超出已确认日历"
    missing = [day for day in calendar.days if last < day <= as_of]
    if not missing or missing[-1] != as_of or len(missing) > MAX_GAP:
        return "", WINDOW, "缺失区间超过增量窗口"
    if full_day and (date.fromisoformat(as_of) - date.fromisoformat(full_day)).days >= 30:
        return "", WINDOW, "每 30 天完整核验历史"
    return start, len(missing) + OVERLAP + 2, "补充缺失交易日并核验最近 5 日"


def merge_history(cached, fresh, as_of, calendar: Calendar):
    validate_series(fresh, as_of)
    last = cached["raw"][-1].day
    anchors = {b.day for b in cached["raw"][-OVERLAP:]}
    required_new = {d for d in calendar.days if last < d <= as_of}
    result = {}
    for adjustment in ("raw", "qfq"):
        old = {b.day: b for b in cached[adjustment]}
        new = {b.day: b for b in fresh[adjustment]}
        if not required_new <= new.keys():
            raise ValueError("incremental history has missing completed days")
        if not anchors <= new.keys():
            raise FullRefreshRequired("增量窗口缺少重叠日期")
        if {price_source(b) for b in cached[adjustment]} != {price_source(b) for b in fresh[adjustment]}:
            raise FullRefreshRequired("数据源切换，重新统一历史口径")
        for day in old.keys() & new.keys():
            a, b = old[day], new[day]
            if any(dec(getattr(a, k)) != dec(getattr(b, k)) for k in ("open", "high", "low", "close")):
                raise FullRefreshRequired("复权基准或历史数据修订，重新获取完整窗口")
        # Optional fields omitted by a price refresh must not erase known values.
        preserved = enrich_metrics(fresh, cached)
        new = {b.day: b for b in preserved[adjustment]}
        merged = old | new
        result[adjustment] = [merged[d] for d in sorted(merged)[-WINDOW:]]
    validate_series(result, as_of)
    return result
