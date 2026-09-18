"""Dynamisches Handelsuniversum des TradingBot v8.1.1 NEXUS.

    modelle          Datentypen und persistenter Zustand
    scoring          Bewertung der Marktqualitaet
    crypto_selector  OKX-Spotauswahl (autonom)
    stock_selector   eToro-Aktienauswahl (Vorschlag + Telegram-Freigabe)
    manager          Hysterese, Aufenthaltsdauer, Focus Set, Diff
    audit            Aenderungsprotokoll
"""

from .modelle import (
    ABGANG, AKTIV, BEOBACHTUNG, ENTFERNT,
    TIER_ETABLIERT, TIER_FAVORIT, TIER_GEPINNT, TIER_KANDIDAT,
    UniverseDiff, UniverseKandidat, UniverseMitglied, UniverseScore, UniverseZustand,
)
from .manager import UniverseManager, UniverseRegeln
from .scoring import cheap_score, erklaere_score, quality_score

__all__ = [
    "UniverseManager", "UniverseRegeln", "UniverseZustand",
    "UniverseKandidat", "UniverseScore", "UniverseMitglied", "UniverseDiff",
    "cheap_score", "quality_score", "erklaere_score",
    "AKTIV", "BEOBACHTUNG", "ABGANG", "ENTFERNT",
    "TIER_ETABLIERT", "TIER_KANDIDAT", "TIER_FAVORIT", "TIER_GEPINNT",
]


def crypto_selector(client, cfg=None):
    """Lazy-Import, damit die Broker-Schicht nicht zwingend geladen wird."""
    from .crypto_selector import CryptoUniverseSelector
    return CryptoUniverseSelector(client, cfg=cfg)


def stock_selector(broker=None, massive=None, cfg=None):
    from .stock_selector import StockUniverseSelector
    return StockUniverseSelector(broker=broker, massive=massive, cfg=cfg)
