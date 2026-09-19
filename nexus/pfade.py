"""Projektpfade fuer Module im nexus-Paket (10.8.0, Schritt 1).

Wurzelmodule finden den Projektordner ueber ``Path(__file__).parent``. Ein Modul,
das nach ``nexus/<schicht>/`` umzieht, darf seine Zustandsdateien deshalb NICHT
mehr relativ zu ``__file__`` suchen -- sonst laege ``risiko_stufen.json`` nach
dem Umzug ploetzlich in ``nexus/application/`` und die gewaehlte Einsatzstufe
waere nach dem Update stillschweigend weg. Alle umgezogenen Module nehmen den
Projektordner von hier.
"""
from __future__ import annotations

import os
from pathlib import Path

PROJEKT_WURZEL = Path(__file__).resolve().parents[1]


def zustandsordner() -> Path:
    """Ordner der Zustandsdateien: Testumgebung oder Projektordner."""
    return Path(os.getenv("TRADINGBOT_TEST_STATE_DIR", "").strip() or PROJEKT_WURZEL)
