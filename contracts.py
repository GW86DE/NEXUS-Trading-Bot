"""Brokerneutrale Instrumente fuer eToro-Aktien und OKX-Krypto."""
from __future__ import annotations
from dataclasses import dataclass


@dataclass
class SimpleContract:
    symbol: str
    currency: str = "USD"
    localSymbol: str = ""
    conId: object = ""

    def __post_init__(self):
        if not self.localSymbol:
            self.localSymbol = self.symbol


@dataclass
class Instrument:
    name: str
    contract: object
    asset_type: str
    currency: str
    sector: str = ""
    exchange: str = ""
    broad: bool = False
    underdog: bool = False

    @property
    def use_rth(self):
        return self.asset_type == "stock"

    @property
    def preferred_data_types(self):
        return ("TRADES",)


def build_stock(symbol, exchange="ETORO", currency="USD"):
    return SimpleContract(str(symbol).upper(), currency)


def build_crypto(symbol, exchange="OKX", currency="EUR", local_symbol=""):
    symbol = str(symbol).upper()
    return SimpleContract(symbol, currency, local_symbol or f"{symbol}-{str(currency).upper()}")


def build_universe(stock_symbols, forex_pairs, crypto_symbols):
    """Erzeugt Aktien fuer eToro und Krypto fuer OKX.

    Der Aktien-Core uebergibt in NEXUS keine ``crypto_symbols``; diese
    Unterstuetzung dient Diagnose/Anzeige und respektiert das Broker-Routing.
    """
    out=[]
    for s in stock_symbols:
        out.append(Instrument(
            str(s["symbol"]).upper(), build_stock(s["symbol"], "ETORO", s.get("currency","USD")),
            "stock", s.get("currency","USD"), s.get("sector",""), "ETORO",
            bool(s.get("broad",False)), bool(s.get("underdog",False))))
    for c in crypto_symbols:
        exchange = str(c.get("exchange") or "OKX").upper()
        currency = str(c.get("currency") or "EUR").upper()
        out.append(Instrument(
            str(c["symbol"]).upper(),
            build_crypto(c["symbol"], exchange, currency, c.get("inst_id", "")),
            "crypto", currency, "crypto", exchange,
            bool(c.get("broad",False)), False))
    return out
