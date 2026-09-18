"""Decision analytics database for TradingBot v8.1.1 NEXUS.

Purpose:
- persist serious BUY candidates (approved and rejected),
- distinguish alpha/quality filters from hard safety filters,
- track whether an approved decision was submitted/filled,
- add forward price observations (1h/4h/1d/5d),
- persist broker heartbeat transitions and daily report state.

The database is analytics only. A write failure must never change the trading
result. All public write functions therefore fail safely and log the problem.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
from collections import Counter
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import config
from decision_snapshot import apply_snapshot, clean_error, new_decision_id
from ledger_database_scope import current_path as scoped_database_path

logger = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parent
_TEST_DIR = os.getenv("TRADINGBOT_TEST_STATE_DIR", "").strip()
DB_PATH = (Path(_TEST_DIR) if _TEST_DIR else ROOT) / getattr(config, "DECISION_DB_FILE", "decision_history.sqlite")
_LOCK = threading.RLock()
_CONNECTION_INIT_LOCK = threading.RLock()


def _local_zone():
    try:
        return ZoneInfo(str(getattr(config, "LOCAL_TIMEZONE", "Europe/Berlin")))
    except Exception:
        return datetime.now().astimezone().tzinfo or timezone.utc


def db_pfad() -> Path:
    """Der Pfad der Entscheidungsdatenbank -- beim Zugriff bestimmt, nicht beim Import.

    v8.1.5: ``DB_PATH`` wurde beim Import festgelegt. Ein Test, der ein Modul
    importiert BEVOR er ``TRADINGBOT_TEST_STATE_DIR`` setzt, schrieb seine
    Datenbank deshalb in den Release-Baum -- sechs Testdateien taten das. Die
    Release-Hygiene hat es jedes Mal zu Recht bemaengelt, und vor jedem Packen
    musste jemand von Hand aufraeumen.

    ``DB_PATH`` bleibt als Modulvariable bestehen: Tests setzen sie gezielt,
    und das soll weiter gelten. Nur wenn sie unveraendert auf den Standard
    zeigt, entscheidet die Umgebungsvariable zum Zugriffszeitpunkt.
    """
    # Offline repairs select their pre-existing ledger before ANY cold import.
    # Outside that explicit scope the established runtime/test contract is unchanged.
    selected = scoped_database_path()
    if selected is not None:
        return selected
    test_dir = os.getenv("TRADINGBOT_TEST_STATE_DIR", "").strip()
    if test_dir and DB_PATH == _STANDARD_DB_PATH:
        return Path(test_dir) / DB_PATH.name
    return DB_PATH


_STANDARD_DB_PATH = DB_PATH

# These filters exist primarily to cap operational/financial risk. A later
# price rise must never be interpreted as evidence that such a guard is "bad".
SAFETY_FILTERS = {
    "risk_manager", "sector_guard", "correlation_guard", "crypto_portfolio_cap",
    "crypto_disabled", "existing_position",
    "duplicate_open_order", "open_order_check_error", "no_cash_margin_disabled",
    "cash_reserve", "position_size_zero", "broker_offline", "auth_error",
    "live_arming", "daily_loss_limit", "max_open_positions", "max_trades_per_day",
}

ALPHA_FILTERS = {
    "net_edge", "nachrichten", "event", "earnings_window", "underdog_screening",
    "market_quality", "cost_quote", "marktlage", "trend",
    "technical", "ml",
}

HORIZONS = {
    "1h": timedelta(hours=1),
    "4h": timedelta(hours=4),
    "1d": timedelta(days=1),
    "5d": timedelta(days=5),
}


def _connect() -> sqlite3.Connection:
    pfad = db_pfad()
    scoped = scoped_database_path() is not None
    if not scoped:
        pfad.parent.mkdir(parents=True, exist_ok=True)
    # Changing journal mode needs an exclusive lock. Doing it on every
    # connection races on a fresh database (core/UI or concurrent reservations).
    # Serialize local setup, keep existing WAL mode, and retry only BUSY/LOCKED
    # from another process. This is connection setup, never a business write
    # or a broker-order retry. Every failed handle is closed before retrying.
    with _CONNECTION_INIT_LOCK:
        deadline = time.monotonic() + 10.0
        while True:
            # mode=rw prevents creating a fresh ledger if a repair input vanishes
            # between validation and opening. Normal runtime creation is unchanged.
            con = (sqlite3.connect(pfad.as_uri() + "?mode=rw", uri=True, timeout=5)
                   if scoped else sqlite3.connect(pfad, timeout=5))
            con.row_factory = sqlite3.Row
            try:
                con.execute("PRAGMA busy_timeout=5000")
                mode = str(con.execute("PRAGMA journal_mode").fetchone()[0]).lower()
                if mode != 'wal':
                    mode = str(con.execute("PRAGMA journal_mode=WAL").fetchone()[0]).lower()
                if mode != 'wal':
                    raise sqlite3.OperationalError('WAL-Modus der Handelsdatenbank nicht verfuegbar')
                con.execute("PRAGMA synchronous=NORMAL")
                con.execute("PRAGMA foreign_keys=ON")
                return con
            except sqlite3.OperationalError as exc:
                con.close()
                code = getattr(exc, 'sqlite_errorcode', 0) or 0
                if code & 255 not in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED} or time.monotonic() >= deadline:
                    raise
                time.sleep(0.025)
            except BaseException:
                con.close()
                raise


def _ensure_column(con: sqlite3.Connection, table: str, name: str, decl: str) -> None:
    cols = {str(r[1]) for r in con.execute(f"PRAGMA table_info({table})").fetchall()}
    if name not in cols:
        con.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")


def init_db() -> None:
    with _LOCK, _connect() as con:
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS decisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at_utc TEXT NOT NULL,
                local_day TEXT NOT NULL,
                symbol TEXT NOT NULL,
                asset_type TEXT,
                broker TEXT,
                paper INTEGER NOT NULL DEFAULT 1,
                status TEXT NOT NULL,
                blocked_by TEXT,
                reason TEXT,
                category TEXT NOT NULL DEFAULT 'alpha',
                price REAL,
                ai_decision TEXT,
                ai_confidence REAL,
                ai_web_searches INTEGER NOT NULL DEFAULT 0,
                ai_source_count INTEGER NOT NULL DEFAULT 0,
                execution_status TEXT NOT NULL DEFAULT '',
                order_ids_json TEXT NOT NULL DEFAULT '[]',
                broker_reference_id TEXT NOT NULL DEFAULT '',
                position_ids_json TEXT NOT NULL DEFAULT '[]',
                fill_price REAL,
                fill_qty REAL,
                updated_at_utc TEXT NOT NULL DEFAULT '',
                requested_qty REAL,
                requested_price REAL,
                planned_stop REAL,
                planned_take REAL,
                broker_profile TEXT NOT NULL DEFAULT '',
                strategy_version TEXT NOT NULL DEFAULT '',
                strategy_mode TEXT NOT NULL DEFAULT '',
                strategy_parameter_hash TEXT NOT NULL DEFAULT '',
                payload_json TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_decisions_day ON decisions(local_day);
            CREATE INDEX IF NOT EXISTS idx_decisions_symbol ON decisions(symbol, created_at_utc);
            CREATE INDEX IF NOT EXISTS idx_decisions_status ON decisions(status, blocked_by);

            CREATE TABLE IF NOT EXISTS outcomes (
                decision_id INTEGER NOT NULL,
                horizon TEXT NOT NULL,
                target_at_utc TEXT NOT NULL,
                observed_at_utc TEXT,
                observed_price REAL,
                return_pct REAL,
                source TEXT,
                PRIMARY KEY(decision_id, horizon),
                FOREIGN KEY(decision_id) REFERENCES decisions(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_outcomes_due ON outcomes(observed_at_utc, target_at_utc);

            CREATE TABLE IF NOT EXISTS heartbeat_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at_utc TEXT NOT NULL,
                broker TEXT NOT NULL,
                state TEXT NOT NULL,
                detail TEXT,
                latency_ms REAL,
                last_contact TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_hb_day ON heartbeat_events(created_at_utc, broker);

            CREATE TABLE IF NOT EXISTS report_state (
                report_key TEXT PRIMARY KEY,
                sent_at_utc TEXT NOT NULL,
                payload_hash TEXT
            );
            CREATE TABLE IF NOT EXISTS decision_orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                decision_id INTEGER NOT NULL,
                broker TEXT NOT NULL,
                symbol TEXT NOT NULL DEFAULT '',
                broker_order_id TEXT NOT NULL,
                client_order_id TEXT NOT NULL DEFAULT '',
                role TEXT NOT NULL,
                status TEXT NOT NULL,
                requested_qty REAL,
                filled_qty REAL,
                remaining_qty REAL,
                requested_price REAL,
                avg_fill_price REAL,
                fees REAL,
                currency TEXT NOT NULL DEFAULT '',
                created_at_utc TEXT NOT NULL,
                updated_at_utc TEXT NOT NULL,
                raw_json TEXT NOT NULL DEFAULT '{}',
                UNIQUE(broker, broker_order_id, role),
                FOREIGN KEY(decision_id) REFERENCES decisions(id) ON DELETE RESTRICT
            );
            CREATE INDEX IF NOT EXISTS idx_decision_orders_decision
                ON decision_orders(decision_id, role, updated_at_utc);
            CREATE TABLE IF NOT EXISTS decision_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                decision_id INTEGER NOT NULL,
                event_id TEXT NOT NULL UNIQUE,
                event_type TEXT NOT NULL,
                created_at_utc TEXT NOT NULL,
                payload_json TEXT NOT NULL DEFAULT '{}',
                FOREIGN KEY(decision_id) REFERENCES decisions(id) ON DELETE RESTRICT
            );
            CREATE INDEX IF NOT EXISTS idx_decision_events_time
                ON decision_events(created_at_utc DESC, id DESC);
            """
        )
        # Transparent migration from early development DBs.
        _ensure_column(con, "decisions", "execution_status", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(con, "decisions", "order_ids_json", "TEXT NOT NULL DEFAULT '[]'")
        _ensure_column(con, "decisions", "broker_reference_id", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(con, "decisions", "position_ids_json", "TEXT NOT NULL DEFAULT '[]'")
        _ensure_column(con, "decisions", "fill_price", "REAL")
        _ensure_column(con, "decisions", "fill_qty", "REAL")
        _ensure_column(con, "decisions", "updated_at_utc", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(con, "decisions", "requested_qty", "REAL")
        _ensure_column(con, "decisions", "requested_price", "REAL")
        _ensure_column(con, "decisions", "planned_stop", "REAL")
        _ensure_column(con, "decisions", "planned_take", "REAL")
        _ensure_column(con, "decisions", "broker_profile", "TEXT NOT NULL DEFAULT ''")
        # v8.1.3: Ohne Strategieversion laesst sich spaeter nicht sagen,
        # unter welchen Einstellungen eine Entscheidung entstanden ist.
        _ensure_column(con, "decisions", "strategy_version", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(con, "decisions", "strategy_mode", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(con, "decisions", "strategy_parameter_hash", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(con, "decisions", "execution_result_json", "TEXT NOT NULL DEFAULT '{}'")


def _dt(value: str | datetime | None) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if value:
        try:
            d = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
        except Exception:
            __import__("logging").getLogger(__name__).debug("Best-effort-Ausnahme unterdrueckt", exc_info=True)
    return datetime.now(timezone.utc)


def _extract_ai(payload: dict) -> tuple[str, float | None, int, int]:
    ai = payload.get("ai") if isinstance(payload.get("ai"), dict) else {}
    decision = str(ai.get("decision") or "")
    try:
        confidence = float(ai.get("confidence")) if ai.get("confidence") is not None else None
    except Exception:
        confidence = None
    try:
        web = int(ai.get("web_searches") or 0)
    except Exception:
        web = 0
    src = ai.get("sources") or []
    try:
        source_count = int(ai.get("source_count") or len(src) or 0)
    except Exception:
        source_count = len(src) if isinstance(src, list) else 0
    return decision, confidence, web, source_count


def record(payload: dict) -> int | None:
    """Persist one serious decision and create forward-observation slots."""
    try:
        init_db()
        payload = dict(payload)
        if not payload.get("strategy_version"):
            try:
                import strategy_version as _sv
                payload["strategy_version"] = _sv.aktuell()
            except Exception:
                payload["strategy_version"] = ""
        payload = apply_snapshot(payload)
        decision_id = int(payload["decision_id"])
        now = _dt(payload.get("time") or payload.get("created_at"))
        local = now.astimezone(_local_zone())
        status = str(payload.get("status") or "UNKNOWN").upper()
        blocked = str(payload.get("blocked_by") or (
            "ai" if status == "AI_BLOCKED" else "ai_hold" if status == "AI_HOLD" else ""
        ))
        category = "safety" if blocked in SAFETY_FILTERS else "alpha"
        symbol = str(payload.get("symbol") or "?").upper()
        price = payload.get("price")
        try:
            price = float(price) if price not in (None, "") else None
        except Exception:
            price = None
        ai_decision, ai_conf, ai_web, ai_sources = _extract_ai(payload)
        order_ids = [str(x) for x in (payload.get("order_ids") or []) if str(x)]
        execution_status = str(payload.get("execution_status") or (
            "APPROVED" if status == "APPROVED" else ""
        )).upper()
        strategy_version = str(payload.get("strategy_version") or "")
        strategy_mode = str(payload.get("entry_strategy_mode") or payload.get("strategy_mode") or "")
        strategy_parameter_hash = str(payload.get("strategy_parameter_hash") or "")
        raw = json.dumps(payload, ensure_ascii=False, default=str)
        with _LOCK, _connect() as con:
            cur = con.execute(
                """INSERT INTO decisions
                   (id, created_at_utc, local_day, symbol, asset_type, broker, paper, status,
                    blocked_by, reason, category, price, ai_decision, ai_confidence,
                    ai_web_searches, ai_source_count, execution_status, order_ids_json,
                    updated_at_utc, requested_qty, requested_price, planned_stop, planned_take,
                    broker_profile, strategy_version, strategy_mode,
                    strategy_parameter_hash, payload_json)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET
                     updated_at_utc=excluded.updated_at_utc,
                     price=COALESCE(excluded.price, decisions.price),
                     ai_decision=excluded.ai_decision,
                     ai_confidence=excluded.ai_confidence,
                     ai_web_searches=excluded.ai_web_searches,
                     ai_source_count=excluded.ai_source_count,
                     execution_status=CASE WHEN excluded.execution_status!=''
                                           THEN excluded.execution_status ELSE decisions.execution_status END,
                     order_ids_json=CASE WHEN excluded.order_ids_json!='[]'
                                         THEN excluded.order_ids_json ELSE decisions.order_ids_json END,
                     strategy_version=CASE WHEN excluded.strategy_version!=''
                                           THEN excluded.strategy_version ELSE decisions.strategy_version END,
                     strategy_mode=CASE WHEN excluded.strategy_mode!=''
                                        THEN excluded.strategy_mode ELSE decisions.strategy_mode END,
                     strategy_parameter_hash=CASE WHEN excluded.strategy_parameter_hash!=''
                                                  THEN excluded.strategy_parameter_hash ELSE decisions.strategy_parameter_hash END,
                     payload_json=CASE WHEN decisions.payload_json='{}' THEN excluded.payload_json ELSE decisions.payload_json END""",
                (
                    decision_id, now.astimezone(timezone.utc).isoformat(), local.strftime("%Y-%m-%d"), symbol,
                    str(payload.get("asset_type") or ""), str(payload.get("broker") or ""),
                    1 if bool(payload.get("paper", True)) else 0, status, blocked,
                    str(payload.get("reason") or payload.get("signal_reason") or "")[:500], category,
                    price, ai_decision, ai_conf, ai_web, ai_sources,
                    execution_status, json.dumps(order_ids), now.astimezone(timezone.utc).isoformat(),
                    payload.get("qty"), payload.get("price"), payload.get("stop"), payload.get("take"),
                    str(payload.get("broker_profile") or payload.get("profile") or ""),
                    strategy_version, strategy_mode, strategy_parameter_hash, raw,
                ),
            )
            if payload.get('execution_status'):
                # Preserve the original decision snapshot; execution is a separate receipt.
                result = {k: payload.get(k) for k in ('execution_status', 'status', 'reason', 'blocked_by')}
                result['observed_at'] = datetime.now(timezone.utc).isoformat()
                con.execute('UPDATE decisions SET execution_result_json=? WHERE id=?',
                            (json.dumps(result, ensure_ascii=False), decision_id))
            if payload.get("reference_id") or payload.get("avg_fill_price") or payload.get("filled_quantity"):
                con.execute(
                    """UPDATE decisions SET broker_reference_id=?, fill_price=?, fill_qty=? WHERE id=?""",
                    (str(payload.get("reference_id") or ""),
                     float(payload.get("avg_fill_price") or 0.0) or None,
                     float(payload.get("filled_quantity") or 0.0) or None,
                     decision_id),
                )
            if price and price > 0 and status in {"BLOCKED", "AI_BLOCKED", "AI_HOLD", "APPROVED"}:
                for horizon, delta in HORIZONS.items():
                    con.execute(
                        "INSERT OR IGNORE INTO outcomes(decision_id,horizon,target_at_utc) VALUES (?,?,?)",
                        (decision_id, horizon, (now + delta).astimezone(timezone.utc).isoformat()),
                    )
        return decision_id
    except Exception as exc:
        logger.warning("Decision-SQLite konnte nicht geschrieben werden: %s", exc)
        return None


def mark_execution(
    decision_id: int | None, status: str, order_ids=None, *,
    reference_id: str | None = None, position_ids=None, fill_price=None,
    fill_qty=None, broker_paper: bool | None = None,
) -> bool:
    """Persistiert den Broker-Ausfuehrungszustand ohne Trading-Verhalten zu aendern.

    Wichtig fuer Echtgeld-Recovery: SUBMITTING wird vor dem POST geschrieben;
    UNKNOWN_AFTER_SUBMIT bedeutet, dass ein erneutes Senden verboten ist.

    Die Brokeridentitaet wird *gepatcht*, nicht bei jedem Statuswechsel als
    Vollersatz geschrieben. In 9.5.0 hat ein spaetes
    ``UNKNOWN_AFTER_SUBMIT`` dadurch einen zuvor belegten eToro-Fill wieder
    aus ``decisions`` geloescht (positionIds=[], Fillpreis/-menge=NULL),
    obwohl der append-only Orderbeleg weiterhin FILLED war. ``None`` bedeutet
    deshalb "vorhandenen Wert behalten"; eine bewusst leere Liste kann
    weiterhin explizit gespeichert werden.
    """
    if not decision_id:
        return False
    try:
        assignments = ["execution_status=?"]
        values = [str(status or "").upper()]
        if order_ids is not None:
            ids = [str(x) for x in order_ids if str(x)]
            assignments.append("order_ids_json=?")
            values.append(json.dumps(ids))
        if reference_id is not None:
            assignments.append("broker_reference_id=?")
            values.append(str(reference_id or ""))
        if position_ids is not None:
            pos = [str(x) for x in position_ids if str(x)]
            assignments.append("position_ids_json=?")
            values.append(json.dumps(pos))
        if fill_price not in (None, ""):
            assignments.append("fill_price=?")
            values.append(float(fill_price))
        if fill_qty not in (None, ""):
            assignments.append("fill_qty=?")
            values.append(float(fill_qty))
        if broker_paper is not None:
            assignments.append("paper=?")
            values.append(1 if bool(broker_paper) else 0)
        assignments.append("updated_at_utc=?")
        values.append(datetime.now(timezone.utc).isoformat())
        values.append(int(decision_id))
        sql = "UPDATE decisions SET " + ", ".join(assignments) + " WHERE id=?"
        with _LOCK, _connect() as con:
            con.execute(sql, tuple(values))
        return True
    except Exception as exc:
        logger.debug("Decision execution status konnte nicht aktualisiert werden: %s", exc)
        return False


def record_execution_event(decision_id: int | None, event_type: str, payload: dict,
                           *, event_id: str = "") -> bool:
    """Append-only execution evidence; never mutates the DecisionSnapshot."""
    if not decision_id:
        return False
    now = datetime.now(timezone.utc).isoformat()
    stable = str(event_id or f"decision:{int(decision_id)}:{event_type}:{now}")
    try:
        init_db()
        with _LOCK, _connect() as con:
            con.execute(
                "INSERT OR IGNORE INTO decision_events(decision_id,event_id,event_type,created_at_utc,payload_json) VALUES (?,?,?,?,?)",
                (int(decision_id), stable, str(event_type).upper(), now,
                 json.dumps(payload or {}, ensure_ascii=False, default=str)),
            )
        return True
    except Exception as exc:
        logger.warning("Ausfuehrungsereignis konnte nicht gespeichert werden: %s", exc)
        return False


def record_order_state(
    decision_id: int | None, *, broker: str, broker_order_id: str,
    role: str, status: str, symbol: str = "", client_order_id: str = "",
    requested_qty=None, filled_qty=None, remaining_qty=None,
    requested_price=None, avg_fill_price=None, fees=None, currency: str = "",
    raw: dict | None = None,
) -> bool:
    """Idempotentes Order-Ledger. Entry und Schutzorders sind eigene Zeilen."""
    if not decision_id or not str(broker_order_id or "").strip():
        return False
    now = datetime.now(timezone.utc).isoformat()
    try:
        def n(value):
            if value in (None, ""):
                return None
            value = float(value)
            return value if value == value and abs(value) != float("inf") else None
        with _LOCK, _connect() as con:
            con.execute(
                """INSERT INTO decision_orders
                   (decision_id,broker,symbol,broker_order_id,client_order_id,role,status,
                    requested_qty,filled_qty,remaining_qty,requested_price,avg_fill_price,
                    fees,currency,created_at_utc,updated_at_utc,raw_json)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(broker,broker_order_id,role) DO UPDATE SET
                     status=excluded.status,
                     filled_qty=COALESCE(excluded.filled_qty,decision_orders.filled_qty),
                     remaining_qty=COALESCE(excluded.remaining_qty,decision_orders.remaining_qty),
                     avg_fill_price=COALESCE(excluded.avg_fill_price,decision_orders.avg_fill_price),
                     fees=COALESCE(excluded.fees,decision_orders.fees),
                     updated_at_utc=excluded.updated_at_utc,
                     raw_json=excluded.raw_json""",
                (int(decision_id), str(broker).lower(), str(symbol).upper(),
                 str(broker_order_id), str(client_order_id or ""), str(role).upper(),
                 str(status).upper(), n(requested_qty), n(filled_qty), n(remaining_qty),
                 n(requested_price), n(avg_fill_price), n(fees), str(currency).upper(),
                 now, now, json.dumps(raw or {}, ensure_ascii=False, default=str)),
            )
        return True
    except Exception as exc:
        logger.warning("Order-Ledger konnte nicht geschrieben werden: %s", exc)
        return False


def record_order_result(decision_id: int | None, result, *, broker: str,
                        symbol: str = "", requested_qty=None,
                        requested_price=None, currency: str = "", role: str = "ENTRY") -> None:
    """Brokerergebnis ohne Vermischung von Entry- und Schutzstatus ablegen."""
    ids = [str(x) for x in (getattr(result, "order_ids", []) or []) if str(x)]
    net_filled = getattr(result, "filled_quantity", None)
    filled = (getattr(result, "gross_filled_quantity", net_filled)
              if str(broker).lower() == "okx" else net_filled)
    # For OKX the net balance delta may be lower because fees were paid in
    # base currency. Those fees are not an unfilled remainder of the order.
    from execution_lifecycle import result_state, observe
    status = result_state(result, requested_qty)
    observe(result, broker=broker)
    client_order_id = str(getattr(result, "client_order_id", "") or
                          getattr(result, "reference_id", "") or "")
    fees_quote = getattr(result, "fees_quote", None)
    evidence = {
        "reference_id": str(getattr(result, "reference_id", "") or ""),
        "client_order_id": client_order_id,
        "order_tag": str(getattr(result, "order_tag", "") or ""),
        "gross_filled_quantity": getattr(result, "gross_filled_quantity", None),
        "net_filled_quantity": net_filled,
        "execution_evidence": dict(getattr(result, "execution_evidence", {}) or {}),
        "execution_quote": dict(getattr(result, "execution_quote", {}) or {}),
        "terminal": bool(getattr(result, "terminal", False)),
        "role": str(role).upper(),
        "fill_ids": list(getattr(result, "fill_ids", []) or []),
        "fills": list(getattr(result, "fills", []) or []),
        "fees": dict(getattr(result, "fees", {}) or {}),
        "fees_quote": fees_quote,
        "fill_evidence_complete": bool(getattr(result, "fill_evidence_complete", False)),
        "stop_order_placed": bool(getattr(result, "stop_order_platziert", False)),
    }
    for index, order_id in enumerate(ids):
        order_role = "EXIT" if str(role).upper() == "EXIT" else ("ENTRY" if index == 0 else "PROTECTIVE")
        record_order_state(
            decision_id, broker=broker, broker_order_id=order_id, role=order_role,
            status=status if order_role in {"ENTRY", "EXIT"} else "ACTIVE",
            symbol=symbol, client_order_id=client_order_id,
            requested_qty=requested_qty,
            filled_qty=filled if order_role in {"ENTRY", "EXIT"} else None,
            remaining_qty=(max(0.0, float(requested_qty) - float(filled))
                           if order_role in {"ENTRY", "EXIT"} and requested_qty is not None and filled is not None else None),
            requested_price=requested_price,
            avg_fill_price=getattr(result, "avg_fill_price", None) if order_role in {"ENTRY", "EXIT"} else None,
            fees=fees_quote if order_role in {"ENTRY", "EXIT"} else None, currency=currency,
            raw=evidence,
        )
    normalized_fill_ids = [str(x) for x in (
        getattr(result, "fill_ids", []) or []) if str(x)]
    for index, row in enumerate(list(getattr(result, "fills", []) or [])):
        fill_id = str((row or {}).get("tradeId") or (row or {}).get("fill_id") or "")
        if fill_id:
            # OKX-tradeId ist nicht instrumentuebergreifend eindeutig. Das
            # Ergebnis des Adapters enthaelt bereits die kontogebundene,
            # zusammengesetzte ID; sie muss auch im append-only Eventjournal
            # verwendet werden, sonst kollidieren z. B. LINK und ONDO.
            stable_fill = (normalized_fill_ids[index]
                           if index < len(normalized_fill_ids) else
                           f"{str(broker).lower()}:{getattr(result, 'account_fingerprint', '')}:"
                           f"{(row or {}).get('instId', '')}:{(row or {}).get('ordId', '')}:{fill_id}")
            record_execution_event(
                decision_id, "FILL", dict(row),
                event_id=f"{str(broker).lower()}-fill:{stable_fill}")


def record_system_error(*, decision_id: int | None = None, symbol: str,
                        asset_type: str, broker: str, gate: str,
                        exc: BaseException, paper: bool = True,
                        context: dict | None = None) -> int | None:
    """Technischen Abbruch als echte fail-safe Entscheidung persistieren."""
    did = int(decision_id or new_decision_id())
    error = clean_error(exc)
    gates = [{"name": str(gate), "status": "SYSTEM_ERROR",
              "reason": error["message"]}]
    payload = apply_snapshot({
        "decision_id": did, "status": "SYSTEM_ERROR", "blocked_by": str(gate),
        "reason": f"{error['exception_type']}: {error['message']}",
        "symbol": symbol, "asset_type": asset_type, "broker": broker,
        "paper": paper, "gates": gates, "system_error": error,
        **dict(context or {}),
    })
    stored_id = record(payload)
    if stored_id is None:
        return None
    payload["decision_id"] = int(stored_id)
    payload["decision_snapshot"]["decision_id"] = int(stored_id)
    # Ein technischer Abbruch ist eine vollwertige Entscheidung. Deshalb wird
    # er best-effort auch in beide lesbaren Spiegel geschrieben. Das geschieht
    # erst nach dem erfolgreichen SQLite-Commit; die Order bleibt bei einem
    # Auditfehler ohnehin gesperrt.
    try:
        from decision_journal import PATH as journal_path
        with journal_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
    except Exception as mirror_exc:
        logger.debug("SYSTEM_ERROR-JSONL nicht schreibbar: %s", mirror_exc)
    try:
        from decision_source import speichere
        speichere({
            "decision_id": int(stored_id), "symbol": str(symbol).upper(),
            "asset_type": str(asset_type), "broker": str(broker).lower(),
            "aktion": "KAUF", "ergebnis": "SYSTEM_ERROR",
            "ergebnis_grund": payload["reason"], "quellen": [{
                "quelle": "SYSTEM", "richtung": "BLOCKIEREND", "gewicht": 1.0,
                "detail": error["message"], "datenquelle": str(gate),
            }],
        }, decision_id=int(stored_id))
    except Exception as source_exc:
        logger.debug("SYSTEM_ERROR-Quellenspiegel nicht schreibbar: %s", source_exc)
    return int(stored_id)



def execution_view(row):
    """Display the execution receipt without rewriting the entry decision."""
    row = dict(row)
    row['decision_status'] = row.get('status')
    row['decision_reason'] = row.get('reason')
    execution = str(row.get('execution_status') or '').upper()
    try:
        receipt = json.loads(row.get('execution_result_json') or '{}')
    except (ValueError, TypeError):
        receipt = {}
    if execution in {'FEHLGESCHLAGEN', 'FAILED', 'REJECTED', 'CANCELED', 'CANCELLED',
                     'NICHT_AUSGEFUEHRT', 'UNKNOWN_AFTER_SUBMIT', 'UNCLEAR'}:
        row['status'] = execution
        row['reason'] = (receipt.get('reason') if receipt.get('execution_status') == execution else None) or (
            'Broker-Ausführung: ' + execution + '; ursprüngliche Entscheidungsbegründung ist kein Ausführungsbeleg')
        row['blocked_by'] = receipt.get('blocked_by') or 'BROKER'
    return row

def latest(limit: int = 200, *, day: str | None = None) -> list[dict]:
    """Return latest decisions with compact outcome columns for GUI."""
    init_db()
    sql = "SELECT * FROM decisions"
    args: list[Any] = []
    if day:
        sql += " WHERE local_day=?"
        args.append(day)
    sql += " ORDER BY created_at_utc DESC, id DESC LIMIT ?"
    args.append(max(1, int(limit)))
    with _LOCK, _connect() as con:
        rows = con.execute(sql, args).fetchall()
        ids = [int(r["id"]) for r in rows]
        outcome_map: dict[int, dict] = {}
        if ids:
            marks = ",".join("?" for _ in ids)
            for o in con.execute(
                f"SELECT decision_id,horizon,return_pct,observed_price,observed_at_utc FROM outcomes WHERE decision_id IN ({marks})",
                ids,
            ).fetchall():
                outcome_map.setdefault(int(o["decision_id"]), {})[str(o["horizon"])] = {
                    "return_pct": o["return_pct"], "observed_price": o["observed_price"],
                    "observed_at_utc": o["observed_at_utc"],
                }
        out=[]
        for r in rows:
            d=dict(r)
            try:
                d["payload"] = json.loads(d.pop("payload_json"))
            except Exception:
                d["payload"] = {}; d.pop("payload_json", None)
            try:
                d["order_ids"] = json.loads(d.pop("order_ids_json") or "[]")
            except Exception:
                d["order_ids"] = []; d.pop("order_ids_json", None)
            d["outcomes"] = outcome_map.get(int(d["id"]), {})
            out.append(execution_view(d))
        return out


def due_outcomes(limit: int = 6) -> list[dict]:
    init_db(); now=datetime.now(timezone.utc).isoformat()
    with _LOCK, _connect() as con:
        rows=con.execute(
            """SELECT o.decision_id,o.horizon,o.target_at_utc,d.symbol,d.asset_type,d.price,d.broker
               FROM outcomes o JOIN decisions d ON d.id=o.decision_id
               WHERE o.observed_at_utc IS NULL AND o.target_at_utc<=? AND d.price>0
               ORDER BY o.target_at_utc ASC LIMIT ?""", (now,max(1,int(limit)))
        ).fetchall()
        return [dict(x) for x in rows]


def set_outcome(decision_id: int, horizon: str, observed_price: float, source: str = "broker") -> bool:
    try:
        observed_price=float(observed_price)
        if observed_price<=0:
            return False
        with _LOCK, _connect() as con:
            row=con.execute("SELECT price FROM decisions WHERE id=?",(int(decision_id),)).fetchone()
            if not row or not row["price"] or float(row["price"])<=0:
                return False
            ret=(observed_price/float(row["price"])-1.0)*100.0
            con.execute(
                "UPDATE outcomes SET observed_at_utc=?,observed_price=?,return_pct=?,source=? WHERE decision_id=? AND horizon=?",
                (datetime.now(timezone.utc).isoformat(),observed_price,ret,str(source),int(decision_id),str(horizon)),
            )
        return True
    except Exception as exc:
        logger.debug("Decision-Outcome konnte nicht gespeichert werden: %s",exc)
        return False


def lokaler_handelstag(jetzt: datetime | None = None) -> str:
    """Der lokale Handelstag nach ``config.LOCAL_TIMEZONE``.

    v9.1: Geschrieben wurde ``local_day`` schon immer mit dieser Zeitzone,
    GELESEN aber mit der Systemzeitzone. Auf einem Pi oder in Docker mit
    UTC-Systemuhr zeigte das Dashboard deshalb zwischen 00:00 und 02:00
    Ortszeit den Vortag -- also 0 Entscheidungen, obwohl welche vorlagen.
    """
    n = jetzt or datetime.now(timezone.utc)
    if n.tzinfo is None:
        n = n.replace(tzinfo=timezone.utc)
    return n.astimezone(_local_zone()).strftime("%Y-%m-%d")


def summary(day: str | None = None) -> dict:
    init_db(); day = day or lokaler_handelstag()
    with _LOCK, _connect() as con:
        rows=con.execute(
            "SELECT status,blocked_by,category,ai_decision,ai_web_searches,ai_source_count,execution_status FROM decisions WHERE local_day=?",
            (day,),
        ).fetchall()
        total=len(rows)
        decision_approved=sum(1 for r in rows if r["status"]=="APPROVED")
        rows = [execution_view(dict(r)) for r in rows]
        approved=sum(1 for r in rows if r["status"]=="APPROVED")
        execution_failed=sum(1 for r in rows if r['status'] in {'FEHLGESCHLAGEN', 'FAILED', 'REJECTED', 'NICHT_AUSGEFUEHRT', 'CANCELED', 'CANCELLED'})
        blocked=sum(1 for r in rows if r["status"] in ("BLOCKED","AI_BLOCKED","AI_HOLD"))
        submitted=sum(1 for r in rows if str(r["execution_status"] or "") in {"SUBMITTED","FILLED"})
        filled=sum(1 for r in rows if str(r["execution_status"] or "")=="FILLED")
        reasons=Counter((r["blocked_by"] or "unbekannt") for r in rows if r["status"] in ("BLOCKED","AI_BLOCKED","AI_HOLD"))
        ai_rows=[r for r in rows if r["ai_decision"]]
        ai_decisions=Counter((r["ai_decision"] or "?").upper() for r in ai_rows)
        web=sum(int(r["ai_web_searches"] or 0) for r in rows)
        evaluated=con.execute(
            """SELECT d.blocked_by,d.category,o.horizon,o.return_pct FROM outcomes o
               JOIN decisions d ON d.id=o.decision_id WHERE d.local_day=? AND o.observed_at_utc IS NOT NULL""",(day,)
        ).fetchall()
        return {
            "day":day,"candidates":total,"approved":approved,"blocked":blocked,
            "submitted":submitted,"filled":filled,"reasons":dict(reasons),
            "decision_approved":decision_approved,"execution_failed":execution_failed,
            "ai_calls":len(ai_rows),"ai_decisions":dict(ai_decisions),"ai_web_searches":web,
            "outcomes_observed":len(evaluated),
        }


def filter_performance(min_samples: int = 1) -> list[dict]:
    """Observed forward returns by rejection reason/horizon.

    `category=safety` rows are intentionally returned but clearly labelled;
    the GUI/report must not suggest relaxing a safety guard from missed profit.
    """
    init_db()
    with _LOCK, _connect() as con:
        rows=con.execute(
            """SELECT d.blocked_by,d.category,o.horizon,COUNT(*) n,AVG(o.return_pct) avg_return
               FROM outcomes o JOIN decisions d ON d.id=o.decision_id
               WHERE o.observed_at_utc IS NOT NULL AND d.status IN ('BLOCKED','AI_BLOCKED','AI_HOLD')
               GROUP BY d.blocked_by,d.category,o.horizon HAVING COUNT(*)>=?
               ORDER BY d.blocked_by,o.horizon""",(max(1,int(min_samples)),)
        ).fetchall()
        return [dict(r) for r in rows]


def heartbeat_record(broker: str, state: str, detail: str = "", latency_ms: float | None = None, last_contact: str | None = None) -> None:
    init_db()
    try:
        with _LOCK, _connect() as con:
            con.execute(
                "INSERT INTO heartbeat_events(created_at_utc,broker,state,detail,latency_ms,last_contact) VALUES (?,?,?,?,?,?)",
                (datetime.now(timezone.utc).isoformat(),str(broker),str(state),str(detail)[:500],latency_ms,last_contact),
            )
    except Exception as exc:
        logger.debug("Heartbeat-Journal: %s",exc)


def heartbeat_day_summary(day: str | None = None, broker: str | None = None) -> dict:
    zone = _local_zone()
    init_db(); day=day or datetime.now(zone).strftime("%Y-%m-%d")
    local_start=datetime.fromisoformat(day+"T00:00:00").replace(tzinfo=zone)
    local_end=local_start+timedelta(days=1)
    start=local_start.astimezone(timezone.utc).isoformat(); end=local_end.astimezone(timezone.utc).isoformat()
    sql="SELECT state,detail,created_at_utc,latency_ms FROM heartbeat_events WHERE created_at_utc>=? AND created_at_utc<?"
    args=[start,end]
    if broker:
        sql+=" AND broker=?";args.append(broker)
    sql+=" ORDER BY id"
    with _LOCK,_connect() as con:
        rows=con.execute(sql,args).fetchall()
    counts=Counter(str(r["state"] or "UNKNOWN") for r in rows)
    # Approximate observed downtime between recorded states. This deliberately
    # does not claim precision between sparse heartbeat samples.
    downtime=0.0
    bad={"OFFLINE","AUTH_ERROR","RECONNECTING","RESYNC_FAILED"}
    for i,r in enumerate(rows[:-1]):
        if str(r["state"]) in bad:
            a=_dt(r["created_at_utc"]); b=_dt(rows[i+1]["created_at_utc"])
            downtime += max(0.0,(b-a).total_seconds())
    lat=[float(r["latency_ms"]) for r in rows if r["latency_ms"] is not None]
    return {
        "events":len(rows),"states":dict(counts),"last_state":rows[-1]["state"] if rows else "UNKNOWN",
        "last_detail":rows[-1]["detail"] if rows else "", "observed_downtime_seconds":downtime,
        "avg_latency_ms": (sum(lat)/len(lat) if lat else None),
    }


def report_sent(report_key: str) -> bool:
    init_db()
    with _LOCK,_connect() as con:
        return con.execute("SELECT 1 FROM report_state WHERE report_key=?",(report_key,)).fetchone() is not None


def mark_report_sent(report_key: str, payload_hash: str = "") -> None:
    init_db()
    with _LOCK,_connect() as con:
        con.execute(
            "INSERT OR REPLACE INTO report_state(report_key,sent_at_utc,payload_hash) VALUES (?,?,?)",
            (report_key,datetime.now(timezone.utc).isoformat(),payload_hash),
        )


init_db()


def _sample_quality(n: int) -> str:
    n=int(n or 0)
    if n < 20: return "nicht_aussagekraeftig"
    if n < 50: return "hinweis"
    if n < 100: return "vorsichtige_tendenz"
    return "zunehmend_belastbar"


def research_context(days: int = 90) -> dict:
    """Kompakte, deterministisch berechnete Eingabe fuer woechentliches Research.

    Keine KI-Bewertung und kein Web. Die Research-KI erhaelt nur diese
    Statistiken plus das aktuell konfigurierte Universum.
    """
    init_db()
    cutoff=(datetime.now(timezone.utc)-timedelta(days=max(1,int(days)))).isoformat()
    with _LOCK,_connect() as con:
        rows=con.execute(
            "SELECT symbol,status,blocked_by,execution_status,payload_json FROM decisions WHERE created_at_utc>=?",
            (cutoff,),
        ).fetchall()
    rejected=Counter(); traded=Counter(); reasons=Counter(); sectors=Counter()
    total=0
    for r in rows:
        total += 1
        symbol=str(r["symbol"] or "?").upper()
        status=str(r["status"] or "")
        if status in {"BLOCKED","AI_BLOCKED","AI_HOLD"}:
            rejected[symbol] += 1
            reasons[str(r["blocked_by"] or "unbekannt")] += 1
        if str(r["execution_status"] or "").upper() == "FILLED":
            traded[symbol] += 1
        try:
            payload=json.loads(r["payload_json"] or "{}")
        except Exception:
            payload={}
        sector=str(payload.get("sector") or "UNCLASSIFIED")
        if status in {"BLOCKED","AI_BLOCKED","AI_HOLD"}:
            sectors[sector] += 1
    return {
        "lookback_days":max(1,int(days)),
        "total_decisions":total,
        "most_rejected_symbols":[{"symbol":k,"count":v} for k,v in rejected.most_common(30)],
        "actually_filled_symbols":[{"symbol":k,"count":v} for k,v in traded.most_common(30)],
        "primary_rejection_reasons":[{"reason":k,"count":v} for k,v in reasons.most_common(20)],
        "rejections_by_sector":[{"sector":k,"count":v} for k,v in sectors.most_common(20)],
    }


def trade_snapshot(broker: str = "", tage: int = 90) -> dict:
    """Handelskennzahlen aus dem Trade-Ledger (v8.1.3, Etappe A).

    Liegt bewusst hier, weil die Spezifikation diesen Namen nennt und weil
    ``strategy_analyst`` bereits aus diesem Modul liest. Gerechnet wird in
    ``trade_ledger`` -- lokaler Import, um einen Ringschluss zu vermeiden.
    """
    from trade_ledger import trade_snapshot as _snapshot
    return _snapshot(broker=broker, tage=tage)


def strategy_snapshot(days: int = 90) -> dict:
    """Berechnet Strategie-Statistik aus eigener Decision-History.

    Der Rueckgabewert ist bewusst bereits aggregiert. Ein Sprachmodell muss
    weder SQL rechnen noch Rohtrades interpretieren. Primary-blocker-Statistik
    wird nicht als Kausalitaet ausgegeben, weil downstream Filter nach dem
    ersten Block bewusst nicht mehr ausgefuehrt wurden.
    """
    init_db()
    lookback=max(1,int(days)); cutoff=(datetime.now(timezone.utc)-timedelta(days=lookback)).isoformat()
    with _LOCK,_connect() as con:
        decisions=con.execute(
            "SELECT id,created_at_utc,symbol,status,blocked_by,category,execution_status,payload_json FROM decisions WHERE created_at_utc>=?",
            (cutoff,),
        ).fetchall()
        outcomes=con.execute(
            """SELECT o.decision_id,o.horizon,o.return_pct FROM outcomes o
               JOIN decisions d ON d.id=o.decision_id
               WHERE d.created_at_utc>=? AND o.observed_at_utc IS NOT NULL AND o.return_pct IS NOT NULL""",
            (cutoff,),
        ).fetchall()
    meta={}; totals=Counter(); primary=Counter(); reached=Counter(); passed=Counter(); sectors=Counter(); regimes=Counter(); hours=Counter()
    for r in decisions:
        try: payload=json.loads(r["payload_json"] or "{}")
        except Exception: payload={}
        sector=str(payload.get("sector") or "UNCLASSIFIED")
        regime=str(payload.get("regime") or "UNSPECIFIED")
        try: hour=_dt(r["created_at_utc"]).astimezone().hour
        except Exception: hour=-1
        meta[int(r["id"])]=dict(row=dict(r),payload=payload,sector=sector,regime=regime,hour=hour)
        totals[str(r["status"] or "UNKNOWN")] += 1
        if str(r["status"] or "") in {"BLOCKED","AI_BLOCKED","AI_HOLD"}:
            primary[str(r["blocked_by"] or "unbekannt")] += 1
            sectors[sector] += 1; regimes[regime] += 1; hours[str(hour)] += 1
        for item in payload.get("evaluated_filters",[]) or []:
            if not isinstance(item,dict): continue
            name=str(item.get("name") or "")
            if not name: continue
            reached[name]+=1
            if str(item.get("status") or "").startswith("PASS"): passed[name]+=1

    grouped={}
    segments={}
    for o in outcomes:
        m=meta.get(int(o["decision_id"]))
        if not m: continue
        row=m["row"]; status=str(row.get("status") or "")
        horizon=str(o["horizon"]); ret=float(o["return_pct"])
        bucket=("APPROVED" if status=="APPROVED" else str(row.get("blocked_by") or "unbekannt"), horizon)
        grouped.setdefault(bucket,[]).append(ret)
        if status in {"BLOCKED","AI_BLOCKED","AI_HOLD"}:
            for dim,value in (("sector",m["sector"]),("regime",m["regime"]),("local_hour",str(m["hour"]))):
                segments.setdefault((dim,str(value),horizon),[]).append(ret)

    import statistics
    perf=[]
    for (group,horizon),vals in grouped.items():
        vals=[float(x) for x in vals]
        n=len(vals)
        perf.append({
            "group":group,"horizon":horizon,"n":n,
            "mean_return_pct":sum(vals)/n,
            "median_return_pct":statistics.median(vals),
            "positive_rate_pct":100.0*sum(1 for x in vals if x>0)/n,
            "missed_winner_rate_pct":100.0*sum(1 for x in vals if x>=1.0)/n,
            "avoided_loser_rate_pct":100.0*sum(1 for x in vals if x<=-1.0)/n,
            "sample_quality":_sample_quality(n),
        })
    perf.sort(key=lambda x:(x["horizon"],-x["n"],x["group"]))
    segment_perf=[]
    for (dimension,value,horizon),vals in segments.items():
        n=len(vals)
        segment_perf.append({
            "dimension":dimension,"value":value,"horizon":horizon,"n":n,
            "mean_return_pct":sum(vals)/n,"median_return_pct":statistics.median(vals),
            "positive_rate_pct":100.0*sum(1 for x in vals if x>0)/n,
            "missed_winner_rate_pct":100.0*sum(1 for x in vals if x>=1.0)/n,
            "avoided_loser_rate_pct":100.0*sum(1 for x in vals if x<=-1.0)/n,
            "sample_quality":_sample_quality(n),
        })
    segment_perf.sort(key=lambda x:(x["dimension"],x["horizon"],-x["n"],x["value"]))
    return {
        "generated_at_utc":datetime.now(timezone.utc).isoformat(),
        "lookback_days":lookback,
        "decision_count":len(decisions),
        "status_counts":dict(totals),
        "primary_rejection_counts":dict(primary),
        "filter_reached_counts":dict(reached),
        "filter_pass_counts":dict(passed),
        "rejections_by_sector":dict(sectors),
        "rejections_by_regime":dict(regimes),
        "rejections_by_local_hour":dict(hours),
        "forward_performance":perf,
        "segment_forward_performance":segment_perf,
        "method_note":(
            "Ablehnungsgruppen verwenden den primaeren ersten Blocker. Downstream-Filter wurden danach "
            "absichtlich nicht ausgefuehrt; die Zahlen sind deshalb deskriptiv und nicht kausal."
        ),
    }


def prune_history(*, now=None):
    """10.1.10: Begrenzte Aufbewahrung fuer NICHT-wirtschaftliche Protokolle.

    Die decision_history wuchs mit >50.000 Kandidatenentscheidungen pro Tag so
    schnell, dass die Diagnose-Sicherung ins Zeitlimit lief und Bot-Abfragen
    OperationalError sahen. Geloescht werden ausschliesslich alte Kandidaten-
    entscheidungen OHNE Order-/Eventbeleg (die Datenbank erzwingt das
    zusaetzlich per ON DELETE RESTRICT) sowie reine Heartbeat-Telemetrie.
    Orders, Fills, Trades, Belege und Ergebnisse bleiben unangetastet.
    """
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc) if now is None else now
    decision_days = max(7, int(getattr(config, "DECISION_RETENTION_DAYS", 90)))
    heartbeat_days = max(7, int(getattr(config, "DECISION_HEARTBEAT_RETENTION_DAYS", 30)))
    decision_cutoff = (now - timedelta(days=decision_days)).isoformat()
    heartbeat_cutoff = (now - timedelta(days=heartbeat_days)).isoformat()
    with _LOCK:
      con = _connect()
      try:
        removed_decisions = con.execute(
            """DELETE FROM decisions WHERE created_at_utc < ?
               AND NOT EXISTS (SELECT 1 FROM decision_orders o WHERE o.decision_id = decisions.id)
               AND NOT EXISTS (SELECT 1 FROM decision_events e WHERE e.decision_id = decisions.id)""",
            (decision_cutoff,)).rowcount
        removed_heartbeats = con.execute(
            "DELETE FROM heartbeat_events WHERE created_at_utc < ?",
            (heartbeat_cutoff,)).rowcount
        con.commit()
        try:
            con.execute("PRAGMA wal_checkpoint(PASSIVE)")
        except sqlite3.OperationalError:
            pass
      finally:
        con.close()
    return {"removed_decisions": int(removed_decisions),
            "removed_heartbeats": int(removed_heartbeats),
            "decision_retention_days": decision_days,
            "heartbeat_retention_days": heartbeat_days,
            "economic_receipts_touched": False}
