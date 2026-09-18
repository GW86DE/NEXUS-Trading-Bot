"""
Handelsuniversum anzeigen -- was der Bot ueberhaupt beobachtet.

Aufruf:
    python watchlist_anzeigen.py
"""

import warnings
warnings.filterwarnings("ignore")

from collections import Counter

import config


def _tabelle(eintraege, spalten=5):
    """Symbole in Spalten ausgeben, damit lange Listen lesbar bleiben."""
    zeilen = []
    for i in range(0, len(eintraege), spalten):
        teil = eintraege[i:i + spalten]
        zeilen.append("   " + "".join(f"{s:<14}" for s in teil))
    return "\n".join(zeilen)


def main():
    eu = config.EU_STOCKS
    us = config.US_STOCKS
    underdogs = getattr(config, "UNDERDOG_STOCKS", [])
    krypto = config.CRYPTO_SYMBOLS
    forex = config.FOREX_PAIRS

    aktiv = config.STOCK_SYMBOLS
    gesamt = len(aktiv) + len(krypto) + len(forex)

    print("=" * 74)
    print("HANDELSUNIVERSUM")
    print("=" * 74)
    print()
    print(f"  US-Kernwerte        {len(us):>4}")
    print(f"  EU-Kernwerte        {len(eu):>4}   (nur wenn von eToro qualifiziert)")
    print(f"  Underdog-Kandidaten {len(underdogs):>4}   "
          f"({'aktiv' if getattr(config, 'UNDERDOGS_AKTIV', True) else 'AUS'})")
    print(f"  {'-' * 22}")
    print(f"  Aktien gesamt       {len(aktiv):>4}")
    print(f"  Kryptowaehrungen    {len(krypto):>4}")
    print(f"  Forex               {len(forex):>4}")
    print(f"  {'=' * 22}")
    print(f"  INSTRUMENTE GESAMT  {gesamt:>4}")
    print()

    # --- Abfragetakt einordnen ---
    pro_zyklus = getattr(config, "INSTRUMENTS_PER_CYCLE", 8)
    takt = getattr(config, "CYCLE_MINUTES", 5)
    runde = (gesamt / pro_zyklus) * takt if pro_zyklus else 0
    print(f"  Der Bot prueft {pro_zyklus} Instrumente alle {takt} Minuten.")
    print(f"  Eine komplette Runde dauert damit rund {runde/60:.1f} Stunden.")
    if runde > 240:
        print("  HINWEIS: Bei Stundenkerzen ist das grenzwertig lang. Entweder")
        print("  INSTRUMENTS_PER_CYCLE erhoehen oder das Universum verkleinern.")
    print()

    print("-" * 74)
    print(f"US-KERNWERTE ({len(us)})")
    print("-" * 74)
    print(_tabelle([s["symbol"] for s in us]))
    print()

    print("-" * 74)
    print(f"EU-KERNWERTE ({len(eu)})")
    print("-" * 74)
    print(_tabelle([s["symbol"] for s in eu]))
    print()

    print("-" * 74)
    print(f"UNDERDOG-KANDIDATEN ({len(underdogs)})")
    print("-" * 74)
    print(_tabelle([s["symbol"] for s in underdogs]))
    print()
    print("  WICHTIG: Diese Liste ist KEINE Empfehlung. Es sind Kandidaten,")
    print("  die vor jedem Kauf zusaetzlich bestehen muessen:")
    print("    1. Kennzahlen-Screening (Menue: Underdog-Screening)")
    print("    2. Nachrichtenpruefung mit strengerer Schwelle")
    print("  Wer durchfaellt, wird nicht gehandelt.")
    print()

    print("-" * 74)
    print(f"KRYPTOWAEHRUNGEN ({len(krypto)})")
    print("-" * 74)
    print(_tabelle([s["symbol"] for s in krypto]))
    print()

    # --- Branchenverteilung ---
    branchen = Counter(s.get("sector", "?") for s in aktiv)
    print("-" * 74)
    print("BRANCHENVERTEILUNG")
    print("-" * 74)
    for branche, anzahl in branchen.most_common():
        balken = "#" * anzahl
        print(f"  {branche:<22} {anzahl:>3}  {balken}")
    print()

    groesste = branchen.most_common(1)[0] if branchen else ("", 0)
    if groesste[1] > len(aktiv) * 0.25:
        print(f"  HINWEIS: '{groesste[0]}' macht {groesste[1]/len(aktiv)*100:.0f} % des")
        print("  Universums aus. Bei einem Branchenabschwung traefe das viele")
        print("  Positionen gleichzeitig -- Streuung ist dann weniger wirksam,")
        print("  als die reine Anzahl vermuten laesst.")
        print()

    print("Anpassen: watchlist.py bearbeiten (Symbole mit Branchenangabe).")


if __name__ == "__main__":
    main()
