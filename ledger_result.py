"""Alias (10.8.0, Schritt 1): Modul liegt jetzt in nexus/domain/ledger_result.py."""
from nexus.weiche import umleiten

umleiten(__name__, "nexus.domain.ledger_result")
