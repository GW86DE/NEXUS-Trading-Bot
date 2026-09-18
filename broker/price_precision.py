"""Preisrundung passend zum Preisniveau.

WARUM DIESES MODUL EXISTIERT
============================
Bis v5.3.1 wurden Orderpreise mit round(preis, 2) gerundet. Zwei Stellen sind
die richtige Tick-Groesse fuer US-Aktien -- fuer Kryptowaehrungen sind sie es
nicht. Konkrete Auswirkung im damaligen Universum:

    SHIB  Kurs 0,0000165  ->  Stop 0,0000162  ->  round(...,2) = 0.00
          Ergebnis: Stop-Order mit Preis null, also faktisch KEIN Schutz.

    ALGO  Kurs 0,22       ->  Stop 0,2156     ->  round(...,2) = 0.22
          Ergebnis: Stop um 2 % verschoben, geplanter Risikoabstand verfehlt.

Der Fehler steckte sowohl im normalen Kaufweg als auch in der
Schutzorder-Reparatur nach einem Verbindungsabbruch -- also ausgerechnet dort,
wo ein fehlender Schutz wiederhergestellt werden soll.

ANSATZ
======
Die Zahl der Nachkommastellen richtet sich nach der Groessenordnung des
Preises. Ziel ist, den relativen Rundungsfehler klein zu halten, statt eine
feste Stellenzahl zu erzwingen.

Aktien bleiben bewusst bei zwei Stellen: dort ist 0,01 die tatsaechliche
Tick-Groesse, und mehr Stellen wuerden von der Boerse abgelehnt.
"""
from __future__ import annotations

# Schwellen von oben nach unten: (ab welchem Preis, wie viele Nachkommastellen)
_STUFEN = (
    (1.0, 2),        # ab 1,00 USD   -> 2 Stellen  (BTC, ETH, SOL, LINK ...)
    (0.01, 4),       # ab 0,01 USD   -> 4 Stellen  (ADA, DOGE, ALGO, XTZ ...)
    (0.0001, 6),     # ab 0,0001 USD -> 6 Stellen
)
_MAX_STELLEN = 8     # darunter (z.B. SHIB) -> 8 Stellen


def preis_stellen(preis: float) -> int:
    """Sinnvolle Nachkommastellen fuer einen Kryptopreis."""
    try:
        p = abs(float(preis or 0))
    except (TypeError, ValueError):
        return 2
    if p <= 0:
        return 2
    for schwelle, stellen in _STUFEN:
        if p >= schwelle:
            return stellen
    return _MAX_STELLEN


def runde_preis(preis: float, asset_type: str = "crypto") -> float:
    """
    Rundet einen Orderpreis passend zur Anlageklasse.

    Aktien/ETFs: fest 2 Stellen (Boersen-Tick).
    Krypto:      abhaengig vom Preisniveau, damit auch Werte unter einem Cent
                 einen brauchbaren Preis behalten.
    """
    try:
        p = float(preis or 0)
    except (TypeError, ValueError):
        return 0.0
    if p <= 0:
        return 0.0
    if str(asset_type or "").lower() != "crypto":
        return round(p, 2)
    return round(p, preis_stellen(p))


def preis_plausibel(gerundet: float, original: float, toleranz: float = 0.005) -> bool:
    """
    Prueft, ob die Rundung den Preis nicht unzulaessig verschoben hat.

    Dient als letzte Sicherung vor dem Absenden: Ein Stop, der durch Rundung
    um mehr als toleranz (Standard 0,5 %) verschoben wurde oder auf null
    faellt, sollte nicht als Schutzorder verwendet werden.
    """
    try:
        g = float(gerundet or 0)
        o = float(original or 0)
    except (TypeError, ValueError):
        return False
    if g <= 0 or o <= 0:
        return False
    return abs(g - o) / o <= toleranz
