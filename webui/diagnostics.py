"""Bounded read-only projections. Observations never grant order authority.

No broker instances, network probes, schema creation or accounting writes are
allowed here. Missing evidence remains UNKNOWN; timestamps are aged on read.
"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import json
import math
import sqlite3

from provider_safety import redact

ORDER_FIELDS = frozenset({"broker", "account", "environment", "instrument", "side",
    "position_id", "client_id", "order_id", "requested", "state", "terminal",
    "evidence_complete", "filled", "net_filled", "accounted", "created_at",
    "updated_at", "last_checked_at", "check_detail"})
EVENT_FIELDS = frozenset({"filled", "terminal", "evidence_complete", "order_id",
    "proof", "final_payload_committed", "old_state", "new_state", "reason", "source",
    "position_closed", "order_outcome", "detail"})


def _now():
    return datetime.now(timezone.utc)


def _dict(value):
    return value if isinstance(value, dict) else {}


def _age(value, now):
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            return None  # no silent local/UTC conversion of ambiguous old data
        age = (now-stamp).total_seconds()
        return round(age, 1) if age >= -5 else None
    except (ValueError, TypeError, OverflowError):
        return None


def _status(state="UNKNOWN", detail="Kein separater Zustandsbeleg vorhanden.",
            observed_at=None, *, now=None, reason_code="", **extra):
    return {"state": state, "detail": redact(detail), "observed_at": observed_at,
        "age_seconds": _age(observed_at, now or _now()), "reason_code": reason_code, **extra}


def _rest(components, runtime, now):
    private = _dict(_dict(_dict(components.get("observations")).get("rest")).get("private"))
    if not private:
        return _status(reason_code="REST_NOT_OBSERVED")
    stamp = private.get("last_error_at") if private.get("state") == "ERROR" else private.get("last_success_at")
    age = _age(stamp, now)
    state = str(private.get("state") or "UNKNOWN")
    if state not in {"OK", "ERROR"}:
        state = "UNKNOWN"
    if state == "OK" and (age is None or age > 180):
        state = "STALE"
    detail = {"OK": "Letzter authentifizierter REST-Aufruf erfolgreich; kein Orderfreigabebeweis.",
        "ERROR": "Letzter authentifizierter REST-Aufruf fehlgeschlagen; Einzelheiten im Brokerprotokoll.",
        "STALE": "Letzter REST-Erfolg ist nicht mehr aktuell.",
        "UNKNOWN": "Kein bestätigter REST-Aufruf vorhanden."}[state]
    if state == "ERROR" and private.get("transport_state") == "OK":
        detail = "Broker hat per REST geantwortet; Anfrage oder Antwortvalidierung ist fehlgeschlagen. Das ist kein Beleg für einen Verbindungsabbruch."
    return _status(state, detail, stamp, now=now,
        reason_code=str(private.get("error_code") or ""),
        last_attempt_at=private.get("last_attempt_at"),
        last_success_at=private.get("last_success_at"), in_flight=private.get("in_flight"),
        transport_state=private.get("transport_state"), http_status=private.get("http_status"),
        latency_ms=private.get("latency_ms"))


def _websocket(components, runtime, now):
    stream = _dict(runtime.get("etoro_private_stream") or runtime.get("position_stream"))
    connected = stream.get("connected", components.get("ws_connected"))
    authenticated = stream.get("authenticated", components.get("ws_authenticated"))
    if connected is None:
        return _status(reason_code="WEBSOCKET_NOT_OBSERVED")
    stamp = runtime.get("last_heartbeat")
    if _age(stamp, now) is None or _age(stamp, now) > 180:
        return _status("STALE", "WebSocket-Zustand stammt aus einem alten Laufzeitstand.", stamp, now=now)
    state = "OK" if connected is True and authenticated is True else "WARN"
    detail = ("Privatstream verbunden und authentifiziert. REST-Abgleich bleibt erforderlich."
        if state == "OK" else "Privatstream getrennt oder nicht authentifiziert. REST-Zustand separat prüfen; keine Aussage über einen Orderfill.")
    return _status(state, detail, stamp, now=now,
        last_message_at=stream.get("last_message") or components.get("ws_last_message"),
        reason_code="" if state == "OK" else "PRIVATE_STREAM_UNAVAILABLE")


def _protection(runtime, now):
    health = _dict(_dict(runtime.get("functional_health")).get("protection"))
    if not health:
        return _status(detail="Kein eigener Zeitbeleg für den Schutzlauf. Einzelne Stops und Exitstatus stehen unter Handel.")
    stamp = health.get("last_completed_at")
    age = _age(stamp, now)
    state = health.get("state", "UNKNOWN")
    if state not in {"OK", "WARN", "ERROR", "UNKNOWN"}:
        state = "UNKNOWN"
    if state == "OK" and (age is None or age > 180):
        state = "STALE"
    return _status(state, "Letzter Positionsschutzlauf; bestätigt weder die Schutzorder jedes einzelnen Trades noch dessen Ausführung.",
        stamp, now=now, checked_positions=health.get("checked_positions"), errors=health.get("errors"),
        running=health.get("running"), duration_ms=health.get("duration_ms"))


def operations_snapshot(payload, *, now=None):
    """Build from one dashboard snapshot; no new GET, disk or provider call."""
    now = now or _now()
    result = {"schema_version": 1, "as_of": now.isoformat(), "brokers": {}}
    for broker in ("etoro", "okx"):
        runtime = _dict(payload.get(broker))
        context = _dict(_dict(payload.get("broker_contexts")).get(broker))
        components = _dict(runtime.get("connection_components"))
        stamp = runtime.get("last_heartbeat")
        age = _age(stamp, now)
        alive = runtime.get("worker_alive") is True and age is not None and age <= 180
        bound = (bool(context.get("account")) and context.get("observed_environment") in {"DEMO", "LIVE"}
                 and not context.get("mode_mismatch"))
        detail = _dict(runtime.get("stock_trading_ready")) if broker == "etoro" else _dict(payload.get("okx_detail"))
        observations = _dict(components.get("observations"))
        accounts = [runtime.get("account_fingerprint"), components.get("account_fingerprint"), detail.get("account_fingerprint")]
        from broker_display_context import environment as observed_environment
        environments = [observed_environment(x) for x in (runtime, components, observations, detail)]
        conflict = (any(str(x) != str(context.get("account")) for x in accounts if x)
            or any(x != context.get("observed_environment") for x in environments if x != "UNKNOWN"))
        bound = bound and not conflict
        reason = detail.get("grund") or detail.get("bereitschaft_grund") or "Keine aktuelle Kaufbereitschaft übermittelt."
        allowed = (alive and runtime.get("online") is True and bound
            and detail.get("kaeufe_erlaubt") is True and not detail.get("werte_veraltet")
            and _dict(payload.get("bot")).get("zustand") == "aktiv")
        if allowed and not (detail.get("grund") or detail.get("bereitschaft_grund")):
            reason = "Aktueller Handelskern meldet Kaufbereitschaft. Jede Order wird vor Versand erneut geprüft."
        if not alive:
            reason = "Handelskern nicht aktuell; gespeicherte Bereitschaft gilt nicht als Freigabe."
        elif not bound:
            reason = "Konto oder Laufzeitumgebung nicht eindeutig bestätigt."
        elif _dict(payload.get("bot")).get("zustand") != "aktiv":
            reason = "Neue Käufe sind in der Kaufsteuerung pausiert oder unklar."
        # Preserve every separately observed blocker, including a risk review
        # hidden behind a market-closed headline. Presentation cannot clear it.
        risk_review = _dict(runtime.get("risk_review"))
        blockers = []
        for value in detail.get("offen") or detail.get("offene_bedingungen") or []:
            if isinstance(value, str) and value:
                blockers.append(value)
        for condition in detail.get("bedingungen") or []:
            if isinstance(condition, dict) and condition.get("erfuellt") is False:
                blockers.append(str(condition.get("detail") or condition.get("beschreibung") or "Offene Kaufbedingung"))
        if risk_review.get("blocks_entries") is True:
            if allowed:
                reason = str(risk_review.get("detail") or "Risikobasis und Kontozuordnung müssen geprüft werden.")
            allowed = False
            blockers.append(str(risk_review.get("detail") or "Risikobasis und Kontozuordnung müssen geprüft werden."))
        risk_gate = _dict(runtime.get("risk_manager_buy_gate"))
        if risk_gate.get("blocked") is True:
            allowed = False
            reason = str(risk_gate.get("reason") or "Der Risikomanager sperrt neue Käufe.")
            blockers.append(reason)
        pending_operations = _dict(runtime.get("etoro_open_operations")) if broker == "etoro" else {}
        operations_bound = (pending_operations.get("account_fingerprint") == context.get("account")
            and pending_operations.get("environment") == context.get("observed_environment"))
        if pending_operations and (not operations_bound or pending_operations.get("complete") is not True
                                   or pending_operations.get("blocks_entries") is True):
            allowed = False
            reason = (str(pending_operations.get("detail") or "Offene eToro-Aufträge benötigen Abschlussbelege.")
                      if operations_bound else "Kontozuordnung der offenen Aufträge ungeklärt.")
            blockers.append(reason)
        if not allowed:
            blockers.insert(0, str(reason))
        blockers = list(dict.fromkeys(redact(value) for value in blockers))[:30]
        buy = _status("ALLOWED" if allowed else "BLOCKED", reason, stamp, now=now,
            reason_code="" if allowed else f"{broker.upper()}_BUY_GATE_CLOSED", blockers=blockers,
            risk_review=risk_review, risk_manager_gate=risk_gate)
        # The core deliberately allows exits while buys are paused. There is no
        # broker-wide SELL authorization receipt: do not fabricate one from OK REST.
        sell = _status("UNKNOWN", "Verkäufe werden je Position anhand Zuordnung, Exitstatus und Brokerbelegen geprüft; eine Kaufsperre ist keine Verkaufssperre.", stamp, now=now)
        if not alive:
            sell = _status("STALE", "Kein aktueller Handelskernbeleg. Bereits beim Broker liegende Schutzorders können weiterhin wirksam sein.", stamp, now=now)
        elif _dict(payload.get("bot")).get("zustand") in {"gestoppt", "beendet"}:
            sell = _status("BLOCKED", "Handelskern angehalten. Bestehende Brokerschutzorders separat prüfen.", stamp, now=now)
        recon = _status(detail="Kein vollständiger Abgleichbeleg. Eine leere Klärungsliste beweist keinen Depotabgleich.")
        if broker == "etoro":
            r = _dict(payload.get("etoro_reconciliation"))
            if r.get("error"):
                recon = _status("ERROR", "Abgleichdaten nicht lesbar.", reason_code="ETORO_RECONCILIATION_UNREADABLE")
            elif pending_operations and (not operations_bound or pending_operations.get("complete") is not True):
                recon = _status("UNKNOWN", "Offene Aufträge nicht vollständig dem aktuellen Konto zugeordnet.")
            elif pending_operations.get("pending"):
                recon = _status("WARN", str(pending_operations.get("detail")), stamp, now=now,
                    reason_code="ETORO_OPEN_OPERATIONS", unresolved_count=pending_operations["pending"],
                    pending_operations=pending_operations.get("rows", []))
            elif r.get("active"):
                recon = _status("WARN", "Ungeklärte Aufträge vorhanden. Genaue Identität und Sperrwirkung stehen unter Handel → Klärung.", stamp, now=now,
                    reason_code="ETORO_RECONCILIATION_REQUIRED", unresolved_count=len(r["active"]))
        else:
            conditions = detail.get("offene_bedingungen") or []
            if any("reconcil" in str(x).lower() or "abgleich" in str(x).lower() for x in conditions):
                recon = _status("WARN", "Kaufbereitschaft meldet einen offenen Abgleich. Details unter Handel → Klärung.", stamp, now=now,
                    reason_code="OKX_RECONCILIATION_REQUIRED")
        channels = {"rest": _rest(components, runtime, now), "websocket": _websocket(components, runtime, now),
            "reconciliation": recon, "protection": _protection(runtime, now)}
        if conflict:
            channels = {key: _status("UNKNOWN", "Laufzeit- und Kontokontext widersprechen sich; diesen Zustandsbeleg nicht dem angezeigten Konto zuordnen.",
                reason_code="BROKER_CONTEXT_MISMATCH") for key in channels}
            sell = _status("UNKNOWN", "Kontokontext widersprüchlich; zuerst Zuordnung prüfen.", reason_code="BROKER_CONTEXT_MISMATCH")
        result["brokers"][broker] = {"broker": broker, "account": context.get("account"),
            "environment": context.get("observed_environment"), "source": "persisted_core_observations",
            "buy": buy, "sell": sell, **channels}
    return result


def _connection():
    # decision_analytics performs schema setup at cold import. A diagnosis GET
    # must not import that writer merely to resolve the same database path.
    import os
    import sys
    from pathlib import Path
    import config
    from ledger_database_scope import current_path
    loaded = sys.modules.get("decision_analytics")
    path = current_path()
    if path is None:
        path = (loaded.db_pfad() if loaded is not None else
            Path(os.getenv("TRADINGBOT_TEST_STATE_DIR", "").strip() or Path(__file__).resolve().parents[1])
            / getattr(config, "DECISION_DB_FILE", "decision_history.sqlite"))
    if not path.is_file():
        raise FileNotFoundError("EXECUTION_DATABASE_NOT_PRESENT")
    con = sqlite3.connect(path.resolve().as_uri()+"?mode=ro", uri=True, timeout=2)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA query_only=ON")
    # Bound costly historical scans on an unexpectedly large Pi database.
    import time
    deadline = time.monotonic()+1.5
    con.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
    return con


def _order(row):
    return {k: redact(v) if isinstance(v, str) else v for k, v in dict(row).items() if k in ORDER_FIELDS}



def _fees_for_orders(con, rows):
    """Fee evidence is independent from quantity accounting; bounded read only."""
    keys = [row["order_key"] for row in rows]
    if not keys:
        return {}
    marks = ",".join("?" for _ in keys)
    fills = con.execute("SELECT order_key,quantity,fee,fee_currency FROM execution_fills WHERE order_key IN (" + marks + ") LIMIT 5001", keys).fetchall()
    if len(fills) > 5000:
        return {key: {"status": "UNKNOWN", "detail": "Gebührenprüfung überschreitet das Anzeigelimit."} for key in keys}
    grouped = {key: [] for key in keys}
    for fill in fills:
        grouped[fill["order_key"]].append(dict(fill))
    result = {}
    for row in rows:
        values = grouped[row["order_key"]]
        known = bool(values)
        for fill in values:
            try:
                known = known and fill["fee"] is not None and math.isfinite(float(fill["fee"])) and bool(fill["fee_currency"])
            except (TypeError, ValueError, OverflowError):
                known = False
        # A complete quantity proof is needed before calling all fee rows complete.
        known = known and bool(row["evidence_complete"])
        result[row["order_key"]] = {"status": "KNOWN" if known else "INCOMPLETE" if values else "UNKNOWN",
            "fill_count": len(values), "detail": "Gebühren zu allen gespeicherten Ausführungen belegt; bestätigt keine vollständige Netto-P&L-Berechnung." if known else "Ausführungszuordnung kann stimmen, obwohl Gebühren oder ihre Währung noch fehlen."}
    return result

def execution_history(*, broker="", page=1, limit=20, active_only=False):
    if broker not in {"", "etoro", "okx"}:
        raise ValueError("Unbekannter Broker")
    page, limit = max(1, int(page)), max(1, min(100, int(limit)))
    result = {"orders": [], "page": page, "pages": 0, "total": None,
        "limit": limit, "as_of": _now().isoformat(), "complete": False}
    clauses, args = [], []
    if broker:
        clauses.append("broker=?"); args.append(broker)
    if active_only:
        clauses.append("(terminal=0 OR evidence_complete=0 OR (CAST(filled AS REAL)>0 AND accounted=0))")
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    try:
        with closing(_connection()) as con:
            con.execute("BEGIN")  # total and rows refer to one SQLite read snapshot
            total = int(con.execute("SELECT COUNT(*) FROM execution_orders"+where, args).fetchone()[0])
            rows = con.execute("SELECT * FROM execution_orders"+where+
                " ORDER BY created_at DESC,order_key DESC LIMIT ? OFFSET ?", [*args, limit, (page-1)*limit]).fetchall()
            fees = _fees_for_orders(con, rows)
            result.update(orders=[dict(_order(row), fee_evidence=fees.get(row["order_key"], {})) for row in rows], total=total,
                pages=math.ceil(total/limit), complete=True)
    except (OSError, sqlite3.Error):
        result["error"] = "EXECUTION_HISTORY_UNAVAILABLE"
    return result


def execution_detail(*, broker, account, environment, client_id):
    if broker not in {"okx", "etoro"} or environment not in {"DEMO", "LIVE"}:
        raise ValueError("Vollständige Broker-/Umgebungsidentität erforderlich")
    if not all(isinstance(x, str) and 0 < len(x) <= 256 for x in (account, client_id)):
        raise ValueError("Vollständige Konto-/Clientidentität erforderlich")
    with closing(_connection()) as con:
        con.execute("BEGIN")
        row = con.execute("SELECT * FROM execution_orders WHERE broker=? AND account=? AND environment=? AND client_id=?",
            (broker, account, environment, client_id)).fetchone()
        if row is None:
            raise LookupError("Order in diesem Konto-/Umgebungskontext nicht gefunden")
        fills = con.execute("SELECT fill_id,quantity,price,fee,fee_currency FROM execution_fills WHERE order_key=? ORDER BY fill_id LIMIT 201", (row["order_key"],)).fetchall()
        events = con.execute("SELECT id,kind,created_at,detail_json FROM execution_events WHERE order_key=? ORDER BY id DESC LIMIT 201", (row["order_key"],)).fetchall()
        rendered_events = []
        for event in events[:200]:
            try:
                detail = _dict(json.loads(event["detail_json"]))
            except (ValueError, TypeError):
                detail = {}
            rendered_events.append({"id": event["id"], "kind": redact(event["kind"]), "created_at": event["created_at"],
                "detail": {k: redact(v) if isinstance(v, str) else v for k, v in detail.items()
                    if k in EVENT_FIELDS and isinstance(v, (str, int, float, bool, type(None)))}})
        fee_evidence = _fees_for_orders(con, [row]).get(row["order_key"], {})
        return {"order": dict(_order(row), fee_evidence=fee_evidence), "fills": [dict(x) for x in fills[:200]],
            "events": rendered_events, "limits": {"fills": 200, "events": 200},
            "truncated": {"fills": len(fills)>200, "events": len(events)>200},
            "as_of": _now().isoformat()}


def ai_diagnostics():
    from ai_router import request_diagnostics
    source = request_diagnostics(limit=20)
    records = [{"task": row.get("aufgabe"), "symbol": row.get("instrumente"),
        "model": row.get("modell"), "reason": redact(row.get("grund")),
        "execution": _dict(row.get("execution"))} for row in source.get("recent_executions", [])]
    return {"records": records, "summary": {"state": "OBSERVED" if records else "UNKNOWN",
        "detail": "Lokale Aufrufbelege; kein Erreichbarkeitstest. Ältere Aufrufe ohne Telemetrie sind nicht rekonstruierbar."},
        "as_of": _now().isoformat()}
