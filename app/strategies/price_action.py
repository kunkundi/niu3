"""Completed daily price-action structures, with intraday price triggers.

The chart's JS engine is the single source of structural recognition. No quote is
turned into an OHLC candle. Optional statistics do not affect selection or signals.
"""

import hashlib
import json
import subprocess
from collections import OrderedDict
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR

from app.core.config import ROOT
from app.core.types import dec
from app.strategies.focus import FOCUS_POLICY, select_focus

STRATEGY = "price-action-v3"
_cache = OrderedDict()


def price_decision(pa, price, minimum_rr, rr_enabled=True):
    """A single observed price plus already completed structures; no future bars."""
    if not pa.get("ready"):
        return {"action": "hold", "reason": pa.get("reason", "完整日 K 结构未就绪")}
    price = float(price)
    if pa.get("exit") and price <= pa["exit"]:
        return {"action": "exit", "reason": f"裸 K 退出：跌破{pa['exit_setup']}低点"}
    if pa.get("structural_stop") and price <= pa["structural_stop"]:
        return {"action": "exit", "reason": "裸 K 退出：跌破已确认摆动低点"}
    if pa.get("entry") and pa["entry_stop"] > 0 and pa["entry"] <= price <= pa["entry_ceiling"]:
        rr = (pa["target"] - price) / max(price - pa["entry_stop"], 1e-9)
        allowed = not rr_enabled or rr >= float(minimum_rr)
        return {
            "action": "buy" if allowed else "hold",
            "reward_risk": rr,
            "reason": f"裸 K 入场：突破{pa['setup']}高点，潜在盈亏比 {rr:.2f}"
            if allowed
            else "距离结构目标过近，潜在盈亏比不足",
        }
    return {"action": "hold", "reason": f"{pa['trend']}；等待有效形态突破，持仓按结构管理"}


def analysis_request(history, as_of, tick, minimum_bars=120):
    # This trading adapter uses price patterns/levels, not the volume-based
    # reversal display layers. Enrichment must not invalidate a live setup.
    return {
        "policy": STRATEGY,
        "minimum_bars": minimum_bars,
        "as_of": as_of,
        "tick": tick / 1_000_000,
        "bars": [
            {
                "date": b.day,
                **{key: float(dec(getattr(b, key))) for key in ("open", "high", "low", "close")},
                "closed": True,
            }
            for b in history
        ],
    }


def input_hash(request):
    return hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest()


def analyze_many(requests):
    keys = [input_hash(r) for r in requests]
    missing = {key: request for key, request in zip(keys, requests) if key not in _cache}
    if missing:
        result = subprocess.run(
            ["node", str(ROOT / "scripts/price_action.mjs")],
            input=json.dumps(list(missing.values())),
            text=True,
            capture_output=True,
            timeout=45,
            check=True,
        )
        output = json.loads(result.stdout)
        if len(output) != len(missing):
            raise ValueError("裸 K 引擎返回数量异常")
        for key, row in zip(missing, output):
            _cache[key] = {**row, "input_sha256": key}
    result = [dict(_cache[key]) for key in keys]
    while len(_cache) > 512:
        _cache.popitem(last=False)
    return result


def build_plan(universe, histories, held, as_of, execute_day, config, cooldown=None, live_prices=None):
    focus = select_focus(universe, histories, as_of)
    tradable = {instrument.symbol for instrument in universe if instrument.tradable}
    rows, requests, analyzed = [], [], []
    for instrument in universe:
        row = {
            "symbol": instrument.symbol,
            "name": instrument.name,
            "index_id": instrument.index_id,
            "eligible": False,
            "selected": False,
            "target_weight": "0",
            "rank": None,
            "score": None,
            "reasons": [],
            **focus[instrument.symbol],
        }
        rows.append(row)
        if not (row["representative"] or instrument.symbol in held):
            row["reasons"].append(row["focus_reason"] or "不在自动交易范围")
            continue
        if not instrument.tradable:
            row["reasons"].append(instrument.accounting_block or "交易属性待核验，暂不自动买入")
        history = [b for b in histories.get(instrument.symbol, []) if b.day <= as_of][-config.history_bars :]
        requests.append(analysis_request(history, as_of, instrument.tick, config.minimum_bars))
        analyzed.append(row)
    for row, pa in zip(analyzed, analyze_many(requests)):
        row["pa"] = pa
        if not pa["ready"]:
            row["reasons"].append(pa["reason"])
            continue
        live = (live_prices or {}).get(row["symbol"])
        row["close"] = str(live) if live is not None else None
        if live is None:
            row["reasons"].append("等待有效盘中行情触发已完成日 K 结构")
            continue
        decision = price_decision(pa, live, config.pa_min_rr, config.pa_rr_enabled)
        pa.update({k: v for k, v in decision.items() if k != "reason"})
        row["reasons"].append(decision["reason"])
        if decision["action"] == "buy":
            row["eligible"] = (
                row["representative"]
                and row["symbol"] in tradable
                and row["symbol"] not in (cooldown or set())
            )
    targets = select_targets(rows, held, config)
    return {
        "strategy": STRATEGY,
        "focus_policy": FOCUS_POLICY,
        "as_of": as_of,
        "execute_day": execute_day,
        "rows": sorted(rows, key=lambda r: (r["rank"] or 100000, r["symbol"])),
        "targets": targets,
        "timeframe": "day",
        "trigger": "intraday_quote",
    }


def select_targets(rows, held, config):
    # An absent entry or a different liquidity leader must never force a holding out.
    for row in rows:
        row["rank"] = None
    selected = {r["symbol"] for r in rows if r["symbol"] in held and r.get("pa", {}).get("action") != "exit"}
    ranked = sorted(
        (r for r in rows if r["eligible"]),
        key=lambda r: (-r["pa"].get("reward_risk", 0), r["symbol"]),
    )
    for rank, row in enumerate(ranked, 1):
        row["rank"] = rank
        if len(selected) < config.max_positions:
            selected.add(row["symbol"])
    weight = min(config.max_weight, config.max_exposure / len(selected)) if selected else Decimal(0)
    for row in rows:
        row["selected"] = row["symbol"] in selected
        row["target_weight"] = str(weight if row["selected"] else 0)
    return {s: str(weight) for s in sorted(selected)}


def trigger_problem(pa, quote, config, kind, side):
    """Revalidate on the executable quote, in raw exchange price units."""
    if not pa or not pa.get("ready") or not pa.get("raw"):
        return "裸 K 结构或价格换算未就绪"
    levels = pa["raw"]
    if kind == "t_sell":
        valid = pa.get("t_allowed") and levels.get("resistance") and quote.bid >= levels["resistance"]
    elif kind == "t_buy":
        valid = (
            pa.get("t_allowed")
            and levels.get("support")
            and levels.get("t_stop")
            and levels["t_stop"] < quote.ask <= levels["support"]
        )
    elif side == "BUY":
        price = quote.ask
        valid = (
            pa.get("action") == "buy"
            and levels.get("entry")
            and levels.get("entry_stop")
            and levels["entry"] <= price <= levels["entry_ceiling"]
            and (
                not config.pa_rr_enabled
                or Decimal(levels["target"] - price) >= config.pa_min_rr * (price - levels["entry_stop"])
            )
        )
    else:
        valid = any(levels.get(key) and quote.bid <= levels[key] for key in ("exit", "structural_stop"))
    return "" if valid else "盘中价格尚未满足裸 K 结构触发条件"


def raw_levels(pa, factor, tick):
    upward = {"entry", "support"}
    keys = (
        "entry",
        "entry_stop",
        "entry_ceiling",
        "target",
        "exit",
        "structural_stop",
        "support",
        "resistance",
        "t_stop",
    )
    return {
        key: int(
            (dec(pa[key]) * factor * 1_000_000 / tick).to_integral_value(
                rounding=ROUND_CEILING if key in upward else ROUND_FLOOR
            )
        )
        * tick
        for key in keys
        if pa.get(key) is not None
    }
