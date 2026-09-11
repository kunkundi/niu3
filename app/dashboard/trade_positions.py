"""Classify displayed fills from the immutable position ledger, before pagination."""


def annotate_trade_positions(conn, items):
    if not items:
        return
    symbols = sorted({item["symbol"] for item in items})
    placeholders = ",".join("?" for _ in symbols)
    rows = conn.execute(
        "WITH positions AS ("
        "SELECT key,symbol,delta,SUM(delta) OVER (PARTITION BY symbol ORDER BY at,id "
        "ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS position_after "
        f"FROM position_ledger WHERE symbol IN ({placeholders})) "
        "SELECT f.id,f.order_id,f.side,f.quantity,p.delta,p.position_after "
        "FROM fills f LEFT JOIN positions p ON p.key='fill:'||f.id AND p.symbol=f.symbol "
        f"WHERE f.symbol IN ({placeholders}) AND f.id<=? ORDER BY f.at,f.id",
        (*symbols, *symbols, max(item["id"] for item in items)),
    )
    order_bases, classified = {}, {}
    for row in rows:
        delta = row["quantity"] if row["side"] == "BUY" else -row["quantity"]
        after = row["position_after"]
        before = after - delta if after is not None else None
        valid = row["delta"] == delta and before is not None and min(before, after) >= 0
        before, after = (before, after) if valid else (None, None)
        # One opening order can have many partial fills; those are not new add-on orders.
        order_bases.setdefault(row["order_id"], before)
        basis = order_bases[row["order_id"]]
        effect = "unknown"
        if valid:
            if row["side"] == "SELL":
                effect = "close" if after == 0 else "reduce"
            elif before == 0 or basis == 0:
                effect = "open"
            elif basis is not None:
                effect = "add"
        classified[row["id"]] = {
            "position_effect": effect,
            "position_before": before,
            "position_after": after,
        }
    for item in items:
        item.update(classified[item["id"]])
