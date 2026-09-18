"""OKX-Abschnitt fuer Telegram-Status und Dashboard (neu in v8.1.4).

WARUM DIESES MODUL EXISTIERT
============================
Der Telegram-Status baute sich bis v8.1.3 ausschliesslich aus
``runtime_status.json`` -- der Datei des eToro-Prozesses -- und aus
``config.STOCK_SYMBOLS``. ``runtime_status_okx.json`` wurde nirgends gelesen.

Folge: Im Status stand "Broker: etoro · PAPER", ein eToro-Kontowert und zwei
eToro-Positionen. Ueber OKX stand dort **kein Wort** -- kein Guthaben, keine
Kryptoposition, kein Modus, keine Handelsbereitschaft. Wer per Telegram
nachsah, konnte nicht erkennen, dass die Kryptoseite ueberhaupt handelt.

Dieses Modul liest die OKX-Datei und macht daraus einen Textblock. Es fragt
nie den Broker -- alles kommt aus dem Heartbeat, den die Kryptomaschine
ohnehin schreibt.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

DATEI = "runtime_status_okx.json"

# Aelter als das: der Heartbeat gilt als veraltet.
MAX_ALTER_SEKUNDEN = 180.0


def _wurzel() -> Path:
    return Path(os.getenv("TRADINGBOT_TEST_STATE_DIR", "").strip()
                or Path(__file__).resolve().parent)


def lies() -> dict:
    """Den OKX-Heartbeat lesen. Nie eine Ausnahme nach aussen."""
    try:
        datei = _wurzel() / DATEI
        if not datei.exists():
            return {}
        return json.loads(datei.read_text(encoding="utf-8")) or {}
    except Exception:
        logger.debug("OKX-Status nicht lesbar", exc_info=True)
        return {}


def alter_sekunden(daten: dict) -> float | None:
    roh = str(daten.get("last_heartbeat") or daten.get("zeit") or "").replace("Z", "+00:00")
    if not roh:
        return None
    try:
        stempel = datetime.fromisoformat(roh)
    except ValueError:
        return None
    if stempel.tzinfo is None:
        stempel = stempel.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - stempel).total_seconds()


def _zahl(wert, stellen: int = 2) -> str:
    try:
        return f"{float(wert):,.{stellen}f}".replace(",", " ")
    except (TypeError, ValueError):
        return "?"


def gemessener_taker_satz() -> float:
    """Der zuletzt bei OKX gemessene Taker-Satz als Bruchteil (0.001 = 0,1 %).

    Der Handelskern misst ihn und schreibt ihn in runtime_status_okx.json.
    Damit haben Dashboard, Telegram und die Trades-Seite EINE Quelle -- vorher
    rechnete die Trades-Seite mit der Annahme aus der config und zeichnete
    damit ein ROI-Ziel ueber dem Kurs, bei dem der Bot tatsaechlich verkauft.
    """
    try:
        wert = float(lies().get("gebuehrensatz_pct") or 0.0)
    except (TypeError, ValueError):
        return 0.0
    return wert / 100.0 if wert > 0 else 0.0


def guthaben_zeile(daten: dict, grenze: int = 6) -> str:
    """Guthaben je Waehrung, absteigend nach Groesse."""
    guthaben = daten.get("guthaben") or {}
    if not guthaben:
        return ""
    eintraege = sorted(guthaben.items(), key=lambda kv: -float(kv[1].get("gesamt", 0) or 0))
    teile = []
    for waehrung, werte in eintraege[:grenze]:
        gesamt = float(werte.get("gesamt", 0) or 0)
        stellen = 2 if gesamt >= 100 else 6
        teile.append(f"{_zahl(gesamt, stellen)} {waehrung}")
    if len(eintraege) > grenze:
        teile.append(f"+{len(eintraege) - grenze} weitere")
    return " · ".join(teile)


def positionszeilen(daten: dict) -> list[str]:
    """Eindeutig belegte Botpositionen -- automatisch UND manuell verwaltet.

    v9.1: Der Filter verlangte AUTO. Eine manuell verwaltete Position war
    damit im Telegram-Status komplett unsichtbar -- inklusive ihrer Warnung
    "ohne Broker-Schutz". Und weil jede TP/SL-Aenderung ueber die WebUI die
    Position dauerhaft auf MANUELL setzt, war das kein Randfall, sondern der
    Normalfall nach jedem Eingriff. Echtes Geld darf nicht aus einer
    Statusuebersicht verschwinden, nur weil jemand den Stop verschoben hat.
    """
    zeilen = []
    for position in daten.get("positionen") or []:
        fills = [str(x) for x in (position.get("fill_ids") or [])
                 if str(x).strip() and not str(x).startswith("okx-entry:")]
        verwaltung = str(position.get("verwaltung", "")).upper()
        if not (position.get("ownership_verified")
                and verwaltung in ("AUTO", "MANUELL")
                and str(position.get("order_id") or "").strip()
                and str(position.get("client_order_id") or position.get("referenz") or "").strip()
                and fills):
            continue
        symbol = str(position.get("symbol", "?")).upper()
        menge = float(position.get("menge", 0) or 0)
        einstieg = float(position.get("einstieg", 0) or 0)
        stop = float(position.get("stop", 0) or 0)
        zeile = f"• {symbol} · {menge:g} @ {einstieg:g}"
        if stop:
            zeile += f" · Stop {stop:g}"
        if not position.get("broker_schutz", True):
            zeile += " · ⚠️ ohne Broker-Schutz"
        if verwaltung != "AUTO":
            zeile += " · MANUELL verwaltet (kein Freqtrade-ROI)"
        zeilen.append(zeile)
    return zeilen


def konto_asset_zeile(daten: dict, grenze: int = 6) -> str:
    """Freie Assets aus dem Brokerkonto; ausdruecklich keine Bottrades."""
    rows = ((daten.get("exposure") or {}).get("nach_klasse") or {}).get(
        "ACCOUNT_ASSET") or []
    teile = []
    for row in rows:
        menge = float(row.get("menge", 0) or 0)
        if menge <= 0:
            continue
        stellen = 2 if menge >= 100 else 8
        teile.append(f"{_zahl(menge, stellen)} {str(row.get('waehrung') or '?').upper()}")
        if len(teile) >= grenze:
            break
    return " · ".join(teile)


def block(daten: dict | None = None) -> list[str]:
    """Der OKX-Abschnitt als Liste von Zeilen. Leer, wenn nichts bekannt ist."""
    daten = lies() if daten is None else daten
    if not daten:
        return ["", "KRYPTO · OKX", "Kein Heartbeat gefunden — laeuft die Kryptoseite?"]

    alter = alter_sekunden(daten)
    veraltet = alter is not None and alter > MAX_ALTER_SEKUNDEN
    verbunden = bool(daten.get("online") or daten.get("verbunden"))
    modus = str(daten.get("modus", "?")).upper()

    zustand = "🟢 ONLINE" if verbunden and not veraltet else (
        "🟠 HEARTBEAT VERALTET" if veraltet else "🔴 OFFLINE")
    zeilen = ["", f"🪙 OKX · SPOT · {modus} · {zustand}"]

    if veraltet and alter is not None:
        zeilen.append(f"Letztes Lebenszeichen vor {int(alter // 60)} min")

    bereit = daten.get("handelsbereitschaft") or {}
    if bereit:
        if verbunden and not veraltet and bereit.get("kaeufe_erlaubt"):
            zeilen.append("Neue Käufe: frei")
        elif not verbunden or veraltet:
            zeilen.append("Neue Käufe: gesperrt — OKX-Status nicht aktuell/authentifiziert")
        else:
            zeilen.append(f"Neue Käufe: {bereit.get('grund', 'gesperrt')}")

    kapital = daten.get("handelbares_kapital")
    if kapital is not None:
        zeilen.append(f"Handelbares Kapital: {_zahl(kapital)}")
    satz = daten.get("gebuehrensatz_pct")
    if satz:
        zeilen.append(f"Taker-Gebühr: {satz} %")

    guthaben = guthaben_zeile(daten)
    if guthaben:
        zeilen.append(f"Kontoguthaben: {guthaben}")

    assets = konto_asset_zeile(daten)
    if assets:
        zeilen.append(f"Konto-Assets (keine Bottrades): {assets}")

    positionen = positionszeilen(daten)
    zeilen.append(f"Bestätigte Bot-Positionen: {len(positionen)}")
    zeilen.extend(positionen)

    risiko = daten.get("risiko") or {}
    if risiko:
        tages = risiko.get("tages_pnl")
        if tages is not None:
            zeilen.append(f"Heute realisiert: {_zahl(tages)}")

    universum = daten.get("universum") or {}
    if universum:
        zeilen.append(
            f"Universum: {universum.get('aktiv', universum.get('handelbar', 0))} aktiv "
            f"({universum.get('kern', 0)} Kern + {universum.get('dynamisch', 0)})")

    fehler = str(daten.get("letzter_fehler") or "")
    if fehler:
        zeilen.append(f"Letzter Fehler: {fehler[:120]}")
    return zeilen


def text(daten: dict | None = None) -> str:
    return "\n".join(block(daten))


__all__ = ["lies", "block", "text", "guthaben_zeile", "konto_asset_zeile", "positionszeilen",
           "alter_sekunden", "DATEI"]
