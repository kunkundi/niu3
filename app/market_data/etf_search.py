"""Bounded name/code lookup; suggestions never change the manually managed ETF list."""

import re
import unicodedata

from app.core.types import symbol_for


def search_key(value: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value)).lower()


def local_suggestions(universe, query: str, limit: int = 10) -> list[dict]:
    key = search_key(query)
    if not key:
        return []
    matches = []
    for instrument in universe.values():
        if not instrument.active or not instrument.source.startswith("eastmoney:fundf10:"):
            continue
        symbol, name, index = (
            search_key(v) for v in (instrument.symbol, instrument.name, instrument.index_id)
        )
        if not any(key in value for value in (symbol, name, index)):
            continue
        rank = (
            0
            if key in (symbol, symbol[2:], name)
            else 1
            if symbol.startswith(key) or symbol[2:].startswith(key)
            else 2
            if name.startswith(key)
            else 3
        )
        matches.append(
            (rank, symbol, {"symbol": symbol, "name": instrument.name, "watched": instrument.watched})
        )
    return [row for _, _, row in sorted(matches, key=lambda value: value[:2])[:limit]]


def parse_suggestions(payload: dict, query: str, limit: int = 10) -> list[dict]:
    from app.market_data.providers import DataError

    table = payload.get("QuotationCodeTable") if isinstance(payload, dict) else None
    if not isinstance(table, dict) or table.get("Status") != 0 or not isinstance(table.get("Data"), list):
        raise DataError("ETF suggestions unavailable")
    result = {}
    prefix = query[:2].lower() if re.fullmatch(r"(?i)(sh|sz)\d*", query) else None
    for row in table["Data"]:
        if (
            not isinstance(row, dict)
            or row.get("Classify") != "Fund"
            or str(row.get("MktNum")) not in {"0", "1"}
        ):
            continue
        name = row.get("Name")
        if (
            not isinstance(name, str)
            or "etf" not in search_key(name)
            or "联接" in name
            or "lof" in search_key(name)
        ):
            continue
        exchange = "sh" if str(row["MktNum"]) == "1" else "sz"
        try:
            symbol = symbol_for(str(row.get("Code", "")), exchange)
        except ValueError:
            continue
        if prefix and prefix != exchange:
            continue
        if row.get("QuoteID") != f"{row['MktNum']}.{symbol[2:]}":
            continue
        result.setdefault(symbol, {"symbol": symbol, "name": name.strip()})
    return list(result.values())[:limit]
