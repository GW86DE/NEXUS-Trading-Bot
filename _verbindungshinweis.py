"""Freundliche eToro-Verbindungsfehler fuer Diagnose-Skripte."""
from __future__ import annotations
from functools import wraps

def freundlich(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            print(f"eToro-Verbindung fehlgeschlagen: {exc}")
            print("Pruefe eToro-Zugangsdaten, Internetverbindung und den DEMO/LIVE-Modus.")
            return 2
    return wrapper
