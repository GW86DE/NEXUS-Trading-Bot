"""Small, process-shared Massive Free request gate and cache.

Reservations commit BEFORE HTTP. A crash/timeout never refunds an attempt.
Readers never sleep for rate limits or another caller: they receive a dated
pause and can continue their trading cycle. Only the optional provider fails
closed when its local store cannot be read. No API keys or URLs with keys are
persisted. Counters are deliberately shared across all NEXUS clients/keys;
response caches and endpoint permissions remain credential-specific.
"""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import time

MINUTE_LIMIT = 4
MIN_INTERVAL = 15.0
MAX_CACHE_BYTES = 4 * 1024 * 1024
try:
    BOOT_ID = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
except OSError:
    BOOT_ID = "MONOTONIC_CLOCK"


class MassivePaused(RuntimeError):
    def __init__(self, reason, retry_after=60.0, code="MASSIVE_PAUSED"):
        self.retry_after = max(1.0, float(retry_after))
        self.code = code
        super().__init__(f"MASSIVE pausiert: {reason}; naechster Versuch in "
                         f"{math.ceil(self.retry_after)} s [{code}]")


def retry_seconds(value, now):
    """Honor integer seconds and RFC HTTP dates, fall back to one minute."""
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        try:
            stamp = parsedate_to_datetime(str(value))
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=timezone.utc)
            seconds = stamp.timestamp() - now
        except (TypeError, ValueError, OverflowError):
            seconds = 60.0
    return max(60.0, seconds) if math.isfinite(seconds) else 60.0


class Store:
    def __init__(self, api_key, base, daily_limit=0, *, path=None, clock=None, monotonic=None):
        root = Path(os.getenv("TRADINGBOT_TEST_STATE_DIR", "").strip()
                    or Path(__file__).resolve().parent)
        self.path = Path(path) if path is not None else root / "massive_service.sqlite"
        self.scope = hashlib.sha256((base + "\0" + api_key).encode()).hexdigest()
        self.daily_limit = max(0, int(daily_limit))
        self.clock = clock or time.time
        self.monotonic = monotonic or (clock if clock is not None else time.monotonic)

    def _clock_changed(self, previous, now, mono):
        return bool(previous and (previous["boot"] != BOOT_ID or mono < previous["mono"] or
                    abs((now - previous["wall"]) - (mono - previous["mono"])) > 2))

    @contextmanager
    def db(self):
        con = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            # Short local lock timeout, no provider queue on a trading thread.
            con = sqlite3.connect(self.path, timeout=0.15)
            self.path.chmod(0o600)
            con.row_factory = sqlite3.Row
            con.execute("PRAGMA synchronous=FULL")
            con.execute("PRAGMA trusted_schema=OFF")
            con.executescript("""
                CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS calls(id INTEGER PRIMARY KEY, at REAL NOT NULL,
                    scope TEXT NOT NULL, cache_key TEXT NOT NULL, path TEXT NOT NULL,
                    symbol TEXT NOT NULL DEFAULT '',
                    mono_at REAL NOT NULL, boot TEXT NOT NULL,
                    lease_until REAL NOT NULL, lease_mono REAL NOT NULL, done INTEGER NOT NULL DEFAULT 0,
                    http_status INTEGER, result TEXT NOT NULL DEFAULT 'RESERVED');
                CREATE INDEX IF NOT EXISTS massive_calls_at ON calls(at);
                CREATE INDEX IF NOT EXISTS massive_calls_key ON calls(scope,cache_key);
                CREATE INDEX IF NOT EXISTS massive_calls_mono ON calls(boot,mono_at);
                CREATE TABLE IF NOT EXISTS cache(scope TEXT, key TEXT, saved REAL,
                    expires REAL, payload TEXT, PRIMARY KEY(scope,key));
                CREATE TABLE IF NOT EXISTS capabilities(scope TEXT, path TEXT, retry REAL,
                    detail TEXT, PRIMARY KEY(scope,path));
            """)
            con.execute("BEGIN IMMEDIATE")
            now, mono = self.clock(), self.monotonic()
            previous = self._meta(con, "clock_guard", {})
            changed = self._clock_changed(previous, now, mono)
            if changed:
                self._pause(con, 60, "Systemzeit oder Systemstart geändert; Minutenbudget wird 60 s abgesichert", now)
            self._put(con, "clock_guard", {"wall": now, "mono": mono, "boot": BOOT_ID})
            if con.execute("SELECT 1 FROM meta WHERE key='observed_since'").fetchone() is None:
                self._put(con, "observed_since", now)
                if (self.path.parent / "news_source_status.json").exists():
                    # Old clients had no shared request history. Consume one
                    # quiet minute at first upgrade, never reset it on restart.
                    self._pause(con, 60, "Einmalige Upgrade-Pause: vorheriges Minutenbudget unbekannt", now)
            # Clock/bootstrap guards survive even when acquire() rejects the
            # request. Otherwise every retry would restart the initial pause.
            con.commit()
            con.execute("BEGIN IMMEDIATE")
            yield con
            con.commit()
        except MassivePaused:
            if con is not None:
                con.rollback()
            raise
        except (sqlite3.Error, OSError, ValueError, TypeError, KeyError):
            if con is not None:
                con.rollback()
            raise MassivePaused("lokaler Budgetzustand nicht verlässlich lesbar",
                                60, "MASSIVE_STATE_UNAVAILABLE") from None
        finally:
            if con is not None:
                con.close()

    @staticmethod
    def _meta(con, key, default):
        row = con.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    @staticmethod
    def _put(con, key, value):
        con.execute("INSERT INTO meta VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (key, json.dumps(value, allow_nan=False)))

    def _increment(self, con, key):
        self._put(con, key, int(self._meta(con, key, 0)) + 1)

    def _pause(self, con, seconds, reason, now):
        mono = self.monotonic()
        prior_mono = (float(self._meta(con, "blocked_mono", 0))
                      if self._meta(con, "blocked_boot", "") == BOOT_ID else 0)
        until_mono = max(prior_mono, mono + seconds)
        self._put(con, "blocked_until", max(float(self._meta(con, "blocked_until", 0)),
                                           now + until_mono - mono))
        self._put(con, "blocked_mono", until_mono)
        self._put(con, "blocked_boot", BOOT_ID)
        if prior_mono <= mono + seconds:
            self._put(con, "block_reason", reason)

    def _status(self, con, now):
        day = datetime.fromtimestamp(now, timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        used = con.execute("SELECT COUNT(*) FROM calls WHERE at>=?", (day.timestamp(),)).fetchone()[0]
        mono = self.monotonic()
        minute = con.execute("SELECT mono_at FROM calls WHERE boot=? AND mono_at>? ORDER BY mono_at",
                             (BOOT_ID, mono - 60)).fetchall()
        last = con.execute("SELECT MAX(at) FROM calls").fetchone()[0]
        last_mono = con.execute("SELECT MAX(mono_at) FROM calls WHERE boot=?", (BOOT_ID,)).fetchone()[0]
        until = float(self._meta(con, "blocked_until", 0))
        if self._meta(con, "blocked_boot", "") == BOOT_ID:
            until = max(until, now + max(0, float(self._meta(con, "blocked_mono", 0)) - self.monotonic()))
        clock_changed = self._clock_changed(self._meta(con, "clock_guard", {}), now, self.monotonic())
        if clock_changed:
            until = max(until, now + 60)
        next_at = max(until, now + last_mono + MIN_INTERVAL - mono if last_mono is not None else 0)
        if len(minute) >= MINUTE_LIMIT:
            next_at = max(next_at, now + minute[-MINUTE_LIMIT][0] + 60 - mono)
        if self.daily_limit and used >= self.daily_limit:
            next_at = max(next_at, (day + timedelta(days=1)).timestamp())
        return {"effective": "FREE", "minute_limit": MINUTE_LIMIT,
                "provider_minute_limit": 5, "minute_used": len(minute),
                "min_interval_seconds": MIN_INTERVAL, "limit": self.daily_limit,
                "verbraucht": used, "rest": max(0, self.daily_limit - used) if self.daily_limit else None,
                "tag": day.date().isoformat(), "reset_zone": "UTC",
                "gesperrt": next_at > now, "blocked_until": until,
                "sperrgrund": "Systemzeit / Minutenbudget muss abgeglichen werden" if clock_changed else
                    self._meta(con, "block_reason", "") if until > now else
                    "Tagesbudget aufgebraucht" if self.daily_limit and used >= self.daily_limit else
                    "Free-Minutenbudget / Mindestabstand" if next_at > now else "",
                "next_allowed_at": next_at if next_at > now else now,
                "retry_after": max(0, next_at - now),
                "last_http_attempt_at": last,
                "last_http_success_at": self._meta(con, "success:" + self.scope, None),
                "cache_hits": self._meta(con, "cache_hits", 0),
                "cache_last_saved_at": self._meta(con, "cache_last_saved_at", None),
                "observed_since": self._meta(con, "observed_since", None),
                "scope": "Alle NEXUS-Prozesse und Keys seit Einfuehrung; fruehere und externe Abrufe nicht messbar"}

    def status(self):
        with self.db() as con:
            return self._status(con, self.clock())

    def acquire(self, path, params, *, lease_seconds=40, cache_only=False):
        """Return (reservation id or None, cached payload or None, saved_at)."""
        now = self.clock()
        key = hashlib.sha256(json.dumps([path, params], sort_keys=True, separators=(",", ":"),
                                        allow_nan=False).encode()).hexdigest()
        with self.db() as con:
            cached = con.execute("SELECT payload,saved FROM cache WHERE scope=? AND key=? AND expires>? AND saved<=?",
                                 (self.scope, key, now, now)).fetchone()
            if cached:
                payload = json.loads(cached[0])
                if not isinstance(payload, dict):
                    raise ValueError("Invalid cache")
                self._increment(con, "cache_hits")
                return None, payload, cached[1]
            if cache_only:
                raise MassivePaused("optionale Ergänzung nicht im Cache; vorhandene Hauptquelle wird genutzt",
                                    60, "MASSIVE_CACHE_MISS")
            mono = self.monotonic()
            pending = con.execute("SELECT MAX(lease_mono) FROM calls WHERE scope=? AND cache_key=? AND done=0 AND boot=? AND lease_mono>?",
                                  (self.scope, key, BOOT_ID, mono)).fetchone()[0]
            if pending:
                raise MassivePaused("gleiche Anfrage läuft bereits", pending - mono, "MASSIVE_INFLIGHT")
            refusal = con.execute("SELECT retry,detail FROM capabilities WHERE scope=? AND path IN (?, '*') AND retry>? ORDER BY retry DESC LIMIT 1",
                                  (self.scope, path, now)).fetchone()
            if refusal:
                raise MassivePaused(refusal[1], refusal[0] - now, "MASSIVE_CAPABILITY_PAUSED")
            status = self._status(con, now)
            if status["gesperrt"]:
                raise MassivePaused(status["sperrgrund"], status["retry_after"], "MASSIVE_RATE_PAUSED")
            # Only four requests/minute; 30 days are bounded at <173k tiny rows.
            con.execute("DELETE FROM calls WHERE at<?", (now - 30 * 86400,))
            con.execute("DELETE FROM cache WHERE expires<?", (now - 86400,))
            token = con.execute("INSERT INTO calls(at,scope,cache_key,path,symbol,mono_at,boot,lease_until,lease_mono) VALUES(?,?,?,?,?,?,?,?,?)",
                                (now, self.scope, key, path, str(params.get("ticker", ""))[:80],
                                 mono, BOOT_ID, now + max(30, lease_seconds), mono + max(30, lease_seconds))).lastrowid
            self._put(con, "last_clock", now)
            return token, None, None

    def finish(self, token, *, data=None, ttl=0, http_status=None, retry_after=None, result="ERROR"):
        now = self.clock()
        with self.db() as con:
            row = con.execute("SELECT * FROM calls WHERE id=? AND scope=? AND done=0", (token, self.scope)).fetchone()
            if row is None:
                raise ValueError("Unknown reservation")
            con.execute("UPDATE calls SET done=1,http_status=?,result=? WHERE id=?", (http_status, result, token))
            if http_status == 429:
                self._pause(con, retry_seconds(retry_after, now), "HTTP 429: Massive verlangt eine Abrufpause", now)
            elif http_status in (401, 402, 403):
                path = "*" if http_status == 401 else row["path"]
                con.execute("INSERT INTO capabilities VALUES(?,?,?,?) ON CONFLICT(scope,path) DO UPDATE SET retry=excluded.retry,detail=excluded.detail",
                            (self.scope, path, now + 86400, f"HTTP {http_status}: Zugang oder Endpunktberechtigung abgelehnt"))
            if data is not None:
                self._put(con, "success:" + self.scope, now)
                con.execute("DELETE FROM capabilities WHERE scope=? AND path=?", (self.scope, row["path"]))
                raw = json.dumps(data, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
                if len(raw.encode()) <= MAX_CACHE_BYTES:
                    con.execute("INSERT INTO cache VALUES(?,?,?,?,?) ON CONFLICT(scope,key) DO UPDATE SET saved=excluded.saved,expires=excluded.expires,payload=excluded.payload",
                                (self.scope, row["cache_key"], now, now + ttl, raw))
                    self._put(con, "cache_last_saved_at", now)


def cache_ttl(path):
    return 1800 if path == "/v2/reference/news" else 3600 if path.startswith("/v2/aggs/") else 86400


def readonly_status(api_key="", base="https://api.massive.com", *, path=None, clock=None, daily_limit=None):
    """Dashboard/diagnosis: no database creation, writes, network or probes."""
    if daily_limit is None:
        import live_settings
        import config
        daily_limit = (live_settings.massive_tageslimit()
                       or getattr(config, "MASSIVE_DAILY_REQUEST_LIMIT", 0) or 0)
    store = Store(api_key, base, daily_limit=daily_limit, path=path, clock=clock)
    fallback = {"state": "unknown", "effective": "FREE", "minute_limit": MINUTE_LIMIT,
                "provider_minute_limit": 5, "min_interval_seconds": MIN_INTERVAL,
                "minute_used": None, "verbraucht": None, "cache_hits": None,
                "last_http_success_at": None, "last_http_attempt_at": None,
                "next_allowed_at": None, "configured": bool(api_key)}
    if not store.path.exists():
        return {**fallback, "detail": "Noch kein gemeinsamer Massive-Abruf erfasst"}
    con = None
    try:
        con = sqlite3.connect(store.path.resolve().as_uri() + "?mode=ro", uri=True, timeout=0.15)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA query_only=ON")
        con.execute("PRAGMA trusted_schema=OFF")
        con.execute("BEGIN")
        row = store._status(con, store.clock())
        return {**row, "configured": bool(api_key), "state": "paused" if row["gesperrt"] else "ready",
                "detail": "Lokaler Abrufstatus; keine aktive Verbindungspruefung"}
    except (sqlite3.Error, OSError, ValueError, TypeError, KeyError):
        return {**fallback, "state": "unknown", "detail": "Massive-Budgetzustand nicht lesbar"}
    finally:
        if con is not None:
            con.close()
