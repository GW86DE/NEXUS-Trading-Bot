"""Historische Daten fuer Offline-Backtests/ML. Live-Marktdaten kommen ausschliesslich von eToro."""
from __future__ import annotations
import logging
import pandas as pd

logger = logging.getLogger(__name__)

def to_yahoo_symbol(name, asset_type):
    symbol=str(name or "").upper().strip()
    if str(asset_type).lower()=="crypto":
        base=symbol.replace("/USD","").replace("-USD","").replace(".USD","")
        return f"{base}-USD"
    return symbol.replace(".US","")

def fetch_history_yfinance(symbol, asset_type="stock", period="2y", interval="1h"):
    try:
        import yfinance as yf
    except ImportError as exc:
        raise RuntimeError("Historische Yahoo-Daten nicht verfuegbar: yfinance fehlt; Abhaengigkeiten installieren") from exc
    df=yf.download(to_yahoo_symbol(symbol,asset_type),period=period,interval=interval,
                   progress=False,auto_adjust=True,threads=False)
    if df.empty:
        raise ValueError(f"Keine Yahoo-Daten fuer {symbol}")
    if isinstance(df.columns,pd.MultiIndex):
        df.columns=[str(c[0]).lower() for c in df.columns]
    else:
        df.columns=[str(c).lower() for c in df.columns]
    needed=["open","high","low","close","volume"]
    for col in needed:
        if col not in df.columns:
            df[col]=0.0 if col=="volume" else df["close"]
    df=df[needed].apply(pd.to_numeric,errors="coerce").dropna(subset=["open","high","low","close"])
    df.index.name="date"
    return df
