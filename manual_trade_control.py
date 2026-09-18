"""Sichere WebUI-Auftraege fuer manuelle OKX-Positionsverwaltung.

Die WebUI besitzt absichtlich keine Brokerverbindung. Sie schreibt nur eine
eindeutige, einmalige Absicht. Der Handelskern fuehrt diese unter seiner
bestehenden OKX-Sitzung aus und schreibt das Ergebnis atomar zurueck.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from safe_persistence import atomic_write_json

logger = logging.getLogger(__name__)

DATEI = "manual_trade_commands.json"
LOCK_DATEI = "manual_coin_locks.json"
AKTIONEN = {"SET_PROTECTION", "SELL"}
LOCK_OPTIONEN = {"1H": 60, "6H": 360, "DAY": -1, "MANUAL": 0}
_LOCK = threading.RLock()


def _verifizierter_botnachweis(value: object) -> bool:
    """Nur explizite Fill-Nachweise berechtigen zu einer manuellen Order.

    Das Ledger verwendet bei neuen OKX-Trades den praeziseren Wert
    VERIFIED_BROKER_FILL_CHAIN. Dieser ist kein schwacher Ersatz, sondern der
    staerkere Nachweis aus instId, Konto, ordId/clOrdId und Fill-Kette.
    """
    return str(value or "").upper() in {
        "VERIFIED", "VERIFIED_BROKER_FILL_CHAIN",
    }


@contextmanager
def _process_lock():
    """Serialisiert WebUI und Handelskern auch ueber Prozessgrenzen."""
    path = _root() / ".manual_trade_control.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(path, "a+b")
    gesperrt = False
    try:
        if os.name == "posix":
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            gesperrt = True
        else:
            # v9.1: Unter Windows wurde die Datei bisher nur geoeffnet und
            # sonst nichts. WebUI (uvicorn) und Handelskern sind dort zwei
            # Prozesse -- im Lese-Aendern-Schreiben ging ein Update verloren,
            # und derselbe manuelle Verkauf konnte ein zweites Mal gesendet
            # werden. Vorlage ist instance_lock.py.
            import msvcrt
            for _ in range(50):
                try:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
                    gesperrt = True
                    break
                except OSError:
                    time.sleep(0.1)
            if not gesperrt:
                raise TimeoutError(
                    "Auftragsdatei ist dauerhaft gesperrt; kein zweiter "
                    "Schreibzugriff, um einen Doppelauftrag auszuschliessen.")
        yield
    finally:
        try:
            if gesperrt and os.name == "posix":
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            elif gesperrt:
                import msvcrt
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            logger.warning("Auftragssperre konnte nicht sauber geloest werden",
                           exc_info=True)
        handle.close()


def _root() -> Path:
    value = os.getenv("TRADINGBOT_TEST_STATE_DIR", "").strip()
    return Path(value) if value else Path(__file__).resolve().parent


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None = None) -> str:
    return (value or _now()).isoformat()


def _read(name: str, default: dict) -> dict:
    path = _root() / name
    if not path.exists():
        return dict(default)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError('Auftragsdatei ist kein Objekt')
        return value
    except (OSError, ValueError) as exc:
        raise RuntimeError('Manueller Auftragsstand nicht lesbar; neue Aktion gesperrt') from exc


def _write(name: str, value: dict) -> None:
    atomic_write_json(_root() / name, value)


def _open_trade(trade_id: int) -> dict:
    import trade_ledger
    row = trade_ledger.trade_detail(int(trade_id))
    if not row or row.get("ausgestiegen_am"):
        raise ValueError("Der Trade ist nicht mehr offen.")
    if str(row.get("broker") or "").lower() != "okx":
        raise ValueError("Manuelle Handelsaktionen sind hier nur fuer OKX verfuegbar.")
    if str(row.get("reconciliation_status") or "").upper() != "CONFIRMED_OPEN":
        raise ValueError("Der Trade ist nicht eindeutig als offen bestaetigt.")
    if not _verifizierter_botnachweis(row.get("ownership_status")):
        raise ValueError("Der Eigentumsnachweis des Bot-Trades fehlt.")
    return row


def request(trade_id: int, action: str, *, stop: float = 0.0,
            take_profit: float = 0.0, lock: str = "6H", actor: str = "webui",
            confirmation: str = "") -> dict:
    """Validierten Auftrag hinterlegen; niemals selbst eine Order senden."""
    row = _open_trade(trade_id)
    action = str(action or "").upper()
    if action not in AKTIONEN:
        raise ValueError("Unbekannte manuelle Aktion.")
    symbol = str(row.get("symbol") or "").upper()
    expected = f"{action} {symbol}"
    if str(confirmation or "").strip().upper() != expected:
        raise ValueError(f"Zur Bestaetigung exakt „{expected}“ eingeben.")
    payload = {
        "id": uuid.uuid4().hex, "trade_id": int(trade_id), "action": action,
        "symbol": symbol, "instrument": str(row.get("broker_position_id") or ""),
        "account_fingerprint": str(row.get("broker_account_fingerprint") or ""),
        "environment": "DEMO" if row.get("paper") else "LIVE",
        "actor": str(actor or "")[:80], "created_at": _iso(), "status": "PENDING",
    }
    if action == "SET_PROTECTION":
        stop, take_profit = float(stop or 0.0), float(take_profit or 0.0)
        if stop <= 0 or take_profit <= 0 or stop >= take_profit:
            raise ValueError("Stop-Loss und Take-Profit muessen positiv sein; SL muss unter TP liegen.")
        payload.update(stop=stop, take_profit=take_profit)
    else:
        lock = str(lock or "6H").upper()
        if lock not in LOCK_OPTIONEN:
            raise ValueError("Unbekannte Wiedereinstiegssperre.")
        payload["lock"] = lock
    with _LOCK, _process_lock():
        data = _read(DATEI, {"commands": []})
        if any(int(x.get("trade_id") or 0) == int(trade_id)
               and str(x.get("status")) in {"PENDING", "PROCESSING", "UNCLEAR"}
               for x in data.get("commands", [])):
            raise ValueError("Fuer diesen Trade laeuft bereits ein manueller Auftrag.")
        data.setdefault("commands", []).append(payload)
        active = [x for x in data['commands'] if x.get('status') in {'PENDING', 'PROCESSING', 'UNCLEAR'}]
        history = [x for x in data['commands'] if x not in active][-100:]
        data['commands'] = history + active
        _write(DATEI, data)
    return dict(payload)


def pending() -> list[dict]:
    with _LOCK, _process_lock():
        data = _read(DATEI, {"commands": []})
        changed = False
        for row in data.get("commands", []):
            if str(row.get("status")) != "PROCESSING":
                continue
            try:
                started = datetime.fromisoformat(
                    str(row.get("updated_at") or row.get("created_at") or "")
                    .replace("Z", "+00:00"))
                if started.tzinfo is None:
                    started = started.replace(tzinfo=timezone.utc)
            except ValueError:
                started = _now() - timedelta(hours=1)
            if (_now() - started).total_seconds() > 120:
                row.update(status="UNCLEAR", updated_at=_iso(),
                           detail=("Handelskern wurde waehrend der Ausfuehrung beendet. "
                                   "Kein automatischer Wiederholungsauftrag; zuerst OKX abgleichen."))
                changed = True
        if changed:
            _write(DATEI, data)
        return [dict(x) for x in data.get("commands", [])
                if str(x.get("status")) == "PENDING"]


def claim(command_id: str) -> bool:
    """Only one consumer may take a queued intention, including two processes."""
    with _LOCK, _process_lock():
        data = _read(DATEI, {"commands": []})
        row = next((r for r in data.get("commands", []) if r.get("id") == command_id), None)
        if row is None or row.get("status") != "PENDING":
            return False
        row.update(status="PROCESSING", updated_at=_iso())
        _write(DATEI, data)
        return True


def unresolved() -> list[dict]:
    with _LOCK, _process_lock():
        return [dict(r) for r in _read(DATEI, {"commands": []}).get("commands", [])
                if r.get("status") in {"PROCESSING", "UNCLEAR"}]


def update(command_id: str, status: str, detail: str = "", **extra) -> None:
    with _LOCK, _process_lock():
        data = _read(DATEI, {"commands": []})
        for row in data.get("commands", []):
            if str(row.get("id")) == str(command_id):
                row.update(status=str(status), updated_at=_iso(), detail=str(detail)[:600], **extra)
                break
        _write(DATEI, data)


def overview() -> dict:
    with _LOCK, _process_lock():
        commands = list(reversed(_read(DATEI, {"commands": []}).get("commands", [])))[:25]
        locks = _active_locks(_read(LOCK_DATEI, {"locks": []}).get("locks", []))
    commands = [dict(c) for c in commands]
    _legacy_command_environments(commands)
    # A failed attempt is audit history, not the current state of its trade.
    # Do not rewrite that receipt or infer resolution from another SUI trade.
    for command in commands:
        if command.get("status") != "FAILED" or command.get("action") != "SELL":
            continue
        keys = ("trade_id", "account_fingerprint", "environment", "instrument", "action")
        if not all(command.get(k) for k in keys):
            continue
        later = [x for x in commands if x.get("status") == "SUCCEEDED"
                 and all(x.get(k) == command.get(k) for k in keys)
                 and str(x.get("created_at") or "") > str(command.get("created_at") or "")]
        if later:
            resolved = max(later, key=lambda x: str(x.get("created_at") or ""))
            command["resolved_by"] = resolved.get("id")
            command["history_note"] = "Frueherer Fehlversuch; diese Position wurde am " + str(resolved.get("created_at")) + " erfolgreich geschlossen."
    return {"commands": commands, "locks": locks}


def _legacy_command_environments(commands):
    """Read-only annotation of old receipts lacking their environment.

    The current broker mode and ticker are never enough. Only the exact bound
    closed ledger trade can provide the missing DEMO/LIVE identity.
    """
    import sqlite3
    import config
    from contextlib import closing
    path = _root() / getattr(config, "DECISION_DB_FILE", "decision_history.sqlite")
    if not path.is_file():
        return
    try:
        with closing(sqlite3.connect(path.resolve().as_uri()+"?mode=ro", uri=True)) as con:
            for c in commands:
                if (c.get("environment") or c.get("action") != "SELL"
                        or not all(c.get(k) for k in ("trade_id", "account_fingerprint", "instrument"))):
                    continue
                row = con.execute("""SELECT paper FROM trades WHERE trade_id=? AND broker='okx'
                    AND broker_account_fingerprint=? AND broker_position_id=?
                    AND superseded_by IS NULL AND ausgestiegen_am IS NOT NULL AND paper IN (0,1)""",
                    (c["trade_id"], c["account_fingerprint"], c["instrument"])).fetchone()
                if row:
                    c["environment"] = "DEMO" if row[0] else "LIVE"
                    c["environment_source"] = "EXACT_LEDGER_TRADE"
    except sqlite3.Error:
        # Missing or unreadable evidence keeps the old receipt unannotated.
        return


def _active_locks(rows: list[dict]) -> list[dict]:
    now = _now()
    result = []
    for row in rows:
        until = str(row.get("until") or "")
        if until:
            try:
                if datetime.fromisoformat(until.replace("Z", "+00:00")) <= now:
                    continue
            except ValueError:
                # v9.1: fail-CLOSED. Ein unlesbares Ablaufdatum liess die
                # Sperre bis 9.0.15 spurlos verschwinden -- an einem
                # Sicherheitsgate ist das die falsche Richtung. Die Sperre
                # bleibt bestehen und kann von Hand freigegeben werden.
                logger.warning("Sperre %s hat ein unlesbares Ablaufdatum %r -- "
                               "sie bleibt aktiv.", row.get("id"), until)
                result.append(dict(row))
                continue
        if not row.get("released_at"):
            result.append(dict(row))
    return result


def add_lock(*, broker: str, account_fingerprint: str, symbol: str,
             duration: str, reason: str, actor: str, source_id: str = "") -> dict:
    duration = str(duration or "6H").upper()
    if duration not in LOCK_OPTIONEN:
        # v9.1: Vorher ein ungefangener KeyError. Der Aufrufer faengt nur
        # BrokerFehler/ValueError -- nach einem ERFOLGREICHEN Verkauf waere der
        # Auftrag auf PROCESSING haengen geblieben und spaeter UNCLEAR
        # geworden, obwohl alles sauber lief.
        raise ValueError(
            f"Unbekannte Sperrdauer {duration!r}; erlaubt sind "
            + ", ".join(sorted(LOCK_OPTIONEN)))
    minutes = LOCK_OPTIONEN[duration]
    now = _now()
    if minutes < 0:
        # Ende des lokalen Handelstags Europe/Berlin ohne zusaetzliche Abhaengigkeit.
        from zoneinfo import ZoneInfo
        # v9.1: die konfigurierte Zeitzone, nicht mehr fest Europe/Berlin.
        try:
            import config as _cfg
            zone = ZoneInfo(str(getattr(_cfg, "LOCAL_TIMEZONE", "Europe/Berlin")))
        except Exception:
            zone = ZoneInfo("Europe/Berlin")
        local = now.astimezone(zone)
        end = (local + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        until = end.astimezone(timezone.utc)
    elif minutes > 0:
        until = now + timedelta(minutes=minutes)
    else:
        until = None
    row = {"id": uuid.uuid4().hex, "broker": str(broker).lower(),
           "account_fingerprint": str(account_fingerprint), "symbol": str(symbol).upper(),
           "created_at": _iso(now), "until": _iso(until) if until else "",
           "duration": duration, "reason": str(reason)[:300], "actor": str(actor)[:80]}
    with _LOCK, _process_lock():
        data = _read(LOCK_DATEI, {"locks": []})
        if source_id:
            prior = data.setdefault('completion_ids', {}).get(str(source_id))
            if prior:
                if any(prior.get(k) != row[k] for k in ('broker', 'account_fingerprint', 'symbol')):
                    raise ValueError('Wiedereinstiegssperre widerspricht dem Bedienauftrag')
                return dict(prior)
            data['completion_ids'][str(source_id)] = dict(row)
        data["locks"] = [x for x in _active_locks(data.get("locks", []))
                         if not (str(x.get("broker")) == row["broker"]
                                 and str(x.get("account_fingerprint")) == row["account_fingerprint"]
                                 and str(x.get("symbol")) == row["symbol"])] + [row]
        _write(LOCK_DATEI, data)
    return row


def lock_reason(broker: str, account_fingerprint: str, symbol: str) -> str:
    with _LOCK, _process_lock():
        rows = _active_locks(_read(LOCK_DATEI, {"locks": []}).get("locks", []))
    for row in rows:
        if (str(row.get("broker")) == str(broker).lower()
                and str(row.get("account_fingerprint")) == str(account_fingerprint)
                and str(row.get("symbol")) == str(symbol).upper()):
            until = row.get("until") or "manuell"
            return f"Manuelle Wiedereinstiegssperre bis {until}: {row.get('reason') or ''}".strip()
    return ""


def release_lock(lock_id: str, actor: str) -> bool:
    with _LOCK, _process_lock():
        data = _read(LOCK_DATEI, {"locks": []})
        changed = False
        for row in data.get("locks", []):
            if str(row.get("id")) == str(lock_id) and not row.get("released_at"):
                row.update(released_at=_iso(), released_by=str(actor)[:80]); changed = True
        if changed:
            _write(LOCK_DATEI, data)
        return changed
