"""Manual ETF membership; classification and liquidity metrics are display-only."""

from decimal import Decimal
import hashlib
import json
import re
import unicodedata
from pathlib import Path

from app.core.config import DEFAULT_MINIMUM_AMOUNT
from app.core.classification import CLASSIFICATION_RULES, observation_category
from app.core.types import Bar, Instrument, dec

FOCUS_RULES = json.loads((Path(__file__).resolve().parents[2] / "config/focus_groups.json").read_text())
LIQUIDITY_RULES = {"revision": "manual-v1", "window": 20, "selection": "manual"}
FOCUS_POLICY = hashlib.sha256(
    json.dumps(
        {
            "groups": FOCUS_RULES,
            "classification": CLASSIFICATION_RULES,
            "liquidity": LIQUIDITY_RULES,
            "eligibility": "verified-etfs-all-types-and-markets-v1",
            "ranking": "price-only-v1",
        },
        sort_keys=True,
        ensure_ascii=False,
    ).encode()
).hexdigest()


def normalized_index(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).upper()
    value = re.sub(r"\s+", "", value)
    value = re.sub(r"(?:全收益|净收益|价格)?指数$", "", value)
    return value


def group_for(instrument: Instrument) -> tuple[str, str]:
    if not instrument.verified or instrument.category == "unknown" or instrument.region == "unknown":
        return "", "类型待确认"
    index = normalized_index(instrument.index_id)
    prefix = f"{instrument.category}:{instrument.region}:"
    if instrument.category == "money":
        return prefix + "cash", "货币 ETF"
    if not index:
        return "", "跟踪指数待确认"
    if instrument.category == "equity" and instrument.region == "CN":
        for rule in FOCUS_RULES["rules"]:
            if any(keyword.upper() in index for keyword in rule["keywords"]):
                return prefix + rule["name"], rule["name"]
    # Cross-border markets and other asset types retain exact benchmark identities.
    return prefix + index, index


def liquidity(bars: list[Bar], as_of: str) -> tuple[Decimal | None, str]:
    history = sorted((b for b in bars if b.day <= as_of), key=lambda b: b.day)
    if not history or history[-1].day != as_of:
        return None, "最新完整交易日数据缺失"
    if len({b.day for b in history}) != len(history):
        return None, "历史日期重复"
    if len(history) < 20:
        return None, "不足 20 个完整交易日，不参与流动性比较"
    if any(b.amount is None for b in history[-20:]):
        return None, "成交额待补齐，不影响价格信号"
    try:
        values = [dec(b.amount) for b in history[-20:]]
        if any(a < 0 for a in values) or any(dec(b.close) <= 0 for b in history[-20:]):
            raise ValueError("invalid bar")
    except (ValueError, ArithmeticError):
        return None, "历史数值异常"
    return sum(values) / 20, ""


def turnover_liquidity(bars: list[Bar], as_of: str) -> tuple[Decimal | None, str]:
    history = sorted((b for b in bars if b.day <= as_of), key=lambda b: b.day)
    if not history or history[-1].day != as_of or len(history) < 20:
        return None, "不足 20 个完整交易日的换手率数据"
    if len({b.day for b in history}) != len(history):
        return None, "换手率历史日期重复"
    window = history[-20:]
    if any(b.turnover_rate is None for b in window):
        return None, "最近 20 日换手率数据缺失"
    sources = {b.turnover_source for b in window}
    if len(sources) != 1 or "" in sources:
        return None, "最近 20 日换手率来源不一致或待核验"
    try:
        rates = [dec(b.turnover_rate) for b in window]
        if any(rate < 0 for rate in rates):
            raise ValueError("negative turnover")
    except (ValueError, ArithmeticError):
        return None, "换手率历史数值异常"
    return sum(rates) / 20, ""


def select_focus(
    universe: list[Instrument],
    histories: dict[str, list[Bar]],
    as_of: str,
    minimum_amount: Decimal = DEFAULT_MINIMUM_AMOUNT,
    categories: list[str] | None = None,
    markets: list[str] | None = None,
    minimum_turnover: Decimal = Decimal("0"),
) -> dict[str, dict]:
    # Legacy arguments remain readable in historical plan evidence; none gates membership.
    results = {}
    for instrument in universe:
        key, label = group_for(instrument)
        average, amount_reason = liquidity(histories.get(instrument.symbol, []), as_of)
        turnover, turnover_reason = turnover_liquidity(histories.get(instrument.symbol, []), as_of)
        watched = instrument.watched
        results[instrument.symbol] = {
            **observation_category(instrument),
            "symbol": instrument.symbol,
            "name": instrument.name,
            "focus_group": key,
            "focus_label": label,
            "group_size": 1,
            "amount20": str(average) if average is not None else None,
            "amount_reason": amount_reason,
            "turnover20": str(turnover) if turnover is not None else None,
            "turnover_reason": turnover_reason,
            "focus_status": "manual" if watched else "archived",
            "representative": watched,
            "representative_symbol": instrument.symbol if watched else None,
            "representative_name": instrument.name if watched else None,
            "liquidity_rank": None,
            "focus_reason": "手动添加" if watched else "未加入手动名单",
            "focus_as_of": as_of,
            "candidate": watched,
        }
    return results
