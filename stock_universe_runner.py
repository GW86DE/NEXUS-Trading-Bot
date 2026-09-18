"""Der Aktien-Universumslauf (neu in v8.1.3).

WARUM ES DIESES MODUL BRAUCHT
=============================
Der ``StockUniverseSelector`` und der ``UniverseManager`` gab es seit v7 --
aber NIEMAND hat den Aktienlauf jemals aufgerufen. Die Kryptoseite hatte
ihren Lauf in ``crypto_engine.universumslauf()``; die Aktienseite hatte
nichts. Folge:

* Der Aktien-Universumszustand blieb dauerhaft leer.
* Die Dashboard-Karte zeigte den statischen Katalog -- Georgs Frage
  "wie viel Aktien sind jetzt im Universum? 0 oder 80?" hatte deshalb
  keine ehrliche Antwort.
* Der Instrumentenfilter (keine CFDs, keine gehebelten Produkte) lief nie.

WAS DIESER LAUF TUT -- UND WAS NICHT
====================================
Er stellt den festen Kern sicher, bewertet den Katalog und schreibt die
Rangfolge fort.

SEIT v8.1.5: AUFNAHME OHNE RUECKFRAGE, ABER MIT BEWAEHRUNG
Bis v8.1.4 wurde jede dynamische Aktie nur vorgeschlagen und brauchte eine
Telegram-Freigabe. Georg am 25.08.2026: "es ist doch eh dynamisch und fliegt
wieder raus wenn es eine schlechte Wahl war". Der Bot nimmt jetzt selbst auf
-- gegen vier feste Grenzen:

    75 Kernwerte, hoechstens 25 dynamische Plaetze,
    hoechstens 5 Wechsel je Lauf,
    4 Stunden Bewaehrung mit stabiler Bewertung, bevor ein Wert handelbar
    wird.

"Im Universum" heisst weiterhin nur: wird beobachtet. Ob gekauft wird,
entscheidet danach allein die deterministische Kaufkaskade -- daran aendert
die autonome Aufnahme nichts.

KEIN ZWEITER BROKERZUGANG
=========================
Der Lauf arbeitet bewusst OHNE eToro-Verbindung. Der Aktienkern haelt seine
eigene Verbindung; eine zweite Sitzung aus einem Nebenlauf heraus waere ein
unnoetiges Risiko fuer Orderpfad und Reconnect. Die noetigen Daten -- Umsatz,
Sektor, Handelsstatus, Tageskerzen -- kommen aus dem FMP-Referenzcache.
Ohne Referenzdaten wird der Kern trotzdem gesetzt; die Dynamik pausiert dann
ehrlich, statt auf Verdacht zu ranken.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Positionsdatei lesen -- gegen unplausible Symbole abgesichert (v8.1.5)
# ---------------------------------------------------------------------------
# Am 25.08.2026 standen "NONE" und "UPDATED_AT" in der Universumstabelle.
# Ursache war eine oder-Kette:
#
#     eintraege = daten.get("positions") or daten.get("positionen") or daten
#
# Bei ``{"updated_at": "...", "positions": {}}`` ist das leere Dict falsy.
# Die Kette fiel deshalb auf die GANZE Datei zurueck, und jeder Schluessel der
# obersten Ebene -- auch ``updated_at`` -- wurde zu einem Symbol. Beim Wert
# ``None`` kam ueber ``str(None)`` zusaetzlich der Text "None" heraus, der
# nicht leer und damit wahr ist.
#
# Drei Lehren, die hier fest verdrahtet sind:
#   1. Ein vorhandener, aber leerer Bereich ist eine Antwort ("keine
#      Positionen") und kein Grund, woanders zu suchen -> ``is None`` statt
#      ``or``.
#   2. Aus ``None`` wird nie ein Symbol -- ``str(None)`` ist ein Textfehler,
#      keine Kennung.
#   3. Ein Symbol muss wie ein Ticker aussehen. Was das nicht tut, wird
#      verworfen und protokolliert, statt gehandelt zu werden.

import re as _re

_TICKER_MUSTER = _re.compile(r"^[A-Z][A-Z0-9]{0,9}(?:[.\-][A-Z0-9]{1,4})?$")

# Feldnamen, die in solchen Dateien neben den Positionen stehen. Sie waren
# der konkrete Fehlerfall und werden deshalb ausdruecklich benannt.
_KEINE_SYMBOLE = frozenset({
    "NONE", "NULL", "NAN", "UPDATED_AT", "UPDATED", "TIMESTAMP", "ZEIT",
    "VERSION", "SCHEMA", "POSITIONS", "POSITIONEN", "META", "METADATA",
    "SAVED_AT", "LAST_UPDATE", "TRUE", "FALSE",
})


def ist_tickerartig(symbol) -> bool:
    """Sieht der Text wie ein handelbares Kuerzel aus?

    Erlaubt sind AAPL, GOOGL, FLR.US, BRK.B, RDS-A. Nicht erlaubt sind
    Feldnamen, Zeitstempel und die Textfassungen von ``None``.
    """
    if not isinstance(symbol, str):
        return False
    text = symbol.strip().upper()
    if not text or text in _KEINE_SYMBOLE:
        return False
    return bool(_TICKER_MUSTER.match(text))


def _positionsbereich(daten):
    """Den Positionsbereich waehlen -- ein leerer Bereich bleibt leer."""
    if not isinstance(daten, dict):
        return daten if isinstance(daten, list) else {}
    for schluessel in ("positions", "positionen"):
        if schluessel in daten:
            bereich = daten[schluessel]
            # Vorhanden und leer heisst: keine Positionen. Punkt.
            return bereich if isinstance(bereich, (dict, list)) else {}
    # Kein benannter Bereich: die Datei selbst ist die Zuordnung. Die
    # Symbolpruefung faengt Feldnamen ab, die dabei mitlaufen.
    return daten


def _symbol_aus(eintrag: dict, schluessel=None) -> str:
    """Das Symbol eines Eintrags -- oder ein leerer Text."""
    kandidaten = []
    for feld in ("symbol", "ticker", "instrument"):
        wert = eintrag.get(feld)
        if wert is not None:            # kein ``or``: 0 und "" sind Antworten
            kandidaten.append(wert)
    if schluessel is not None:
        kandidaten.append(schluessel)
    for kandidat in kandidaten:
        if isinstance(kandidat, str) and ist_tickerartig(kandidat):
            return kandidat.strip().upper()
    if kandidaten:
        logger.debug("Positionseintrag ohne brauchbares Symbol verworfen: %r",
                     kandidaten[0])
    return ""


def _menge_positiv(eintrag: dict) -> bool:
    """Haelt der Eintrag tatsaechlich Stueck?

    Fehlt das Feld ganz, gilt der Eintrag als gehalten -- alte Dateien ohne
    Mengenfeld sollen ihre Position nicht verlieren. Steht dort eine 0 oder
    etwas Unlesbares, gilt er als nicht gehalten.
    """
    for feld in ("quantity", "menge", "qty", "shares"):
        if feld in eintrag:
            try:
                menge = float(eintrag[feld])
            except (TypeError, ValueError):
                return False
            return menge > 0 and menge == menge   # NaN ist keine Menge
    return True


class StockUniverseRunner:
    """Fuehrt den Aktien-Universumslauf auf dem Aktientakt aus."""

    def __init__(self, universum, *, taktgeber=None, melder=None, cfg=None):
        import config as _config
        self.cfg = cfg or _config
        self.universum = universum
        self.takt = taktgeber
        self.melder = melder
        self.letzter_lauf: Optional[dict] = None
        self.letzter_fehler = ""
        self._lock = threading.RLock()

    # -- Hilfsmittel --------------------------------------------------------
    def _melde(self, text: str, *, wichtig: bool = False) -> None:
        if self.melder is None:
            return
        try:
            self.melder(text, wichtig=wichtig)
        except TypeError:
            try:
                self.melder(text)
            except Exception:
                logger.debug("Meldung nicht zustellbar", exc_info=True)
        except Exception:
            logger.debug("Meldung nicht zustellbar", exc_info=True)

    def _favoriten(self) -> list[str]:
        """Favoriten sind Prioritaetsmarker, keine Freigabe."""
        try:
            from favorites import load_favorites
            return [str(f.symbol).upper() for f in load_favorites()
                    if str(getattr(f, "asset_type", "")).lower() in ("stock", "etf", "")]
        except Exception:
            logger.debug("Favoriten nicht lesbar", exc_info=True)
            return []

    def _offene_positionen(self) -> list[str]:
        """Offene Aktienpositionen bleiben im Universum -- auch bei schlechtem Rang.

        Gelesen wird der persistierte Positionszustand des Aktienkerns, nicht
        der Broker: dieser Lauf haelt keine eigene Verbindung.
        """
        try:
            import json
            import os
            from pathlib import Path
            wurzel = Path(os.getenv("TRADINGBOT_TEST_STATE_DIR", "").strip()
                          or Path(__file__).resolve().parent)
            datei = wurzel / "position_state.json"
            if not datei.exists():
                return []
            daten = json.loads(datei.read_text(encoding="utf-8"))
            eintraege = _positionsbereich(daten)
            symbole: list[str] = []
            if isinstance(eintraege, dict):
                for schluessel, wert in eintraege.items():
                    if not isinstance(wert, dict):
                        # Ein Nicht-Dict unter "positions" ist kein Positionseintrag.
                        # Bis v8.1.4 wurde der Schluessel trotzdem als Symbol
                        # uebernommen -- so kam "UPDATED_AT" ins Universum.
                        continue
                    symbol = _symbol_aus(wert, schluessel)
                    if symbol and _menge_positiv(wert):
                        symbole.append(symbol)
            elif isinstance(eintraege, list):
                for wert in eintraege:
                    if not isinstance(wert, dict):
                        continue
                    symbol = _symbol_aus(wert, None)
                    if symbol and _menge_positiv(wert):
                        symbole.append(symbol)
            return sorted(set(symbole))
        except Exception:
            logger.debug("Offene Aktienpositionen nicht lesbar", exc_info=True)
            return []

    # -- Der Lauf -----------------------------------------------------------
    def lauf(self) -> dict:
        """Einen Universumslauf ausfuehren und das Ergebnis melden."""
        with self._lock:
            try:
                from universe.stock_selector import StockUniverseSelector
                from market_calendar import darf_arbeiten
                netz_erlaubt, netz_grund = darf_arbeiten(
                    puffer_minuten=int(getattr(
                        self.cfg, "FMP_PREMARKET_REFRESH_MINUTES", 30)))
                if not bool(getattr(self.cfg, "ETORO_ENABLED", True)):
                    netz_erlaubt = False
                    netz_grund = "eToro-Aktiendomaene deaktiviert"
                selektor = StockUniverseSelector(broker=None, cfg=self.cfg)
                auswahl = selektor.auswahl(
                    erlaube_referenz_abruf=bool(netz_erlaubt))
                auswahl["referenz_netz_erlaubt"] = bool(netz_erlaubt)
                auswahl["referenz_netz_grund"] = str(netz_grund or "")
            except Exception as exc:
                self.letzter_fehler = str(exc)
                logger.warning("Aktien-Universumslauf fehlgeschlagen: %s", exc)
                # Auch ohne Bewertung muss der feste Kern stehen. Sonst
                # haengt die gesamte Aktienseite an einer Datenquelle.
                auswahl = {"broker": "etoro", "asset_type": "stock", "rangliste": [],
                           "hinweis": f"Bewertung nicht moeglich: {exc}"}

            try:
                diff = self.universum.lauf(
                    auswahl,
                    offene_positionen=self._offene_positionen(),
                    favoriten=self._favoriten())
            except Exception as exc:
                self.letzter_fehler = str(exc)
                logger.exception("Aktien-Universumszustand nicht fortschreibbar: %s", exc)
                return {"ok": False, "grund": str(exc)}

            import universe_diagnose
            diagnose = universe_diagnose.protokolliere(auswahl)
            vorschlaege = list((diff.gruende or {}).get("vorschlaege") or [])
            beobachtung = list(diff.beobachtung_gestartet or [])
            freigegeben = list(diff.freigegeben or [])
            self.letzter_lauf = {
                "zeit": datetime.now(timezone.utc).isoformat(),
                "diff": diff.als_dict(),
                "katalog": auswahl.get("katalog_gesamt"),
                "pool": auswahl.get("pool"),
                "bewertet": len(auswahl.get("rangliste") or []),
                "vorschlaege": vorschlaege[:25],
                "diagnose": diagnose,
                "hinweis": auswahl.get("hinweis", ""),
            }

            if diff.hat_aenderungen:
                self._melde("Aktien-Universum: " + diff.kurzfassung()
                            + (f" | neu: {', '.join(diff.aufgenommen[:12])}"
                               f"{' …' if len(diff.aufgenommen) > 12 else ''}"
                               if diff.aufgenommen else "")
                            + (f" | raus: {', '.join(diff.entfernt)}" if diff.entfernt else ""))
            if beobachtung:
                self._melde(self.bewaehrungstext(beobachtung, diff, auswahl))
            if freigegeben:
                self._melde("✅ AKTIEN-UNIVERSUM · BEWÄHRUNG BESTANDEN\n"
                            + ", ".join(sorted(freigegeben))
                            + "\nAb jetzt handelbar -- ob gekauft wird, "
                              "entscheidet weiterhin die Kaufkaskade.")
            if vorschlaege:
                self._melde(self.vorschlagstext(vorschlaege))
            return {"ok": True, "aenderungen": diff.kurzfassung(),
                    "vorschlaege": len(vorschlaege),
                    "beobachtung": len(beobachtung),
                    "freigegeben": len(freigegeben),
                    "beobachtet": len(self.universum.zustand.fuer_broker("etoro"))}

    @staticmethod
    def bewaehrungstext(symbole: list[str], diff=None, auswahl=None,
                        grenze: int = 8) -> str:
        """Neu aufgenommene Aktien -- eine Mitteilung, keine Rueckfrage.

        Seit v8.1.5 nimmt der Bot selbst auf. Diese Nachricht sagt deshalb,
        was PASSIERT IST, und nennt die Grenzen, innerhalb derer es passiert
        ist -- damit "ohne Rueckfrage" nicht wie "ohne Regeln" klingt.
        """
        import config as _cfg
        zeilen = ["🔍 AKTIEN-UNIVERSUM · NEU IN BEWÄHRUNG",
                  ", ".join(sorted(symbole)[:grenze])
                  + (f" … (+{len(symbole) - grenze})" if len(symbole) > grenze else ""),
                  f"Bewährung: {float(getattr(_cfg, 'STOCK_UNIVERSE_PROBATION_HOURS', 4.0)):.0f} h "
                  "mit stabiler Bewertung. Beobachtet heißt noch nicht handelbar.",
                  f"Grenzen: {int(getattr(_cfg, 'STOCK_CORE_LIMIT', 75))} feste Kernwerte + "
                  f"max. {int(getattr(_cfg, 'STOCK_UNIVERSE_DYNAMIC_LIMIT', 25))} dynamische, "
                  f"max. {int(getattr(_cfg, 'STOCK_UNIVERSE_MAX_CHANGES_PER_RUN', 5))} Wechsel je Lauf.",
                  "Schlechte Wahl fliegt über die Rangfolge wieder raus."]
        return "\n".join(zeilen)

    @staticmethod
    def vorschlagstext(vorschlaege: list[dict], grenze: int = 8) -> str:
        """Vorschlaege, die der Lauf NICHT aufnehmen konnte.

        Seit v8.1.5 nimmt der Bot autonom auf; hier landen nur noch Werte,
        die an einem Deckel haengen geblieben sind (dynamische Plaetze voll,
        Wechselgrenze des Laufs erreicht).
        """
        zeilen = ["📋 AKTIEN-UNIVERSUM · NICHT AUFGENOMMEN",
                  "Technisch gut bewertet, aber kein Platz frei -- die Deckel "
                  "des Universums greifen. Beim nächsten Lauf erneut geprüft."]
        for eintrag in vorschlaege[:grenze]:
            zeilen.append(f"• {eintrag.get('symbol','?')} · Rang {eintrag.get('rang','?')} · "
                          f"Score {eintrag.get('score','?')}"
                          + (f" · {eintrag.get('begruendung','')}"[:120]
                             if eintrag.get("begruendung") else ""))
        if len(vorschlaege) > grenze:
            zeilen.append(f"… und {len(vorschlaege) - grenze} weitere")
        return "\n".join(zeilen)

    # -- Takt ---------------------------------------------------------------
    def faellig(self) -> bool:
        if self.takt is None:
            return True
        try:
            from scheduler_v7 import UNIVERSUM
            # Bewusst OHNE Marktfensterpruefung. Ein Universumslauf platziert
            # keine Order; er ordnet nur, was beobachtet wird. Mit Pruefung
            # waere das Aktienuniversum am Wochenende und ueber Nacht leer --
            # und genau dann schaut man auf das Dashboard.
            return bool(self.takt.faellig("stock", UNIVERSUM, markt_pruefen=False)[0])
        except Exception:
            logger.debug("Aktientakt nicht lesbar", exc_info=True)
            return False

    def markiere(self) -> None:
        if self.takt is None:
            return
        try:
            from scheduler_v7 import UNIVERSUM
            self.takt.markiere("stock", UNIVERSUM)
        except Exception:
            logger.debug("Aktientakt nicht markierbar", exc_info=True)

    def zyklus(self) -> dict:
        """Ein Taktschritt: nur laufen, wenn faellig."""
        if not self.faellig():
            return {"ok": True, "uebersprungen": "nicht faellig"}
        try:
            return self.lauf()
        finally:
            self.markiere()


__all__ = ["StockUniverseRunner"]
