"""Daily trigger audit. Completed OHLC can show a touch, never prove a fill.

Levels and price decisions share the live strategy. Neither later confirmation
nor later profits filter candidates. Account/quote-path simulation is not implied.
"""

import math

from app.strategies.price_action import STRATEGY, analyze_many, price_decision


def causal_review(request):
    rr_enabled = request.get("rr_enabled", True)
    bars = [b for b in request["bars"] if b["date"] <= request["as_of"] and b.get("closed") is not False][
        -260:
    ]
    days = [b["date"] for b in bars[-10:]]
    result = {
        "as_of": request["as_of"],
        "window_start": days[0] if days else None,
        "window_days": days,
        "mode": "causal",
        "strategy": STRATEGY,
        "adjustment": "qfq",
        "rr_enabled": rr_enabled,
        "ready": False,
        "evaluated_days": 0,
        "markers": [],
        "checks": [],
    }
    if (
        len(days) != 10
        or days[-1] != request["as_of"]
        or ("window_days" in request and request["window_days"] != days)
        or any(
            (i > 0 and b["date"] <= bars[i - 1]["date"])
            or not all(
                isinstance(b.get(k), (int, float)) and math.isfinite(b[k]) and b[k] > 0
                for k in ("open", "high", "low", "close")
            )
            or b["low"] > min(b["open"], b["close"])
            or b["high"] < max(b["open"], b["close"])
            for i, b in enumerate(bars)
        )
    ):
        return {**result, "reason": "最近十个交易日的完整日 K 尚未齐全，或 OHLC 异常，等待盘后同步。"}
    start = max(1, len(bars) - 10)
    requests = [
        {
            "policy": STRATEGY,
            "minimum_bars": request["minimum_bars"],
            "as_of": bars[i - 1]["date"],
            "tick": request["tick"],
            "bars": bars[max(0, i - 250) : i],
        }
        for i in range(start, len(bars))
    ]
    markers, checks = [], []
    evaluated = 0
    for bar, pa in zip(bars[start:], analyze_many(requests)):
        day = bar["date"]
        check = {"day": day, "known_through": pa.get("as_of"), "reason": ""}
        checks.append(check)
        if not pa["ready"]:
            check["reason"] = "该日前的完整日 K 不足，无法核验形态背景"
            continue
        evaluated += 1
        if not pa.get("entry") or not pa.get("entry_stop") or not pa.get("target"):
            check["reason"] = "前一交易日未形成有效看涨入场形态"
            continue
        if bar["high"] < pa["entry"]:
            check["reason"] = "当日未触及事先确定的入场价"
            continue
        price = max(bar["open"], pa["entry"])
        decision = price_decision(pa, price, request["minimum_rr"], rr_enabled)
        check["action_at_reference"] = decision["action"]
        if decision["action"] != "buy":
            check["reason"] = f"首次突破参考价不满足自动策略：{decision['reason']}；日 K 不推测回落后再次入场"
            continue
        ceiling = pa["entry_ceiling"]
        if rr_enabled:
            ceiling = min(
                ceiling,
                (pa["target"] + request["minimum_rr"] * pa["entry_stop"]) / (1 + request["minimum_rr"]),
            )
        ceiling = math.floor(ceiling / request["tick"] + 1e-9) * request["tick"]
        ambiguous = any(
            level and bar["low"] <= level
            for level in (pa["entry_stop"], pa.get("exit"), pa.get("structural_stop"))
        )
        reasons = [
            f"仅用截至 {pa['as_of']} 的已完成日 K；{pa['signal_day']} 出现{pa['setup']}",
            f"事先确定入场 {pa['entry']:.3f}、失效 {pa['entry_stop']:.3f}、目标 {pa['target']:.3f}",
            f"当日价格范围触及入场区间，参考 {price:.3f} 时与自动策略共用的规则返回买入，潜在盈亏比 {decision['reward_risk']:.2f}",
            "不等待当日或后续收盘确认，后续失败也保留触价候选",
        ]
        markers.append(
            {
                "id": f"causal:{day}:{pa['signal_day']}:{pa['setup']}",
                "day": day,
                "signal_day": pa["signal_day"],
                "known_through": pa["as_of"],
                "setup": pa["setup"],
                "entry": price,
                "trigger": pa["entry"],
                "entry_ceiling": ceiling,
                "stop": pa["entry_stop"],
                "target": pa["target"],
                "reward_risk": decision["reward_risk"],
                "input_sha256": pa["input_sha256"],
                "reasons": reasons,
                "execution_verified": False,
                "path_ambiguous": ambiguous,
                "outcome": "同日也触及失效或退出价，先后未知；仅保留可能触发记录"
                if ambiguous
                else "缺少当时连续报价与账户快照，尚未证明下单或成交",
                "outcome_day": day,
            }
        )
        check["reason"] = "触价候选；同日退出价先后未知" if ambiguous else "触价候选；无需后续收盘确认"
    return {
        **result,
        "ready": True,
        "markers": markers,
        "checks": checks,
        "evaluated_days": evaluated,
        "reason": f"逐日核验 {evaluated}/10 日，发现 {len(markers)} 个触价候选；候选不等于实际下单或成交。",
    }
