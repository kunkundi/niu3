import math
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from app.core.calendar import Calendar
from app.core.types import Bar, Instrument, Quote, TZ, iso, units
from app.storage.db import Database, put_instrument, put_quote, set_state, settings
from app.trading.engine import Engine


def at(text="2026-09-07T09:35:00"):
    return datetime.fromisoformat(text).replace(tzinfo=TZ)


def bars(count=140, end="2026-09-04", start=1, growth=0.003, amount="150000000"):
    from decimal import Decimal

    day = datetime.fromisoformat(end).date()
    dates = []
    while len(dates) < count:
        if day.weekday() < 5:
            dates.append(day.isoformat())
        day -= timedelta(days=1)
    return [
        Bar(d, str(Decimal(str(start)) * (1 + Decimal(str(growth))) ** i), amount)
        for i, d in enumerate(reversed(dates))
    ]


def candles():
    history = []
    for i, b in enumerate(bars()):
        p = 1 + i * 0.002 + math.sin(i * 0.6) * 0.025
        history.append(
            replace(b, open=str(p - 0.004), close=str(p + 0.004), high=str(p + 0.012), low=str(p - 0.012))
        )
    history[-1] = replace(history[-1], open="1.3", close="1.305", high="1.31", low="1.24")
    return history


class Fixture:
    def __init__(self):
        self.temp = TemporaryDirectory(prefix="niuno3-test-")
        self.db = Database(Path(self.temp.name) / "test.sqlite3")
        self.calendar = Calendar()
        self.engine = Engine(self.db, self.calendar)
        self.instrument = Instrument("sh510300", "沪深300ETF", "equity", "CN", "沪深300指数", verified=True)
        with self.db.transaction() as conn:
            put_instrument(conn, self.instrument)
            set_state(conn, "buy_ready", True)
            set_state(conn, "actions:sh510300", {"at": iso(at())})

    def close(self):
        self.temp.cleanup()

    def quote(self, when=None, price="1.000", volume=1_000_000, **kwargs):
        now = when or at()
        payload = dict(
            symbol="sh510300",
            at=iso(now),
            fetched_at=iso(now),
            last=units(price),
            previous_close=units("1.00"),
            bid=units(price),
            ask=units(price),
            volume=volume,
            amount=units("50000000"),
            upper=units("1.1"),
            lower=units("0.9"),
            status="trading",
            source="test",
        )
        payload.update(kwargs)
        quote = Quote(**payload)
        with self.db.transaction() as conn:
            put_quote(conn, quote)
        return quote

    def order(self, side="BUY", quantity=1000, when=None, kind="rebalance", key=None):
        when = when or at()
        with self.db.transaction() as conn:
            version, _ = settings(conn)
            self.engine._order(
                conn,
                key or f"{side}:{iso(when)}",
                "sh510300",
                side,
                quantity,
                "测试信号",
                kind,
                when,
                version,
            )

    def buy(self, when=None, quantity=1000):
        when = when or at()
        self.quote(when - timedelta(seconds=10))
        self.order(quantity=quantity, when=when)
        self.quote(when + timedelta(seconds=30), volume=2_000_000)
        self.engine.match(when + timedelta(seconds=30))

    def rows(self, table):
        with self.db.connect() as conn:
            return [dict(r) for r in conn.execute(f"SELECT * FROM {table}")]
