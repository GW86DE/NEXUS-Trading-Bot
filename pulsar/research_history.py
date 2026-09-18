"""Observed-only hourly samples; no inferred absence or fabricated past dates.

All writes share the research transaction. Trading/approval journals are never
opened here. Raw samples retain seven days; compact samples retain 35 days.
The baseline remains a median of observed hourly counts per UTC date, NOT a
claim that a full day or all Reddit was observed.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import math
import statistics
import time

DAY = 86400
METHOD = "OBSERVED_HOURLY_MEDIAN_UTC_V2"


def schema(con):
    con.executescript('''
      CREATE INDEX IF NOT EXISTS pulsar_source_observed ON observations(source,symbol,observed);
      CREATE INDEX IF NOT EXISTS pulsar_raw_age ON observations(observed);
      CREATE TABLE IF NOT EXISTS attention_hours (
        source TEXT NOT NULL, symbol TEXT NOT NULL, hour INTEGER NOT NULL,
        observed REAL NOT NULL, mentions INTEGER NOT NULL CHECK(mentions>=0),
        evidence_id TEXT NOT NULL, PRIMARY KEY(source,symbol,hour));
      CREATE INDEX IF NOT EXISTS pulsar_hours_age ON attention_hours(observed);
      CREATE TABLE IF NOT EXISTS research_maintenance (
        key TEXT PRIMARY KEY, value TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS attention_coverage_hours (
        source TEXT NOT NULL, page INTEGER NOT NULL, hour INTEGER NOT NULL,
        observed REAL NOT NULL, symbols TEXT NOT NULL, evidence_id TEXT NOT NULL,
        PRIMARY KEY(source,page,hour));
    ''')


def _sample(stamp, payload):
    data = json.loads(payload) if isinstance(payload, str) else payload
    count = data.get("mentions") if isinstance(data, dict) else None
    if (type(stamp) not in (int, float) or not math.isfinite(stamp) or stamp < 0
            or type(count) not in (int, float) or not math.isfinite(count)
            or count < 0 or not float(count).is_integer()):
        raise ValueError("Unbelegte Aufmerksamkeitsmessung")
    return int(count)


def record(con, source, symbol, observed, payload, evidence_id):
    """At most one actual observation per UTC hour, always keep the latest."""
    count = _sample(observed, payload)
    con.execute('''INSERT INTO attention_hours VALUES(?,?,?,?,?,?)
        ON CONFLICT(source,symbol,hour) DO UPDATE SET observed=excluded.observed,
        mentions=excluded.mentions,evidence_id=excluded.evidence_id
        WHERE excluded.observed > attention_hours.observed''',
        (source, symbol, int(observed // 3600), observed, count, evidence_id))


def record_coverage(con, source, page, observed, rows):
    """Record the actually returned subset, without inferring zero mentions."""
    from .control import digest, encode
    symbols = sorted({r["symbol"] for r in rows})
    evidence = digest({"source": source, "page": page, "rows": rows})
    con.execute('''INSERT INTO attention_coverage_hours VALUES(?,?,?,?,?,?)
        ON CONFLICT(source,page,hour) DO UPDATE SET observed=excluded.observed,
        symbols=excluded.symbols,evidence_id=excluded.evidence_id
        WHERE excluded.observed > attention_coverage_hours.observed''',
        (source, page, int(observed//3600), observed, encode(symbols), evidence))


def baseline(con, symbol, source, now):
    samples = {}
    start, end = now - 28*DAY, now - DAY
    for row in con.execute("SELECT hour,observed,mentions FROM attention_hours WHERE source=? AND symbol=? "
                           "AND observed BETWEEN ? AND ?", (source, symbol, start, end)):
        samples[row["hour"]] = (row["observed"], row["mentions"])
    # Old installations may not have been compacted yet. Read their genuine
    # records, without pretending they already had source coverage snapshots.
    invalid = 0
    for row in con.execute("SELECT observed,payload FROM observations WHERE source=? AND symbol=? "
                           "AND observed BETWEEN ? AND ? ORDER BY observed", (source, symbol, start, end)):
        try:
            count = _sample(row["observed"], row["payload"])
        except (ValueError, TypeError, json.JSONDecodeError):
            invalid += 1
            continue
        hour = int(row["observed"] // 3600)
        if hour not in samples or row["observed"] > samples[hour][0]:
            samples[hour] = (row["observed"], count)
    days = {}
    for observed, count in samples.values():
        day = datetime.fromtimestamp(observed, timezone.utc).date().isoformat()
        days.setdefault(day, []).append(count)
    daily = {day: statistics.median(vals) for day, vals in sorted(days.items())}
    covered, absent = set(), set()
    for row in con.execute("SELECT observed,symbols FROM attention_coverage_hours WHERE source=? "
                           "AND observed BETWEEN ? AND ?", (source, start, end)):
        day = datetime.fromtimestamp(row["observed"], timezone.utc).date().isoformat()
        covered.add(day)
        if symbol not in json.loads(row["symbols"]):
            absent.add(day)
    # This is an actual timestamp window, which can overlap 28 UTC dates; do
    # not silently call it 28 complete daily observations.
    window_dates = int((datetime.fromtimestamp(end, timezone.utc).date()
                        - datetime.fromtimestamp(start, timezone.utc).date()).days)+1
    unknown_days = max(0, window_dates-len(set(daily) | covered))
    return {"days": len(daily), "median_mentions": statistics.median(daily.values()) if daily else None,
        "ready_14d": len(daily) >= 14, "ready_28d": len(daily) >= 28,
        "method": METHOD, "hours": len(samples), "daily_samples": daily,
        "censored_days": len(absent-set(daily)), "absence_inferred": False, "invalid_samples": invalid,
        "source_subset_days": len(covered), "absent_from_observed_subset_days": sorted(absent-set(daily)),
        "window_dates": window_dates, "unknown_days": unknown_days,
        "coverage": "PRESENT_ROWS_ONLY", "source": source,
        "detail": "Median beobachteter Stundenwerte je UTC-Datum; fehlende Stunden/Tage sind unbekannt. "
                  "Die Topliste bildet keine vollstaendige Instrument- oder Reddit-Abdeckung ab."}


def maintain(*, now=None, force=False, batch=5000):
    """Bounded, atomic compaction. DELETE reuses space; no online VACUUM."""
    from . import research
    now = time.time() if now is None else now
    batch = max(1, min(5000, int(batch)))
    if not force:
        with research.db(readonly=True) as con:
            old = con.execute("SELECT value FROM research_maintenance WHERE key='status'").fetchone()
            previous = json.loads(old[0]) if old else {}
            if 0 <= now-previous.get('at', 0) < 3600:
                return previous
    with research.db() as con:
        old = con.execute("SELECT value FROM research_maintenance WHERE key='status'").fetchone()
        previous = json.loads(old[0]) if old else {}
        if not force and 0 <= now - previous.get("at", 0) < 3600:
            return previous
        rows = con.execute("SELECT * FROM observations WHERE observed<? ORDER BY observed LIMIT ?",
                           (now-7*DAY, batch)).fetchall()
        removed = compacted = invalid = 0
        for row in rows:
            if row["observed"] >= now-35*DAY:
                try:
                    record(con, row["source"], row["symbol"], row["observed"], row["payload"], row["id"])
                except (ValueError, TypeError, json.JSONDecodeError):
                    invalid += 1
                    continue  # Preserve malformed in-window evidence, visible in status.
                compacted += 1
            con.execute("DELETE FROM observations WHERE id=?", (row["id"],))
            removed += 1
        # Also bound the deletion of compact samples, important on the Pi.
        pruned = con.execute("DELETE FROM attention_hours WHERE rowid IN "
            "(SELECT rowid FROM attention_hours WHERE observed<? LIMIT ?)", (now-35*DAY, batch)).rowcount
        con.execute("DELETE FROM attention_coverage_hours WHERE rowid IN "
            "(SELECT rowid FROM attention_coverage_hours WHERE observed<? LIMIT ?)", (now-35*DAY, batch))
        cache_removed = con.execute("DELETE FROM cache WHERE key IN (SELECT key FROM cache WHERE expires<? "
            "AND key NOT IN ('top5','status','coverage') LIMIT ?)", (now-7*DAY, batch)).rowcount
        stats = {"at": now, "raw_removed": removed, "compacted": compacted, "hours_removed": pruned,
            "expired_cache_removed": cache_removed, "invalid_preserved": invalid,
            "backlog": bool(con.execute("SELECT 1 FROM observations WHERE observed<? LIMIT 1", (now-7*DAY,)).fetchone()),
            "raw_retention_days": 7, "compact_retention_days": 35,
            "audit_preserved": True, "vacuum_run": False,
            "detail": "Rohdaten werden in begrenzten Batches verdichtet. Assessments, Budgets und "
                      "Handelsjournale bleiben erhalten; keine garantierte Gesamtdateigroesse."}
        con.execute("INSERT OR REPLACE INTO research_maintenance VALUES('status',?)", (research.encode(stats),))
        return stats


def status():
    from . import research
    with research.db(readonly=True) as con:
        row = con.execute("SELECT value FROM research_maintenance WHERE key='status'").fetchone()
        result = json.loads(row[0]) if row else {"detail": "Verdichtung noch nicht gelaufen"}
        result.update(page_count=con.execute("PRAGMA page_count").fetchone()[0],
                      free_pages=con.execute("PRAGMA freelist_count").fetchone()[0],
                      page_size=con.execute("PRAGMA page_size").fetchone()[0])
        return result
