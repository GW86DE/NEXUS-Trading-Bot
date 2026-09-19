"""Compatibility facade: SQLite analytics + JSONL mirror for old tools."""
from __future__ import annotations
import json, logging, os
from datetime import datetime, timezone
from pathlib import Path
import config
from decision_analytics import record as _record_sqlite, latest as _latest_sqlite
from decision_snapshot import apply_snapshot

logger=logging.getLogger(__name__)
_STATE_ROOT=Path(os.getenv("TRADINGBOT_TEST_STATE_DIR","").strip() or Path(__file__).resolve().parent)
PATH=_STATE_ROOT / str(getattr(config,"DECISION_JOURNAL_FILE","decision_journal.jsonl"))

def record_decision(**payload):
    payload=apply_snapshot(dict(payload))
    payload.setdefault("time",datetime.now(timezone.utc).isoformat())
    decision_id=_record_sqlite(payload)
    if decision_id is not None:
        payload["decision_id"] = int(decision_id)
        payload["decision_snapshot"]["decision_id"] = int(decision_id)
    # Keep a best-effort JSONL mirror for backwards compatible CLI tools. It is
    # not the authoritative database and may fail without affecting trading.
    # 10.8.1: rotiert wie decision_sources.jsonl -- bis 10.8.0 wuchs der
    # Spiegel unbegrenzt (833 MB am 19.09.2026 auf dem Pi).
    try:
        if PATH.exists() and PATH.stat().st_size > max_bytes():
            PATH.replace(PATH.with_suffix(".1.jsonl"))
        with PATH.open("a",encoding="utf-8") as f:
            f.write(json.dumps(payload,ensure_ascii=False,default=str)+"\n")
    except Exception as exc:
        logger.debug("Decision-JSONL-Mirror konnte nicht geschrieben werden: %s",exc)
    return decision_id

def max_bytes() -> int:
    """Obergrenze des JSONL-Spiegels (``DECISION_JOURNAL_MAX_MB``, Standard 20)."""
    try:
        mb = float(getattr(config, "DECISION_JOURNAL_MAX_MB", 20) or 20)
    except (TypeError, ValueError):
        mb = 20.0
    return int(max(1.0, mb) * 1024 * 1024)

def latest(limit=50):
    return _latest_sqlite(limit=limit)
