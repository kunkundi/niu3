from __future__ import annotations

from datetime import datetime

from app.core.types import dt, yuan
from app.storage.db import get_state, instruments, latest_quote, settings


def positions(conn, now: datetime) -> list[dict]:
    result = []
    universe = instruments(conn)
    _, config = settings(conn)
    for row in conn.execute(
        "SELECT symbol,SUM(quantity) quantity,SUM(cost) cost,SUM(risk_cost) risk_cost,MAX(high) high "
        "FROM lots WHERE quantity>0 GROUP BY symbol"
    ):
        symbol = row["symbol"]
        latest = latest_quote(conn, symbol)
        quote = latest[1] if latest else None
        quantity, cost = row["quantity"], row["cost"]
        price = quote.last if quote and quote.last > 0 else cost // quantity
        available = conn.execute(
            "SELECT COALESCE(SUM(quantity),0) FROM lots WHERE symbol=? AND available_day<=?",
            (symbol, now.date().isoformat()),
        ).fetchone()[0]
        result.append(
            {
                "symbol": symbol,
                "name": universe[symbol].name if symbol in universe else symbol,
                "quantity": quantity,
                "available": available,
                "cost_units": cost,
                "risk_cost_units": row["risk_cost"],
                "price_units": price,
                "market_units": quantity * price,
                "high_units": row["high"],
                "cost": yuan(cost),
                "average_cost": yuan(cost // quantity),
                "price": yuan(price),
                "market_value": yuan(quantity * price),
                "pnl": yuan(quantity * price - cost),
                "pnl_pct": (quantity * price / cost - 1) if cost else 0,
                "quote_at": quote.at if quote else None,
                "stale": not quote or not quote.fresh(now, config.quote_max_age) or quote.last <= 0 or quote.status != "trading",
                "accounting_block": universe[symbol].accounting_block if symbol in universe else "目录缺失",
            }
        )
    return result


def snapshot(conn, now: datetime) -> dict:
    holdings = positions(conn, now)
    cash = conn.execute("SELECT COALESCE(SUM(delta),0) FROM cash_ledger").fetchone()[0]
    receivable = conn.execute(
        "SELECT COALESCE(SUM(receivable),0) FROM actions WHERE status='entitled'"
    ).fetchone()[0]
    market = sum(p["market_units"] for p in holdings)
    nav = cash + market + receivable
    realized = conn.execute("SELECT COALESCE(SUM(realized),0) FROM fills WHERE side='SELL'").fetchone()[0]
    _, config = settings(conn)
    peak = get_state(conn, "peak_nav", nav)
    stale = any(p["stale"] or p["accounting_block"] for p in holdings)
    return {
        "cash_units": cash,
        "nav_units": nav,
        "market_units": market,
        "receivable_units": receivable,
        "cash": yuan(cash),
        "nav": yuan(nav),
        "market_value": yuan(market),
        "receivable": yuan(receivable),
        "realized": yuan(realized),
        "unrealized": sum(p["pnl"] for p in holdings),
        "return_pct": yuan(nav) / float(config.initial_cash) - 1,
        "exposure": market / nav if nav > 0 else 0,
        "drawdown": max(0, 1 - nav / peak) if peak else 0,
        "positions": holdings,
        "stale": bool(stale),
    }


def reconcile(conn) -> list[str]:
    """Compare mutable projections to independent immutable transaction evidence."""
    errors = []
    lot_quantities = dict(conn.execute("SELECT symbol,SUM(quantity) FROM lots GROUP BY symbol"))
    event_quantities = dict(conn.execute("SELECT symbol,SUM(delta) FROM position_ledger GROUP BY symbol"))
    for symbol in set(lot_quantities) | set(event_quantities):
        if lot_quantities.get(symbol, 0) != event_quantities.get(symbol, 0):
            errors.append(f"持仓账本不一致：{symbol}")
    costs = dict(conn.execute("SELECT symbol,SUM(cost) FROM lots GROUP BY symbol"))
    basis = dict(
        conn.execute(
            "SELECT symbol,SUM(CASE WHEN side='BUY' THEN gross+fee ELSE -(gross-fee-realized) END) "
            "FROM fills GROUP BY symbol"
        )
    )
    for symbol in costs.keys() | basis.keys():
        if costs.get(symbol, 0) != basis.get(symbol, 0):
            errors.append(f"持仓成本与成交不一致：{symbol}")
    for row in conn.execute(
        "SELECT f.id,f.symbol,f.quantity,f.side,f.gross,f.fee,c.delta cash_delta,p.delta quantity_delta,"
        "p.symbol position_symbol FROM fills f LEFT JOIN cash_ledger c ON c.key='fill:'||f.id "
        "LEFT JOIN position_ledger p ON p.key='fill:'||f.id"
    ):
        sign = 1 if row["side"] == "BUY" else -1
        if (
            row["cash_delta"] != -sign * row["gross"] - row["fee"]
            or row["quantity_delta"] != sign * row["quantity"]
            or row["position_symbol"] != row["symbol"]
        ):
            errors.append(f"成交分录不一致：{row['id']}")
    capital = conn.execute(
        "SELECT COALESCE(SUM(delta),0) FROM cash_ledger WHERE kind IN ('initial','capital_adjustment')"
    ).fetchone()[0]
    fills = conn.execute(
        "SELECT COALESCE(SUM(CASE WHEN side='BUY' THEN -gross-fee ELSE gross-fee END),0) FROM fills"
    ).fetchone()[0]
    dividends = conn.execute(
        "SELECT COALESCE(SUM(entitlement),0) FROM actions WHERE kind='dividend' AND status='paid'"
    ).fetchone()[0]
    actual = conn.execute("SELECT COALESCE(SUM(delta),0) FROM cash_ledger").fetchone()[0]
    if actual != capital + fills + dividends:
        errors.append("资金账本与成交／分红不一致")
    if actual < 0:
        errors.append("现金余额为负")
    for row in conn.execute(
        "SELECT o.id,o.filled,COALESCE(SUM(f.quantity),0) evidence "
        "FROM orders o LEFT JOIN fills f ON o.id=f.order_id GROUP BY o.id"
    ):
        if row["filled"] != row["evidence"]:
            errors.append(f"订单成交数量不一致：{row['id']}")
    return errors


def quote_age(at: str | None, now: datetime) -> int | None:
    return int((now - dt(at)).total_seconds()) if at else None
