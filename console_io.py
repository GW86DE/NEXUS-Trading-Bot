"""Robuste UTF-8-Konsolenausgabe fuer Pi-GUI-Werkzeuge.

Verhindert UnicodeEncodeError bei nicht-UTF-8 geerbten Locales/Streams.
"""
from __future__ import annotations
import os
import sys


def configure_utf8_console() -> None:
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    os.environ.setdefault("PYTHONUTF8", "1")
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
        except (AttributeError, ValueError, OSError):
            # StringIO/IDE-Streams muessen nicht rekonfigurierbar sein.
            continue
