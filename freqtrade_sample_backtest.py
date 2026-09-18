"""Deterministic long-only backtest for the NEXUS SampleStrategy mode.

Signals are calculated on completed 5-minute candles. Entries and explicit
exit signals execute on the following candle open, preventing lookahead.
Stop-loss and ROI thresholds use the following candle's intrabar low/high.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import argparse
import hashlib
import json
import math
from pathlib import Path

import pandas as pd

from freqtrade_sample_strategy import (
    BUY_RSI, PARAMETER_HASH, SELL_RSI, STARTUP_CANDLES, STOPLOSS,
    STRATEGY_NAME, STRATEGY_VERSION, indicators, parameter_snapshot,
    roi_threshold,
)


@dataclass
class BacktestTrade:
    entry_time: str
    exit_time: str
    entry_price: float
    exit_price: float
    quantity: float
    exit_reason: str
    gross_pnl: float
    fees: float
    net_pnl: float
    return_pct: float
    bars: int


def _signals(raw: pd.DataFrame) -> pd.DataFrame:
    df = indicators(raw).copy()
    cross_buy = (df["rsi"] > BUY_RSI) & (df["rsi"].shift(1) <= BUY_RSI)
    cross_sell = (df["rsi"] > SELL_RSI) & (df["rsi"].shift(1) <= SELL_RSI)
    df["enter_long"] = (cross_buy & (df["tema"] <= df["bb_middle"])
                        & (df["tema"] > df["tema"].shift(1)) & (df["volume"] > 0))
    df["exit_long"] = (cross_sell & (df["tema"] > df["bb_middle"])
                       & (df["tema"] < df["tema"].shift(1)) & (df["volume"] > 0))
    return df


def run_backtest(raw: pd.DataFrame, *, initial_capital: float = 10_000.0,
                 stake_pct: float = 0.10, fee_pct: float = 0.001,
                 slippage_pct: float = 0.0, execution_mode: str = "observed_bars",
                 include_equity_curve: bool = False) -> dict:
    if not (0 < float(stake_pct) <= 1):
        raise ValueError("stake_pct muss > 0 und <= 1 sein")
    if (not all(math.isfinite(float(v)) for v in (initial_capital, fee_pct, slippage_pct))
            or float(initial_capital) <= 0 or not 0 <= float(fee_pct) < 1
            or not 0 <= float(slippage_pct) < 1):
        raise ValueError("Kapital muss endlich und positiv; Gebuehr und Slippage muessen in [0, 1) liegen")
    if execution_mode not in {"observed_bars", "legacy_gap_fill"}:
        raise ValueError("Unbekanntes Backtest-Ausfuehrungsmodell")
    from freqtrade_candles import normalize
    normalized = normalize(raw)
    # A caller may supply already-normalized data. Do not turn its marked
    # placeholders into observations when the normalizer runs a second time.
    if "synthetic_gap" in raw:
        marked = raw["synthetic_gap"].fillna(False).astype(bool)
        marked.index = marked.index.tz_convert("UTC")
        marked = marked.groupby(level=0).all()
        normalized["synthetic_gap"] |= marked.reindex(normalized.index, fill_value=False)
    input_digest = hashlib.sha256(normalized.to_json(date_format="iso", orient="split",
        double_precision=15).encode("utf-8")).hexdigest()
    df = _signals(normalized)
    # Some indicator functions project OHLCV only. The execution mask belongs
    # to the input contract, independently of which indicators are calculated.
    df["synthetic_gap"] = normalized["synthetic_gap"].reindex(df.index, fill_value=True)
    if len(df) < STARTUP_CANDLES + 2:
        raise ValueError(f"mindestens {STARTUP_CANDLES + 2} 5m-Kerzen erforderlich")
    capital = float(initial_capital)
    peak = capital
    max_drawdown = 0.0
    equity_peak = capital
    equity_drawdown = 0.0
    equity_curve = []
    position = None
    pending_entry = False
    pending_exit = False
    trades: list[BacktestTrade] = []

    def record_equity(timestamp, mark_price=None):
        nonlocal peak, max_drawdown, equity_peak, equity_drawdown
        equity = capital
        if position is not None and mark_price is not None:
            liquidation = float(mark_price) * (1.0-float(slippage_pct))
            quantity = float(position["quantity"])
            equity += ((liquidation-float(position["price"]))*quantity
                       - float(position["entry_fee"]) - liquidation*quantity*float(fee_pct))
        peak = max(peak, capital)
        max_drawdown = max(max_drawdown, (peak-capital)/peak if peak > 0 else 0.0)
        equity_peak = max(equity_peak, equity)
        equity_drawdown = max(equity_drawdown,
                              (equity_peak-equity)/equity_peak if equity_peak > 0 else 0.0)
        if include_equity_curve:
            point = {"time": str(timestamp), "realized_capital": capital, "equity": equity}
            if equity_curve and equity_curve[-1]["time"] == point["time"]:
                equity_curve[-1] = point
            else:
                equity_curve.append(point)

    for i in range(STARTUP_CANDLES, len(df)):
        row = df.iloc[i]
        timestamp = df.index[i]
        if execution_mode == "observed_bars" and bool(row["synthetic_gap"]):
            # Carry a pending action to the next observed open. Neither an
            # entry, an exit nor a risk/ROI fill can be priced from a gap row.
            # Indicators still see the reference-compatible normalized grid.
            continue

        if position is None and pending_entry:
            entry = float(row["open"]) * (1.0 + float(slippage_pct))
            stake = min(capital / (1.0+float(fee_pct)), capital * float(stake_pct))
            entry_fee = stake * float(fee_pct)
            quantity = max(0.0, stake / entry)
            if quantity > 0:
                position = {"index": i, "time": str(timestamp), "price": entry,
                            "quantity": quantity, "entry_fee": entry_fee}
            pending_entry = False

        if position is not None:
            elapsed = max(0.0, (timestamp - pd.Timestamp(position["time"])).total_seconds() / 60.0)
            stop_price = float(position["price"]) * (1.0 + float(STOPLOSS))
            threshold = roi_threshold(elapsed)
            # Freqtrade's minimal ROI is net of entry and exit fees. Use the
            # exact cost already paid for this simulated position instead of
            # treating a gross price move as net profit.
            open_cost = (float(position["price"]) * float(position["quantity"])
                         + float(position["entry_fee"]))
            roi_price = (open_cost * (1.0 + threshold)
                         / (float(position["quantity"]) * (1.0 - float(fee_pct))))
            exit_price = None; reason = ""

            # The prior close's signal is evaluated at this open before
            # the following intrabar stop/ROI, matching the reference engine.
            if pending_exit:
                exit_price = float(row["open"]) * (1.0 - float(slippage_pct))
                reason = "exit_signal"
            elif float(row["open"]) <= stop_price:
                exit_price = float(row["open"]) * (1.0 - float(slippage_pct))
                reason = "stop_loss_gap"
            elif float(row["low"]) <= stop_price:
                exit_price = stop_price * (1.0 - float(slippage_pct))
                reason = "stop_loss"
            elif round(float(row["high"]) * float(position["quantity"]) * (1-float(fee_pct)) / open_cost - 1, 8) > threshold:
                roi_entry = max(int(k) for k in parameter_snapshot()["minimal_roi"] if int(k) <= int(elapsed))
                if elapsed > 0 and int(elapsed) == roi_entry and float(row["open"]) > roi_price:
                    roi_price = float(row["open"])
                exit_price = max(float(row["low"]), min(roi_price, float(row["high"]))) * (1.0-float(slippage_pct))
                reason = f"roi_{threshold * 100:g}pct"

            pending_exit = False
            if exit_price is not None:
                quantity = float(position["quantity"])
                gross = (exit_price - float(position["price"])) * quantity
                exit_fee = exit_price * quantity * float(fee_pct)
                fees = float(position["entry_fee"]) + exit_fee
                net = gross - fees
                capital += net
                trades.append(BacktestTrade(
                    entry_time=str(position["time"]), exit_time=str(timestamp),
                    entry_price=float(position["price"]), exit_price=exit_price,
                    quantity=quantity, exit_reason=reason, gross_pnl=gross,
                    fees=fees, net_pnl=net,
                return_pct=(net / max(1e-12, float(position["price"]) * quantity
                                      + float(position["entry_fee"]))) * 100.0,
                    bars=i-int(position["index"]),
                ))
                position = None

        # The current close creates an action for the next candle open.
        if position is None:
            pending_entry = bool(row.get("enter_long", False)) and not bool(row.get("exit_long", False))
        else:
            pending_exit = bool(row.get("exit_long", False)) and not bool(row.get("enter_long", False))

        record_equity(timestamp, row["close"])

    if position is not None:
        eligible = df if execution_mode == "legacy_gap_fill" else df.loc[~df["synthetic_gap"]]
        row = eligible.iloc[-1]
        final_timestamp = eligible.index[-1]
        exit_price = float(row["close"]) * (1.0 - float(slippage_pct))
        quantity = float(position["quantity"])
        gross = (exit_price-float(position["price"])) * quantity
        exit_fee = exit_price * quantity * float(fee_pct)
        fees = float(position["entry_fee"]) + exit_fee
        net = gross-fees; capital += net
        trades.append(BacktestTrade(
            entry_time=str(position["time"]), exit_time=str(final_timestamp),
            entry_price=float(position["price"]), exit_price=exit_price,
            quantity=quantity, exit_reason="end_of_data", gross_pnl=gross,
            fees=fees, net_pnl=net,
            return_pct=(net/max(1e-12, float(position["price"])*quantity
                                 + float(position["entry_fee"])))*100.0,
            bars=int(df.index.get_loc(final_timestamp))-int(position["index"]),
        ))
        position = None
        record_equity(final_timestamp)

    wins = [x for x in trades if x.net_pnl > 0]
    losses = [x for x in trades if x.net_pnl < 0]
    gross_profit = sum(x.net_pnl for x in wins)
    gross_loss = abs(sum(x.net_pnl for x in losses))
    return {
        "result_schema_version": 2,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "input_digest": input_digest,
        "strategy": parameter_snapshot(),
        "assumptions": {"completed_5m_only": True, "next_open_execution": True,
                        "same_candle_priority": "exit_signal>stoploss>roi",
                        "elapsed_time": "UTC timestamps", "gap_handling": "previous_close_zero_volume",
                        "execution_revision": ("nexus-backtest-observed-bars-v2" if execution_mode == "observed_bars"
                                               else "nexus-backtest-legacy-gap-fill-v1"),
                        "execution_mode": execution_mode,
                        "gap_execution": ("defer pending actions to next observed open; no gap fills"
                                          if execution_mode == "observed_bars" else "legacy synthetic-price fills enabled"),
                        "metrics_revision": "net-equity-close-and-realized-v2",
                        "equity_mark": "observed candle close, net liquidation estimate" if execution_mode == "observed_bars"
                                       else "normalized candle close, net liquidation estimate",
                        "equity_limitation": "No intrabar equity path; equity between observations is unknown",
                        "execution_model": "single-pair next-open; no orderbook or queue simulation",
                        "initial_capital": float(initial_capital),
                        "stake_pct": float(stake_pct), "fee_pct": float(fee_pct),
                        "slippage_pct": float(slippage_pct)},
        "candles": len(df), "synthetic_gap_candles": int(df["synthetic_gap"].sum()),
        "trades": len(trades), "wins": len(wins),
        "losses": len(losses),
        "win_rate_pct": (len(wins)/len(trades)*100.0 if trades else 0.0),
        "initial_capital": float(initial_capital), "final_capital": capital,
        "net_pnl": capital-float(initial_capital),
        "return_pct": ((capital/float(initial_capital)-1.0)*100.0
                       if initial_capital else 0.0),
        "max_drawdown_pct": equity_drawdown*100.0,
        "equity_max_drawdown_pct": equity_drawdown*100.0,
        "realized_max_drawdown_pct": max_drawdown*100.0,
        "equity_curve": equity_curve if include_equity_curve else None,
        "profit_factor": (gross_profit/gross_loss if gross_loss > 0 else None),
        "trade_rows": [asdict(x) for x in trades],
    }


def load_csv(path: str | Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    time_column = next((x for x in ("timestamp", "date", "datetime", "time") if x in df.columns), None)
    if not time_column:
        raise ValueError("CSV benoetigt timestamp/date/datetime/time")
    df[time_column] = pd.to_datetime(df[time_column], utc=True, errors="raise")
    return df.set_index(time_column).sort_index()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="NEXUS 9.0 Freqtrade SampleStrategy Backtest")
    parser.add_argument("csv", help="CSV mit timestamp, open, high, low, close, volume")
    parser.add_argument("--capital", type=float, default=10_000.0)
    parser.add_argument("--stake-pct", type=float, default=0.10)
    parser.add_argument("--fee-pct", type=float, default=0.001)
    parser.add_argument("--slippage-pct", type=float, default=0.0)
    parser.add_argument("--execution-mode", choices=["observed_bars", "legacy_gap_fill"], default="observed_bars")
    parser.add_argument("--equity-curve", action="store_true", help="Bewertungskurve im Ergebnis ausgeben")
    args = parser.parse_args(argv)
    result = run_backtest(load_csv(args.csv), initial_capital=args.capital,
                          stake_pct=args.stake_pct, fee_pct=args.fee_pct,
                          slippage_pct=args.slippage_pct, execution_mode=args.execution_mode,
                          include_equity_curve=args.equity_curve)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
