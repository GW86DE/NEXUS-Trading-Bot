"""Minimale systemd sd_notify-Unterstuetzung ohne externes Python-Paket."""
from __future__ import annotations

import os
import socket


def notify(message: str) -> bool:
    address = os.getenv("NOTIFY_SOCKET", "")
    if not address:
        return False
    if address.startswith("@"):  # systemd abstract namespace
        address = "\0" + address[1:]
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    try:
        sock.connect(address)
        sock.sendall(str(message).encode("utf-8"))
        return True
    except Exception:
        return False
    finally:
        try:
            sock.close()
        except Exception:
            __import__("logging").getLogger(__name__).debug("Best-effort-Ausnahme unterdrueckt", exc_info=True)


def ready(status: str = "TradingBot gestartet") -> bool:
    return notify(f"READY=1\nSTATUS={status}")


def watchdog(status: str = "TradingBot aktiv") -> bool:
    return notify(f"WATCHDOG=1\nSTATUS={status}")


def stopping(status: str = "TradingBot wird beendet") -> bool:
    return notify(f"STOPPING=1\nSTATUS={status}")
