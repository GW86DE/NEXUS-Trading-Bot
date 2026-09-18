"""Sicheres Aktivieren eines Risiko-Profils.

Das Modul enthaelt absichtlich nur die lokale Profilumschaltung. Telegram-
Bestaetigungen und GUI-Dialoge liegen ausserhalb, damit keine Transportlogik
versehentlich Risikowerte direkt manipuliert.
"""
from __future__ import annotations

from pathlib import Path

import config
import profiles
from safe_persistence import atomic_write_text

ROOT = Path(__file__).resolve().parent
PROFILE_FILE = ROOT / "aktives_profil.txt"


def activate_risk_profile(name: str) -> str:
    """Profil atomar persistieren und fuer den laufenden Prozess anwenden."""
    key = str(name or "").strip().lower()
    profiles.get_profile(key)  # validiert vor jedem Schreibzugriff
    atomic_write_text(PROFILE_FILE, key, durable=True)
    profiles.apply_profile(config, key)
    config.ACTIVE_PROFILE = key
    return key
