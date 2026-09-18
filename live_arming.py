"""Unabhaengige, kurzlebige LIVE-Freigabe.

Der Handelsmodus (paper/live) und diese Freigabe sind zwei getrennte Faktoren.
Ein versehentliches ``TRADING_MODE=live`` reicht dadurch nicht fuer echte Orders.
"""
from __future__ import annotations

import json
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path

from safe_persistence import atomic_write_json

ARM_FILE = Path(__file__).with_name("live_trading_arm.json")
ARM_PHRASE = "LIVE HANDEL FREIGEBEN"
DEFAULT_MINUTES = 30


def create_arm(minutes: int = DEFAULT_MINUTES) -> dict:
    now = datetime.now(timezone.utc)
    payload = {
        "created_at": now.isoformat(),
        "expires_at": (now + timedelta(minutes=max(5, int(minutes)))).isoformat(),
        "nonce": secrets.token_hex(16),
        "purpose": "independent-live-trading-arm",
    }
    atomic_write_json(ARM_FILE, payload)
    return payload


def clear_arm() -> None:
    try:
        ARM_FILE.unlink(missing_ok=True)
    except Exception:
        __import__("logging").getLogger(__name__).debug("Best-effort-Ausnahme unterdrueckt", exc_info=True)


def arm_status(*, cleanup_expired: bool = True) -> tuple[bool, str, dict]:
    if not ARM_FILE.exists():
        return False, "keine unabhaengige LIVE-Freigabe vorhanden", {}
    try:
        data = json.loads(ARM_FILE.read_text(encoding="utf-8"))
        exp = datetime.fromisoformat(str(data.get("expires_at", "")))
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        if now >= exp:
            if cleanup_expired:
                clear_arm()
            return False, "LIVE-Freigabe ist abgelaufen", data
        if data.get("purpose") != "independent-live-trading-arm" or not data.get("nonce"):
            return False, "LIVE-Freigabedatei ist ungueltig", data
        return True, f"freigegeben bis {exp.astimezone().isoformat(timespec='minutes')}", data
    except Exception as exc:
        return False, f"LIVE-Freigabe unlesbar: {exc}", {}
