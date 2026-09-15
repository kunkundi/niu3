import json
import os
import threading
import unittest
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit
from unittest.mock import Mock, patch

import httpx
from fastapi.testclient import TestClient

from app.dashboard.api import create_app
from app.notifications.channels import (
    DeliveryUncertain,
    NotificationError,
    request_payload,
    send,
    validate_channel,
)
from app.notifications.config import NotificationPatch, current, save_config
from app.notifications.service import NotificationDispatcher, enqueue_committed
from app.storage.db import Database, get_state, set_state
from app.core.types import iso
from app.trading.account import reconcile
from tests.helpers import Fixture, at

FEISHU = {
    "webhook": "https://open.feishu.cn/open-apis/bot/v2/hook/fixture-12345678",
    "signing_secret": "fixture-secret",
}
DINGTALK = {
    "webhook": "https://oapi.dingtalk.com/robot/send?access_token=fixture-dingtalk",
    "signing_secret": "fixture-secret",
}
WECOM = {"webhook": "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=fixture-wecom"}
TELEGRAM = {"bot_token": "123456789:abcdefghijklmnopqrstuvwxyz0123456789", "chat_id": "-1001234567890"}
VALUES = {"feishu": FEISHU, "dingtalk": DINGTALK, "wecom": WECOM, "telegram": TELEGRAM}


class ChannelTests(unittest.TestCase):
    def test_signatures_and_message_payloads(self):
        _, payload = request_payload("feishu", FEISHU, "模拟成交，非实盘", 1700000000.125)
        self.assertEqual(payload["timestamp"], "1700000000")
        self.assertEqual(payload["sign"], "eSSBpPPdWfBl7avPl9BWSBfQOnLGZ91Xn8G5oZis0sc=")
        url, payload = request_payload("dingtalk", DINGTALK, "模拟成交，非实盘", 1700000000.125)
        query = parse_qs(urlsplit(url).query)
        self.assertEqual(query["timestamp"], ["1700000000125"])
        self.assertEqual(query["sign"], ["IIuws1KPnbyJqUNaQoLhXt/mh0hz5H71CtxzlkRjh1U="])
        self.assertFalse(payload["at"]["isAtAll"])
        _, payload = request_payload("wecom", WECOM, "模拟成交，非实盘", 0)
        self.assertNotIn("mentioned_list", payload["text"])
        url, payload = request_payload("telegram", TELEGRAM, "模拟成交，非实盘", 0)
        self.assertEqual(url, f"https://api.telegram.org/bot{TELEGRAM['bot_token']}/sendMessage")
        self.assertEqual(payload["chat_id"], TELEGRAM["chat_id"])
        self.assertNotIn("parse_mode", payload)

    def test_official_url_allowlist_and_credential_validation(self):
        for channel, fields in VALUES.items():
            validate_channel(channel, fields)
        for url in [
            "http://127.0.0.1/test",
            "https://example.com/hook",
            FEISHU["webhook"] + "?redirect=test",
            FEISHU["webhook"].replace("open.feishu.cn", "open.feishu.cn.example.com"),
            FEISHU["webhook"].replace("https://", "https://@"),
            FEISHU["webhook"] + "\n",
            FEISHU["webhook"].replace("open.feishu.cn", "open.feishu.cn:444"),
        ]:
            with self.subTest(url=url), self.assertRaises(NotificationError):
                validate_channel("feishu", {"webhook": url})
        for fields in [
            {"webhook": WECOM["webhook"] + "&key=second"},
            {"webhook": WECOM["webhook"], "bot_token": "wrong"},
        ]:
            with self.assertRaises(NotificationError):
                validate_channel("wecom", fields)
        with self.assertRaises(NotificationError):
            validate_channel("telegram", {"bot_token": "../bad", "chat_id": "x"})

    def test_all_four_providers_require_positive_acknowledgement(self):
        for channel, fields in VALUES.items():
            good = (
                {"ok": True}
                if channel == "telegram"
                else {"code": 0}
                if channel == "feishu"
                else {"errcode": 0}
            )
            send(
                channel,
                fields,
                "模拟成交，非实盘",
                5,
                0,
                transport=httpx.MockTransport(lambda r: httpx.Response(200, json=good)),
            )
            for bad in [
                {},
                {"ok": False, "description": TELEGRAM["bot_token"]},
                {"errcode": 1, "errmsg": FEISHU["webhook"]},
            ]:
                with self.subTest(channel=channel, bad=bad), self.assertRaises(NotificationError) as caught:
                    send(
                        channel,
                        fields,
                        "test",
                        5,
                        0,
                        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=bad)),
                    )
                self.assertNotIn("fixture-", str(caught.exception))
                self.assertNotIn(TELEGRAM["bot_token"], str(caught.exception))

    def test_redirects_timeouts_and_invalid_responses_do_not_retry_or_leak(self):
        seen = []

        def redirect(request):
            seen.append(request)
            return httpx.Response(
                302, headers={"location": "https://example.com/private"}, text=FEISHU["webhook"]
            )

        with self.assertRaises(NotificationError):
            send("feishu", FEISHU, "test", 5, 0, transport=httpx.MockTransport(redirect))
        self.assertEqual(len(seen), 1)

        def timeout(request):
            raise httpx.ReadTimeout(FEISHU["webhook"], request=request)

        with self.assertRaises(DeliveryUncertain) as caught:
            send("feishu", FEISHU, "test", 5, 0, transport=httpx.MockTransport(timeout))
        self.assertNotIn(FEISHU["webhook"], str(caught.exception))
        for content in ["not-json", "[]", "x" * 65537]:
            with self.assertRaises(DeliveryUncertain):
                send(
                    "feishu",
                    FEISHU,
                    "test",
                    5,
                    0,
                    transport=httpx.MockTransport(lambda r: httpx.Response(200, text=content)),
                )

    def test_http_client_logs_hide_credential_url(self):
        with self.assertLogs("httpx", level="INFO") as logs:
            send(
                "telegram",
                TELEGRAM,
                "test",
                5,
                0,
                transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"ok": True})),
            )
        self.assertNotIn(TELEGRAM["bot_token"], "\n".join(logs.output))
        self.assertIn("credential URL hidden", "\n".join(logs.output))


class NotificationTests(unittest.TestCase):
    def setUp(self):
        self.f = Fixture()

    def tearDown(self):
        self.f.close()

    def configure(self, channels=None, enabled=True, **kwargs):
        with self.f.db.connect() as conn:
            version = current(conn)["version"]
        return save_config(
            self.f.db,
            NotificationPatch.model_validate(
                {
                    "version": version,
                    "enabled": enabled,
                    "channels": channels
                    if channels is not None
                    else {"feishu": {"added": True, "enabled": True, "fields": FEISHU}},
                    **kwargs,
                }
            ),
            at(),
        )

    def enqueue(self, now=None):
        with self.f.db.transaction() as conn:
            enqueue_committed(conn, now or at())


    def test_notification_install_enable_disable_lifecycle_never_replays_fills(self):
        self.f.buy()
        with self.f.db.transaction() as conn:
            conn.execute("DELETE FROM state WHERE key IN ('notification_settings','notification_cursor')")
        Database(self.f.db.path)
        self.enqueue()
        self.assertEqual(self.f.rows("notification_deliveries"), [])
        self.configure()
        self.enqueue()
        self.assertEqual(self.f.rows("notification_deliveries"), [])
        self.f.buy(when=at() + timedelta(minutes=1))
        self.enqueue()
        self.assertEqual(json.loads(self.f.rows("notification_deliveries")[0]["fill_ids"]), [2])
        self.configure(enabled=False)
        self.assertEqual(self.f.rows("notification_deliveries")[0]["status"], "cancelled")
        self.configure()
        sender = Mock()
        dispatcher = NotificationDispatcher(self.f.db, sender)
        dispatcher.tick(at())
        dispatcher.close()
        sender.assert_not_called()

    def test_config_secrets_preserve_disable_remove_and_separate_version(self):
        result = self.configure()
        self.assertNotIn("fixture-", json.dumps(result))
        self.assertTrue(result["channels"]["feishu"]["configured"]["webhook"])
        self.f.order()
        self.configure({"feishu": {"enabled": False, "fields": {"webhook": "", "signing_secret": ""}}})
        with self.f.db.connect() as conn:
            self.assertEqual(current(conn)["channels"]["feishu"]["fields"], FEISHU)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM configs").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT status FROM orders").fetchone()[0], "pending")
        self.configure({"feishu": {"enabled": True, "clear_signing_secret": True}})
        with self.f.db.connect() as conn:
            self.assertNotIn("signing_secret", current(conn)["channels"]["feishu"]["fields"])
        result = self.configure({"feishu": {"added": False}})
        self.assertFalse(result["channels"]["feishu"]["added"])
        with self.f.db.connect() as conn:
            self.assertNotIn("fixture-", json.dumps(current(conn)))

    def test_config_conflicts_and_invalid_fields_are_atomic(self):
        self.configure()
        for payload in [
            {"version": 1, "enabled": True},
            {
                "version": 2,
                "enabled": True,
                "channels": {"feishu": {"enabled": True, "fields": {"webhook": "https://127.0.0.1/private"}}},
            },
        ]:
            with self.assertRaises(NotificationError):
                save_config(self.f.db, NotificationPatch.model_validate(payload), at())
        with self.f.db.connect() as conn:
            self.assertEqual(current(conn)["version"], 2)

    def test_committed_batch_partial_fills_three_decimals_and_deduplication(self):
        self.configure()
        self.f.quote(at() - timedelta(seconds=10))
        self.f.order(quantity=5000)
        self.f.quote(at() + timedelta(seconds=30), volume=1_100_000)
        self.f.engine.match(at() + timedelta(seconds=30))
        self.f.quote(at() + timedelta(seconds=60), volume=1_200_000)
        self.f.engine.match(at() + timedelta(seconds=60))
        with self.assertRaises(RuntimeError):
            with self.f.db.transaction() as conn:
                enqueue_committed(conn, at())
                raise RuntimeError("storage rollback")
        self.assertEqual(self.f.rows("notification_deliveries"), [])
        self.enqueue()
        self.enqueue()
        rows = self.f.rows("notification_deliveries")
        self.assertEqual(len(rows), 1)
        self.assertEqual(json.loads(rows[0]["fill_ids"]), [1, 2])
        self.assertIn("1.000", rows[0]["message"])
        self.assertIn("1,000 份", rows[0]["message"])
        self.assertIn("模拟成交，非实盘", rows[0]["message"])
        self.assertLessEqual(len(rows[0]["message"].encode()), 1800)

    def test_rolled_back_fill_never_generates_notification(self):
        self.configure()
        self.f.quote(at() - timedelta(seconds=10))
        self.f.order()
        self.f.quote(at() + timedelta(seconds=30), volume=2_000_000)
        original = self.f.engine._fill

        def failed(*args):
            original(*args)
            raise RuntimeError("rollback fixture")

        with patch.object(self.f.engine, "_fill", failed), self.assertRaises(RuntimeError):
            self.f.engine.match(at() + timedelta(seconds=30))
        self.enqueue()
        self.assertEqual(self.f.rows("fills"), [])
        self.assertEqual(self.f.rows("notification_deliveries"), [])

    def test_restart_keeps_pending_and_sends_once(self):
        self.configure()
        self.f.buy()
        self.enqueue()
        restarted = Database(self.f.db.path)
        sender = Mock()
        dispatcher = NotificationDispatcher(restarted, sender)
        dispatcher.tick(at())
        dispatcher.close()
        again = NotificationDispatcher(restarted, sender)
        again.tick(at() + timedelta(seconds=1))
        again.close()
        self.assertEqual(sender.call_count, 1)
        self.assertEqual(self.f.rows("notification_deliveries")[0]["status"], "sent")

    def test_channel_failure_does_not_block_other_channels_or_ledger(self):
        self.configure({key: {"enabled": True, "fields": value} for key, value in VALUES.items()})
        self.f.buy()

        def sender(channel, *args):
            if channel == "feishu":
                raise RuntimeError(FEISHU["webhook"])

        dispatcher = NotificationDispatcher(self.f.db, sender)
        dispatcher.tick(at())
        dispatcher.close()
        rows = self.f.rows("notification_deliveries")
        self.assertEqual(
            {row["channel"]: row["status"] for row in rows},
            {"feishu": "failed", "dingtalk": "sent", "wecom": "sent", "telegram": "sent"},
        )
        self.assertNotIn(FEISHU["webhook"], json.dumps(rows))
        self.assertEqual(len(self.f.rows("fills")), 1)
        with self.f.db.connect() as conn:
            self.assertEqual(reconcile(conn), [])

    def test_slow_sender_does_not_block_tick_or_duplicate_inflight(self):
        self.configure()
        self.f.buy()
        started, release = threading.Event(), threading.Event()

        def slow(*args):
            started.set()
            release.wait(3)

        sender = Mock(side_effect=slow)
        dispatcher = NotificationDispatcher(self.f.db, sender)
        try:
            dispatcher.tick(at())
            self.assertTrue(started.wait(1))
            dispatcher.tick(at() + timedelta(seconds=1))
            self.assertEqual(sender.call_count, 1)
            self.assertEqual(self.f.rows("notification_deliveries")[0]["status"], "sending")
        finally:
            release.set()
            dispatcher.close()

    def test_interrupted_delivery_marked_unknown_without_resending(self):
        self.configure()
        self.f.buy()
        self.enqueue()
        with self.f.db.transaction() as conn:
            conn.execute(
                "UPDATE notification_deliveries SET status='sending',expires_at='2026-09-06T00:00:00+08:00'"
            )
        sender = Mock()
        dispatcher = NotificationDispatcher(self.f.db, sender)
        dispatcher.tick(at())
        dispatcher.close()
        self.assertEqual(self.f.rows("notification_deliveries")[0]["status"], "unknown")
        sender.assert_not_called()

    def test_sell_notification_contains_realized_pnl_and_reason(self):
        self.configure()
        self.f.buy()
        self.enqueue()
        tomorrow = at("2026-09-08T09:35:00")
        with self.f.db.transaction() as conn:
            set_state(conn, "actions:sh510300", {"at": iso(tomorrow)})
        self.f.quote(tomorrow - timedelta(seconds=10), price=".940")
        self.f.engine.risk_check(tomorrow)
        self.f.quote(tomorrow + timedelta(seconds=30), price=".940", volume=2_000_000)
        self.f.engine.match(tomorrow + timedelta(seconds=30))
        self.enqueue(tomorrow)
        message = self.f.rows("notification_deliveries")[-1]["message"]
        self.assertIn("卖出", message)
        self.assertIn("已实现盈亏", message)
        self.assertIn("成本", message)

    def test_large_batch_splits_without_dropping_fills(self):
        self.configure()
        for minute in range(10):
            self.f.buy(when=at() + timedelta(minutes=minute))
        self.enqueue()
        rows = self.f.rows("notification_deliveries")
        self.assertGreater(len(rows), 1)
        self.assertEqual([i for row in rows for i in json.loads(row["fill_ids"])], list(range(1, 11)))
        self.assertTrue(all(len(row["message"].encode()) <= 1800 for row in rows))

    def test_uncertain_delivery_is_not_automatically_retried(self):
        self.configure()
        self.f.buy()
        sender = Mock(side_effect=DeliveryUncertain("请求超时，送达结果不确定；不会自动重发"))
        dispatcher = NotificationDispatcher(self.f.db, sender)
        dispatcher.tick(at())
        dispatcher.close()
        self.assertEqual(self.f.rows("notification_deliveries")[0]["status"], "unknown")
        again = NotificationDispatcher(self.f.db, sender)
        again.tick(at() + timedelta(seconds=10))
        again.close()
        self.assertEqual(sender.call_count, 1)


class NotificationApiTests(unittest.TestCase):
    def setUp(self):
        self.f = Fixture()
        self.sender = Mock()
        with patch.dict(os.environ, {"NIUNO3_ADMIN_PASSWORD": "notification-fixture-only"}):
            self.client = TestClient(
                create_app(self.f.db, self.f.calendar, clock=at, notification_sender=self.sender)
            )
        self.headers = {"X-NiuNo3-Request": "1"}

    def tearDown(self):
        self.client.close()
        self.f.close()

    def login(self):
        self.client.post(
            "/api/v1/auth/login", json={"password": "notification-fixture-only"}, headers=self.headers
        )

    def test_unsaved_test_ignores_switches_without_saving_or_trading(self):
        self.login()
        response = self.client.post(
            "/api/v1/notifications/test/telegram",
            json={"version": 1, "timeout": 30, "fields": TELEGRAM},
            headers=self.headers,
        )
        self.assertTrue(response.json()["ok"])
        self.sender.assert_called_once()
        self.assertEqual(self.sender.call_args.args[0], "telegram")
        self.assertIn("模拟成交，非实盘", self.sender.call_args.args[2])
        config = self.client.get("/api/v1/notifications/config").json()
        self.assertEqual(config["version"], 1)
        self.assertFalse(config["enabled"])
        self.assertFalse(config["channels"]["telegram"]["added"])
        self.assertEqual(self.f.rows("fills"), [])
        history = self.client.get("/api/v1/notifications/history").json()
        self.assertEqual(history["items"][0]["status"], "sent")
        self.assertNotIn(TELEGRAM["bot_token"], json.dumps(history))
        repeated = self.client.post(
            "/api/v1/notifications/test/telegram",
            json={"version": 1, "fields": TELEGRAM},
            headers=self.headers,
        )
        self.assertEqual(repeated.status_code, 422)
        self.assertEqual(self.sender.call_count, 1)

    def test_saved_secret_fallback_masking_and_validation_error_redaction(self):
        self.login()
        saved = self.client.patch(
            "/api/v1/notifications/config",
            json={
                "version": 1,
                "enabled": False,
                "channels": {"feishu": {"enabled": False, "fields": FEISHU}},
            },
            headers=self.headers,
        )
        self.assertEqual(saved.status_code, 200)
        self.assertNotIn(FEISHU["webhook"], saved.text)
        response = self.client.post(
            "/api/v1/notifications/test/feishu",
            json={"version": 2, "fields": {"webhook": ""}},
            headers=self.headers,
        )
        self.assertTrue(response.json()["ok"])
        self.assertEqual(self.sender.call_args.args[1], FEISHU)
        for payload in [
            {"version": 2, "enabled": True, "timeout": FEISHU["webhook"]},
            {
                "version": 2,
                "enabled": True,
                "channels": {"feishu": {"fields": {"webhook": {"secret": TELEGRAM["bot_token"]}}}},
            },
        ]:
            result = self.client.patch("/api/v1/notifications/config", json=payload, headers=self.headers)
            self.assertEqual(result.status_code, 422)
            self.assertNotIn("fixture-", result.text)
            self.assertNotIn(TELEGRAM["bot_token"], result.text)

    def test_test_failure_is_recorded_and_does_not_expose_transport_exception(self):
        self.login()
        self.sender.side_effect = RuntimeError(FEISHU["webhook"])
        result = self.client.post(
            "/api/v1/notifications/test/feishu", json={"version": 1, "fields": FEISHU}, headers=self.headers
        )
        self.assertFalse(result.json()["ok"])
        history = self.client.get("/api/v1/notifications/history").json()
        self.assertEqual(history["items"][0]["status"], "failed")
        self.assertNotIn(FEISHU["webhook"], json.dumps(history))
        self.assertEqual(self.f.rows("orders"), [])
        with self.f.db.connect() as conn:
            self.assertEqual(reconcile(conn), [])
            self.assertFalse(get_state(conn, "notification_settings")["enabled"])


if __name__ == "__main__":
    unittest.main()
