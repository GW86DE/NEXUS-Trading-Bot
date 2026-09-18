"""FINRA Short Interest als Squeeze-Merkmal (10.5.0, nur Information).

FINRA veroeffentlicht die konsolidierte Leerverkaufsposition je Symbol zweimal
im Monat (Stichtage 15. und Monatsletzter, Veroeffentlichung etwa zehn Tage
spaeter). Die Daten-API (api.finra.org) ist ohne Schluessel abrufbar; die
Partition ist ``settlementDate``, deshalb werden die juengsten Stichtage der
Reihe nach angefragt (geprueft 18.09.2026: 31.08. verfuegbar, 15.09. noch nicht).

Grundsaetze:
- Ein Squeeze-Merkmal ist eine INFORMATION auf der Karte und ein Vergleichs-
  merkmal in der Vorwaertsmessung. Es blockt nichts und gibt nichts frei.
- Fehlende oder veraltete Daten sind UNKNOWN (kein Merkmal), nie "kein Short".
- Der Anteil bezieht sich auf ausstehende Aktien aus dem FMP-Quote, nicht auf
  den Free Float; das wird ausdruecklich so beschriftet.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import json
import logging
import math
import time

from . import research
from .control import Blocked

LOG = logging.getLogger(__name__)
URL = "https://api.finra.org/data/group/otcMarket/name/consolidatedShortInterest"
TTL = 24*3600
MAX_AGE_DAYS = 45
SHORT_RATIO_FLAG = 0.15    # >= 15 % der ausstehenden Aktien
DAYS_TO_COVER_FLAG = 5.0   # >= 5 Tage Eindeckung


def settlement_dates(today):
    """Juengste moegliche Stichtage (15. und Monatsletzter), neueste zuerst."""
    out = []
    cursor = today
    for _ in range(8):
        last = (cursor.replace(day=1) + timedelta(days=32)).replace(day=1) - timedelta(days=1)
        mid = cursor.replace(day=15)
        for candidate in (last, mid):
            while candidate.weekday() >= 5:  # Stichtag am Wochenende: letzter Handelstag davor
                candidate -= timedelta(days=1)
            if candidate <= today and candidate not in out:
                out.append(candidate)
        cursor = cursor.replace(day=1) - timedelta(days=1)
    return sorted(out, reverse=True)[:6]


def _post(body, *, session=None):
    import requests
    client = session or requests.Session()
    try:
        response = client.post(URL, data=json.dumps(body), timeout=(3.05, 15),
                               headers={"User-Agent": "NEXUS-PULSAR/1.0 private research",
                                        "Accept": "application/json", "Content-Type": "application/json"})
        if response.status_code == 429:
            research.cache_put("backoff:finra", {"status": 429}, 3600)
            raise Blocked("finra: Rate-Limit")
        if response.status_code == 204 or not response.text.strip():
            return []
        response.raise_for_status()
        if len(response.content) > 500_000:
            raise ValueError("FINRA-Antwort groesser als erwartet")
        rows = response.json()
        return rows if isinstance(rows, list) else []
    finally:
        if session is None:
            client.close()


def normalise(rows, symbol):
    """Juengste Zeile fuer das Symbol; Zahlen als float, Rest verworfen."""
    best = None
    for row in rows:
        if not isinstance(row, dict) or str(row.get("symbolCode") or "").upper() != symbol:
            continue
        stamp = str(row.get("settlementDate") or "")[:10]
        if best is None or stamp > best["settlement_date"]:
            best = {"symbol": symbol, "settlement_date": stamp,
                    "short_shares": _number(row.get("currentShortPositionQuantity")),
                    "previous_short_shares": _number(row.get("previousShortPositionQuantity")),
                    "average_daily_volume": _number(row.get("averageDailyVolumeQuantity")),
                    "days_to_cover": _number(row.get("daysToCoverQuantity")),
                    "market": str(row.get("marketClassCode") or "")[:12]}
    return best


def _number(value):
    try:
        v = float(value)
        return v if math.isfinite(v) and v >= 0 else None
    except (TypeError, ValueError):
        return None


def fetch(symbol, *, now=None, session=None):
    """Juengster FINRA-Stichtag fuer ein Symbol; hoechstens einmal je Tag abgerufen."""
    now = time.time() if now is None else now
    if not research.TICKER.fullmatch(str(symbol or "")):
        raise ValueError("Ungueltiges Symbol")
    key = "finra:" + symbol
    hit = research.cached(key, now=now)
    if hit:
        return hit["data"]
    if research.cached("backoff:finra", now=now):
        raise Blocked("finra: Abrufpause")
    token = research.reserve("finra", now=now)
    overall = research.reserve("data", now=now)
    today = datetime.fromtimestamp(now, timezone.utc).date()
    try:
        result = None
        tried = []
        for stamp in settlement_dates(today):
            tried.append(stamp.isoformat())
            rows = _post({"limit": 5, "compareFilters": [
                {"compareType": "EQUAL", "fieldName": "settlementDate", "fieldValue": stamp.isoformat()},
                {"compareType": "EQUAL", "fieldName": "symbolCode", "fieldValue": symbol}]}, session=session)
            result = normalise(rows, symbol)
            if result:
                break
        data = {"symbol": symbol, "observed_at": now, "status": "OK" if result else "NOT_FOUND",
                "tried_settlement_dates": tried, "source": "FINRA consolidatedShortInterest", "url": URL,
                **(result or {})}
        research.cache_put(key, data, TTL, now=now)
        research.settle(token, 1); research.settle(overall, 1)
        return data
    except Exception as exc:
        if not research.cached("backoff:finra", now=now):
            research.cache_put("backoff:finra", {"error": type(exc).__name__}, 1800, now=now)
        raise


def squeeze_profile(data, shares_outstanding=None, *, now=None):
    """Squeeze-Merkmal (Information): Short-Anteil und Days-to-Cover einordnen."""
    now = time.time() if now is None else now
    if not data or data.get("status") != "OK":
        return {"status": "UNKNOWN", "flag": None, "detail": "Kein FINRA-Beleg (nicht abgerufen oder Symbol nicht enthalten)"}
    stamp = str(data.get("settlement_date") or "")
    try:
        age_days = (datetime.fromtimestamp(now, timezone.utc).date() - date.fromisoformat(stamp)).days
    except ValueError:
        age_days = None
    short = data.get("short_shares")
    dtc = data.get("days_to_cover")
    ratio = (short / shares_outstanding) if short and shares_outstanding and shares_outstanding > 0 else None
    stale = age_days is None or age_days > MAX_AGE_DAYS
    flag = None
    if not stale and ((ratio is not None and ratio >= SHORT_RATIO_FLAG) or (dtc is not None and dtc >= DAYS_TO_COVER_FLAG)):
        flag = True
    elif not stale and (ratio is not None or dtc is not None):
        flag = False
    parts = []
    if ratio is not None:
        parts.append(f"Short-Anteil {ratio*100:.1f} % der ausstehenden Aktien (nicht Free Float)")
    elif short is not None:
        parts.append(f"{short:,.0f} leerverkaufte Aktien (ausstehende Aktien unbekannt)")
    if dtc is not None:
        parts.append(f"Days-to-Cover {dtc:.1f}")
    detail = ("FINRA-Stichtag " + stamp + ": " + ", ".join(parts) if parts else "FINRA-Zeile ohne Zahlen")
    if stale:
        detail += f" -- Stichtag {age_days} Tage alt, kein aktuelles Merkmal" if age_days is not None else " -- Stichtag unlesbar"
    return {"status": "STALE" if stale else "OK", "flag": flag, "short_ratio": ratio, "days_to_cover": dtc,
            "short_shares": short, "settlement_date": stamp, "age_days": age_days,
            "detail": detail + (". Squeeze-Merkmal: " + ("JA" if flag else "nein" if flag is False else "unbekannt")
                                + " (nur Information, kein Block, keine Freigabe).")}


def status(*, now=None):
    """Lokaler Abrufstand fuer die WebUI (kein Testabruf)."""
    now = time.time() if now is None else now
    pause = research.cached("backoff:finra", now=now)
    with research.db(readonly=True) as con:
        rows = con.execute("SELECT key, saved FROM cache WHERE key LIKE 'finra:%' ORDER BY saved DESC LIMIT 50").fetchall()
    last = rows[0]["saved"] if rows else None
    return {"state": "backoff" if pause else "ok" if last and now - last <= 2*86400 else "unknown",
            "symbols_cached": len(rows), "last_fetch_at": last,
            "backoff_seconds": max(0, int(pause["expires"] - now)) if pause else 0,
            "detail": "FINRA Short Interest: zweimal monatlich, Veroeffentlichung etwa zehn Tage nach Stichtag; "
                      "je Kandidat hoechstens ein Abruf pro Tag."}


__all__ = ["fetch", "squeeze_profile", "settlement_dates", "normalise", "status"]
