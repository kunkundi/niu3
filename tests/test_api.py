import os
import sqlite3
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.dashboard.api import create_app
from app.core.types import iso
from app.storage.db import get_state, set_state
from tests.helpers import Fixture, at


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.f = Fixture()
        with patch.dict(os.environ, {"NIUNO3_ADMIN_PASSWORD": "niuno3-test-password-2026"}):
            self.client = TestClient(create_app(self.f.db, self.f.calendar, clock=at))
        self.headers = {"X-NiuNo3-Request": "1"}

    def tearDown(self):
        self.client.close()
        self.f.close()

    def login(self):
        return self.client.post(
            "/api/v1/auth/login", json={"password": "niuno3-test-password-2026"}, headers=self.headers
        )

    def test_viewing_workbench_is_public_but_settings_require_a_session(self):
        self.assertEqual(self.client.get("/healthz").status_code, 200)
        for path in ("/nonexistent", "/api/v1/etfs/sh999999"):
            self.assertEqual(self.client.get(path).status_code, 404)
        for path in ("status", "etfs", "account", "orders", "trades", "signals", "signals/history", "actions"):
            with self.subTest(public=path):
                response = self.client.get("/api/v1/" + path)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers["cache-control"], "no-store")
        for path in ("config", "runs", "notifications/config", "notifications/history"):
            with self.subTest(protected=path):
                self.assertEqual(self.client.get("/api/v1/" + path).status_code, 401)


    def test_closed_session_equity_stops_at_the_display_day_without_changing_the_ledger(self):
        for day in ("2026-09-11", "2026-09-12", "2026-09-13", "2026-09-14"):
            with self.f.db.transaction() as conn:
                conn.execute(
                    "INSERT INTO equity VALUES(?,?,?,?,?,?,?)",
                    (iso(at(day + "T09:30:00")), day, 100000000000, 0, 0, 100000000000, 0),
                )
        before = {table: self.f.rows(table) for table in ("equity", "lots", "cash_ledger")}
        for stamp, day in (
            ("2026-09-12T16:00:00", "2026-09-11"),
            ("2026-09-14T09:29:59", "2026-09-11"),
            ("2026-09-14T09:30:00", "2026-09-14"),
        ):
            with self.subTest(stamp=stamp):
                with TestClient(create_app(self.f.db, self.f.calendar, clock=lambda: at(stamp))) as client:
                    data = client.get("/api/v1/account").json()
                    self.assertEqual(data["display_day"], day)
                    self.assertEqual(data["equity"][-1]["at"][:10], day)
                    self.assertEqual(data["cash"], 100000)
        self.assertEqual(before, {table: self.f.rows(table) for table in before})

    def test_orders_include_names_keep_missing_metadata_and_preserve_pagination(self):
        self.f.order()
        self.f.order(when=at("2026-09-07T10:00:00"), key="missing-metadata")
        with self.f.db.transaction() as conn:
            conn.execute("UPDATE orders SET symbol='sh999999' WHERE key='missing-metadata'")
        before = self.f.rows("orders")
        first = self.client.get("/api/v1/orders?limit=1").json()
        second = self.client.get("/api/v1/orders?limit=1&offset=1").json()
        self.assertEqual(first["total"], 2)
        self.assertEqual(second["total"], 2)
        self.assertEqual(first["items"][0]["symbol"], "sh999999")
        self.assertIsNone(first["items"][0]["name"])
        self.assertEqual(second["items"][0]["name"], "沪深300ETF")
        self.assertEqual(second["items"][0]["symbol"], "sh510300")
        self.assertGreater(first["items"][0]["id"], second["items"][0]["id"])
        self.assertEqual(self.f.rows("orders"), before)

    def test_removed_etf_with_historical_order_can_still_open_its_daily_chart(self):
        self.f.order()
        with self.f.db.transaction() as conn:
            conn.execute("UPDATE orders SET status='cancelled'")
            conn.execute(
                "INSERT INTO bars(symbol,adjustment,day,payload,source,fetched_at) VALUES(?,?,?,?,?,?)",
                ("sh510300", "qfq", "2026-09-04", '{"day":"2026-09-04","open":"1","high":"1.1","low":"0.9","close":"1","volume":"100","amount":"1000"}', "test", "2026-09-04T15:00:00+08:00"),
            )
        self.f.db.remove_etf("sh510300", at())
        before = {table: self.f.rows(table) for table in ("instruments", "orders", "bars", "configs")}
        orders = self.client.get("/api/v1/orders").json()
        self.assertEqual(orders["items"][0]["name"], "沪深300ETF")
        response = self.client.get("/api/v1/etfs/sh510300")
        self.assertEqual(response.status_code, 200)
        detail = response.json()
        self.assertTrue(detail["history_only"])
        self.assertFalse(detail["watched"])
        self.assertEqual(detail["name"], "沪深300ETF")
        self.assertEqual(detail["bars"][0]["day"], "2026-09-04")
        self.assertEqual({table: self.f.rows(table) for table in before}, before)

    def test_trade_chart_filter_paginates_only_requested_symbol_without_mutating_ledger(self):
        self.f.buy()
        self.f.buy(when=at("2026-09-07T10:00:00"))
        self.f.order(when=at("2026-09-07T11:00:00"))
        with self.f.db.transaction() as conn:
            order_id = conn.execute("SELECT MAX(id) FROM orders").fetchone()[0]
            conn.execute("UPDATE orders SET symbol='sz159865' WHERE id=?", (order_id,))
            conn.execute(
                "INSERT INTO fills(order_id,quote_id,symbol,side,quantity,price,gross,fee,realized,at) "
                "VALUES(?,999,'sz159865','BUY',100,1000000,100000000,100000,0,'2026-09-07T11:00:30+08:00')",
                (order_id,),
            )
        before = {table: self.f.rows(table) for table in ("orders", "fills", "lots", "cash_ledger")}
        first = self.client.get("/api/v1/trades?symbol=sh510300&limit=1").json()
        second = self.client.get("/api/v1/trades?symbol=sh510300&limit=1&offset=1").json()
        self.assertEqual(first["total"], 2)
        self.assertEqual(second["total"], 2)
        self.assertEqual(first["items"][0]["symbol"], "sh510300")
        self.assertEqual(first["items"][0]["name"], "沪深300ETF")
        self.assertEqual(second["items"][0]["name"], "沪深300ETF")
        self.assertGreater(first["items"][0]["id"], second["items"][0]["id"])
        self.assertEqual(first["items"][0]["price"], 1.001)
        self.assertTrue(first["items"][0]["reason"])
        missing_metadata = self.client.get("/api/v1/trades?symbol=sz159865").json()
        self.assertEqual(missing_metadata["total"], 1)
        self.assertIsNone(missing_metadata["items"][0]["name"])
        self.assertEqual(self.client.get("/api/v1/trades").json()["total"], 3)
        self.assertEqual(self.client.get("/api/v1/trades?symbol=sh999999").json(), {"items": [], "total": 0})
        self.assertEqual(self.client.get("/api/v1/trades?symbol=invalid").status_code, 422)
        self.assertEqual({table: self.f.rows(table) for table in before}, before)

    def test_mutation_auth_and_origin_boundaries_leave_data_unchanged(self):
        tables = ("state", "configs", "instruments", "orders", "cash_ledger", "runs", "notification_deliveries")
        mutations = (
            ("POST", "/etfs", {"code": "510500"}),
            ("DELETE", "/etfs/sh510300", None),
            ("PATCH", "/config", {"stop_loss": ".04"}),
            ("PATCH", "/notifications/config", {}),
            ("POST", "/notifications/test/feishu", {}),
            ("POST", "/auth/password", {"current_password": "old", "new_password": "1", "confirm_password": "1"}),
        )
        for boundary, headers, status in (
            ("missing_session", self.headers, 401),
            ("expired_session", self.headers, 401),
            ("missing_header", {}, 403),
            ("cross_origin", {**self.headers, "Origin": "https://other.example"}, 403),
        ):
            if boundary != "missing_session":
                self.assertEqual(self.login().status_code, 200)
            if boundary == "expired_session":
                with self.f.db.transaction() as conn:
                    conn.execute("UPDATE sessions SET expires_at='2020-01-01T00:00:00+08:00'")
            before = {table: self.f.rows(table) for table in tables}
            for method, path, body in mutations:
                with self.subTest(boundary=boundary, method=method, path=path):
                    response = self.client.request(method, "/api/v1" + path, json=body, headers=headers)
                    self.assertEqual(response.status_code, status)
                    self.assertEqual({table: self.f.rows(table) for table in tables}, before)


    def test_server_errors_are_json_uncached_and_redacted(self):
        for error, status, detail in (
            (sqlite3.OperationalError("private-storage-path"), 503, "服务数据暂时不可用，请检查服务存储状态后重试"),
            (RuntimeError("private-implementation-detail"), 500, "服务内部错误，请稍后重试"),
        ):
            with (
                self.subTest(status=status),
                TestClient(self.client.app, raise_server_exceptions=False) as client,
                patch.object(self.f.db, "connect", side_effect=error),
                self.assertLogs("app.dashboard.api", level="ERROR"),
            ):
                response = client.get("/api/v1/auth/session")
            self.assertEqual(response.status_code, status)
            self.assertEqual(response.json(), {"detail": detail})
            self.assertEqual(response.headers["cache-control"], "no-store")
            self.assertNotIn("private-", response.text)

    def test_login_cookie_security_and_logout(self):
        response = self.login()
        self.assertEqual(response.status_code, 200)
        self.assertIn("HttpOnly", response.headers["set-cookie"])
        self.assertIn("SameSite=strict", response.headers["set-cookie"])
        self.assertEqual(self.client.get("/api/v1/account").status_code, 200)
        self.assertEqual(self.client.get("/api/v1/config").status_code, 200)
        self.client.post("/api/v1/auth/logout", headers=self.headers)
        self.assertEqual(self.client.get("/api/v1/config").status_code, 401)
        self.assertEqual(self.client.get("/api/v1/account").status_code, 200)
        self.assertEqual(self.client.get("/api/v1/signals").status_code, 200)

    def test_settings_validated_versioned_and_unknown_fields_rejected(self):
        self.login()
        bad = self.client.patch("/api/v1/config", json={"max_weight": 2}, headers=self.headers)
        self.assertEqual(bad.status_code, 422)
        bad = self.client.patch("/api/v1/config", json={"undeclared": True}, headers=self.headers)
        self.assertEqual(bad.status_code, 422)
        result = self.client.patch("/api/v1/config", json={"stop_loss": ".04"}, headers=self.headers)
        self.assertEqual(result.status_code, 200)
        config = self.client.get("/api/v1/config").json()
        self.assertEqual(config["version"], 2)
        self.assertEqual(config["values"]["stop_loss"], "0.04")

    def test_display_frequency_is_persisted_validated_and_does_not_reset_trading(self):
        self.login()
        self.f.order(kind="intraday")
        with self.f.db.transaction() as conn:
            conn.execute(
                "INSERT INTO t_cycles(day,symbol,config_id,sell_order_id,status,at,updated_at) "
                "VALUES('2026-09-07','sh510300',1,1,'waiting_buy','2026-09-07T09:35:00+08:00','2026-09-07T09:35:00+08:00')"
            )
            set_state(conn, "intraday_confirmation", {"count": 2})
            set_state(conn, "intraday_execution", {"last": "2026-09-07T09:35:00+08:00"})
        initial = self.client.get("/api/v1/config").json()
        self.assertEqual(initial["values"]["intraday_interval"], 10)
        tables = ("orders", "plans", "configs", "t_cycles", "cash_ledger")
        before = {table: self.f.rows(table) for table in tables}
        for invalid in (0, 4, 61, 5.5, "bad"):
            self.assertEqual(
                self.client.patch(
                    "/api/v1/config", json={"intraday_interval": invalid}, headers=self.headers
                ).status_code,
                422,
            )
        updated = self.client.patch("/api/v1/config", json={"intraday_interval": 5}, headers=self.headers)
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json()["version"], initial["version"])
        self.assertEqual(self.client.get("/api/v1/config").json()["values"]["intraday_interval"], 5)
        self.assertEqual(self.client.get("/api/v1/status").json()["intraday_polling"]["interval_seconds"], 5)
        self.assertEqual(before, {table: self.f.rows(table) for table in tables})
        with self.f.db.connect() as conn:
            self.assertEqual(get_state(conn, "intraday_confirmation"), {"count": 2})
            self.assertEqual(get_state(conn, "intraday_execution"), {"last": "2026-09-07T09:35:00+08:00"})
        bad = self.client.patch(
            "/api/v1/config", json={"intraday_interval": 30, "max_weight": 2}, headers=self.headers
        )
        self.assertEqual(bad.status_code, 422)
        self.assertEqual(self.client.get("/api/v1/config").json()["values"]["intraday_interval"], 5)
        self.assertEqual(before, {table: self.f.rows(table) for table in tables})

    def test_intraday_configuration_round_trip_and_default_signal_mode(self):
        self.login()
        result = self.client.patch(
            "/api/v1/config",
            json={"execution_mode": "intraday", "intraday_t_enabled": False, "intraday_t_trigger": "0.015"},
            headers=self.headers,
        )
        self.assertEqual(result.status_code, 200)
        config = self.client.get("/api/v1/config").json()
        self.assertEqual(config["values"]["execution_mode"], "intraday")
        self.assertIs(config["values"]["intraday_t_enabled"], False)
        self.assertEqual(config["values"]["intraday_t_trigger"], "0.015")
        self.assertIn("13:00", config["execution_window"])
        self.assertEqual(self.client.get("/api/v1/signals").json()["mode"], "live")
        self.assertEqual(self.client.get("/api/v1/status").json()["execution_mode"], "intraday")
        for invalid_config in (
            {"execution_mode": "anything"},
            {"intraday_min_interval": 1},
            {"intraday_t_fraction": "1"},
        ):
            self.assertEqual(
                self.client.patch("/api/v1/config", json=invalid_config, headers=self.headers).status_code,
                422,
            )

    def test_empty_state_and_readiness_explain_warmup(self):
        status = self.client.get("/api/v1/status").json()
        self.assertEqual(status["history_progress"]["completed"], status["history_count"])
        self.assertEqual(status["history_progress"]["total"], status["tradable_count"])
        self.assertEqual(status["history_progress"]["state"], "offline")
        account = self.client.get("/api/v1/account").json()
        self.assertEqual(account["cash"], 100000)
        self.assertEqual(account["positions"], [])
        self.assertEqual(self.client.get("/api/v1/signals").json()["rows"], [])
        self.assertEqual(self.client.get("/readyz").status_code, 503)
        self.assertTrue(self.client.get("/readyz").json()["reasons"])

    def test_price_action_settings_require_intraday_and_status_reports_selected_strategy(self):
        self.login()
        self.assertEqual(
            self.client.patch(
                "/api/v1/config", json={"strategy_model": "price_action"}, headers=self.headers
            ).status_code,
            422,
        )
        response = self.client.patch(
            "/api/v1/config",
            json={"strategy_model": "price_action", "execution_mode": "intraday", "pa_min_rr": "1.8"},
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get("/api/v1/config").json()["values"]["pa_min_rr"], "1.8")
        self.assertEqual(self.client.get("/api/v1/status").json()["strategy"], "裸 K 价格行为 v2")
        self.assertEqual(self.client.get("/api/v1/signals").json()["strategy_model"], "price_action")
        before = self.f.rows("configs")
        response = self.client.patch(
            "/api/v1/config", json={"execution_mode": "daily"}, headers=self.headers
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.f.rows("configs"), before)

    def test_rr_filter_settings_default_persistence_and_validation(self):
        self.login()
        values = self.client.get("/api/v1/config").json()["values"]
        self.assertTrue(values["pa_rr_enabled"])
        response = self.client.patch("/api/v1/config", json={
            "strategy_model": "price_action", "execution_mode": "intraday",
            "pa_rr_enabled": False, "pa_min_rr": "2.3",
        }, headers=self.headers)
        self.assertEqual(response.status_code, 200)
        values = self.client.get("/api/v1/config").json()["values"]
        self.assertFalse(values["pa_rr_enabled"])
        self.assertEqual(values["pa_min_rr"], "2.3")
        for invalid in ("NaN", "0.9", "5.1"):
            response = self.client.patch("/api/v1/config", json={"pa_min_rr": invalid}, headers=self.headers)
            self.assertEqual(response.status_code, 422)
        response = self.client.patch("/api/v1/config", json={"pa_rr_enabled": True}, headers=self.headers)
        self.assertEqual(response.status_code, 200)
        values = self.client.get("/api/v1/config").json()["values"]
        self.assertTrue(values["pa_rr_enabled"])
        self.assertEqual(values["pa_min_rr"], "2.3")

    def test_signal_rows_wait_for_plan_using_current_strategy_setting(self):
        from app.storage.db import dump
        from app.strategies.focus import FOCUS_POLICY

        payload = {
            "focus_policy": FOCUS_POLICY,
            "rows": [{"symbol": "sh510300"}],
            "targets": {"sh510300": ".2"},
        }
        with self.f.db.transaction() as conn:
            conn.execute(
                "INSERT INTO plans(as_of,execute_day,config_id,at,payload) VALUES(?,?,1,?,?)",
                ("2026-09-04", "2026-09-07", "2026-09-04T15:30:00+08:00", dump(payload)),
            )
        self.login()
        self.assertEqual(len(self.client.get("/api/v1/signals").json()["rows"]), 1)
        self.client.patch("/api/v1/config", json={"max_exposure": "0.60"}, headers=self.headers)
        signals = self.client.get("/api/v1/signals").json()
        self.assertEqual(signals["rows"], [])
        self.assertEqual(signals["targets"], {})
        self.assertEqual(signals["focus"]["mode"], "manual")
        with self.f.db.connect() as conn:
            self.assertEqual(conn.execute("SELECT payload FROM plans").fetchone()[0], dump(payload))

    def test_signals_use_latest_quotes_without_changing_frozen_plan(self):
        from app.storage.db import dump
        from app.strategies.focus import FOCUS_POLICY

        symbols = ["sh510300", "sh510500", "sh512000", "sh512001", "sh512002"]
        payload = {
            "focus_policy": FOCUS_POLICY,
            "as_of": "2026-09-04",
            "input_sha256": "frozen-evidence",
            "rows": [
                {"symbol": symbol, "selected": True, "rank": rank, "target_weight": ".16"}
                for rank, symbol in enumerate(symbols, 1)
            ],
            "targets": dict.fromkeys(symbols, ".16"),
        }
        with self.f.db.transaction() as conn:
            conn.execute(
                "INSERT INTO plans(as_of,execute_day,config_id,at,payload) VALUES(?,?,1,?,?)",
                ("2026-09-04", "2026-09-07", "2026-09-04T15:30:00+08:00", dump(payload)),
            )
        self.f.quote(at("2026-09-07T09:34:50"), price="1.025")
        self.f.quote(price="0.980", symbol=symbols[1])
        self.f.quote(at("2026-09-04T15:00:00"), price="1.000", symbol=symbols[2])
        self.f.quote(symbol=symbols[4], previous_close=0)
        first = self.client.get("/api/v1/signals").json()
        quotes = [row["quote"] for row in first["rows"]]
        self.assertAlmostEqual(quotes[0]["change_pct"], 0.025)
        self.assertFalse(quotes[0]["stale"])
        self.assertEqual(quotes[0]["quality"], "valid")
        self.assertAlmostEqual(quotes[1]["change_pct"], -0.02)
        self.assertEqual(quotes[2]["change_pct"], 0)
        self.assertTrue(quotes[2]["stale"])
        self.assertIsNone(quotes[3])
        self.assertIsNone(quotes[4]["change_pct"])

        latest = self.f.quote(price="1.040")
        self.f.quote(at("2026-09-07T09:34:55"), price="0.990")
        updated = self.client.get("/api/v1/signals").json()
        self.assertEqual(updated["id"], first["id"])
        self.assertEqual(updated["as_of"], payload["as_of"])
        self.assertEqual(updated["targets"], payload["targets"])
        self.assertEqual(updated["input_sha256"], payload["input_sha256"])
        self.assertAlmostEqual(updated["rows"][0]["quote"]["change_pct"], 0.04)
        self.assertEqual(updated["rows"][0]["quote"]["at"], latest.at)
        self.assertEqual(
            [{key: value for key, value in row.items() if key != "quote"} for row in updated["rows"]],
            payload["rows"],
        )
        with self.f.db.connect() as conn:
            self.assertEqual(conn.execute("SELECT payload FROM plans").fetchone()[0], dump(payload))

if __name__ == "__main__":
    unittest.main()
