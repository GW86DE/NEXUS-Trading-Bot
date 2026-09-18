"""Bewertung der Marktqualitaet fuer das dynamische Universum (DU-006).

GRUNDGEDANKE
============
Ein Universum, das nach 24h-Performance sortiert, kauft systematisch das,
was gerade schon gestiegen ist. Das ist der klassische Pump-/Winner-Bias und
er zerstoert die Ergebnisse zuverlaessig.

Deshalb bewertet dieses Modul NICHT die erwartete Rendite, sondern die
HANDELBARKEIT:

    Liquiditaet         -- kommt der Bot ohne Marktbewegung rein und raus?
    Spread-Qualitaet    -- wie teuer ist der Ein-/Ausstieg sofort?
    Umsatz              -- ist ueberhaupt genug los?
    Datenqualitaet      -- sind die Kerzen vollstaendig und aktuell?
    Volatilitaets-Guete -- gibt es Bewegung, aber keine Achterbahn?
    Marktaktivitaet     -- passiert gerade etwas, oder schlaeft der Wert?

VOLATILITAET IST NICHT MONOTON
==============================
Zu wenig Bewegung heisst: keine Chance. Zu viel Bewegung heisst: der Stop
wird von Rauschen ausgeloest. Deshalb gibt es ein Optimum in der Mitte und
extreme Werte senken den Score wieder.
"""

from __future__ import annotations

import math
from typing import Optional

from .modelle import UniverseKandidat, UniverseScore

# Gewichte laut Backlog DU-006.
GEWICHTE_KRYPTO = {
    "liquiditaet": 0.30,
    "spread": 0.20,
    "umsatz": 0.20,
    "datenqualitaet": 0.10,
    "volatilitaet": 0.10,
    "aktivitaet": 0.10,
}

GEWICHTE_AKTIEN = {
    "liquiditaet": 0.30,
    "spread": 0.20,
    "umsatz": 0.20,
    "datenqualitaet": 0.10,
    "volatilitaet": 0.10,
    "aktivitaet": 0.10,
}

# Referenzwerte: ab hier gilt ein Kriterium als "sehr gut" (Score 1,0).
REFERENZ_KRYPTO = {
    "umsatz_quote_24h": 150_000_000.0,   # Tagesumsatz in Quotewaehrung
    "orderbuch_tiefe": 250_000.0,        # Quotewaehrung im sichtbaren Buch
    "spread_gut_pct": 0.0005,            # 0,05 %
    "spread_schlecht_pct": 0.0060,       # 0,60 %
    "atr_optimum_pct": 0.030,            # 3 % Tagesschwankung
    "atr_max_pct": 0.150,                # ab 15 % wird es Rauschen
}

REFERENZ_AKTIEN = {
    "umsatz_quote_24h": 200_000_000.0,   # USD Tagesumsatz
    "orderbuch_tiefe": 500_000.0,
    "spread_gut_pct": 0.0003,
    "spread_schlecht_pct": 0.0080,
    "atr_optimum_pct": 0.020,
    "atr_max_pct": 0.100,
}


# ---------------------------------------------------------------------------
# Normierungshelfer -- alle liefern Werte zwischen 0 und 1
# ---------------------------------------------------------------------------
def log_normiert(wert: float, referenz: float) -> float:
    """Logarithmische Normierung fuer Groessen ueber viele Zehnerpotenzen.

    Umsatz reicht von 10.000 bis 10.000.000.000. Linear normiert waeren
    99 % aller Werte praktisch null; logarithmisch bleiben Unterschiede im
    unteren Bereich sichtbar.
    """
    try:
        w = max(0.0, float(wert))
        r = max(1.0, float(referenz))
    except (TypeError, ValueError):
        return 0.0
    if w <= 0:
        return 0.0
    return max(0.0, min(1.0, math.log10(1.0 + w) / math.log10(1.0 + r)))


def spread_guete(spread_pct: float, gut: float, schlecht: float) -> float:
    """1,0 bei sehr engem Spread, 0,0 ab der Schmerzgrenze."""
    try:
        s = float(spread_pct)
    except (TypeError, ValueError):
        return 0.0
    if s <= 0:
        return 0.0                     # kein Bid/Ask = keine Aussage = kein Bonus
    if s <= gut:
        return 1.0
    if s >= schlecht:
        return 0.0
    return max(0.0, min(1.0, 1.0 - (s - gut) / (schlecht - gut)))


def volatilitaets_guete(atr_pct: float, optimum: float, maximum: float) -> float:
    """Glockenfoermig: zu ruhig ist schlecht, zu wild ist schlechter."""
    try:
        a = max(0.0, float(atr_pct))
    except (TypeError, ValueError):
        return 0.0
    if a <= 0:
        return 0.0
    if a <= optimum:
        return max(0.0, min(1.0, a / optimum))
    if a >= maximum:
        return 0.0
    # Nach dem Optimum faellt der Wert linear bis zum Maximum.
    return max(0.0, min(1.0, 1.0 - (a - optimum) / (maximum - optimum)))


def datenguete(kandidat: UniverseKandidat) -> float:
    """Vollstaendigkeit und Aktualitaet der Kursdaten."""
    punkte = 1.0
    if kandidat.kerzen_anzahl:
        luecken_anteil = kandidat.kerzen_luecken / max(1, kandidat.kerzen_anzahl)
        punkte -= min(0.6, luecken_anteil * 3.0)
    if kandidat.datenalter_sekunden > 0:
        # Aelter als 10 Minuten kostet spuerbar, aelter als eine Stunde fast alles.
        alter = kandidat.datenalter_sekunden
        if alter > 3600:
            punkte -= 0.6
        elif alter > 600:
            punkte -= 0.25
    if kandidat.preis <= 0:
        punkte = 0.0
    if kandidat.bid <= 0 or kandidat.ask <= 0:
        punkte -= 0.2
    return max(0.0, min(1.0, punkte))


def aktivitaets_guete(kandidat: UniverseKandidat) -> float:
    """Bewegt sich der Wert ueberhaupt -- ohne Richtung zu belohnen.

    Bewusst der BETRAG der Tagesbewegung: eine Aufwaertsbewegung ist hier
    kein Pluspunkt, sonst waere der Winner-Bias durch die Hintertuer zurueck.
    """
    bewegung = abs(float(kandidat.change_24h_pct or 0.0))
    # 1 % Bewegung gilt als lebendig, 8 % als voll ausgereizt.
    if bewegung <= 0:
        return 0.0
    if bewegung >= 0.08:
        return 1.0
    return max(0.0, min(1.0, bewegung / 0.08))


# ---------------------------------------------------------------------------
# Bewertungsstufen
# ---------------------------------------------------------------------------
def cheap_score(kandidat: UniverseKandidat, *, asset_type: str = "crypto") -> UniverseScore:
    """Stufe A: nur aus Ticker-Daten, fuer sehr viele Instrumente (DU-005).

    Bewusst OHNE Orderbuch, ATR oder Kerzen: diese Stufe laeuft ueber
    mehrere hundert Werte und muss mit einem einzigen API-Aufruf auskommen.
    """
    ref = REFERENZ_KRYPTO if asset_type == "crypto" else REFERENZ_AKTIEN
    gewichte = GEWICHTE_KRYPTO if asset_type == "crypto" else GEWICHTE_AKTIEN

    umsatz = log_normiert(kandidat.volumen_quote_24h, ref["umsatz_quote_24h"])
    spread = spread_guete(kandidat.spread_pct, ref["spread_gut_pct"], ref["spread_schlecht_pct"])
    daten = datenguete(kandidat)
    aktivitaet = aktivitaets_guete(kandidat)

    # In der guenstigen Stufe ist der Umsatz der beste verfuegbare Stellvertreter
    # fuer Liquiditaet. Die echte Orderbuchtiefe kommt erst in Stufe B.
    teile = {
        "liquiditaet": umsatz,
        "spread": spread,
        "umsatz": umsatz,
        "datenqualitaet": daten,
        "volatilitaet": 0.5,        # neutral, bis ATR bekannt ist
        "aktivitaet": aktivitaet,
    }
    gesamt = sum(teile[k] * gewichte[k] for k in gewichte)
    return UniverseScore(gesamt=gesamt, teile=teile, stufe="cheap",
                         begruendung=f"Umsatz {kandidat.volumen_quote_24h:,.0f}, "
                                     f"Spanne {kandidat.spread_pct * 100:.3f} %")


def quality_score(kandidat: UniverseKandidat, *, asset_type: str = "crypto") -> UniverseScore:
    """Stufe B: mit Orderbuch, ATR und Kerzenqualitaet (DU-005).

    Laeuft nur noch fuer die reduzierte Vorauswahl -- typischerweise 60 bis
    120 Werte statt mehreren hundert.
    """
    ref = REFERENZ_KRYPTO if asset_type == "crypto" else REFERENZ_AKTIEN
    gewichte = GEWICHTE_KRYPTO if asset_type == "crypto" else GEWICHTE_AKTIEN

    tiefe = log_normiert(kandidat.orderbuch_tiefe_quote, ref["orderbuch_tiefe"])
    umsatz = log_normiert(kandidat.volumen_quote_24h, ref["umsatz_quote_24h"])
    spread_roh = spread_guete(kandidat.spread_pct, ref["spread_gut_pct"], ref["spread_schlecht_pct"])
    # Ein enger, aber staendig springender Spread ist wertlos.
    stabilitaet = max(0.0, min(1.0, float(kandidat.spread_stabilitaet or 1.0)))
    spread = spread_roh * (0.6 + 0.4 * stabilitaet)
    volatilitaet = volatilitaets_guete(kandidat.atr_pct, ref["atr_optimum_pct"], ref["atr_max_pct"])
    daten = datenguete(kandidat)
    aktivitaet = aktivitaets_guete(kandidat)

    # Liquiditaet ist Orderbuchtiefe UND Umsatz -- eines allein taeuscht.
    liquiditaet = 0.6 * tiefe + 0.4 * umsatz if kandidat.orderbuch_tiefe_quote > 0 else umsatz

    teile = {
        "liquiditaet": liquiditaet,
        "spread": spread,
        "umsatz": umsatz,
        "datenqualitaet": daten,
        "volatilitaet": volatilitaet,
        "aktivitaet": aktivitaet,
    }
    gesamt = sum(teile[k] * gewichte[k] for k in gewichte)
    return UniverseScore(
        gesamt=gesamt, teile=teile, stufe="quality",
        begruendung=(f"Tiefe {kandidat.orderbuch_tiefe_quote:,.0f}, "
                     f"ATR {kandidat.atr_pct * 100:.2f} %, "
                     f"Spanne {kandidat.spread_pct * 100:.3f} %"),
    )


def erklaere_score(score: UniverseScore, *, asset_type: str = "crypto") -> str:
    """Menschenlesbare Begruendung fuer Logbuch, Telegram und Web-UI."""
    gewichte = GEWICHTE_KRYPTO if asset_type == "crypto" else GEWICHTE_AKTIEN
    beitraege = sorted(
        ((name, score.teile.get(name, 0.0) * gewicht) for name, gewicht in gewichte.items()),
        key=lambda x: x[1], reverse=True,
    )
    stark = ", ".join(f"{n} {w:.2f}" for n, w in beitraege[:2])
    schwach = ", ".join(f"{n} {w:.2f}" for n, w in beitraege[-2:])
    return f"Score {score.gesamt:.3f} ({score.stufe}); stark: {stark}; schwach: {schwach}"


__all__ = [
    "cheap_score", "quality_score", "erklaere_score",
    "log_normiert", "spread_guete", "volatilitaets_guete", "datenguete", "aktivitaets_guete",
    "GEWICHTE_KRYPTO", "GEWICHTE_AKTIEN", "REFERENZ_KRYPTO", "REFERENZ_AKTIEN",
]
