"""
Dauerhafter Betriebszustand des Bots.

WOZU
====
Damit du den Bot unterwegs per Telegram anhalten kannst, muss irgendwo
stehen, ob er handeln darf. Diese Information muss einen Neustart
ueberleben -- sonst wuerde ein Stromausfall oder ein Absturz den
Not-Stopp aufheben und der Bot faengt beim Neustart wieder an zu handeln.

Deshalb liegt der Zustand in einer Datei, nicht nur im Arbeitsspeicher.

DREI ZUSTAENDE
==============
  AKTIV      -- normaler Betrieb: kaufen und verkaufen erlaubt
  PAUSIERT   -- KEINE neuen Kaeufe, bestehende Positionen werden aber
                weiter ueberwacht und bei Stop/Ziel/Signal verkauft.
                Das ist der Zustand fuer "erstmal nichts Neues".
  GESTOPPT   -- keine neuen Kaeufe UND keine automatischen Verkaeufe.
                Der Bot ruehrt nichts mehr an.

WICHTIGER UNTERSCHIED ZWISCHEN PAUSE UND STOPP
-----------------------------------------------
GESTOPPT klingt sicherer, ist es aber nicht automatisch: Wenn der Bot
nicht mehr verkauft, laufen offene Positionen ohne seine Aufsicht weiter.
Bei eToro bleibt ein bereits vom Broker bestaetigter Stop-Loss auch dann
serverseitig aktiv. Nicht bestaetigte oder manuell veraenderte Positionen
duerfen darauf aber nicht blind vertrauen.

Deshalb ist PAUSE in den meisten Faellen die bessere Wahl: Sie sperrt nur
Neukaeufe, waehrend Schutz- und Verkaufslogik weiterlaufen.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
import os
from pathlib import Path
from safe_persistence import atomic_write_json, atomic_write_text
from threading import RLock

logger = logging.getLogger(__name__)

AKTIV = "aktiv"
PAUSIERT = "pausiert"
GESTOPPT = "gestoppt"

GUELTIGE_ZUSTAENDE = (AKTIV, PAUSIERT, GESTOPPT)



def _zustandswurzel() -> Path:
    """Das Botverzeichnis -- niemals das Arbeitsverzeichnis.

    v9.1: Ein relativer Dateiname loeste bisher gegen das ARBEITSVERZEICHNIS
    auf. Startet der Bot einmal aus einem anderen Ordner, schriebe er seinen
    Zustand woanders hin -- der Not-Aus griffe ins Leere und der
    Ordereigentumsnachweis waere weg. Genau diese Regel wurde in v8.1.5 fuer
    den Risikozustand eingefuehrt (risk_manager._zustandswurzel) und hier
    nachgezogen.
    """
    test_dir = os.getenv("TRADINGBOT_TEST_STATE_DIR", "").strip()
    return Path(test_dir) if test_dir else Path(__file__).resolve().parent

class BotZustand:
    def __init__(self, pfad: str = "bot_zustand.json"):
        kandidat = Path(pfad)
        self.pfad = kandidat if kandidat.is_absolute() else _zustandswurzel() / kandidat.name
        self._lock = RLock()
        self._daten = {
            "zustand": AKTIV,
            "grund": "Erststart",
            "geaendert_am": self._jetzt(),
            "geaendert_von": "system",
            "letzter_tagesbericht": None,
        }
        self._laden()

    # ---------------------------------------------------------------
    @staticmethod
    def _jetzt() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _laden(self):
        with self._lock:
            if not self.pfad.exists():
                self._speichern()
                return
            try:
                gelesen = json.loads(self.pfad.read_text(encoding="utf-8"))
                if gelesen.get("zustand") in GUELTIGE_ZUSTAENDE:
                    self._daten.update(gelesen)
                else:
                    raise ValueError(f"unbekannter Zustand {gelesen.get('zustand')!r}")
            except Exception as exc:
                # Bei beschaedigter Datei bewusst auf PAUSIERT gehen, nicht
                # auf AKTIV: im Zweifel lieber nicht handeln.
                logger.warning("Zustandsdatei unlesbar (%s) -- setze auf PAUSIERT.", exc)
                self._daten["zustand"] = PAUSIERT
                self._daten["grund"] = f"Zustandsdatei fehlerhaft: {exc}"
                self._speichern()

    def _speichern(self):
        with self._lock:
            self._daten["geaendert_am"] = self._jetzt()
            # Erst in eine temporaere Datei schreiben, dann umbenennen --
            # so kann ein Absturz mitten im Schreiben die Datei nicht
            # zerstoeren.
            atomic_write_json(self.pfad, self._daten)

            # Zusaetzlich als reine Textdatei: das Startmenue (Batch) kann
            # kein JSON lesen, aber problemlos eine Zeile Text. Frueher
            # wurde dort per findstr im JSON gesucht -- das war fehleranfaellig.
            try:
                atomic_write_text(self.pfad.with_name("bot_zustand.txt"), self._daten["zustand"])
            except Exception:
                __import__("logging").getLogger(__name__).debug("Best-effort-Ausnahme unterdrueckt", exc_info=True)

    # ---------------------------------------------------------------
    def setze(self, zustand: str, grund: str = "", quelle: str = "menue"):
        if zustand not in GUELTIGE_ZUSTAENDE:
            raise ValueError(f"Unbekannter Zustand {zustand!r}")
        with self._lock:
            vorher = self._daten["zustand"]
            self._daten["zustand"] = zustand
            self._daten["grund"] = grund or f"gesetzt via {quelle}"
            self._daten["geaendert_von"] = quelle
            self._speichern()
            logger.info("Zustand: %s -> %s (%s, via %s)", vorher, zustand, grund, quelle)
            return vorher

    def zustand(self) -> str:
        with self._lock:
            self._laden()          # frisch lesen: Telegram kann ihn
            return self._daten["zustand"]   # zwischenzeitlich geaendert haben

    def darf_kaufen(self) -> bool:
        return self.zustand() == AKTIV

    def darf_verkaufen(self) -> bool:
        """Bei PAUSE weiterhin ja -- nur GESTOPPT unterbindet auch Verkaeufe."""
        return self.zustand() in (AKTIV, PAUSIERT)

    def momentaufnahme(self) -> dict:
        with self._lock:
            self._laden()
            return dict(self._daten)

    # ---------------------------------------------------------------
    def tagesbericht_faellig(self, heute_key: str) -> bool:
        with self._lock:
            self._laden()
            return self._daten.get("letzter_tagesbericht") != heute_key

    def tagesbericht_vermerken(self, heute_key: str):
        with self._lock:
            self._daten["letzter_tagesbericht"] = heute_key
            self._speichern()

    # ---------------------------------------------------------------
    def beschreibung(self) -> str:
        d = self.momentaufnahme()
        z = d["zustand"]
        erklaerung = {
            AKTIV: "Der Bot handelt normal (Kaeufe und Verkaeufe erlaubt).",
            PAUSIERT: ("Keine neuen Kaeufe. Bestehende Positionen werden weiter "
                       "ueberwacht und bei Stop, Ziel oder Verkaufssignal geschlossen."),
            GESTOPPT: ("Der Bot handelt gar nicht mehr -- auch keine Verkaeufe. "
                       "Offene Positionen laufen ohne seine Aufsicht weiter."),
        }[z]
        try:
            geaendert = datetime.fromisoformat(d["geaendert_am"]).astimezone()
            zeit = geaendert.strftime("%d.%m.%Y %H:%M")
        except Exception:
            zeit = d["geaendert_am"]
        return (f"Zustand : {z.upper()}\n"
                f"{erklaerung}\n"
                f"Grund   : {d.get('grund', '-')}\n"
                f"Geaendert: {zeit} (via {d.get('geaendert_von', '?')})")
