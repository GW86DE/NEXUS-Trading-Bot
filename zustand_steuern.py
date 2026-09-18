"""
Betriebszustand des Bots ansehen und aendern (lokal, ueber das Menue).

Aufruf:
    python zustand_steuern.py

Dasselbe laesst sich auch per Telegram von unterwegs machen.
Dieses Skript ist der lokale Weg am Rechner.

WICHTIG: Der Zustand wirkt auf den LAUFENDEN Bot. Er liest die Datei bei
jedem Durchlauf neu, du musst ihn also nicht neu starten. Bis zur naechsten
Wirkung koennen aber bis zu CYCLE_MINUTES vergehen.
"""

import config
from bot_zustand import BotZustand, AKTIV, PAUSIERT, GESTOPPT


def main():
    zustand = BotZustand(getattr(config, "BOT_STATE_FILE", "bot_zustand.json"))

    while True:
        print()
        print("=" * 70)
        print("BETRIEBSZUSTAND")
        print("=" * 70)
        print()
        print(zustand.beschreibung())
        print()
        print("-" * 70)
        print("  [1] AKTIV     -- normal handeln (kaufen und verkaufen)")
        print("  [2] PAUSE     -- keine neuen Kaeufe, Verkaeufe laufen weiter")
        print("  [3] STOPP     -- gar kein Handel mehr, auch keine Verkaeufe")
        print("  [0] Zurueck")
        print()
        print("  Hinweis: PAUSE ist meist die bessere Wahl. Bei STOPP")
        print("  ueberwacht der Bot offene Positionen nicht mehr.")
        print()

        wahl = input("Auswahl: ").strip()

        if wahl in ("0", ""):
            return

        ziel = {"1": AKTIV, "2": PAUSIERT, "3": GESTOPPT}.get(wahl)
        if ziel is None:
            print("Ungueltige Auswahl.")
            continue

        if ziel == GESTOPPT:
            print()
            print("Bei STOPP fuehrt der Bot keine clientseitige Verwaltung mehr aus.")
            print("Nur ein von eToro bestaetigter serverseitiger Stop/Take-Profit")
            print("bleibt unabhaengig vom Bot aktiv. PAUSE ist deshalb meist sicherer.")
            print()
            if input("Trotzdem stoppen? (ja/nein): ").strip().lower() not in ("ja", "j", "y"):
                print("Abgebrochen.")
                continue

        if ziel == AKTIV and zustand.zustand() != AKTIV:
            print()
            print("Der Bot darf danach wieder eigenstaendig kaufen.")
            if input("Handel freigeben? (ja/nein): ").strip().lower() not in ("ja", "j", "y"):
                print("Abgebrochen.")
                continue

        vorher = zustand.setze(ziel, "ueber das Menue gesetzt", "menue")
        print()
        print(f"Zustand geaendert: {vorher.upper()} -> {ziel.upper()}")
        print(f"Der laufende Bot uebernimmt das beim naechsten Durchlauf "
              f"(spaetestens in {getattr(config, 'CYCLE_MINUTES', 5)} Minuten).")


if __name__ == "__main__":
    main()
