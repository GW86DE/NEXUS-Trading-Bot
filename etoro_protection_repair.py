"""Explicit BOT protection-plan adoption, never a broker order or takeover.

Preview is read-only apart from the requested report. Apply binds the reviewed
decision to unchanged local state and fresh authoritative broker evidence.
This maintenance path intentionally cannot PATCH: differing broker prices
remain blocked and require separately reviewed order/eligibility handling.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path

from etoro_protection_evidence import assess, number
from broker.base import BrokerFehler

SCHEMA = "ETORO_EXISTING_PROTECTION_PLAN_V1"


def canonical_state_path():
    """Use exactly the state file loaded by PositionManager in this install."""
    import config
    path = Path(config.POSITION_STATE_FILE)
    return path.resolve() if path.is_absolute() else Path(__file__).resolve().parent / path


def digest(value):
    raw = value if isinstance(value, bytes) else json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(raw).hexdigest()


def fresh(timestamp, *, now=None, maximum_seconds=90):
    now = now or datetime.now(timezone.utc)
    try:
        at = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
        if not at.tzinfo or not -5 <= (now - at).total_seconds() <= maximum_seconds:
            raise ValueError
    except (ValueError, TypeError):
        raise ValueError("ETORO_REPAIR_SNAPSHOT_STALE_OR_UNTIMED") from None
    return at


def merge_breakdown(rows, breakdown, *, instrument_id, now=None):
    """Join stop type only after all overlapping position facts agree.

    Called by the CID-bound adapter. A display or a cached historical fixture
    can never supply missing broker facts. Missing flags remain missing.
    """
    if not isinstance(breakdown, dict) or breakdown.get("accountCurrency") != "USD":
        raise ValueError("ETORO_REPAIR_BREAKDOWN_CURRENCY_UNPROVEN")
    fresh(breakdown.get("timestamp"), now=now)
    groups = breakdown.get("instruments")
    if not isinstance(groups, list):
        raise ValueError("ETORO_REPAIR_BREAKDOWN_INCOMPLETE")
    groups = [g for g in groups if isinstance(g, dict)
              and str(g.get("instrumentId")) == str(instrument_id)]
    if len(groups) != 1 or not isinstance(groups[0].get("positions"), list):
        raise ValueError("ETORO_REPAIR_BREAKDOWN_INSTRUMENT_UNPROVEN")
    if not isinstance(groups[0].get("orders"), list):
        raise ValueError("ETORO_REPAIR_PENDING_ORDER_VIEW_UNPROVEN")
    output = []
    for row in rows:
        matches = [r for r in groups[0]["positions"] if isinstance(r, dict)
                   and str(r.get("positionId")) == str(row.get("positionId"))]
        if len(matches) != 1:
            raise ValueError("ETORO_REPAIR_BREAKDOWN_POSITION_UNPROVEN")
        other = matches[0]
        if (str(other.get("instrumentId")) != str(instrument_id)
                or row.get("isBuy") is not True or other.get("direction") != "long"
                or other.get("assetCurrency") != "USD"):
            raise ValueError("ETORO_REPAIR_BREAKDOWN_IDENTITY_MISMATCH")
        # Exact Decimal equality deliberately avoids guessing a tolerance or tick.
        for field in ("units", "stopLossRate", "takeProfitRate"):
            if number(row.get(field)) != number(other.get(field)):
                raise ValueError("ETORO_REPAIR_READBACK_CHANGED")
        if row.get("stopLossType") not in (None, other.get("stopLossType")):
            raise ValueError("ETORO_REPAIR_STOP_TYPE_CONFLICT")
        merged = dict(row)
        merged["stopLossType"] = other.get("stopLossType")
        merged["_stop_type_source"] = {
            "source": "ETORO_CID_BOUND_INSTRUMENT_BREAKDOWN",
            "timestamp": breakdown["timestamp"], "response_sha256": digest(breakdown)}
        output.append(merged)
    return output, deepcopy(groups[0]["orders"])


def validate_record(record):
    if (record.get("source") != "BOT" or record.get("ownership_status") != "VERIFIED"
            or record.get("management_mode") not in {"AUTO", "PENDING_CONFIRMATION"}
            or record.get("user_observe_locked") is not False
            or record.get("asset_type") != "stock" or record.get("currency") != "USD"
            or record.get("reconciliation_status") != "CONFIRMED_OPEN"
            or not (record.get("entry_order_ids") or record.get("entry_reference_id"))):
        raise ValueError("ETORO_REPAIR_BOT_OWNERSHIP_UNPROVEN")
    ids = [str(x) for x in record.get("owned_position_ids", [])]
    if (len(ids) != 1 or not ids[0]
            or {str(x) for x in record.get("observed_position_ids", [])} != set(ids)
            or {str(x) for x in record.get("broker_position_ids", [])} != set(ids)):
        raise ValueError("ETORO_REPAIR_SINGLE_OWNED_POSITION_REQUIRED")
    if (not record.get("broker_account_fingerprint")
            or record.get("broker_environment") not in {"DEMO", "LIVE"}
            or not str(record.get("broker_instrument_id") or "").isdigit()):
        raise ValueError("ETORO_REPAIR_ACCOUNT_SCOPE_UNPROVEN")
    number(record.get("quantity"))
    return ids


def validate_snapshot(record, snapshot, *, stop, take, now=None):
    ids = validate_record(record)
    if (not isinstance(snapshot, dict) or snapshot.get("complete") is not True
            or snapshot.get("source") != "ETORO_PNL_AND_INSTRUMENT_BREAKDOWN"
            or not snapshot.get("snapshot_id")
            or snapshot.get("account_fingerprint") != record["broker_account_fingerprint"]
            or snapshot.get("environment") != record["broker_environment"]):
        raise ValueError("ETORO_REPAIR_FRESH_ACCOUNT_SNAPSHOT_REQUIRED")
    fresh(snapshot.get("snapshot_at"), now=now)
    if snapshot.get("pending_orders_complete") is not True:
        raise ValueError("ETORO_REPAIR_PENDING_ORDER_VIEW_UNPROVEN")
    if snapshot.get("pending_orders") != []:
        raise ValueError("ETORO_REPAIR_PENDING_BROKER_ORDER")
    if snapshot.get("pending_protection_updates") != []:
        raise ValueError("ETORO_REPAIR_PENDING_PROTECTION_UPDATE")
    rows = snapshot.get("rows")
    if not isinstance(rows, list):
        raise ValueError("ETORO_REPAIR_ROWS_UNPROVEN")
    selected = [r for r in rows if isinstance(r, dict) and str(r.get("positionId")) in ids]
    if len(selected) == 1 and number(selected[0].get("units")) != number(record["quantity"]):
        raise ValueError("ETORO_REPAIR_REMAINING_QUANTITY_CHANGED")
    for row in selected:
        source = row.get("_stop_type_source") or {}
        if (source.get("source") != "ETORO_CID_BOUND_INSTRUMENT_BREAKDOWN"
                or len(str(source.get("response_sha256") or "")) != 64):
            raise ValueError("ETORO_REPAIR_STOP_TYPE_SOURCE_UNPROVEN")
        fresh(source.get("timestamp"), now=now)
    proof = assess(selected, ids, stop, take, quantity=record["quantity"],
        instrument_id=record["broker_instrument_id"], snapshot_id=snapshot["snapshot_id"],
        strict_contract=True)
    if not proof["confirmed"]:
        raise ValueError(proof["reason_code"])
    proof["snapshot_at"] = snapshot["snapshot_at"]
    return proof


def create_plan(original, record_id, snapshot, *, stop, take, reason, now=None):
    """Explicit new values are inputs, never automatically copied from observed."""
    now = now or datetime.now(timezone.utc)
    raw = json.loads(original)
    record = raw["positions"][str(record_id)]
    validate_record(record)
    sl, tp = number(stop), number(take)
    old_sl, old_tp = number(record["planned_stop"]), number(record["planned_take"])
    if sl >= tp or sl < old_sl or tp > old_tp:
        raise ValueError("ETORO_REPAIR_PLAN_RANGE_OR_RISK_INCREASE")
    if not isinstance(reason, str) or not 8 <= len(reason.strip()) <= 500:
        raise ValueError("ETORO_REPAIR_EXPLICIT_DECISION_REASON_REQUIRED")
    history = record.get("protection_plan_history") or []
    if not isinstance(history, list) or len(history) >= 1000:
        raise ValueError("ETORO_REPAIR_PLAN_HISTORY_REVIEW")
    proof = validate_snapshot(record, snapshot, stop=stop, take=take, now=now)
    plan = {"schema": SCHEMA, "status": "READY_TO_ADOPT", "record_id": str(record_id),
        "source_sha256": digest(original), "decided_at": now.isoformat(),
        "reason": reason.strip(), "account_fingerprint": record["broker_account_fingerprint"],
        "environment": record["broker_environment"], "symbol": record["symbol"],
        "position_ids": list(record["owned_position_ids"]),
        "instrument_id": record["broker_instrument_id"], "quantity": record["quantity"],
        "original_plan": {"stop": record["planned_stop"], "take_profit": record["planned_take"],
            "risk_amount": record.get("planned_risk_amount"), "risk_pct": record.get("planned_risk_pct")},
        "effective_plan": {"stop": str(sl), "take_profit": str(tp), "stop_type": "fixed"},
        "snapshot_id": snapshot["snapshot_id"], "snapshot_at": snapshot["snapshot_at"],
        "evidence": proof, "broker_action": "NONE", "sent": None}
    plan["decision_id"] = digest(plan)
    return plan


def check_plan(plan):
    if (not isinstance(plan, dict) or plan.get("schema") != SCHEMA
            or plan.get("status") != "READY_TO_ADOPT" or plan.get("broker_action") != "NONE"
            or plan.get("sent") is not None
            or plan.get("decision_id") != digest({k: v for k, v in plan.items() if k != "decision_id"})):
        raise ValueError("ETORO_REPAIR_PLAN_CHANGED_OR_INVALID")


def apply_plan(target, plan, *, expected_decision_id, fetch_snapshot, assert_writers_stopped, now=None):
    """One backed-up atomic local transaction after a fresh broker readback.

    fetch_snapshot is supplied by the CID-bound adapter in the CLI. Tests use
    synthetic payloads; no offline payload can be supplied to the apply CLI.
    """
    from safe_persistence import atomic_write_json, atomic_write_text
    from state_lock import critical_state_lock
    check_plan(plan)
    if expected_decision_id != plan["decision_id"]:
        raise ValueError("ETORO_REPAIR_EXPLICIT_DECISION_ID_REQUIRED")
    target = Path(target)
    if target.is_symlink() or not target.is_file():
        raise ValueError("ETORO_REPAIR_STATE_FILE_INVALID")
    assert_writers_stopped()
    with critical_state_lock(target):
        original = target.read_bytes()
        raw = json.loads(original)
        record = raw["positions"][plan["record_id"]]
        applied = [r for r in record.get("protection_plan_history", [])
                   if r.get("decision_id") == expected_decision_id]
        if applied:
            validate_record(record)
            for current, reviewed in (("broker_account_fingerprint", "account_fingerprint"),
                    ("broker_environment", "environment"), ("broker_instrument_id", "instrument_id"),
                    ("owned_position_ids", "position_ids"), ("quantity", "quantity")):
                if record[current] != plan[reviewed]:
                    raise ValueError("ETORO_REPAIR_APPLIED_PLAN_SCOPE_CHANGED")
            if (number(record["planned_stop"]) != number(plan["effective_plan"]["stop"])
                    or number(record["planned_take"]) != number(plan["effective_plan"]["take_profit"])):
                raise ValueError("ETORO_REPAIR_APPLIED_PLAN_NO_LONGER_CURRENT")
            return {"status": "ALREADY_APPLIED", "decision_id": expected_decision_id,
                    "detail": "Lokaler Plan bereits gespeichert; aktueller Brokerschutz wird im Core neu geprueft."}
        if digest(original) != plan["source_sha256"]:
            raise ValueError("ETORO_REPAIR_STATE_CHANGED_SINCE_REVIEW")
        fresh(plan["decided_at"], now=now, maximum_seconds=900)
        snapshot = fetch_snapshot(deepcopy(record))
        # Do not freeze time before a potentially slow network request.
        checked_at = now or datetime.now(timezone.utc)
        proof = validate_snapshot(record, snapshot, stop=plan["effective_plan"]["stop"],
            take=plan["effective_plan"]["take_profit"], now=checked_at)
        if fresh(snapshot["snapshot_at"], now=checked_at) <= fresh(plan["snapshot_at"], now=checked_at, maximum_seconds=900):
            raise ValueError("ETORO_REPAIR_NEW_READBACK_REQUIRED")
        verified = create_plan(original, plan["record_id"], snapshot,
            stop=plan["effective_plan"]["stop"], take=plan["effective_plan"]["take_profit"],
            reason=plan["reason"], now=checked_at)
        for key in ("account_fingerprint", "environment", "position_ids", "instrument_id", "quantity", "original_plan"):
            if plan[key] != verified[key]:
                raise ValueError("ETORO_REPAIR_PLAN_BINDING_MISMATCH")
        assert_writers_stopped()
        if target.read_bytes() != original:
            raise ValueError("ETORO_REPAIR_CONCURRENT_STATE_CHANGE")
        backup = target.with_name(target.name + ".protection-plan-" + digest(original)[:20] + ".bak")
        if backup.is_symlink() or (backup.exists() and backup.read_bytes() != original):
            raise ValueError("ETORO_REPAIR_BACKUP_CONFLICT")
        atomic_write_text(backup, original.decode("utf-8"))
        if backup.read_bytes() != original:
            raise ValueError("ETORO_REPAIR_BACKUP_INVALID")
        updated = deepcopy(raw)
        target_record = updated["positions"][plan["record_id"]]
        receipt = {"schema_version": 1, "decision_id": plan["decision_id"],
            "method": "ADOPT_EXISTING_BROKER_PROTECTION", "decided_at": plan["decided_at"],
            "confirmed_at": checked_at.isoformat(), "reason": plan["reason"],
            **{k: deepcopy(plan[k]) for k in ("original_plan", "effective_plan", "account_fingerprint",
                "environment", "position_ids", "instrument_id", "quantity")},
            "snapshot_id": snapshot["snapshot_id"], "snapshot_at": snapshot["snapshot_at"],
            "before_sha256": digest(original), "backup": backup.name, "evidence": proof,
            "sent": None}
        target_record.setdefault("protection_plan_history", []).append(receipt)
        target_record["planned_stop"] = float(number(plan["effective_plan"]["stop"]))
        target_record["planned_take"] = float(number(plan["effective_plan"]["take_profit"]))
        # Keep awaiting a new normal-cycle readback; maintenance is no live approval.
        target_record.update(protection_status="UNCONFIRMED", management_mode="PENDING_CONFIRMATION",
            protection_detail="Neuer Schutzplan mit Brokerbeleg gespeichert; naechster Core-Abgleich steht aus.",
            management_note="Neuer Schutzplan mit Brokerbeleg gespeichert; naechster Core-Abgleich steht aus.")
        atomic_write_json(target, updated)
        if json.loads(target.read_text()) != updated:
            raise RuntimeError("ETORO_REPAIR_WRITE_VERIFICATION_FAILED")
        return {"status": "APPLIED", "decision_id": plan["decision_id"], "backup": str(backup),
                "broker_action": "NONE", "detail": target_record["protection_detail"]}


def main(argv=None):
    import argparse
    from contextlib import nullcontext
    p = argparse.ArgumentParser(description="eToro-BOT-Schutzplan pruefen/uebernehmen; niemals Brokerorders")
    p.add_argument("--state", type=Path, default=canonical_state_path())
    p.add_argument("--record-id")
    p.add_argument("--stop")
    p.add_argument("--take-profit")
    p.add_argument("--reason")
    p.add_argument("--output", type=Path, help="Geprueften Plan als JSON speichern")
    p.add_argument("--plan", type=Path, help="Zuvor angezeigter Plan fuer explizite Uebernahme")
    p.add_argument("--apply", action="store_true")
    p.add_argument("--decision-id", help="Exakte decision_id aus dem angezeigten Plan")
    p.add_argument("--workers-stopped", action="store_true")
    p.add_argument("--read-broker", action="store_true", help="Jetzt frisch lesend vom Broker abrufen")
    args = p.parse_args(argv)
    try:
        if args.state.resolve() != canonical_state_path().resolve():
            raise ValueError("ETORO_REPAIR_INSTALLED_POSITION_STATE_REQUIRED")
        if args.output and args.output.resolve() == args.state.resolve():
            raise ValueError("ETORO_REPAIR_OUTPUT_MUST_NOT_BE_STATE")
        if args.output and args.output.exists():
            raise ValueError("ETORO_REPAIR_OUTPUT_EXISTS")
        if args.apply and (not args.plan or not args.decision_id or not args.workers_stopped):
            p.error("--apply braucht --plan, --decision-id und --workers-stopped")
        original = args.state.read_bytes()
        raw = json.loads(original)
        if not args.read_broker:
            print(json.dumps({"status": "READ_ONLY", "source_sha256": digest(original),
                "positions": [{"record_id": key, "symbol": row.get("symbol"),
                    "source": row.get("source"), "ownership_status": row.get("ownership_status"),
                    "planned_stop": row.get("planned_stop"), "planned_take": row.get("planned_take")}
                    for key, row in raw.get("positions", {}).items()],
                "detail": "Fuer frische Vorschau --read-broker und konkrete neue Planwerte angeben."}, indent=2))
            if args.apply:
                p.error("--apply erfordert --read-broker; ein Offline-Snapshot reicht nicht")
            return 0
        plan = json.loads(args.plan.read_text()) if args.plan else None
        if plan:
            check_plan(plan)
        record_id = plan["record_id"] if plan else args.record_id
        if not record_id or (not plan and not all((args.stop, args.take_profit, args.reason))):
            p.error("Vorschau braucht --record-id, --stop, --take-profit und --reason")
        record = raw["positions"][record_id]
        validate_record(record)
        from broker.etoro import EtoroBroker
        from instance_lock import SingleInstanceLock
        from nexus_update import Host
        def stopped():
            if not args.workers_stopped:
                raise ValueError("ETORO_REPAIR_STOP_WRITERS_FIRST")
            Host().other_writers(args.state.parent.resolve())
        lock = SingleInstanceLock(args.state.parent / "tradingbot.instance.lock") if args.apply else nullcontext()
        with lock:
            if args.apply:
                stopped()
            broker = EtoroBroker(paper=record["broker_environment"] == "DEMO")
            # Minimal read-only initialization: connect() also starts recovery workers.
            broker._bind_account_identity(broker._request("GET", "/api/v1/me"))
            def fetch(row):
                return broker.protection_repair_snapshot(row["broker_instrument_id"], row["owned_position_ids"])
            if args.apply:
                out = apply_plan(args.state, plan, expected_decision_id=args.decision_id,
                    fetch_snapshot=fetch, assert_writers_stopped=stopped)
            else:
                out = create_plan(original, record_id, fetch(record),
                    stop=(plan or {}).get("effective_plan", {}).get("stop", args.stop),
                    take=(plan or {}).get("effective_plan", {}).get("take_profit", args.take_profit),
                    reason=(plan or {}).get("reason", args.reason))
        if args.output:
            from safe_persistence import atomic_write_json
            atomic_write_json(args.output, out)
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, KeyError, OSError, RuntimeError, BrokerFehler) as exc:
        print(json.dumps({"status": "BLOCKED", "reason": str(exc), "broker_action": "NONE"}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
