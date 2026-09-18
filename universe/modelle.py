"""Datentypen und Zustandsverwaltung des dynamischen Universums (v8.1.1 NEXUS).

VIER EBENEN (DU-G001)
=====================
    BROKER_CATALOG   alles, was der Broker ueberhaupt anbietet
    ELIGIBLE_POOL    was die harten Sicherheitsfilter besteht
    ACTIVE_UNIVERSE  was tatsaechlich beobachtet wird (groessenbegrenzt)
    FOCUS_SET        der kleine Teil, der teure Analyse bekommt

Je tiefer die Ebene, desto aufwendiger darf die Analyse werden. Genau das
haelt Kosten und API-Last unter Kontrolle.

ZUSTAENDE EINES MITGLIEDS
=========================
    BEOBACHTUNG   aufgenommen, aber noch NICHT handelbar (Bewaehrung)
    AKTIV         handelbar
    ABGANG        Entfernung angekuendigt, wartet auf Mehrfachbestaetigung
    ENTFERNT      raus aus dem Entry-Universe

Ein Wert mit offener Position bleibt IMMER im Monitoring, egal in welchem
Zustand er ist (DU-010). Exposure ohne Ueberwachung ist der gefaehrlichste
Zustand, den ein Bot haben kann.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

logger = logging.getLogger(__name__)

# Zustaende
BEOBACHTUNG = "BEOBACHTUNG"
AKTIV = "AKTIV"
ABGANG = "ABGANG"
ENTFERNT = "ENTFERNT"

# Aufnahmewege
TIER_ETABLIERT = "ETABLIERT"      # direkt handelbar
TIER_KANDIDAT = "KANDIDAT"        # erst Bewaehrung
TIER_FAVORIT = "FAVORIT"          # Nutzerwunsch, feste Slots
TIER_GEPINNT = "GEPINNT"          # offene Position
TIER_KERN = "KERN"                # fester Kern: nie durch Rang entfernbar

# Ein Kernwert, den ein harter Sicherheitsfilter blockiert. Er bleibt im
# Universum und im Monitoring, darf aber keine neuen Einstiege erzeugen.
KERN_SICHERHEIT_BLOCKIERT = "CORE_SAFETY_BLOCKED"
# 9.5.5: Marker im Abganggrund, wenn die Sperre am Kursalter liegt. Er
# unterscheidet die eigene Sperre von fremden Sicherheitsgruenden -- nur die
# eigene darf beim Wiederaufleben automatisch aufgehoben werden.
KURSE_VERALTET = "STALE_QUOTES"


def jetzt_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _alter_stunden(iso_text: str) -> float:
    if not iso_text:
        return 0.0
    try:
        ts = datetime.fromisoformat(str(iso_text))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - ts).total_seconds() / 3600.0)
    except (TypeError, ValueError):
        return 0.0


# ---------------------------------------------------------------------------
# Kandidat und Bewertung
# ---------------------------------------------------------------------------
@dataclass
class UniverseKandidat:
    """Ein Instrument mit allen Rohdaten, die zur Bewertung gebraucht werden."""
    symbol: str                       # 'BTC' oder 'AAPL'
    broker: str                       # 'okx' oder 'etoro'
    asset_type: str = "crypto"
    inst_id: str = ""                 # 'BTC-EUR' bzw. Tickersymbol
    preis: float = 0.0
    bid: float = 0.0
    ask: float = 0.0
    spread_pct: float = 0.0
    volumen_quote_24h: float = 0.0    # Umsatz in Quotewaehrung (EUR/USD/USDC)
    volumen_basis_24h: float = 0.0
    change_24h_pct: float = 0.0
    alter_tage: float = 0.0
    handelbar: bool = True
    datenalter_sekunden: float = 0.0
    # Erst in der Qualitaetsstufe gefuellt (teurer):
    atr_pct: float = 0.0
    orderbuch_tiefe_quote: float = 0.0
    spread_stabilitaet: float = 0.0
    kerzen_luecken: int = 0
    kerzen_anzahl: int = 0
    sektor: str = ""
    name: str = ""
    zusatz: dict = field(default_factory=dict)

    @property
    def schluessel(self) -> str:
        return f"{self.broker}:{self.symbol}".lower()


@dataclass
class UniverseScore:
    """Ergebnis der Bewertung, aufgeschluesselt nach Bestandteilen.

    Die Aufschluesselung ist kein Luxus: ohne sie laesst sich spaeter nicht
    mehr nachvollziehen, WARUM ein Wert aufgenommen oder verworfen wurde.
    """
    gesamt: float = 0.0
    teile: dict = field(default_factory=dict)
    stufe: str = "cheap"            # 'cheap' oder 'quality'
    begruendung: str = ""

    def als_dict(self) -> dict:
        return {"gesamt": round(self.gesamt, 4), "teile": {k: round(v, 4) for k, v in self.teile.items()},
                "stufe": self.stufe, "begruendung": self.begruendung}


# ---------------------------------------------------------------------------
# Mitglied im Universum
# ---------------------------------------------------------------------------
@dataclass
class UniverseMitglied:
    """Ein Wert im Universum inklusive Verlauf."""
    symbol: str
    broker: str
    asset_type: str = "crypto"
    inst_id: str = ""
    market_quote_ccy: str = ""
    trade_quote_ccy: str = ""
    zustand: str = BEOBACHTUNG
    tier: str = TIER_KANDIDAT
    aufgenommen_am: str = field(default_factory=jetzt_iso)
    zustand_seit: str = field(default_factory=jetzt_iso)
    letzter_score: float = 0.0
    letzter_rang: int = 0
    bester_rang: int = 0
    schlechte_raenge_in_folge: int = 0
    # 9.5.5: Wie oft in Folge dieser Wert bei OFFENER Boerse nur veraltete
    # Kurse geliefert hat. Ein einzelner Aussetzer darf nichts parken.
    veraltete_kurse_in_folge: int = 0
    letzte_pruefung: str = field(default_factory=jetzt_iso)
    aufnahmegrund: str = ""
    abganggrund: str = ""
    ai_bewertung: str = ""            # 'HIGH'/'NORMAL'/'LOW' oder ''
    ai_modell: str = ""
    kaufblock_grund: str = ""        # sofortige Sperre ohne untertaegige Entfernung
    gepinnt: bool = False             # offene Position
    favorit: bool = False
    score_verlauf: list = field(default_factory=list)

    @property
    def schluessel(self) -> str:
        return f"{self.broker}:{self.symbol}".lower()

    @property
    def stunden_im_zustand(self) -> float:
        return _alter_stunden(self.zustand_seit)

    @property
    def stunden_im_universum(self) -> float:
        return _alter_stunden(self.aufgenommen_am)

    @property
    def kern_blockiert(self) -> bool:
        """Kernwert, der gerade einen harten Sicherheitsfilter reisst."""
        return bool(self.abganggrund.startswith(KERN_SICHERHEIT_BLOCKIERT))

    @property
    def handelbar(self) -> bool:
        """Nur AKTIV und ABGANG duerfen neue Einstiege erzeugen.

        BEOBACHTUNG bewusst nicht: ein frisch entdeckter Wert wird erst
        beobachtet und bewertet, bevor echtes Geld hineingeht.
        """
        if self.kern_blockiert or self.kaufblock_grund:
            return False
        return self.zustand in (AKTIV, ABGANG)

    def wechsle(self, zustand: str, grund: str = "") -> None:
        if zustand == self.zustand:
            return
        self.zustand = zustand
        self.zustand_seit = jetzt_iso()
        if zustand == ABGANG:
            self.abganggrund = grund
        elif zustand == AKTIV and grund:
            self.aufnahmegrund = grund

    def merke_score(self, score: float, rang: int, max_verlauf: int = 24) -> None:
        self.letzter_score = float(score)
        self.letzter_rang = int(rang)
        self.bester_rang = int(rang) if not self.bester_rang else min(self.bester_rang, int(rang))
        self.letzte_pruefung = jetzt_iso()
        self.score_verlauf.append({"zeit": self.letzte_pruefung, "score": round(float(score), 4),
                                   "rang": int(rang)})
        if len(self.score_verlauf) > max_verlauf:
            del self.score_verlauf[:-max_verlauf]

    def als_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def aus_dict(cls, data: dict) -> "UniverseMitglied":
        erlaubt = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in (data or {}).items() if k in erlaubt})


# ---------------------------------------------------------------------------
# Aenderungsprotokoll eines Laufs
# ---------------------------------------------------------------------------
@dataclass
class UniverseDiff:
    """Was hat sich seit dem letzten Lauf geaendert? (DU-012)

    Nur die Aenderungen gehen an die KI -- niemals das gesamte Universum.
    Das ist der wichtigste Kostenhebel der ganzen Architektur.
    """
    broker: str = ""
    zeit: str = field(default_factory=jetzt_iso)
    aufgenommen: list = field(default_factory=list)
    entfernt: list = field(default_factory=list)
    beobachtung_gestartet: list = field(default_factory=list)
    freigegeben: list = field(default_factory=list)
    abgang_angekuendigt: list = field(default_factory=list)
    unveraendert: int = 0
    gepinnt: list = field(default_factory=list)
    gruende: dict = field(default_factory=dict)

    @property
    def hat_aenderungen(self) -> bool:
        return bool(self.aufgenommen or self.entfernt or self.beobachtung_gestartet
                    or self.freigegeben or self.abgang_angekuendigt)

    def als_dict(self) -> dict:
        return asdict(self)

    def kurzfassung(self) -> str:
        teile = []
        if self.aufgenommen:
            teile.append(f"+{len(self.aufgenommen)} neu")
        if self.beobachtung_gestartet:
            teile.append(f"{len(self.beobachtung_gestartet)} in Beobachtung")
        if self.freigegeben:
            teile.append(f"{len(self.freigegeben)} freigegeben")
        if self.abgang_angekuendigt:
            teile.append(f"{len(self.abgang_angekuendigt)} auf Abgang")
        if self.entfernt:
            teile.append(f"-{len(self.entfernt)} entfernt")
        if not teile:
            return "keine Aenderungen"
        return ", ".join(teile)


# ---------------------------------------------------------------------------
# Persistenz
# ---------------------------------------------------------------------------
class UniverseZustand:
    """Speichert die Mitgliedschaften dauerhaft.

    Ohne Persistenz waeren Mindestaufenthaltsdauer und Hysterese nach jedem
    Neustart wirkungslos -- der Bot wuerde sein Universum bei jedem Start neu
    wuerfeln.
    """

    def __init__(self, datei: str | Path = "universe_state.json"):
        self.datei = Path(datei)
        if not self.datei.is_absolute():
            # Ein relativer Pfad gehoert in das Zustandsverzeichnis, nicht
            # neben den Quelltext. Sonst schreibt jeder Testlauf in den
            # Release-Baum -- derselbe Fehler wie frueher in news_sources.py.
            import os
            wurzel = (os.getenv("TRADINGBOT_TEST_STATE_DIR", "").strip()
                      or str(Path(__file__).resolve().parent.parent))
            self.datei = Path(wurzel) / self.datei
        self.mitglieder: dict[str, UniverseMitglied] = {}
        self.tagesauswahl: dict[str, str] = {}
        self.tageskern: dict[str, list[str]] = {}
        self._lock = threading.RLock()
        # Auswahl serialisieren, ohne lesende Positionspfade waehrend einer
        # optionalen KI-Bewertung auf das Zustandsschloss warten zu lassen.
        self._lauf_lock = threading.RLock()
        self.laden()

    # -- Laden/Speichern ----------------------------------------------------
    def laden(self) -> None:
        if not self.datei.exists():
            return
        try:
            roh = json.loads(self.datei.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("Universumszustand nicht lesbar (%s) -- starte leer.", exc)
            return
        with self._lock:
            self.mitglieder = {}
            self.tagesauswahl = dict(roh.get("tagesauswahl") or {})
            self.tageskern = dict(roh.get("tageskern") or {})
            for eintrag in roh.get("mitglieder", []):
                try:
                    m = UniverseMitglied.aus_dict(eintrag)
                    self.mitglieder[m.schluessel] = m
                except Exception:
                    logger.debug("Ungueltiger Universumseintrag uebersprungen", exc_info=True)

    def speichern(self, *, strict: bool = False) -> None:
        from safe_persistence import atomic_write_json
        with self._lock:
            payload = {
                "version": 2,
                "gespeichert": jetzt_iso(),
                "mitglieder": [m.als_dict() for m in self.mitglieder.values()],
                "tagesauswahl": dict(self.tagesauswahl),
                "tageskern": dict(self.tageskern),
            }
            try:
                atomic_write_json(self.datei, payload)
            except Exception:
                logger.warning("Universumszustand konnte nicht gespeichert werden", exc_info=True)
                if strict:
                    raise

    # -- Zugriff ------------------------------------------------------------
    def hole(self, broker: str, symbol: str) -> Optional[UniverseMitglied]:
        with self._lock:
            return self.mitglieder.get(f"{broker}:{symbol}".lower())

    def setze(self, mitglied: UniverseMitglied) -> None:
        with self._lock:
            self.mitglieder[mitglied.schluessel] = mitglied

    def entferne(self, broker: str, symbol: str) -> None:
        with self._lock:
            self.mitglieder.pop(f"{broker}:{symbol}".lower(), None)

    def fuer_broker(self, broker: str) -> list[UniverseMitglied]:
        broker = str(broker).lower()
        with self._lock:
            return [m for m in self.mitglieder.values() if m.broker.lower() == broker]

    def aktive(self, broker: str) -> list[UniverseMitglied]:
        return [m for m in self.fuer_broker(broker) if m.zustand in (AKTIV, ABGANG)]

    def handelbare_symbole(self, broker: str) -> list[str]:
        return sorted(m.symbol for m in self.fuer_broker(broker) if m.handelbar)

    def beobachtete(self, broker: str) -> list[UniverseMitglied]:
        return [m for m in self.fuer_broker(broker) if m.zustand == BEOBACHTUNG]

    def alle(self) -> list[UniverseMitglied]:
        with self._lock:
            return list(self.mitglieder.values())

    def setze_pins(self, broker: str, symbole: Iterable[str]) -> int:
        """Markiert Werte mit offener Position als gepinnt."""
        gesetzt = set(str(s).upper() for s in symbole or [])
        anzahl = 0
        with self._lock:
            for m in self.mitglieder.values():
                if m.broker.lower() != str(broker).lower():
                    continue
                war = m.gepinnt
                m.gepinnt = m.symbol.upper() in gesetzt
                if m.gepinnt and not war:
                    anzahl += 1
        return anzahl


__all__ = [
    "UniverseKandidat", "UniverseScore", "UniverseMitglied", "UniverseDiff",
    "UniverseZustand", "BEOBACHTUNG", "AKTIV", "ABGANG", "ENTFERNT",
    "TIER_ETABLIERT", "TIER_KANDIDAT", "TIER_FAVORIT", "TIER_GEPINNT", "TIER_KERN",
    "KERN_SICHERHEIT_BLOCKIERT", "KURSE_VERALTET", "jetzt_iso",
]
