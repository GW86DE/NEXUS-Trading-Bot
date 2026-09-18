"""
Kennzahlen-Screening fuer Underdogs.

WARUM ES DAS GIBT
=================
Eine Liste "vielversprechender" kleiner Aktien ist schnell zusammengestellt
-- und wenig wert, solange niemand die Zahlen prueft. Dieses Modul holt
AKTUELLE Kennzahlen und laesst nur Werte durch, die messbare Kriterien
erfuellen. Damit beruht die Auswahl auf Daten statt auf Behauptungen.

GEPRUEFTE KRITERIEN
===================
  Marktkapitalisierung  im Zielbereich (nicht zu klein, nicht zu gross)
  Handelsvolumen        ausreichend liquide -- sonst schlechte Ausfuehrung
  Umsatzwachstum        das Geschaeft waechst
  Profitabilitaet       positive Marge; Verluste nur bei starkem Wachstum
  Verschuldung          Schulden im Verhaeltnis zum Eigenkapital begrenzt
  Kursabstand           nicht direkt am 52-Wochen-Tief (fallendes Messer)

EHRLICHE EINORDNUNG
===================
Auch bestandene Kennzahlen sagen nichts ueber die Zukunft. Sie schliessen
lediglich Firmen aus, die schon nach heutigen Zahlen fragwuerdig sind.
Kennzahlen sind zudem rueckwaertsgewandt und teils veraltet; eine Firma
kann heute gute Zahlen zeigen und morgen eine schlechte Meldung bringen --
dafuer ist der Nachrichtenfilter zustaendig.

Datenquelle ist yfinance (kostenlos). Die Daten sind brauchbar, aber nicht
in Profi-Qualitaet: einzelne Felder fehlen gelegentlich oder sind
verspaetet. Fehlende Werte fuehren nicht automatisch zum Ausschluss,
werden aber vermerkt.

Aufruf:
    python underdog_screening.py
"""

from __future__ import annotations

import json
import logging
import warnings
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

warnings.filterwarnings("ignore")

import config

logger = logging.getLogger(__name__)

ERGEBNIS_DATEI = Path(__file__).parent / "underdog_freigabe.json"


@dataclass
class Pruefergebnis:
    symbol: str
    bestanden: bool = False
    punkte: int = 0
    kennzahlen: dict = field(default_factory=dict)
    gruende: list = field(default_factory=list)
    fehler: str = ""

    def zeile(self) -> str:
        if self.fehler:
            return f"  {self.symbol:6s} FEHLER    {self.fehler[:50]}"
        k = self.kennzahlen
        marke = "BESTANDEN" if self.bestanden else "abgelehnt"
        return (f"  {self.symbol:6s} {marke:10s} "
                f"MK {k.get('marktkap_mrd', 0):6.2f} Mrd | "
                f"Wachstum {k.get('umsatzwachstum_pct', 0):6.1f} % | "
                f"Marge {k.get('marge_pct', 0):6.1f} % | "
                f"Schulden/EK {k.get('schulden_ek', 0):5.2f}")


def _hole_kennzahlen(symbol: str) -> dict:
    import yfinance as yf

    ticker = yf.Ticker(symbol)
    info = ticker.info or {}

    def zahl(*schluessel, standard=None):
        for s in schluessel:
            wert = info.get(s)
            if wert is not None and wert == wert:      # NaN ausschliessen
                return wert
        return standard

    marktkap = zahl("marketCap", standard=0) or 0
    kurs = zahl("currentPrice", "regularMarketPrice", standard=0) or 0
    tief52 = zahl("fiftyTwoWeekLow", standard=0) or 0
    hoch52 = zahl("fiftyTwoWeekHigh", standard=0) or 0
    volumen = zahl("averageVolume", "averageVolume10days", standard=0) or 0

    return {
        "marktkap_mrd": marktkap / 1e9,
        "kurs": kurs,
        "volumen": volumen,
        "tagesumsatz_mio": (volumen * kurs) / 1e6 if kurs else 0,
        "umsatzwachstum_pct": (zahl("revenueGrowth", standard=0) or 0) * 100,
        "gewinnwachstum_pct": (zahl("earningsGrowth", "earningsQuarterlyGrowth",
                                    standard=0) or 0) * 100,
        "marge_pct": (zahl("profitMargins", standard=0) or 0) * 100,
        "operative_marge_pct": (zahl("operatingMargins", standard=0) or 0) * 100,
        "schulden_ek": (zahl("debtToEquity", standard=0) or 0) / 100,
        "kgv": zahl("trailingPE", "forwardPE", standard=0) or 0,
        "abstand_tief_pct": ((kurs / tief52 - 1) * 100) if tief52 else 0,
        "abstand_hoch_pct": ((kurs / hoch52 - 1) * 100) if hoch52 else 0,
        "branche": info.get("sector", "?"),
        "name": info.get("shortName", symbol),
    }


def pruefe_symbol(symbol: str) -> Pruefergebnis:
    ergebnis = Pruefergebnis(symbol=symbol)
    try:
        k = _hole_kennzahlen(symbol)
    except Exception as exc:
        ergebnis.fehler = str(exc)
        return ergebnis

    ergebnis.kennzahlen = k
    punkte = 0
    gruende = []

    min_mk = float(getattr(config, "UNDERDOG_MIN_MARKTKAP_MRD", 0.3))
    max_mk = float(getattr(config, "UNDERDOG_MAX_MARKTKAP_MRD", 25.0))
    min_umsatz = float(getattr(config, "UNDERDOG_MIN_TAGESUMSATZ_MIO", 5.0))
    min_wachstum = float(getattr(config, "UNDERDOG_MIN_WACHSTUM_PCT", 3.0))
    max_schulden = float(getattr(config, "UNDERDOG_MAX_SCHULDEN_EK", 2.5))

    # --- Ausschlusskriterien (K.o.) ---
    if k["marktkap_mrd"] <= 0:
        gruende.append("keine Marktkapitalisierung verfuegbar")
    elif not (min_mk <= k["marktkap_mrd"] <= max_mk):
        gruende.append(f"Marktkap {k['marktkap_mrd']:.2f} Mrd ausserhalb "
                       f"{min_mk}-{max_mk} Mrd")
    else:
        punkte += 1

    if k["tagesumsatz_mio"] < min_umsatz:
        gruende.append(f"zu wenig Handelsumsatz ({k['tagesumsatz_mio']:.1f} Mio/Tag, "
                       f"noetig {min_umsatz})")
    else:
        punkte += 1

    if k["schulden_ek"] > max_schulden:
        gruende.append(f"hohe Verschuldung (Schulden/EK {k['schulden_ek']:.2f})")
    else:
        punkte += 1

    # --- Qualitaetskriterien ---
    # Wachstum ist bei Underdogs ebenfalls ein AUSSCHLUSSKRITERIUM.
    # Die ganze Ueberlegung hinter kleineren Werten ist, dass sie noch
    # wachsen koennen. Ein stagnierendes kleines Unternehmen hat weder die
    # Stabilitaet eines Grosskonzerns noch die Wachstumsaussicht -- also
    # den Nachteil ohne den Vorteil.
    if k["umsatzwachstum_pct"] >= min_wachstum:
        punkte += 1
    else:
        gruende.append(f"Umsatzwachstum nur {k['umsatzwachstum_pct']:.1f} % "
                       f"(mind. {min_wachstum:.0f} % noetig)")

    if k["marge_pct"] > 0:
        punkte += 1
    elif k["umsatzwachstum_pct"] >= 20:
        # Verlust wird toleriert, wenn das Geschaeft stark waechst --
        # aber ausdruecklich vermerkt.
        punkte += 1
        gruende.append("noch keine Gewinnmarge, aber starkes Wachstum")
    else:
        gruende.append(f"negative Marge ({k['marge_pct']:.1f} %) ohne starkes Wachstum")

    if k["operative_marge_pct"] > 0:
        punkte += 1

    # Nicht direkt am Jahrestief kaufen. Das ist bewusst ein
    # AUSSCHLUSSKRITERIUM, kein Punktabzug: ein Wert, der gerade auf ein
    # Jahrestief faellt, faellt erfahrungsgemaess oft weiter -- gute
    # Kennzahlen an anderer Stelle wiegen das nicht auf.
    min_abstand = float(getattr(config, "UNDERDOG_MIN_ABSTAND_TIEF_PCT", 10.0))
    if k["abstand_tief_pct"] >= min_abstand:
        punkte += 1
    else:
        gruende.append(f"Kurs nur {k['abstand_tief_pct']:.1f} % ueber dem "
                       f"52-Wochen-Tief (mind. {min_abstand:.0f} % noetig)")

    noetig = int(getattr(config, "UNDERDOG_MIN_PUNKTE", 6))
    ergebnis.punkte = punkte
    # Ausschlusskriterien: einzelne davon genuegen zur Ablehnung,
    # unabhaengig von der Gesamtpunktzahl.
    ko_kriterien = ("Marktkap", "keine Marktkap", "zu wenig", "hohe Versch",
                    "Kurs nur", "Umsatzwachstum nur", "negative Marge")
    hat_ko = any(g.startswith(ko_kriterien) for g in gruende)
    ergebnis.bestanden = punkte >= noetig and not hat_ko
    ergebnis.gruende = gruende
    return ergebnis


def screening_ausfuehren(symbole=None, verbose=True):
    import watchlist

    symbole = symbole or [x["symbol"] for x in watchlist.underdogs()]

    if verbose:
        print("=" * 78)
        print("UNDERDOG-SCREENING")
        print("=" * 78)
        print(f"Pruefe {len(symbole)} Kandidaten anhand aktueller Kennzahlen ...")
        print("(Datenquelle yfinance -- kann einen Moment dauern)")
        print()

    ergebnisse = []
    for i, symbol in enumerate(symbole, 1):
        ergebnis = pruefe_symbol(symbol)
        ergebnisse.append(ergebnis)
        if verbose:
            print(f"[{i:2d}/{len(symbole)}]{ergebnis.zeile()}")

    bestanden = [e for e in ergebnisse if e.bestanden]
    abgelehnt = [e for e in ergebnisse if not e.bestanden and not e.fehler]
    fehler = [e for e in ergebnisse if e.fehler]

    if verbose:
        print()
        print("=" * 78)
        print(f"ERGEBNIS: {len(bestanden)} bestanden | {len(abgelehnt)} abgelehnt "
              f"| {len(fehler)} nicht pruefbar")
        print("=" * 78)

        if bestanden:
            print("\nFREIGEGEBEN (der Bot darf diese Werte handeln):")
            for e in sorted(bestanden, key=lambda x: -x.punkte):
                k = e.kennzahlen
                print(f"  {e.symbol:6s} {e.punkte}/7 Punkte  {k.get('name','')[:32]}")
                print(f"         {k.get('branche','?')} | "
                      f"Wachstum {k.get('umsatzwachstum_pct',0):.1f} % | "
                      f"Marge {k.get('marge_pct',0):.1f} % | "
                      f"{k.get('tagesumsatz_mio',0):.0f} Mio Umsatz/Tag")

        if abgelehnt:
            print("\nABGELEHNT (Grund):")
            for e in abgelehnt:
                print(f"  {e.symbol:6s} {e.punkte}/7 -- {e.gruende[0] if e.gruende else '?'}")

        if fehler:
            print("\nNICHT PRUEFBAR (keine Daten):")
            for e in fehler:
                print(f"  {e.symbol:6s} {e.fehler[:60]}")

        print()
        print("-" * 78)
        print("WICHTIG: Bestandene Kennzahlen sind kein Qualitaetssiegel und erst")
        print("recht keine Prognose. Sie schliessen nur Werte aus, die schon nach")
        print("heutigen Zahlen fragwuerdig sind. Kennzahlen sind rueckwaertsgewandt.")
        print("-" * 78)

    freigabe = {
        "erstellt": datetime.now(timezone.utc).isoformat(),
        "freigegeben": [e.symbol for e in bestanden],
        "abgelehnt": {e.symbol: (e.gruende[0] if e.gruende else "") for e in abgelehnt},
        "nicht_pruefbar": [e.symbol for e in fehler],
        "kennzahlen": {e.symbol: e.kennzahlen for e in ergebnisse if e.kennzahlen},
    }
    ERGEBNIS_DATEI.write_text(json.dumps(freigabe, indent=2, ensure_ascii=False,
                                        default=str), encoding="utf-8")
    if verbose:
        print(f"\nGespeichert in {ERGEBNIS_DATEI.name} -- der Bot liest das beim Start.")

    return ergebnisse


def freigegebene_underdogs() -> list:
    """
    Vom Bot verwendet: welche Underdogs sind aktuell freigegeben?
    Ohne Screening-Ergebnis wird eine LEERE Liste zurueckgegeben --
    lieber gar keine Underdogs handeln als ungeprueft.
    """
    if not ERGEBNIS_DATEI.exists():
        return []
    try:
        daten = json.loads(ERGEBNIS_DATEI.read_text(encoding="utf-8"))
        max_alter = int(getattr(config, "UNDERDOG_SCREENING_MAX_ALTER_TAGE", 14))
        erstellt = datetime.fromisoformat(daten["erstellt"])
        alter = (datetime.now(timezone.utc) - erstellt).days
        if alter > max_alter:
            logger.warning("Underdog-Screening ist %d Tage alt (Grenze %d) -- "
                           "bitte erneut ausfuehren.", alter, max_alter)
            return []
        return list(daten.get("freigegeben", []))
    except Exception as exc:
        logger.warning("Underdog-Freigabe nicht lesbar: %s", exc)
        return []


if __name__ == "__main__":
    screening_ausfuehren()
