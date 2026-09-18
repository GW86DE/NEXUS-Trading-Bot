"""Das Tagesergebnis -- aus den Buchungen, nicht aus einem Summenzaehler.

WARUM DIESES MODUL EXISTIERT
============================
Am 25.08.2026 meldete Telegram "Heute realisiert: -226,60 USD". Tatsaechlich
war an diesem Tag genau ein Trade geschlossen worden:

    DVLT    realisiert   -44,40 USD
    FLR.US  noch offen  -121,26 USD Buchverlust
    eToro-Gebuehren        -3,00 USD

Keine Kombination dieser Zahlen ergibt -226,60. Der gemeldete Wert kam aus
``risk_state.realized_pnl_today`` -- einem fortgeschriebenen Summenzaehler,
der bei jedem Verkauf erhoeht wird. Ein solcher Zaehler hat keine Belege: Er
kann durch einen doppelt verbuchten Fill, einen Abbruch zwischen Buchung und
Speicherung oder einen alten Zustand aus einer frueheren Version danebenliegen,
und niemand kann hinterher sagen, welcher Betrag zu welchem Trade gehoerte.

Das Handelsbuch (``trade_ledger``) kann das. Dort steht jeder Ausstieg
einzeln mit Symbol, Zeit, Brutto, Gebuehren und Netto -- und der DVLT-Wert
darin war korrekt.

DREI ZAHLEN, NICHT EINE
=======================
"Tagesergebnis" ist keine einzelne Zahl, und das Zusammenziehen war Teil des
Problems:

    Realisiert heute      geschlossene Trades, netto (Gebuehren schon ab)
    Offen unrealisiert    Buchwert laufender Positionen -- noch kein Geld
    Gebuehren heute       im realisierten Wert bereits enthalten, nur Ausweis

    Tagesergebnis  =  realisiert netto  +  offen unrealisiert

Die Gebuehren werden BEWUSST NICHT noch einmal abgezogen. Sie stecken schon
im Nettowert; ein zweiter Abzug waere der naechste falsche Betrag.

WAS BEI LUECKEN PASSIERT
========================
Nichts wird erfunden. Ein Ausstieg ohne bekannten Einstand geht nicht als
0,00 in die Summe, sondern wird als "unvollstaendig" gezaehlt und benannt.
Weicht der alte Summenzaehler vom Handelsbuch ab, wird die ABWEICHUNG
GEMELDET statt eine der beiden Zahlen auszuwaehlen -- die Auswahl waere
geraten, und geraten war der Fehler.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from typing import Any, Optional

import config

logger = logging.getLogger(__name__)

# Ab welcher Abweichung zwischen Summenzaehler und Handelsbuch gewarnt wird.
# 1 Cent Rundung ist normal, ein Euro nicht.
ABWEICHUNGSSCHWELLE = 0.50


@dataclass
class Tagesergebnis:
    """Was heute passiert ist -- mit Belegen."""
    broker: str = ""
    waehrung: str = "USD"
    realisiert_netto: float = 0.0
    realisiert_brutto: float = 0.0
    gebuehren: float = 0.0
    offen_unrealisiert: float = 0.0
    geschlossene_trades: int = 0
    offene_positionen: int = 0
    unvollstaendige_trades: int = 0
    buchungen: list[dict] = field(default_factory=list)
    zaehlerwert: Optional[float] = None       # risk_state.realized_pnl_today
    abweichung: Optional[float] = None
    hinweise: list[str] = field(default_factory=list)

    @property
    def gesamt(self) -> float:
        """Realisiert netto plus offener Buchwert. Gebuehren stecken drin."""
        return self.realisiert_netto + self.offen_unrealisiert

    @property
    def vollstaendig(self) -> bool:
        return self.unvollstaendige_trades == 0

    @property
    def stimmig(self) -> bool:
        """Deckt sich das Handelsbuch mit dem Summenzaehler?"""
        if self.abweichung is None:
            return True
        return abs(self.abweichung) < ABWEICHUNGSSCHWELLE

    def als_dict(self) -> dict:
        return {
            "broker": self.broker, "waehrung": self.waehrung,
            "realisiert_netto": round(self.realisiert_netto, 2),
            "realisiert_brutto": round(self.realisiert_brutto, 2),
            "gebuehren": round(self.gebuehren, 2),
            "offen_unrealisiert": round(self.offen_unrealisiert, 2),
            "gesamt": round(self.gesamt, 2),
            "geschlossene_trades": self.geschlossene_trades,
            "offene_positionen": self.offene_positionen,
            "unvollstaendige_trades": self.unvollstaendige_trades,
            "vollstaendig": self.vollstaendig,
            "stimmig": self.stimmig,
            "zaehlerwert": (None if self.zaehlerwert is None
                            else round(self.zaehlerwert, 2)),
            "abweichung": (None if self.abweichung is None
                           else round(self.abweichung, 2)),
            "buchungen": self.buchungen,
            "hinweise": list(self.hinweise),
        }

    # -- Darstellung --------------------------------------------------------
    def zeilen(self, *, mit_buchungen: bool = True) -> list[str]:
        """Der Block fuer Telegram und Tagesbericht."""
        w = self.waehrung
        aus = [f"Realisiert heute: {_geld(self.realisiert_netto)} {w}"
               f" ({self.geschlossene_trades} Trade"
               f"{'s' if self.geschlossene_trades != 1 else ''})"]
        if abs(self.gebuehren) > 0.004:
            aus.append(f"  davon Gebuehren: {_geld(-abs(self.gebuehren))} {w}"
                       " (bereits abgezogen)")
        aus.append(f"Offen unrealisiert: {_geld(self.offen_unrealisiert)} {w}"
                   f" ({self.offene_positionen} Position"
                   f"{'en' if self.offene_positionen != 1 else ''})")
        aus.append(f"Tagesergebnis gesamt: {_geld(self.gesamt)} {w}")

        if mit_buchungen and self.buchungen:
            aus.append("Buchungen:")
            for b in self.buchungen[:12]:
                zeit = str(b.get("zeit") or "")[11:16]
                netto = b.get("netto")
                betrag = _geld(netto) if netto is not None else "ohne Ergebnis"
                aus.append(f"  {zeit} {b.get('symbol', '?')} {betrag}"
                           + (f" · {b.get('grund')}" if b.get("grund") else ""))
            if len(self.buchungen) > 12:
                aus.append(f"  … und {len(self.buchungen) - 12} weitere")

        for hinweis in self.hinweise:
            aus.append(f"⚠️ {hinweis}")
        return aus

    def text(self, *, mit_buchungen: bool = True) -> str:
        return "\n".join(self.zeilen(mit_buchungen=mit_buchungen))


def _geld(wert) -> str:
    try:
        return f"{float(wert):+,.2f}"
    except (TypeError, ValueError):
        return "unbekannt"


def _zahl(wert) -> Optional[float]:
    if wert is None:
        return None
    try:
        z = float(wert)
    except (TypeError, ValueError):
        return None
    return None if z != z else z          # NaN ist keine Zahl


def _tagesbeginn_utc(jetzt: Optional[datetime] = None) -> datetime:
    """UTC-Zeitpunkt der lokalen Mitternacht des Handelstags."""
    n = jetzt or datetime.now(timezone.utc)
    if n.tzinfo is None:
        n = n.replace(tzinfo=timezone.utc)
    try:
        zone = ZoneInfo(str(getattr(config, "LOCAL_TIMEZONE", "Europe/Berlin")))
    except Exception:
        zone = timezone.utc
    local = n.astimezone(zone)
    return local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)


def _lokale_zeit(value) -> str:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        zone = ZoneInfo(str(getattr(config, "LOCAL_TIMEZONE", "Europe/Berlin")))
        return parsed.astimezone(zone).isoformat()
    except Exception:
        return str(value or "")


def _geschlossene_trades_heute(broker: str, seit: datetime) -> list[dict]:
    """Die heutigen Ausstiege aus dem Handelsbuch."""
    from decision_analytics import _LOCK, _connect
    import trade_ledger

    trade_ledger.init_ledger()
    # 9.5.3: Zusammengefuehrte Altzeilen (link_status SUPERSEDED) bleiben als
    # Auditspur erhalten, duerfen aber nie ins Tagesergebnis. Sonst zaehlt der
    # ADBE-Verlust von -344,76 USD weiterhin doppelt -- und die
    # Tagesverlustgrenze rechnet mit einer falschen Zahl.
    sql = ("SELECT symbol, ausgestiegen_am, brutto_pnl, gebuehren, netto_pnl, "
           "exit_grund, paper FROM trades "
           "WHERE ausgestiegen_am IS NOT NULL AND ausgestiegen_am >= ? "
           "AND link_status <> 'SUPERSEDED'")
    werte: list[Any] = [seit.isoformat()]
    if broker:
        sql += " AND broker=?"
        werte.append(str(broker).lower())
    sql += " ORDER BY ausgestiegen_am ASC"
    with _LOCK, _connect() as con:
        return [dict(r) for r in con.execute(sql, tuple(werte)).fetchall()]


def tagesergebnis(*, broker: str = "", waehrung: str = "USD",
                  offene_positionen=None, zaehlerwert=None,
                  jetzt: Optional[datetime] = None) -> Tagesergebnis:
    """Das Tagesergebnis aus den einzelnen Buchungen zusammensetzen.

    ``offene_positionen`` ist eine Liste von Objekten oder Dicts mit einem
    unrealisierten Ergebnis; erkannt werden die ueblichen Feldnamen. Fehlt
    die Angabe, bleibt der offene Teil 0 und wird als unbekannt vermerkt.

    ``zaehlerwert`` ist ``risk_state.realized_pnl_today``. Er wird NICHT
    verwendet, um die Zahl zu bilden -- nur, um Abweichungen zu melden.
    """
    ergebnis = Tagesergebnis(broker=str(broker or "").lower(),
                             waehrung=str(waehrung or "USD"))
    seit = _tagesbeginn_utc(jetzt)

    try:
        zeilen = _geschlossene_trades_heute(ergebnis.broker, seit)
    except Exception as exc:
        logger.warning("Handelsbuch nicht lesbar: %s", exc)
        ergebnis.hinweise.append(
            "Handelsbuch nicht lesbar -- das Tagesergebnis kann heute nicht "
            "belegt werden. Bitte den Wert im Broker gegenpruefen.")
        zeilen = []

    for zeile in zeilen:
        netto = _zahl(zeile.get("netto_pnl"))
        brutto = _zahl(zeile.get("brutto_pnl"))
        gebuehr = _zahl(zeile.get("gebuehren"))
        ergebnis.geschlossene_trades += 1
        ergebnis.buchungen.append({
            "symbol": zeile.get("symbol"),
            "zeit": _lokale_zeit(zeile.get("ausgestiegen_am")),
            "brutto": None if brutto is None else round(brutto, 2),
            "gebuehren": None if gebuehr is None else round(gebuehr, 2),
            "netto": None if netto is None else round(netto, 2),
            "grund": zeile.get("exit_grund"),
            "paper": bool(zeile.get("paper")),
        })
        if gebuehr is not None:
            ergebnis.gebuehren += abs(gebuehr)
        if netto is None:
            # NIE als 0,00 verbuchen -- eine Null sieht aus wie ein
            # Nullergebnis und verfaelscht die Summe.
            ergebnis.unvollstaendige_trades += 1
            continue
        ergebnis.realisiert_netto += netto
        if brutto is not None:
            ergebnis.realisiert_brutto += brutto

    if ergebnis.unvollstaendige_trades:
        ergebnis.hinweise.append(
            f"Tages-P&L unvollstaendig: {ergebnis.unvollstaendige_trades} von "
            f"{ergebnis.geschlossene_trades} Trades ohne bekannten Einstand. "
            "Diese Trades fehlen in der Summe -- sie werden nicht als 0,00 "
            "mitgerechnet.")

    # -- Offener Teil -------------------------------------------------------
    if offene_positionen is None:
        ergebnis.hinweise.append(
            "Offene Positionen nicht abrufbar; 'Offen unrealisiert' fehlt.")
    else:
        offen, ohne_wert = _unrealisiert(offene_positionen)
        ergebnis.offen_unrealisiert = offen
        ergebnis.offene_positionen = len(list(offene_positionen))
        if ohne_wert:
            ergebnis.hinweise.append(
                f"{ohne_wert} offene Position(en) ohne aktuellen Kurs -- ihr "
                "Buchwert fehlt im Tagesergebnis.")

    # -- Abgleich mit dem alten Summenzaehler -------------------------------
    zaehler = _zahl(zaehlerwert)
    if zaehler is not None:
        ergebnis.zaehlerwert = zaehler
        ergebnis.abweichung = zaehler - ergebnis.realisiert_netto
        if not ergebnis.stimmig:
            ergebnis.hinweise.append(
                f"Der laufende Tageszaehler meldet {_geld(zaehler)} "
                f"{ergebnis.waehrung}, das Handelsbuch belegt "
                f"{_geld(ergebnis.realisiert_netto)} {ergebnis.waehrung} "
                f"(Abweichung {_geld(ergebnis.abweichung)}). Angezeigt wird der "
                "belegte Wert aus dem Handelsbuch.")
    return ergebnis


def _unrealisiert(positionen) -> tuple[float, int]:
    """Buchwert der offenen Positionen. Zweiter Wert: wie viele fehlten."""
    summe = 0.0
    fehlend = 0
    for position in positionen or []:
        wert = None
        for feld in ("unrealized_pnl", "unrealisiert", "buchwert_pnl",
                     "unrealized_profit", "pnl_unrealized", "open_pnl"):
            roh = (position.get(feld) if isinstance(position, dict)
                   else getattr(position, feld, None))
            wert = _zahl(roh)
            if wert is not None:
                break
        if wert is None:
            fehlend += 1
            continue
        summe += wert
    return summe, fehlend


__all__ = ["Tagesergebnis", "tagesergebnis", "ABWEICHUNGSSCHWELLE"]
