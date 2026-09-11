import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.core.types import units
from app.dashboard.api import create_app
from app.trading.actions import ingest_actions
from tests.helpers import Fixture, at


class AccountActionTests(unittest.TestCase):
    def setUp(self):
        self.f = Fixture()
        with patch.dict(os.environ, {"NIUNO3_ADMIN_PASSWORD": "niuno3-test-password-2026"}):
            self.client = TestClient(
                create_app(self.f.db, self.f.calendar, clock=lambda: at("2026-09-30T10:00:00"))
            )

    def tearDown(self):
        self.client.close()
        self.f.close()

    def movement(self, delta, day, *, symbol="sh510300", key=None, kind="trade"):
        with self.f.db.transaction() as conn:
            conn.execute(
                "INSERT INTO position_ledger(key,symbol,delta,at,kind) VALUES(?,?,?,?,?)",
                (
                    key or f"test:{len(self.f.rows('position_ledger'))}",
                    symbol,
                    delta,
                    day + "T10:00:00+08:00",
                    kind,
                ),
            )

    def action(
        self,
        action_id,
        record="2026-09-07",
        ex="2026-09-08",
        pay="2026-09-30",
        *,
        kind="dividend",
        symbol="sh510300",
        status="pending",
        entitlement=0,
        receivable=0,
    ):
        with self.f.db.transaction() as conn:
            ingest_actions(
                conn,
                [
                    {
                        "id": action_id,
                        "symbol": symbol,
                        "kind": kind,
                        "record_day": record,
                        "ex_day": ex,
                        "pay_day": pay,
                        "value": "0.1" if kind == "dividend" else "2",
                        "verified": True,
                        "source": "fixture",
                    }
                ],
            )
            conn.execute(
                "UPDATE actions SET status=?,entitlement=?,receivable=? WHERE id=?",
                (status, units(entitlement), units(receivable), action_id),
            )

    def items(self):
        response = self.client.get("/api/v1/actions")
        self.assertEqual(response.status_code, 200)
        return response.json()["items"]

    def test_unrelated_history_and_buying_after_record_date_are_excluded(self):
        self.movement(1000, "2026-09-07")
        self.action("old-dividend", record="2025-09-01", ex="2025-09-02", status="paid")
        self.action("old-split", record="2025-09-02", ex="2025-09-02", kind="split", status="applied")
        self.movement(0, "2025-09-02", key="action:old-split", kind="split")
        self.action("before-record", record="2026-09-04", ex="2026-09-08")
        self.action("never-held", symbol="sz159865")
        self.assertEqual(self.items(), [])

    def test_dividend_remains_after_sale_even_when_payment_is_later(self):
        self.movement(1000, "2026-09-07")
        self.movement(-1000, "2026-09-08")
        self.action("late-payment", status="entitled", entitlement=100, receivable=100)
        self.action("unprocessed", pay="2026-10-01")
        before = {t: self.f.rows(t) for t in ("actions", "position_ledger", "cash_ledger", "lots", "orders")}
        items = {i["id"]: i for i in self.items()}
        self.assertEqual(set(items), {"late-payment", "unprocessed"})
        self.assertEqual(items["late-payment"]["receivable"], 100)
        self.assertEqual({t: self.f.rows(t) for t in before}, before)

    def test_holding_gaps_and_same_day_round_trip_do_not_create_entitlement(self):
        self.movement(1000, "2026-09-07")
        self.movement(-1000, "2026-09-08")
        self.movement(500, "2026-09-12")
        self.action("no-holdings-at-record", record="2026-09-10", ex="2026-09-12")
        self.action("sold-on-record", record="2026-09-08", ex="2026-09-09")
        self.action("reopened", record="2026-09-12", ex="2026-09-14")
        self.movement(100, "2026-09-12", symbol="sz159865")
        self.movement(-100, "2026-09-12", symbol="sz159865")
        self.action("same-day-round-trip", record="2026-09-12", ex="2026-09-14", symbol="sz159865")
        self.assertEqual([i["id"] for i in self.items()], ["reopened"])

    def test_split_uses_pre_effective_holdings_and_remains_after_liquidation(self):
        self.movement(1000, "2026-09-07")
        self.action("held-split", kind="split", status="applied")
        self.movement(1000, "2026-09-08", key="action:held-split", kind="split")
        self.movement(-2000, "2026-09-09")
        self.action("after-sale", ex="2026-09-10", kind="split")
        self.movement(1000, "2026-09-08", symbol="sz159865")
        self.action("bought-on-effective-day", kind="split", symbol="sz159865")
        self.assertEqual([i["id"] for i in self.items()], ["held-split"])

    def test_booked_entitlement_and_share_adjustments_are_never_hidden(self):
        self.action("paid", status="paid", entitlement=100)
        self.action("receivable", status="entitled", receivable=50)
        self.action("actual-split", kind="split", status="applied")
        self.movement(100, "2026-09-08", key="action:actual-split", kind="split")
        self.assertEqual({i["id"] for i in self.items()}, {"paid", "receivable", "actual-split"})

    def test_relevance_is_filtered_before_the_200_record_limit(self):
        self.movement(1000, "2026-09-07")
        self.action("relevant")
        for i in range(205):
            self.action(f"unrelated:{i}", record="2026-09-28", ex="2026-09-29", symbol="sz159865")
        self.assertEqual([i["id"] for i in self.items()], ["relevant"])


if __name__ == "__main__":
    unittest.main()
