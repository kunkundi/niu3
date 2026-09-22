from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError

from app.automation.service import readiness, history_target
from app.automation.status import automation_status
from app.core.calendar import Calendar
from app.strategies.price_action import STRATEGY
from app.core.config import CONFIG_LABELS, ROOT, data_dir, DisplaySettings, REMOVED_POOL_FIELDS, RETIRED_RISK_FIELDS
from app.core.types import Instrument, iso, now_cn, units, yuan, dec, dt, symbol_for
from app.dashboard.security import (
    authenticate,
    authorized,
    initialize_auth,
    change_password,
    IncorrectPassword,
    RateLimited,
)
from app.storage.db import (
    Database,
    get_state,
    instruments,
    tracked_instruments,
    latest_quote,
    settings,
    display_settings,
)
from app.storage.focus import focus_snapshot
from app.strategies.buy_review import review_payload
from app.strategies.focus import FOCUS_POLICY
from app.trading.account import snapshot
from app.notifications.channels import NotificationError, send as send_notification
from app.notifications.config import NotificationPatch, TestInput, current, public_config, save_config
from app.notifications.service import test_channel
from app.market_data.intraday import IntradayService, expected_day, polling_schedule
from app.market_data.index_chart import IndexChartService
from app.market_data.providers import PublicProvider, DataError
from app.market_data.etf_search import local_suggestions, search_key
from app.dashboard.market_performance import calculate_performance, load_histories
from app.dashboard.quote_display import quote_display_state
from app.dashboard.post_close import post_close_payload, post_close_window
from app.dashboard.live_candle import live_candle_payload
from app.dashboard.trade_positions import annotate_trade_positions
from app.dashboard.trade_intraday import recorded_intraday
from app.dashboard.daily_returns import daily_returns

logger = logging.getLogger(__name__)


class LoginInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    password: str = Field(min_length=1)


class AddEtfInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(min_length=6, max_length=16)


class PasswordChangeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    current_password: SecretStr = Field(min_length=1)
    new_password: SecretStr = Field(min_length=1)
    confirm_password: SecretStr = Field(min_length=1)


def quote_payload(quote, now, calendar=None):
    data = quote.to_dict()
    for key in ("last", "previous_close", "bid", "ask", "amount", "upper", "lower", "open", "high", "low"):
        data[key] = yuan(data[key])
    data["change_pct"] = (quote.last / quote.previous_close - 1) if quote.previous_close else None
    data["stale"] = not quote.fresh(now)
    data["quality"] = (
        "stale" if data["stale"] else "valid" if quote.last > 0 and quote.status == "trading" else "unknown"
    )
    if calendar is not None:
        data["display"] = quote_display_state(quote, calendar, now)
    return data


def create_app(
    db: Database | None = None,
    calendar: Calendar | None = None,
    clock=now_cn,
    web_dist: Path | None = None,
    notification_sender=send_notification,
    intraday_service=None,
    index_chart_service=None,
    etf_provider=None,
) -> FastAPI:
    db = db or Database(data_dir() / "niuno3.sqlite3")
    calendar = calendar or Calendar()
    initialize_auth(db)
    intraday = intraday_service or IntradayService(calendar, db=db)
    index_chart = index_chart_service or IndexChartService()
    etf_data = etf_provider or PublicProvider()

    @asynccontextmanager
    async def lifespan(app):
        try:
            yield
        finally:
            intraday.close()
            index_chart.close()
            etf_data.close()

    app = FastAPI(
        title="NiuNo3 ETF Workbench",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.db = db

    @app.exception_handler(sqlite3.Error)
    async def database_error(request, exc):
        logger.error("Database unavailable on %s", request.url.path, exc_info=exc)
        return JSONResponse(
            {"detail": "服务数据暂时不可用，请检查服务存储状态后重试"},
            status_code=503,
            headers={"Cache-Control": "no-store"},
        )

    @app.exception_handler(Exception)
    async def server_error(request, exc):
        logger.error("Unhandled server error on %s", request.url.path, exc_info=exc)
        return JSONResponse(
            {"detail": "服务内部错误，请稍后重试"},
            status_code=500,
            headers={"Cache-Control": "no-store"},
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        if request.url.path.startswith("/api/v1/auth/"):
            # FastAPI's default error includes rejected input values; never echo credential fields.
            return JSONResponse({"detail": "密钥输入无效，请检查必填项及输入格式"}, status_code=422)
        return await request_validation_exception_handler(request, exc)

    @app.middleware("http")
    async def boundaries(request: Request, call_next):
        if request.method in {"POST", "PATCH", "DELETE", "PUT"}:
            if request.headers.get("x-niuno3-request") != "1":
                return JSONResponse({"detail": "缺少请求校验头"}, status_code=403)
            origin = request.headers.get("origin")
            if origin and origin.rstrip("/") != str(request.base_url).rstrip("/"):
                return JSONResponse({"detail": "来源不匹配"}, status_code=403)
            try:
                length = int(request.headers.get("content-length", "0") or 0)
            except ValueError:
                return JSONResponse({"detail": "请求长度无效"}, status_code=400)
            if length > 65536:
                return JSONResponse({"detail": "请求过大"}, status_code=413)
            if len(await request.body()) > 65536:
                return JSONResponse({"detail": "请求过大"}, status_code=413)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; object-src 'none'; frame-ancestors 'none'"
        )
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    def admin(request: Request):
        with db.connect() as conn:
            if not authorized(conn, request.cookies.get("niuno3_session"), clock()):
                raise HTTPException(401, "请先验证管理密码后再进行变更操作")

    @app.get("/healthz")
    def health():
        with db.connect() as conn:
            conn.execute("SELECT 1")
        return {"status": "ok", "service": "niuno3"}

    @app.get("/readyz")
    def ready():
        with db.connect() as conn:
            state = readiness(conn, calendar, clock())
        return JSONResponse(state, status_code=200 if state["ready"] else 503)

    @app.post("/api/v1/auth/login")
    def login(body: LoginInput, request: Request, response: Response):
        try:
            token = authenticate(
                db, body.password, request.client.host if request.client else "local", clock()
            )
        except ValueError as exc:
            raise HTTPException(429, str(exc)) from exc
        if not token:
            raise HTTPException(401, "密码或管理密钥不正确")
        response.set_cookie(
            "niuno3_session",
            token,
            httponly=True,
            samesite="strict",
            max_age=43200,
            secure=request.url.scheme == "https",
            path="/",
        )
        return {"authenticated": True}

    @app.get("/api/v1/auth/session")
    def session(request: Request):
        with db.connect() as conn:
            return {"authenticated": authorized(conn, request.cookies.get("niuno3_session"), clock())}

    @app.post("/api/v1/auth/logout", dependencies=[Depends(admin)])
    def logout(request: Request, response: Response):
        token_hash = hashlib.sha256(request.cookies["niuno3_session"].encode()).hexdigest()
        with db.transaction() as conn:
            conn.execute("DELETE FROM sessions WHERE token_hash=?", (token_hash,))
        response.delete_cookie("niuno3_session", path="/")
        return {"ok": True}

    @app.post("/api/v1/auth/password", dependencies=[Depends(admin)])
    def password_change(body: PasswordChangeInput, request: Request, response: Response):
        new_password = body.new_password.get_secret_value()
        if new_password != body.confirm_password.get_secret_value():
            raise HTTPException(422, "两次输入的新管理密钥不一致")
        try:
            cleaned = change_password(
                db,
                body.current_password.get_secret_value(),
                new_password,
                request.cookies.get("niuno3_session"),
                request.client.host if request.client else "local",
                clock(),
            )
        except PermissionError as exc:
            raise HTTPException(401, str(exc)) from exc
        except RateLimited as exc:
            raise HTTPException(429, str(exc)) from exc
        except IncorrectPassword as exc:
            raise HTTPException(400, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        response.delete_cookie("niuno3_session", path="/")
        return {
            "ok": True,
            "authenticated": False,
            "message": "管理密钥已修改，请使用新密钥重新登录",
            "warning": "旧初始化密钥文件未能清理，但已失效；请在数据目录手动清理" if not cleaned else "",
        }

    @app.get("/api/v1/status")
    def status():
        with db.connect() as conn:
            now = clock()
            data = readiness(conn, calendar, now)
            _, config = settings(conn)
            data.update(
                {
                    "session_open": calendar.session(now),
                    "display_day": expected_day(calendar, now),
                    "automation": automation_status(conn, calendar, now, data),
                    "strategy": "裸 K 价格行为",
                    "strategy_model": config.strategy_model,
                    "execution_mode": config.execution_mode,
                    "intraday_t_enabled": config.intraday_t_enabled,
                    "intraday_polling": polling_schedule(
                        calendar, clock(), display_settings(conn).intraday_interval
                    ),
                }
            )
            return data

    @app.get("/api/v1/etfs/suggestions")
    def etf_suggestions(q: str = Query("", max_length=60)):
        key = search_key(q)
        if not key:
            return {"items": [], "source": "local"}
        with db.connect() as conn:
            universe = instruments(conn)
            local = local_suggestions(universe, key)
        if local and (len(local) >= 10 or any(key in (r["symbol"], r["symbol"][2:]) for r in local)):
            return {"items": local, "source": "local"}
        try:
            rows = etf_data.suggest_etfs(key)
        except (DataError, ValueError, TypeError, KeyError):
            if local:
                return {"items": local, "source": "local"}
            raise HTTPException(503, "ETF 提示暂时不可用，可稍后重试或直接输入完整代码添加") from None
        merged = {row["symbol"]: row for row in local}
        for row in rows:
            merged.setdefault(row["symbol"], row)
        return {
            "items": [
                {**row, "watched": bool(universe.get(row["symbol"]) and universe[row["symbol"]].watched)}
                for row in list(merged.values())[:10]
            ],
            "source": "eastmoney",
        }

    @app.post("/api/v1/etfs", dependencies=[Depends(admin)])
    def add_etf(body: AddEtfInput):
        try:
            symbol = symbol_for(body.code)
        except ValueError:
            raise HTTPException(422, "请输入沪深 ETF 六位代码，例如 510300 或 159915") from None
        with db.connect() as conn:
            instrument = instruments(conn).get(symbol)
        if instrument and instrument.watched:
            return {"symbol": symbol, "added": False, "message": "该 ETF 已在手动名单中"}
        # The archived verified directory can resolve a requested code without a market-wide fetch.
        if (
            not instrument
            or not instrument.verified
            or not instrument.source.startswith("eastmoney:fundf10:")
        ):
            try:
                instrument = etf_data.profile(Instrument(symbol, symbol.upper()), clock())
            except DataError as exc:
                if str(exc) in {"profile is not an ETF", "fund profile identity mismatch"}:
                    raise HTTPException(
                        422, "未找到对应 ETF，请核对代码；不支持股票、LOF 或 ETF 联接基金"
                    ) from None
                raise HTTPException(503, "ETF 资料源暂时不可用，请稍后重试") from None
        added = db.add_etf(instrument, clock())
        return {
            "symbol": symbol,
            "name": instrument.name,
            "added": added,
            "message": "已添加，后台将自动获取该 ETF 的行情和日 K" if added else "该 ETF 已在手动名单中",
        }

    @app.delete("/api/v1/etfs/{symbol}", dependencies=[Depends(admin)])
    def remove_etf(symbol: str):
        try:
            symbol = symbol_for(symbol)
            removed = db.remove_etf(symbol, clock())
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None
        return {"removed": removed, "message": "已移除" if removed else "该 ETF 不在手动名单中"}

    @app.get("/api/v1/etfs")
    def etfs(
        q: str = "",
        category: str = "all",
        tradable: bool = False,
        recent_triggered: bool = False,
        sort: str = "change",
        offset: int = Query(0, ge=0),
        limit: int = Query(50, ge=1, le=100),
    ):
        now = clock()
        rows = []
        triggered_count = pending_count = 0
        with db.connect() as conn:
            conn.execute("BEGIN")
            complete_day = history_target(calendar, now)
            review_days = [day for day in calendar.days if complete_day and day <= complete_day][-10:]
            focus = focus_snapshot(conn, complete_day)
            universe = tracked_instruments(conn)
            histories = load_histories(conn, list(universe), complete_day)
            for instrument in universe.values():
                selection = focus["items"][instrument.symbol]
                if (
                    not instrument.watched
                    or q.lower()
                    not in (
                        instrument.symbol + instrument.name + instrument.index_id + selection["focus_label"]
                    ).lower()
                ):
                    continue
                if category != "all" and instrument.category != category:
                    continue
                if tradable and not instrument.tradable:
                    continue
                review = review_payload(conn, instrument.symbol, complete_day, calendar)
                ready = bool(review and review.get("ready"))
                triggered = bool(ready and review.get("markers"))
                triggered_count += triggered
                pending_count += not ready
                if recent_triggered and not triggered:
                    continue
                latest = latest_quote(conn, instrument.symbol)
                quote = quote_payload(latest[1], now, calendar) if latest else None
                rows.append(
                    {
                        **instrument.to_dict(),
                        **selection,
                        "tradable": instrument.tradable,
                        "quote": quote,
                        "performance": calculate_performance(
                            latest[1] if latest else None,
                            histories.get(instrument.symbol, {}),
                            calendar,
                            now,
                            complete_day,
                        ),
                    }
                )
        allowed = {
            "amount": lambda r: (r["quote"] or {}).get("amount"),
            "change": lambda r: r["performance"]["change_pct"],
            "price": lambda r: (r["quote"] or {}).get("last"),
            "amount20": lambda r: r["amount20"],
            "turnover20": lambda r: r["turnover20"],
            **{
                key: (lambda r, field=key: r["performance"][field])
                for key in ("low_change_pct", "high_change_pct", "return5", "return10", "return20")
            },
        }
        if sort == "symbol":
            rows.sort(key=lambda r: r["symbol"])
        else:
            # Sort before pagination; missing quotes stay last in both directions.
            metric = allowed.get(sort.removesuffix("_asc"), allowed["change"])
            direction = 1 if sort.endswith("_asc") else -1

            def ranking(row):
                value = metric(row)
                return (value is None, direction * dec(value or 0), row["symbol"])

            rows.sort(key=ranking)
        return {
            "total": len(rows),
            "offset": offset,
            "items": rows[offset : offset + limit],
            "at": iso(now),
            "recent_triggers": {
                "as_of": complete_day,
                "window_start": review_days[0] if review_days else None,
                "matched_count": triggered_count,
                "pending_count": pending_count,
            },
            "focus": {k: v for k, v in focus.items() if k != "items"},
        }

    @app.get("/api/v1/indices/sh000001/chart")
    def shanghai_chart():
        now = clock()
        return index_chart.get(history_target(calendar, now), now)

    @app.get("/api/v1/indices/sh000001/intraday")
    def shanghai_intraday(snapshot_retry: bool = False):
        # Display-only identity: never added to the ETF universe or trading tables.
        instrument = Instrument("sh000001", "上证指数", category="index", region="CN")
        with db.connect() as conn:
            interval = display_settings(conn).intraday_interval
        return {
            **intraday.get(instrument, clock(), interval=interval, snapshot_retry=snapshot_retry),
            "symbol": instrument.symbol,
            "name": instrument.name,
            "price_unit": "点",
            "price_digits": 2,
        }

    @app.get("/api/v1/etfs/{symbol}/intraday")
    def etf_intraday(symbol: str, snapshot_retry: bool = False, day: date | None = None):
        now = clock()
        with db.connect() as conn:
            conn.execute("BEGIN")
            instrument = tracked_instruments(conn).get(symbol)
            interval = display_settings(conn).intraday_interval
            if day is not None:
                if day > now.date() or not calendar.known(day):
                    raise HTTPException(422, "请选择已知的历史或当日交易日期")
                if instrument is None and conn.execute(
                    "SELECT 1 FROM fills WHERE symbol=? LIMIT 1", (symbol,)
                ).fetchone():
                    instrument = instruments(conn).get(symbol)
                if instrument is None:
                    raise HTTPException(404, "ETF 不存在")
                return {**recorded_intraday(conn, symbol, day, calendar, now), "name": instrument.name}
        if not instrument or not instrument.active:
            raise HTTPException(404, "ETF 不存在或已不活跃")
        # Network work holds no SQLite transaction and never updates trading quotes or daily bars.
        return {
            "symbol": symbol,
            **intraday.get(instrument, clock(), interval=interval, snapshot_retry=snapshot_retry),
        }

    @app.get("/api/v1/etfs/{symbol}/live-candle")
    def etf_live_candle(symbol: str):
        with db.connect() as conn:
            conn.execute("BEGIN")
            if symbol not in tracked_instruments(conn):
                raise HTTPException(404, "ETF 不存在或已不活跃")
            return live_candle_payload(conn, symbol, calendar, clock())

    @app.get("/api/v1/etfs/{symbol}")
    def etf(symbol: str):
        now = clock()
        complete_day = history_target(calendar, now)
        with db.connect() as conn:
            conn.execute("BEGIN")
            instrument = tracked_instruments(conn).get(symbol)
            history_only = instrument is None
            if history_only and conn.execute("SELECT 1 FROM orders WHERE symbol=? LIMIT 1", (symbol,)).fetchone():
                instrument = instruments(conn).get(symbol)
            if not instrument:
                raise HTTPException(404, "ETF 不存在")
            latest = latest_quote(conn, symbol)
            bars = [
                json.loads(row[0])
                for row in conn.execute(
                    "SELECT payload FROM bars WHERE symbol=? AND adjustment='qfq' AND day<=? ORDER BY day DESC LIMIT 250",
                    (symbol, complete_day or ""),
                )
            ]
            return {
                **instrument.to_dict(),
                **focus_snapshot(conn, complete_day)["items"].get(symbol, {}),
                "history_only": history_only,
                "tradable": instrument.tradable,
                "quote": quote_payload(latest[1], now, calendar) if latest else None,
                "performance": calculate_performance(
                    latest[1] if latest else None,
                    load_histories(conn, [symbol], complete_day).get(symbol, {}),
                    calendar,
                    now,
                    complete_day,
                ),
                "bars": list(reversed(bars)),
                "buy_review": review_payload(conn, symbol, complete_day, calendar),
                "adjustment": "qfq",
            }

    @app.get("/api/v1/signals")
    def signals(mode: str = Query("auto", pattern="^(auto|daily|live|post_close)$")):
        now = clock()
        with db.connect() as conn:
            conn.execute("BEGIN")
            config_id, config = settings(conn)
            waiting_post_close = False
            if mode in {"auto", "post_close"}:
                mode = "live"
                if mode == "live" and post_close_window(calendar, now):
                    preview = post_close_payload(conn, calendar, now, config_id, config)
                    if preview:
                        for signal in preview["rows"]:
                            latest = latest_quote(conn, signal["symbol"])
                            signal["quote"] = quote_payload(latest[1], now) if latest else None
                        return preview
                    waiting_post_close = True
            if mode == "live":
                payload = get_state(conn, "live_targets", {})
                # A live signal keeps the previous-day basis of its trading session.
                # The 15:30 history download cutoff must not erase the last session's display.
                session_day = expected_day(calendar, now)
                created, basis = None, None
                try:
                    basis = calendar.previous(date.fromisoformat(session_day)) if session_day else None
                    created = dt(payload["created_at"]) if payload.get("created_at") else None
                except (ValueError, TypeError):
                    pass
                valid = bool(
                    payload.get("id")
                    and payload.get("config_id") == config_id
                    and payload.get("focus_policy") == FOCUS_POLICY
                    and payload.get("strategy") == STRATEGY
                    and basis
                    and payload.get("as_of") == basis
                    and created
                    and created <= now
                    and created.date().isoformat() == session_day
                )
                focus = focus_snapshot(conn, basis if valid else history_target(calendar, now))
                summary = {k: v for k, v in focus.items() if k != "items"}
                running = calendar.session(now)
                session_snapshot = valid and not running
                heartbeat = get_state(conn, "worker_heartbeat", {})
                alive = bool(heartbeat.get("at") and 0 <= (now - dt(heartbeat["at"])).total_seconds() <= 30)
                stale = not valid or (now - created).total_seconds() > config.market_interval + 30
                update_state = (
                    "offline"
                    if not alive
                    else "waiting_session"
                    if not running
                    else "stale"
                    if stale
                    else "live"
                )
                message = {
                    "offline": "后台心跳中断，当前交易信号等待服务恢复。",
                    "waiting_session": f"非交易时段，显示 {created.strftime('%m-%d %H:%M:%S')} 的最近盘中结果；开盘后自动更新。"
                    if session_snapshot
                    else "非交易时段，等待生成盘中结果；开盘后自动更新交易信号。",
                    "stale": "当前交易信号正在更新，后台会自动重试。",
                    "live": "裸 K 自动交易已启用：完整日 K 确定结构，盘中触发并复核买卖。",
                }[update_state]
                if not valid:
                    payload = {"id": None, "rows": [], "targets": {}}
                if waiting_post_close:
                    message = "等待最新完整日 K 与当前参数的盘后分析；" + message
                for signal in payload["rows"]:
                    latest = latest_quote(conn, signal["symbol"])
                    signal["quote"] = quote_payload(latest[1], now) if latest else None
                return {
                    **payload,
                    "mode": mode,
                    "update_state": update_state,
                    "stale": stale,
                    "session_snapshot": session_snapshot,
                    "message": message,
                    "refresh_seconds": config.market_interval,
                    "focus": summary,
                    "execution_mode": config.execution_mode,
                    "strategy_model": config.strategy_model,
                    "execution": get_state(conn, "intraday_execution", {}),
                    "t_enabled": config.intraday_t_enabled,
                    "post_close_pending": waiting_post_close,
                }
            focus = focus_snapshot(conn, history_target(calendar, now))
            summary = {k: v for k, v in focus.items() if k != "items"}
            row = conn.execute("SELECT * FROM plans ORDER BY id DESC LIMIT 1").fetchone()
            if (
                not row
                or row["config_id"] != config_id
                or json.loads(row["payload"]).get("focus_policy") != FOCUS_POLICY
                or json.loads(row["payload"]).get("strategy") != STRATEGY
            ):
                return {
                    "id": None,
                    "rows": [],
                    "targets": {},
                    "message": "等待按手动 ETF 名单与当前参数生成策略计划",
                    "focus": summary,
                }
            payload = json.loads(row["payload"])
            current = row["as_of"] == history_target(calendar, now)
            execution_message = "当前由裸 K 盘中信号驱动模拟交易；收盘记录仅供参考，不直接执行。"
            # Live quotes are display data; keep the stored strategy plan and evidence frozen.
            for signal in payload["rows"]:
                latest = latest_quote(conn, signal["symbol"])
                signal["quote"] = quote_payload(latest[1], now) if latest else None
            return {
                "id": row["id"],
                "config_id": row["config_id"],
                "created_at": row["at"],
                **payload,
                "mode": "daily",
                "update_state": "current" if current else "waiting_history",
                "message": "收盘目标每天 15:30 起随日 K 同步自动生成，盘中保持冻结。"
                if current
                else "最新完整日 K 同步中，完成后自动生成新计划。",
                "execution_message": execution_message,
                "execution_mode": config.execution_mode,
                "focus": summary,
            }

    @app.get("/api/v1/signals/history")
    def signal_history(before_id: int | None = Query(None, ge=1), limit: int = Query(20, ge=1, le=100)):
        # Immutable plans across configuration versions; never attach today's quotes.
        with db.connect() as conn:
            records = conn.execute(
                "SELECT * FROM plans WHERE (? IS NULL OR id<?) ORDER BY id DESC LIMIT ?",
                (before_id, before_id, limit + 1),
            ).fetchall()
        items = []
        for row in records[:limit]:
            payload = json.loads(row["payload"])
            targets = payload.get("targets", {})
            items.append(
                {
                    "id": row["id"],
                    "as_of": row["as_of"],
                    "created_at": row["at"],
                    "config_id": row["config_id"],
                    "strategy": payload.get("strategy"),
                    "selected_count": len(targets),
                    "target_exposure": str(sum((dec(value) for value in targets.values()), dec(0))),
                }
            )
        return {"items": items, "next_before_id": items[-1]["id"] if len(records) > limit else None}

    @app.get("/api/v1/signals/history/{plan_id}")
    def signal_history_detail(plan_id: int):
        with db.connect() as conn:
            row = conn.execute("SELECT * FROM plans WHERE id=?", (plan_id,)).fetchone()
        if not row:
            raise HTTPException(404, "收盘记录不存在")
        return {
            **json.loads(row["payload"]),
            "id": row["id"],
            "as_of": row["as_of"],
            "config_id": row["config_id"],
            "created_at": row["at"],
            "mode": "archive",
        }

    @app.get("/api/v1/signals/{symbol}/chart")
    def signal_chart(
        symbol: str,
        mode: str = Query("auto", pattern="^(auto|daily|live|post_close)$"),
        signal_id: str | None = Query(None, max_length=100),
    ):
        from app.dashboard.signal_chart import chart_payload

        plan = signals(mode)
        if not plan.get("id") or (signal_id is not None and str(plan["id"]) != signal_id):
            raise HTTPException(409, "策略信号已更新，请刷新后查看 K 线")
        try:
            with db.connect() as conn:
                conn.execute("BEGIN")
                return chart_payload(conn, plan, symbol, calendar)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/api/v1/t-strategy")
    def t_strategy():
        from app.dashboard.minute_t import minute_t_payload

        with db.connect() as conn:
            conn.execute("BEGIN")
            return minute_t_payload(conn, calendar, clock(), settings(conn)[1])

    @app.get("/api/v1/account")
    def account():
        with db.connect() as conn:
            conn.execute("BEGIN")
            now = clock()
            data = snapshot(conn, now)
            initial_cash = units(settings(conn)[1].initial_cash)
            data["initial_cash"] = yuan(initial_cash)
            data["total_pnl"] = yuan(data["nav_units"] - initial_cash)
            data["display_day"] = expected_day(calendar, now)
            data["daily_return"] = daily_returns(conn, calendar, now, data)
            data["equity"] = [
                {"at": r["at"], "nav": yuan(r["nav"]), "stale": bool(r["stale"])}
                for r in conn.execute(
                    "SELECT * FROM equity WHERE at IN (SELECT MAX(at) FROM equity "
                    "WHERE day<=? AND at<=? GROUP BY day) ORDER BY at DESC LIMIT 365",
                    (data["display_day"] or iso(now)[:10], iso(now)),
                )
            ][::-1]
            data["fees"] = yuan(conn.execute("SELECT COALESCE(SUM(fee),0) FROM fills").fetchone()[0])
            data["dividends"] = yuan(
                conn.execute(
                    "SELECT COALESCE(SUM(delta),0) FROM cash_ledger WHERE kind='dividend'"
                ).fetchone()[0]
            )
            return data

    @app.get("/api/v1/orders")
    def orders(offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=200)):
        with db.connect() as conn:
            conn.execute("BEGIN")
            return {
                "items": [
                    dict(r)
                    for r in conn.execute(
                        "SELECT o.*,json_extract(i.payload,'$.name') AS name FROM orders o "
                        "LEFT JOIN instruments i ON i.symbol=o.symbol ORDER BY o.id DESC LIMIT ? OFFSET ?",
                        (limit, offset),
                    )
                ],
                "total": conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0],
            }

    @app.get("/api/v1/trades")
    def trades(
        offset: int = Query(0, ge=0),
        limit: int = Query(100, ge=1, le=200),
        symbol: str | None = Query(None, pattern=r"^(sh|sz)\d{6}$"),
    ):
        with db.connect() as conn:
            conn.execute("BEGIN")
            where = " WHERE f.symbol=?" if symbol else ""
            params = (symbol,) if symbol else ()
            items = [
                dict(r)
                for r in conn.execute(
                    "SELECT f.*,o.reason,o.kind AS order_kind,json_extract(i.payload,'$.name') AS name "
                    "FROM fills f JOIN orders o ON f.order_id=o.id "
                    "LEFT JOIN instruments i ON i.symbol=f.symbol"
                    + where + " ORDER BY f.id DESC LIMIT ? OFFSET ?",
                    (*params, limit, offset),
                )
            ]
            annotate_trade_positions(conn, items)
            for item in items:
                for key in ("price", "gross", "fee", "realized"):
                    item[key] = yuan(item[key])
            total = conn.execute("SELECT COUNT(*) FROM fills f" + where, params).fetchone()[0]
            return {"items": items, "total": total}

    @app.get("/api/v1/actions")
    def actions():
        with db.connect() as conn:
            # Account history is based on entitlement-date holdings, not today's
            # positions or the payout date. Filter before limiting the result.
            items = [
                dict(r)
                for r in conn.execute(
                    "SELECT a.* FROM actions a WHERE "
                    "(a.kind='dividend' AND (a.entitlement>0 OR a.receivable>0 OR "
                    "COALESCE((SELECT SUM(p.delta) FROM position_ledger p "
                    "WHERE p.symbol=a.symbol AND substr(p.at,1,10)<=a.record_day),0)>0)) "
                    "OR (a.kind='split' AND ("
                    "EXISTS(SELECT 1 FROM position_ledger p WHERE p.key='action:'||a.id "
                    "AND p.symbol=a.symbol AND p.kind='split' AND p.delta<>0) OR "
                    "COALESCE((SELECT SUM(p.delta) FROM position_ledger p "
                    "WHERE p.symbol=a.symbol AND substr(p.at,1,10)<a.ex_day),0)>0)) "
                    "ORDER BY a.ex_day DESC,a.id DESC LIMIT 200"
                )
            ]
            for item in items:
                item["entitlement"] = yuan(item["entitlement"])
                item["receivable"] = yuan(item["receivable"])
            return {"items": items}

    @app.get("/api/v1/runs", dependencies=[Depends(admin)])
    def runs(limit: int = Query(100, ge=1, le=200)):
        with db.connect() as conn:
            return {
                "items": [
                    dict(r) for r in conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,))
                ],
                "reconciliation": get_state(conn, "reconciliation", {}),
                "sources": [
                    json.loads(r[0])
                    for r in conn.execute("SELECT value FROM state WHERE key LIKE 'error:%' LIMIT 20")
                ],
            }

    @app.get("/api/v1/config", dependencies=[Depends(admin)])
    def config():
        with db.connect() as conn:
            version, value = settings(conn)
            return {
                "version": version,
                "values": {
                    **value.model_dump(mode="json", exclude=REMOVED_POOL_FIELDS),
                    **display_settings(conn).model_dump(mode="json"),
                },
                "labels": CONFIG_LABELS,
                "initial_cash_locked": bool(conn.execute("SELECT 1 FROM orders LIMIT 1").fetchone()),
                "execution_window": "交易日 09:30—11:30、13:00—15:00",
                "stamp_duty": 0,
                "transfer_fee": 0,
            }

    @app.patch("/api/v1/config", dependencies=[Depends(admin)])
    def change_config(payload: dict):
        if REMOVED_POOL_FIELDS.intersection(payload):
            raise HTTPException(422, "自动筛选已移除，请在 ETF 页面手动管理名单")
        if RETIRED_RISK_FIELDS.intersection(payload):
            raise HTTPException(422, "组合回撤保护已移除，不能再配置该参数")
        try:
            version = db.change_config(payload, clock())
        except (ValueError, ValidationError) as exc:
            raise HTTPException(422, str(exc)) from exc
        display_only = bool(payload) and all(key in DisplaySettings.model_fields for key in payload)
        db.log("config", "ok", "分时获取频率已更新" if display_only else f"参数更新至版本 {version}", clock())
        return {
            "version": version,
            "message": "分时获取频率已保存，交易目标与订单保持不变。"
            if display_only
            else "已保存。后台自动按新参数计算目标；原参数的普通待成交订单已取消，成交记录保持原费用。",
        }

    @app.get("/api/v1/notifications/config", dependencies=[Depends(admin)])
    def notification_config():
        with db.connect() as conn:
            return public_config(current(conn))

    @app.patch("/api/v1/notifications/config", dependencies=[Depends(admin)])
    def notification_save(payload: dict):
        try:
            return save_config(db, NotificationPatch.model_validate(payload), clock())
        except ValidationError:
            raise HTTPException(422, "通知设置格式无效；超时应为 1–30 秒，请检查渠道字段") from None
        except NotificationError as exc:
            raise HTTPException(422, str(exc)) from None

    @app.post("/api/v1/notifications/test/{channel}", dependencies=[Depends(admin)])
    def notification_test(channel: str, payload: dict):
        try:
            return test_channel(db, channel, TestInput.model_validate(payload), clock(), notification_sender)
        except ValidationError:
            raise HTTPException(422, "测试设置格式无效；超时应为 1–30 秒，请检查渠道字段") from None
        except NotificationError as exc:
            raise HTTPException(422, str(exc)) from None

    @app.get("/api/v1/notifications/history", dependencies=[Depends(admin)])
    def notification_history(limit: int = Query(50, ge=1, le=100)):
        with db.connect() as conn:
            return {
                "items": [
                    dict(row)
                    for row in conn.execute(
                        "SELECT id,channel,config_version,kind,fill_ids,message,status,at,updated_at,error "
                        "FROM notification_deliveries ORDER BY id DESC LIMIT ?",
                        (limit,),
                    )
                ]
            }

    dist = web_dist or ROOT / "web/dist"
    if (dist / "assets").exists():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")
    for path in ("/", "/market", "/signals", "/account", "/settings"):

        def page():
            if not (dist / "index.html").exists():
                raise HTTPException(503, "前端尚未构建，请运行 pnpm --dir web build")
            return FileResponse(dist / "index.html")

        app.add_api_route(path, page, methods=["GET"], include_in_schema=False)
    return app
