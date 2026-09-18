"""Zusatzstrategien 10.2.0 -- sechs veroeffentlichte, regelbasierte Handelsstrategien.

Drei Aktienstrategien (eToro) und drei Kryptostrategien (OKX), jeweils auf
abgeschlossenen Tageskerzen. Die Signal-Mathematik ist identisch mit dem
eigenstaendigen Backtest ``NEXUS_Universum_Backtest_V6.sh`` -- was der Backtest
bewertet, handelt der Bot, ohne zweite Implementierung.

Grundsaetze (unveraendert gegenueber allen anderen Einstiegsmodi):
- Die Strategie liefert nur ENTER/EXIT-Signale. Positionsgroesse, Kosten-,
  News-, Session-, Cash- und Risiko-Gates sowie der Broker-OCO-Schutz bleiben
  exakt die der installierten NEXUS-Kaskade.
- Jede Position traegt ihren unveraenderlichen Strategie-Snapshot
  (Name, Version, Parameter-Hash). Der Laufzeitschalter wirkt nur auf NEUE
  Einstiege.
- Es werden ausschliesslich abgeschlossene Tageskerzen bewertet; eine laufende
  Kerze wird verworfen.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import json

import numpy as np
import pandas as pd

TIMEFRAME = "1 day"

# Broker-Zuordnung. Ein Name darf nur auf seinem Broker aktiviert werden.
ETORO_STRATEGIEN = ("RSI2_MEAN_REVERSION", "HIGH_52W_MOMENTUM", "GOLDEN_CROSS_TREND")
OKX_STRATEGIEN = ("TSMOM_LONG_FLAT", "KELTNER_BREAKOUT", "MACD_TREND_CRYPTO")
ALLE_STRATEGIEN = ETORO_STRATEGIEN + OKX_STRATEGIEN


def normalize_frame(df):
    """OHLCV-Normalisierung wie im Backtest V6 (UTC-Index, klein geschriebene Spalten)."""
    if df is None or len(df) == 0:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    out = df.copy()
    if not isinstance(out.index, pd.DatetimeIndex):
        out.index = pd.to_datetime(out.index, utc=True, errors="coerce")
    elif out.index.tz is None:
        out.index = out.index.tz_localize("UTC")
    else:
        out.index = out.index.tz_convert("UTC")
    if isinstance(out.columns, pd.MultiIndex):
        out.columns = [str(c[0]).lower() for c in out.columns]
    else:
        out.columns = [str(c).lower() for c in out.columns]
    needed = ["open", "high", "low", "close", "volume"]
    for c in needed:
        if c not in out.columns:
            out[c] = np.nan
        out[c] = pd.to_numeric(out[c], errors="coerce")
    out = out[needed].replace([np.inf, -np.inf], np.nan)
    return out[~out.index.isna()].sort_index()


def wilder_rsi(close, period):
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = gain.ewm(alpha=1.0 / float(period), min_periods=int(period), adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / float(period), min_periods=int(period), adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    rsi = 100.0 - 100.0 / (1.0 + rs)
    # avg_loss == 0 bei vorhandenem avg_gain bedeutet RSI 100.
    rsi = rsi.where(avg_loss.ne(0.0), 100.0)
    return rsi.where(avg_gain.notna() & avg_loss.notna())


def true_range_atr(df, period=14):
    prev = df["close"].shift(1)
    tr = pd.concat([(df["high"] - df["low"]).abs(), (df["high"] - prev).abs(),
                    (df["low"] - prev).abs()], axis=1).max(axis=1)
    return tr.rolling(int(period), min_periods=int(period)).mean()


def rsi2_signals(daily, rsi_period=2, buy_below=10.0, trend_sma=200, exit_sma=5):
    d = normalize_frame(daily).copy()
    d["rsi2"] = wilder_rsi(d["close"], int(rsi_period))
    d["sma_trend"] = d["close"].rolling(int(trend_sma), min_periods=int(trend_sma)).mean()
    d["sma_exit"] = d["close"].rolling(int(exit_sma), min_periods=int(exit_sma)).mean()
    d["enter_signal"] = (d["close"] > d["sma_trend"]) & (d["rsi2"] < float(buy_below)) & (d["volume"] > 0)
    d["exit_signal"] = ((d["close"] > d["sma_exit"]) | (d["close"] < d["sma_trend"])) & (d["volume"] > 0)
    return d


def high52w_signals(daily, high_window=252, proximity=0.97, trend_sma=100, exit_sma=50):
    d = normalize_frame(daily).copy()
    d["rolling_high52"] = d["high"].shift(1).rolling(int(high_window), min_periods=int(high_window)).max()
    d["sma_trend"] = d["close"].rolling(int(trend_sma), min_periods=int(trend_sma)).mean()
    d["sma_exit"] = d["close"].rolling(int(exit_sma), min_periods=int(exit_sma)).mean()
    d["enter_signal"] = (d["close"] >= float(proximity) * d["rolling_high52"]) & (d["close"] > d["sma_trend"]) & (d["volume"] > 0)
    d["exit_signal"] = (d["close"] < d["sma_exit"]) & (d["volume"] > 0)
    return d


def golden_cross_signals(daily, fast=50, slow=200):
    d = normalize_frame(daily).copy()
    d["sma_fast"] = d["close"].rolling(int(fast), min_periods=int(fast)).mean()
    d["sma_slow"] = d["close"].rolling(int(slow), min_periods=int(slow)).mean()
    above = (d["sma_fast"] > d["sma_slow"]) & d["sma_slow"].notna()
    d["enter_signal"] = above & (d["volume"] > 0)
    d["exit_signal"] = (~above) & d["sma_slow"].notna() & (d["volume"] > 0)
    return d


def tsmom_signals(daily, lookback=90, exit_lookback=30, trend_sma=100):
    d = normalize_frame(daily).copy()
    d["mom_entry"] = d["close"].pct_change(int(lookback))
    d["mom_exit"] = d["close"].pct_change(int(exit_lookback))
    d["sma_trend"] = d["close"].rolling(int(trend_sma), min_periods=int(trend_sma)).mean()
    d["enter_signal"] = (d["mom_entry"] > 0) & (d["close"] > d["sma_trend"]) & (d["volume"] > 0)
    d["exit_signal"] = ((d["mom_exit"] < 0) | (d["close"] < d["sma_trend"])) & (d["volume"] > 0)
    return d


def keltner_signals(daily, ema_period=20, atr_period=10, atr_mult=2.0):
    d = normalize_frame(daily).copy()
    d["ema_mid"] = d["close"].ewm(span=int(ema_period), min_periods=int(ema_period), adjust=False).mean()
    d["atr"] = true_range_atr(d, int(atr_period))
    d["kc_upper"] = d["ema_mid"] + float(atr_mult) * d["atr"]
    d["enter_signal"] = (d["close"] > d["kc_upper"].shift(1)) & (d["volume"] > 0)
    d["exit_signal"] = (d["close"] < d["ema_mid"]) & (d["volume"] > 0)
    return d


def macd_trend_signals(daily, fast=12, slow=26, signal=9):
    d = normalize_frame(daily).copy()
    ema_fast = d["close"].ewm(span=int(fast), min_periods=int(fast), adjust=False).mean()
    ema_slow = d["close"].ewm(span=int(slow), min_periods=int(slow), adjust=False).mean()
    d["macd"] = ema_fast - ema_slow
    d["macd_signal"] = d["macd"].ewm(span=int(signal), min_periods=int(signal), adjust=False).mean()
    d["enter_signal"] = (d["macd"] > d["macd_signal"]) & (d["macd"] > 0) & (d["volume"] > 0)
    d["exit_signal"] = (d["macd"] < d["macd_signal"]) & (d["volume"] > 0)
    return d


# Semantik jeder Strategie. "params" geht vollstaendig in den Parameter-Hash;
# min_candles ist die harte Untergrenze abgeschlossener Tageskerzen,
# history_duration die Anforderung an broker.historie (OKX kappt bei 300
# Tageskerzen je Abruf -- alle OKX-Strategien brauchen hoechstens 200).
STRATEGIEN = {
    "RSI2_MEAN_REVERSION": {
        "label": "RSI-2 Ruecksetzer (Connors)",
        "broker": "etoro", "builder": rsi2_signals,
        "params": {"timeframe": "1d", "rsi_period": 2, "buy_below": 10.0,
                   "trend_sma": 200, "exit_sma": 5},
        "min_candles": 210, "history_duration": "400 D",
        "version": "NEXUS-ZUSATZ-RSI2-V1",
        "source": "https://www.quantifiedstrategies.com/rsi-2-strategy/",
        "beschreibung": ("Kauft starke Aktien im Aufwaertstrend, wenn sie kurz "
                          "heftig durchsacken - und verkauft schon nach wenigen Tagen wieder."),
        "indikator_spalten": ("rsi2", "sma_trend", "sma_exit"),
    },
    "HIGH_52W_MOMENTUM": {
        "label": "52-Wochen-Hoch Momentum",
        "broker": "etoro", "builder": high52w_signals,
        "params": {"timeframe": "1d", "high_window": 252, "proximity": 0.97,
                   "trend_sma": 100, "exit_sma": 50},
        "min_candles": 260, "history_duration": "500 D",
        "version": "NEXUS-ZUSATZ-52WHOCH-V1",
        "source": "https://www.jstor.org/stable/3694871",
        "beschreibung": ("Kauft Aktien, die dicht an ihrem 52-Wochen-Hoch notieren - "
                          "Staerke zieht erfahrungsgemaess weitere Staerke nach sich."),
        "indikator_spalten": ("rolling_high52", "sma_trend", "sma_exit"),
    },
    "GOLDEN_CROSS_TREND": {
        "label": "Goldenes Kreuz (50/200)",
        "broker": "etoro", "builder": golden_cross_signals,
        "params": {"timeframe": "1d", "fast_sma": 50, "slow_sma": 200},
        "min_candles": 205, "history_duration": "400 D",
        "version": "NEXUS-ZUSATZ-GOLDENESKREUZ-V1",
        "source": ("https://chartschool.stockcharts.com/table-of-contents/"
                    "trading-strategies-and-models/trading-strategies"),
        "beschreibung": ("Die wohl bekannteste Trendfolgeregel: investiert sein, solange "
                          "der 50-Tage-Durchschnitt ueber dem 200-Tage-Durchschnitt liegt."),
        "indikator_spalten": ("sma_fast", "sma_slow"),
    },
    "TSMOM_LONG_FLAT": {
        "label": "Zeitreihen-Momentum (Krypto)",
        "broker": "okx", "builder": tsmom_signals,
        "params": {"timeframe": "1d", "lookback_days": 90, "exit_lookback_days": 30,
                   "trend_sma": 100},
        "min_candles": 195, "history_duration": "290 D",
        "version": "NEXUS-ZUSATZ-TSMOM-V1",
        "source": "https://pages.stern.nyu.edu/~lpederse/papers/TimeSeriesMomentum.pdf",
        "beschreibung": ("Haelt einen Coin nur, wenn er in den letzten Monaten gestiegen "
                          "ist - sonst bleibt das Geld in Cash."),
        "indikator_spalten": ("mom_entry", "mom_exit", "sma_trend"),
    },
    "KELTNER_BREAKOUT": {
        "label": "Keltner-Ausbruch (Krypto)",
        "broker": "okx", "builder": keltner_signals,
        "params": {"timeframe": "1d", "ema_period": 20, "atr_period": 10, "atr_mult": 2.0},
        "min_candles": 40, "history_duration": "120 D",
        "version": "NEXUS-ZUSATZ-KELTNER-V1",
        "source": "https://www.quantifiedstrategies.com/keltner-bands-trading-strategies/",
        "beschreibung": ("Kauft erst, wenn der Kurs kraftvoll aus seinem normalen "
                          "Schwankungsband nach oben ausbricht - und steigt beim Rueckfall "
                          "zur Mitte wieder aus."),
        "indikator_spalten": ("ema_mid", "atr", "kc_upper"),
    },
    "MACD_TREND_CRYPTO": {
        "label": "MACD-Trend (Krypto)",
        "broker": "okx", "builder": macd_trend_signals,
        "params": {"timeframe": "1d", "fast_ema": 12, "slow_ema": 26, "signal_ema": 9},
        "min_candles": 60, "history_duration": "150 D",
        "version": "NEXUS-ZUSATZ-MACD-V1",
        "source": ("https://chartschool.stockcharts.com/table-of-contents/"
                    "technical-indicators-and-overlays/technical-indicators/"
                    "moving-average-convergence-divergence-macd"),
        "beschreibung": ("Misst mit zwei gleitenden Durchschnitten, ob der Aufwaertstrend "
                          "an Kraft gewinnt - und ist nur dann investiert."),
        "indikator_spalten": ("macd", "macd_signal"),
    },
}


def ist_zusatzstrategie(name: str) -> bool:
    return str(name or "").upper() in STRATEGIEN


def broker_strategien(broker: str) -> tuple:
    b = str(broker or "").strip().lower()
    return ETORO_STRATEGIEN if b == "etoro" else OKX_STRATEGIEN if b == "okx" else ()


def parameter_snapshot(name: str) -> dict:
    """Nachrechenbarer Strategie-Snapshot wie beim Freqtrade-Referenzmodus.

    Der Hash wird ueber die kanonisch serialisierten Semantikfelder gebildet
    (Name, Version, Timeframe, Parameter). Ausfuehrungsdetails des Bots sind
    bewusst KEIN Teil des Hashes -- sie sind keine Strategieparameter.
    """
    key = str(name or "").upper()
    spec = STRATEGIEN.get(key)
    if spec is None:
        raise ValueError(f"unbekannte Zusatzstrategie: {name}")
    semantik = {
        "strategy_name": spec["label"],
        "strategy_version": spec["version"],
        "timeframe": TIMEFRAME,
        "completed_daily_candles_only": True,
        "signal_scope": "enter/exit only; NEXUS sizing, gates and broker protection unchanged",
        "params": dict(spec["params"]),
    }
    canonical = json.dumps(semantik, sort_keys=True, separators=(",", ":"))
    return {
        "entry_strategy_mode": key,
        "strategy_name": spec["label"],
        "strategy_version": spec["version"],
        "timeframe": TIMEFRAME,
        "parameters": dict(spec["params"]),
        "min_candles": int(spec["min_candles"]),
        "history_duration": spec["history_duration"],
        "source_reference": spec["source"],
        "parameter_hash": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        "parameter_hash_scope": "zusatz-strategie-semantik-v1",
    }


def parameter_hash(name: str) -> str:
    return parameter_snapshot(name)["parameter_hash"]


@dataclass(frozen=True)
class Bewertung:
    """Ergebnis der letzten abgeschlossenen Tageskerze."""
    entry: bool
    exit: bool
    close: float
    atr: float
    candle_time: str
    entry_reason: str = ""
    exit_reason: str = ""
    indicators: dict = field(default_factory=dict)


def _abgeschlossene_tageskerzen(df) -> pd.DataFrame:
    """Verwirft defensiv eine noch laufende Tageskerze.

    Beide Broker filtern bereits mit completed_bars_only; diese zweite Pruefung
    kostet nichts und schuetzt Testdoubles und kuenftige Datenpfade.
    """
    d = normalize_frame(df)
    if len(d) == 0:
        return d
    letzte = d.index[-1].to_pydatetime()
    if letzte + timedelta(days=1) > datetime.now(timezone.utc):
        d = d.iloc[:-1]
    return d


def bewerte(name: str, df) -> Bewertung:
    """Signale der letzten abgeschlossenen Tageskerze; wirft bei zu wenig Daten."""
    key = str(name or "").upper()
    spec = STRATEGIEN.get(key)
    if spec is None:
        raise ValueError(f"unbekannte Zusatzstrategie: {name}")
    d = _abgeschlossene_tageskerzen(df)
    if len(d) < int(spec["min_candles"]):
        raise ValueError(
            f"{spec['label']}: zu wenige abgeschlossene Tageskerzen "
            f"({len(d)} < {spec['min_candles']})")
    sig = spec["builder"](d)
    letzte = sig.iloc[-1]
    atr = true_range_atr(sig, 14).iloc[-1]
    indikatoren = {}
    for spalte in spec["indikator_spalten"]:
        wert = letzte.get(spalte)
        indikatoren[spalte] = float(wert) if wert == wert else None
    entry = bool(letzte.get("enter_signal", False))
    exit_ = bool(letzte.get("exit_signal", False))
    return Bewertung(
        entry=entry, exit=exit_,
        close=float(letzte["close"]),
        atr=float(atr) if atr == atr else 0.0,
        candle_time=str(sig.index[-1]),
        entry_reason=(f"{spec['label']}: Einstiegsregel auf Tageskerze erfuellt"
                       if entry else f"{spec['label']}: keine Einstiegsbedingung"),
        exit_reason=(f"{spec['label']}: Ausstiegsregel auf Tageskerze erfuellt"
                      if exit_ else ""),
        indicators=indikatoren,
    )


__all__ = [
    "ALLE_STRATEGIEN", "Bewertung", "ETORO_STRATEGIEN", "OKX_STRATEGIEN",
    "STRATEGIEN", "TIMEFRAME", "bewerte", "broker_strategien",
    "ist_zusatzstrategie", "parameter_hash", "parameter_snapshot",
]
