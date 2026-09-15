import json
import sqlite3
import unittest
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from pathlib import Path
from unittest.mock import patch

from app.core.types import iso, units
from app.storage.db import dump, put_instrument, set_state, settings
from app.trading.account import reconcile
from app.trading.engine import fee_for
from scripts.repair_historical_slippage import apply_plan, backup_database, connect, make_plan, source_data
from tests.helpers import Fixture, at


class HistoricalSlippageRepairTests(unittest.TestCase):
    def setUp(self):
        with patch("app.storage.db.now_cn", return_value=at("2026-09-06T09:00:00")):
            self.f = Fixture()
        self.number = 0
        self.instrument = replace(self.f.instrument, settlement=0)
        with self.f.db.transaction() as conn:
            put_instrument(conn, self.instrument)
            version, config = settings(conn)
            conn.execute("UPDATE configs SET payload=? WHERE id=?", (dump({**config.model_dump(mode="json"), "slippage_bps": "5"}), version))

    def tearDown(self):
        self.f.close()

    def fill(self, side, quantity, book, key):
        self.number += 1
        now = at() + timedelta(seconds=self.number * 30)
        quote = self.f.quote(now, price=book, volume=1_000_000 + self.number * 100000)
        with self.f.db.transaction() as conn:
            version, config = settings(conn)
            order = conn.execute("SELECT * FROM orders WHERE key=?", (key,)).fetchone()
            if order is None:
                self.f.engine._order(conn, key, quote.symbol, side, 1000, "legacy fixture", "rebalance", now, version)
                order = conn.execute("SELECT * FROM orders WHERE key=?", (key,)).fetchone()
            quote_id = conn.execute("SELECT id FROM quotes WHERE at=?", (iso(now),)).fetchone()[0]
            raw = Decimal(quote.ask if side == "BUY" else quote.bid) * (Decimal("1.0005") if side == "BUY" else Decimal("0.9995"))
            rounding = ROUND_CEILING if side == "BUY" else ROUND_FLOOR
            price = int((raw / 1000).to_integral_value(rounding=rounding)) * 1000
            gross, fees = conn.execute("SELECT COALESCE(SUM(gross),0),COALESCE(SUM(fee),0) FROM fills WHERE order_id=?", (order["id"],)).fetchone()
            fee = max(0, fee_for(gross + quantity * price, config) - fees)
            self.f.engine._fill(conn, order, quote_id, price, quantity, fee, now, self.instrument)
        self.f.engine.record_equity(now + timedelta(seconds=1))

    def history(self, *, close=False):
        self.f.engine.record_equity(at() - timedelta(seconds=1))
        self.fill("BUY", 600, "1.000", "buy")
        self.fill("BUY", 400, "1.002", "buy")
        self.fill("SELL", 300, "1.010", "sell")
        if close:
            self.fill("SELL", 700, "1.020", "sell")
        with self.f.db.transaction() as conn:
            conn.execute("UPDATE orders SET status='cancelled' WHERE status IN ('pending','partial')")

    def plan(self):
        with self.f.db.connect() as conn:
            conn.execute("BEGIN")
            return make_plan(conn, self.number)

    def test_fifo_partial_sale_cash_equity_and_audit_are_rebuilt_idempotently(self):
        self.history()
        untouched = {table:self.f.rows(table) for table in ("orders", "configs", "position_ledger", "quotes", "instruments")}
        before_lots = self.f.rows("lots")
        plan = self.plan()
        self.assertEqual(plan["summary"]["cash_increase"], 1.3)
        self.assertEqual(plan["summary"]["realized_change"], .6)
        self.assertEqual(plan["summary"]["rows_changed"]["equity"], 3)
        self.assertEqual([item["after"]["price"] for item in plan["changes"]["fills"]], [units("1"), units("1.002"), units("1.010")])
        with self.f.db.transaction() as conn:
            self.assertTrue(apply_plan(conn, plan, backup_path="test-backup.sqlite3"))
        self.assertEqual({table:self.f.rows(table) for table in untouched}, untouched)
        lots = self.f.rows("lots")
        self.assertEqual([row["quantity"] for row in lots], [300, 400])
        self.assertEqual([row["cost"] for row in lots], [units("300.03"), units("400.84")])
        self.assertEqual([row["high"] for row in lots], [row["high"] for row in before_lots])
        self.assertEqual(self.f.rows("fills")[-1]["realized"], units("2.94"))
        audit = self.f.rows("ledger_repairs")
        self.assertEqual(len(audit), 1)
        self.assertEqual(json.loads(audit[0]["payload"]), plan)
        self.assertEqual(self.plan()["summary"]["repriced_fills"], 0)
        with self.f.db.transaction() as conn:
            self.assertFalse(apply_plan(conn, make_plan(conn, self.number), backup_path="unused"))
            self.assertEqual(reconcile(conn), [])
        self.assertEqual(self.f.rows("ledger_repairs"), audit)
        with self.f.db.connect() as conn:
            for sql in ("UPDATE fills SET price=1", "DELETE FROM fills", "UPDATE cash_ledger SET delta=0", "DELETE FROM cash_ledger", "UPDATE position_ledger SET delta=0"):
                with self.assertRaisesRegex(sqlite3.IntegrityError, "immutable"):
                    conn.execute(sql)

    def test_cumulative_minimum_commission_and_closed_lots(self):
        with self.f.db.transaction() as conn:
            version, config = settings(conn)
            conn.execute("UPDATE configs SET payload=? WHERE id=?", (dump({**config.model_dump(mode="json"), "slippage_bps":"5", "minimum_commission":"5"}), version))
        self.history(close=True)
        plan = self.plan()
        self.assertEqual([item["after"]["fee"] for item in plan["changes"]["fills"]], [units(5), 0, units(5), 0])
        self.assertEqual(plan["summary"]["cash_increase"], 2)
        self.assertEqual(plan["summary"]["realized_change"], 2)
        with self.f.db.transaction() as conn:
            apply_plan(conn, plan, backup_path="test-backup.sqlite3")
            self.assertEqual(reconcile(conn), [])
        self.assertTrue(all(row["quantity"] == row["cost"] == row["risk_cost"] == 0 for row in self.f.rows("lots")))

    def test_failure_rolls_back_every_update_audit_and_trigger_change(self):
        self.history()
        plan = self.plan()
        with self.f.db.connect() as conn:
            before = source_data(conn)
        with self.assertRaisesRegex(ValueError, "修复后账本核对失败"):
            with self.f.db.transaction() as conn, patch("scripts.repair_historical_slippage.reconcile", return_value=["injected failure"]):
                apply_plan(conn, plan, backup_path="test-backup.sqlite3")
        with self.f.db.connect() as conn:
            self.assertEqual(source_data(conn), before)
            self.assertIsNone(conn.execute("SELECT name FROM sqlite_master WHERE name='ledger_repairs'").fetchone())
            with self.assertRaisesRegex(sqlite3.IntegrityError, "immutable fills"):
                conn.execute("UPDATE fills SET price=0")

    def test_changed_source_rejects_a_stale_plan(self):
        self.history()
        plan = self.plan()
        self.f.engine.record_equity(at() + timedelta(minutes=20))
        with self.f.db.transaction() as conn:
            with self.assertRaisesRegex(ValueError, "清单已过期"):
                apply_plan(conn, plan, backup_path="unused")

    def test_missing_quote_is_rejected_without_guessing(self):
        self.history()
        with self.f.db.transaction() as conn:
            conn.execute("DELETE FROM quotes WHERE id=(SELECT quote_id FROM fills WHERE id=1)")
        with self.assertRaisesRegex(ValueError, "缺少原始成交报价"):
            self.plan()

    def test_missing_legacy_setting_does_not_guess_the_old_slippage(self):
        self.history()
        with self.f.db.transaction() as conn:
            version, config = settings(conn)
            conn.execute("UPDATE configs SET payload=? WHERE id=?", (config.model_dump_json(), version))
        with self.assertRaisesRegex(ValueError, "历史价格无法"):
            self.plan()

    def test_active_orders_corporate_actions_and_ambiguous_equity_are_rejected(self):
        self.history()
        with self.f.db.transaction() as conn:
            conn.execute("UPDATE orders SET status='partial' WHERE key='sell'")
        with self.assertRaisesRegex(ValueError, "待撮合订单"):
            self.plan()
        with self.f.db.transaction() as conn:
            conn.execute("UPDATE orders SET status='cancelled'")
            conn.execute("INSERT INTO actions(id,symbol,kind,record_day,ex_day,pay_day,value,verified,source,status) VALUES('test','sh510300','dividend','2026-09-07','2026-09-08','2026-09-09','0.01',1,'test','recorded')")
        with self.assertRaisesRegex(ValueError, "持有期间存在公司行动"):
            self.plan()
        with self.f.db.transaction() as conn:
            conn.execute("DELETE FROM actions WHERE id='test'")
            conn.execute("UPDATE equity SET cash=cash+1,nav=nav+1")
        with self.assertRaisesRegex(ValueError, "估值现金时间线不符"):
            self.plan()

    def test_backup_is_exclusive_and_contains_the_exact_unmodified_evidence(self):
        self.history()
        plan = self.plan()
        backup = Path(self.f.temp.name) / "before.sqlite3"
        with self.f.db.transaction():
            backup_database(self.f.db.path, backup, plan["source_sha256"])
        self.assertEqual(backup.stat().st_mode & 0o777, 0o600)
        with connect(backup, readonly=True) as conn:
            conn.execute("BEGIN")
            self.assertEqual(make_plan(conn, self.number), plan)
        with self.assertRaises(FileExistsError):
            backup_database(self.f.db.path, backup, plan["source_sha256"])

    def test_peak_is_rebased_on_saved_valid_valuations_only_when_repairing(self):
        self.history()
        with self.f.db.transaction() as conn:
            set_state(conn, "peak_nav", units(200000))
            conn.execute("UPDATE equity SET market_value=market_value+?,nav=nav+?,stale=0 WHERE at=(SELECT MAX(at) FROM equity)", (units(10), units(10)))
        plan = self.plan()
        self.assertEqual(plan["peak"]["before"], dump(units(200000)))
        self.assertEqual(json.loads(plan["peak"]["after"]), plan["changes"]["equity"][-1]["after"]["nav"])


if __name__ == "__main__":
    unittest.main()
