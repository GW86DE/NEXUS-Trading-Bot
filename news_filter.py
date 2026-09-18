"""
Nachrichtenlage pruefen -- Krisenfilter vor Kauf und beim Verkauf.

WAS DIESES MODUL LEISTET
========================
Vor jedem Kauf und bei der Verwaltung offener Positionen werden aktuelle
Meldungen zum jeweiligen Wert abgerufen und auf Warnsignale untersucht.
Findet sich etwas Ernstes (Betrugsvorwurf, Insolvenzverfahren, Ermittlungen,
Handelsaussetzung, Gewinnwarnung ...), wird

  - ein geplanter KAUF verhindert
  - bei einer offenen Position ein Verkauf ausgeloest oder empfohlen

Zusaetzlich gibt es eine Marktlage-Pruefung: bei breiten Markteinbruechen
werden vorsichtshalber gar keine neuen Positionen eroeffnet.


WAS DIESES MODUL NICHT LEISTET -- bitte ernst nehmen
=====================================================
Das ist eine STICHWORTSUCHE, kein Verstehen von Text. Daraus folgt:

  - FALSCHE ALARME sind normal. "Untersuchung" kann auch in einer
    harmlosen Meldung stehen ("Studie untersucht Wirkstoff"). Der Bot
    verzichtet dann auf einen Kauf, der vielleicht gut gewesen waere.
    Das ist der bewusst gewaehlte, guenstigere Fehler.
  - UEBERSEHENE KRISEN sind ebenso moeglich. Eine schlecht formulierte
    oder sehr neue Meldung wird nicht erkannt. Der Filter ersetzt also
    KEINE eigene Aufmerksamkeit.
  - Nachrichten kommen mit Verzoegerung. Wer schneller reagieren will als
    die Nachrichtenlage, braucht andere Werkzeuge.
  - Kursrelevante Ereignisse ohne Meldung (z.B. ein Kurssturz ohne
    Nachricht) erkennt dieser Filter grundsaetzlich nicht -- dafuer sind
    die Stop-Orders zustaendig.

Kurz: Der Filter faengt das Offensichtliche ab. Er macht den Bot nicht
klug, sondern nur etwas weniger blind.


DATENQUELLEN
============
Die v8.2 nutzt einen Multi-Source-Aggregator. Je nach Einrichtung werden
SEC EDGAR, Nasdaq Halts, Yahoo Finance, Google News, GDELT, Alpha Vantage,
Finnhub, Financial Modeling Prep und MASSIVE kombiniert. Meldungen werden
vor der Bewertung dedupliziert.
Nicht eingerichtete Quellen werden uebersprungen; im Status ist sichtbar,
welche Quelle zuletzt erreichbar war.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import config
from live_settings import news_rule
from provider_safety import redact
from news_sources import MultiSourceNews

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Signalwoerter
# ---------------------------------------------------------------------------
# Gewichtung: 3 = schwerwiegend, 2 = ernst, 1 = beobachten.
# Ab SCHWELLE_BLOCK wird ein Kauf verhindert, ab SCHWELLE_VERKAUF wird bei
# offener Position zum Ausstieg geraten.

SIGNALE_SCHWER = {
    "bankruptcy": 3, "chapter 11": 3, "insolvenz": 3, "insolvency": 3,
    "fraud": 3, "betrug": 3, "accounting scandal": 3, "bilanzskandal": 3,
    "delisting": 3, "delisted": 3, "trading halt": 3, "handelsaussetzung": 3,
    "sec investigation": 3, "sec probe": 3, "criminal charges": 3,
    "going concern": 3, "restatement": 3, "embezzlement": 3,
    "liquidation": 3, "receivership": 3, "default on debt": 3,
    "bilanzfaelschung": 3, "bilanzfälschung": 3, "zahlungsunfaehig": 3,
    "zahlungsunfähig": 3, "glaeubigerschutz": 3, "gläubigerschutz": 3,
    "untreue": 3, "geldwaesche": 3, "geldwäsche": 3,
}

SIGNALE_ERNST = {
    "investigation": 2, "ermittlung": 2, "probe": 2, "subpoena": 2,
    "lawsuit": 2, "klage": 2, "litigation": 2, "settlement": 2,
    "guidance cut": 2, "cuts guidance": 2, "lowers guidance": 2,
    "profit warning": 2, "gewinnwarnung": 2, "warns": 2,
    "recall": 2, "rueckruf": 2, "data breach": 2, "datenleck": 2,
    "ceo resigns": 2, "cfo resigns": 2, "steps down": 2, "ruecktritt": 2,
    "short seller": 2, "short-seller": 2, "fraud allegations": 2,
    "layoffs": 2, "restructuring": 2, "impairment": 2, "writedown": 2,
    "downgrade": 2, "herabgestuft": 2, "misses estimates": 2,
    "disappointing": 2, "plunge": 2, "sinks": 2, "tumbles": 2,
    "prognose gesenkt": 2, "umsatzwarnung": 2, "abschreibung": 2,
    "stellenabbau": 2, "kursverlust": 2, "einbruch": 2, "skandal": 2,
    "verlustwarnung": 2, "kapitalerhoehung": 2, "kapitalerhöhung": 2,
}

SIGNALE_BEOBACHTEN = {
    "concern": 1, "risk": 1, "weakness": 1, "slowdown": 1,
    "competition": 1, "pressure": 1, "uncertainty": 1, "volatile": 1,
    "declines": 1, "falls": 1, "warnung": 1,
}

ALLE_SIGNALE = {**SIGNALE_SCHWER, **SIGNALE_ERNST, **SIGNALE_BEOBACHTEN}

# STAMMWORT-TREFFER
# Die Woerter oben werden mit Wortgrenzen gesucht, damit "risk" nicht in
# "brisk" trifft. Das hat aber einen Haken: gebeugte Formen fallen durch.
# "trading halt" trifft NICHT auf "trading halted", "lay off" nicht auf
# "layoffs" -- genau die Formen, die in Schlagzeilen ueblich sind.
#
# Fuer lange, eindeutige Wortstaemme ist eine Teilwortsuche deshalb
# sinnvoll und ungefaehrlich: "bankrupt" trifft "bankruptcy" und
# "bankrupted", aber es gibt kein harmloses Wort, das "bankrupt" enthaelt.
# Kurze oder mehrdeutige Staemme gehoeren NICHT hierher.
SIGNALE_STAMM = {
    "bankrupt": 3,          # bankruptcy, bankrupted
    "insolven": 3,          # insolvency, insolvent, Insolvenz
    "delist": 3,            # delisting, delisted
    "trading halt": 3,      # trading halted, trading halts
    "halted": 3,            # shares halted
    # "suspend" allein waere zu breit -- eine Firma kann auch eine
    # Werbekampagne aussetzen. Nur die kursrelevanten Kombinationen:
    "trading suspend": 3, "suspended trading": 3, "suspends trading": 3,
    "notierung ausgesetzt": 3, "handel ausgesetzt": 3,
    "suspends dividend": 2, "dividende ausgesetzt": 2,
    "suspends production": 2, "produktion ausgesetzt": 2,
    "embezzl": 3,           # embezzlement, embezzled
    "liquidat": 3,          # liquidation, liquidated
    "indict": 3,            # indictment, indicted
    "fraudulen": 3,         # fraudulent
    "layoff": 2,            # layoffs
    "lay off": 2,           # lay off
    "downgrad": 2,          # downgrade, downgraded
    "plunge": 2,            # plunged, plunges
    "slash": 2,             # slashes guidance, slashed
    "recall": 2,            # recalls, recalled
    "resign": 2,            # resigns, resigned, resignation
    "writedown": 2, "write-down": 2, "impair": 2,
    "misses estimate": 2, "missed estimate": 2,
    "guidance cut": 2, "cuts guidance": 2, "slashes guidance": 2,
}

# Woerter, die einen Treffer ENTSCHAERFEN -- reduziert falsche Alarme.
# Beispiel: "study investigates" ist keine Behoerdenermittlung.
ENTWARNUNG = [
    "study", "studie", "research investigates", "clinical trial",
    "settles lawsuit favorably", "dismissed", "abgewiesen",
    "resolves", "beigelegt", "raises guidance", "beats estimates",
]

# KOMBINATIONEN: Behoerde + Handlung im selben Satz ist schwerwiegend,
# auch wenn die feste Wortfolge nicht passt ("SEC opens investigation"
# statt "SEC investigation"). Ohne diese Regel wuerde eine Ermittlung der
# Boersenaufsicht wie eine beliebige Untersuchung gewertet.
# Kurze, mehrdeutige Kuerzel brauchen exakte Wortgrenzen -- "sec" als
# Teilwort wuerde sonst in "second", "section" oder "security" treffen.
BEHOERDEN_EXAKT = ["sec", "doj", "ftc", "finra", "bafin", "esma", "cftc"]

# Lange, eindeutige Begriffe als Teilwort -- noetig fuer deutsche
# Zusammensetzungen wie "Staatsanwaltschaft" oder "Aufsichtsbehoerde",
# bei denen eine Wortgrenzen-Suche nicht greift.
BEHOERDEN_TEIL = ["staatsanwalt", "prosecutor", "regulator", "aufsichtsbehoerde",
                  "aufsichtsbehörde", "attorney general", "finanzaufsicht"]

BEHOERDEN_HANDLUNG = ["investigat", "probe", "charges", "subpoena", "sues",
                      "lawsuit", "ermitt", "anklage", "durchsuch", "raid",
                      "razzia", "verfahren", "klagt", "vorwurf"]
KOMBINATION_GEWICHT = 3

SCHWELLE_BLOCK = 3      # ab hier: kein Kauf
SCHWELLE_VERKAUF = 4    # ab hier: offene Position schliessen


@dataclass
class Nachrichtenlage:
    """Ergebnis einer Pruefung."""
    symbol: str
    punkte: int = 0
    treffer: list = field(default_factory=list)
    schlagzeilen: list = field(default_factory=list)
    geprueft: bool = False          # False = keine Daten verfuegbar
    hinweis: str = ""
    quellen: list = field(default_factory=list)
    quellen_fehler: dict = field(default_factory=dict)
    quellen_meldungen: list = field(default_factory=list)
    quellen_abdeckung: dict = field(default_factory=dict)
    risiko_belege: dict = field(default_factory=dict)

    @property
    def aktiver_halt(self) -> bool:
        from news_sources import _safe_dt, _now_utc
        for row in self.quellen_meldungen:
            meta = row.get('metadata', {})
            checked = _safe_dt(meta.get('observed_at'))
            resumed = _safe_dt(meta.get('resumes_at'))
            if (row.get('source') == 'Nasdaq Halts' and row.get('official')
                    and self.symbol in row.get('symbols', []) and meta.get('active_halt')
                    and meta.get('hard_signal') and checked
                    and 0 <= (_now_utc() - checked).total_seconds() <= 90
                    and (not resumed or resumed > _now_utc())):
                return True
        return False

    @property
    def kauf_blockiert(self) -> bool:
        return self.aktiver_halt or (self.geprueft and self.punkte >= news_rule("NEWS_BLOCK_SCORE", SCHWELLE_BLOCK))

    @property
    def verkauf_empfohlen(self) -> bool:
        return self.geprueft and self.punkte >= news_rule("NEWS_EXIT_THRESHOLD", SCHWELLE_VERKAUF)

    def kurzfassung(self) -> str:
        if not self.geprueft:
            return f"{self.symbol}: keine Nachrichtendaten ({self.hinweis})"
        q = f" | Quellen={len(set(self.quellen))}" if self.quellen else ""
        if not self.treffer:
            if any(not r.get('complete') for r in self.quellen_abdeckung.values()):
                return f"{self.symbol}: kein Warnbeleg in vorhandenen Meldungen; Quellenabdeckung unvollstaendig{q}"
            return f"{self.symbol}: unauffaellig ({len(self.schlagzeilen)} Meldungen){q}"
        worte = ", ".join(sorted({w for w, _ in self.treffer})[:5])
        return f"{self.symbol}: {self.punkte} Punkte [{worte}]{q}"

    def details(self) -> str:
        zeilen = [self.kurzfassung()]
        for schlagzeile in self.schlagzeilen[:5]:
            zeilen.append(f"    - {schlagzeile[:110]}")
        return "\n".join(zeilen)


class NachrichtenFilter:
    def __init__(self, cache_minuten: int = None):
        self._cache = {}
        self._cache_minuten = (cache_minuten
                               if cache_minuten is not None
                               else getattr(config, "NEWS_CACHE_MINUTES", 30))
        self._multi = MultiSourceNews()
        self._last_bundle = {}

    # -----------------------------------------------------------------
    def _hole_schlagzeilen(self, symbol: str, stunden: int, focused: bool = True) -> tuple:
        """Gibt (schlagzeilen, konnte_pruefen, hinweis, quellen, fehler) zurueck."""
        try:
            bundle = self._multi.fetch_symbol(symbol, hours=stunden, focused=focused)
            self._last_bundle[str(symbol).upper()] = bundle
            schlagzeilen = []
            for artikel in bundle.items:
                provider = "+".join(artikel.providers or [artikel.source])
                text = f"[{provider}] {artikel.headline} {artikel.summary}".strip()
                if artikel.official:
                    text += " [OFFIZIELLE MELDUNG]"
                schlagzeilen.append(text)
            konnte = bool(set(bundle.sources_ok) - {"Federal Reserve", "GDELT", "FMP Symbol Search"}) or bool(bundle.items)
            hinweis = ""
            if bundle.sources_failed:
                hinweis = "Teilquellen ausgefallen: " + ", ".join(sorted(bundle.sources_failed))
            if not konnte:
                hinweis = hinweis or "keine Nachrichtendatenquelle erreichbar/eingerichtet"
            # Fuer die Handelsentscheidung zaehlen nur Quellen, die zu DIESEM
            # Symbol tatsaechlich mindestens eine relevante Meldung beigetragen
            # haben. "API erreichbar" allein ist kein zweiter Beleg.
            # Nach Deduplizierung zaehlt pro Meldung nur die Primaerquelle.
            # Derselbe syndizierte Artikel auf mehreren Quellen ist damit EIN
            # Beleg, nicht zwei kuenstlich unabhaengige Quellen.
            contributing_sources = sorted({artikel.source for artikel in bundle.items})
            source_items = [
                {
                    "source": artikel.source,
                    "text": f"{artikel.headline} {artikel.summary}".strip(),
                    "official": bool(artikel.official),
                    "symbols": list(artikel.symbols), "metadata": dict(artikel.metadata),
                    "url": artikel.url, "published_at": artikel.published_at.isoformat() if artikel.published_at else None,
                }
                for artikel in bundle.items
            ]
            return schlagzeilen, konnte, hinweis, contributing_sources, bundle.sources_failed, source_items
        except Exception as exc:
            logger.warning("Multi-Source-Nachrichtenabruf %s fehlgeschlagen: %s", symbol, redact(exc))
            return [], False, f"Abruf fehlgeschlagen: {exc}", [], {"Aggregator": redact(exc)}, []

    # -----------------------------------------------------------------
    @staticmethod
    def _bewerte(schlagzeilen: list) -> tuple:
        """Zaehlt Signalwoerter. Gibt (punkte, treffer) zurueck."""
        punkte = 0
        treffer = []
        # Multi-Source-Deduplizierung findet bereits vorher statt. Fuer direkte
        # Unit-Tests/Manuellisten hier trotzdem identische Texte nur einmal.
        unique = []
        seen = set()
        for raw in schlagzeilen:
            norm = re.sub(r"\s+", " ", str(raw).strip().lower())
            if norm and norm not in seen:
                seen.add(norm); unique.append(raw)

        # Dasselbe Signal darf ueber mehrere wirklich verschiedene Meldungen
        # bestaetigt werden, aber nicht unbegrenzt Punkte durch News-Spam sammeln.
        signal_counts = {}
        cap = news_rule('NEWS_MAX_SIGNAL_REPETITIONS', 2)
        def add(signal, weight):
            nonlocal punkte
            if signal_counts.get(signal, 0) < cap:
                signal_counts[signal] = signal_counts.get(signal, 0) + 1
                punkte += weight
                treffer.append((signal, weight))
        for text in unique:
            klein = text.lower()

            # Entwarnung: Meldung wirkt harmlos trotz Signalwort
            if any(e in klein for e in ENTWARNUNG):
                continue

            gefunden = set()

            for wort, gewicht in ALLE_SIGNALE.items():
                # Wortgrenzen beachten, damit "risk" nicht in "brisk" trifft
                if re.search(r"\b" + re.escape(wort) + r"\b", klein):
                    add(wort, gewicht)
                    gefunden.add(wort)

            # Stammwoerter als Teiltreffer -- faengt gebeugte Formen ab.
            for stamm, gewicht in SIGNALE_STAMM.items():
                if stamm in klein:
                    # Nicht doppelt zaehlen, wenn oben schon getroffen
                    if any(stamm in w for w in gefunden):
                        continue
                    add(stamm, gewicht)
                    gefunden.add(stamm)

            # Behoerde + Handlung im selben Text -> schwerwiegend
            behoerde = next(
                (b for b in BEHOERDEN_EXAKT
                 if re.search(r"\b" + re.escape(b) + r"\b", klein)),
                None,
            ) or next((b for b in BEHOERDEN_TEIL if b in klein), None)
            if behoerde and any(h in klein for h in BEHOERDEN_HANDLUNG):
                add(f"{behoerde}+ermittlung", KOMBINATION_GEWICHT)

        return punkte, treffer

    # -----------------------------------------------------------------
    def pruefe(self, symbol: str, stunden: int = None, focused: bool = True) -> Nachrichtenlage:
        """Nachrichtenlage eines Wertes pruefen (mit Zwischenspeicher).

        ``focused=False`` nutzt vor allem den gemeinsamen Marktfeed. Das ist fuer
        regulaere Scanner-HOLDs ausreichend und verhindert hunderte unnoetige
        Company-API-Abfragen. Bei gehaltenen Positionen, BUY-Signalen,
        priorisierten News-Events und freigegebenen Underdogs wird focused=True
        verwendet.
        """
        stunden = stunden or int(getattr(config, "NEWS_LOOKBACK_HOURS", 48))
        symbol = str(symbol).upper()
        focus_key = (symbol, True)
        base_key = (symbol, False)
        # Eine bereits fokussierte Pruefung ist auch fuer den Basisfall gueltig.
        keys = [focus_key] if focused else [focus_key, base_key]
        for key in keys:
            eintrag = self._cache.get(key)
            ttl = self._cache_minuten * 60
            if eintrag and ('Nasdaq Halts' in eintrag[1].quellen_abdeckung or
                            any(r.get('metadata', {}).get('active_halt') for r in eintrag[1].quellen_meldungen)):
                ttl = min(ttl, 60)
                from news_sources import _safe_dt, _now_utc
                for row in eintrag[1].quellen_meldungen:
                    meta = row.get('metadata', {})
                    if meta.get('active_halt'):
                        observed = _safe_dt(meta.get('observed_at'))
                        resumes = _safe_dt(meta.get('resumes_at'))
                        if not observed or not 0 <= (_now_utc()-observed).total_seconds() <= 90 or (resumes and resumes <= _now_utc()):
                            ttl = 0
            if eintrag and (time.time() - eintrag[0]) < ttl:
                return eintrag[1]

        schlagzeilen, konnte, hinweis, quellen, fehler, source_items = self._hole_schlagzeilen(symbol, stunden, focused=focused)
        punkte, treffer = self._bewerte(schlagzeilen)

        lage = Nachrichtenlage(
            symbol=symbol, punkte=punkte, treffer=treffer,
            schlagzeilen=schlagzeilen, geprueft=konnte, hinweis=hinweis,
            quellen=quellen, quellen_fehler=fehler, quellen_meldungen=source_items,
            quellen_abdeckung=getattr(self._last_bundle.get(str(symbol).upper()), 'source_coverage', {}),
        )
        if not konnte:
            logger.warning('NEWS_DEGRADED %s: Keine geprueften Newsdaten; deterministische Kaskade bleibt aktiv; %s', symbol, hinweis)
        self._cache[focus_key if focused else base_key] = (time.time(), lage)

        if lage.kauf_blockiert:
            logger.warning("NACHRICHTEN %s: Kauf blockiert (%d Punkte) %s",
                           symbol, punkte, sorted({w for w, _ in treffer}))
        return lage

    # -----------------------------------------------------------------
    def marktlage(self) -> Nachrichtenlage:
        """Breite Markt-/Krisenlage aus mehreren Nachrichtenquellen.

        Anders als frueher ist dies nicht nur die Summe von SPY/QQQ/DIA-News.
        GDELT, MASSIVE, Alpha Vantage, Finnhub und FMP koennen hier auch
        globale Ereignisse erfassen, die noch keinem Index-Ticker zugeordnet sind.
        """
        try:
            universe = [x.get("symbol") for x in getattr(config, "STOCK_SYMBOLS", [])]
            bundle = self._multi.fetch_market(hours=24, universe=universe)
            schlagzeilen = []
            for artikel in bundle.items:
                provider = "+".join(artikel.providers or [artikel.source])
                schlagzeilen.append(f"[{provider}] {artikel.headline} {artikel.summary}".strip())
            punkte, treffer = self._bewerte(schlagzeilen)
            lage = Nachrichtenlage(
                symbol="MARKT", punkte=punkte, treffer=treffer,
                schlagzeilen=schlagzeilen, geprueft=bool(bundle.sources_ok),
                hinweis=("Teilquellen ausgefallen: " + ", ".join(sorted(bundle.sources_failed))) if bundle.sources_failed else "",
                quellen=sorted({x.source for x in bundle.items}), quellen_fehler=bundle.sources_failed,
                quellen_abdeckung=bundle.source_coverage,
                quellen_meldungen=[{"source": x.source, "text": x.text(), "official": bool(x.official),
                    "url": x.url, "published_at": x.published_at.isoformat() if x.published_at else None,
                    "symbols": list(x.symbols), "metadata": dict(x.metadata)} for x in bundle.items],
            )
            from event_intelligence import news_risk_evidence
            lage.risiko_belege = news_risk_evidence(lage)
            if lage.risiko_belege["status"] == "UNCONFIRMED_RISK_HINT":
                lage.hinweis = (lage.hinweis + "; " if lage.hinweis else "") + "Einzelquellen-Risikohinweis; Mehrquellenabgleich fehlt"
            return lage
        except Exception as exc:
            logger.warning("Multi-Source-Marktlage fehlgeschlagen: %s", exc)
            return Nachrichtenlage(symbol="MARKT", geprueft=False, hinweis=redact(exc))

    def markt_kritisch(self) -> bool:
        grenze = int(news_rule("NEWS_MARKET_THRESHOLD", 8))
        lage = self.marktlage()
        from event_intelligence import news_risk_evidence
        evidence = getattr(lage, "risiko_belege", {}) or news_risk_evidence(lage)
        # Broad market risk hints require separate publication origins. Actual
        # Nasdaq halts and position SL/price exits keep their own protection path.
        supported_points, _ = self._bewerte(evidence.get("risk_texts", []))
        return lage.geprueft and evidence["buy_pause_supported"] and supported_points >= grenze

    def leere_cache(self):
        self._cache.clear()
