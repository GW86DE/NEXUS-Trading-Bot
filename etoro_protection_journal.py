"""Durable identities for asynchronous eToro protection PATCH requests."""
import hashlib
import json
import os
from pathlib import Path
from datetime import datetime, timezone
from safe_persistence import atomic_write_json
from state_lock import critical_state_lock


def path():
    return Path(os.environ.get("TRADINGBOT_TEST_STATE_DIR") or Path(__file__).parent) / "etoro_protection_journal.json"


def _key(account, environment, position):
    if not account or environment not in {"DEMO", "LIVE"} or not position:
        raise ValueError("ETORO_PROTECTION_SCOPE_UNPROVEN")
    return hashlib.sha256(json.dumps([account, environment, position]).encode()).hexdigest()


def pending(account, environment, positions):
    """Read-only unresolved requests; absence is only proven from valid state."""
    target = path()
    with critical_state_lock(target):
        if not target.exists():
            return []
        data = json.loads(target.read_text())
        if data.get("schema") != 1 or not isinstance(data.get("records"), dict):
            raise ValueError("ETORO_PROTECTION_JOURNAL_INVALID")
        result = []
        for position in positions:
            row = data["records"].get(_key(account, environment, position))
            if row:
                if (row.get("account") != account or row.get("environment") != environment
                        or str(row.get("position_id")) != str(position)):
                    raise ValueError("ETORO_PROTECTION_JOURNAL_SCOPE_MISMATCH")
                if row.get("state") not in {"SUBMITTING", "ACCEPTED", "CONFIRMED", "REJECTED"}:
                    raise ValueError("ETORO_PROTECTION_JOURNAL_STATE_UNPROVEN")
                if row.get("state") in {"SUBMITTING", "ACCEPTED"}:
                    result.append({k: row.get(k) for k in ("position_id", "reference_id", "state", "payload",
                        "operation_id", "contract", "last_reconciliation")})
        return result


def prepare(account, environment, position, reference_id, payload, evidence, *, contract=None):
    """Reserve before PATCH; an ambiguous prior PATCH cannot be silently retried."""
    target = path()
    key = _key(account, environment, position)
    with critical_state_lock(target):
        data = json.loads(target.read_text()) if target.exists() else {"schema": 1, "records": {}}
        if data.get("schema") != 1 or not isinstance(data.get("records"), dict):
            raise ValueError("ETORO_PROTECTION_JOURNAL_INVALID")
        old = data["records"].get(key)
        if old and old.get("state") in {"SUBMITTING", "ACCEPTED"}:
            raise ValueError("ETORO_PROTECTION_PATCH_RECONCILIATION_REQUIRED")
        if key not in data["records"] and len(data["records"]) >= 5000:
            raise ValueError("ETORO_PROTECTION_JOURNAL_CAPACITY_REVIEW")
        history = list((old or {}).get("history") or [])
        if old:
            history.append({k: v for k, v in old.items() if k != "history"})
        if len(history) >= 5000:
            raise ValueError("ETORO_PROTECTION_JOURNAL_HISTORY_REVIEW")
        data["records"][key] = {"account": account, "environment": environment,
            "position_id": position, "reference_id": reference_id,
            "payload": payload, "normalization": evidence, "state": "SUBMITTING",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "contract": contract, "history": history}
        atomic_write_json(target, data)


def accepted(account, environment, position, reference_id, response):
    """Persist asynchronous acceptance; malformed/mismatched ACK stays ambiguous."""
    from uuid import UUID
    if (not isinstance(response, dict)
            or str(response.get("positionId")) != str(position)
            or str(response.get("referenceId")) != str(reference_id)):
        raise ValueError("ETORO_PROTECTION_ACK_SCOPE_MISMATCH")
    try:
        UUID(str(response["operationId"]))
    except (KeyError, ValueError, TypeError) as exc:
        raise ValueError("ETORO_PROTECTION_ACK_OPERATION_UNPROVEN") from exc
    target = path()
    with critical_state_lock(target):
        data = json.loads(target.read_text())
        row = data.get("records", {}).get(_key(account, environment, position))
        if not row or row.get("reference_id") != reference_id or row.get("state") != "SUBMITTING":
            raise ValueError("ETORO_PROTECTION_ACK_REFERENCE_MISMATCH")
        row.update(state="ACCEPTED", operation_id=response["operationId"],
                   accepted_at=datetime.now(timezone.utc).isoformat(),
                   acceptance={k: response[k] for k in ("operationId", "positionId", "referenceId")})
        atomic_write_json(target, data)


def confirm(account, environment, position, stop, take_profit, snapshot_id, *, flags=None,
            position_row=None, snapshot_at=None):
    """Only a matching subsequent broker snapshot can confirm the stored rates."""
    target = path()
    if not target.exists():
        return None
    key = _key(account, environment, position)
    with critical_state_lock(target):
        data = json.loads(target.read_text())
        row = data.get("records", {}).get(key)
        if not row:
            return None
        from etoro_protection_evidence import assess
        contract = row.get("contract") or {}
        observed = dict(position_row or {"positionId": position, "stopLossRate": stop,
            "takeProfitRate": take_profit, **(flags or {})})
        proof = assess([observed], {position}, row["payload"]["stopLossRate"],
            row["payload"]["takeProfitRate"], quantity=contract.get("quantity"),
            instrument_id=contract.get("instrument_id"),
            strict_contract=bool(contract.get("strict_contract")))
        if contract:
            try:
                created = datetime.fromisoformat(row["created_at"])
                observed_at = datetime.fromisoformat(str(snapshot_at))
                if not observed_at.tzinfo or observed_at <= created:
                    proof["confirmed"] = False
                    proof["reason_code"] = "ETORO_PROTECTION_SNAPSHOT_NOT_AFTER_SUBMIT"
            except (ValueError, TypeError):
                proof["confirmed"] = False
                proof["reason_code"] = "ETORO_PROTECTION_SNAPSHOT_TIME_UNPROVEN"
        if proof["confirmed"] and snapshot_id and row.get("state") in {"SUBMITTING", "ACCEPTED"}:
            row.update(state="CONFIRMED", snapshot_id=snapshot_id,
                       confirmed_at=datetime.now(timezone.utc).isoformat())
            atomic_write_json(target, data)
        elif snapshot_id and row.get("state") in {"SUBMITTING", "ACCEPTED"}:
            last = {"snapshot_id": snapshot_id, "snapshot_at": snapshot_at,
                "expected_quantity": contract.get("quantity"), "observed_quantity": observed.get("units"),
                "reason_code": proof.get("reason_code"), "operation_completion_proven": False}
            if row.get("last_reconciliation") != last:
                row["last_reconciliation"] = last
                atomic_write_json(target, data)
        return {"reference_id": row["reference_id"], "state": row["state"],
                "operation_id": row.get("operation_id"),
                "stop": row["payload"]["stopLossRate"],
                "take_profit": row["payload"]["takeProfitRate"]}


def rejected(account, environment, position, reference_id, reason_code):
    """Only use after a typed, proven broker rejection; never after a timeout."""
    target = path()
    key = _key(account, environment, position)
    with critical_state_lock(target):
        data = json.loads(target.read_text())
        row = data.get("records", {}).get(key)
        if not row or row.get("reference_id") != reference_id:
            raise ValueError("ETORO_PROTECTION_REJECTION_REFERENCE_MISMATCH")
        row.update(state="REJECTED", reason_code=str(reason_code)[:100])
        atomic_write_json(target, data)
