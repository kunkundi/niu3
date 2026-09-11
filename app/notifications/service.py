from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

from app.core.types import iso, now_cn
from app.storage.db import dump, get_state, set_state
from .channels import CHANNELS, DeliveryUncertain, NotificationError, send
from .config import current, merged_fields


def money(value, digits=2):
    return f"{Decimal(value) / Decimal(1000000):,.{digits}f}"


def fill_text(row):
    name = json.loads(row["instrument"]).get("name", row["symbol"]) if row["instrument"] else row["symbol"]
    direction = "买入" if row["side"] == "BUY" else "卖出"
    lines = [
        f"{direction} {name[:40]}（{row['symbol'].upper()}）",
        f"{row['quantity']} 份 × {money(row['price'], 3)} 元，成交额 {money(row['gross'])} 元",
        f"费用 {money(row['fee'])} 元"
        + (f"，已实现盈亏 {money(row['realized'])} 元" if row["side"] == "SELL" else ""),
        f"原因：{row['reason'][:80]}",
        f"时间：{row['at'][:19].replace('T', ' ')} · 订单 #{row['order_id']} / 成交 #{row['id']}",
    ]
    return "\n".join(lines)


def enqueue_committed(conn, now):
    """The cursor and outbox commit together; only already-committed fills are read."""
    config = current(conn)
    cursor = get_state(conn, "notification_cursor", 0)
    maximum = conn.execute("SELECT COALESCE(MAX(id),0) FROM fills").fetchone()[0]
    channels = (
        [key for key, value in config["channels"].items() if value["enabled"]] if config["enabled"] else []
    )
    if not channels:
        set_state(conn, "notification_cursor", maximum)
        return
    rows = conn.execute(
        "SELECT f.*,o.reason,i.payload instrument FROM fills f JOIN orders o ON o.id=f.order_id "
        "LEFT JOIN instruments i ON i.symbol=f.symbol WHERE f.id>? AND f.id<=? ORDER BY f.id LIMIT ?",
        (cursor, maximum, 100),
    ).fetchall()
    if not rows:
        return
    header = "NiuNo3 · 模拟成交通知\n模拟成交，非实盘 · 北京时间\n\n"
    groups, ids, text = [], [], header
    for row in rows:
        part = fill_text(row) + "\n\n"
        if ids and len((text + part).encode("utf-8")) > 1800:
            groups.append((ids, text.rstrip()))
            ids, text = [], header
        ids.append(row["id"])
        text += part
    groups.append((ids, text.rstrip()))
    for ids, message in groups:
        for channel in channels:
            conn.execute(
                "INSERT OR IGNORE INTO notification_deliveries(event_key,channel,config_version,kind,fill_ids,message,at,updated_at) "
                "VALUES(?,?,?,'trade',?,?,?,?)",
                (
                    f"fills:{ids[0]}:{ids[-1]}:{channel}",
                    channel,
                    config["version"],
                    dump(ids),
                    message,
                    iso(now),
                    iso(now),
                ),
            )
    set_state(conn, "notification_cursor", rows[-1]["id"])


def outcome(sender, channel, fields, message, timeout, now):
    try:
        sender(channel, fields, message, timeout, now.timestamp())
        return "sent", ""
    except DeliveryUncertain as exc:
        return "unknown", str(exc)
    except NotificationError as exc:
        return "failed", str(exc)
    except Exception:
        return "failed", "通知发送异常；请检查渠道配置和网络"


def finish(db, row_id, status, error, now):
    with db.transaction() as conn:
        changed = conn.execute(
            "UPDATE notification_deliveries SET status=?,error=?,updated_at=? WHERE id=? AND status='sending'",
            (status, error, iso(now), row_id),
        ).rowcount
        if changed:
            row = conn.execute(
                "SELECT channel,kind FROM notification_deliveries WHERE id=?", (row_id,)
            ).fetchone()
            label = "测试通知" if row["kind"] == "test" else "成交通知"
            conn.execute(
                "INSERT INTO runs(task,at,status,detail) VALUES('notification',?,?,?)",
                (
                    iso(now),
                    "ok" if status == "sent" else "error",
                    f"{CHANNELS[row['channel']]['label']} {label} #{row_id}："
                    + ("已发送" if status == "sent" else error),
                ),
            )


def test_channel(db, channel, request, now, sender=send):
    if channel not in CHANNELS:
        raise NotificationError("不支持的通知渠道")
    with db.transaction() as conn:
        config = current(conn)
        if request.version != config["version"]:
            raise NotificationError("通知配置已被更新，请重新加载后再测试")
        fields = merged_fields(
            channel,
            config["channels"].get(channel, {}).get("fields", {}),
            request.fields,
            request.clear_signing_secret,
        )
        from .channels import validate_channel

        validate_channel(channel, fields)
        # Rate-limit explicit test requests, independently of the general trading switches.
        previous = get_state(conn, f"notification_test:{channel}", 0)
        if now.timestamp() - previous < 10:
            raise NotificationError("该渠道刚刚测试过，请等待 10 秒后重试")
        set_state(conn, f"notification_test:{channel}", now.timestamp())
        message = (
            f"NiuNo3 · 通知渠道测试\n模拟成交，非实盘\n{iso(now)}\n此消息仅验证通知渠道，不产生订单或成交。"
        )
        result = conn.execute(
            "INSERT INTO notification_deliveries(event_key,channel,config_version,kind,fill_ids,message,status,at,updated_at,expires_at) "
            "VALUES(?,?,?,'test','[]',?,'sending',?,?,?)",
            (
                f"test:{uuid4().hex}",
                channel,
                config["version"],
                message,
                iso(now),
                iso(now),
                iso(now + timedelta(seconds=120)),
            ),
        )
        row_id = result.lastrowid
    status, error = outcome(sender, channel, fields, message, request.timeout, now)
    finish(db, row_id, status, error, now_cn())
    return {"ok": status == "sent", "channel": channel, "id": row_id, "error": error}


class NotificationDispatcher:
    def __init__(self, db, sender=send):
        self.db, self.sender = db, sender
        self.pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="trade-notification")
        self.futures = {}

    def collect(self, now):
        for channel, (row_id, future) in list(self.futures.items()):
            if future.done():
                try:
                    status, error = future.result()
                except Exception:
                    status, error = "failed", "通知任务异常；不会自动重发"
                finish(self.db, row_id, status, error, now)
                del self.futures[channel]

    def deliver(self, row):
        with self.db.connect() as conn:
            config = current(conn)
        if config["version"] != row["config_version"] or not config["enabled"]:
            return "cancelled", "通知配置已变更，取消发送"
        channel = config["channels"].get(row["channel"], {})
        if not channel.get("enabled"):
            return "cancelled", "通知渠道已关闭，取消发送"
        return outcome(
            self.sender, row["channel"], channel["fields"], row["message"], config["timeout"], now_cn()
        )

    def tick(self, now, deliver=True):
        self.collect(now)
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE notification_deliveries SET status='unknown',error='发送中断，送达结果不确定；不会自动重发',updated_at=? "
                "WHERE status='sending' AND expires_at<?",
                (iso(now), iso(now)),
            )
            enqueue_committed(conn, now)
        if not deliver:
            return
        for channel in CHANNELS:
            if channel in self.futures:
                continue
            with self.db.transaction() as conn:
                row = conn.execute(
                    "SELECT * FROM notification_deliveries WHERE channel=? AND status='pending' ORDER BY id LIMIT 1",
                    (channel,),
                ).fetchone()
                if row is None:
                    continue
                conn.execute(
                    "UPDATE notification_deliveries SET status='sending',updated_at=?,expires_at=? WHERE id=?",
                    (iso(now), iso(now + timedelta(seconds=120)), row["id"]),
                )
            self.futures[channel] = (row["id"], self.pool.submit(self.deliver, dict(row)))

    def close(self):
        # Claimed requests must finish or remain explicitly uncertain after a process crash.
        self.pool.shutdown(wait=True, cancel_futures=False)
        self.collect(now_cn())
