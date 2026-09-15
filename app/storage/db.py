from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from contextlib import contextmanager
from pathlib import Path

from app.core.config import DisplaySettings, RETIRED_EXECUTION_FIELDS, Settings
from app.core.types import Instrument, Quote, iso, now_cn, units
from app.strategies.focus import FOCUS_POLICY


def dump(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def get_state(conn, key: str, default=None):
    row = conn.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
    return json.loads(row[0]) if row else default


def set_state(conn, key: str, value):
    conn.execute(
        "INSERT INTO state VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, dump(value)),
    )


def settings(conn) -> tuple[int, Settings]:
    row = conn.execute("SELECT id,payload FROM configs ORDER BY id DESC LIMIT 1").fetchone()
    return row["id"], Settings.model_validate_json(row["payload"])


def display_settings(conn) -> DisplaySettings:
    return DisplaySettings.model_validate(get_state(conn, "display_settings", {}))


def instruments(conn) -> dict[str, Instrument]:
    return {row[0]: Instrument(**json.loads(row[1])) for row in conn.execute("SELECT * FROM instruments")}


def tracked_instruments(conn) -> dict[str, Instrument]:
    """Manual watchlist plus instruments still needed to manage existing positions/orders."""
    protected = {r[0] for r in conn.execute("SELECT DISTINCT symbol FROM lots WHERE quantity>0")}
    protected.update(
        r[0] for r in conn.execute("SELECT DISTINCT symbol FROM orders WHERE status IN ('pending','partial')")
    )
    return {s: i for s, i in instruments(conn).items() if i.watched or s in protected}


def sold_today(conn, now) -> set[str]:
    """Actual sell fills in this Shanghai calendar day, through the current time."""
    end = iso(now)
    start = end[:10] + "T00:00:00+08:00"
    return {
        row[0]
        for row in conn.execute(
            "SELECT DISTINCT symbol FROM fills WHERE side='SELL' AND quantity>0 AND at>=? AND at<=?",
            (start, end),
        )
    }


def invalidate_pool(conn, at):
    _, config = settings(conn)
    cursor = conn.execute("INSERT INTO configs(at,payload) VALUES(?,?)", (iso(at), config.model_dump_json()))
    conn.execute(
        "UPDATE orders SET status='cancelled',updated_at=?,blocked_reason='手动 ETF 名单变更，等待新计划' "
        "WHERE status IN ('pending','partial') AND kind IN ('rebalance','intraday','t_sell','t_buy')",
        (iso(at),),
    )
    conn.execute(
        "UPDATE t_cycles SET status='abandoned',updated_at=? WHERE status IN ('selling','waiting_buy','buying')",
        (iso(at),),
    )
    conn.execute(
        "DELETE FROM state WHERE key IN ('live_targets','intraday_confirmation','intraday_execution','readiness')"
    )
    set_state(conn, "buy_ready", False)
    set_state(conn, "watchlist", {"at": iso(at), "mode": "manual"})
    return cursor.lastrowid


def put_instrument(conn, instrument: Instrument):
    conn.execute(
        "INSERT INTO instruments VALUES(?,?) ON CONFLICT(symbol) DO UPDATE SET payload=excluded.payload",
        (instrument.symbol, dump(instrument.to_dict())),
    )


def latest_quote(conn, symbol: str) -> tuple[int, Quote] | None:
    row = conn.execute(
        "SELECT id,payload FROM quotes WHERE symbol=? ORDER BY at DESC,id DESC LIMIT 1", (symbol,)
    ).fetchone()
    return (row[0], Quote(**json.loads(row[1]))) if row else None


def put_quote(conn, quote: Quote):
    conn.execute(
        "INSERT OR IGNORE INTO quotes(symbol,at,source,volume,payload) VALUES(?,?,?,?,?)",
        (quote.symbol, quote.at, quote.source, quote.volume, dump(quote.to_dict())),
    )


class Database:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version > 1:
                raise RuntimeError("database schema newer than this application")
            conn.executescript(Path(__file__).with_name("schema.sql").read_text(encoding="utf-8"))
        with self.transaction() as conn:
            # Retire manual pause and the former drawdown latch. Keep the
            # recorded high-water mark, historical controls and all order evidence.
            conn.execute("DELETE FROM state WHERE key IN ('drawdown_latched','paused')")
            if get_state(conn, "notification_settings") is None:
                set_state(
                    conn,
                    "notification_settings",
                    {"version": 1, "enabled": False, "timeout": 5, "channels": {}},
                )
                # Installing notifications never broadcasts the existing account history.
                set_state(
                    conn,
                    "notification_cursor",
                    conn.execute("SELECT COALESCE(MAX(id),0) FROM fills").fetchone()[0],
                )
            if conn.execute("SELECT 1 FROM configs LIMIT 1").fetchone() is None:
                config = Settings()
                at = iso(now_cn())
                conn.execute("INSERT INTO configs(at,payload) VALUES(?,?)", (at, config.model_dump_json()))
                conn.execute(
                    "INSERT INTO cash_ledger(key,delta,kind,at) VALUES('initial',?,'initial',?)",
                    (units(config.initial_cash), at),
                )
                set_state(conn, "peak_nav", units(config.initial_cash))
            if get_state(conn, "etf_pool_mode") != "manual-v1":
                # Archive the old automatically discovered directory. Historical account evidence
                # remains intact; only explicitly added ETFs enter the new watchlist.
                for instrument in instruments(conn).values():
                    put_instrument(conn, replace(instrument, watched=False))
                if instruments(conn):
                    invalidate_pool(conn, now_cn())
                else:
                    set_state(conn, "watchlist", {"at": iso(now_cn()), "mode": "manual"})
                set_state(conn, "etf_pool_mode", "manual-v1")
                set_state(conn, "focus_policy", FOCUS_POLICY)
                conn.execute("DELETE FROM state WHERE key IN ('catalog','error:market')")
            if get_state(conn, "focus_policy") != FOCUS_POLICY:
                # Preserve prior plan evidence. A fresh config version gives the new policy its own plan key.
                if conn.execute("SELECT 1 FROM plans LIMIT 1").fetchone():
                    _, current = settings(conn)
                    at = iso(now_cn())
                    conn.execute(
                        "INSERT INTO configs(at,payload) VALUES(?,?)", (at, current.model_dump_json())
                    )
                    conn.execute(
                        "UPDATE orders SET status='cancelled',updated_at=?,blocked_reason='ETF 名单规则更新，等待新计划' "
                        "WHERE kind IN ('rebalance','intraday','t_sell','t_buy') AND status IN ('pending','partial')",
                        (at,),
                    )
                    set_state(conn, "buy_ready", False)
                    conn.execute(
                        "INSERT INTO runs(task,at,status,detail) VALUES('strategy',?,'ok',?)",
                        (at, "ETF 名单规则已更新；原计划与历史成交保留"),
                    )
                set_state(conn, "focus_policy", FOCUS_POLICY)

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=15000")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA synchronous=FULL")
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def transaction(self):
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
                conn.commit()
            except BaseException:
                conn.rollback()
                raise

    def log(self, task: str, status: str, detail: str, at=None, duration=0):
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO runs(task,at,status,detail,duration) VALUES(?,?,?,?,?)",
                (task, iso(at or now_cn()), status, detail[:2000], duration),
            )

    def change_config(self, payload: dict, at):
        if RETIRED_EXECUTION_FIELDS.intersection(payload):
            raise ValueError("额外滑点已移除，不能再配置该参数")
        with self.transaction() as conn:
            version, old = settings(conn)
            display_patch = {
                key: value for key, value in payload.items() if key in DisplaySettings.model_fields
            }
            strategy_patch = {
                key: value for key, value in payload.items() if key not in DisplaySettings.model_fields
            }
            # Validate the whole request before writing either group. Display preferences must not
            # invalidate frozen plans, cancel orders, or reset an in-progress T cycle.
            display = DisplaySettings.model_validate({**display_settings(conn).model_dump(), **display_patch})
            config = Settings.model_validate({**old.model_dump(), **strategy_patch})
            if display_patch:
                set_state(conn, "display_settings", display.model_dump(mode="json"))
            if not strategy_patch:
                return version
            if config.strategy_model != old.strategy_model:
                conn.execute("DELETE FROM state WHERE key LIKE 'pa_position:%'")
                if config.strategy_model == "price_action":
                    # Retire percentage-based intents when explicitly switching to structural exits.
                    conn.execute(
                        "DELETE FROM cooldown WHERE symbol IN (SELECT symbol FROM risk_intents WHERE reason IN ('成本止损','持仓高点回撤止损'))"
                    )
                    conn.execute("DELETE FROM risk_intents WHERE reason IN ('成本止损','持仓高点回撤止损')")
                    conn.execute(
                        "UPDATE orders SET status='cancelled',updated_at=?,blocked_reason='切换裸 K 结构风控' WHERE kind='risk' AND reason IN ('成本止损','持仓高点回撤止损') AND status IN ('pending','partial')",
                        (iso(at),),
                    )
            if old.initial_cash != config.initial_cash:
                if conn.execute("SELECT 1 FROM orders LIMIT 1").fetchone():
                    raise ValueError("已有订单，不能改写初始资金")
                delta = units(config.initial_cash - old.initial_cash)
                conn.execute(
                    "INSERT INTO cash_ledger(key,delta,kind,at) VALUES(?,?,'capital_adjustment',?)",
                    (f"capital:{iso(at)}:{config.initial_cash}", delta, iso(at)),
                )
                set_state(conn, "peak_nav", units(config.initial_cash))
            cursor = conn.execute(
                "INSERT INTO configs(at,payload) VALUES(?,?)", (iso(at), config.model_dump_json())
            )
            # Existing intents keep their frozen fees; new strategy plans use the new version.
            conn.execute(
                "UPDATE orders SET status='cancelled',updated_at=?,blocked_reason='配置变更，等待新计划' "
                "WHERE status IN ('pending','partial') AND kind IN ('rebalance','intraday','t_sell','t_buy')",
                (iso(at),),
            )
            conn.execute(
                "UPDATE t_cycles SET status='abandoned',updated_at=? WHERE status IN ('selling','waiting_buy','buying')",
                (iso(at),),
            )
            conn.execute("DELETE FROM state WHERE key IN ('intraday_confirmation','intraday_execution')")
            return cursor.lastrowid

    def add_etf(self, instrument: Instrument, at):
        with self.transaction() as conn:
            current = instruments(conn).get(instrument.symbol)
            if current and current.watched:
                return False
            if current:
                instrument = replace(instrument, accounting_block=current.accounting_block)
            put_instrument(conn, replace(instrument, watched=True))
            version = invalidate_pool(conn, at)
            conn.execute(
                "INSERT INTO runs(task,at,status,detail) VALUES('watchlist',?,'ok',?)",
                (iso(at), f"手动添加 {instrument.name}（{instrument.symbol}），配置版本 {version}"),
            )
            return True

    def remove_etf(self, symbol: str, at):
        with self.transaction() as conn:
            instrument = instruments(conn).get(symbol)
            if not instrument or not instrument.watched:
                return False
            if conn.execute("SELECT 1 FROM lots WHERE symbol=? AND quantity>0", (symbol,)).fetchone():
                raise ValueError("该 ETF 仍有持仓，请在持仓退出后移除")
            if conn.execute(
                "SELECT 1 FROM orders WHERE symbol=? AND status IN ('pending','partial')", (symbol,)
            ).fetchone():
                raise ValueError("该 ETF 仍有待成交订单，请处理订单后移除")
            put_instrument(conn, replace(instrument, watched=False))
            version = invalidate_pool(conn, at)
            conn.execute(
                "INSERT INTO runs(task,at,status,detail) VALUES('watchlist',?,'ok',?)",
                (iso(at), f"移除 {instrument.name}（{symbol}），配置版本 {version}"),
            )
            return True
