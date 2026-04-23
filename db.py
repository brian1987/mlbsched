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
    conn.execute("""
        CREATE TABLE IF NOT EXISTS odds_cache (
            id                 INTEGER PRIMARY KEY AUTOINCREMENT,
            fetched_at         TEXT NOT NULL,
            data               TEXT NOT NULL,
            requests_remaining INTEGER
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_odds_fetched ON odds_cache (fetched_at)")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS weather_cache (
            location   TEXT PRIMARY KEY,
            fetched_at TEXT NOT NULL,
            data       TEXT NOT NULL
        )
    """)
    conn.commit()


def read_weather_cache(location: str) -> sqlite3.Row | None:
    rows = _conn().execute(
        "SELECT fetched_at, data FROM weather_cache WHERE location = ?",
        (location,),
    ).fetchall()
    return rows[0] if rows else None


def write_weather_cache(location: str, data_json: str):
    now = datetime.now(timezone.utc).isoformat()
    conn = _conn()
    conn.execute(
        "INSERT OR REPLACE INTO weather_cache (location, fetched_at, data) VALUES (?, ?, ?)",
        (location, now, data_json),
    )
    conn.commit()


def read_latest_odds_cache() -> sqlite3.Row | None:
    rows = _conn().execute(
        "SELECT fetched_at, data, requests_remaining FROM odds_cache ORDER BY id DESC LIMIT 1"
    ).fetchall()
    return rows[0] if rows else None


def write_odds_cache(data_json: str, requests_remaining: int | None):
    now = datetime.now(timezone.utc).isoformat()
    conn = _conn()
    conn.execute(
        "INSERT INTO odds_cache (fetched_at, data, requests_remaining) VALUES (?, ?, ?)",
        (now, data_json, requests_remaining),
    )
    conn.execute(
        "DELETE FROM odds_cache WHERE fetched_at < datetime('now', '-36 hours')"
    )
    conn.commit()


def read_earliest_odds_cache_since(iso_cutoff: str) -> sqlite3.Row | None:
    rows = _conn().execute(
        "SELECT fetched_at, data FROM odds_cache WHERE fetched_at >= ? ORDER BY id ASC LIMIT 1",
        (iso_cutoff,),
    ).fetchall()
    return rows[0] if rows else None


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
