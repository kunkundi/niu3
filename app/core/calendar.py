from __future__ import annotations

import json
from bisect import bisect_left, bisect_right
from datetime import date, datetime, time, timedelta
from pathlib import Path

from app.core.config import ROOT
from app.core.types import TZ


class Calendar:
    """Only explicitly covered exchange dates are executable. Never guess next year's holidays."""

    def __init__(self, payload: dict | None = None):
        self.payload = payload or json.loads((ROOT / "config/calendar.json").read_text(encoding="utf-8"))
        self.start = date.fromisoformat(self.payload["start"])
        self.end = date.fromisoformat(self.payload["end"])
        closed = set(self.payload["closed"])
        self.days = []
        cursor = self.start
        while cursor <= self.end:
            if cursor.weekday() < 5 and cursor.isoformat() not in closed:
                self.days.append(cursor.isoformat())
            cursor += timedelta(days=1)

    @classmethod
    def from_file(cls, path: Path):
        return cls(json.loads(path.read_text(encoding="utf-8")))

    def known(self, day: date) -> bool:
        return self.start <= day <= self.end

    def is_open(self, day: date) -> bool:
        return day.isoformat() in self.days

    def previous(self, day: date) -> str:
        if not self.known(day):
            raise ValueError("calendar out of coverage")
        index = bisect_left(self.days, day.isoformat()) - 1
        if index < 0:
            raise ValueError("previous trading date unavailable")
        return self.days[index]

    def next(self, day: date) -> str:
        if not self.known(day):
            raise ValueError("calendar out of coverage")
        index = bisect_right(self.days, day.isoformat())
        if index == len(self.days):
            raise ValueError("next trading date unavailable")
        return self.days[index]

    def session(self, now: datetime) -> bool:
        local = now.astimezone(TZ)
        clock = local.time()
        return self.is_open(local.date()) and (
            time(9, 30) <= clock < time(11, 30) or time(13) <= clock < time(15)
        )
