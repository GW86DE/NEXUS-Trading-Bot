"""Meldeschicht: was gemeldet wird und wie (neu in v8.1.4).

WARUM DIESES MODUL EXISTIERT
============================
Zwei Gruende, beide am 25.08.2026 sichtbar geworden.

**1. Der Kanal war kaputt.** ``nexus_start._melder()`` importierte eine Klasse
``Notifier``, die es in ``notifier.py`` nie gab -- dort stehen nur Funktionen.
Der ImportError wurde zu einer INFO-Zeile verschluckt, ``_melder()`` gab
``None`` zurueck, und damit hatte die gesamte Kryptomaschine keinen
Meldekanal. Kein Kauf, kein Verkauf, keine Sperre erreichte den Nutzer. Der
Aktienkern meldete weiter, weil er ``notify_trade`` direkt aufruft -- deshalb
fiel es lange nicht auf.

**2. Die Meldungen sagten das Falsche.** Ein Teilverkauf von 1,67 aus 15,86
SOL wurde gemeldet wie eine vollstaendige Schliessung; dass 14,19 SOL ohne
Stop liegen blieben, stand nirgends.

DER AUFBAU
==========
Vier Klassen mit unterschiedlicher Zustellung:

    KRITISCH   sofort, nie gedrosselt -- hier muss jemand eingreifen
    HANDEL     sofort -- Kauf, Verkauf, Teilausfuehrung
    INFO       gedrosselt -- Universum, Moduswechsel, Vorschlaege
    STILL      nur Protokoll

Der Telegram-ZUGANG (Token, Chat-ID, Long-Polling, Steuerbefehle) wird von
diesem Modul nicht angefasst. Es entscheidet nur, was gesendet wird.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Optional

logger = logging.getLogger(__name__)

KRITISCH = "KRITISCH"
HANDEL = "HANDEL"
INFO = "INFO"
STILL = "STILL"

# Zustellpriorität je Klasse, passend zu notifier._priority.
_PRIORITAET = {KRITISCH: "critical", HANDEL: "critical", INFO: "normal", STILL: "low"}
_EMOJI = {KRITISCH: "🚨", HANDEL: "📊", INFO: "ℹ️"}

# INFO-Meldungen mit gleichem Text werden innerhalb dieses Fensters nur
# einmal zugestellt. KRITISCH und HANDEL nie.
_DROSSEL_SEKUNDEN = 900.0

_lock = threading.RLock()
_zuletzt: dict[str, float] = {}


def _mit_emoji(text: str, klasse: str) -> str:
    """Ein kleines, einheitliches Symbol pro Telegram-Meldung.

    Fachmeldungen duerfen ihr eigenes Symbol bereits vorne tragen. So werden
    Kauf-/Verkaufsmeldungen nicht mit zwei Piktogrammen ueberladen.
    """
    text = str(text or "").strip()
    if not text or text.startswith(("🟢", "🔴", "🟡", "🚨", "🛡", "📈", "📉", "💰", "ℹ")):
        return text
    return f"{_EMOJI.get(klasse, 'ℹ️')} {text}"


def _telegram_bereit() -> bool:
    """Ist Telegram eingerichtet? Ohne Netzabfrage."""
    try:
        from live_settings import telegram_runtime
        status = telegram_runtime()
        return bool(status["enabled"] and status["configured"])
    except Exception:
        return False


def _gedrosselt(klasse: str, text: str) -> bool:
    """Wurde derselbe INFO-Text gerade eben schon gemeldet?"""
    if klasse in (KRITISCH, HANDEL):
        return False
    schluessel = f"{klasse}:{text[:160]}"
    jetzt = time.monotonic()
    with _lock:
        letzte = _zuletzt.get(schluessel)
        if letzte is not None and jetzt - letzte < _DROSSEL_SEKUNDEN:
            return True
        _zuletzt[schluessel] = jetzt
        # Alte Eintraege aufraeumen, damit die Tabelle nicht waechst.
        if len(_zuletzt) > 500:
            grenze = jetzt - _DROSSEL_SEKUNDEN
            for k in [k for k, v in _zuletzt.items() if v < grenze]:
                _zuletzt.pop(k, None)
    return False


def melde(text: str, *, klasse: str = INFO, titel: str = "") -> bool:
    """Eine Meldung zustellen. Gibt True zurueck, wenn sie rausging.

    Faellt die Zustellung aus, ist das nie ein Fehler des Aufrufers: die
    Meldung steht dann im Protokoll und der Handel laeuft weiter.
    """
    text = str(text or "").strip()
    if not text:
        return False
    klasse = str(klasse or INFO).upper()
    if klasse not in _PRIORITAET:
        klasse = INFO

    text = _mit_emoji(text, klasse)
    logger.info("%s %s", klasse, text.replace("\n", " | "))
    if klasse == STILL:
        return False
    if _gedrosselt(klasse, text):
        logger.debug("Meldung gedrosselt: %s", text[:80])
        return False
    if not _telegram_bereit():
        return False

    try:
        from notifier import notify, send_telegram
        if titel:
            return bool(notify(titel, text, priority=_PRIORITAET[klasse]))
        return bool(send_telegram(text, priority=_PRIORITAET[klasse]))
    except Exception:
        logger.warning("Telegram-Zustellung fehlgeschlagen", exc_info=True)
        return False


def melder(domaene: str = ""):
    """Erzeugt die Meldefunktion, die Engines und Laeufe erwarten.

    Signatur bewusst ``(text, *, wichtig=False)`` -- so, wie die bestehenden
    Aufrufer sie benutzen. ``wichtig=True`` wird als HANDEL zugestellt,
    alles andere als INFO.

    **Gibt NIE None zurueck.** Genau das war der Fehler bis v8.1.3: Der
    Aufrufer bekam ``None`` und meldete daraufhin gar nichts.
    """
    kennung = str(domaene or "").upper()

    def senden(text: str, *, wichtig: bool = False, klasse: str = "") -> bool:
        gewaehlt = str(klasse or "").upper() or (HANDEL if wichtig else INFO)
        volltext = f"{kennung} · {text}" if kennung and not text.startswith(kennung) else text
        return melde(volltext, klasse=gewaehlt)

    return senden


# ---------------------------------------------------------------------------
# Zahlenformate
# ---------------------------------------------------------------------------
def mengentext(wert) -> str:
    """Eine Handelsmenge exakt darstellen.

    Bewusst NICHT ``:g``: das rundet auf 6 signifikante Stellen und macht aus
    15,85775 SOL eine 15,8577 -- in einer Handelsmeldung ist das falsch.
    Bis zu 8 Nachkommastellen, ohne ueberfluessige Nullen.
    """
    try:
        zahl = float(wert)
    except (TypeError, ValueError):
        return "?"
    text = f"{zahl:.8f}".rstrip("0").rstrip(".")
    return text or "0"


def betragstext(wert, stellen: int = 2) -> str:
    """Ein Geldbetrag im deutschen Format: 1.234,56.

    v9.1: Vorher stand in derselben Kaufmeldung "= 1,234.57 EUR" neben
    "50 000.00 EUR" -- englisches und deutsches Format nebeneinander. Fuer
    einen deutschen Leser sind 1,234.57 EUR genau ein Euro dreiundzwanzig.
    Dieselbe Zahl erschien in /status als "1.234,56".
    """
    try:
        zahl = float(wert)
    except (TypeError, ValueError):
        return "?"
    return (f"{zahl:,.{stellen}f}"
            .replace(",", "\x00").replace(".", ",").replace("\x00", "."))


def betragstext_vz(wert, stellen: int = 2) -> str:
    """Wie ``betragstext``, aber immer mit Vorzeichen (Ergebniszeilen)."""
    try:
        zahl = float(wert)
    except (TypeError, ValueError):
        return "?"
    return ("+" if zahl >= 0 else "-") + betragstext(abs(zahl), stellen)


def preistext(wert) -> str:
    """Ein Kurs mit genug Stellen -- Kryptokurse brauchen mehr als zwei."""
    try:
        zahl = float(wert)
    except (TypeError, ValueError):
        return "?"
    if abs(zahl) >= 1000:
        return f"{zahl:,.2f}".replace(",", " ")
    if abs(zahl) >= 1:
        return f"{zahl:.4f}".rstrip("0").rstrip(".")
    return f"{zahl:.8f}".rstrip("0").rstrip(".")


# ---------------------------------------------------------------------------
# Fertige Meldungstexte
# ---------------------------------------------------------------------------
def kauf(*, broker: str, symbol: str, menge: float, preis: float, waehrung: str,
         gebuehr: float = 0.0, gebuehr_pct: float = 0.0, stop: float = 0.0,
         ziel: float = 0.0, grund: str = "", geplant: float = 0.0,
         schutz: bool = True, hinweise=()) -> str:
    """Kaufmeldung, einheitlich fuer beide Broker."""
    wert = abs(float(menge)) * abs(float(preis))
    zeilen = [f"🟢 KAUF · {broker.upper()} · {symbol.upper()}",
              f"{mengentext(menge)} zu {preistext(preis)} {waehrung} "
              f"= {betragstext(wert)} {waehrung}"]
    kosten = f"Gebuehr {betragstext(gebuehr, 4) if gebuehr is not None else 'unbekannt'} {waehrung}"
    if gebuehr_pct:
        kosten += f" ({gebuehr_pct * 100:.2f} %)"
    if stop:
        kosten += f" · Stop {preistext(stop)}"
    if ziel:
        kosten += f" · Ziel {preistext(ziel)}"
    zeilen.append(kosten)
    if grund:
        zeilen.append(f"Grund: {grund}")
    if geplant and float(geplant) > float(menge) * (1 + 1e-9):
        zeilen.append(f"Order {mengentext(geplant)} geplant, {mengentext(menge)} "
                      f"ausgefuehrt — Rest storniert")
    if not schutz:
        zeilen.append("ACHTUNG: keine Broker-Schutzorder — nur der Client-Stop schuetzt")
    zeilen.extend(str(h) for h in hinweise if h)
    return "\n".join(zeilen)


def verkauf(*, broker: str, symbol: str, menge: float, geplant: float,
            preis: float, waehrung: str, gebuehr: float = 0.0,
            ergebnis: Optional[float] = None, grund: str = "",
            haltedauer: str = "", rest_gefuehrt: bool = False) -> str:
    """Verkaufsmeldung. Eine Teilausfuehrung wird ausdruecklich benannt.

    Genau das fehlte am 25.08.2026: Ein Verkauf von 1,66715 aus 15,85775 SOL
    wurde gemeldet wie eine vollstaendige Schliessung.
    """
    rest = round(max(0.0, float(geplant) - float(menge)), 12)
    zeilen = [f"💰 VERKAUF · {broker.upper()} · {symbol.upper()} · {grund}".rstrip(" ·")]
    if rest > 0:
        zeilen.append(f"{mengentext(menge)} von {mengentext(geplant)} ausgefuehrt "
                      f"— {mengentext(rest)} "
                      + ("bleiben gefuehrt und geschuetzt" if rest_gefuehrt
                         else "verfallen (unter Mindestgroesse)"))
    else:
        zeilen.append(f"{mengentext(menge)} vollstaendig ausgefuehrt")
    kosten = betragstext(gebuehr, 4) if gebuehr is not None else "unbekannt"
    zeilen.append(f"zu {preistext(preis)} {waehrung} · Gebuehr {kosten} {waehrung}")
    if ergebnis is not None:
        zeile = f"Ergebnis {betragstext_vz(ergebnis)} {waehrung} nach Gebuehren"
        if haltedauer:
            zeile += f" · Haltedauer {haltedauer}"
        zeilen.append(zeile)
    elif haltedauer:
        zeilen.append(f"Haltedauer {haltedauer} · Ergebnis nicht ermittelbar")
    return "\n".join(zeilen)


def sicherheit(titel: str, text: str, *, handlung: str = "") -> str:
    """Sicherheitsereignis mit klarer Handlungsempfehlung."""
    zeilen = [f"🛡️ SICHERHEIT · {titel}", text]
    if handlung:
        zeilen.append(f"Was jetzt: {handlung}")
    return "\n".join(zeilen)


__all__ = ["KRITISCH", "HANDEL", "INFO", "STILL", "melde", "melder",
           "kauf", "verkauf", "sicherheit", "mengentext", "preistext"]
