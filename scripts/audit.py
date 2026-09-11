"""Read-only account reconstruction and optional consistent SQLite online backup."""

import argparse
import json
import os
import sqlite3
from pathlib import Path

from app.core.config import data_dir
from app.core.types import now_cn
from app.trading.account import reconcile, snapshot


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, default=data_dir() / "niuno3.sqlite3")
    parser.add_argument("--backup", type=Path)
    args = parser.parse_args()
    source = sqlite3.connect(args.database.resolve().as_uri() + "?mode=ro", uri=True)
    source.row_factory = sqlite3.Row
    try:
        source.execute("BEGIN")
        problems = reconcile(source)
        account = snapshot(source, now_cn())
        print(
            json.dumps(
                {
                    "ok": not problems,
                    "errors": problems,
                    "cash": account["cash"],
                    "market_value": account["market_value"],
                    "receivable": account["receivable"],
                    "nav": account["nav"],
                    "valuation_stale": account["stale"],
                    "positions": [
                        {"symbol": p["symbol"], "quantity": p["quantity"], "cost": p["cost"]}
                        for p in account["positions"]
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        if args.backup:
            descriptor = os.open(args.backup, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(descriptor)
            target = sqlite3.connect(args.backup)
            try:
                source.backup(target)
            finally:
                target.close()
    finally:
        source.close()
    if problems:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
