"""Explicit, backed-up maintenance for legacy simulated fills; never runs at startup.

Reprice recorded quantities against their retained executable quotes, replay FIFO
costs and cumulative order fees, and preserve a before/after audit in one transaction.
Corporate actions affecting held shares require a separate replay and are rejected.
"""

import argparse
import hashlib
import json
import os
import sqlite3
from collections import defaultdict
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from pathlib import Path

from app.core.config import Settings, data_dir
from app.core.types import Quote, iso, now_cn
from app.storage.db import dump
from app.trading.account import reconcile
from app.trading.engine import fee_for, fill_price

POLICY = "historical-slippage-removal-v1"
FILL_FIELDS = ("price", "gross", "fee", "realized")
TABLE_KEYS = {"fills": "id", "cash_ledger": "id", "lots": "id", "equity": "at"}
UPDATE_TRIGGERS = ("fills_immutable_update", "cash_immutable_update")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def rows(conn, sql, params=()):
    return [dict(row) for row in conn.execute(sql, params)]


def source_data(conn):
    result = {table: rows(conn, f"SELECT * FROM {table} ORDER BY 1") for table in (
        "fills", "orders", "configs", "lots", "cash_ledger", "position_ledger", "actions", "equity"
    )}
    result["quotes"] = rows(conn, "SELECT * FROM quotes WHERE id IN (SELECT quote_id FROM fills) ORDER BY id")
    result["instruments"] = rows(conn, "SELECT * FROM instruments WHERE symbol IN (SELECT symbol FROM fills) ORDER BY symbol")
    result["peak"] = rows(conn, "SELECT * FROM state WHERE key='peak_nav'")
    return result


def fingerprint(value):
    return hashlib.sha256(dump(value).encode()).hexdigest()


def replay(fills, lots, configs, *, validate_fees=False):
    inventory, totals, output = {}, defaultdict(lambda: [0, 0]), []
    lot_by_fill = {lot["fill_id"]: lot for lot in lots}
    for original in fills:
        fill = dict(original)
        quantity, order_id = fill["quantity"], fill["order_id"]
        gross = fill["price"] * quantity
        previous_gross, previous_fee = totals[order_id]
        fee = max(0, fee_for(previous_gross + gross, configs[order_id]) - previous_fee)
        if validate_fees:
            require((gross, fee) == (fill["gross"], fill["fee"]), f"成交金额／累计佣金不符：{fill['id']}")
        totals[order_id] = [previous_gross + gross, previous_fee + fee]
        realized = 0
        if fill["side"] == "BUY":
            require(fill["id"] in lot_by_fill, f"缺少买入批次：{fill['id']}")
            lot = lot_by_fill[fill["id"]]
            require(lot["symbol"] == fill["symbol"] and lot["acquired_day"] == fill["at"][:10], "批次来源不符")
            inventory[lot["id"]] = {**lot, "quantity": quantity, "cost": gross + fee, "risk_cost": gross + fee}
        else:
            remaining, basis = quantity, 0
            available = sorted(inventory.values(), key=lambda lot: (lot["acquired_day"], lot["id"]))
            for lot in available:
                if lot["symbol"] != fill["symbol"] or lot["available_day"] > fill["at"][:10] or not lot["quantity"]:
                    continue
                take = min(remaining, lot["quantity"])
                cost = lot["cost"] if take == lot["quantity"] else lot["cost"] * take // lot["quantity"]
                lot["quantity"] -= take
                lot["cost"] -= cost
                lot["risk_cost"] = lot["cost"]
                basis += cost
                remaining -= take
                if not remaining:
                    break
            require(remaining == 0, f"FIFO 可卖批次不足：{fill['id']}")
            realized = gross - fee - basis
        if validate_fees:
            require(realized == original["realized"], f"原已实现盈亏无法重建：{fill['id']}")
        output.append({**fill, "gross": gross, "fee": fee, "realized": realized})
    require(set(inventory) == {lot["id"] for lot in lots}, "存在无法重建的持仓批次")
    return output, inventory


def changes(before, after, fields):
    return [{"before": old, "after": new} for old, new in zip(before, after, strict=True)
            if any(old[field] != new[field] for field in fields)]


def make_plan(conn, through_fill_id):
    require(conn.in_transaction, "必须在一致的数据库事务中读取")
    require(not reconcile(conn), "原账本核对失败，停止修复")
    source = source_data(conn)
    fills = sorted(source["fills"], key=lambda fill: (fill["at"], fill["id"]))
    require(fills and through_fill_id in {fill["id"] for fill in fills}, "修复边界必须是已存在的成交 ID")
    require(not any(order["status"] in ("pending", "partial") for order in source["orders"]), "存在待撮合订单，停止修复")
    require(not any(row["kind"] != "trade" and row["delta"] for row in source["position_ledger"]), "存在非成交份额变动，需专门重放公司行动")
    first_buy = {}
    for fill in fills:
        if fill["side"] == "BUY":
            first_buy.setdefault(fill["symbol"], fill["at"][:10])
    for action in source["actions"]:
        start = first_buy.get(action["symbol"])
        if start and action["status"] != "pending":
            require(action["ex_day"] < start, "持有期间存在公司行动，需专门重放")
    require(all(lot["risk_cost"] == lot["cost"] for lot in source["lots"]), "存在独立风险成本调整，停止修复")
    quotes = {row["id"]: Quote(**json.loads(row["payload"])) for row in source["quotes"]}
    instruments = {row["symbol"]: json.loads(row["payload"]) for row in source["instruments"]}
    configs = {row["id"]: json.loads(row["payload"]) for row in source["configs"]}
    orders = {row["id"]: row for row in source["orders"]}
    order_configs = {key: Settings.model_validate(configs[order["config_id"]]) for key, order in orders.items()}
    repriced = []
    for fill in fills:
        require(fill["quantity"] > 0 and fill["side"] in ("BUY", "SELL"), "无效成交")
        updated = dict(fill)
        if fill["id"] <= through_fill_id:
            quote = quotes.get(fill["quote_id"])
            require(quote and quote.symbol == fill["symbol"], f"缺少原始成交报价：{fill['id']}")
            require(fill["symbol"] in instruments, f"缺少证券最小价位：{fill['id']}")
            tick = instruments[fill["symbol"]]["tick"]
            require(tick > 0 and 0 < quote.bid <= quote.ask, f"无效盘口：{fill['id']}")
            new_price = fill_price(quote, fill["side"], tick)
            raw_config = configs[orders[fill["order_id"]]["config_id"]]
            bps = Decimal(str(raw_config.get("slippage_bps", 0)))
            require(bps.is_finite() and 0 <= bps <= 100, "历史滑点参数无效")
            book = Decimal(quote.ask if fill["side"] == "BUY" else quote.bid)
            raw = book * (1 + bps / 10000 * (1 if fill["side"] == "BUY" else -1))
            rounding = ROUND_CEILING if fill["side"] == "BUY" else ROUND_FLOOR
            legacy_price = int((raw / tick).to_integral_value(rounding=rounding)) * tick
            require(fill["price"] in (new_price, legacy_price), f"历史价格无法用原始滑点规则解释：{fill['id']}")
            require(new_price > 0, "修复价格必须为正")
            updated["price"] = new_price
        repriced.append(updated)
    _, original_lots = replay(fills, source["lots"], order_configs, validate_fees=True)
    for lot in source["lots"]:
        require(all(lot[key] == original_lots[lot["id"]][key] for key in ("quantity", "cost", "risk_cost")), "原持仓批次无法逐笔重建")
    new_fills, new_lots = replay(repriced, source["lots"], order_configs)
    new_by_id = {fill["id"]: fill for fill in new_fills}
    new_cash = []
    for row in source["cash_ledger"]:
        updated = dict(row)
        if row["key"].startswith("fill:"):
            fill = new_by_id[int(row["key"].split(":")[1])]
            updated["delta"] = (-fill["gross"] if fill["side"] == "BUY" else fill["gross"]) - fill["fee"]
        new_cash.append(updated)
    require(sum(row["delta"] for row in new_cash) >= 0, "修复后现金为负")
    events = sorted(zip(source["cash_ledger"], new_cash, strict=True), key=lambda pair: (pair[0]["at"], pair[0]["id"]))
    new_equity, index, old_balance, new_balance = [], 0, 0, 0
    for equity in source["equity"]:
        while index < len(events) and events[index][0]["at"] <= equity["at"]:
            old_balance += events[index][0]["delta"]
            new_balance += events[index][1]["delta"]
            index += 1
        require(equity["cash"] == old_balance, f"估值现金时间线不符：{equity['at']}")
        require(equity["nav"] == equity["cash"] + equity["market_value"] + equity["receivable"], "原净值不平衡")
        new_equity.append({**equity, "cash": new_balance, "nav": equity["nav"] + new_balance - old_balance})
    modified = {
        "fills": changes(fills, new_fills, FILL_FIELDS),
        "cash_ledger": changes(source["cash_ledger"], new_cash, ("delta",)),
        "lots": changes(source["lots"], [new_lots[lot["id"]] for lot in source["lots"]], ("cost", "risk_cost")),
        "equity": changes(source["equity"], new_equity, ("cash", "nav")),
    }
    # Rebase the derived peak on recorded valuations. An old scalar peak has no
    # timestamp, so adding today's correction would invent its historical timing.
    peak_before = source["peak"][0]["value"] if source["peak"] else None
    capital = sum(row["delta"] for row in new_cash if row["kind"] in ("initial", "capital_adjustment"))
    peak_after = dump(max([capital] + [row["nav"] for row in new_equity if not row["stale"]]))
    price_count = sum(old["price"] != new["price"] for old, new in zip(fills, new_fills, strict=True))
    if not modified["fills"]:
        peak_after = peak_before
    cash_delta = sum(row["delta"] for row in new_cash) - sum(row["delta"] for row in source["cash_ledger"])
    return {
        "policy": POLICY, "through_fill_id": through_fill_id, "source_sha256": fingerprint(source),
        "changes": modified, "peak": {"before": peak_before, "after": peak_after},
        "summary": {
            "repriced_fills": price_count, "cash_increase": cash_delta / 1_000_000,
            "fee_change": sum(new["fee"] - old["fee"] for old, new in zip(fills, new_fills, strict=True)) / 1_000_000,
            "realized_change": sum(new["realized"] - old["realized"] for old, new in zip(fills, new_fills, strict=True)) / 1_000_000,
            "cash_before": sum(row["delta"] for row in source["cash_ledger"]) / 1_000_000,
            "cash_after": sum(row["delta"] for row in new_cash) / 1_000_000,
            "rows_changed": {table: len(items) for table, items in modified.items()},
            "peak_basis": "recorded non-stale valuations and capital",
        },
    }


def apply_plan(conn, plan, *, backup_path):
    require(conn.in_transaction, "必须在独占写事务中修复")
    require(fingerprint(source_data(conn)) == plan["source_sha256"], "修复清单已过期")
    if not plan["changes"]["fills"]:
        return False
    triggers = rows(conn, "SELECT name,sql FROM sqlite_master WHERE type='trigger' AND name IN (?,?) ORDER BY name", UPDATE_TRIGGERS)
    require(len(triggers) == 2 and all(row["sql"] for row in triggers), "账本保护触发器缺失")
    for trigger in triggers:
        conn.execute(f"DROP TRIGGER {trigger['name']}")
    fields = {"fills": FILL_FIELDS, "cash_ledger": ("delta",), "lots": ("cost", "risk_cost"), "equity": ("cash", "nav")}
    for table, items in plan["changes"].items():
        key = TABLE_KEYS[table]
        for item in items:
            new = item["after"]
            assignments = ",".join(field + "=?" for field in fields[table])
            cursor = conn.execute(f"UPDATE {table} SET {assignments} WHERE {key}=?", (*[new[field] for field in fields[table]], new[key]))
            require(cursor.rowcount == 1, "修复行数异常")
    if plan["peak"]["after"] != plan["peak"]["before"]:
        conn.execute("INSERT INTO state VALUES('peak_nav',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (plan["peak"]["after"],))
    for trigger in triggers:
        conn.execute(trigger["sql"])
    require(not reconcile(conn), "修复后账本核对失败")
    require(not conn.execute("PRAGMA foreign_key_check").fetchall(), "修复后外键检查失败")
    require([row[0] for row in conn.execute("PRAGMA quick_check")] == ["ok"], "修复后数据库检查失败")
    require(rows(conn, "SELECT name,sql FROM sqlite_master WHERE type='trigger' AND name IN (?,?) ORDER BY name", UPDATE_TRIGGERS) == triggers, "保护触发器未完整恢复")
    for table, items in plan["changes"].items():
        for item in items:
            actual = rows(conn, f"SELECT * FROM {table} WHERE {TABLE_KEYS[table]}=?", (item["after"][TABLE_KEYS[table]],))
            require(actual == [item["after"]], "修复结果与清单不符")
    conn.execute("CREATE TABLE IF NOT EXISTS ledger_repairs(id TEXT PRIMARY KEY,at TEXT NOT NULL,policy TEXT NOT NULL,backup_path TEXT NOT NULL,payload TEXT NOT NULL)")
    audit_id = fingerprint(plan)
    conn.execute("INSERT INTO ledger_repairs VALUES(?,?,?,?,?)", (audit_id, iso(now_cn()), POLICY, str(backup_path), dump(plan)))
    conn.execute("INSERT INTO runs(task,at,status,detail) VALUES(?,?,'ok',?)", ("ledger_repair", iso(now_cn()), dump({"audit_id": audit_id, **plan["summary"]})))
    return True


def connect(path, *, readonly=False):
    conn = sqlite3.connect(path.resolve().as_uri() + ("?mode=ro" if readonly else "?mode=rw"), uri=True, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def backup_database(path, backup_path, source_sha256):
    descriptor = os.open(backup_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(descriptor)
    with connect(path, readonly=True) as source, sqlite3.connect(backup_path) as target:
        source.backup(target)
    with connect(backup_path, readonly=True) as backup:
        require(fingerprint(source_data(backup)) == source_sha256, "备份与修复前数据不一致")
        require([row[0] for row in backup.execute("PRAGMA quick_check")] == ["ok"], "备份完整性检查失败")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=data_dir() / "niuno3.sqlite3")
    parser.add_argument("--through-fill-id", type=int, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--expected-sha256")
    parser.add_argument("--backup", type=Path)
    args = parser.parse_args()
    if args.apply:
        require(args.expected_sha256 and args.backup, "写入必须指定已复核指纹和新的备份路径")
        require(args.database.resolve() != args.backup.resolve(), "备份不能覆盖正式数据库")
    with connect(args.database, readonly=not args.apply) as conn:
        conn.execute("BEGIN IMMEDIATE" if args.apply else "BEGIN")
        plan = make_plan(conn, args.through_fill_id)
        with args.report.open("x", encoding="utf-8") as report:
            report.write(json.dumps(plan, ensure_ascii=False, indent=2))
        if args.apply and plan["changes"]["fills"]:
            require(plan["source_sha256"] == args.expected_sha256, "修复清单已过期，请重新预览")
            backup_database(args.database, args.backup, plan["source_sha256"])
            apply_plan(conn, plan, backup_path=args.backup)
    print(json.dumps({"applied": bool(args.apply and plan["changes"]["fills"]), "source_sha256": plan["source_sha256"], **plan["summary"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
