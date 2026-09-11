import json

from app.core.types import Bar
from app.storage.db import tracked_instruments
from app.strategies.focus import FOCUS_POLICY, select_focus


def focus_snapshot(conn, as_of: str | None) -> dict:
    universe = list(tracked_instruments(conn).values())
    histories = {}
    symbols = [i.symbol for i in universe]
    for row in conn.execute(
        "SELECT symbol,payload FROM (SELECT symbol,payload,ROW_NUMBER() OVER "
        f"(PARTITION BY symbol ORDER BY day DESC) n FROM bars WHERE symbol IN ({','.join('?' for _ in symbols)}) AND adjustment='qfq' AND day<=?) "
        "WHERE n<=20 ORDER BY symbol,n DESC",
        (*symbols, as_of or ""),
    ):
        histories.setdefault(row["symbol"], []).append(Bar(**json.loads(row["payload"])))
    rows = select_focus(universe, histories, as_of or "")
    active = [rows[i.symbol] for i in universe if i.active]
    candidates = [r for r in active if r["candidate"]]
    return {
        "policy": FOCUS_POLICY,
        "as_of": as_of,
        "mode": "manual",
        "scope_description": "手动添加的 ETF",
        "candidate_count": sum(i.watched for i in universe),
        "scope_excluded_count": sum(r["focus_status"] == "out_of_scope" for r in active),
        "items": rows,
        "representative_count": sum(i.watched for i in universe),
        "group_count": len({r["focus_group"] for r in candidates}),
        "pending_group_count": len({r["focus_group"] for r in candidates if r["focus_status"] == "pending"}),
        "low_liquidity_group_count": sum(
            r["liquidity_rank"] == 1 and r["focus_status"] == "low_liquidity" for r in candidates
        ),
    }
