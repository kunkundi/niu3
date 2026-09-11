from dataclasses import replace
from datetime import timezone
import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.core.calendar import Calendar
from app.core.types import Quote, iso, units
from app.dashboard.api import create_app, quote_payload
from tests.helpers import Fixture, at


class QuoteDisplayTests(unittest.TestCase):
    def quote(self, stamp, **changes):
        return Quote(
            "sh510300", iso(at(stamp)), iso(at(stamp)),
            units("1"), units("1"), units("1"), units("1"),
            1000, units("1000"), status="trading", **changes,
        )

    def test_display_tracks_sessions_without_relaxing_execution_freshness(self):
        cases = [
            ("2026-09-11T10:00:00", "2026-09-11T09:59:50", "live", False),
            ("2026-09-11T10:00:00", "2026-09-11T09:58:29", "delayed", True),
            ("2026-09-11T11:30:00", "2026-09-11T11:29:50", "break", False),
            ("2026-09-11T12:45:00", "2026-09-11T11:28:30", "break", False),
            ("2026-09-11T12:45:00", "2026-09-11T10:30:00", "delayed", True),
            ("2026-09-11T13:00:00", "2026-09-11T11:30:00", "delayed", True),
            ("2026-09-11T15:00:00", "2026-09-11T14:59:50", "closed", False),
            ("2026-09-11T16:15:00", "2026-09-11T15:58:00", "closed", False),
            ("2026-09-11T16:15:00", "2026-09-11T14:58:29", "delayed", True),
            ("2026-09-11T16:15:00", "2026-09-10T15:00:00", "delayed", True),
            ("2026-09-12T10:00:00", "2026-09-11T15:00:00", "closed", False),
            ("2026-09-14T09:29:59", "2026-09-11T15:00:00", "closed", False),
            ("2026-09-14T09:30:00", "2026-09-11T15:00:00", "delayed", True),
            ("2026-10-01T10:00:00", "2026-09-30T15:00:00", "closed", False),
            ("2027-01-04T10:00:00", "2026-12-31T15:00:00", "unknown", True),
            ("2026-09-11T16:15:00", "2026-09-11T16:16:00", "unknown", True),
        ]
        for current, stamp, status, warning in cases:
            with self.subTest(now=current, quote=stamp):
                quote, now = self.quote(stamp), at(current)
                raw = quote_payload(quote, now)
                displayed = quote_payload(quote, now, Calendar())
                display = displayed.pop("display")
                self.assertEqual((display["status"], display["warning"]), (status, warning))
                self.assertEqual(displayed, raw, "Display labels must not change prices or trading freshness")
                self.assertEqual(displayed["stale"], not quote.fresh(now))

    def test_closed_quote_does_not_require_an_executable_order_book(self):
        quote = replace(self.quote("2026-09-11T15:58:00"), status="unknown", bid=0, ask=0)
        display = quote_payload(quote, at("2026-09-11T16:15:00"), Calendar())["display"]
        self.assertEqual(display, {"status": "closed", "label": "已收盘", "warning": False})
        for changes in ({"last": 0}, {"previous_close": 0}, {"status": "suspended"}):
            with self.subTest(changes=changes):
                result = quote_payload(replace(quote, **changes), at("2026-09-11T16:15:00"), Calendar())
                self.assertTrue(result["display"]["warning"])
        live = replace(quote, at=iso(at("2026-09-11T10:00:00")))
        self.assertTrue(quote_payload(live, at("2026-09-11T10:00:00"), Calendar())["display"]["warning"])

    def test_weekend_label_uses_shanghai_date_even_with_a_utc_clock(self):
        now = at("2026-09-12T00:05:00").astimezone(timezone.utc)
        result = quote_payload(self.quote("2026-09-11T15:58:00"), now, Calendar())
        self.assertEqual(result["display"]["label"], "最近交易日 · 已收盘")

    def test_etf_list_and_detail_share_read_only_closed_state(self):
        f = Fixture()
        self.addCleanup(f.close)
        quote = f.quote(at("2026-09-11T15:58:00"))
        with patch.dict(os.environ, {"NIUNO3_ADMIN_PASSWORD": "quote-display-test"}):
            client = TestClient(create_app(f.db, f.calendar, clock=lambda: at("2026-09-11T16:15:00")))
        self.addCleanup(client.close)
        before = {table: f.rows(table) for table in ("quotes", "orders", "cash_ledger", "configs")}
        listed = client.get("/api/v1/etfs").json()["items"][0]["quote"]
        detail = client.get("/api/v1/etfs/sh510300").json()["quote"]
        self.assertEqual(listed, detail)
        self.assertEqual(listed["at"], quote.at)
        self.assertTrue(listed["stale"])
        self.assertEqual(listed["display"], {"status": "closed", "label": "已收盘", "warning": False})
        self.assertEqual({table: f.rows(table) for table in before}, before)
