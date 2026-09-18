"""
Walk-Forward-Validierung fuer das erste konfigurierte Symbol.

Aufruf:
    python run_walkforward.py

Im Unterschied zu run_backtest.py (EIN Train/Test-Split) wird die Zeitreihe
hier in mehrere aufeinanderfolgende Abschnitte geteilt und JEDER einzeln
ausgewertet -- das Modell dabei immer nur auf den Daten VOR dem jeweiligen
Abschnitt trainiert. Das beantwortet die eigentlich wichtige Frage: War die
Strategie durchgaengig brauchbar, oder hatte sie in einer einzelnen Periode
nur Glueck?
"""

import warnings
warnings.filterwarnings("ignore")

import config
import json
from pathlib import Path
from datetime import datetime, timezone
from data import fetch_history_yfinance
from backtest import run_walkforward


def main():
    symbol = config.STOCK_SYMBOLS[0]["symbol"]
    print(f"Lade historische Daten fuer {symbol} ...")
    df = fetch_history_yfinance(symbol, asset_type="stock", period="2y", interval="1h")
    print(f"{len(df)} Balken geladen.")
    print(f"Einstellungen: ENTRY_MODE={config.ENTRY_MODE}, "
          f"SMA {config.SMA_FAST}/{config.SMA_SLOW}, "
          f"ML-Filter={'an' if config.USE_ML_FILTER else 'aus'}\n")

    table = run_walkforward(df, n_folds=4, starting_equity=10_000.0, verbose=True)
    status={"time":datetime.now(timezone.utc).isoformat(),"symbol":symbol,"ok":table is not None,"folds":int(len(table)) if table is not None else 0}
    if table is not None:
        status["beats_buy_hold"] = int((table["total_return_pct"] > table["buy_hold_return_pct"]).sum())
        status["avg_return_pct"] = float(table["total_return_pct"].mean())
    Path(__file__).with_name("walkforward_status.json").write_text(json.dumps(status,indent=2),encoding="utf-8")


if __name__ == "__main__":
    main()
