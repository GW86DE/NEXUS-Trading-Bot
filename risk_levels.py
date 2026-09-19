"""Alias (10.8.0, Schritt 1): Modul liegt jetzt in nexus/application/risk_levels.py."""
from nexus.weiche import umleiten

umleiten(__name__, "nexus.application.risk_levels")
