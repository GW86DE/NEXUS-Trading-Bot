"""Sichere Benutzeraktionen fuer ungeklärte OKX-Ledgereintraege.

Die WebUI schreibt nur einen Auftrag. Ausschliesslich der laufende Krypto-Core
prueft Eigentumsbeweis und offene Orders und veraendert danach lokale Daten.
Keine Aktion in diesem Modul sendet eine Order an OKX oder veraendert Guthaben.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

import config
from safe_persistence import atomic_write_json
from state_lock import critical_state_lock

RECHECK = "RECHECK"
CONFIRM_ACCOUNT_ASSET = "CONFIRM_ACCOUNT_ASSET"
DELETE_LOCAL = "DELETE_LOCAL"
AKTIONEN = {RECHECK, CONFIRM_ACCOUNT_ASSET, DELETE_LOCAL}
VERDECKTE_STATUS = {"ACCOUNT_ASSET_CONFIRMED", "DISMISSED"}
_LOCK = threading.RLock()


def _root() -> Path:
    return Path(os.getenv("TRADINGBOT_TEST_STATE_DIR", "").strip() or
                Path(__file__).resolve().parent)


def _path() -> Path:
    return _root() / "okx_reconciliation_actions.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load() -> dict:
    try:
        return json.loads(_path().read_text(encoding="utf-8")) if _path().exists() else {"actions": []}
    except Exception:
        return {"actions": []}


def _save(data: dict) -> None:
    actions = list(data.get("actions") or [])[-200:]
    atomic_write_json(_path(), {"version": 1, "updated_at": _now(), "actions": actions})


def _fill_ids(row: dict) -> set[str]:
    ids: set[str] = set()
    raw = row.get("entry_fill_ids_json") or "[]"
    try:
        values = json.loads(raw) if isinstance(raw, str) else raw
        ids.update(str(x) for x in (values or []) if str(x).strip())
    except (TypeError, ValueError, json.JSONDecodeError):
        pass
    if str(row.get("entry_fill_id") or "").strip():
        ids.add(str(row.get("entry_fill_id")))
    return ids


def hat_echten_eigentumsbeweis(row: dict) -> bool:
    """Kuenstliche ``okx-entry:<orderId>``-IDs sind ausdrücklich kein Fill."""
    fills = {x for x in _fill_ids(row) if not x.startswith("okx-entry:")}
    return bool(str(row.get("entry_order_id") or "").strip()
                and str(row.get("client_order_id") or "").strip()
                and fills)


def anfordern(trade_id: int, aktion: str, *, symbol: str, actor: str) -> dict:
    import trade_ledger

    target = str(aktion or "").upper()
    if target not in AKTIONEN:
        raise ValueError("Unbekannte Klärungsaktion")
    row = trade_ledger.trade_detail(int(trade_id))
    if not row or str(row.get("broker") or "").lower() != "okx":
        raise ValueError("OKX-Ledgereintrag nicht gefunden")
    if str(row.get("symbol") or "").upper() != str(symbol or "").upper():
        raise ValueError("Symbol-Bestätigung stimmt nicht mit dem Eintrag überein")
    if row.get("ausgestiegen_am"):
        raise ValueError("Der Eintrag ist bereits abgeschlossen")
    if target != RECHECK and str(row.get("reconciliation_status") or "").upper() == "CONFIRMED_OPEN":
        raise ValueError("Eine bestätigte offene Botposition kann hier nicht gelöscht werden")
    if target != RECHECK and hat_echten_eigentumsbeweis(row):
        raise ValueError("Der Eintrag besitzt echte Fill-Beweise und muss zuerst mit OKX abgeglichen werden")

    action = {
        "id": uuid.uuid4().hex,
        "trade_id": int(trade_id),
        "symbol": str(symbol).upper(),
        "action": target,
        "account": str(row.get("broker_account_fingerprint") or ""),
        "environment": "DEMO" if row.get("paper") else "LIVE",
        "instrument": str(row.get("broker_position_id") or ""),
        "entry_order_id": str(row.get("entry_order_id") or ""),
        "actor": str(actor or "webui")[:120],
        "requested_at": _now(),
        "status": "PENDING",
    }
    with _LOCK, critical_state_lock(_path(), timeout_seconds=3):
        data = _load()
        pending = [x for x in data.get("actions", [])
                   if int(x.get("trade_id") or 0) == int(trade_id)
                   and str(x.get("status")) == "PENDING"]
        if pending:
            raise ValueError("Für diesen Eintrag wartet bereits eine Klärungsaktion")
        data.setdefault("actions", []).append(action)
        _save(data)
    return dict(action)


def _backup_once(action_id: str) -> str:
    """Konsistente lokale Sicherung vor einer manuellen Bereinigung."""
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    target = _root() / "state_backups" / f"okx_reconciliation_{stamp}_{action_id[:8]}"
    target.mkdir(parents=True, exist_ok=True)
    positions = _root() / "crypto_positions.json"
    if positions.exists():
        shutil.copy2(positions, target / positions.name)
    db = _root() / str(getattr(config, "DECISION_DB_FILE", "decision_history.sqlite"))
    if db.exists():
        source = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
        destination = sqlite3.connect(str(target / db.name))
        try:
            source.backup(destination)
        finally:
            destination.close()
            source.close()
    return str(target)


def verarbeite(engine, broker) -> list[dict]:
    """Wird vom Krypto-Core vor dem normalen Brokerabgleich aufgerufen."""
    import trade_ledger

    results: list[dict] = []
    with _LOCK, critical_state_lock(_path(), timeout_seconds=3):
        data = _load()
        for action in data.get("actions", []):
            if str(action.get("status")) != "PENDING":
                continue
            try:
                trade_id = int(action.get("trade_id") or 0)
                symbol = str(action.get("symbol") or "").upper()
                target = str(action.get("action") or "").upper()
                row = trade_ledger.trade_detail(trade_id)
                if not row or row.get("ausgestiegen_am"):
                    raise ValueError("Eintrag existiert nicht mehr oder ist bereits abgeschlossen")
                account = str(broker.account_fingerprint() or '') if broker is not None else ''
                environment = 'DEMO' if bool(getattr(broker, 'demo', False)) else 'LIVE'
                if (not account or row.get('broker') != 'okx'
                        or row.get('symbol') != symbol
                        or row.get('broker_account_fingerprint') != account
                        or bool(row.get('paper')) != (environment == 'DEMO')
                        or action.get('account', account) != account
                        or action.get('environment', environment) != environment
                        or action.get('instrument', row['broker_position_id']) != row['broker_position_id']
                        or action.get('entry_order_id', row['entry_order_id']) != row['entry_order_id']):
                    raise ValueError('Auftrag, Ledger und verbundenes Konto/Umgebung stimmen nicht exakt ueberein')
                if target != RECHECK and str(row.get("reconciliation_status") or "").upper() == "CONFIRMED_OPEN":
                    raise ValueError("OKX-Abgleich bestätigt eine offene Botposition")
                if target != RECHECK and hat_echten_eigentumsbeweis(row):
                    raise ValueError("Echte Fill-Beweiskette vorhanden; Löschen gesperrt")
                if target != RECHECK and symbol in set(engine._offene_order_symbole()):
                    raise ValueError("Eigene OKX-Order ist noch ungeklärt; Löschen gesperrt")
                position = engine.buch.hole(symbol)
                if target != RECHECK and position is not None and position.darf_automatisch_verkaufen:
                    raise ValueError("Bestätigte Botposition im Positionsbuch; Löschen gesperrt")

                if target == RECHECK:
                    # v9.2: Der Knopf fuehrt jetzt einen ECHTEN Abgleich aus.
                    # Vorher schrieb er nur einen Text und verliess sich auf
                    # den naechsten Zyklus -- der denselben Vergleich
                    # wiederholte. Bei den drei Positionen vom 31.08.2026 hat
                    # er deshalb nichts geaendert, egal wie oft man ihn drueckte.
                    detail = _echter_abgleich(engine, broker, symbol, trade_id)
                else:
                    backup = _backup_once(str(action.get("id") or "manual"))
                    if position is not None:
                        engine.buch.entferne(symbol)
                    status = ("ACCOUNT_ASSET_CONFIRMED" if target == CONFIRM_ACCOUNT_ASSET
                              else "DISMISSED")
                    label = ("Vom Benutzer als Konto-Asset bestätigt" if target == CONFIRM_ACCOUNT_ASSET
                             else "Lokalen ungeklärten Eintrag vom Benutzer gelöscht")
                    if not trade_ledger.dismiss_unproven_trade(
                            trade_id, status=status, actor=str(action.get("actor") or "webui"),
                            reason=label):
                        raise ValueError("Ledgerstatus konnte nicht aktualisiert werden")
                    detail = f"{label}; OKX-Guthaben unverändert; Sicherung: {backup}"
                action["status"] = "DONE"
                action["completed_at"] = _now()
                action["detail"] = detail
            except Exception as exc:
                action["status"] = "FAILED"
                action["completed_at"] = _now()
                action["detail"] = str(exc)[:300]
            results.append(dict(action))
        _save(data)
    return results


def _echter_abgleich(engine, broker, symbol: str, trade_id) -> str:
    """Broker fragen, Ledger abgleichen und ein konkretes Ergebnis melden."""
    import trade_ledger

    if broker is None:
        return "OKX ist nicht verbunden; Abgleich nicht moeglich"

    from execution_lifecycle import recover_okx
    recover_okx(broker)
    position = engine.buch.hole(symbol)
    if position is not None:
        if (position.account_fingerprint != str(broker.account_fingerprint() or '')
                or bool(position.paper) != bool(broker.demo)):
            raise ValueError('Positionsbuch gehoert zu einem anderen Konto/Umgebung')
        # Recovery merges exact receipts. The normal engine remains responsible
        # for quantity accounting and protection on the next pass.
        engine._externer_verkaufsbeweis(position, position.menge)

    # 1. Aktuellen Bestand holen und den Ledgerabgleich wirklich ausfuehren.
    try:
        bestaende = {str(p.symbol).upper(): p for p in broker.positionen()}
    except Exception as exc:
        return f"OKX-Bestand nicht abrufbar: {type(exc).__name__}"
    try:
        engine._offene_ledger_abgleichen(bestaende)
    except Exception as exc:
        return f"Abgleich fehlgeschlagen: {type(exc).__name__}: {str(exc)[:120]}"

    # 2. Ergebnis getrennt nach Eigentum, Abgleich und Verwaltung berichten.
    position = engine.buch.hole(symbol)
    zeile = trade_ledger.trade_detail(int(trade_id)) or {}
    abgleich = str(zeile.get("reconciliation_status") or "").upper()

    if position is None:
        if abgleich in ("CLOSED", "EXTERNAL_CLOSED"):
            return "Position bei OKX bereits geschlossen"
        return ("Kein Eintrag mehr im Positionsbuch; OKX-Guthaben unveraendert")
    if not position.ownership_chain_complete:
        return ("Brokerbeweis unvollstaendig (orderId, clOrdId oder echte "
                "tradeId fehlt) -- nur beobachten")
    if str(position.exit_state or '').upper() in {'SUBMITTING', 'UNCLEAR', 'MANUAL_EXIT_PENDING', 'ACCOUNTING_PENDING'}:
        return ('Verkaufsabgleich weiter offen: ' + str(position.exit_last_detail or
                'Eindeutiger Abschluss-/Fill-Beleg fehlt. Kein erneuter Verkaufsauftrag.'))
    verwaltung = str(getattr(position, "management_status", "") or "")
    if verwaltung:
        return (f"Bestaetigte Botposition -- {verwaltung}. Broker-Schutz und "
                "Wiedereinstiegssperre bleiben aktiv.")
    if str(position.verwaltung).upper() == "MANUELL":
        return "Bestaetigte Botposition -- manuelle Verwaltung"
    if position.strategy_execution_enabled:
        return "Bestaetigte Botposition -- automatische Verwaltung aktiv"
    return "Bestaetigte Botposition -- automatische Ausgaenge pausiert"


def status() -> dict:
    with _LOCK, critical_state_lock(_path(), timeout_seconds=3):
        return _load()


__all__ = ["RECHECK", "CONFIRM_ACCOUNT_ASSET", "DELETE_LOCAL", "AKTIONEN",
           "VERDECKTE_STATUS", "anfordern", "verarbeite", "status",
           "hat_echten_eigentumsbeweis"]
