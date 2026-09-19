"""Beobachtungsbasierte, begrenzte X-Quellen-Registry (Zielbild Abschnitt 7, v1).

Vorschlaege entstehen ausschliesslich aus bereits bezahlten, lokal
gespeicherten Posts: Ein Autor, der wiederholt relevante Themen mit
Primaerquellen-Links veroeffentlicht, wird als PROPOSED gefuehrt. Die
Registry arbeitet nur mit numerischen Autoren-IDs; es werden keine
Nutzernamen, Bios oder Profile abgerufen und keine zusaetzlichen
kostenpflichtigen Anfragen erzeugt.

Aktivierung bleibt eine menschliche Entscheidung: Erst wenn der Nutzer die
Identitaet des Kontos selbst geprueft und einen Identitaetsvermerk
hinterlegt hat, wandert es als ACTIVE in den begrenzten Abrufplan
(``from:<id>`` im Finanz-Slot). Ein aktives Konto ist weiterhin nur eine
X-Quelle: Es kann Aufmerksamkeit priorisieren, aber weder eine Krise noch
einen Kauf oder Verkauf bestaetigen (``trade_effect`` bleibt False).

Automatische Uebergaenge: PROPOSED verfaellt ohne neue Relevanz nach 14
Tagen, ACTIVE nach 30 Tagen (EXPIRED). QUARANTINED und REMOVED setzt nur
der Nutzer; ein entferntes oder per Tombstone geloeschtes Konto wird nie
erneut vorgeschlagen.
"""
from __future__ import annotations

import json
import re
import time

from . import store

DAY = 86400
STATE_KEY = "account_registry"
MAX_PROPOSED = 10
MAX_DYNAMIC_ACTIVE = 5
MAX_ROWS = 40
PROPOSAL_MIN_RELEVANT = 3
PROPOSAL_MIN_DAYS = 2
PROPOSAL_TTL = 14 * DAY
ACTIVE_TTL = 30 * DAY
ACCOUNT_ID = re.compile(r"^[0-9]{1,25}$")
STATES = {"PROPOSED", "ACTIVE", "QUARANTINED", "EXPIRED", "REMOVED"}
# Domains, deren Verlinkung auf eine Primaer-/Behoerdenquelle hinweist. Die
# Liste bewertet nur den Link eines Posts; sie beweist keine Kontoidentitaet.
PRIMARY_DOMAINS = (
    "sec.gov", "federalreserve.gov", "treasury.gov", "home.treasury.gov",
    "whitehouse.gov", "bls.gov", "bea.gov", "cftc.gov", "fdic.gov", "occ.gov",
    "justice.gov", "commerce.gov", "ustr.gov", "energy.gov", "fda.gov",
    "nhtsa.gov", "faa.gov", "ferc.gov", "ecb.europa.eu", "europa.eu",
    "bundesbank.de", "bafin.de",
)


def _digest(value):
    return store.digest(value)


def _is_primary(domain):
    d = str(domain or "").lower()
    return any(d == known or d.endswith("." + known) for known in PRIMARY_DOMAINS)


def _rows(con):
    rows = store.value(con, STATE_KEY, [])
    return [dict(r) for r in rows if isinstance(r, dict)
            and ACCOUNT_ID.fullmatch(str(r.get("account_id", "")))
            and r.get("state") in STATES]


def _save(con, rows):
    # ACTIVE/QUARANTINED/REMOVED bleiben erhalten; abgelaufene Vorschlaege
    # raeumen sich selbst auf, damit die Zustandsdatei begrenzt bleibt.
    keep = sorted(rows, key=lambda r: (
        {"ACTIVE": 0, "QUARANTINED": 1, "REMOVED": 2, "PROPOSED": 3, "EXPIRED": 4}[r["state"]],
        -float(r.get("last_relevant_at") or 0)))[:MAX_ROWS]
    store.put(con, STATE_KEY, keep)
    return keep


def _tombstoned(con, account_id):
    return bool(con.execute(
        "SELECT 1 FROM tombstones WHERE kind='author' AND identity_hash=?",
        (_digest(str(account_id)),)).fetchone())


def refresh(con, now=None):
    """Metriken aktualisieren, neue Vorschlaege bilden, TTLs anwenden.

    Liest ausschliesslich lokale Posts (7-Tage-Bestand); erzeugt keine
    Anfrage. Rueckgabe: die aktiven Konten-IDs fuer den Abrufplan.
    """
    now = time.time() if now is None else now
    metrics = {}
    for row in con.execute(
            "SELECT author,created,topic,spam,urls FROM posts WHERE author!='' AND created>? ORDER BY created",
            (now - 7 * DAY,)):
        try:
            domains = json.loads(row["urls"])
        except (TypeError, ValueError):
            domains = []
        primary = sorted({d for d in domains if _is_primary(d)})
        relevant = not row["spam"] and (row["topic"] != "OTHER" or primary)
        if not relevant:
            continue
        m = metrics.setdefault(row["author"], {
            "relevant_post_count": 0, "days": set(), "topics": set(),
            "primary_domains": set(), "last_relevant_at": 0.0, "post_ids": []})
        m["relevant_post_count"] += 1
        m["days"].add(int(row["created"] // DAY))
        if row["topic"] != "OTHER":
            m["topics"].add(row["topic"])
        m["primary_domains"].update(primary)
        m["last_relevant_at"] = max(m["last_relevant_at"], float(row["created"]))
    for author, m in metrics.items():
        for pid_row in con.execute(
                "SELECT id FROM posts WHERE author=? AND spam=0 ORDER BY created DESC LIMIT 10", (author,)):
            m["post_ids"].append("x-post:" + pid_row["id"])

    rows = {r["account_id"]: r for r in _rows(con)}
    for author, m in metrics.items():
        row = rows.get(author)
        if row is None:
            if (m["relevant_post_count"] < PROPOSAL_MIN_RELEVANT
                    or len(m["days"]) < PROPOSAL_MIN_DAYS
                    or _tombstoned(con, author)):
                continue
            proposed = sum(r["state"] == "PROPOSED" for r in rows.values())
            if proposed >= MAX_PROPOSED:
                continue
            row = rows[author] = {"account_id": author, "state": "PROPOSED",
                                  "proposed_at": now, "false_signal_count": 0,
                                  "profile_url": "https://x.com/i/user/" + author}
        if row["state"] in {"REMOVED", "QUARANTINED"}:
            continue
        row.update(relevant_post_count=m["relevant_post_count"],
                   distinct_day_count=len(m["days"]),
                   topics=sorted(m["topics"]),
                   primary_domains=sorted(m["primary_domains"]),
                   evidence_post_ids=m["post_ids"][:10],
                   last_relevant_at=m["last_relevant_at"])
        if row["state"] == "EXPIRED":
            # Neue Relevanz reaktiviert nur den Vorschlagsstatus, nie ACTIVE.
            row["state"] = "PROPOSED"
            row["proposed_at"] = now

    for row in rows.values():
        last = float(row.get("last_relevant_at") or row.get("proposed_at") or 0)
        if row["state"] == "PROPOSED":
            row["expires_at"] = last + PROPOSAL_TTL
            if now > row["expires_at"]:
                row["state"] = "EXPIRED"
        elif row["state"] == "ACTIVE":
            row["expires_at"] = max(last, float(row.get("activated_at") or 0)) + ACTIVE_TTL
            if now > row["expires_at"]:
                row["state"] = "EXPIRED"
        if _tombstoned(con, row["account_id"]) and row["state"] != "REMOVED":
            row.update(state="REMOVED", removed_reason="AUTOR_GELOESCHT")
    saved = _save(con, list(rows.values()))
    return [r["account_id"] for r in saved if r["state"] == "ACTIVE"][:MAX_DYNAMIC_ACTIVE]


def active_ids(con, now=None):
    now = time.time() if now is None else now
    return [r["account_id"] for r in _rows(con)
            if r["state"] == "ACTIVE" and now <= float(r.get("expires_at") or now)][:MAX_DYNAMIC_ACTIVE]


def _transition(account_id, actor, wanted, extra):
    account_id = str(account_id)
    if not ACCOUNT_ID.fullmatch(account_id):
        raise ValueError("Ungueltige Konto-ID")
    if not str(actor or "").strip():
        raise ValueError("Bearbeiter fehlt")
    now = time.time()
    with store.db() as con:
        rows = _rows(con)
        row = next((r for r in rows if r["account_id"] == account_id), None)
        if row is None:
            raise ValueError("Konto ist nicht in der Registry")
        if wanted == "ACTIVE":
            if row["state"] != "PROPOSED":
                raise ValueError("Nur ein vorgeschlagenes Konto kann aktiviert werden")
            if sum(r["state"] == "ACTIVE" for r in rows) >= MAX_DYNAMIC_ACTIVE:
                raise ValueError(f"Hoechstens {MAX_DYNAMIC_ACTIVE} dynamische Konten gleichzeitig")
            note = str(extra or "").strip()
            if len(note) < 10:
                raise ValueError("Identitaetsvermerk (mind. 10 Zeichen) erforderlich: "
                                 "wer steht nach eigener Pruefung hinter diesem Konto?")
            row.update(state="ACTIVE", activated_at=now, activated_by=str(actor)[:80],
                       identity_note=note[:400], expires_at=now + ACTIVE_TTL)
        elif wanted == "QUARANTINED":
            if row["state"] == "REMOVED":
                raise ValueError("Ein entferntes Konto bleibt entfernt")
            row.update(state="QUARANTINED", quarantined_at=now,
                       quarantined_by=str(actor)[:80],
                       quarantine_reason=str(extra or "MANUELL")[:200],
                       false_signal_count=int(row.get("false_signal_count") or 0) + 1)
        elif wanted == "REMOVED":
            row.update(state="REMOVED", removed_at=now, removed_by=str(actor)[:80],
                       removed_reason=str(extra or "MANUELL")[:200])
        else:
            raise ValueError("Unbekannter Zielzustand")
        _save(con, rows)
        return {**row, "trade_effect": False}


def activate(account_id, *, actor, identity_note):
    return _transition(account_id, actor, "ACTIVE", identity_note)


def quarantine(account_id, *, actor, reason=""):
    return _transition(account_id, actor, "QUARANTINED", reason)


def remove(account_id, *, actor, reason=""):
    return _transition(account_id, actor, "REMOVED", reason)


def snapshot(con, now=None):
    now = time.time() if now is None else now
    rows = _rows(con)
    return {"rows": rows,
            "active_count": sum(r["state"] == "ACTIVE" for r in rows),
            "proposed_count": sum(r["state"] == "PROPOSED" for r in rows),
            "limits": {"active": MAX_DYNAMIC_ACTIVE, "proposed": MAX_PROPOSED,
                       "proposal_ttl_days": PROPOSAL_TTL // DAY,
                       "active_ttl_days": ACTIVE_TTL // DAY,
                       "proposal_thresholds": {"relevant_posts": PROPOSAL_MIN_RELEVANT,
                                               "distinct_days": PROPOSAL_MIN_DAYS}},
            "trade_effect": False,
            "detail": ("Vorschlaege entstehen nur aus bereits bezahlten Stichproben. "
                       "Aktivierung erfordert eine eigene Identitaetspruefung des Nutzers; "
                       "auch ein aktives Konto bestaetigt allein weder Krise noch Kauf oder Verkauf.")}
