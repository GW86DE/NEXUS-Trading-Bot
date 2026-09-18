"""Bounded, cached research. Aggregate mentions are attention, not buy proof."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
import json
import math
import os
import re
import secrets
import sqlite3
import time
import threading

from .control import Blocked, digest, encode, week

URLS = {"apewisdom": "https://apewisdom.io/api/v1.0/filter/all-stocks",
        # Das TLS-Zertifikat von api.tradestie.com ist abgelaufen (gemessen
        # 16.09.2026); die Hauptdomain liefert dieselbe API mit gueltigem
        # Zertifikat. TLS-Pruefung bleibt unveraendert aktiv.
        "tradestie": "https://tradestie.com/api/v1/apps/reddit",
        # 10.5.0: zweite Social-Familie; eigener Abrufweg in pulsar/stocktwits.py.
        "stocktwits": "https://api.stocktwits.com/api/2/trending/symbols.json"}
TICKER = re.compile(r"^[A-Z][A-Z0-9]{0,9}(?:[.-][A-Z0-9]{1,4})?$")
LIMITS = {"social": (80, 400), "data": (300, 1500), "web_search": (4, 16), "web_jobs": (2, 8),
          "stocktwits": (150, 800), "finra": (40, 200)}
APE_FILTERS = {"all-stocks", "stocks", "wallstreetbets", "investing", "options"}


def facts(value):
    """Canonical facts: fetching unchanged data is not new evidence."""
    if isinstance(value, dict):
        return {k: facts(v) for k, v in value.items() if k not in
                {"observed_at", "source_updated_at", "fetched_at", "_ts", "evidence_id", "id"}}
    if isinstance(value, list):
        return [facts(v) for v in value]
    return value


def path():
    return Path(os.getenv("TRADINGBOT_TEST_STATE_DIR", "").strip() or
                Path(__file__).resolve().parents[1]) / "pulsar_research.sqlite"


_schema_lock = threading.RLock()
_schema_ready = set()


@contextmanager
def db(*, readonly=False):
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(p, timeout=10)
    con.row_factory = sqlite3.Row
    try:
        con.execute("PRAGMA synchronous=FULL")
        identity = (str(p.resolve()), p.stat().st_ino)
        with _schema_lock:
            if identity not in _schema_ready:
                con.execute("PRAGMA journal_mode=WAL")
                _initialize(con)
                _schema_ready.add(identity)
        if readonly:
            con.execute("PRAGMA query_only=ON")
            con.execute("BEGIN")
        else:
            con.execute("BEGIN IMMEDIATE")
        yield con
        con.commit()
    except BaseException:
        con.rollback()
        raise
    finally:
        con.close()


def _initialize(con):
    con.executescript('''
          CREATE TABLE IF NOT EXISTS observations (
            id TEXT PRIMARY KEY, source TEXT NOT NULL, observed REAL NOT NULL,
            symbol TEXT NOT NULL, payload TEXT NOT NULL);
          CREATE INDEX IF NOT EXISTS pulsar_observed ON observations(symbol,observed);
          CREATE TABLE IF NOT EXISTS cache (
            key TEXT PRIMARY KEY, saved REAL NOT NULL, expires REAL NOT NULL, payload TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS usage (
            id TEXT PRIMARY KEY, at REAL NOT NULL, day TEXT NOT NULL,
            week TEXT NOT NULL, month TEXT NOT NULL, kind TEXT NOT NULL,
            reserved REAL NOT NULL, actual REAL, status TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS assessments (
            id TEXT PRIMARY KEY, at REAL NOT NULL, symbol TEXT NOT NULL,
            evidence_hash TEXT NOT NULL, payload TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS enrichment_candidates (
            symbol TEXT PRIMARY KEY, first_day TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS observation_weeks (
            week TEXT PRIMARY KEY, at REAL NOT NULL, payload TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS universe_guests (
            symbol TEXT PRIMARY KEY, first_seen REAL NOT NULL, last_seen REAL NOT NULL);
        ''')
    from .research_history import schema
    schema(con)
    # 10.5.0: Vorwaertsmessung der Hype-Spur (attention_outcomes).
    from .measurement import schema as outcomes_schema
    outcomes_schema(con)


def reserve(kind, amount=1., *, now=None):
    now = time.time() if now is None else now
    dt = datetime.fromtimestamp(now, ZoneInfo("Europe/Berlin"))
    day, iso, month = dt.date().isoformat(), week(now), dt.strftime("%Y-%m")
    if kind not in {*LIMITS, "ai"} or not math.isfinite(amount) or amount <= 0:
        raise ValueError("Ungueltige Budgetreservierung")
    limits = (1., 2., 10.) if kind == "ai" else (*LIMITS[kind], float("inf"))
    with db() as con:
        for column, period, limit in zip(("day", "week", "month"), (day, iso, month), limits):
            total = con.execute(f"SELECT COALESCE(SUM(COALESCE(actual,reserved)),0) FROM usage "
                                f"WHERE kind=? AND {column}=?", (kind, period)).fetchone()[0]
            if total + amount > limit + 1e-10:
                raise Blocked(f"PULSAR-{kind}-Budget fuer {period} ausgeschöpft")
        token = secrets.token_hex(12)
        con.execute("INSERT INTO usage VALUES(?,?,?,?,?,?,?,?,?)",
                    (token, now, day, iso, month, kind, amount, None, "RESERVED"))
        return token


def settle(token, actual):
    if actual is None or not math.isfinite(float(actual)) or float(actual) < 0:
        return  # Unknown usage keeps the full reservation.
    with db() as con:
        row = con.execute("SELECT * FROM usage WHERE id=?", (token,)).fetchone()
        if row is None:
            raise Blocked("Unbekannte Budgetreservierung")
        if row['actual'] is not None:
            if not math.isclose(float(row['actual']),float(actual),rel_tol=1e-9,abs_tol=1e-10):
                raise Blocked('Widerspruechliche erneute Budgetabrechnung')
            return
        con.execute("UPDATE usage SET actual=?,status='CONFIRMED' WHERE id=?",
                    (float(actual), token))


def cached(key, *, stale=False, now=None):
    now = time.time() if now is None else now
    with db(readonly=True) as con:
        row = con.execute("SELECT * FROM cache WHERE key=?", (key,)).fetchone()
        if not row or (not stale and row["expires"] < now):
            return None
        return {"saved": row["saved"], "expires": row["expires"], "data": json.loads(row["payload"])}


def cache_put(key, data, ttl, *, now=None):
    now = time.time() if now is None else now
    with db() as con:
        con.execute("INSERT INTO cache VALUES(?,?,?,?) ON CONFLICT(key) DO UPDATE SET "
                    "saved=excluded.saved,expires=excluded.expires,payload=excluded.payload",
                    (key, now, now + ttl, encode(data)))


def _count(value):
    if isinstance(value, bool):
        return None
    try:
        n = float(value)
        return int(n) if math.isfinite(n) and n >= 0 and n.is_integer() else None
    except (ValueError, TypeError):
        return None


def normalise(source, raw):
    records = raw.get("results") if source == "apewisdom" and isinstance(raw, dict) else raw
    if not isinstance(records, list):
        raise ValueError(f"{source}: unerwartetes Antwortformat")
    out, seen = [], set()
    for item in records[:100 if source == "apewisdom" else 50]:
        if not isinstance(item, dict):
            continue
        symbol = str(item.get("ticker") or item.get("symbol") or "").strip().upper()
        if not TICKER.fullmatch(symbol) or symbol in seen:
            continue
        seen.add(symbol)
        current = _count(item.get("mentions" if source == "apewisdom" else "no_of_comments"))
        previous = _count(item.get("mentions_24h_ago")) if source == "apewisdom" else None
        if current is None:
            continue
        sentiment_score = item.get("sentiment_score")
        try:
            sentiment_score = float(sentiment_score)
            if not math.isfinite(sentiment_score):
                sentiment_score = None
        except (TypeError, ValueError):
            sentiment_score = None
        out.append({"symbol": symbol, "source": source, "mentions": current,
            "mentions_24h_ago": previous, "growth_ratio": (current / previous if previous else None),
            "newly_observed": previous == 0,
            "rank": _count(item.get("rank")), "rank_24h_ago": _count(item.get("rank_24h_ago")),
            "upvotes": _count(item.get("upvotes")), "sentiment_score": sentiment_score,
            "sentiment": str(item.get("sentiment") or "")[:40],
            "unique_authors": None, "argument_clusters": None, "posts": None,
            "source_family": "reddit_aggregate", "url": URLS[source]})
    if not out:
        raise ValueError(f"{source}: keine brauchbaren Datensaetze")
    return out


def social_source_status(*, now=None):
    """Local status only: old errors survive recovery without claiming outage."""
    now = time.time() if now is None else now
    out = {}
    for source in URLS:
        record = cached(f"source_status:{source}", stale=True, now=now)
        row = dict(record['data']) if record else {}
        pause = cached(f"backoff:{source}", now=now)
        if not row and pause:
            row = {'ok': False, 'last_attempt': pause['saved'], 'error_at': pause['saved'],
                   'detail': 'Historischer Abruffehler; Details nicht gespeichert',
                   'failure_kind': str(pause['data'].get('error') or pause['data'].get('status') or 'error')}
        error = row.get('error_at')
        age = max(0, now-row['last_attempt']) if row.get('last_attempt') is not None else None
        state = 'backoff' if pause else 'unknown' if not row else 'stale' if age is None or age > 3600 else 'ok' if row.get('ok') else 'error'
        def iso(value):
            return datetime.fromtimestamp(value, timezone.utc).isoformat() if isinstance(value, (int, float)) else ''
        out[source] = {**row, 'state': state, 'healthy': state == 'ok',
            'last_checked_at': iso(row.get('last_attempt')), 'last_success_at': iso(row.get('last_success')),
            'age_seconds': age, 'error_at': iso(error),
            'error_age_seconds': max(0, now-error) if isinstance(error, (int, float)) else None,
            'error_scope': ('HISTORICAL' if state in {'ok', 'stale'} else 'CURRENT') if error is not None else 'NONE',
            'current_effect': 'SOURCE_PAUSED' if pause else 'SOURCE_UNAVAILABLE' if state == 'error' else 'UNKNOWN' if state in {'unknown', 'stale'} else 'NONE',
            'next_retry_at': iso(pause['expires']) if pause else '',
            'backoff_seconds': max(0, int(pause['expires']-now)) if pause else 0,
            'impact': ('Optionale Stimmungsdaten fehlen; andere PULSAR-Quellen und Core arbeiten weiter'
                       if source == 'tradestie' and state in {'backoff', 'error'} else
                       'Zweite Social-Familie fehlt; StockTwits-Bestaetigung bleibt UNKNOWN, Reddit-Aggregate und Kursdaten arbeiten weiter'
                       if source == 'stocktwits' and state in {'backoff', 'error'} else
                       'Aktueller Quellenstand ungeprueft' if state in {'unknown', 'stale'} else
                       'Aufmerksamkeit aus dieser Quelle derzeit nicht verfuegbar' if state in {'backoff', 'error'} else
                       'Keine aktuelle Fehlerwirkung')}
    return out


def _social_status(source, ok, now, *, exc=None):
    from provider_safety import redact
    old = (cached(f"source_status:{source}", stale=True, now=now) or {}).get('data', {})
    row = {**old, 'ok': ok, 'last_attempt': now,
           'last_success': now if ok else old.get('last_success'),
           'consecutive_failures': 0 if ok else int(old.get('consecutive_failures', 0)) + 1}
    if exc is not None:
        import requests
        kind = 'tls' if isinstance(exc, requests.exceptions.SSLError) else 'rate_limit' if 'Rate-Limit' in str(exc) or '429' in str(exc) else 'error'
        row.update(error_at=now, detail=redact(exc)[:300], last_error_detail=redact(exc)[:300], failure_kind=kind)
    else:
        row.update(detail='Quelle erfolgreich abgerufen', failure_kind='')
    cache_put(f"source_status:{source}", row, 365*86400, now=now)
    return row


def fetch_social(source, *, session=None, now=None, feed="all-stocks", page=1):
    """One bounded public request; 429 cooldown is shared and persistent."""
    if source not in URLS:
        raise ValueError("Quelle nicht zugelassen")
    if feed not in APE_FILTERS or type(page) is not int or not 1 <= page <= 3:
        raise ValueError("Feed oder Seite nicht zugelassen")
    if source == "stocktwits":
        # 10.5.0: Trending-Liste ueber dieselbe Naht wie die Reddit-Aggregate,
        # damit Testdoubles und Abrufpausen einheitlich greifen.
        from .stocktwits import trending
        return trending(now=now, session=session)
    suffix = "" if feed == "all-stocks" and page == 1 else f":{feed}:{page}"
    key = f"social:{source}" + suffix
    url = URLS[source] if not suffix or source != "apewisdom" else f"https://apewisdom.io/api/v1.0/filter/{feed}/page/{page}"
    now = time.time() if now is None else now
    old = cached(key, now=now)
    if old:
        return old["data"]
    backoff = cached(f"backoff:{source}", now=now)
    if backoff:
        raise Blocked(f"{source}: Abrufpause bis {backoff['expires']:.0f}")
    token = reserve("social", now=now)
    # Every source request also counts against the overall data cap.
    overall = reserve("data", now=now)
    import requests
    client = session or requests.Session()
    try:
        response = client.get(url, timeout=(3.05, 10), stream=True,
                              headers={"User-Agent": "NEXUS-PULSAR/1.0 private research"})
        if response.status_code == 429:
            try:
                delay = max(900, min(7200, int(response.headers.get("Retry-After", 900))))
            except (TypeError, ValueError):
                delay = 900
            cache_put(f"backoff:{source}", {"status": 429}, delay, now=now)
            raise Blocked(f"{source}: Rate-Limit; spaeter erneut pruefen")
        response.raise_for_status()
        # Refuse oversized aggregate payloads; no arbitrary URL traversal.
        raw = bytearray()
        for chunk in response.iter_content(chunk_size=32768):
            raw.extend(chunk)
            if len(raw) > 2_000_000:
                raise ValueError("Antwort groesser als das PULSAR-Datenlimit")
        body = json.loads(raw)
        rows = normalise(source, body)
        if source == "apewisdom":
            cache_put("pages:" + feed, {"pages": _count(body.get("pages")) or 1}, 3600, now=now)
        observation_source = source if feed == "all-stocks" else source + ":" + feed
        for row in rows:
            row.update(discovery=discovery_state(row), feed=feed if source == "apewisdom" else "wallstreetbets", url=url,
                       observation_source=observation_source)
            row["observed_at"] = now
            row["source_updated_at"] = None  # The API supplies no update timestamp.
            row["evidence_id"] = digest(facts(row))
        with db() as con:
            from .research_history import record_coverage
            record_coverage(con, observation_source, page, now, rows)
            for row in rows:
                con.execute("INSERT OR IGNORE INTO observations VALUES(?,?,?,?,?)",
                            (digest([row["evidence_id"], now]), observation_source, now, row["symbol"], encode(row)))
                from .research_history import record
                record(con, observation_source, row["symbol"], now, row, row["evidence_id"])
        cache_put(key, rows, 3600, now=now)
        settle(token, 1); settle(overall, 1)
        _social_status(source, True, now)
        return rows
    except Exception as exc:
        status = _social_status(source, False, now, exc=exc)
        if not cached(f"backoff:{source}", now=now):
            delay = (min(86400, 3600 * 2**min(5, status['consecutive_failures']-1))
                     if status['failure_kind'] == 'tls' else 900)
            cache_put(f"backoff:{source}", {"error": type(exc).__name__,
                      "failure_kind": status['failure_kind'], "detail": status['detail']}, delay, now=now)
        raise
    finally:
        if "response" in locals():
            response.close()
        if session is None:
            client.close()


def discover(*, now=None):
    """At most five feeds/pages; partial coverage is visible, never invented."""
    from .control import settings
    now = time.time() if now is None else now
    rows, coverage, errors = [], [], []
    requests = [("all-stocks", 1), ("all-stocks", 2), ("all-stocks", 3),
                ("stocks", 1), ("investing", 1)]
    for feed, page in requests:
        if settings()["mode"] == "AUS":
            break
        pages = cached("pages:" + feed, now=now)
        if page > 1 and pages and page > pages["data"]["pages"]:
            continue
        try:
            batch = fetch_social("apewisdom", feed=feed, page=page, now=now)
            rows.extend(batch)
            coverage.append({"feed": feed, "page": page, "count": len(batch),
                             "observed_at": min(r["observed_at"] for r in batch)})
        except Exception as exc:
            errors.append(f"ApeWisdom {feed}/{page}: {str(exc)[:140]}")
    # Prefer the aggregate count. Never add overlapping forum counts together.
    merged = {}
    for row in rows:
        symbol = row["symbol"]
        if symbol not in merged:
            merged[symbol] = {**row, "feeds_seen": [row.get("feed", "all-stocks")]}
        else:
            merged[symbol]["feeds_seen"] = sorted(set(merged[symbol]["feeds_seen"] + [row.get("feed", "all-stocks")]))
    # 10.5.0 Zusatzkandidaten ohne Reddit-Schluessel: Volumen-Ausloeser aus den
    # Stundenkerzen des Kerns (kein Abruf) und StockTwits-Trending (eine
    # Abfrage je 30 Minuten). Sie tragen ihre Quelle im Feld ``source`` und
    # werden von der Kandidatenauswahl getrennt eingereiht.
    extra = []
    try:
        from .volume_watch import hourly_rows_from_store, scan_universe
        extra.extend(scan_universe(hourly_rows_from_store(), now=now)[:6])
    except Exception as exc:
        errors.append("Volumenscan: " + str(exc)[:140])
    current = settings()
    if current["mode"] != "AUS" and current.get("stocktwits"):
        try:
            extra.extend(fetch_social("stocktwits", now=now)[:6])
        except Exception as exc:
            errors.append("StockTwits-Trending: " + str(exc)[:140])
    cache_put("coverage", {"feeds": coverage, "errors": errors, "symbols": len(merged), "extra_candidates": len(extra),
                           "observed_at": now, "complete": bool(coverage) and not errors,
                           "transport_complete": bool(coverage) and not errors,
                           "instrument_coverage": "UNKNOWN", "absence_is_evidence": False,
                           "detail": "Abrufstatus der angefragten Seiten, keine vollstaendige Reddit-/Instrumentabdeckung; Filter bleiben getrennt"}, 3600, now=now)
    return list(merged.values()) + extra


EXTRA_SOURCES = {"volume_watch", "stocktwits_trending"}


def split_discoveries(rows):
    """Reddit-Aggregate von den 10.5.0-Zusatzkandidaten trennen (Quelle im Feld ``source``)."""
    reddit = [r for r in rows or [] if isinstance(r, dict) and r.get("source") not in EXTRA_SOURCES]
    extra = [r for r in rows or [] if isinstance(r, dict) and r.get("source") in EXTRA_SOURCES]
    return reddit, extra


def usage_summary(*, now=None):
    now = time.time() if now is None else now
    dt = datetime.fromtimestamp(now, ZoneInfo("Europe/Berlin"))
    periods = {"day": dt.date().isoformat(), "week": week(now), "month": dt.strftime("%Y-%m")}
    with db(readonly=True) as con:
        return {col: {r[0]: r[1] for r in con.execute(
            f"SELECT kind,SUM(COALESCE(actual,reserved)) FROM usage WHERE {col}=? GROUP BY kind", (p,))}
            for col, p in periods.items()}


def discovery_state(row):
    """Missing previous counts are unknown, never zero or infinite growth."""
    current, old = _count(row.get("mentions")), _count(row.get("mentions_24h_ago"))
    if current is None:
        return {"state": "UNBELEGT", "growth_score": 0., "detail": "Aktuelle Stichprobe fehlt"}
    if old is None:
        return {"state": "NEU_OHNE_VERGLEICH", "growth_score": 0., "detail":
            "Aktuell erfasst; fruehere Anzahl unbekannt. Recherche moeglich, kein belegter Anstieg."}
    ratio = current / max(20, old)
    score = math.log1p(current) * max(0., math.log2(max(1., ratio)))
    return {"state": "AUFFAELLIG" if current >= 40 and ratio >= 2 else "ERFASST",
        "growth_score": score, "reported_previous_zero": old == 0,
        "detail": "Quellenvergleich mit Mindestbasis 20; kein Nachweis unabhaengiger Menschen oder einer Push-Kampagne."}


def attention_order(rows):
    """Measured growth first; one of five discovery slots can carry a cold start.

    The separate slot has no score, 14-day proof or nomination privilege. It
    prevents permanently excluding newly covered instruments from research.
    """
    ranked = sorted(rows, key=lambda row: (-discovery_state(row)["growth_score"],
                                         -(_count(row.get("mentions")) or 0), row["symbol"]))
    new = next((r for r in ranked if _count(r.get("mentions_24h_ago")) is None
                and (_count(r.get("mentions")) or 0) >= 40), None)
    if new is not None and new not in ranked[:5]:
        ranked.remove(new)
        ranked.insert(4, new)
    return ranked


def baseline(symbol, source="apewisdom", *, now=None):
    from .research_history import baseline as observed_baseline
    now = time.time() if now is None else now
    with db(readonly=True) as con:
        return observed_baseline(con, symbol, source, now)


def save_assessment(card, *, now=None):
    now = time.time() if now is None else now
    payload = dict(card)
    payload["assessed_at"] = now
    payload["id"] = digest({"symbol": card["symbol"], "at": now, "evidence": card["evidence_hash"]})
    with db() as con:
        con.execute("INSERT OR REPLACE INTO assessments VALUES(?,?,?,?,?)",
                    (payload["id"], now, card["symbol"], card["evidence_hash"], encode(payload)))
    return payload


def latest_cards(limit=5):
    latest = cached("top5", stale=True)
    return (latest["data"] if latest else [])[:max(1, min(int(limit), 5))]


def reserve_enrichment(symbol, *, now=None):
    """Persist the 20-new-candidate daily cap before any enrichment request."""
    if not TICKER.fullmatch(symbol):
        raise ValueError("Ungueltiger Kandidat")
    now = time.time() if now is None else now
    day = datetime.fromtimestamp(now, ZoneInfo("Europe/Berlin")).date().isoformat()
    with db() as con:
        if con.execute("SELECT 1 FROM enrichment_candidates WHERE symbol=?", (symbol,)).fetchone():
            return
        if con.execute("SELECT COUNT(*) FROM enrichment_candidates WHERE first_day=?", (day,)).fetchone()[0] >= 20:
            raise Blocked("20 neue PULSAR-Kandidaten heute bereits angereichert")
        con.execute("INSERT INTO enrichment_candidates VALUES(?,?)", (symbol, day))


def merke_universumsgaeste(symbols, *, now=None):
    """Entdeckte Aktien mit belegter Identitaet fuer das normale Universum.

    10.3.0: PULSAR bleibt Zubringer -- diese Gaeste durchlaufen ausschliesslich
    die unveraenderte NEXUS-Standard-Pruefung (Qualifikation, Signale, News,
    Earnings, Risiko). Kein Sonderweg, keine PULSAR-Freigabe, kein Score.
    """
    now = time.time() if now is None else now
    cleaned = sorted({s for s in symbols if isinstance(s, str) and TICKER.fullmatch(s)})
    if not cleaned:
        return
    with db() as con:
        for symbol in cleaned:
            con.execute("INSERT INTO universe_guests VALUES(?,?,?) ON CONFLICT(symbol) "
                        "DO UPDATE SET last_seen=excluded.last_seen",
                        (symbol, now, now))
        con.execute("DELETE FROM universe_guests WHERE last_seen<?", (now-14*86400,))


def universe_guests(*, now=None, limit=10):
    """Aktuelle Gastliste: hoechstens ``limit`` Aktien, 14 Tage Verfall."""
    now = time.time() if now is None else now
    try:
        with db(readonly=True) as con:
            rows = [dict(r) for r in con.execute(
                "SELECT symbol, first_seen, last_seen FROM universe_guests "
                "WHERE last_seen>=? ORDER BY last_seen DESC, symbol LIMIT ?",
                (now-14*86400, max(0, min(20, int(limit)))))]
    except Exception:
        return []
    return rows


def stable_candidate(symbol, *, now=None):
    """Ein Hype-Kandidat ist stabil, wenn er sich ueber zwei Messungen haelt.

    10.3.0: Die alte Pflicht aus Katalysator-Primaerbeleg, vollstaendiger
    X-Tagesmessung und zwei Bewertungen im Abstand von 1-2 Stunden ist mit der
    Score-Maschine entfallen. Auf Squeeze-Zeitskala verlangt die Hype-Spur:
    juengste Bewertung hoechstens eine Stunde alt, eligible nach den aktuellen
    Regeln, frische Aufmerksamkeitsbeobachtung und eine zweite eligible
    Bewertung mindestens 10 Minuten davor (innerhalb von zwei Stunden) --
    ein einzelner Messausreisser nominiert nichts.
    """
    now = time.time() if now is None else now
    with db(readonly=True) as con:
        rows = [json.loads(r[0]) for r in con.execute(
            "SELECT payload FROM assessments WHERE symbol=? AND at>=? ORDER BY at DESC LIMIT 16",
            (symbol, now - 3*3600))]
    from .evidence import REVISION
    if (not rows or now - rows[0]["assessed_at"] > 3600 or not rows[0].get("eligible")
            or rows[0].get("rules_version") != REVISION):
        return None
    from .source_coordination import attention_fresh
    if not attention_fresh(rows[0], now=now):
        return None
    if any(r.get("eligible") and r.get("rules_version") == REVISION
            and 600 <= rows[0]["assessed_at"] - r["assessed_at"] <= 7200 for r in rows[1:]):
        return rows[0]
    return None
