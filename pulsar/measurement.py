"""Vorwaertsmessung der PULSAR-Hype-Spur (10.5.0).

Jede Beobachtung, die einen Ausloeser erreicht (relatives Volumen, Social-
Spike in einer Familie), wird mit Zeitpunkt, Kurs und Merkmalen gespeichert.
Spaeter traegt NEXUS den Schlusskurs nach 1, 3, 5 und 10 Handelstagen nach.
Daraus entsteht die Trefferquote je Ausloeserart und je Squeeze-Merkmal --
die einzige belastbare Antwort auf die Frage, ob die Hype-Spur Geld wert ist.

Grundsaetze:
- Die Messung laeuft unabhaengig vom Handel und vom Modus BEOBACHTEN/FREIGABE
  (im Modus AUS ruft PULSAR keine Quellen ab, dann entstehen keine Messungen).
- Ein Papierergebnis enthaelt keinen Spread und keine Slippage; die Auswertung
  zieht pauschal 0,7 % Rundreise-Kosten ab und weist das getrennt aus.
- Fehlende Kurse bleiben offen (OPEN/PARTIAL), nie null. Nach 20 Handelstagen
  ohne Kurs gilt eine Messung als UNRESOLVABLE.
- Handelstage = Werktage NY; Boersenfeiertage verschieben auf den letzten
  verfuegbaren Schluss davor (dokumentierte Vereinfachung).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
import json
import logging
import math
import statistics
import time

from . import research
from .control import digest, encode

LOG = logging.getLogger(__name__)
NY = ZoneInfo("America/New_York")
HORIZONS = (1, 3, 5, 10)
ROUND_TRIP_COST = 0.007
MIN_COMPLETE_FOR_VERDICT = 30
UNRESOLVABLE_AFTER_TRADING_DAYS = 20
PRIMARY_HORIZON = 5
SCHEMA_VERSION = 1


def schema(con):
    con.executescript('''
      CREATE TABLE IF NOT EXISTS attention_outcomes (
        id TEXT PRIMARY KEY, symbol TEXT NOT NULL, triggered_at REAL NOT NULL,
        trigger_day TEXT NOT NULL, trigger_kind TEXT NOT NULL, price_at REAL NOT NULL,
        eligible INTEGER NOT NULL DEFAULT 0, mode TEXT NOT NULL DEFAULT '',
        squeeze INTEGER, rvol REAL, social_kind TEXT, stocktwits_kind TEXT, price_kind TEXT,
        existence_blocked INTEGER NOT NULL DEFAULT 0, rules_version TEXT NOT NULL DEFAULT '',
        evidence TEXT NOT NULL DEFAULT '{}',
        r1 REAL, r3 REAL, r5 REAL, r10 REAL, c1 REAL, c3 REAL, c5 REAL, c10 REAL,
        status TEXT NOT NULL DEFAULT 'OPEN', updated REAL NOT NULL,
        UNIQUE(symbol, trigger_day));
      CREATE INDEX IF NOT EXISTS pulsar_outcomes_status ON attention_outcomes(status, triggered_at);
    ''')


def trading_day_offset(start: date, days: int) -> date:
    cursor = start
    step = 0
    while step < days:
        cursor += timedelta(days=1)
        if cursor.weekday() < 5:
            step += 1
    return cursor


def _number(value):
    try:
        v = float(value)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def trigger_of(card):
    """Ausloeserart einer Karte aus dem Hype-Block; None ohne Ausloeser."""
    hype = card.get("hype") or {}
    trigger = hype.get("trigger") or {}
    if trigger.get("kind"):
        return str(trigger["kind"])
    return None


def price_of(card, *, now):
    """Kurs zum Ausloesezeitpunkt: frischer FMP-Quote, sonst Profilkurs des Tages."""
    for source in card.get("sources") or []:
        if (isinstance(source, dict) and source.get("provider") == "FMP" and source.get("kind") == "quote"
                and isinstance(source.get("data"), dict)):
            data = source["data"]
            stamp = data.get("timestamp")
            if type(stamp) in (int, float) and -60 <= now - stamp <= 3600 and _number(data.get("price")):
                return float(data["price"]), "FMP_QUOTE"
    price = _number((card.get("market") or {}).get("price"))
    if price and price > 0:
        return price, "FMP_PROFILE"
    return None, None


def record(card, *, now=None, mode=""):
    """Speichert eine Messung je Symbol und NY-Handelstag; ohne Ausloeser oder Kurs nichts."""
    now = time.time() if now is None else now
    kind = trigger_of(card)
    if not kind:
        return None
    price, price_source = price_of(card, now=now)
    if not price:
        return None
    symbol = str(card.get("symbol") or "").upper()
    if not research.TICKER.fullmatch(symbol):
        return None
    day = datetime.fromtimestamp(now, NY).date().isoformat()
    hype = card.get("hype") or {}
    squeeze = (hype.get("squeeze") or {}).get("flag")
    evidence = {"price_source": price_source,
                "trigger": hype.get("trigger"), "confirmations": hype.get("confirmations"),
                "social": (hype.get("social") or {}).get("kind"),
                "stocktwits": (hype.get("stocktwits") or {}).get("kind"),
                "price": (hype.get("price") or {}).get("kind"),
                "volume": (hype.get("volume") or {}).get("rvol"),
                "blocks": list(card.get("blocks") or [])[:6]}
    row = {"id": digest({"symbol": symbol, "day": day}), "symbol": symbol, "triggered_at": now,
           "trigger_day": day, "trigger_kind": kind, "price_at": float(price),
           "eligible": int(bool(card.get("eligible"))), "mode": str(mode or ""),
           "squeeze": (None if squeeze is None else int(bool(squeeze))),
           "rvol": _number((hype.get("volume") or {}).get("rvol")),
           "social_kind": (hype.get("social") or {}).get("kind"),
           "stocktwits_kind": (hype.get("stocktwits") or {}).get("kind"),
           "price_kind": (hype.get("price") or {}).get("kind"),
           "existence_blocked": int(bool((hype.get("existence_risk") or {}).get("blocked"))),
           "rules_version": str(card.get("rules_version") or ""), "evidence": encode(evidence)}
    with research.db() as con:
        existing = con.execute("SELECT id FROM attention_outcomes WHERE symbol=? AND trigger_day=?",
                               (symbol, day)).fetchone()
        if existing:
            return None
        con.execute('''INSERT INTO attention_outcomes(id,symbol,triggered_at,trigger_day,trigger_kind,price_at,
            eligible,mode,squeeze,rvol,social_kind,stocktwits_kind,price_kind,existence_blocked,rules_version,evidence,status,updated)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'OPEN',?)''',
            (row["id"], symbol, now, day, kind, row["price_at"], row["eligible"], row["mode"], row["squeeze"], row["rvol"],
             row["social_kind"], row["stocktwits_kind"], row["price_kind"], row["existence_blocked"], row["rules_version"],
             row["evidence"], now))
    LOG.info("PULSAR-Messung %s: Ausloeser %s bei %.4f (%s)", symbol, kind, price, price_source)
    return row


def _close_on_or_before(bars, target: date):
    best = None
    for bar in bars or []:
        if not isinstance(bar, dict):
            continue
        stamp = str(bar.get("date") or "")[:10]
        close = _number(bar.get("close"))
        if not stamp or close is None or close <= 0:
            continue
        try:
            day = date.fromisoformat(stamp)
        except ValueError:
            continue
        if day <= target and (best is None or day > best[0]):
            best = (day, close)
    return best


def update_outcomes(bars_provider, *, now=None, max_symbols=10):
    """Traegt faellige Schlusskurse nach; ``bars_provider(symbol)`` liefert Tageskerzen."""
    now = time.time() if now is None else now
    today = datetime.fromtimestamp(now, NY).date()
    with research.db(readonly=True) as con:
        schema_ready = con.execute("SELECT name FROM sqlite_master WHERE name='attention_outcomes'").fetchone()
        rows = [dict(r) for r in con.execute(
            "SELECT * FROM attention_outcomes WHERE status IN ('OPEN','PARTIAL') ORDER BY triggered_at")] if schema_ready else []
    touched, fetched = 0, {}
    for row in rows:
        start = date.fromisoformat(row["trigger_day"])
        due = [n for n in HORIZONS if row.get(f"r{n}") is None and trading_day_offset(start, n) < today]
        if not due:
            continue
        symbol = row["symbol"]
        if symbol not in fetched:
            if len(fetched) >= max_symbols:
                break
            try:
                fetched[symbol] = list(bars_provider(symbol) or [])
            except Exception as exc:
                LOG.debug("Messung %s: Tageskerzen nicht abrufbar: %s", symbol, exc)
                fetched[symbol] = None
        bars = fetched[symbol]
        if not bars:
            if trading_day_offset(start, UNRESOLVABLE_AFTER_TRADING_DAYS) < today:
                _mark(row["id"], {"status": "UNRESOLVABLE"}, now)
                touched += 1
            continue
        updates = {}
        for n in due:
            hit = _close_on_or_before(bars, trading_day_offset(start, n))
            if hit and hit[0] > start:
                updates[f"c{n}"] = hit[1]
                updates[f"r{n}"] = hit[1] / row["price_at"] - 1
        filled = {n for n in HORIZONS if row.get(f"r{n}") is not None or f"r{n}" in updates}
        if filled == set(HORIZONS):
            updates["status"] = "COMPLETE"
        elif updates:
            updates["status"] = "PARTIAL"
        elif trading_day_offset(start, UNRESOLVABLE_AFTER_TRADING_DAYS) < today:
            updates["status"] = "UNRESOLVABLE"
        if updates:
            _mark(row["id"], updates, now)
            touched += 1
    return {"rows_open": len(rows), "updated": touched, "symbols_fetched": len(fetched)}


def _mark(identity, updates, now):
    columns = ", ".join(f"{k}=?" for k in updates) + ", updated=?"
    with research.db() as con:
        con.execute(f"UPDATE attention_outcomes SET {columns} WHERE id=?", (*updates.values(), now, identity))


def _stats(values, costs):
    values = [v for v in values if v is not None]
    if not values:
        return {"n": 0, "hit_rate": None, "median": None, "mean": None, "median_after_costs": None, "worst": None, "best": None}
    return {"n": len(values), "hit_rate": sum(v > 0 for v in values) / len(values),
            "median": statistics.median(values), "mean": sum(values) / len(values),
            "median_after_costs": statistics.median(values) - costs,
            "worst": min(values), "best": max(values)}


def summarize(rows, *, costs=ROUND_TRIP_COST):
    """Reine Auswertung einer Zeilenliste (auch von der Diagnose ohne DB nutzbar)."""
    rows = [dict(r) for r in rows or []]
    out = {"schema": SCHEMA_VERSION, "total": len(rows), "round_trip_costs": costs,
           "status_counts": {}, "horizons": {}, "by_trigger": {}, "by_squeeze": {}, "by_eligible": {},
           "min_complete_for_verdict": MIN_COMPLETE_FOR_VERDICT, "primary_horizon_days": PRIMARY_HORIZON}
    for row in rows:
        out["status_counts"][row.get("status") or "OPEN"] = out["status_counts"].get(row.get("status") or "OPEN", 0) + 1
    for n in HORIZONS:
        out["horizons"][str(n)] = _stats([_number(r.get(f"r{n}")) for r in rows], costs)
    def grouped(key_fn):
        groups = {}
        for row in rows:
            groups.setdefault(key_fn(row), []).append(row)
        return {str(k): {str(n): _stats([_number(r.get(f"r{n}")) for r in group], costs) for n in HORIZONS}
                | {"total": len(group)} for k, group in sorted(groups.items(), key=lambda kv: str(kv[0]))}
    out["by_trigger"] = grouped(lambda r: r.get("trigger_kind") or "UNBEKANNT")
    out["by_squeeze"] = grouped(lambda r: "JA" if r.get("squeeze") == 1 else "nein" if r.get("squeeze") == 0 else "unbekannt")
    out["by_eligible"] = grouped(lambda r: "HYPE_KANDIDAT" if r.get("eligible") else "nur_Ausloeser")
    primary = out["horizons"][str(PRIMARY_HORIZON)]
    n = primary["n"]
    if n < MIN_COMPLETE_FOR_VERDICT:
        verdict, reason = "ZU_WENIG_DATEN", f"{n} von {MIN_COMPLETE_FOR_VERDICT} vollstaendigen {PRIMARY_HORIZON}-Tage-Messungen"
    elif primary["hit_rate"] >= 0.55 and primary["median_after_costs"] >= 0.005:
        verdict, reason = "ERFOLGREICH", (f"Trefferquote {primary['hit_rate']*100:.0f} %, Median nach Kosten "
                                          f"{primary['median_after_costs']*100:+.2f} % ueber {n} Messungen")
    elif primary["hit_rate"] < 0.45 or primary["median_after_costs"] <= 0:
        verdict, reason = "NICHT_ERFOLGREICH", (f"Trefferquote {primary['hit_rate']*100:.0f} %, Median nach Kosten "
                                                f"{primary['median_after_costs']*100:+.2f} % ueber {n} Messungen")
    else:
        verdict, reason = "UNKLAR", f"Grenzbereich ueber {n} Messungen; weiter messen"
    out["verdict"] = {"status": verdict, "reason": reason, "horizon_days": PRIMARY_HORIZON, "n": n,
                      "rule": ("ERFOLGREICH: Trefferquote >= 55 % und Median nach 0,7 % Kosten >= +0,5 % auf 5 Handelstage; "
                               "NICHT ERFOLGREICH: Trefferquote < 45 % oder Median nach Kosten <= 0; dazwischen UNKLAR; "
                               f"Urteil erst ab {MIN_COMPLETE_FOR_VERDICT} vollstaendigen Messungen.")}
    out["detail"] = ("Papiermessung ab Ausloeser bis Schluss nach n Handelstagen; ohne Spread/Slippage, "
                     "pauschal 0,7 % Rundreise-Kosten. Feiertage nehmen den letzten Schluss davor. "
                     "Keine Kursprognose, keine Handelsfreigabe.")
    return out


def rows(*, limit=200, now=None):
    with research.db(readonly=True) as con:
        if not con.execute("SELECT name FROM sqlite_master WHERE name='attention_outcomes'").fetchone():
            return []
        return [dict(r) for r in con.execute(
            "SELECT * FROM attention_outcomes ORDER BY triggered_at DESC LIMIT ?", (max(1, min(2000, int(limit))),))]


def summary(*, now=None):
    all_rows = rows(limit=2000, now=now)
    out = summarize(all_rows)
    out["recent"] = [{k: r.get(k) for k in ("symbol", "trigger_day", "trigger_kind", "price_at", "eligible", "squeeze",
                                              "rvol", "r1", "r3", "r5", "r10", "status")} for r in all_rows[:40]]
    return out


def public_row(row):
    return {k: row.get(k) for k in ("symbol", "trigger_day", "trigger_kind", "price_at", "eligible", "squeeze", "rvol",
                                    "social_kind", "stocktwits_kind", "price_kind", "r1", "r3", "r5", "r10", "status",
                                    "existence_blocked", "rules_version")}


__all__ = ["record", "update_outcomes", "summarize", "summary", "rows", "public_row", "schema",
           "trading_day_offset", "HORIZONS", "MIN_COMPLETE_FOR_VERDICT"]
