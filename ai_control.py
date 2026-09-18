"""Persistente Steuerung aller optionalen OpenAI-Rollen.

Keine dieser Rollen besitzt Kauf-/Verkaufsrechte. ``OFF`` sperrt saemtliche
OpenAI-Aufrufe (Aufmerksamkeitsranking, Research-Vorschlaege und die optionale
sprachliche Strategy-Interpretation). Die deterministische Scan-Reihenfolge und
die rein lokalen Strategy-Statistiken funktionieren trotzdem weiter.
AUTO: konfigurierte Budgets/Zeitplaene. OFF: keine OpenAI-Aufrufe.
ON: aktivierte Rollen bleiben an alle Tagesbudgets gebunden.
"""
from __future__ import annotations
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from safe_persistence import atomic_write_json
from state_lock import critical_state_lock

logger = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parent
_STATE_ROOT = Path(os.getenv("TRADINGBOT_TEST_STATE_DIR", "").strip() or ROOT)
PATH = _STATE_ROOT / "ai_control_state.json"
VALID = {"AUTO", "OFF", "ON"}

def read_mode() -> str:
    try:
        if PATH.exists():
            mode = str(json.loads(PATH.read_text(encoding="utf-8")).get("mode", "AUTO")).upper()
            if mode in VALID:
                return mode
    except Exception as exc:
        logger.debug("AI-Control-Status nicht lesbar: %s", exc)
    return "OFF" if PATH.exists() else "AUTO"

def set_mode(mode: str, source: str = "local") -> str:
    mode = str(mode or "").upper()
    if mode not in VALID:
        raise ValueError("KI-Modus muss AUTO, OFF oder ON sein")
    with critical_state_lock(PATH):
        atomic_write_json(PATH, {
        "mode": mode,
        "source": source,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        })
    return mode
