"""Read-only next-session preview; never an input to order execution."""

import hashlib
import json
import math
from datetime import date, time

from app.automation.service import history_target
from app.core.types import Bar, TZ, dt
from app.storage.db import tracked_instruments
from app.storage.focus import focus_snapshot
from app.strategies.focus import FOCUS_POLICY
from app.strategies.price_action import STRATEGY, analysis_request, build_plan, input_hash, price_decision


def post_close_window(calendar, now):
    local = now.astimezone(TZ)
    return calendar.known(local.date()) and (
        not calendar.is_open(local.date()) or local.time() < time(9, 30) or local.time() >= time(15, 30)
    )


def post_close_payload(conn, calendar, now, config_id, config):
    as_of = history_target(calendar, now)
    if not as_of:
        return None
    try:
        next_day = calendar.next(date.fromisoformat(as_of))
    except ValueError:
        return None
    record = conn.execute("SELECT * FROM plans WHERE as_of=? AND config_id=?", (as_of, config_id)).fetchone()
    if not record or record["execute_day"] != next_day or dt(record["at"]) > now:
        return None
    plan = json.loads(record["payload"])
    if plan.get("focus_policy") != FOCUS_POLICY:
        return None
    universe = tracked_instruments(conn)
    histories, hashes = {}, {}
    updated_at = record["at"]
    if config.strategy_model == "price_action":
        for symbol, instrument in universe.items():
            rows = conn.execute(
                "SELECT payload,fetched_at FROM bars WHERE symbol=? AND adjustment='qfq' AND day<=? "
                "ORDER BY day DESC LIMIT ?",
                (symbol, as_of, config.history_bars),
            ).fetchall()[::-1]
            history = [Bar(**json.loads(row["payload"])) for row in rows]
            if instrument.watched and (not history or history[-1].day != as_of):
                return None
            histories[symbol] = history
            try:
                request = analysis_request(history, as_of, instrument.tick, config.minimum_bars)
            except (ValueError, TypeError, ArithmeticError):
                return None
            if any(
                not math.isfinite(bar[key])
                for bar in request["bars"]
                for key in ("open", "high", "low", "close")
            ):
                return None
            hashes[symbol] = input_hash(request)
            updated_at = max(updated_at, *(row["fetched_at"] for row in rows)) if rows else updated_at
        # Corrected daily bars refresh this display without overwriting frozen
        # plans or rewriting historical trades. The structure engine caches inputs.
        if plan.get("strategy") != STRATEGY or any(
            row.get("pa", {}).get("input_sha256") != hashes.get(row["symbol"])
            for row in plan["rows"]
            if row["symbol"] in hashes
        ):
            plan = build_plan(list(universe.values()), histories, set(), as_of, next_day, config)
    rows = []
    for source in plan["rows"]:
        instrument = universe.get(source["symbol"])
        if not instrument:
            continue
        row = {**source, "selected": False, "eligible": False, "target_weight": "0", "rank": None}
        pa = row.get("pa", {})
        candidate = bool(source.get("eligible"))
        if config.strategy_model == "price_action":
            decision = (
                price_decision(pa, pa["entry"], config.pa_min_rr, config.pa_rr_enabled)
                if (pa.get("ready") and pa.get("entry") and pa.get("entry_stop", 0) > 0)
                else None
            )
            candidate = bool(decision and decision["action"] == "buy")
            if decision and not candidate:
                row["reasons"] = [
                    reason for reason in source["reasons"] if reason != "等待有效盘中行情触发已完成日 K 结构"
                ] + [decision["reason"]]
        row["post_close_candidate"] = bool(candidate and instrument.tradable and source["representative"])
        if row["post_close_candidate"]:
            row["reasons"] = [
                f"已形成{pa.get('setup') or '策略候选'}；{next_day} 等待有效盘中报价触发并确认。"
            ]
        rows.append(row)
    focus = focus_snapshot(conn, as_of)
    digest = hashlib.sha256(
        json.dumps(
            {
                "config_id": config_id,
                "strategy": plan["strategy"],
                "inputs": hashes or plan.get("input_sha256"),
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()
    return {
        **plan,
        "id": f"post-close:{record['id']}:{digest[:16]}",
        "input_sha256": digest,
        "config_id": config_id,
        "created_at": updated_at,
        "as_of": as_of,
        "execute_day": next_day,
        "rows": rows,
        "targets": {},
        "mode": "post_close",
        "preview_only": True,
        "session_snapshot": False,
        "stale": False,
        "update_state": "post_close",
        "execution_mode": config.execution_mode,
        "strategy_model": config.strategy_model,
        "candidate_count": sum(row["post_close_candidate"] for row in rows),
        "structure_count": sum(
            bool(row["representative"] and row.get("pa", {}).get("ready")) for row in rows
        ),
        "representative_count": sum(bool(row["representative"]) for row in rows),
        "focus": {k: v for k, v in focus.items() if k != "items"},
        "message": f"盘后参考：依据 {as_of} 完整日 K，供 {next_day} 观察；开盘后按实时价格重新确认买卖。",
        "execution_message": "盘后候选仅供观察，开盘后自动切回盘中信号。",
    }
