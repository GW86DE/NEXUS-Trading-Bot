"""Portfolio-Schutz gegen Branchen- und Korrelations-Klumpenrisiko.

Der Guard verhindert, dass mehrere scheinbar unterschiedliche Positionen in
Wahrheit denselben Risikofaktor abbilden (z.B. mehrere stark korrelierte
Halbleiterwerte). Er nutzt nur bereits geladene Kursserien des aktuellen
Zyklus; faellt eine Serie aus, wird NICHT aus dem Nichts eine Korrelation
behauptet.
"""
from __future__ import annotations

import math
import pandas as pd
import config


def _price_lookup(last_price_by_id, key, fallback=0.0):
    """Broker-IDs koennen numerisch oder als Zeichenkette geliefert werden."""
    candidates = [key, str(key)]
    if str(key).isdigit():
        try: candidates.append(int(str(key)))
        except Exception: __import__("logging").getLogger(__name__).debug("Best-effort-Ausnahme unterdrueckt", exc_info=True)
    for cand in candidates:
        try:
            v = float(last_price_by_id.get(cand, 0) or 0)
            if math.isfinite(v) and v > 0:
                return v
        except Exception:
            __import__("logging").getLogger(__name__).debug("Best-effort-Ausnahme unterdrueckt", exc_info=True)
    try:
        v = float(fallback or 0)
        return v if math.isfinite(v) and v > 0 else 0.0
    except Exception:
        return 0.0


def candidate_sector_guard(candidate, current_positions, last_price_by_id, equity):
    sector = (getattr(candidate, "sector", "") or "UNBEKANNT").lower()
    count = 0
    value = 0.0
    for key, (_contract, qty, avg, inst) in current_positions.items():
        if not inst or qty <= 0:
            continue
        if (getattr(inst, "sector", "") or "UNBEKANNT").lower() != sector:
            continue
        count += 1
        px = _price_lookup(last_price_by_id, key, avg)
        value += abs(float(qty)) * px

    max_count = int(getattr(config, "MAX_SECTOR_POSITIONS", 3))
    max_pct = float(getattr(config, "MAX_SECTOR_EXPOSURE_PCT", 0.25))
    if count >= max_count:
        return False, f"Branche {sector}: bereits {count} Positionen (Limit {max_count})"
    if equity > 0 and value / equity >= max_pct:
        return False, (f"Branche {sector}: Exposure {value/equity*100:.1f}% "
                       f"(Limit {max_pct*100:.0f}%)")
    return True, "ok"


def _returns(df, lookback):
    if df is None or len(df) < 32 or "close" not in df.columns:
        return None
    try:
        close = pd.to_numeric(df["close"], errors="coerce").dropna().tail(int(lookback) + 1)
        r = close.pct_change().replace([float("inf"), float("-inf")], pd.NA).dropna()
        if len(r) < 30:
            return None
        return r.astype(float)
    except Exception:
        return None


def correlation_value(candidate_df, other_df, lookback=None):
    """Absolute Renditekorrelation zweier Historien; None bei zu wenig Daten."""
    lookback = int(lookback or getattr(config, "CORRELATION_LOOKBACK_BARS", 120))
    a = _returns(candidate_df, lookback)
    b = _returns(other_df, lookback)
    if a is None or b is None:
        return None
    try:
        # Wenn beide Datenquellen Zeitindizes tragen, nach Zeit ausrichten.
        joined = pd.concat([a.rename("a"), b.rename("b")], axis=1, join="inner").dropna()
        # Bei unterschiedlichen Indexformaten konservativ
        # die letzten gleich vielen Renditen vergleichen.
        if len(joined) < 30:
            n = min(len(a), len(b), lookback)
            if n < 30:
                return None
            joined = pd.DataFrame({"a": a.iloc[-n:].to_numpy(), "b": b.iloc[-n:].to_numpy()})
        corr = float(joined["a"].corr(joined["b"]))
        if not math.isfinite(corr):
            return None
        return abs(corr)
    except Exception:
        return None


def candidate_correlation_guard(candidate, candidate_df, current_positions, position_histories):
    """Blockiert einen Kandidaten bei zu vielen stark korrelierten Positionen.

    Rueckgabe: ``(ok, begruendung)``. Fehlende Historien blockieren NICHT; sie
    werden nur nicht als Evidenz fuer oder gegen Korrelation verwendet.
    """
    if not bool(getattr(config, "CORRELATION_GUARD_ENABLED", True)):
        return True, "Korrelationsschutz deaktiviert"
    if getattr(candidate, "asset_type", "stock") != "stock":
        return True, "nur Aktien"

    threshold = float(getattr(config, "MAX_POSITION_CORRELATION", 0.85))
    max_high = int(getattr(config, "MAX_HIGHLY_CORRELATED_POSITIONS", 2))
    lookback = int(getattr(config, "CORRELATION_LOOKBACK_BARS", 120))
    hits = []

    for _key, (_contract, qty, _avg, inst) in current_positions.items():
        if not inst or qty <= 0 or getattr(inst, "asset_type", "stock") != "stock":
            continue
        if str(getattr(inst, "name", "")).upper() == str(getattr(candidate, "name", "")).upper():
            continue
        hist = position_histories.get(str(getattr(inst, "name", "")).upper())
        corr = correlation_value(candidate_df, hist, lookback)
        if corr is not None and corr >= threshold:
            hits.append((str(getattr(inst, "name", "?")), corr))

    hits.sort(key=lambda x: x[1], reverse=True)
    if len(hits) >= max_high:
        detail = ", ".join(f"{s} {c:.2f}" for s, c in hits[:4])
        return False, (f"Korrelationslimit: {len(hits)} offene Position(en) >= {threshold:.2f} "
                       f"({detail}); Limit {max_high-1} vor Neueinstieg")
    if hits:
        detail = ", ".join(f"{s} {c:.2f}" for s, c in hits[:4])
        return True, f"Korrelation beobachtet, noch im Limit: {detail}"
    return True, "ok"
