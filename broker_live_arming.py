"""Kurzlebige, broker-spezifische LIVE-Freigaben.

Eine dauerhaft gespeicherte Moduswahl reicht absichtlich nicht fuer neue
Live-Orders. Der Arming-Faktor verfällt automatisch; Ausstiege und
Schutzorders bleiben davon unberuehrt.
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from safe_persistence import atomic_write_json

ROOT = Path(__file__).resolve().parent
SUPPORTED = {"okx", "etoro"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _path(broker: str, root: Path = ROOT) -> Path:
    name = str(broker or "").strip().lower()
    if name not in SUPPORTED:
        raise ValueError(f"Unbekannter Broker: {broker}")
    return Path(root) / f"{name}_live_arm.json"


def arm(broker: str, *, minutes: int = 15, actor: str = "local",
        root: Path = ROOT) -> dict:
    minutes = max(1, min(60, int(minutes)))
    now = _now()
    data = {
        "broker": str(broker).lower(),
        "armed_at_utc": now.isoformat(),
        "expires_at_utc": (now + timedelta(minutes=minutes)).isoformat(),
        "actor": str(actor or "local")[:80],
        "pid": os.getpid(),
    }
    target = _path(broker, root)
    atomic_write_json(target, data)
    try:
        target.chmod(0o600)
    except OSError:
        pass
    return data


def disarm(broker: str, *, root: Path = ROOT) -> None:
    try:
        _path(broker, root).unlink(missing_ok=True)
    except OSError:
        pass


def status(broker: str, *, root: Path = ROOT) -> tuple[bool, str, dict]:
    target = _path(broker, root)
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
        if str(data.get("broker", "")).lower() != str(broker).lower():
            return False, "Freigabe gehoert zu einem anderen Broker", {}
        expires = datetime.fromisoformat(str(data.get("expires_at_utc", "")))
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        if expires <= _now():
            disarm(broker, root=root)
            return False, "Freigabe ist abgelaufen", data
        seconds = int((expires - _now()).total_seconds())
        return True, f"LIVE-Einstiege fuer noch {max(0, seconds // 60)} min freigegeben", data
    except FileNotFoundError:
        return False, "nicht freigegeben", {}
    except Exception:
        return False, "Freigabedatei ungueltig", {}


def main() -> int:
    parser = argparse.ArgumentParser(description="Kurzlebige LIVE-Freigabe je Broker")
    parser.add_argument("broker", choices=sorted(SUPPORTED))
    parser.add_argument("action", choices=("status", "arm", "disarm"))
    parser.add_argument("--minutes", type=int, default=15)
    args = parser.parse_args()

    if args.action == "status":
        ok, message, data = status(args.broker)
        print(json.dumps({"armed": ok, "message": message, **data}, ensure_ascii=False, indent=2))
        return 0 if ok else 1
    if args.action == "disarm":
        disarm(args.broker)
        print(f"{args.broker.upper()} LIVE-Freigabe entfernt.")
        return 0

    phrase = input(f"Zum Freigeben exakt '{args.broker.upper()} LIVE ARM' eingeben: ").strip()
    if phrase != f"{args.broker.upper()} LIVE ARM":
        print("Abgebrochen: Sicherheitsphrase stimmt nicht.")
        return 2
    data = arm(args.broker, minutes=args.minutes, actor="local_cli")
    print(f"{args.broker.upper()} LIVE-Einstiege bis {data['expires_at_utc']} freigegeben.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
