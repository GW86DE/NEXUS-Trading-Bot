"""5m spot OHLCV normalization and a bounded, restart-safe OKX history cache.

The normalization follows Freqtrade's converter: duplicate OHLCV aggregation,
ascending UTC dates, missing intervals at previous close and zero volume.
Gap rows are marked and never stored as broker observations.
"""
from __future__ import annotations
import hashlib
import os
from pathlib import Path
import sqlite3
import threading
import time
from contextlib import contextmanager
import numpy as np
import pandas as pd

COLUMNS = ["open", "high", "low", "close", "volume"]
_LOCK = threading.RLock()
LIVE_CANDLES = 500  # OKX's 300 + SampleStrategy's 200 startup candles.


def utc(value):
    stamp = pd.Timestamp(value)
    if stamp.tzinfo is None:
        raise ValueError("5m-Kerzenzeit braucht eine eindeutige Zeitzone")
    return stamp.tz_convert("UTC")


def candle_cutoff(now=None):
    seconds = time.time() if now is None else float(now)
    # Matches Freqtrade's one second processing offset after candle close.
    return pd.Timestamp(int((seconds-1)//300)*300, unit="s", tz="UTC")


def normalize(raw, *, cutoff=None, fill_missing=True):
    if raw is None or raw.empty:
        return pd.DataFrame(columns=COLUMNS, index=pd.DatetimeIndex([], tz="UTC"))
    if not isinstance(raw.index, pd.DatetimeIndex) or raw.index.tz is None:
        raise ValueError("5m-OHLCV braucht einen UTC-Zeitindex; keine erfundenen Zeitabstaende")
    if not set(COLUMNS) <= set(raw.columns):
        raise ValueError("OHLCV-Spalten unvollstaendig")
    df = raw[COLUMNS].copy().astype(float)
    df.index = df.index.tz_convert("UTC")
    df.index.name = None
    if not (df.index == df.index.floor("5min")).all():
        raise ValueError("Kerzen liegen nicht auf dem 5m-Raster")
    if cutoff is not None:
        df = df.loc[df.index + pd.Timedelta(minutes=5) <= utc(cutoff)]
    if df.empty:
        return df
    if (not np.isfinite(df.to_numpy()).all() or (df[COLUMNS[:4]] <= 0).any().any()
            or (df.volume < 0).any() or (df.high < df[["open", "close", "low"]].max(axis=1)).any()
            or (df.low > df[["open", "close", "high"]].min(axis=1)).any()):
        raise ValueError("Ungueltige OHLCV-Werte")
    df = df.groupby(level=0, sort=True).agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "max"})
    real_index = df.index
    if fill_missing:
        if (df.index[-1]-df.index[0]).total_seconds()/300 > 2_000_000:
            raise ValueError("Kerzenzeitraum zu gross fuer einen einzelnen Abgleich")
        df = df.resample("5min").agg(
            {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
        df["close"] = df.close.ffill()
        for col in ("open", "high", "low"):
            df[col] = df[col].fillna(df.close)
    df["synthetic_gap"] = ~df.index.isin(real_index)
    return df


def latest_signal_valid(raw, *, now=None):
    if raw is None or raw.empty:
        return False
    stamp = utc(raw.index[-1])
    current = pd.Timestamp(time.time() if now is None else float(now), unit="s", tz="UTC")
    # Freqtrade get_latest_candle: 2 * timeframe + exchange.outdated_offset(5m).
    return stamp + pd.Timedelta(minutes=5) <= current and current-stamp <= pd.Timedelta(minutes=15)


def require_signal_current(context, instrument, *, now=None):
    if context.get("strategy_mode") != "FREQTRADE_SAMPLE":
        return
    if context.get("signal_instrument") != instrument or not context.get("signal_candle"):
        raise ValueError("Freqtrade-Signal ist nicht an dieses Instrument gebunden")
    stamp = utc(context["signal_candle"])
    seconds = time.time() if now is None else float(now)
    if not stamp.timestamp()+300 <= seconds <= stamp.timestamp()+900:
        raise ValueError("Freqtrade-Signal vor Orderversand veraltet oder Kerze noch offen")


class History:
    def __init__(self, client, path=None):
        self.client = client
        self.path = Path(path) if path else Path(os.environ.get("TRADINGBOT_TEST_STATE_DIR") or
            Path(__file__).resolve().parent) / "market_candles.sqlite"
        self.domain = hashlib.sha256((str(getattr(client, "base_url", "")) + ":" +
            ("DEMO" if client.demo else "LIVE")).encode()).hexdigest()

    @contextmanager
    def _connect(self):
        con = sqlite3.connect(self.path, timeout=5)
        try:
            con.execute("PRAGMA journal_mode=WAL")
            con.execute("CREATE TABLE IF NOT EXISTS candles(domain TEXT, instrument TEXT, ts INTEGER, "
                        "open REAL, high REAL, low REAL, close REAL, volume REAL, "
                        "PRIMARY KEY(domain,instrument,ts))")
            con.execute("CREATE TABLE IF NOT EXISTS fetch_state(domain TEXT,instrument TEXT, archive_at REAL, "
                        "PRIMARY KEY(domain,instrument))")
            with con:
                yield con
        finally:
            con.close()

    def _store(self, instrument, frame):
        with self._connect() as con:
            con.executemany("INSERT OR REPLACE INTO candles VALUES(?,?,?,?,?,?,?,?)",
                [(self.domain, instrument, int(stamp.timestamp()*1000), *map(float, r[COLUMNS]))
                 for stamp, r in frame.tail(LIVE_CANDLES).iterrows()])
            if len(frame):
                con.execute("DELETE FROM candles WHERE domain=? AND instrument=? AND ts<?",
                    (self.domain, instrument, int(frame.tail(LIVE_CANDLES).index[0].timestamp()*1000)))

    def load(self, instrument, *, cutoff=None, cached_only=False):
        cutoff = candle_cutoff() if cutoff is None else utc(cutoff)
        deadline = time.monotonic() + 8
        target = int((cutoff-pd.Timedelta(minutes=5)).timestamp()*1000)
        with _LOCK:
            with self._connect() as con:
                rows = con.execute("SELECT ts,open,high,low,close,volume FROM candles WHERE domain=? "
                    "AND instrument=? AND ts<=? ORDER BY ts DESC LIMIT ?",
                    (self.domain, instrument, target, LIVE_CANDLES)).fetchall()
                archive = con.execute("SELECT archive_at FROM fetch_state WHERE domain=? AND instrument=?",
                    (self.domain, instrument)).fetchone()
            cached = pd.DataFrame(rows, columns=["ts"]+COLUMNS)
            cached.index = pd.to_datetime(cached.pop("ts"), unit="ms", utc=True)
            cached = cached.sort_index()
            archive_due = not archive or time.time()-archive[0] >= 86400
            complete = (bool(len(cached)) and int(cached.index[-1].timestamp()*1000) >= target
                        and (len(cached) >= LIVE_CANDLES or not archive_due))
            if not complete and not cached_only:
                missing = (int((cutoff-cached.index[-1]).total_seconds()/300)+2 if len(cached) else 300)
                newest = self.client.candles(instrument, bar="5m", limit=min(300, max(5, missing)),
                                             nur_abgeschlossen=True)
                merged = normalize(pd.concat([cached, newest]) if len(cached) else newest,
                                   cutoff=cutoff, fill_missing=False)
                self._store(instrument, merged)
                # Startup/backfill bounded to five archive pages, advancing strictly backwards.
                for _ in range(5 if archive_due else 0):
                    if len(merged) >= LIVE_CANDLES or merged.empty:
                        break
                    if time.monotonic() >= deadline:
                        raise ValueError("Historien-Zeitbudget erreicht; Teilhistorie gespeichert, naechster Lauf setzt fort")
                    cursor = int(merged.index[0].timestamp()*1000)
                    page = self.client.historical_candles(instrument, bar="5m", limit=100,
                                                         end_ms=cursor, nur_abgeschlossen=True)
                    if page.empty:
                        break
                    page = page.loc[page.index < merged.index[0]]
                    if page.empty:
                        break
                    merged = normalize(pd.concat([page, merged]), cutoff=cutoff, fill_missing=False)
                    self._store(instrument, merged)
                cached = merged.tail(LIVE_CANDLES)
                with self._connect() as con:
                    if archive_due:
                        con.execute("INSERT OR REPLACE INTO fetch_state VALUES(?,?,?)",
                            (self.domain, instrument, time.time()))
            result = normalize(cached, cutoff=cutoff).tail(LIVE_CANDLES)
            # Persisted rows predate this response too: do not claim a current
            # raw receipt confirms all 500 cached rows. Receipt provenance is
            # attached only to the newest HTTP response, if one occurred.
            result.attrs["confirmation"] = "FILTERED_BEFORE_CACHE_NOT_REMEASURED"
            result.attrs["source_receipt"] = (newest.attrs.get("source_receipt")
                if not complete and not cached_only else None)
            result.attrs.update(source="OKX", environment="DEMO" if self.client.demo else "LIVE",
                instrument=instrument, cutoff=cutoff.isoformat(), real_candles=int((~result.get("synthetic_gap", pd.Series(dtype=bool))).sum()),
                gap_candles=int(result.get("synthetic_gap", pd.Series(dtype=bool)).sum()))
            from candle_observation import quality, publish
            result.attrs["candle_quality"] = quality(result,
                base_url=getattr(self.client, "base_url", ""),
                environment="DEMO" if self.client.demo else "LIVE", instrument=instrument)
            publish(result.attrs["candle_quality"])
            return result


class ScanCursor:
    """Durable consumed candles. Order intent must be committed before mark()."""
    def __init__(self, domain):
        self.domain = domain
        self.path = Path(os.environ.get("TRADINGBOT_TEST_STATE_DIR") or Path(__file__).resolve().parent) / "freqtrade_scan.sqlite"

    @contextmanager
    def _connect(self):
        con = sqlite3.connect(self.path, timeout=5)
        try:
            con.execute("CREATE TABLE IF NOT EXISTS processed(domain TEXT, symbol TEXT, candle INTEGER, "
                        "decision_id INTEGER, PRIMARY KEY(domain,symbol))")
            with con:
                yield con
        finally:
            con.close()

    def seen(self, symbol, candle):
        with self._connect() as con:
            row = con.execute("SELECT candle FROM processed WHERE domain=? AND symbol=?", (self.domain,symbol)).fetchone()
        return bool(row and row[0] >= int(utc(candle).timestamp()))

    def mark(self, symbol, candle, decision_id):
        with self._connect() as con:
            con.execute("INSERT INTO processed VALUES(?,?,?,?) ON CONFLICT(domain,symbol) DO UPDATE SET "
                        "candle=excluded.candle, decision_id=excluded.decision_id WHERE excluded.candle>processed.candle",
                        (self.domain,symbol,int(utc(candle).timestamp()),int(decision_id or 0)))
