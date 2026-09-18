"""Export market inputs only through a read-only SQLite connection; no account secrets."""

import argparse
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


def export(path, start, end):
    conn = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("BEGIN")
    instruments = [json.loads(r[0]) for r in conn.execute("SELECT payload FROM instruments")
                   if json.loads(r[0]).get("watched")]
    symbols = {i["symbol"] for i in instruments}
    def rows(table, clause, args):
        return [dict(r) for r in conn.execute(
            f"SELECT * FROM {table} WHERE {clause}", args) if r["symbol"] in symbols]
    result = {
        "exported_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
        "start": start, "end": end,
        "config": json.loads(conn.execute("SELECT payload FROM configs ORDER BY id DESC LIMIT 1").fetchone()[0]),
        "instruments": instruments,
        "bars": rows("bars", "day<=?", (end,)),
        "minutes": rows("minute_bars", "substr(at,1,10) BETWEEN ? AND ?", (start, end)),
        "quotes": rows("quotes", "substr(at,1,10) BETWEEN ? AND ? ORDER BY id", (start, end)),
        "actions": rows("actions", "ex_day BETWEEN ? AND ?", (start, end)),
    }
    conn.close()
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database")
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    args = parser.parse_args()
    print(json.dumps(export(args.database, args.start, args.end), ensure_ascii=False))
