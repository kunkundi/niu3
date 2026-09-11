from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import re
import time
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx

CHANNELS = {
    "feishu": {"label": "飞书", "fields": ["webhook", "signing_secret"]},
    "dingtalk": {"label": "钉钉", "fields": ["webhook", "signing_secret"]},
    "wecom": {"label": "企业微信", "fields": ["webhook"]},
    "telegram": {"label": "Telegram", "fields": ["bot_token", "chat_id"]},
}
FIELD_LABELS = {
    "webhook": "机器人 Webhook",
    "signing_secret": "签名密钥（可选）",
    "bot_token": "Bot Token",
    "chat_id": "Chat ID",
}


class NotificationError(ValueError):
    """Only controlled, credential-free messages may cross this boundary."""


class DeliveryUncertain(NotificationError):
    """The request may have reached its destination, but acknowledgement was lost."""


class CredentialLogFilter(logging.Filter):
    def filter(self, record):
        if any(
            host in record.getMessage()
            for host in (
                "open.feishu.cn",
                "open.larksuite.com",
                "oapi.dingtalk.com",
                "qyapi.weixin.qq.com",
                "api.telegram.org",
            )
        ):
            record.msg, record.args = "Notification HTTP request (credential URL hidden)", ()
        return True


logging.getLogger("httpx").addFilter(CredentialLogFilter())


def validate_channel(channel: str, values: dict, required=True):
    if channel not in CHANNELS:
        raise NotificationError("不支持的通知渠道")
    for key, value in values.items():
        if key not in CHANNELS[channel]["fields"]:
            raise NotificationError("渠道字段不匹配")
        if (
            not isinstance(value, str)
            or len(value) > 2048
            or any(ord(c) < 32 or ord(c) == 127 for c in value)
        ):
            raise NotificationError("通知字段格式无效")
    required_fields = ["bot_token", "chat_id"] if channel == "telegram" else ["webhook"]
    if required and any(not values.get(key) for key in required_fields):
        raise NotificationError("请填写该渠道的必填字段")
    if channel == "telegram":
        if values.get("bot_token") and not re.fullmatch(
            r"[0-9]{5,20}:[A-Za-z0-9_-]{20,100}", values["bot_token"]
        ):
            raise NotificationError("Telegram Bot Token 格式无效")
        if values.get("chat_id") and not re.fullmatch(
            r"-?[0-9]{1,20}|@[A-Za-z][A-Za-z0-9_]{4,31}", values["chat_id"]
        ):
            raise NotificationError("Telegram Chat ID 格式无效")
        return
    if not values.get("webhook"):
        return
    try:
        url = urlsplit(values["webhook"])
        if (
            url.scheme != "https"
            or url.port not in (None, 443)
            or url.username is not None
            or url.password is not None
            or url.fragment
        ):
            raise ValueError
        if channel == "feishu":
            valid = (
                url.hostname in {"open.feishu.cn", "open.larksuite.com"}
                and not url.query
                and re.fullmatch(r"/open-apis/bot/v2/hook/[A-Za-z0-9_-]{8,}", url.path)
            )
        else:
            host, path, key = (
                ("oapi.dingtalk.com", "/robot/send", "access_token")
                if channel == "dingtalk"
                else ("qyapi.weixin.qq.com", "/cgi-bin/webhook/send", "key")
            )
            query = parse_qs(url.query, keep_blank_values=True)
            valid = (
                url.hostname == host
                and url.path == path
                and set(query) == {key}
                and len(query[key]) == 1
                and re.fullmatch(r"[A-Za-z0-9_-]+", query[key][0])
            )
        if not valid:
            raise ValueError
    except ValueError as exc:
        raise NotificationError("请填写该渠道官方 HTTPS 机器人地址，不支持代理地址或附加签名参数") from exc


def request_payload(channel: str, values: dict, message: str, timestamp: float):
    validate_channel(channel, values)
    if channel == "telegram":
        return f"https://api.telegram.org/bot{values['bot_token']}/sendMessage", {
            "chat_id": values["chat_id"],
            "text": message,
            "link_preview_options": {"is_disabled": True},
        }
    url = values["webhook"]
    secret = values.get("signing_secret", "")
    if channel == "feishu":
        payload = {"msg_type": "text", "content": {"text": message}}
        if secret:
            stamp = str(int(timestamp))
            digest = hmac.new(f"{stamp}\n{secret}".encode(), digestmod=hashlib.sha256).digest()
            payload.update(timestamp=stamp, sign=base64.b64encode(digest).decode())
    else:
        payload = {"msgtype": "text", "text": {"content": message}}
        if channel == "dingtalk":
            payload["at"] = {"isAtAll": False}
            if secret:
                stamp = str(int(timestamp * 1000))
                digest = hmac.new(secret.encode(), f"{stamp}\n{secret}".encode(), hashlib.sha256).digest()
                url += "&" + urlencode({"timestamp": stamp, "sign": base64.b64encode(digest).decode()})
    return url, payload


def send(channel: str, values: dict, message: str, timeout: int, timestamp: float, *, transport=None):
    """One request only. Never follow redirects or expose provider text / credential URLs."""
    url, payload = request_payload(channel, values, message, timestamp)
    deadline = time.monotonic() + timeout
    try:
        with httpx.Client(
            timeout=timeout, follow_redirects=False, trust_env=False, transport=transport
        ) as client:
            with client.stream("POST", url, json=payload) as response:
                if not 200 <= response.status_code < 300:
                    raise NotificationError(f"通知服务返回 HTTP {response.status_code}，请检查渠道配置")
                body = b""
                for chunk in response.iter_bytes():
                    if time.monotonic() > deadline:
                        raise DeliveryUncertain("请求超时，送达结果不确定；不会自动重发")
                    body += chunk
                    if len(body) > 65536:
                        raise DeliveryUncertain("通知服务响应过大，送达结果不确定")

                result = json.loads(body)
        if not isinstance(result, dict):
            raise DeliveryUncertain("通知服务响应格式无效，送达结果不确定")
        if channel == "telegram":
            accepted = result.get("ok") is True
        else:
            code = (
                result.get("code", result.get("StatusCode")) if channel == "feishu" else result.get("errcode")
            )
            accepted = type(code) is int and code == 0 or type(code) is str and code == "0"
        if not accepted:
            raise NotificationError("通知服务拒绝消息，请检查机器人权限、签名或关键词设置")
    except NotificationError:
        raise
    except httpx.TimeoutException as exc:
        raise DeliveryUncertain("请求超时，送达结果不确定；不会自动重发") from exc
    except Exception as exc:
        raise DeliveryUncertain("通知请求异常或响应无效，送达结果不确定；请检查网络与渠道配置") from exc
