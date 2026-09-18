"""Clean-room implementation of Freqtrade's official SampleStrategy rules.

The module contains no exchange or order code. It evaluates completed 5-minute
candles deterministically and exposes immutable parameters for persistence,
live trading and backtesting.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import json
import math

import numpy as np
import pandas as pd

STRATEGY_NAME = "Freqtrade SampleStrategy"
# Pinned reference: official SampleStrategy, uploaded 2026.8-dev source.
STRATEGY_VERSION = "NEXUS-FT-SAMPLE-V3"
TIMEFRAME = "5m"
STARTUP_CANDLES = 200
RSI_PERIOD = 14
BUY_RSI = 30
SELL_RSI = 70
STOPLOSS = -0.10
MINIMAL_ROI = {"0": 0.04, "30": 0.02, "60": 0.01}


# Warmup, data window and exit ordering influence decisions and are hashed.
SEMANTIK_FELDER = (
    "strategy_name", "strategy_version", "interface_version", "timeframe",
    "process_only_new_candles", "can_short", "buy_rsi", "sell_rsi",
    "rsi_period", "rsi_implementation", "tema_period", "tema_implementation",
    "bollinger_middle", "minimal_roi", "roi_includes_fees", "stoploss",
    "trailing_stop", "use_exit_signal", "exit_profit_only",
    "ignore_roi_if_entry_signal",
    # Ab V2 ausdruecklich Teil der Semantik:
    "exit_order", "stop_needs_candles", "roi_needs_candles",
    "startup_candle_count", "live_candle_window", "roi_comparison", "roi_clock",
)


def parameter_snapshot() -> dict:
    params = {
        "strategy_name": STRATEGY_NAME,
        "strategy_version": STRATEGY_VERSION,
        "interface_version": 3,
        "timeframe": TIMEFRAME,
        "process_only_new_candles": True,
        "startup_candle_count": STARTUP_CANDLES,
        "live_candle_window": 500, "roi_comparison": "round8>threshold",
        "roi_clock": "initial_order_created_at",
        "can_short": False,
        "buy_rsi": BUY_RSI,
        "sell_rsi": SELL_RSI,
        "rsi_period": RSI_PERIOD,
        "rsi_implementation": "TA-Lib-compatible Wilder RSI14",
        "tema_period": 9,
        "tema_implementation": "TA-Lib-compatible TEMA9",
        "bollinger_middle": "SMA20 typical_price",
        "minimal_roi": dict(MINIMAL_ROI),
        "roi_includes_fees": True,
        "stoploss": STOPLOSS,
        "trailing_stop": False,
        "use_exit_signal": True,
        "exit_profit_only": False,
        "ignore_roi_if_entry_signal": False,
        # V2: die Ausstiegsreihenfolge und die Kerzenunabhaengigkeit von Stop
        # und ROI sind echte Semantik und gehoeren deshalb in den Hash.
        "exit_order": "signal>stoploss>roi",
        "stop_needs_candles": False,
        "roi_needs_candles": False,
        "sample_entry_order_type": "limit",
        "sample_exit_order_type": "limit",
        "sample_stoploss_order_type": "market",
        "sample_stoploss_on_exchange": False,
        "sample_order_time_in_force": {"entry": "GTC", "exit": "GTC"},
        "hyperopt_enabled": False,
        "source_reference_date": "2026-08-22",
        "source_reference": ("https://github.com/freqtrade/freqtrade/blob/develop/"
                             "freqtrade/templates/sample_strategy.py"),
        # NEXUS behaelt bewusst seine eigene, bereits geprüfte OKX-Ausfuehrung.
        # Sie ist kein Strategieparameter und geht deshalb nicht in den Hash.
        "nexus_actual_entry_order_type": "price-capped-fok",
        "nexus_actual_exit_order_type": "price-capped-fok",
        "nexus_execution_wrapper": "spot+safety-gates+full-size-vwap+fok+broker-oco",
        "parameter_hash_scope": "strategy-semantics-v3",
    }
    # v9.2: Der Hash wird ueber die ENDGUELTIGEN Werte gebildet, und zwar nur
    # ueber die Semantikfelder. Bis 9.1 wurde er ueber ALLE Felder gebildet und
    # danach wurden Ausfuehrungsfelder ueberschrieben -- der ausgelieferte
    # Snapshot war damit nicht nachrechenbar, ein Pruefer konnte den Audit-Hash
    # nicht verifizieren.
    semantik = {k: params[k] for k in SEMANTIK_FELDER if k in params}
    canonical = json.dumps(semantik, sort_keys=True, separators=(",", ":"))
    params["parameter_hash"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    params["parameter_hash_fields"] = list(SEMANTIK_FELDER)
    return params


PARAMETER_HASH = parameter_snapshot()["parameter_hash"]


@dataclass(frozen=True)
class Evaluation:
    entry: bool
    exit: bool
    close: float
    rsi: float
    tema: float
    bb_middle: float
    volume: float
    atr: float
    candle_time: str
    entry_reason: str = ""
    exit_reason: str = ""
    entry_checks: dict = field(default_factory=dict)

    def audit_values(self) -> dict:
        return asdict(self)


def _require_ohlcv(raw: pd.DataFrame) -> pd.DataFrame:
    if raw is None or not isinstance(raw, pd.DataFrame):
        return pd.DataFrame()
    renamed = {str(c).lower(): c for c in raw.columns}
    needed = ("open", "high", "low", "close", "volume")
    if any(name not in renamed for name in needed):
        return pd.DataFrame()
    df = pd.DataFrame(index=raw.index)
    for name in needed:
        df[name] = pd.to_numeric(raw[renamed[name]], errors="coerce")
    return df.replace([np.inf, -np.inf], np.nan)


def _rsi(close: pd.Series, period: int = RSI_PERIOD) -> pd.Series:
    """TA-Lib-compatible RSI (SMA seed, then Wilder smoothing)."""
    values = pd.to_numeric(close, errors="coerce").to_numpy(dtype=float)
    result = np.full(len(values), np.nan, dtype=float)
    if len(values) <= period or not np.isfinite(values[:period + 1]).all():
        return pd.Series(result, index=close.index, dtype=float)
    deltas = np.diff(values)
    gains = np.maximum(deltas, 0.0)
    losses = np.maximum(-deltas, 0.0)
    avg_gain = float(gains[:period].sum()) / period
    avg_loss = float(losses[:period].sum()) / period

    def value(gain: float, loss: float) -> float:
        denominator = gain + loss
        return 0.0 if denominator == 0.0 else 100.0 * gain / denominator

    result[period] = value(avg_gain, avg_loss)
    for i in range(period + 1, len(values)):
        if not (math.isfinite(values[i]) and math.isfinite(values[i - 1])):
            avg_gain = avg_loss = float("nan")
            continue
        delta = values[i] - values[i - 1]
        avg_gain = ((avg_gain * (period - 1)) + max(delta, 0.0)) / period
        avg_loss = ((avg_loss * (period - 1)) + max(-delta, 0.0)) / period
        result[i] = value(avg_gain, avg_loss)
    return pd.Series(result, index=close.index, dtype=float)


def _ema(series: pd.Series, period: int) -> pd.Series:
    """TA-Lib-compatible EMA with an SMA seed at the first valid window."""
    values = pd.to_numeric(series, errors="coerce").to_numpy(dtype=float)
    result = np.full(len(values), np.nan, dtype=float)
    finite = np.isfinite(values)
    starts = np.flatnonzero(finite)
    if not len(starts):
        return pd.Series(result, index=series.index, dtype=float)
    start = int(starts[0])
    seed_end = start + period
    if seed_end > len(values) or not finite[start:seed_end].all():
        return pd.Series(result, index=series.index, dtype=float)
    previous = float(values[start:seed_end].mean())
    result[seed_end - 1] = previous
    alpha = 2.0 / (period + 1.0)
    for i in range(seed_end, len(values)):
        if not math.isfinite(values[i]):
            previous = float("nan")
            continue
        if not math.isfinite(previous):
            # OHLCV quality gates reject gaps. Do not silently invent a new
            # seed in the middle of a series.
            continue
        previous = ((values[i] - previous) * alpha) + previous
        result[i] = previous
    return pd.Series(result, index=series.index, dtype=float)


def _tema(close: pd.Series, period: int = 9) -> pd.Series:
    ema1 = _ema(close, period)
    ema2 = _ema(ema1, period)
    ema3 = _ema(ema2, period)
    return 3.0 * ema1 - 3.0 * ema2 + ema3


def _atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    previous = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - previous).abs(),
        (df["low"] - previous).abs(),
    ], axis=1).max(axis=1)
    values = tr.to_numpy(dtype=float)
    result = np.full(len(values), np.nan, dtype=float)
    # TA-Lib ATR starts after ``period`` true ranges and ignores TR[0].
    if len(values) <= period or not np.isfinite(values[1:period + 1]).all():
        return pd.Series(result, index=df.index, dtype=float)
    previous_atr = float(values[1:period + 1].mean())
    result[period] = previous_atr
    for i in range(period + 1, len(values)):
        if not math.isfinite(values[i]):
            previous_atr = float("nan")
            continue
        if not math.isfinite(previous_atr):
            continue
        previous_atr = ((previous_atr * (period - 1)) + values[i]) / period
        result[i] = previous_atr
    return pd.Series(result, index=df.index, dtype=float)


def indicators(raw: pd.DataFrame) -> pd.DataFrame:
    df = _require_ohlcv(raw)
    if df.empty:
        return df
    typical = (df["high"] + df["low"] + df["close"]) / 3.0
    df["rsi"] = _rsi(df["close"])
    df["tema"] = _tema(df["close"], 9)
    df["bb_middle"] = typical.rolling(20, min_periods=20).mean()
    df["atr"] = _atr(df)
    return df


def _crossed_above(series: pd.Series, threshold: float) -> bool:
    if len(series) < 2:
        return False
    previous, current = float(series.iloc[-2]), float(series.iloc[-1])
    return math.isfinite(previous) and math.isfinite(current) and previous <= threshold < current


def evaluate(raw: pd.DataFrame) -> Evaluation:
    df = indicators(raw)
    if len(df) < STARTUP_CANDLES:
        raise ValueError(f"mindestens {STARTUP_CANDLES} abgeschlossene 5m-Kerzen erforderlich")
    row = df.iloc[-1]
    previous = df.iloc[-2]
    required = (row.get("close"), row.get("rsi"), row.get("tema"),
                row.get("bb_middle"), row.get("volume"), row.get("atr"),
                previous.get("tema"), previous.get("rsi"))
    if not all(pd.notna(value) and math.isfinite(float(value)) for value in required):
        raise ValueError("SampleStrategy-Indikatoren der letzten Kerze unvollstaendig")
    checks = {
        "rsi_cross_above_30": {
            "passed": _crossed_above(df["rsi"], BUY_RSI),
            "previous": float(previous["rsi"]), "current": float(row["rsi"]),
            "threshold": float(BUY_RSI),
        },
        "tema_at_or_below_bb_middle": {
            "passed": float(row["tema"]) <= float(row["bb_middle"]),
            "tema": float(row["tema"]), "bb_middle": float(row["bb_middle"]),
        },
        "tema_rising": {
            "passed": float(row["tema"]) > float(previous["tema"]),
            "previous": float(previous["tema"]), "current": float(row["tema"]),
        },
        "volume_positive": {
            "passed": float(row["volume"]) > 0.0,
            "volume": float(row["volume"]),
        },
    }
    entry = all(bool(item["passed"]) for item in checks.values())
    exit_signal = (
        _crossed_above(df["rsi"], SELL_RSI)
        and float(row["tema"]) > float(row["bb_middle"])
        and float(row["tema"]) < float(previous["tema"])
        and float(row["volume"]) > 0.0
    )
    candle_time = str(df.index[-1]) if len(df.index) else ""
    failed = []
    if not checks["rsi_cross_above_30"]["passed"]:
        failed.append(
            f"RSI kreuzt 30 nicht aufwaerts ({previous['rsi']:.2f} -> {row['rsi']:.2f})")
    if not checks["tema_at_or_below_bb_middle"]["passed"]:
        failed.append(
            f"TEMA {row['tema']:.8g} liegt ueber BB-Mitte {row['bb_middle']:.8g}")
    if not checks["tema_rising"]["passed"]:
        failed.append(
            f"TEMA steigt nicht ({previous['tema']:.8g} -> {row['tema']:.8g})")
    if not checks["volume_positive"]["passed"]:
        failed.append(f"Volumen ist nicht positiv ({row['volume']:.8g})")
    return Evaluation(
        entry=entry, exit=exit_signal, close=float(row["close"]),
        rsi=float(row["rsi"]), tema=float(row["tema"]),
        bb_middle=float(row["bb_middle"]), volume=float(row["volume"]),
        atr=float(row["atr"]), candle_time=candle_time,
        entry_reason=("RSI kreuzt 30 aufwaerts; TEMA unter BB-Mitte und steigend"
                      if entry else "; ".join(failed)),
        exit_reason=("RSI kreuzt 70 aufwaerts; TEMA ueber BB-Mitte und fallend"
                     if exit_signal else "SampleStrategy-Ausstieg nicht erfuellt"),
        entry_checks=checks,
    )


def roi_threshold(elapsed_minutes: float) -> float:
    elapsed = max(0, int(float(elapsed_minutes or 0.0)))
    eligible = [int(key) for key in MINIMAL_ROI if int(key) <= elapsed]
    return float(MINIMAL_ROI[str(max(eligible) if eligible else 0)])


def net_profit_ratio(*, entry_price: float, current_price: float,
                     entry_fee_pct: float = 0.0,
                     exit_fee_pct: float = 0.0) -> float:
    """Long profit after entry and assumed exit fees, per Freqtrade semantics."""
    entry = float(entry_price or 0.0)
    current = float(current_price or 0.0)
    fee_in = max(0.0, float(entry_fee_pct or 0.0))
    fee_out = max(0.0, float(exit_fee_pct or 0.0))
    if entry <= 0.0 or current <= 0.0 or fee_out >= 1.0:
        return float("nan")
    open_cost = entry * (1.0 + fee_in)
    close_value = current * (1.0 - fee_out)
    return (close_value / open_cost) - 1.0


def roi_exit_price(entry_price: float, threshold: float, *,
                   entry_fee_pct: float = 0.0,
                   exit_fee_pct: float = 0.0) -> float:
    """Gross market price required to reach a net ROI after both fees."""
    fee_in = max(0.0, float(entry_fee_pct or 0.0))
    fee_out = max(0.0, float(exit_fee_pct or 0.0))
    if float(entry_price or 0.0) <= 0.0 or fee_out >= 1.0:
        raise ValueError("ungueltiger Preis oder Gebuehrensatz")
    return (float(entry_price) * (1.0 + fee_in) * (1.0 + float(threshold))
            / (1.0 - fee_out))


def stoploss_price(entry_price: float) -> float:
    """Der statische Stop, gemessen am Einstiegspreis -- wie bei Freqtrade.

    Bewusst ohne Dataframe: Freqtrade rechnet Stoploss und ROI aus reiner
    Trade-Mathematik, nicht aus Indikatoren.
    """
    entry = float(entry_price or 0.0)
    return entry * (1.0 + float(STOPLOSS)) if entry > 0 else 0.0


def exit_decision(raw: pd.DataFrame, *, entry_price: float, current_price: float,
                  elapsed_minutes: float, entry_fee_pct: float = 0.0,
                  exit_fee_pct: float = 0.0) -> tuple[bool, str, dict]:
    """Official long SampleStrategy: exit signal, stoploss, ROI.

    Missing/stale indicators suppress only the signal. Stop and ROI always
    remain available from the actual trade and price, independently of OHLCV.
    """
    gross_profit = ((float(current_price) / float(entry_price)) - 1.0
                    if entry_price and current_price else float("nan"))
    profit = net_profit_ratio(
        entry_price=entry_price, current_price=current_price,
        entry_fee_pct=entry_fee_pct, exit_fee_pct=exit_fee_pct)
    threshold = roi_threshold(elapsed_minutes)
    stop_px = stoploss_price(entry_price)
    audit = {"gross_profit_ratio": gross_profit,
             "net_profit_ratio": profit, "profit_ratio": profit,
             "entry_fee_pct": float(entry_fee_pct),
             "exit_fee_pct": float(exit_fee_pct),
             "roi_threshold": threshold, "elapsed_minutes": float(elapsed_minutes),
             "stoploss_price": stop_px, "current_price": float(current_price or 0.0)}

    try:
        evaluation = evaluate(raw)
    except ValueError as exc:
        evaluation = None
        audit["signal_error"] = str(exc)
    if evaluation is not None:
        audit.update(evaluation.audit_values())
        if evaluation.exit and not evaluation.entry:
            audit["exit_stage"] = "exit_signal"
            return True, "freqtrade_exit_signal", audit
    if stop_px > 0 and 0 < float(current_price or 0.0) <= stop_px:
        audit["exit_stage"] = "stoploss"
        return True, "freqtrade_stop_loss", audit
    if math.isfinite(profit) and round(profit, 8) > threshold:
        audit["exit_stage"] = "roi"
        return True, f"freqtrade_roi_{threshold * 100:g}pct", audit
    if evaluation is None:
        audit["exit_stage"] = "signal_unavailable"
        return False, "", audit
    audit["exit_stage"] = "hold"
    return False, "", audit


__all__ = [
    "BUY_RSI", "Evaluation", "stoploss_price", "MINIMAL_ROI", "PARAMETER_HASH", "SELL_RSI",
    "STARTUP_CANDLES", "STOPLOSS", "STRATEGY_NAME", "STRATEGY_VERSION",
    "TIMEFRAME", "evaluate", "exit_decision", "indicators", "parameter_snapshot",
    "net_profit_ratio", "roi_exit_price", "roi_threshold",
]
