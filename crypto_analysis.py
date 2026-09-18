"""Orderfreie OKX-Backtests fuer den NEXUS-Freqtrade-Modus.

Die Analyse verwendet ausschliesslich oeffentliche, abgeschlossene 5-Minuten-
Kerzen. Sie liest weder Kontodaten noch Zugangsschluessel und besitzt keinen
Orderpfad. Die Live- und Backtestregeln stammen aus demselben
``freqtrade_sample_strategy``-Modul.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json
import math
import os

import pandas as pd

import config
from broker.okx import OKXClient
from freqtrade_sample_backtest import run_backtest
from freqtrade_sample_strategy import STARTUP_CANDLES, parameter_snapshot
from safe_persistence import atomic_write_json
from state_lock import critical_state_lock


ROOT = Path(__file__).resolve().parent
REQUIRED_COLUMNS = ("open", "high", "low", "close", "volume")


def _runtime_dir() -> Path:
    test = str(os.getenv("TRADINGBOT_TEST_STATE_DIR", "") or "").strip()
    return Path(test) if test else ROOT / "runtime"


def _public_client() -> OKXClient:
    return OKXClient(
        api_key="", api_secret="", passphrase="",
        demo=False,
        base_url=str(getattr(config, "OKX_BASE_URL", "https://eea.okx.com")),
    )


def _clean(frame: pd.DataFrame) -> pd.DataFrame:
    if frame is None or not isinstance(frame, pd.DataFrame) or frame.empty:
        return pd.DataFrame(columns=list(REQUIRED_COLUMNS))
    out = frame.copy()
    if not isinstance(out.index, pd.DatetimeIndex):
        out.index = pd.to_datetime(out.index, utc=True, errors="coerce")
    elif out.index.tz is None:
        out.index = out.index.tz_localize("UTC")
    else:
        out.index = out.index.tz_convert("UTC")
    for column in REQUIRED_COLUMNS:
        if column not in out:
            return pd.DataFrame(columns=list(REQUIRED_COLUMNS))
        out[column] = pd.to_numeric(out[column], errors="coerce")
    out = out.dropna(subset=list(REQUIRED_COLUMNS))
    out = out[(out["open"] > 0) & (out["high"] > 0) &
              (out["low"] > 0) & (out["close"] > 0) &
              (out["volume"] >= 0)]
    return out[~out.index.duplicated(keep="last")].sort_index()


def _cache_path(inst_id: str) -> Path:
    safe = "".join(c for c in str(inst_id).upper() if c.isalnum() or c in "-_")
    return _runtime_dir() / "analysis_data" / f"okx_{safe}_5m.csv"


def _read_cache(inst_id: str) -> pd.DataFrame:
    path = _cache_path(inst_id)
    if not path.exists():
        return pd.DataFrame(columns=list(REQUIRED_COLUMNS))
    try:
        frame = pd.read_csv(path, index_col="date", parse_dates=["date"])
        return _clean(frame)
    except Exception:
        return pd.DataFrame(columns=list(REQUIRED_COLUMNS))


def _write_cache(inst_id: str, frame: pd.DataFrame) -> None:
    path = _cache_path(inst_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with critical_state_lock(path):
        tmp = path.with_name(f".{path.name}.tmp")
        out = frame.copy()
        out.index.name = "date"
        out.to_csv(tmp)
        os.replace(tmp, path)


def resolve_instruments(client: OKXClient | None = None,
                        symbols=None) -> list[str]:
    """Je Basiswert genau einen aktuell live gehandelten Spotmarkt waehlen."""
    client = client or _public_client()
    catalog = client.public_instruments()
    bases = tuple(symbols or getattr(
        config, "CRYPTO_ANALYSIS_SYMBOLS", ("BTC", "ETH", "LINK")))
    primary = str(getattr(config, "OKX_PRIMARY_QUOTE_CCY", "EUR")).upper()
    quotes = tuple(dict.fromkeys((primary, "USDC", "USD", "EUR", "USDT")))
    selected: list[str] = []
    for raw in bases:
        base = str(raw).upper().strip()
        candidates = [inst for inst in catalog.values()
                      if inst.base_ccy == base and inst.ist_live]
        chosen = next((x for quote in quotes for x in candidates
                       if x.quote_ccy == quote), None)
        if chosen is not None:
            selected.append(chosen.inst_id)
    return list(dict.fromkeys(selected))


def load_candles(inst_id: str, *, candles: int | None = None,
                 client: OKXClient | None = None) -> pd.DataFrame:
    """Neueste Kerzen laden und einen vorhandenen Cache nur nach hinten fuellen."""
    client = client or _public_client()
    target = max(300, int(candles or getattr(
        config, "CRYPTO_ANALYSIS_CANDLES", 3000)))
    cached = _read_cache(inst_id)
    newest = _clean(client.candles(inst_id, bar="5m", limit=300,
                                   nur_abgeschlossen=True))
    frame = _clean(pd.concat([cached, newest]))
    if frame.empty:
        raise RuntimeError(f"OKX liefert keine abgeschlossenen 5m-Kerzen fuer {inst_id}")

    seen_oldest: set[int] = set()
    # 100 ist die dokumentierte Obergrenze des History-Candle-Endpunkts.
    max_pages = max(1, int(math.ceil(max(0, target - len(frame)) / 100.0)) + 2)
    for _ in range(max_pages):
        if len(frame) >= target:
            break
        oldest_ms = int(frame.index.min().timestamp() * 1000) - 1
        if oldest_ms in seen_oldest:
            break
        seen_oldest.add(oldest_ms)
        page = _clean(client.historical_candles(
            inst_id, bar="5m", limit=100, end_ms=oldest_ms,
            nur_abgeschlossen=True))
        if page.empty:
            break
        before = len(frame)
        frame = _clean(pd.concat([page, frame]))
        if len(frame) == before:
            break
    frame = frame.iloc[-target:]
    if len(frame) < STARTUP_CANDLES + 2:
        raise RuntimeError(
            f"{inst_id}: nur {len(frame)} statt mindestens "
            f"{STARTUP_CANDLES + 2} abgeschlossene Kerzen")
    _write_cache(inst_id, frame)
    return frame


def _compact(result: dict) -> dict:
    keys = ("candles", "trades", "wins", "losses", "win_rate_pct",
            "initial_capital", "final_capital", "net_pnl", "return_pct",
            "max_drawdown_pct", "profit_factor", "realized_max_drawdown_pct",
            "equity_max_drawdown_pct", "result_schema_version", "input_digest",
            "synthetic_gap_candles")
    return {**{key: result.get(key) for key in keys},
            "assumptions": dict(result.get("assumptions") or {})}


def _run_sample(frame: pd.DataFrame) -> dict:
    """Mit denselben konservativen OKX-Kostenannahmen wie der Live-Kern."""
    return run_backtest(
        frame,
        fee_pct=max(0.0, float(getattr(config, "OKX_TAKER_FEE_PCT", 0.0035))),
        slippage_pct=max(0.0, float(getattr(config, "CRYPTO_SLIPPAGE_PCT", 0.0015))),
    )


def crypto_backtest(*, client: OKXClient | None = None,
                    instruments=None, candles: int | None = None) -> dict:
    client = client or _public_client()
    selected = list(instruments or resolve_instruments(client))
    if not selected:
        raise RuntimeError("Kein passender oeffentlicher OKX-Spotmarkt gefunden")
    results = {}
    for inst_id in selected:
        frame = load_candles(inst_id, candles=candles, client=client)
        results[inst_id] = _compact(_run_sample(frame))
    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "analysis": "OKX Freqtrade SampleStrategy Backtest",
        "data_rights": "public-market-data-only",
        "strategy": parameter_snapshot(),
        "results": results,
    }
    result_dir = _runtime_dir() / "analysis_results"
    result_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_json(result_dir / "crypto_freqtrade_backtest.json", payload)
    return payload


def _folds(frame: pd.DataFrame, count: int) -> list[tuple[int, int, pd.DataFrame]]:
    """Expanding-window-Zeitfenster; nur der nachfolgende Abschnitt wird bewertet."""
    count = max(2, int(count))
    minimum_training = max(300, len(frame) // 3)
    available = len(frame) - minimum_training
    fold_size = available // count
    if fold_size < STARTUP_CANDLES + 20:
        raise ValueError("Zu wenige Kerzen fuer eine belastbare Walk-Forward-Aufteilung")
    out = []
    for index in range(count):
        test_start = minimum_training + index * fold_size
        test_end = len(frame) if index == count - 1 else test_start + fold_size
        # Exakt STARTUP_CANDLES alte Zeilen dienen nur dem Indikator-Warmup.
        # run_backtest beginnt danach; kein Trade aus dem Trainingsfenster
        # kann in den Testabschnitt hineinragen.
        test = frame.iloc[test_start - STARTUP_CANDLES:test_end].copy()
        out.append((test_start, test_end, test))
    return out


def crypto_walkforward(*, client: OKXClient | None = None,
                       instruments=None, candles: int | None = None,
                       folds: int | None = None) -> dict:
    client = client or _public_client()
    selected = list(instruments or resolve_instruments(client))
    if not selected:
        raise RuntimeError("Kein passender oeffentlicher OKX-Spotmarkt gefunden")
    fold_count = int(folds or getattr(config, "CRYPTO_ANALYSIS_FOLDS", 4))
    results = {}
    for inst_id in selected:
        frame = load_candles(inst_id, candles=candles, client=client)
        rows = []
        for number, (test_start, test_end, test) in enumerate(
                _folds(frame, fold_count), start=1):
            result = _compact(_run_sample(test))
            result.update({
                "fold": number,
                "training_rows_before_test": test_start,
                "test_start": frame.index[test_start].isoformat(),
                "test_end": frame.index[test_end - 1].isoformat(),
                "test_rows": test_end - test_start,
            })
            rows.append(result)
        returns = [float(x.get("return_pct") or 0.0) for x in rows]
        results[inst_id] = {
            "folds": rows,
            "positive_folds": sum(1 for value in returns if value > 0),
            "total_folds": len(rows),
            "average_return_pct": sum(returns) / len(returns) if returns else 0.0,
            "worst_fold_return_pct": min(returns) if returns else 0.0,
            "total_trades": sum(int(x.get("trades") or 0) for x in rows),
        }
    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "analysis": "OKX Freqtrade SampleStrategy Walk-Forward",
        "method": ("expanding chronology; fixed official parameters; "
                   "each test fold uses only preceding candles for warmup"),
        "data_rights": "public-market-data-only",
        "strategy": parameter_snapshot(),
        "results": results,
    }
    result_dir = _runtime_dir() / "analysis_results"
    result_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_json(result_dir / "crypto_freqtrade_walkforward.json", payload)
    return payload


def print_report(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


__all__ = [
    "crypto_backtest", "crypto_walkforward", "load_candles",
    "print_report", "resolve_instruments",
]
