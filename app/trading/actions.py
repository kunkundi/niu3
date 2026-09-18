from __future__ import annotations

from dataclasses import replace
from datetime import datetime, time
from decimal import Decimal, ROUND_HALF_UP

from app.core.types import dec, iso, units
from app.storage.db import get_state, instruments, put_instrument, set_state


def adjust_pa_reference(conn, symbol, *, dividend=0, ratio=Decimal(1)):
    reference = get_state(conn, f"pa_position:{symbol}", {})
    if reference:
        for key in ("stop", "target", "profit_core_stop"):
            if reference.get(key):
                reference[key] = max(0, int((Decimal(reference[key]) - dividend) / ratio))
        for key in ("profit_budget", "profit_sold"):
            if key in reference:
                reference[key] = int(Decimal(reference[key]) * ratio)
        set_state(conn, f"pa_position:{symbol}", reference)


def ingest_actions(conn, actions: list[dict]):
    for item in actions:
        if item["kind"] not in {"split", "dividend"} or dec(item["value"]) <= 0:
            raise ValueError("invalid corporate action")
        existing = conn.execute("SELECT * FROM actions WHERE id=?", (item["id"],)).fetchone()
        if existing:
            changed = any(
                str(existing[k]) != str(item[k]) for k in ("record_day", "ex_day", "pay_day", "value")
            )
            if changed:
                instrument = instruments(conn).get(item["symbol"])
                if instrument:
                    put_instrument(conn, replace(instrument, accounting_block="公司行动数据发生修订，需核验"))
            continue
        conn.execute(
            "INSERT INTO actions(id,symbol,kind,record_day,ex_day,pay_day,value,verified,source) VALUES(?,?,?,?,?,?,?,?,?)",
            (
                item["id"],
                item["symbol"],
                item["kind"],
                item["record_day"],
                item["ex_day"],
                item["pay_day"],
                str(item["value"]),
                int(item["verified"]),
                item["source"],
            ),
        )


def apply_actions(conn, now: datetime):
    universe = instruments(conn)
    today = now.date().isoformat()
    for action in conn.execute(
        "SELECT * FROM actions WHERE status NOT IN ('paid','applied') ORDER BY ex_day,id"
    ).fetchall():
        instrument = universe.get(action["symbol"])
        if not instrument:
            continue
        if not action["verified"]:
            put_instrument(conn, replace(instrument, accounting_block="分红／折算事件待核验"))
            continue
        if action["kind"] == "dividend":
            record = action["record_day"]
            if action["status"] == "pending" and (
                record < today or record == today and now.time() >= time(15)
            ):
                shares = conn.execute(
                    "SELECT COALESCE(SUM(delta),0) FROM position_ledger WHERE symbol=? AND substr(at,1,10)<=?",
                    (action["symbol"], record),
                ).fetchone()[0]
                entitlement = units(
                    (dec(action["value"]) * shares).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                )
                conn.execute(
                    "UPDATE actions SET status='recorded',entitlement=? WHERE id=?",
                    (entitlement, action["id"]),
                )
            action = conn.execute("SELECT * FROM actions WHERE id=?", (action["id"],)).fetchone()
            if action["status"] == "recorded" and action["ex_day"] <= today:
                conn.execute(
                    "UPDATE actions SET status='entitled',receivable=entitlement WHERE id=?", (action["id"],)
                )
                per_share = units(action["value"])
                adjust_pa_reference(conn, action["symbol"], dividend=per_share)
                # Adjust risk references, not historical accounting cost.
                conn.execute(
                    "UPDATE lots SET high=MAX(0,high-?),risk_cost=MAX(0,risk_cost-quantity*?) "
                    "WHERE symbol=? AND quantity>0 AND acquired_day<=?",
                    (per_share, per_share, action["symbol"], record),
                )
                cancel_action_orders(conn, action["symbol"], now)
            action = conn.execute("SELECT * FROM actions WHERE id=?", (action["id"],)).fetchone()
            if action["status"] == "entitled" and action["pay_day"] <= today:
                conn.execute(
                    "INSERT OR IGNORE INTO cash_ledger(key,delta,kind,at,reference) VALUES(?,?,'dividend',?,?)",
                    (f"action:{action['id']}", action["entitlement"], iso(now), action["id"]),
                )
                conn.execute("UPDATE actions SET status='paid',receivable=0 WHERE id=?", (action["id"],))
        elif action["ex_day"] <= today:
            # Split tables specify the effective day; only pre-effective-day lots participate.
            lots = conn.execute(
                "SELECT * FROM lots WHERE symbol=? AND quantity>0 AND acquired_day<?",
                (action["symbol"], action["ex_day"]),
            ).fetchall()
            ratio = dec(action["value"])
            if any(dec(lot["quantity"]) * ratio != int(dec(lot["quantity"]) * ratio) for lot in lots):
                put_instrument(
                    conn, replace(instrument, accounting_block="份额折算含不足一份余额，需核验现金补偿")
                )
                continue
            delta = 0
            for lot in lots:
                quantity = int(dec(lot["quantity"]) * ratio)
                high = int(dec(lot["high"]) / ratio)
                conn.execute("UPDATE lots SET quantity=?,high=? WHERE id=?", (quantity, high, lot["id"]))
                delta += quantity - lot["quantity"]
            conn.execute(
                "INSERT OR IGNORE INTO position_ledger(key,symbol,delta,at,kind) VALUES(?,?,?,?,'split')",
                (f"action:{action['id']}", action["symbol"], delta, action["ex_day"] + "T09:00:00+08:00"),
            )
            conn.execute("UPDATE actions SET status='applied' WHERE id=?", (action["id"],))
            adjust_pa_reference(conn, action["symbol"], ratio=ratio)
            cancel_action_orders(conn, action["symbol"], now)


def cancel_action_orders(conn, symbol, now):
    conn.execute(
        "UPDATE orders SET status='cancelled',updated_at=?,blocked_reason='除息／折算后重新核验' "
        "WHERE symbol=? AND status IN ('pending','partial')",
        (iso(now), symbol),
    )
