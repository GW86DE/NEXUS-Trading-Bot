"""Broker-Schicht des TradingBot v8.1.1 NEXUS.

In NEXUS 8.1.1 laufen ZWEI Broker gleichzeitig:

    eToro  -- Aktien und ETFs, nur waehrend der Boersenzeiten
    OKX    -- Krypto-Spot, rund um die Uhr

Beide Konten sind vollstaendig getrennt: eigenes Kapital, eigene Grenzen,
eigener Gesundheitszustand. Ein Ausfall auf einer Seite darf die andere
niemals mitreissen -- deshalb gibt es keinen gemeinsamen Verbindungszustand
und keinen gemeinsamen Kontostand.
"""
from .base import (
    BrokerBase, BrokerFehler, NichtVerbunden, NichtUnterstuetzt,
    VerbindungVerloren, Zeitabweichung, OrderStatusUnklar, AuthentifizierungsFehler,
    Position, Fill, OrderErgebnis,
)

# Namen, die der Bot kennt. Alles andere ist ein harter Fehler, damit ein
# alter Konfigurationsrest niemals still auf einen fremden Broker ausweicht.
BEKANNTE_BROKER = ("etoro", "okx")

# Welcher Broker bedient welche Anlageklasse.
ASSET_ROUTING = {
    "stock": "etoro",
    "etf": "etoro",
    "crypto": "okx",
}


def get_broker(name: str | None = None, **kwargs) -> BrokerBase:
    """Liefert den Adapter fuer den gewuenschten Broker."""
    import config
    configured = str(name or getattr(config, "BROKER", "etoro")).strip().lower()
    if configured == "etoro":
        from .etoro import EtoroBroker
        return EtoroBroker(**kwargs)
    if configured == "okx":
        from .okx import OKXBroker
        return OKXBroker(**kwargs)
    raise BrokerFehler(
        f"Unbekannter Broker {configured!r}. Erlaubt sind: {', '.join(BEKANNTE_BROKER)}."
    )


def broker_fuer_asset(asset_type: str) -> str:
    """Welcher Brokername ist fuer diese Anlageklasse zustaendig?"""
    return ASSET_ROUTING.get(str(asset_type or "").strip().lower(), "etoro")


__all__ = [
    "get_broker", "broker_fuer_asset", "BEKANNTE_BROKER", "ASSET_ROUTING",
    "BrokerBase", "BrokerFehler", "NichtVerbunden",
    "NichtUnterstuetzt", "VerbindungVerloren", "Zeitabweichung", "OrderStatusUnklar",
    "AuthentifizierungsFehler", "Position", "Fill", "OrderErgebnis",
]
