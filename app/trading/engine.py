from __future__ import annotations

import json
from datetime import datetime, time
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP

from app.core.calendar import Calendar
from app.core.config import Settings
from app.core.types import Quote, dec, dt, iso, units
from app.storage.db import Database, get_state, instruments, latest_quote, set_state, settings
from app.trading.account import reconcile, snapshot
from app.strategies.focus import FOCUS_POLICY

OPEN = "('pending','partial')"


def fee_for(gross: int, config: Settings) -> int:
    value = max(Decimal(gross) / 1_000_000 * config.commission_rate, config.minimum_commission)
    return units(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def fill_price(quote: Quote, side: str, tick: int, config: Settings) -> int:
    raw = Decimal(quote.ask if side == "BUY" else quote.bid)
    raw *= 1 + config.slippage_bps / 10000 * (1 if side == "BUY" else -1)
    rounding = ROUND_CEILING if side == "BUY" else ROUND_FLOOR
    return int((raw / tick).to_integral_value(rounding=rounding)) * tick


class Engine:
    def __init__(self, db: Database, calendar: Calendar):
        self.db, self.calendar = db, calendar

    def _order(self, conn, key, symbol, side, quantity, reason, kind, now, config_id, plan_id=None):
        if quantity <= 0:
            return
        conn.execute(
            "INSERT OR IGNORE INTO orders(key,symbol,side,quantity,reason,kind,created_at,updated_at,"
            "config_id,plan_id) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (key, symbol, side, quantity, reason, kind, iso(now), iso(now), config_id, plan_id),
        )

    def rebalance(self, plan_id: int, now: datetime):
        if not self.calendar.session(now) or not time(9, 35) <= now.time() < time(10):
            return
        with self.db.transaction() as conn:
            row = conn.execute("SELECT * FROM plans WHERE id=?", (plan_id,)).fetchone()
            if not row or row["execute_day"] != now.date().isoformat():
                return
            config_id, config = settings(conn)
            if config.execution_mode != "daily":
                return
            if config_id != row["config_id"]:
                return
            key = f"rebalance:{row['execute_day']}"
            slot = conn.execute("SELECT status FROM slots WHERE key=?", (key,)).fetchone()
            if slot and slot["status"] == "done":
                return
            plan = json.loads(row["payload"])
            if plan.get("focus_policy") != FOCUS_POLICY:
                return
            account = snapshot(conn, now)
            if reconcile(conn):
                return
            universe = instruments(conn)
            holdings = {p["symbol"]: p for p in account["positions"]}
            symbols = sorted(set(holdings) | set(plan["targets"]))
            # Wait for fresh opening marks before freezing quantities; never price from yesterday's bars.
            if any(
                not latest_quote(conn, s) or not latest_quote(conn, s)[1].fresh(now, config.quote_max_age)
                for s in symbols
            ):
                return
            waiting = False
            for symbol in symbols:
                # A retry must not resize an already submitted intent after prices or fills change.
                if conn.execute(
                    "SELECT 1 FROM orders WHERE key IN (?,?)",
                    (f"{key}:{symbol}:BUY", f"{key}:{symbol}:SELL"),
                ).fetchone():
                    continue
                if conn.execute("SELECT 1 FROM risk_intents WHERE symbol=?", (symbol,)).fetchone():
                    continue
                target = dec(plan["targets"].get(symbol, 0))
                instrument = universe.get(symbol)
                if not instrument:
                    continue
                quote = latest_quote(conn, symbol)[1]
                current = holdings.get(symbol, {}).get("quantity", 0)
                desired = (
                    int(Decimal(account["nav_units"]) * target / quote.last / instrument.lot_size)
                    * instrument.lot_size
                )
                delta = desired - current
                if delta == 0:
                    continue
                side = "BUY" if delta > 0 else "SELL"
                if side == "BUY" and not get_state(conn, "buy_ready", False):
                    waiting = True
                    continue
                self._order(
                    conn,
                    f"{key}:{symbol}:{side}",
                    symbol,
                    side,
                    abs(delta),
                    "日频趋势动量调仓" if desired else "趋势失效或排名退出",
                    "rebalance",
                    now,
                    config_id,
                    plan_id,
                )
            conn.execute(
                "INSERT INTO slots VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET "
                "at=excluded.at,status=excluded.status",
                (key, iso(now), "waiting" if waiting else "done"),
            )

    def risk_check(self, now: datetime):
        if not self.calendar.session(now):
            return
        with self.db.transaction() as conn:
            config_id, config = settings(conn)
            account = snapshot(conn, now)
            if not account["stale"] and not reconcile(conn):
                peak = max(account["nav_units"], get_state(conn, "peak_nav", account["nav_units"]))
                set_state(conn, "peak_nav", peak)
            for position in account["positions"]:
                symbol = position["symbol"]
                latest = latest_quote(conn, symbol)
                if (
                    not latest
                    or not latest[1].fresh(now, config.quote_max_age)
                    or latest[1].last <= 0
                    or latest[1].status != "trading"
                    or position["accounting_block"]
                ):
                    continue
                price = latest[1].last
                high = max(price, position["high_units"])
                conn.execute("UPDATE lots SET high=? WHERE symbol=? AND quantity>0", (high, symbol))
                reason = ""
                if config.strategy_model == "price_action":
                    from app.trading.intraday import live_problem

                    plan = get_state(conn, "live_targets", {})
                    reference = get_state(conn, f"pa_position:{symbol}", {})
                    pa = {}
                    if not live_problem(plan, config_id, config, self.calendar, now):
                        pa = next((r.get("pa", {}) for r in plan["rows"] if r["symbol"] == symbol), {})
                        stop = pa.get("raw", {}).get("structural_stop", 0)
                        if pa.get("ready") and stop > reference.get("stop", 0):
                            reference.update(stop=stop, as_of=plan["as_of"], structure=pa)
                            set_state(conn, f"pa_position:{symbol}", reference)
                    if reference.get("stop") and price <= reference["stop"]:
                        reason = "裸 K 结构失效：跌破入场／已确认摆动低点"
                    elif reference.get("target") and price >= reference["target"]:
                        reason = "裸 K 结构止盈：到达入场时确定的压力／测量目标"
                    elif pa.get("raw", {}).get("exit") and price <= pa["raw"]["exit"]:
                        reason = f"裸 K 反转退出：跌破{pa.get('exit_setup', '反向形态')}低点"
                elif Decimal(price * position["quantity"]) <= Decimal(position["risk_cost_units"]) * (
                    1 - config.stop_loss
                ):
                    reason = "成本止损"
                elif Decimal(price) <= Decimal(high) * (1 - config.trailing_stop):
                    reason = "持仓高点回撤止损"
                if reason:
                    conn.execute(
                        "INSERT OR IGNORE INTO risk_intents VALUES(?,?,?)", (symbol, reason, iso(now))
                    )
                intent = conn.execute("SELECT * FROM risk_intents WHERE symbol=?", (symbol,)).fetchone()
                if not intent:
                    continue
                conn.execute(
                    f"UPDATE orders SET status='cancelled',blocked_reason='风险退出优先',updated_at=? "
                    f"WHERE symbol=? AND kind IN ('rebalance','intraday','t_sell','t_buy') AND status IN {OPEN}",
                    (iso(now), symbol),
                )
                conn.execute("INSERT OR IGNORE INTO cooldown VALUES(?,?)", (symbol, now.date().isoformat()))
                existing = conn.execute(
                    f"SELECT 1 FROM orders WHERE symbol=? AND kind='risk' AND status IN {OPEN}", (symbol,)
                ).fetchone()
                if not existing:
                    key = f"risk:{symbol}:{intent['at']}:{now.date()}"
                    self._order(
                        conn,
                        key,
                        symbol,
                        "SELL",
                        position["quantity"],
                        intent["reason"],
                        "risk",
                        now,
                        config_id,
                    )
                    if config.strategy_model == "price_action":
                        from app.storage.db import dump

                        order_id = conn.execute("SELECT id FROM orders WHERE key=?", (key,)).fetchone()[0]
                        conn.execute(
                            "INSERT OR IGNORE INTO intraday_decisions VALUES(?,?,?,?)",
                            (
                                order_id,
                                iso(now),
                                config_id,
                                dump(
                                    {
                                        "pa": pa or reference.get("structure", {}),
                                        "position_reference": reference,
                                        "quote": latest[1].to_dict(),
                                        "config": config.model_dump(mode="json"),
                                        "reason": intent["reason"],
                                    }
                                ),
                            ),
                        )

    def match(self, now: datetime):
        if not self.calendar.session(now):
            return
        with self.db.transaction() as conn:
            if reconcile(conn):
                return
            _, current_config = settings(conn)
            universe = instruments(conn)
            orders = conn.execute(
                f"SELECT * FROM orders WHERE status IN {OPEN} ORDER BY "
                "CASE side WHEN 'SELL' THEN 0 ELSE 1 END,id"
            ).fetchall()
            for order in orders:
                instrument = universe.get(order["symbol"])
                if not instrument:
                    continue
                latest = latest_quote(conn, order["symbol"])
                if not latest:
                    continue
                quote_id, quote = latest
                if quote_id == order["last_quote_id"]:
                    continue
                config_row = conn.execute(
                    "SELECT payload FROM configs WHERE id=?", (order["config_id"],)
                ).fetchone()
                config = Settings.model_validate_json(config_row[0])
                blocked = self._blocker(conn, order, instrument, quote, now, current_config)
                price = fill_price(quote, order["side"], instrument.tick, config)
                if not blocked and (price <= 0 or price > quote.upper or price < quote.lower):
                    blocked = "滑点后价格超出涨跌停边界"
                previous = conn.execute(
                    "SELECT payload FROM quotes WHERE symbol=? AND (at<? OR (at=? AND id<?)) "
                    "ORDER BY at DESC,id DESC LIMIT 1",
                    (quote.symbol, quote.at, quote.at, quote_id),
                ).fetchone()
                old = Quote(**json.loads(previous[0])) if previous else None
                capacity = 0
                if (
                    old
                    and old.source == quote.source
                    and dt(old.at).date() == dt(quote.at).date()
                    and quote.volume >= old.volume
                    and 0 < (dt(quote.at) - dt(old.at)).total_seconds() <= 120
                ):
                    capacity = int(Decimal(quote.volume - old.volume) * config.participation)
                consumed_row = conn.execute(
                    "SELECT consumed FROM liquidity WHERE quote_id=?", (quote_id,)
                ).fetchone()
                capacity = max(0, capacity - (consumed_row[0] if consumed_row else 0))
                if not blocked and capacity <= 0:
                    blocked = "等待同源新增成交量"
                conn.execute(
                    "UPDATE orders SET last_quote_id=?,blocked_reason=?,updated_at=? WHERE id=?",
                    (quote_id, blocked, iso(now), order["id"]),
                )
                if blocked:
                    continue
                remaining = order["quantity"] - order["filled"]
                quantity = min(remaining, capacity)
                if order["kind"] in {"intraday", "t_buy"}:
                    from app.trading.intraday import target_quantity

                    live = get_state(conn, "live_targets", {})
                    account = snapshot(conn, now)
                    held = next(
                        (p["quantity"] for p in account["positions"] if p["symbol"] == order["symbol"]), 0
                    )
                    desired = target_quantity(
                        live, account, order["symbol"], quote, instrument, current_config
                    )
                    gap = desired - held if order["side"] == "BUY" else held - desired
                    quantity = min(quantity, max(0, gap))
                if order["side"] == "SELL":
                    available = conn.execute(
                        "SELECT COALESCE(SUM(quantity),0) FROM lots WHERE symbol=? AND available_day<=?",
                        (order["symbol"], now.date().isoformat()),
                    ).fetchone()[0]
                    quantity = min(quantity, available)
                    if quantity < remaining or quantity < available:
                        quantity = quantity // instrument.lot_size * instrument.lot_size
                else:
                    account = snapshot(conn, now)
                    by_symbol = {p["symbol"]: p for p in account["positions"]}
                    held_value = by_symbol.get(order["symbol"], {}).get("market_units", 0)
                    capacity_value = min(
                        Decimal(account["nav_units"]) * current_config.max_weight - held_value,
                        Decimal(account["nav_units"]) * current_config.max_exposure - account["market_units"],
                    )
                    quantity = min(
                        quantity, max(0, int(capacity_value / price)), account["cash_units"] // price
                    )
                    quantity = quantity // instrument.lot_size * instrument.lot_size
                filled_totals = conn.execute(
                    "SELECT COALESCE(SUM(gross),0),COALESCE(SUM(fee),0) FROM fills WHERE order_id=?",
                    (order["id"],),
                ).fetchone()
                cash = conn.execute("SELECT SUM(delta) FROM cash_ledger").fetchone()[0]
                while quantity > 0:
                    gross = price * quantity
                    fee = max(0, fee_for(gross + filled_totals[0], config) - filled_totals[1])
                    safe = order["side"] == "SELL"
                    if order["side"] == "BUY" and gross + fee <= cash:
                        post_nav = account["nav_units"] - gross - fee + quote.last * quantity
                        safe = (
                            Decimal(held_value + quote.last * quantity)
                            <= Decimal(post_nav) * current_config.max_weight
                            and Decimal(account["market_units"] + quote.last * quantity)
                            <= Decimal(post_nav) * current_config.max_exposure
                        )
                    if safe:
                        break
                    quantity -= instrument.lot_size
                if quantity <= 0:
                    conn.execute(
                        "UPDATE orders SET blocked_reason=? WHERE id=?",
                        ("T+1 可卖量／整手数量／现金或仓位额度不足", order["id"]),
                    )
                    continue
                self._fill(conn, order, quote_id, price, quantity, fee, now, instrument)
                conn.execute(
                    "INSERT INTO liquidity VALUES(?,?) ON CONFLICT(quote_id) DO UPDATE SET consumed=consumed+excluded.consumed",
                    (quote_id, quantity),
                )

    def _blocker(self, conn, order, instrument, quote, now, config):
        if instrument.accounting_block:
            return instrument.accounting_block
        actions = get_state(conn, f"actions:{instrument.symbol}", {})
        if not actions.get("at") or dt(actions["at"]).date() != now.date():
            return "当日分红／折算核验未完成"
        if order["side"] == "BUY" and instrument.settlement:
            try:
                self.calendar.next(now.date())
            except ValueError:
                return "下一交易日尚未核验"
        if not instrument.active or not instrument.verified:
            return "交易属性未确认"
        if not quote.fresh(now, config.quote_max_age):
            return "行情过期或时间异常"
        if dt(quote.at) <= dt(order["created_at"]):
            return "等待订单提交后的行情"
        if order["kind"] == "rebalance" and dt(order["created_at"]).date() != now.date():
            return "调仓单已跨日"
        if quote.status != "trading" or min(quote.bid, quote.ask, quote.last, quote.lower, quote.upper) <= 0:
            return "停牌或交易状态／盘口／价格边界未知"
        if quote.bid > quote.ask:
            return "盘口倒挂"
        if order["kind"] in {"intraday", "t_sell", "t_buy"}:
            from app.trading.intraday import order_problem

            problem = order_problem(conn, order, instrument, quote, config, self.calendar, now)
            if problem:
                return problem
            if conn.execute("SELECT 1 FROM risk_intents WHERE symbol=?", (order["symbol"],)).fetchone():
                return "风险退出优先"
        if order["side"] == "BUY":
            if order["plan_id"]:
                row = conn.execute("SELECT payload FROM plans WHERE id=?", (order["plan_id"],)).fetchone()
                if not row or json.loads(row[0]).get("focus_policy") != FOCUS_POLICY:
                    return "计划的 ETF 名单规则已过期"
            if not instrument.tradable or not instrument.watched:
                return "不在手动 ETF 名单或自动交易范围"
            if not get_state(conn, "buy_ready", False):
                return "数据未就绪，暂不能买入"
            if quote.ask >= quote.upper:
                return "涨停不模拟买入"
            if conn.execute(
                "SELECT 1 FROM cooldown WHERE symbol=? AND day=?", (order["symbol"], now.date().isoformat())
            ).fetchone():
                return "当日止损冷却"
            if conn.execute(
                f"SELECT 1 FROM orders WHERE side='SELL' AND kind IN ('rebalance','intraday','t_sell') AND status IN {OPEN}"
            ).fetchone():
                return "等待本轮卖单完成"
            account = snapshot(conn, now)
            if account["stale"]:
                return "持仓估值未就绪"
            if (
                order["symbol"] not in {p["symbol"] for p in account["positions"]}
                and len(account["positions"]) >= config.max_positions
            ):
                return "最大持仓数限制"
        elif quote.bid <= quote.lower:
            return "跌停不模拟卖出"
        return ""

    def _fill(self, conn, order, quote_id, price, quantity, fee, now, instrument):
        gross = price * quantity
        realized = 0
        if order["side"] == "SELL":
            left, cost = quantity, 0
            lots = conn.execute(
                "SELECT * FROM lots WHERE symbol=? AND quantity>0 AND available_day<=? ORDER BY acquired_day,id",
                (order["symbol"], now.date().isoformat()),
            ).fetchall()
            for lot in lots:
                take = min(left, lot["quantity"])
                basis = lot["cost"] if take == lot["quantity"] else lot["cost"] * take // lot["quantity"]
                risk_basis = (
                    lot["risk_cost"]
                    if take == lot["quantity"]
                    else lot["risk_cost"] * take // lot["quantity"]
                )
                conn.execute(
                    "UPDATE lots SET quantity=quantity-?,cost=cost-?,risk_cost=risk_cost-? WHERE id=?",
                    (take, basis, risk_basis, lot["id"]),
                )
                cost += basis
                left -= take
                if not left:
                    break
            if left:
                raise RuntimeError("sell allocation mismatch")
            realized = gross - fee - cost
        cursor = conn.execute(
            "INSERT INTO fills(order_id,quote_id,symbol,side,quantity,price,gross,fee,realized,at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                order["id"],
                quote_id,
                order["symbol"],
                order["side"],
                quantity,
                price,
                gross,
                fee,
                realized,
                iso(now),
            ),
        )
        fill_id = cursor.lastrowid
        delta = -gross - fee if order["side"] == "BUY" else gross - fee
        conn.execute(
            "INSERT INTO cash_ledger(key,delta,kind,at,reference) VALUES(?,?,'trade',?,?)",
            (f"fill:{fill_id}", delta, iso(now), str(fill_id)),
        )
        conn.execute(
            "INSERT INTO position_ledger(key,symbol,delta,at,kind) VALUES(?,?,?,?,'trade')",
            (f"fill:{fill_id}", order["symbol"], quantity if order["side"] == "BUY" else -quantity, iso(now)),
        )
        if order["side"] == "BUY":
            evidence = conn.execute(
                "SELECT payload FROM intraday_decisions WHERE order_id=?", (order["id"],)
            ).fetchone()
            if evidence:
                decision = json.loads(evidence[0])
                pa = decision.get("pa", {})
                levels = pa.get("raw", {})
                stop = levels.get("t_stop" if order["kind"] == "t_buy" else "entry_stop")
                if order["kind"] == "t_buy" and decision.get("minute_t"):
                    # Minute support invalidates only this T cycle. The base position
                    # keeps its daily protection rather than inheriting a T entry stop.
                    stop = levels.get("structural_stop")
                if stop:
                    reference = get_state(conn, f"pa_position:{order['symbol']}", {})
                    reference.update(
                        stop=max(stop, reference.get("stop", 0)), structure=pa, as_of=pa.get("as_of")
                    )
                    if order["kind"] == "intraday":
                        reference["target"] = levels.get("target")
                    set_state(conn, f"pa_position:{order['symbol']}", reference)
            available_day = (
                self.calendar.next(now.date()) if instrument.settlement else now.date().isoformat()
            )
            conn.execute(
                "INSERT INTO lots(symbol,acquired_day,available_day,quantity,cost,risk_cost,high,fill_id) VALUES(?,?,?,?,?,?,?,?)",
                (
                    order["symbol"],
                    now.date().isoformat(),
                    available_day,
                    quantity,
                    gross + fee,
                    gross + fee,
                    price,
                    fill_id,
                ),
            )
        filled = order["filled"] + quantity
        conn.execute(
            "UPDATE orders SET filled=?,status=?,updated_at=?,blocked_reason='' WHERE id=?",
            (filled, "filled" if filled == order["quantity"] else "partial", iso(now), order["id"]),
        )
        if order["kind"] == "risk":
            conn.execute(
                "INSERT OR IGNORE INTO cooldown VALUES(?,?)", (order["symbol"], now.date().isoformat())
            )
        remaining = conn.execute(
            "SELECT COALESCE(SUM(quantity),0) FROM lots WHERE symbol=?", (order["symbol"],)
        ).fetchone()[0]
        if remaining == 0:
            conn.execute("DELETE FROM risk_intents WHERE symbol=?", (order["symbol"],))
            conn.execute("DELETE FROM state WHERE key=?", (f"pa_position:{order['symbol']}",))

    def expire(self, now: datetime):
        with self.db.transaction() as conn:
            _, config = settings(conn)
            for row in conn.execute(
                f"SELECT id,kind,created_at FROM orders WHERE kind IN ('rebalance','intraday','t_sell','t_buy') AND status IN {OPEN}"
            ).fetchall():
                created_day = dt(row["created_at"]).date()
                timed_out = (
                    row["kind"] != "rebalance"
                    and (now - dt(row["created_at"])).total_seconds() >= config.intraday_order_ttl
                )
                if created_day < now.date() or now.time() >= time(15) or timed_out:
                    conn.execute(
                        "UPDATE orders SET status='expired',updated_at=?,blocked_reason=? WHERE id=?",
                        (iso(now), "盘中订单超时" if timed_out else "收盘失效", row["id"]),
                    )

    def record_equity(self, now: datetime):
        with self.db.transaction() as conn:
            account = snapshot(conn, now)
            conn.execute(
                "INSERT OR IGNORE INTO equity VALUES(?,?,?,?,?,?,?)",
                (
                    iso(now),
                    now.date().isoformat(),
                    account["cash_units"],
                    account["market_units"],
                    account["receivable_units"],
                    account["nav_units"],
                    int(account["stale"]),
                ),
            )
            set_state(conn, "reconciliation", {"at": iso(now), "errors": reconcile(conn)})
