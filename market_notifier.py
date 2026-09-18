"""Meldungen zu Boersenoeffnung und -schluss ueber Telegram.

ZWECK
=====
Zwei Dinge in einem:

1. Du bekommst eine Nachricht, wenn der US-Aktienmarkt oeffnet und schliesst --
   inklusive Hinweis auf verkuerzte Handelstage und den naechsten Handelstag.

2. Der Bot spart Arbeit. Ausserhalb der Handelszeit und an Feiertagen sind
   Kursabruf, Nachrichtenpruefung und KI-Aufmerksamkeit fuer Aktien wertlos.
   Das betrifft insbesondere kostenpflichtige OpenAI-Aufrufe.

ZUSTAND UEBERLEBT NEUSTARTS
===========================
Ohne persistenten Zustand wuerde jeder Neustart eine neue Oeffnungsmeldung
ausloesen. Deshalb wird der zuletzt gemeldete Zustand in einer Datei gehalten.
Ein Neustart mitten in der Sitzung erzeugt keine Doppelmeldung.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import config
from market_calendar import sitzungsstatus, naechster_handelstag, NY

logger = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parent
STATE_ROOT = Path(os.getenv("TRADINGBOT_TEST_STATE_DIR", "").strip() or ROOT)
STATE = STATE_ROOT / "market_session_state.json"

LOKAL = ZoneInfo(getattr(config, "LOCAL_TIMEZONE", "Europe/Berlin"))


def _lade() -> dict:
    try:
        if STATE.exists():
            return json.loads(STATE.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.debug("Sitzungszustand nicht lesbar: %s", exc)
    return {}


def _speichere(daten: dict) -> None:
    try:
        tmp = STATE.with_suffix(".tmp")
        tmp.write_text(json.dumps(daten, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(STATE)
    except Exception as exc:
        logger.debug("Sitzungszustand nicht speicherbar: %s", exc)


def _lokal(dt) -> str:
    if not dt:
        return "-"
    return dt.astimezone(LOKAL).strftime("%d.%m.%Y %H:%M")


def _oeffnungstext(st: dict) -> str:
    zeilen = ["Der US-Aktienmarkt ist ab jetzt geoeffnet.", ""]
    zeilen.append(f"Handelsschluss: {_lokal(st['schluss'])} (deine Zeit)")
    if st["verkuerzt"]:
        zeilen.append("")
        zeilen.append(f"ACHTUNG: Verkuerzter Handelstag ({st['verkuerzt_grund']}).")
        zeilen.append("Schluss bereits um 13:00 Ortszeit New York statt 16:00.")
    return "\n".join(zeilen)


def _schlusstext(st: dict) -> str:
    naechste = st.get("naechste_oeffnung")
    zeilen = ["Der US-Aktienmarkt ist geschlossen.", ""]
    if naechste:
        zeilen.append(f"Naechste Oeffnung: {_lokal(naechste)} (deine Zeit)")
        tag = naechste.astimezone(NY).date()
        from market_calendar import verkuerzte_tage
        kurz = verkuerzte_tage(tag.year).get(tag)
        if kurz:
            zeilen.append(f"Hinweis: verkuerzter Handelstag ({kurz}).")
    zeilen.append("")
    zeilen.append("Verkaeufe, Stops und Krypto laufen unabhaengig davon weiter.")
    return "\n".join(zeilen)


def pruefe_und_melde(notify_func=None, *, jetzt: datetime | None = None) -> str | None:
    """
    Vergleicht den aktuellen Sitzungszustand mit dem zuletzt gemeldeten und
    verschickt bei einem Wechsel genau eine Nachricht.

    Gibt den ausgeloesten Ereignistyp zurueck ("OPEN"/"CLOSE") oder None.
    """
    if not bool(getattr(config, "MARKET_SESSION_ALERTS", True)):
        return None

    st = sitzungsstatus(jetzt)
    jetzt_ny = st["jetzt_ny"]
    heute = jetzt_ny.date().isoformat()
    zustand = _lade()

    aktuell = "OFFEN" if st["offen"] else "ZU"
    vorher = str(zustand.get("phase") or "")
    letzter_tag = str(zustand.get("tag") or "")

    # Erststart: nur merken, nicht melden. Sonst kaeme nach jeder Neuinstallation
    # eine Meldung, die keinen Zustandswechsel beschreibt.
    if not vorher:
        _speichere({"phase": aktuell, "tag": heute, "gemeldet_am": jetzt_ny.isoformat()})
        return None

    if aktuell == vorher and (aktuell == "OFFEN" or letzter_tag == heute):
        return None

    ereignis = "OPEN" if aktuell == "OFFEN" else "CLOSE"
    if notify_func is None:
        from notifier import notify as notify_func  # spaet importieren, testbar

    try:
        if ereignis == "OPEN":
            notify_func("BOERSE GEOEFFNET", _oeffnungstext(st))
        else:
            notify_func("BOERSE GESCHLOSSEN", _schlusstext(st))
    except Exception as exc:
        logger.warning("Marktmeldung konnte nicht gesendet werden: %s", exc)
        return None

    _speichere({"phase": aktuell, "tag": heute, "gemeldet_am": jetzt_ny.isoformat()})
    logger.info("Marktsitzung gemeldet: %s", ereignis)
    return ereignis


def sitzungszeile() -> str:
    """Kurze Statuszeile fuer GUI und Berichte."""
    st = sitzungsstatus()
    if not st["handelstag"]:
        return f"US-Markt geschlossen ({st['grund']}) · naechste Oeffnung {_lokal(st['naechste_oeffnung'])}"
    if st["offen"]:
        zusatz = " · verkuerzt" if st["verkuerzt"] else ""
        return f"US-Markt OFFEN bis {_lokal(st['schluss'])}{zusatz}"
    if st["phase"] == "VORBOERSLICH":
        return f"US-Markt oeffnet {_lokal(st['oeffnung'])}"
    return f"US-Markt geschlossen · naechste Oeffnung {_lokal(st['naechste_oeffnung'])}"
