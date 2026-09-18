"""Read-only, evidence-based projection of eToro accounting gaps (9.7.3).

The ledger remains the financial source of truth. A JSON success flag alone
never releases a gap. Resolving a diagnostic does not book a trade, infer
ownership, overwrite fees, or submit/cancel an order.
"""
from __future__ import annotations

import math
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo


def number(value):
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def same_number(left, right):
    a, b = number(left), number(right)
    return a is not None and b is not None and abs(a-b) <= max(1e-8, abs(b)*1e-7)


def domain_parts(value):
    parts = str(value or "").strip().lower().split(":")
    if len(parts) not in (2, 3) or parts[0] != "etoro" or parts[1] not in ("demo", "live"):
        return None
    return parts[0], parts[1], parts[2] if len(parts) == 3 else ""


def verified_aliases(data):
    """Only accept aliases accompanied by the persisted broker lookup proof."""
    evidence = data.get("konto_alias_belege") or {}
    result = {}
    for old, new in (data.get("konto_aliase") or {}).items():
        proof = evidence.get(old) or {}
        if (str(proof.get("neu") or "") == str(new)
                and str(proof.get("beleg") or "").strip()):
            result[str(old).lower()] = str(new).lower()
    return result


def canonical_account(account, aliases):
    value = str(account or "").lower()
    visited = set()
    while value in aliases:
        if value in visited:
            return ""  # A cycle is not an identity proof.
        visited.add(value)
        value = aliases[value]
    return value


def same_domain(left, right, aliases):
    a, b = domain_parts(left), domain_parts(right)
    if not a or not b or a[:2] != b[:2] or not a[2] or not b[2]:
        return False
    ca, cb = canonical_account(a[2], aliases), canonical_account(b[2], aliases)
    return bool(ca and cb and ca == cb)


def record_domain(record):
    """Conflicting paper/environment/fingerprint fields are not normalised away."""
    raw = str(record.get("domain") or "")
    parts = domain_parts(raw)
    if not parts:
        return ""
    account = str(record.get("account_fingerprint") or "").lower()
    if account != parts[2]:
        return ""
    if type(record.get("paper")) not in (bool, int) or record.get("paper") not in (0, 1) or bool(record.get("paper")) != (parts[1] == "demo"):
        return ""
    return raw.lower()


def read_ledger(db_path):
    """Consistent read transaction; missing/corrupt databases are never created."""
    path = Path(db_path).resolve()
    if not path.is_file():
        return {"error": "Ledgerdatenbank fehlt", "trades": [], "exits": []}
    try:
        con = sqlite3.connect(path.as_uri()+"?mode=ro", uri=True, timeout=3)
        con.row_factory = sqlite3.Row
        try:
            con.execute("PRAGMA query_only=ON")
            con.execute("BEGIN")
            trades = [dict(row) for row in con.execute("SELECT * FROM trades WHERE broker='etoro'")]
            exits = [dict(row) for row in con.execute("SELECT * FROM trade_exit_events WHERE broker='etoro'")]
            return {"trades": trades, "exits": exits, "error": ""}
        finally:
            con.close()
    except (OSError, sqlite3.Error) as exc:
        return {"error": f"Ledger nicht lesbar: {type(exc).__name__}", "trades": [], "exits": []}


def _date(value, zone):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return None  # Unzoned timestamps must not release today's risk lock.
        return parsed.astimezone(zone).date()
    except (ValueError, TypeError, OverflowError):
        return None


def _instant(value):
    try:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return result.astimezone(timezone.utc) if result.tzinfo else None
    except (ValueError, TypeError, OverflowError):
        return None


def _closed_record(record, pid):
    return bool(pid in {str(x) for x in record.get("closed_position_ids") or []}
                and pid not in {str(x) for x in record.get("open_position_ids") or []}
                and pid not in {str(x) for x in record.get("unresolved_position_ids") or []}
                and not record.get("manual_review_required")
                and str(record.get("position_state") or "CLOSED_CONFIRMED") == "CLOSED_CONFIRMED")


def _proof(record, gap, ledger, aliases):
    """Return financial row ids only for an exact, fully closed entry lineage."""
    pid = str(gap.get("broker_position_id") or "")
    domain = domain_parts(record_domain(record))
    backfill = (record.get("ledger_close_backfill") or {}).get(pid) or {}
    if not domain or not domain[2] or not _closed_record(record, pid):
        return None, "Geschlossene kontogebundene Brokerposition nicht belegt"
    if str(backfill.get("status") or "") not in {"LINKED_CLOSED", "CLOSED"}:
        return None, "Ledgerabgleich noch nicht erfolgreich"
    account = canonical_account(domain[2], aliases)
    orders = {str(x) for x in record.get("order_ids") or []}
    if not account or not orders:
        return None, "Einstiegsorder oder Kontobeweis fehlt"
    rows = [r for r in ledger["trades"] if
            not r.get("superseded_by") and str(r.get("broker") or "").lower() == "etoro"
            and type(r.get("paper")) in (int,bool) and r.get("paper") in (0,1)
            and str(r.get("broker_position_id") or "") == pid
            and canonical_account(r.get("broker_account_fingerprint"), aliases) == account
            and bool(r.get("paper")) == (domain[1] == "demo")]
    if not rows or any(str(r.get("symbol") or "").upper() != str(gap.get("symbol") or "").upper()
                       or str(r.get("entry_order_id") or "") not in orders
                       or not r.get("ausgestiegen_am") for r in rows):
        return None, "Exakte Ledger-Lineage fehlt, ist widerspruechlich oder noch offen"
    ids = {int(r["trade_id"]) for r in rows}
    if int(backfill.get("trade_id") or 0) not in ids:
        return None, "Gespeicherte trade_id passt nicht zum aktuellen Ledger"
    entries = [x for x in record.get("fills") or [] if str(x.get("position_id") or "") == pid]
    if not entries or any(number(x.get("quantity")) is None or number(x.get("price")) is None for x in entries):
        return None, "Positionsgenaue Einstiegsfills fehlen"
    qty = sum(number(x.get("quantity")) for x in entries)
    if qty <= 0 or any((number(r.get("menge")) or 0) <= 0 for r in rows):
        return None, "Ausgefuehrte Menge nicht belegt"
    if not same_number(sum(number(r.get("menge")) for r in rows), qty):
        return None, "Ledger- und Broker-Ausfuehrungsmenge weichen ab"
    entry_price = sum(number(x["quantity"])*number(x["price"]) for x in entries)/qty
    if any(not same_number(r.get("einstieg_preis"), entry_price) for r in rows):
        return None, "Einstand der exakten Lineage weicht ab"
    detail = (record.get("close_evidence_by_position_id") or {}).get(pid) or {}
    close_time = detail.get("closed_at_utc") or detail.get("closeTimestamp")
    close_price = detail.get("closePrice", detail.get("closeRate"))
    # For a single full close, the broker close and the persisted financial row
    # must agree. A complex partial history stays pending unless its explicit
    # ledger events cover each financial row; no amount-proximity matching.
    if len(rows) == 1:
        if (not detail.get("_terminal_close_confirmed")
                or not same_number(detail.get("quantity", detail.get("closedUnits")), qty)
                or not same_number(rows[0].get("ausstieg_preis"), close_price)
                or _instant(close_time) is None
                or _instant(rows[0].get("ausgestiegen_am")) != _instant(close_time)):
            return None, "Positionsgenauer Abschlussbeleg stimmt nicht mit dem Ledger ueberein"
    for row in rows:
        events = [x for x in ledger["exits"] if int(x.get("trade_id") or 0) == int(row["trade_id"])
                  and str(x.get("instrument") or "") == pid
                  and canonical_account(x.get("broker_account_fingerprint"), aliases) == account
                  and str(x.get("fill_id") or x.get("event_id") or "")]
        if not events:
            return None, "Dauerhafter Exitbeleg fehlt"
        if any(number(x.get("quantity")) is not None and number(x.get("quantity")) > qty+1e-8 for x in events):
            return None, "Widerspruechliche Exitmenge"
        if len(rows) == 1:
            if not any(same_number(e.get("quantity"), row.get("menge")) for e in events):
                return None, "Exitjournal bestaetigt die vollstaendige Menge nicht"
            if any(number(e.get("price")) is not None and not same_number(e.get("price"),row.get("ausstieg_preis")) for e in events):
                return None, "Exitjournal und Ledger-Abschlusspreis widersprechen sich"
        if len(rows) > 1 and not any(same_number(e.get("quantity"), row.get("menge"))
                                     and same_number(e.get("price"), row.get("ausstieg_preis")) for e in events):
            return None, "Teilabschluss nicht vollstaendig im Exitjournal belegt"
    if gap.get("evidence_conflict"):
        return None, "Widerspruechliche neue Ausfuehrungsbelege"
    # Additional observed fills cannot be ignored merely because an old
    # backfill flag is still green. Compare their economic payload as well.
    for observed in gap.get("close_evidence") or []:
        candidates = [r for r in rows if same_number(r.get("menge"), observed.get("quantity"))
                      and same_number(r.get("ausstieg_preis"), observed.get("price"))
                      and _instant(r.get("ausgestiegen_am")) == _instant(observed.get("closed_at_utc"))]
        if len(candidates) != 1:
            return None, "Neuer Verkaufsbeleg passt nicht eindeutig zum verbuchten Abschluss"
        if observed.get("fee") is not None:
            total, entry = number(candidates[0].get("gebuehren")), number(candidates[0].get("einstieg_gebuehr"))
            if (total is None or entry is None
                    or not same_number(total-entry, observed["fee"])
                    or str(observed.get("fee_currency") or "").upper() != str(candidates[0].get("waehrung") or "").upper()):
                return None, "Neue Gebuehrenangabe nicht exakt im Ledger bestaetigt"
    return rows, "Konto, Umgebung, Position, Einstiegsorder, Menge, Preis und Abschluss im Ledger bestaetigt"


def _project(data, ledger, *, domain="", now=None, timezone_name="Europe/Berlin"):
    """Project ACTIVE / RESOLVED / LEGACY_AUDIT without changing source data."""
    now = now or datetime.now(timezone.utc)
    zone = ZoneInfo(timezone_name)
    today = now.astimezone(zone).date()
    if not isinstance(data, dict) or not isinstance(data.get("records", {}), dict):
        raise ValueError("Abgleichrecords ungueltig")
    if not isinstance(data.get("unzugeordnete_verkaeufe", []), list):
        raise ValueError("Buchungslueckenregister ungueltig")
    if any(not isinstance(x, dict) for x in data.get("unzugeordnete_verkaeufe", [])):
        raise ValueError("Buchungsluecke ungueltig")
    aliases = verified_aliases(data)
    if any(not isinstance(r, dict) for r in (data.get("records") or {}).values()):
        raise ValueError("Ein Abgleichrecord hat ein ungueltiges Format")
    records = list((data.get("records") or {}).values())
    gaps = [dict(x) for x in (data.get("unzugeordnete_verkaeufe") or []) if isinstance(x, dict)]
    keys = {(str(x.get("domaene") or "UNKNOWN").lower(), str(x.get("broker_position_id") or "")) for x in gaps}
    for record in records:
        for pid in (record.get("closed_position_ids") or []):
            result = (record.get("ledger_close_backfill") or {}).get(str(pid)) or {}
            if str(result.get("status") or "") in {"LINKED_CLOSED", "CLOSED", "LEDGER_BACKFILL_LEGACY_UNBOUND"}:
                continue
            rd = record_domain(record)
            if (rd, str(pid)) not in keys:
                gaps.append({"symbol": record.get("symbol"), "broker_position_id": str(pid),
                             "domaene": rd or "UNKNOWN", "grund": result.get("error") or "Ledger-Commit ausstehend",
                             "decision_id": record.get("decision_id"), "derived": True})
                keys.add((rd, str(pid)))
    out = []
    for gap in gaps:
        gd = str(gap.get("domaene") or "UNKNOWN").lower()
        if domain and gd not in {"", "unknown"} and not same_domain(gd, domain, aliases):
            # Fail closed: a gap whose account/domain cannot be proven is not
            # evidence that it belongs elsewhere. It blocks every eToro domain
            # until durable broker+ledger evidence scopes or resolves it.
            continue
        pid, symbol = str(gap.get("broker_position_id") or ""), str(gap.get("symbol") or "").upper()
        item = {"symbol": symbol, "broker_position_id": pid, "closed_position_ids": [pid],
                "domaene": gd, "broker": "etoro", "decision_id": int(gap.get("decision_id") or 0),
                "observed_at_utc": gap.get("zeit"), "status": "ACTIVE", "blocks_entries": True,
                "trade_ids": [], "detail": str(gap.get("grund") or "Ledgerabgleich ausstehend"),
                "result_status": "UNKNOWN", "effective_at_utc": None}
        matches = [r for r in records if pid in {str(x) for x in r.get("closed_position_ids") or []}
                   and str(r.get("symbol") or "").upper() == symbol and same_domain(record_domain(r), gd, aliases)]
        if len(matches) == 1 and not ledger.get("error"):
            rows, explanation = _proof(matches[0], gap, ledger, aliases)
            item["detail"] = explanation
            if rows:
                # 10.7.0: Ein gekennzeichneter Erwartungswert beziffert das
                # Ergebnis ebenfalls und sperrt keine Kaeufe mehr; im Status
                # bleibt er von einem belegten Ergebnis unterscheidbar.
                from ledger_result import confirmed_net, usable_net
                effective = max(_instant(r.get("ausgestiegen_am")) for r in rows).isoformat()
                known_net = all(usable_net(r) for r in rows)
                proven_net = all(confirmed_net(r) for r in rows)
                day = _date(effective, zone)
                # A historical fee gap is not today's loss. Today's unknown
                # result remains a risk block even though the linkage is fixed.
                blocks = not known_net and (day is None or day >= today)
                item.update(status="RESOLVED", trade_ids=[int(r["trade_id"]) for r in rows],
                            decision_id=int(matches[0].get("decision_id") or 0),
                            effective_at_utc=effective, blocks_entries=blocks,
                            result_status=("CONFIRMED" if proven_net else
                                           "EXPECTED" if known_net else "FEES_OR_RESULT_PENDING"))
                if blocks:
                    item["detail"] += "; aktuelles Ergebnis/Gebuehren noch unvollstaendig"
        elif not matches:
            legacy = [r for r in records if pid and str(r.get("symbol") or "").upper() == symbol
                      and _closed_record(r, pid) and bool(r.get("legacy_unbound_closed"))
                      and not r.get("account_fingerprint") and domain_parts(record_domain(r))
                      and domain_parts(gd) and domain_parts(record_domain(r))[:2] == domain_parts(gd)[:2]
                      and str(((r.get("ledger_close_backfill") or {}).get(pid) or {}).get("status") or "") == "LEDGER_BACKFILL_LEGACY_UNBOUND"]
            conflicting = [r for r in records if pid in {str(x) for x in r.get("position_ids") or []}
                           and r.get("account_fingerprint")]
            conflicting += [r for r in ledger.get("trades", []) if not r.get("superseded_by")
                            and str(r.get("broker_position_id") or "") == pid
                            and r.get("broker_account_fingerprint")]
            if len(legacy) == 1 and not ledger.get("error") and not conflicting and not gap.get("evidence_conflict") and not gap.get("close_evidence"):
                item.update(status="LEGACY_AUDIT", blocks_entries=False, result_status="LEGACY_UNBOUND",
                            decision_id=int(legacy[0].get("decision_id") or 0),
                            detail="Historischer, bereits geschlossen migrierter Altbestand ohne Kontobindung. Keine Kontozuschreibung, kein P&L erfunden; Audit bleibt erhalten.")
        if ledger.get("error") and item["status"] != "LEGACY_AUDIT":
            item["detail"] = ledger["error"]
        item["state"] = item["status"]
        out.append(item)
    storage_error = str(data.get("storage_error") or "")
    if storage_error or (ledger.get("error") and (records or gaps)):
        out.append({"symbol": "", "broker": "etoro", "domaene": domain or "UNKNOWN", "status": "STORAGE_ERROR",
                    "state": "STORAGE_ERROR", "blocks_entries": True, "trade_ids": [], "decision_id": 0,
                    "broker_position_id": "", "closed_position_ids": [], "detail": "Abgleichdatei oder Ledger nicht lesbar; keine Freigabe"})
    return {"items": out, "blocking": [x for x in out if x["blocks_entries"]],
            "resolved": [x for x in out if x["status"] == "RESOLVED"],
            "legacy": [x for x in out if x["status"] == "LEGACY_AUDIT"],
            "domain": domain, "checked_at_utc": now.isoformat(), "storage_error": storage_error,
            "ledger_error": str(ledger.get("error") or "")}


def project(data, ledger, *, domain="", now=None, timezone_name="Europe/Berlin"):
    """A malformed diagnostic is a visible block, never an implicit green state."""
    try:
        return _project(data, ledger, domain=domain, now=now, timezone_name=timezone_name)
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError) as exc:
        issue = {"symbol": "", "broker": "etoro", "domaene": domain or "UNKNOWN",
                 "status": "STORAGE_ERROR", "state": "STORAGE_ERROR", "blocks_entries": True,
                 "trade_ids": [], "decision_id": 0, "broker_position_id": "", "closed_position_ids": [],
                 "detail": "Buchungsabgleich hat ein ungueltiges Datenformat; keine Freigabe"}
        return {"items": [issue], "blocking": [issue], "resolved": [], "legacy": [],
                "domain": domain, "checked_at_utc": (now or datetime.now(timezone.utc)).isoformat(),
                "storage_error": type(exc).__name__, "ledger_error": ""}
