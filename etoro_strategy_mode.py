"""Persistent runtime switch for eToro stock entry strategies (10.2.0).

Gleiche Politik wie ``crypto_strategy_mode``: Der Schalter wirkt nur auf NEUE
Aktien-Einstiege. Offene Positionen tragen ihren unveraenderlichen
Strategie-Snapshot im Trade-Ledger und werden niemals still migriert.

Abweichung zur Kryptoseite, bewusst: Es gibt keinen Pausenmodus (der Bot hat
dafuer den globalen Stopp), und eine fehlende oder beschaedigte Modusdatei
faellt auf NEXUS_STANDARD zurueck -- den seit Jahren gepruueften Standardpfad.
Ein Verlust der Datei kann so niemals still eine schaerfere Strategie
aktivieren, sondern hoechstens auf den konservativen Standard zurueckgehen;
der Rueckfall wird geloggt.
"""
from __future__ import annotations

from datetime import datetime, timezone
import logging
import os
from pathlib import Path
import threading

import config
from safe_persistence import atomic_write_json

NEXUS_STANDARD = "NEXUS_STANDARD"
ZUSATZ_MODES = frozenset({"RSI2_MEAN_REVERSION", "HIGH_52W_MOMENTUM", "GOLDEN_CROSS_TREND"})
VALID_MODES = frozenset({NEXUS_STANDARD}) | ZUSATZ_MODES
_LOCK = threading.RLock()
logger = logging.getLogger(__name__)


def _root() -> Path:
    return Path(os.getenv("TRADINGBOT_TEST_STATE_DIR", "").strip() or Path(__file__).resolve().parent)


def path() -> Path:
    return _root() / getattr(config, "ETORO_STRATEGY_MODE_FILE", "etoro_strategy_mode.json")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _default() -> dict:
    return {"schema": 1, "active_mode": NEXUS_STANDARD, "revision": 0,
            "updated_at_utc": "", "source": "default", "history": []}


def status() -> dict:
    with _LOCK:
        if not path().exists():
            return _default()
        try:
            import json
            data = json.loads(path().read_text(encoding="utf-8"))
            if not isinstance(data, dict) or str(data.get("active_mode")) not in VALID_MODES:
                raise ValueError("invalid strategy mode state")
            return {**_default(), **data}
        except Exception as exc:
            logger.warning("eToro-Strategiemodusdatei unlesbar (%s) -- "
                           "Rueckfall auf NEXUS Standard.", type(exc).__name__)
            return {**_default(),
                    "error": f"mode state unreadable: {type(exc).__name__}"}


def current_mode() -> str:
    return str(status().get("active_mode") or NEXUS_STANDARD)


def set_mode(mode: str, *, source: str, reason: str = "", notify: bool = True) -> dict:
    selected = str(mode or "").strip().upper()
    if selected not in VALID_MODES:
        raise ValueError(f"ungueltiger eToro-Strategiemodus: {selected}")
    with _LOCK:
        state = status()
        previous = str(state.get("active_mode") or NEXUS_STANDARD)
        revision = int(state.get("revision") or 0) + 1
        changed_at = _now()
        event = {"revision": revision, "changed_at_utc": changed_at,
                 "previous": previous, "mode": selected,
                 "source": str(source or "unknown")[:80],
                 "reason": str(reason or "")[:300]}
        history = list(state.get("history") or [])[-99:] + [event]
        state = {"schema": 1, "active_mode": selected, "revision": revision,
                 "updated_at_utc": changed_at, "source": event["source"],
                 "reason": event["reason"], "history": history}
        atomic_write_json(path(), state)
    if notify:
        try:
            from notifier import send_telegram
            if selected == NEXUS_STANDARD:
                label = "NEXUS Standard"
            else:
                from zusatz_strategien import STRATEGIEN
                label = STRATEGIEN[selected]["label"]
            send_telegram(
                f"🔁 eToro-Strategiemodus: {previous} → {label}. "
                "Der Wechsel gilt nur fuer neue Aktien-Einstiege; offene "
                "Positionen behalten ihre Einstiegsstrategie.",
                event_id=f"etoro-strategy-mode:{revision}:{changed_at}",
            )
        except Exception:
            logger.debug("Strategiemodus-Telegram nicht zustellbar", exc_info=True)
    return state


def entry_snapshot(mode: str | None = None) -> dict:
    selected = str(mode or current_mode()).upper()
    if selected in ZUSATZ_MODES:
        from zusatz_strategien import parameter_snapshot
        return parameter_snapshot(selected)
    if selected == NEXUS_STANDARD:
        try:
            import strategy_version
            version = strategy_version.aktuell()
        except Exception:
            version = ""
        return {
            "entry_strategy_mode": selected,
            "strategy_name": "NEXUS Standard Aktien",
            "strategy_version": str(version or "NEXUS-STANDARD-LEGACY"),
            "parameter_hash": "",
            "timeframe": str(getattr(config, "BAR_SIZE", "1 hour")),
            "parameters": {"entry_mode": str(getattr(config, "ENTRY_MODE", "trend"))},
        }
    raise ValueError(f"unbekannter eToro-Strategiemodus: {selected}")


__all__ = [
    "NEXUS_STANDARD", "VALID_MODES", "ZUSATZ_MODES",
    "current_mode", "entry_snapshot", "path", "set_mode", "status",
]
