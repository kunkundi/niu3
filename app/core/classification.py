"""Explicit observation taxonomy; never infer a broad index from a missing match."""

import json
import re
import unicodedata
from pathlib import Path

from app.core.types import Instrument

CLASSIFICATION_RULES = json.loads(
    (Path(__file__).resolve().parents[2] / "config/observation_categories.json").read_text(encoding="utf-8")
)
CATEGORY_OPTIONS = CLASSIFICATION_RULES["categories"]
MARKET_OPTIONS = CLASSIFICATION_RULES["markets"]
CATEGORY_LABELS = {option["value"]: option["label"] for option in CATEGORY_OPTIONS}
MARKET_LABELS = {option["value"]: option["label"] for option in MARKET_OPTIONS}
_PATTERNS = [
    (rule["category"], re.compile(rule["pattern"], re.IGNORECASE)) for rule in CLASSIFICATION_RULES["rules"]
]


def observation_category(instrument: Instrument) -> dict:
    category, reason = "unknown", "基金类型或投资市场待核验"
    if instrument.verified and instrument.region in MARKET_LABELS:
        if instrument.category in {"bond", "commodity", "money"}:
            category, reason = instrument.category, "按已核验的基金资产类型分类"
        elif instrument.category in {"equity", "cross_border"}:
            index = re.sub(r"\s+", "", unicodedata.normalize("NFKC", instrument.index_id))
            if index:
                category, reason = "other_equity", "跟踪指数尚未匹配宽基、行业、主题、风格或策略规则"
                for value, pattern in _PATTERNS:
                    if pattern.search(index):
                        category, reason = value, f"跟踪指数匹配工作台的{CATEGORY_LABELS[value]}规则"
                        break
            else:
                reason = "跟踪指数待核验"
    return {
        "focus_category": category,
        "focus_category_label": CATEGORY_LABELS.get(category, "待分类"),
        "focus_category_reason": reason,
        "focus_market": instrument.region,
        "focus_market_label": MARKET_LABELS.get(instrument.region, "市场待核验"),
    }


def scope_description(categories: list[str], markets: list[str]) -> str:
    if not categories or not markets:
        return "未选择候选范围"
    market = (
        "全部市场" if set(markets) == set(MARKET_LABELS) else "、".join(MARKET_LABELS[m] for m in markets)
    )
    category = (
        "全部分类"
        if set(categories) == set(CATEGORY_LABELS)
        else "、".join(CATEGORY_LABELS[c] for c in categories)
    )
    return f"{market} · {category}"
