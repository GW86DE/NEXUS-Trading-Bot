"""Wo der Risikozustand eines Brokers liegt -- genau eine Antwort (v8.1.5).

DER FEHLER
==========
Bis v8.1.4 gab es fuer eToro ZWEI Risikozustaende, die nichts voneinander
wussten:

    live_trader.py      RiskState.load(config.RISK_STATE_FILE)  -> risk_state.json
    risk_pots.RiskPot   f"risk_state_{broker}.json"             -> risk_state_etoro.json

``live_trader`` fuehrte den echten Handel und schrieb ausschliesslich in die
erste Datei. Die zweite wurde nie beschrieben. Georgs Datei vom 25.08.2026
enthielt entsprechend nur Nullen -- an einem Tag mit zwei geschlossenen
Positionen und dreistelligem Verlust.

Die Folgen waren keine Anzeigefehler:

* ``RiskPot.tagesverlust_erreicht()`` verglich immer 0,00 gegen die Grenze.
  Die Tagesverlustbremse des eToro-Topfes konnte NIE ausloesen.
* ``RiskPotManager.globale_pruefung()`` summiert die Toepfe. Die Aktienseite
  steuerte dauerhaft 0,00 bei -- die brokerübergreifende Klammer sah nur die
  Kryptoverluste.

``settings_migration.UMBENENNUNGEN`` sagt seit v8 ``risk_state.json ->
risk_state_etoro.json``. Gemeint war also immer EIN Zustand; nur der
Aktienkern hat den Wechsel nie mitgemacht.

DIE REGEL
=========
Jeder Broker hat genau eine Zustandsdatei, und niemand bildet ihren Namen
mehr selbst. Wer den Pfad braucht, fragt hier.

BEIM UMSTIEG WIRD NICHTS WEGGEWORFEN
====================================
Auf einer laufenden Installation existiert die alte Datei mit den echten
Werten und die neue mit Nullen. Der Umzug uebernimmt deshalb die Datei mit
dem tatsaechlichen Inhalt und legt die andere als ``.abgeloest`` daneben --
statt eine davon stillschweigend zu ueberschreiben.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

# Broker -> kanonischer Dateiname.
ZUSTANDSDATEIEN = {
    "etoro": "risk_state_etoro.json",
    "okx": "risk_state_okx.json",
}

# Der Name aus v6/v7, als es nur einen Broker gab.
ALTNAME_ETORO = "risk_state.json"


def wurzel() -> Path:
    """Das Verzeichnis der Zustandsdateien.

    Ein relativer Name wuerde beim Start als systemd-Dienst in einem voellig
    anderen Ordner landen -- und die Tagesbremse faenge nach jedem Neustart
    bei null an.
    """
    test_dir = os.getenv("TRADINGBOT_TEST_STATE_DIR", "").strip()
    return Path(test_dir) if test_dir else Path(__file__).resolve().parent


def zustandsdatei(broker: str) -> Path:
    """Der Pfad des Risikozustands eines Brokers."""
    name = str(broker or "").strip().lower()
    return wurzel() / ZUSTANDSDATEIEN.get(name, f"risk_state_{name or 'unbekannt'}.json")


# ---------------------------------------------------------------------------
# Umzug der alten Einzeldatei
# ---------------------------------------------------------------------------
def _inhalt(pfad: Path) -> dict:
    try:
        daten = json.loads(pfad.read_text(encoding="utf-8"))
        return daten if isinstance(daten, dict) else {}
    except Exception:
        return {}


def _hat_gehandelt(daten: dict) -> bool:
    """Steht in dieser Datei ein echter Handelstag?"""
    if not daten:
        return False
    if int(daten.get("trades_today", 0) or 0) > 0:
        return True
    for feld in ("realized_pnl_today", "estimated_costs_today", "gross_profit_today",
                 "gross_loss_today", "lifetime_realized_pnl"):
        try:
            if abs(float(daten.get(feld, 0) or 0)) > 1e-9:
                return True
        except (TypeError, ValueError):
            continue
    if int(daten.get("open_positions", 0) or 0) > 0:
        return True
    return bool(daten.get("trading_halted") or daten.get("equity_guard_halted"))


def _datum(daten: dict) -> str:
    return str(daten.get("current_date") or "")


def _beiseite_legen(pfad: Path) -> Path:
    """Eine Datei aufbewahren statt loeschen."""
    stempel = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    ziel = pfad.with_name(f"{pfad.name}.abgeloest-{stempel}")
    try:
        shutil.move(str(pfad), str(ziel))
    except Exception:
        logger.warning("Alte Risikodatei %s konnte nicht verschoben werden", pfad,
                       exc_info=True)
        return pfad
    return ziel


def umzug_etoro(*, melder=None) -> str:
    """``risk_state.json`` auf ``risk_state_etoro.json`` umstellen.

    Mehrfach aufrufbar, tut beim zweiten Mal nichts. Rueckgabe ist ein
    kurzer Text fuer das Protokoll -- leer heisst: nichts zu tun.
    """
    alt = wurzel() / ALTNAME_ETORO
    neu = zustandsdatei("etoro")
    if not alt.exists():
        return ""
    if alt.resolve() == neu.resolve():
        return ""

    alt_daten, neu_daten = _inhalt(alt), _inhalt(neu)
    alt_aktiv, neu_aktiv = _hat_gehandelt(alt_daten), _hat_gehandelt(neu_daten)

    if not neu.exists():
        try:
            shutil.move(str(alt), str(neu))
        except Exception:
            logger.exception("Risikozustand konnte nicht umgezogen werden")
            return ""
        text = (f"Risikozustand umgezogen: {ALTNAME_ETORO} -> {neu.name}. "
                "Aktienkern und Risikotopf lesen ab jetzt dieselbe Datei.")
    elif alt_aktiv and not neu_aktiv:
        # Georgs Fall: die neue Datei ist unberuehrt, die alte fuehrt den Tag.
        _beiseite_legen(neu)
        try:
            shutil.move(str(alt), str(neu))
        except Exception:
            logger.exception("Risikozustand konnte nicht umgezogen werden")
            return ""
        text = (f"Risikozustand zusammengefuehrt: die gefuehrten Werte aus "
                f"{ALTNAME_ETORO} gelten jetzt als {neu.name}; die leere "
                "Vorgaengerdatei wurde als .abgeloest aufbewahrt.")
    elif neu_aktiv and not alt_aktiv:
        gesichert = _beiseite_legen(alt)
        text = (f"Risikozustand: {neu.name} fuehrt bereits den Tag; die leere "
                f"{ALTNAME_ETORO} wurde als {gesichert.name} aufbewahrt.")
    else:
        # Beide gefuellt: hier wird NICHT geraten. Das juengere Datum fuehrt,
        # bei Gleichstand die kanonische Datei -- und beides steht im Klartext
        # in der Meldung, damit es nachpruefbar bleibt.
        alt_datum, neu_datum = _datum(alt_daten), _datum(neu_daten)
        if alt_datum > neu_datum:
            _beiseite_legen(neu)
            try:
                shutil.move(str(alt), str(neu))
            except Exception:
                logger.exception("Risikozustand konnte nicht umgezogen werden")
                return ""
            text = (f"Zwei gefuellte Risikozustaende gefunden. Der juengere "
                    f"({ALTNAME_ETORO}, {alt_datum}) gilt jetzt; der aeltere "
                    f"({neu_datum}) wurde als .abgeloest aufbewahrt. Bitte den "
                    "heutigen Tagesverlust im Broker gegenpruefen.")
        else:
            gesichert = _beiseite_legen(alt)
            text = (f"Zwei gefuellte Risikozustaende gefunden. {neu.name} "
                    f"({neu_datum}) gilt weiter; {ALTNAME_ETORO} ({alt_datum}) "
                    f"wurde als {gesichert.name} aufbewahrt. Bitte den heutigen "
                    "Tagesverlust im Broker gegenpruefen.")

    logger.warning("%s", text)
    if melder is not None:
        try:
            melder("RISIKOZUSTAND ZUSAMMENGEFUEHRT", text)
        except Exception:
            logger.debug("Umzugsmeldung nicht zustellbar", exc_info=True)
    return text


__all__ = ["ZUSTANDSDATEIEN", "ALTNAME_ETORO", "wurzel", "zustandsdatei",
           "umzug_etoro"]
