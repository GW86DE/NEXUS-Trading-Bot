"""Klassifizierung von Brokerguthaben (P0-07, neu in v8.1.2).

DAS PROBLEM AUS DEM LAUFZEITPROTOKOLL
=====================================
    crypto_engine: OKX-Guthaben ohne Positionsbuch-Eintrag: BTC, XRP, USD, ETH

Diese Zeile stand alle paar Minuten im Log und hat drei voellig
verschiedene Sachverhalte in einen Topf geworfen:

    USD      ist gar kein Bestand, sondern Guthaben (Cash)
    BTC/ETH  koennen manuell gekaufte Altbestaende sein
    XRP      koennte auch der Rest einer nicht zugeordneten Bot-Order sein

Nur der letzte Fall ist gefaehrlich. Ohne Unterscheidung sieht man ihn im
Rauschen der anderen nicht -- und ein Bot, der ungeklaerte Exposure nur
protokolliert statt sie zu behandeln, hat ein Sicherheitsproblem.

DIE SECHS KLASSEN
=================
    CASH             Quote-/Cashwaehrung. Kein Positionsalarm.
    BOT_MANAGED      Steht im Positionsbuch. Normaler Stop-/Ziel-Ablauf.
    ACCOUNT_ASSET    Frei verfuegbares Spot-Konto-Asset. Kein offener Trade.
    EXTERNAL_HOLDING Historischer Kompatibilitaetsname fuer Fremdbestand.
    RESIDUAL         Passt zu einer nicht abgeschlossenen Bot-Order.
                     Neue Einstiege sperren, Reconciliation anstossen.
    UNKNOWN          Nicht erklaerbar. Fail-closed fuer diese Domaene,
                     bis der Ursprung geklaert ist.

GRUNDREGEL
==========
Der Bot verkauft NIEMALS automatisch einen Bestand, den er nicht selbst
eroeffnet hat. Fremdbestand wird sichtbar gemacht, nicht angefasst.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

logger = logging.getLogger(__name__)

CASH = "CASH"
BOT_MANAGED = "BOT_MANAGED"
EXTERNAL_HOLDING = "EXTERNAL_HOLDING"
ACCOUNT_ASSET = "ACCOUNT_ASSET"
RESIDUAL = "RESIDUAL_EXPOSURE"
UNKNOWN = "UNKNOWN_EXPOSURE"

# Klassen, die neue Einstiege in dieser Brokerdomaene sperren.
SPERRENDE_KLASSEN = frozenset({RESIDUAL, UNKNOWN})

# Waehrungen, die immer als Cash gelten -- auch wenn sie nicht die aktuell
# eingestellte Quotewaehrung sind. USD tauchte im Log als vermeintlicher
# Kryptobestand auf, weil es in OKX_ALLOWED_QUOTE_CCY fehlte.
STANDARD_CASH = ("EUR", "USDC", "USD", "USDG", "USDT")

# Unterhalb dieses Gegenwerts ist ein Restbestand Staub und kein Risiko.
STAUB_GRENZE_QUOTE = 1.0


@dataclass
class Bestand:
    """Ein klassifizierter Guthabenposten."""
    waehrung: str
    menge: float
    klasse: str
    gegenwert_quote: float = 0.0
    begruendung: str = ""
    sperrt_einstiege: bool = False
    zusatz: dict = field(default_factory=dict)

    def als_dict(self) -> dict:
        return {
            "waehrung": self.waehrung,
            "menge": self.menge,
            "klasse": self.klasse,
            "gegenwert_quote": round(self.gegenwert_quote, 2),
            "begruendung": self.begruendung,
            "sperrt_einstiege": self.sperrt_einstiege,
            **({"zusatz": self.zusatz} if self.zusatz else {}),
        }


def cash_waehrungen(cfg=None) -> set[str]:
    """Alle Waehrungen, die als Guthaben und nicht als Position gelten."""
    try:
        import config as _config
        cfg = cfg or _config
    except Exception:
        return set(STANDARD_CASH)
    erlaubt = set(STANDARD_CASH)
    erlaubt.add(str(getattr(cfg, "OKX_QUOTE_CCY", "EUR")).upper())
    for wert in getattr(cfg, "OKX_ALLOWED_QUOTE_CCY", ()) or ():
        erlaubt.add(str(wert).upper())
    return {w for w in erlaubt if w}


def klassifiziere(guthaben: dict, *, positionsbuch: Iterable = (),
                  offene_orders: Iterable = (), ledger_trades: Iterable = (),
                  preise: Optional[dict] = None,
                  schutzorders: Iterable = (),
                  cfg=None) -> dict:
    """Ordnet jeden Guthabenposten genau einer Klasse zu.

    guthaben        {'BTC': {'gesamt': 0.5, 'cash': 0.5}, 'EUR': {...}}
    positionsbuch   Objekte oder dicts mit 'symbol' und 'menge'
    offene_orders   nicht abgeschlossene Bot-Orders mit 'symbol'
    ledger_trades   offene Ledger-Trades; ohne Positionsbuch sind sie Residuen
    preise          {'BTC': 60000.0} zur Bewertung in Quotewaehrung
    schutzorders    beim Broker liegende Schutzorders (9.5.5)

    KORREKTUR 9.5.5 -- warum ``schutzorders`` dazugekommen ist.

    Am 02.09.2026 lagen 1,043377 ETH (rund 2530 EUR) im OKX-Konto, gesichert
    durch eine LEBENDE OCO-Order mit Stop 1894,9 und Take-Profit 2205,1. Diese
    Funktion kannte nur Guthaben, Positionsbuch, offene Orders und Ledger --
    Schutzorders bekam sie nie zu sehen. Der Bestand wurde deshalb als "frei
    verfuegbares Konto-Asset; kein offener Trade" eingestuft, und
    ``einstiege_gesperrt`` blieb aus.

    Ein Bestand mit scharfem Stop und Take-Profit ist aber alles andere als
    frei verfuegbar: irgendjemand verwaltet ihn aktiv. Solange unklar ist wer,
    ist das ungeklaerte Exposure und gehoert benannt.
    """
    cash = cash_waehrungen(cfg)
    preise = {str(k).upper(): float(v or 0.0) for k, v in (preise or {}).items()}
    # Basiswaehrungen, fuer die beim Broker eine Schutzorder liegt.
    geschuetzt: dict[str, str] = {}
    for zeile in schutzorders or ():
        waehrung = str((zeile.get("symbol") if isinstance(zeile, dict)
                        else getattr(zeile, "symbol", "")) or "").upper()
        if not waehrung:
            continue
        kennung = str((zeile.get("algo_id") if isinstance(zeile, dict)
                       else getattr(zeile, "algo_id", "")) or "")
        instrument = str((zeile.get("instrument") if isinstance(zeile, dict)
                          else getattr(zeile, "instrument", "")) or "")
        geschuetzt[waehrung] = (f"Schutzorder {kennung} auf {instrument}"
                                if kennung else f"Schutzorder auf {instrument}")

    gebucht: dict[str, float] = {}
    freigegeben: set[str] = set()
    for eintrag in positionsbuch or ():
        symbol = str(getattr(eintrag, "symbol", None) or
                     (eintrag.get("symbol") if isinstance(eintrag, dict) else "")).upper()
        menge = float(getattr(eintrag, "menge", None) or
                      (eintrag.get("menge") if isinstance(eintrag, dict) else 0.0) or 0.0)
        herkunft = str(getattr(eintrag, "herkunft", None) or
                       (eintrag.get("herkunft") if isinstance(eintrag, dict) else "BOT") or "BOT").upper()
        verwaltung = str(getattr(eintrag, "verwaltung", None) or
                         (eintrag.get("verwaltung") if isinstance(eintrag, dict) else "AUTO") or "AUTO").upper()
        if isinstance(eintrag, dict):
            if "ownership_verified" in eintrag:
                aktiv = bool(eintrag.get("ownership_verified")) and bool(
                    eintrag.get("order_id")) and bool(eintrag.get("fill_ids"))
            else:
                # Lesekompatibilitaet fuer alte Diagnose-/Exportobjekte. Der
                # produktive Positionspfad uebergibt KryptoPosition-Objekte
                # und verlangt dort immer die neue Beweiskette.
                aktiv = herkunft == "BOT" and verwaltung == "AUTO"
        else:
            aktiv = bool(getattr(eintrag, "darf_automatisch_verkaufen", False))
        aktiv = aktiv and herkunft == "BOT" and verwaltung == "AUTO"
        if symbol and aktiv:
            gebucht[symbol] = gebucht.get(symbol, 0.0) + menge
        elif symbol:
            # Beobachtete/Legacy-Eintraege sind Herkunftshinweise, aber keine
            # aktive Botposition. Der OKX-Saldo bleibt ein Konto-Asset.
            freigegeben.add(symbol)

    offen = set()
    for eintrag in offene_orders or ():
        symbol = str(getattr(eintrag, "symbol", None) or
                     (eintrag.get("symbol") if isinstance(eintrag, dict) else "")).upper()
        if symbol:
            offen.add(symbol)

    ledger: dict[str, float] = {}
    for eintrag in ledger_trades or ():
        reconciliation = str(
            getattr(eintrag, "reconciliation_status", None) or
            (eintrag.get("reconciliation_status") if isinstance(eintrag, dict) else "")
            or "").upper()
        if reconciliation in {"EXTERNAL_OBSERVE", "CLOSED", "ACCOUNT_MISMATCH"}:
            continue
        symbol = str(getattr(eintrag, "symbol", None) or
                     (eintrag.get("symbol") if isinstance(eintrag, dict) else "")).upper()
        menge = float(getattr(eintrag, "menge", None) or
                      (eintrag.get("menge") if isinstance(eintrag, dict) else 0.0) or 0.0)
        if symbol and menge > 0:
            ledger[symbol] = ledger.get(symbol, 0.0) + menge

    bestaende: list[Bestand] = []
    for waehrung, werte in (guthaben or {}).items():
        waehrung = str(waehrung).upper()
        if isinstance(werte, dict):
            menge = float(werte.get("gesamt", werte.get("cash", 0.0)) or 0.0)
        else:
            menge = float(werte or 0.0)
        if menge <= 0:
            continue

        if waehrung in cash:
            bestaende.append(Bestand(waehrung, menge, CASH,
                                     gegenwert_quote=menge,
                                     begruendung="Guthaben in Cash-/Quotewaehrung"))
            continue

        gegenwert = menge * preise.get(waehrung, 0.0)
        gebuchte_menge = gebucht.get(waehrung, 0.0)

        if gebuchte_menge > 0:
            rest = menge - gebuchte_menge
            if abs(rest) <= max(gebuchte_menge * 0.02, 1e-12):
                bestaende.append(Bestand(waehrung, menge, BOT_MANAGED, gegenwert,
                                         "Im Positionsbuch gefuehrt"))
                continue
            if rest > 0:
                # Spot ist fungibel: Nur die exakt bewiesene Buchmenge gehoert
                # dem Bot. Ein darueber liegender Kontosaldo ist ein getrenntes
                # Konto-Asset und kein Fehler. Am 28.08.2026 blockierte genau
                # dieser falsche Schluss 0,996348 bereits vorhandene ETH.
                bestaende.append(Bestand(waehrung, gebuchte_menge, BOT_MANAGED,
                                         gebuchte_menge * preise.get(waehrung, 0.0),
                                         "Im Positionsbuch gefuehrt"))
                kurs = float(preise.get(waehrung, 0.0) or 0.0)
                ueberhang_wert = rest * kurs
                bestaende.append(Bestand(
                    waehrung, rest, ACCOUNT_ASSET, ueberhang_wert,
                    "Frei verfuegbares OKX-Konto-Asset ausserhalb der bewiesenen Botmenge",
                    zusatz={"bot_menge": round(gebuchte_menge, 12)}))
                continue
            # Weniger im Konto als im Buch -- der Rest wurde extern verkauft.
            bestaende.append(Bestand(waehrung, menge, RESIDUAL, gegenwert,
                                     "Weniger Bestand als im Positionsbuch gefuehrt",
                                     sperrt_einstiege=True))
            continue

        if waehrung in freigegeben:
            bestaende.append(Bestand(
                waehrung, menge, ACCOUNT_ASSET, gegenwert,
                "Frei verfuegbares OKX-Konto-Asset; kein aktiver Bottrade",
                zusatz={"positionsbuch_hinweis": "BEOBACHTEN/LEGACY"}))
            continue

        if waehrung in offen:
            bestaende.append(Bestand(waehrung, menge, RESIDUAL, gegenwert,
                                     "Passt zu einer noch offenen Bot-Order",
                                     sperrt_einstiege=True))
            continue

        # Ein Rundungsrest bleibt ein Konto-Asset, auch wenn eine alte offene
        # Ledgerzeile dieselbe Waehrung nennt. Sonst blockierten 0,01 EUR DOGE
        # das gesamte Kryptosystem.
        if gegenwert and gegenwert <= STAUB_GRENZE_QUOTE:
            bestaende.append(Bestand(waehrung, menge, ACCOUNT_ASSET, gegenwert,
                                     "Frei verfuegbares Konto-Asset (Staubrest)"))
            continue

        if ledger.get(waehrung, 0.0) > 0:
            # 10.3.0: Spot ist fungibel -- nur die offene Ledger-Restmenge ist
            # an den Trade gebunden. Am 17.09.2026 ordnete diese Stelle den
            # kompletten OKX-Demo-Startbestand (1 BTC / 10 ETH) zwei offenen
            # Staub-Restzeilen von 2,79e-9 BTC bzw. 1,02e-7 ETH zu und sperrte
            # damit alle Kryptokaeufe. Ein Saldo ueber der Ledger-Restmenge ist
            # ein getrenntes Konto-Asset (gleiches Prinzip wie beim
            # Positionsbuch-Ueberhang oben); ein gebundener Rest unterhalb der
            # Staubgrenze ist Staub. Ohne Kursbeleg gibt es keine
            # Staub-Entwarnung.
            kurs = float(preise.get(waehrung, 0.0) or 0.0)
            ledger_menge = ledger[waehrung]
            gebunden = min(menge, ledger_menge)
            ueberhang = menge - gebunden
            if ueberhang > 0:
                bestaende.append(Bestand(
                    waehrung, ueberhang, ACCOUNT_ASSET, ueberhang * kurs,
                    "Frei verfuegbares OKX-Konto-Asset ausserhalb der offenen Ledger-Restmenge",
                    zusatz={"ledger_menge": round(ledger_menge, 12)}))
            gebunden_wert = gebunden * kurs
            if kurs > 0 and gebunden_wert <= STAUB_GRENZE_QUOTE:
                bestaende.append(Bestand(
                    waehrung, gebunden, ACCOUNT_ASSET, gebunden_wert,
                    "Frei verfuegbares Konto-Asset (Staubrest)",
                    zusatz={"ledger_menge": round(ledger_menge, 12)}))
            else:
                bestaende.append(Bestand(
                    waehrung, gebunden, RESIDUAL, gebunden_wert,
                    "Offener Trade im Ledger, aber kein Eintrag im Positionsbuch",
                    sperrt_einstiege=True,
                    zusatz={"ledger_menge": round(ledger_menge, 12)}))
            continue

        # OKX Spot liefert im Account-Kanal Salden/Assets, keine offenen
        # Positionen. Ohne exakten aktiven Bot-Einstieg ist BTC/ETH/SOL/XRP
        # daher verfuegbares Konto-Asset und niemals ein automatisch zu
        # verwaltender Trade.
        if waehrung in geschuetzt:
            # 9.5.5: Ein Bestand mit lebender Schutzorder ist kein freies
            # Konto-Asset. Er wird aktiv verwaltet -- nur nicht von NEXUS.
            bestaende.append(Bestand(
                waehrung, menge, UNKNOWN, gegenwert,
                f"Bestand mit lebender {geschuetzt[waehrung]}, aber ohne "
                "zugehoerigen Trade im Buch -- Herkunft ungeklaert"))
            continue
        bestaende.append(Bestand(waehrung, menge, ACCOUNT_ASSET, gegenwert,
                                 "Frei verfuegbares OKX-Konto-Asset; kein offener Trade"))

    nach_klasse: dict[str, list] = {}
    for b in bestaende:
        nach_klasse.setdefault(b.klasse, []).append(b.als_dict())

    sperren = [b for b in bestaende if b.sperrt_einstiege]
    return {
        "zeit": datetime.now(timezone.utc).isoformat(),
        "bestaende": [b.als_dict() for b in bestaende],
        "nach_klasse": nach_klasse,
        "cash_waehrungen": sorted(cash),
        "einstiege_gesperrt": bool(sperren),
        "sperrgruende": [f"{b.waehrung}: {b.begruendung}" for b in sperren],
        # 9.5.8: Die betroffenen Waehrungen einzeln, damit der Aufrufer nur
        # DIESE Werte sperren kann statt den gesamten Kryptoscan. Bis 9.5.7
        # legte ein einziger ungeklaerter Posten alle Coins still -- die
        # Klassifizierung wusste genau, welcher es war, gab es aber nur als
        # Fliesstext heraus.
        "gesperrte_waehrungen": sorted({str(b.waehrung).upper() for b in sperren}),
    }


def kurzfassung(ergebnis: dict) -> str:
    """Eine Zeile fuer das Protokoll -- statt einer nichtssagenden Liste."""
    zaehler: dict[str, int] = {}
    for b in ergebnis.get("bestaende", []):
        zaehler[b["klasse"]] = zaehler.get(b["klasse"], 0) + 1
    if not zaehler:
        return "Kein Guthaben vorhanden"
    teile = [f"{anzahl}x {klasse}" for klasse, anzahl in sorted(zaehler.items())]
    text = ", ".join(teile)
    if ergebnis.get("einstiege_gesperrt"):
        text += " | NEUE EINSTIEGE GESPERRT: " + "; ".join(ergebnis.get("sperrgruende", []))
    return text


__all__ = [
    "klassifiziere", "kurzfassung", "cash_waehrungen", "Bestand",
    "CASH", "BOT_MANAGED", "ACCOUNT_ASSET", "EXTERNAL_HOLDING", "RESIDUAL", "UNKNOWN",
    "SPERRENDE_KLASSEN", "STANDARD_CASH", "STAUB_GRENZE_QUOTE",
]
