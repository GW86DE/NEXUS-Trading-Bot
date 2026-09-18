"""Technische Machbarkeitsstudie der Grundstrategie.

Wichtig: Der historische Backtest reproduziert keine zeitpunktgenauen eToro-
Eligibility-/What-if-Kosten, News-, Event- und Earnings-Zustaende. Er ist daher
KEINE Ertragsprognose und kein Beleg fuer das konkrete Live-Verhalten.

Verbessert in v5.10.0: Positionsobergrenze entspricht dem Live-Limit und
Stop/Take-Profit werden intrabar ueber High/Low geprueft. Treffen Stop und TP
in derselben Kerze, wird konservativ der Stop zuerst angenommen.
"""

import warnings

import numpy as np
import pandas as pd

import config
from strategy import prepare, entry_conditions, exit_conditions
from ml_model import train_model, predict_up_probability_batch
from risk_manager import calculate_stop_take, size_new_position
from cost_engine import commission_for as _commission_for, regulatory_cost_for, slippage_pct as _slippage_pct, fallback_spread_pct, estimate_roundtrip

warnings.filterwarnings("ignore", category=UserWarning)


def commission_for(quantity: float, price: float, asset_type: str = "stock",
                   currency: str = "USD") -> float:
    """Kompatibilitaets-Wrapper zur zentralen Cost Engine."""
    return _commission_for(quantity, price, asset_type, currency)


def slippage_for(asset_type: str = "stock") -> float:
    return _slippage_pct(asset_type)


def spread_for(asset_type: str = "stock", underdog: bool = False) -> float:
    return fallback_spread_pct(asset_type, underdog)


def intrabar_exit(open_px: float, low: float, high: float, stop: float, take_profit: float):
    """Bestimmt einen Stop/TP-Exit aus OHLC-Daten.

    Wenn Stop und Take-Profit in derselben Kerze beruehrt werden und keine
    Tick-Reihenfolge vorliegt, wird konservativ der Stop zuerst angenommen.
    Ein Gap unter den Stop wird zum Open statt zum besseren Stoppreis gefuellt.
    """
    hit_stop=float(low) <= float(stop)
    hit_take=float(high) >= float(take_profit)
    if hit_stop:
        return "stop_loss", (min(float(stop), float(open_px)) if float(open_px) <= float(stop) else float(stop))
    if hit_take:
        return "take_profit", float(take_profit)
    return None, None

def _simulate(df_full: pd.DataFrame, test_start: int, test_end: int, model,
             starting_equity: float, capital_cap_pct: float,
             asset_type: str = "stock", currency: str = "USD"):
    """
    Kernsimulation auf einem Ausschnitt [test_start, test_end) eines bereits
    mit Indikatoren versehenen DataFrames. Wird sowohl von run_backtest() als
    auch von run_walkforward() genutzt, damit beide exakt dieselbe
    Handelslogik verwenden.

    Gibt None zurueck, wenn das Fenster zu klein fuer eine sinnvolle
    Auswertung ist.
    """
    df = df_full.iloc[test_start:test_end].reset_index(drop=True)
    if len(df) < 50:
        return None

    ml_probs = (predict_up_probability_batch(model, df)
                if config.USE_ML_FILTER else np.full(len(df), 0.5))

    equity = starting_equity
    position = 0
    entry_price = entry_index = stop_loss = take_profit = None
    entry_fees = 0.0
    trades = []
    equity_curve = []
    total_commission = 0.0
    total_spread_cost = 0.0
    total_slippage_cost = 0.0
    total_regulatory_cost = 0.0

    for i in range(1, len(df)):
        curr = df.iloc[i]
        price = float(curr["close"])

        if position > 0:
            low = float(curr.get("low", price))
            high = float(curr.get("high", price))
            open_px = float(curr.get("open", price))
            intrabar_reason, intrabar_price = intrabar_exit(open_px, low, high, stop_loss, take_profit)
            should_exit, exit_reason = exit_conditions(df, i)

            if intrabar_reason or should_exit:
                if intrabar_reason:
                    reason = intrabar_reason
                    trigger_price = float(intrabar_price)
                else:
                    reason = exit_reason
                    trigger_price = price
                sp = spread_for(asset_type)
                slip = slippage_for(asset_type)
                fill = trigger_price * (1 - slip - sp / 2.0)
                gross = (fill - entry_price) * position
                cost = commission_for(position, fill, asset_type, currency)
                regulatory = regulatory_cost_for(position, fill, asset_type, currency, side="sell")
                total_commission += cost
                total_regulatory_cost += regulatory
                total_spread_cost += price * position * (sp / 2.0)
                total_slippage_cost += price * position * slip
                # Entry-Kommission/Regulatorik wurde beim Kauf bereits vom Konto
                # abgezogen. Fuer Equity darf sie hier NICHT erneut abgezogen werden.
                # Fuer Trade-Statistiken muss sie aber in den Netto-P&L hinein, sonst
                # wuerden kleine Trades faelschlich als Gewinner zaehlen.
                cash_pnl = gross - cost - regulatory
                trade_pnl = cash_pnl - entry_fees

                equity += cash_pnl
                trades.append({
                    "entry_index": entry_index, "exit_index": i,
                    "entry_price": entry_price, "exit_price": fill,
                    "qty": position, "pnl": trade_pnl, "reason": reason,
                    "bars_held": i - entry_index,
                    "capital": entry_price * position,
                    "entry_fees": entry_fees, "exit_fees": cost + regulatory,
                })
                position = 0
                entry_price = entry_index = stop_loss = take_profit = None
                entry_fees = 0.0

        if position == 0:
            should_enter, _ = entry_conditions(df, i, ml_probs[i])
            if should_enter:
                sp = spread_for(asset_type)
                slip = slippage_for(asset_type)
                fill = price * (1 + slip + sp / 2.0)
                candidate_stop, candidate_take = calculate_stop_take(
                    fill, "BUY", atr_value=curr.get("atr"), asset_type=asset_type
                )
                qty = size_new_position(equity, fill, candidate_stop,
                                        max_capital_pct=capital_cap_pct, asset_type=asset_type)
                if qty > 0:
                    cost = commission_for(qty, fill, asset_type, currency)
                    regulatory = regulatory_cost_for(qty, fill, asset_type, currency, side="buy")
                    total_commission += cost
                    total_regulatory_cost += regulatory
                    total_spread_cost += price * qty * (sp / 2.0)
                    total_slippage_cost += price * qty * slip
                    entry_fees = cost + regulatory
                    equity -= entry_fees
                    position = qty
                    entry_price = fill
                    entry_index = i
                    stop_loss, take_profit = candidate_stop, candidate_take

        unrealized = position * (price - entry_price) if position else 0.0
        equity_curve.append(equity + unrealized)

    if position > 0:
        final_price = float(df.iloc[-1]["close"])
        sp = spread_for(asset_type)
        slip = slippage_for(asset_type)
        fill = final_price * (1 - slip - sp / 2.0)
        gross = (fill - entry_price) * position
        cost = commission_for(position, fill, asset_type, currency)
        regulatory = regulatory_cost_for(position, fill, asset_type, currency, side="sell")
        total_commission += cost
        total_regulatory_cost += regulatory
        total_spread_cost += final_price * position * (sp / 2.0)
        total_slippage_cost += final_price * position * slip
        cash_pnl = gross - cost - regulatory
        trade_pnl = cash_pnl - entry_fees
        equity += cash_pnl
        trades.append({
            "entry_index": entry_index, "exit_index": len(df)-1,
            "entry_price": entry_price, "exit_price": fill,
            "qty": position, "pnl": trade_pnl, "reason": "end_of_test",
            "bars_held": len(df)-1-entry_index, "capital": entry_price*position,
            "entry_fees": entry_fees, "exit_fees": cost + regulatory,
        })
        equity_curve.append(equity)

    first_price=float(df.iloc[0]["close"]); last_price=float(df.iloc[-1]["close"])
    buy_hold_pct = (last_price / first_price - 1) * 100
    # Fairer Vergleich: Buy&Hold bekommt ebenfalls EINEN realistischen Roundtrip.
    # (Die exakte Exit-Kommission kann sich mit dem Kurs aendern; die zentrale
    # Cost Engine liefert hier eine konservative Naeherung auf Startnotional.)
    bh_qty=max(starting_equity/max(first_price,1e-12),1e-8)
    bh_cost=estimate_roundtrip(bh_qty,first_price,asset_type,currency)
    buy_hold_net_pct=buy_hold_pct - bh_cost.total_cost_pct*100
    return trades, equity_curve, buy_hold_pct, len(df), {
        "commission": total_commission, "regulatory": total_regulatory_cost,
        "spread": total_spread_cost, "slippage": total_slippage_cost,
        "buy_hold_net_pct": buy_hold_net_pct
    }


def _prepare_full(raw_df: pd.DataFrame) -> pd.DataFrame:
    df_full = prepare(raw_df)
    if df_full.empty:
        raise ValueError("Nach Indikator-Berechnung keine verwertbaren Daten uebrig.")
    return df_full


def run_backtest(raw_df: pd.DataFrame, model=None, starting_equity: float = 10_000.0,
                 train_ml_inline: bool = True, verbose: bool = True,
                 asset_type: str = "stock", currency: str = "USD"):
    """
    Einzelner Train/Test-Split: Modell (falls aktiviert) auf der ersten
    Haelfte trainiert, Auswertung ausschliesslich auf der zweiten Haelfte.

    Fuer eine belastbarere Einschaetzung: run_walkforward() nutzen, das
    denselben Mechanismus mehrfach auf verschiedenen Zeitabschnitten anwendet.
    """
    df_full = _prepare_full(raw_df)
    split_idx = len(raw_df) // 2

    if model is None and train_ml_inline and config.USE_ML_FILTER:
        try:
            if verbose:
                print("Trainiere Modell auf erster Haelfte der Daten ...\n")
            model, _ = train_model(
                raw_df.iloc[:split_idx],
                model_path="backtest_model_tmp.joblib",
                verbose=verbose,
            )
            if verbose:
                print()
        except ValueError as exc:
            if verbose:
                print(f"Modelltraining uebersprungen: {exc}\n")
            model = None

    cutoff_pos = max(1, int(len(df_full) * 0.5))
    capital_cap = getattr(config, "BACKTEST_POSITION_PCT", config.MAX_POSITION_PCT)

    result = _simulate(df_full, cutoff_pos, len(df_full), model, starting_equity,
                       capital_cap, asset_type, currency)
    if result is None:
        raise ValueError("Zu wenig Out-of-Sample-Daten fuer einen Backtest.")

    trades, equity_curve, buy_hold_pct, n_bars, costs = result
    return summarize_backtest(trades, equity_curve, starting_equity,
                              buy_hold_pct, n_bars, verbose, costs=costs)


def run_walkforward(raw_df: pd.DataFrame, n_folds: int = 4,
                    starting_equity: float = 10_000.0, verbose: bool = True,
                    asset_type: str = "stock", currency: str = "USD"):
    """
    Teilt die Zeitreihe in n_folds aufeinanderfolgende Testabschnitte.
    Fuer jeden Abschnitt wird das Modell (falls aktiviert) NUR auf den davor
    liegenden Daten trainiert -- rollierend, kein Blick in die Zukunft.

    Das beantwortet die eigentlich wichtige Frage: Funktioniert die Strategie
    ueber verschiedene Marktphasen hinweg, oder war ein einzelner guter
    Backtest nur ein Zufallstreffer in einer guenstigen Periode?
    """
    if n_folds < 2:
        raise ValueError("Mindestens 2 Folds noetig.")

    df_full = _prepare_full(raw_df)
    n = len(df_full)

    # Erstes Fenster braucht genug Historie zum Trainieren/fuer Indikatoren;
    # wir beginnen bei 40% der Daten und verteilen den Rest gleichmaessig.
    warmup = int(n * 0.4)
    remaining = n - warmup
    fold_size = remaining // n_folds

    if fold_size < 60:
        raise ValueError(
            f"Zu wenig Daten fuer {n_folds} Folds (nur {fold_size} Balken/Fold). "
            "Weniger Folds waehlen oder mehr Historie laden (period='5y' o.ae.)."
        )

    capital_cap = getattr(config, "BACKTEST_POSITION_PCT", config.MAX_POSITION_PCT)
    fold_rows = []

    for k in range(n_folds):
        test_start = warmup + k * fold_size
        test_end = n if k == n_folds - 1 else warmup + (k + 1) * fold_size

        model = None
        if config.USE_ML_FILTER:
            # Rohdaten-Schnittpunkt proportional zur Position in df_full
            # schaetzen (df_full hat durch Indikator-Warmup etwas weniger
            # Zeilen als raw_df, die Abweichung ist fuer diesen Zweck
            # vernachlaessigbar).
            raw_cut = max(200, int(len(raw_df) * (test_start / n)))
            try:
                model, _ = train_model(
                    raw_df.iloc[:raw_cut],
                    model_path=f"walkforward_model_fold{k}.joblib",
                    verbose=False,
                )
            except ValueError:
                model = None

        result = _simulate(df_full, test_start, test_end, model, starting_equity,
                           capital_cap, asset_type, currency)
        if result is None:
            continue

        trades, equity_curve, buy_hold_pct, n_bars, costs = result
        summary, _, _ = summarize_backtest(
            trades, equity_curve, starting_equity, buy_hold_pct, n_bars, verbose=False, costs=costs
        )
        summary["fold"] = k + 1
        fold_rows.append(summary)

        if verbose:
            beats = "OK " if summary["total_return_pct"] > summary["buy_hold_return_pct"] else "--"
            print(f"Fold {k + 1}/{n_folds} [{beats}]: {summary['trades_total']:3d} Trades, "
                  f"Rendite {summary['total_return_pct']:+7.2f}% "
                  f"(B&H {summary['buy_hold_return_pct']:+7.2f}%), "
                  f"PF {summary['profit_factor']}")

    if not fold_rows:
        if verbose:
            print("Keine auswertbaren Folds -- Daten oder Fold-Anzahl pruefen.")
        return None

    table = pd.DataFrame(fold_rows)
    beats_bh = int((table["total_return_pct"] > table["buy_hold_return_pct"]).sum())

    if verbose:
        print()
        print("=" * 70)
        print("WALK-FORWARD ZUSAMMENFASSUNG")
        print("=" * 70)
        print(f"Folds insgesamt:                {len(table)}")
        print(f"Davon besser als Buy&Hold:       {beats_bh} von {len(table)}")
        print(f"Trades pro Fold (Durchschnitt):  {table['trades_total'].mean():.1f}")
        print(f"Rendite Durchschnitt:            {table['total_return_pct'].mean():+.2f}%")
        print(f"Rendite Streuung (Std-Abw.):     {table['total_return_pct'].std():.2f}")
        print()

        if beats_bh <= len(table) // 2:
            print("! In der Mehrheit der Zeitabschnitte schlaegt die Strategie NICHT")
            print("  Buy&Hold. Das ist ein deutliches Warnsignal -- kein Ausreisser,")
            print("  sondern ueber mehrere unabhaengige Perioden hinweg bestaetigt.")
        else:
            print("+ Die Strategie schlaegt Buy&Hold in der Mehrheit der Abschnitte.")
            print("  Das ist ein ECHTES Indiz (mehrere unabhaengige Perioden), aber")
            print("  immer noch kein Beweis. Naechster Schritt: dasselbe auf ein")
            print("  paar andere Symbole anwenden (siehe sweep.py), bevor du dem")
            print("  Ergebnis Kapital anvertraust.")

    return table


def summarize_backtest(trades, equity_curve, starting_equity,
                       buy_hold_pct, n_bars, verbose=True, costs=None):
    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]

    final_equity = equity_curve[-1] if equity_curve else starting_equity
    total_return_pct = (final_equity / starting_equity - 1) * 100

    equity_series = pd.Series(equity_curve) if equity_curve else pd.Series([starting_equity])
    running_max = equity_series.cummax()
    drawdown = (equity_series - running_max) / running_max
    max_drawdown_pct = drawdown.min() * 100 if len(drawdown) else 0.0

    win_rate = (len(wins) / len(trades) * 100) if trades else 0.0
    avg_win = np.mean([t["pnl"] for t in wins]) if wins else 0.0
    avg_loss = np.mean([t["pnl"] for t in losses]) if losses else 0.0
    total_win = sum(t["pnl"] for t in wins)
    total_loss = abs(sum(t["pnl"] for t in losses))
    profit_factor = (total_win / total_loss) if total_loss > 0 else float("inf") if total_win > 0 else 0.0

    bars_in_market = sum(t.get("bars_held", 0) for t in trades)
    time_in_market_pct = (bars_in_market / n_bars * 100) if n_bars else 0.0
    avg_capital = np.mean([t.get("capital", 0) for t in trades]) if trades else 0.0

    costs = costs or {"commission":0.0,"regulatory":0.0,"spread":0.0,"slippage":0.0}
    total_costs = sum(float(costs.get(k,0) or 0) for k in ("commission","regulatory","spread","slippage"))
    net_result_abs = final_equity - starting_equity
    gross_result_before_costs = net_result_abs + total_costs
    turnover = sum(float(t.get("capital",0) or 0) for t in trades) / starting_equity if starting_equity>0 else 0.0
    avg_bars_held = float(np.mean([t.get("bars_held",0) for t in trades])) if trades else 0.0

    summary = {
        "scope": "Machbarkeitsstudie, keine Live-Ertragsprognose",
        "trades_total": len(trades),
        "win_rate_pct": round(win_rate, 1),
        "profit_factor": round(profit_factor, 2) if np.isfinite(profit_factor) else "inf",
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "total_return_pct": round(total_return_pct, 2),
        "buy_hold_return_pct": round(buy_hold_pct, 2),
        "buy_hold_net_return_pct": round(float(costs.get("buy_hold_net_pct",buy_hold_pct)),2),
        "gross_return_before_costs_pct": round(gross_result_before_costs/starting_equity*100,2) if starting_equity>0 else 0.0,
        "max_drawdown_pct": round(max_drawdown_pct, 2),
        "final_equity": round(final_equity, 2),
        "time_in_market_pct": round(time_in_market_pct, 1),
        "avg_capital_per_trade": round(avg_capital, 2),
        "bars_tested": n_bars,
        "avg_bars_held": round(avg_bars_held,1),
        "turnover_x": round(turnover,2),
        "commission_cost": round(float(costs.get("commission",0)),2),
        "regulatory_cost": round(float(costs.get("regulatory",0)),2),
        "spread_cost": round(float(costs.get("spread",0)),2),
        "slippage_cost": round(float(costs.get("slippage",0)),2),
        "total_trading_cost": round(total_costs,2),
        "cost_share_of_gross_pct": round(total_costs/gross_result_before_costs*100,1) if gross_result_before_costs>0 else 0.0,
    }

    if verbose:
        print("=== Backtest-Machbarkeitsstudie (nur Out-of-Sample) ===")
        for k, v in summary.items():
            print(f"{k:22s}: {v}")
        print()
        print(interpret(summary))

    return summary, equity_curve, trades


def interpret(s) -> str:
    """Klartext-Einordnung statt blosser Zahlen."""
    lines = []

    if s["trades_total"] < 20:
        lines.append(
            f"! Nur {s['trades_total']} Trades -- statistisch NICHT aussagekraeftig. "
            "Mindestens 30-50 Trades noetig, um Zufall von Koennen zu trennen."
        )

    bh_net=s.get("buy_hold_net_return_pct",s["buy_hold_return_pct"])
    if s["total_return_pct"] < bh_net:
        lines.append(
            f"! Die Strategie netto ({s['total_return_pct']}%) liegt UNTER kostenbereinigtem "
            f"Kaufen-und-Halten ({bh_net}%; brutto {s['buy_hold_return_pct']}%). "
            "Dann waere Nichtstun in diesem Zeitraum besser gewesen."
        )
    else:
        lines.append(
            f"+ Die Strategie netto ({s['total_return_pct']}%) schlaegt kostenbereinigtes "
            f"Kaufen-und-Halten ({bh_net}%; brutto {s['buy_hold_return_pct']}%)."
        )

    pf = s["profit_factor"]
    if isinstance(pf, (int, float)):
        if pf < 1.0:
            lines.append(f"! Profit-Faktor {pf} < 1.0 -- die Verluste uebersteigen die Gewinne.")
        elif pf < 1.3:
            lines.append(f"~ Profit-Faktor {pf} -- knapp positiv, aber ohne Sicherheitspuffer.")
        else:
            lines.append(f"+ Profit-Faktor {pf} -- die Gewinne uebersteigen die Verluste deutlich.")

    if s.get("time_in_market_pct", 0) < 5:
        lines.append(
            f"~ Nur {s['time_in_market_pct']}% der Zeit im Markt. Die Strategie steht "
            "fast immer an der Seitenlinie -- das begrenzt Verluste, aber eben auch "
            "jede Chance auf Ertrag."
        )

    lines.append(
        "Hinweis: Dieser Backtest ist eine Machbarkeitsstudie der technischen Grundregel. "
        "Historische eToro-Eligibility/What-if-Kosten sowie zeitpunktgenaue News-, Event- und Earnings-"
        "Filter sind nicht reproduziert. Ein gutes Ergebnis ist deshalb keine Live-Ertragsprognose. "
        "Spread, Slippage, risikobasierte Groesse und Intrabar-Stop/TP sind modelliert."
    )
    return "\n".join(lines)
