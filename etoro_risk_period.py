"""Forward-only recovery of the known unassigned OKX basis in the eToro file.

This does not reconstruct midnight equity or reassign old financial receipts.
It is limited to an unstarted trading day with no local economic activity.
The original file, current account evidence and legacy totals remain retained.
"""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import time

from safe_persistence import atomic_write_json, atomic_write_text

BASIS = "broker_kontowert:v2:etoro:USD"
METHOD = "FORWARD_ONLY_FIRST_OBSERVATION"


def eligible(raw):
    if (raw.get("risk_schema_version") != 2 or raw.get("risk_scope_key")
            or raw.get("risk_scope_origin") != "LEGACY_UNASSIGNED"
            or raw.get("equity_basis_key") != "handelbares_kapital:v3:okx:USDC"
            or raw.get("pending_persistence_operations") or raw.get("basis_review_receipt")):
        return False
    # No existing daily basis, loss or counted execution may be replaced.
    zero = ("day_start_equity", "last_equity", "realized_pnl_today", "trades_today",
        "estimated_costs_today", "gross_profit_today", "gross_loss_today", "net_profit_today",
        "net_loss_today", "unknown_pnl_trades_today", "equity_drawdown_pct")
    if any(type(raw.get(k)) not in (int, float) or raw[k] != 0 for k in zero):
        return False
    if any(raw.get(k) for k in ("counted_cost_ids_today", "counted_trade_ids_today", "unknown_pnl_ids_today")):
        return False
    return not any(r.get("booked_day") == raw.get("current_date")
                   for r in (raw.get("realized_receipts") or {}).values())


def apply(risk, evidence, *, now=None):
    """Run inside RiskState's reload/lock/save transaction, retaining all counters."""
    from etoro_risk_maintenance import review_evidence
    now = now or datetime.now(timezone.utc)
    expected_scope = getattr(risk, "_basis_observation", {}).get("observed_scope")
    if not expected_scope:
        raise ValueError("RISK_FORWARD_CURRENT_WORKER_SCOPE_UNPROVEN")
    assessment = review_evidence(evidence, expected_scope=expected_scope, today=risk.current_date, now=now)
    if assessment["status"] != "CURRENT_ACCOUNT_CONFIRMED":
        raise ValueError("RISK_FORWARD_ACCOUNT_EVIDENCE_UNPROVEN")
    if evidence["observations"]["closed_trade_history"]["data"]["rows"]:
        raise ValueError("RISK_FORWARD_DAY_HAS_BROKER_EXECUTIONS")
    current_scope = evidence["observed_scope"]
    equity = evidence["observations"]["account"]["data"]["equity"]
    def change():
        raw = risk._payload()
        target = risk._target()
        if target.name != "risk_state_etoro.json" or not eligible(raw):
            return False
        if str(raw["current_date"]) != evidence["day"]:
            return False
        original = target.read_text(encoding="utf-8")
        digest = hashlib.sha256(original.encode("utf-8")).hexdigest()
        archive = target.with_name(target.name + ".legacy-" + digest + ".bak")
        atomic_write_text(archive, original)
        evidence_name = target.with_name("etoro_risk_period_" + digest + ".json")
        atomic_write_json(evidence_name, evidence)
        # Everything except the new basis/scope and their review metadata is
        # retained. Loss/cooldown/persistence guards never get cleared here.
        risk.risk_scope_key = json.dumps(current_scope, separators=(",", ":"))
        risk.risk_scope_origin = "FORWARD_PERIOD_V10_1_2"
        risk.equity_basis_key = BASIS
        risk.equity_basis_changed_at = now.isoformat()
        risk.day_start_equity = equity
        risk.last_equity = equity
        risk.equity_basis_review_required = False
        risk.equity_basis_review_reason = ""
        risk.basis_review_receipt = {"method": METHOD, "started_at": now.isoformat(),
            "baseline_meaning": "First verified equity of this forward period; not midnight equity",
            "scope": current_scope, "basis": BASIS, "equity": equity,
            "archive": archive.name, "archive_sha256": digest, "evidence": evidence_name.name,
            "original_directory": str(target.parent.resolve()),
            "legacy_history_unassigned": True,
            "legacy_totals": {k: deepcopy(v) for k,v in raw.items() if k.startswith("lifetime_")},
            "legacy_receipts": deepcopy(raw.get("realized_receipts") or {})}
        return True
    return bool(risk._transaction(change))


def attempt(risk, broker):
    """Called only after the main thread completes its broker/ledger reconciliation."""
    if risk.risk_scope_origin == "FORWARD_PERIOD_V10_1_2":
        return initialize_result_scope(risk)
    if not eligible(risk._payload()):
        return False
    now = time.monotonic()
    if now < getattr(risk, "_next_forward_period_probe", 0):
        return False
    risk._next_forward_period_probe = now + 900
    from etoro_risk_maintenance import collect
    import config
    evidence = collect(broker, local_timezone=getattr(config, "LOCAL_TIMEZONE", "Europe/Berlin"))
    return apply(risk, evidence)


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def _valid_period_scope(risk, proof):
    """Validate the original cohort; a timestamp alone never excludes results."""
    from etoro_risk_maintenance import review_evidence
    receipt = risk.basis_review_receipt
    if (risk.risk_scope_origin != "FORWARD_PERIOD_V10_1_2"
            or risk.equity_basis_key != BASIS
            or receipt.get("method") != METHOD
            or receipt.get("legacy_history_unassigned") is not True
            or proof.get("method") != "ARCHIVED_UNASSIGNED_COHORT_V1"
            or proof.get("scope") != json.loads(risk.risk_scope_key)
            or proof.get("scope") != receipt.get("scope")
            or proof.get("started_at") != receipt.get("started_at")
            or proof.get("archive_sha256") != receipt.get("archive_sha256")):
        raise ValueError("RISK_RESULT_PERIOD_IDENTITY_UNPROVEN")
    original_text = proof["original_state"]
    if hashlib.sha256(original_text.encode("utf-8")).hexdigest() != proof["archive_sha256"]:
        raise ValueError("RISK_RESULT_PERIOD_ARCHIVE_CHANGED")
    original = json.loads(original_text)
    if (not eligible(original)
            or original.get("realized_receipts", {}) != receipt.get("legacy_receipts", {})
            or {k: v for k, v in original.items() if k.startswith("lifetime_")}
                != receipt.get("legacy_totals")):
        raise ValueError("RISK_RESULT_PERIOD_COHORT_UNPROVEN")
    start = datetime.fromisoformat(receipt["started_at"].replace("Z", "+00:00"))
    if start.tzinfo is None:
        raise ValueError("RISK_RESULT_PERIOD_TIME_UNPROVEN")
    evidence = proof["account_evidence"]
    if hashlib.sha256(_canonical(evidence)).hexdigest() != proof["evidence_sha256"]:
        raise ValueError("RISK_RESULT_PERIOD_EVIDENCE_CHANGED")
    assessment = review_evidence(evidence, expected_scope=proof["scope"],
        today=original["current_date"], now=start)
    if (assessment["status"] != "CURRENT_ACCOUNT_CONFIRMED"
            or evidence["observations"]["closed_trade_history"]["data"]["rows"]
            or evidence["observations"]["account"]["data"]["equity"] != receipt.get("equity")):
        raise ValueError("RISK_RESULT_PERIOD_ACCOUNT_UNPROVEN")
    count = original.get("lifetime_unknown_pnl_trades")
    if type(count) is not int or count < 0:
        raise ValueError("RISK_RESULT_PERIOD_COUNTER_UNPROVEN")
    cohort = {key: row for key, row in original.get("realized_receipts", {}).items()
              if row.get("status") == "UNKNOWN"}
    if len(cohort) > count:
        raise ValueError("RISK_RESULT_PERIOD_COUNTER_CONFLICT")
    return count, cohort


def initialize_result_scope(risk):
    """One audited projection; does not change money, counts, loss or cooldown.

    Archives are read only from the state directory. The validated original
    bytes and account evidence are then retained in the state, so a later
    upgrade cannot detach this result-period proof from its risk state.
    """
    if risk.result_period_scope or risk.risk_scope_origin != "FORWARD_PERIOD_V10_1_2":
        return False
    from pathlib import Path
    receipt = risk.basis_review_receipt
    names = [receipt.get("archive"), receipt.get("evidence")]
    if any(not isinstance(name, str) or Path(name).name != name for name in names):
        return False
    parent = risk._target().parent
    paths = [parent / name for name in names]
    if any(not path.is_file() or path.is_symlink() or path.stat().st_size > 2_000_000 for path in paths):
        return False
    original = paths[0].read_text(encoding="utf-8")
    evidence = json.loads(paths[1].read_text(encoding="utf-8"))
    proof = {"method": "ARCHIVED_UNASSIGNED_COHORT_V1", "scope": receipt.get("scope"),
        "started_at": receipt.get("started_at"), "archive_sha256": receipt.get("archive_sha256"),
        "original_state": original, "account_evidence": evidence,
        "evidence_sha256": hashlib.sha256(_canonical(evidence)).hexdigest()}
    _valid_period_scope(risk, proof)
    def change():
        if risk.result_period_scope:
            return False
        _valid_period_scope(risk, proof)
        risk.result_period_scope = proof
        return True
    return bool(risk._transaction(change))


def result_scope_summary(risk):
    """One buy-gate/readiness projection of active and historical UNKNOWNs.

    Lifetime counts are conserved, including anonymous legacy counts. Only
    the original archived cohort is outside this forward period.

    10.7.0 (Entscheidung Georg, 18.09.2026): Ein unbekanntes Verkaufsergebnis
    sperrt neue Kaeufe nur noch am Handelstag des Verkaufs. Bis 10.6.0 sperrte
    es "across days and restarts" -- ein Gebuehrenloch vom 08.09. hielt am
    18.09. den Handel an, obwohl es die Tagesverlustgrenze des 18.09. nicht
    beruehren kann. Die Buchhaltung bleibt vollstaendig: ``open_unknown`` und
    ``lifetime_unknown`` zaehlen weiter alle offenen Belege; nur
    ``active_unknown`` (die Sperrwirkung) ist auf den laufenden Tag begrenzt.
    """
    receipts = getattr(risk, "realized_receipts", {})
    today_key = str(getattr(risk, "current_date", "") or "")
    today = int(getattr(risk, "unknown_pnl_trades_today", 0) or 0)
    lifetime = int(getattr(risk, "lifetime_unknown_pnl_trades", 0) or 0)

    def is_unknown(row):
        return row.get("status") == "UNKNOWN"

    def is_today(row):
        return str(row.get("booked_day") or "") == today_key

    all_unknown = sum(is_unknown(r) for r in receipts.values())
    today_unknown = sum(is_unknown(r) and is_today(r) for r in receipts.values())
    result = {"active_unknown": max(today, today_unknown),
              "open_unknown": all_unknown,
              "historical_unknown": 0, "lifetime_unknown": lifetime,
              "review_required": False, "method": "TODAY_UNRESOLVED_RESULTS"}
    proof = getattr(risk, "result_period_scope", {})
    if not proof:
        return result
    try:
        original_count, cohort = _valid_period_scope(risk, proof)
        completed, archived_keys = 0, set()
        for key, old in cohort.items():
            current = receipts.get(key, {})
            resolved_key = key
            if current.get("status") == "ALIAS":
                resolved_key = str(current.get("target") or "")
                current = receipts.get(resolved_key, {})
            if resolved_key in archived_keys or current.get("status") not in {"UNKNOWN", "CONFIRMED"}:
                raise ValueError("RISK_RESULT_PERIOD_RECEIPT_MISSING")
            archived_keys.add(resolved_key)
            if current.get("booked_day") != old.get("booked_day"):
                raise ValueError("RISK_RESULT_PERIOD_RECEIPT_DAY_CHANGED")
            completed += current.get("status") == "CONFIRMED"
        historical = original_count - completed
        if historical < 0 or lifetime < historical:
            raise ValueError("RISK_RESULT_PERIOD_COUNTER_CONFLICT")
        open_receipts = sum(is_unknown(row) for key, row in receipts.items() if key not in archived_keys)
        active_receipts = sum(is_unknown(row) and is_today(row)
                              for key, row in receipts.items() if key not in archived_keys)
        result.update(active_unknown=max(today, active_receipts), open_unknown=open_receipts,
                      historical_unknown=historical, method=proof["method"],
                      period_started_at=proof["started_at"])
    except (ValueError, KeyError, TypeError, AttributeError):
        result["review_required"] = True
    return result
