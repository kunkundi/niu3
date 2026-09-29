"""Bounded retries of unfilled ordinary entries; old decisions stay immutable."""

import json
from dataclasses import replace

from app.core.types import dt, iso
from app.storage.db import get_state, latest_quote, set_state
from app.strategies.price_action import trigger_problem

POLICY = "unfilled-entry-retry-v1"
PRICE_CANCEL = "入场价格暂离有效区间，零成交撤单"
INVALIDATED = "入场结构已失效，停止重试"


def same_structure(frozen, current):
    return bool(frozen.get("input_sha256") and all(
        frozen.get(key) == current.get(key)
        for key in ("input_sha256", "execution_sha256", "signal_day")
    ))


def observe_entries(conn, plan, config_id, config, now):
    """Observe flat accounts too: a cancelled entry cannot forget a broken stop.

    Return terminal-order cutoffs so confirmations must use new observations.
    Only orders explicitly created under this policy can become retry candidates.
    """
    day = now.date().isoformat()
    state = get_state(conn, "entry_retry_blocks", {})
    previous = state if state.get("day") == day else {"day": day, "intents": {}}
    blocked = dict(previous["intents"])
    rows = {r["symbol"]: r for r in plan["rows"]}
    cutoffs = {}
    orders = conn.execute(
        "SELECT o.*,d.payload FROM orders o JOIN intraday_decisions d ON d.order_id=o.id "
        "WHERE o.kind='intraday' AND o.side='BUY' AND o.config_id=? "
        "AND substr(o.created_at,1,10)=? AND json_extract(d.payload,'$.entry_attempt.policy')=?",
        (config_id, day, POLICY),
    ).fetchall()
    for order in orders:
        symbol = order["symbol"]
        evidence = json.loads(order["payload"])
        intent = evidence["entry_attempt"]["intent_key"]
        if order["status"] in {"cancelled", "expired"}:
            cutoffs[symbol] = max(cutoffs.get(symbol, ""), order["updated_at"])
        if order["filled"] or intent in blocked:
            continue
        latest = latest_quote(conn, symbol)
        if not latest or not latest[1].fresh(now, config.quote_max_age):
            continue
        quote = latest[1]
        if quote.status != "trading" or quote.last <= 0:
            continue
        frozen = evidence.get("pa", {})
        current = rows.get(symbol, {}).get("pa", {})
        levels = [frozen.get("raw", {})]
        if current.get("ready"):
            levels.append(current.get("raw", {}))
        stops = [p[k] for p in levels for k in ("entry_stop", "structural_stop", "exit") if p.get(k)]
        if stops and quote.last <= max(stops):
            blocked[intent] = {"at": iso(now), "quote_at": quote.at, "reason": INVALIDATED}
    for order in orders:
        intent = json.loads(order["payload"])["entry_attempt"]["intent_key"]
        if intent in blocked and not order["filled"] and order["status"] in {"pending", "partial"}:
            conn.execute("UPDATE orders SET status='cancelled',updated_at=?,blocked_reason=? WHERE id=?",
                         (iso(now), INVALIDATED, order["id"]))
            cutoffs[order["symbol"]] = iso(now)
    if blocked != previous["intents"]:
        set_state(conn, "entry_retry_blocks", {"day": day, "intents": blocked})
    return cutoffs


def price_cancellation(conn, order, pa, quote, config):
    if order["kind"] != "intraday" or order["side"] != "BUY" or order["filled"]:
        return False
    row = conn.execute("SELECT payload FROM intraday_decisions WHERE order_id=?", (order["id"],)).fetchone()
    evidence = json.loads(row[0]) if row else {}
    if (evidence.get("entry_attempt", {}).get("policy") != POLICY
            or not pa.get("ready") or pa.get("action") != "hold"
            or not same_structure(evidence.get("pa", {}), pa)):
        return False
    levels = pa.get("raw", {})
    if not all(levels.get(k) for k in ("entry", "entry_stop", "entry_ceiling", "target")):
        return False
    return not levels["entry"] <= quote.last <= levels["entry_ceiling"] or (
        config.pa_rr_enabled
        and levels["target"] - quote.last < config.pa_min_rr * (quote.last - levels["entry_stop"])
    )


def next_attempt(conn, intent, pa, quote, instrument, config, now):
    rows = conn.execute(
        "SELECT o.*,d.payload,EXISTS(SELECT 1 FROM fills f WHERE f.order_id=o.id) has_fills "
        "FROM orders o LEFT JOIN intraday_decisions d ON d.order_id=o.id "
        "WHERE o.key=? OR json_extract(d.payload,'$.entry_attempt.intent_key')=? ORDER BY o.id",
        (intent, intent),
    ).fetchall()
    metadata = {"policy": POLICY, "intent_key": intent, "attempt": len(rows) + 1,
                "previous_order_id": rows[-1]["id"] if rows else None}
    if not rows:
        return intent, metadata
    blocks = get_state(conn, "entry_retry_blocks", {})
    if blocks.get("day") == now.date().isoformat() and intent in blocks.get("intents", {}):
        return None
    for order in rows:
        evidence = json.loads(order["payload"]) if order["payload"] else {}
        safe_end = (
            (order["status"] == "cancelled" and order["blocked_reason"] == PRICE_CANCEL)
            or (order["status"] == "expired" and order["blocked_reason"] == "盘中订单超时")
        )
        if (not safe_end or order["filled"] or order["has_fills"]
                or evidence.get("entry_attempt", {}).get("policy") != POLICY
                or not same_structure(evidence.get("pa", {}), pa)):
            return None
    # Budgets/intervals are enforced by the caller; recheck the executable ask
    # before spending another attempt. Matching will check it again afterwards.
    executable = replace(quote, ask=(quote.ask + instrument.tick - 1) // instrument.tick * instrument.tick)
    if (dt(quote.at) <= dt(rows[-1]["updated_at"])
            or trigger_problem(pa, executable, config, "intraday", "BUY")):
        return None
    return f"{intent}:retry:{rows[-1]['id']}", metadata
