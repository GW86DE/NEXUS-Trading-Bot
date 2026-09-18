"""Boersenkalender der NYSE/NASDAQ -- vollstaendig offline berechnet.

WARUM DIESES MODUL EXISTIERT
============================
Bis v5.12 kannte der Bot nur Wochentag und Handelszeit. Feiertage erkannte er
erst daran, dass der Broker "geschlossen" meldete oder Kurse veraltet waren.

Folge: An einem Feiertag lief der komplette Zyklus normal weiter -- Kursabruf,
Nachrichtenpruefung, Aufmerksamkeitssteuerung per KI -- obwohl von vornherein
kein Handel moeglich war. Das kostet Geld (OpenAI-Aufrufe), erzeugt
Protokollrauschen und belastet die Nachrichtenquellen ohne Nutzen.

Dieses Modul beantwortet die Frage VORHER und ohne Netzzugriff.

WARUM OHNE API
==============
Die US-Boersenfeiertage folgen festen Regeln (Bundesfeiertage plus Karfreitag)
und lassen sich exakt berechnen. Eine API waere eine zusaetzliche Fehlerquelle
und ein weiterer Schluessel -- fuer Daten, die sich aus dem Kalender ergeben.

Grundlage: NYSE/NASDAQ-Handelskalender. Neun Bundesfeiertage plus Karfreitag,
dazu die verkuerzten Handelstage. Faellt ein Feiertag auf Samstag, ruht der
Handel am Freitag davor; faellt er auf Sonntag, am Montag danach.

GRENZE, DIE MAN KENNEN MUSS
===========================
Ausserplanmaessige Schliessungen (Staatstrauer, Naturereignisse, technische
Stoerungen) stehen in keinem Kalender. Dafuer bleibt die bestehende Pruefung
ueber Brokerstatus und Kursalter zustaendig -- dieses Modul ersetzt sie nicht,
es entlastet sie nur.
"""
from __future__ import annotations

from datetime import date, datetime, time as dtime, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")

RTH_OPEN = dtime(9, 30)
RTH_CLOSE = dtime(16, 0)
EARLY_CLOSE = dtime(13, 0)


# ---------------------------------------------------------------------------
def _nth_weekday(jahr: int, monat: int, wochentag: int, n: int) -> date:
    """n-ter bestimmter Wochentag eines Monats (wochentag: Mo=0 ... So=6)."""
    d = date(jahr, monat, 1)
    versatz = (wochentag - d.weekday()) % 7
    return d + timedelta(days=versatz + 7 * (n - 1))


def _last_weekday(jahr: int, monat: int, wochentag: int) -> date:
    """Letzter bestimmter Wochentag eines Monats."""
    if monat == 12:
        d = date(jahr, 12, 31)
    else:
        d = date(jahr, monat + 1, 1) - timedelta(days=1)
    return d - timedelta(days=(d.weekday() - wochentag) % 7)


def _ostersonntag(jahr: int) -> date:
    """
    Ostersonntag nach dem anonymen gregorianischen Algorithmus.
    Wird nur fuer Karfreitag gebraucht -- den einzigen Boersenfeiertag,
    der kein Bundesfeiertag ist.
    """
    a = jahr % 19
    b, c = divmod(jahr, 100)
    d, e = divmod(b, 4)
    g = (8 * b + 13) // 25
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (2 * e + 2 * i - h - k + 32) % 7
    m = (a + 11 * h + 19 * l) // 433
    monat = (h + l - 7 * m + 90) // 25
    tag = (h + l - 7 * m + 33 * monat + 19) % 32
    return date(jahr, monat, tag)


def _verschiebe(d: date) -> date:
    """Samstag -> Freitag davor, Sonntag -> Montag danach."""
    if d.weekday() == 5:
        return d - timedelta(days=1)
    if d.weekday() == 6:
        return d + timedelta(days=1)
    return d


# ---------------------------------------------------------------------------
@lru_cache(maxsize=32)
def feiertage(jahr: int) -> dict[date, str]:
    """Alle ganztaegigen Boersenfeiertage eines Jahres mit Bezeichnung."""
    tage: dict[date, str] = {}

    def setze(d: date, name: str):
        tage[d] = name

    setze(_verschiebe(date(jahr, 1, 1)), "Neujahr")
    setze(_nth_weekday(jahr, 1, 0, 3), "Martin Luther King Day")
    setze(_nth_weekday(jahr, 2, 0, 3), "Presidents Day")
    setze(_ostersonntag(jahr) - timedelta(days=2), "Karfreitag")
    setze(_last_weekday(jahr, 5, 0), "Memorial Day")
    if jahr >= 2022:            # Juneteenth erst seit 2022 Boersenfeiertag
        setze(_verschiebe(date(jahr, 6, 19)), "Juneteenth")
    setze(_verschiebe(date(jahr, 7, 4)), "Independence Day")
    setze(_nth_weekday(jahr, 9, 0, 1), "Labor Day")
    setze(_nth_weekday(jahr, 11, 3, 4), "Thanksgiving")
    setze(_verschiebe(date(jahr, 12, 25)), "Weihnachten")
    return tage


@lru_cache(maxsize=32)
def verkuerzte_tage(jahr: int) -> dict[date, str]:
    """
    Handelstage mit vorgezogenem Schluss um 13:00 Ortszeit New York.
    Praktisch relevant: An diesen Tagen ist um 13:00 Schluss, nicht um 16:00 --
    ein Bot, der bis 16:00 scannt, arbeitet drei Stunden ins Leere.
    """
    tage: dict[date, str] = {}
    thanksgiving = _nth_weekday(jahr, 11, 3, 4)
    tage[thanksgiving + timedelta(days=1)] = "Tag nach Thanksgiving"

    # Heiligabend nur, wenn er auf einen regulaeren Handelstag faellt
    heiligabend = date(jahr, 12, 24)
    if heiligabend.weekday() < 5 and heiligabend not in feiertage(jahr):
        tage[heiligabend] = "Heiligabend"

    # 3. Juli nur, wenn der 4. Juli auf einen Wochentag faellt
    vierter = date(jahr, 7, 4)
    dritter = date(jahr, 7, 3)
    if vierter.weekday() < 5 and dritter.weekday() < 5:
        tage[dritter] = "Tag vor Independence Day"
    return tage


# ---------------------------------------------------------------------------
def ist_feiertag(tag: date) -> tuple[bool, str]:
    ft = feiertage(tag.year)
    if tag in ft:
        return True, ft[tag]
    return False, ""


def ist_handelstag(tag: date) -> tuple[bool, str]:
    """Wird an diesem Tag ueberhaupt gehandelt?"""
    if tag.weekday() >= 5:
        return False, "Wochenende"
    feier, name = ist_feiertag(tag)
    if feier:
        return False, f"Boersenfeiertag: {name}"
    return True, ""


def schlusszeit(tag: date) -> tuple[dtime, str]:
    """Regulaerer Handelsschluss des Tages (13:00 an verkuerzten Tagen)."""
    kurz = verkuerzte_tage(tag.year)
    if tag in kurz:
        return EARLY_CLOSE, kurz[tag]
    return RTH_CLOSE, ""


def naechster_handelstag(ab: date) -> date:
    tag = ab + timedelta(days=1)
    for _ in range(15):
        offen, _grund = ist_handelstag(tag)
        if offen:
            return tag
        tag += timedelta(days=1)
    return tag


# ---------------------------------------------------------------------------
def sitzungsstatus(jetzt: datetime | None = None) -> dict:
    """
    Vollstaendiger Status der US-Aktiensitzung zu einem Zeitpunkt.

    Rueckgabe enthaelt bewusst auch 'grund' und 'naechste_oeffnung', damit
    Meldungen an den Nutzer erklaeren koennen, WARUM gerade nicht gehandelt
    wird -- statt nur "geschlossen" zu sagen.
    """
    jetzt = (jetzt or datetime.now(NY)).astimezone(NY)
    heute = jetzt.date()
    offen_heute, grund = ist_handelstag(heute)
    schluss, kurz_grund = schlusszeit(heute)

    if not offen_heute:
        naechster = naechster_handelstag(heute)
        return {
            "offen": False,
            "phase": "GESCHLOSSEN",
            "grund": grund,
            "handelstag": False,
            "verkuerzt": False,
            "verkuerzt_grund": "",
            "oeffnung": None,
            "schluss": None,
            "naechste_oeffnung": datetime.combine(naechster, RTH_OPEN, tzinfo=NY),
            "jetzt_ny": jetzt,
        }

    oeffnung_dt = datetime.combine(heute, RTH_OPEN, tzinfo=NY)
    schluss_dt = datetime.combine(heute, schluss, tzinfo=NY)

    if jetzt < oeffnung_dt:
        phase, offen = "VORBOERSLICH", False
    elif jetzt < schluss_dt:
        phase, offen = "OFFEN", True
    else:
        phase, offen = "NACHBOERSLICH", False

    naechste = oeffnung_dt if jetzt < oeffnung_dt else datetime.combine(
        naechster_handelstag(heute), RTH_OPEN, tzinfo=NY)

    return {
        "offen": offen,
        "phase": phase,
        "grund": kurz_grund if kurz_grund else "",
        "handelstag": True,
        "verkuerzt": bool(kurz_grund),
        "verkuerzt_grund": kurz_grund,
        "oeffnung": oeffnung_dt,
        "schluss": schluss_dt,
        "naechste_oeffnung": naechste,
        "jetzt_ny": jetzt,
    }


def darf_arbeiten(jetzt: datetime | None = None, *, puffer_minuten: int = 20) -> tuple[bool, str]:
    """
    Lohnt sich ein vollstaendiger Arbeitszyklus (Kurse, Nachrichten, KI)?

    Der Puffer laesst den Bot kurz vor Handelsbeginn hochlaufen, damit die
    ersten Kerzen bereits vorliegen. Ausserhalb dieses Fensters ist ein
    Zyklus fuer Aktien reine Verschwendung -- genau die unnoetigen
    GPT-Abfragen, um die es hier geht.
    """
    st = sitzungsstatus(jetzt)
    if not st["handelstag"]:
        return False, st["grund"]
    if st["offen"]:
        return True, ""
    jetzt_ny = st["jetzt_ny"]
    if st["phase"] == "VORBOERSLICH":
        vorlauf = (st["oeffnung"] - jetzt_ny).total_seconds() / 60.0
        if vorlauf <= puffer_minuten:
            return True, ""
        return False, f"Handelsbeginn in {vorlauf:.0f} Minuten"
    return False, "Handelsschluss erreicht"
