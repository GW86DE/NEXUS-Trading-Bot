"""Durable approval/outbox coordination in the EXISTING decision database.

No broker calls. SQLite BEGIN IMMEDIATE protects quotas across threads and
processes. Approval is not execution; uncertain execution is never retried.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from hashlib import sha256
from zoneinfo import ZoneInfo
import json
import math
import secrets
import sqlite3
import time

MODES = {"AUS", "BEOBACHTEN", "FREIGABE"}
PRE_SUBMIT = {"WAITING_APPROVAL", "CONFIRMING", "APPROVED"}
EXPOSURE = {"SUBMITTING", "SUBMITTED", "UNKNOWN", "FILLED", "PARTIALLY_FILLED"}
ACTIVE = PRE_SUBMIT | EXPOSURE
# 10.3.0 (PULSAR-2.0, Hype-Spur): hoechstens EIN offener PULSAR-Trade,
# hoechstens eine Nominierung pro Tag (Europe/Berlin) -- eine Ablehnung
# verbraucht den Tag, nicht mehr die Woche. Zeitstop 10 Handelstage.
RULES = {"version": "PULSAR-2.0", "risk_pct": .0025,
         "position_cap_pct": .03, "total_cap_pct": .03,
         "max_open": 1, "daily_nominations": 1, "loss_30d_pct": .0075,
         "entry_band_pct": .003, "max_hold_sessions": 10}


class Blocked(RuntimeError):
    pass


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False, ensure_ascii=False)


def digest(value):
    return sha256(encode(value).encode()).hexdigest()


def week(now=None):
    day = datetime.fromtimestamp(time.time() if now is None else now, ZoneInfo("Europe/Berlin"))
    y, w, _ = day.isocalendar()
    return f"{y}-W{w:02d}"


# 10.4.0: Das Schema wird je Datenbankpfad genau einmal je Prozess angelegt.
# Vorher lief bei JEDEM Zugriff ein executescript (CREATE TABLE IF NOT
# EXISTS ...), das ausserhalb der Transaktion eine eigene Schreibsperre
# nimmt -- am 17.09.2026 kollidierte das mit der Diagnose-Sicherung
# ("database is locked", pulsar/control.py:59) und kostete eine Entscheidung.
_SCHEMA_READY: set = set()
_SCHEMA_LOCK = __import__("threading").Lock()


def _ensure_schema(con, path):
    key = str(path)
    with _SCHEMA_LOCK:
        if key in _SCHEMA_READY:
            # Nur eine Leseabfrage (keine Schreibsperre): faengt eine zwischen-
            # zeitlich geloeschte/ersetzte Datei ab (Tests, Wiederherstellung).
            if con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='pulsar_control'").fetchone():
                return
            _SCHEMA_READY.discard(key)
        con.executescript('''
          CREATE TABLE IF NOT EXISTS pulsar_control (
            id INTEGER PRIMARY KEY CHECK(id=1), mode TEXT NOT NULL,
            revision INTEGER NOT NULL, session TEXT NOT NULL,
            heartbeat REAL NOT NULL, tradestie INTEGER NOT NULL DEFAULT 0);
          INSERT OR IGNORE INTO pulsar_control(id,mode,revision,session,heartbeat,tradestie) VALUES(1,'AUS',0,'',0,0);
          CREATE TABLE IF NOT EXISTS pulsar_proposals (
            id TEXT PRIMARY KEY, week TEXT NOT NULL, symbol TEXT NOT NULL,
            account TEXT NOT NULL, environment TEXT NOT NULL,
            plan TEXT NOT NULL, plan_hash TEXT NOT NULL, status TEXT NOT NULL,
            created REAL NOT NULL, expires REAL NOT NULL, revision INTEGER NOT NULL,
            session TEXT NOT NULL, chat TEXT NOT NULL, user TEXT NOT NULL,
            message_id TEXT NOT NULL DEFAULT '', nonce_hash TEXT NOT NULL DEFAULT '',
            nonce_expires REAL NOT NULL DEFAULT 0, decision_id INTEGER,
            execution TEXT NOT NULL DEFAULT '{}', reason TEXT NOT NULL DEFAULT '');
          CREATE TABLE IF NOT EXISTS pulsar_weeks (
            week TEXT PRIMARY KEY, proposal_id TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS pulsar_audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT, at REAL NOT NULL,
            proposal_id TEXT, action TEXT NOT NULL, detail TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS pulsar_position_control (
            proposal_id TEXT PRIMARY KEY, high15m REAL NOT NULL DEFAULT 0,
            stop REAL NOT NULL DEFAULT 0, partial_state TEXT NOT NULL DEFAULT '',
            partial_quantity REAL NOT NULL DEFAULT 0, review_at REAL NOT NULL DEFAULT 0);
        ''')
        if "web_search" not in {r[1] for r in con.execute("PRAGMA table_info(pulsar_control)")}:
            con.execute("ALTER TABLE pulsar_control ADD COLUMN web_search INTEGER NOT NULL DEFAULT 1")
        # 10.5.0: neue externe Quellen sind opt-in (wie Tradestie/GPT-Websuche).
        control_columns = {r[1] for r in con.execute("PRAGMA table_info(pulsar_control)")}
        for column in ("stocktwits", "finra"):
            if column not in control_columns:
                con.execute(f"ALTER TABLE pulsar_control ADD COLUMN {column} INTEGER NOT NULL DEFAULT 0")
        columns = {r[1] for r in con.execute("PRAGMA table_info(pulsar_position_control)")}
        if "partial_orders" not in columns:
            con.execute("ALTER TABLE pulsar_position_control ADD COLUMN partial_orders TEXT NOT NULL DEFAULT '[]'")
        con.commit()
        _SCHEMA_READY.add(key)


@contextmanager
def transaction():
    from decision_analytics import db_pfad
    path = db_pfad()
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, timeout=5)
    con.row_factory = sqlite3.Row
    try:
        con.execute("PRAGMA busy_timeout=5000")
        con.execute("PRAGMA synchronous=FULL")
        _ensure_schema(con, path)
        con.execute("BEGIN IMMEDIATE")
        yield con
        con.commit()
    except BaseException:
        con.rollback()
        raise
    finally:
        con.close()


def _audit(con, pid, action, detail="", now=None):
    con.execute("INSERT INTO pulsar_audit(at,proposal_id,action,detail) VALUES(?,?,?,?)",
                (time.time() if now is None else now, pid, action, str(detail)[:2000]))


def _state(con):
    row = dict(con.execute("SELECT * FROM pulsar_control WHERE id=1").fetchone())
    if row["mode"] not in MODES or row["revision"] < 0:
        raise Blocked("PULSAR-Steuerzustand ungueltig")
    return row


def settings():
    with transaction() as con:
        return {**_state(con), "rules": dict(RULES)}


def set_mode(mode, *, tradestie=None, web_search=None, stocktwits=None, finra=None):
    if mode not in MODES or any(v is not None and not isinstance(v, bool) for v in (tradestie, web_search, stocktwits, finra)):
        raise ValueError("Ungueltiger PULSAR-Modus oder Quellenwert")
    with transaction() as con:
        old = _state(con)
        con.execute("UPDATE pulsar_control SET mode=?,revision=revision+1,tradestie=?,web_search=?,stocktwits=?,finra=? WHERE id=1",
                    (mode, old["tradestie"] if tradestie is None else int(tradestie),
                     old["web_search"] if web_search is None else int(web_search),
                     old.get("stocktwits", 0) if stocktwits is None else int(stocktwits),
                     old.get("finra", 0) if finra is None else int(finra)))
        # Mode/config changes revoke permissions, never economic ownership.
        con.execute("UPDATE pulsar_proposals SET status='REVOKED',nonce_hash='',reason=? "
                    "WHERE status IN ('WAITING_APPROVAL','CONFIRMING','APPROVED')",
                    ("PULSAR-Einstellung geaendert; Freigabe widerrufen",))
        _audit(con, "", "SETTINGS", mode)
        return _state(con)


def start_session(now=None):
    token = secrets.token_hex(16)
    with transaction() as con:
        con.execute("UPDATE pulsar_control SET session=?,heartbeat=? WHERE id=1",
                    (token, time.time() if now is None else now))
        con.execute("UPDATE pulsar_proposals SET status='REVOKED',nonce_hash='',reason='Bot-Neustart' "
                    "WHERE status IN ('WAITING_APPROVAL','CONFIRMING','APPROVED')")
        _audit(con, "", "CORE_SESSION", "Alte Freigaben ungueltig", now)
    return token


def heartbeat(session, now=None):
    with transaction() as con:
        cur = con.execute("UPDATE pulsar_control SET heartbeat=? WHERE id=1 AND session=?",
                          (time.time() if now is None else now, session))
        if cur.rowcount != 1:
            raise Blocked("PULSAR-Sitzung wurde ersetzt")


def _expire(con, now):
    con.execute("UPDATE pulsar_proposals SET status='EXPIRED',nonce_hash='',reason='Freigabe abgelaufen' "
                "WHERE status IN ('WAITING_APPROVAL','CONFIRMING','APPROVED') AND expires<?", (now,))


def _decode(row):
    if row is None:
        return None
    item = dict(row)
    item["plan"] = json.loads(item["plan"])
    if digest(item["plan"]) != item["plan_hash"]:
        raise Blocked("Gespeicherter PULSAR-Plan stimmt nicht mit seinem Hash ueberein")
    item["execution"] = json.loads(item["execution"])
    return item


def proposals(*, active_only=False):
    with transaction() as con:
        _expire(con, time.time())
        rows = [_decode(r) for r in con.execute("SELECT * FROM pulsar_proposals ORDER BY created DESC")]
        return [r for r in rows if not active_only or r["status"] in ACTIVE]


def for_symbol(symbol, *, account="", environment=""):
    matches = [r for r in proposals(active_only=True) if r["symbol"] == str(symbol).upper()
               and (not account or r["account"] == account)
               and (not environment or r["environment"] == environment)]
    if len(matches) > 1:
        raise Blocked("Mehrere PULSAR-Bindungen fuer denselben Basiswert; Abgleich erforderlich")
    return matches[0] if matches else None


def _positive(v):
    if isinstance(v, bool):
        raise Blocked("Boolescher Wert ist keine Planmenge")
    try:
        n = float(v)
    except (ValueError, TypeError) as exc:
        raise Blocked("Fehlender numerischer Planwert") from exc
    if not math.isfinite(n) or n <= 0:
        raise Blocked("Planwert muss positiv und endlich sein")
    return n


def check_cost_budget(quantity, cap, stop, equity, costs, cash=None):
    values = (quantity, cap, stop, equity, costs)
    if not all(math.isfinite(float(v)) for v in values) or min(quantity, cap, stop, equity) <= 0 or costs < 0:
        raise Blocked("PULSAR-Kosten-/Risikowerte ungueltig")
    if quantity*cap+costs > min(equity*RULES["position_cap_pct"], cash if cash is not None else math.inf)+1e-8:
        raise Blocked("PULSAR-Positionskapital einschliesslich Kosten ueberschritten")
    if quantity*(cap-stop)+costs > equity*RULES["risk_pct"]+1e-8:
        raise Blocked("PULSAR-Risiko einschliesslich Kosten und Preisband ueberschritten")


def submit_plan(decision_id, account, environment):
    if not decision_id:
        return None
    with transaction() as con:
        rows = con.execute("SELECT * FROM pulsar_proposals WHERE decision_id=?", (int(decision_id),)).fetchall()
    if not rows:
        return None
    if len(rows) != 1:
        raise Blocked("PULSAR-Entscheidung nicht eindeutig")
    row = _decode(rows[0])
    if row["status"] != "SUBMITTING" or row["account"] != account or row["environment"] != environment:
        raise Blocked("PULSAR-Intent passt nicht zur Orderdomaene oder wurde bereits uebermittelt")
    return validate_plan(row["plan"])


def validate_plan(plan):
    if plan.get("rules") != RULES:
        raise Blocked("PULSAR-Regelversion passt nicht")
    if plan.get("broker") != "etoro" or plan.get("settlement") != "REAL" or plan.get("leverage") != 1:
        raise Blocked("PULSAR braucht eine echte Aktie ohne Hebel")
    if plan.get("currency") != "USD" or not str(plan.get("instrument_id", "")).isdigit():
        raise Blocked("eToro-Instrumentidentitaet oder Abrechnung fehlt")
    if not plan.get("account") or plan.get("environment") not in {"DEMO", "LIVE"}:
        raise Blocked("Kontobindung fehlt")
    if not plan.get("evidence_hash") or not plan.get("assessment_id"):
        raise Blocked("Unveraenderlicher Quellenbezug fehlt")
    qty, price, stop, target, equity = [_positive(plan.get(k)) for k in
                                       ("quantity", "price", "stop", "take", "equity")]
    if not stop < price < target or not .08 <= (price - stop) / price <= .18:
        raise Blocked("PULSAR-Stop ausserhalb des festgelegten Risikorahmens")
    cost = plan.get("cost_budget")
    cap = plan.get("max_entry_price")
    if (cost is None or cap is None or not math.isfinite(float(cost)) or float(cost) < 0
            or not math.isfinite(float(cap)) or not price <= float(cap) <= price*(1+RULES["entry_band_pct"])+1e-8):
        raise Blocked("PULSAR braucht einen kosteninklusiven Preis- und Risikorahmen")
    check_cost_budget(qty, float(cap), stop, equity, float(cost))
    if float(plan.get("capital_reserved") or 0) + 1e-8 < qty*float(cap)+float(cost):
        raise Blocked("PULSAR-Kapitalreservierung deckt Kosten und Preisband nicht")
    return plan


def _ready(con, now):
    state = _state(con)
    if state["mode"] != "FREIGABE":
        raise Blocked("PULSAR laeuft nicht im Freigabemodus")
    if not state["session"] or not 0 <= now - state["heartbeat"] <= 30:
        raise Blocked("PULSAR-Worker nicht frisch")
    _expire(con, now)
    return state


def nominate(plan, *, chat, user, now=None):
    now = time.time() if now is None else now
    validate_plan(plan)
    if not str(chat) or not str(user):
        raise Blocked("Persoenlicher Telegram-Nutzer und Chat muessen eingerichtet sein")
    with transaction() as con:
        state = _ready(con, now)
        iso = week(now)
        # 10.3.0: Der Wochen-Slot wird nicht mehr bei der Nominierung
        # verbraucht (eine Ablehnung blockierte sonst die ganze Woche).
        # pulsar_weeks bleibt als Fill-Journal in record_execution bestehen.
        day_start = datetime.fromtimestamp(now, ZoneInfo("Europe/Berlin")).replace(
            hour=0, minute=0, second=0, microsecond=0).timestamp()
        nominated_today = con.execute(
            "SELECT COUNT(*) FROM pulsar_audit WHERE action='NOMINATED' AND at>=?",
            (day_start,)).fetchone()[0]
        if nominated_today >= RULES["daily_nominations"]:
            raise Blocked("Heute wurde bereits ein PULSAR-Kandidat nominiert")
        active = [_decode(r) for r in con.execute("SELECT * FROM pulsar_proposals")]
        active = [r for r in active if r["status"] in ACTIVE]
        if len(active) >= RULES["max_open"] or any(r["symbol"] == plan["symbol"] for r in active):
            raise Blocked("PULSAR-Positionsplatz belegt oder Instrument bereits gebunden")
        if any(r["status"] == "UNKNOWN" for r in active):
            raise Blocked("Ungeklaerte PULSAR-Uebermittlung muss zuerst abgeglichen werden")
        if any(r["execution"].get("fill_time_unknown") for r in active):
            raise Blocked("PULSAR-Ausfuehrungszeit fuer die Wochenzuordnung fehlt")
        if con.execute("SELECT 1 FROM pulsar_position_control WHERE partial_state IN "
                       "('SUBMITTING','SUBMITTED','UNKNOWN') LIMIT 1").fetchone():
            raise Blocked("PULSAR-Teilausfuehrung muss zuerst mit Brokerbelegen abgeglichen werden")
        # Reservations in another currency/account cannot be summed as dollars.
        # Their own domain still counts against the installation-wide 2 slots.
        reserved = sum(r["plan"].get("capital_reserved") or r["plan"]["quantity"] * r["plan"]["price"] for r in active
                       if r["account"] == plan["account"] and r["environment"] == plan["environment"])
        if reserved + (plan.get("capital_reserved") or plan["quantity"] * plan["price"]) > plan["equity"] * RULES["total_cap_pct"]:
            raise Blocked("PULSAR-Gesamtkapital einschliesslich Reservierungen ueberschritten")
        pid, nonce = secrets.token_hex(12), secrets.token_urlsafe(16)
        con.execute('''INSERT INTO pulsar_proposals(id,week,symbol,account,environment,
            plan,plan_hash,status,created,expires,revision,session,chat,user,nonce_hash,nonce_expires)
            VALUES(?,?,?,?,?,?,?,'WAITING_APPROVAL',?,?,?,?,?,?,?,?)''',
            (pid, iso, plan["symbol"], plan["account"], plan["environment"], encode(plan), digest(plan),
             now, now + 1800, state["revision"], state["session"], str(chat), str(user),
             digest(nonce), now + 1800))
        _audit(con, pid, "NOMINATED", digest(plan), now)
        return {"id": pid, "nonce": nonce, "plan": plan, "expires": now + 1800}


def bind_message(pid, message_id):
    with transaction() as con:
        if not str(message_id).isdigit():
            raise Blocked("Telegram-Nachrichten-ID fehlt")
        con.execute("UPDATE pulsar_proposals SET message_id=? WHERE id=? AND message_id='' "
                    "AND status='WAITING_APPROVAL'", (str(message_id), pid))


def callback(pid, nonce, action, *, chat, user, message_id, now=None):
    now = time.time() if now is None else now
    with transaction() as con:
        state = _ready(con, now)
        row = _decode(con.execute("SELECT * FROM pulsar_proposals WHERE id=?", (pid,)).fetchone())
        if not row or row["status"] not in {"WAITING_APPROVAL", "CONFIRMING"}:
            raise Blocked("Diese Freigabe ist bereits verbraucht oder abgelaufen")
        if (row["chat"] != str(chat) or row["user"] != str(user)
                or not row["message_id"] or row["message_id"] != str(message_id)
                or row["session"] != state["session"] or row["revision"] != state["revision"]
                or row["week"] != week(now) or row["nonce_expires"] < now
                or not secrets.compare_digest(row["nonce_hash"], digest(nonce))):
            raise Blocked("Freigabe passt nicht zu Nutzer, Nachricht, Sitzung, Woche oder Plan")
        if action == "reject":
            con.execute("UPDATE pulsar_proposals SET status='DECLINED',nonce_hash='' WHERE id=?", (pid,))
            _audit(con, pid, "DECLINED", now=now)
            return {"status": "DECLINED"}
        if action != "confirm":
            raise Blocked("Unbekannte PULSAR-Aktion")
        if row["status"] == "WAITING_APPROVAL":
            nonce2 = secrets.token_urlsafe(16)
            con.execute("UPDATE pulsar_proposals SET status='CONFIRMING',nonce_hash=?,nonce_expires=? WHERE id=?",
                        (digest(nonce2), min(row["expires"], now + 60), pid))
            _audit(con, pid, "SECOND_CONFIRMATION", now=now)
            return {"status": "CONFIRMING", "nonce": nonce2, "plan": row["plan"]}
        con.execute("UPDATE pulsar_proposals SET status='APPROVED',nonce_hash='' WHERE id=?", (pid,))
        _audit(con, pid, "APPROVED", "Core muss alle Gates neu pruefen", now)
        return {"status": "APPROVED"}


def claim(pid, *, decision_id, account, environment, price, quantity, stop, take,
          equity, cash, evidence_hash, estimated_fees=None, now=None):
    """Called only by the normal core AFTER its final gates, BEFORE submit."""
    now = time.time() if now is None else now
    with transaction() as con:
        state = _ready(con, now)
        row = _decode(con.execute("SELECT * FROM pulsar_proposals WHERE id=?", (pid,)).fetchone())
        if not row or row["status"] != "APPROVED":
            raise Blocked("Kein unverbrauchter persoenlich freigegebener PULSAR-Intent")
        plan = validate_plan(row["plan"])
        if (row["account"] != account or row["environment"] != environment
                or row["week"] != week(now) or row["revision"] != state["revision"]
                or row["session"] != state["session"] or plan["evidence_hash"] != evidence_hash):
            raise Blocked("Freigabe passt nicht mehr zur aktuellen Entscheidungsgrundlage")
        price, qty, equity, cash = map(_positive, (price, quantity, equity, cash))
        if (abs(price / plan["price"] - 1) > RULES["entry_band_pct"]
                or qty != plan["quantity"] or stop != plan["stop"] or take != plan["take"]):
            raise Blocked("Kursband, Menge oder Schutzplan haben sich geaendert")
        if estimated_fees is None or not math.isfinite(float(estimated_fees)) or not 0 <= float(estimated_fees) <= plan["cost_budget"]:
            raise Blocked("PULSAR-Kosten fehlen oder uebersteigen die Freigabe")
        check_cost_budget(qty, plan["max_entry_price"], stop, equity, plan["cost_budget"], cash)
        if int(decision_id) <= 0:
            raise Blocked("Persistente Core-Entscheidung fehlt")
        con.execute("UPDATE pulsar_proposals SET status='SUBMITTING',decision_id=? WHERE id=?",
                    (int(decision_id), pid))
        _audit(con, pid, "CLAIMED", str(decision_id), now)
        return row


def record_execution(pid, state, evidence, *, now=None):
    """No order retry. Native reconciliation/ledger evidence advances state."""
    if state not in EXPOSURE | {"REJECTED", "CLOSED"}:
        raise ValueError("Unbekannter Ausfuehrungszustand")
    now = time.time() if now is None else now
    with transaction() as con:
        row = _decode(con.execute("SELECT * FROM pulsar_proposals WHERE id=?", (pid,)).fetchone())
        if not row or row["status"] not in EXPOSURE:
            raise Blocked("Ausfuehrungsbeleg ohne uebermittelten Intent")
        evidence = {**row["execution"], **evidence}
        if state in {"FILLED", "PARTIALLY_FILLED", "CLOSED"}:
            if not evidence.get("order_ids") or not evidence.get("position_ids"):
                raise Blocked("Native Order-/Positionsbelege fehlen")
            stamp = evidence.get("filled_at")
            evidence["fill_time_unknown"] = not bool(stamp)
            if stamp:
                # 10.3.0: pulsar_weeks ist nur noch ein Fill-Journal. Mehrere
                # Fills in derselben Woche sind mit Tageslimit + max_open=1
                # regulaer; ein Wochenkonflikt schaltet den Modus nicht mehr um.
                iso = week(_positive(stamp))
                existing = con.execute("SELECT proposal_id FROM pulsar_weeks WHERE week=?", (iso,)).fetchone()
                if existing and existing[0] != pid:
                    _audit(con, pid, "ADDITIONAL_FILL_SAME_WEEK", iso, now)
                con.execute("INSERT OR IGNORE INTO pulsar_weeks VALUES(?,?)", (iso, pid))
        if row["status"] in {"FILLED", "PARTIALLY_FILLED"} and state in {"SUBMITTED", "UNKNOWN", "REJECTED"}:
            return
        if row["status"] == state and row["execution"] == evidence:
            return
        con.execute("UPDATE pulsar_proposals SET status=?,execution=? WHERE id=?",
                    (state, encode(evidence), pid))
        _audit(con, pid, state, now=now)
