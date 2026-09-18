"""
Nachrichtenlage pruefen -- was der Bot vor einem Kauf sieht.

Aufruf:
    python news_check.py

Zeigt fuer einzelne Werte oder das ganze Universum, welche Meldungen der
Bot findet und wie er sie bewertet. Nuetzlich, um nachzuvollziehen, warum
ein Kauf blockiert wurde -- oder um zu pruefen, ob der Filter ueberhaupt
Daten bekommt.
"""

import warnings
warnings.filterwarnings("ignore")

import config
from console_io import configure_utf8_console
configure_utf8_console()
from news_filter import NachrichtenFilter


def _kopf():
    print("=" * 74)
    print("NACHRICHTENPRUEFUNG")
    print("=" * 74)
    print()
    print("Der Bot sucht in aktuellen Meldungen nach Warnsignalen und")
    print("blockiert Kaeufe, wenn zu viel Beunruhigendes zusammenkommt.")
    print()
    print("WICHTIG: Der Krisenfilter bewertet Warnsignale regelbasiert; er ist kein LLM.")
    print("NEXUS kombiniert mehrere Newsquellen, dedupliziert Meldungen und")
    print("nutzt zusaetzlich Event-/Earnings-/Preisreaktionslogik im Live-Trader.")
    print("Fehlalarme oder uebersehene Meldungen bleiben trotzdem moeglich.")
    print()


def _pruefe_liste(symbole, filt, ueberschrift):
    print("-" * 74)
    print(ueberschrift)
    print("-" * 74)

    auffaellig = []
    ungeprueft = 0

    for i, symbol in enumerate(symbole, 1):
        lage = filt.pruefe(symbol)
        if not lage.geprueft:
            ungeprueft += 1
            if ungeprueft <= 2:
                print(f"  {symbol:8s} keine Daten ({lage.hinweis})")
            continue

        markierung = "  "
        if lage.kauf_blockiert:
            markierung = "!!"
            auffaellig.append(lage)
        elif lage.treffer:
            markierung = " ~"

        print(f"{markierung} {lage.kurzfassung()}")
        if lage.treffer and lage.schlagzeilen:
            for zeile in lage.schlagzeilen[:2]:
                print(f"       - {zeile[:100]}")

    if ungeprueft > 2:
        print(f"  ... und {ungeprueft - 2} weitere ohne Nachrichtendaten")

    print()
    if auffaellig:
        print(f"ERGEBNIS: {len(auffaellig)} Wert(e) wuerden aktuell NICHT gekauft:")
        for lage in auffaellig:
            print(f"  - {lage.symbol} ({lage.punkte} Punkte)")
    elif ungeprueft == len(symbole):
        print("ERGEBNIS: Es konnten gar keine Nachrichten abgerufen werden.")
        print("  Der Filter blockiert dann bewusst NICHTS -- ein Filter, der")
        print("  mangels Daten alles verhindert, waere unbrauchbar.")
        print("  Pruefe die aktivierten Research-Quellen und deren Zugangsdaten.")
        print("  SEC, Yahoo und Google News funktionieren ohne Broker-News-Zugang.")
    else:
        print("ERGEBNIS: Nichts Auffaelliges gefunden.")
    print()


def main():
    _kopf()
    filt = NachrichtenFilter()

    while True:
        print("  [1] Einzelnen Wert pruefen")
        print("  [2] Alle Underdogs pruefen (die mit den strengeren Huerden)")
        print("  [3] Offene Positionen pruefen")
        print("  [4] Gesamtes Aktien-Universum pruefen (dauert)")
        print("  [5] Marktlage pruefen (breiter Einbruch?)")
        print("  [6] Newsquellen-Status anzeigen")
        print("  [0] Zurueck")
        try:
            wahl = input("Auswahl: ").strip()
        except EOFError:
            print("\nKeine Konsoleneingabe verfuegbar. Bitte ueber die NEXUS-WebUI erneut oeffnen.")
            return
        print()

        if wahl in ("0", ""):
            return

        if wahl == "1":
            symbol = input("Symbol (z.B. AAPL): ").strip().upper()
            if symbol:
                lage = filt.pruefe(symbol)
                print()
                print(lage.details())
                print()
                if lage.kauf_blockiert:
                    print(">> Ein Kauf wuerde derzeit BLOCKIERT.")
                elif lage.verkauf_empfohlen:
                    print(">> Bei offener Position wuerde ein Verkauf empfohlen.")
                elif lage.geprueft:
                    print(">> Unauffaellig -- ein Kauf waere aus Nachrichtensicht moeglich.")
                print()

        elif wahl == "2":
            symbole = [s["symbol"] for s in config.STOCK_SYMBOLS if s.get("underdog")]
            _pruefe_liste(symbole, filt, f"UNDERDOGS ({len(symbole)} Werte)")

        elif wahl == "3":
            try:
                from broker import get_broker
                broker = get_broker()
                broker.connect()
                symbole = [p.symbol for p in broker.positionen()]
                broker.disconnect()
                if symbole:
                    _pruefe_liste(symbole, filt, f"OFFENE POSITIONEN ({len(symbole)})")
                else:
                    print("Es sind keine Positionen offen.\n")
            except Exception as exc:
                print(f"Broker nicht erreichbar: {exc}")
                print("Fuer diese Auswahl muss die Verbindung stehen.\n")

        elif wahl == "4":
            symbole = [s["symbol"] for s in config.STOCK_SYMBOLS]
            print(f"Pruefe {len(symbole)} Werte -- das dauert einen Moment ...\n")
            _pruefe_liste(symbole, filt, f"GESAMTES UNIVERSUM ({len(symbole)} Werte)")

        elif wahl == "5":
            lage = filt.marktlage()
            print(lage.details() if hasattr(lage, "details") else lage)
            print()

        elif wahl == "6":
            try:
                from news_sources_status import main as show_sources
                show_sources()
            except Exception as exc:
                print(f"Quellenstatus konnte nicht gelesen werden: {exc}\n")

        else:
            print("Ungueltige Auswahl.\n")


if __name__ == "__main__":
    main()
