"""Durable, account-bound private eToro messages; hints, never fill authority.

Persist before attempting reconciliation so unmatched events survive restarts.
No status number is interpreted as a cancellation, fill, or TP/SL reason here.
"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3


def _path() -> Path:
    root = os.getenv("TRADINGBOT_TEST_STATE_DIR", "").strip()
    return (Path(root) if root else Path(__file__).resolve().parent) / "etoro_stream_inbox.sqlite"


def _connect():
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, timeout=10.0)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=FULL")
    con.executescript("""
    CREATE TABLE IF NOT EXISTS etoro_stream_events (
        event_key TEXT PRIMARY KEY, broker TEXT NOT NULL DEFAULT 'etoro',
        account_fingerprint TEXT NOT NULL, environment TEXT NOT NULL,
        received_at_utc TEXT NOT NULL, message_id TEXT NOT NULL,
        message_type TEXT NOT NULL, kind TEXT NOT NULL,
        position_id TEXT NOT NULL, instrument_id TEXT NOT NULL,
        validation TEXT NOT NULL, state TEXT NOT NULL,
        event_json TEXT NOT NULL, deliveries INTEGER NOT NULL DEFAULT 1,
        matched_json TEXT NOT NULL DEFAULT '[]', last_error TEXT NOT NULL DEFAULT '',
        processed_at_utc TEXT NOT NULL DEFAULT ''
    );
    CREATE INDEX IF NOT EXISTS etoro_stream_scope
        ON etoro_stream_events(account_fingerprint,environment,position_id,received_at_utc);
    """)
    return con


def _clean(value):
    if isinstance(value, dict):
        return {str(k): ("[REDACTED]" if re.sub(r"[^a-z]", "", str(k).lower()) in
                {"apikey", "userkey", "xapikey", "xuserkey", "authorization", "token", "accesstoken", "password", "secret"}
                else _clean(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [_clean(v) for v in value]
    if value is None or isinstance(value, (str, float, int, bool)):
        return value
    return str(value)


def field(content: dict, *names) -> str:
    for name in names:
        if content.get(name) not in (None, ""):
            return str(content[name])
    return ""


def event_kind(message_type: str) -> str:
    normalized = str(message_type).lower()
    if normalized == "trading.position.closed" or normalized.startswith("trading.orderforclose"):
        return "CLOSE"
    if normalized == "trading.position.opened" or normalized.startswith("trading.orderforopen"):
        return "OPEN"
    return "OTHER"


def persist_event(event: dict, *, account_fingerprint: str, paper: bool | None,
                  cid: str = "", replay: bool = False) -> dict:
    account = str(account_fingerprint or "")
    environment = "DEMO" if paper is True else "LIVE" if paper is False else "UNKNOWN"
    clean = _clean(event if isinstance(event, dict) else {})
    content = clean.get("content") if isinstance(clean.get("content"), dict) else {}
    scope_cid = str(cid or "")
    event_cid = field(content, "CID", "Cid", "cid", "clientId", "ClientID")
    expected = hashlib.sha256(f"etoro|{environment.lower()}|cid:{scope_cid}".encode()).hexdigest()[:24]
    valid = bool(account and scope_cid and environment != "UNKNOWN" and account == expected)
    validation = "VALID" if valid else "UNBOUND_SCOPE"
    if valid and event_cid and event_cid != scope_cid:
        validation = "ACCOUNT_MISMATCH"
    claimed_scope = clean.get("stream_scope") or {}
    if isinstance(claimed_scope, dict) and any(
            claimed_scope.get(k) not in (None, "", v)
            for k, v in (("account_fingerprint", account), ("environment", environment))):
        validation = "ACCOUNT_MISMATCH"
    clean["stream_scope"] = {"broker": "etoro", "account_fingerprint": account,
                             "environment": environment}
    raw = json.dumps(clean, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    message_id = str(clean.get("message_id") or "")
    message_type = str(clean.get("message_type") or "")
    # Identical broker event redelivery has the same identity despite receive
    # time/session changes; conflicting content under the same ID is retained.
    identity = json.dumps([account, environment, message_id, message_type, content], sort_keys=True, separators=(",", ":"))
    event_key = hashlib.sha256(identity.encode()).hexdigest()
    if len(raw.encode()) > 262144:
        validation = "OVERSIZE"
        raw = json.dumps({"payload_sha256": hashlib.sha256(raw.encode()).hexdigest(), "omitted": "oversize"})
    state = "PENDING" if validation == "VALID" else "QUARANTINED"
    now = datetime.now(timezone.utc).isoformat()
    with closing(_connect()) as con, con:
        con.execute("""INSERT INTO etoro_stream_events
            (event_key,account_fingerprint,environment,received_at_utc,message_id,
             message_type,kind,position_id,instrument_id,validation,state,event_json)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(event_key) DO UPDATE SET deliveries=deliveries+?""", (
                event_key, account, environment, str(clean.get("received_at_utc") or now),
                message_id, message_type, event_kind(message_type),
                field(content, "PositionID", "PositionId", "positionId", "positionID"),
                field(content, "InstrumentID", "InstrumentId", "instrumentId", "instrumentID"),
                validation, state, raw, 0 if replay else 1))
        return dict(con.execute("SELECT event_key,validation,state FROM etoro_stream_events WHERE event_key=?", (event_key,)).fetchone())


def mark_processed(event_key: str, *, matched=(), error: str = "") -> None:
    ids = sorted(set(int(v) for v in matched if int(v) > 0))
    state = "ERROR" if error else "MATCHED" if ids else "UNMATCHED"
    with closing(_connect()) as con, con:
        # Redelivery cannot downgrade a previous successful matching receipt.
        con.execute("""UPDATE etoro_stream_events SET
            state=CASE WHEN state='MATCHED' AND ?='UNMATCHED' THEN state ELSE ? END,
            matched_json=CASE WHEN state='MATCHED' AND ?='UNMATCHED' THEN matched_json ELSE ? END,
            last_error=?,processed_at_utc=? WHERE event_key=? AND validation='VALID'""",
            (state, state, state, json.dumps(ids), str(error)[:300], datetime.now(timezone.utc).isoformat(), event_key))


def _read(*, account_fingerprint: str, paper: bool, position_id: str = "", limit: int = 50,
          closure: bool = False, pending: bool = False) -> list[dict]:
    path = _path()
    if not path.exists():
        return []
    where = "account_fingerprint=? AND environment=? AND validation='VALID'"
    args: list = [str(account_fingerprint), "DEMO" if paper else "LIVE"]
    if position_id:
        where += " AND position_id=?"
        args.append(str(position_id))
    if closure:
        where += " AND kind='CLOSE'"
    if pending:
        where += " AND state IN ('PENDING','UNMATCHED','ERROR')"
    args.append(max(1, min(200, int(limit))))
    with closing(_connect()) as con:
        ordering = "processed_at_utc ASC,received_at_utc ASC" if pending else "received_at_utc DESC"
        rows = con.execute("SELECT event_key,event_json FROM etoro_stream_events WHERE " + where + " ORDER BY " + ordering + " LIMIT ?", args).fetchall()
    return [{**json.loads(r["event_json"]), "event_key": r["event_key"]} for r in rows]


def closure_candidates(*, account_fingerprint: str, paper: bool, position_id: str,
                       limit: int = 50) -> list[dict]:
    """Bounded lookup hints only. Every order candidate still requires REST."""
    if not position_id:
        return []
    return _read(account_fingerprint=account_fingerprint, paper=paper,
                 position_id=position_id, limit=limit, closure=True)


def closure_order_hints(*, account_fingerprint: str, paper: bool, instrument_id: str,
                        limit: int = 50) -> list[dict]:
    """IDs to LOOK UP, never a position/order ownership inference.

    Documented OrderForCloseMultiple messages can omit PositionID. Keep their
    scope-verified IDs available for bounded REST lookup; the REST response
    must independently prove the exact closed position, execution and costs.
    """
    if not instrument_id or not isinstance(paper, bool) or not _path().exists():
        return []
    with closing(_connect()) as con:
        rows = con.execute("""SELECT event_key,event_json FROM etoro_stream_events
            WHERE account_fingerprint=? AND environment=? AND validation='VALID'
            AND instrument_id=? AND kind='CLOSE'
            AND message_type LIKE 'Trading.OrderForClose%'
            ORDER BY received_at_utc DESC LIMIT ?""",
            (str(account_fingerprint), "DEMO" if paper else "LIVE", str(instrument_id),
             max(1,min(50,int(limit))))).fetchall()
    return [{**json.loads(r["event_json"]), "event_key":r["event_key"]} for r in rows]


def replay_pending(*, account_fingerprint: str, paper: bool, limit: int = 50) -> list[dict]:
    return _read(account_fingerprint=account_fingerprint, paper=paper, limit=limit, pending=True)


def metrics(*, account_fingerprint: str, paper: bool) -> dict:
    if not _path().exists():
        return {"received": 0, "unique": 0, "matched": 0, "unmatched": 0, "errors": 0, "quarantined": 0}
    with closing(_connect()) as con:
        row = con.execute("""SELECT COALESCE(SUM(deliveries),0) received, COUNT(*) AS unique_events,
            COALESCE(SUM(state='MATCHED'),0) matched,
            COALESCE(SUM(state IN ('UNMATCHED','PENDING')),0) unmatched,
            COALESCE(SUM(state='ERROR'),0) errors,
            COALESCE(SUM(state='QUARANTINED'),0) quarantined,
            MAX(received_at_utc) last_received_at_utc, MAX(processed_at_utc) last_processed_at_utc
            FROM etoro_stream_events WHERE account_fingerprint=? AND environment=?""",
            (str(account_fingerprint), "DEMO" if paper else "LIVE")).fetchone()
    result = dict(row)
    result["unique"] = result.pop("unique_events")
    return result
