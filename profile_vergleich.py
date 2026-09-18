"""
Vergleicht alle drei Risiko-Profile auf denselben Daten.

Aufruf:
    python profile_vergleich.py

Zeigt schwarz auf weiss, was ein Profilwechsel tatsaechlich bewirkt:
Rendite UND Drawdown veraendern sich zusammen -- meist in dieselbe
Richtung. Genau das ist der Punkt, den man beim Thema "mehr Risiko =
mehr Gewinn" leicht uebersieht.
"""

import warnings
warnings.filterwarnings("ignore")

import pandas as pd

import config
import profiles
from data import fetch_history_yfinance
from backtest import run_backtest, run_walkforward


def main():
    symbol = config.STOCK_SYMBOLS[0]["symbol"]
    print(f"Lade historische Daten fuer {symbol} ...")
    df = fetch_history_yfinance(symbol, asset_type="stock", period="2y", interval="1h")
    print(f"{len(df)} Balken geladen.\n")

    # Ausgangszustand sichern, um ihn hinterher wiederherzustellen
    gesichert = {}
    for profil in profiles.PROFILES.values():
        for key in profil["werte"]:
            if key not in gesichert:
                gesichert[key] = getattr(config, key, None)

    zeilen = []

    for name in ["konservativ", "ausgewogen", "offensiv"]:
        profiles.apply_profile(config, name)
        print(f"Teste Profil: {name.upper()} ...")

        try:
            summary, _, _ = run_backtest(
                df, starting_equity=10_000.0,
                train_ml_inline=config.USE_ML_FILTER,
                verbose=False,
            )
            zeilen.append({
                "Profil": name,
                "Trades": summary["trades_total"],
                "Treffer%": summary["win_rate_pct"],
                "PF": summary["profit_factor"],
                "Rendite%": summary["total_return_pct"],
                "MaxVerlust%": summary["max_drawdown_pct"],
                "B&H%": summary["buy_hold_return_pct"],
            })
        except Exception as exc:
            print(f"  -> Fehler: {exc}")

    # Ausgangszustand wiederherstellen
    for key, value in gesichert.items():
        if value is not None:
            setattr(config, key, value)

    if not zeilen:
        print("Keine auswertbaren Ergebnisse.")
        return

    tabelle = pd.DataFrame(zeilen)

    print()
    print("=" * 78)
    print("VERGLEICH DER RISIKO-PROFILE")
    print("=" * 78)
    print(tabelle.to_string(index=False))
    print()

    print("=" * 78)
    print("EINORDNUNG")
    print("=" * 78)

    bester = tabelle.loc[tabelle["Rendite%"].idxmax()]
    tiefster = tabelle.loc[tabelle["MaxVerlust%"].idxmin()]

    print(f"Hoechste Rendite:       {bester['Profil']} ({bester['Rendite%']:+.2f} %)")
    print(f"Groesster Zwischenverlust: {tiefster['Profil']} ({tiefster['MaxVerlust%']:.2f} %)")
    print()

    # Kernaussage: steigen Rendite UND Drawdown gemeinsam?
    korrelation_hinweis = (
        tabelle["Rendite%"].idxmax() == tabelle["MaxVerlust%"].idxmin()
    )
    if korrelation_hinweis:
        print("Beachte: Dasselbe Profil hat die hoechste Rendite UND den")
        print("groessten Zwischenverlust. Genau das ist gemeint mit 'Risiko")
        print("wirkt in beide Richtungen' -- du kaufst die Mehrrendite mit")
        print("groesseren Einbruechen, nicht mit besseren Signalen.")
        print()

    schlaegt_bh = tabelle[tabelle["Rendite%"] > tabelle["B&H%"]]
    if schlaegt_bh.empty:
        print("WICHTIG: KEIN Profil schlaegt simples Kaufen-und-Halten.")
        print("Das bedeutet: Die Frage nach dem richtigen Risiko-Profil ist")
        print("hier zweitrangig -- zuerst muesste die Strategie ueberhaupt")
        print("einen Vorteil zeigen. Mehr Einsatz auf eine Strategie ohne")
        print("Vorteil vergroessert nur den Verlust.")
    else:
        print(f"{len(schlaegt_bh)} Profil(e) schlagen Kaufen-und-Halten in")
        print("diesem Zeitraum. Zur Absicherung bitte zusaetzlich")
        print("'python run_walkforward.py' laufen lassen -- ein einzelner")
        print("Zeitraum kann taeuschen.")

    print()
    print("Erinnerung zur Verlustrechnung:")
    print("  -10 % -> +11 % noetig zum Ausgleich")
    print("  -25 % -> +33 % noetig")
    print("  -50 % -> +100 % noetig")


if __name__ == "__main__":
    main()
