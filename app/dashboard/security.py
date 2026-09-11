from __future__ import annotations

import hashlib
import os
import secrets
from datetime import timedelta

from app.core.types import iso
from app.storage.db import get_state, set_state


class RateLimited(ValueError):
    pass


class IncorrectPassword(ValueError):
    pass


def password_hash(password: str, salt: str) -> str:
    return hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()


def initialize_auth(db):
    with db.transaction() as conn:
        if get_state(conn, "admin_credential"):
            return
        password = os.environ.get("NIUNO3_ADMIN_PASSWORD")
        if not password:
            path = db.path.parent / "admin-token.txt"
            if path.exists():
                password = path.read_text(encoding="utf-8").removesuffix("\n")
            else:
                password = secrets.token_urlsafe(24)
                descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                    stream.write(password + "\n")
        if not password:
            raise ValueError("请输入管理员密码")
        salt = secrets.token_hex(16)
        set_state(conn, "admin_credential", {"salt": salt, "hash": password_hash(password, salt)})


def verify_password(conn, password, peer, now) -> bool:
    cutoff = iso(now - timedelta(minutes=10))
    conn.execute("DELETE FROM login_attempts WHERE at<?", (cutoff,))
    if conn.execute("SELECT COUNT(*) FROM login_attempts WHERE peer=?", (peer,)).fetchone()[0] >= 10:
        raise RateLimited("尝试过于频繁，请 10 分钟后重试")
    conn.execute("INSERT INTO login_attempts VALUES(?,?)", (peer, iso(now)))
    credential = get_state(conn, "admin_credential")
    valid = secrets.compare_digest(password_hash(password, credential["salt"]), credential["hash"])
    if valid:
        conn.execute("DELETE FROM login_attempts WHERE peer=?", (peer,))
    return valid


def authenticate(db, password, peer, now) -> str | None:
    with db.transaction() as conn:
        if not verify_password(conn, password, peer, now):
            return None
        conn.execute("DELETE FROM sessions WHERE expires_at<=?", (iso(now),))
        token = secrets.token_urlsafe(32)
        conn.execute(
            "INSERT INTO sessions VALUES(?,?)",
            (hashlib.sha256(token.encode()).hexdigest(), iso(now + timedelta(hours=12))),
        )
        return token


def change_password(db, current_password, new_password, token, peer, now) -> bool:
    """Commit credential, session revocation and audit together. Return bootstrap cleanup status."""
    if not new_password:
        raise ValueError("请输入新管理密钥")
    with db.transaction() as conn:
        # Recheck under the write lock: a concurrent rotation may already have revoked this session.
        if not authorized(conn, token, now):
            raise PermissionError("登录已失效，请重新登录")
        valid = verify_password(conn, current_password, peer, now)
        if valid:
            if new_password == current_password:
                raise ValueError("新管理密钥不能与当前密钥相同")
            salt = secrets.token_hex(16)
            set_state(
                conn,
                "admin_credential",
                {
                    "salt": salt,
                    "hash": password_hash(new_password, salt),
                    "changed_at": iso(now),
                },
            )
            conn.execute("DELETE FROM sessions")
            conn.execute(
                "INSERT INTO runs(task,at,status,detail) VALUES('auth:password',?,'ok',?)",
                (iso(now), "管理员修改管理密钥，全部登录会话已失效"),
            )
    # Reject only after committing the failed attempt, so retries cannot bypass throttling.
    if not valid:
        raise IncorrectPassword("当前管理密钥不正确")
    try:
        (db.path.parent / "admin-token.txt").unlink(missing_ok=True)
    except OSError:
        # The database is authoritative; an obsolete bootstrap file cannot restore the old key.
        return False
    return True


def authorized(conn, token, now):
    if not token:
        return False
    row = conn.execute(
        "SELECT expires_at FROM sessions WHERE token_hash=?", (hashlib.sha256(token.encode()).hexdigest(),)
    ).fetchone()
    return row is not None and row[0] > iso(now)
