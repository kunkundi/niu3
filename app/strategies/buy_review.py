"""Persisted daily trigger reviews, isolated from orders."""

import hashlib
import json
import math

from app.core.types import iso
from app.storage.db import dump, get_state, instruments, settings
from app.strategies.causal_review import causal_review
from app.strategies.price_action import STRATEGY

VERSION = "buy-review-v3"


def numeric_price(value):
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def review_request(conn, instrument, as_of, calendar):
    _, config = settings(conn)
    bars = [
        json.loads(r[0])
        for r in conn.execute(
            "SELECT payload FROM bars WHERE symbol=? AND adjustment='qfq' AND day<=? ORDER BY day DESC LIMIT 260",
            (instrument.symbol, as_of),
        )
    ][::-1]
    return {
        "version": VERSION,
        "strategy": STRATEGY,
        "symbol": instrument.symbol,
        "as_of": as_of,
        "tick": instrument.tick / 1_000_000,
        "minimum_bars": config.minimum_bars,
        "minimum_rr": float(config.pa_min_rr),
        # Missing means enabled, preserving existing enabled-review fingerprints.
        **({"rr_enabled": False} if not config.pa_rr_enabled else {}),
        "window_days": [day for day in calendar.days if day <= as_of][-10:],
        "bars": [
            {
                "date": b["day"],
                **{key: numeric_price(b.get(key)) for key in ("open", "high", "low", "close")},
                "closed": True,
            }
            for b in bars
        ],
    }


def fingerprint(request):
    return hashlib.sha256(dump(request).encode()).hexdigest()


def calculate_review(request, now):
    return {
        **causal_review(request),
        "version": VERSION,
        "symbol": request["symbol"],
        "at": iso(now),
        "input_sha256": fingerprint(request),
        "input": request,
    }


def save_review(conn, result, calendar):
    instrument = instruments(conn).get(result["symbol"])
    if not instrument or not instrument.watched:
        return False
    current = review_request(conn, instrument, result["as_of"], calendar)
    if fingerprint(current) != result["input_sha256"]:
        return False
    conn.execute(
        "INSERT INTO buy_reviews VALUES(?,?,?,?,?) ON CONFLICT(symbol,as_of) DO UPDATE SET "
        "at=excluded.at,input_sha256=excluded.input_sha256,payload=excluded.payload",
        (result["symbol"], result["as_of"], result["at"], result["input_sha256"], dump(result)),
    )
    return True


def review_payload(conn, symbol, as_of, calendar):
    instrument = instruments(conn).get(symbol)
    if not instrument or not instrument.watched or not as_of:
        return None
    row = conn.execute("SELECT * FROM buy_reviews WHERE symbol=? AND as_of=?", (symbol, as_of)).fetchone()
    current = review_request(conn, instrument, as_of, calendar)
    expected = fingerprint(current)
    if row and row["input_sha256"] == expected:
        payload = json.loads(row["payload"])
        payload.pop("input", None)
        return payload
    # v2 already saved the same causal result. Preserve read-only access to
    # historical reviews without ever falling back to its retrospective markers.
    if row:
        previous = {**current, "version": "buy-review-v2"}
        previous.pop("strategy")
        payload = json.loads(row["payload"])
        causal = payload.get("causal", {})
        if (
            row["input_sha256"] == fingerprint(previous)
            and causal.get("mode") == "causal"
            and causal.get("strategy") == STRATEGY
        ):
            payload.pop("input", None)
            payload.pop("causal", None)
            return {**payload, **causal}
    error = get_state(conn, f"error:buy_review:{symbol}", {})
    return {
        "symbol": symbol,
        "as_of": as_of,
        "ready": False,
        "markers": [],
        "reason": "盘后复盘暂未完成，后台将重试。" if error else "等待盘后日 K 同步及十日买点分析。",
    }
