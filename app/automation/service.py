"""Single-writer scheduler; network jobs run outside the trading loop and SQLite transactions."""

from __future__ import annotations

import hashlib
import json
import time
import uuid
import zlib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, time as clock, timedelta
from decimal import ROUND_CEILING

from app.core.calendar import Calendar
from app.core.classification import CLASSIFICATION_RULES
from app.core.types import Bar, dt, iso, now_cn
from app.market_data.providers import DataError, PublicProvider, SourceCooling
from app.market_data.history import HistoryUpdate, signature, validate_series, enrich_metrics, missing_metrics
from app.storage.db import (
    Database,
    dump,
    get_state,
    instruments,
    tracked_instruments,
    put_instrument,
    put_quote,
    set_state,
    settings,
    sold_today,
)
from app.strategies.momentum import build_plan
from app.strategies.focus import FOCUS_RULES, LIQUIDITY_RULES
from app.storage.maintenance import retain_evidence
from app.trading.account import reconcile
from app.trading.actions import apply_actions, ingest_actions
from app.trading.engine import Engine
from app.trading.intraday import IntradayTrader
from app.notifications.service import NotificationDispatcher
from app.automation.targets import calculate_targets
from app.strategies.buy_review import calculate_review, fingerprint, review_request, save_review

HISTORY_DOWNLOAD_REVISION = 3
TURNOVER_DATA_REVISION = 1


def history_target(calendar: Calendar, now: datetime) -> str | None:
    if not calendar.known(now.date()):
        return None
    if calendar.is_open(now.date()) and now.time() >= clock(15, 30):
        return now.date().isoformat()
    try:
        return calendar.previous(now.date())
    except ValueError:
        return None


def stored_history(conn, symbol):
    result = {"raw": [], "qfq": []}
    for row in conn.execute("SELECT adjustment,payload FROM bars WHERE symbol=? ORDER BY day", (symbol,)):
        result[row["adjustment"]].append(Bar(**json.loads(row["payload"])))
    return result


def readiness(conn, calendar: Calendar, now: datetime) -> dict:
    _, config = settings(conn)
    universe = tracked_instruments(conn)
    active = [i for i in universe.values() if i.active]
    tradable = [i for i in active if i.tradable]
    catalog = get_state(conn, "watchlist", {})
    watched = [i for i in universe.values() if i.watched]
    target = history_target(calendar, now)
    fresh_count, watched_quotes, profiles, histories = 0, 0, 0, 0
    recent_completed, failed_count = 0, 0
    cached_count, incremental_count = 0, 0
    last_success_at = None
    optional_pending, optional_failed = 0, 0
    for instrument in active:
        row = conn.execute(
            "SELECT at,payload FROM quotes WHERE symbol=? ORDER BY at DESC,id DESC LIMIT 1",
            (instrument.symbol,),
        ).fetchone()
        if (
            row
            and 0 <= (now - dt(row[0])).total_seconds() <= config.quote_max_age
            and json.loads(row[1])["last"] > 0
        ):
            fresh_count += 1
            watched_quotes += int(instrument.watched)
        profile = get_state(conn, f"profile:{instrument.symbol}", {})
        if profile.get("at") and now - dt(profile["at"]) < timedelta(days=8):
            profiles += 1
    # Download progress covers the entire tracked list, including instruments
    # whose trading attributes are still being verified.
    for instrument in active:
        row = conn.execute(
            "SELECT COUNT(*),MAX(day) FROM bars WHERE symbol=? AND adjustment='qfq' AND day<=?",
            (instrument.symbol, target or ""),
        ).fetchone()
        # Successfully downloaded new funds with <120 bars are known exclusions, not network failures.
        fetched = get_state(conn, f"history:{instrument.symbol}", {})
        if row[1] and fetched.get("target") == row[1]:
            cached_count += 1
        fetched_at = fetched.get("at")
        if fetched_at and (not last_success_at or dt(fetched_at) > dt(last_success_at)):
            last_success_at = fetched_at
        if target and row[1] == target and fetched.get("target") == target:
            histories += 1
            if fetched.get("mode") == "incremental":
                incremental_count += 1
            if fetched_at and timedelta(0) <= now - dt(fetched_at) <= timedelta(minutes=5):
                recent_completed += 1
        elif get_state(conn, f"error:history:{instrument.symbol}"):
            failed_count += 1
        optional_missing = conn.execute(
            "SELECT 1 FROM bars WHERE symbol=? AND adjustment='raw' AND day<=? AND "
            "(json_extract(payload,'$.amount') IS NULL OR json_extract(payload,'$.volume') IS NULL "
            "OR json_extract(payload,'$.turnover_rate') IS NULL) LIMIT 1",
            (instrument.symbol, target or ""),
        ).fetchone()
        if optional_missing:
            optional_pending += 1
            optional_failed += bool(get_state(conn, f"error:metrics:{instrument.symbol}"))
    errors = reconcile(conn)
    reasons = []
    if not target:
        reasons.append("交易日历超出已核验范围")
    if not watched:
        reasons.append("尚未手动添加 ETF")
    if not active or profiles / len(active) < float(config.coverage_required):
        reasons.append("基金分类资料预热中")
    cooldown = get_state(conn, "history_source_cooldown", {})
    cooling = bool(cooldown.get("until") and dt(cooldown["until"]) > now)
    if not active or histories / len(active) < float(config.coverage_required):
        reasons.append("历史日 K 覆盖率不足")
        if cooling:
            reasons.append(f"历史源冷却，{dt(cooldown['until']).strftime('%H:%M:%S')} 后重试")
    if active and not tradable:
        reasons.append("ETF 交易属性待核验")
    if calendar.session(now) and (not active or fresh_count / len(active) < float(config.coverage_required)):
        reasons.append("有效行情覆盖率不足")
    reasons.extend(errors)
    for row in conn.execute("SELECT DISTINCT symbol FROM lots WHERE quantity>0"):
        instrument = universe.get(row[0])
        fetched = get_state(conn, f"history:{row[0]}", {})
        if not instrument or instrument.accounting_block or fetched.get("target") != target:
            reasons.append(f"持仓数据／核算未就绪：{row[0]}")
    heartbeat = get_state(conn, "worker_heartbeat", {})
    # The worker can update its heartbeat while the API is reading this snapshot.
    worker_alive = bool(heartbeat.get("at") and (now - dt(heartbeat["at"])).total_seconds() <= 30)
    if not worker_alive:
        reasons.append("交易 worker 心跳中断")
    required_count = int((len(active) * config.coverage_required).to_integral_value(rounding=ROUND_CEILING))
    pending = len(active) - histories
    if not target:
        progress_state = "waiting_calendar"
    elif not active:
        progress_state = "preparing"
    elif not pending:
        progress_state = "complete"
    elif not worker_alive:
        progress_state = "offline"
    elif cooling:
        progress_state = "cooling"
    elif failed_count and not recent_completed:
        progress_state = "retrying"
    else:
        progress_state = "syncing"
    return {
        "ready": not reasons,
        "reasons": reasons,
        "catalog_count": len(watched),
        "pool_mode": "manual",
        "tracked_count": len(active),
        "watched_tradable_count": sum(i.tradable for i in watched),
        "watched_quote_count": watched_quotes,
        "quote_count": fresh_count,
        "profile_count": profiles,
        "tradable_count": len(tradable),
        "history_count": histories,
        "catalog_at": catalog.get("at"),
        "history_target": target,
        "history_progress": {
            "state": progress_state,
            "total": len(active),
            "completed": histories,
            "pending": pending,
            "coverage": histories / len(active) if active else 0,
            "required_coverage": float(config.coverage_required),
            "required_count": required_count,
            "remaining_to_ready": max(0, required_count - histories),
            "coverage_met": bool(active and histories >= required_count),
            "failed_count": failed_count,
            "recent_completed": recent_completed,
            "cached_count": cached_count,
            "incremental_count": incremental_count,
            "optional_pending": optional_pending,
            "optional_failed": optional_failed,
            "last_success_at": last_success_at,
            "target": target,
            "cooldown_until": cooldown["until"] if cooling and pending else None,
            "cooldown_source": cooldown.get("source") if cooling and pending else None,
        },
        "calendar_end": calendar.end.isoformat(),
        "worker_at": heartbeat.get("at"),
        "at": iso(now),
    }


class Worker:
    def __init__(self, db: Database, calendar: Calendar | None = None, provider=None):
        self.db = db
        self.calendar = calendar or Calendar()
        self.provider = provider or PublicProvider()
        self.engine = Engine(db, self.calendar)
        self.intraday = IntradayTrader(self.engine)
        self.last_intraday = 0
        self.notifications = NotificationDispatcher(db)
        self.pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="market-data")
        self.target_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="live-targets")
        self.minute_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="minute-bars")
        self.last_targets = 0
        self.target_version = None
        self.futures = {}
        self.attempts = {}
        self.owner = uuid.uuid4().hex
        self.last_schedule = 0
        self.last_ready = 0
        self.last_equity = 0
        self.history_cooldown_until = 0
        with db.connect() as conn:
            cooldown = get_state(conn, "history_source_cooldown", {})
        if cooldown.get("until") and dt(cooldown["until"]) > now_cn():
            self.history_cooldown_until = (
                time.monotonic() + (dt(cooldown["until"]) - now_cn()).total_seconds()
            )

    def lease(self, now: datetime) -> bool:
        with self.db.transaction() as conn:
            row = conn.execute("SELECT * FROM leases WHERE key='worker'").fetchone()
            if row and row["owner"] != self.owner and row["expires"] > now.timestamp():
                return False
            conn.execute(
                "INSERT INTO leases VALUES('worker',?,?) ON CONFLICT(key) DO UPDATE SET owner=excluded.owner,expires=excluded.expires",
                (self.owner, now.timestamp() + 30),
            )
            set_state(conn, "worker_heartbeat", {"at": iso(now), "owner": self.owner})
        return True

    def submit(self, key: str, fn, *args, cooldown=60):
        if key in self.futures or time.monotonic() - self.attempts.get(key, -1e12) < cooldown:
            return False
        self.attempts[key] = time.monotonic()
        pool = self.minute_pool if key.startswith("minute5:") else self.pool
        self.futures[key] = pool.submit(fn, *args)
        return True

    def collect(self, now):
        for key, future in list(self.futures.items()):
            if not future.done():
                continue
            del self.futures[key]
            try:
                result = future.result()
                if key.startswith("history:"):
                    series = result.series if isinstance(result, HistoryUpdate) else result
                    with self.db.connect() as conn:
                        request = get_state(conn, f"history_request:{key.split(':', 1)[1]}", {})
                    if request.get("target") and (
                        not series.get("qfq") or series["qfq"][-1].day != request["target"]
                    ):
                        raise DataError("history latest completed date missing")
                self.ingest(key, result, now)
            except Exception as exc:
                if key.startswith("history:") and isinstance(exc, SourceCooling):
                    self.history_cooldown_until = exc.until
                    with self.db.transaction() as conn:
                        set_state(
                            conn,
                            "history_source_cooldown",
                            {
                                "until": iso(now + timedelta(seconds=max(0, exc.until - time.monotonic()))),
                                "source": exc.host,
                                "message": str(exc),
                            },
                        )
                # Diagnostic class and controlled adapter messages only; never log raw upstream responses.
                detail = str(exc) if isinstance(exc, (DataError, ValueError)) else type(exc).__name__
                self.db.log(key, "error", detail, now)
                with self.db.transaction() as conn:
                    set_state(conn, f"error:{key}", {"at": iso(now), "message": detail})
                    if key.startswith(("history:", "metrics:")):
                        prefix = "history" if key.startswith("history:") else "metrics"
                        request_key = f"{prefix}_request:{key.split(':', 1)[1]}"
                        request = get_state(conn, request_key, {})
                        failures = min(12, request.get("failures", 0) + 1)
                        delay = min(3600, 300 * 2 ** min(failures - 1, 4))
                        set_state(
                            conn,
                            request_key,
                            {
                                "at": request.get("at", iso(now)),
                                "target": request.get("target", history_target(self.calendar, now)),
                                "failures": failures,
                                "retry_at": iso(now + timedelta(seconds=delay)),
                                "revision": request.get("revision", HISTORY_DOWNLOAD_REVISION),
                            },
                        )

    def ingest(self, key: str, result, now: datetime):
        with self.db.transaction() as conn:
            if key.startswith("minute5:"):
                from app.market_data.minute_bars import save_five_minute

                if result["symbol"] != key.split(":", 1)[1]:
                    raise DataError("5 分钟 K 标的不匹配")
                if result["symbol"] in tracked_instruments(conn):
                    save_five_minute(conn, result)
            elif key.startswith("buy_review:"):
                if result["as_of"] == history_target(self.calendar, now):
                    save_review(conn, result, self.calendar)
            elif key == "live_targets":
                if result["config_id"] == settings(conn)[0] and result["as_of"] == history_target(
                    self.calendar, now
                ):
                    set_state(conn, key, result)
            elif key == "market":
                # An obsolete in-flight directory response must never repopulate the manual list.
                return
            elif key in {"quotes", "fallback"}:
                tracked = tracked_instruments(conn)
                for quote in result:
                    if quote.symbol in tracked:
                        put_quote(conn, quote)
            elif key.startswith("profile:"):
                current = instruments(conn).get(result.symbol)
                if not current or result.symbol not in tracked_instruments(conn):
                    return
                result = replace(
                    result,
                    accounting_block=current.accounting_block,
                    active=current.active,
                    watched=current.watched,
                )
                put_instrument(conn, result)
                set_state(conn, key, {"at": iso(now)})
            elif key.startswith("history:"):
                symbol = key.split(":", 1)[1]
                if symbol not in tracked_instruments(conn):
                    return
                update = result if isinstance(result, HistoryUpdate) else None
                series = update.series if update else result
                if update:
                    if signature(stored_history(conn, symbol)) != update.base_signature:
                        raise DataError("history cache changed while downloading; retry with current cache")
                    validate_series(series, series["qfq"][-1].day)
                series = enrich_metrics(series, stored_history(conn, symbol))
                previous = get_state(conn, key, {})
                mode = update.mode if update else "full"
                for adjustment, bars in series.items():
                    if mode == "cached":
                        continue
                    if mode == "full":
                        conn.execute("DELETE FROM bars WHERE symbol=? AND adjustment=?", (symbol, adjustment))
                    else:
                        conn.execute(
                            "DELETE FROM bars WHERE symbol=? AND adjustment=? AND day<?",
                            (symbol, adjustment, bars[0].day),
                        )
                        bars = [b for b in bars if b.day >= update.write_from]
                    conn.executemany(
                        "INSERT INTO bars VALUES(?,?,?,?,?,?) ON CONFLICT(symbol,adjustment,day) DO UPDATE SET "
                        "payload=excluded.payload,source=excluded.source,fetched_at=excluded.fetched_at",
                        [(symbol, adjustment, b.day, dump(b.to_dict()), b.source, iso(now)) for b in bars],
                    )
                target = series["qfq"][-1].day
                set_state(
                    conn,
                    key,
                    {
                        "at": previous.get("at", iso(now)) if mode == "cached" else iso(now),
                        "target": target,
                        "mode": mode,
                        "turnover_revision": TURNOVER_DATA_REVISION,
                        "updated_bars": 0
                        if mode == "cached"
                        else sum(mode == "full" or b.day >= update.write_from for b in series["qfq"]),
                        "requested_bars": update.requested_bars if update else 320,
                        "reason": update.reason if update else "完整窗口下载",
                        "full_refreshed_day": target
                        if mode == "full"
                        else previous.get("full_refreshed_day", previous.get("target", target)),
                    },
                )
                conn.execute("DELETE FROM state WHERE key=?", (f"history_request:{symbol}",))
            elif key.startswith("metrics:"):
                symbol = key.split(":", 1)[1]
                if symbol not in tracked_instruments(conn):
                    return
                cached = stored_history(conn, symbol)
                if signature(cached) != result["base_signature"]:
                    raise DataError("价格历史已更新，辅助字段稍后重新核验")
                validate_series(result["series"], result["target"])
                enriched = enrich_metrics(cached, result["series"])
                changed = 0
                for adjustment, bars in enriched.items():
                    for old, bar in zip(cached[adjustment], bars):
                        if old == bar:
                            continue
                        changed += 1
                        conn.execute(
                            "UPDATE bars SET payload=? WHERE symbol=? AND adjustment=? AND day=?",
                            (dump(bar.to_dict()), symbol, adjustment, bar.day),
                        )
                if not changed and missing_metrics(enriched):
                    raise DataError("成交额／换手率等辅助字段仍不可用；价格历史已保留")
                pending_metrics = missing_metrics(enriched)
                set_state(conn, key, {"at": iso(now), "target": result["target"], "pending": pending_metrics})
                set_state(
                    conn,
                    f"metrics_request:{symbol}",
                    {
                        "at": iso(now),
                        "target": result["target"],
                        "failures": 0,
                        "retry_at": iso(now + timedelta(hours=1)),
                    },
                )
            elif key.startswith("actions:"):
                ingest_actions(conn, result)
                set_state(conn, key, {"at": iso(now)})
            conn.execute("DELETE FROM state WHERE key=?", (f"error:{key}",))
        if key in {"market", "fallback"}:
            self.db.log(key, "ok", f"更新 {len(result)} 条", now)

    def schedule(self, now: datetime):
        with self.db.connect() as conn:
            universe = tracked_instruments(conn)
            _, config = settings(conn)
            held = {row[0] for row in conn.execute("SELECT DISTINCT symbol FROM lots WHERE quantity>0")}
            pending = {
                row[0]
                for row in conn.execute(
                    "SELECT DISTINCT symbol FROM orders WHERE status IN ('pending','partial')"
                )
            }
            plan_row = conn.execute("SELECT payload FROM plans ORDER BY id DESC LIMIT 1").fetchone()
            selected = set(json.loads(plan_row[0])["targets"]) if plan_row else set()
            if config.execution_mode == "intraday":
                selected = set(get_state(conn, "live_targets", {}).get("targets", {}))
            priority = held | pending | selected
            from app.market_data.minute_bars import fetch_five_minute
            from app.strategies.minute_t import enabled as minute_t_enabled

            if config.intraday_t_enabled and minute_t_enabled(config) and self.calendar.session(now):
                # Closed positions sold today still need reference levels on their
                # charts. This observation universe does not change trading targets.
                minute_symbols = held | pending | sold_today(conn, now)
                known = instruments(conn)
                capacity = 2 - sum(k.startswith("minute5:") for k in self.futures)
                for symbol in sorted(
                    minute_symbols, key=lambda s: (self.attempts.get(f"minute5:{s}", -1e12), s)
                ):
                    if capacity <= 0:
                        break
                    if symbol in known and known[symbol].active and self.submit(
                        f"minute5:{symbol}", fetch_five_minute, self.provider, symbol, now, cooldown=30
                    ):
                        capacity -= 1
            interval = config.market_interval if self.calendar.session(now) else 900
            if universe:
                self.submit("fallback", self.provider.quotes, universe, now, cooldown=interval)
            if priority and self.calendar.session(now):
                self.submit(
                    "quotes",
                    self.provider.quotes,
                    {s: universe[s] for s in priority if s in universe},
                    now,
                    cooldown=config.holding_interval,
                )
            profiles_running = sum(k.startswith("profile:") for k in self.futures)
            actions_running = sum(k.startswith("actions:") for k in self.futures)
            for instrument in sorted(
                universe.values(), key=lambda i: (i.symbol not in priority, not i.tradable, i.symbol)
            ):
                if not instrument.active:
                    continue
                profile = get_state(conn, f"profile:{instrument.symbol}", {})
                profile_interval = (
                    timedelta(minutes=5)
                    if not instrument.verified or not instrument.index_id
                    else timedelta(days=7)
                )
                if profiles_running < 3 and (
                    not profile.get("at") or now - dt(profile["at"]) >= profile_interval
                ):
                    if self.submit(
                        f"profile:{instrument.symbol}", self.provider.profile, instrument, now, cooldown=300
                    ):
                        profiles_running += 1
                if instrument.symbol in priority:
                    actions = get_state(conn, f"actions:{instrument.symbol}", {})
                    if actions_running < 1 and (
                        not actions.get("at") or dt(actions["at"]).date() != now.date()
                    ):
                        if self.submit(
                            f"actions:{instrument.symbol}",
                            self.provider.actions,
                            instrument.symbol,
                            cooldown=300,
                        ):
                            actions_running += 1
        self.schedule_histories(universe, priority, now)
        self.schedule_metrics(universe, now)

    def schedule_metrics(self, universe, now):
        # One optional request at a time, after price jobs have been dispatched.
        if any(k.startswith(("history:", "metrics:")) for k in self.futures):
            return
        target = history_target(self.calendar, now)
        if not target:
            return
        with self.db.connect() as conn:
            requests = {
                i.symbol: get_state(conn, f"metrics_request:{i.symbol}", {}) for i in universe.values()
            }
            for instrument in sorted(
                universe.values(), key=lambda i: (requests[i.symbol].get("at", ""), i.symbol)
            ):
                if not instrument.active:
                    continue
                request = requests[instrument.symbol]
                if (
                    request.get("target") == target
                    and request.get("retry_at")
                    and dt(request["retry_at"]) > now
                ):
                    continue
                cached = stored_history(conn, instrument.symbol)
                if not cached["raw"] or cached["raw"][-1].day != target or not missing_metrics(cached):
                    continue
                if not self.submit(f"metrics:{instrument.symbol}", self.download_metrics, instrument, target):
                    continue
                with self.db.transaction() as writer:
                    set_state(
                        writer,
                        f"metrics_request:{instrument.symbol}",
                        {
                            "at": iso(now),
                            "target": target,
                            "failures": request.get("failures", 0) if request.get("target") == target else 0,
                            "retry_at": iso(now + timedelta(minutes=5)),
                        },
                    )
                break

    def download_metrics(self, instrument, target):
        with self.db.connect() as conn:
            cached = stored_history(conn, instrument.symbol)
        return {
            "target": target,
            "base_signature": signature(cached),
            "series": self.provider.history_metrics(
                instrument, target, start=cached["raw"][0].day, limit=320
            ),
        }

    def schedule_histories(self, universe, priority, now):
        target = history_target(self.calendar, now)
        capacity = 3 - sum(key.startswith("history:") for key in self.futures)
        if not target or capacity <= 0 or time.monotonic() < self.history_cooldown_until:
            return
        with self.db.connect() as conn:
            records = {
                row["key"]: json.loads(row["value"])
                for row in conn.execute(
                    "SELECT key,value FROM state WHERE key LIKE 'history:%' "
                    "OR key LIKE 'history_request:%' OR key LIKE 'error:history:%'"
                )
            }

        def previous(symbol):
            # Existing installations retain their last failed attempt's place in the queue.
            return records.get(f"history_request:{symbol}", records.get(f"error:history:{symbol}", {}))

        # Rotate through untouched/oldest attempts within each priority class. Failed low codes
        # must not starve the rest of the universe when a full pass takes over five minutes.
        queue = sorted(
            universe.values(),
            key=lambda i: (
                i.symbol not in priority,
                not i.tradable,
                previous(i.symbol).get("at", ""),
                i.symbol,
            ),
        )
        for instrument in queue:
            symbol = instrument.symbol
            key = f"history:{symbol}"
            if not instrument.active or key in self.futures or (records.get(key, {}).get("target") == target):
                continue
            request = previous(symbol)
            same_target = (
                request.get("target") == target and request.get("revision") == HISTORY_DOWNLOAD_REVISION
            )
            if same_target and request.get("retry_at") and dt(request["retry_at"]) > now:
                continue
            if not self.submit(key, self.download_history, instrument, target, cooldown=0):
                continue
            with self.db.transaction() as conn:
                set_state(
                    conn,
                    f"history_request:{symbol}",
                    {
                        "at": iso(now),
                        "target": target,
                        "revision": HISTORY_DOWNLOAD_REVISION,
                        "failures": request.get("failures", 0) if same_target else 0,
                        # A restart during an in-flight read waits before reissuing that request.
                        "retry_at": iso(now + timedelta(minutes=5)),
                    },
                )
            capacity -= 1
            if not capacity:
                break

    def download_history(self, instrument, target):
        # Read a bounded cache snapshot outside the trading loop; the ingest transaction
        # checks its signature again before committing both adjustments together.
        with self.db.connect() as conn:
            cached = stored_history(conn, instrument.symbol)
            state = get_state(conn, f"history:{instrument.symbol}", {})
        return self.provider.update_history(
            instrument,
            target,
            cached,
            self.calendar,
            state.get("full_refreshed_day", state.get("target", "")),
            refresh_turnover=False,
        )

    def plan(self, now: datetime):
        as_of = history_target(self.calendar, now)
        if not as_of:
            return
        try:
            execute = self.calendar.next(datetime.fromisoformat(as_of).date())
        except ValueError:
            return
        with self.db.transaction() as conn:
            status = get_state(conn, "readiness", {})
            # No partial-universe strategy freezing during initial data warmup.
            reasons = [r for r in status.get("reasons", []) if r != "有效行情覆盖率不足"]
            if reasons or not status.get("tradable_count"):
                return
            config_id, config = settings(conn)
            if conn.execute(
                "SELECT 1 FROM plans WHERE as_of=? AND config_id=?", (as_of, config_id)
            ).fetchone():
                return
            universe = tracked_instruments(conn)
            histories = {}
            symbols = list(universe)
            for row in conn.execute(
                f"SELECT symbol,payload FROM bars WHERE symbol IN ({','.join('?' for _ in symbols)}) AND adjustment='qfq' AND day<=? ORDER BY symbol,day",
                (*symbols, as_of),
            ):
                if row["symbol"] in universe:
                    histories.setdefault(row["symbol"], []).append(Bar(**json.loads(row["payload"])))
            held = {r[0] for r in conn.execute("SELECT DISTINCT symbol FROM lots WHERE quantity>0")}
            builder = build_plan
            if config.strategy_model == "price_action":
                from app.strategies.price_action import build_plan as builder
            plan = builder(list(universe.values()), histories, held, as_of, execute, config)
            evidence = dump(
                {
                    "instruments": [i.to_dict() for i in universe.values()],
                    "histories": {s: [b.to_dict() for b in bars] for s, bars in histories.items()},
                    "holdings": sorted(held),
                    "config": config.model_dump(mode="json"),
                    "focus_rules": FOCUS_RULES,
                    "focus_liquidity_rules": LIQUIDITY_RULES,
                    "observation_classification": CLASSIFICATION_RULES,
                }
            ).encode()
            plan["input_sha256"] = hashlib.sha256(evidence).hexdigest()
            cursor = conn.execute(
                "INSERT INTO plans(as_of,execute_day,config_id,at,payload) VALUES(?,?,?,?,?)",
                (as_of, execute, config_id, iso(now), dump(plan)),
            )
            conn.execute("INSERT INTO plan_inputs VALUES(?,?)", (cursor.lastrowid, zlib.compress(evidence)))
        self.db.log(
            "strategy", "ok", f"{as_of} 信号已冻结，目标 {len(plan['targets'])} 只，执行日 {execute}", now
        )

    def schedule_targets(self, now):
        if not self.calendar.session(now) or "live_targets" in self.futures:
            return
        as_of = history_target(self.calendar, now)
        if not as_of:
            return
        with self.db.connect() as conn:
            config_id, config = settings(conn)
        version = (as_of, config_id)
        if version == self.target_version and time.monotonic() - self.last_targets < config.market_interval:
            return
        self.futures["live_targets"] = self.target_pool.submit(calculate_targets, self.db, as_of, now)
        self.last_targets = time.monotonic()
        self.target_version = version

    def schedule_reviews(self, now):
        # Run after the 15:30 completed-bar sync; recover the last completed
        # session on an overnight/weekend restart without requiring an open UI.
        if self.calendar.is_open(now.date()) and clock(9, 30) <= now.time() < clock(15, 30):
            return
        as_of = history_target(self.calendar, now)
        if not as_of:
            return
        capacity = 2 - sum(key.startswith("buy_review:") for key in self.futures)
        if capacity <= 0:
            return
        with self.db.connect() as conn:
            for instrument in instruments(conn).values():
                if not instrument.watched:
                    continue
                request = review_request(conn, instrument, as_of, self.calendar)
                row = conn.execute(
                    "SELECT input_sha256 FROM buy_reviews WHERE symbol=? AND as_of=?",
                    (instrument.symbol, as_of),
                ).fetchone()
                if row and row[0] == fingerprint(request):
                    continue
                if self.submit(f"buy_review:{instrument.symbol}", calculate_review, request, now, cooldown=60):
                    capacity -= 1
                    if capacity == 0:
                        break

    def tick(self, now: datetime | None = None, network=True):
        now = now or now_cn()
        if not self.lease(now):
            return
        self.collect(now)
        if network and time.monotonic() - self.last_schedule >= 1:
            self.schedule(now)
            self.last_schedule = time.monotonic()
        if time.monotonic() - self.last_ready >= 5 or not network:
            with self.db.transaction() as conn:
                status = readiness(conn, self.calendar, now)
                set_state(conn, "readiness", status)
                set_state(conn, "buy_ready", status["ready"])
            self.last_ready = time.monotonic()
            self.plan(now)
            if network:
                self.schedule_targets(now)
                self.schedule_reviews(now)
        with self.db.transaction() as conn:
            apply_actions(conn, now)
        self.engine.expire(now)
        self.engine.risk_check(now)
        with self.db.connect() as conn:
            plan = conn.execute(
                "SELECT id FROM plans WHERE execute_day=? ORDER BY id DESC LIMIT 1", (now.date().isoformat(),)
            ).fetchone()
        if plan:
            self.engine.rebalance(plan["id"], now)
        if time.monotonic() - self.last_intraday >= 5 or not network:
            self.intraday.tick(now)
            self.last_intraday = time.monotonic()
        self.engine.match(now)
        try:
            self.notifications.tick(now, deliver=network)
        except Exception:
            self.db.log("notification", "error", "通知队列处理异常，已提交的成交不受影响", now)
        if time.monotonic() - self.last_equity >= 60:
            self.engine.record_equity(now)
            self.last_equity = time.monotonic()
            if now.time() >= clock(15, 10) or not self.calendar.is_open(now.date()):
                with self.db.transaction() as conn:
                    retain_evidence(conn, now, self.calendar)

    def close(self):
        self.notifications.close()
        self.target_pool.shutdown(wait=True, cancel_futures=True)
        self.minute_pool.shutdown(wait=True, cancel_futures=True)
        self.pool.shutdown(wait=True, cancel_futures=True)
        self.provider.close()
        with self.db.transaction() as conn:
            conn.execute("DELETE FROM leases WHERE key='worker' AND owner=?", (self.owner,))
