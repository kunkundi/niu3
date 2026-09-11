"""Boundary types. Prices/money are integer millionths of a yuan in storage."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Literal
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Shanghai")
SCALE = 1_000_000


def now_cn() -> datetime:
    return datetime.now(TZ)


def iso(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("timezone required")
    return value.astimezone(TZ).isoformat(timespec="seconds")


def dt(value: str) -> datetime:
    result = datetime.fromisoformat(value)
    if result.tzinfo is None:
        raise ValueError("timezone required")
    return result.astimezone(TZ)


def dec(value: object) -> Decimal:
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError("non-finite number")
    return result


def units(value: object) -> int:
    return int((dec(value) * SCALE).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def yuan(value: int) -> float:
    return float(Decimal(value) / SCALE)


def symbol_for(code: str, exchange: str | None = None) -> str:
    code = code.lower().strip()
    if code.startswith(("sh", "sz")):
        prefix, code = code[:2], code[2:]
        if exchange and exchange.lower() != prefix:
            raise ValueError("exchange mismatch")
        exchange = prefix
    if len(code) != 6 or not code.isdigit():
        raise ValueError("invalid ETF code")
    expected = "sh" if code.startswith("5") else "sz" if code.startswith("1") else ""
    if not expected or (exchange and exchange.lower() != expected):
        raise ValueError("invalid ETF exchange")
    return expected + code


@dataclass(frozen=True)
class Instrument:
    symbol: str
    name: str
    category: str = "unknown"
    region: str = "unknown"
    index_id: str = ""
    listed_on: str = ""
    active: bool = True
    verified: bool = False
    tick: int = 1000
    lot_size: int = 100
    settlement: int = 1
    limit_ratio: str = "0.10"
    source: str = ""
    updated_at: str = ""
    accounting_block: str = ""
    watched: bool = True

    @property
    def tradable(self) -> bool:
        return self.active and self.verified and bool(self.index_id) and not self.accounting_block

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class Quote:
    symbol: str
    at: str
    fetched_at: str
    last: int
    previous_close: int
    bid: int
    ask: int
    volume: int
    amount: int
    upper: int = 0
    lower: int = 0
    status: Literal["trading", "suspended", "unknown"] = "unknown"
    source: str = ""
    high: int = 0
    low: int = 0
    open: int = 0

    def to_dict(self) -> dict:
        return asdict(self)

    def fresh(self, now: datetime, seconds: int = 90) -> bool:
        age = (now - dt(self.at)).total_seconds()
        return 0 <= age <= seconds and dt(self.at).date() == now.astimezone(TZ).date()


@dataclass(frozen=True)
class Bar:
    day: str
    close: str
    amount: str | None
    open: str = "0"
    high: str = "0"
    low: str = "0"
    volume: int | None = 0
    source: str = "eastmoney"
    turnover_rate: str | None = None  # Ratio: 0.01 means 1%; never infer from current fund size.
    turnover_source: str = ""
    amount_source: str = ""

    def to_dict(self) -> dict:
        return asdict(self)
