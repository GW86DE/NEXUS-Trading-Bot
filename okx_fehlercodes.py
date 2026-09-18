"""Klartext zu OKX-V5-Fehlercodes (NEXUS 9.5.5).

WARUM DIESE DATEI EXISTIERT

Am 02.09.2026 stand 25 Minuten lang nur "OKX-Serverfehler HTTP 503" im Log.
Ob die Zugangsdaten falsch waren, die Uhr abgelaufen, das Konto gesperrt oder
schlicht OKX selbst nicht erreichbar -- daraus ging es nicht hervor. Geklaert
wurde es erst mit einem eigens gebauten Diagnoseskript.

Diese Zuordnung nimmt jedem Fehlercode diese Ratearbeit ab. Jeder Eintrag
sagt drei Dinge:

  * was OKX offiziell dazu schreibt,
  * in welche Klasse er faellt (davon haengt ab, wie NEXUS reagiert),
  * was daraus praktisch folgt -- und vor allem, was es NICHT ist.

Die Bedeutungen stammen aus der offiziellen OKX-V5-Dokumentation
(https://www.okx.com/docs-v5/en/#error-code, Abschnitte "General Class",
"API Class", "Trade Class"), abgerufen am 02.09.2026. Codes, die dort nicht
belegt sind, stehen hier bewusst NICHT drin: eine erfundene Erklaerung waere
schlimmer als keine. Fuer einen unbekannten Code bleibt es bei der
Originalmeldung von OKX plus dem Aufrufort.
"""
from __future__ import annotations

# Klassen. Sie sagen, WORAN es liegt -- nicht, was NEXUS technisch tut.
ZUGANG = "ZUGANG"        # Schluessel, Signatur, Passphrase, falsche Umgebung
ZEIT = "ZEIT"            # Zeitstempel/Zeitfenster
UEBERLAST = "UEBERLAST"  # zu viele Anfragen
WARTUNG = "WARTUNG"      # OKX-Dienst voruebergehend nicht verfuegbar
ORDER = "ORDER"          # fachliche Ablehnung einer Order
UNKLAR = "UNKLAR"        # Ausgang weder Erfolg noch Misserfolg

# Code -> (Klasse, Klartext auf Deutsch)
FEHLERCODES: dict[str, tuple[str, str]] = {
    # -- Allgemein --------------------------------------------------------
    "50000": (ORDER,
              "Die Anfrage wurde ohne Inhalt gesendet. Das ist ein Formatfehler "
              "im Anfrage-Aufbau, kein Problem mit Zugangsdaten oder Zeit."),
    "50001": (WARTUNG,
              "Der OKX-Dienst ist voruebergehend nicht erreichbar. Das liegt an "
              "OKX, nicht an den Zugangsdaten -- warten und erneut versuchen."),
    "50004": (UNKLAR,
              "Die Anfrage ist in ein Zeitlimit gelaufen. Das bedeutet WEDER "
              "Erfolg NOCH Misserfolg. Vor einem erneuten Senden zwingend den "
              "echten Orderstatus abfragen, sonst droht eine Doppelorder."),
    "50011": (UEBERLAST,
              "Zu viele Anfragen in zu kurzer Zeit. Das liegt am eigenen "
              "Sendetempo, nicht an falschen Zugangsdaten -- drosseln und "
              "gestaffelt erneut versuchen."),
    "50013": (WARTUNG,
              "Die Systeme von OKX sind ausgelastet. Voruebergehend und auf "
              "OKX-Seite -- kurz warten und erneut versuchen."),
    "50026": (WARTUNG,
              "Interner Systemfehler bei OKX. Kein Fehler in der eigenen "
              "Anfrage -- kurz warten und unveraendert erneut senden."),
    # -- Zugang und Zeit --------------------------------------------------
    "50101": (ZUGANG,
              "Der API-Schluessel passt nicht zur angesprochenen Umgebung -- "
              "also Demo-Schluessel gegen Live oder umgekehrt. Pruefen, ob "
              "Demo-Kennzeichen und Schluesseltyp zusammenpassen. Kein "
              "Ausfall, kein Zeitproblem."),
    "50102": (ZEIT,
              "Der Zeitstempel der Anfrage ist abgelaufen (OKX erlaubt 30 "
              "Sekunden Abweichung). Haeufigste Ursache laut OKX: die lokale "
              "Uhr laeuft nicht auf UTC. Systemzeit synchronisieren."),
    "50103": (ZUGANG,
              "Der Kopfzeile OK-ACCESS-KEY fehlt. Der API-Schluessel wird gar "
              "nicht mitgesendet."),
    "50104": (ZUGANG,
              "Die Kopfzeile OK-ACCESS-PASSPHRASE fehlt. Die Passphrase wird "
              "gar nicht mitgesendet."),
    "50105": (ZUGANG,
              "Die Passphrase stimmt nicht mit der beim Anlegen des "
              "API-Schluessels hinterlegten ueberein."),
    "50111": (ZUGANG,
              "Der API-Schluessel ist ungueltig -- falsch, geloescht oder nicht "
              "vorhanden. Kein Zeit- oder Lastproblem."),
    "50112": (ZUGANG,
              "Der Zeitstempel-Kopfzeile hat ein ungueltiges FORMAT. Nicht zu "
              "verwechseln mit 50102 (abgelaufen)."),
    "50113": (ZUGANG,
              "Die Signatur der Anfrage liess sich nicht pruefen -- meist "
              "falscher Secret-Key oder ein Fehler beim Signieren."),
    "50114": (ZUGANG,
              "Uebergeordneter Anmeldefehler. Zugangsdaten und Kopfzeilen "
              "insgesamt pruefen."),
    # -- Handel -----------------------------------------------------------
    "51000": (ORDER,
              "Ein Parameter der Anfrage ist ungueltig; welcher, steht meist in "
              "der Meldung. Kein Zugangs- oder Zeitproblem."),
    "51001": (ORDER,
              "Das angegebene Instrument gibt es bei OKX nicht oder es ist "
              "falsch geschrieben."),
    "51008": (ORDER,
              "Die Order wurde abgelehnt, weil Guthaben oder Margin nicht "
              "ausreichen. Kontostand pruefen -- kein technischer Fehler."),
    "51009": (ORDER,
              "Das Konto ist fuer diese Order gesperrt. Ein erneutes Senden "
              "hilft nicht; das klaert nur der OKX-Support."),
    "51400": (ORDER,
              "Stornieren fehlgeschlagen, weil die Order bereits ausgefuehrt, "
              "schon storniert oder nicht mehr vorhanden ist. Fachlich ist das "
              "meist der gewuenschte Zustand."),
    "51401": (ORDER,
              "Stornieren fehlgeschlagen, weil die Order bereits storniert ist "
              "(laut OKX nur fuer Nitro Spread). Der Zielzustand liegt vor."),
    "51402": (ORDER,
              "Stornieren fehlgeschlagen, weil die Order bereits vollstaendig "
              "ausgefuehrt ist (laut OKX nur fuer Nitro Spread)."),
    "51603": (ORDER,
              "Die angesprochene Order existiert nicht. Order-Kennung pruefen "
              "oder den Status vorher abfragen."),
}


def klartext(code: str) -> str:
    """Erklaerung zu einem Code, oder "" wenn er nicht belegt ist.

    Bewusst leer statt geraten: eine erfundene Erklaerung wuerde eine
    Fehlersuche in die falsche Richtung schicken.
    """
    eintrag = FEHLERCODES.get(str(code or "").strip())
    return eintrag[1] if eintrag else ""


def klasse(code: str) -> str:
    """Klasse eines Codes, oder "" wenn er nicht belegt ist."""
    eintrag = FEHLERCODES.get(str(code or "").strip())
    return eintrag[0] if eintrag else ""


def ergaenze(meldung: str, code: str) -> str:
    """Haengt den Klartext an eine Fehlermeldung an, wenn es einen gibt."""
    text = klartext(code)
    if not text:
        return meldung
    return f"{meldung} -- {klasse(code)}: {text}"


__all__ = ["FEHLERCODES", "klartext", "klasse", "ergaenze",
           "ZUGANG", "ZEIT", "UEBERLAST", "WARTUNG", "ORDER", "UNKLAR"]
