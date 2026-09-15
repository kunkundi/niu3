"""Durable intraday paper intents and sell-first inventory round trips."""

import hashlib
import json
from datetime import time
from decimal import Decimal

from app.core.types import dec, dt, iso
from app.storage.db import dump, get_state, instruments, latest_quote, set_state, settings
from app.strategies.focus import FOCUS_POLICY
from app.strategies.price_action import STRATEGY
from app.strategies.minute_t import enabled as minute_t_enabled, signal as minute_t_signal, execution_problem
from app.trading.account import reconcile, snapshot

KINDS = "('intraday','t_sell','t_buy')"
OPEN = "('pending','partial')"


def live_problem(plan, config_id, config, calendar, now):
    if plan and (plan.get("preview_only") or plan.get("mode") == "post_close"):
        return "盘后参考等待开盘后的实时信号确认"
    if not plan or plan.get("config_id") != config_id or plan.get("focus_policy") != FOCUS_POLICY:
        return "等待当前参数的盘中目标"
    if config.strategy_model == "price_action" and plan.get("strategy") != STRATEGY:
        return "等待裸 K 策略目标"
    if not plan.get("created_at") or not plan.get("id"):
        return "盘中目标尚未生成"
    age = (now - dt(plan["created_at"])).total_seconds()
    if not 0 <= age <= config.market_interval + 30 or dt(plan["created_at"]).date() != now.date():
        return "盘中目标已过期，等待自动重算"
    try:
        expected = calendar.previous(now.date())
    except ValueError:
        return "交易日历未就绪"
    if plan.get("as_of") != expected:
        return "盘中目标的日 K 基准已过期"
    missing = plan.get("missing_quotes", -1)
    # Price-action conditions are local to each instrument. A missing peer quote
    # cannot disable protection of a holding whose own structure and quote are valid.
    if missing < 0 or (missing and config.strategy_model != "price_action"):
        return "候选行情未齐，等待完整排名"
    return ""


def exit_evaluable(row):
    # Missing history or a pending group is not an instruction to liquidate a holding.
    return bool(
        row
        and row.get("evaluated_quote_at")
        and (row.get("score") is not None or row.get("pa", {}).get("ready"))
        and row.get("focus_status") != "pending"
    )


def target_quantity(plan, account, symbol, quote, instrument, config):
    weight = min(dec(plan["targets"].get(symbol, 0)), config.max_weight)
    return (
        int(Decimal(account["nav_units"]) * weight / quote.last / instrument.lot_size) * instrument.lot_size
    )


def confirm_price_action(plan, previous, config_id, config, now):
    """Confirm each ETF on distinct fresh observations, independently of peers."""
    result = {}
    for row in plan["rows"]:
        symbol, pa = row["symbol"], row.get("pa", {})
        quote_at = row.get("evaluated_quote_at")
        old = previous.get(symbol, {})
        fingerprint = hashlib.sha256(dump({
            "config": config_id,
            "strategy": STRATEGY,
            "day": now.date().isoformat(),
            "structure": pa.get("input_sha256"),
            "action": pa.get("action"),
            "selected": symbol in plan["targets"],
        }).encode()).hexdigest()
        valid = (
            pa.get("ready") and quote_at
            and 0 <= (now - dt(quote_at)).total_seconds() <= config.quote_max_age
        )
        consecutive = (
            valid and old.get("fingerprint") == fingerprint and old.get("observed_at")
            and old.get("quote_at") and dt(quote_at) >= dt(old["quote_at"])
            and 0 <= (dt(plan["created_at"]) - dt(old["observed_at"])).total_seconds()
            <= config.market_interval + 30
        )
        fresh = bool(quote_at and (not old.get("quote_at") or dt(quote_at) > dt(old["quote_at"])))
        count = old.get("count", 0) + int(fresh) if consecutive else int(bool(valid))
        result[symbol] = {
            "fingerprint": fingerprint,
            "signal_id": plan["id"],
            "quote_at": quote_at,
            "observed_at": old["observed_at"] if consecutive and not fresh else plan["created_at"],
            "count": min(count, config.intraday_confirmations),
        }
    return result


def order_problem(conn, order, instrument, quote, config, calendar, now):
    config_id, _ = settings(conn)
    if config.execution_mode != "intraday" or order["config_id"] != config_id:
        return "盘中执行方式或参数已变更"
    if (
        dt(order["created_at"]).date() != now.date()
        or (now - dt(order["created_at"])).total_seconds() >= config.intraday_order_ttl
    ):
        return "盘中订单已过期"
    plan = get_state(conn, "live_targets", {})
    problem = live_problem(plan, config_id, config, calendar, now)
    if problem:
        return problem
    if not get_state(conn, "buy_ready", False):
        return "数据未就绪，盘中订单等待"
    confirmation = get_state(conn, "intraday_confirmation", {})
    if config.strategy_model == "price_action":
        confirmation = confirmation.get("symbols", {}).get(instrument.symbol, {})
    if (
        confirmation.get("signal_id") != plan["id"]
        or confirmation.get("count", 0) < config.intraday_confirmations
    ):
        return "实时目标等待连续确认"
    row = next((r for r in plan["rows"] if r["symbol"] == instrument.symbol), None)
    if instrument.symbol not in plan["targets"] and not exit_evaluable(row):
        return "持仓筛选数据待齐，暂不退出"
    if config.strategy_model == "price_action":
        from dataclasses import replace
        from app.strategies.price_action import trigger_problem
        from app.trading.engine import fill_price

        evidence = conn.execute(
            "SELECT payload FROM intraday_decisions WHERE order_id=?", (order["id"],)
        ).fetchone()
        decision = json.loads(evidence[0]) if evidence else {}
        frozen = decision.get("pa", {})
        pa = (row or {}).get("pa", {})
        if frozen.get("input_sha256") != pa.get("input_sha256"):
            return "裸 K 结构已更新，等待新决策"
        executable = replace(
            quote,
            ask=fill_price(quote, "BUY", instrument.tick),
            bid=fill_price(quote, "SELL", instrument.tick),
        )
        problem = (
            execution_problem(conn, decision.get("minute_t"), instrument, quote, config, now, order["kind"])
            if minute_t_enabled(config) and order["kind"] in {"t_sell", "t_buy"}
            else trigger_problem(pa, executable, config, order["kind"], order["side"])
        )
        if problem:
            return problem
    if order["kind"] in {"t_sell", "t_buy"}:
        if not config.intraday_t_enabled or instrument.symbol not in plan["targets"]:
            return "做 T 条件已失效"
        if order["kind"] == "t_sell":
            evidence = conn.execute(
                "SELECT payload FROM intraday_decisions WHERE order_id=?", (order["id"],)
            ).fetchone()
            if config.strategy_model != "price_action" and (
                not evidence
                or quote.bid
                < dec(json.loads(evidence[0])["t_anchor_price"]) * (1 + config.intraday_t_trigger)
            ):
                return "价格已回落，等待做 T 卖出门槛"
        else:
            cycle = conn.execute("SELECT * FROM t_cycles WHERE buy_order_id=?", (order["id"],)).fetchone()
            if not cycle or cycle["status"] != "buying":
                return "做 T 买回轮次已结束"
            sale = conn.execute(
                "SELECT SUM(quantity) quantity,SUM(gross) gross,SUM(fee) fee FROM fills WHERE order_id=?",
                (cycle["sell_order_id"],),
            ).fetchone()
            if not sale["quantity"] or (
                config.strategy_model != "price_action"
                and quote.ask > Decimal(sale["gross"]) / sale["quantity"] * (1 - config.intraday_t_trigger)
            ):
                return "价格已反弹，等待做 T 买回门槛"
            from app.trading.engine import fee_for, fill_price

            cost = order["quantity"] * fill_price(quote, "BUY", instrument.tick)
            proceeds = Decimal(sale["gross"] - sale["fee"]) * order["quantity"] / sale["quantity"]
            if cost + fee_for(cost, config) >= proceeds:
                return "当前价差不足以覆盖模拟费用"
    return ""


class IntradayTrader:
    def __init__(self, engine):
        self.engine, self.db, self.calendar = engine, engine.db, engine.calendar

    def _cancel(self, conn, order_id, now, reason):
        conn.execute(
            f"UPDATE orders SET status='cancelled',updated_at=?,blocked_reason=? WHERE id=? AND status IN {OPEN}",
            (iso(now), reason, order_id),
        )

    def _submit(
        self, conn, plan, account, instrument, quote, side, quantity, kind, reason, now, config_id, config,
        minute_t=None,
    ):
        key = f"{kind}:{plan['id']}:{instrument.symbol}:{side}"
        pa = next((r.get("pa", {}) for r in plan["rows"] if r["symbol"] == instrument.symbol), {})
        if config.strategy_model == "price_action" and kind == "intraday":
            # One intent per completed setup/day; refreshed quotes cannot pyramid the same setup.
            key = f"{kind}:pa:{config_id}:{now.date()}:{pa.get('signal_day') if side == 'BUY' else pa.get('exit_day')}:{instrument.symbol}:{side}"
        if minute_t:
            # One intent per closed candle and side, including after restart/expiry.
            key = f"{kind}:minute5:{config_id}:{instrument.symbol}:{minute_t['bar_at']}"
        if conn.execute("SELECT 1 FROM orders WHERE key=?", (key,)).fetchone():
            return None
        self.engine._order(conn, key, instrument.symbol, side, quantity, reason, kind, now, config_id)
        order_id = conn.execute("SELECT id FROM orders WHERE key=?", (key,)).fetchone()[0]
        conn.execute(
            "INSERT INTO intraday_decisions VALUES(?,?,?,?)",
            (
                order_id,
                iso(now),
                config_id,
                dump(
                    {
                        "signal": plan,
                        "config": config.model_dump(mode="json"),
                        "account": account,
                        "instrument": instrument.to_dict(),
                        "quote": quote.to_dict(),
                        "reason": reason,
                        "pa": pa,
                        "minute_t": minute_t,
                        "t_anchor_price": get_state(conn, f"t_anchor:{instrument.symbol}", {}).get(
                            "price", quote.previous_close
                        )
                        if get_state(conn, f"t_anchor:{instrument.symbol}", {}).get("day")
                        == now.date().isoformat()
                        else quote.previous_close,
                    }
                ),
            ),
        )
        conn.execute(
            "INSERT INTO runs(task,at,status,detail) VALUES('intraday',?,'ok',?)",
            (iso(now), f"{instrument.symbol} {side} {quantity} 份：{reason}"),
        )
        return order_id

    def _limit(self, conn, symbol, config, now):
        rows = conn.execute(
            f"SELECT created_at,updated_at FROM orders WHERE symbol=? AND kind IN {KINDS} AND substr(created_at,1,10)=? ORDER BY id DESC",
            (symbol, now.date().isoformat()),
        ).fetchall()
        if len(rows) >= config.intraday_max_orders:
            return "达到本日订单上限"
        if rows and (now - dt(rows[0]["updated_at"])).total_seconds() < config.intraday_min_interval:
            return "等待最短操作间隔"
        return ""

    def _cycle(self, conn, cycle, plan, account, instrument, quote, current, desired, now, config_id, config):
        sell = conn.execute("SELECT * FROM orders WHERE id=?", (cycle["sell_order_id"],)).fetchone()
        buy = (
            conn.execute("SELECT * FROM orders WHERE id=?", (cycle["buy_order_id"],)).fetchone()
            if cycle["buy_order_id"]
            else None
        )
        bought = conn.execute(
            "SELECT COALESCE(SUM(o.filled),0) FROM t_cycle_buys b JOIN orders o ON o.id=b.order_id WHERE b.cycle_id=?",
            (cycle["id"],),
        ).fetchone()[0]
        status = cycle["status"]
        message = ""
        if (
            cycle["day"] != now.date().isoformat()
            or cycle["config_id"] != config_id
            or not config.intraday_t_enabled
            or instrument.symbol not in plan["targets"]
        ):
            status = "abandoned"
            self._cancel(conn, sell["id"], now, "做 T 条件已失效")
            if cycle["buy_order_id"]:
                self._cancel(conn, cycle["buy_order_id"], now, "做 T 条件已失效")
        elif sell["status"] in {"pending", "partial"}:
            return "做 T 卖单等待成交"
        elif not sell["filled"]:
            status = "abandoned"
        elif buy and buy["status"] in {"pending", "partial"}:
            return "做 T 买回单等待成交"
        elif bought >= sell["filled"]:
            status = "complete"
            set_state(conn, f"t_anchor:{instrument.symbol}", {"day": cycle["day"], "price": quote.last})
        else:
            status = "waiting_buy"
            totals = conn.execute(
                "SELECT SUM(gross) gross,SUM(fee) fee FROM fills WHERE order_id=?", (sell["id"],)
            ).fetchone()
            sale_price = Decimal(totals["gross"]) / sell["filled"]
            quantity = (
                min(sell["filled"] - bought, max(0, desired - current))
                // instrument.lot_size
                * instrument.lot_size
            )
            minute_t = (
                minute_t_signal(conn, plan, instrument, quote, config, now, "t_buy", cycle)
                if minute_t_enabled(config) else None
            )
            trigger = minute_t["ready"] if minute_t else self._t_trigger(
                plan, instrument.symbol, quote, config, "t_buy", sale_price
            )
            limit = self._limit(conn, instrument.symbol, config, now)
            if minute_t:
                message = minute_t["message"]
            if quantity <= 0:
                status = "abandoned"
                message = "本轮买回数量受目标仓位限制，轮次结束"
            elif minute_t and minute_t.get("invalidated"):
                status = "abandoned"
            elif trigger and not limit:
                from app.trading.engine import fee_for, fill_price

                cost = quantity * fill_price(quote, "BUY", instrument.tick)
                proceeds = Decimal(totals["gross"] - totals["fee"]) * quantity / sell["filled"]
                if cost + fee_for(cost, config) < proceeds:
                    order_id = self._submit(
                        conn,
                        plan,
                        account,
                        instrument,
                        quote,
                        "BUY",
                        quantity,
                        "t_buy",
                        "5 分钟做 T：冻结支撑附近企稳，买回实际已卖份额"
                        if minute_t else "裸 K 做 T：回到支撑且未跌破失效位，买回已卖份额"
                        if config.strategy_model == "price_action"
                        else "底仓做 T：价格回落，买回已卖份额",
                        now,
                        config_id,
                        config,
                        minute_t=minute_t,
                    )
                    if order_id:
                        conn.execute("INSERT INTO t_cycle_buys VALUES(?,?)", (cycle["id"], order_id))
                        conn.execute("UPDATE t_cycles SET buy_order_id=? WHERE id=?", (order_id, cycle["id"]))
                        status = "buying"
                        message = "已提交 5 分钟做 T 买回单" if minute_t else ""
                    elif minute_t:
                        message = "该 5 分钟买回形态已有执行记录，等待新形态"
                else:
                    message = "当前买回价差不足以覆盖模拟费用"
            elif limit:
                message = limit
        conn.execute("UPDATE t_cycles SET status=?,updated_at=? WHERE id=?", (status, iso(now), cycle["id"]))
        return message or {
            "selling": "做 T 卖单等待成交",
            "waiting_buy": "已卖出底仓，等待价格回落买回",
            "buying": "做 T 买回单等待成交",
            "complete": "本轮做 T 已完成",
            "abandoned": "本轮做 T 已结束，后续按实时目标评估",
        }[status]

    def _t_trigger(self, plan, symbol, quote, config, kind, anchor):
        if config.strategy_model == "price_action":
            from app.strategies.price_action import trigger_problem

            pa = next((r.get("pa") for r in plan["rows"] if r["symbol"] == symbol), None)
            return not trigger_problem(pa, quote, config, kind, "SELL" if kind == "t_sell" else "BUY")
        return (
            anchor > 0 and quote.bid >= Decimal(anchor) * (1 + config.intraday_t_trigger)
            if kind == "t_sell"
            else quote.ask <= anchor * (1 - config.intraday_t_trigger)
        )

    def tick(self, now):
        with self.db.transaction() as conn:
            config_id, config = settings(conn)
            if config.execution_mode != "intraday":
                return
            plan = get_state(conn, "live_targets", {})
            reason = (
                "非交易时段，等待自动执行"
                if not self.calendar.session(now)
                else live_problem(plan, config_id, config, self.calendar, now)
            )
            if not reason and (not get_state(conn, "buy_ready", False) or reconcile(conn)):
                reason = "数据或账本未就绪；风险监测继续运行"
            if reason:
                # Matching independently checks the same conditions; transient outages wait
                # for recovery without consuming the daily order budget by cancelling/recreating.
                set_state(
                    conn,
                    "intraday_execution",
                    {"at": iso(now), "state": "waiting", "message": reason, "items": []},
                )
                return
            account = snapshot(conn, now)
            if account["stale"]:
                set_state(
                    conn,
                    "intraday_execution",
                    {"at": iso(now), "state": "waiting", "message": "持仓估值未就绪，更新后自动继续", "items": []},
                )
                return
            fingerprint = hashlib.sha256(
                dump(
                    {
                        "config": config_id,
                        "day": now.date().isoformat(),
                        "targets": plan["targets"],
                        "structures": [
                            (r["symbol"], r.get("pa", {}).get("input_sha256"), r.get("pa", {}).get("action"))
                            for r in plan["rows"]
                            if r.get("pa")
                        ]
                        if config.strategy_model == "price_action"
                        else [],
                    }
                ).encode()
            ).hexdigest()
            confirmation = get_state(conn, "intraday_confirmation", {})
            previous_symbols = confirmation.get("symbols", {})
            if confirmation.get("fingerprint") != fingerprint:
                confirmation = {"fingerprint": fingerprint, "signal_id": plan["id"], "count": 1}
            elif confirmation.get("signal_id") != plan["id"]:
                confirmation["signal_id"] = plan["id"]
                confirmation["count"] += 1
            if config.strategy_model == "price_action":
                confirmation["symbols"] = confirm_price_action(
                    plan, previous_symbols, config_id, config, now
                )
                confirmation["count"] = min(
                    (v["count"] for s, v in confirmation["symbols"].items() if s in plan["targets"]),
                    default=0,
                )
            set_state(conn, "intraday_confirmation", confirmation)
            universe = instruments(conn)
            holdings = {p["symbol"]: p for p in account["positions"]}
            rows = {r["symbol"]: r for r in plan["rows"]}
            active_cycles = {
                r["symbol"]: r
                for r in conn.execute(
                    "SELECT * FROM t_cycles WHERE status IN ('selling','waiting_buy','buying') ORDER BY id"
                )
            }
            pending_symbols = {
                r[0]
                for r in conn.execute(
                    f"SELECT DISTINCT symbol FROM orders WHERE kind IN {KINDS} AND status IN {OPEN}"
                )
            }
            symbols = sorted(set(holdings) | set(plan["targets"]) | set(active_cycles) | pending_symbols)
            items = []
            for symbol in symbols:
                instrument = universe.get(symbol)
                latest = latest_quote(conn, symbol)
                if not instrument or not instrument.tradable or not latest:
                    continue
                quote = latest[1]
                position = holdings.get(symbol, {})
                current, available = position.get("quantity", 0), position.get("available", 0)
                if quote.last <= 0:
                    continue
                desired = target_quantity(plan, account, symbol, quote, instrument, config)
                delta = desired - current
                row = rows.get(symbol)
                pa = (row or {}).get("pa", {})
                symbol_confirmation = (
                    confirmation["symbols"].get(symbol, {"count": 0})
                    if config.strategy_model == "price_action" else confirmation
                )
                confirmed = symbol_confirmation["count"] >= config.intraday_confirmations
                if config.strategy_model == "price_action" and current and symbol not in active_cycles:
                    desired = 0 if pa.get("action") == "exit" else current
                    delta = desired - current
                risk = conn.execute(
                    "SELECT 1 FROM risk_intents WHERE symbol=? UNION SELECT 1 FROM cooldown WHERE symbol=? AND day=?",
                    (symbol, symbol, now.date().isoformat()),
                ).fetchone()
                symbol_reason = (
                    "风险退出或当日止损冷却"
                    if risk
                    else "行情或盘口未就绪"
                    if not quote.fresh(now, config.quote_max_age)
                    or quote.status != "trading"
                    or min(quote.bid, quote.ask, quote.upper, quote.lower) <= 0
                    or quote.bid > quote.ask
                    else "持仓筛选数据待齐，暂不退出"
                    if symbol not in plan["targets"] and not exit_evaluable(row)
                    else ""
                )
                pending = conn.execute(
                    f"SELECT * FROM orders WHERE symbol=? AND kind IN {KINDS} AND status IN {OPEN}", (symbol,)
                ).fetchall()
                for order in pending:
                    intent_changed = (
                        (pa.get("action") != ("buy" if order["side"] == "BUY" else "exit"))
                        if config.strategy_model == "price_action"
                        else (
                            (order["side"] == "BUY" and delta <= 0)
                            or (order["side"] == "SELL" and delta >= 0)
                        )
                    )
                    obsolete = (
                        risk
                        or (order["kind"] == "intraday" and intent_changed)
                        or (order["kind"] in {"t_sell", "t_buy"} and symbol not in plan["targets"])
                    )
                    if obsolete:
                        self._cancel(conn, order["id"], now, symbol_reason or "实时目标已改变，取消剩余订单")
                if risk and symbol in active_cycles:
                    conn.execute(
                        "UPDATE t_cycles SET status='abandoned',updated_at=? WHERE id=?",
                        (iso(now), active_cycles[symbol]["id"]),
                    )
                if symbol_reason:
                    items.append({"symbol": symbol, "message": symbol_reason})
                    continue
                if not confirmed:
                    items.append(
                        {
                            "symbol": symbol,
                            "message": f"等待目标确认 {symbol_confirmation['count']}/{config.intraday_confirmations}",
                        }
                    )
                    continue
                if symbol in active_cycles:
                    message = self._cycle(
                        conn,
                        active_cycles[symbol],
                        plan,
                        account,
                        instrument,
                        quote,
                        current,
                        desired,
                        now,
                        config_id,
                        config,
                    )
                    items.append({"symbol": symbol, "message": message})
                    continue
                if conn.execute(
                    f"SELECT 1 FROM orders WHERE symbol=? AND status IN {OPEN}", (symbol,)
                ).fetchone():
                    items.append({"symbol": symbol, "message": "已有订单，等待成交"})
                    continue
                limit = self._limit(conn, symbol, config, now)
                if limit:
                    items.append({"symbol": symbol, "message": limit})
                    continue
                # Entries/exits act on confirmed membership; weight-only moves use a deadband.
                needs_rebalance = delta and (
                    current == 0
                    or desired == 0
                    or Decimal(abs(delta) * quote.last)
                    >= Decimal(account["nav_units"]) * config.intraday_drift
                )
                if config.strategy_model == "price_action":
                    needs_rebalance = delta and (
                        (current == 0 and pa.get("action") == "buy")
                        or (current > 0 and pa.get("action") == "exit")
                    )
                if needs_rebalance:
                    side = "BUY" if delta > 0 else "SELL"
                    quantity = abs(delta) if side == "BUY" else min(abs(delta), available)
                    if not (side == "SELL" and desired == 0 and quantity == current):
                        quantity = quantity // instrument.lot_size * instrument.lot_size
                    if quantity:
                        order_id = self._submit(
                            conn,
                            plan,
                            account,
                            instrument,
                            quote,
                            side,
                            quantity,
                            "intraday",
                            (
                                "；".join(row["reasons"])
                                if config.strategy_model == "price_action"
                                else "实时目标确认：盘中建仓／调仓"
                                if desired
                                else "实时目标确认：趋势或排名退出"
                            ),
                            now,
                            config_id,
                            config,
                        )
                        items.append(
                            {
                                "symbol": symbol,
                                "message": f"已提交盘中{'买入' if side == 'BUY' else '卖出'} {quantity} 份"
                                if order_id
                                else "当前形态已有执行记录，等待新形态",
                            }
                        )
                    else:
                        items.append({"symbol": symbol, "message": "T+1 可卖份额不足，后续交易时段自动评估"})
                    continue
                if (
                    not config.intraday_t_enabled
                    or not available
                    or symbol not in plan["targets"]
                    or now.time() >= time(14, 45)
                ):
                    continue
                cycles = conn.execute(
                    "SELECT COUNT(*) FROM t_cycles WHERE symbol=? AND day=?", (symbol, now.date().isoformat())
                ).fetchone()[0]
                # Reserve a daily order slot for the eventual buyback.
                count = conn.execute(
                    f"SELECT COUNT(*) FROM orders WHERE symbol=? AND kind IN {KINDS} AND substr(created_at,1,10)=?",
                    (symbol, now.date().isoformat()),
                ).fetchone()[0]
                if cycles >= config.intraday_t_cycles or count + 2 > config.intraday_max_orders:
                    items.append({"symbol": symbol, "message": "达到本日做 T 轮数或订单预算上限"})
                    continue
                anchor = get_state(conn, f"t_anchor:{symbol}", {})
                anchor_price = (
                    anchor.get("price", 0)
                    if anchor.get("day") == now.date().isoformat()
                    else quote.previous_close
                )
                quantity = (
                    min(available, int(Decimal(current) * config.intraday_t_fraction))
                    // instrument.lot_size
                    * instrument.lot_size
                )
                minute_t = (
                    minute_t_signal(conn, plan, instrument, quote, config, now, "t_sell")
                    if minute_t_enabled(config) else None
                )
                trigger = minute_t["ready"] if minute_t else self._t_trigger(
                    plan, symbol, quote, config, "t_sell", anchor_price
                )
                if minute_t and not trigger:
                    items.append({"symbol": symbol, "message": minute_t["message"]})
                if trigger and quantity:
                    if minute_t:
                        from app.trading.engine import fee_for, fill_price

                        proceeds = quantity * fill_price(quote, "SELL", instrument.tick)
                        cost = quantity * (minute_t["support"] + 2 * minute_t["tolerance"])
                        if proceeds - fee_for(proceeds, config) <= cost + fee_for(cost, config):
                            items.append({"symbol": symbol, "message": "分钟波段价差不足以覆盖模拟费用"})
                            continue
                    order_id = self._submit(
                        conn,
                        plan,
                        account,
                        instrument,
                        quote,
                        "SELL",
                        quantity,
                        "t_sell",
                        "5 分钟做 T：盘中压力附近转弱，卖出部分可卖底仓"
                        if minute_t else "裸 K 做 T：触及已确认压力位，卖出部分可卖底仓"
                        if config.strategy_model == "price_action"
                        else "底仓做 T：上涨达到门槛，卖出部分可卖份额",
                        now,
                        config_id,
                        config,
                        minute_t=minute_t,
                    )
                    if order_id:
                        conn.execute(
                            "INSERT INTO t_cycles(day,symbol,config_id,sell_order_id,status,at,updated_at) VALUES(?,?,?,?,'selling',?,?)",
                            (now.date().isoformat(), symbol, config_id, order_id, iso(now), iso(now)),
                        )
                        items.append({"symbol": symbol, "message": f"已提交做 T 卖出 {quantity} 份"})
                    elif minute_t:
                        items.append({"symbol": symbol, "message": "该 5 分钟卖出形态已有执行记录，等待新形态"})
            set_state(
                conn,
                "intraday_execution",
                {
                    "at": iso(now),
                    "state": "running",
                    "message": "裸 K 自动交易运行中：日线管理底仓，5 分钟 K 确认做 T"
                    if minute_t_enabled(config) and config.intraday_t_enabled
                    else "裸 K 自动交易运行中：完整日 K 定位，盘中触发买卖与做 T"
                    if config.strategy_model == "price_action"
                    else "盘中自动交易运行中，按实时目标与底仓做 T 规则执行",
                    "confirmations": min(confirmation["count"], config.intraday_confirmations),
                    "items": items,
                },
            )
