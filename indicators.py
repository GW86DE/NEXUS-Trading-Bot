"""
Technische Indikatoren, reine pandas/numpy Implementierung
(keine externe TA-Bibliothek noetig).

Neu gegenueber der ersten Fassung:
- ATR (Average True Range) fuer volatilitaetsabhaengige Stops. Ein fester
  2%-Stop ist bei einer ruhigen Aktie viel zu weit und bei einer nervoesen
  viel zu eng -- der ATR passt sich automatisch an.
- Relative Kennzahlen (sma_ratio, price_vs_slow, rsi_change, return_5),
  die als ML-Features taugen, weil sie unabhaengig vom absoluten Kursniveau
  sind.
"""

import numpy as np
import pandas as pd


def sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(window=period, min_periods=period).mean()


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False, min_periods=period).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.rolling(window=period, min_periods=period).mean()
    avg_loss = loss.rolling(window=period, min_periods=period).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi_val = 100 - (100 / (1 + rs))
    return rsi_val.fillna(50)


def macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    ema_fast = ema(series, fast)
    ema_slow = ema(series, slow)
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def realized_volatility(series: pd.Series, period: int = 20) -> pd.Series:
    log_returns = np.log(series / series.shift(1))
    return log_returns.rolling(window=period).std() * np.sqrt(period)


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """
    Average True Range -- durchschnittliche tatsaechliche Schwankungsbreite.
    Faellt auf eine Naeherung zurueck, wenn keine High/Low-Spalten da sind.
    """
    if not {"high", "low"}.issubset(df.columns):
        return (df["close"].diff().abs()).rolling(window=period).mean()

    high_low = df["high"] - df["low"]
    high_close = (df["high"] - df["close"].shift()).abs()
    low_close = (df["low"] - df["close"].shift()).abs()

    true_range = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    return true_range.rolling(window=period, min_periods=period).mean()


def add_all_indicators(df: pd.DataFrame, price_col: str = "close",
                       sma_fast: int = 20, sma_slow: int = 50,
                       rsi_period: int = 14, atr_period: int = 14) -> pd.DataFrame:
    """
    Erwartet ein DataFrame mit mindestens einer Preis-Spalte (Standard: 'close').
    Gibt eine Kopie mit allen zusaetzlichen Indikator-Spalten zurueck.
    """
    out = df.copy()
    price = out[price_col]

    out["sma_fast"] = sma(price, sma_fast)
    out["sma_slow"] = sma(price, sma_slow)
    out["rsi"] = rsi(price, rsi_period)

    macd_line, signal_line, hist = macd(price)
    out["macd"] = macd_line
    out["macd_signal"] = signal_line
    out["macd_hist"] = hist

    out["volatility"] = realized_volatility(price)
    out["atr"] = atr(out, atr_period)

    # --- relative Kennzahlen (kursniveau-unabhaengig, gut als ML-Features) ---
    out["return_1"] = price.pct_change(1)
    out["return_5"] = price.pct_change(5)
    # >1 = Aufwaertstrend, <1 = Abwaertstrend
    out["sma_ratio"] = out["sma_fast"] / out["sma_slow"]
    # Wie weit ist der Kurs vom langsamen Schnitt entfernt (relativ)?
    out["price_vs_slow"] = price / out["sma_slow"] - 1
    out["rsi_change"] = out["rsi"].diff(3)

    return out
