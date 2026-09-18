"""Eindeutige Instrumentidentitaet ueber eToro und OKX hinweg.

Sicherheitsziel: Ein Ticker allein ist NIE eine Identitaet. FLR (Flare/Krypto)
und FLR.US (Fluor/Aktie) duerfen sich an keiner Stelle gegenseitig matchen.
"""
from __future__ import annotations

from dataclasses import dataclass


def normalize_asset_type(value: str) -> str:
    value = str(value or "").strip().lower()
    if value not in {"stock", "crypto"}:
        raise ValueError(f"Nicht unterstuetzter Asset-Typ: {value!r}")
    return value


def normalize_user_symbol(symbol: str, asset_type: str) -> str:
    asset_type = normalize_asset_type(asset_type)
    s = str(symbol or "").strip().upper().replace(" ", "")
    if asset_type == "crypto":
        for suffix in ("/EUR", "-EUR", ".EUR", "/USDC", "-USDC", ".USDC",
                       "/USD", "-USD", ".USD"):
            if s.endswith(suffix):
                s = s[:-len(suffix)]
        # Ein Aktien-Boersensuffix darf niemals als Krypto normalisiert werden.
        if s.endswith(".US"):
            raise ValueError(f"Aktien-Symbol {symbol!r} kann nicht als Krypto verwendet werden")
    else:
        # Intern bleibt der Watchlist-Ticker suffixfrei; der eToro-Marktname
        # darf spaeter .US tragen. Andere Suffixe werden nicht geraten.
        if s.endswith(".US"):
            s = s[:-3]
    if not s:
        raise ValueError("Leeres Instrument-Symbol")
    return s


def canonical_key(symbol: str, asset_type: str) -> str:
    return f"{normalize_asset_type(asset_type)}:{normalize_user_symbol(symbol, asset_type)}"


def etoro_market_symbol_matches(requested_symbol: str, asset_type: str, market_symbol: str) -> bool:
    """True nur fuer einen asset-sicheren eToro-Symboltreffer.

    Aktien: Watchlist FLR -> eToro FLR.US (oder exakt FLR, falls eToro das so
    liefert). Krypto: FLR -> eToro FLR, FLR/USD oder FLR-USD, aber NIE FLR.US.
    """
    kind = normalize_asset_type(asset_type)
    req = normalize_user_symbol(requested_symbol, kind)
    raw = str(market_symbol or "").strip().upper().replace(" ", "")
    if not raw:
        return False
    if kind == "stock":
        return raw in {req, f"{req}.US"}
    if raw.endswith(".US"):
        return False
    return raw in {req, f"{req}/USD", f"{req}-USD", f"{req}.USD"}


def market_asset_type(market_symbol: str, *, metadata: dict | None = None) -> str | None:
    """Asset-Typ aus Broker-Metadaten bestimmen, niemals aus einer Coin-Liste.

    Explizite eToro-Felder haben Vorrang. Nur .US wird als eindeutiger
    Aktienhinweis verwendet. Ist die Art sonst unklar, liefert die Funktion
    None und der Aufrufer muss fail-closed behandeln.
    """
    md = metadata or {}
    for key in ("instrumentType", "assetType", "type", "instrumentClass", "marketType"):
        value = str(md.get(key) or "").strip().lower()
        if not value:
            continue
        if any(x in value for x in ("crypto", "coin", "digital")):
            return "crypto"
        if any(x in value for x in ("stock", "equity", "share")):
            return "stock"
    raw = str(market_symbol or "").strip().upper()
    if raw.endswith(".US"):
        return "stock"
    return None


def same_instrument(symbol_a: str, asset_a: str, symbol_b: str, asset_b: str) -> bool:
    try:
        return canonical_key(symbol_a, asset_a) == canonical_key(symbol_b, asset_b)
    except Exception:
        return False


@dataclass(frozen=True)
class InstrumentIdentity:
    asset_type: str
    symbol: str

    @property
    def key(self) -> str:
        return canonical_key(self.symbol, self.asset_type)
