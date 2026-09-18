"""
Risiko-Profil auswaehlen.

Aufruf:
    python profil_waehlen.py

Schreibt die Auswahl in 'aktives_profil.txt'. Alle anderen Skripte
(Backtest, Walk-Forward, Live-Bot) lesen das beim Start automatisch ein.
"""

from pathlib import Path

import profiles

PROFIL_DATEI = Path(__file__).parent / "aktives_profil.txt"
REIHENFOLGE = ["konservativ", "ausgewogen", "offensiv"]


def aktuelles_profil() -> str:
    if PROFIL_DATEI.exists():
        name = PROFIL_DATEI.read_text(encoding="utf-8").strip().lower()
        if name in profiles.PROFILES:
            return name
    return profiles.DEFAULT_PROFILE


def main():
    aktuell = aktuelles_profil()

    print("=" * 70)
    print("RISIKO-PROFIL WAEHLEN")
    print("=" * 70)
    print()
    print(f"Aktuell aktiv: {aktuell.upper()}")
    print()
    print("-" * 70)
    print("WICHTIG ZUM VERSTAENDNIS")
    print("-" * 70)
    print("Ein Profil aendert nur, WIE VIEL pro Signal eingesetzt wird --")
    print("nicht, wie gut die Signale sind. Mehr Risiko erhoeht die")
    print("Schwankung in BEIDE Richtungen, nicht die Trefferquote.")
    print()
    print("Die Walk-Forward-Tests dieser Strategie waren bisher NICHT")
    print("ueberzeugend (meist schlechter als simples Kaufen-und-Halten).")
    print("Solange das so ist, fuehrt 'offensiv' nicht schneller zum")
    print("Gewinn, sondern schneller zum Verlust.")
    print()

    for i, name in enumerate(REIHENFOLGE, start=1):
        print("=" * 70)
        print(f"[{i}] {profiles.describe(name)}")
        print()

    print("=" * 70)
    print("[0] Abbrechen (nichts aendern)")
    print()

    wahl = input("Auswahl (0-3): ").strip()

    if wahl == "0" or wahl == "":
        print("\nAbgebrochen -- Profil bleibt unveraendert.")
        return

    try:
        index = int(wahl)
        name = REIHENFOLGE[index - 1]
    except (ValueError, IndexError):
        print(f"\nUngueltige Eingabe {wahl!r} -- nichts geaendert.")
        return

    if name == "offensiv":
        print()
        print("!" * 70)
        print("HINWEIS ZUM OFFENSIVEN PROFIL")
        print("!" * 70)
        print("Du setzt damit bis zu 15 % des Kapitals in EINE Position und")
        print("laesst bis zu 6 % Tagesverlust zu, bevor gestoppt wird.")
        print()
        print("Zur Einordnung, wie sich Verluste auswirken:")
        print("  -25 % Verlust  ->  +33 % noetig, nur um wieder bei null zu sein")
        print("  -50 % Verlust  ->  +100 % noetig")
        print()
        print("Bitte zuerst im PAPER-Modus laufen lassen und die Ergebnisse")
        print("mit den anderen Profilen vergleichen.")
        print()
        bestaetigung = input("Trotzdem auf 'offensiv' setzen? (ja/nein): ").strip().lower()
        if bestaetigung not in ("ja", "j", "yes", "y"):
            print("\nAbgebrochen -- Profil bleibt unveraendert.")
            return

    PROFIL_DATEI.write_text(name, encoding="utf-8")
    print()
    print("=" * 70)
    print(f"Profil gesetzt: {name.upper()}")
    print("=" * 70)
    print()
    print("Gilt ab dem naechsten Start von Backtest, Walk-Forward oder Bot.")
    print("Laeuft der Bot gerade, muss er dafuer neu gestartet werden.")
    print()
    print("TIPP: Fuehre jetzt einen Backtest (Option 3) oder Walk-Forward")
    print("(Option 4) aus, um zu sehen, wie sich das Profil auf Rendite")
    print("UND Drawdown auswirkt -- am besten alle drei vergleichen.")


if __name__ == "__main__":
    main()
