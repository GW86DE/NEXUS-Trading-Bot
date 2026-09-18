"""Uebernahme-Auftraege aus der Oberflaeche an den Handelskern (v8.1.5).

WARUM ES DIESEN UMWEG GIBT
==========================
Georg wollte am 25.08.2026 eine selbst gekaufte Aktie an den Bot uebergeben:

    "Zusaetzlich wollte ich heute in der GUI eine aktie die ich selber gekauft
     habe dem Bot uebergeben das er sie handeln soll. die funktion gibt es
     dort ja aber es wurde blockiert weil sie zu sehr im minus war. kann man
     das anders loesen? kann man das in die WebUi auch einbauen"

Die WebUI laeuft als EIGENER PROZESS neben dem Handelskern. Sie hat keine
Brokerverbindung -- und soll auch keine bekommen:

* Zwei Prozesse, die gleichzeitig ``position_state.json`` schreiben, koennen
  sich gegenseitig ueberschreiben. Betroffen waeren Stop und Einstand, also
  genau die Werte, an denen Geld haengt.
* Eine zweite eToro-Sitzung aus der Oberflaeche heraus waere ein unnoetiges
  Risiko fuer Orderpfad und Reconnect.
* Ohne aktuellen Kurs kann die Oberflaeche Stop und Ziel gar nicht pruefen.

Deshalb schreibt die WebUI nur die ABSICHT in eine Datei. Der Handelskern
holt sie in seiner eigenen Schleife ab, wo er den Broker und den aktuellen
Kurs hat, fuehrt sie aus und schreibt das Ergebnis zurueck. Es ist derselbe
Weg, den ``bot_zustand.json`` fuer Pausieren/Aktivieren schon geht.

PROZENT STATT ABSOLUTZAHLEN
===========================
Ein Auftrag nennt Stop und Ziel als Abstand IN PROZENT vom Kurs, nicht als
feste Betraege. Der Kurs auf der Seite ist ein paar Sekunden alt; bis der
Kern den Auftrag ausfuehrt, kann er sich bewegt haben. Ein fester Stop
koennte dann schon ueber dem Marktpreis liegen und sofort ausloesen. Prozente
rechnet der Kern gegen den Kurs, den er im Moment der Ausfuehrung sieht.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from state_lock import critical_state_lock

logger = logging.getLogger(__name__)

DATEI = "position_auftraege.json"
MAX_ERLEDIGT = 25          # so viele Ergebnisse bleiben zum Nachlesen stehen
AUFTRAG_GUELTIG_MINUTEN = 30.0

AKTIONEN = ("uebernehmen", "beobachten")

_LOCK = threading.RLock()


def _pfad() -> Path:
    wurzel = os.getenv("TRADINGBOT_TEST_STATE_DIR", "").strip()
    return (Path(wurzel) if wurzel else Path(__file__).resolve().parent) / DATEI


def _jetzt() -> str:
    return datetime.now(timezone.utc).isoformat()


def _lies() -> dict:
    pfad = _pfad()
    if not pfad.exists():
        return {"offen": [], "erledigt": []}
    try:
        daten = json.loads(pfad.read_text(encoding="utf-8"))
        if not isinstance(daten, dict):
            raise ValueError("kein Objekt")
        daten.setdefault("offen", [])
        daten.setdefault("erledigt", [])
        return daten
    except Exception as exc:
        logger.error("Auftragsdatei unlesbar -- keine Aktion wird ueberschrieben",
                     exc_info=True)
        raise RuntimeError("Positionsauftragsdatei ist unlesbar") from exc


def _schreibe(daten: dict) -> None:
    from safe_persistence import atomic_write_json
    daten["erledigt"] = list(daten.get("erledigt") or [])[-MAX_ERLEDIGT:]
    atomic_write_json(_pfad(), daten)


def _alter_minuten(zeitstempel: str) -> float:
    try:
        stempel = datetime.fromisoformat(str(zeitstempel).replace("Z", "+00:00"))
        if stempel.tzinfo is None:
            stempel = stempel.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - stempel).total_seconds() / 60.0
    except Exception:
        return AUFTRAG_GUELTIG_MINUTEN + 1


# ---------------------------------------------------------------------------
# Seite der Oberflaeche
# ---------------------------------------------------------------------------
def anfordern(symbol: str, aktion: str, *, stop_pct: float = 0.0,
              ziel_pct: float = 0.0, von: str = "webui",
              record_id: str = "", position_ids=None,
              instrument_id: str = "", account_fingerprint: str = "",
              snapshot_id: str = "") -> dict:
    """Einen Auftrag hinterlegen. Wirft ValueError bei unsinnigen Angaben."""
    kennung = str(symbol or "").strip().upper()
    if not kennung:
        raise ValueError("Kein Symbol angegeben.")
    was = str(aktion or "").strip().lower()
    if was not in AKTIONEN:
        raise ValueError(f"Unbekannte Aktion {aktion!r}.")

    auftrag = {"id": uuid.uuid4().hex[:12], "symbol": kennung, "aktion": was,
               "erstellt_am": _jetzt(), "von": str(von or "")[:40],
               "record_id": str(record_id or ""),
               "position_ids": sorted({str(x) for x in (position_ids or []) if str(x)}),
               "instrument_id": str(instrument_id or ""),
               "account_fingerprint": str(account_fingerprint or ""),
               "snapshot_id": str(snapshot_id or "")}

    if was == "uebernehmen":
        try:
            stop = float(stop_pct)
            ziel = float(ziel_pct)
        except (TypeError, ValueError):
            raise ValueError("Stop und Ziel muessen Prozentzahlen sein.")
        # Die Grenzen sind bewusst weit: eine bestehende Position kann jede
        # Lage haben. Sie fangen nur offensichtlichen Unsinn ab -- ob der Stop
        # wirklich passt, prueft der Kern am echten Kurs.
        if not (0.1 <= stop <= 50.0):
            raise ValueError("Der Stop muss zwischen 0,1 % und 50 % unter dem "
                             "Kurs liegen.")
        if not (0.1 <= ziel <= 200.0):
            raise ValueError("Das Ziel muss zwischen 0,1 % und 200 % ueber dem "
                             "Kurs liegen.")
        auftrag["stop_pct"] = stop
        auftrag["ziel_pct"] = ziel

    with _LOCK, critical_state_lock(_pfad()):
        daten = _lies()
        # Pro exakter Position-Line nur ein offener Auftrag. Zwei eToro-Lines
        # desselben Symbols duerfen sich nicht gegenseitig ersetzen.
        identity = auftrag["record_id"] or kennung
        daten["offen"] = [a for a in daten["offen"]
                          if str(a.get("record_id") or a.get("symbol")) != identity]
        daten["offen"].append(auftrag)
        _schreibe(daten)
    logger.info("Positionsauftrag hinterlegt: %s %s", was, kennung)
    return auftrag


def uebersicht() -> dict:
    """Was liegt an, was ist zuletzt passiert -- fuer die Oberflaeche."""
    with _LOCK, critical_state_lock(_pfad()):
        daten = _lies()
    return {"offen": list(daten.get("offen") or []),
            "erledigt": list(reversed(daten.get("erledigt") or []))[:10]}


# ---------------------------------------------------------------------------
# Seite des Handelskerns
# ---------------------------------------------------------------------------
def offene() -> list[dict]:
    """Die abzuarbeitenden Auftraege; abgelaufene fallen dabei heraus."""
    with _LOCK, critical_state_lock(_pfad()):
        daten = _lies()
        frisch, abgelaufen = [], []
        for auftrag in daten.get("offen") or []:
            if _alter_minuten(auftrag.get("erstellt_am", "")) > AUFTRAG_GUELTIG_MINUTEN:
                abgelaufen.append(auftrag)
            else:
                frisch.append(auftrag)
        if abgelaufen:
            daten["offen"] = frisch
            for auftrag in abgelaufen:
                daten.setdefault("erledigt", []).append({
                    **auftrag, "ok": False, "erledigt_am": _jetzt(),
                    "detail": (f"Verfallen: der Handelskern hat den Auftrag "
                               f"nicht innerhalb von "
                               f"{AUFTRAG_GUELTIG_MINUTEN:.0f} Minuten "
                               "abgeholt. Laeuft er?")})
            _schreibe(daten)
        return frisch


def erledigt(auftrag_id: str, ok: bool, detail: str) -> None:
    """Einen Auftrag abschliessen und das Ergebnis hinterlegen."""
    kennung = str(auftrag_id or "")
    with _LOCK, critical_state_lock(_pfad()):
        daten = _lies()
        rest, getroffen = [], None
        for auftrag in daten.get("offen") or []:
            if str(auftrag.get("id")) == kennung and getroffen is None:
                getroffen = auftrag
            else:
                rest.append(auftrag)
        if getroffen is None:
            return
        daten["offen"] = rest
        daten.setdefault("erledigt", []).append({
            **getroffen, "ok": bool(ok), "erledigt_am": _jetzt(),
            "detail": str(detail or "")[:400]})
        _schreibe(daten)


def verarbeite(manager, kurse: dict, *, melder=None) -> int:
    """Alle offenen Auftraege ausfuehren. Rueckgabe: wie viele.

    ``kurse`` bildet Symbol auf den aktuellen Kurs ab. Ein Auftrag ohne Kurs
    wird NICHT auf den Einstand ausgewichen -- er wartet auf den naechsten
    Durchlauf, in dem Kursdaten anliegen.
    """
    verarbeitet = 0
    for auftrag in offene():
        symbol = str(auftrag.get("symbol") or "")
        record_id = str(auftrag.get("record_id") or symbol)
        aktion = str(auftrag.get("aktion") or "")
        try:
            rec = (manager.get_by_record_id(record_id)
                   if hasattr(manager, "get_by_record_id") else None)
            if rec is None:
                rec = manager.get_by_symbol(symbol)
            if rec is None:
                raise ValueError("Position nicht gefunden oder nicht eindeutig")
            expected_ids = {str(x) for x in (auftrag.get("position_ids") or [])}
            if expected_ids and expected_ids != rec.position_id_set():
                raise ValueError(
                    "Position hat sich seit dem WebUI-Auftrag geaendert; bitte neu laden")
            expected_account = str(auftrag.get("account_fingerprint") or "")
            if (expected_account and expected_account !=
                    str(getattr(rec, "broker_account_fingerprint", "") or "")):
                raise ValueError("eToro-Konto passt nicht mehr zum Auftrag")
            expected_instrument = str(auftrag.get("instrument_id") or "")
            if (expected_instrument and expected_instrument !=
                    str(getattr(rec, "broker_instrument_id", "") or "")):
                raise ValueError("eToro-Instrument passt nicht mehr zum Auftrag")
            expected_snapshot = str(auftrag.get("snapshot_id") or "")
            if (expected_snapshot and expected_snapshot !=
                    str(getattr(rec, "broker_snapshot_id", "") or "")):
                raise ValueError(
                    "Depot-Snapshot hat sich seit dem WebUI-Auftrag geaendert; "
                    "bitte Position neu laden")
            if aktion == "beobachten":
                ok = bool(manager.set_observe_only(
                    record_id, "Über die WebUI auf Nur beobachten gesetzt."))
                detail = ("Position wird nur noch beobachtet; der Bot verkauft "
                          "sie nicht mehr selbst."
                          if ok else "Position nicht gefunden.")
            else:
                pid_key = next(iter(rec.position_id_set()), "")
                kurs = float(kurse.get(record_id) or kurse.get(f"pid:{pid_key}")
                             or kurse.get(symbol.upper()) or 0.0)
                if kurs <= 0:
                    # Kein Ergebnis eintragen: der Auftrag bleibt offen und
                    # wird beim naechsten Durchlauf erneut versucht.
                    logger.info("Uebernahme %s wartet auf einen Kurs", symbol)
                    continue
                stop = kurs * (1.0 - float(auftrag.get("stop_pct", 0.0)) / 100.0)
                ziel = kurs * (1.0 + float(auftrag.get("ziel_pct", 0.0)) / 100.0)
                ok, detail = manager.request_takeover(record_id, stop, ziel,
                                                      aktueller_kurs=kurs)
                if ok:
                    detail = (f"Stop {stop:.6g} · Ziel {ziel:.6g} "
                              f"(Kurs {kurs:.6g}). " + detail)
        except Exception as exc:
            logger.exception("Positionsauftrag %s fehlgeschlagen", symbol)
            ok, detail = False, f"Fehler bei der Ausfuehrung: {exc}"

        erledigt(str(auftrag.get("id")), ok, detail)
        verarbeitet += 1
        if melder is not None:
            try:
                melder("POSITIONSVERWALTUNG",
                       f"{symbol}: {'übernommen' if ok else 'nicht möglich'} — {detail}")
            except Exception:
                logger.debug("Auftragsmeldung nicht zustellbar", exc_info=True)
    return verarbeitet


__all__ = ["anfordern", "uebersicht", "offene", "erledigt", "verarbeite",
           "AKTIONEN", "AUFTRAG_GUELTIG_MINUTEN", "DATEI"]
