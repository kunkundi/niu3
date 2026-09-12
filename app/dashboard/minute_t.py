"""Read-only status for minute T signals, frozen cycle levels and execution waits."""

from datetime import datetime, time

from app.core.types import TZ, dt, iso, yuan
from app.market_data.intraday import expected_day, snapshot_key
from app.storage.db import get_state, instruments, latest_quote, sold_today
from app.strategies.minute_t import enabled, context, signal, sell_reference


def display_context(conn, symbol, calendar, now, tick):
    """Keep the last observed structure at rest; execution still uses the live clock."""
    phase = snapshot_key(calendar, now)
    if phase:
        source = get_state(conn, f"minute5:{symbol}", {})
        try:
            bar_at, fetched_at = dt(source["as_of"]), dt(source["fetched_at"])
            if (
                bar_at.date() == dt(phase).date()
                and bar_at <= dt(phase)
                and bar_at <= fetched_at <= now
            ):
                return context(conn, symbol, fetched_at, tick)
        except (KeyError, ValueError, TypeError):
            pass
    return context(conn, symbol, now, tick)


def minute_t_payload(conn, calendar, now, config):
    now = now.astimezone(TZ)
    day = expected_day(calendar, now)
    display_at = (
        min(now, datetime.combine(datetime.fromisoformat(day).date(), time(23, 59, 59), TZ))
        if day else now
    )
    mode = enabled(config)
    active = mode and config.intraday_t_enabled and config.execution_mode == "intraday"
    plan = get_state(conn, "live_targets", {})
    execution = get_state(conn, "intraday_execution", {})
    heartbeat = get_state(conn, "worker_heartbeat", {})
    alive = bool(heartbeat.get("at") and 0 <= (now - dt(heartbeat["at"])).total_seconds() <= 30)
    running = calendar.session(now)
    universe = instruments(conn)
    positions = {
        row["symbol"]: dict(row)
        for row in conn.execute(
            "SELECT symbol,SUM(quantity) quantity,SUM(CASE WHEN available_day<=? THEN quantity ELSE 0 END) available "
            "FROM lots WHERE quantity>0 GROUP BY symbol",
            (display_at.date().isoformat(),),
        )
    }
    for symbol in sold_today(conn, display_at) if day else ():
        positions.setdefault(symbol, {"symbol": symbol, "quantity": 0, "available": 0})
    items = []
    for symbol in sorted(positions):
        position = positions[symbol]
        symbol = position["symbol"]
        instrument = universe.get(symbol)
        if not instrument:
            continue
        cycle = conn.execute(
            "SELECT * FROM t_cycles WHERE symbol=? AND day=? ORDER BY id DESC LIMIT 1",
            (symbol, day),
        ).fetchone()
        live_cycle = cycle if cycle and cycle["status"] in {"selling", "waiting_buy", "buying"} else None
        observation_only = position["quantity"] == 0 and not live_cycle
        latest = latest_quote(conn, symbol)
        quote = latest[1] if latest else None
        observation = display_context(conn, symbol, calendar, now, instrument.tick)
        if active and running and quote and not observation_only:
            observation = signal(
                conn, plan, instrument, quote, config, now, "t_buy" if live_cycle else "t_sell", live_cycle
            )
        reference = sell_reference(conn, live_cycle) if live_cycle else None
        levels = reference or observation
        message = observation["message"]
        if observation_only and observation["ready"]:
            message = "今日已清仓，继续观察分钟支撑压力；买回需满足交易条件"
        elif observation["ready"]:
            message += "；等待执行复核"
        if not active:
            message = "当前使用日线做 T" if config.intraday_t_enabled else "做 T 已关闭"
        elif not running:
            message = (
                f"休市，展示 {day} 最近已采集的分钟结构；开盘后自动更新"
                if day and observation.get("support") else "非交易时段，等待下一交易时段"
            )
        elif not alive:
            message = "后台心跳中断，等待服务恢复"
        elif not observation_only and not get_state(conn, "buy_ready", False):
            message = "交易数据或账本未就绪，等待恢复"
        elif (
            not observation_only
            and execution.get("at")
            and 0 <= (now - dt(execution["at"])).total_seconds() <= 30
        ):
            item = next((x for x in execution.get("items", []) if x["symbol"] == symbol), None)
            if item:
                message = item["message"]
            elif execution.get("state") == "waiting":
                message = execution["message"]
        if active and running and alive and not live_cycle and position["quantity"] > 0:
            if not position["available"]:
                message = "T+1 可卖份额不足，等待解锁"
            elif now.time() >= time(14, 45):
                message = "14:45 后不开新轮次，已有买回继续检查"
        source = get_state(conn, f"minute5:{symbol}", {})
        pending = conn.execute(
            "SELECT blocked_reason FROM orders WHERE symbol=? AND kind IN ('t_sell','t_buy') "
            "AND status IN ('pending','partial') ORDER BY id DESC LIMIT 1",
            (symbol,),
        ).fetchone()
        error = get_state(conn, f"error:minute5:{symbol}", {})
        items.append(
            {
                **dict(position),
                "name": instrument.name,
                "observation_only": observation_only,
                "message": message,
                "bar_at": source.get("as_of"),
                "source": source.get("source"),
                "fetched_at": source.get("fetched_at"),
                "data_error": error.get("message"),
                "support": yuan(levels["support"]) if levels.get("support") else None,
                "resistance": yuan(levels["resistance"]) if levels.get("resistance") else None,
                "support_stop": yuan(levels["support_stop"]) if levels.get("support_stop") else None,
                "frozen": bool(reference),
                "cycle_state": cycle["status"] if cycle else None,
                "blocked_reason": pending["blocked_reason"] if pending else "",
            }
        )
    return {
        "enabled": active,
        "model": config.intraday_t_model,
        "strategy_model": config.strategy_model,
        "t_enabled": config.intraday_t_enabled,
        "timeframe": "5m",
        "running": running,
        "worker_alive": alive,
        "at": iso(now),
        "day": day,
        "session_snapshot": bool(day and not running),
        "items": items,
    }
