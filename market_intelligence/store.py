"""Transactional reservations survive restarts and are never reset by settings.

EUR amounts are integer micro-euros. They are conservative local upper estimates,
not an invoice: published USD prices * 1.30, with USD valued at EUR 1. At X set
the provider spending cap too; unrelated clients cannot be metered locally.
"""
from contextlib import contextmanager
from pathlib import Path
from hashlib import sha256
import json
import os
import sqlite3
import tempfile

ROOT = Path(os.environ.get("TRADINGBOT_TEST_STATE_DIR") or Path(__file__).resolve().parents[1])


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    # 10.8.0 (Schritt 2): kanonischer Inhalts-Hash liegt hier, damit die
    # Kontenliste ihn ohne Rueckgriff auf service nutzen kann (Import-Zyklus).
    return sha256(encode(value).encode()).hexdigest()


def atomic_json(name, value):
    ROOT.mkdir(parents=True, exist_ok=True)
    fd, filename = tempfile.mkstemp(prefix=".x-write-", dir=ROOT)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(encode(value))
            f.flush()
            os.fsync(f.fileno())
        os.chmod(filename, 0o600)
        os.replace(filename, ROOT / name)
    finally:
        if os.path.exists(filename):
            os.unlink(filename)


@contextmanager
def db(*, readonly=False):
    path = ROOT / "market_intelligence.sqlite"
    existing_readonly = readonly and path.exists()
    if readonly:
        con = sqlite3.connect(path.resolve().as_uri()+"?mode=ro", uri=True, timeout=5) if existing_readonly else sqlite3.connect(":memory:")
    else:
        ROOT.mkdir(parents=True, exist_ok=True)
        # Create with restrictive permissions before sqlite opens the file.
        fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        os.chmod(path, 0o600)
        con = sqlite3.connect(path, timeout=5)
    con.row_factory = sqlite3.Row
    try:
        if not readonly:
            con.execute("PRAGMA journal_mode=WAL")
        if not existing_readonly:
            con.execute("PRAGMA foreign_keys=ON")
            con.execute("PRAGMA synchronous=FULL")
            con.execute("PRAGMA secure_delete=ON")
            con.executescript('''
          CREATE TABLE IF NOT EXISTS settings(id INTEGER PRIMARY KEY CHECK(id=1),payload TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS state(key TEXT PRIMARY KEY,value TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS requests(
            id TEXT PRIMARY KEY,request_key TEXT UNIQUE NOT NULL,kind TEXT NOT NULL,
            query_hash TEXT NOT NULL,context TEXT NOT NULL,started REAL NOT NULL,
            month TEXT NOT NULL,day TEXT NOT NULL,reserved INTEGER NOT NULL,
            charged INTEGER NOT NULL DEFAULT 0,status TEXT NOT NULL,http INTEGER,
            received INTEGER NOT NULL DEFAULT 0,processed INTEGER NOT NULL DEFAULT 0,
            duplicates INTEGER NOT NULL DEFAULT 0,body_hash TEXT NOT NULL DEFAULT '',
            coverage TEXT NOT NULL DEFAULT 'NICHT_ABGEFRAGT',finished REAL);
          CREATE INDEX IF NOT EXISTS x_request_month ON requests(month,day,kind);
          CREATE TABLE IF NOT EXISTS attention(
            query_hash TEXT NOT NULL,symbol TEXT NOT NULL,day TEXT NOT NULL,
            count INTEGER,coverage TEXT NOT NULL,request_id TEXT NOT NULL,
            day_type TEXT NOT NULL,recorded REAL NOT NULL,
            PRIMARY KEY(query_hash,symbol,day));
          CREATE TABLE IF NOT EXISTS posts(
            id TEXT PRIMARY KEY,author TEXT NOT NULL,created REAL NOT NULL,
            observed REAL NOT NULL,text TEXT NOT NULL,text_hash TEXT NOT NULL,
            topic TEXT NOT NULL,spam INTEGER NOT NULL,urls TEXT NOT NULL,
            cashtags TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS post_context(
            post_id TEXT NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
            query_hash TEXT NOT NULL,symbol TEXT NOT NULL,kind TEXT NOT NULL,
            PRIMARY KEY(post_id,query_hash,symbol,kind));
          CREATE TABLE IF NOT EXISTS post_link_hashes(
            post_id TEXT NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
            url_hash TEXT NOT NULL,PRIMARY KEY(post_id,url_hash));
          CREATE TABLE IF NOT EXISTS consumers(
            consumer TEXT NOT NULL,context TEXT NOT NULL,payload_hash TEXT NOT NULL,
            consumed REAL NOT NULL,PRIMARY KEY(consumer,context,payload_hash));
          CREATE TABLE IF NOT EXISTS tombstones(kind TEXT NOT NULL,identity_hash TEXT NOT NULL,
            PRIMARY KEY(kind,identity_hash));
        ''')
        if not readonly:
            con.execute("BEGIN IMMEDIATE")
        yield con
        con.commit()
    except BaseException:
        con.rollback()
        raise
    finally:
        con.close()


def value(con, key, default=None):
    row = con.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
    return json.loads(row[0]) if row else default


def put(con, key, data):
    con.execute("INSERT OR REPLACE INTO state VALUES(?,?)", (key, encode(data)))
