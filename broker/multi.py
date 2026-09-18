"""Gleichzeitiger Betrieb von eToro und OKX (BrokerHub).

WARUM
=====
Bis v6.0 gab es genau einen Broker und damit genau einen Zustand: verbunden
oder nicht. Mit zwei Brokern reicht das nicht mehr. Wenn OKX kurz nicht
erreichbar ist, darf der Aktienhandel nicht stehenbleiben -- und umgekehrt.

Der BrokerHub haelt deshalb pro Broker einen EIGENEN Zustand:

    VERBUNDEN     -- normal handelbar
    GESTOERT      -- vorher verbunden, aktuell Fehler; Wiederverbindung laeuft
    ABGEMELDET    -- bewusst deaktiviert (z.B. Krypto ausgeschaltet)
    ZUGANG_FEHLT  -- Zugangsdaten fehlen oder werden abgelehnt

Nur ein globaler Notaus stoppt beide Seiten gleichzeitig.

WICHTIG
=======
Der Hub trifft KEINE Handelsentscheidungen. Er beantwortet ausschliesslich:
"Welcher Broker ist fuer dieses Instrument zustaendig, und kann er gerade?"
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Iterable, Optional

from . import BrokerBase, broker_fuer_asset, get_broker
from .base import AuthentifizierungsFehler, BrokerFehler, NichtVerbunden, VerbindungVerloren

logger = logging.getLogger(__name__)

VERBUNDEN = "VERBUNDEN"
GESTOERT = "GESTOERT"
ABGEMELDET = "ABGEMELDET"
ZUGANG_FEHLT = "ZUGANG_FEHLT"


@dataclass
class BrokerZustand:
    """Laufzeitzustand genau eines Brokers."""
    name: str
    aktiv: bool = True
    status: str = ABGEMELDET
    letzter_kontakt: str = ""
    letzter_fehler: str = ""
    fehler_in_folge: int = 0
    naechster_versuch: float = 0.0
    verbunden_seit: str = ""

    @property
    def handelsbereit(self) -> bool:
        return self.aktiv and self.status == VERBUNDEN

    def als_dict(self) -> dict:
        return {
            "name": self.name,
            "aktiv": self.aktiv,
            "status": self.status,
            "handelsbereit": self.handelsbereit,
            "letzter_kontakt": self.letzter_kontakt,
            "letzter_fehler": self.letzter_fehler,
            "fehler_in_folge": self.fehler_in_folge,
            "verbunden_seit": self.verbunden_seit,
        }


class BrokerHub:
    """Verwaltet mehrere Broker parallel und routet nach Anlageklasse."""

    # Wartezeiten bis zum naechsten Verbindungsversuch (Sekunden).
    BACKOFF = (5, 15, 30, 60, 120, 300)
    # 9.5.3: Ein 5xx ist ein voruebergehender Serverfehler beim Broker, kein
    # Zugangs- oder Konfigurationsproblem. Am 02.09.2026 stand der Backoff
    # nach fuenf Minuten bei 300 s -- eine wiederhergestellte Verbindung waere
    # damit bis zu fuenf Minuten unbemerkt geblieben, bei 5-Minuten-Kerzen
    # eine ganze Kerze. Serverfehler bekommen deshalb einen eigenen, kuerzeren
    # Takt.
    BACKOFF_SERVERFEHLER = (5, 10, 20, 30, 45, 60)

    def __init__(self, *, factory: Callable[[str], BrokerBase] | None = None):
        self._factory = factory or (lambda name: get_broker(name))
        self._broker: dict[str, BrokerBase] = {}
        self._zustand: dict[str, BrokerZustand] = {}
        self._lock = threading.RLock()
        self._notaus = False
        self._notaus_grund = ""

    # -- Aufbau -------------------------------------------------------------
    def registriere(self, name: str, *, aktiv: bool = True) -> BrokerZustand:
        name = str(name).strip().lower()
        with self._lock:
            zustand = self._zustand.get(name) or BrokerZustand(name=name)
            zustand.aktiv = bool(aktiv)
            if not aktiv:
                zustand.status = ABGEMELDET
            self._zustand[name] = zustand
        return zustand

    @classmethod
    def aus_config(cls, config_modul=None) -> "BrokerHub":
        """Baut den Hub anhand der Konfiguration."""
        import config as _config
        cfg = config_modul or _config
        hub = cls()
        hub.registriere("etoro", aktiv=bool(getattr(cfg, "ETORO_ENABLED", True)))
        hub.registriere("okx", aktiv=bool(getattr(cfg, "OKX_ENABLED", False)))
        return hub

    # -- Verbindung ---------------------------------------------------------
    def verbinde(self, name: str) -> bool:
        name = str(name).strip().lower()
        with self._lock:
            zustand = self._zustand.get(name)
            if zustand is None or not zustand.aktiv:
                return False
            if self._notaus:
                return False
            if time.time() < zustand.naechster_versuch:
                return False
        try:
            broker = self._factory(name)
            broker.connect()
        except AuthentifizierungsFehler as exc:
            self._merke_fehler(name, str(exc), zugang_fehlt=True)
            logger.error("Broker %s: Zugangsdaten abgelehnt -- kein automatischer Neuversuch.", name)
            return False
        except Exception as exc:
            self._merke_fehler(name, f"{type(exc).__name__}: {exc}")
            logger.warning("Broker %s nicht verbunden: %s", name, exc)
            return False

        with self._lock:
            self._broker[name] = broker
            zustand = self._zustand[name]
            zustand.status = VERBUNDEN
            zustand.fehler_in_folge = 0
            zustand.letzter_fehler = ""
            zustand.naechster_versuch = 0.0
            zustand.verbunden_seit = datetime.now(timezone.utc).isoformat()
            zustand.letzter_kontakt = zustand.verbunden_seit
        logger.info("Broker %s verbunden: %s", name, broker.beschreibung())
        return True

    def verbinde_alle(self) -> dict[str, bool]:
        return {name: self.verbinde(name) for name in list(self._zustand)}

    def trenne_alle(self) -> None:
        with self._lock:
            broker = dict(self._broker)
            self._broker.clear()
        for name, b in broker.items():
            try:
                b.disconnect()
            except Exception:
                logger.debug("Trennen von %s fehlgeschlagen", name, exc_info=True)
            with self._lock:
                if name in self._zustand:
                    self._zustand[name].status = ABGEMELDET

    @staticmethod
    def _ist_serverfehler(text: str) -> bool:
        """Ein voruebergehender Brokerfehler, kein Zugangs-/Konfigproblem."""
        roh = str(text or "")
        return ("Serverfehler HTTP 5" in roh or "HTTP 502" in roh
                or "HTTP 503" in roh or "HTTP 504" in roh
                or "Ratenbegrenzung" in roh)

    def _merke_fehler(self, name: str, text: str, *, zugang_fehlt: bool = False) -> None:
        failed_broker = None
        serverfehler = self._ist_serverfehler(text)
        with self._lock:
            zustand = self._zustand.get(name)
            if zustand is None:
                return
            zustand.fehler_in_folge += 1
            zustand.letzter_fehler = text[:400]
            zustand.status = ZUGANG_FEHLT if zugang_fehlt else GESTOERT
            failed_broker = self._broker.pop(name, None)
            if zugang_fehlt:
                # Warten hilft bei falschen Zugangsdaten nicht. Erst nach
                # bewusster Neukonfiguration erneut versuchen.
                zustand.naechster_versuch = time.time() + 3600.0
            else:
                staffel = (self.BACKOFF_SERVERFEHLER if serverfehler
                           else self.BACKOFF)
                index = min(zustand.fehler_in_folge - 1, len(staffel) - 1)
                zustand.naechster_versuch = time.time() + staffel[index]
        if failed_broker is not None:
            try:
                failed_broker.disconnect()
            except Exception:
                logger.debug("Fehlerhaften Broker %s trennen fehlgeschlagen", name, exc_info=True)

    # -- Zugriff ------------------------------------------------------------
    def broker(self, name: str) -> Optional[BrokerBase]:
        with self._lock:
            if self._notaus:
                return None
            zustand = self._zustand.get(str(name).strip().lower())
            if zustand is None or not zustand.handelsbereit:
                return None
            return self._broker.get(str(name).strip().lower())

    def broker_fuer(self, instrument) -> Optional[BrokerBase]:
        """Welcher Broker handelt dieses Instrument?"""
        asset = str(getattr(instrument, "asset_type", "stock"))
        return self.broker(broker_fuer_asset(asset))

    def name_fuer(self, instrument) -> str:
        return broker_fuer_asset(str(getattr(instrument, "asset_type", "stock")))

    def aktive_broker(self) -> dict[str, BrokerBase]:
        with self._lock:
            if self._notaus:
                return {}
            return {n: b for n, b in self._broker.items() if self._zustand[n].handelsbereit}

    def zustaende(self) -> dict[str, dict]:
        with self._lock:
            return {n: z.als_dict() for n, z in self._zustand.items()}

    # -- Gesundheit ---------------------------------------------------------
    def pruefe_gesundheit(self) -> dict[str, bool]:
        """Leichter Erreichbarkeitstest je Broker; markiert Ausfaelle."""
        ergebnis: dict[str, bool] = {}
        for name in list(self._zustand):
            broker = self.broker(name)
            if broker is None:
                ergebnis[name] = False
                continue
            try:
                ok = bool(broker.health_check())
            except Exception as exc:
                self._merke_fehler(name, f"Health: {type(exc).__name__}: {exc}")
                ergebnis[name] = False
                continue
            if ok:
                with self._lock:
                    self._zustand[name].letzter_kontakt = (
                        broker.last_contact() or datetime.now(timezone.utc).isoformat())
            else:
                self._merke_fehler(name, "Health-Check negativ")
            ergebnis[name] = ok
        return ergebnis

    def wiederverbinde_gestoerte(self) -> dict[str, bool]:
        """Versucht gestoerte Broker erneut zu verbinden (mit Backoff)."""
        out: dict[str, bool] = {}
        for name, zustand in list(self._zustand.items()):
            if zustand.status == GESTOERT and zustand.aktiv:
                out[name] = self.verbinde(name)
        return out

    # -- Notaus -------------------------------------------------------------
    def notaus(self, grund: str = "") -> None:
        """Globaler Notaus: stoppt beide Broker gleichzeitig."""
        with self._lock:
            self._notaus = True
            self._notaus_grund = str(grund or "manueller Notaus")
        logger.critical("GLOBALER NOTAUS aktiv: %s", self._notaus_grund)

    def notaus_aufheben(self) -> None:
        with self._lock:
            self._notaus = False
            self._notaus_grund = ""

    @property
    def notaus_aktiv(self) -> bool:
        with self._lock:
            return self._notaus

    # -- Uebersicht ---------------------------------------------------------
    def uebersicht(self) -> dict:
        """Kompakter Zustand fuer Dashboard, Telegram und Health-Seite."""
        with self._lock:
            notaus, grund = self._notaus, self._notaus_grund
        broker_info = {}
        for name, zustand in self.zustaende().items():
            eintrag = dict(zustand)
            broker = self._broker.get(name)
            if broker is not None:
                try:
                    eintrag["beschreibung"] = broker.beschreibung()
                    eintrag["paper"] = bool(broker.ist_paper())
                except Exception:
                    logger.debug("Broker-Beschreibung nicht lesbar", exc_info=True)
            broker_info[name] = eintrag
        return {
            "notaus": notaus,
            "notaus_grund": grund,
            "broker": broker_info,
            "handelsbereit": [n for n, e in broker_info.items() if e.get("handelsbereit")],
        }


__all__ = ["BrokerHub", "BrokerZustand", "VERBUNDEN", "GESTOERT", "ABGEMELDET", "ZUGANG_FEHLT"]
