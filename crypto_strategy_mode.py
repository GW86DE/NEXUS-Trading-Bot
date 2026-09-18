"""Persistent runtime switch for OKX entry strategies.

The switch affects new entries only. Open positions carry their own immutable
strategy snapshot and are never silently migrated when the global mode changes.
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
FREQTRADE_SAMPLE = "FREQTRADE_SAMPLE"
CRYPTO_PAUSED = "CRYPTO_PAUSED"
# 10.2.0: drei veroeffentlichte Tagesstrategien (zusatz_strategien.py). Sie
# nutzen dieselbe Schalter-, Snapshot- und Migrationsmechanik wie Freqtrade.
ZUSATZ_MODES = frozenset({"TSMOM_LONG_FLAT", "KELTNER_BREAKOUT", "MACD_TREND_CRYPTO"})
VALID_MODES = frozenset({NEXUS_STANDARD, FREQTRADE_SAMPLE, CRYPTO_PAUSED}) | ZUSATZ_MODES
_LOCK = threading.RLock()
logger = logging.getLogger(__name__)


def _root() -> Path:
    return Path(os.getenv("TRADINGBOT_TEST_STATE_DIR", "").strip() or Path(__file__).resolve().parent)


def path() -> Path:
    return _root() / getattr(config, "CRYPTO_STRATEGY_MODE_FILE", "crypto_strategy_mode.json")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _markierung() -> Path:
    """Merkt, dass die Modusdatei schon einmal existiert hat."""
    return _root() / ".crypto_strategy_mode.seen"


def _je_geschrieben() -> bool:
    try:
        return _markierung().exists()
    except OSError:
        return False


def _default() -> dict:
    return {"schema": 1, "active_mode": NEXUS_STANDARD, "revision": 0,
            "updated_at_utc": "", "source": "default", "history": []}


def status() -> dict:
    with _LOCK:
        if not path().exists():
            # v9.1: Eine FEHLENDE Datei lieferte NEXUS_STANDARD, also wieder
            # Handel -- eine BESCHAEDIGTE pausierte. Wer die Datei loescht
            # (Aufraeumen, abgebrochener Schreibvorgang), hob damit ein
            # gesetztes CRYPTO_PAUSED still auf. Beim ersten Start gibt es sie
            # noch nicht; danach ist ihr Verschwinden ein Grund zur Vorsicht.
            if _je_geschrieben():
                logger.warning("Strategiemodusdatei fehlt, wurde aber schon "
                               "einmal geschrieben -- Krypto pausiert.")
                return {**_default(), "active_mode": CRYPTO_PAUSED,
                        "error": "mode state file missing"}
            return _default()
        try:
            import json
            data = json.loads(path().read_text(encoding="utf-8"))
            if not isinstance(data, dict) or str(data.get("active_mode")) not in VALID_MODES:
                raise ValueError("invalid strategy mode state")
            return {**_default(), **data}
        except Exception as exc:
            # Corrupt control state pauses only crypto entries; eToro and all
            # protection/exit loops remain available.
            return {**_default(), "active_mode": CRYPTO_PAUSED,
                    "error": f"mode state unreadable: {type(exc).__name__}"}


def current_mode() -> str:
    return str(status().get("active_mode") or CRYPTO_PAUSED)


def set_mode(mode: str, *, source: str, reason: str = "", notify: bool = True) -> dict:
    selected = str(mode or "").strip().upper()
    if selected not in VALID_MODES:
        raise ValueError(f"ungueltiger Krypto-Strategiemodus: {selected}")
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
        try:
            _markierung().write_text(changed_at, encoding="utf-8")
        except OSError:
            logger.debug("Modus-Markierung nicht schreibbar", exc_info=True)
    if notify:
        try:
            from notifier import send_telegram
            label = {
                NEXUS_STANDARD: "NEXUS Standard",
                FREQTRADE_SAMPLE: "Freqtrade SampleStrategy",
                CRYPTO_PAUSED: "Krypto pausiert (eToro laeuft weiter)",
            }.get(selected)
            if label is None:
                from zusatz_strategien import STRATEGIEN
                label = STRATEGIEN[selected]["label"]
            send_telegram(
                f"🔁 OKX-Strategiemodus: {previous} → {label}. "
                "Der Wechsel gilt nur fuer neue Krypto-Einstiege; offene "
                "Positionen behalten ihre Einstiegsstrategie.",
                priority="critical" if selected == CRYPTO_PAUSED else "normal",
                # v9.1: zusaetzlich der Zeitstempel. Nach einer Reparatur
                # springt revision auf 1 zurueck; die schon zugestellte
                # event_id haette die Bestaetigung still verworfen.
                event_id=f"crypto-strategy-mode:{revision}:{changed_at}",
            )
        except Exception:
            logger.debug("Strategiemodus-Telegram nicht zustellbar", exc_info=True)
    return state


def signal_timeframe(mode: str | None = None, *, standard_timeframe=None) -> str | None:
    """The active entry timeframe; independent from the safety warmup timer."""
    selected = str(mode or current_mode()).upper()
    if selected == FREQTRADE_SAMPLE:
        from freqtrade_sample_strategy import TIMEFRAME
        return str(TIMEFRAME)
    if selected in ZUSATZ_MODES:
        from zusatz_strategien import TIMEFRAME as ZUSATZ_TIMEFRAME
        return str(ZUSATZ_TIMEFRAME)
    if selected == NEXUS_STANDARD:
        return str(standard_timeframe if standard_timeframe is not None else
                   getattr(config, "CRYPTO_BAR_SIZE", "15 mins"))
    return None


def entry_snapshot(mode: str | None = None) -> dict:
    selected = str(mode or current_mode()).upper()
    if selected == FREQTRADE_SAMPLE:
        from freqtrade_sample_strategy import parameter_snapshot
        return {"entry_strategy_mode": selected, **parameter_snapshot()}
    if selected in ZUSATZ_MODES:
        from zusatz_strategien import parameter_snapshot as zusatz_snapshot
        snap = zusatz_snapshot(selected)
        snap["parameters"] = dict(snap.get("parameters") or {})
        return snap
    if selected == NEXUS_STANDARD:
        try:
            import strategy_version
            version = strategy_version.aktuell()
        except Exception:
            version = ""
        return {
            "entry_strategy_mode": selected,
            "strategy_name": "NEXUS Standard Krypto",
            "strategy_version": str(version or "NEXUS-STANDARD-LEGACY"),
            "parameter_hash": "",
            "timeframe": str(getattr(config, "CRYPTO_BAR_SIZE", "15 mins")),
            "parameters": {"entry_mode": str(getattr(config, "ENTRY_MODE", "trend"))},
        }
    raise ValueError("CRYPTO_PAUSED kann keine Einstiegsstrategie erzeugen")


def strategy_is_resolved(snapshot: dict) -> bool:
    return (str((snapshot or {}).get("entry_strategy_mode") or "")
            in ({NEXUS_STANDARD, FREQTRADE_SAMPLE} | ZUSATZ_MODES)
            and bool(str((snapshot or {}).get("strategy_version") or "")))


__all__ = [
    "CRYPTO_PAUSED", "FREQTRADE_SAMPLE", "NEXUS_STANDARD", "VALID_MODES",
    "ZUSATZ_MODES", "current_mode", "entry_snapshot", "path", "set_mode",
    "status", "strategy_is_resolved", "signal_timeframe",
]
