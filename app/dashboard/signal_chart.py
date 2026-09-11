"""Read-only chart data aligned with the selected strategy's completed-day window."""

import json
import math

from app.core.types import Bar, yuan
from app.storage.db import get_state, instruments, settings
from app.strategies.price_action import analysis_request, input_hash, price_decision
from app.strategies.buy_review import review_payload


def candidate_setup(plan, row, config, tick):
    """Explain an existing preview candidate using its exact strategy evidence."""
    pa = row.get("pa", {})
    if plan.get("mode") != "post_close" or not row.get("post_close_candidate"):
        return None
    if not pa.get("ready") or not pa.get("entry") or not pa.get("entry_stop"):
        return None
    decision = price_decision(pa, pa["entry"], config.pa_min_rr, config.pa_rr_enabled)
    if decision["action"] != "buy":
        return None
    ceiling = pa["entry_ceiling"]
    if config.pa_rr_enabled:
        rr = float(config.pa_min_rr)
        ceiling = min(ceiling, (pa["target"] + rr * pa["entry_stop"]) / (1 + rr))
    ceiling = math.floor(ceiling / tick + 1e-9) * tick
    bull = pa.get("evidence", {}).get("bull") or {}
    context = {
        "confirmed-breakout": "突破已确认",
        "breakout-retest": "突破后回测",
        "continuation": "突破延续",
    }.get(bull.get("context"), bull.get("context") or pa.get("trend", ""))
    rr_reason = (
        f"盈亏比过滤已开启，入场线潜在盈亏比 {decision['reward_risk']:.2f}，达到最低 {config.pa_min_rr}"
        if config.pa_rr_enabled
        else f"盈亏比过滤已关闭，入场线潜在盈亏比 {decision['reward_risk']:.2f}，不作为筛选门槛"
    )
    target_reason = (
        "目标取入场线上方最近的已确认压力"
        if pa.get("evidence", {}).get("target")
        else "上方无已确认压力，目标按信号区间振幅的两倍测量"
    )
    return {
        "as_of": plan["as_of"],
        "execute_day": plan["execute_day"],
        "signal_day": pa["signal_day"],
        "setup": pa["setup"],
        "trend": pa.get("trend", ""),
        "context": context,
        "signal_high": bull.get("high"),
        "signal_low": bull.get("low"),
        "entry": pa["entry"],
        "entry_stop": pa["entry_stop"],
        "target": pa["target"],
        "entry_ceiling": ceiling,
        "reward_risk": decision["reward_risk"],
        "rr_enabled": config.pa_rr_enabled,
        "minimum_rr": float(config.pa_min_rr),
        "reasons": [
            f"{pa['signal_day']} 完成{pa['setup']}（{context}），截至 {plan['as_of']} 入场结构仍有效",
            f"突破信号区间上沿 {bull['high']:.3f} 后观察入场，区间下沿 {bull['low']:.3f} 下方为失效价"
            if bull.get("high") and bull.get("low")
            else "价格须突破已确定的入场线，跌破入场失效价则结构失效",
            target_reason,
            rr_reason,
            f"{plan['execute_day']} 等待盘中价格触发并连续确认，执行时继续核对价格上限、账户及风控条件",
        ],
    }


def chart_payload(conn, plan, symbol, calendar):
    row = next((r for r in plan["rows"] if r["symbol"] == symbol), None)
    if not row:
        raise LookupError("该 ETF 不在当前策略记录中，请刷新列表")
    config_id, config = settings(conn)
    if config_id != plan.get("config_id"):
        raise ValueError("策略参数已更新，请刷新目标列表")
    instrument = instruments(conn).get(symbol)
    if not instrument:
        raise LookupError("ETF 不存在")
    bars = [
        json.loads(r[0])
        for r in conn.execute(
            "SELECT payload FROM bars WHERE symbol=? AND adjustment='qfq' AND day<=? ORDER BY day DESC LIMIT ?",
            (symbol, plan["as_of"], config.history_bars),
        )
    ][::-1]
    pa = row.get("pa", {})
    matched = not plan.get("strategy", "").startswith("price-action-")
    if pa.get("ready"):
        request = analysis_request(
            [Bar(**b) for b in bars], plan["as_of"], instrument.tick, config.minimum_bars
        )
        matched = input_hash(request) == pa.get("input_sha256")
    position = get_state(conn, f"pa_position:{symbol}", {}) if plan.get("mode") == "live" else {}
    raw = pa.get("raw", {}) if matched and pa.get("ready") and plan.get("mode") == "live" else {}
    return {
        "symbol": symbol,
        "name": instrument.name,
        "bars": bars,
        "buy_review": review_payload(conn, symbol, plan["as_of"], calendar),
        "adjustment": "qfq",
        "as_of": plan["as_of"],
        "signal_id": plan["id"],
        "config_id": config_id,
        "history_count": config.history_bars,
        "minimum_bars": config.minimum_bars,
        "matched": matched,
        "candidate_setup": candidate_setup(plan, row, config, instrument.tick / 1_000_000)
        if matched
        else None,
        "row": row,
        "position_reference": {k: yuan(position[k]) for k in ("stop", "target") if position.get(k)},
        "intraday_reference": {
            "session_day": (row.get("evaluated_quote_at") or "")[:10],
            # Use the exact tick-rounded prices checked by execution, expressed in yuan.
            "levels": {k: yuan(v) for k, v in raw.items() if isinstance(v, int) and v > 0},
        },
        "warning": ""
        if matched
        else (
            "历史数据与当前信号尚未对齐，等待下一次计算；暂不叠加策略价位。"
            if pa.get("ready")
            else "暂无有效策略结构，当前展示历史 K 线和观察指标。"
        ),
    }
