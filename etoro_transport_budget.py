"""Credential-scoped eToro HTTP windows, shared across local processes.

Only a hash of the credential scope reaches SQLite. Broker retry deadlines
survive restarts; every actual HTTP attempt obtains its own reservation.
"""
from __future__ import annotations

import os
from contextlib import closing
from pathlib import Path
import sqlite3
import time


def _path() -> Path:
    root = os.getenv("TRADINGBOT_TEST_STATE_DIR", "").strip()
    return (Path(root) if root else Path(__file__).resolve().parent) / "etoro_http_budget.sqlite"


class SharedLimiter:
    def __init__(self, scope: str, bucket: str, maximum: int, *,
                 min_interval: float = 0.0, seconds: float = 60.0):
        self.scope, self.bucket = str(scope), str(bucket)
        self.maximum = max(1, int(maximum))
        self.seconds = max(1.0, float(seconds))
        self.min_interval = max(0.0, float(min_interval))

    def _connect(self):
        path = _path()
        path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(path, timeout=5.0)
        con.execute("PRAGMA busy_timeout=5000")
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=FULL")
        con.execute("CREATE TABLE IF NOT EXISTS http_attempts (scope TEXT NOT NULL, bucket TEXT NOT NULL, at REAL NOT NULL)")
        con.execute("CREATE INDEX IF NOT EXISTS http_attempt_scope ON http_attempts(scope,bucket,at)")
        con.execute("CREATE TABLE IF NOT EXISTS http_cooldowns (scope TEXT NOT NULL, bucket TEXT NOT NULL, until_at REAL NOT NULL, PRIMARY KEY(scope,bucket))")
        return con

    def defer(self, seconds: float) -> None:
        deadline = time.time() + max(0.0, float(seconds))
        with closing(self._connect()) as con, con:
            con.execute("INSERT INTO http_cooldowns VALUES(?,?,?) ON CONFLICT(scope,bucket) DO UPDATE SET until_at=MAX(until_at,excluded.until_at)",
                        (self.scope, self.bucket, deadline))

    def acquire(self, timeout: float = 30.0) -> bool:
        stop_at = time.monotonic() + max(0.0, float(timeout))
        while True:
            with closing(self._connect()) as con, con:
                con.execute("BEGIN IMMEDIATE")
                now = time.time()
                con.execute("DELETE FROM http_attempts WHERE scope=? AND bucket=? AND at<=?",
                            (self.scope, self.bucket, now - self.seconds))
                count, first, last = con.execute("SELECT COUNT(*),MIN(at),MAX(at) FROM http_attempts WHERE scope=? AND bucket=?",
                                                (self.scope, self.bucket)).fetchone()
                cooldown = con.execute("SELECT until_at FROM http_cooldowns WHERE scope=? AND bucket=?",
                                       (self.scope, self.bucket)).fetchone()
                wait = max(0.0, (float(cooldown[0]) - now) if cooldown else 0.0,
                           (float(first) + self.seconds - now) if count >= self.maximum else 0.0,
                           (float(last) + self.min_interval - now) if last is not None else 0.0)
                if wait <= 0.0:
                    con.execute("INSERT INTO http_attempts VALUES(?,?,?)", (self.scope, self.bucket, now))
                    return True
            if time.monotonic() + wait > stop_at:
                return False
            time.sleep(min(0.25, max(0.01, wait)))
