import os
import unittest
from dataclasses import replace
from datetime import timedelta
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.core.types import iso, units, yuan
from app.dashboard.api import create_app
from app.storage.db import dump, put_instrument, set_state
from app.trading.actions import apply_actions, ingest_actions
from tests.helpers import Fixture, at


class DailyReturnTests(unittest.TestCase):
    def setUp(self):
        self.f = Fixture()
        self.now = at("2026-09-07T10:00:00")
        with patch.dict(os.environ, {"NIUNO3_ADMIN_PASSWORD": "test"}):
            self.client = TestClient(create_app(self.f.db, self.f.calendar, clock=lambda: self.now))

    def tearDown(self):
        self.client.close()
        self.f.close()

    def account(self):
        response = self.client.get("/api/v1/account")
        self.assertEqual(response.status_code, 200)
        return response.json()

    def old_buy(self):
        with self.f.db.transaction() as conn:
            set_state(conn, "actions:sh510300", {"at": iso(at("2026-09-04T09:30:00"))})
        self.f.buy(when=at("2026-09-04T09:35:00"))
        self.assertEqual(sum(f["quantity"] for f in self.f.rows("fills")), 1000)
        with self.f.db.transaction() as conn:
            set_state(conn, "actions:sh510300", {"at": iso(self.now)})

    def current_quote(self, price="1.05", previous="1.02", **kwargs):
        self.f.quote(self.now, price=price, previous_close=units(previous), **kwargs)

    def sell(self, quantity=400, price="1.04"):
        when = at("2026-09-07T09:40:00")
        self.f.quote(when - timedelta(seconds=10), price=price, volume=3_000_000)
        self.f.order(side="SELL", quantity=quantity, when=when)
        self.f.quote(when + timedelta(seconds=30), price=price, volume=4_000_000)
        self.f.engine.match(when + timedelta(seconds=30))
        fills = [f for f in self.f.rows("fills") if f["side"] == "SELL"]
        self.assertEqual(sum(f["quantity"] for f in fills), quantity)
        return sum(f["gross"] - f["fee"] for f in fills)

    def raw_close(self, price="1.02", day="2026-09-04"):
        with self.f.db.transaction() as conn:
            conn.execute(
                "INSERT INTO bars VALUES(?,?,?,?,?,?)",
                ("sh510300", "raw", day, dump({"close": price}), "test", day + "T15:00:00+08:00"),
            )

    def action(self, kind="dividend", pay="2026-09-08", verified=True):
        with self.f.db.transaction() as conn:
            ingest_actions(conn, [{
                "id": kind, "symbol": "sh510300", "kind": kind,
                "record_day": "2026-09-04", "ex_day": "2026-09-07", "pay_day": pay,
                "value": "0.1" if kind == "dividend" else "2", "verified": verified, "source": "test",
            }])
            apply_actions(conn, self.now)

    def assert_profit(self, expected, account=None):
        data = account or self.account()
        self.assertAlmostEqual(data["daily_return"]["pnl"], expected, places=6)
        if data["positions"]:
            self.assertAlmostEqual(data["positions"][0]["daily_pnl"], expected, places=6)
        return data

    def test_account_total_profit_uses_configured_capital_and_includes_purchase_fees(self):
        self.f.db.change_config({"initial_cash": "123456.78"}, self.now)
        data = self.account()
        self.assertEqual(data["initial_cash"], 123456.78)
        self.assertEqual(data["total_pnl"], 0)
        self.f.buy()
        for price, expected in (("0.95", -50.1), ("1.05", 49.9)):
            with self.subTest(price=price):
                self.now += timedelta(seconds=1)
                self.current_quote(price=price)
                data = self.account()
                self.assertEqual(data["initial_cash"], 123456.78)
                self.assertAlmostEqual(data["total_pnl"], expected)
                self.assertAlmostEqual(data["return_pct"], expected / 123456.78)

    def test_new_position_uses_actual_purchase_cost_and_fees(self):
        self.f.buy()
        self.current_quote(previous="0.8")
        data = self.assert_profit(49.9)
        self.assertEqual(data["daily_return"]["day"], "2026-09-07")
        self.assertTrue(data["daily_return"]["is_today"])
        self.assertEqual(data["daily_return"]["warning"], "")

    def test_overnight_hold_uses_previous_close_not_historical_cost(self):
        self.old_buy()
        self.current_quote()
        data = self.assert_profit(30)
        self.assertAlmostEqual(data["positions"][0]["pnl"], 49.9)
        self.assertAlmostEqual(data["total_pnl"], 49.9)

    def test_addition_counts_only_post_purchase_movement_and_new_fee(self):
        self.old_buy()
        self.f.buy(quantity=500)
        self.current_quote()
        self.assert_profit(54.95)

    def test_partial_sale_includes_sold_shares_day_profit(self):
        self.old_buy()
        net_sale = self.sell()
        self.current_quote()
        data = self.assert_profit(yuan(600 * units("1.05") + net_sale - 1000 * units("1.02")))
        self.assertAlmostEqual(data["total_pnl"], 45.86)
        self.assertAlmostEqual(data["total_pnl"], data["realized"] + data["unrealized"])

    def test_fully_sold_position_contributes_without_current_quote(self):
        self.old_buy()
        self.raw_close()
        net_sale = self.sell(1000)
        data = self.assert_profit(yuan(net_sale - 1000 * units("1.02")))
        self.assertEqual(data["positions"], [])
        self.assertEqual(data["daily_return"]["closed_pnl"], data["daily_return"]["pnl"])
        self.assertAlmostEqual(data["total_pnl"], 39.8)

    def test_same_day_round_trip_includes_both_sides_fees(self):
        with self.f.db.transaction() as conn:
            put_instrument(conn, replace(self.f.instrument, settlement=0))
        self.f.buy()
        net_sale = self.sell(1000)
        self.assert_profit(yuan(net_sale - units("1000.1")))

    def test_total_combines_holdings_and_closed_symbols_and_requires_all_inputs(self):
        self.old_buy()
        self.raw_close()
        net_sale = self.sell(1000)
        with self.f.db.transaction() as conn:
            # Add an independent opening holding without changing the immutable
            # history of the fully sold first ETF.
            put_instrument(conn, replace(self.f.instrument, symbol="sh510500", name="中证500ETF"))
            conn.execute(
                "INSERT INTO position_ledger(key,symbol,delta,at,kind) VALUES('second','sh510500',1000,?,'trade')",
                (iso(at("2026-09-04T10:00:00")),),
            )
            conn.execute(
                "INSERT INTO lots(symbol,acquired_day,available_day,quantity,cost,risk_cost,high,fill_id) "
                "VALUES('sh510500','2026-09-04','2026-09-07',1000,?,?,?,99999)",
                (units(1000), units(1000), units(1)),
            )
        # The first ETF is closed; without the second one's quote the total must
        # not silently publish just the completed sale's contribution.
        data = self.account()
        self.assertIsNone(data["daily_return"]["pnl"])
        self.f.quote(self.now, symbol="sh510500", price="1.05", previous_close=units("1.02"))
        data = self.account()
        closed = yuan(net_sale - units(1020))
        self.assertAlmostEqual(data["daily_return"]["closed_pnl"], closed)
        self.assertAlmostEqual(data["daily_return"]["pnl"], closed + 30)
        self.assertEqual(data["positions"][0]["daily_pnl"], 30)

    def test_missing_data_does_not_display_zero_or_partial_total(self):
        self.old_buy()
        data = self.account()
        self.assertIsNone(data["daily_return"]["pnl"])
        self.assertIsNone(data["positions"][0]["daily_pnl"])
        self.assertIn("待齐", data["daily_return"]["warning"])
        self.current_quote(previous="0")
        self.assertIsNone(self.account()["daily_return"]["pnl"])

    def test_old_raw_close_does_not_substitute_for_exact_previous_session(self):
        self.old_buy()
        self.raw_close(day="2026-09-03")
        self.current_quote(previous="0")
        self.assertIsNone(self.account()["daily_return"]["pnl"])

    def test_latest_day_quote_excludes_future_quotes_and_marks_delays(self):
        self.old_buy()
        self.current_quote()
        self.f.quote(self.now + timedelta(minutes=10), price="1.09", previous_close=units("1.02"))
        self.assert_profit(30)
        self.now += timedelta(minutes=5)
        data = self.assert_profit(30)
        self.assertEqual(data["positions"][0]["daily_pnl_warning"], "行情待更新")
        self.assertEqual(data["daily_return"]["warning"], "部分行情待更新")

    def test_weekend_and_preopen_use_labeled_last_session_with_its_quotes(self):
        self.old_buy()
        self.f.quote(at("2026-09-04T15:00:00"), price="1.05")
        self.f.quote(at("2026-09-07T09:20:00"), price="1.09")
        for stamp in ("2026-09-06T12:00:00", "2026-09-07T09:29:59"):
            self.now = at(stamp)
            data = self.assert_profit(49.9)
            self.assertEqual(data["daily_return"]["day"], "2026-09-04")
            self.assertFalse(data["daily_return"]["is_today"])
            self.assertEqual(data["daily_return"]["warning"], "")

    def test_dividend_accrues_on_ex_day_and_payment_is_not_counted_twice(self):
        self.old_buy()
        self.raw_close(price="1.00")
        self.action()
        self.current_quote(price="0.90", previous="0.90")
        data = self.assert_profit(0)
        self.assertAlmostEqual(data["total_pnl"], -0.1)
        self.assertEqual(data["receivable"], 100)
        self.now = at("2026-09-08T10:00:00")
        with self.f.db.transaction() as conn:
            apply_actions(conn, self.now)
        self.current_quote(price="0.91", previous="0.90")
        data = self.assert_profit(10)
        self.assertAlmostEqual(data["total_pnl"], 9.9)
        self.assertEqual(data["receivable"], 0)

    def test_split_uses_original_shares_and_unadjusted_close(self):
        self.old_buy()
        self.raw_close(price="1.00")
        self.action(kind="split")
        self.current_quote(price="0.50", previous="0.50")
        self.assert_profit(0)

    def test_ex_reference_is_never_used_as_raw_close_on_action_day(self):
        self.old_buy()
        self.action()
        self.current_quote(price="0.90", previous="0.90")
        self.assertIsNone(self.account()["daily_return"]["pnl"])

    def test_unverified_action_is_unavailable_instead_of_a_fabricated_loss(self):
        self.old_buy()
        self.raw_close(price="1.00")
        self.action(verified=False)
        self.current_quote(price="0.90", previous="0.90")
        self.assertIsNone(self.account()["daily_return"]["pnl"])

    def test_cash_only_capital_changes_are_not_income_and_endpoint_is_read_only(self):
        with self.f.db.transaction() as conn:
            conn.execute(
                "INSERT INTO cash_ledger(key,delta,kind,at) VALUES('extra',?,'capital_adjustment',?)",
                (units(5000), iso(self.now)),
            )
        tables = ("lots", "position_ledger", "cash_ledger", "fills", "actions", "equity", "orders", "configs")
        before = {t: self.f.rows(t) for t in tables}
        self.assert_profit(0)
        self.assertEqual(before, {t: self.f.rows(t) for t in tables})

    def test_unknown_calendar_does_not_report_zero(self):
        self.now = at("2035-09-07T10:00:00")
        data = self.account()
        self.assertIsNone(data["daily_return"]["pnl"])
        self.assertEqual(data["daily_return"]["warning"], "交易日历待核验")


if __name__ == "__main__":
    unittest.main()
