"""
Parameter- UND Symbol-Test: probiert systematisch verschiedene Einstellungen
und mehrere Symbole durch.

Aufruf:
    python sweep.py

WOZU DAS GUT IST
----------------
Einzelne Backtests verleiten dazu, so lange an Parametern zu drehen, bis
das Ergebnis gut aussieht. Das ist Selbstbetrug ("Overfitting"): man findet
die Einstellung, die in der VERGANGENHEIT (und fuer GENAU DIESES SYMBOL) am
besten war -- sie funktioniert deshalb noch lange nicht in der Zukunft oder
bei einem anderen Wert.

Dieses Skript testet deshalb zwei Dimensionen gleichzeitig:
  1. Parameter-Kombinationen (GRID)
  2. Mehrere Symbole (SYMBOLS) -- eine Einstellung, die nur bei EINEM
     bestimmten Symbol gut abschneidet, bei anderen aber nicht, ist mit
     hoher Wahrscheinlichkeit zufaellig gut, nicht wirklich brauchbar.

WORAUF DU ACHTEN SOLLTEST
-------------------------
- trades >= 30, sonst statistisch wertlos
- return_pct groesser als buy_hold_pct (sonst waere Nichtstun besser)
- profit_factor > 1.3
- Schneidet eine Einstellung bei MEHREREN Symbolen aehnlich gut ab? Nur dann
  ist es ein Hinweis auf echten Edge statt auf Zufall.
"""

import warnings
import itertools

import pandas as pd

import config
from data import fetch_history_yfinance
from backtest import run_backtest

warnings.filterwarnings("ignore")

# Getestete Parameter-Kombinationen -- bei Bedarf anpassen.
GRID = {
    "ENTRY_MODE": ["trend", "crossover"],
    "SMA_FAST": [10, 20],
    "SMA_SLOW": [50, 100],
}

# Getestete Symbole -- bewusst unterschiedliche Branchen/Charakteristika,
# damit ein Ergebnis nicht nur fuer eine einzelne Aktie gilt. Bei Bedarf
# anpassen/erweitern (mehr Symbole = laengere Laufzeit).
SYMBOLS = ["AAPL", "MSFT", "JNJ"]


def apply_settings(settings: dict):
    for key, value in settings.items():
        setattr(config, key, value)


def run_sweep(symbols=None, period: str = "2y", interval: str = "1h"):
    symbols = symbols or SYMBOLS

    data_by_symbol = {}
    for sym in symbols:
        try:
            print(f"Lade historische Daten fuer {sym} ...")
            data_by_symbol[sym] = fetch_history_yfinance(sym, asset_type="stock",
                                                          period=period, interval=interval)
        except Exception as exc:
            print(f"  -> {sym} uebersprungen: {exc}")
    print()

    if not data_by_symbol:
        print("Keine Daten geladen -- Abbruch.")
        return None

    original = {k: getattr(config, k) for k in GRID}
    keys = list(GRID.keys())
    combinations = list(itertools.product(*(GRID[k] for k in keys)))

    total_runs = len(combinations) * len(data_by_symbol)
    print(f"Teste {len(combinations)} Parameter-Kombination(en) x "
          f"{len(data_by_symbol)} Symbol(e) = {total_runs} Backtests ...\n")

    results = []
    run_no = 0

    for combo in combinations:
        settings = dict(zip(keys, combo))
        apply_settings(settings)

        for sym, df in data_by_symbol.items():
            run_no += 1
            try:
                summary, _, _ = run_backtest(
                    df, starting_equity=10_000.0,
                    train_ml_inline=config.USE_ML_FILTER,
                    verbose=False,
                )
                row = {
                    "symbol": sym,
                    "mode": settings["ENTRY_MODE"],
                    "sma": f"{settings['SMA_FAST']}/{settings['SMA_SLOW']}",
                    "trades": summary["trades_total"],
                    "win%": summary["win_rate_pct"],
                    "pf": summary["profit_factor"],
                    "return%": summary["total_return_pct"],
                    "b&h%": summary["buy_hold_return_pct"],
                    "beats_bh": summary["total_return_pct"] > summary["buy_hold_return_pct"],
                }
                results.append(row)
                print(f"[{run_no}/{total_runs}] {sym:6s} {row['mode']:9s} SMA {row['sma']:7s} "
                      f"-> {row['trades']:3d} Trades, {row['return%']:+7.2f}% "
                      f"(B&H {row['b&h%']:+7.2f}%)")
            except Exception as exc:
                print(f"[{run_no}/{total_runs}] {sym} {settings} -> Fehler: {exc}")

    apply_settings(original)

    if not results:
        print("\nKeine auswertbaren Ergebnisse.")
        return None

    table = pd.DataFrame(results)
    print("\n" + "=" * 82)
    print("GESAMTUEBERSICHT")
    print("=" * 82)
    print(table.sort_values(["mode", "sma", "symbol"]).to_string(index=False))

    print("\n" + "=" * 82)
    print("STABILITAET UEBER SYMBOLE HINWEG (das Entscheidende)")
    print("=" * 82)
    per_setting = (
        table.groupby(["mode", "sma"])
        .agg(
            symbole_getestet=("symbol", "count"),
            symbole_ueber_bh=("beats_bh", "sum"),
            trades_min=("trades", "min"),
            return_mittel=("return%", "mean"),
            return_streuung=("return%", "std"),
        )
        .reset_index()
        .sort_values("symbole_ueber_bh", ascending=False)
    )
    print(per_setting.to_string(index=False))

    print("\n" + "=" * 82)
    print("EINORDNUNG")
    print("=" * 82)

    usable = table[table["trades"] >= 30]
    if usable.empty:
        print("! KEINE Kombination erreicht 30+ Trades. Alle Ergebnisse sind")
        print("  statistisch wertlos -- egal wie gut die Rendite aussieht.")
        return table

    robust = per_setting[
        (per_setting["symbole_ueber_bh"] == per_setting["symbole_getestet"])
        & (per_setting["trades_min"] >= 30)
    ]

    if robust.empty:
        print("! KEINE Einstellung schlaegt Buy&Hold bei ALLEN getesteten Symbolen")
        print("  mit ausreichend Trades. Einzelne gute Zeilen in der Tabelle oben")
        print("  sind wahrscheinlich Zufall, kein uebertragbarer Edge.")
    else:
        print(f"+ {len(robust)} Einstellung(en) schlagen Buy&Hold bei ALLEN getesteten")
        print("  Symbolen. Das ist das staerkste Indiz, das dieses Skript liefern")
        print("  kann -- aber weiterhin kein Beweis. Naechster Schritt: dieselbe")
        print("  Einstellung mit 'python run_walkforward.py' pruefen (zeitliche")
        print("  statt symbolbezogene Stabilitaet), und danach lange im")
        print("  Paper-Trading beobachten, BEVOR echtes Geld involviert wird.")
        print()
        print(robust.to_string(index=False))

    return table


if __name__ == "__main__":
    run_sweep()
