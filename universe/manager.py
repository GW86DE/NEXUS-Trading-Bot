"""UniverseManager -- Herzstueck des dynamischen Universums (DU-001).

VERANTWORTUNG
=============
    Catalog laden -> harte Filter -> Ranking -> Hysterese -> Aufenthaltsdauer
    -> Mehrfachbestaetigung -> Groessenbegrenzung -> Pins -> Focus Set -> Diff

WAS DIESES MODUL BEWUSST NICHT TUT
==================================
Es entscheidet nicht ueber Trades. Ein Wert im Universum ist ein Wert, den
der Bot BEOBACHTET. Ob gekauft wird, entscheidet danach ausschliesslich das
deterministische Candidate Gate.

DIE DREI STABILITAETSREGELN
===========================
Ohne sie wuerde das Universum bei jedem Lauf durchgewuerfelt, weil Raenge
im Minutentakt um wenige Plaetze schwanken:

    Hysterese (DU-007)
        Aufnahme ab Rang 50, Entfernung erst schlechter als Rang 75.
        Zwei verschiedene Schwellen statt einer Kante.

    Mindestaufenthalt (DU-008)
        Ein neu aufgenommener Wert bleibt mindestens 6 Stunden (Krypto)
        bzw. einen Handelstag (Aktien) -- ausser bei Sicherheitsgruenden.

    Mehrfachbestaetigung (DU-009)
        Ein schlechter Rang allein entfernt nichts. Erst drei Laeufe in
        Folge fuehren zum Abgang.
"""

from __future__ import annotations

import logging
from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Iterable, Optional
from zoneinfo import ZoneInfo

from . import audit
from .modelle import (
    KERN_SICHERHEIT_BLOCKIERT, KURSE_VERALTET, TIER_KERN,
    ABGANG, AKTIV, BEOBACHTUNG, ENTFERNT, TIER_ETABLIERT, TIER_FAVORIT,
    TIER_GEPINNT, TIER_KANDIDAT, UniverseDiff, UniverseKandidat, UniverseMitglied,
    UniverseZustand, jetzt_iso,
)

logger = logging.getLogger(__name__)


@dataclass
class UniverseRegeln:
    """Alle Stellschrauben eines Brokers an einer Stelle."""
    aktiv_limit: int = 50
    kern_limit: int = 20          # feste Kernwerte, nie per Rang entfernbar
    dynamisch_limit: int = 30     # monatlich bewertete Beobachtungswerte
    focus_limit: int = 12
    aufnahme_rang: int = 50
    entfernung_rang: int = 75
    min_aufenthalt_stunden: float = 6.0
    bestaetigungen_fuer_abgang: int = 3
    beobachtung_stunden: float = 24.0
    max_aenderungen_pro_lauf: int = 0     # 0 = unbegrenzt
    autonome_aufnahme: bool = True        # False = nur Vorschlaege

    @classmethod
    def fuer(cls, broker: str, cfg=None) -> "UniverseRegeln":
        import config as _config
        cfg = cfg or _config
        if str(broker).lower() == "okx":
            aktiv = int(getattr(cfg, "CRYPTO_UNIVERSE_ACTIVE_LIMIT", 50))
            kern = int(getattr(cfg, "CRYPTO_CORE_LIMIT", 20))
            dynamisch = int(getattr(cfg, "CRYPTO_UNIVERSE_DYNAMIC_LIMIT", 30))
            return cls(
                aktiv_limit=aktiv,
                kern_limit=kern,
                dynamisch_limit=min(max(0, aktiv - kern), max(0, dynamisch)),
                focus_limit=int(getattr(cfg, "CRYPTO_UNIVERSE_FOCUS_LIMIT", 12)),
                aufnahme_rang=aktiv,
                entfernung_rang=int(getattr(cfg, "CRYPTO_UNIVERSE_REMOVAL_RANK", int(aktiv * 1.5))),
                min_aufenthalt_stunden=float(getattr(cfg, "CRYPTO_UNIVERSE_MIN_RESIDENCE_HOURS", 6.0)),
                bestaetigungen_fuer_abgang=int(getattr(cfg, "CRYPTO_UNIVERSE_REMOVAL_CONFIRMATIONS", 3)),
                beobachtung_stunden=float(getattr(cfg, "CRYPTO_UNIVERSE_PROBATION_HOURS", 24.0)),
                max_aenderungen_pro_lauf=int(getattr(cfg, "CRYPTO_UNIVERSE_MAX_CHANGES_PER_RUN", 8)),
                autonome_aufnahme=True,
            )
        # 75 feste Kernaktien + hoechstens 25 dynamische = 100 aktiv.
        # Das aktive Limit wird bewusst aus beiden Teilen berechnet, damit
        # eine geaenderte Kernliste die Gesamtzahl nicht still verschiebt.
        kern = int(getattr(cfg, "STOCK_CORE_LIMIT", 75))
        dynamisch = int(getattr(cfg, "STOCK_UNIVERSE_DYNAMIC_LIMIT", 25))
        aktiv = int(getattr(cfg, "STOCK_UNIVERSE_ACTIVE_LIMIT", kern + dynamisch))
        return cls(
            aktiv_limit=aktiv,
            kern_limit=kern,
            dynamisch_limit=dynamisch,
            focus_limit=int(getattr(cfg, "STOCK_UNIVERSE_FOCUS_LIMIT", 15)),
            aufnahme_rang=aktiv,
            entfernung_rang=int(getattr(cfg, "STOCK_UNIVERSE_REMOVAL_RANK", int(aktiv * 1.5))),
            min_aufenthalt_stunden=float(getattr(cfg, "STOCK_UNIVERSE_MIN_RESIDENCE_HOURS", 24.0)),
            bestaetigungen_fuer_abgang=int(getattr(cfg, "STOCK_UNIVERSE_REMOVAL_CONFIRMATIONS", 3)),
            # v8.1.5: Die Aufnahme braucht keine Bestaetigung mehr, aber eine
            # Bewaehrung. Vier Stunden statt der 24 h der Kryptoseite -- der
            # Wert ist ja gerade wegen guter Kennzahlen in die Rangliste
            # gekommen, und wer erst am naechsten Tag einsteigt, kommt zu spaet.
            beobachtung_stunden=float(getattr(cfg, "STOCK_UNIVERSE_PROBATION_HOURS", 4.0)),
            max_aenderungen_pro_lauf=int(getattr(cfg, "STOCK_UNIVERSE_MAX_CHANGES_PER_RUN", 5)),
            autonome_aufnahme=True,
        )


class UniverseManager:
    """Fuehrt einen Universumslauf durch und pflegt den Zustand."""

    def __init__(self, zustand: Optional[UniverseZustand] = None, *,
                 cfg=None, ai_router=None):
        import config as _config
        self.cfg = cfg or _config
        self.zustand = zustand or UniverseZustand(
            getattr(self.cfg, "UNIVERSE_STATE_FILE", "universe_state.json"))
        self.ai = ai_router
        self._mitgliedschaft_aendern = True
        if "okx" in self.zustand.tageskern:
            self._runtime_core_symbols = set(self.zustand.tageskern["okx"])

    # -- Hauptlauf ----------------------------------------------------------
    def lauf(self, ergebnis: dict, *, offene_positionen: Iterable[str] = (),
             favoriten: Iterable[str] = (), jetzt: Optional[datetime] = None) -> UniverseDiff:
        """Bewertungen laufend, Zusammensetzung einmal je lokalem Kalendertag.

        Der Tagesmarker liegt atomar bei den Mitgliedern und ueberlebt einen
        Neustart. Ohne bewertbare Kandidaten wird die Tagesauswahl aufgeschoben.
        Die einmalige Anlage des festen Kerns und Positionspins bleiben moeglich.
        """
        broker = str(ergebnis.get("broker", "okx")).lower()
        instant = jetzt if jetzt is not None else datetime.now(timezone.utc)
        if instant.tzinfo is None:
            raise ValueError("Universumszeit braucht eine Zeitzone")
        heute = instant.astimezone(ZoneInfo(str(getattr(
            self.cfg, "LOCAL_TIMEZONE", "Europe/Berlin")))).date().isoformat()
        # Beide Broker verwenden denselben Zustand. Auswahl, Tagesmarker und
        # Dateiwechsel duerfen sich nicht gegenseitig ueberholen.
        with self.zustand._lauf_lock:
            letzter_tag = self.zustand.tagesauswahl.get(broker, "")
            if letzter_tag:
                date.fromisoformat(letzter_tag)  # unlesbaren Marker nicht freigeben
            vorher = (deepcopy(self.zustand.mitglieder),
                      deepcopy(self.zustand.tagesauswahl),
                      deepcopy(self.zustand.tageskern))
            alter_kern = getattr(self, "_runtime_core_symbols", None)
            self._mitgliedschaft_aendern = bool(
                (ergebnis.get("rangliste") or
                 (broker == "okx" and ergebnis.get("eligible_symbols")))
                and (not letzter_tag or letzter_tag < heute))
            try:
                diff = self._fortschreiben(ergebnis, offene_positionen=offene_positionen,
                                          favoriten=favoriten)
                if self._mitgliedschaft_aendern:
                    self.zustand.tagesauswahl[broker] = heute
                else:
                    diff.gruende["tagesauswahl"] = (
                        "Zusammensetzung heute bereits geprueft; Bewertungen laufen weiter"
                        if letzter_tag and letzter_tag >= heute else
                        "Tagesauswahl wartet auf bewertbare Kandidaten")
                self.zustand.speichern(strict=True)
                logger.info("Universumslauf %s: %s", broker, diff.kurzfassung())
                return diff
            except Exception:
                (self.zustand.mitglieder, self.zustand.tagesauswahl,
                 self.zustand.tageskern) = vorher
                if alter_kern is None:
                    self.__dict__.pop("_runtime_core_symbols", None)
                else:
                    self._runtime_core_symbols = alter_kern
                raise
            finally:
                self._mitgliedschaft_aendern = True

    def _fortschreiben(self, ergebnis: dict, *, offene_positionen: Iterable[str],
                      favoriten: Iterable[str]) -> UniverseDiff:
        """Bewertung und bestehende Aufnahme-/Entfernungsregeln anwenden."""
        broker = str(ergebnis.get("broker", "okx")).lower()
        regeln = UniverseRegeln.fuer(broker, self.cfg)
        rangliste = list(ergebnis.get("rangliste") or [])
        raw_eligible = ergebnis.get("eligible_symbols")
        self._eligible_symbols = ({
            str(x).upper() for x in (raw_eligible or [])
        } if broker == "okx" and raw_eligible is not None else None)
        self._ineligible_reasons = {
            str(k).upper(): str(v) for k, v in
            (ergebnis.get("ineligible_reasons") or {}).items()
        } if broker == "okx" else {}
        if broker == "okx" and ergebnis.get("core_symbols") is not None:
            if self._mitgliedschaft_aendern:
                self.zustand.tageskern[broker] = sorted({
                    str(x).upper() for x in (ergebnis.get("core_symbols") or [])})
            self._runtime_core_symbols = set(self.zustand.tageskern.get(broker, [
                m.symbol.upper() for m in self.zustand.fuer_broker(broker)
                if m.tier == TIER_KERN]))
        # 9.5.5: Werte, deren Kurse bei offener Boerse dauerhaft zu alt sind.
        self._veraltete_kurse = {
            str(k).upper(): float(v)
            for k, v in (ergebnis.get("veraltete_kurse") or {}).items()
        } if broker == "etoro" else {}

        diff = UniverseDiff(broker=broker)

        offene = {str(s).upper() for s in offene_positionen or ()}
        favoriten_set = {str(s).upper() for s in favoriten or ()}
        self.zustand.setze_pins(broker, offene)

        bewertet = {e["kandidat"].symbol.upper(): e for e in rangliste}
        aenderungen = 0

        # ---- 0. Fester Kern -----------------------------------------------
        # Der Kern ist gesetzt und haengt an keinem Rang. Ohne diesen Schritt
        # waere ein Kernwert, den der Selektor gerade nicht bewerten kann
        # (fehlende Kursdaten, Marktpause), einfach nicht im Universum -- und
        # die Aktienseite bliebe dauerhaft leer, weil dort jede Aufnahme sonst
        # eine Telegram-Freigabe braucht.
        if self._mitgliedschaft_aendern or not self.zustand.fuer_broker(broker):
            self._kern_sicherstellen(broker, diff, bewertet)
        if (self._mitgliedschaft_aendern and broker == "okx"
                and hasattr(self, "_runtime_core_symbols")):
            # Statische Altkerne aus frueheren Versionen duerfen keine toten
            # Plaetze mehr belegen. Der neue Kern enthaelt ausschliesslich
            # aktuell voll geeignete Kontomaerkte.
            for mitglied in list(self.zustand.fuer_broker(broker)):
                if (mitglied.tier == TIER_KERN
                        and mitglied.symbol.upper() not in self._runtime_core_symbols):
                    self._entferne(
                        mitglied, diff, broker,
                        "kein aktuell fuer dieses OKX-Konto geeigneter Kernmarkt",
                        sofort=True)

        # ---- 1. Bestehende Mitglieder fortschreiben ------------------------
        for mitglied in self.zustand.fuer_broker(broker):
            eintrag = bewertet.get(mitglied.symbol.upper())
            mitglied.favorit = mitglied.symbol.upper() in favoriten_set
            if eintrag is None:
                # Nicht mehr in der Bewertung: entweder ausgefiltert oder weg.
                if self._sicherheitsabgang_pruefen(mitglied, diff, regeln, broker,
                                                   "faellt aus dem Eligible Pool"):
                    aenderungen += 1
                continue

            alter_rang, alter_score = mitglied.letzter_rang, mitglied.letzter_score
            score = float(eintrag["score"].gesamt)
            rang = int(eintrag["rang"])
            kandidat = eintrag["kandidat"]
            mitglied.inst_id = kandidat.inst_id or mitglied.inst_id
            mitglied.market_quote_ccy = str(
                kandidat.zusatz.get("quote") or "").upper()
            mitglied.trade_quote_ccy = str(
                kandidat.zusatz.get("trade_quote_ccy") or "").upper()
            mitglied.merke_score(score, rang)

            if self.ist_kern(broker, mitglied.symbol) and mitglied.kern_blockiert:
                mitglied.abganggrund = ""
                mitglied.zustand = AKTIV
                audit.schreibe("KERN_ENTSPERRT", symbol=mitglied.symbol, broker=broker,
                               grund="aktuelles OKX-Paar besteht die Aufnahmefilter wieder",
                               quelle="sicherheit")

            grund_sofort = str(eintrag.get("sicherheitsabgang") or "")
            if (self._mitgliedschaft_aendern and not grund_sofort
                    and mitglied.symbol.upper() not in self._veraltete_kurse):
                mitglied.kaufblock_grund = ""
            if grund_sofort:
                self._entferne(mitglied, diff, broker, grund_sofort, sofort=True)
                aenderungen += 1
                continue

            if mitglied.zustand == BEOBACHTUNG:
                if self._beobachtung_pruefen(mitglied, eintrag, regeln, diff, broker):
                    aenderungen += 1
                continue

            self._hysterese(mitglied, rang, alter_rang, alter_score, regeln, diff, broker)
            # Ein soeben entferntes Mitglied darf hier nicht zurueckgeschrieben
            # werden -- sonst waere die Entfernung wirkungslos.
            if mitglied.zustand != ENTFERNT:
                self.zustand.setze(mitglied)

        # ---- 2. Neuaufnahmen ----------------------------------------------
        platz = self._freie_plaetze(broker, regeln, favoriten_set)
        for eintrag in (rangliste if self._mitgliedschaft_aendern else []):
            if platz <= 0:
                break
            if regeln.max_aenderungen_pro_lauf and aenderungen >= regeln.max_aenderungen_pro_lauf:
                diff.gruende["aenderungslimit"] = (
                    f"Limit von {regeln.max_aenderungen_pro_lauf} Aenderungen pro Lauf erreicht")
                break
            kandidat: UniverseKandidat = eintrag["kandidat"]
            symbol = kandidat.symbol.upper()
            if symbol in diff.entfernt:
                continue  # keine Entfernung und Wiederaufnahme im selben Tageslauf
            if self.zustand.hole(broker, kandidat.symbol) is not None:
                continue
            # Ein Favorit darf frueher und sicher vollstaendig geprueft
            # werden, aber niemals die normale Ranggrenze fuer die Aufnahme
            # umgehen. Sonst waere die Favoritenliste eine versteckte
            # Hintertuer ins handelbare Universum.
            if int(eintrag["rang"]) > regeln.aufnahme_rang:
                continue
            if self._aufnehmen(eintrag, regeln, diff, broker, favoriten_set):
                aenderungen += 1
                platz -= 1

        # ---- 3. Gepinnte Werte sichern ------------------------------------
        for symbol in offene:
            mitglied = self.zustand.hole(broker, symbol)
            if mitglied is None:
                mitglied = UniverseMitglied(
                    symbol=symbol, broker=broker,
                    asset_type="crypto" if broker == "okx" else "stock",
                    inst_id=symbol, zustand=AKTIV, tier=TIER_GEPINNT, gepinnt=True,
                    aufnahmegrund="offene Position -- Monitoring erzwungen")
                self.zustand.setze(mitglied)
                audit.schreibe("GEPINNT", symbol=symbol, broker=broker,
                               grund="offene Position ausserhalb des Universums",
                               quelle="sicherheit")
            mitglied.gepinnt = True
            if mitglied.zustand == ENTFERNT:
                mitglied.wechsle(AKTIV, "offene Position")
            diff.gepinnt.append(symbol)
            self.zustand.setze(mitglied)

        # 9.5.5: Erst nach allen Rangentscheidungen parken. Sonst wuerde ein
        # gerade erst aufgenommener Wert im selben Lauf wieder verschwinden.
        self._kursalter_pflegen(broker, diff)

        diff.unveraendert = max(0, len(self.zustand.fuer_broker(broker)) - (
            len(diff.aufgenommen) + len(diff.entfernt) + len(diff.freigegeben)
            + len(diff.beobachtung_gestartet) + len(diff.abgang_angekuendigt)))

        return diff

    # -- Fester Kern ---------------------------------------------------------
    def _kernwerte(self, broker: str) -> set:
        """Der feste Kern des jeweiligen Brokers.

        Krypto: 20 dauerhaft gesetzte, etablierte Basiswerte. Aktien: bis zu
        75 Kernwerte.

        Bis 8.1.1 galten Kernwerte nur als ETABLIERT. Das ueberspringt zwar
        die Bewaehrung, schuetzt aber nicht vor Rang und Hysterese -- ein
        Kernwert konnte also nach drei schlechten Laeufen verschwinden. Ein
        "fester Kern", der sich selbst entfernen kann, ist keiner.

        Ab 8.1.3 gilt das ausdruecklich auch fuer Aktien. Vorher lieferte
        diese Methode fuer eToro eine leere Menge; die Aktienseite hatte
        damit gar keinen Kern.
        """
        name = str(broker).lower()
        if name == "okx":
            if hasattr(self, "_runtime_core_symbols"):
                return set(self._runtime_core_symbols)
            quelle = list(getattr(self.cfg, "CRYPTO_CORE_SYMBOLS", ()))
        elif name == "etoro":
            quelle = getattr(self.cfg, "STOCK_CORE_SYMBOLS", ())
        else:
            return set()
        grenze = self._kern_grenze(name)
        werte = [str(x).upper() for x in quelle if str(x).strip()]
        # Harte Obergrenze: eine versehentlich zu lange Kernliste darf das
        # aktive Limit nicht sprengen und damit jede Dynamik ersticken.
        return set(werte[:grenze]) if grenze > 0 else set(werte)

    def _kern_grenze(self, broker: str) -> int:
        if str(broker).lower() == "etoro":
            return int(getattr(self.cfg, "STOCK_CORE_LIMIT", 75))
        return int(getattr(self.cfg, "CRYPTO_CORE_LIMIT", 20))

    def ist_kern(self, broker: str, symbol: str) -> bool:
        return str(symbol).upper() in self._kernwerte(broker)

    def kernwerte(self, broker: str) -> set:
        """Der feste Kern des Brokers -- oeffentlich fuer Oberflaechen.

        Bewusst eine Kopie: eine Anzeige darf den Kern nicht versehentlich
        veraendern.
        """
        return set(self._kernwerte(broker))

    # -- Teilschritte -------------------------------------------------------
    def _kursalter_pflegen(self, broker: str, diff: "UniverseDiff") -> None:
        """Parkt Werte mit dauerhaft veralteten Kursen -- und loest das wieder.

        9.5.5. Der Befund vom 02.09.2026: eToro lieferte fuer einen Teil der
        Aktien Kurse mit konstant rund 20 Minuten Rueckstand. Diese Werte
        blieben im aktiven Universum, belegten Kandidatenplaetze und wurden bei
        jedem Versuch erneut von der Kursaltergrenze abgelehnt -- sichtbar nur
        als endlose BLOCKED-Zeilen im Logbuch.

        Vorgehen, bewusst zurueckhaltend:

        * Geparkt wird erst nach ``STOCK_UNIVERSE_STALE_RUNS_BEFORE_PARK``
          Laeufen hintereinander. Ein einzelner Aussetzer aendert nichts.
        * Ein KERNWERT wird gesperrt, nicht entfernt. Die Kernzusammensetzung
          wird nicht heimlich umgeschrieben, und der Wert bleibt mit Grund
          sichtbar. Dafuer gibt es den vorhandenen, sich selbst wieder
          loesenden Mechanismus ``kern_blockiert``.
        * Ein DYNAMISCHER Wert wird entfernt und macht seinen Platz frei.
        * Liefert ein Wert wieder frische Kurse, loest sich die Sperre von
          selbst -- ohne Handgriff.
        """
        if broker != "etoro":
            return
        veraltet = getattr(self, "_veraltete_kurse", {}) or {}
        noetig = max(1, int(getattr(
            self.cfg, "STOCK_UNIVERSE_STALE_RUNS_BEFORE_PARK", 2)))
        for mitglied in list(self.zustand.fuer_broker(broker)):
            symbol = mitglied.symbol.upper()
            alter = veraltet.get(symbol)
            if alter is None:
                # Wieder frisch: Zaehler zuruecksetzen und eine Kurssperre
                # aufheben. Nur die eigene Sperre -- fremde Gruende bleiben.
                if mitglied.veraltete_kurse_in_folge:
                    mitglied.veraltete_kurse_in_folge = 0
                    if (self.ist_kern(broker, symbol) and mitglied.kern_blockiert
                            and KURSE_VERALTET in mitglied.abganggrund):
                        mitglied.abganggrund = ""
                        mitglied.zustand = AKTIV
                        audit.schreibe("KERN_ENTSPERRT", symbol=symbol,
                                       broker=broker,
                                       grund="Kurse wieder aktuell",
                                       quelle="kursalter")
                        logger.info("Kernwert %s liefert wieder aktuelle Kurse.",
                                    symbol)
                    self.zustand.setze(mitglied)
                continue

            mitglied.veraltete_kurse_in_folge = int(
                mitglied.veraltete_kurse_in_folge or 0) + 1
            if mitglied.veraltete_kurse_in_folge < noetig:
                self.zustand.setze(mitglied)
                continue

            detail = (f"Kurse seit {mitglied.veraltete_kurse_in_folge} Laeufen "
                      f"veraltet (zuletzt {float(alter) / 60:.0f} min alt), "
                      "obwohl die Boerse offen ist")
            if self.ist_kern(broker, symbol):
                if not mitglied.kern_blockiert:
                    mitglied.abganggrund = (
                        f"{KERN_SICHERHEIT_BLOCKIERT}: {KURSE_VERALTET} -- {detail}")
                    audit.schreibe("KERN_GESPERRT", symbol=symbol, broker=broker,
                                   grund=detail, quelle="kursalter")
                    logger.warning("Kernwert %s gesperrt: %s", symbol, detail)
                self.zustand.setze(mitglied)
            else:
                self._entferne(mitglied, diff, broker, detail, sofort=True)

    def _gesperrte_kernwerte(self, broker: str) -> int:
        """Kernwerte, die gerade keinen Einstieg erlauben."""
        kern = self._kernwerte(broker)
        return sum(1 for m in self.zustand.fuer_broker(broker)
                   if m.symbol.upper() in kern and m.kern_blockiert)

    def _freie_plaetze(self, broker: str, regeln: UniverseRegeln,
                       favoriten: set) -> int:
        """Wie viele DYNAMISCHE Werte duerfen noch aufgenommen werden?

        Ab 8.1.3 zaehlt nur der dynamische Teil gegen das Limit:

            Aktien  75 feste Kernwerte + hoechstens 25 dynamische
            Krypto   20 feste Kernwerte + hoechstens 30 monatlich dynamische

        Kernwerte belegen ihre Plaetze also nicht auf Kosten der Dynamik --
        sonst haetten 75 Kernaktien das gesamte 100er-Limit aufgebraucht und
        es waere nie wieder eine neue Aktie hinzugekommen.

        Gepinnte Positionen zaehlen weiterhin NICHT gegen das Limit: sie
        muessen ueberwacht werden, egal wie voll das Universum ist.
        Favoriten reservieren nichts mehr -- sie sind reine
        Prioritaetsmarker (Georgs Vorgabe).
        """
        kern = self._kernwerte(broker)
        dynamische = [
            m for m in self.zustand.fuer_broker(broker)
            if not m.gepinnt
            and m.symbol.upper() not in kern
            and m.zustand in (AKTIV, ABGANG, BEOBACHTUNG)
        ]
        # 9.5.5 -- das Nachruecken.
        #
        # Ein gesperrter Kernwert belegt keinen dynamischen Platz, also wuerde
        # ohne diese Zeile schlicht nichts nachruecken: von 100 handelbaren
        # Werten blieben bei 24 gesperrten Kernwerten nur 76 uebrig. Fuer jeden
        # gesperrten Kernwert darf deshalb ein zusaetzlicher dynamischer Wert
        # aufgenommen werden. Die handelbare Gesamtzahl bleibt damit erhalten.
        #
        # Das dreht sich von selbst wieder zurueck, sobald der Kernwert wieder
        # brauchbare Kurse liefert: dann sinkt der Zuschlag, und die Dynamik
        # baut sich ueber die normalen Abgangsregeln wieder ab.
        zuschlag = self._gesperrte_kernwerte(broker)
        return max(0, int(regeln.dynamisch_limit) + zuschlag - len(dynamische))

    def _standard_inst_id(self, broker: str, symbol: str) -> str:
        """Vollstaendige Instrumentkennung fuer einen Kernwert ohne Bewertung.

        Bei OKX ist ein blosses "BTC" keine gueltige Kennung -- der Broker
        braucht "BTC-EUR" oder "BTC-USDC". Wird nur das Symbol gespeichert,
        normalisiert der Adapter spaeter still auf EUR, und auf einem
        USDC-Konto wuerde gegen die Lot- und Mindestgroessenregeln des
        falschen Marktes gerundet.
        """
        if str(broker).lower() != "okx":
            return symbol
        quote = str(getattr(self.cfg, "OKX_QUOTE_CCY", "") or "").upper()
        if not quote:
            erlaubt = tuple(getattr(
                self.cfg, "OKX_ALLOWED_QUOTE_CCY", ("EUR", "USD", "USDC")))
            quote = str(erlaubt[0]).upper() if erlaubt else "EUR"
        return f"{symbol}-{quote}"

    def _kern_sicherstellen(self, broker: str, diff: UniverseDiff,
                            bewertet: dict) -> int:
        """Legt fehlende Kernwerte als aktive Mitglieder an.

        Bewusst ohne Bewaehrung und ohne Rangpruefung: der Kern ist die feste
        Haelfte des Universums. Er wird beobachtet -- was nicht heisst, dass
        gekauft wird; darueber entscheidet weiterhin allein die Kaufkaskade.
        """
        angelegt = 0
        for symbol in sorted(self._kernwerte(broker)):
            if self.zustand.hole(broker, symbol) is not None:
                continue
            eintrag = bewertet.get(symbol)
            kandidat = eintrag["kandidat"] if eintrag else None
            mitglied = UniverseMitglied(
                symbol=symbol, broker=broker,
                asset_type=getattr(kandidat, "asset_type", "") or (
                    "crypto" if str(broker).lower() == "okx" else "stock"),
                inst_id=getattr(kandidat, "inst_id", "") or self._standard_inst_id(broker, symbol),
                market_quote_ccy=str(getattr(kandidat, "zusatz", {}).get(
                    "quote", "") or "").upper(),
                trade_quote_ccy=str(getattr(kandidat, "zusatz", {}).get(
                    "trade_quote_ccy", "") or "").upper(),
                zustand=AKTIV, tier=TIER_KERN,
                aufnahmegrund="fester Kern des Universums",
            )
            if (str(broker).lower() == "okx" and self._eligible_symbols is not None
                    and symbol not in self._eligible_symbols):
                grund = self._ineligible_reasons.get(
                    symbol, "aktuelles OKX-Paar besteht die Aufnahmefilter nicht")
                mitglied.abganggrund = f"{KERN_SICHERHEIT_BLOCKIERT}: {grund}"
            if eintrag:
                mitglied.merke_score(float(eintrag["score"].gesamt), int(eintrag["rang"]))
            self.zustand.setze(mitglied)
            diff.aufgenommen.append(symbol)
            audit.schreibe("AUFGENOMMEN", symbol=symbol, broker=broker,
                           grund="fester Kern des Universums", quelle="technisch")
            angelegt += 1
        return angelegt

    def _aufnehmen(self, eintrag: dict, regeln: UniverseRegeln, diff: UniverseDiff,
                   broker: str, favoriten: set) -> bool:
        kandidat: UniverseKandidat = eintrag["kandidat"]
        score = float(eintrag["score"].gesamt)
        rang = int(eintrag["rang"])
        tier = str(eintrag.get("tier", TIER_KANDIDAT))
        begruendung = str(eintrag.get("tier_begruendung", ""))
        symbol = kandidat.symbol.upper()
        ist_favorit = symbol in favoriten

        if not regeln.autonome_aufnahme and symbol not in self._kernwerte(broker):
            # Der Broker nimmt nicht selbst auf: der Wert wird nur
            # vorgeschlagen. Auch Favoriten sind hier nur Prioritaetsmarker.
            # Der feste Kern ist keine Neuaufnahme, sondern die Definition des
            # Universums -- er wird beobachtet, ohne dass jemand zustimmt.
            # Seit v8.1.5 gilt das fuer keinen der beiden Broker mehr; der
            # Zweig bleibt, damit ein Broker jederzeit wieder auf reine
            # Vorschlaege umgestellt werden kann.
            diff.gruende.setdefault("vorschlaege", []).append(
                {"symbol": symbol, "rang": rang, "score": round(score, 4),
                 "begruendung": begruendung})
            return False

        # Favoriten sind ab 8.1.3 AUSSCHLIESSLICH Prioritaetsmarker. Sie
        # loesen keine Aufnahme aus, reservieren keinen Platz und
        # ueberspringen keine Bewaehrung. Im Focus Set stehen sie weiterhin
        # vorn -- das ist Prioritaet, keine Freigabe.
        ist_kern = symbol in self._kernwerte(broker)
        # Nur der feste Kern darf die Bewaehrung ueberspringen. Ein Coin, der
        # lediglich als "etabliert" eingestuft wurde, muss sie ab 8.1.3
        # durchlaufen -- sonst waere das Universum wieder zu dynamisch.
        direkt = ist_kern
        mitglied = UniverseMitglied(
            symbol=kandidat.symbol, broker=broker, asset_type=kandidat.asset_type,
            inst_id=kandidat.inst_id or kandidat.symbol,
            market_quote_ccy=str(kandidat.zusatz.get("quote") or "").upper(),
            trade_quote_ccy=str(kandidat.zusatz.get("trade_quote_ccy") or "").upper(),
            zustand=AKTIV if direkt else BEOBACHTUNG,
            tier=TIER_KERN if ist_kern else tier,
            favorit=ist_favorit,
            aufnahmegrund=begruendung or eintrag["score"].begruendung,
        )
        mitglied.merke_score(score, rang)
        self.zustand.setze(mitglied)

        if direkt:
            diff.aufgenommen.append(symbol)
            audit.schreibe("AUFGENOMMEN", symbol=symbol, broker=broker,
                           grund=f"Kernwert: {begruendung}", neuer_rang=rang, neuer_score=score,
                           quelle="technisch")
        else:
            diff.beobachtung_gestartet.append(symbol)
            audit.schreibe("BEOBACHTUNG", symbol=symbol, broker=broker,
                           grund=f"Bewaehrung {regeln.beobachtung_stunden:.0f} h: {begruendung}",
                           neuer_rang=rang, neuer_score=score, quelle="technisch")
        return True

    def _beobachtung_pruefen(self, mitglied: UniverseMitglied, eintrag: dict,
                             regeln: UniverseRegeln, diff: UniverseDiff,
                             broker: str) -> bool:
        """Entscheidet, ob ein beobachteter Wert handelbar werden darf.

        Drei Bedingungen muessen ALLE erfuellt sein:
            1. Mindestbeobachtungszeit abgelaufen,
            2. Rang noch im Aufnahmebereich,
            3. Score stabil (kein Absturz gegenueber dem Verlauf).

        Nur wenn diese drei stimmen, wird optional zusaetzlich Luna gefragt.
        Die KI beeinflusst ausschliesslich die spaetere Aufmerksamkeit; sie
        kann weder freigeben noch blockieren (AIU-005).
        """
        rang = int(eintrag["rang"])
        score = float(eintrag["score"].gesamt)

        if mitglied.stunden_im_zustand < regeln.beobachtung_stunden:
            return False
        if rang > regeln.entfernung_rang:
            self._entferne(mitglied, diff, broker,
                           f"Bewaehrung nicht bestanden (Rang {rang})", sofort=False)
            return True
        if not self._score_stabil(mitglied, score):
            mitglied.zustand_seit = jetzt_iso()      # Bewaehrung verlaengern
            self.zustand.setze(mitglied)
            audit.schreibe("BEOBACHTUNG_VERLAENGERT", symbol=mitglied.symbol, broker=broker,
                           grund="Bewertung zu schwankend", neuer_rang=rang, neuer_score=score,
                           quelle="technisch")
            return False

        urteil, modell = self._ki_urteil(mitglied, eintrag)
        mitglied.ai_bewertung, mitglied.ai_modell = urteil, modell
        mitglied.wechsle(AKTIV, f"Bewaehrung bestanden (Rang {rang})")
        self.zustand.setze(mitglied)
        diff.freigegeben.append(mitglied.symbol.upper())
        audit.schreibe("FREIGEGEBEN", symbol=mitglied.symbol, broker=broker,
                       grund=f"Bewaehrung bestanden, Rang {rang}",
                       neuer_rang=rang, neuer_score=score,
                       quelle="technisch", ai_modell=modell)
        return True

    @staticmethod
    def _score_stabil(mitglied: UniverseMitglied, score: float,
                      max_abfall: float = 0.25) -> bool:
        """Ist die Bewertung ueber die Beobachtungszeit stabil geblieben?"""
        verlauf = [float(e.get("score", 0.0)) for e in mitglied.score_verlauf[-6:]]
        if len(verlauf) < 3:
            return False            # zu wenig Beobachtung = noch keine Freigabe
        bester = max(verlauf)
        if bester <= 0:
            return False
        return (bester - score) / bester <= max_abfall

    def _ki_urteil(self, mitglied: UniverseMitglied, eintrag: dict) -> tuple[str, str]:
        """Optionale Luna-Einschaetzung. Ausfall ist unkritisch (AIU-006)."""
        # Im Freqtrade-Sample-Modus gilt die vom Nutzer verlangte harte
        # Trennung: keine GPT-/News-Entscheidung, auch nicht indirekt als
        # Attention-Sortierung des Krypto-Universums. Die technische
        # Freigabe bleibt weiterhin allein von den harten Filtern abhaengig.
        try:
            from crypto_strategy_mode import FREQTRADE_SAMPLE, current_mode
            if current_mode() == FREQTRADE_SAMPLE and mitglied.broker == "okx":
                return "", ""
        except Exception:
            # Ein kaputter Modusschalter wird in crypto_strategy_mode selbst
            # fail-closed auf CRYPTO_PAUSED gesetzt. Hier darf daraus kein
            # zusaetzlicher Universumsabbruch fuer eToro entstehen.
            logger.debug("Krypto-Strategiemodus fuer KI-Trennung nicht lesbar",
                         exc_info=True)
        if self.ai is None:
            return "", ""
        try:
            antwort = self.ai.bewerte_universe_kandidat({
                "symbol": mitglied.symbol,
                "broker": mitglied.broker,
                "rang": int(eintrag["rang"]),
                "score": round(float(eintrag["score"].gesamt), 4),
                "score_teile": eintrag["score"].teile,
                "begruendung": eintrag["score"].begruendung,
                "alter_tage": round(float(getattr(eintrag["kandidat"], "alter_tage", 0.0)), 1),
                "umsatz_24h": float(getattr(eintrag["kandidat"], "volumen_quote_24h", 0.0)),
            })
        except Exception as exc:
            logger.info("KI-Urteil nicht verfuegbar (%s) -- technische Freigabe gilt.", exc)
            return "", ""
        if not isinstance(antwort, dict):
            return "", ""
        return str(antwort.get("attention", "") or "").upper(), str(antwort.get("modell", ""))

    def _hysterese(self, mitglied: UniverseMitglied, rang: int, alter_rang: int,
                   alter_score: float, regeln: UniverseRegeln, diff: UniverseDiff,
                   broker: str) -> None:
        if self.ist_kern(broker, mitglied.symbol):
            # Kernwerte kennen keinen Rangabgang. Der Rang wird trotzdem
            # fortgeschrieben, damit man in der Oberflaeche sieht, wie der
            # Wert gerade dasteht.
            if mitglied.schlechte_raenge_in_folge or mitglied.zustand == ABGANG:
                mitglied.schlechte_raenge_in_folge = 0
                mitglied.wechsle(AKTIV, "Kernwert -- kein Rangabgang")
            # Eine Kernsperre muss sich wieder loesen koennen. Sie wurde
            # gesetzt, weil ein harter Filter angeschlagen hat; wird der Wert
            # anschliessend wieder sauber bewertet, ohne dass die Bewertung
            # einen Sicherheitsgrund meldet, war der Anlass voruebergehend.
            # Ohne diese Zeile bliebe die Sperre bis zum Loeschen der
            # Zustandsdatei bestehen -- niemand raeumt abganggrund je auf.
            if mitglied.kern_blockiert:
                mitglied.abganggrund = ""
                audit.schreibe("KERN_ENTSPERRT", symbol=mitglied.symbol, broker=broker,
                               grund="wieder regulaer bewertet", neuer_rang=rang,
                               neuer_score=mitglied.letzter_score, quelle="technisch")
                logger.info("Kernwert %s ist wieder handelbar (Rang %s).",
                            mitglied.symbol, rang)
            mitglied.tier = TIER_KERN
            return
        """Rangbasierter Auf-/Abstieg mit Bestaetigungszaehler."""
        if rang <= regeln.entfernung_rang:
            if mitglied.schlechte_raenge_in_folge:
                mitglied.schlechte_raenge_in_folge = 0
                if mitglied.zustand == ABGANG:
                    mitglied.wechsle(AKTIV, f"Rang wieder im gruenen Bereich ({rang})")
                    audit.schreibe("ABGANG_ABGEBROCHEN", symbol=mitglied.symbol, broker=broker,
                                   grund=f"Rang {rang} wieder innerhalb der Grenze",
                                   alter_rang=alter_rang, neuer_rang=rang, quelle="technisch")
            return

        mitglied.schlechte_raenge_in_folge += 1
        if mitglied.zustand != ABGANG:
            mitglied.wechsle(ABGANG, f"Rang {rang} schlechter als {regeln.entfernung_rang}")
            diff.abgang_angekuendigt.append(mitglied.symbol.upper())
            audit.schreibe("ABGANG_ANGEKUENDIGT", symbol=mitglied.symbol, broker=broker,
                           grund=f"Rang {rang} > Entfernungsgrenze {regeln.entfernung_rang}",
                           alter_rang=alter_rang, neuer_rang=rang,
                           alter_score=alter_score, neuer_score=mitglied.letzter_score,
                           quelle="technisch")
            return

        if mitglied.schlechte_raenge_in_folge >= regeln.bestaetigungen_fuer_abgang:
            self._entferne(mitglied, diff, broker,
                           f"{mitglied.schlechte_raenge_in_folge} Laeufe in Folge schlechter "
                           f"als Rang {regeln.entfernung_rang}", sofort=False)

    def _sicherheitsabgang_pruefen(self, mitglied: UniverseMitglied, diff: UniverseDiff,
                                   regeln: UniverseRegeln, broker: str, grund: str) -> bool:
        """Ein Wert, der nicht mehr bewertet werden kann, verschwindet nicht still."""
        if self.ist_kern(broker, mitglied.symbol):
            if str(broker).lower() == "okx" and self._eligible_symbols is not None:
                eligible = self._eligible_symbols
                if mitglied.symbol.upper() not in eligible:
                    detail = self._ineligible_reasons.get(
                        mitglied.symbol.upper(),
                        "faellt durch einen aktuellen OKX-Aufnahmefilter")
                    self._entferne(
                        mitglied, diff, broker,
                        detail, sofort=True)
                    return False
                if mitglied.kern_blockiert:
                    mitglied.abganggrund = ""
                    mitglied.zustand = AKTIV
                    audit.schreibe(
                        "KERN_ENTSPERRT", symbol=mitglied.symbol, broker=broker,
                        grund="harte OKX-Filter wieder bestanden", quelle="sicherheit")
            # FEHLENDE BEWERTUNG IST KEIN SICHERHEITSPROBLEM.
            #
            # Ein Kernwert kann aus voellig harmlosen Gruenden aus der
            # Rangliste fallen: geschlossene Boerse, fehlende Kerzen,
            # ausgefallene Referenzquelle, aufgebrauchtes FMP-Tagesbudget.
            # Wuerde das wie ein Sicherheitsfilter behandelt, waere BTC nach
            # drei Laeufen -- also 45 Minuten -- dauerhaft unkaufbar, ohne
            # dass irgendetwas passiert waere.
            #
            # Ein echter Sicherheitsgrund kommt immer AUS der Bewertung
            # (Feld "sicherheitsabgang") und wird in lauf() sofort behandelt.
            # Hier landet nur, wer gar nicht bewertet wurde.
            mitglied.tier = TIER_KERN
            self.zustand.setze(mitglied)
            diff.gruende.setdefault("kern_ohne_bewertung", []).append(
                mitglied.symbol.upper())
            return False

        mitglied.schlechte_raenge_in_folge += 1
        if mitglied.schlechte_raenge_in_folge < regeln.bestaetigungen_fuer_abgang:
            if mitglied.zustand != ABGANG:
                mitglied.wechsle(ABGANG, grund)
                diff.abgang_angekuendigt.append(mitglied.symbol.upper())
            self.zustand.setze(mitglied)
            return False
        self._entferne(mitglied, diff, broker, grund, sofort=False)
        return True

    def _entferne(self, mitglied: UniverseMitglied, diff: UniverseDiff, broker: str,
                  grund: str, *, sofort: bool) -> None:
        """Entfernt aus dem Entry-Universe -- niemals aus dem Monitoring.

        Ein gepinnter Wert (offene Position) bleibt bestehen und wird nur
        auf ABGANG gesetzt. Sonst waere Exposure unbeobachtet, und genau das
        ist der Hummingbot-Lernpunkt.
        """
        if self.ist_kern(broker, mitglied.symbol):
            # Ein Kernwert verlaesst das Universum nie. Reisst er einen
            # harten Sicherheitsfilter, wird er fuer neue Einstiege
            # gesperrt und bleibt im Monitoring -- sichtbar mit Grund.
            mitglied.tier = TIER_KERN
            mitglied.abganggrund = f"{KERN_SICHERHEIT_BLOCKIERT}: {grund}"
            mitglied.zustand_seit = jetzt_iso()
            mitglied.zustand = AKTIV
            self.zustand.setze(mitglied)
            diff.gruende[mitglied.symbol.upper()] = mitglied.abganggrund
            audit.schreibe("KERN_GESPERRT", symbol=mitglied.symbol, broker=broker,
                           grund=grund, alter_rang=mitglied.letzter_rang,
                           quelle="sicherheit" if sofort else "technisch")
            # 10.1.10: Ein dauerhaft gesperrter Kernwert erzeugte dieselbe
            # Warnung bei jedem Universumslauf (alle 15 min). WARN nur beim
            # Zustandswechsel oder neuem Grund; der Audit-Beleg bleibt je Lauf.
            merkmal = (str(broker), mitglied.symbol.upper(), str(grund))
            gemerkt = getattr(self, "_kern_sperr_warnungen", set())
            if merkmal not in gemerkt:
                logger.warning("Kernwert %s ist wegen eines Sicherheitsfilters gesperrt: %s",
                               mitglied.symbol, grund)
                gemerkt.add(merkmal)
                self._kern_sperr_warnungen = gemerkt
            else:
                logger.debug("Kernwert %s weiterhin gesperrt: %s", mitglied.symbol, grund)
            return

        if mitglied.gepinnt:
            if sofort:
                mitglied.kaufblock_grund = grund
            mitglied.wechsle(ABGANG, f"{grund} (bleibt wegen offener Position im Monitoring)")
            self.zustand.setze(mitglied)
            audit.schreibe("ABGANG_GEPINNT", symbol=mitglied.symbol, broker=broker,
                           grund=grund, alter_rang=mitglied.letzter_rang,
                           quelle="sicherheit" if sofort else "technisch")
            return

        if not self._mitgliedschaft_aendern:
            if sofort:
                # Schutz sofort wirksam machen, ohne die Kandidatenliste
                # mehrmals taeglich zu entfernen und neu aufzubauen.
                mitglied.kaufblock_grund = grund
                self.zustand.setze(mitglied)
                diff.gruende[mitglied.symbol.upper()] = grund
            return

        mitglied.wechsle(ENTFERNT, grund)
        self.zustand.entferne(broker, mitglied.symbol)
        diff.entfernt.append(mitglied.symbol.upper())
        diff.gruende[mitglied.symbol.upper()] = grund
        audit.schreibe("ENTFERNT", symbol=mitglied.symbol, broker=broker, grund=grund,
                       alter_rang=mitglied.letzter_rang, alter_score=mitglied.letzter_score,
                       quelle="sicherheit" if sofort else "technisch")

    # -- Abfragen -----------------------------------------------------------
    def handelbare_symbole(self, broker: str) -> list[str]:
        """Werte, die neue Einstiege erzeugen duerfen."""
        return self.zustand.handelbare_symbole(broker)

    def monitoring_symbole(self, broker: str) -> list[str]:
        """Alles, was ueberwacht werden muss -- inklusive gepinnter Werte."""
        return sorted({m.symbol for m in self.zustand.fuer_broker(broker)
                       if m.handelbar or m.gepinnt})

    def focus_set(self, broker: str, limit: Optional[int] = None) -> list[str]:
        """Der kleine Teil, der teure Analyse bekommt (DU-011).

        Auswahl nach Score, aber gepinnte Werte stehen immer vorn: eine
        offene Position ist wichtiger als ein hoher Score ohne Einsatz.
        """
        regeln = UniverseRegeln.fuer(broker, self.cfg)
        grenze = int(limit or regeln.focus_limit)
        mitglieder = [m for m in self.zustand.fuer_broker(broker) if m.handelbar or m.gepinnt]
        ai_rank = {"HIGH": 0, "": 1, "MEDIUM": 1, "LOW": 2}
        mitglieder.sort(key=lambda m: (
            not m.gepinnt, not m.favorit, ai_rank.get(str(m.ai_bewertung).upper(), 1),
            -m.letzter_score,
        ))
        return [m.symbol for m in mitglieder[:max(1, grenze)]]

    def uebersicht(self, broker: str) -> dict:
        regeln = UniverseRegeln.fuer(broker, self.cfg)
        mitglieder = self.zustand.fuer_broker(broker)
        nach_zustand: dict[str, int] = {}
        for m in mitglieder:
            nach_zustand[m.zustand] = nach_zustand.get(m.zustand, 0) + 1
        kern = [m for m in mitglieder if self.ist_kern(broker, m.symbol)]
        return {
            "broker": broker,
            "gesamt": len(mitglieder),
            # Ab v8.1.3 ist jedes Universum zweigeteilt: fester Kern plus
            # begrenzter dynamischer Rest. Ohne diese Zahlen kann eine
            # Oberflaeche nicht zeigen, wie viel Dynamik ueberhaupt erlaubt ist.
            "kern": len(kern),
            "dynamisch": len(mitglieder) - len(kern),
            "kern_limit": regeln.kern_limit,
            "dynamisch_limit": regeln.dynamisch_limit,
            "aktiv": len([m for m in mitglieder if m.zustand == AKTIV]),
            "aktiv_limit": regeln.aktiv_limit,
            "focus_limit": regeln.focus_limit,
            "zustaende": nach_zustand,
            "handelbar": len([m for m in mitglieder if m.handelbar]),
            "gepinnt": len([m for m in mitglieder if m.gepinnt]),
            "beobachtung": len([m for m in mitglieder if m.zustand == BEOBACHTUNG]),
            "autonome_aufnahme": regeln.autonome_aufnahme,
            # v8.1.5: Wenn ohne Rueckfrage aufgenommen wird, muss die Oberflaeche
            # zeigen, WIE LANGE ein Wert erst beobachtet wird und wie viele
            # Wechsel ein Lauf ueberhaupt machen darf. Sonst sieht "autonom"
            # nach "unbegrenzt" aus.
            "beobachtung_stunden": regeln.beobachtung_stunden,
            "max_aenderungen_pro_lauf": regeln.max_aenderungen_pro_lauf,
            "letzte_tagesauswahl": self.zustand.tagesauswahl.get(str(broker).lower(), ""),
            "focus_set": self.focus_set(broker),
        }

    def mitglieder_tabelle(self, broker: str) -> list[dict]:
        """Fuer die Web-UI: eine Zeile je Mitglied, sortiert nach Rang."""
        import config
        underdogs = {str(r.get("symbol") or "").upper() for r in
                     getattr(config, "STOCK_CATALOG_SYMBOLS", [])
                     if isinstance(r, dict) and r.get("underdog")}
        zeilen = []
        for m in sorted(self.zustand.fuer_broker(broker),
                        key=lambda x: (x.letzter_rang or 9999)):
            zeilen.append({
                "symbol": m.symbol, "zustand": m.zustand, "tier": m.tier,
                "underdog": broker.lower() == "etoro" and m.symbol.upper() in underdogs,
                "rang": m.letzter_rang, "score": round(m.letzter_score, 4),
                "bester_rang": m.bester_rang, "gepinnt": m.gepinnt, "favorit": m.favorit,
                "stunden_im_universum": round(m.stunden_im_universum, 1),
                "stunden_im_zustand": round(m.stunden_im_zustand, 1),
                "aufnahmegrund": m.aufnahmegrund, "abganggrund": m.abganggrund,
                "kaufblock_grund": m.kaufblock_grund or (m.abganggrund if m.kern_blockiert else ""),
                "handelbar": m.handelbar,
                "ki": m.ai_bewertung, "ki_modell": m.ai_modell,
                "schlechte_raenge": m.schlechte_raenge_in_folge,
            })
        return zeilen


__all__ = ["UniverseManager", "UniverseRegeln"]
