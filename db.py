"""Request logging to SQLite — long-term metrics storage."""

import hashlib
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).parent / "metrics.db"

_local = threading.local()


def _conn() -> sqlite3.Connection:
    if not hasattr(_local, "conn"):
        _local.conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _local.conn.row_factory = sqlite3.Row
    return _local.conn


def init_db():
    conn = _conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS requests (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            ts         TEXT NOT NULL,
            date       TEXT NOT NULL,
            path       TEXT NOT NULL,
            client     TEXT NOT NULL,
            ip_hash    TEXT NOT NULL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_date ON requests (date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_path ON requests (path)")
    conn.commit()


def log_request(path: str, ip: str, user_agent: str):
    now = datetime.now(timezone.utc)
    ip_hash = hashlib.sha256(ip.encode()).hexdigest()[:16]
    client = "curl" if user_agent.lower().startswith("curl") else "browser"
    conn = _conn()
    conn.execute(
        "INSERT INTO requests (ts, date, path, client, ip_hash) VALUES (?, ?, ?, ?, ?)",
        (now.isoformat(), now.strftime("%Y-%m-%d"), path, client, ip_hash),
    )
    conn.commit()


def query(sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    return _conn().execute(sql, params).fetchall()
