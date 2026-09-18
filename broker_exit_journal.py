"""Durable Exit-Intent-Journal fuer eToro und OKX.

Ein Broker-POST ist kein atomarer Vorgang mit den lokalen JSON-Dateien. Darum
wird die Absicht *vor* dem POST in SQLite gespeichert und bleibt bei unklarem
Transportzustand aktiv. Ein Neustart darf fuer dieselbe Brokerposition erst
nach eindeutigem Terminalbeweis einen neuen Exit erzeugen.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json
import hashlib
import logging
import math
import os
import sqlite3
import threading
import uuid


_LOCK = threading.RLock()
ACTIVE = ("SUBMITTING", "SUBMITTED", "UNCLEAR", "PARTIALLY_FILLED")
POSITION_CLOSED_ORDER_UNPROVEN = "POSITION_CLOSED_ORDER_UNPROVEN"


def _path() -> Path:
    root = os.getenv("TRADINGBOT_TEST_STATE_DIR", "").strip()
    base = Path(root) if root else Path(__file__).resolve().parent
    try:
        import config
        configured = str(getattr(config, "BROKER_EXIT_JOURNAL_FILE", "") or "")
        if configured:
            value = Path(configured)
            return value if value.is_absolute() else base / value
    except Exception as exc:
        logging.getLogger(__name__).debug(
            "Konfigurierter Exit-Journal-Pfad nicht lesbar; Standardpfad wird genutzt: %s",
            exc)
    return base / "broker_exit_journal.sqlite"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect():
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, timeout=20.0)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=FULL")
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS broker_exit_intents (
            intent_id TEXT PRIMARY KEY,
            broker TEXT NOT NULL,
            account_fingerprint TEXT NOT NULL,
            environment TEXT NOT NULL DEFAULT '',
            instrument_id TEXT NOT NULL,
            position_id TEXT NOT NULL,
            client_order_id TEXT NOT NULL DEFAULT '',
            reference_id TEXT NOT NULL DEFAULT '',
            broker_order_id TEXT NOT NULL DEFAULT '',
            requested_quantity REAL NOT NULL,
            filled_quantity REAL NOT NULL DEFAULT 0,
            status TEXT NOT NULL,
            reason TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            detail_json TEXT NOT NULL DEFAULT '{}'
        );
        CREATE INDEX IF NOT EXISTS idx_exit_active
          ON broker_exit_intents(
            broker, account_fingerprint, environment, instrument_id,
            position_id, status);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_exit_client_identity
          ON broker_exit_intents(broker, account_fingerprint, client_order_id)
          WHERE client_order_id <> '';
        CREATE TABLE IF NOT EXISTS broker_exit_fill_events (
            intent_id TEXT NOT NULL,
            fill_identity TEXT NOT NULL,
            quantity REAL NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY(intent_id, fill_identity),
            FOREIGN KEY(intent_id) REFERENCES broker_exit_intents(intent_id)
        );
        CREATE TABLE IF NOT EXISTS broker_exit_reconciliation_events (
            event_key TEXT PRIMARY KEY, intent_id TEXT NOT NULL,
            kind TEXT NOT NULL, created_at TEXT NOT NULL, detail_json TEXT NOT NULL
        );
        """)
    con.execute("BEGIN IMMEDIATE")
    columns = {r[1] for r in con.execute("PRAGMA table_info(broker_exit_fill_events)")}
    if "broker_order_id" not in columns:
        con.execute("ALTER TABLE broker_exit_fill_events ADD COLUMN broker_order_id TEXT NOT NULL DEFAULT ''")
    con.commit()
    return con


def begin(*, broker: str, account_fingerprint: str, environment: str,
          instrument_id: str, position_id: str, quantity: float,
          reason: str = "", intent_id: str = "",
          client_order_id: str = "") -> tuple[dict, bool]:
    """Aktiven Intent wiedergeben oder genau einen neuen persistent anlegen."""
    with _LOCK, _connect() as con:
        con.execute("BEGIN IMMEDIATE")
        # A proven closed position must never acquire another close intent.
        # An explicitly cancelled eToro operation requires a new user decision,
        # not an automatic retry with a new request ID.
        blocking = (*ACTIVE, POSITION_CLOSED_ORDER_UNPROVEN, "CANCELED") if str(broker).lower() == "etoro" else ACTIVE
        marks = ",".join("?" for _ in blocking)
        row = con.execute(
            f"""SELECT * FROM broker_exit_intents
                WHERE broker=? AND account_fingerprint=? AND environment=?
                  AND instrument_id=? AND position_id=?
                  AND status IN ({marks})
                ORDER BY created_at DESC LIMIT 1""",
            (str(broker).lower(), str(account_fingerprint), str(environment),
             str(instrument_id), str(position_id), *blocking)).fetchone()
        if row:
            con.commit()
            return dict(row), False
        now = _now()
        iid = str(intent_id or uuid.uuid4().hex)
        con.execute(
            """INSERT INTO broker_exit_intents
               (intent_id, broker, account_fingerprint, environment,
                instrument_id, position_id, client_order_id,
                requested_quantity, status, reason, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (iid, str(broker).lower(), str(account_fingerprint), str(environment),
             str(instrument_id), str(position_id), str(client_order_id),
             float(quantity), "SUBMITTING", str(reason)[:300], now, now))
        con.commit()
        return dict(con.execute(
            "SELECT * FROM broker_exit_intents WHERE intent_id=?", (iid,)).fetchone()), True


def update(intent_id: str, status: str, *, reference_id: str = "",
           broker_order_id: str = "", filled_quantity=None,
           detail: dict | None = None) -> bool:
    with _LOCK, _connect() as con:
        cur = con.execute(
            """UPDATE broker_exit_intents SET status=?,
                   reference_id=CASE WHEN ?<>'' THEN ? ELSE reference_id END,
                   broker_order_id=CASE WHEN ?<>'' THEN ? ELSE broker_order_id END,
                   filled_quantity=COALESCE(?, filled_quantity),
                   detail_json=CASE WHEN ?<>'' THEN ? ELSE detail_json END,
                   updated_at=? WHERE intent_id=?""",
            (str(status).upper(), str(reference_id), str(reference_id),
             str(broker_order_id), str(broker_order_id),
             None if filled_quantity is None else float(filled_quantity),
             json.dumps(detail or {}, ensure_ascii=False) if detail else "",
             json.dumps(detail or {}, ensure_ascii=False) if detail else "",
             _now(), str(intent_id)))
        return bool(cur.rowcount)


def active(*, broker: str = "", account_fingerprint: str = "") -> list[dict]:
    with _LOCK, _connect() as con:
        clauses = ["status IN (%s)" % ",".join("?" for _ in ACTIVE)]
        params: list[object] = list(ACTIVE)
        if broker:
            clauses.append("broker=?"); params.append(str(broker).lower())
        if account_fingerprint:
            clauses.append("account_fingerprint=?"); params.append(str(account_fingerprint))
        rows = con.execute(
            "SELECT * FROM broker_exit_intents WHERE " + " AND ".join(clauses)
            + " ORDER BY created_at", tuple(params)).fetchall()
        return [dict(row) for row in rows]


def confirm_from_fill(*, broker: str, account_fingerprint: str,
                      position_id: str, broker_order_id: str = "",
                      filled_quantity: float = 0.0, detail: dict | None = None,
                      fill_identity: str = "", environment: str = "",
                      instrument_id: str = "") -> str:
    """Genau passenden Intent fill-genau und wiederholbar fortschreiben.

    Ein einzelner Teilfill ist noch kein terminal geschlossener Auftrag. Die
    stabile Fill-Identitaet verhindert ausserdem, dass derselbe Brokerbeleg bei
    jedem Poll erneut zur kumulierten Menge addiert wird.
    """
    etoro = str(broker).lower() == "etoro"
    # History identifies a closed POSITION. Its generic orderId is the entry
    # order, and an absent close-order ID is never permission to guess one.
    if etoro and not str(broker_order_id or "").strip():
        return ""
    quantity = float(filled_quantity or 0.0)
    if not math.isfinite(quantity) or quantity <= 0:
        raise ValueError("Exit-Fill ohne positive endliche Menge")
    with _LOCK, _connect() as con:
        con.execute("BEGIN IMMEDIATE")
        states = (*ACTIVE, POSITION_CLOSED_ORDER_UNPROVEN) if etoro else ACTIVE
        marks = ",".join("?" for _ in states)
        clauses = ["broker=?", "account_fingerprint=?", "position_id=?",
                   f"status IN ({marks})"]
        params: list[object] = [str(broker).lower(), str(account_fingerprint),
                               str(position_id), *states]
        if broker_order_id:
            clauses.append("broker_order_id=?" if etoro else "(broker_order_id='' OR broker_order_id=?)")
            params.append(str(broker_order_id))
        if environment:
            clauses.append("environment=?"); params.append(str(environment).upper())
        if instrument_id:
            clauses.append("instrument_id=?"); params.append(str(instrument_id))
        rows = con.execute(
            "SELECT * FROM broker_exit_intents WHERE " + " AND ".join(clauses)
            + " ORDER BY created_at DESC", tuple(params)).fetchall()
        if len(rows) != 1:
            con.commit()
            return ""
        row = rows[0]
        intent_id = str(row["intent_id"])
        identity = str(fill_identity or "").strip()[:500]
        if identity and quantity > 0:
            previous = con.execute("SELECT quantity,broker_order_id FROM broker_exit_fill_events WHERE intent_id=? AND fill_identity=?", (intent_id, identity)).fetchone()
            if previous and (float(previous["quantity"]) != quantity or (previous["broker_order_id"] and previous["broker_order_id"] != str(broker_order_id))):
                raise ValueError("Exit-Fill widerspricht gespeichertem Ausfuehrungsbeleg")
            con.execute(
                """INSERT OR IGNORE INTO broker_exit_fill_events
                   (intent_id, fill_identity, quantity, created_at, broker_order_id)
                   VALUES (?,?,?,?,?)""",
                (intent_id, identity, quantity, _now(), str(broker_order_id)))
            # A newly received exact order receipt can enrich an old event.
            if etoro:
                con.execute("UPDATE broker_exit_fill_events SET broker_order_id=? WHERE intent_id=? AND fill_identity=? AND broker_order_id=''", (str(broker_order_id), intent_id, identity))
            total = float(con.execute(
                "SELECT COALESCE(SUM(quantity),0) FROM broker_exit_fill_events WHERE intent_id=?" + (" AND broker_order_id=?" if etoro else ""),
                (intent_id, str(broker_order_id)) if etoro else (intent_id,)).fetchone()[0] or 0.0)
        else:
            # Ohne stabile Ereignis-ID niemals addieren: wiederholte Polls
            # duerfen den Auftrag nicht kuenstlich vollstaendig machen.
            total = max(float(row["filled_quantity"] or 0.0), quantity)
        requested = max(0.0, float(row["requested_quantity"] or 0.0))
        tolerance = max(1e-9, requested * 1e-8)
        if total > requested + tolerance:
            raise ValueError("Exit-Fill uebersteigt beauftragte Menge")
        status = "FILLED" if requested > 0 and total + tolerance >= requested \
            else "PARTIALLY_FILLED"
        con.execute(
            """UPDATE broker_exit_intents SET status=?,
                   broker_order_id=CASE WHEN ?<>'' THEN ? ELSE broker_order_id END,
                   filled_quantity=?, detail_json=?, updated_at=?
               WHERE intent_id=?""",
            (status, str(broker_order_id), str(broker_order_id), total,
             json.dumps(detail or {}, ensure_ascii=False), _now(), intent_id))
        con.commit()
        return intent_id


def _utc(value):
    value = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if value.tzinfo is None:
        raise ValueError("Brokerbeleg ohne Zeitzone")
    return value.astimezone(timezone.utc)


def confirm_cancelled(*, account_fingerprint, environment, intent_id, broker_order_id, detail):
    """Record an exact explicit zero-fill cancellation without allowing retry."""
    status = detail.get("status")
    name = str((status.get("name") if isinstance(status, dict) else status if isinstance(status, str) else "") or detail.get("statusName") or "").upper()
    returned_oid = str(detail.get("orderId") or detail.get("orderID") or detail.get("closeOrderId") or "")
    cid = str(detail.get("CID") or detail.get("cid") or detail.get("accountId") or "")
    scoped_account = hashlib.sha256(f"etoro|{str(environment).lower()}|cid:{cid}".encode()).hexdigest()[:24] if cid else ""
    if (name not in {"CANCELED", "CANCELLED"} or returned_oid != str(broker_order_id)
            or not broker_order_id or detail.get("positions") != []
            or environment not in {"DEMO", "LIVE"} or scoped_account != account_fingerprint
            or detail.get("errorCode") not in (None, 0) or detail.get("errorMessage")
            or detail.get("proceeds") not in (None, 0, 0.0)):
        raise ValueError("ETORO_CANCEL_NOT_PROVEN")
    with _LOCK, _connect() as con:
        con.execute("BEGIN IMMEDIATE")
        row = con.execute("SELECT * FROM broker_exit_intents WHERE intent_id=? AND broker='etoro' AND account_fingerprint=? AND environment=? AND broker_order_id=?", (intent_id, account_fingerprint, environment, str(broker_order_id))).fetchone()
        if not row or float(row["filled_quantity"] or 0) != 0:
            return False
        evidence = json.dumps(detail, sort_keys=True, default=str)
        key = hashlib.sha256((intent_id + ":cancel:" + evidence).encode()).hexdigest()
        con.execute("INSERT OR IGNORE INTO broker_exit_reconciliation_events VALUES(?,?,?,?,?)", (key, intent_id, "CANCELED", _now(), json.dumps({"previous_intent": dict(row), "proof": detail}, sort_keys=True, default=str)))
        con.execute("UPDATE broker_exit_intents SET status='CANCELED',detail_json=?,updated_at=? WHERE intent_id=?", (evidence, _now(), intent_id))
        con.commit()
    from execution_lifecycle import observe_etoro_cancelled
    observe_etoro_cancelled(account=account_fingerprint, environment=environment,
        instrument=row["instrument_id"], position_id=row["position_id"],
        client_id=row["client_order_id"], order_id=str(broker_order_id), detail=detail)
    return True


def replay_cancelled_projections(*, account_fingerprint, environment):
    """Finish a previously committed cancellation after a second-DB failure."""
    if not _path().exists():
        return
    with _LOCK, _connect() as con:
        rows = con.execute("SELECT * FROM broker_exit_intents WHERE broker='etoro' AND account_fingerprint=? AND environment=? AND status='CANCELED'", (account_fingerprint, environment)).fetchall()
    for row in rows:
        confirm_cancelled(account_fingerprint=account_fingerprint, environment=environment,
            intent_id=row['intent_id'], broker_order_id=row['broker_order_id'],
            detail=json.loads(row['detail_json'] or '{}'))


def _order_anchor(detail):
    # Never include generic orderId: in position history it means the BUY.
    return str(detail.get("close_order_id") or detail.get("closeOrderId")
               or detail.get("closeOrderID") or detail.get("closingOrderId") or "")


def reconcile_position_closures(*, account_fingerprint: str, environment: str,
                               snapshot: dict, history: dict, now=None) -> list[dict]:
    """Release position exposure without inventing the old order's outcome.

    This is a repeatable recovery/migration, not a fill consumer. It requires
    fresh, complete, identically scoped portfolio AND history evidence. Native
    order fills remain separate. Every changed old projection is retained in
    the audit table, including any quantity formerly attributed without proof.
    """
    if not account_fingerprint or environment not in {"DEMO", "LIVE"}:
        raise ValueError("ETORO_CLOSE_SCOPE_UNPROVEN")
    now = now or datetime.now(timezone.utc)
    for evidence in (snapshot, history):
        if (evidence.get("complete") is not True or evidence.get("truncated")
                or evidence.get("account_fingerprint") != account_fingerprint
                or evidence.get("environment") != environment):
            raise ValueError("ETORO_CLOSE_SCOPE_UNPROVEN")
        age = (now - _utc(evidence.get("snapshot_at"))).total_seconds()
        if age < -5 or age > 120:
            raise ValueError("ETORO_CLOSE_EVIDENCE_STALE")
    if not snapshot.get("snapshot_id") or not isinstance(snapshot.get("rows"), list) or not isinstance(history.get("rows"), list):
        raise ValueError("ETORO_CLOSE_EVIDENCE_INCOMPLETE")
    open_ids = set()
    for row in snapshot["rows"]:
        if not isinstance(row, dict) or not row.get("positionId"):
            raise ValueError("ETORO_CLOSE_SNAPSHOT_INVALID")
        open_ids.add(str(row["positionId"]))
    for name in ("open_ids", "position_ids"):
        if name in snapshot and set(map(str, snapshot[name])) != open_ids:
            raise ValueError("ETORO_CLOSE_SNAPSHOT_CONTRADICTORY")
    projections = []
    with _LOCK, _connect() as con:
        con.execute("BEGIN IMMEDIATE")
        candidates = con.execute("""SELECT * FROM broker_exit_intents
            WHERE broker='etoro' AND account_fingerprint=? AND environment=?
              AND status IN ('SUBMITTING','SUBMITTED','UNCLEAR','PARTIALLY_FILLED',
                             'FILLED','POSITION_CLOSED_ORDER_UNPROVEN')""",
            (account_fingerprint, environment)).fetchall()
        for raw_intent in candidates:
            intent = dict(raw_intent)
            pid, iid, oid = intent["position_id"], intent["instrument_id"], intent["broker_order_id"]
            if not pid or not iid or pid in open_ids:
                continue
            requested = float(intent["requested_quantity"])
            if not math.isfinite(requested) or requested <= 0:
                continue
            matching, unique = [], {}
            invalid = False
            for row in history["rows"]:
                if not isinstance(row, dict) or str(row.get("positionId") or "") != pid:
                    continue
                try:
                    quantity = float(row.get("units"))
                    price = float(row.get("closeRate"))
                    closed = _utc(row.get("closeTimestamp"))
                    if (str(row.get("instrumentId") or "") != iid or row.get("isBuy") is not True
                            or not all(math.isfinite(x) and x > 0 for x in (quantity, price))
                            or closed < _utc(intent["created_at"])
                            or closed > _utc(snapshot["snapshot_at"])):
                        invalid = True
                        break
                    # Include native IDs when supplied; otherwise require the
                    # entire position/time/amount/rate economic receipt.
                    lower = {str(k).lower(): v for k, v in row.items()}
                    key = str(lower.get("executionid") or lower.get("positionexecutionid") or lower.get("dealid") or "")
                    identity = ("native", key) if key else ("economic", closed.isoformat(), quantity, price)
                    previous = unique.get(identity)
                    if previous and (float(previous["units"]) != quantity
                                     or float(previous["closeRate"]) != price
                                     or _utc(previous["closeTimestamp"]) != closed):
                        invalid = True
                        break
                    unique[identity] = dict(row)
                except (TypeError, ValueError, OverflowError):
                    invalid = True
                    break
            matching = [unique[k] for k in sorted(unique, key=str)]
            total = sum(float(r["units"]) for r in matching)
            if invalid or not matching or not math.isfinite(total) or total + max(1e-9, requested * 1e-8) < requested:
                continue
            detail = json.loads(intent["detail_json"] or "{}")
            # Preserve orders already confirmed with exact order evidence.
            proven_total = float(con.execute("SELECT COALESCE(SUM(quantity),0) FROM broker_exit_fill_events WHERE intent_id=? AND broker_order_id=? AND broker_order_id<>''", (intent["intent_id"], oid)).fetchone()[0])
            if (intent["status"] == "FILLED" and oid and
                    (_order_anchor(detail) == oid or proven_total + max(1e-9, requested * 1e-8) >= requested)):
                continue
            proof = {"schema": 1, "account_fingerprint": account_fingerprint,
                     "environment": environment, "instrument_id": iid,
                     "position_id": pid, "broker_order_id": oid,
                     "position_closed": True, "order_outcome": "UNPROVEN",
                     "native_order_filled_quantity": proven_total,
                     "position_closed_quantity": total,
                     "snapshot_id": snapshot["snapshot_id"],
                     "snapshot_at": snapshot["snapshot_at"],
                     "history_snapshot_at": history["snapshot_at"],
                     "history_rows": matching,
                     "reason": "Position geschlossen; Ausfuehrung des alten Schliessauftrags nicht belegt"}
            fingerprint = hashlib.sha256(json.dumps([intent["intent_id"], pid, iid, matching], sort_keys=True, default=str).encode()).hexdigest()
            audit = {"previous_intent": intent, "proof": proof}
            changed = con.execute("INSERT OR IGNORE INTO broker_exit_reconciliation_events VALUES (?,?,?,?,?)", (fingerprint, intent["intent_id"], POSITION_CLOSED_ORDER_UNPROVEN, _now(), json.dumps(audit, ensure_ascii=False, sort_keys=True, default=str))).rowcount
            if changed:
                con.execute("UPDATE broker_exit_intents SET status=?,filled_quantity=?,detail_json=?,updated_at=? WHERE intent_id=?", (POSITION_CLOSED_ORDER_UNPROVEN, proven_total, json.dumps(proof, ensure_ascii=False, sort_keys=True, default=str), _now(), intent["intent_id"]))
            # Repeat the second database projection after crashes; its own
            # audit key makes this safe even if the first commit already ran.
            projections.append({**proof, "client_order_id": intent["client_order_id"],
                                "event_key": fingerprint})
        con.commit()
    if projections:
        from execution_lifecycle import observe_etoro_position_closed
        for proof in projections:
            observe_etoro_position_closed(proof)
    return projections
