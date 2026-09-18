"""Read all asynchronous money-path journals in the same account/environment.

Missing journals mean no locally recorded operation; corrupt/unscoped active
records mean unknown. This projection never resolves or retries any operation.
"""
import json
import sqlite3
import time


def snapshot(account, environment):
    if not account or environment not in {"DEMO", "LIVE"}:
        raise ValueError("ETORO_OPERATION_SCOPE_UNPROVEN")
    rows = []
    import broker_exit_journal as exits
    path = exits._path()
    if path.exists():
        if path.is_symlink():
            raise ValueError("ETORO_EXIT_JOURNAL_INVALID")
        with sqlite3.connect(path.resolve().as_uri()+"?mode=ro", uri=True, timeout=2) as con:
            con.row_factory = sqlite3.Row
            con.execute("PRAGMA query_only=ON")
            deadline = time.monotonic()+2
            con.set_progress_handler(lambda: int(time.monotonic()>deadline), 1000)
            marks = ",".join("?" for _ in exits.ACTIVE)
            active = con.execute(f"SELECT * FROM broker_exit_intents WHERE broker='etoro' "
                f"AND account_fingerprint IN (?, '') AND environment IN (?, '') AND status IN ({marks}) "
                "ORDER BY created_at LIMIT 101", (account, environment, *exits.ACTIVE)).fetchall()
            if len(active) > 100:
                raise ValueError("ETORO_EXIT_JOURNAL_REVIEW_LIMIT")
            for row in active:
                detail = json.loads(row["detail_json"] or "{}")
                rows.append({"kind": "EXIT", "state": row["status"], "position_id": row["position_id"],
                    "order_id": row["broker_order_id"], "instrument_id": row["instrument_id"],
                    "requested_quantity": row["requested_quantity"], "filled_quantity": row["filled_quantity"],
                    "broker_status_id": detail.get("statusId", detail.get("statusID")),
                    "account_bound": row["account_fingerprint"] == account and row["environment"] == environment,
                    "detail": "Schliessauftrag noch ohne Terminalbeleg; kein erneuter Verkaufsauftrag"})
    import etoro_protection_journal as protection
    path = protection.path()
    if path.exists():
        if path.is_symlink():
            raise ValueError("ETORO_PROTECTION_JOURNAL_INVALID")
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("schema") != 1 or not isinstance(data.get("records"), dict):
            raise ValueError("ETORO_PROTECTION_JOURNAL_INVALID")
        if len(data["records"]) > 5000:
            raise ValueError("ETORO_PROTECTION_JOURNAL_REVIEW_LIMIT")
        pending = []
        for key, record in data["records"].items():
            if (not isinstance(record, dict) or not record.get("account")
                    or record.get("environment") not in {"DEMO", "LIVE"}
                    or not record.get("position_id")
                    or record.get("state") not in {"SUBMITTING", "ACCEPTED", "CONFIRMED", "REJECTED"}):
                raise ValueError("ETORO_PROTECTION_JOURNAL_SCOPE_UNPROVEN")
            if key != protection._key(record["account"], record["environment"], record["position_id"]):
                raise ValueError("ETORO_PROTECTION_JOURNAL_SCOPE_MISMATCH")
            if (record["account"] == account and record["environment"] == environment
                    and record["state"] in {"SUBMITTING", "ACCEPTED"}):
                pending.append(record)
        for row in pending:
            rows.append({"kind": "PROTECTION", "state": row["state"],
                "position_id": row["position_id"], "reference_id": row["reference_id"],
                "operation_id": row.get("operation_id"), "last_reconciliation": row.get("last_reconciliation"),
                "detail": "Schutzaenderung ungeklärt; risikoreduzierende Positionsausstiege bleiben separat geprueft"})
    def label(row):
        if row["kind"] == "EXIT":
            return f"Schließauftrag {row['order_id'] or 'ohne Broker-ID'} für Position {row['position_id']}: Abschluss unbestätigt"
        return f"Schutzänderung für Position {row['position_id']}: Abschluss unbestätigt"
    return {"account_fingerprint": account, "environment": environment, "complete": True,
            "rows": rows, "pending": len(rows), "blocks_entries": bool(rows),
            "detail": "; ".join(label(r) for r in rows[:8])}
