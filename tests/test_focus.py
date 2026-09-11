import unittest
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

from app.storage.db import Database, dump, put_instrument, get_state
from app.strategies.focus import group_for, FOCUS_POLICY
from app.trading.account import reconcile
from tests.helpers import Fixture, at


class FocusTests(unittest.TestCase):
    def setUp(self):
        self.f = Fixture()
        self.a = replace(self.f.instrument, index_id="中证全指半导体产品与设备指数")
        self.b = replace(self.a, symbol="sz159995", name="芯片 ETF", index_id="国证半导体芯片指数")

    def tearDown(self):
        self.f.close()

    def test_broad_indices_aliases_and_markets_do_not_collapse_to_asset_class(self):
        a = replace(self.a, index_id="沪深 300 指数")
        self.assertEqual(group_for(a), group_for(replace(a, index_id="沪深300")))
        self.assertNotEqual(group_for(a), group_for(replace(a, index_id="中证500指数")))
        self.assertNotEqual(
            group_for(self.a), group_for(replace(self.b, category="cross_border", region="OVERSEAS"))
        )
        self.assertEqual(group_for(replace(a, verified=False))[0], "")
        self.assertEqual(group_for(replace(a, index_id="中证酒指数"))[1], "食品饮料")
        self.assertEqual(
            group_for(replace(a, index_id="中证科技龙头指数")),
            group_for(replace(a, index_id="中证新兴科技100策略指数")),
        )

    def test_execution_allows_manually_selected_second_holding_in_same_direction(self):
        with self.f.db.transaction() as conn:
            put_instrument(conn, self.a)
            put_instrument(conn, self.b)
        self.f.buy()
        now = at() + timedelta(seconds=60)
        quote = replace(self.f.quote(now, volume=3_000_000), symbol=self.b.symbol)
        with self.f.db.transaction() as conn:
            from app.storage.db import settings, set_state

            set_state(conn, f"actions:{self.b.symbol}", {"at": quote.at})
            self.f.engine._order(conn, "same-group", self.b.symbol, "BUY", 1000, "test", "rebalance", at(), 1)
            order = conn.execute("SELECT * FROM orders WHERE key='same-group'").fetchone()
            reason = self.f.engine._blocker(conn, order, self.b, quote, now, settings(conn)[1])
            self.assertEqual(reason, "")

    def test_policy_upgrade_preserves_ledger_and_invalidates_only_old_plans(self):
        self.f.buy()
        self.f.order(when=at() + timedelta(minutes=1), key="legacy")
        payload = dump({"targets": {self.a.symbol: ".2"}, "rows": []})
        with self.f.db.transaction() as conn:
            conn.execute(
                "INSERT INTO plans(as_of,execute_day,config_id,at,payload) VALUES(?,?,1,?,?)",
                ("2026-09-04", "2026-09-07", "2026-09-04T15:30:00+08:00", payload),
            )
            conn.execute("DELETE FROM state WHERE key='focus_policy'")
        for _ in range(2):
            Database(Path(self.f.db.path))
        with self.f.db.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM configs").fetchone()[0], 2)
            self.assertEqual(conn.execute("SELECT payload FROM plans").fetchone()[0], payload)
            self.assertEqual(
                conn.execute("SELECT status FROM orders WHERE key='legacy'").fetchone()[0], "cancelled"
            )
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM fills").fetchone()[0], 1)
            self.assertEqual(get_state(conn, "focus_policy"), FOCUS_POLICY)
            self.assertEqual(reconcile(conn), [])
