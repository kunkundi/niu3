"""Deterministic acceptance stages, each executed in a fresh disposable Compose container."""

import argparse
import json
import os
from datetime import timedelta
from unittest.mock import Mock

from app.automation.service import Worker
from app.automation.targets import calculate_targets
from app.core.calendar import Calendar
from app.core.config import data_dir
from app.core.types import iso, units
from app.storage.db import Database, put_instrument, put_quote, set_state
from app.trading.account import reconcile, snapshot
from app.trading.engine import Engine
from tests.helpers import Fixture, at, candles


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=["buy", "hold", "exit", "verify"])
    stage = parser.parse_args().stage
    if os.environ.get("NIUNO3_ACCEPTANCE") != "1":
        raise SystemExit("Set NIUNO3_ACCEPTANCE=1; use only the niuno3-acceptance Compose project.")
    db = Database(data_dir() / "acceptance.sqlite3")
    engine = Engine(db, Calendar())
    fixture = Fixture()
    try:
        instrument = fixture.instrument
        start = at()
        end = at("2026-09-08T09:35:30")

        def quote(when, volume, price="1.007"):
            value = fixture.quote(when, volume=volume, price=price)
            with db.transaction() as conn:
                put_quote(conn, value)

        if stage == "buy":
            with db.connect() as conn:
                if conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]:
                    raise SystemExit("Acceptance volume already contains orders; use a fresh test project.")
            with db.transaction() as conn:
                put_instrument(conn, instrument)
                set_state(conn, "catalog", {"at": iso(start), "total": 1})
                set_state(conn, "profile:sh510300", {"at": iso(start)})
                set_state(conn, "actions:sh510300", {"at": iso(start)})
            worker = Worker(db, Calendar(), Mock())
            try:
                worker.ingest("history:sh510300", {"qfq": candles(), "raw": candles()}, start)
                quote(start - timedelta(seconds=10), 1_000_000)
                for seconds in (0, 60):
                    observed = start + timedelta(seconds=seconds)
                    quote(observed, 1_000_000)
                    worker.ingest("live_targets", calculate_targets(db, "2026-09-04", observed), observed)
                    worker.tick(observed, network=False)
                quote(start + timedelta(seconds=90), 4_000_000)
                worker.tick(start + timedelta(seconds=90), network=False)
            finally:
                worker.close()
        elif stage == "hold":
            engine.match(start + timedelta(seconds=100))
            with db.connect() as conn:
                assert conn.execute("SELECT COUNT(*) FROM fills").fetchone()[0] == 1
                account = snapshot(conn, start + timedelta(seconds=100))
                assert account["positions"][0]["available"] == 0
                assert not reconcile(conn)
        elif stage == "exit":
            with db.transaction() as conn:
                set_state(conn, "actions:sh510300", {"at": iso(end)})
            quote(end - timedelta(seconds=100), 1_000_000, ".940")
            engine.risk_check(end - timedelta(seconds=30))
            quote(end, 4_000_000, ".940")
            engine.match(end)
            engine.record_equity(end)
        elif stage == "verify":
            engine.match(end + timedelta(seconds=5))
            with db.connect() as conn:
                fills = conn.execute("SELECT * FROM fills ORDER BY id").fetchall()
                assert [r["side"] for r in fills] == ["BUY", "SELL"]
                assert not reconcile(conn)
                account = snapshot(conn, end)
                assert account["positions"] == []
                assert account["cash_units"] < units(100000)
                assert conn.execute("SELECT COUNT(*) FROM plans").fetchone()[0] == 1
                assert conn.execute("SELECT COUNT(*) FROM cooldown").fetchone()[0] == 1
        with db.connect() as conn:
            print(
                json.dumps(
                    {
                        "stage": stage,
                        "ok": True,
                        "fills": conn.execute("SELECT COUNT(*) FROM fills").fetchone()[0],
                        "reconciliation": reconcile(conn),
                    }
                )
            )
    finally:
        fixture.close()


if __name__ == "__main__":
    main()
