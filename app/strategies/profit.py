"""Conditional profit exits and bounded, causal re-entry after a profit sale."""

import hashlib
import json
from dataclasses import replace
from datetime import time
from decimal import Decimal

from app.core.types import dt, iso
from app.storage.db import dump
from app.strategies.minute_t import context

LEGACY_PROFIT = "裸 K 结构止盈：到达入场时确定的压力／测量目标"
PROFIT_REASON = "裸 K 确认止盈：目标附近已完成 5 分钟 K 转弱"
PROFIT_REASONS = (LEGACY_PROFIT, PROFIT_REASON)
POLICY = "pa-profit-v1"


def cooling_symbols(conn, day, config):
    symbols = {r[0] for r in conn.execute("SELECT symbol FROM cooldown WHERE day=?", (day,))}
    if config.strategy_model != "price_action":
        return symbols
    # Preserve historical rows. Ignore only a cooldown positively attributable
    # solely to profit orders; unknown causes and actual stops remain blocked.
    for symbol in list(symbols):
        reasons = [r[0] for r in conn.execute(
            "SELECT reason FROM orders WHERE symbol=? AND kind='risk' AND "
            "(substr(created_at,1,10)=? OR id IN "
            "(SELECT order_id FROM fills WHERE symbol=? AND substr(at,1,10)=?)) "
            "UNION SELECT reason FROM risk_intents WHERE symbol=?",
            (symbol, day, symbol, day, symbol),
        )]
        if reasons and all(reason in PROFIT_REASONS for reason in reasons):
            symbols.remove(symbol)
    return symbols


def profit_signal(conn, symbol, reference, quote, tick, config, now):
    """A target touch alone cannot latch a cross-day liquidation."""
    target = reference.get("target", 0)
    result = context(conn, symbol, now, tick)
    if not target or not result["ready"] or not quote.fresh(now, config.quote_max_age):
        return None
    bar, tolerance = result["bar"], result["tolerance"]
    # Re-check the executable bid; a breakout above the rejection candle cancels
    # profit taking. Hard daily invalidation is managed independently.
    lower = max(target - tolerance, bar["low"])
    if not (
        bar["high"] >= target and bar["close"] < bar["open"]
        and bar["close"] <= (bar["high"] + bar["low"]) // 2
        and lower <= quote.bid <= bar["close"] + tolerance
        and quote.status == "trading"
    ):
        return None
    return {**result, "policy": POLICY, "target": target, "price_min": lower,
            "price_max": bar["close"] + tolerance}


def profit_order_problem(conn, order, instrument, quote, config, now):
    row = conn.execute("SELECT payload FROM intraday_decisions WHERE order_id=?", (order["id"],)).fetchone()
    evidence = json.loads(row[0]) if row else {}
    frozen = evidence.get("profit_exit", {})
    version = conn.execute("SELECT MAX(id) FROM configs").fetchone()[0]
    if (not frozen or frozen.get("policy") != POLICY or order["config_id"] != version
            or config.strategy_model != "price_action"
            or dt(order["created_at"]).date() != now.date()
            or (now - dt(order["created_at"])).total_seconds() >= config.intraday_order_ttl):
        return "止盈条件已过期，等待重新确认"
    executable = replace(quote, bid=quote.bid // instrument.tick * instrument.tick)
    live = profit_signal(conn, instrument.symbol, {"target": frozen["target"]},
                         executable, instrument.tick, config, now)
    if not live or live["input_sha256"] != frozen.get("input_sha256"):
        return "止盈转弱条件已改变，等待重新确认"
    return ""


def latest_profit_sale(conn, symbol, now):
    pending = conn.execute(
        "SELECT d.payload FROM orders o JOIN intraday_decisions d ON d.order_id=o.id "
        "WHERE o.symbol=? AND o.side='BUY' AND o.kind='intraday' AND o.status IN ('pending','partial') "
        "AND substr(o.created_at,1,10)=? ORDER BY o.id DESC LIMIT 1", (symbol, now.date().isoformat()),
    ).fetchone()
    if pending:
        sale = json.loads(pending[0]).get("pa", {}).get("reentry", {}).get("sale")
        if sale:
            return {**sale, "pending": True}
    row = conn.execute(
        "SELECT f.at,o.id,o.reason,o.filled,d.payload FROM fills f JOIN orders o ON o.id=f.order_id "
        "LEFT JOIN intraday_decisions d ON d.order_id=o.id "
        "WHERE f.symbol=? AND f.at<=? ORDER BY f.at DESC,f.id DESC LIMIT 1", (symbol, iso(now)),
    ).fetchone()
    if not row or row["reason"] not in PROFIT_REASONS or row["at"][:10] != now.date().isoformat():
        return None
    evidence = json.loads(row["payload"]) if row["payload"] else {}
    target = evidence.get("position_reference", {}).get("target")
    if not target:
        return None
    # Historical raw prices cannot be reused across an ex-date correction.
    if conn.execute("SELECT 1 FROM actions WHERE symbol=? AND ex_day>? AND ex_day<=?",
                    (symbol, evidence.get("quote", {}).get("at", "")[:10], now.date().isoformat())).fetchone():
        return None
    return {"order_id": row["id"], "at": row["at"], "quantity": row["filled"], "target": target}


def reentry_setup(conn, instrument, pa, sale, quote, config, now, factor):
    """Reclaim the sold target AND break a completed intraday range, without chasing."""
    if (not sale or not pa.get("ready") or pa.get("action") == "exit"
            or "下降" in pa.get("trend", "") or now.time() >= time(14, 45)
            or not quote.fresh(now, config.quote_max_age) or quote.status != "trading"
            or min(quote.ask, quote.bid) <= 0 or quote.bid > quote.ask):
        return None, "止盈后等待有效日线背景与盘中新结构"
    minute = context(conn, instrument.symbol, now, instrument.tick)
    if not minute["ready"]:
        return None, minute["message"]
    bar, tick = minute["bar"], instrument.tick
    if bar["at"] <= sale["at"]:
        return None, "等待止盈成交后的完整 5 分钟 K"
    entry = max(minute["resistance"], sale["target"]) + tick
    stop = max(min(b["low"] for b in minute["bars"][-2:]) - tick,
               pa.get("raw", {}).get("structural_stop", 0))
    if not (bar["close"] >= entry and bar["close"] > bar["open"]
            and bar["close"] >= (bar["high"] + bar["low"]) // 2 and 0 < stop < entry):
        return None, "止盈后等待 5 分钟收阳突破盘中压力并收复原目标"
    risk = entry - stop
    targets = [pa["raw"].get(k, 0) for k in ("target", "resistance")]
    target = min((p for p in targets if p > entry), default=entry + 2 * risk)
    ceiling = min(entry + risk // 2, bar["close"] + minute["tolerance"])
    if config.pa_rr_enabled:
        ceiling = min(ceiling, int((Decimal(target) + config.pa_min_rr * stop) / (1 + config.pa_min_rr)))
    ceiling = ceiling // tick * tick
    price = (quote.ask + tick - 1) // tick * tick
    if not entry <= price <= ceiling:
        return None, "止盈后突破已确认，等待盘口进入再入场区间（不追价）"
    raw = {**pa["raw"], "entry": entry, "entry_stop": stop, "entry_ceiling": ceiling, "target": target}
    evidence = {**minute, "policy": POLICY, "sale": sale, "price_min": entry, "price_max": ceiling}
    result = {**pa, "raw": raw, "action": "buy", "setup": "止盈后 5 分钟突破再入场",
              "signal_day": bar["at"], "reentry": evidence,
              "reward_risk": (target - price) / (price - stop)}
    # Display levels stay in the same adjusted daily-price units as other signals.
    for key in ("entry", "entry_stop", "entry_ceiling", "target"):
        result[key] = float(Decimal(raw[key]) / 1_000_000 / factor)
    result["execution_sha256"] = hashlib.sha256(dump({"daily": pa["input_sha256"],
        "minute": minute["input_sha256"], "sale": sale["order_id"], "policy": POLICY}).encode()).hexdigest()
    return result, "裸 K 再入场：止盈后已完成 5 分钟 K 突破压力并收复原目标，限定价格买回"


def reentry_problem(conn, pa, instrument, quote, config, now):
    evidence = pa.get("reentry", {})
    if not evidence:
        return ""
    if evidence.get("policy") != POLICY or evidence.get("sale", {}).get("at", "")[:10] != now.date().isoformat():
        return "再入场依据已过期，等待新决策"
    minute = context(conn, instrument.symbol, now, instrument.tick)
    if not minute["ready"] or minute["input_sha256"] != evidence.get("input_sha256"):
        return "再入场分钟结构已更新，等待新决策"
    if now.time() >= time(14, 45):
        return "尾盘不发起止盈后再入场"
    price = (quote.ask + instrument.tick - 1) // instrument.tick * instrument.tick
    if not evidence["price_min"] <= price <= evidence["price_max"]:
        return "盘口价格超出止盈后再入场区间"
    return ""
