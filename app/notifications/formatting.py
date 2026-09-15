"""Persist readable messages; derive native channel layouts from the same frozen text."""

import html
import json
import re
from decimal import Decimal

from app.core.types import dt, iso

NOTICE = "模拟成交，非实盘 · 北京时间"
TRADE_TITLE = "牛牛3号 · 成交信息"
TEST_TITLE = "牛牛3号 · 通知渠道测试"
MAX_TEXT_BYTES = 1800


def clean(value, limit=120):
    text = re.sub(r"\s+", " ", re.sub(r"[\x00-\x1f\x7f]", " ", str(value or ""))).strip()
    text = re.sub(r"\b(?:sh|sz)(\d{6})\b", r"\1", text, flags=re.I)
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def money(value, digits=2, signed=False):
    number = (Decimal(value) / 1000000).quantize(Decimal(10) ** -digits)
    sign = "+" if signed and number > 0 else "-" if number < 0 else ""
    return f"{sign}¥{abs(number):,.{digits}f}"


def display_code(value):
    return re.sub(r"^(sh|sz)(?=\d{6}$)", "", clean(value, 16), flags=re.I)


def ratio(quantity, before):
    if quantity == before:
        return "100%"
    percent = Decimal(quantity) * 100 / before
    rounded = round(percent, 2)
    return "<100%" if rounded >= 100 else "<0.01%" if rounded <= 0 else f"{percent:.2f}%"


def fill_text(row, index=1):
    instrument = json.loads(row["instrument"]) if row.get("instrument") else {}
    name = clean(instrument.get("name"), 32) or display_code(row["symbol"])
    buy = row["side"] == "BUY"
    direction = "买入" if buy else "卖出"
    effect = {"open": "开仓", "add": "加仓", "reduce": "减仓", "close": "清仓"}.get(row.get("position_effect"))
    if row.get("order_kind") == ("t_buy" if buy else "t_sell"):
        direction = "做 T 买回" if buy else "做 T 卖出"
    if effect:
        direction += f" · {effect}"
    lines = [
        f"{index}. {direction}｜{name}（{display_code(row['symbol'])}）",
        f"成交：{row['quantity']:,} 份 × {money(row['price'], 3)}",
        f"金额：{money(row['gross'])}",
        f"费用：{money(row['fee'])}",
    ]
    before, after = row.get("position_before"), row.get("position_after")
    delta = row["quantity"] if buy else -row["quantity"]
    valid_position = (
        type(before) is int and type(after) is int and min(before, after) >= 0 and after - before == delta
    )
    if not buy:
        sold = ratio(row["quantity"], before) if valid_position and before > 0 else "暂不可用"
        lines.append(f"卖出比例：{sold}（占该 ETF 持仓）")
    lines.append(f"持仓变化：{before:,} → {after:,} 份" if valid_position else "持仓变化：暂不可用")
    if not buy:
        pnl = money(row["realized"], signed=True)
        basis = row["gross"] - row["fee"] - row["realized"]
        if basis > 0:
            percent = (Decimal(row["realized"]) * 100 / basis).quantize(Decimal("0.01"))
            pnl += f"（{'+' if percent > 0 else ''}{percent:.2f}%）"
        lines.append(f"本笔已实现盈亏：{pnl}")
    reason = clean(row.get("reason")) or "未记录"
    lines.extend([
        f"时间：{iso(dt(row['at']))[:19].replace('T', ' ')}",
        f"原因：{reason}",
        f"记录：订单 #{row['order_id']} / 成交 #{row['id']}",
    ])
    return "\n".join(lines)


def trade_message(rows):
    return f"{TRADE_TITLE}（{len(rows)}笔）\n{NOTICE}\n\n" + "\n\n".join(
        fill_text(row, index) for index, row in enumerate(rows, 1)
    )


def test_message(channel_label, now):
    return (
        f"{TEST_TITLE}\n{NOTICE}\n\n"
        f"渠道：{channel_label}\n时间：{iso(now)[:19].replace('T', ' ')}\n"
        "说明：此消息仅验证通知渠道，不产生订单或成交。"
    )


def markdown_text(text):
    return re.sub(r"([\\`*_\[\]~#])", r"\\\1", html.escape(text, quote=False))


def rich_message(message):
    """Recognize only the new owned template; legacy pending messages stay plain text.

    Generated values occupy one line, so reading headings/fields from the stored
    message preserves exactly the same data after restart without a schema change.
    All values are escaped before entering Markdown, HTML or Lark markup.
    """
    lines = message.splitlines()
    if len(lines) < 3 or lines[1] != NOTICE or not (
        re.fullmatch(re.escape(TRADE_TITLE) + r"（\d+笔）", lines[0]) or lines[0] == TEST_TITLE
    ):
        return None
    title = lines[0]
    markdown = [f"### {markdown_text(title)}", markdown_text(NOTICE), ""]
    html_lines = [f"<b>{html.escape(title)}</b>", html.escape(NOTICE), ""]
    elements = [{"tag": "note", "elements": [{"tag": "plain_text", "content": NOTICE}]}]
    compact, details = [], []

    def flush():
        if compact:
            elements.append({"tag": "div", "fields": list(compact)})
            compact.clear()
        if details:
            elements.append({"tag": "div", "text": {"tag": "lark_md", "content": "\n".join(details)}})
            details.clear()

    def field(label, value, short=False):
        if short:
            compact.append({"is_short": True, "text": {
                "tag": "lark_md", "content": f"**{markdown_text(label)}**\n{markdown_text(value)}",
            }})
        else:
            details.append(f"**{markdown_text(label)}**　{markdown_text(value)}")

    for line in lines[3:]:
        if not line:
            markdown.append("")
            html_lines.append("")
        elif re.match(r"^\d+\. .*｜", line):
            flush()
            if len(elements) > 1:
                elements.append({"tag": "hr"})
            heading, security = line.split("｜", 1)
            # Only the formatter-owned action portion selects the direction color.
            color = "red" if "买" in heading else "green" if "卖" in heading else "grey"
            elements.append({"tag": "div", "text": {"tag": "lark_md", "content":
                f"<font color='{color}'>**{markdown_text(heading)}**</font>｜**{markdown_text(security)}**"}})
            markdown.append(f"#### {markdown_text(line)}")
            html_lines.append(f"<b>{html.escape(line)}</b>")
        else:
            label, separator, value = line.partition("：")
            if not separator:
                return None
            markdown.append(f"**{markdown_text(label)}**　{markdown_text(value)}  ")
            html_lines.append(f"<b>{html.escape(label)}</b>　{html.escape(value)}")
            if label == "成交" and " × " in value:
                quantity, price = value.split(" × ", 1)
                field("成交数量", quantity, True)
                field("成交价格", price, True)
            else:
                field("成交金额" if label == "金额" else label, value, label in ("金额", "费用"))
    flush()
    return {
        "title": title,
        "markdown": "\n".join(markdown),
        "html": "\n".join(html_lines),
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {"template": "blue", "title": {"tag": "plain_text", "content": title}},
            "elements": elements,
        },
    }


def message_fits(message):
    rich = rich_message(message)
    return len(message.encode()) <= MAX_TEXT_BYTES and rich is not None and (
        len(rich["markdown"].encode()) <= 3800
        and len(rich["html"].encode()) <= 3800
        and len(json.dumps(rich["card"], ensure_ascii=False).encode()) <= 18000
    )
