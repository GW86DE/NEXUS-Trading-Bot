"""Retain broker-reported results without promoting an unknown fee scope.

The history endpoint calls ``netProfit`` net profit and ``fees`` trade fees.
Neither its schema nor two arithmetically consistent fields in one response
establish whether separately charged opening/closing commission is included.
PEP demonstrates this: history fees=0, but its native opening receipt fees=1.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json

from ledger_result import finite_number


def _stamp(value):
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            return None
        return dt.astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None


def _same(a, b):
    a, b = finite_number(a), finite_number(b)
    return a is not None and b is not None and abs(a-b) <= max(1e-8, abs(b)*1e-7)


def assess(row, history_row):
    """Return a scope assessment, never a permission to confirm fee_quality."""
    net = finite_number(history_row.get("netProfit"))
    fees = finite_number(history_row.get("fees"))
    gross = (float(history_row["closeRate"])-float(history_row["openRate"]))*float(history_row["units"])
    known_entry = (finite_number(row.get("einstieg_gebuehr"))
                   if row.get("entry_fee_quality") == "CONFIRMED"
                   or row.get("fee_quality") == "CONFIRMED" else None)
    calculated_net = finite_number(row.get("netto_pnl"))
    arithmetic_delta = gross-fees-net if net is not None and fees is not None else None
    implied_cost = gross-net if net is not None else None
    quality = "BROKER_HISTORY_COST_SCOPE_UNPROVEN"
    reason = "Brokerwert vorhanden; Umfang der separat berechneten Handelskosten nicht belegt"
    if net is None or fees is None or fees < 0:
        quality, reason = "BROKER_HISTORY_FIELDS_INCOMPLETE", "Broker-Ergebnis oder gueltiger Gebuehrenwert fehlt"
    elif known_entry is not None and implied_cost < known_entry-0.011:
        quality = "BROKER_HISTORY_COST_SCOPE_CONFLICT"
        reason = "Broker-Historienergebnis beruecksichtigt bereits bestaetigte Einstiegskosten nicht vollstaendig"
    elif abs(arithmetic_delta) > 0.011:
        quality = "BROKER_HISTORY_ARITHMETIC_MISMATCH"
        reason = "Kursdifferenz minus Historiengebuehren weicht vom gemeldeten Broker-Ergebnis ab"
    return {
        "quality": quality, "reason": reason,
        "broker_reported_net_pnl": net, "broker_reported_fees": fees,
        "currency": row["waehrung"], "computed_gross_pnl": gross,
        "confirmed_entry_costs": known_entry, "computed_net_pnl": calculated_net,
        "arithmetic_delta": arithmetic_delta,
        "broker_vs_computed_net_delta": (net-calculated_net
            if net is not None and calculated_net is not None else None),
        "fee_scope_confirmed": False, "risk_release": False,
    }


def record_history(*, account, paper, position_id, entry_order_id, snapshot):
    """Idempotently project one full position's report using exact lineage.

    Partial or ambiguous history cannot be allocated by guessing. Both account
    and environment must be explicit on the already-authenticated snapshot.
    This receipt path never changes executed quantity, order IDs or risk P&L.
    """
    import trade_ledger as ledger
    if (not account or not isinstance(paper, bool) or not position_id or not entry_order_id
            or not isinstance(snapshot, dict) or snapshot.get("complete") is not True
            or snapshot.get("truncated") is True
            or snapshot.get("account_fingerprint") != account
            or snapshot.get("environment") != ("DEMO" if paper else "LIVE")
            or _stamp(snapshot.get("snapshot_at")) is None):
        raise ValueError("ETORO_HISTORY_RESULT_SNAPSHOT_UNPROVEN")
    matches = [r for r in snapshot.get("rows", []) if isinstance(r, dict)
               and str(r.get("positionId")) == str(position_id)]
    if len(matches) != 1:
        raise ValueError("ETORO_HISTORY_RESULT_FULL_POSITION_REQUIRED")
    item = matches[0]
    closed = _stamp(item.get("closeTimestamp"))
    opened = _stamp(item.get("openTimestamp"))
    if (str(item.get("orderId")) != str(entry_order_id)
            or item.get("isBuy") is not True or isinstance(item.get("leverage"), bool)
            or item.get("leverage") != 1
            or closed is None or opened is None or closed < opened
            or _stamp(snapshot["snapshot_at"]) < closed
            or any(finite_number(item.get(k)) is None or float(item[k]) <= 0
                   for k in ("units", "openRate", "closeRate"))):
        raise ValueError("ETORO_HISTORY_RESULT_IDENTITY_UNPROVEN")
    ledger.init_ledger()
    with ledger._LOCK, ledger._connect() as con:
        con.execute("BEGIN IMMEDIATE")
        rows = [dict(r) for r in con.execute("""SELECT * FROM trades WHERE broker='etoro'
            AND broker_account_fingerprint=? AND paper=? AND broker_position_id=?
            AND entry_order_id=? AND superseded_by IS NULL""",
            (account, int(paper), str(position_id), str(entry_order_id)))]
        if len(rows) != 1:
            raise ValueError("ETORO_HISTORY_RESULT_LEDGER_AMBIGUOUS")
        row = rows[0]
        # Ledger timestamps can be legacy local wall-clock strings. Do not
        # reinterpret those as UTC; identity/quantity/prices are exact and
        # timestamp consistency is also checked wherever it has a UTC offset.
        ledger_close = _stamp(row.get("ausgestiegen_am"))
        if (not row.get("ausgestiegen_am") or row["waehrung"] != "USD"
                or not _same(row["menge"], item["units"])
                or not _same(row["einstieg_preis"], item["openRate"])
                or not _same(row["ausstieg_preis"], item["closeRate"])
                or ledger_close is not None and abs((closed-ledger_close).total_seconds()) > 0.001):
            raise ValueError("ETORO_HISTORY_RESULT_LEDGER_CONFLICT")
        result = assess(row, item)
        # Only fee/result/identity fields, never headers or request credentials.
        retained = {k: item[k] for k in (
            "positionId", "orderId", "instrumentId", "isBuy", "leverage", "units",
            "openRate", "closeRate", "openTimestamp", "closeTimestamp", "netProfit", "fees") if k in item}
        proof = {"source": "ETORO_AUTHENTICATED_TRADE_HISTORY", "account": account,
                 "environment": snapshot["environment"], "position_id": str(position_id),
                 "entry_order_id": str(entry_order_id), "row": retained, "assessment": result}
        encoded = json.dumps(proof, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
        digest = hashlib.sha256(encoded.encode()).hexdigest()
        con.execute("""CREATE TABLE IF NOT EXISTS etoro_history_result_receipts (
            trade_id INTEGER NOT NULL, receipt_hash TEXT NOT NULL, observed_at TEXT NOT NULL,
            receipt_json TEXT NOT NULL, PRIMARY KEY(trade_id,receipt_hash))""")
        con.execute("INSERT OR IGNORE INTO etoro_history_result_receipts VALUES (?,?,?,?)",
                    (row["trade_id"], digest, snapshot["snapshot_at"], encoded))
        changed = row.get("broker_result_receipt_hash") != digest
        if changed:
            con.execute("""UPDATE trades SET broker_reported_net_pnl=?,broker_reported_fees=?,
                broker_result_currency=?,broker_result_quality=?,broker_result_receipt_hash=?,
                broker_result_detail_json=? WHERE trade_id=?""",
                (result["broker_reported_net_pnl"], result["broker_reported_fees"], "USD",
                 result["quality"], digest, json.dumps(result, ensure_ascii=False), row["trade_id"]))
        return {**result, "updated": int(changed), "trade_id": row["trade_id"], "receipt_hash": digest}
