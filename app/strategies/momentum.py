"""Pure, causal trend/momentum baseline. No account writes or network access."""

from __future__ import annotations

from decimal import Decimal

from app.core.config import Settings
from app.core.types import Bar, Instrument, dec
from app.strategies.focus import FOCUS_POLICY, select_focus, liquidity


def evaluate(
    instrument: Instrument, bars: list[Bar], as_of: str, config: Settings, live_close: Decimal | None = None
) -> dict:
    result = {
        "symbol": instrument.symbol,
        "name": instrument.name,
        "index_id": instrument.index_id,
        "eligible": False,
        "reasons": [],
        "rank": None,
        "selected": False,
        "target_weight": "0",
    }
    reasons = result["reasons"]
    if not instrument.active:
        reasons.append("已终止上市或不在当前目录")
    if not instrument.verified:
        reasons.append("基金分类待核验")
    if not instrument.index_id:
        reasons.append("跟踪指数未确认")
    if instrument.accounting_block:
        reasons.append(instrument.accounting_block)
    history = sorted((b for b in bars if b.day <= as_of), key=lambda b: b.day)
    if len(history) < config.minimum_bars or len({b.day for b in history}) != len(history):
        reasons.append("有效日 K 不足或日期重复")
        return result
    if history[-1].day != as_of:
        reasons.append("最新完整交易日日 K 缺失")
        return result
    closes = [dec(b.close) for b in history]
    if any(c <= 0 for c in closes):
        reasons.append("日 K 数值异常")
        return result
    # Intraday references add a provisional price point for trend only. Liquidity still
    # uses completed sessions, and no synthetic daily bar is written to storage.
    if live_close is not None:
        closes.append(live_close)
    ma20 = sum(closes[-20:]) / 20
    ma60 = sum(closes[-60:]) / 60
    r20 = closes[-1] / closes[-21] - 1
    r60 = closes[-1] / closes[-61] - 1
    amount, _ = liquidity(history, as_of)
    score = Decimal("0.6") * r20 + Decimal("0.4") * r60
    result.update(
        {
            "close": str(closes[-1]),
            "ma20": str(ma20),
            "ma60": str(ma60),
            "r20": str(r20),
            "r60": str(r60),
            "amount20": str(amount) if amount is not None else None,
            "score": str(score),
        }
    )
    if not closes[-1] > ma20 > ma60 or r20 <= 0:
        reasons.append("趋势门槛未通过")
    result["eligible"] = not reasons
    return result


def build_plan(
    universe: list[Instrument],
    histories: dict[str, list[Bar]],
    held: set[str],
    as_of: str,
    execute_day: str,
    config: Settings,
    cooldown: set[str] | None = None,
    live_prices: dict[str, Decimal] | None = None,
) -> dict:
    rows = [
        evaluate(i, histories.get(i.symbol, []), as_of, config, (live_prices or {}).get(i.symbol))
        for i in universe
    ]
    focus = select_focus(universe, histories, as_of)
    cooldown = cooldown or set()
    # Only manual membership determines which instruments can enter the strategy.
    for row in rows:
        row.update(focus[row["symbol"]])
        if live_prices is not None and row["symbol"] not in live_prices:
            row["eligible"] = False
            row["reasons"].append("盘中参考行情缺失、过期或状态待确认")
        if not row["representative"]:
            row["eligible"] = False
            row["reasons"].append(row["focus_reason"])
    ranked = sorted((r for r in rows if r["eligible"]), key=lambda r: (-dec(r["score"]), r["symbol"]))
    for rank, row in enumerate(ranked, 1):
        row["rank"] = rank
    keep = [
        r
        for r in ranked
        if r["symbol"] in held and r["rank"] <= config.retain_rank and r["symbol"] not in cooldown
    ][: config.max_positions]
    selected = {r["symbol"] for r in keep}
    for row in ranked:
        if len(selected) >= config.max_positions:
            break
        if row["symbol"] not in cooldown:
            selected.add(row["symbol"])
    weight = min(config.max_weight, config.max_exposure / len(selected)) if selected else Decimal(0)
    for row in rows:
        row["selected"] = row["symbol"] in selected
        row["target_weight"] = str(weight if row["selected"] else 0)
        if row["eligible"] and not row["selected"]:
            row["reasons"].append("当日止损冷却" if row["symbol"] in cooldown else "排名未进入目标持仓")
    return {
        "strategy": "trend-momentum-v1",
        "focus_policy": FOCUS_POLICY,
        "as_of": as_of,
        "execute_day": execute_day,
        "rows": sorted(rows, key=lambda r: (r["rank"] or 100000, r["symbol"])),
        "targets": {symbol: str(weight) for symbol in sorted(selected)},
    }
