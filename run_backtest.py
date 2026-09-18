"""
Fuehrt einen Backtest fuer das erste konfigurierte Symbol aus.

Aufruf:
    python run_backtest.py

Es wird ausschliesslich auf Daten getestet, die das Modell nie gesehen hat
(Out-of-Sample), inklusive Kommission und Slippage. Zum Vergleich wird
immer ausgewiesen, was simples Kaufen-und-Halten im selben Zeitraum
gebracht haette -- das ist die Messlatte, die eine Strategie schlagen muss.
"""

import warnings
warnings.filterwarnings("ignore")

import config
from data import fetch_history_yfinance
from backtest import run_backtest


def main():
    symbol = config.STOCK_SYMBOLS[0]["symbol"]
    print("HINWEIS: BACKTEST = MACHBARKEITSSTUDIE DER GRUNDREGEL, KEINE LIVE-ERTRAGSPROGNOSE")
    print(f"Lade historische Daten fuer {symbol} ...")
    df = fetch_history_yfinance(symbol, asset_type="stock", period="2y", interval="1h")
    print(f"{len(df)} Balken geladen.")
    print(f"Einstellungen: ENTRY_MODE={config.ENTRY_MODE}, "
          f"SMA {config.SMA_FAST}/{config.SMA_SLOW}, "
          f"ML-Filter={'an' if config.USE_ML_FILTER else 'aus'}\n")

    run_backtest(
        df,
        starting_equity=10_000.0,
        train_ml_inline=config.USE_ML_FILTER,
        verbose=True,
    )

    print()
    print("-" * 70)
    print("NAECHSTER SCHRITT")
    print("-" * 70)
    print("Einzelne Backtests koennen taeuschen. Fuehre 'python sweep.py' aus,")
    print("um zu sehen, ob das Ergebnis ueber viele Einstellungen hinweg stabil")
    print("ist -- oder ob es nur ein Zufallstreffer war.")


if __name__ == "__main__":
    main()
