#!/usr/bin/env python3
"""Versionsgebundener, rein lesender Einstieg fuer NEXUS 10.8.0 Diagnose 1.9.0."""
from pathlib import Path

EXPECTED_VERSION = "10.8.0-NEXUS"
HERE = Path(__file__).resolve().parent
try:
    observed = (HERE / "VERSION.txt").read_text(encoding="utf-8").strip()
except OSError as exc:
    raise SystemExit("VERSION.txt nicht lesbar; Diagnose abgebrochen: " + str(exc))
if observed != EXPECTED_VERSION:
    raise SystemExit(f"Diagnose gehoert zu {EXPECTED_VERSION}, lokaler Quellstand ist {observed or 'UNBEKANNT'}.")

from NEXUS_10_Diagnose import main

if __name__ == "__main__":
    raise SystemExit(main())
