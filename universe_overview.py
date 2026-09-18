"""Eine einzige Quelle fuer die Universums-Kennzahlen (neu in v8.1.3).

WARUM DIESES MODUL EXISTIERT
============================
Die Dashboard-Karte "UNIVERSUM" in der Tkinter-GUI hat bis v8.1.2 den
STATISCHEN Katalog gezaehlt (``config.STOCK_SYMBOLS`` /
``config.CRYPTO_SYMBOLS``). Das war aus zwei Gruenden falsch:

1. Der Katalog ist nur die Vorauswahl. Was der Bot wirklich beobachtet,
   steht im Universums-Zustand -- vier Ebenen (Katalog, Eligible Pool,
   aktives Universum, Fokus).
2. ``CRYPTO_SYMBOLS`` ist in v8 leer, weil die Coins von OKX kommen.
   Die Karte zeigte deshalb strukturell immer "0 Krypto" -- auch dann,
   wenn OKX gerade 40 Coins beobachtet hat.

Die WebUI liest den Universums-Zustand bereits richtig. Damit GUI und
WebUI nicht auseinanderlaufen, holen sich ab v8.1.3 BEIDE ihre Zahlen
hier. Wer die Zaehlweise aendern will, aendert genau eine Stelle.

BEGRIFFE (identisch zur Spezifikation v8.1.3)
=============================================
    katalog     Was der Broker grundsaetzlich anbietet (Vorauswahl).
    kern        Fester Kern -- immer beobachtet, faellt nie heraus.
    dynamisch   Frei wechselnder Teil, begrenzt durch das Dynamik-Limit.
    aktiv       Kern + dynamisch im Zustand AKTIV = wird wirklich beobachtet.
    beobachtung Bewaehrung: aufgenommen, aber noch nicht aktiv.
    fokus       Die wenigen Werte, fuer die teure Analysen laufen duerfen.

"Im Universum" heisst ausdruecklich nur: wird beobachtet. Nicht: wird
gekauft. Diese Zahlen sagen also nichts ueber offene Positionen aus.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Anzeigename und Assetklasse je Broker -- an einer Stelle, damit GUI und
# WebUI dieselbe Sprache sprechen.
BROKER_ANZEIGE = {
    "etoro": ("eToro Aktien", "stock"),
    "okx": ("OKX Krypto", "crypto"),
}


def _cfg():
    import config
    return config


def _katalog_groesse(cfg, broker: str) -> int:
    """Wie gross ist die Vorauswahl des Brokers?

    Fuer Aktien ist das die Katalogvorauswahl (v8.1.3: rund 250 Werte).
    Fuer Krypto gibt es keine statische Liste -- OKX liefert den Katalog
    zur Laufzeit. Dann ist das Kandidatenlimit die ehrlichste Angabe.
    """
    if broker == "etoro":
        werte = getattr(cfg, "STOCK_SYMBOLS", []) or []
        return max(int(getattr(cfg, "STOCK_UNIVERSE_PRESELECTION", 0) or 0), len(werte))
    return int(getattr(cfg, "CRYPTO_UNIVERSE_PRESELECTION", 0) or 0)


def _manager():
    """Universums-Manager auf dem echten Zustand -- oder None."""
    try:
        import config
        from universe.manager import UniverseManager
        from universe.modelle import UniverseZustand
        pfad = getattr(config, "UNIVERSE_STATE_FILE", "universe_state.json")
        return UniverseManager(UniverseZustand(pfad))
    except Exception as exc:  # pragma: no cover - defensiv
        logger.debug("Universums-Zustand nicht ladbar: %s", exc)
        return None


def broker_kennzahlen(broker: str, manager: Optional[Any] = None) -> dict:
    """Kennzahlen eines Brokers aus dem echten Universums-Zustand.

    Faellt der Zustand aus, sind die dynamischen Zahlen ``None`` statt 0 --
    "nicht bekannt" darf nie wie "keine Werte" aussehen.
    """
    broker = str(broker).lower()
    anzeige, assetklasse = BROKER_ANZEIGE.get(broker, (broker.upper(), ""))
    cfg = _cfg()
    daten: dict[str, Any] = {
        "broker": broker,
        "anzeige": anzeige,
        "asset_type": assetklasse,
        "katalog": _katalog_groesse(cfg, broker),
        "kern": None,
        "dynamisch": None,
        "aktiv": None,
        "beobachtung": None,
        "fokus": None,
        "gesamt": None,
        "kern_limit": None,
        "dynamisch_limit": None,
        "aktiv_limit": None,
        "fokus_limit": None,
        "bekannt": False,
        "fehler": "",
    }

    if manager is None:
        manager = _manager()
    if manager is None:
        daten["fehler"] = "Universums-Zustand nicht ladbar"
        return daten

    try:
        from universe.manager import UniverseRegeln
        from universe.modelle import AKTIV, BEOBACHTUNG

        regeln = UniverseRegeln.fuer(broker, cfg)
        mitglieder = list(manager.zustand.fuer_broker(broker))
        kern = [m for m in mitglieder if manager.ist_kern(broker, m.symbol)]
        aktive = [m for m in mitglieder if m.zustand == AKTIV]

        daten.update({
            "gesamt": len(mitglieder),
            "kern": len(kern),
            # Alles, was nicht zum festen Kern gehoert, ist der dynamische
            # Teil -- genau der Teil, den das Dynamik-Limit begrenzt.
            "dynamisch": len(mitglieder) - len(kern),
            "aktiv": len(aktive),
            "beobachtung": len([m for m in mitglieder if m.zustand == BEOBACHTUNG]),
            "fokus": len(manager.focus_set(broker)),
            "kern_limit": int(regeln.kern_limit),
            "dynamisch_limit": int(regeln.dynamisch_limit),
            "aktiv_limit": int(regeln.aktiv_limit),
            "fokus_limit": int(regeln.focus_limit),
            "bekannt": True,
        })
        if str(broker).lower() == "etoro":
            daten.update(_underdog_kennzahlen(cfg, mitglieder, aktive))
    except Exception as exc:
        daten["fehler"] = f"{type(exc).__name__}: {exc}"
        logger.debug("Universums-Kennzahlen fuer %s nicht ermittelbar", broker, exc_info=True)
    return daten


def _underdog_kennzahlen(cfg, mitglieder, aktive) -> dict:
    """Underdogs getrennt ausweisen (v9.3).

    Bis 9.2 stand UNDERDOGS_AKTIV auf True, 25 Kandidaten lagen im Katalog --
    und keiner war handelbar, weil der feste Kern per blindem Schnitt
    gebildet wurde. Sichtbar war das nirgends. Diese Zahlen machen genau
    diesen Unterschied lesbar.
    """
    katalog = [x for x in (getattr(cfg, "STOCK_CATALOG_SYMBOLS", []) or [])
               if isinstance(x, dict) and x.get("underdog")]
    im_katalog = {str(x.get("symbol", "")).upper() for x in katalog}
    kern = {str(x.get("symbol", "")).upper()
            for x in (getattr(cfg, "STOCK_FIXED_CORE_SYMBOLS", []) or [])
            if isinstance(x, dict) and x.get("underdog")}
    aktive_symbole = {str(m.symbol).upper() for m in aktive}
    bekannte = {str(m.symbol).upper() for m in mitglieder}
    return {
        "underdogs_eingeschaltet": bool(getattr(cfg, "UNDERDOGS_AKTIV", False)),
        "underdogs_im_katalog": len(im_katalog),
        "underdogs_kernplaetze": int(getattr(cfg, "STOCK_FIXED_UNDERDOG_SLOTS", 0)),
        "underdogs_im_kern": len(kern),
        "underdogs_aktiv": len(im_katalog & aktive_symbole),
        "underdogs_blockiert": len((im_katalog & bekannte) - aktive_symbole),
    }


def kennzahlen() -> dict:
    """Kennzahlen beider Broker in einem Aufruf (ein Zustandsladevorgang)."""
    manager = _manager()
    return {name: broker_kennzahlen(name, manager) for name in ("etoro", "okx")}


def dashboard_text(daten: Optional[dict] = None) -> str:
    """Eine Zeile fuer die Dashboard-Karte "UNIVERSUM".

    Beispiel:  ``Aktien 78 aktiv (75 Kern + 3) · Krypto 41 aktiv (3 Kern + 38)``

    Ist der Zustand noch leer -- etwa vor dem ersten Universumslauf --,
    steht dort der Katalogumfang mit klarer Kennzeichnung, damit niemand
    eine Katalogzahl fuer ein aktives Universum haelt.
    """
    daten = daten or kennzahlen()
    teile = []
    for name, kurz in (("etoro", "Aktien"), ("okx", "Krypto")):
        d = daten.get(name) or {}
        if d.get("bekannt") and int(d.get("gesamt") or 0) > 0:
            aktiv = int(d.get("aktiv") or 0)
            kern = int(d.get("kern") or 0)
            dyn = int(d.get("dynamisch") or 0)
            text = f"{kurz} {aktiv} aktiv ({kern} Kern + {dyn})"
            beob = int(d.get("beobachtung") or 0)
            if beob:
                text += f" · {beob} in Bewaehrung"
        elif d.get("fehler"):
            text = f"{kurz} unbekannt"
        else:
            katalog = int(d.get("katalog") or 0)
            text = f"{kurz} noch kein Lauf (Katalog {katalog})" if katalog else f"{kurz} noch kein Lauf"
        teile.append(text)
    return " · ".join(teile)


__all__ = ["BROKER_ANZEIGE", "broker_kennzahlen", "kennzahlen", "dashboard_text"]
