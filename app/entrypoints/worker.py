from __future__ import annotations

import signal
import threading

from app.automation.service import Worker
from app.core.config import data_dir
from app.storage.db import Database


def main():
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    db = Database(data_dir() / "niuno3.sqlite3")
    worker = Worker(db)
    try:
        while not stop.is_set():
            try:
                worker.tick()
            except Exception as exc:
                db.log("worker", "error", type(exc).__name__)
            stop.wait(1)
    finally:
        worker.close()


if __name__ == "__main__":
    main()
