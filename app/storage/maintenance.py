from datetime import date, datetime, time, timedelta

from app.core.calendar import Calendar
from app.core.types import TZ, iso
from app.storage.db import get_state, set_state


def retain_evidence(conn, now: datetime, calendar=None):
    """Bound raw snapshots while keeping every fill's quote, prior sample, and current baselines."""
    now = now.astimezone(TZ)
    today = now.date().isoformat()
    if get_state(conn, "maintenance_day") == today:
        return
    conn.execute("CREATE TEMP TABLE IF NOT EXISTS protected_quotes(id INTEGER PRIMARY KEY)")
    conn.execute("DELETE FROM protected_quotes")
    conn.execute("INSERT OR IGNORE INTO protected_quotes SELECT quote_id FROM fills")
    conn.execute(
        "INSERT OR IGNORE INTO protected_quotes SELECT q2.id FROM fills f JOIN quotes q ON q.id=f.quote_id "
        "JOIN quotes q2 ON q2.id=(SELECT id FROM quotes p WHERE p.symbol=q.symbol "
        "AND (p.at<q.at OR (p.at=q.at AND p.id<q.id)) ORDER BY p.at DESC,p.id DESC LIMIT 1)"
    )
    conn.execute(
        "INSERT OR IGNORE INTO protected_quotes SELECT last_quote_id FROM orders WHERE last_quote_id>0"
    )
    conn.execute(
        "INSERT OR IGNORE INTO protected_quotes SELECT id FROM "
        "(SELECT id,ROW_NUMBER() OVER(PARTITION BY symbol ORDER BY at DESC,id DESC) n FROM quotes) WHERE n<=2"
    )
    cutoff = now - timedelta(days=7)
    calendar = calendar or Calendar()
    try:
        day = (
            today if calendar.is_open(now.date()) and now.time() >= time(9, 30)
            else calendar.previous(now.date())
        )
        # Long holidays must not age the last session's intraday display out of storage.
        cutoff = min(cutoff, datetime.combine(date.fromisoformat(day), time.min, TZ))
    except ValueError:
        pass
    cursor = conn.execute(
        "DELETE FROM quotes WHERE at<? AND id NOT IN (SELECT id FROM protected_quotes)",
        (iso(cutoff),),
    )
    conn.execute("DELETE FROM liquidity WHERE quote_id NOT IN (SELECT id FROM quotes)")
    # Every order freezes its own minute input; retain source bars for recent inspection.
    conn.execute("DELETE FROM minute_bars WHERE at<?", (iso(now - timedelta(days=30)),))
    conn.execute(
        "DELETE FROM runs WHERE at<? AND task NOT IN ('strategy','control')", (iso(now - timedelta(days=30)),)
    )
    set_state(conn, "maintenance_day", today)
    conn.execute(
        "INSERT INTO runs(task,at,status,detail) VALUES('maintenance',?,'ok',?)",
        (iso(now), f"清理 {cursor.rowcount} 条旧行情；成交依据与决策保留"),
    )
