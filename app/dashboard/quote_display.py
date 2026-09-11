"""Session-aware quote labels for display, independent of execution freshness."""

from datetime import timedelta

from app.core.types import TZ, dt
from app.market_data.intraday import expected_day, snapshot_key


def quote_display_state(quote, calendar, now):
    def state(status, label, warning=False):
        return {"status": status, "label": label, "warning": warning}

    now = now.astimezone(TZ)
    target = expected_day(calendar, now)
    if target is None:
        return state("unknown", "日历待核验", True)
    if quote.last <= 0 or quote.previous_close <= 0 or dt(quote.at) > now:
        return state("unknown", "行情待核验", True)
    if quote.status == "suspended":
        return state("unknown", "停牌", True)
    if calendar.session(now):
        if not quote.fresh(now):
            return state("delayed", "已过期 · 待更新", True)
        if quote.status != "trading":
            return state("unknown", "行情待核验", True)
        return state("live", "")

    phase = snapshot_key(calendar, now)
    # Require the expected trading date and an observation near the session end.
    # Keep the same 90-second tolerance as live quotes, frozen at that endpoint.
    stamp = dt(quote.at)
    if stamp.date().isoformat() != target or stamp < dt(phase) - timedelta(seconds=90):
        return state("delayed", "休市 · 行情待更新", True)
    if phase[11:16] == "11:30":
        return state("break", "午间休市")
    return state("closed", "已收盘" if target == now.date().isoformat() else "最近交易日 · 已收盘")
