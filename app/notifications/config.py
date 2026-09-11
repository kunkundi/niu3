from __future__ import annotations

from copy import deepcopy
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.core.types import iso
from app.storage.db import get_state, set_state
from .channels import CHANNELS, FIELD_LABELS, NotificationError, validate_channel

ChannelId = Literal["feishu", "dingtalk", "wecom", "telegram"]


class ChannelPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    added: bool = True
    enabled: bool = False
    fields: dict[str, str] = Field(default_factory=dict)
    clear_signing_secret: bool = False


class NotificationPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    version: int = Field(ge=1)
    enabled: bool
    timeout: int = Field(default=5, ge=1, le=30)
    channels: dict[ChannelId, ChannelPatch] = Field(default_factory=dict)


class TestInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    version: int = Field(ge=1)
    timeout: int = Field(default=5, ge=1, le=30)
    fields: dict[str, str] = Field(default_factory=dict)
    clear_signing_secret: bool = False


def current(conn):
    return get_state(conn, "notification_settings")


def public_config(config):
    result = {key: config[key] for key in ("version", "enabled", "timeout")}
    result["channels"] = {}
    for key, spec in CHANNELS.items():
        saved = config["channels"].get(key, {})
        result["channels"][key] = {
            **spec,
            "added": bool(saved),
            "enabled": saved.get("enabled", False),
            "configured": {field: bool(saved.get("fields", {}).get(field)) for field in spec["fields"]},
        }
    result["field_labels"] = FIELD_LABELS
    return result


def merged_fields(channel, saved, patch, clear=False):
    validate_channel(channel, patch, required=False)
    fields = {key: value for key, value in saved.items() if key in CHANNELS[channel]["fields"]}
    for key, value in patch.items():
        if value.strip():
            fields[key] = value.strip()
    if clear:
        fields.pop("signing_secret", None)
    return fields


def save_config(db, patch, now):
    with db.transaction() as conn:
        old = current(conn)
        if patch.version != old["version"]:
            raise NotificationError("通知配置已被更新，请重新加载后再保存")
        config = deepcopy(old)
        config.update(version=old["version"] + 1, enabled=patch.enabled, timeout=patch.timeout)
        for channel, value in patch.channels.items():
            if not value.added:
                config["channels"].pop(channel, None)
                continue
            fields = merged_fields(
                channel,
                old["channels"].get(channel, {}).get("fields", {}),
                value.fields,
                value.clear_signing_secret,
            )
            validate_channel(channel, fields, required=value.enabled)
            config["channels"][channel] = {"enabled": value.enabled, "fields": fields}
        # A new configuration starts at this committed fill boundary, never at historical fills.
        set_state(
            conn, "notification_cursor", conn.execute("SELECT COALESCE(MAX(id),0) FROM fills").fetchone()[0]
        )
        set_state(conn, "notification_settings", config)
        conn.execute(
            "UPDATE notification_deliveries SET status='cancelled',updated_at=?,error='通知配置变更，取消旧待发送消息' "
            "WHERE status='pending'",
            (iso(now),),
        )
        conn.execute(
            "INSERT INTO runs(task,at,status,detail) VALUES('notification_config',?,'ok',?)",
            (iso(now), f"通知配置更新至 v{config['version']}"),
        )
    return public_config(config)
