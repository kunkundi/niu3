"""Causal five-minute sell-first T signals, independent of daily entry signals."""

import hashlib
import json

from app.core.types import dt, iso
from app.market_data.minute_bars import bar_number
from app.storage.db import dump, get_state

POLICY = "minute-t-v1"


def enabled(config):
    return config.intraday_t_model == "minute5"


def context(conn, symbol, now, tick):
    data = get_state(conn, f"minute5:{symbol}", {})
    base = {
        "ready": False,
        "policy": POLICY,
        "timeframe": "5m",
        "source": data.get("source"),
        "bar_at": data.get("as_of"),
        "fetched_at": data.get("fetched_at"),
    }
    if not data or data.get("symbol") != symbol:
        return {**base, "message": "等待后台采集真实 5 分钟 K"}
    bars = [
        b
        for b in data["bars"]
        if b["at"][:10] == now.date().isoformat() and 5 <= (now - dt(b["at"])).total_seconds()
    ]
    if not bars or not 0 <= (now - dt(data["fetched_at"])).total_seconds() <= 120:
        return {**base, "message": "5 分钟 K 数据过期，等待刷新"}
    if not 5 <= (now - dt(bars[-1]["at"])).total_seconds() <= 360:
        return {**base, "message": "等待本时段已完成的 5 分钟 K"}
    window = bars[-13:]
    if len(window) < 7:
        return {**base, "message": f"等待当日分钟结构积累 {len(window)}/7 根"}
    numbers = [bar_number(dt(b["at"])) for b in window]
    if any(a is None or b is None or b != a + 1 for a, b in zip(numbers, numbers[1:])):
        return {**base, "message": "5 分钟 K 存在缺口，等待连续完整数据"}
    if any(b["volume"] <= 0 for b in window):
        return {**base, "message": "5 分钟 K 成交量不足，等待有效成交"}
    prior, bar = window[:-1], window[-1]
    support, resistance = min(b["low"] for b in prior), max(b["high"] for b in prior)
    tolerance = max(tick, int(sum(b["high"] - b["low"] for b in prior) / len(prior) / 4))
    digest = hashlib.sha256(
        dump({"policy": POLICY, "source": data["source"], "bars": window}).encode()
    ).hexdigest()
    return {
        **base,
        "ready": True,
        "message": "5 分钟结构已就绪",
        "bar_at": bar["at"],
        "input_sha256": digest,
        "bars": window,
        "bar": bar,
        "support": support,
        "resistance": resistance,
        "tolerance": tolerance,
        "support_stop": support - 2 * tolerance,
    }


def sell_reference(conn, cycle):
    if not cycle:
        return None
    row = conn.execute(
        "SELECT payload FROM intraday_decisions WHERE order_id=?", (cycle["sell_order_id"],)
    ).fetchone()
    return json.loads(row[0]).get("minute_t") if row else None


def signal(conn, plan, instrument, quote, config, now, kind, cycle=None):
    from app.trading.engine import fill_price

    result = context(conn, instrument.symbol, now, instrument.tick)
    if not result["ready"]:
        return result
    result.update(ready=False, action=kind, message="等待压力附近转弱的 5 分钟 K")
    pa = next((r.get("pa", {}) for r in plan.get("rows", []) if r["symbol"] == instrument.symbol), {})
    if not pa.get("ready") or pa.get("action") == "exit":
        return {**result, "message": "日线结构未就绪或已触发退出"}
    if not quote.fresh(now, config.quote_max_age) or quote.status != "trading":
        return {**result, "message": "等待新鲜有效盘口"}
    bar = result["bar"]
    support, resistance, tolerance = result["support"], result["resistance"], result["tolerance"]
    if kind == "t_sell":
        if "下降" in pa.get("trend", ""):
            return {**result, "message": "日线为下降结构，暂不开新做 T 轮次"}
        if resistance - support <= 4 * tolerance:
            return {**result, "message": "盘中支撑压力空间不足"}
        reversal = (
            bar["high"] >= resistance - tolerance
            and bar["high"] <= resistance + 2 * tolerance
            and bar["close"] < bar["open"]
            and bar["close"] <= (bar["high"] + bar["low"]) / 2
            and bar["close"] >= resistance - 2 * tolerance
            and bar["low"] > result["support_stop"]
        )
        lower, upper = max(support + 2 * tolerance, bar["close"] - tolerance), resistance + 2 * tolerance
        executable = fill_price(quote, "SELL", instrument.tick)
    else:
        reference = sell_reference(conn, cycle)
        if not reference or reference.get("policy") != POLICY:
            return {**result, "message": "本轮缺少分钟卖出依据，等待轮次结束"}
        support, resistance, tolerance = (reference[k] for k in ("support", "resistance", "tolerance"))
        stop = reference["support_stop"]
        result.update(
            support=support,
            resistance=resistance,
            tolerance=tolerance,
            support_stop=stop,
            cycle_started_at=cycle["at"],
            sell_order_id=cycle["sell_order_id"],
        )
        # Check the whole observed path since selling, not just the latest candle.
        data = get_state(conn, f"minute5:{instrument.symbol}", {})
        crossed = any(
            b["at"] > cycle["at"] and b["at"] <= result["bar_at"] and b["low"] <= stop
            for b in data.get("bars", [])
        )
        if crossed or quote.ask <= stop:
            return {**result, "invalidated": True, "message": "本轮分钟支撑已失效，结束买回等待"}
        result["message"] = "已卖出底仓，等待冻结支撑附近企稳的 5 分钟 K"
        reversal = (
            bar["at"] > cycle["at"]
            and bar["low"] <= support + tolerance
            and bar["low"] > stop
            and bar["close"] > bar["open"]
            and bar["close"] >= (bar["high"] + bar["low"]) / 2
        )
        lower, upper = (
            max(stop + instrument.tick, support - tolerance),
            min(support + 2 * tolerance, bar["close"] + tolerance),
        )
        executable = fill_price(quote, "BUY", instrument.tick)
    result.update(price_min=lower, price_max=upper)
    if not reversal:
        return result
    if not lower <= executable <= upper:
        return {**result, "message": "分钟形态已确认，等待可成交价格进入执行区间"}
    return {
        **result,
        "ready": True,
        "message": "5 分钟压力转弱，卖出部分底仓"
        if kind == "t_sell"
        else "5 分钟支撑企稳，按实际已卖数量买回",
        "observed_at": iso(now),
    }


def execution_problem(conn, evidence, instrument, quote, config, now, kind):
    from app.trading.engine import fill_price

    if not evidence or evidence.get("policy") != POLICY or evidence.get("action") != kind:
        return "缺少有效的分钟做 T 决策依据"
    latest = context(conn, instrument.symbol, now, instrument.tick)
    if not latest["ready"]:
        return latest["message"]
    if latest["input_sha256"] != evidence.get("input_sha256"):
        return "5 分钟 K 已更新，等待新的分钟决策"
    price = fill_price(quote, "SELL" if kind == "t_sell" else "BUY", instrument.tick)
    if not evidence["price_min"] <= price <= evidence["price_max"]:
        return "盘口取整价格不在分钟做 T 执行区间"
    if kind == "t_buy" and quote.ask <= evidence["support_stop"]:
        return "本轮分钟支撑已失效"
    return ""
