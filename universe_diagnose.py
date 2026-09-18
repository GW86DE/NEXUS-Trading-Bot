"""Universumsdiagnose: welcher Filter verwirft wie viele Werte (neu in v8.1.4).

WARUM DIESES MODUL EXISTIERT
============================
Am 25.08.2026 stand im Logbuch:

    OKX-Universum: 2 von 581 Instrumenten bestehen die harten Filter.
    Universumslauf okx: keine Aenderungen

Zwei von 581. Damit ging nie ein Kandidat in Bewaehrung, die KI wurde nie
gefragt, und das ganze dynamische Universum -- 50 aktive Werte, 12 im Focus
Set -- war Theorie.

Nur: WELCHER Filter die 579 verworfen hat, stand nirgends. Die Ablehnungen
lagen zwar im Rueckgabewert des Selektors, wurden aber nirgends ausgewertet.
Ohne diese Zahl laesst sich nicht entscheiden, ob eine Schwelle falsch steht
oder ob das duenne Demo-Orderbuch die Ursache ist.

Dieses Modul zaehlt und gruppiert -- es aendert **keine Schwelle**. Erst
messen, dann reden.
"""

from __future__ import annotations

import logging
import re
from collections import Counter

logger = logging.getLogger(__name__)

# Ablehnungsgruende auf stabile Gruppen abbilden. Die Texte enthalten
# konkrete Zahlen ("Tagesumsatz 12.345 unter Minimum 2.000.000"), deshalb
# wird auf Muster geprueft und nicht auf Gleichheit.
GRUPPEN: tuple[tuple[str, str], ...] = (
    (r"handelt gegen", "andere Quotewaehrung"),
    (r"Instrumentstatus", "nicht handelbar (Status)"),
    (r"Sperrliste", "Sperrliste"),
    (r"Stablecoin", "Stablecoin"),
    (r"gehebeltes Produkt", "Hebelprodukt"),
    (r"Tick/Lot|Mindestgroesse unbekannt", "Handelsregeln unbekannt"),
    (r"Tage gelistet", "zu jung"),
    (r"kein Ticker", "kein Ticker"),
    (r"kein gueltiger Preis", "kein Preis"),
    (r"kein positiver Tagesumsatz", "kein Tagesumsatz"),
    # Nur fuer alte Diagnosehistorien; 9.0.12 erzeugt diese starre
    # Mindestumsatz-Ablehnung nicht mehr.
    (r"Tagesumsatz .* unter Minimum", "Tagesumsatz zu klein"),
    (r"kein finanzierter Handelskanal", "Handelskanal ohne Guthaben"),
    (r"Marktquote .* keine Bot-Cashwaehrung|keine nutzbare OKX-Abrechnungswaehrung",
     "keine erlaubte Abrechnungswaehrung"),
    (r"beidseitiges Orderbuch", "kein beidseitiges Orderbuch"),
    (r"Spanne nicht berechenbar", "Spanne nicht berechenbar"),
    (r"Spanne .* ueber Grenze", "Spanne zu weit"),
    (r"nicht im kontoseitig handelbaren Kern oder in DYNAMIC_30", "nicht in Kern/Dynamik"),
    (r"sekundaere Quotewaehrung", "zweites Paar desselben Coins"),
    (r"CFD|Warrant|SPAC|Hebel|inverse|Unit", "unerwuenschtes Instrument"),
    (r"Assetklasse", "falsche Assetklasse"),
)


def gruppiere(grund: str) -> str:
    """Einen Ablehnungstext auf eine Gruppe abbilden."""
    text = str(grund or "")
    for muster, name in GRUPPEN:
        if re.search(muster, text, re.IGNORECASE):
            return name
    return "sonstiges"


def zaehle(abgelehnt) -> list[dict]:
    """Ablehnungen zaehlen, absteigend.

    ``abgelehnt`` ist die Liste ``[(inst_id, grund), ...]`` aus dem Selektor.
    """
    zaehler: Counter = Counter()
    beispiele: dict[str, str] = {}
    for eintrag in abgelehnt or ():
        try:
            kennung, grund = eintrag[0], eintrag[1]
        except (TypeError, IndexError):
            continue
        gruppe = gruppiere(grund)
        zaehler[gruppe] += 1
        beispiele.setdefault(gruppe, f"{kennung}: {str(grund)[:120]}")
    return [{"gruppe": gruppe, "anzahl": anzahl, "beispiel": beispiele.get(gruppe, "")}
            for gruppe, anzahl in zaehler.most_common()]


def bericht(auswahl: dict) -> dict:
    """Aus einem Selektorergebnis eine Diagnose machen."""
    abgelehnt = auswahl.get("abgelehnt") or []
    gesamt = int(auswahl.get("katalog_gesamt", 0) or 0)
    pool = int(auswahl.get("pool", 0) or 0)
    gruppen = zaehle(abgelehnt)
    return {
        "broker": auswahl.get("broker", ""),
        "katalog_gesamt": gesamt,
        "public_catalog_gesamt": int(auswahl.get("public_catalog_gesamt", 0) or 0),
        "account_catalog_gesamt": int(auswahl.get("account_catalog_gesamt", gesamt) or 0),
        "public_only_gesamt": int(auswahl.get("public_only_gesamt", 0) or 0),
        "trade_lanes": dict(auswahl.get("trade_lanes") or {}),
        "volume_policy": str(auswahl.get("volume_policy") or ""),
        "pool": pool,
        "abgelehnt_gesamt": len(abgelehnt),
        "bewertet": len(auswahl.get("rangliste") or []),
        "hard_eligible_bases": len(auswahl.get("hard_eligible_bases") or []),
        "core_actual": len(auswahl.get("core_symbols") or []),
        "core_target": int((auswahl.get("core_state") or {}).get("target", 20)),
        "gruppen": gruppen,
        # Der Filter, der am meisten verwirft -- die erste Stellschraube,
        # falls der Pool zu klein ist.
        "groesster_filter": gruppen[0]["gruppe"] if gruppen else "",
    }


def textbericht(diagnose: dict, grenze: int = 8) -> str:
    """Der Bericht als lesbarer Block fuer Protokoll und Oberflaeche."""
    broker = str(diagnose.get("broker") or "").lower()
    if broker == "okx":
        katalog = (f"OKX meldet {diagnose.get('account_catalog_gesamt', diagnose.get('katalog_gesamt', 0))} "
                   "Instrumente als fuer dieses Konto verfuegbare SPOT-Maerkte; "
                   f"{diagnose.get('public_catalog_gesamt', 0)} Maerkte sind oeffentlich sichtbar, "
                   "aber nicht automatisch fuer dieses Konto orderfaehig. ")
    else:
        name = "eToro" if broker == "etoro" else "Broker nicht belegt"
        katalog = (f"{name}: {diagnose.get('katalog_gesamt', 0)} Instrumente im untersuchten Katalog. "
                   "Die Katalogaufnahme allein bestaetigt keine Orderfreigabe. ")
    zeilen = [katalog +
              f"→ {diagnose.get('hard_eligible_bases', 0)} geeignete Basen "
              f"→ Kern {diagnose.get('core_actual', 0)}/{diagnose.get('core_target', 20)} "
              f"→ {diagnose.get('pool', 0)} im Pool "
              f"→ {diagnose.get('bewertet', 0)} bewertet"]
    for eintrag in (diagnose.get("gruppen") or [])[:grenze]:
        zeilen.append(f"  {eintrag['gruppe']:<32s} {eintrag['anzahl']:>5d}")
    rest = len(diagnose.get("gruppen") or []) - grenze
    if rest > 0:
        zeilen.append(f"  … und {rest} weitere Gruppen")
    return "\n".join(zeilen)


def protokolliere(auswahl: dict) -> dict:
    """Diagnose erstellen und ins Protokoll schreiben. Wirft nie."""
    try:
        diagnose = bericht(auswahl)
        for zeile in textbericht(diagnose).split("\n"):
            logger.info("Universumsdiagnose: %s", zeile)
        return diagnose
    except Exception:
        logger.debug("Universumsdiagnose nicht erstellbar", exc_info=True)
        return {}


__all__ = ["GRUPPEN", "gruppiere", "zaehle", "bericht", "textbericht", "protokolliere"]
