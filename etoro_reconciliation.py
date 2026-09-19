"""Persistente eToro-Wiederherstellung nach einem moeglicherweise angenommenen Kauf.

Der Datensatz ist die lokale Beweiskette zwischen Kaufentscheidung, POST,
Order, Fill und Position. Er ist absichtlich unabhaengig vom Scanner: ein
Neustart darf weder einen zweiten POST erlauben noch Identitaeten verlieren.
"""
from __future__ import annotations

import json
import functools
import logging
import os
import time
from datetime import datetime, timezone
import threading
from pathlib import Path

import config
from broker.base import BrokerFehler, Fill, VerbindungVerloren, OrderErgebnis
from decision_analytics import mark_execution, record_execution_event, record_order_state
from safe_persistence import atomic_write_json
from state_lock import critical_state_lock

SCHEMA_VERSION = 3

# ``state`` bleibt als kompatibler, abgeleiteter Anzeigezustand erhalten.
# Die Brokerwahrheit lebt ab Schema 3 getrennt in submit/execution/position.
# Zustaende, in denen der BROKER-Ausgang noch offen ist. Nur diese begruenden
# ein Doppelkaufrisiko.
NON_TERMINAL = {
    "SUBMITTING", "RECONCILING", "UNKNOWN_AFTER_SUBMIT",
    "AWAITING_POSITION_CONFIRMATION", "PARTIALLY_FILLED",
}
# KORREKTUR 9.5.2: ``CLOSED_ACCOUNTING_PENDING`` stand bis 9.5.1 in
# NON_TERMINAL. Der Broker hat hier aber laengst abgeschlossen -- es fehlt nur
# die lokale Beleg-/Ledgerverknuepfung. Das als "ungeklaerte Order" zu fuehren
# ist fachlich falsch: es gibt nichts, was doppelt gekauft werden koennte.
# Die Buchungsluecke ist trotzdem ein Mangel und wird als eigener Zustand
# gefuehrt und angezeigt (siehe BUCHUNGSLUECKE).
BUCHUNGSLUECKE = {"CLOSED_ACCOUNTING_PENDING"}
TERMINAL = {
    "FILLED", "REJECTED", "CANCELLED", "EXPIRED",
    "REJECTED_PARTIALLY_FILLED", "CANCELED_PARTIALLY_FILLED", "UNPROVABLE",
    "CLOSED_BEFORE_IMPORT",
    "FAILED_BEFORE_SUBMIT", "FAILED",
}
logger = logging.getLogger(__name__)


def _unique_strings(values) -> list[str]:
    return sorted({str(value) for value in (values or []) if str(value)})


def _float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _fill_key(fill: dict) -> str:
    """Stabile Identitaet; ohne executionId ist eine positionExecution einmalig."""
    execution_id = str(fill.get("execution_id") or "")
    if execution_id:
        return f"execution:{execution_id}"
    position_id = str(fill.get("position_id") or "")
    if position_id:
        return f"position:{position_id}"
    return (f"fallback:{fill.get('execution_time')}:{_float(fill.get('quantity')):.12g}:"
            f"{_float(fill.get('price')):.12g}")


def _explicit_fee(row):
    """Only explicit USD costs; never infer zero fees from their absence."""
    import math
    currency = str(row.get("fee_currency") or row.get("currency") or "").upper()
    if currency != "USD":
        return None
    values = ([row["fee"]] if row.get("fee") is not None else
              [row["fees"], row["taxes"]] if row.get("fees") is not None and row.get("taxes") is not None else [])
    try:
        if not values or any(isinstance(v, bool) or not math.isfinite(float(v)) or float(v) < 0 for v in values):
            return None
        return sum(float(v) for v in values)
    except (ValueError, TypeError):
        return None


def _reconcile_fees(record, pid, order_id, entry_fills, close_events):
    import trade_ledger
    if not entry_fills or any(r.get("fee") is None for r in entry_fills):
        return
    # An exact entry receipt is useful even when external close costs are
    # still absent. Never promote the total/net result from this partial proof.
    trade_ledger.reconcile_entry_fees_exact(
        broker="etoro", account=record.get("account_fingerprint", ""),
        paper=bool(record.get("paper")), position_id=pid, entry_order_id=order_id,
        entry_fills=entry_fills)
    exits = [{"fill_id": _explicit_close_fill_id(r),
              "order_id": _explicit_close_order_id(r),
              "quantity": abs(_float(r.get("closedUnits"))),
              "price": _float(r.get("closeRate") or r.get("closePrice") or r.get("avgPrice") or r.get("price")),
              "fee": _explicit_fee(r), "fee_currency": "USD"} for r in close_events]
    if not exits or any(r["fee"] is None for r in exits):
        return
    return trade_ledger.reconcile_fees_exact(
        broker="etoro", account=record.get("account_fingerprint", ""),
        paper=bool(record.get("paper")), position_id=pid, entry_order_id=order_id,
        entry_fills=entry_fills, exit_fills=exits)


def _normalise_fill(fill: dict) -> dict:
    out = {
        "position_id": str(fill.get("position_id") or fill.get("positionId") or ""),
        "quantity": abs(_float(fill.get("quantity") or fill.get("units"))),
        "price": _float(fill.get("price") or fill.get("avgPrice")),
        "execution_time": fill.get("execution_time") or fill.get("executionTime"),
        "execution_id": str(fill.get("execution_id") or fill.get("executionId") or ""),
    }
    # Preserve explicit economic evidence. Missing fields remain unknown.
    for key in ("fee", "fee_currency", "fees", "taxes", "currency", "fee_source"):
        if key in fill:
            out[key] = fill[key]
    return out


def _merge_fills(existing, incoming) -> list[dict]:
    """Brokerbelege wachsen monoton; ein kuerzerer Lookup darf nichts loeschen."""
    merged: dict[str, dict] = {}
    for raw in list(existing or []) + list(incoming or []):
        if not isinstance(raw, dict):
            continue
        fill = _normalise_fill(raw)
        if not fill["position_id"] or fill["quantity"] <= 0 or fill["price"] <= 0:
            continue
        key = _fill_key(fill)
        previous = merged.get(key)
        if previous is None:
            merged[key] = fill
            continue
        # Bei kumulativen Antworten ist der groessere, reichhaltigere Beleg
        # staerker. Mengen werden niemals addiert, wenn die Identitaet gleich ist.
        if fill["quantity"] > previous["quantity"]:
            previous["quantity"] = fill["quantity"]
            previous["price"] = fill["price"]
        if not previous.get("execution_time") and fill.get("execution_time"):
            previous["execution_time"] = fill["execution_time"]
        if not previous.get("execution_id") and fill.get("execution_id"):
            previous["execution_id"] = fill["execution_id"]
        for field in ("fee", "fee_currency", "fees", "taxes", "currency"):
            if fill.get(field) is not None:
                if previous.get(field) not in (None, fill[field]):
                    raise ValueError("Widerspruechlicher Gebuehrenbeleg derselben eToro-Ausfuehrung")
                previous[field] = fill[field]
        if fill.get("fee_source"):
            previous["fee_source"] = fill["fee_source"]
    return list(merged.values())


def _execution_has_fill(record: dict) -> bool:
    state = str(record.get("execution_state") or
                record.get("broker_execution_state") or "").upper()
    return (_float(record.get("filled_quantity")) > 0 or bool(record.get("fills"))
            or state in {"FILLED", "PARTIALLY_FILLED", "REJECTED_PARTIALLY_FILLED", "CANCELED_PARTIALLY_FILLED"})


def _known_position_ids(record: dict) -> set[str]:
    return ({str(x) for x in (record.get("position_ids") or []) if str(x)} |
            {str(x.get("position_id") or "") for x in (record.get("fills") or [])
             if isinstance(x, dict) and str(x.get("position_id") or "")})


def _merge_execution_state(current: str, incoming: str, *, filled: float,
                           requested: float) -> str:
    """Fuehrt nur vorwaerts; Fill-Beweise schlagen Status-/Transporttexte."""
    current = str(current or "UNKNOWN").upper()
    incoming = str(incoming or "UNKNOWN").upper()
    if current == "FILLED" or incoming == "FILLED":
        return "FILLED"
    # eToro 5/10 beschreiben einen Teilfill bzw. einen abgelehnten Rest.
    # Selbst eine numerisch volle Snapshot-Menge darf diesen expliziten
    # Brokerstatus nicht still zu FILLED umdeuten.
    if current == "REJECTED_PARTIALLY_FILLED" or incoming == "REJECTED_PARTIALLY_FILLED":
        return "REJECTED_PARTIALLY_FILLED"
    if current == "CANCELED_PARTIALLY_FILLED" or incoming == "CANCELED_PARTIALLY_FILLED":
        return "CANCELED_PARTIALLY_FILLED"
    if filled > 0 and incoming == "CANCELLED":
        return "CANCELED_PARTIALLY_FILLED"
    if filled > 0 and incoming == "REJECTED":
        return "REJECTED_PARTIALLY_FILLED"
    if current == "PARTIALLY_FILLED" or incoming == "PARTIALLY_FILLED":
        return "PARTIALLY_FILLED"
    if filled > 0 and requested > 0 and filled >= requested - 1e-10:
        return "FILLED"
    if filled > 0:
        return "PARTIALLY_FILLED"
    # Status 10 behauptet einen Teilfill, auch wenn dieser Lookup die
    # positionExecutions noch nicht mitliefert. Das bleibt fail-closed.
    if current in {"REJECTED", "CANCELLED", "EXPIRED"}:
        return current
    if incoming in {"REJECTED", "CANCELLED", "EXPIRED"}:
        return incoming
    if current not in {"", "UNKNOWN", "RECONCILING", "SUBMITTING"}:
        return current
    if incoming in {"", "UNKNOWN"}:
        return "UNKNOWN" if current in {"", "UNKNOWN", "SUBMITTING"} else current
    return incoming


def _sync_position_evidence(record: dict) -> None:
    """Normalisiert OPEN/CLOSED-Mengen, wobei CLOSED staerker als OPEN ist."""
    known = _known_position_ids(record)
    closed = set(_unique_strings(record.get("closed_position_ids"))) & known
    open_ids = (set(_unique_strings(record.get("open_position_ids"))) |
                set(_unique_strings(record.get("verified_position_ids")))) & known
    open_ids -= closed
    unresolved = known - open_ids - closed
    record["position_ids"] = sorted(known)
    record["open_position_ids"] = sorted(open_ids)
    record["closed_position_ids"] = sorted(closed)
    record["unresolved_position_ids"] = sorted(unresolved)
    all_resolved = bool(known) and not unresolved
    if all_resolved and open_ids:
        record["position_state"] = "OPEN_CONFIRMED"
        record["broker_position_status"] = "OPEN_CONFIRMED"
        record["verified_position_ids"] = sorted(open_ids)
        record["position_verified"] = True
    elif all_resolved and closed == known:
        record["position_state"] = "CLOSED_CONFIRMED"
        record["broker_position_status"] = "CLOSED_CONFIRMED"
        record["verified_position_ids"] = []
        record["position_verified"] = False
    else:
        previous = str(record.get("position_state") or "").upper()
        record["position_state"] = ("MISSING_REVIEW" if previous == "MISSING_REVIEW"
                                    else "NOT_SEEN")
        record["broker_position_status"] = (
            "PARTIALLY_RESOLVED" if open_ids or closed else "PROPAGATION_PENDING")
        record["verified_position_ids"] = sorted(open_ids)
        record["position_verified"] = False


_LEDGER_CLOSE_SUCCESS = {"LINKED_CLOSED", "CLOSED", "CLOSED_WITHOUT_RESULT"}
# 9.5.2: Endzustaende, die NICHT mehr nachbearbeitet werden. Ein Altbestand
# ohne Kontobindung laesst sich nicht exakt verknuepfen -- er bleibt als
# Auditspur stehen, wird aber nicht in jedem Zyklus erneut versucht.
LEDGER_CLOSE_ENDGUELTIG = _LEDGER_CLOSE_SUCCESS | {"LEDGER_BACKFILL_LEGACY_UNBOUND"}


def _closed_accounting_pending(record: dict) -> bool:
    """Ein Broker-Close gibt die Kaufdomain erst nach lokalem Commit frei."""
    closed = set(_unique_strings(record.get("closed_position_ids")))
    if not closed:
        return False
    # Vor Schema 3 bereits terminal gespeicherte, kontolose CLOSED-Saetze sind
    # keine beweisbare Ownership dieses Kontos. Sie bleiben als unveraenderte
    # historische Auditspur sichtbar, werden aber weder nachtraeglich einem
    # konkreten Account zugeschrieben noch als neuer Accounting-Lock projiziert.
    # Jeder *neue*, kontogebundene Close durchlaeuft weiterhin den strikten
    # Ledger-Commit unten.
    if (bool(record.get("legacy_unbound_closed"))
            and not str(record.get("account_fingerprint") or "")):
        return False
    results = record.get("ledger_close_backfill") or {}
    if not isinstance(results, dict):
        return True
    for position_id in closed:
        result = results.get(position_id) or {}
        if str(result.get("status") or "").upper() not in _LEDGER_CLOSE_SUCCESS:
            return True
    return False


def _record_is_active(record: dict) -> bool:
    """Die wirkliche, domain-sperrende Bedeutung des v3-Datensatzes."""
    if bool(record.get("manual_review_required")):
        return True
    # 9.5.2: Eine reine Buchungsluecke sperrt den KAUF nicht mehr. Der Broker
    # hat abgeschlossen; es fehlt die lokale Verknuepfung. Sie bleibt ueber
    # buchungsluecken() sichtbar und setzt dort den Hinweis PNL_INCOMPLETE.
    # Bis 9.5.1 stand deswegen "2 ungeklaerte Orders" in der Oberflaeche,
    # obwohl eine davon (CRM) gar keine offene Order mehr war.
    execution = str(record.get("execution_state") or
                    record.get("broker_execution_state") or "UNKNOWN").upper()
    submit = str(record.get("submit_state") or "").upper()
    if record.get('order_terminal') is False:
        # A verified partial position does not prove cancellation of the rest.
        return True
    if _execution_has_fill(record):
        known = _known_position_ids(record)
        resolved = (set(_unique_strings(record.get("open_position_ids"))) |
                    set(_unique_strings(record.get("closed_position_ids"))))
        # Ein behaupteter Fill ohne IDs bzw. eine nicht vollstaendig geklaerte
        # ID-Menge bleibt gesperrt, ohne zeitgesteuerte Freigabe.
        return not known or not known.issubset(resolved)
    if execution in {"REJECTED", "CANCELLED", "EXPIRED"}:
        return False
    if submit in {"DEFINITIVE_REJECT", "FAILED_BEFORE_SUBMIT", "UNPROVABLE"}:
        return False
    if str(record.get("state") or "").upper() in {"FAILED", "FAILED_BEFORE_SUBMIT"}:
        return False
    return submit in {"PREPARED", "POST_MAY_HAVE_BEEN_SENT", "ACCEPTED"} or (
        str(record.get("state") or "").upper() in NON_TERMINAL)


def _sync_legacy_state(record: dict) -> None:
    """Leitet das alte ``state``-Feld ohne Verlust staerkerer Evidenz ab."""
    _sync_position_evidence(record)
    execution = str(record.get("execution_state") or "UNKNOWN").upper()
    submit = str(record.get("submit_state") or "PREPARED").upper()
    position = str(record.get("position_state") or "NOT_SEEN").upper()
    if (position == "CLOSED_CONFIRMED" and _known_position_ids(record)
            and _closed_accounting_pending(record)):
        state = "CLOSED_ACCOUNTING_PENDING"
    elif position == "CLOSED_CONFIRMED" and _known_position_ids(record):
        state = "CLOSED_BEFORE_IMPORT"
    elif _execution_has_fill(record):
        state = (execution if position == "OPEN_CONFIRMED"
                 else "AWAITING_POSITION_CONFIRMATION")
    elif execution in {"REJECTED", "CANCELLED", "EXPIRED"}:
        state = execution
    elif submit == "FAILED_BEFORE_SUBMIT":
        state = "FAILED_BEFORE_SUBMIT"
    elif submit == "DEFINITIVE_REJECT":
        state = "FAILED"
    elif submit == "UNPROVABLE":
        state = "UNPROVABLE"
    elif submit == "PREPARED":
        state = "SUBMITTING"
    elif submit == "ACCEPTED":
        state = ("UNKNOWN_AFTER_SUBMIT" if record.get("lookup_uncertain")
                 else "RECONCILING")
    else:
        state = "UNKNOWN_AFTER_SUBMIT"
    record["state"] = state
    record["broker_execution_state"] = execution


def _migrate_record(raw: dict) -> dict:
    """Liest v1/v2 verlustfrei und relockt gefuellte UNPROVABLE-Altsaetze."""
    record = dict(raw or {})
    legacy = str(record.get("state") or "SUBMITTING").upper()
    if (legacy == "CLOSED_BEFORE_IMPORT"
            and not str(record.get("account_fingerprint") or "")):
        record["legacy_unbound_closed"] = True
    record["order_ids"] = _unique_strings(record.get("order_ids"))
    record["fills"] = _merge_fills([], record.get("fills"))
    record["position_ids"] = _unique_strings(
        list(record.get("position_ids") or []) +
        [x.get("position_id") for x in record["fills"]])
    record["stream_position_ids"] = _unique_strings(record.get("stream_position_ids"))
    if not record.get("submit_state"):
        if legacy == "UNPROVABLE" and not _execution_has_fill(record):
            record["submit_state"] = "UNPROVABLE"
        elif legacy in {"FAILED", "FAILED_BEFORE_SUBMIT"}:
            record["submit_state"] = ("FAILED_BEFORE_SUBMIT" if
                                      legacy == "FAILED_BEFORE_SUBMIT" else
                                      "DEFINITIVE_REJECT")
        elif legacy == "SUBMITTING":
            record["submit_state"] = "PREPARED"
        elif record.get("order_ids") or record.get("reference_id"):
            record["submit_state"] = "ACCEPTED"
        else:
            record["submit_state"] = "POST_MAY_HAVE_BEEN_SENT"
    current_execution = str(record.get("execution_state") or
                            record.get("broker_execution_state") or "").upper()
    if not current_execution:
        current_execution = (legacy if legacy in {
            "FILLED", "PARTIALLY_FILLED", "REJECTED_PARTIALLY_FILLED", "CANCELED_PARTIALLY_FILLED",
            "REJECTED", "CANCELLED", "EXPIRED"} else "UNKNOWN")
    requested = _float(record.get("quantity"))
    exact_filled = sum(_float(x.get("quantity")) for x in record["fills"])
    record["filled_quantity"] = max(_float(record.get("filled_quantity")), exact_filled)
    record["remaining_quantity"] = max(
        0.0, requested - _float(record.get("filled_quantity")))
    record["execution_state"] = _merge_execution_state(
        "UNKNOWN", current_execution,
        filled=_float(record.get("filled_quantity")), requested=requested)
    if legacy == "CLOSED_BEFORE_IMPORT" or str(
            record.get("broker_position_status") or "").upper() == "CLOSED_CONFIRMED":
        record["closed_position_ids"] = _unique_strings(
            list(record.get("closed_position_ids") or []) + record["position_ids"])
    elif bool(record.get("position_verified")) or str(
            record.get("broker_position_status") or "").upper() == "OPEN_CONFIRMED":
        record["open_position_ids"] = _unique_strings(
            list(record.get("open_position_ids") or []) +
            list(record.get("verified_position_ids") or []) + record["position_ids"])
    # v2 UNPROVABLE war eine Zeitheuristik. Bei Fillbeleg darf sie niemals
    # Kapital/Domain freigeben; Schema 3 macht daraus eine sichtbare Pruefung.
    if legacy == "UNPROVABLE" and _execution_has_fill(record):
        record["position_state"] = "MISSING_REVIEW"
        record["last_error"] = ("Migriert: Fill bewiesen, Position noch nicht ueber alle "
                                "positionIds als OPEN/CLOSED geklaert")
    _sync_legacy_state(record)
    return record

# v9.3: Hauptthread und der 5-Sekunden-Worker (broker/etoro.py) rufen dieselben
# Funktionen. Jede davon macht _load() -> aendern -> _save(). Ohne Lock geht
# genau so eine Aenderung verloren:
#     A liest X | B liest X | A speichert A' | B speichert B' (auf Basis X)
# atomic_write_json() verhindert nur eine halbe Datei, nicht dieses Muster.
# Der Lock ist reentrant, damit verschachtelte Aufrufe nicht blockieren, und
# er wird NIE ueber einen Netzaufruf gehalten -- sonst wuerde der Worker den
# Scanner ausbremsen.
_LOCK = threading.RLock()


def _synchron(fn):
    """Die ganze Lese-Aendere-Schreib-Folge thread- und prozesssicher."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        with _LOCK, critical_state_lock(path()):
            return fn(*args, **kwargs)
    return wrapper


def _root() -> Path:
    return Path(os.getenv("TRADINGBOT_TEST_STATE_DIR", "").strip() or Path(__file__).resolve().parent)


def path() -> Path:
    return _root() / getattr(config, "ETORO_RECONCILIATION_FILE", "etoro_reconciliation.json")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load() -> dict:
    try:
        data = json.loads(path().read_text(encoding="utf-8")) if path().exists() else {}
        if not isinstance(data, dict):
            return {"schema": SCHEMA_VERSION, "records": {}, "storage_error": "invalid_root"}
        if not data.get("storage_error"):
            data["records"] = {
                str(key): _migrate_record(record)
                for key, record in (data.get("records") or {}).items()
                if isinstance(record, dict)
            }
            data["schema"] = SCHEMA_VERSION
        return data
    except Exception:
        # Fail closed: corrupt evidence must not become an empty, tradeable domain.
        return {"schema": 1, "records": {}, "storage_error": "unreadable"}


def _save(data: dict) -> None:
    normalised = {}
    for key, record in (data.get("records") or {}).items():
        if not isinstance(record, dict):
            continue
        migrated = _migrate_record(record)
        record.clear()
        record.update(migrated)
        normalised[str(key)] = record
    data["records"] = normalised
    data["schema"] = SCHEMA_VERSION
    data["updated_at_utc"] = _now()
    atomic_write_json(path(), data)


def domain_key(*, paper: bool, profile: str,
               account_fingerprint: str = "") -> str:
    # An unresolved broker order belongs to the account, not to the selected
    # risk profile. Otherwise a profile switch could bypass the buy lock.
    account = str(account_fingerprint or "").strip()
    base = f"etoro:{'demo' if paper else 'live'}"
    return f"{base}:{account}" if account else base


KONTO_ALIAS_SCHEMA = 1


def _alias_index(data: dict) -> dict:
    """{alter Fingerprint: aktueller Fingerprint} -- nur bewiesene Paare."""
    roh = (data or {}).get("konto_aliase") or {}
    return {str(k): str(v) for k, v in roh.items() if str(k) and str(v)}


@_synchron
def konto_aliase() -> dict:
    return _alias_index(_load())


@_synchron
def konto_alias_registrieren(alt_fingerprint: str, neu_fingerprint: str,
                             *, beleg: str) -> bool:
    """Einen Vorgaengerfingerprint als DASSELBE Konto festhalten (9.5.2).

    Wird ausschliesslich aufgerufen, wenn der Broker eine gespeicherte
    orderId/referenceId dieses Datensatzes im AKTUELLEN Konto bestaetigt hat.
    Der Beweis ist damit brokerseitig, nicht namensbasiert -- eine
    Formelrekonstruktion des alten Verfahrens waere geraten.
    """
    alt_fp = str(alt_fingerprint or "").strip()
    neu_fp = str(neu_fingerprint or "").strip()
    if not alt_fp or not neu_fp or alt_fp == neu_fp:
        return False
    data = _load()
    aliase = _alias_index(data)
    if aliase.get(alt_fp) == neu_fp:
        return False
    aliase[alt_fp] = neu_fp
    data["konto_aliase"] = aliase
    data.setdefault("konto_alias_belege", {})[alt_fp] = {
        "neu": neu_fp, "beleg": str(beleg)[:300], "at_utc": _now(),
        "schema": KONTO_ALIAS_SCHEMA}
    _save(data)
    logger.warning("eToro-Kontoalias belegt: %s gehoert zu %s (%s)",
                   alt_fp, neu_fp, beleg)
    return True


def domaenen_basis(domain: str) -> str:
    """``etoro:demo`` aus ``etoro:demo:<fingerprint>``."""
    return ":".join(str(domain or "").split(":")[:2])


def domaenen_zuordnung(record: dict, domain: str, aliase: dict | None = None) -> str:
    """Wie gehoert dieser Datensatz zur angefragten Kontodomaene? (9.5.2)

    * ``AKTUELL``   -- exakt dieselbe Domaene inklusive Fingerprint
    * ``ALTBESTAND``-- gleiche Umgebung, noch voellig ohne Fingerprint
    * ``FREMDER_FINGERPRINT`` -- gleiche Umgebung, ANDERER Fingerprint
    * ``FREMD``     -- andere Umgebung oder anderer Broker

    Der dritte Fall entstand beim Wechsel des Fingerprint-Verfahrens in
    9.5.1: Datensaetze aus 9.5 tragen noch die alte Kennung. In 9.5.1 fielen
    sie durch jede Pruefung -- der Worker sah sie nicht, konnte sie also nie
    klaeren, waehrend die Oberflaeche sie zaehlte. ADBE stand deshalb am
    02.09.2026 seit 17 Stunden auf ``position_absence_checks: 1`` und waere
    dort fuer immer geblieben.
    """
    aliase = dict(aliase or {})
    record_domain = str(record.get("domain") or "")
    requested = str(domain or "")
    if record_domain == requested:
        return "AKTUELL"
    basis = domaenen_basis(requested)
    if domaenen_basis(record_domain) != basis:
        return "FREMD"
    eigener = str(record.get("account_fingerprint") or "")
    if not eigener:
        return "ALTBESTAND"
    ziel = ":".join(requested.split(":")[2:])
    if aliase and ziel and aliase.get(eigener) == ziel:
        return "ALIAS"        # brokerseitig als dasselbe Konto belegt
    return "FREMDER_FINGERPRINT"


@_synchron
def active_for_domain(domain: str, *, exclude_decision_id: int | None = None,
                      mit_fremdem_fingerprint: bool = False) -> list[dict]:
    """Sperrende Datensaetze dieser Kontodomaene.

    Sperrend sind: die aktuelle Domaene, kontolose Altbestaende derselben
    Umgebung und Fingerprints, die per Order-Lookup als DASSELBE Konto
    belegt wurden (``konto_alias_registrieren``).

    Ein unbekannter fremder Fingerprint sperrt NICHT -- er koennte zu einem
    anderen eToro-Konto gehoeren, und ein fremdes Konto darf diesen Handel
    nicht anhalten. Er verschwindet aber auch nicht: ``klaerungskandidaten()``
    liefert ihn dem Worker, damit er belegt oder ausgeschlossen wird.
    """
    data = _load()
    if data.get("storage_error"):
        return [{"state": "RECONCILING", "reason": "reconciliation storage unreadable"}]
    out = []
    aliase = _alias_index(data)
    for record in (data.get("records") or {}).values():
        zuordnung = domaenen_zuordnung(record, domain, aliase)
        if zuordnung == "FREMD":
            continue
        if zuordnung == "FREMDER_FINGERPRINT" and not mit_fremdem_fingerprint:
            continue
        if exclude_decision_id is not None and int(record.get("decision_id") or 0) == int(exclude_decision_id):
            continue
        if _record_is_active(record):
            eintrag = dict(record)
            eintrag["_domaenen_zuordnung"] = zuordnung
            out.append(eintrag)
    return out


@_synchron
def klaerungskandidaten(domain: str) -> list[dict]:
    """Saetze, die der Worker bearbeiten MUSS -- inklusive Altgenerationen.

    Der Klaerungsbereich ist bewusst weiter als der Sperrbereich. In 9.5.1
    waren beide identisch und exakt: ein Satz mit dem 9.5-Fingerprint fiel
    durch jede Pruefung, wurde also nie bearbeitet und konnte sich nie
    klaeren. ADBE stand deshalb am 02.09.2026 seit 17 Stunden unveraendert
    auf ``position_absence_checks: 1``.
    """
    data = _load()
    if data.get("storage_error"):
        return []
    aliase = _alias_index(data)
    out = []
    for record in (data.get("records") or {}).values():
        if domaenen_zuordnung(record, domain, aliase) == "FREMD":
            continue
        if _record_is_active(record) or _closed_accounting_pending(record):
            out.append(dict(record))
    return out


@_synchron
def melde_buchungsluecke(*, symbol: str, broker_position_id: str = "",
                         grund: str = "", domain: str = "",
                         account_fingerprint: str = "", environment: str = "",
                         close_evidence: dict | None = None) -> None:
    """Persist a scoped repair record before an unbooked fill is acknowledged.

    Records are retained, including their resolution history. A diagnostic
    success flag never substitutes for the ledger proof read by accounting_status.
    """
    data = _load()
    if data.get("storage_error"):
        raise BrokerFehler("eToro-Buchungsluecke kann nicht dauerhaft gespeichert werden")
    env = str(environment or "").upper()
    dom = str(domain or "").strip().lower()
    if not dom and account_fingerprint and env in {"DEMO", "LIVE"}:
        dom = f"etoro:{env.lower()}:{account_fingerprint}"
    dom = dom or "UNKNOWN"
    symbol = str(symbol).upper()
    pid = str(broker_position_id or "")
    rows = data.setdefault("unzugeordnete_verkaeufe", [])
    if not isinstance(rows, list):
        raise BrokerFehler("eToro-Buchungslueckenregister hat ein ungueltiges Format")
    existing = next((x for x in rows if isinstance(x, dict)
                     and str(x.get("domaene") or "UNKNOWN").lower() == dom.lower()
                     and str(x.get("symbol") or "").upper() == symbol
                     and str(x.get("broker_position_id") or "") == pid), None)
    if existing is None:
        existing = {"kennung": f"{symbol}:{pid}", "symbol": symbol,
                    "broker_position_id": pid, "grund": str(grund or ""),
                    "zeit": _now(), "domaene": dom,
                    "account_fingerprint": str(account_fingerprint or ""),
                    "environment": env}
        rows.append(existing)
    elif not close_evidence:
        return
    if close_evidence:
        evidence = {k: v for k, v in close_evidence.items() if k in {
            "fill_id", "order_id", "quantity", "price", "closed_at_utc", "fee", "fee_currency"}}
        known = existing.setdefault("close_evidence", [])
        if evidence in known:
            return
        same_id = [x for x in known if x.get("fill_id") and x.get("fill_id") == evidence.get("fill_id")]
        if same_id and any(x != evidence for x in same_id):
            existing["evidence_conflict"] = True
        known.append(evidence)
    # No truncation of unresolved accounting evidence.
    _save(data)


def accounting_status(domain: str = "", *, now=None) -> dict:
    """Read-only projection from the actual ledger, not from a stale UI flag."""
    from decision_analytics import db_pfad
    from etoro_accounting_resolution import project, read_ledger
    data = _load()
    return project(data, read_ledger(db_pfad()), domain=str(domain or ""), now=now,
                   timezone_name=str(getattr(config, "LOCAL_TIMEZONE", "Europe/Berlin")))


@_synchron
def resolve_accounting_gaps(domain: str = "", *, now=None) -> dict:
    """Persist diagnostic resolutions; never modify ledger amounts or ownership."""
    from decision_analytics import db_pfad
    from etoro_accounting_resolution import project, read_ledger
    data = _load()
    report = project(data, read_ledger(db_pfad()), domain=str(domain or ""), now=now,
                     timezone_name=str(getattr(config, "LOCAL_TIMEZONE", "Europe/Berlin")))
    if report.get("storage_error"):
        return {**report, "changed": 0}
    changes = 0
    for gap in data.get("unzugeordnete_verkaeufe") or []:
        match = next((x for x in report["items"] if
                      x.get("domaene", "").lower() == str(gap.get("domaene") or "UNKNOWN").lower()
                      and x.get("broker_position_id") == str(gap.get("broker_position_id") or "")
                      and x.get("symbol") == str(gap.get("symbol") or "").upper()), None)
        if match is None:
            continue
        resolution = {k: match.get(k) for k in (
            "status", "blocks_entries", "trade_ids", "result_status", "effective_at_utc", "detail")}
        if resolution == gap.get("resolution"):
            continue
        if gap.get("resolution"):
            gap.setdefault("resolution_history", []).append(gap["resolution"])
        gap["resolution"] = resolution
        gap["resolution_updated_at_utc"] = report["checked_at_utc"]
        changes += 1
    if changes:
        _save(data)
        logger.info("eToro-Buchungsabgleich: %d Diagnosezustaende aktualisiert; %d sperrend, %d verknuepft, %d Legacy-Audit",
                    changes, len(report["blocking"]), len(report["resolved"]), len(report["legacy"]))
    return {**report, "changed": changes}


def buchungsluecken(domain: str = "") -> list[dict]:
    """Only proof-backed, scoped outstanding accounting issues block readiness."""
    return accounting_status(domain)["blocking"]


def pnl_unvollstaendig(domain: str = "") -> tuple[bool, str]:
    issues = buchungsluecken(domain)
    if not issues:
        return False, ""
    names = ", ".join(sorted({str(x.get("symbol") or "") for x in issues if x.get("symbol")}))
    return True, (f"PNL_INCOMPLETE: {len(issues)} Buchungsfall/-faelle in eToro "
                  f"noch zu klaeren ({names or 'Speicherpruefung'}). "
                  "Keine Freigabe ohne Ledgerbeleg; Details und historische "
                  "Auditfaelle stehen im eToro-Buchungsabgleich.")


def blocking_detail(domain: str, *, exclude_decision_id: int | None = None) -> str:
    """Konkreter, ausschliesslich fuer ``domain`` geltender Sperrgrund.

    Ein leerer String bedeutet: diese Domain ist frei. Der Helper gibt keine
    globale Aussage ab und kann deshalb sicher in Readiness/WebUI erscheinen.
    """
    records = active_for_domain(
        str(domain), exclude_decision_id=exclude_decision_id)
    if not records:
        return ""
    record = records[0]
    if record.get("reason"):
        return str(record["reason"])
    symbol = str(record.get("symbol") or "?")
    did = int(record.get("decision_id") or 0)
    execution = str(record.get("execution_state") or
                    record.get("broker_execution_state") or "UNKNOWN")
    position = str(record.get("position_state") or
                   record.get("broker_position_status") or "NOT_SEEN")
    pids = _unique_strings(record.get("unresolved_position_ids") or
                           record.get("position_ids"))
    ids = f"; positionIds={','.join(pids)}" if pids else "; positionId fehlt"
    order_ids = _unique_strings(record.get("order_ids"))
    anchors = (f"; orderId={','.join(order_ids)}" if order_ids else "")
    if record.get("reference_id"):
        anchors += f"; referenceId={record.get('reference_id')}"
    error = str(record.get("last_error") or "").strip()
    anomaly = str(record.get("execution_anomaly_code") or "").strip()
    if anomaly:
        error = (f"MANUAL_REVIEW {anomaly}: "
                 f"{record.get('execution_anomaly_detail') or error}")
    suffix = f"; {error}" if error else ""
    return (f"{symbol} decision_id={did}: state={record.get('state')}, "
            f"execution={execution}, position={position}{anchors}{ids}{suffix}")


def assert_domain_available(*, paper: bool, profile: str,
                            account_fingerprint: str = "",
                            decision_id: int | None = None) -> None:
    domain = domain_key(paper=paper, profile=profile,
                        account_fingerprint=account_fingerprint)
    detail = blocking_detail(domain, exclude_decision_id=decision_id)
    if detail:
        raise BrokerFehler(
            f"Neue Kaeufe gesperrt: ungeklärte eToro-Ausführung in {domain}: {detail}")


@_synchron
def start_intent(*, decision_id: int, symbol: str, paper: bool, profile: str,
                 quantity: float, price: float, stop: float, take_profit: float,
                 account_fingerprint: str = "") -> dict:
    data = _load()
    if data.get("storage_error"):
        raise BrokerFehler("eToro-Reconciliation-Datei ist nicht lesbar; Kauf fail-closed")
    key = str(int(decision_id))
    existing = (data.get("records") or {}).get(key)
    if existing and str(existing.get("state") or "").upper() in NON_TERMINAL | TERMINAL:
        raise BrokerFehler("Diese decision_id besitzt bereits einen Submit-Datensatz; kein Doppel-POST")
    record = {
        "decision_id": int(decision_id), "symbol": str(symbol).upper(), "broker": "etoro",
        "paper": bool(paper), "profile": str(profile or ""),
        "domain": domain_key(paper=paper, profile=profile,
                             account_fingerprint=account_fingerprint),
        "account_fingerprint": str(account_fingerprint or ""),
        "quantity": float(quantity),
        "price": float(price), "stop": float(stop), "take_profit": float(take_profit),
        "state": "SUBMITTING", "created_at_utc": _now(), "updated_at_utc": _now(),
        "submit_state": "PREPARED", "execution_state": "UNKNOWN",
        "position_state": "NOT_SEEN", "post_attempted_at_utc": "",
        "order_ids": [], "reference_id": "", "position_ids": [], "fills": [],
        "open_position_ids": [], "closed_position_ids": [],
        "unresolved_position_ids": [], "verified_position_ids": [],
        "filled_quantity": 0.0, "remaining_quantity": float(quantity),
    }
    data.setdefault("records", {})[key] = record
    _save(data)
    record_execution_event(decision_id, "SUBMITTING", record,
                           event_id=f"etoro:{decision_id}:submitting")
    return record


@_synchron
def reserve_and_start_intent(*, decision_id: int, symbol: str, paper: bool,
                             profile: str, quantity: float, price: float,
                             stop: float, take_profit: float,
                             account_fingerprint: str = "") -> dict:
    """Domain-Sperre und SUBMITTING-Datensatz in einer atomaren Operation.

    In 9.3.1 lagen ``assert_domain_available`` und ``start_intent`` getrennt.
    Zwei gleichzeitige Kaufentscheidungen konnten beide die freie Domain
    sehen und danach jeweils einen POST vorbereiten.
    """
    domain = domain_key(
        paper=paper, profile=profile,
        account_fingerprint=account_fingerprint)
    if active_for_domain(domain, exclude_decision_id=decision_id):
        raise BrokerFehler(
            f"Neue Kaeufe gesperrt: eToro-Ausfuehrung in {domain} laeuft")
    return start_intent(
        decision_id=decision_id, symbol=symbol, paper=paper, profile=profile,
        quantity=quantity, price=price, stop=stop, take_profit=take_profit,
        account_fingerprint=account_fingerprint)


@_synchron
def accepted(decision_id: int, *, order_id: str = "", reference_id: str = "") -> dict:
    """Persist immediately after POST acceptance and before every lookup."""
    data = _load(); record = data.setdefault("records", {}).get(str(int(decision_id)))
    if not record:
        raise BrokerFehler("Angenommene eToro-Order ohne persistierte Kaufabsicht")
    if order_id and str(order_id) not in record["order_ids"]:
        record["order_ids"].append(str(order_id))
    if reference_id:
        record["reference_id"] = str(reference_id)
    if str(record.get("submit_state") or "") not in {
            "ACCEPTED", "DEFINITIVE_REJECT", "FAILED_BEFORE_SUBMIT"}:
        record["submit_state"] = "ACCEPTED"
    record["post_attempted_at_utc"] = (
        str(record.get("post_attempted_at_utc") or "") or _now())
    _sync_legacy_state(record)
    record["updated_at_utc"] = _now()
    _save(data)
    mark_execution(decision_id, "RECONCILING", record["order_ids"],
                   reference_id=record["reference_id"], broker_paper=record["paper"])
    record_order_state(decision_id, broker="etoro",
                       broker_order_id=str(order_id or reference_id), role="ENTRY",
                       status="RECONCILING", symbol=record["symbol"],
                       client_order_id=reference_id, requested_qty=record["quantity"],
                       remaining_qty=record["quantity"], requested_price=record["price"],
                       raw={"stop": record["stop"], "take_profit": record["take_profit"]})
    record_execution_event(decision_id, "ORDER_ACCEPTED", record,
                           event_id=f"etoro:{decision_id}:accepted:{order_id or reference_id}")
    _notify(record, "uncertain")
    return dict(record)


@_synchron
def register_reference(decision_id: int, reference_id: str) -> dict:
    """Persist the idempotency/lookup anchor before the broker POST."""
    data = _load(); record = data.setdefault("records", {}).get(str(int(decision_id)))
    if not record:
        raise BrokerFehler("eToro-referenceId ohne persistierte Kaufabsicht")
    record["reference_id"] = str(reference_id or "")
    record["updated_at_utc"] = _now()
    _save(data)
    record_execution_event(
        decision_id, "REFERENCE_RESERVED", {"reference_id": record["reference_id"]},
        event_id=f"etoro:{decision_id}:reference:{record['reference_id']}",
    )
    return dict(record)


@_synchron
def mark_post_attempted(decision_id: int, *, reference_id: str = "") -> dict:
    """Persistiert die Sicherheitsgrenze unmittelbar VOR dem Netzwerk-POST.

    Ab hier darf eine generische Exception den Kauf nicht mehr freigeben. Nur
    ein definitiver Brokerbeleg darf die Domain terminalisieren.
    """
    data = _load()
    record = data.setdefault("records", {}).get(str(int(decision_id)))
    if not record:
        raise BrokerFehler("eToro-POST ohne persistierte Kaufabsicht")
    if reference_id:
        existing = str(record.get("reference_id") or "")
        if existing and existing != str(reference_id):
            raise BrokerFehler("Abweichende referenceId vor eToro-POST")
        record["reference_id"] = str(reference_id)
    if str(record.get("submit_state") or "") == "PREPARED":
        record["submit_state"] = "POST_MAY_HAVE_BEEN_SENT"
    record["post_attempted_at_utc"] = (
        str(record.get("post_attempted_at_utc") or "") or _now())
    _sync_legacy_state(record)
    record["updated_at_utc"] = _now()
    _save(data)
    record_execution_event(
        decision_id, "POST_MAY_HAVE_BEEN_SENT",
        {"reference_id": str(record.get("reference_id") or "")},
        event_id=f"etoro:{decision_id}:post-attempted",
    )
    return dict(record)


@_synchron
def apply_stream_event(event: dict, *, account_fingerprint: str = "",
                       paper: bool | None = None, cid: str = "",
                       _replay: bool = False) -> list[int]:
    """Persist a bound private hint before matching; never infer executions."""
    import etoro_stream_inbox as inbox
    receipt = inbox.persist_event(event, account_fingerprint=account_fingerprint,
                                  paper=paper, cid=cid, replay=_replay)
    if receipt["validation"] != "VALID":
        return []
    content = dict((event or {}).get("content") or {})
    kind = inbox.event_kind((event or {}).get("message_type", ""))
    request_guid = inbox.field(content, "RequestGuid", "requestGuid")
    order_id = inbox.field(content, "OrderID", "OrderId", "orderId", "orderID")
    position_id = inbox.field(content, "PositionID", "PositionId", "positionId", "positionID")
    instrument_id = inbox.field(content, "InstrumentID", "InstrumentId", "instrumentId", "instrumentID")
    data = _load()
    if data.get("storage_error"):
        inbox.mark_processed(receipt["event_key"], error="Reconciliation-Speicher nicht lesbar")
        return []
    matched: list[int] = []
    try:
        for record in (data.get("records") or {}).values():
            # Matching a position number alone is insufficient across accounts
            # or DEMO/LIVE. Binding comes from authenticated /me + stream.
            if (str(record.get("account_fingerprint") or "") != account_fingerprint
                    or "paper" not in record or record["paper"] is not paper):
                continue
            record_instrument = str(record.get("instrument_id") or "")
            if instrument_id and record_instrument and instrument_id != record_instrument:
                continue
            same_reference = bool(request_guid and request_guid == str(record.get("reference_id") or ""))
            same_order = bool(order_id and order_id in {str(x) for x in (record.get("order_ids") or [])})
            same_position = bool(position_id and position_id in {str(x) for x in (record.get("position_ids") or [])})
            if not (same_reference or same_order or same_position):
                continue
            did = int(record.get("decision_id") or 0)
            previous = record.get("last_stream_event") or {}
            if (previous.get("event_key") == receipt["event_key"] or
                    str(previous.get("received_at_utc") or "") > str((event or {}).get("received_at_utc") or _now())):
                if did:
                    matched.append(did)
                continue
            # Only a typed opening event tied to its own reference/order may
            # contribute an entry ID. A close ID can NEVER become an entry ID.
            if kind == "OPEN" and (same_reference or same_order):
                if order_id and order_id not in record.setdefault("order_ids", []):
                    record["order_ids"].append(order_id)
                if (str(record.get("submit_state") or "") in {"PREPARED", "POST_MAY_HAVE_BEEN_SENT"}
                        and str(record.get("execution_state") or "UNKNOWN") == "UNKNOWN"
                        and str(record.get("position_state") or "NOT_SEEN") == "NOT_SEEN"):
                    record["submit_state"] = "ACCEPTED"
                    _sync_legacy_state(record)
            elif kind == "CLOSE" and position_id and same_position:
                close_order = inbox.field(content, "CloseOrderID", "CloseOrderId", "closeOrderId", "ClosingOrderId")
                if str((event or {}).get("message_type") or "").lower().startswith("trading.orderforclose"):
                    close_order = close_order or order_id
                if close_order and close_order not in {str(x) for x in (record.get("order_ids") or [])}:
                    if close_order not in record.setdefault("stream_close_order_ids", []):
                        record["stream_close_order_ids"].append(close_order)
                record["last_stream_close_hint"] = receipt["event_key"]
            if position_id and position_id not in record.setdefault("stream_position_ids", []):
                record["stream_position_ids"].append(position_id)
            record["last_stream_event"] = {
                "event_key": receipt["event_key"], "kind": kind,
                "message_id": str((event or {}).get("message_id") or ""),
                "message_type": str((event or {}).get("message_type") or ""),
                "received_at_utc": str((event or {}).get("received_at_utc") or _now()),
                "status_id": content.get("StatusID"),
                "executed_units": content.get("ExecutedUnits"),
                "error_code": content.get("ErrorCode"),
            }
            record["updated_at_utc"] = _now()
            if did:
                matched.append(did)
                # Inbox holds the sanitized payload; this event links to it.
                record_execution_event(did, "ETORO_PRIVATE_WS", record["last_stream_event"],
                                       event_id=f"etoro-ws:{receipt['event_key']}:{did}")
        if matched:
            _save(data)
        inbox.mark_processed(receipt["event_key"], matched=matched)
    except Exception as exc:
        inbox.mark_processed(receipt["event_key"], error=type(exc).__name__)
        raise
    return matched


@_synchron
def mark_submit_failed(decision_id: int, *, state: str, detail: str = "") -> dict:
    """Terminalize a definitively failed submit without discarding evidence.

    This must only be used for failures where the broker adapter did not turn
    the result into OrderStatusUnklar. Ambiguous POST outcomes stay locked.
    """
    target = str(state or "FAILED").upper()
    if target not in {"FAILED", "FAILED_BEFORE_SUBMIT"}:
        raise ValueError(f"ungueltiger terminaler Submit-Status: {target}")
    data = _load(); record = data.setdefault("records", {}).get(str(int(decision_id)))
    if not record:
        return {}
    if (str(record.get("submit_state") or "") != "PREPARED"
            or bool(record.get("post_attempted_at_utc"))
            or bool(record.get("order_ids"))
            or _execution_has_fill(record)):
        # Accepted/ambiguous evidence must never be unlocked by a broad catch.
        return dict(record)
    record["submit_state"] = (
        "FAILED_BEFORE_SUBMIT" if target == "FAILED_BEFORE_SUBMIT"
        else "DEFINITIVE_REJECT")
    record["last_error"] = str(detail)[:500]
    _sync_legacy_state(record)
    record["updated_at_utc"] = _now()
    _save(data)
    mark_execution(
        decision_id, target, record.get("order_ids") or [],
        reference_id=record.get("reference_id", ""), broker_paper=record.get("paper"),
    )
    record_execution_event(
        decision_id, target, {"detail": record["last_error"]},
        event_id=f"etoro:{decision_id}:{target.lower()}",
    )
    return dict(record)


@_synchron
def mark_unknown(decision_id: int, *, order_ids=(), reference_id: str = "", detail: str = "") -> None:
    data = _load(); record = data.setdefault("records", {}).get(str(int(decision_id)))
    if not record:
        return
    for oid in order_ids or ():
        if str(oid) and str(oid) not in record["order_ids"]:
            record["order_ids"].append(str(oid))
    if reference_id:
        existing = str(record.get("reference_id") or "")
        if not existing:
            record["reference_id"] = str(reference_id)
        elif existing != str(reference_id):
            record["last_conflicting_reference_id"] = str(reference_id)
    # Ein spaeter Transport-/Lookupfehler ist schwaecher als jeder Fill- oder
    # Positionsbeweis. Nur die Submit-Unsicherheit wird vermerkt.
    if str(record.get("submit_state") or "") == "PREPARED":
        record["submit_state"] = "POST_MAY_HAVE_BEEN_SENT"
    record["post_attempted_at_utc"] = (
        str(record.get("post_attempted_at_utc") or "") or _now())
    record["last_error"] = str(detail)[:500]
    record["lookup_uncertain"] = True
    record["last_lookup_uncertain_at_utc"] = _now()
    _sync_legacy_state(record)
    actual_state = str(record.get("state") or "UNKNOWN_AFTER_SUBMIT")
    record["updated_at_utc"] = _now(); _save(data)
    mark_execution(decision_id, actual_state, record["order_ids"],
                   reference_id=record["reference_id"], broker_paper=record["paper"])
    record_execution_event(
        decision_id, "SUBMIT_OR_LOOKUP_UNCERTAIN",
        {"detail": str(detail)[:500], "preserved_state": actual_state},
        event_id=f"etoro:{decision_id}:unknown")
    if _record_is_active(record):
        _notify(record, "uncertain")


@_synchron
def mark_execution_anomaly(decision_id: int, code: str, detail: str = "") -> dict:
    """Persistenter manueller Pruefstopp fuer einen Ausfuehrungs-Risikoverstoss.

    OPEN/CLOSED-/Lookup-Evidenz darf dieses Flag absichtlich nie entfernen.
    Die spaetere explizite Resolution muss ueber einen separaten, auditierten
    Bedienpfad erfolgen.
    """
    data = _load()
    record = data.setdefault("records", {}).get(str(int(decision_id)))
    if not record:
        raise BrokerFehler("Ausfuehrungsanomalie ohne Reconciliation-Datensatz")
    record["manual_review_required"] = True
    record["execution_anomaly_code"] = str(code or "EXECUTION_ANOMALY")[:120]
    record["execution_anomaly_detail"] = str(detail or "")[:500]
    record["execution_anomaly_at_utc"] = (
        str(record.get("execution_anomaly_at_utc") or "") or _now())
    record["updated_at_utc"] = _now()
    _save(data)
    record_execution_event(
        decision_id, "EXECUTION_ANOMALY",
        {"code": record["execution_anomaly_code"],
         "detail": record["execution_anomaly_detail"]},
        event_id=(f"etoro:{decision_id}:execution-anomaly:"
                  f"{record['execution_anomaly_code']}"),
    )
    _mirror_ownership_registry(
        record, reconciliation_note=(
            f"Manuelle Pruefung: {record['execution_anomaly_code']} - "
            f"{record['execution_anomaly_detail']}"))
    return dict(record)


@_synchron
def resolve_execution_anomaly(decision_id: int, *, resolution: str,
                              resolved_by: str) -> dict:
    """Einziger expliziter, auditierter Freigabepfad fuer eine Anomalie."""
    if not str(resolution or "").strip() or not str(resolved_by or "").strip():
        raise ValueError("Anomalie-Resolution benoetigt resolution und resolved_by")
    data = _load()
    record = data.setdefault("records", {}).get(str(int(decision_id)))
    if not record:
        raise BrokerFehler("Anomalie-Resolution ohne Reconciliation-Datensatz")
    if not bool(record.get("manual_review_required")):
        return dict(record)
    record["manual_review_required"] = False
    record["execution_anomaly_resolution"] = str(resolution)[:500]
    record["execution_anomaly_resolved_by"] = str(resolved_by)[:120]
    record["execution_anomaly_resolved_at_utc"] = _now()
    record["updated_at_utc"] = _now()
    _save(data)
    record_execution_event(
        decision_id, "EXECUTION_ANOMALY_RESOLVED",
        {"resolution": record["execution_anomaly_resolution"],
         "resolved_by": record["execution_anomaly_resolved_by"]},
        event_id=f"etoro:{decision_id}:execution-anomaly-resolved",
    )
    _mirror_ownership_registry(
        record, reconciliation_note=(
            f"Anomalie explizit geklaert: {record['execution_anomaly_resolution']}"))
    return dict(record)


def _status(data: dict) -> tuple[str, int]:
    status = data.get("status") or {}
    if not isinstance(status,dict):
        return '', -1
    sid = status.get('id',0)
    if isinstance(sid,bool) or (sid is not None and not isinstance(sid,int)):
        sid = -1
    return str(status.get("name") or "").upper(), sid or 0


def _execution_from_status(name: str, sid: int) -> str:
    from etoro_order_status import lookup_status
    return lookup_status({"name": name, "id": sid})["execution"]



@_synchron
def apply_broker_evidence(decision_id: int, evidence: dict) -> dict:
    """Merged autoritative Order-/Fill-Evidenz, niemals einen Beleg ersetzen."""
    data = _load(); record = data.setdefault("records", {}).get(str(int(decision_id)))
    if not record:
        raise BrokerFehler("Kein lokaler Reconciliation-Datensatz")
    returned_order = str(evidence.get("orderId") or evidence.get("orderID") or
                         evidence.get("OrderId") or evidence.get("OrderID") or "")
    known_orders = {str(x) for x in record.get("order_ids") or []}
    if returned_order and known_orders and returned_order not in known_orders:
        raise BrokerFehler("Brokerantwort gehoert nicht zur exakt gespeicherten Order-ID")
    returned_ref = str(evidence.get("referenceId") or evidence.get("referenceID") or
                       evidence.get("ReferenceId") or "")
    if returned_ref and record.get("reference_id") and returned_ref != record["reference_id"]:
        raise BrokerFehler("Brokerantwort gehoert nicht zur exakt gespeicherten referenceId")
    if returned_order and returned_order not in known_orders:
        # A referenceId lookup can reveal the broker order ID for the first
        # time. It is exact broker evidence, not a symbol/quantity guess.
        record.setdefault("order_ids", []).append(returned_order)
        known_orders.add(returned_order)
    name, sid = _status(evidence)
    executions = evidence.get("positionExecutions") or evidence.get("PositionExecutions") or []
    incoming_fills = []
    for ex in executions:
        if not isinstance(ex, dict):
            continue
        opening = ex.get("openingData") or {}
        q = abs(float(opening.get("units") or ex.get("filledUnits") or 0))
        px = float(opening.get("avgPrice") or 0)
        pid = str(ex.get("positionId") or ex.get("positionID") or
                  ex.get("PositionId") or ex.get("PositionID") or "")
        if q > 0 and px > 0 and pid:
            fill = {
                "position_id": pid, "quantity": q, "price": px,
                "execution_time": opening.get("executionTime"),
                "execution_id": str(opening.get("executionId") or
                                    ex.get("executionId") or ""),
            }
            # v2 opening costs are separate from the legacy history `fees`.
            # Keep explicit zero taxes; absence does not mean zero.
            import hashlib
            cid = str(evidence.get("accountId") or "")
            native_account = hashlib.sha256(
                f"etoro|{'demo' if record.get('paper') else 'live'}|cid:{cid}".encode()).hexdigest()[:24]
            if (str(evidence.get("orderCurrency") or "").upper() == "USD"
                    and str((evidence.get("asset") or {}).get("currency") or "").upper() == "USD"
                    and str(evidence.get("action") or "").lower() == "open"
                    and cid.isdigit() and native_account == record.get("account_fingerprint")
                    and returned_order and str(opening.get("orderId")) == returned_order):
                for key in ("fees", "taxes"):
                    if key in opening:
                        fill[key] = opening[key]
                fill["fee_currency"] = "USD"
                fill["fee_source"] = "ETORO_V2_OPENING_DATA"
            incoming_fills.append(fill)
    fills = _merge_fills(record.get("fills"), incoming_fills)
    exact_filled = sum(_float(x.get("quantity")) for x in fills)
    # Sobald detaillierte positionExecutions vorliegen, ist deren monoton
    # gemergte Summe staerker als ein vorheriges aggregiertes OrderErgebnis.
    filled = exact_filled if exact_filled > 0 else _float(
        record.get("filled_quantity"))
    requested = float(record.get("quantity") or 0)
    remaining = max(0.0, requested - filled)
    record["fills"] = fills; record["filled_quantity"] = filled
    record["remaining_quantity"] = remaining
    record["position_ids"] = _unique_strings(
        list(record.get("position_ids") or []) +
        [x["position_id"] for x in fills])
    from etoro_order_status import lookup_status
    phase = lookup_status({"name": name, "id": sid})
    record["broker_status_code"] = phase["code"]
    record["broker_status_label"] = phase["label"]
    record["order_terminal"] = phase["terminal"] or (phase['known'] and record.get('order_terminal') is True)
    record["status_conflict"] = not phase["known"]
    incoming_execution = _execution_from_status(name, sid)
    execution_state = _merge_execution_state(
        str(record.get("execution_state") or
            record.get("broker_execution_state") or "UNKNOWN"),
        incoming_execution, filled=filled, requested=requested)

    # Eine Orderauskunft mit positionExecutions ist noch kein Beweis, dass
    # die Position im aktuellen Depot existiert. Genau diese Verwechslung
    # erzeugte den MSFT-Phantomtrade: der Lookup meldete einen Fill, der
    # aktuelle Brokerbestand enthielt die positionId aber nicht. Fills bleiben
    # deshalb gesperrt, bis verify_broker_truth() dieselbe positionId im Depot
    # oder in der vollstaendig gelesenen Historie gefunden hat.
    record["submit_state"] = "ACCEPTED"
    record["lookup_uncertain"] = not phase["known"]
    record["broker_execution_state"] = execution_state
    record["execution_state"] = execution_state
    record["order_evidence_strength"] = "BROKER_ORDER_LOOKUP"
    if _execution_has_fill(record) and not record.get("position_confirmation_started_at_utc"):
        record["position_confirmation_started_at_utc"] = _now()
        record["position_absence_checks"] = 0
    _sync_legacy_state(record)
    state = str(record.get("state") or "RECONCILING")
    record["broker_status"] = name or str(sid)
    exact_value = sum(_float(x.get("quantity")) * _float(x.get("price")) for x in fills)
    avg = ((exact_value / exact_filled) if exact_filled > 0 else
           (_float(record.get("avg_fill_price")) or None))
    if avg:
        record["avg_fill_price"] = avg
    record["updated_at_utc"] = _now(); _save(data)
    _mirror_ownership_registry(
        record, reconciliation_note="eToro-Order-/Fill-Evidenz exakt gemerged")
    mark_execution(decision_id, state, record["order_ids"], reference_id=record["reference_id"],
                   position_ids=record["position_ids"], fill_price=avg, fill_qty=filled,
                   broker_paper=record["paper"])
    record_order_state(decision_id, broker="etoro",
                       broker_order_id=str(next(iter(known_orders), record.get("reference_id") or decision_id)),
                       # Orderausfuehrung und Positionsbeweis sind getrennte
                       # Zustaende: CANCELED/REJECTED_PARTIALLY_FILLED darf
                       # nicht durch den uebergeordneten Abgleichstatus
                       # AWAITING_POSITION_CONFIRMATION verdeckt werden.
                       role="ENTRY", status=execution_state, symbol=record["symbol"],
                       client_order_id=record["reference_id"], requested_qty=requested,
                       filled_qty=filled, remaining_qty=remaining, requested_price=record["price"],
                       avg_fill_price=avg, raw=evidence)
    record_execution_event(decision_id, state, record,
                           event_id=f"etoro:{decision_id}:{state}:{filled:g}")
    # Ein Order-Lookup beweist die Ausfuehrung, aber noch keine aktuell
    # offene Position. Die Erfolgsnachricht darf erst nach dem exakten
    # positionId-Abgleich mit Depot oder Trade-Historie entstehen.
    if execution_state in {"REJECTED", "CANCELLED", "EXPIRED"} and not _execution_has_fill(record):
        _notify(record, "failed")
    from execution_lifecycle import observe_etoro_entry
    observe_etoro_entry(record,evidence)
    return dict(record)


@_synchron
def apply_execution_result(decision_id: int, result: OrderErgebnis) -> dict:
    """Merged die sicheren Felder eines erfolgreichen ``OrderErgebnis``.

    Das Ergebnis kennt gegebenenfalls nur eine Gesamtmenge und mehrere
    positionIds. Deshalb wird hier niemals eine erfundene Menge je ID als
    Fill angelegt. Ein spaeterer Order-Lookup bleibt der staerkere Detailbeleg.
    """
    data = _load()
    record = data.setdefault("records", {}).get(str(int(decision_id)))
    if not record:
        raise BrokerFehler("OrderErgebnis ohne Reconciliation-Datensatz")
    for order_id in getattr(result, "order_ids", []) or []:
        if str(order_id) and str(order_id) not in record.setdefault("order_ids", []):
            record["order_ids"].append(str(order_id))
    reference_id = str(getattr(result, "reference_id", "") or "")
    if reference_id:
        existing = str(record.get("reference_id") or "")
        if existing and existing != reference_id:
            raise BrokerFehler("OrderErgebnis besitzt eine abweichende referenceId")
        record["reference_id"] = reference_id
    record["position_ids"] = _unique_strings(
        list(record.get("position_ids") or []) +
        list(getattr(result, "position_ids", []) or []))
    result_filled = max(0.0, _float(getattr(result, "filled_quantity", 0.0)))
    adapter_evidence = str(record.get("order_evidence_strength") or "") == (
        "BROKER_ORDER_LOOKUP")
    if not adapter_evidence:
        record["filled_quantity"] = max(
            _float(record.get("filled_quantity")), result_filled)
    requested = _float(record.get("quantity")) or _float(
        getattr(result, "requested_quantity", 0.0))
    record["remaining_quantity"] = max(
        0.0, requested - _float(record.get("filled_quantity")))
    result_avg = _float(getattr(result, "avg_fill_price", 0.0))
    if (not adapter_evidence and result_avg > 0 and
            not _float(record.get("avg_fill_price"))):
        record["avg_fill_price"] = result_avg
    raw = str(getattr(result, "raw_status", "") or
              getattr(result, "status", "") or "")
    token = "".join(ch for ch in raw.upper() if ch.isalnum())
    if "REJECT" in token and "PARTIAL" in token:
        incoming = "REJECTED_PARTIALLY_FILLED"
    elif "CANCEL" in token and "PARTIAL" in token:
        incoming = "CANCELED_PARTIALLY_FILLED"
    elif "PARTIAL" in token or record["remaining_quantity"] > 1e-10 and result_filled > 0:
        incoming = "PARTIALLY_FILLED"
    elif result_filled > 0:
        incoming = "FILLED"
    elif bool(getattr(result, "terminal", False)) and "REJECT" in token:
        incoming = "REJECTED"
    else:
        incoming = "RECONCILING"
    if adapter_evidence:
        record["execution_state"] = str(
            record.get("execution_state") or
            record.get("broker_execution_state") or "UNKNOWN")
    else:
        record["execution_state"] = _merge_execution_state(
            str(record.get("execution_state") or "UNKNOWN"), incoming,
            filled=_float(record.get("filled_quantity")), requested=requested)
    record["broker_execution_state"] = record["execution_state"]
    record["submit_state"] = "ACCEPTED"
    record["execution_result_evidence"] = {
        "filled_quantity": result_filled,
        "avg_fill_price": result_avg,
        "position_ids": _unique_strings(getattr(result, "position_ids", []) or []),
        "raw_status": raw,
    }
    if _execution_has_fill(record) and not record.get("position_confirmation_started_at_utc"):
        record["position_confirmation_started_at_utc"] = _now()
        record["position_absence_checks"] = 0
    _sync_legacy_state(record)
    record["updated_at_utc"] = _now()
    _save(data)
    _mirror_ownership_registry(
        record, reconciliation_note="Erfolgreiches OrderErgebnis uebernommen")
    return dict(record)


def _mirror_ownership_registry(record: dict, *, reconciliation_note: str = "") -> None:
    """Spiegelt beide Entry-Anker kontogebunden, ohne Exitgrund zu faelschen."""
    anchors = _unique_strings(
        list(record.get("order_ids") or []) +
        [str(record.get("reference_id") or "")])
    if not anchors:
        return
    execution = str(record.get("execution_state") or "UNKNOWN").upper()
    position = str(record.get("position_state") or "NOT_SEEN").upper()
    if execution in {"REJECTED", "CANCELLED", "EXPIRED"} and not _execution_has_fill(record):
        registry_state = "REJECTED" if execution == "REJECTED" else execution
    elif position in {"OPEN_CONFIRMED", "CLOSED_CONFIRMED"}:
        # Der ENTRY-Auftrag ist in beiden Faellen terminal ausgefuehrt.
        registry_state = "FILLED"
    else:
        registry_state = "AWAITING_POSITION_CONFIRMATION"
    environment = "DEMO" if bool(record.get("paper")) else "LIVE"
    try:
        from order_ownership import OrderOwnershipRegistry
        registry = OrderOwnershipRegistry(
            _root() / getattr(config, "BOT_ORDER_REGISTRY_FILE",
                              "bot_order_registry.json"))
        registry.register_orders(
            anchors, str(record.get("symbol") or ""),
            meta={
                "decision_id": int(record.get("decision_id") or 0),
                "zustand": registry_state,
                "reconciliation_status": position,
                "profile": record.get("profile", ""),
                "broker": "etoro", "asset_type": "stock",
                "account_fingerprint": str(record.get("account_fingerprint") or ""),
                "paper": bool(record.get("paper")),
                "environment": environment,
                "qty": _float(record.get("quantity")),
                "filled_qty": _float(record.get("filled_quantity")),
                "position_ids": _unique_strings(record.get("position_ids")),
                "open_position_ids": _unique_strings(
                    record.get("open_position_ids")),
                "closed_position_ids": _unique_strings(
                    record.get("closed_position_ids")),
                "signal_price": record.get("price"),
                "stop": record.get("stop"), "take": record.get("take_profit"),
                "reference_id": record.get("reference_id", ""),
                "entry_reason": str(record.get("entry_reason") or ""),
                "reconciliation_note": str(reconciliation_note or
                                            record.get("last_error") or ""),
            },
            owner="BOT", asset_type="stock",
        )
        registry.setze_zustand(
            str(record.get("symbol") or ""), registry_state,
            asset_type="stock", decision_id=int(record.get("decision_id") or 0),
            account_fingerprint=str(record.get("account_fingerprint") or ""),
            paper=bool(record.get("paper")), environment=environment,
            reference_id=str(record.get("reference_id") or ""),
            position_ids=_unique_strings(record.get("position_ids")),
            open_position_ids=_unique_strings(record.get("open_position_ids")),
            closed_position_ids=_unique_strings(
                record.get("closed_position_ids")),
            reconciliation_status=position,
            reconciliation_note=str(reconciliation_note or ""))
    except Exception:
        logger.exception("Exakte eToro-Order-Ownership konnte nicht persistiert werden")


# Zustaende, aus denen NIE eine Position entstanden ist. Alles andere kann
# im Depot liegen und muss dem Bot zugeordnet werden koennen.
OHNE_POSITION = {"REJECTED", "CANCELLED", "EXPIRED", "FAILED",
                 "FAILED_BEFORE_SUBMIT", "CLOSED_BEFORE_IMPORT"}


@_synchron
def eigene_kaeufe(max_alter_stunden: float = 48.0) -> list[dict]:
    """Alle eigenen Kaeufe, aus denen eine Depotposition entstanden sein kann.

    KORREKTUR 31.08.2026 (SPGI): Die Vorgaengerfassung lieferte nur Saetze in
    einem nicht-terminalen Zustand. Sobald ``verify_broker_truth`` die
    positionId bestaetigt hatte, verschwand der Kauf aus dieser Liste. War der
    Depotbestand in genau diesem Moment schon als Fremdbestand angelegt, gab
    es danach nichts mehr, was ihn haette reparieren koennen -- der Bot hatte
    die Aktie gekauft und fuehrte sie dauerhaft als fremd.

    Zurueckgegeben wird deshalb JEDER Kauf der letzten Stunden, aus dem eine
    Position entstanden sein kann. Ob daraus Eigentum wird, entscheidet
    ausschliesslich der positionId-Abgleich beim Aufrufer.
    """
    data = _load()
    if data.get("storage_error"):
        return []
    # KORREKTUR 9.5.5: ``max_alter_stunden`` stand in der Signatur, wurde im
    # Rumpf aber NIE benutzt -- der Docstring versprach "jeder Kauf der letzten
    # Stunden", der Code lieferte jeden Kauf ueberhaupt.
    #
    # Folge: der symbolbasierte Rueckfall im Positionsmanager hatte gar kein
    # Zeitfenster. Ein Monate alter, nie geklaerter AAPL-Kauf konnte damit
    # einen heutigen manuellen AAPL-Kauf als eigenen beanspruchen. Verkauft
    # haette der Bot ihn nicht (dafuer fehlt die bewiesene positionId), aber
    # Depotbild und Kapitalreservierung waren falsch.
    grenze = max(0.0, float(max_alter_stunden or 0.0))
    jetzt = datetime.now(timezone.utc)
    out: list[dict] = []
    for record in (data.get("records") or {}).values():
        zustand = str(record.get("state") or "").upper()
        verified_open = bool(
            record.get("position_verified") and
            str(record.get("position_state") or
                record.get("broker_position_status") or "").upper() ==
            "OPEN_CONFIRMED" and record.get("verified_position_ids"))
        # Offenes, exakt bewiesenes Eigentum hat kein Ablaufdatum.
        if grenze > 0 and not verified_open:
            roh = str(record.get("created_at_utc") or "")
            try:
                erstellt = datetime.fromisoformat(roh.replace("Z", "+00:00"))
                if erstellt.tzinfo is None:
                    erstellt = erstellt.replace(tzinfo=timezone.utc)
                if (jetzt - erstellt).total_seconds() > grenze * 3600.0:
                    continue
            except (TypeError, ValueError):
                # Ohne lesbaren Zeitstempel wird NICHT ausgeschlossen -- eine
                # fehlende Angabe darf keinen echten eigenen Kauf verschlucken.
                pass
        active = not _ist_terminal(record)
        # Geschlossene/abgelehnte/fehlgeschlagene Altorders sind niemals
        # Symbol-PENDING-Kandidaten. Sonst koennte ein alter ADBE-Kauf spaeter
        # eine neue manuelle ADBE-Position beanspruchen.
        if not active and not verified_open:
            continue
        if not (record.get("order_ids") or record.get("reference_id")):
            continue
        candidate_ids = (record.get("verified_position_ids")
                         if verified_open else record.get("position_ids"))
        out.append({
            "decision_id": int(record.get("decision_id") or 0),
            "symbol": str(record.get("symbol") or "").upper(),
            "position_ids": [str(x) for x in (candidate_ids or [])],
            "verified_position_ids": [str(x) for x in
                                      (record.get("verified_position_ids") or [])],
            "order_ids": [str(x) for x in (record.get("order_ids") or [])],
            "reference_id": str(record.get("reference_id") or ""),
            "stop": float(record.get("stop") or 0.0),
            "take_profit": float(record.get("take_profit") or 0.0),
            "state": zustand,
            "bestaetigt": verified_open,
            "offen": active,
            "paper": bool(record.get("paper")),
            "account_fingerprint": str(record.get("account_fingerprint") or ""),
            # Gebundenes Kapital: was noch nicht als Fill belegt ist, bleibt
            # reserviert. Ein Teilfill gibt nur den nicht verbrauchten Teil
            # frei -- nie den ganzen Betrag.
            "reserved_cash": max(0.0, (
                float(record.get("quantity") or 0.0)
                - float(record.get("filled_quantity") or 0.0))
                * float(record.get("price") or 0.0)),
        })
    return out


@_synchron
def offene_kaufabsichten() -> list[dict]:
    """Nur die Kaeufe, die noch Kapital binden (fuer die Cash-Reservierung).

    Bewusst enger als ``eigene_kaeufe()``: gebunden ist Kapital nur, solange
    der Ausgang offen ist oder eine Ausfuehrung noch nicht im Depot belegt
    wurde. Ein terminaler Satz -- auch ein UNPROVABLE -- gibt das Geld frei;
    sonst koennte ein einzelner ungeklaerter Fall den Handel tagelang
    lahmlegen, ohne dass jemand sieht warum. Gegen den gefaehrlichen Fall
    schuetzt weiterhin ``assert_domain_available()``.
    """
    return [x for x in eigene_kaeufe(max_alter_stunden=48.0)
            if x.get("offen") or (str(x.get("state") or "").upper() == "FILLED"
                                  and not x.get("bestaetigt"))]


def record_for(decision_id: int) -> dict:
    """Aktueller persistierter Datensatz einer Kaufentscheidung."""
    return dict(((_load().get("records") or {}).get(str(int(decision_id))) or {}))


def reconcile_one(broker, decision_id: int, *, attempts: int = 4,
                  delays=(0.0, 0.8, 1.6, 3.2)) -> dict:
    """Rate-limit friendly GET recovery; orderId/referenceId are never combined."""
    record = (_load().get("records") or {}).get(str(int(decision_id)))
    if not record:
        raise BrokerFehler("Kein Reconciliation-Datensatz")
    last_error = ""
    anchors = [("order_id", x) for x in record.get("order_ids") or []]
    if record.get("reference_id"):
        anchors.append(("reference_id", record["reference_id"]))
    for attempt in range(max(1, int(attempts))):
        if attempt < len(delays) and float(delays[attempt]) > 0:
            time.sleep(float(delays[attempt]))
        for kind, value in anchors:
            try:
                evidence = broker._lookup_order(**{kind: value})
                if evidence:
                    return apply_broker_evidence(decision_id, evidence)
            except (BrokerFehler, VerbindungVerloren) as exc:
                last_error = str(exc)[:500]
    mark_unknown(decision_id, order_ids=record.get("order_ids"),
                 reference_id=record.get("reference_id", ""), detail=last_error or "nicht gefunden")
    return dict(((_load().get("records") or {}).get(str(int(decision_id))) or {}))


def recover_all(broker, *, paper: bool, profile: str) -> list[dict]:
    fingerprint = str(broker.account_fingerprint()
                      if callable(getattr(broker, "account_fingerprint", None)) else "")
    domain = domain_key(paper=paper, profile=profile,
                        account_fingerprint=fingerprint)
    try:
        recovered = _retry_closed_ledger_backfills(domain)
    except Exception as exc:
        logger.warning("eToro-Ledger-Recovery wartet weiter: %s", exc)
        recovered = []
    for record in active_for_domain(domain):
        decision_id = int(record.get("decision_id") or 0)
        if decision_id <= 0:
            # Fail closed without crashing reconnect/startup on corrupt storage.
            recovered.append(dict(record))
            continue
        recovered.append(reconcile_one(broker, decision_id))
    # Auch bereits terminal als FILLED gespeicherte Altfaelle pruefen. Genau
    # diese Pruefung fehlte in 9.0.2 und konnte nach einem Neustart einen
    # Phantomtrade erzeugen.
    return recovered + verify_broker_truth(
        broker, paper=paper, profile=profile,
        account_fingerprint=fingerprint)


def background_tick(broker, *, paper: bool, profile: str, nachlauf=()) -> list[dict]:
    """Ein gepaceter Hintergrundschritt ohne POST und ohne Schlafschleife.

    Pro Datensatz wird jeder exakte Anker hoechstens einmal gelesen. Ein
    voruebergehendes 404 terminalisiert nichts; der naechste Tick versucht es
    erneut. Sobald Order-/Fillbelege vorliegen, folgt derselbe exakte
    positionId-Abgleich wie beim Start- und Reconnect-Pfad.

    10.8.0: ``nachlauf`` sind die Schritte nach der Brokerwahrheit (Storno-
    abgleich, Gebuehrennachlauf; siehe ``etoro_nachlauf.SCHRITTE``). Der
    Aufrufer uebergibt sie -- dieses Modul importiert sie nicht mehr selbst,
    weil beide Schritte umgekehrt dieses Modul importieren (Import-Zyklus).
    """
    fingerprint = str(broker.account_fingerprint()
                      if callable(getattr(broker, "account_fingerprint", None)) else "")
    domain = domain_key(paper=paper, profile=profile,
                        account_fingerprint=fingerprint)
    changed: list[dict] = []
    lookup_changed = False
    position_snapshot = None
    # Ein bereits sicher CLOSED bestaetigter Brokertrade darf die Domain
    # freigeben, sein exakter Ledger-Link muss aber nach einem DB-/Prozessfehler
    # weiter idempotent nachgezogen werden.
    try:
        changed.extend(_retry_closed_ledger_backfills(domain))
    except Exception as exc:
        logger.warning("eToro-Ledger-Backfill wartet weiter: %s", exc)
    # 9.5.3: Einmal je Prozess doppelt gefuehrte Brokerpositionen
    # zusammenfuehren. Idempotent; ein zweiter Lauf findet nichts mehr.
    _dubletten_einmalig_zusammenfuehren()
    # Truth first: ADBE war 13 Sekunden nach dem Fill bereits im Depot, aber
    # ein spaeter Lookupfehler setzte den Satz zuvor wieder auf UNKNOWN. Der
    # aktuelle Depot-/Historienbeweis muss deshalb vor jedem Orderlookup
    # verarbeitet werden.
    try:
        position_snapshot = _authoritative_position_snapshot(broker)
        changed.extend(verify_broker_truth(
            broker, paper=paper, profile=profile,
            account_fingerprint=fingerprint,
            position_snapshot=position_snapshot))
    except Exception as exc:
        logger.warning("eToro Truth-first-Abgleich wartet weiter: %s", exc)

    # 9.5.2: Der Worker arbeitet auf dem KLAERUNGSbereich, nicht auf dem
    # Sperrbereich. Ein Satz aus einer aelteren Fingerprint-Generation muss
    # bearbeitet werden koennen -- sonst klaert er sich nie (ADBE stand
    # deshalb 17 Stunden unveraendert).
    for record in klaerungskandidaten(domain):
        decision_id = int(record.get("decision_id") or 0)
        if decision_id <= 0:
            continue
        anchors = [("order_id", x) for x in record.get("order_ids") or []]
        if record.get("reference_id"):
            anchors.append(("reference_id", record["reference_id"]))
        for kind, value in anchors:
            try:
                evidence = broker._lookup_order(**{kind: value})
                if evidence:
                    # Der Lookup lief gegen das AKTUELLE Konto. Findet er
                    # diese exakte Order, ist damit brokerseitig belegt, dass
                    # der Satz zu diesem Konto gehoert -- unabhaengig davon,
                    # welchen Fingerprint eine frueherere Version vergeben hat.
                    _uebernehme_altdomaene(record, fingerprint, paper=paper,
                                           profile=profile,
                                           beleg=f"{kind}={value}")
                    changed.append(apply_broker_evidence(decision_id, evidence))
                    lookup_changed = True
                    break
            except Exception as exc:
                # Ein fehlerhafter/alter Datensatz darf weder andere Records
                # noch die nachfolgende Brokerwahrheit blockieren.
                logger.info(
                    "eToro-Reconciliation %s via %s=%s wartet weiter: %s",
                    decision_id, kind, value, exc)
                continue
    # Ein Lookup kann erstmals positionIds geliefert haben. Derselbe bereits
    # gelesene Snapshot wird danach noch einmal dagegen ausgewertet; keine
    # zweite, zeitlich widerspruechliche PnL-Abfrage.
    if lookup_changed:
        try:
            changed.extend(verify_broker_truth(
                broker, paper=paper, profile=profile,
                account_fingerprint=fingerprint,
                position_snapshot=position_snapshot))
        except Exception as exc:
            logger.warning("eToro Abschluss-Wahrheitsabgleich wartet weiter: %s", exc)
    try:
        resolve_accounting_gaps(domain)
    except Exception as exc:
        logger.warning("eToro-Diagnoseabgleich wartet auf dauerhafte Speicherung: %s", exc)
    for schritt in nachlauf:
        # Jeder Schritt faengt seine Fehler selbst (etoro_nachlauf); ein
        # unerwarteter Fehler eines Schritts darf den Tick trotzdem nicht reissen.
        try:
            changed.extend(schritt(broker, paper=paper, profile=profile,
                                   position_snapshot=position_snapshot) or [])
        except Exception as exc:
            logger.warning("eToro-Nachlaufschritt %s wartet weiter: %s",
                           getattr(schritt, "__name__", "?"), type(exc).__name__)
    return changed


_DUBLETTEN_GEPRUEFT = False


def _dubletten_einmalig_zusammenfuehren() -> None:
    """Doppelt gefuehrte Brokerpositionen einmal je Prozess bereinigen.

    Anlass: ADBE stand am 02.09.2026 mit -344,76 USD zweimal im Ledger, weil
    9.5.1 das Kontofingerprint-Verfahren wechselte und dieselbe Position
    danach unter zwei Fingerprints lag. Es wird nichts geloescht -- die
    Altzeile bleibt als Auditspur und wird nur aus den Auswertungen genommen.
    """
    global _DUBLETTEN_GEPRUEFT
    if _DUBLETTEN_GEPRUEFT:
        return
    _DUBLETTEN_GEPRUEFT = True
    try:
        import trade_ledger
        bericht = trade_ledger.fuehre_dubletten_zusammen(
            "etoro", probelauf=False, actor="reconciliation-worker")
    except Exception:
        logger.warning("Dublettenpruefung im Ledger fehlgeschlagen", exc_info=True)
        return
    if bericht.get("zusammengefuehrt"):
        logger.warning("Ledger: %s doppelt gefuehrte Position(en) zusammengefuehrt",
                       len(bericht["zusammengefuehrt"]))
    for uebersprungen in bericht.get("uebersprungen") or []:
        logger.warning("Ledger-Dublette NICHT zusammengefuehrt: %s", uebersprungen)


def _uebernehme_altdomaene(record: dict, fingerprint: str, *, paper: bool,
                           profile: str, beleg: str) -> bool:
    """Einen Satz aus einer Vorgaenger-Fingerprintgeneration uebernehmen.

    Bedingung ist ein brokerseitiger Beleg (bestandener Order-Lookup gegen
    das aktuelle Konto). Es wird ausschliesslich die Kontozuordnung
    geschrieben -- keine Menge, kein Preis, keine Buchung. Der alte
    Fingerprint bleibt als ``account_fingerprint_vorher`` erhalten, damit der
    Vorgang nachvollziehbar bleibt und wiederholbar ist.
    """
    if not fingerprint:
        return False
    alt_fp = str(record.get("account_fingerprint") or "")
    if alt_fp == fingerprint:
        return False
    ziel_domain = domain_key(paper=paper, profile=profile,
                             account_fingerprint=fingerprint)
    if alt_fp:
        konto_alias_registrieren(alt_fp, fingerprint, beleg=beleg)
        # 9.5.3: Denselben Beleg auch im Ledger hinterlegen. Sonst sucht das
        # Ledger weiter nur unter dem neuen Fingerprint und legt fuer dieselbe
        # Brokerposition eine zweite Zeile an -- so entstand die
        # ADBE-Doppelbuchung ueber -344,76 USD.
        try:
            import trade_ledger
            trade_ledger.register_account_alias(
                broker="etoro", alias_fingerprint=alt_fp,
                account_fingerprint=fingerprint, beleg=beleg)
        except Exception:
            logger.warning("Kontoalias nicht ins Ledger uebernommen", exc_info=True)

    def _schreibe(satz, _fp=fingerprint, _domain=ziel_domain, _alt=alt_fp,
                  _beleg=beleg):
        if str(satz.get("account_fingerprint") or "") == _fp:
            return False
        if _alt:
            satz["account_fingerprint_vorher"] = _alt
        satz["account_fingerprint"] = _fp
        satz["domain"] = _domain
        satz["kontomigration"] = {
            "von": _alt or "(ohne Kontobindung)", "nach": _fp,
            "beleg": str(_beleg)[:200], "at_utc": _now(),
            "schema": KONTO_ALIAS_SCHEMA}

    ergebnis = _mutiere(int(record.get("decision_id") or 0), _schreibe)
    if ergebnis:
        logger.warning("eToro-Datensatz %s auf aktuelles Konto uebernommen "
                       "(%s -> %s, Beleg %s)", record.get("symbol"),
                       alt_fp or "ohne", fingerprint, beleg)
    return bool(ergebnis)


@_synchron
def recovered_buy_fills(*, paper: bool,
                        current_position_ids: set[str] | None = None,
                        account_fingerprint: str = "") -> list[Fill]:
    """Expose exact persisted BUY executions after restart.

    Fill IDs intentionally match the adapter's immediate queue. The global
    fill tracker therefore makes repeated polls and restart recovery exactly
    once without deleting reconciliation evidence prematurely.
    """
    data = _load()
    if data.get("storage_error"):
        return []
    # Fail closed: ohne aktuellen, erfolgreich gelesenen Depot-Snapshot wird
    # kein persistierter BUY erneut in den Geldpfad eingespielt.
    # ``None`` bleibt ausschliesslich fuer direkte Diagnose-/Altaufrufe
    # kompatibel. Der produktive Adapter uebergibt immer einen expliziten
    # Snapshot (auch eine leere Menge), sodass dort ohne Brokerbeweis weiter
    # strikt keine Wiederherstellung erfolgt.
    if current_position_ids is None:
        current = {
            str(pid)
            for record in (data.get("records") or {}).values()
            for pid in (record.get("position_ids") or [])
            if str(pid)
        }
        legacy_verified = True
    else:
        current = {str(x) for x in current_position_ids if str(x)}
        legacy_verified = False
    if not current:
        return []
    out: list[Fill] = []
    for record in (data.get("records") or {}).values():
        if bool(record.get("paper")) != bool(paper):
            continue
        if (account_fingerprint and
                str(record.get("account_fingerprint") or "") !=
                str(account_fingerprint)):
            continue
        if not legacy_verified and not bool(record.get("position_verified")):
            continue
        if (not legacy_verified and
                str(record.get("broker_position_status") or "") != "OPEN_CONFIRMED"):
            continue
        order_id = str(next(iter(record.get("order_ids") or []), "") or
                       record.get("reference_id") or "")
        if not order_id:
            continue
        for item in record.get("fills") or []:
            quantity = float(item.get("quantity") or 0)
            price = float(item.get("price") or 0)
            position_id = str(item.get("position_id") or "")
            if quantity <= 0 or price <= 0 or not position_id or position_id not in current:
                continue
            timestamp = item.get("execution_time")
            out.append(Fill(
                fill_id=(f"etoro:{account_fingerprint or 'unresolved'}:open:"
                         f"{order_id}:{position_id}:{timestamp}"),
                order_id=order_id, symbol=str(record.get("symbol") or "").upper(),
                side="BUY", quantity=quantity, price=price, currency="USD",
                asset_type="stock", broker_id=position_id, timestamp=timestamp,
                raw_fill_id=str(item.get("execution_id") or ""),
                account_fingerprint=str(account_fingerprint or
                                        record.get("account_fingerprint") or ""),
            ))
    return out


def _created_day(record: dict) -> str:
    raw = str(record.get("created_at_utc") or "")
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).date().isoformat()
    except (TypeError, ValueError):
        return datetime.now(timezone.utc).date().isoformat()


def _confirmation_age_seconds(record: dict) -> float:
    """Alter des ersten exakten Fill-Belegs, ohne die Frist zu verlaengern."""
    raw = str(record.get("position_confirmation_started_at_utc") or
              record.get("updated_at_utc") or record.get("created_at_utc") or "")
    try:
        stamp = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - stamp).total_seconds())
    except (TypeError, ValueError):
        return 0.0


# ---------------------------------------------------------------------------
# Schema-v3 broker truth. Dies ist die einzige oeffentliche Implementierung.
# ---------------------------------------------------------------------------
_POSITION_ID_KEYS = ("positionId", "positionID", "PositionId", "PositionID")
_ORDER_ID_KEYS = (
    "orderId", "orderID", "OrderId", "OrderID", "openOrderId",
    "openOrderID", "openingOrderId", "openingOrderID", "parentOrderId",
)


def _row_value(row, keys) -> str:
    if isinstance(row, dict):
        for key in keys:
            if row.get(key) is not None and str(row.get(key)):
                return str(row.get(key))
    else:
        for key in keys:
            value = getattr(row, key, None)
            if value is not None and str(value):
                return str(value)
    return ""


def _row_position_ids(row) -> set[str]:
    out: set[str] = set()
    direct = _row_value(row, _POSITION_ID_KEYS)
    if direct:
        out.add(direct)
    values = (row.get("position_ids") if isinstance(row, dict)
              else getattr(row, "position_ids", ()))
    out.update(str(x) for x in (values or []) if str(x))
    broker_id = (row.get("broker_id") if isinstance(row, dict)
                 else getattr(row, "broker_id", ""))
    if broker_id and not out:
        out.add(str(broker_id))
    return out


def _row_order_ids(row) -> set[str]:
    out: set[str] = set()
    direct = _row_value(row, _ORDER_ID_KEYS)
    if direct:
        out.add(direct)
    if isinstance(row, dict):
        for nested_key in ("openingData", "order", "openOrder", "execution"):
            nested = row.get(nested_key)
            if isinstance(nested, dict):
                value = _row_value(nested, _ORDER_ID_KEYS)
                if value:
                    out.add(value)
        out.update(str(x) for x in (row.get("order_ids") or []) if str(x))
    return out


def _fill_from_truth_row(row) -> dict:
    """Sicherer Entry-Fill aus einer ueber exakte orderId verbundenen Zeile."""
    if not isinstance(row, dict):
        return {}
    pids = _row_position_ids(row)
    if len(pids) != 1:
        return {}
    opening = row.get("openingData") or {}
    quantity = abs(_float(
        opening.get("units") or row.get("units") or row.get("filledUnits")))
    price = _float(
        opening.get("avgPrice") or row.get("openRate") or
        row.get("openPrice") or row.get("avgOpenPrice"))
    if quantity <= 0 or price <= 0:
        return {}
    return {
        "position_id": next(iter(pids)),
        "quantity": quantity,
        "price": price,
        "execution_time": (opening.get("executionTime") or
                           opening.get("openTime") or
                           row.get("openTimestamp") or row.get("openTime")),
        "execution_id": str(opening.get("executionId") or
                            row.get("openingExecutionId") or ""),
    }


def _snapshot_rows(snapshot) -> list:
    """Akzeptiert raw PnL, v3-Snapshot, Position-Objekte oder eine ID-Menge."""
    if snapshot is None:
        return []
    rows: list = []
    if isinstance(snapshot, dict):
        cp = snapshot.get("clientPortfolio") or {}
        if isinstance(cp, dict):
            rows.extend(x for x in (cp.get("positions") or []) if x is not None)
            for mirror in cp.get("mirrors") or []:
                if isinstance(mirror, dict):
                    rows.extend(x for x in (mirror.get("positions") or [])
                                if x is not None)
        for key in ("rows", "positions", "normalised_rows", "normalized_rows"):
            value = snapshot.get(key)
            if isinstance(value, (list, tuple, set)):
                rows.extend(value)
        for pid in (snapshot.get("position_ids") or
                    snapshot.get("current_position_ids") or
                    snapshot.get("open_ids") or []):
            rows.append({"positionId": str(pid)})
        if _row_position_ids(snapshot):
            rows.append(snapshot)
    elif isinstance(snapshot, (list, tuple, set)):
        for value in snapshot:
            rows.append({"positionId": str(value)} if isinstance(
                value, (str, int)) else value)
    else:
        rows.append(snapshot)
    unique = []
    seen = set()
    for row in rows:
        key = (tuple(sorted(_row_position_ids(row))),
               tuple(sorted(_row_order_ids(row))))
        marker = key if any(key) else ("object", id(row))
        if marker in seen:
            continue
        seen.add(marker)
        unique.append(row)
    return unique


def _authoritative_position_snapshot(broker):
    """Liest genau einen frischen Brokerstand, bevorzugt die rohe PnL-Sicht."""
    provider = getattr(broker, "position_snapshot", None)
    if callable(provider):
        try:
            return provider(force=True)
        except TypeError:
            return provider()
    pnl = getattr(broker, "_pnl", None)
    if callable(pnl):
        return pnl(force=True)
    return {
        "position_ids": list(broker.current_position_ids(force=True)),
        "complete": True,
        "environment": ("DEMO" if bool(getattr(broker, "paper", True))
                        else "LIVE"),
        "account_fingerprint": str(
            broker.account_fingerprint()
            if callable(getattr(broker, "account_fingerprint", None)) else ""),
    }


def _snapshot_complete_for_domain(snapshot, *, paper: bool,
                                  account_fingerprint: str) -> bool:
    """Nur ein vollständiger Snapshot derselben Domain beweist Abwesenheit.

    Positive OPEN-Zeilen bleiben auch aus einem unvollständigen Snapshot
    nutzbar. Ein leerer, fremder oder schematisch unbestätigter Snapshot darf
    dagegen niemals eine Position terminalisieren.
    """
    if not isinstance(snapshot, dict):
        return False
    if snapshot.get("complete") is True:
        complete = True
    else:
        portfolio = snapshot.get("clientPortfolio")
        mirrors = (portfolio.get("mirrors", [])
                   if isinstance(portfolio, dict) else None)
        complete = bool(
            isinstance(portfolio, dict)
            and isinstance(portfolio.get("positions"), list)
            and isinstance(mirrors, list)
            and all(
                isinstance(mirror, dict)
                and ("positions" not in mirror
                     or isinstance(mirror.get("positions"), list))
                for mirror in (mirrors or [])))
    if not complete:
        return False
    expected_account = str(account_fingerprint or "").strip()
    actual_account = str(snapshot.get("account_fingerprint") or "").strip()
    # Ein ``complete=True`` ohne Kontobindung ist nur eine Behauptung ueber
    # irgendein Depot. Es darf weder Abwesenheit beweisen noch einen kontolosen
    # Altdatensatz an das gerade aktive Konto binden.
    if not expected_account or not actual_account or actual_account != expected_account:
        return False
    expected_environment = "DEMO" if bool(paper) else "LIVE"
    actual_environment = str(snapshot.get("environment") or "").upper()
    if not actual_environment or actual_environment != expected_environment:
        return False
    return True


def _snapshot_matches_domain(snapshot, *, paper: bool,
                             account_fingerprint: str) -> bool:
    """Auch positive Positionsbelege muessen demselben Konto gehoeren.

    ``complete`` ist fuer einen positiven OPEN-Treffer nicht erforderlich. Die
    Konto- und Umgebungsidentitaet dagegen schon: Ein Snapshot des anderen
    eToro-Depots darf niemals Ownership oder eine Legacy-Kontobindung erzeugen.
    """
    if not isinstance(snapshot, dict):
        return False
    expected_account = str(account_fingerprint or "").strip()
    actual_account = str(snapshot.get("account_fingerprint") or "").strip()
    expected_environment = "DEMO" if bool(paper) else "LIVE"
    actual_environment = str(snapshot.get("environment") or "").upper()
    return bool(expected_account and actual_account == expected_account
                and actual_environment == expected_environment)


def _history_position_id(row: dict) -> str:
    return _row_value(row, _POSITION_ID_KEYS)


def _explicit_close_order_id(detail: dict) -> str:
    """Liest nur Felder, die den *Close*-Auftrag eindeutig bezeichnen.

    eToro-Historienzeilen tragen in ``orderId`` regelmaessig die Entry-Order.
    Dieses Feld darf deshalb niemals still als Exit-Anker ins Ledger wandern.
    """
    return _row_value(detail, (
        "closeOrderId", "closeOrderID", "CloseOrderId", "CloseOrderID",
        "closingOrderId", "closingOrderID", "exitOrderId", "exitOrderID",
    ))


def _explicit_close_fill_id(detail: dict) -> str:
    return _row_value(detail, (
        "closeExecutionId", "closeExecutionID", "closingExecutionId",
        "closingExecutionID", "closeFillId", "closeFillID",
        "exitFillId", "exitFillID", "fillId", "FillId", "FillID",
    ))


def _history_close_bundle(rows: list[dict]) -> dict:
    """Bewahrt jede History-Teil-Schliessung einer ``positionId``.

    Die History kann mehrere Zeilen fuer dieselbe Position liefern. Ein simples
    ``{positionId: row}`` verlor bislang alle bis auf eine und konnte danach den
    Rest des Ledgertrades ohne Ergebnis schliessen. Das Bundle enthaelt deshalb
    stabile, einzeln buchbare Close-Fills sowie nur zusaetzlich aggregierte
    Anzeige-/Vergleichswerte.
    """
    raw_rows = [dict(row) for row in (rows or []) if isinstance(row, dict)]
    if not raw_rows:
        return {}
    pids = {_history_position_id(row) for row in raw_rows
            if _history_position_id(row)}
    if len(pids) != 1:
        return {}
    pid = next(iter(pids))
    time_counts: dict[str, int] = {}
    seen_time_rows: set[tuple] = set()
    for row in raw_rows:
        stamp = str(row.get("closeTimestamp") or row.get("closeTime") or
                    row.get("executionTime") or row.get("closed_at_utc") or "")
        signature = (stamp, abs(_float(row.get("closedUnits") or row.get("quantity") or row.get("units"))),
                     _float(row.get("closeRate") or row.get("closePrice") or row.get("avgPrice") or row.get("price")),
                     _explicit_close_fill_id(row), _explicit_close_order_id(row))
        if signature in seen_time_rows:
            continue
        seen_time_rows.add(signature)
        time_counts[stamp] = time_counts.get(stamp, 0) + 1

    events: list[dict] = []
    seen: set[tuple] = set()
    for row in raw_rows:
        stamp = str(row.get("closeTimestamp") or row.get("closeTime") or
                    row.get("executionTime") or row.get("closed_at_utc") or "")
        quantity = abs(_float(
            row.get("closedUnits") or row.get("quantity") or row.get("units")))
        price = _float(row.get("closeRate") or row.get("closePrice") or
                       row.get("avgPrice") or row.get("price"))
        explicit_fill = _explicit_close_fill_id(row)
        identity = (explicit_fill, stamp, quantity, price,
                    _explicit_close_order_id(row))
        if identity in seen:
            continue
        seen.add(identity)
        if explicit_fill:
            fill_id = explicit_fill
        elif stamp and time_counts.get(stamp, 0) == 1:
            # Kompatibel zum bisherigen stabilen ADBE-Migrationsanker.
            fill_id = f"etoro-close:history:{pid}:{stamp}"
        else:
            fill_id = (f"etoro-close:history:{pid}:{stamp or 'unknown'}:"
                       f"{quantity:.12g}:{price:.12g}")
        event = dict(row)
        event.update({
            "positionId": pid,
            "closedUnits": quantity,
            "closeRate": price,
            "closed_at_utc": stamp,
            "closeFillId": fill_id,
        })
        events.append(event)
    events.sort(key=lambda item: (
        str(item.get("closed_at_utc") or ""),
        str(item.get("closeFillId") or "")))
    if not events:
        return {}

    # Die juengste Zeile liefert nicht-aggregierbare Metadaten (z. B. Grund).
    bundle = dict(events[-1])
    total_qty = sum(abs(_float(item.get("closedUnits"))) for item in events)
    priced_qty = sum(abs(_float(item.get("closedUnits"))) for item in events
                     if _float(item.get("closeRate")) > 0)
    weighted_value = sum(
        abs(_float(item.get("closedUnits"))) * _float(item.get("closeRate"))
        for item in events if _float(item.get("closeRate")) > 0)
    if total_qty > 0:
        bundle["closedUnits"] = total_qty
        bundle["quantity"] = total_qty
    if priced_qty > 0:
        bundle["closeRate"] = weighted_value / priced_qty
        bundle["closePrice"] = weighted_value / priced_qty
    close_orders = _unique_strings(
        _explicit_close_order_id(item) for item in events)
    if len(close_orders) == 1:
        bundle["closeOrderId"] = close_orders[0]
    elif close_orders:
        bundle.pop("closeOrderId", None)
    bundle["_close_fills"] = events
    bundle["_history_rows_complete_for_position"] = True
    return bundle


def _close_events_from_detail(detail: dict, position_id: str) -> list[dict]:
    raw_events = detail.get("_close_fills") if isinstance(detail, dict) else None
    if not isinstance(raw_events, list) or not raw_events:
        raw_events = [detail]
    bundle = _history_close_bundle([
        {**dict(item), "positionId": str(position_id or
                                          _history_position_id(item))}
        for item in raw_events if isinstance(item, dict)
    ])
    return [dict(item) for item in (bundle.get("_close_fills") or [])]


def _derived_close_reason(record: dict, detail: dict, close_price: float) -> str:
    """Leitet einen Exitgrund nur aus Brokertext oder Preisnaehe ab."""
    explicit = str(detail.get("executionReason") or
                   detail.get("execution_reason") or
                   detail.get("closeReason") or "").strip()
    if explicit:
        return explicit
    if close_price > 0:
        levels = (
            ("STOP_LOSS", _float(record.get("stop") or
                                  record.get("stop_loss"))),
            ("TAKE_PROFIT", _float(record.get("take_profit") or
                                    record.get("take") or record.get("tp"))),
        )
        candidates = []
        for name, level in levels:
            if level <= 0:
                continue
            distance = abs(close_price - level)
            # Brokerfills koennen wenige Ticks/slippage vom geplanten Niveau
            # abweichen. 0,25 % ist eng genug, um keine normale Schliessung
            # willkuerlich als SL/TP umzudeuten.
            if distance <= max(0.01, abs(level) * 0.0025):
                candidates.append((distance, name))
        if candidates:
            return min(candidates)[1]
    return "BROKER_CLOSED_CONFIRMED"


def verify_broker_truth(broker, *, paper: bool, profile: str,
                        account_fingerprint: str = "",
                        current_position_ids: set[str] | None = None,
                        position_snapshot=None) -> list[dict]:
    """Klaert jede exakte positionId monoton als OPEN oder CLOSED.

    ``position_snapshot`` ist optional und kann die rohe PnL-Antwort, einen
    normalisierten Snapshot oder Position-Objekte enthalten. Wird er
    uebergeben, erfolgt keine zweite Depotabfrage innerhalb dieser Wahrheit.
    """
    data = _load()
    if data.get("storage_error"):
        return []
    fingerprint = str(account_fingerprint or "")
    domain = domain_key(paper=paper, profile=profile,
                        account_fingerprint=fingerprint)
    legacy_domain = domain_key(paper=paper, profile=profile)
    domain_records: list[dict] = []
    for raw in (data.get("records") or {}).values():
        exact_domain = (
            (str(raw.get("domain")) == domain and
             (not fingerprint or
              str(raw.get("account_fingerprint") or "") == fingerprint)))
        legacy_unbound = bool(
            fingerprint and str(raw.get("domain")) == legacy_domain
            and not str(raw.get("account_fingerprint") or ""))
        if not (exact_domain or legacy_unbound):
            continue
        if (_record_is_active(raw) or raw.get("position_ids") or
                raw.get("order_ids") or raw.get("reference_id")):
            domain_records.append(dict(raw))
    if not domain_records:
        return []

    try:
        snapshot = (position_snapshot if position_snapshot is not None else
                    _authoritative_position_snapshot(broker))
        open_rows = _snapshot_rows(snapshot)
        current_ids = {
            pid for row in open_rows for pid in _row_position_ids(row) if pid}
        if current_position_ids is not None:
            current_ids.update(str(x) for x in current_position_ids if str(x))
    except Exception as exc:
        logger.warning(
            "eToro-Depotbeweis nicht lesbar; Reconciliation bleibt gesperrt: %s", exc)
        return []

    if not _snapshot_matches_domain(
            snapshot, paper=paper, account_fingerprint=fingerprint):
        logger.error(
            "eToro-Positionssnapshot nicht konto-/umgebungsgebunden; "
            "Reconciliation bleibt unveraendert gesperrt")
        return []

    snapshot_complete = _snapshot_complete_for_domain(
        snapshot, paper=paper, account_fingerprint=fingerprint)
    # Terminale Datensätze werden normalerweise nicht mehr angefasst. Eine
    # aktuell wieder OPEN gemeldete identische positionId ist jedoch der
    # Reparaturbeweis für die historische Teilclose-Fehlklassifikation und
    # muss in diesem Zyklus wieder auf OPEN gehoben werden.
    candidates = [
        record for record in domain_records
        if (str(record.get("position_state") or "") != "CLOSED_CONFIRMED"
            or bool(_known_position_ids(record) & current_ids))
    ]
    if not candidates:
        return []

    earliest = min(_created_day(record) for record in candidates)
    try:
        history_provider = getattr(broker, "trade_history_snapshot", None)
        if callable(history_provider):
            try:
                history_snapshot = history_provider(earliest, force=True)
            except TypeError:
                history_snapshot = history_provider(earliest)
            history_rows = [dict(x) for x in (
                (history_snapshot or {}).get("rows") or []) if isinstance(x, dict)]
            history_complete = bool((history_snapshot or {}).get("complete"))
        else:
            history_rows = [dict(x) for x in broker.trade_history(earliest)
                            if isinstance(x, dict)]
            # Legacy-API hatte keine Vollstaendigkeitsmetadaten. Positive
            # Treffer sind nutzbar; fuer negative Freigaben wird sie nicht
            # mehr verwendet.
            history_complete = True
    except Exception as exc:
        logger.warning("eToro-Historienbeweis nicht lesbar: %s", exc)
        history_rows = []
        history_complete = False
    closed_rows_by_id: dict[str, list[dict]] = {}
    for row in history_rows:
        history_pid = _history_position_id(row)
        if history_pid:
            closed_rows_by_id.setdefault(history_pid, []).append(dict(row))
    closed_by_id = {
        pid: _history_close_bundle(rows)
        for pid, rows in closed_rows_by_id.items()
    }

    changed: list[dict] = []
    for candidate in candidates:
        did = int(candidate.get("decision_id") or 0)
        if did <= 0:
            continue
        try:
            latest = record_for(did) or candidate
            if (str(latest.get("position_state") or "") == "CLOSED_CONFIRMED"
                    and not (_known_position_ids(latest) & current_ids)):
                continue
            known_orders = set(_unique_strings(latest.get("order_ids")))
            pids = _known_position_ids(latest)
            matched_open_rows = [
                row for row in open_rows if known_orders & _row_order_ids(row)]
            matched_closed_rows = [
                row for row in history_rows if known_orders & _row_order_ids(row)]
            discovered_open = {
                pid for row in matched_open_rows for pid in _row_position_ids(row)}
            discovered_closed = {
                pid for row in matched_closed_rows for pid in _row_position_ids(row)}
            recovered_fills = [
                fill for fill in
                (_fill_from_truth_row(row)
                 for row in matched_open_rows + matched_closed_rows)
                if fill]
            pids |= discovered_open | discovered_closed
            legacy_unbound = bool(
                fingerprint and str(latest.get("domain") or "") == legacy_domain
                and not str(latest.get("account_fingerprint") or ""))
            exact_account_evidence = bool(
                (pids & current_ids) or
                (pids & set(closed_by_id)) or
                discovered_open or discovered_closed)
            if legacy_unbound and not exact_account_evidence:
                # Ein kontoloser Altsatz blockiert weiterhin fail-closed, wird
                # aber erst dann an dieses Konto gebunden, wenn positionId oder
                # Entry-orderId im aktuellen Konto exakt wiedergefunden wurde.
                # Damit kann ein Durchlauf auf dem falschen Konto ihn nicht
                # vereinnahmen; auf dem richtigen Konto heilt er automatisch.
                continue
            # Eine History-Zeile kann auch nur einen Teilverkauf derselben
            # weiterhin offenen positionId beschreiben. Der frische P&L-
            # OPEN-Beweis gewinnt immer. CLOSED entsteht nur aus positiver
            # History-Evidenz PLUS nachgewiesener Abwesenheit im vollständigen
            # Konto-/Umgebungs-Snapshot.
            open_now = pids & current_ids
            history_closed_candidates = (
                (pids & set(closed_by_id)) | set(discovered_closed))
            closed_now = ((history_closed_candidates - current_ids)
                          if snapshot_complete else set())
            discovered_open &= current_ids
            before = (
                str(latest.get("state") or ""),
                tuple(_unique_strings(latest.get("position_ids"))),
                tuple(_unique_strings(latest.get("open_position_ids"))),
                tuple(_unique_strings(latest.get("closed_position_ids"))),
                tuple(_unique_strings(latest.get("unresolved_position_ids"))),
                str(latest.get("position_state") or ""),
                int(latest.get("position_absence_checks") or 0),
            )
            outcome = {}

            def _apply_truth(record, _out=outcome):
                if recovered_fills:
                    record["fills"] = _merge_fills(
                        record.get("fills"), recovered_fills)
                    exact_filled = sum(_float(item.get("quantity"))
                                       for item in record["fills"])
                    record["filled_quantity"] = max(
                        _float(record.get("filled_quantity")), exact_filled)
                    requested = _float(record.get("quantity"))
                    record["remaining_quantity"] = max(
                        0.0, requested - _float(record.get("filled_quantity")))
                    exact_value = sum(
                        _float(item.get("quantity")) * _float(item.get("price"))
                        for item in record["fills"])
                    if exact_filled > 0:
                        record["avg_fill_price"] = exact_value / exact_filled
                record["position_ids"] = _unique_strings(
                    list(record.get("position_ids") or []) + list(pids) +
                    [item.get("position_id") for item in recovered_fills])
                if discovered_open or discovered_closed:
                    requested = _float(record.get("quantity"))
                    reconstructed = _float(record.get("filled_quantity"))
                    current_execution = str(
                        record.get("execution_state") or "UNKNOWN").upper()
                    incoming_execution = (
                        "FILLED" if current_execution not in {
                            "PARTIALLY_FILLED", "REJECTED_PARTIALLY_FILLED"}
                        and requested > 0 and reconstructed >= requested - 1e-10
                        else "PARTIALLY_FILLED")
                    record["execution_state"] = _merge_execution_state(
                        current_execution, incoming_execution,
                        filled=reconstructed, requested=requested)
                    record["broker_execution_state"] = record["execution_state"]
                    record["submit_state"] = "ACCEPTED"
                    record["recovered_via_order_id"] = sorted(known_orders)
                old_closed = set(_unique_strings(record.get("closed_position_ids")))
                old_open = (set(_unique_strings(record.get("open_position_ids"))) |
                            set(_unique_strings(record.get("verified_position_ids"))))
                stored_close_evidence = (
                    record.get("close_evidence_by_position_id") or {})
                strong_closed = {
                    closed_pid for closed_pid in old_closed
                    if bool((stored_close_evidence.get(closed_pid) or {}).get(
                        "_terminal_close_confirmed"))
                }
                # OPEN im aktuellen Brokerbestand repariert auch eine ältere,
                # ausschließlich aus History abgeleitete CLOSED-Markierung.
                all_closed = (
                    ((old_closed | set(closed_now)) - current_ids) |
                    strong_closed)
                all_open = (old_open | set(open_now) |
                            set(discovered_open)) - all_closed
                record["closed_position_ids"] = sorted(all_closed)
                record["open_position_ids"] = sorted(all_open)
                close_evidence = record.setdefault(
                    "close_evidence_by_position_id", {})
                for closed_pid in set(closed_now):
                    incoming_detail = closed_by_id.get(closed_pid) or {}
                    if not isinstance(incoming_detail, dict):
                        continue
                    stored_detail = close_evidence.setdefault(closed_pid, {})
                    # Ein expliziter Close-Hook kann reichhaltiger sein als
                    # die Historienzeile. Historie fuellt deshalb nur Luecken.
                    for key, value in incoming_detail.items():
                        if key not in stored_detail and value not in (None, ""):
                            stored_detail[key] = value
                # Eine vom P&L widerlegte History-Terminalisierung darf weder
                # ihren Close-Beleg noch einen alten Pending-Backfill behalten.
                # Wurde allerdings schon ein Ledger-Close committed, bleibt
                # die Position aus Sicherheitsgründen in manueller Prüfung:
                # der Brokerbestand ist OPEN, das lokale Geldbuch CLOSED.
                backfills = record.setdefault("ledger_close_backfill", {})
                for open_pid in (old_closed - strong_closed) & current_ids:
                    result = backfills.get(open_pid) or {}
                    if str(result.get("status") or "").upper() in _LEDGER_CLOSE_SUCCESS:
                        record["manual_review_required"] = True
                        record["last_error"] = (
                            "Brokerposition wieder OPEN, obwohl der lokale "
                            f"Ledger-Close bereits committed ist: {open_pid}")
                    else:
                        close_evidence.pop(open_pid, None)
                        backfills.pop(open_pid, None)
                for closed_pid in all_closed:
                    result = backfills.get(closed_pid) or {}
                    if str(result.get("status") or "").upper() not in _LEDGER_CLOSE_SUCCESS:
                        backfills.setdefault(closed_pid, {
                            "status": "LEDGER_BACKFILL_PENDING",
                            "position_id": closed_pid,
                        })
                if isinstance(snapshot, dict):
                    record["last_truth_snapshot_id"] = str(
                        snapshot.get("snapshot_id") or
                        snapshot.get("_snapshot_id") or "")
                known_now = _known_position_ids(record)
                unresolved = known_now - all_open - all_closed
                if unresolved:
                    checks = int(record.get("position_absence_checks") or 0) + 1
                    record["position_absence_checks"] = checks
                    record["last_position_absence_check_at_utc"] = _now()
                    age = _confirmation_age_seconds(record)
                    grace = max(0.0, float(getattr(
                        config, "ETORO_POSITION_CONFIRMATION_GRACE_SECONDS", 300.0)))
                    min_checks = max(1, int(getattr(
                        config, "ETORO_POSITION_CONFIRMATION_MIN_ABSENT_SNAPSHOTS", 3)))
                    if history_complete and age >= grace and checks >= min_checks:
                        record["position_state"] = "MISSING_REVIEW"
                        record["last_error"] = (
                            "Fill bewiesen; positionIds weiterhin weder OPEN noch CLOSED: "
                            + ",".join(sorted(unresolved)))
                _sync_legacy_state(record)
                record["position_verified_at_utc"] = _now()
                if fingerprint:
                    record["account_fingerprint"] = fingerprint
                    record["domain"] = domain
                _out["open"] = list(record.get("open_position_ids") or [])
                _out["closed"] = list(record.get("closed_position_ids") or [])
                _out["unresolved"] = list(record.get("unresolved_position_ids") or [])

            record = _mutiere(did, _apply_truth)
            if not record:
                continue
            after = (
                str(record.get("state") or ""),
                tuple(_unique_strings(record.get("position_ids"))),
                tuple(_unique_strings(record.get("open_position_ids"))),
                tuple(_unique_strings(record.get("closed_position_ids"))),
                tuple(_unique_strings(record.get("unresolved_position_ids"))),
                str(record.get("position_state") or ""),
                int(record.get("position_absence_checks") or 0),
            )
            if before == after:
                continue
            open_ids = outcome.get("open", [])
            closed_ids = outcome.get("closed", [])
            unresolved_ids = outcome.get("unresolved", [])
            # Auch bei einem Multi-Execution-Kauf kann eine positionId bereits
            # CLOSED sein, während eine andere noch OPEN bleibt. Jeder Close
            # wird deshalb unabhängig vom Gesamtzustand ins Ledger gespiegelt.
            ledger_results = {}
            for position_id in closed_ids:
                previous_result = dict(
                    (record.get("ledger_close_backfill") or {}).get(
                        position_id) or {})
                if str(previous_result.get("status") or "").upper() in LEDGER_CLOSE_ENDGUELTIG:
                    ledger_results[position_id] = previous_result
                else:
                    ledger_results[position_id] = _backfill_closed_ledger(
                        record, position_id,
                        closed_by_id.get(position_id) or {})
            if ledger_results:
                def _remember_backfill(current, _results=ledger_results):
                    current.setdefault("ledger_close_backfill", {}).update(
                        {str(key): dict(value)
                         for key, value in _results.items()})
                    current["ledger_close_backfill_updated_at_utc"] = _now()
                    _sync_legacy_state(current)
                    return True

                record = _mutiere(did, _remember_backfill) or record
            if not unresolved_ids and open_ids:
                execution_state = str(record.get("execution_state") or "FILLED")
                mark_execution(
                    did, execution_state, record.get("order_ids") or [],
                    reference_id=record.get("reference_id", ""),
                    position_ids=open_ids,
                    fill_price=(_float(record.get("avg_fill_price")) or None),
                    fill_qty=_float(record.get("filled_quantity")),
                    broker_paper=record.get("paper"),
                )
                record_execution_event(
                    did, "POSITION_OPEN_CONFIRMED",
                    {"position_ids": open_ids,
                     "closed_position_ids": closed_ids},
                    event_id=f"etoro:{did}:position-open:{','.join(open_ids)}")
                try:
                    import trade_ledger
                    trade_ledger.mark_decision_reconciliation(
                        did, "CONFIRMED_OPEN",
                        notiz="Alle eToro-positionIds als OPEN/CLOSED bestaetigt")
                except Exception:
                    logger.debug(
                        "eToro-Positionsbeweis nicht ins Ledger gespiegelt",
                        exc_info=True)
                if not bool(latest.get("position_verified")):
                    _notify(record, "confirmed")
                _mirror_ownership_registry(
                    record,
                    reconciliation_note="Alle positionIds als OPEN/CLOSED geklaert")
            elif not unresolved_ids and closed_ids and not open_ids:
                if not _closed_accounting_pending(record):
                    mark_execution(
                        did, "CLOSED_BEFORE_IMPORT", record.get("order_ids") or [],
                        reference_id=record.get("reference_id", ""),
                        position_ids=closed_ids, broker_paper=record.get("paper"))
                    record_execution_event(
                        did, "POSITION_CLOSED_CONFIRMED",
                        {"position_ids": closed_ids},
                        event_id=(f"etoro:{did}:position-closed:"
                                  f"{','.join(closed_ids)}"))
                    try:
                        import trade_ledger
                        trade_ledger.mark_decision_reconciliation(
                            did, "CLOSED", close_without_result=True,
                            notiz=("eToro-Historie bestaetigt alle positionIds "
                                   "als geschlossen und lokal verbucht"))
                    except Exception:
                        logger.debug(
                            "eToro-Schliessungsbeweis nicht ins Ledger gespiegelt",
                            exc_info=True)
                    _mirror_ownership_registry(
                        record,
                        reconciliation_note=(
                            "Alle positionIds CLOSED und im Ledger bestaetigt"))
                else:
                    record_execution_event(
                        did, "POSITION_CLOSED_ACCOUNTING_PENDING",
                        {"position_ids": closed_ids,
                         "ledger_results": ledger_results},
                        event_id=(f"etoro:{did}:position-closed-accounting-"
                                  f"pending:{','.join(closed_ids)}"))
            changed.append(dict(record))
        except Exception:
            # Ein defekter Altsatz darf den naechsten Kandidaten nicht stoppen.
            logger.exception("eToro-Wahrheitsabgleich fuer decision_id=%s fehlgeschlagen", did)
    return changed


def _backfill_closed_ledger(record: dict, position_id: str,
                            close_detail: dict | None = None) -> dict:
    """Verbindet Entry und Close ausschliesslich ueber account+positionId.

    Bereits als ``unmatched-position:*`` verbrauchte SELL-Ereignisse werden
    damit unter ihrer echten Position erneut idempotent verbucht. Fehlen
    belastbare Preise, wird CLOSED ohne erfundenes Ergebnis gespiegelt.
    """
    detail = dict(close_detail or {})
    pid = str(position_id or "")
    entries = [dict(item) for item in (record.get("fills") or [])
               if str(item.get("position_id") or "") == pid]
    entry_qty = sum(abs(_float(item.get("quantity"))) for item in entries)
    entry_value = sum(abs(_float(item.get("quantity"))) *
                      _float(item.get("price")) for item in entries)
    entry_price = entry_value / entry_qty if entry_qty > 0 else 0.0
    entry_times = [str(item.get("execution_time") or "") for item in entries
                   if str(item.get("execution_time") or "")]
    entry_time = (min(entry_times) if entry_times else
                  detail.get("openTimestamp") or detail.get("openTime"))
    # Nur bei genau einer Position ist die sichere OrderErgebnis-Gesamtmenge
    # zugleich die Menge dieser Position. Bei mehreren IDs wird nichts verteilt.
    if not entry_qty and len(_known_position_ids(record)) == 1:
        entry_qty = _float(record.get("filled_quantity"))
        entry_price = _float(record.get("avg_fill_price"))

    order_ids = _unique_strings(record.get("order_ids"))
    close_events = _close_events_from_detail(detail, pid)
    history_entry_orders = {
        value for event in close_events
        for value in _row_order_ids(event) if value}
    if history_entry_orders - set(order_ids):
        return {
            "status": "LEDGER_BACKFILL_PENDING", "position_id": pid,
            "error": "Historien-orderId passt zu keiner gespeicherten Entry-Order",
        }
    if len(history_entry_orders) == 1:
        order_id = next(iter(history_entry_orders))
    elif not history_entry_orders and len(order_ids) == 1:
        order_id = order_ids[0]
    else:
        order_id = ""

    entry_fill_rows = []
    entry_fill_ids = []
    for item in entries:
        item_time = str(item.get("execution_time") or "")
        fill_id = str(item.get("execution_id") or (
            f"etoro-entry:{order_id}:{pid}:{item_time}" if item_time else
            f"etoro-entry:{order_id}:{pid}"))
        if fill_id in entry_fill_ids:
            continue
        entry_fill_ids.append(fill_id)
        entry_fill_rows.append({
            "fill_id": fill_id, "ordId": order_id,
            "quantity": abs(_float(item.get("quantity"))),
            "price": _float(item.get("price")), "filled_at": item_time,
            "fee": _explicit_fee(item),
            "fee_currency": str(item.get("fee_currency") or item.get("currency") or ""),
            "fee_source": item.get("fee_source", ""),
        })
    if not entry_fill_ids:
        fallback_entry_id = (
            f"etoro-entry:{order_id}:{pid}:{entry_time}" if entry_time else
            f"etoro-entry:{order_id}:{pid}")
        entry_fill_ids = [fallback_entry_id]
        entry_fill_rows = [{
            "fill_id": fallback_entry_id, "ordId": order_id,
            "quantity": entry_qty, "price": entry_price,
            "filled_at": entry_time,
        }]
    entry_fill_id = entry_fill_ids[0]

    close_qty = sum(abs(_float(item.get("closedUnits")))
                    for item in close_events)
    if entry_qty > 0 and close_qty > entry_qty + max(1e-8, entry_qty * 1e-7):
        return {
            "status": "LEDGER_BACKFILL_PENDING", "position_id": pid,
            "error": ("Historien-Teilverkaeufe uebersteigen die exakt belegte "
                      f"Entry-Menge ({close_qty:g} > {entry_qty:g})"),
        }
    final_event = dict(close_events[-1]) if close_events else dict(detail)
    close_order_id = _explicit_close_order_id(final_event)
    close_fill_id = _explicit_close_fill_id(final_event)
    close_time = (final_event.get("closed_at_utc") or
                  final_event.get("closeTimestamp") or
                  final_event.get("closeTime") or
                  final_event.get("executionTime"))
    close_price = _float(final_event.get("closeRate") or
                         final_event.get("closePrice") or
                         final_event.get("avgPrice") or
                         final_event.get("price"))
    account = str(record.get("account_fingerprint") or "")
    decision_id = int(record.get("decision_id") or 0)
    # KORREKTUR 9.5.2: Ein Altbestand aus der Zeit vor der Kontobindung hat
    # positionId, orderId, decision_id, Menge und Preis -- nur den
    # Kontofingerprint hatte er nie. In 9.5.1 scheiterte der Backfill
    # deshalb an ALLEN 14 Altsaetzen, in JEDEM Zyklus, und schrieb die Datei
    # jedes Mal neu. Die Fehlermeldung nannte dabei fuenf moegliche Ursachen,
    # obwohl nur eine zutraf.
    #
    # Nachtraeglich einem Konto zuschreiben duerfen wir sie nicht -- das waere
    # eine Eigentumsbehauptung ohne Beweis. Also wird der Versuch einmal
    # sauber als endgueltig markiert und danach in Ruhe gelassen.
    if (not account and bool(record.get("legacy_unbound_closed"))
            and pid and order_id and decision_id > 0
            and entry_qty > 0 and entry_price > 0):
        return {"status": "LEDGER_BACKFILL_LEGACY_UNBOUND", "position_id": pid,
                "error": ("Altbestand ohne Kontobindung: bleibt als Auditspur "
                          "erhalten und wird nicht nachtraeglich einem Konto "
                          "zugeschrieben")}
    if (not pid or not order_id or not account or decision_id <= 0 or
            entry_qty <= 0 or entry_price <= 0):
        # Kein decision-/symbolweiter Fallback: eine Entscheidung kann mehrere
        # positionIds besitzen, von denen andere noch OPEN sind.
        fehlt = [name for name, wert in (
            ("positionId", pid), ("orderId", order_id), ("Konto", account),
            ("decision_id", decision_id > 0), ("Menge", entry_qty > 0),
            ("Entry-Preis", entry_price > 0)) if not wert]
        return {"status": "LEDGER_BACKFILL_PENDING", "position_id": pid,
                "fehlende_felder": fehlt,
                "error": (f"Fehlt fuer exakten Backfill: {', '.join(fehlt)}"
                          if fehlt else
                          "Konto/Decision/Entry-Preis/Menge/Order fuer exakten "
                          "Backfill unvollstaendig")}

    try:
        import trade_ledger
        symbol = str(record.get("symbol") or "")
        paper = bool(record.get("paper"))
        client_order_id = str(record.get("reference_id") or "")
        exit_reason = _derived_close_reason(record, final_event, close_price)

        def _exact_link(note: str):
            return trade_ledger.reconcile_closed_trade_exact(
                broker="etoro", symbol=symbol, paper=paper,
                decision_id=decision_id, broker_position_id=pid,
                broker_account_fingerprint=account,
                entry_order_id=order_id, entry_fill_id=entry_fill_id,
                entry_fill=entry_fill_rows[0],
                client_order_id=client_order_id,
                close_order_id=close_order_id, close_fill_id=close_fill_id,
                exit_reason=exit_reason, expected_entry_price=entry_price,
                expected_close_price=close_price or None,
                expected_quantity=entry_qty, notiz=note)

        exact = _exact_link(
            "eToro-Close ueber exakte Reconciliation-Kette verknuepft")
        exact_status = str((exact or {}).get("status") or "")
        if exact_status == "LINKED_CLOSED":
            _reconcile_fees(record, pid, order_id, entry_fill_rows, close_events)
            return dict(exact)
        if exact_status not in {"NOT_FOUND", "NOT_CLOSED"}:
            return {
                "status": "LEDGER_BACKFILL_PENDING", "position_id": pid,
                "exact_link_result": dict(exact or {}),
                "error": f"Exakter Ledger-Link nicht sicher: {exact_status or 'EMPTY'}",
            }

        if exact_status == "NOT_FOUND":
            trade_id = trade_ledger.trade_open(
                broker="etoro", symbol=symbol, menge=entry_qty,
                einstieg_preis=entry_price, asset_type="stock", waehrung="USD",
                decision_id=decision_id,
                referenzpreis=_float(record.get("price")) or None,
                paper=paper, zeit=entry_time, broker_position_id=pid,
                entry_order_id=order_id, entry_fill_id=entry_fill_id,
                entry_fill_ids=entry_fill_ids, entry_fills=entry_fill_rows,
                gebuehr=(sum(r["fee"] for r in entry_fill_rows)
                         if all(r.get("fee") is not None for r in entry_fill_rows) else None),
                client_order_id=client_order_id,
                ownership_status="BOT_VERIFIED",
                broker_account_fingerprint=account,
                reconciliation_status="CONFIRMED_OPEN",
                notiz="Aus exakter eToro-Reconciliation nachgetragen",
                critical=True)
            if not trade_id:
                return {"status": "LEDGER_BACKFILL_PENDING", "position_id": pid,
                        "error": "Exakter Ledger-Entry konnte nicht angelegt werden"}

        # Jede History-Zeile wird mit ihrem eigenen stabilen Fillanker gebucht.
        # Ein Replay bereits verarbeiteter Teilfills liefert idempotent deren
        # alte trade_id zurueck; der naechste neue Fill schliesst den exakten
        # noch offenen Rest derselben Konto+positionId+Entry-orderId-Lineage.
        for event in close_events:
            event_qty = abs(_float(event.get("closedUnits")))
            event_price = _float(event.get("closeRate") or
                                 event.get("closePrice") or
                                 event.get("avgPrice") or event.get("price"))
            event_time = (event.get("closed_at_utc") or
                          event.get("closeTimestamp") or event.get("closeTime") or
                          event.get("executionTime"))
            event_fill_id = _explicit_close_fill_id(event)
            event_order_id = _explicit_close_order_id(event)
            if event_qty <= 0 or event_price <= 0 or not event_fill_id:
                continue
            closed_trade = trade_ledger.trade_close(
                broker="etoro", symbol=symbol, ausstieg_preis=event_price,
                menge=event_qty,
                gebuehr=_explicit_fee(event),
                exit_grund=_derived_close_reason(record, event, event_price),
                asset_type="stock", waehrung="USD", paper=paper,
                zeit=event_time, exit_order_id=event_order_id,
                exit_fill_ids=[event_fill_id], event_id=event_fill_id,
                broker_position_id=pid,
                broker_account_fingerprint=account,
                entry_order_id=order_id,
                notiz=("eToro-History-Teilclose positionsgenau "
                       "nachgetragen"), critical=True)
            if not closed_trade:
                return {
                    "status": "LEDGER_BACKFILL_PENDING", "position_id": pid,
                    "error": f"History-Teilclose {event_fill_id} nicht verbucht",
                }

        retry = _exact_link(
            "Alle eToro-History-Teilcloses exakt und idempotent verknuepft")
        retry_status = str((retry or {}).get("status") or "")
        if retry_status == "LINKED_CLOSED":
            _reconcile_fees(record, pid, order_id, entry_fill_rows, close_events)
            return dict(retry)
        if retry_status == "NOT_CLOSED":
            # Der vollständige P&L-Snapshot beweist den Broker-Close, aber die
            # Historie enthielt nicht fuer jede Restmenge einen Preis. Die exakt
            # verbleibende Ledgerzeile wird daher ohne erfundenes Ergebnis
            # geschlossen und bleibt als solche sichtbar.
            remaining_trade_id = int((retry or {}).get("trade_id") or 0)
            if remaining_trade_id > 0 and trade_ledger.mark_reconciled_closed(
                    remaining_trade_id, grund="BROKER_CLOSED_RESULT_MISSING",
                    zeit=close_time,
                    notiz=("eToro-Position geschlossen; fuer die Restmenge fehlt "
                           "ein belastbarer History-Preis")):
                final = _exact_link(
                    "eToro-Close vollstaendig; Rest ohne erfundenes Ergebnis")
                if str((final or {}).get("status") or "") == "LINKED_CLOSED":
                    return {
                        "status": "CLOSED_WITHOUT_RESULT", "position_id": pid,
                        "trade_id": remaining_trade_id,
                        "exact_link_result": dict(final),
                    }
        return {
            "status": "LEDGER_BACKFILL_PENDING", "position_id": pid,
            "exact_link_result": dict(retry or {}),
            "error": "Geschlossene Position nicht vollstaendig im Ledger committed",
        }
    except Exception as exc:
        logger.warning("Exaktes eToro-Ledger-Backfill fuer %s fehlgeschlagen: %s", pid, exc)
        return {"status": "LEDGER_BACKFILL_PENDING", "position_id": pid,
                "error": str(exc)[:500]}


def _retry_closed_ledger_backfills(domain: str) -> list[dict]:
    """Wiederholt nur positionsgenaue CLOSED-Ledgerlinks ohne Brokerheuristik."""
    data = _load()
    requested = str(domain)
    legacy = ":".join(requested.split(":")[:2])
    candidates = []
    for record in (data.get("records") or {}).values():
        record_domain = str(record.get("domain") or "")
        if not (record_domain == requested or
                (record_domain == legacy and
                 not str(record.get("account_fingerprint") or ""))):
            continue
        if not _unique_strings(record.get("closed_position_ids")):
            continue
        candidates.append(dict(record))

    changed = []
    success = {"LINKED_CLOSED", "CLOSED", "CLOSED_WITHOUT_RESULT"}
    for snapshot in candidates:
        did = int(snapshot.get("decision_id") or 0)
        if did <= 0:
            continue
        for pid in _unique_strings(snapshot.get("closed_position_ids")):
            previous = dict((snapshot.get("ledger_close_backfill") or {}).get(pid) or {})
            detail = dict((snapshot.get("close_evidence_by_position_id") or {}).get(pid) or {})
            # A completed quantity link is not a completed fee receipt. Retry
            # exact accounting only when the persisted economic evidence changes.
            import hashlib
            fee_evidence_hash = hashlib.sha256(json.dumps(
                {"fills": snapshot.get("fills", []), "close": detail},
                sort_keys=True, default=str).encode("utf-8")).hexdigest()
            if (str(previous.get("status") or "") in success
                    and previous.get("fee_evidence_hash") == fee_evidence_hash):
                continue
            result = _backfill_closed_ledger(snapshot, pid, detail)
            result["fee_evidence_hash"] = fee_evidence_hash

            def _remember(record, _pid=pid, _result=result):
                record.setdefault("ledger_close_backfill", {})[_pid] = dict(_result)
                record["ledger_close_backfill_updated_at_utc"] = _now()
                _sync_legacy_state(record)
                return True

            persisted = _mutiere(did, _remember)
            if persisted:
                changed.append(persisted)
                snapshot = persisted
                if (str(result.get("status") or "").upper() in success
                        and not _closed_accounting_pending(persisted)):
                    closed_ids = _unique_strings(
                        persisted.get("closed_position_ids"))
                    mark_execution(
                        did, "CLOSED_BEFORE_IMPORT",
                        persisted.get("order_ids") or [],
                        reference_id=persisted.get("reference_id", ""),
                        position_ids=closed_ids,
                        broker_paper=persisted.get("paper"))
                    record_execution_event(
                        did, "POSITION_CLOSED_CONFIRMED",
                        {"position_ids": closed_ids},
                        event_id=(f"etoro:{did}:position-closed:"
                                  f"{','.join(closed_ids)}"))
                    _mirror_ownership_registry(
                        persisted,
                        reconciliation_note=(
                            "Alle positionIds CLOSED und im Ledger bestaetigt"))
    return changed


@_synchron
def confirm_position_closed(position_id: str, *, account_fingerprint: str = "",
                            paper: bool | None = None, environment: str = "",
                            decision_id: int | None = None,
                            close_order_id: str = "", fill_id: str = "",
                            price=None, quantity=None, closed_at_utc: str = "",
                            close_detail: dict | None = None,
                            evidence: dict | None = None) -> list[dict]:
    """Terminalisiert einen exakten Broker-Close idempotent.

    Ohne Konto/Umgebung wird nur bei genau einem global eindeutigen Treffer
    geschrieben. Symbol und Mengennaehe sind niemals Zuordnungsmerkmale.
    """
    pid = str(position_id or "")
    if not pid:
        return []
    detail = dict(close_detail or evidence or {})
    # Diese API darf nur der orchestrierte Close-Consumer nach erfolgreichem
    # Ledger-Commit und vollständigem P&L-Abwesenheitsbeweis aufrufen. Der
    # Marker unterscheidet diesen terminalen Beleg von einer bloßen
    # Trade-History-Zeile, die auch ein Teilverkauf sein kann.
    detail["_terminal_close_confirmed"] = True
    if close_order_id:
        detail.setdefault("closeOrderId", str(close_order_id))
    if fill_id:
        detail.setdefault("fillId", str(fill_id))
    if price not in (None, ""):
        detail.setdefault("closePrice", price)
    if quantity not in (None, ""):
        detail.setdefault("quantity", quantity)
    if closed_at_utc:
        detail.setdefault("closed_at_utc", closed_at_utc)
    if paper is None and environment:
        paper = str(environment).upper() == "DEMO"
    data = _load()
    matches: list[dict] = []
    for record in (data.get("records") or {}).values():
        if decision_id is not None and int(record.get("decision_id") or 0) != int(decision_id):
            continue
        if pid not in _known_position_ids(record):
            continue
        if paper is not None and bool(record.get("paper")) != bool(paper):
            continue
        if account_fingerprint and str(record.get("account_fingerprint") or "") != str(
                account_fingerprint):
            continue
        matches.append(record)
    if (not account_fingerprint and decision_id is None
            and len(matches) != 1):
        logger.error(
            "eToro-Close %s nicht zugeordnet: ohne Domain nicht global eindeutig", pid)
        return []
    mutated: list[tuple[dict, bool]] = []
    for record in matches:
        already_closed = pid in set(_unique_strings(record.get("closed_position_ids")))
        closed = set(_unique_strings(record.get("closed_position_ids")))
        closed.add(pid)
        opened = set(_unique_strings(record.get("open_position_ids")))
        opened.discard(pid)
        record["closed_position_ids"] = sorted(closed)
        record["open_position_ids"] = sorted(opened)
        record.setdefault("close_evidence_by_position_id", {})[pid] = detail
        previous_backfill = dict(
            (record.get("ledger_close_backfill") or {}).get(pid) or {})
        if str(previous_backfill.get("status") or "").upper() not in _LEDGER_CLOSE_SUCCESS:
            record.setdefault("ledger_close_backfill", {})[pid] = {
                "status": "LEDGER_BACKFILL_PENDING", "position_id": pid}
        if close_order_id:
            record["close_order_ids"] = _unique_strings(
                list(record.get("close_order_ids") or []) + [close_order_id])
        _sync_legacy_state(record)
        record["position_verified_at_utc"] = _now()
        record["updated_at_utc"] = _now()
        mutated.append((record, already_closed))
    if mutated:
        # Broker-CLOSED und ACCOUNTING-PENDING werden zuerst gemeinsam
        # persistiert. Die Domain bleibt dabei gesperrt; ein Absturz darf weder
        # den Brokerbeleg vergessen noch vor dem Ledger-Commit freigeben.
        _save(data)

    for record, _already_closed in mutated:
        ledger_result = _backfill_closed_ledger(record, pid, detail)
        record.setdefault("ledger_close_backfill", {})[pid] = ledger_result
        record["ledger_close_backfill_updated_at_utc"] = _now()
        _sync_legacy_state(record)
        did = int(record.get("decision_id") or 0)
        # Event-ID ist deterministisch; Wiederholung nach einem Absturz ist
        # absichtlich idempotent und schliesst die Audit-Luecke.
        record_execution_event(
            did, "POSITION_CLOSED_CONFIRMED",
            {"position_ids": [pid], "close_detail": detail},
            event_id=f"etoro:{did}:position-closed:{pid}")
        unresolved = _unique_strings(record.get("unresolved_position_ids"))
        open_ids = _unique_strings(record.get("open_position_ids"))
        closed_ids = _unique_strings(record.get("closed_position_ids"))
        accounting_complete = not _closed_accounting_pending(record)
        if (str(record.get("position_state") or "") == "CLOSED_CONFIRMED"
                and not unresolved and accounting_complete):
            mark_execution(
                did, "CLOSED_BEFORE_IMPORT", record.get("order_ids") or [],
                reference_id=record.get("reference_id", ""),
                position_ids=closed_ids,
                fill_price=(_float(record.get("avg_fill_price")) or None),
                fill_qty=_float(record.get("filled_quantity")),
                broker_paper=record.get("paper"))
        elif not unresolved and open_ids:
            # Bei mehreren positionIds kann eine bereits CLOSED und eine
            # weiterhin OPEN sein. Der Entry-Status bleibt dann der belegte
            # Ausfuehrungsstatus; nur die exakten offenen IDs werden gespiegelt.
            mark_execution(
                did, str(record.get("execution_state") or "FILLED"),
                record.get("order_ids") or [],
                reference_id=record.get("reference_id", ""),
                position_ids=open_ids,
                fill_price=(_float(record.get("avg_fill_price")) or None),
                fill_qty=_float(record.get("filled_quantity")),
                broker_paper=record.get("paper"))
        if accounting_complete:
            _mirror_ownership_registry(
                record,
                reconciliation_note=(
                    f"positionId {pid} CLOSED und im Ledger bestaetigt"))
        else:
            record_execution_event(
                did, "POSITION_CLOSED_ACCOUNTING_PENDING",
                {"position_ids": [pid], "ledger_result": ledger_result},
                event_id=f"etoro:{did}:position-closed-accounting-pending:{pid}")
    if mutated:
        # Backfill-Ergebnis separat persistieren; PENDING bleibt dadurch fuer
        # den naechsten Hintergrund-Tick sichtbar und retrybar.
        _save(data)
    return [dict(record) for record, _ in mutated]


@_synchron
def _mutiere(decision_id, aenderung) -> dict:
    """Eine Lese-Aendere-Schreib-Folge unter dem Modul-Lock (v9.3).

    ``aenderung(record)`` bekommt den FRISCH gelesenen Datensatz, nicht den
    Schnappschuss des Aufrufers. Damit kann ein aelterer Worker-Snapshot
    einen terminalen Zustand nicht mehr zurueckdrehen. Gibt ``aenderung``
    ``False`` zurueck, wird nichts geschrieben.
    """
    data = _load()
    record = data.setdefault("records", {}).get(str(decision_id))
    if not record:
        return {}
    if aenderung(record) is False:
        return dict(record)
    record["updated_at_utc"] = _now()
    _save(data)
    return dict(record)


def _ist_terminal(record: dict) -> bool:
    return not _record_is_active(_migrate_record(record))


@_synchron
def mark_unprovable(decision_id: int, reason: str) -> None:
    data = _load(); record = data.setdefault("records", {}).get(str(int(decision_id)))
    if not record:
        return
    record["last_error"] = str(reason)[:500]
    if _execution_has_fill(record):
        # Ein exakter Fill ist beweisbar. Fehlende Depot-/History-Sicht ist
        # eine manuelle Klaerung, niemals eine zeitgesteuerte Freigabe.
        record["position_state"] = "MISSING_REVIEW"
        _sync_legacy_state(record)
        event_state = "POSITION_MISSING_REVIEW"
    else:
        record["submit_state"] = "UNPROVABLE"
        _sync_legacy_state(record)
        event_state = "UNPROVABLE"
    record["updated_at_utc"] = _now(); _save(data)
    mark_execution(decision_id, str(record.get("state") or event_state), record.get("order_ids"),
                   reference_id=record.get("reference_id", ""))
    record_execution_event(
        decision_id, event_state, {"reason": str(reason)[:500]},
        event_id=f"etoro:{decision_id}:{event_state.lower()}")
    if event_state == "UNPROVABLE":
        _notify(record, "failed")


def _notify(record: dict, phase: str) -> None:
    try:
        from notifier import send_telegram
        did = int(record["decision_id"]); symbol = record.get("symbol", "?")
        if phase == "uncertain":
            text = (f"⚠️ eToro-Kauf {symbol} angenommen, Ausführung noch ungeklärt. "
                    "Kein erneuter Kauf; automatische Wiederherstellung läuft.")
            event = f"etoro-reconcile:{did}:uncertain"
        elif phase == "confirmed":
            text = (f"✅ eToro-Ausführung {symbol} eindeutig wiederhergestellt: "
                    f"{float(record.get('filled_quantity') or 0):g} gefüllt.")
            event = f"etoro-reconcile:{did}:confirmed:{float(record.get('filled_quantity') or 0):g}"
        else:
            text = (f"🚨 eToro-Status {symbol} endgültig nicht sicher beweisbar/abgelehnt. "
                    "Position bleibt OBSERVE; bitte Brokerkonto prüfen.")
            event = f"etoro-reconcile:{did}:failed"
        send_telegram(text, priority="critical", event_id=event)
    except Exception:
        # Broker evidence must survive even if notification is unavailable.
        logger.debug("Reconciliation-Telegram nicht zustellbar", exc_info=True)


@_synchron
def status() -> dict:
    data = _load()
    records = list((data.get("records") or {}).values())
    records.sort(key=lambda x: (str(x.get("created_at_utc") or ""), int(x.get("decision_id") or 0)), reverse=True)
    return {"updated_at_utc": data.get("updated_at_utc"), "storage_error": data.get("storage_error"),
            "active": [x for x in records if _record_is_active(x)],
            "records": records}
