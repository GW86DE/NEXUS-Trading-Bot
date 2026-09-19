"""StockTwits als zweite Social-Quellenfamilie (10.5.0).

Oeffentliche Endpunkte ohne Schluessel (geprueft 18.09.2026):
- Trending-Liste: 30 Symbole mit Watchlist-Zahl, Rang und Trending-Score.
- Symbolstrom: die 30 juengsten Nachrichten mit Zeitstempel, Autor und
  Bullish/Bearish-Markierung.

Grundsaetze:
- Ein Abruf liefert eine Stichprobe (30 Nachrichten), nie eine Vollzaehlung.
  Sind alle 30 juenger als eine Stunde, ist die Stundenzahl eine Untergrenze
  (``truncated_1h``), keine Messung.
- Kein Rohtext, keine Autorenkennung wird gespeichert: nur Zaehlwerte.
- DNS-/Netzfehler sind UNKNOWN (Abrufpause), nie "keine Erwaehnungen".
- Eigenes Budget (``stocktwits``: 400/Tag, 2000/Woche; bis 10.7.1 150/800)
  unterhalb des dokumentierten Anbieterlimits (200 Abfragen je Stunde ohne
  Anmeldung).
- 10.8.0: Ist die Stundenzahl nur eine Untergrenze (alle 30 Nachrichten der
  ersten Seite juenger als eine Stunde), werden bis zu drei aeltere Seiten
  ueber den ``max``-Cursor nachgeladen, bis die Stunde vollstaendig ist.
  Aktive Karten (Ausloeser/Hype-Kandidat) werden alle 15 Minuten statt
  einmal je Stunde gezaehlt; die Trending-Liste alle 15 statt 30 Minuten.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
import math
import time

from . import research
from .control import Blocked, digest, encode

LOG = logging.getLogger(__name__)

TRENDING_URL = "https://api.stocktwits.com/api/2/trending/symbols.json"
STREAM_URL = "https://api.stocktwits.com/api/2/streams/symbol/{symbol}.json"
TRENDING_TTL = 900
STREAM_TTL = 3600
STREAM_TTL_ACTIVE = 900
SAMPLE_SIZE = 30
WINDOW = 3600
MAX_BYTES = 2_000_000
MAX_EXTRA_PAGES = 3
FORBIDDEN_PAUSE = 4*3600


def _stamp(value):
    try:
        stamp = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        return stamp.timestamp()
    except (ValueError, TypeError):
        return None


def normalise_trending(raw, *, now):
    """Aktien (keine Krypto ``.X``) mit Rang, Score und Watchlist-Zahl."""
    rows = raw.get("symbols") if isinstance(raw, dict) else None
    if not isinstance(rows, list):
        raise ValueError("stocktwits: unerwartetes Trending-Format")
    out, seen = [], set()
    for position, item in enumerate(rows[:60], start=1):
        if not isinstance(item, dict):
            continue
        symbol = str(item.get("symbol") or "").strip().upper()
        klass = str(item.get("instrument_class") or "")
        if klass and klass.lower() != "stock":
            continue
        if not research.TICKER.fullmatch(symbol) or symbol.endswith(".X") or symbol in seen:
            continue
        seen.add(symbol)
        out.append({"symbol": symbol, "source": "stocktwits_trending", "source_family": "stocktwits",
                    "rank": research._count(item.get("rank")) or position,
                    "trending_score": _number(item.get("trending_score")),
                    "watchlist_count": research._count(item.get("watchlist_count")),
                    "title": str(item.get("title") or "")[:80],
                    "observed_at": now, "url": TRENDING_URL,
                    "detail": "StockTwits-Trending ist eine Rangliste ohne Erwaehnungszahl; "
                              "der Symbolstrom liefert die Stundenzaehlung."})
    if not out:
        raise ValueError("stocktwits: keine Aktien in der Trending-Liste")
    return out


def _number(value):
    try:
        v = float(value)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def _messages(raw):
    messages = raw.get("messages") if isinstance(raw, dict) else None
    if not isinstance(messages, list):
        raise ValueError("stocktwits: unerwartetes Stromformat")
    return messages


def oldest_id(raw):
    """Kleinste Nachrichten-ID einer Seite (Cursor fuer ``max``); None ohne IDs."""
    ids = []
    for item in _messages(raw):
        try:
            ids.append(int(item.get("id")))
        except (TypeError, ValueError, AttributeError):
            continue
    return min(ids) if ids else None


def normalise_stream(symbol, raw, *, now, pages=1):
    """Zaehlwerte der letzten Stunde aus der Stichprobe (30 je Seite); Rohtexte verworfen."""
    messages = _messages(raw)
    stamps, authors, bullish, bearish = [], set(), 0, 0
    for item in messages[:SAMPLE_SIZE*max(1, int(pages))]:
        if not isinstance(item, dict):
            continue
        stamp = _stamp(item.get("created_at"))
        if stamp is None or stamp > now + 300:
            continue
        stamps.append(stamp)
        if now - stamp > WINDOW:
            continue
        user = item.get("user") if isinstance(item.get("user"), dict) else {}
        author = user.get("id")
        if author is not None:
            authors.add(digest(["st-author", author])[:16])  # nur zum Zaehlen, nie gespeichert
        sentiment = ((item.get("entities") or {}).get("sentiment") or {}) if isinstance(item.get("entities"), dict) else {}
        basic = str(sentiment.get("basic") or "").lower() if isinstance(sentiment, dict) else ""
        bullish += basic == "bullish"
        bearish += basic == "bearish"
    recent = [s for s in stamps if now - s <= WINDOW]
    truncated = len(stamps) >= SAMPLE_SIZE*max(1, int(pages)) and bool(stamps) and now - min(stamps) <= WINDOW
    meta = raw.get("symbol") if isinstance(raw.get("symbol"), dict) else {}
    return {"symbol": symbol, "source": "stocktwits", "source_family": "stocktwits",
            "mentions": len(recent), "messages_1h": len(recent), "authors_1h": len(authors),
            "bullish_1h": bullish, "bearish_1h": bearish,
            "messages_sampled": len(stamps), "pages": max(1, int(pages)),
            "sample_span_seconds": (max(stamps) - min(stamps)) if stamps else None,
            "truncated_1h": truncated,
            "watchlist_count": research._count(meta.get("watchlist_count")),
            "observed_at": now, "url": STREAM_URL.format(symbol=symbol),
            "detail": (f"Stundenzahl ist eine Untergrenze: alle {len(stamps)} Stichprobennachrichten "
                       f"({max(1, int(pages))} Seite(n)) liegen in der letzten Stunde."
                       if truncated else f"Nachrichten der letzten Stunde aus {len(stamps)} Stichprobennachrichten "
                       f"({max(1, int(pages))} Seite(n)); keine Vollzaehlung.")}


def merge_pages(pages):
    """Mehrere Seiten desselben Stroms zu einer Nachrichtenliste (neueste zuerst)."""
    out = {"messages": [], "symbol": None}
    for raw in pages:
        if not isinstance(raw, dict):
            continue
        if out["symbol"] is None and isinstance(raw.get("symbol"), dict):
            out["symbol"] = raw["symbol"]
        out["messages"].extend(m for m in _messages(raw) if isinstance(m, dict))
    if out["symbol"] is None:
        out.pop("symbol")
    return out


def _get(url, *, session=None, now=None):
    import requests
    now = time.time() if now is None else now
    client = session or requests.Session()
    try:
        response = client.get(url, timeout=(3.05, 10), stream=True,
                              headers={"User-Agent": "NEXUS-PULSAR/1.0 private research", "Accept": "application/json"})
        if response.status_code == 429:
            try:
                delay = max(900, min(7200, int(response.headers.get("Retry-After", 1800))))
            except (TypeError, ValueError):
                delay = 1800
            research.cache_put("backoff:stocktwits", {"status": 429}, delay, now=now)
            raise Blocked("stocktwits: Rate-Limit; spaeter erneut pruefen")
        if response.status_code == 403:
            # 10.8.0: Am 18.09.2026 antwortete StockTwits auf Trending UND Strom mit
            # 403 (20 Abrufe am Tag, weit unter dem Limit) -- eine Anbieter-Sperre,
            # keine Ratengrenze. Vier Stunden Pause statt jede Stunde erneut anzuklopfen.
            research.cache_put("backoff:stocktwits", {"status": 403, "failure_kind": "error",
                               "detail": "StockTwits verweigert den Zugang (HTTP 403); keine Zaehlwerte"}, FORBIDDEN_PAUSE, now=now)
            raise Blocked("stocktwits: Zugang verweigert (HTTP 403); Abrufpause")
        response.raise_for_status()
        raw = bytearray()
        for chunk in response.iter_content(chunk_size=32768):
            raw.extend(chunk)
            if len(raw) > MAX_BYTES:
                raise ValueError("Antwort groesser als das PULSAR-Datenlimit")
        return json.loads(raw)
    finally:
        if session is None:
            client.close()


def _guarded(key, ttl, url, parse, *, now, session=None, max_age=None, fetch=None):
    old = research.cached(key, now=now)
    if old and (max_age is None or now - float(old.get("saved") or 0) <= max_age):
        return old["data"]
    if research.cached("backoff:stocktwits", now=now):
        raise Blocked("stocktwits: Abrufpause")
    token = research.reserve("stocktwits", now=now)
    overall = research.reserve("data", now=now)
    try:
        # ``fetch`` (10.8.0) darf Folgeseiten laden; jede Seite reserviert ihr eigenes Budget.
        rows = parse(fetch(url, session=session) if fetch else _get(url, session=session, now=now))
        research.cache_put(key, rows, ttl, now=now)
        research.settle(token, 1); research.settle(overall, 1)
        research._social_status("stocktwits", True, now)
        return rows
    except Exception as exc:
        status = research._social_status("stocktwits", False, now, exc=exc)
        if not research.cached("backoff:stocktwits", now=now):
            delay = 3600 if status["failure_kind"] in {"tls", "error"} else 900
            research.cache_put("backoff:stocktwits", {"error": type(exc).__name__,
                               "failure_kind": status["failure_kind"], "detail": status["detail"]}, delay, now=now)
        raise


def trending(*, now=None, session=None):
    """Aktien der Trending-Liste (hoechstens alle 15 Minuten abgerufen; bis 10.7.1: 30)."""
    now = time.time() if now is None else now
    return _guarded("stocktwits:trending", TRENDING_TTL, TRENDING_URL,
                    lambda raw: normalise_trending(raw, now=now), now=now, session=session)


def _fetch_hour(symbol, url, *, now, session=None):
    """Erste Seite plus bis zu MAX_EXTRA_PAGES aeltere Seiten, bis die Stunde vollstaendig ist (10.8.0).

    Jede Folgeseite reserviert eigenes Budget; ohne Budget oder bei einem
    Fehler bleibt die bisher gelesene Stichprobe (Untergrenze) gueltig.
    """
    pages = [_get(url, session=session, now=now)]
    for _ in range(MAX_EXTRA_PAGES):
        raw = pages[-1]
        stamps = [s for s in (_stamp(m.get("created_at")) for m in _messages(raw) if isinstance(m, dict)) if s]
        if len(stamps) < SAMPLE_SIZE or not stamps or now - min(stamps) > WINDOW:
            break  # Stunde vollstaendig abgedeckt oder Seite unvollstaendig
        cursor = oldest_id(raw)
        if cursor is None:
            break
        try:
            token = research.reserve("stocktwits", now=now)
            overall = research.reserve("data", now=now)
            pages.append(_get(f"{url}?max={cursor - 1}", session=session, now=now))
            research.settle(token, 1); research.settle(overall, 1)
        except Exception as exc:
            LOG.debug("StockTwits-Folgeseite fuer %s nicht geladen: %s", symbol, exc)
            break
    return merge_pages(pages) if len(pages) > 1 else pages[0], len(pages)


def stream(symbol, *, now=None, session=None, active=False):
    """Stundenzaehlung fuer ein Symbol: einmal je Stunde, fuer aktive Karten alle 15 Minuten (10.8.0)."""
    now = time.time() if now is None else now
    if not research.TICKER.fullmatch(str(symbol or "")):
        raise ValueError("Ungueltiges Symbol")

    def fetch(url, *, session=None):
        raw, pages = _fetch_hour(symbol, url, now=now, session=session)
        return {"raw": raw, "pages": pages}

    row = _guarded("stocktwits:stream:" + symbol, STREAM_TTL_ACTIVE if active else STREAM_TTL,
                   STREAM_URL.format(symbol=symbol),
                   lambda got: normalise_stream(symbol, got["raw"], now=now, pages=got["pages"]),
                   now=now, session=session, max_age=STREAM_TTL_ACTIVE if active else None, fetch=fetch)
    if row.get("observed_at") == now:
        # Frische Messung in die eigene Zeitreihe (Stundenmedian-Basislinie).
        try:
            from .research_history import record
            stored = {**row, "evidence_id": digest(research.facts(row))}
            with research.db() as con:
                con.execute("INSERT OR IGNORE INTO observations VALUES(?,?,?,?,?)",
                            (digest([stored["evidence_id"], now]), "stocktwits", now, symbol, encode(stored)))
                record(con, "stocktwits", symbol, now, stored, stored["evidence_id"])
        except Exception:
            # Zeitreihe ist Zusatz; der Messwert selbst bleibt gueltig.
            LOG.debug("StockTwits-Zeitreihe fuer %s nicht gespeichert", symbol, exc_info=True)
    return row


def activity(row, baseline=None):
    """Einordnung: belegt / zu duenn / unbekannt -- absolut oder gegen eigene Basislinie."""
    if not row:
        return None, "StockTwits-Strom nicht abgerufen"
    messages, authors = row.get("messages_1h"), row.get("authors_1h")
    if messages is None or authors is None:
        return None, "StockTwits-Zaehlung unvollstaendig"
    median = (baseline or {}).get("median_mentions")
    ready = bool((baseline or {}).get("ready_14d"))
    if ready and median:
        ratio = messages / max(1.0, float(median))
        if messages >= 10 and authors >= 6 and ratio >= 3.0:
            return {"kind": "STOCKTWITS_WACHSTUM", "messages": messages, "authors": authors, "ratio": ratio,
                    "truncated": bool(row.get("truncated_1h")),
                    "detail": f"{messages} StockTwits-Nachrichten/h von {authors} Konten, {ratio:.1f}x der eigenen 14-Tage-Basis"
                              + (" (Untergrenze)" if row.get("truncated_1h") else "") + "."}, None
        return None, (f"StockTwits ohne Spike: {messages} Nachrichten/h, {ratio:.1f}x der eigenen Basis "
                      f"(noetig: >= 10 Nachrichten, >= 6 Konten, >= 3x)")
    if messages >= 20 and authors >= 10:
        return {"kind": "STOCKTWITS_ABSOLUT", "messages": messages, "authors": authors, "ratio": None,
                "truncated": bool(row.get("truncated_1h")),
                "detail": f"{messages} StockTwits-Nachrichten/h von {authors} Konten (absolute Schwelle; "
                          "eigene Basislinie noch nicht 14 Tage alt)."}, None
    return None, (f"StockTwits zu leise: {messages} Nachrichten/h von {authors} Konten "
                  "(ohne Basislinie noetig: >= 20 und >= 10 Konten)")


__all__ = ["trending", "stream", "activity", "normalise_trending", "normalise_stream", "merge_pages", "oldest_id"]
