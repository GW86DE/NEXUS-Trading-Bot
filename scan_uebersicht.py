"""Scan-Uebersicht je Zyklus (10.4.0): was wurde geprueft, was kam dabei heraus.

Hintergrund (17.09.2026): Das Entscheidungsjournal zeichnet bewusst nur
Kaufwuensche auf (freigegeben oder an einem Gate blockiert). HOLD und SELL
ohne Position erzeugen keinen Eintrag -- so bleibt das Logbuch lesbar. Am
17.09. sah Georg deshalb nur GOOGL/NVDA und dachte, eToro laufe nicht,
obwohl 22 Aktien alle 6 Minuten geprueft wurden.

Dieses Modul zaehlt je Broker und Zyklus, was mit jedem Instrument passiert
ist, und schreibt EINE kompakte Zeile pro Zyklus in ``scan_uebersicht.json``
(letzte 48 Zyklen je Broker). Das Logbuch zeigt sie ueber der
Entscheidungstabelle. Kein Journal-Eintrag je Instrument, kein Datenbank-
zugriff, ein Fehler hier darf den Handelspfad nie beruehren.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import logging
import os
from pathlib import Path
import threading

DATEI = "scan_uebersicht.json"
SCHEMA = 1
MAX_ZYKLEN = 48
_LOCK = threading.RLock()
logger = logging.getLogger(__name__)

# Lesbare Gruppen fuer die Anzeige; unbekannte Codes landen unter "sonstige".
GATE_GRUPPEN = {
    "market_session": "Boerse geschlossen / Kurs veraltet",
    "startup_readiness": "Anlaufphase",
    "risk_manager": "Risikomanager",
    "existing_position": "Position bereits vorhanden",
    "duplicate_open_order": "Order bereits offen",
    "earnings_window": "Earnings-Schutzfenster",
    "event": "Nachrichten-/Event-Score",
    "nachrichten": "Nachrichtenfilter",
    "marktlage": "Marktlage kritisch",
    "cash_reserve": "Kapital / Reserve",
    "no_cash_margin_disabled": "Kapital / Reserve",
    "position_size_zero": "Positionsgroesse null",
    "cost_quote": "Kostenpruefung",
    "cost_quote_unavailable": "Kostenpruefung",
    "net_edge": "Netto-Edge zu klein",
    "market_quality": "Marktqualitaet",
    "broad_liquidity": "Liquiditaet (Broad)",
    "sector_guard": "Sektorlimit",
    "correlation_guard": "Korrelationslimit",
    "underdog_screening": "Underdog-Screening",
    "crypto_disabled": "Krypto pausiert",
    "crypto_portfolio_cap": "Krypto-Gesamtlimit",
    "candidate_processing": "Systemfehler",
    "open_order_check_error": "Systemfehler",
}


def pfad() -> Path:
    root = Path(os.getenv("TRADINGBOT_TEST_STATE_DIR", "").strip()
                or Path(__file__).resolve().parent)
    return root / DATEI


class Zyklus:
    """Zaehlt einen Scannerzyklus eines Brokers; Ergebnisse je Instrument."""

    def __init__(self, broker: str, zyklus: int):
        self.broker = str(broker or "?")
        self.zyklus = int(zyklus)
        self.begonnen = datetime.now(timezone.utc)
        self.geprueft: set[str] = set()
        self.ergebnis: dict[str, tuple[str, str]] = {}

    def _merke(self, symbol, kategorie: str, detail: str = "") -> None:
        name = str(symbol or "?")
        self.geprueft.add(name)
        self.ergebnis[name] = (kategorie, str(detail or "")[:120])

    def hold(self, symbol, signal_action: str = "HOLD") -> None:
        action = str(signal_action or "HOLD").upper()
        self._merke(symbol, "sell_ohne_position" if action == "SELL" else "kein_signal", action)

    def blockiert(self, symbol, gate: str, detail: str = "") -> None:
        self._merke(symbol, "kaufwunsch_blockiert", f"{gate}: {detail}" if detail else str(gate))

    def freigegeben(self, symbol) -> None:
        self._merke(symbol, "kauf_freigegeben")

    def fehler(self, symbol, detail: str = "") -> None:
        self._merke(symbol, "fehler", detail)

    def zusammenfassung(self) -> dict:
        arten = Counter(v[0] for v in self.ergebnis.values())
        gates = Counter()
        beispiele = {}
        for name, (kategorie, detail) in sorted(self.ergebnis.items()):
            if kategorie == "kaufwunsch_blockiert":
                code = detail.split(":", 1)[0].strip()
                gruppe = GATE_GRUPPEN.get(code, "sonstige")
                gates[gruppe] += 1
                beispiele.setdefault(gruppe, []).append(name)
        return {
            "broker": self.broker, "zyklus": self.zyklus,
            "begonnen_utc": self.begonnen.isoformat(),
            "beendet_utc": datetime.now(timezone.utc).isoformat(),
            "geprueft": len(self.geprueft),
            "kein_signal": arten.get("kein_signal", 0),
            "sell_ohne_position": arten.get("sell_ohne_position", 0),
            "kaufwunsch_blockiert": arten.get("kaufwunsch_blockiert", 0),
            "kauf_freigegeben": arten.get("kauf_freigegeben", 0),
            "fehler": arten.get("fehler", 0),
            "blockiert_nach_gate": [
                {"gate": gruppe, "anzahl": anzahl, "symbole": beispiele.get(gruppe, [])[:6]}
                for gruppe, anzahl in gates.most_common()],
            "freigegeben_symbole": sorted(n for n, v in self.ergebnis.items() if v[0] == "kauf_freigegeben"),
            "fehler_symbole": sorted(n for n, v in self.ergebnis.items() if v[0] == "fehler")[:6],
        }

    def text(self) -> str:
        z = self.zusammenfassung()
        teile = [f"{z['geprueft']} Instrumente geprueft"]
        if z["kauf_freigegeben"]:
            teile.append(f"{z['kauf_freigegeben']} Kauf freigegeben ({', '.join(z['freigegeben_symbole'])})")
        if z["kaufwunsch_blockiert"]:
            gates = "; ".join(f"{g['anzahl']} {g['gate']} ({', '.join(g['symbole'])})" for g in z["blockiert_nach_gate"])
            teile.append(f"{z['kaufwunsch_blockiert']} Kaufwunsch blockiert: {gates}")
        if z["sell_ohne_position"]:
            teile.append(f"{z['sell_ohne_position']} ueberkauft/Verkaufssignal ohne Position")
        if z["kein_signal"]:
            teile.append(f"{z['kein_signal']} ohne Signal")
        if z["fehler"]:
            teile.append(f"{z['fehler']} Fehler ({', '.join(z['fehler_symbole'])})")
        return f"{self.broker}: " + " -- ".join(teile)


def _laden() -> dict:
    try:
        import json
        data = json.loads(pfad().read_text(encoding="utf-8"))
        if isinstance(data, dict) and data.get("schema") == SCHEMA and isinstance(data.get("broker"), dict):
            return data
    except FileNotFoundError:
        pass
    except Exception as exc:
        logger.debug("Scan-Uebersicht unlesbar (%s); wird neu aufgebaut", type(exc).__name__)
    return {"schema": SCHEMA, "broker": {}}


def speichern(zyklus: Zyklus) -> None:
    """Haengt die Zusammenfassung an; niemals eine Ausnahme in den Handelspfad."""
    try:
        from safe_persistence import atomic_write_json
        eintrag = zyklus.zusammenfassung()
        eintrag["text"] = zyklus.text()
        with _LOCK:
            data = _laden()
            liste = data["broker"].setdefault(zyklus.broker, [])
            liste.append(eintrag)
            del liste[:-MAX_ZYKLEN]
            data["aktualisiert_utc"] = eintrag["beendet_utc"]
            atomic_write_json(pfad(), data)
        logger.info("SCAN %s", eintrag["text"])
    except Exception as exc:
        logger.debug("Scan-Uebersicht nicht speicherbar: %s", exc, exc_info=True)


def lesen(limit: int = 12) -> dict:
    """Fuer WebUI/Diagnose: die letzten Zyklen je Broker, neueste zuerst."""
    with _LOCK:
        data = _laden()
    limit = max(1, min(MAX_ZYKLEN, int(limit)))
    return {"schema": SCHEMA, "aktualisiert_utc": data.get("aktualisiert_utc", ""),
            "broker": {name: list(reversed(rows))[:limit] for name, rows in (data.get("broker") or {}).items()},
            "detail": "Eine Zeile je Scannerzyklus. Nur Kaufwuensche stehen zusaetzlich als Einzelbelege im Journal; "
                      "HOLD/SELL ohne Position werden hier gezaehlt, nicht einzeln protokolliert."}


__all__ = ["Zyklus", "speichern", "lesen", "pfad", "GATE_GRUPPEN"]
