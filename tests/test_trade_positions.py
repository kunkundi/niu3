import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.dashboard.api import create_app
from tests.helpers import Fixture, at


class TradePositionTests(unittest.TestCase):
    def setUp(self):
        self.f = Fixture()
        with patch.dict(os.environ, {"NIUNO3_ADMIN_PASSWORD": "niuno3-test-password-2026"}):
            self.client = TestClient(create_app(self.f.db, self.f.calendar, clock=at))

    def tearDown(self):
        self.client.close()
        self.f.close()

    def fill(
        self, side, quantity, *, order_id=None, ledger=True, delta=None, symbol="sh510300", kind="rebalance"
    ):
        if order_id is None:
            self.f.order(side=side, quantity=quantity, key=f"test:{len(self.f.rows('orders'))}", kind=kind)
            order_id = self.f.rows("orders")[-1]["id"]
        with self.f.db.transaction() as conn:
            fill_id = conn.execute(
                "INSERT INTO fills(order_id,quote_id,symbol,side,quantity,price,gross,fee,at) "
                "VALUES(?,?,?,?,?,1000000,?,0,'2026-09-07T09:35:30+08:00')",
                (order_id, len(self.f.rows("fills")) + 1, symbol, side, quantity, quantity * 1000000),
            ).lastrowid
            if ledger:
                conn.execute(
                    "INSERT INTO position_ledger(key,symbol,delta,at,kind) "
                    "VALUES(?,?,?,'2026-09-07T09:35:30+08:00','trade')",
                    (
                        f"fill:{fill_id}",
                        symbol,
                        delta if delta is not None else quantity if side == "BUY" else -quantity,
                    ),
                )
        return order_id

    def trades(self, query=""):
        response = self.client.get("/api/v1/trades" + query)
        self.assertEqual(response.status_code, 200)
        return response.json()

    def test_partial_open_add_reduce_close_and_reopen_stay_correct_across_pages(self):
        opening_order = self.fill("BUY", 100)
        self.fill("BUY", 200, order_id=opening_order)
        self.fill("BUY", 100)
        closing_order = self.fill("SELL", 100)
        self.fill("SELL", 300, order_id=closing_order)
        self.fill("BUY", 100)
        before = {t: self.f.rows(t) for t in ("orders", "fills", "position_ledger", "lots", "cash_ledger")}
        items = list(reversed(self.trades()["items"]))
        self.assertEqual(
            [i["position_effect"] for i in items], ["open", "open", "add", "reduce", "close", "open"]
        )
        self.assertEqual([i["position_before"] for i in items], [0, 100, 300, 400, 300, 0])
        self.assertEqual([i["position_after"] for i in items], [100, 300, 400, 300, 0, 100])
        for offset, item in enumerate(reversed(items)):
            page = self.trades(f"?symbol=sh510300&limit=1&offset={offset}")
            self.assertEqual(page["total"], 6)
            self.assertEqual(page["items"], [item])
        self.assertEqual(self.trades("?offset=6")["items"], [])
        self.assertEqual({t: self.f.rows(t) for t in before}, before)

    def test_split_shares_are_included_and_other_symbols_are_independent(self):
        self.fill("BUY", 100)
        with self.f.db.transaction() as conn:
            conn.execute(
                "INSERT INTO position_ledger(key,symbol,delta,at,kind) "
                "VALUES('action:split','sh510300',100,'2026-09-07T09:35:30+08:00','split')"
            )
        self.fill("SELL", 100)
        self.fill("BUY", 100, symbol="sz159865")
        self.fill("SELL", 100)
        items = list(reversed(self.trades("?symbol=sh510300")["items"]))
        self.assertEqual([i["position_effect"] for i in items], ["open", "reduce", "close"])
        self.assertEqual([i["position_after"] for i in items], [100, 100, 0])
        other = self.trades("?symbol=sz159865")["items"][0]
        self.assertEqual(
            (other["position_before"], other["position_after"], other["position_effect"]), (0, 100, "open")
        )

    def test_incomplete_sell_never_looks_like_a_completed_liquidation(self):
        self.fill("BUY", 300)
        closing_order = self.fill("SELL", 100)
        partial = self.trades()["items"][0]
        self.assertEqual(partial["position_effect"], "reduce")
        self.fill("SELL", 200, order_id=closing_order)
        self.assertEqual(self.trades()["items"][0]["position_effect"], "close")
        self.assertEqual(self.trades("?limit=1&offset=1")["items"][0], partial)

    def test_missing_or_inconsistent_ledger_does_not_guess_an_effect(self):
        self.fill("BUY", 100, ledger=False)
        self.fill("BUY", 100, delta=200)
        self.fill("SELL", 500)
        for item in self.trades()["items"]:
            self.assertEqual(item["position_effect"], "unknown")
            self.assertIsNone(item["position_before"])
            self.assertIsNone(item["position_after"])

    def test_real_engine_fills_have_the_same_opening_and_addition_effects(self):
        self.f.buy()
        self.f.buy(when=at("2026-09-07T10:00:00"))
        items = list(reversed(self.trades()["items"]))
        self.assertEqual([i["position_effect"] for i in items], ["open", "add"])
        self.assertEqual([i["position_after"] for i in items], [1000, 2000])

    def test_trades_expose_t_order_kinds_without_relabeling_position_effects_or_including_pending_orders(
        self,
    ):
        self.fill("BUY", 1000)
        sell_order = self.fill("SELL", 100, kind="t_sell")
        self.fill("SELL", 100, order_id=sell_order)
        self.fill("BUY", 200, kind="t_buy")
        self.fill("SELL", 100, kind="intraday")
        self.f.order(side="BUY", quantity=100, key="pending-t-buy", kind="t_buy")
        before = {t: self.f.rows(t) for t in ("orders", "fills", "position_ledger", "lots", "cash_ledger")}
        response = self.trades("?symbol=sh510300")
        self.assertEqual(response["total"], 5)
        items = list(reversed(response["items"]))
        self.assertEqual(
            [i["order_kind"] for i in items], ["rebalance", "t_sell", "t_sell", "t_buy", "intraday"]
        )
        self.assertEqual([i["position_effect"] for i in items], ["open", "reduce", "reduce", "add", "reduce"])
        self.assertEqual(self.trades("?symbol=sh510300&limit=1&offset=1")["items"], [items[3]])
        self.assertEqual({t: self.f.rows(t) for t in before}, before)


if __name__ == "__main__":
    unittest.main()
