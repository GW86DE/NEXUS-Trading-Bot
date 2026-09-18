"""
Umschalten zwischen PAPER- und LIVE-Handel.

Aufruf:
    python handelsmodus.py

Der Modus wird in 'handelsmodus.txt' gespeichert und von config.py beim
Start gelesen. Damit muss keine Python-Datei mehr von Hand bearbeitet
werden -- der haeufigste Weg, sich versehentlich in den Live-Handel zu
bringen.

WARUM MEHRERE ABFRAGEN?
-----------------------
Der Wechsel in den Live-Modus ist die einzige Aktion in diesem Programm,
die echtes Geld bewegt und sich nicht rueckgaengig machen laesst. Ein
einzelner Klick oder Tippfehler soll dafuer nicht ausreichen. Die
Rueckfragen sind kein Misstrauen, sondern dieselbe Vorsicht, die man
auch bei einer Ueberweisung erwartet.
"""

from pathlib import Path

import profiles
from live_arming import create_arm, clear_arm

MODUS_DATEI = Path(__file__).parent / "handelsmodus.txt"


def aktueller_modus() -> str:
    if MODUS_DATEI.exists():
        wert = MODUS_DATEI.read_text(encoding="utf-8").strip().lower()
        if wert in ("paper", "live"):
            return wert
    return "paper"


def _checkliste_live() -> bool:
    """Fragt die Punkte ab, die vor echtem Geld geklaert sein sollten."""
    fragen = [
        ("Hast du den Bot mindestens mehrere WOCHEN im Paper-Modus laufen lassen?",
         "Ohne echte Laufzeit weisst du nicht, wie sich die Strategie im Alltag verhaelt."),
        ("Hast du die Ergebnisse dieser Paper-Phase ausgewertet (Trades, Treffer, Drawdown)?",
         "Ein Bot, der laeuft, ist nicht dasselbe wie ein Bot, der funktioniert."),
        ("Ist der Betrag, den du einsetzt, fuer dich VOLLSTAENDIG verschmerzbar?",
         "Automatisierter Handel kann zum Totalverlust fuehren."),
        ("Ist dir klar, dass die Walk-Forward-Tests dieser Strategie bisher NICHT ueberzeugend waren?",
         "In der Mehrheit der getesteten Zeitraeume lag sie unter Kaufen-und-Halten."),
    ]

    print()
    print("-" * 70)
    print("CHECKLISTE VOR DEM LIVE-HANDEL")
    print("-" * 70)

    for frage, hinweis in fragen:
        print()
        print(frage)
        print(f"  ({hinweis})")
        antwort = input("  ja / nein: ").strip().lower()
        if antwort not in ("ja", "j", "yes", "y"):
            print()
            print("  -> Abgebrochen. Der Modus bleibt auf PAPER.")
            print("     Das ist eine vernuenftige Entscheidung, kein Rueckschritt.")
            return False
    return True


def _hinweis_schutz():
    print()
    print("-" * 70)
    print("HINWEIS ZUM SCHUTZ IM LIVE-BETRIEB")
    print("-" * 70)
    print("Dieser Modusschalter steuert ausschliesslich eToro-Aktien. Stop-Loss und")
    print("Take-Profit werden positionsseitig beim Broker angefordert.")
    print("Eine Position wird nur automatisch uebernommen, wenn der Schutz")
    print("ueber ein explizites Adapterfeld bestaetigt wurde.")
    print()
    print("PAUSE sperrt nur neue Kaeufe; Schutz- und Verkaufslogik laufen weiter.")
    print("STOPP beendet auch die clientseitige Verwaltung und sollte deshalb")
    print("nur bewusst verwendet werden.")
    print("OKX-Krypto besitzt getrennte Demo/Live-Keys und eine eigene kurze Freigabe.")


def setze_modus(modus: str):
    MODUS_DATEI.write_text(modus, encoding="utf-8")


def main():
    aktuell = aktueller_modus()

    print("=" * 70)
    print("HANDELSMODUS")
    print("=" * 70)
    print()
    print(f"Aktuell aktiv: {aktuell.upper()}")
    print()
    print("  [1] PAPER  -- Aktienhandel ueber das eToro-Demokonto / virtuelle Mittel.")
    print("               Der komplette Botpfad bleibt aktiv, ohne Echtgeld.")
    print()
    print("  [2] LIVE   -- Aktienhandel mit ECHTEM GELD ueber eToro.")
    print("               Gewinne und Verluste sind real.")
    print()
    print("  [0] Abbrechen")
    print()

    wahl = input("Auswahl (0-2): ").strip()

    if wahl == "1":
        clear_arm()
        setze_modus("paper")
        print()
        print("Modus gesetzt: PAPER (Spielgeld).")
        print("Gilt ab dem naechsten Start des Bots.")
        return

    if wahl != "2":
        print("\nAbgebrochen -- nichts geaendert.")
        return

    # ---------------- Weg in den Live-Handel ----------------
    print()
    print("!" * 70)
    print("  ACHTUNG: WECHSEL AUF ECHTES GELD")
    print("!" * 70)

    if not _checkliste_live():
        setze_modus("paper")
        return

    _hinweis_schutz()

    print()
    print("-" * 70)
    aktives_profil = "unbekannt"
    profil_datei = Path(__file__).parent / "aktives_profil.txt"
    if profil_datei.exists():
        aktives_profil = profil_datei.read_text(encoding="utf-8").strip()
    try:
        werte = profiles.get_profile(aktives_profil)["werte"]
        print(f"Aktives Risiko-Profil: {aktives_profil.upper()}")
        print(f"  Risiko je Trade:        {werte['RISK_PER_TRADE_PCT'] * 100:.2f} % des Kontos")
        print(f"  Max. Kapital/Position:  {werte['MAX_POSITION_PCT'] * 100:.0f} %")
        print(f"  Max. offene Positionen: {werte['MAX_OPEN_POSITIONS']}")
        print(f"  Tages-Verlustlimit:     {werte['MAX_DAILY_LOSS_PCT'] * 100:.0f} %")
        if aktives_profil.lower() == "offensiv":
            print()
            print("  HINWEIS: 'offensiv' ist fuer den ERSTEN Live-Betrieb keine")
            print("  gute Wahl. Es erhoeht die Schwankung in beide Richtungen,")
            print("  nicht die Trefferquote.")
    except Exception:
        print(f"Aktives Risiko-Profil: {aktives_profil}")
    print("-" * 70)

    print()
    print("Zur Bestaetigung bitte exakt eingeben:  LIVE HANDEL")
    eingabe = input("> ").strip()

    if eingabe != "LIVE HANDEL":
        print()
        print("Eingabe stimmt nicht ueberein -- Modus bleibt auf PAPER.")
        setze_modus("paper")
        return

    # Unabhaengiger zweiter Faktor: kurzlebige Arming-Datei. Sie wird nicht
    # aus TRADING_MODE abgeleitet und laeuft automatisch ab.
    arm = create_arm(minutes=30)
    setze_modus("live")
    print()
    print("=" * 70)
    print("MODUS GESETZT: LIVE -- es wird mit ECHTEM GELD gehandelt.")
    print(f"Unabhaengige LIVE-Freigabe gueltig bis: {arm['expires_at']}")
    print("Nach Ablauf muss der LIVE-Modus ueber dieses Menue erneut bewusst freigegeben werden.")
    print("=" * 70)
    print()
    print("Vor dem Start noch pruefen:")
    print("  1. eToro-Zugang und Kontoart (LIVE) sind korrekt konfiguriert")
    print("  2. Auf dem Konto liegt nur so viel Geld, wie du einsetzen willst")
    print("  3. Starte mit dem Profil KONSERVATIV")
    print()
    print("Zurueck auf Spielgeld: dieses Menue erneut aufrufen, dann [1].")


if __name__ == "__main__":
    main()
