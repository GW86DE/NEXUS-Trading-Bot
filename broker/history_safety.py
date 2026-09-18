"""Gemeinsame Sicherheitshelfer fuer Broker-Historien.

Ziele:
- niemals eine noch laufende Kerze an die Strategie weiterreichen, wenn
  USE_COMPLETED_BAR_ONLY aktiv ist;
- kurze Datenfehler kontrolliert wiederholen statt ein Instrument sofort fuer
  den gesamten Scanner-Zyklus zu verlieren.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Callable, TypeVar

import pandas as pd
import config

logger = logging.getLogger(__name__)
T = TypeVar("T")


def bar_seconds(bar_size: str) -> int | None:
    text = str(bar_size or "").strip().lower()
    parts = text.split()
    if len(parts) < 2:
        return None
    try:
        n = int(float(parts[0]))
    except Exception:
        return None
    unit = parts[1]
    if unit.startswith("sec"):
        return n
    if unit.startswith("min"):
        return n * 60
    if unit.startswith("hour"):
        return n * 3600
    if unit.startswith("day"):
        return n * 86400
    return None


def completed_bars_only(df: pd.DataFrame, bar_size: str, *, now=None) -> pd.DataFrame:
    """Entfernt nur die letzte Kerze, wenn sie noch laufen kann.

    Bei timezone-bewussten Zeitstempeln wird die Kerzendauer gegen UTC
    geprueft. Bei nicht eindeutig interpretierbaren/naiven Zeitstempeln gilt
    bewusst die sichere Seite: die letzte Zeile wird verworfen. So kann ein
    laufender Balken nie ein Live-Signal erzeugen.
    """
    if df is None or len(df) <= 1 or not bool(getattr(config, "USE_COMPLETED_BAR_ONLY", True)):
        return df
    seconds = bar_seconds(bar_size)
    if not seconds:
        return df.iloc[:-1].copy()
    try:
        ts = pd.Timestamp(df.index[-1])
        if ts.tzinfo is None:
            return df.iloc[:-1].copy()
        now_ts = pd.Timestamp(now or datetime.now(timezone.utc))
        if now_ts.tzinfo is None:
            now_ts = now_ts.tz_localize("UTC")
        else:
            now_ts = now_ts.tz_convert("UTC")
        end_ts = ts.tz_convert("UTC") + pd.Timedelta(seconds=seconds)
        # kleine Toleranz fuer Provider-Zeitstempel / Netzlaufzeit
        if end_ts > now_ts - pd.Timedelta(seconds=2):
            return df.iloc[:-1].copy()
        return df
    except Exception as exc:
        logger.debug("Kerzenabschluss konnte nicht sicher bestimmt werden: %s", exc)
        return df.iloc[:-1].copy()


def retry_call(func: Callable[[], T], *, label: str = "Datenabruf") -> T:
    attempts = max(1, int(getattr(config, "MAX_DATA_RETRIES", 3)))
    base_delay = max(0.0, float(getattr(config, "DATA_RETRY_DELAY_SECONDS", 1.0)))
    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return func()
        except Exception as exc:
            last_exc = exc
            if attempt >= attempts:
                break
            delay = min(8.0, base_delay * (2 ** (attempt - 1)))
            logger.warning("%s fehlgeschlagen (%d/%d): %s; neuer Versuch in %.1fs", label, attempt, attempts, exc, delay)
            if delay:
                time.sleep(delay)
    assert last_exc is not None
    raise last_exc
