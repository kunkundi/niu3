import json
import unittest
from copy import deepcopy
from datetime import timedelta
from unittest.mock import Mock

import httpx

from app.core.types import units
from app.notifications.channels import request_payload, send
from app.notifications.formatting import (
    NOTICE, TEST_TITLE, fill_text, message_fits, rich_message, test_message, trade_message,
)
from app.notifications.service import NotificationDispatcher
from app.storage.db import Database, dump
from tests.helpers import at
from tests import test_notifications as notification_fixtures
from tests.test_notifications import VALUES


def fill(**overrides):
    row = {
        "id": 26, "order_id": 14, "symbol": "sh510300", "side": "SELL", "order_kind": "t_sell",
        "quantity": 1000, "price": units("1.025"), "gross": units("1025"), "fee": units(".1"),
        "realized": units("24.8"), "instrument": dump({"name": "沪深300ETF"}),
        "position_before": 4000, "position_after": 3000, "position_effect": "reduce",
        "at": "2026-09-15T10:20:30+08:00", "reason": "5 分钟压力附近转弱，执行做 T 卖出",
    }
    return {**row, **overrides}


class NotificationFormatTests(unittest.TestCase):
    def test_plain_message_has_sections_six_digit_codes_and_signed_net_returns(self):
        source = fill()
        original = deepcopy(source)
        message = trade_message([source])
        self.assertEqual(message, "\n".join([
            "牛牛3号 · 成交信息（1笔）", NOTICE, "",
            "1. 做 T 卖出 · 减仓｜沪深300ETF（510300）",
            "成交：1,000 份 × ¥1.025", "金额：¥1,025.00", "费用：¥0.10",
            "卖出比例：25.00%（占该 ETF 持仓）", "持仓变化：4,000 → 3,000 份",
            "本笔已实现盈亏：+¥24.80（+2.48%）", "时间：2026-09-15 10:20:30",
            "原因：5 分钟压力附近转弱，执行做 T 卖出", "记录：订单 #14 / 成交 #26",
        ]))
        self.assertEqual(source, original)

    def test_native_layouts_keep_all_fields_and_signatures(self):
        rows = [
            fill(side="BUY", order_kind="t_buy", position_effect="add", position_before=3000, position_after=4000),
            fill(),
        ]
        message = trade_message(rows)
        bodies = {channel: request_payload(channel, values, message, 1700000000.125)[1]
                  for channel, values in VALUES.items()}
        feishu = bodies["feishu"]
        self.assertEqual(feishu["msg_type"], "interactive")
        self.assertEqual(feishu["card"]["header"]["title"]["content"], "牛牛3号 · 成交信息（2笔）")
        self.assertEqual(feishu["sign"], "eSSBpPPdWfBl7avPl9BWSBfQOnLGZ91Xn8G5oZis0sc=")
        elements = feishu["card"]["elements"]
        self.assertEqual(sum(e["tag"] == "hr" for e in elements), 1)
        fields = next(e["fields"] for e in elements if "fields" in e)
        self.assertEqual([f["text"]["content"] for f in fields], [
            "**成交数量**\n1,000 份", "**成交价格**\n¥1.025", "**成交金额**\n¥1,025.00", "**费用**\n¥0.10",
        ])
        card = json.dumps(feishu, ensure_ascii=False)
        self.assertIn("<font color='red'>", card)
        self.assertIn("<font color='green'>", card)
        self.assertIn("做 T 买回", card)
        self.assertIn("+¥24.80（+2.48%）", card)
        self.assertEqual(bodies["dingtalk"]["msgtype"], "markdown")
        self.assertEqual(bodies["dingtalk"]["at"], {"isAtAll": False})
        markdown = bodies["dingtalk"]["markdown"]["text"]
        self.assertIn("#### 2. 做 T 卖出 · 减仓｜沪深300ETF（510300）", markdown)
        self.assertEqual(bodies["wecom"]["markdown"]["content"], markdown)
        telegram = bodies["telegram"]
        self.assertEqual(telegram["parse_mode"], "HTML")
        self.assertTrue(telegram["link_preview_options"]["is_disabled"])
        self.assertIn("<b>本笔已实现盈亏</b>　+¥24.80（+2.48%）", telegram["text"])
        for channel, values in VALUES.items():
            captured = []

            def transport(request):
                captured.append(json.loads(request.content))
                return httpx.Response(200, json={"ok": True, "code": 0, "errcode": 0})

            send(channel, values, message, 5, 1700000000.125, transport=httpx.MockTransport(transport))
            self.assertEqual(captured, [bodies[channel]])

    def test_complete_sale_missing_history_and_extreme_ratios(self):
        self.assertIn("卖出比例：100%", fill_text(fill(position_before=1000, position_after=0, position_effect="close")))
        unknown = fill_text(fill(position_before=None, position_after=None, position_effect="unknown"))
        self.assertIn("持仓变化：暂不可用", unknown)
        self.assertIn("卖出比例：暂不可用", unknown)
        self.assertNotIn("清仓", unknown)
        self.assertIn("卖出比例：暂不可用", fill_text(fill(position_before=1000, position_after=3000)))
        almost_all = fill_text(fill(quantity=999999, position_before=1000000, position_after=1))
        self.assertIn("卖出比例：<100%", almost_all)
        tiny = fill_text(fill(quantity=1, position_before=1000000, position_after=999999))
        self.assertIn("卖出比例：<0.01%", tiny)

    def test_no_invented_pnl_for_buys_and_ordinary_trades_are_not_t_trades(self):
        message = fill_text(fill(side="BUY", order_kind="intraday", position_effect="open",
                                 position_before=0, position_after=1000, realized=0))
        self.assertIn("买入 · 开仓", message)
        self.assertNotIn("做 T", message.splitlines()[0])
        self.assertNotIn("已实现盈亏", message)
        loss = fill_text(fill(realized=units("-25")))
        self.assertIn("本笔已实现盈亏：-¥25.00（-2.38%）", loss)

    def test_text_is_normalized_and_cannot_inject_markup_or_new_fields(self):
        hostile = "<at id=all></at> & [链接](https://example.com) **假内容**\n记录：伪造\x00"
        message = trade_message([fill(instrument=dump({"name": hostile}), reason=hostile)])
        self.assertEqual(message.count("\n记录："), 1)
        rich = rich_message(message)
        for rendered in (rich["markdown"], rich["html"], json.dumps(rich["card"], ensure_ascii=False)):
            self.assertNotIn("<at id=all>", rendered)
            self.assertIn("&lt;at id=all&gt;", rendered)
        self.assertIn(r"\[链接\]", rich["markdown"])
        self.assertIn("&amp;", rich["html"])
        self.assertNotIn("\x00", message)

    def test_single_trade_unicode_and_escaping_fit_every_channel(self):
        for token in ("😀", "汉", "&", "<", "*", "\\"):
            with self.subTest(token=token):
                message = trade_message([fill(reason=token * 10000, instrument=dump({"name": token * 1000}))])
                self.assertTrue(message_fits(message))
                self.assertIn("…", message)

    def test_times_use_beijing_and_test_message_uses_the_same_rich_layout(self):
        self.assertIn("时间：2026-09-15 10:20:30", fill_text(fill(at="2026-09-15T02:20:30+00:00")))
        message = test_message("飞书", at())
        self.assertTrue(message.startswith(TEST_TITLE))
        self.assertIn("不产生订单或成交", message)
        for channel, values in VALUES.items():
            payload = request_payload(channel, values, message, 0)[1]
            self.assertNotIn('"msgtype": "text"', json.dumps(payload))
            self.assertIsNotNone(rich_message(message))

    def test_legacy_pending_notifications_keep_exact_plain_text(self):
        for message in ("NiuNo3 · 模拟成交通知\n模拟成交，非实盘\n旧消息 <>&", "test"):
            self.assertIsNone(rich_message(message))
            for channel, values in VALUES.items():
                payload = request_payload(channel, values, message, 0)[1]
                actual = payload["text"] if channel == "telegram" else (
                    payload["content"]["text"] if channel == "feishu" else payload["text"]["content"]
                )
                self.assertEqual(actual, message)


class NotificationFormatQueueTests(unittest.TestCase):
    setUp = notification_fixtures.NotificationTests.setUp
    tearDown = notification_fixtures.NotificationTests.tearDown
    configure = notification_fixtures.NotificationTests.configure
    enqueue = notification_fixtures.NotificationTests.enqueue

    def test_enqueued_trade_freezes_historical_positions_and_survives_restart(self):
        self.configure()
        self.f.buy()
        self.enqueue()
        message = self.f.rows("notification_deliveries")[0]["message"]
        self.assertIn("买入 · 开仓", message)
        self.assertIn("持仓变化：0 → 1,000 份", message)
        self.f.buy(when=at() + timedelta(minutes=1))
        self.enqueue()
        self.assertIn("持仓变化：1,000 → 2,000 份", self.f.rows("notification_deliveries")[1]["message"])
        self.assertEqual(self.f.rows("notification_deliveries")[0]["message"], message)
        requests = []
        sender = Mock(side_effect=lambda channel, values, message, timeout, timestamp:
                      requests.append(request_payload(channel, values, message, timestamp)[1]))
        dispatcher = NotificationDispatcher(Database(self.f.db.path), sender)
        dispatcher.tick(at())
        dispatcher.close()
        self.assertEqual(requests[0]["msg_type"], "interactive")
        self.assertIn("0 → 1,000 份", json.dumps(requests[0], ensure_ascii=False))

    def test_long_unicode_batches_split_by_complete_fill_for_all_channels(self):
        self.configure({key: {"enabled": True, "fields": value} for key, value in VALUES.items()})
        for minute in range(10):
            self.f.buy(when=at() + timedelta(minutes=minute))
        with self.f.db.transaction() as conn:
            conn.execute("UPDATE orders SET reason=?", ("&<*😀理由" * 1000,))
        self.enqueue()
        rows = self.f.rows("notification_deliveries")
        for channel in VALUES:
            deliveries = [r for r in rows if r["channel"] == channel]
            self.assertEqual([i for r in deliveries for i in json.loads(r["fill_ids"])], list(range(1, 11)))
            for row in deliveries:
                self.assertTrue(message_fits(row["message"]))
                count = len(json.loads(row["fill_ids"]))
                self.assertIn(f"成交信息（{count}笔）", row["message"])
                self.assertEqual(row["message"].count("\n记录："), count)

    def test_old_pending_message_is_not_reformatted_or_reenqueued_on_restart(self):
        self.configure()
        self.f.buy()
        self.enqueue()
        legacy = "NiuNo3 · 模拟成交通知\n模拟成交，非实盘\n原始内容"
        with self.f.db.transaction() as conn:
            conn.execute("UPDATE notification_deliveries SET message=?", (legacy,))
        sender = Mock()
        dispatcher = NotificationDispatcher(Database(self.f.db.path), sender)
        dispatcher.tick(at())
        dispatcher.close()
        sender.assert_called_once()
        self.assertEqual(sender.call_args.args[2], legacy)
        self.assertEqual(len(self.f.rows("notification_deliveries")), 1)


if __name__ == "__main__":
    unittest.main()
