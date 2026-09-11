"""Describe continuous operation separately from permission to trade right now."""

from app.core.types import dt
from app.storage.db import get_state
from app.trading.account import snapshot


def automation_status(conn, calendar, now, readiness):
    account = snapshot(conn, now)
    heartbeat = get_state(conn, "worker_heartbeat", {}).get("at")
    alive = bool(heartbeat and 0 <= (now - dt(heartbeat)).total_seconds() <= 30)
    if not alive:
        state, label = "offline", "后台连接中断"
        message = "自动运行已开启，但后台心跳中断；后台恢复后将自动继续。"
    elif not calendar.known(now.date()):
        state, label = "waiting_data", "自动运行 · 等待日历"
        message = "交易日历超出已核验范围；日历更新后自动继续。"
    elif not calendar.session(now):
        state, label = "waiting_session", "自动运行 · 休市等待"
        message = "自动运行已开启；休市期间持续维护数据，开市并通过数据与风控检查后自动执行。"
    elif not readiness["ready"]:
        state, label = "waiting_data", "自动运行 · 等待数据"
        message = "数据就绪后自动继续：" + "；".join(readiness["reasons"])
    elif account["stale"]:
        state, label = "waiting_valuation", "自动运行 · 等待估值"
        message = "持仓行情或核算暂未就绪；条件恢复后自动继续。"
    else:
        state, label = "running", "自动运行中"
        message = "自动运行已开启，后台持续检查策略信号、持仓风险与成交条件。"
    return {
        "enabled": True,
        "state": state,
        "label": label,
        "message": message,
    }
