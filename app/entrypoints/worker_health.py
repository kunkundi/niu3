import sys

from app.core.config import data_dir
from app.core.types import dt, now_cn
from app.storage.db import Database, get_state

if __name__ == "__main__":
    db = Database(data_dir() / "niuno3.sqlite3")
    with db.connect() as conn:
        heartbeat = get_state(conn, "worker_heartbeat", {})
        healthy = heartbeat.get("at") and (now_cn() - dt(heartbeat["at"])).total_seconds() < 30
    sys.exit(0 if healthy else 1)
