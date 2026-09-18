"""Prozessweiter Single-Instance-Lock fuer TradingBot.

Auf Linux/Raspberry Pi wird flock() verwendet. Der Lock wird vom Kernel beim
Prozessende automatisch freigegeben; ein Stromausfall kann daher keinen
permanenten "stale lock" erzeugen.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


class InstanceAlreadyRunning(RuntimeError):
    pass


class SingleInstanceLock:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.handle = None

    def acquire(self) -> "SingleInstanceLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = open(self.path, "a+", encoding="utf-8")
        try:
            if os.name == "nt":
                import msvcrt
                self.handle.seek(0)
                self.handle.write(" ")
                self.handle.flush()
                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError) as exc:
            owner = ""
            try:
                self.handle.seek(0)
                owner = self.handle.read().strip()
            except Exception:
                __import__("logging").getLogger(__name__).debug("Best-effort-Ausnahme unterdrueckt", exc_info=True)
            try:
                self.handle.close()
            except Exception:
                __import__("logging").getLogger(__name__).debug("Best-effort-Ausnahme unterdrueckt", exc_info=True)
            self.handle = None
            detail = f" Details: {owner}" if owner else ""
            raise InstanceAlreadyRunning(
                "Es laeuft bereits eine TradingBot-Instanz. Zweitstart wurde blockiert." + detail
            ) from exc

        payload = {
            "pid": os.getpid(),
            "started_at": datetime.now(timezone.utc).isoformat(),
            "python": sys.executable,
        }
        try:
            self.handle.seek(0)
            self.handle.truncate(0)
            self.handle.write(json.dumps(payload, ensure_ascii=False))
            self.handle.flush()
            os.fsync(self.handle.fileno())
        except Exception:
            __import__("logging").getLogger(__name__).debug("Best-effort-Ausnahme unterdrueckt", exc_info=True)
        return self

    def release(self) -> None:
        if self.handle is None:
            return
        try:
            if os.name == "nt":
                import msvcrt
                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        except Exception:
            __import__("logging").getLogger(__name__).debug("Best-effort-Ausnahme unterdrueckt", exc_info=True)
        try:
            self.handle.close()
        except Exception:
            __import__("logging").getLogger(__name__).debug("Best-effort-Ausnahme unterdrueckt", exc_info=True)
        self.handle = None

    def __enter__(self):
        return self.acquire()

    def __exit__(self, exc_type, exc, tb):
        self.release()
        return False
