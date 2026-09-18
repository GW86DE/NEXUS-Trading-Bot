"""Dynamische Aktien-Auswahl fuer eToro (DU-002).

UNTERSCHIED ZUR KRYPTOSEITE -- BEWUSST
======================================
Krypto darf der Bot eigenstaendig aufnehmen und entfernen. Aktien NICHT.

Bei Aktien bleibt der menschliche Freigabeschritt aus v6 vollstaendig
erhalten: der Bot rechnet, bewertet und SCHLAEGT VOR; die Aufnahme
passiert erst nach einer Bestaetigung per Telegram und wird erst beim
naechsten Start aktiv.

Gruende dafuer:
    - Aktien haben Unternehmensereignisse (Splits, Uebernahmen,
      Namensaenderungen), die ein Ticker allein nicht abbildet.
    - Ein falsch aufgeloester Ticker kann ein voellig anderes Unternehmen
      treffen. Der FLR.US-Regressionstest aus v5.10 existiert genau deshalb.
    - Der Aktienmarkt ist geschlossen, wenn die meisten Fehler auffallen --
      Korrekturen sind dann nicht sofort moeglich.

ENTFERNUNG IST DIE AUSNAHME
===========================
Ein Sicherheitsgrund (Delisting, Instrument nicht mehr handelbar,
Datenqualitaet kritisch) fuehrt SOFORT zur Deaktivierung, ohne Rueckfrage.
Nur die AUFNAHME braucht eine Bestaetigung -- das Abschalten eines
gefaehrlichen Werts darf nie an einer fehlenden Antwort haengen.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
import time
from typing import Iterable, Optional

import pandas as pd

from .modelle import TIER_ETABLIERT, TIER_KANDIDAT, UniverseKandidat, UniverseScore
from .scoring import cheap_score, quality_score

logger = logging.getLogger(__name__)

# Gruende, die eine sofortige Entfernung ohne Rueckfrage rechtfertigen (DU-009).
SOFORT_ENTFERNEN = (
    "delisting", "nicht handelbar", "identitaet unklar",
    "datenqualitaet kritisch", "extremer spread",
)


class StockUniverseSelector:
    """Bewertet Aktien fuer das dynamische Universum und erzeugt Vorschlaege."""

    def __init__(self, broker=None, massive=None, cfg=None):
        import config as _config
        self.broker = broker
        self.massive = massive
        self.cfg = cfg or _config

        self.min_dollar_volumen = float(getattr(self.cfg, "STOCK_UNIVERSE_MIN_DOLLAR_VOLUME", 20_000_000.0))
        self.max_spread = float(getattr(self.cfg, "STOCK_UNIVERSE_MAX_SPREAD_PCT", 0.008))
        self.vorauswahl = int(getattr(self.cfg, "STOCK_UNIVERSE_PRESELECTION", 160))
        self.sperrliste = {s.upper() for s in getattr(self.cfg, "STOCK_UNIVERSE_BLOCKLIST", ())}
        self.reference_network_allowed = True

    # -- Katalog ------------------------------------------------------------
    def katalog(self, instrumente: Optional[Iterable] = None) -> list:
        """Das zu bewertende Feld: aktuelles Universum plus Reserve."""
        if instrumente is not None:
            return list(instrumente)
        from contracts import build_universe
        stocks = list(getattr(self.cfg, "STOCK_SYMBOLS", []) or [])
        # Ein Aktien-Favorit wird nur als zusaetzlicher Kandidat fuer eToro
        # vorgemerkt. Er durchlaeuft danach exakt dieselben Broker-, Kurs-,
        # Liquiditaets-, Score- und Bewaehrungspruefungen wie jeder andere
        # dynamische Wert; die Favoritenliste veraendert config nicht.
        try:
            from favorites import merge_stock_favorite_rows
            stocks = merge_stock_favorite_rows(stocks)
        except Exception:
            logger.debug("Aktien-Favoriten nicht zum Katalog ergaenzt", exc_info=True)
        return build_universe(stocks, [], [])

    # -- Harte Filter -------------------------------------------------------
    # Namensbestandteile, die auf ein Instrument hindeuten, das der Bot
    # bewusst NICHT handelt. Georgs Vorgabe: nur Aktien und ETFs, keine
    # unklaren Instrumente und keine unerwuenschten CFDs.
    UNERWUENSCHTE_INSTRUMENTE = (
        "cfd", "warrant", "rights", " unit", "units ", "depositary",
        "acquisition corp", "merger corp", "blank check", "spac",
        "leveraged", " 2x", " 3x", "-2x", "-3x", "inverse", "ultrashort",
        "bull ", "bear ", "futures", "option",
    )

    ERLAUBTE_ASSETKLASSEN = ("stock", "etf", "equity", "")

    def _instrument_erlaubt(self, kandidat: UniverseKandidat) -> str:
        """Nur Aktien und ETFs. Alles Unklare bleibt draussen.

        Ein CFD verhaelt sich steuerlich und im Risiko anders als die Aktie,
        auf die er lautet -- und der Bot rechnet durchgehend mit Aktienlogik.
        Ein gehebeltes oder inverses Produkt bricht zusaetzlich die Annahme,
        dass der Kurs dem Basiswert folgt.
        """
        art = str(kandidat.zusatz.get("instrument_typ")
                  or kandidat.asset_type or "").strip().lower()
        if art not in self.ERLAUBTE_ASSETKLASSEN:
            return f"Instrumentenart {art!r} ist nicht Aktie oder ETF"
        if bool(kandidat.zusatz.get("ist_cfd")):
            return "CFD -- der Bot handelt nur echte Aktien und ETFs"
        name = str(kandidat.name or kandidat.zusatz.get("bezeichnung") or "").lower()
        for muster in self.UNERWUENSCHTE_INSTRUMENTE:
            if muster in name:
                return f"Instrumentbezeichnung enthaelt {muster.strip()!r}"
        return ""

    def _harter_filter(self, kandidat: UniverseKandidat) -> str:
        symbol = kandidat.symbol.upper()
        if symbol in self.sperrliste:
            return "steht auf der Sperrliste"
        instrument_grund = self._instrument_erlaubt(kandidat)
        if instrument_grund:
            return instrument_grund
        if not kandidat.handelbar:
            return "vom Broker nicht als handelbar gemeldet"
        if kandidat.preis <= 0:
            return "kein gueltiger Kurs"
        if kandidat.spread_pct > self.max_spread:
            return f"Spanne {kandidat.spread_pct * 100:.2f} % ueber Grenze {self.max_spread * 100:.2f} %"
        if kandidat.volumen_quote_24h and kandidat.volumen_quote_24h < self.min_dollar_volumen:
            return (f"Tagesumsatz {kandidat.volumen_quote_24h / 1e6:.1f} Mio unter Minimum "
                    f"{self.min_dollar_volumen / 1e6:.0f} Mio")
        return ""

    # -- Datenbeschaffung ---------------------------------------------------
    def sammle(self, instrumente: Optional[Iterable] = None) -> tuple[list[UniverseKandidat], list[tuple[str, str]]]:
        """Baut Kandidaten aus Broker-Quotes und MASSIVE-Referenzdaten."""
        pool: list[UniverseKandidat] = []
        abgelehnt: list[tuple[str, str]] = []

        for inst in self.katalog(instrumente):
            symbol = str(getattr(inst, "name", inst)).upper()
            kandidat = UniverseKandidat(
                symbol=symbol, broker="etoro", asset_type="stock", inst_id=symbol,
                sektor=str(getattr(inst, "sector", "") or ""),
            )
            # Was der Broker ueber das Instrument weiss, hat Vorrang.
            art = str(getattr(inst, "instrument_typ", "") or "").strip().lower()
            if art:
                kandidat.zusatz["instrument_typ"] = art
            if bool(getattr(inst, "ist_cfd", False)):
                kandidat.zusatz["ist_cfd"] = True
            bezeichnung = str(getattr(inst, "bezeichnung", "") or "").strip()
            if bezeichnung:
                kandidat.name = bezeichnung
            handelbar, grund = self._broker_pruefung(inst)
            kandidat.handelbar = handelbar
            if not handelbar:
                abgelehnt.append((symbol, grund))
                continue
            self._fuelle_quote(kandidat, inst)
            self._fuelle_referenz(kandidat)
            ablehnung = self._harter_filter(kandidat)
            if ablehnung:
                abgelehnt.append((symbol, ablehnung))
                continue
            pool.append(kandidat)
        return pool, abgelehnt

    def _broker_pruefung(self, inst) -> tuple[bool, str]:
        if self.broker is None:
            return True, ""
        try:
            return self.broker.instrument_handelbar(inst)
        except Exception as exc:
            logger.debug("Brokerpruefung %s fehlgeschlagen: %s", inst, exc)
            return True, ""      # kein Urteil ist besser als ein falsches

    def _fuelle_quote(self, kandidat: UniverseKandidat, inst) -> None:
        if self.broker is None:
            return
        try:
            quote = self.broker.latest_bid_ask(inst)
        except Exception as exc:
            logger.debug("Quote %s nicht verfuegbar: %s", kandidat.symbol, exc)
            return
        if not quote:
            return
        kandidat.bid = float(quote.get("bid") or 0.0)
        kandidat.ask = float(quote.get("ask") or 0.0)
        kandidat.preis = float(quote.get("last") or 0.0) or kandidat.ask or kandidat.bid
        # 9.5.5: Das Kursalter kommt in derselben Antwort mit und wurde bisher
        # weggeworfen. Es kostet also KEINEN zusaetzlichen Abruf.
        #
        # Am 02.09.2026 lieferte eToro fuer einen Teil der Aktien Kurse mit
        # konstant rund 20 Minuten Rueckstand (belegt: die Datenachse lief mit
        # Faktor 1,00 bei festem Versatz, und JPM wich um das Neunfache der
        # Handelsspanne von einer zweiten Quelle ab). Diese Werte landeten
        # weiter im Universum, belegten Kandidatenplaetze und wurden dann bei
        # jedem Versuch von der Kursaltergrenze abgelehnt.
        kandidat.zusatz["quote_age_seconds"] = self._quote_alter(quote)
        kandidat.zusatz["quote_source"] = str(quote.get("source") or "")
        if kandidat.bid > 0 and kandidat.ask > 0 and kandidat.ask >= kandidat.bid:
            mitte = (kandidat.bid + kandidat.ask) / 2.0
            kandidat.spread_pct = (kandidat.ask - kandidat.bid) / mitte if mitte > 0 else 0.0

    @staticmethod
    def _quote_alter(quote: dict):
        """Alter der Kurszeile in Sekunden, oder None wenn unbekannt."""
        stempel = quote.get("timestamp")
        if stempel in (None, ""):
            return None
        try:
            if isinstance(stempel, (int, float)):
                n = float(stempel)
                if n > 10_000_000_000:
                    n /= 1000.0
                gemessen = datetime.fromtimestamp(n, tz=timezone.utc)
            else:
                gemessen = datetime.fromisoformat(
                    str(stempel).strip().replace("Z", "+00:00"))
                if gemessen.tzinfo is None:
                    gemessen = gemessen.replace(tzinfo=timezone.utc)
            return max(0.0, (datetime.now(timezone.utc) - gemessen).total_seconds())
        except (TypeError, ValueError, OSError, OverflowError):
            return None

    def _fuelle_referenz(self, kandidat: UniverseKandidat) -> None:
        """Umsatz, Sektor und Handelsstatus aus FMP, ersatzweise MASSIVE.

        FMP liefert diese Daten im Gratistarif nachweislich (Profil und
        Tageskerzen); die Nachrichten-Endpunkte tun es nicht. Deshalb ist
        FMP hier die erste Wahl. MASSIVE bleibt als Ausweichquelle, falls
        FMP kein Budget mehr hat oder nicht eingerichtet ist.
        """
        daten = {}
        try:
            import fmp_reference
            referenz = fmp_reference.client()
            if referenz.konfiguriert:
                # Kein Abruf im Scan: es gilt der taegliche Zwischenspeicher.
                # Nachgezogen wird gebuendelt in auswahl().
                daten = referenz.referenzdaten(kandidat.symbol, erlaube_abruf=False)
        except Exception as exc:
            logger.debug("FMP-Referenz %s: %s", kandidat.symbol, exc)

        umsatz = float(daten.get("dollar_volumen") or 0.0)
        if umsatz > 0:
            kandidat.volumen_quote_24h = umsatz
        if daten.get("sektor") and not kandidat.sektor:
            kandidat.sektor = str(daten["sektor"])
        # Die Bezeichnung ist die Grundlage des Instrumentenfilters. Ohne sie
        # laesst sich ein CFD oder ein gehebeltes Produkt am Ticker allein
        # nicht erkennen.
        if daten.get("bezeichnung"):
            kandidat.name = str(daten["bezeichnung"])
            kandidat.zusatz["bezeichnung"] = kandidat.name
        if daten.get("ist_etf"):
            kandidat.zusatz["instrument_typ"] = "etf"
        for key in ("tagesanalyse", "jahresanalyse"):
            if key in daten:
                kandidat.zusatz[key] = daten[key]
        if "aktiv" in daten:
            kandidat.zusatz["fmp_aktiv"] = bool(daten["aktiv"])

        if umsatz > 0:
            return
        # Ausweichquelle
        if (not self.reference_network_allowed or self.massive is None
                or not getattr(self.massive, "konfiguriert", False)):
            return
        try:
            umsatz = self.massive.dollar_volumen(kandidat.symbol, tage=20)
        except Exception as exc:
            logger.debug("MASSIVE-Umsatz %s: %s", kandidat.symbol, exc)
            return
        if umsatz > 0:
            kandidat.volumen_quote_24h = umsatz

    def cheap_ranking(self, kandidaten: list[UniverseKandidat]) -> list[tuple[UniverseKandidat, UniverseScore]]:
        bewertet = [(k, cheap_score(k, asset_type="stock")) for k in kandidaten]
        bewertet.sort(key=lambda x: x[1].gesamt, reverse=True)
        return bewertet

    def quality_ranking(self, vorauswahl: list[UniverseKandidat], *,
                        instrumente_nach_symbol: Optional[dict] = None
                        ) -> list[tuple[UniverseKandidat, UniverseScore]]:
        ergebnis = []
        for kandidat in vorauswahl:
            inst = (instrumente_nach_symbol or {}).get(kandidat.symbol)
            if inst is not None and self.broker is not None:
                try:
                    self._fuelle_atr(kandidat, inst)
                except Exception as exc:
                    logger.debug("ATR %s nicht berechenbar: %s", kandidat.symbol, exc)
            ergebnis.append((kandidat, quality_score(kandidat, asset_type="stock")))
        ergebnis.sort(key=lambda x: x[1].gesamt, reverse=True)
        return ergebnis

    def _fuelle_atr(self, kandidat: UniverseKandidat, inst) -> None:
        df = self.broker.historie(inst, "40 D", "1 day", True)
        if df is None or df.empty or len(df) < 10:
            kandidat.kerzen_anzahl = int(len(df) if df is not None else 0)
            return
        kandidat.kerzen_anzahl = int(len(df))
        hoch, tief, schluss = df["high"], df["low"], df["close"]
        vorher = schluss.shift(1)
        spanne = pd.concat([hoch - tief, (hoch - vorher).abs(), (tief - vorher).abs()], axis=1).max(axis=1)
        atr = spanne.rolling(min(14, len(df) - 1)).mean().iloc[-1]
        letzter = float(schluss.iloc[-1] or 0.0)
        if letzter > 0 and not pd.isna(atr):
            kandidat.atr_pct = float(atr) / letzter
        if kandidat.preis <= 0:
            kandidat.preis = letzter

    # -- Einstufung ---------------------------------------------------------
    def einstufung(self, kandidat: UniverseKandidat) -> tuple[str, str]:
        """Aktien landen IMMER als Vorschlag -- nie automatisch handelbar."""
        return TIER_KANDIDAT, "Aktien benoetigen eine Freigabe per Telegram"

    def sicherheitsabgang(self, kandidat: UniverseKandidat) -> str:
        """Gibt einen Grund zurueck, wenn SOFORT entfernt werden muss."""
        # FMP-Profil zuerst: isActivelyTrading ist im Gratistarif enthalten
        # und beantwortet die Delisting-Frage ohne Zusatzkosten aus dem
        # taeglichen Zwischenspeicher.
        if kandidat.zusatz.get("fmp_aktiv") is False:
            return "Delisting laut FMP-Profil (isActivelyTrading = false)"
        if self.massive is not None and getattr(self.massive, "konfiguriert", False):
            aktiv = self.massive.ist_aktiv(kandidat.symbol)
            if aktiv is False:
                return "Delisting laut MASSIVE-Referenzdaten"
        if not kandidat.handelbar:
            return "vom Broker nicht mehr als handelbar gemeldet"
        if kandidat.spread_pct > max(0.05, self.max_spread * 5):
            return f"extremer Spread {kandidat.spread_pct * 100:.1f} %"
        return ""

    # -- Gesamtlauf ---------------------------------------------------------
    def auswahl(self, instrumente: Optional[Iterable] = None, *,
                erlaube_referenz_abruf: bool = True) -> dict:
        start = time.time()
        self.reference_network_allowed = bool(erlaube_referenz_abruf)
        liste = list(self.katalog(instrumente))
        nach_symbol = {str(getattr(i, "name", i)).upper(): i for i in liste}

        # Referenzdaten gebuendelt und budgetschonend nachziehen, BEVOR
        # bewertet wird. Pro Lauf nur die aeltesten Eintraege: 250 Anfragen
        # am Tag reichen nicht fuer 100 Werte in jedem der 32 Taeglaeufe.
        referenz_bericht = self._referenz_nachziehen(
            nach_symbol.keys(), erlaube_abruf=erlaube_referenz_abruf)

        pool, abgelehnt = self.sammle(liste)
        billig = self.cheap_ranking(pool)
        vorauswahl = [k for k, _ in billig[:max(20, self.vorauswahl)]]
        # 9.5.5: fuer die Boersenstatus-Pruefung unten gebraucht.
        self._instrumente_nach_symbol = nach_symbol
        hochwertig = self.quality_ranking(vorauswahl, instrumente_nach_symbol=nach_symbol)

        eingestuft = []
        for rang, (kandidat, score) in enumerate(hochwertig, start=1):
            tier, begruendung = self.einstufung(kandidat)
            eingestuft.append({"kandidat": kandidat, "score": score, "rang": rang,
                               "tier": tier, "tier_begruendung": begruendung,
                               "sicherheitsabgang": self.sicherheitsabgang(kandidat)})

        # 9.5.5: Werte mit dauerhaft veralteten Kursen benennen.
        veraltet = self._veraltete_kurse(eingestuft)

        dauer = time.time() - start
        logger.info("Aktien-Auswahl: %d im Katalog -> %d im Pool -> %d bewertet "
                    "(%.1fs), %d mit veralteten Kursen",
                    len(liste), len(pool), len(hochwertig), dauer, len(veraltet))
        return {
            "broker": "etoro",
            "asset_type": "stock",
            "katalog_gesamt": len(liste),
            "pool": len(pool),
            "abgelehnt": abgelehnt,
            "rangliste": eingestuft,
            "veraltete_kurse": veraltet,
            "dauer_sekunden": round(dauer, 2),
            "referenz": referenz_bericht,
            "hinweis": "Aufnahmen benoetigen eine Bestaetigung per Telegram.",
        }

    def _veraltete_kurse(self, eingestuft: list) -> dict:
        """Werte, deren Kurs bei OFFENER Boerse zu alt ist.

        9.5.5. Wichtig sind zwei Bedingungen, die beide erfuellt sein muessen:

        * Die Boerse muss laut Kalender offen sein. Nachts und am Wochenende
          ist jeder Kurs legitim alt -- ohne diese Pruefung wuerde jeden Abend
          das halbe Universum geparkt.
        * Das Alter muss deutlich ueber der Einstiegsgrenze liegen
          (Standard 10 Minuten gegenueber 3 Minuten fuer den Einstieg). Ein
          kurzer Aussetzer ist kein Grund, einen Wert aus dem Universum zu
          nehmen; ein konstanter Rueckstand von 20 Minuten schon.

        Die endgueltige Entscheidung faellt nicht hier: der Manager parkt erst,
        wenn derselbe Wert das in ZWEI Laeufen hintereinander meldet.
        """
        grenze = max(60.0, float(getattr(
            self.cfg, "STOCK_UNIVERSE_MAX_QUOTE_AGE_SECONDS", 600.0)))
        veraltet: dict[str, float] = {}
        for eintrag in eingestuft:
            kandidat = eintrag.get("kandidat")
            if kandidat is None:
                continue
            alter = (kandidat.zusatz or {}).get("quote_age_seconds")
            if alter is None or float(alter) <= grenze:
                continue
            if not self._boerse_offen(kandidat):
                continue
            veraltet[str(kandidat.symbol).upper()] = float(alter)
        return veraltet

    def _boerse_offen(self, kandidat) -> bool:
        """Ist die Boerse dieses Wertes laut Kalender gerade offen?

        Ohne belastbare Auskunft wird NICHT geparkt -- eine fehlende Messung
        darf keinen Wert aus dem Universum werfen.
        """
        if self.broker is None:
            return False
        try:
            from market_session import market_session_status
            inst = (self._instrumente_nach_symbol or {}).get(
                str(kandidat.symbol).upper())
            if inst is None:
                return False
            status = market_session_status(self.broker, inst)
            return bool(getattr(status, "kalender_offen", False))
        except Exception:
            logger.debug("Boersenstatus fuer %s nicht ermittelbar",
                         kandidat.symbol, exc_info=True)
            return False

    def _referenz_nachziehen(self, symbole, *, erlaube_abruf: bool = True) -> dict:
        """Haelt den FMP-Referenzcache aktuell, ohne das Tagesbudget zu sprengen."""
        if not erlaube_abruf:
            return {"aktiv": False, "netzabruf": False,
                    "grund": "Aktienmarkt geschlossen; nur persistenter Cache"}
        try:
            import fmp_reference
            referenz = fmp_reference.client()
        except Exception as exc:
            return {"aktiv": False, "grund": f"FMP nicht ladbar: {exc}"}
        if not referenz.konfiguriert:
            return {"aktiv": False, "grund": "Kein FMP-Schluessel hinterlegt"}
        grenze = 70 if referenz.starter() else int(getattr(self.cfg, "FMP_REQUESTS_PER_UNIVERSE_RUN", 20))
        try:
            bericht = referenz.aktualisiere_stapel(symbole, max_anfragen=grenze)
        except Exception as exc:
            logger.warning("FMP-Referenzabgleich fehlgeschlagen: %s", exc)
            return {"aktiv": False, "grund": str(exc)[:200]}
        return {"aktiv": True, **bericht}


__all__ = ["StockUniverseSelector", "SOFORT_ENTFERNEN"]
