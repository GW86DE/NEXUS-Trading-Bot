"""Handelsbereitschaft: TRADING_READY plus Anlaufsperre (neu in v8.1.4).

WARUM DIESES MODUL EXISTIERT
============================
Am 25.08.2026 hat der Bot **2,5 Sekunden** nach dem Start eine Marktorder
ausgeloest. Zwischen Prozessstart und Kauforder lagen: Universumsabfrage,
Technikpruefung, Kerzenabruf, Order. Der Kontostand war zu diesem Zeitpunkt
noch nie gegen das Positionsbuch abgeglichen -- und genau dieser fehlende
Abgleich hat kurz darauf einen Fremdbestand von 0,94 BTC mitverkauft.

Eine Anlaufsperre gab es in keiner Version. Die einzigen ``startup_grace``-
Werte im Code (600 s, 900 s) betreffen ausschliesslich den systemd-Watchdog.

Der Gedanke stand als P0 im Backlog: ``HB-007 TRADING_READY getrennt von
BROKER_CONNECTED`` -- "Erreichbarer Broker bedeutet nicht automatisch, dass
sicher gehandelt werden darf." Gebaut wurde er nie.

ZWEI BEDINGUNGEN, BEIDE MUESSEN ERFUELLT SEIN
=============================================
1. **TRADING_READY** -- alle fachlichen Voraussetzungen liegen vor.
2. **Mindestwartezeit** -- auch wenn 1 frueher gruen wird. Eine Pruefung kann
   gruen werden, weil alle Schnittstellen schnell antworten; die 15-Minuten-
   Signalkerze ist deshalb trotzdem noch nicht abgeschlossen.

WAS DIE SPERRE NICHT BETRIFFT
=============================
Ausschliesslich NEUE EINSTIEGE warten. Sofort und ohne Wartezeit laufen:
Verkaeufe, Stops, Ziele, Zeitstopps, das Setzen und Nachziehen der
Broker-Schutzorders, die Positions-Reconciliation, der Universumslauf,
Nachrichten und Meldungen.

Eine bestehende Position darf nach einem Neustart keine Sekunde ungeschuetzt
sein. Genau das war der Fall vom 25.08.: Die BTC-Position lief ueber den
Neustart hinweg weiter.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional

from safe_persistence import best_effort_json

logger = logging.getLogger(__name__)


# Die Handelsbereitschaft selbst wird bewusst nicht persistiert: Nach einem
# Neustart muessen Konto, Schutzorders und Universum immer erneut geprueft
# werden. Persistiert wird ausschliesslich, ob die *Information* "bereit"
# bereits versendet wurde. Sonst erzeugt jeder saubere Dienstneustart dieselbe
# Telegram-Meldung, obwohl sich am Handelszustand nichts geaendert hat.
_MELDESTATUS_DATEI = "trading_ready_notifications.json"


def _meldestatus_pfad() -> Path:
    wurzel = os.getenv("TRADINGBOT_TEST_STATE_DIR", "").strip()
    return Path(wurzel or Path(__file__).resolve().parent) / _MELDESTATUS_DATEI


def _lade_meldezustand(domaene: str) -> dict:
    try:
        daten = json.loads(_meldestatus_pfad().read_text(encoding="utf-8"))
        bereiche = daten.get("domaenen", {}) if isinstance(daten, dict) else {}
        eintrag = bereiche.get(str(domaene).lower(), {}) if isinstance(bereiche, dict) else {}
        return dict(eintrag) if isinstance(eintrag, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


@dataclass
class Bedingung:
    """Eine einzelne Voraussetzung fuer den Handel."""
    name: str
    beschreibung: str
    erfuellt: bool = False
    detail: str = ""
    seit: str = ""

    def setze(self, erfuellt: bool, detail: str = "") -> bool:
        """Zustand setzen. Gibt True zurueck, wenn sich etwas geaendert hat."""
        vorher = self.erfuellt
        self.erfuellt = bool(erfuellt)
        self.detail = str(detail or "")[:200]
        if self.erfuellt != vorher:
            self.seit = datetime.now(timezone.utc).isoformat()
            return True
        return False

    def als_dict(self) -> dict:
        return {"name": self.name, "beschreibung": self.beschreibung,
                "erfuellt": self.erfuellt, "detail": self.detail, "seit": self.seit}


# Die fachlichen Voraussetzungen. Reihenfolge = Anzeigereihenfolge.
STANDARD_BEDINGUNGEN: tuple[tuple[str, str], ...] = (
    ("broker_verbunden", "Broker authentifiziert und erreichbar"),
    ("systemzeit", "Systemzeit plausibel"),
    ("instrumente", "Instrumentkatalog geladen, Identitaet eindeutig"),
    ("handelsregeln", "Tick, Lot und Mindestgroesse bekannt"),
    ("guthaben", "Kontostand und freies Guthaben abrufbar"),
    ("gebuehren", "Gebuehrensatz bekannt"),
    ("reconciliation", "Positionen einmal mit dem Konto abgeglichen"),
    ("universum", "Universumslauf einmal durchgelaufen"),
    ("kursdaten", "Kursdaten aktuell"),
    ("signalkerze", "Mindestens eine vollstaendige Signalkerze seit dem Start"),
    # Beide werden von der Domaene gemessen, nicht vom Anlauftimer abgeleitet.
    ("keine_unklare_order", "Ausfuehrungsabgleich abgeschlossen"),
    # 9.5.2: Eigene Bedingung fuer die Buchungsluecke. Bis 9.5.1 lief sie
    # unter "ungeklaerte Order" mit -- fachlich falsch, weil beim Broker
    # nichts mehr offen ist. Sie sperrt trotzdem: solange ein abgeschlossener
    # Trade unverbucht ist, kennt die Tagesverlustgrenze ihren eigenen Wert
    # nicht. Nur der GRUND heisst jetzt richtig.
    ("buchung_vollstaendig", "Abgeschlossene Trades vollstaendig verbucht"),
    ("protokoll", "Entscheidungsprotokoll beschreibbar"),
)

# KORREKTUR 9.5.2: Der Text zu "keine_unklare_order" lautete fuer ALLE Broker
# fest "eToro-Ausfuehrungsabgleich abgeschlossen". Auf der OKX-Seite stand
# damit ein Sperrgrund, der einen fremden Broker nannte -- am 01./02.09.2026
# war die Ursache tatsaechlich eine eigene OKX-FOK-Order (FET-EUR), und die
# Meldung zeigte in die vollkommen falsche Richtung.
BROKER_TEXTE: dict[str, dict[str, str]] = {
    "etoro": {"keine_unklare_order": "eToro-Ausfuehrungsabgleich abgeschlossen"},
    "okx": {
        "keine_unklare_order": "OKX-Ausfuehrungsabgleich abgeschlossen",
        "guthaben": "Kontostand gueltig und freigegebenes Handelskapital vorhanden",
    },
}


def bedingungstext(domaene: str, name: str, standard: str) -> str:
    """Beschreibung einer Bedingung fuer genau diese Brokerdomaene."""
    return (BROKER_TEXTE.get(str(domaene or "").lower(), {}).get(name)
            or standard)


class Handelsbereitschaft:
    """Fuehrt den TRADING_READY-Zustand einer Brokerdomaene.

    Bewusst ohne eigene Broker-Aufrufe: die Domaene meldet ihre Bedingungen
    selbst. So kann dieses Modul niemals eine Netzabfrage ausloesen und damit
    einen Zyklus aufhalten.
    """

    def __init__(self, domaene: str, *, cfg=None, melder: Optional[Callable] = None,
                 jetzt: Optional[Callable[[], float]] = None):
        import config as _config
        self.cfg = cfg or _config
        self.domaene = str(domaene).lower()
        self.melder = melder
        self._jetzt = jetzt or time.monotonic
        self._start = self._jetzt()
        self._lock = threading.RLock()
        self._expires = {}
        self.market_status = {}
        # Ein Neustart darf den fachlichen Check nicht ueberspringen, wohl aber
        # eine identische "handelsbereit"-Info. Erst wenn dieser Prozess die
        # Bereitschaft einmal selbst gesehen hat, darf ein spaeterer Verlust
        # den gespeicherten Zustand wieder auf "nicht bereit" setzen.
        meldezustand = _lade_meldezustand(self.domaene)
        self._persistiert_frei = bool(meldezustand.get("bereit", False))
        self._hatte_freigabe = bool(meldezustand.get("hatte_freigabe", False))
        self._gemeldet_frei = self._persistiert_frei
        self._in_diesem_lauf_bereit = False
        self.bedingungen: dict[str, Bedingung] = {
            name: Bedingung(name, bedingungstext(self.domaene, name, text))
            for name, text in STANDARD_BEDINGUNGEN
        }

    def _speichere_meldezustand(self, bereit: bool) -> None:
        """Merkt nur den Informationszustand; Fehler blockieren nie den Bot."""
        with self._lock:
            self._persistiert_frei = bool(bereit)
            self._hatte_freigabe = self._hatte_freigabe or bool(bereit)
            pfad = _meldestatus_pfad()
            try:
                daten = json.loads(pfad.read_text(encoding="utf-8")) if pfad.exists() else {}
            except (OSError, ValueError, TypeError):
                daten = {}
            if not isinstance(daten, dict):
                daten = {}
            bereiche = daten.get("domaenen", {})
            if not isinstance(bereiche, dict):
                bereiche = {}
            bereiche[self.domaene] = {
                "bereit": bool(bereit),
                "hatte_freigabe": self._hatte_freigabe,
                "aktualisiert_am": datetime.now(timezone.utc).isoformat(),
            }
            daten["format"] = 1
            daten["domaenen"] = bereiche
        best_effort_json(pfad, daten, label="Handelsbereitschaft-Meldestatus", durable=False)

    # -- Konfiguration ------------------------------------------------------
    @property
    def wartezeit_sekunden(self) -> float:
        minuten = float(getattr(self.cfg, "STARTUP_TRADING_GRACE_MINUTES", 15.0))
        return max(0.0, minuten * 60.0)

    # -- Bedingungen --------------------------------------------------------
    def melde(self, name: str, erfuellt: bool, detail: str = "", *,
              gueltig_fuer: Optional[float] = None) -> None:
        """Eine Bedingung aktualisieren. Unbekannte Namen werden angelegt."""
        with self._lock:
            bedingung = self.bedingungen.get(name)
            if bedingung is None:
                bedingung = Bedingung(name, name)
                self.bedingungen[name] = bedingung
            if gueltig_fuer is not None:
                import math
                duration = float(gueltig_fuer)
                if not math.isfinite(duration) or duration <= 0:
                    erfuellt = False
                    detail = "Gueltigkeitsdauer des Nachweises fehlt"
                self._expires[name] = self._jetzt() + max(0.0, duration) if erfuellt else self._jetzt()
            elif not erfuellt:
                self._expires.pop(name, None)
            geaendert = bedingung.setze(erfuellt, detail)
        if geaendert and not erfuellt:
            logger.info("%s: Handelsbereitschaft verloren -- %s (%s)",
                        self.domaene.upper(), bedingung.beschreibung, detail)

    def offene_bedingungen(self) -> list[Bedingung]:
        with self._lock:
            for name, deadline in list(self._expires.items()):
                if self._jetzt() >= deadline:
                    self.bedingungen[name].setze(False, "Kurs-/Datenbeleg abgelaufen; frischer Nachweis erforderlich")
                    del self._expires[name]
            return [b for b in self.bedingungen.values() if not b.erfuellt]

    @staticmethod
    def _blocktext(bedingung: Bedingung) -> str:
        """Eine *nicht* erfuellte Voraussetzung unmissverstaendlich benennen.

        Bis 9.5.0 wurde beim roten Zustand die positive Beschreibung
        ``Keine ungeklaerte Order offen`` ausgegeben. Das las sich wie eine
        Entwarnung, obwohl genau diese Voraussetzung nicht erfuellt war. Ein
        vom fachlichen Aufrufer gelieferter Detailtext ist deshalb der primaere
        Sperrgrund; ohne Detail wird die fehlende Voraussetzung explizit als
        solche gekennzeichnet.
        """
        detail = str(bedingung.detail or "").strip()
        basis = f"Nicht erfuellt: {bedingung.beschreibung}"
        return f"{basis} ({detail})" if detail else basis

    @property
    def bereit(self) -> bool:
        """Alle fachlichen Voraussetzungen erfuellt?"""
        return not self.offene_bedingungen()

    # -- Anlaufsperre -------------------------------------------------------
    def kerzen_vollstaendig(self, kerzengroesse_sekunden: float) -> bool:
        """Ist seit dem Start mindestens eine volle Signalkerze vergangen?

        Bewusst eine eigene Messung und keine Umformulierung der
        Anlaufsperre: wer die Wartezeit auf eine Minute stellt, hat deshalb
        noch lange keine abgeschlossene 15-Minuten-Kerze.
        """
        try:
            dauer = float(kerzengroesse_sekunden)
        except (TypeError, ValueError):
            return False
        if dauer <= 0:
            return False
        return (self._jetzt() - self._start) >= dauer

    @property
    def restwartezeit(self) -> float:
        verstrichen = self._jetzt() - self._start
        return max(0.0, self.wartezeit_sekunden - verstrichen)

    def pruefe(self) -> tuple[bool, str]:
        """Reine Auskunft: duerfen JETZT neue Einstiege stattfinden?

        Ohne Nebenwirkung -- diese Methode meldet nichts. Nur so kann eine
        Statusabfrage die Freigabemeldung nicht verbrauchen.
        """
        rest = self.restwartezeit
        if rest > 0:
            return False, (f"Anlaufsperre: neue Kaeufe frei in "
                           f"{self._minuten(rest)} ({self.freigabe_uhrzeit()})")
        offen = self.offene_bedingungen()
        if offen:
            if any(b.name == "marktsitzung" for b in offen) and self.market_status.get("offen") is False:
                others = [b for b in offen if b.name not in {"marktsitzung", "kursdaten", "signalkerze"}]
                reason = ("US-Markt geschlossen; wartet auf Boersenoeffnung " +
                          str(self.market_status.get("naechste_oeffnung") or "laut Kalender"))
                if others:
                    reason += ". Weitere Sperren: " + "; ".join(self._blocktext(b) for b in others[:3])
                return False, reason
            namen = "; ".join(self._blocktext(b) for b in offen[:3])
            weitere = f" (+{len(offen) - 3})" if len(offen) > 3 else ""
            return False, f"Kaeufe gesperrt: {namen}{weitere}"
        return True, "handelsbereit"

    def darf_kaufen(self) -> tuple[bool, str]:
        """Wie ``pruefe()``, meldet aber den Uebergang zur Handelsbereitschaft.

        Verkaeufe, Stops und Schutzorders fragen hier nie -- sie laufen
        immer.
        """
        erlaubt, grund = self.pruefe()
        if not erlaubt:
            # Beim Start sind die Bedingungen erwartbar noch rot. Dieses
            # erwartete Anlaufen darf den gespeicherten Gruenzustand nicht
            # loeschen, sonst wuerde ein Neustart sofort wieder Telegram-Spam
            # vorbereiten. Ein echter Verlust NACH einer Freigabe dagegen
            # macht die folgende Wiederherstellung meldepflichtig.
            with self._lock:
                verloren = self._in_diesem_lauf_bereit and self._gemeldet_frei
                if verloren:
                    self._gemeldet_frei = False
                    self._in_diesem_lauf_bereit = False
            if verloren:
                self._speichere_meldezustand(False)
            return erlaubt, grund

        with self._lock:
            neu_zu_melden = not self._gemeldet_frei
            war_frueher_bereit = self._hatte_freigabe
            self._gemeldet_frei = True
            self._in_diesem_lauf_bereit = True
        if neu_zu_melden:
            self._speichere_meldezustand(True)
            zusatz = "wieder " if war_frueher_bereit else ""
            self._melde(f"🟢 {self.domaene.upper()}: {zusatz}handelsbereit — "
                        "neue Kaeufe sind jetzt moeglich.")
        return erlaubt, grund

    def freigabe_uhrzeit(self) -> str:
        """Ortszeit, zu der die Anlaufsperre endet."""
        rest = self.restwartezeit
        if rest <= 0:
            return "jetzt"
        return (datetime.now().astimezone() + timedelta(seconds=rest)).strftime("%H:%M")

    @staticmethod
    def _minuten(sekunden: float) -> str:
        minuten = int(sekunden // 60)
        rest = int(sekunden % 60)
        if minuten <= 0:
            return f"{rest} s"
        return f"{minuten} min" + (f" {rest} s" if rest else "")

    def _melde(self, text: str) -> None:
        logger.info(text)
        if self.melder is None:
            return
        try:
            self.melder(text)
        except Exception:
            logger.debug("Bereitschaftsmeldung nicht zustellbar", exc_info=True)

    # -- Anzeige ------------------------------------------------------------
    def status(self) -> dict:
        rest = self.restwartezeit
        erlaubt, grund = self.pruefe()
        with self._lock:
            bedingungen = [b.als_dict() for b in self.bedingungen.values()]
            offen = [b for b in self.bedingungen.values() if not b.erfuellt]
        # ``jetzt`` ist nur eine sinnvolle Zeitangabe, wenn wirklich alle
        # fachlichen Bedingungen erfuellt sind. Bei einem Reconciliation- oder
        # Datenblock gibt es keine vorhersagbare Uhrzeit; 9.5.0 zeigte dort
        # irrefuehrend "Frueheste Freigabe: jetzt".
        freigabe_um = self.freigabe_uhrzeit() if (rest > 0 or erlaubt) else ""
        return {
            "domaene": self.domaene,
            "market_status": dict(self.market_status),
            "market_wait_only": bool(self.market_status.get("offen") is False and
                "marktsitzung" in self.bedingungen and
                all(b.name in {"marktsitzung", "kursdaten", "signalkerze"} for b in offen)) ,
            "trading_ready": self.bereit,
            "kaeufe_erlaubt": erlaubt,
            "grund": grund,
            "restwartezeit_sekunden": round(rest, 1),
            "safety_wait": {"configured_seconds": self.wartezeit_sekunden,
                            "remaining_seconds": round(rest, 1), "active": rest > 0,
                            "detail": "Separate Sicherheits-Anlaufsperre; unabhaengig vom Signalkerzen-Takt"},
            "freigabe_um": freigabe_um,
            "offen": [self._blocktext(b) for b in offen],
            "bedingungen": bedingungen,
        }

    def kurzfassung(self) -> str:
        erlaubt, grund = self.pruefe()
        return "neue Kaeufe frei" if erlaubt else grund


__all__ = ["Handelsbereitschaft", "Bedingung", "STANDARD_BEDINGUNGEN"]
