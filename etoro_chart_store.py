"""eToro-Kerzenspeicher fuer die WebUI-Kerzenansicht (10.4.0).

Die WebUI hat bewusst keine Brokerverbindung. Bis 10.3.1 hiess es deshalb
"Kerzen sind auf dieser Seite derzeit fuer OKX-Kryptotrades verfuegbar",
obwohl der Handelskern fuer jedes Aktiensignal ohnehin 60 Tage Stundenkerzen
von eToro laedt (/history/candles). Dieses Modul sichert diese bereits
abgerufenen Reihen in eine SQLite-Datei; die Kerzenansicht liest nur daraus.

Grundsaetze:
- Stundenkerzen: KEIN zusaetzlicher Abruf -- der Scanner-DataFrame wird
  nach dem Signal gespeichert (``merke_scan``).
- 15-Minuten- und Tageskerzen: nur fuer Instrumente mit offener Position
  oder Trade der letzten 7 Tage, hoechstens alle 15 Minuten je Instrument,
  innerhalb des bestehenden eToro-Lesebudgets (``ergaenze_fuer_trades``).
- Nur abgeschlossene Kerzen; Rohwerte, keine Fuellung fehlender Intervalle.
- Ein Fehler hier beruehrt den Handelspfad nie.
"""
from __future__ import annotations

from datetime import datetime, timezone
import logging
import os
from pathlib import Path
import sqlite3
import threading
import time

DATEI = "etoro_chart_candles.sqlite"
BARS = {"15m": ("15 mins", 900, 400), "1h": ("1 hour", 3600, 500), "1d": ("1 day", 86400, 400)}
MAX_ROWS_PER_SERIES = 1500
REFRESH_SECONDS = 900
TRADE_LOOKBACK_DAYS = 7
_LOCK = threading.RLock()
_LAST_FETCH: dict[tuple[str, str], float] = {}
logger = logging.getLogger(__name__)


def pfad() -> Path:
    root = Path(os.getenv("TRADINGBOT_TEST_STATE_DIR", "").strip()
                or Path(__file__).resolve().parent)
    return root / DATEI


def _connect():
    p = pfad()
    p.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(p, timeout=5)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=5000")
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("CREATE TABLE IF NOT EXISTS candles(account TEXT, environment TEXT, symbol TEXT, bar TEXT, "
                "ts INTEGER, open REAL, high REAL, low REAL, close REAL, volume REAL, "
                "PRIMARY KEY(account,environment,symbol,bar,ts))")
    con.execute("CREATE TABLE IF NOT EXISTS series_state(account TEXT, environment TEXT, symbol TEXT, bar TEXT, "
                "saved_at REAL, rows INTEGER, PRIMARY KEY(account,environment,symbol,bar))")
    return con


def _frame_rows(frame):
    rows = []
    try:
        for index, row in frame.iterrows():
            stamp = index.to_pydatetime()
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=timezone.utc)
            values = [float(row[k]) for k in ("open", "high", "low", "close")]
            if any(v != v or v <= 0 for v in values):
                continue
            volume = float(row.get("volume") or 0.0)
            rows.append((int(stamp.timestamp()), *values, volume if volume == volume else 0.0))
    except Exception:
        return []
    return rows


def speichere(account: str, environment: str, symbol: str, bar: str, frame) -> int:
    """Schreibt abgeschlossene Kerzen idempotent; kappt auf MAX_ROWS_PER_SERIES."""
    if bar not in BARS or not account or environment not in {"DEMO", "LIVE"} or not symbol:
        return 0
    rows = _frame_rows(frame)
    if not rows:
        return 0
    symbol = str(symbol).upper()
    with _LOCK, _connect() as con:
        # Nur neue Kerzen schreiben (plus die letzten zwei zur Korrektur):
        # der Scanner liefert alle 6 Minuten dieselben 60 Tage; die SD-Karte
        # des Pi soll dafuer nicht 1400 Zeilen je Instrument neu schreiben.
        last = con.execute("SELECT MAX(ts) FROM candles WHERE account=? AND environment=? AND symbol=? AND bar=?",
                           (account, environment, symbol, bar)).fetchone()[0]
        if last is not None:
            rows = [r for r in rows if r[0] >= int(last) - 2*BARS[bar][1]]
        if rows:
            con.executemany("INSERT OR REPLACE INTO candles VALUES(?,?,?,?,?,?,?,?,?,?)",
                            [(account, environment, symbol, bar, *r) for r in rows])
        cutoff = con.execute("SELECT ts FROM candles WHERE account=? AND environment=? AND symbol=? AND bar=? "
                             "ORDER BY ts DESC LIMIT 1 OFFSET ?", (account, environment, symbol, bar, MAX_ROWS_PER_SERIES)).fetchone()
        if cutoff:
            con.execute("DELETE FROM candles WHERE account=? AND environment=? AND symbol=? AND bar=? AND ts<=?",
                        (account, environment, symbol, bar, cutoff[0]))
        count = con.execute("SELECT COUNT(*) FROM candles WHERE account=? AND environment=? AND symbol=? AND bar=?",
                            (account, environment, symbol, bar)).fetchone()[0]
        con.execute("INSERT OR REPLACE INTO series_state VALUES(?,?,?,?,?,?)",
                    (account, environment, symbol, bar, time.time(), int(count)))
    return len(rows)


def merke_scan(broker, instrument, frame, bar_size: str) -> None:
    """Hook im Scanner: die ohnehin geladene Signalreihe wegsichern (kein Abruf)."""
    try:
        if str(getattr(broker, "name", "")).lower() != "etoro" or getattr(instrument, "asset_type", "") != "stock":
            return
        bar = next((k for k, v in BARS.items() if v[0] == str(bar_size).strip().lower()), None)
        if bar is None or frame is None or getattr(frame, "empty", True):
            return
        account = str(broker.account_fingerprint() or "")
        environment = "DEMO" if broker.ist_paper() else "LIVE"
        speichere(account, environment, getattr(instrument, "name", ""), bar, frame)
    except Exception:
        logger.debug("eToro-Chartreihe (Scan) nicht gespeichert", exc_info=True)


def _trade_symbols(account: str, environment: str, now: float) -> set[str]:
    try:
        import trade_ledger as tl
        tl.init_ledger()
        cutoff = datetime.fromtimestamp(now - TRADE_LOOKBACK_DAYS*86400, timezone.utc).isoformat()
        with tl._connect() as con:
            rows = con.execute("""SELECT DISTINCT symbol FROM trades WHERE broker='etoro'
                AND broker_account_fingerprint=? AND paper=? AND superseded_by IS NULL
                AND (ausgestiegen_am IS NULL OR ausgestiegen_am>=?)""",
                (account, int(environment == "DEMO"), cutoff)).fetchall()
        return {str(r[0]).upper() for r in rows if r[0]}
    except Exception:
        logger.debug("Trade-Symbole fuer Chartreihen nicht lesbar", exc_info=True)
        return set()


def ergaenze_fuer_trades(broker, instrument_by_symbol: dict, *, now=None, max_fetches: int = 4) -> int:
    """15m- und Tageskerzen nur fuer Instrumente mit Position/Trade (<=7 Tage).

    Hoechstens ``max_fetches`` Abrufe je Aufruf und 15 Minuten Ruhe je Reihe,
    damit das eToro-Lesebudget (54/min) fuer Signale und Positionen frei bleibt.
    """
    now = time.time() if now is None else now
    try:
        if str(getattr(broker, "name", "")).lower() != "etoro":
            return 0
        account = str(broker.account_fingerprint() or "")
        environment = "DEMO" if broker.ist_paper() else "LIVE"
        if not account:
            return 0
        wanted = _trade_symbols(account, environment, now)
        done = 0
        for symbol in sorted(wanted):
            inst = instrument_by_symbol.get(symbol) or instrument_by_symbol.get(f"stock:{symbol}".lower()) \
                or next((i for k, i in instrument_by_symbol.items() if str(getattr(i, "name", "")).upper() == symbol), None)
            if inst is None:
                continue
            for bar in ("15m", "1d"):
                if done >= max_fetches:
                    return done
                key = (symbol, bar)
                if now - _LAST_FETCH.get(key, 0.0) < REFRESH_SECONDS:
                    continue
                bar_size, _, _ = BARS[bar]
                # broker.historie() bildet "N D" auf count=min(1000, N*24) ab:
                # 17 D -> 408 Fuenfzehnminutenkerzen (~6 Handelstage),
                # 400 D -> 1000 Tageskerzen (eToro kappt auf sein Maximum).
                dauer = "17 D" if bar == "15m" else "400 D"
                try:
                    frame = broker.historie(inst, dauer, bar_size, nur_handelszeiten=True)
                    speichere(account, environment, symbol, bar, frame)
                    _LAST_FETCH[key] = now
                    done += 1
                except Exception as exc:
                    _LAST_FETCH[key] = now
                    logger.debug("eToro-Chartreihe %s %s nicht abrufbar: %s", symbol, bar, exc)
        return done
    except Exception:
        logger.debug("eToro-Chartreihen nicht ergaenzt", exc_info=True)
        return 0


def lade(account: str, environment: str, symbol: str, bar: str, *, start_ts: int | None = None,
         end_ts: int | None = None, limit: int = 500) -> dict:
    """Kerzen fuer die WebUI; gibt auch den Speicherstand (Alter) zurueck."""
    if bar not in BARS:
        raise ValueError("Unbekannter Kerzenzeitraum")
    symbol = str(symbol).upper()
    limit = max(10, min(MAX_ROWS_PER_SERIES, int(limit)))
    with _LOCK, _connect() as con:
        state = con.execute("SELECT saved_at, rows FROM series_state WHERE account=? AND environment=? AND symbol=? AND bar=?",
                            (account, environment, symbol, bar)).fetchone()
        query = "SELECT ts,open,high,low,close,volume FROM candles WHERE account=? AND environment=? AND symbol=? AND bar=?"
        params = [account, environment, symbol, bar]
        if start_ts is not None:
            query += " AND ts>=?"; params.append(int(start_ts))
        if end_ts is not None:
            query += " AND ts<=?"; params.append(int(end_ts))
        query += " ORDER BY ts DESC LIMIT ?"; params.append(limit)
        rows = con.execute(query, params).fetchall()
    candles = [{"zeit": datetime.fromtimestamp(r["ts"], timezone.utc).isoformat(),
                "open": r["open"], "high": r["high"], "low": r["low"], "close": r["close"], "volume": r["volume"]}
               for r in reversed(rows)]
    return {"candles": candles, "saved_at": (state["saved_at"] if state else None),
            "rows_total": (state["rows"] if state else 0), "bar": bar,
            "bar_seconds": BARS[bar][1]}


def gespeicherte_reihen(bar: str = "1h") -> list[tuple[str, str, str]]:
    """Alle gesicherten Reihen (Konto, Umgebung, Symbol) eines Zeitrahmens (10.5.0, PULSAR-Volumenscan)."""
    if bar not in BARS or not pfad().exists():
        return []
    with _LOCK, _connect() as con:
        rows = con.execute("SELECT account, environment, symbol FROM series_state WHERE bar=? AND rows>0 "
                           "ORDER BY symbol", (bar,)).fetchall()
    return [(r[0], r[1], r[2]) for r in rows]


def verfuegbare_bars(account: str, environment: str, symbol: str) -> list[str]:
    symbol = str(symbol).upper()
    with _LOCK, _connect() as con:
        rows = con.execute("SELECT bar FROM series_state WHERE account=? AND environment=? AND symbol=? AND rows>0",
                           (account, environment, symbol)).fetchall()
    return [b for b in ("15m", "1h", "1d") if b in {r[0] for r in rows}]


__all__ = ["speichere", "merke_scan", "ergaenze_fuer_trades", "lade", "verfuegbare_bars", "gespeicherte_reihen", "pfad", "BARS"]
