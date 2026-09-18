"""Frei waehlbare Fokusliste (max. 5 Aktien/Kryptos).

Ein Favorit ist ausschliesslich ein Beobachtungswunsch:

* Aktien werden nur dem eToro-Aktienpfad zugeordnet.
* Kryptowaehrungen werden nur dem OKX-Spotpfad zugeordnet.
* Der Eintrag ergaenzt die jeweilige Vorauswahl, um die normale technische
  und brokerseitige Pruefung ueberhaupt zu ermoeglichen.
* Er umgeht **keine** Risiko-, Kosten-, News-, Liquiditaets-, Bewaehrungs-
  oder Broker-Sicherheitspruefung und loest keine Order aus.

Die Zuordnung wird beim Hinzufuegen moeglichst automatisch getroffen. Ist ein
Ticker nicht sicher zuordenbar, muss die Oberflaeche bewusst nachfragen,
anstatt einen Coin versehentlich als Aktie (oder umgekehrt) zu behandeln.
"""
from __future__ import annotations
from dataclasses import dataclass, asdict
from pathlib import Path
import json
import re
from typing import Iterable
from safe_persistence import atomic_write_json

ROOT = Path(__file__).resolve().parent
PATH = ROOT / "favorites.json"
MAX_FAVORITES = 5
ASSET_TYPES = ("stock", "crypto")
_PAIR_RE = re.compile(r"^([A-Z0-9]{1,16})[-/]([A-Z0-9]{2,12})$")

@dataclass(frozen=True)
class Favorite:
    symbol: str
    asset_type: str = "stock"
    currency: str = "USD"
    exchange: str = "SMART"

    @property
    def canonical_key(self):
        return f"{self.asset_type}:{self.symbol}".upper()

    @property
    def broker(self) -> str:
        """Der Broker wird allein aus der Anlageklasse abgeleitet.

        Das verhindert, dass ein gespeicherter oder alter ``exchange``-Wert
        einen Krypto-Favoriten versehentlich in den eToro-Pfad bringt.
        """
        return "okx" if self.asset_type == "crypto" else "etoro"


def normalize_symbol(symbol: str, asset_type: str) -> str:
    s = str(symbol or "").strip().upper().replace(" ", "")
    if asset_type == "crypto":
        for quote in ("EUR", "USDC", "USD"):
            if any(s.endswith(separator + quote) for separator in ("-", "/", ".", "_")):
                s = s[:-(len(quote) + 1)]
                break
    if not re.fullmatch(r"[A-Z0-9.\-]{1,16}", s):
        raise ValueError("Ungueltiges Symbol. Beispiel Aktie: AAPL, Krypto: BTC")
    return s


def _okx_quote() -> str:
    """Bevorzugte, im deutschen OKX-Setup erlaubte Spot-Quotewaehrung."""
    try:
        import config
        allowed = tuple(str(x).upper() for x in getattr(
            config, "OKX_ALLOWED_QUOTE_CCY", ("EUR", "USD", "USDC")) if str(x).strip())
        preferred = str(getattr(config, "OKX_QUOTE_CCY", "EUR") or "EUR").upper()
    except Exception:
        allowed, preferred = ("EUR", "USD", "USDC"), "EUR"
    if preferred in allowed:
        return preferred
    return allowed[0] if allowed else "EUR"


def _known_symbols() -> tuple[set[str], set[str]]:
    """Liefert die lokal bekannten Aktien- und Krypto-Symbole.

    Das ist absichtlich nur ein Hinweis fuer die automatische UI-Zuordnung.
    Die verbindliche Pruefung erfolgt anschliessend weiterhin beim jeweiligen
    Broker (eToro bzw. OKX) im normalen Universumslauf.
    """
    stocks: set[str] = set()
    crypto: set[str] = set()
    try:
        import config
        for row in getattr(config, "STOCK_CATALOG_SYMBOLS", ()) or ():
            value = row.get("symbol", "") if isinstance(row, dict) else row
            if str(value).strip():
                stocks.add(normalize_symbol(str(value), "stock"))
        for row in getattr(config, "CRYPTO_SYMBOLS", ()) or ():
            value = row.get("symbol", "") if isinstance(row, dict) else row
            if str(value).strip():
                crypto.add(normalize_symbol(str(value), "crypto"))
        crypto.update(str(x).upper() for x in getattr(config, "CRYPTO_CORE_SYMBOLS", ()) or ())
    except Exception:
        # Bei einer reinen Offline-Ansicht bleibt eine sichere manuelle Wahl
        # moeglich; es wird nie still ein falscher Broker geraten.
        return stocks, crypto
    return stocks, crypto


def resolve_favorite(symbol: str, asset_type: str = "auto", *, currency: str = "") -> tuple[Favorite, str]:
    """Ordnet einen Favoriten sicher eToro oder OKX zu.

    ``asset_type`` kann ``auto``, ``stock`` oder ``crypto`` sein. Die
    automatische Zuordnung akzeptiert eindeutige Krypto-Paare (z. B.
    ``BTC-EUR``) sowie Werte aus dem lokalen Aktien-/Kryptokatalog. Alles
    andere bleibt absichtlich mehrdeutig und braucht eine explizite Auswahl.
    """
    requested = str(asset_type or "auto").strip().lower()
    if requested not in {"auto", *ASSET_TYPES}:
        raise ValueError("Typ muss automatisch, Aktie oder Krypto sein.")

    raw = str(symbol or "").strip().upper().replace(" ", "")
    if not raw:
        raise ValueError("Bitte ein Symbol eingeben, z. B. AAPL oder BTC.")

    # Freiwillige Praefixe machen die Eingabe auch ausserhalb der GUI klar.
    explicit_type = ""
    for prefix, kind in (("ETORO:", "stock"), ("STOCK:", "stock"),
                         ("OKX:", "crypto"), ("CRYPTO:", "crypto")):
        if raw.startswith(prefix):
            explicit_type, raw = kind, raw[len(prefix):]
            break
    if explicit_type and requested != "auto" and explicit_type != requested:
        raise ValueError("Praefix und ausgewaehlter Typ widersprechen sich.")

    pair = _PAIR_RE.fullmatch(raw)
    pair_quote = pair.group(2) if pair else ""
    if pair:
        raw = pair.group(1)

    if requested == "auto":
        if explicit_type:
            resolved = explicit_type
            reason = "durch Praefix eindeutig festgelegt"
        elif pair_quote:
            resolved = "crypto"
            reason = f"Krypto-Paar mit Quotewaehrung {pair_quote} erkannt"
        elif raw.endswith(".US"):
            resolved = "stock"
            reason = "Aktien-Suffix .US erkannt"
        else:
            stocks, crypto = _known_symbols()
            stock_symbol = normalize_symbol(raw, "stock")
            crypto_symbol = normalize_symbol(raw, "crypto")
            in_stocks = stock_symbol in stocks
            in_crypto = crypto_symbol in crypto
            if in_stocks and not in_crypto:
                resolved, reason = "stock", "im Aktienkatalog erkannt"
            elif in_crypto and not in_stocks:
                resolved, reason = "crypto", "im OKX-Kryptokatalog erkannt"
            elif in_stocks and in_crypto:
                raise ValueError(
                    f"{raw} ist als Aktie und Krypto moeglich. Bitte einmal Aktie oder Krypto auswaehlen.")
            else:
                raise ValueError(
                    f"{raw} ist lokal nicht eindeutig zuordenbar. Bitte einmal Aktie oder Krypto auswaehlen.")
    else:
        resolved = requested
        reason = "manuell bestaetigt"

    if pair_quote and resolved != "crypto":
        raise ValueError("Ein Paar wie BTC-EUR kann nur als OKX-Spot-Krypto gespeichert werden.")

    normalized = normalize_symbol(raw, resolved)
    if resolved == "crypto":
        quote = pair_quote or str(currency or _okx_quote()).upper()
        # In Deutschland nutzt der Bot nur die erlaubten OKX-Spot-Quotes.
        try:
            import config
            allowed = {str(x).upper() for x in getattr(
                config, "OKX_ALLOWED_QUOTE_CCY", ("EUR", "USD", "USDC"))}
        except Exception:
            allowed = {"EUR", "USD", "USDC"}
        if quote not in allowed:
            quote = _okx_quote()
        return Favorite(normalized, "crypto", quote, "OKX"), reason

    # Der eToro-Adapter qualifiziert derzeit nur eindeutig aufgeloeste
    # USD-Aktien. Ein Favorit kann trotzdem gespeichert werden; nicht
    # handelbare oder mehrdeutige Werte bleiben im normalen Broker-Check aus.
    return Favorite(normalized, "stock", str(currency or "USD").upper(), "ETORO"), reason


def _clean_favorite(item: Favorite) -> Favorite:
    typ = str(item.asset_type or "").lower()
    if typ not in ASSET_TYPES:
        raise ValueError(f"Nicht unterstuetzter Favoritentyp: {typ!r}")
    symbol = normalize_symbol(item.symbol, typ)
    if typ == "crypto":
        return Favorite(symbol, "crypto", _okx_quote() if not item.currency else
                        resolve_favorite(symbol, "crypto", currency=item.currency)[0].currency, "OKX")
    return Favorite(symbol, "stock", str(item.currency or "USD").upper(), "ETORO")


def load_favorites() -> list[Favorite]:
    try:
        raw = json.loads(PATH.read_text(encoding="utf-8")) if PATH.exists() else []
    except Exception:
        raw = []
    out=[]; seen=set()
    for row in raw if isinstance(raw, list) else []:
        try:
            typ = str(row.get("asset_type", "stock")).lower()
            if typ not in ASSET_TYPES:
                continue
            default_ccy = "USD" if typ == "stock" else _okx_quote()
            f = _clean_favorite(Favorite(
                str(row.get("symbol", "")), typ,
                str(row.get("currency", default_ccy) or default_ccy).upper(),
                str(row.get("exchange", "") or ""),
            ))
            if f.canonical_key in seen: continue
            seen.add(f.canonical_key); out.append(f)
        except Exception:
            continue
        if len(out) >= MAX_FAVORITES: break
    return out


def save_favorites(items: list[Favorite]) -> None:
    cleaned=[]; seen=set()
    for f in items:
        item = _clean_favorite(f)
        if item.canonical_key in seen: continue
        seen.add(item.canonical_key); cleaned.append(item)
        if len(cleaned)>=MAX_FAVORITES: break
    atomic_write_json(PATH, [asdict(x) for x in cleaned])


def as_universe_rows():
    stocks=[]; crypto=[]
    for f in load_favorites():
        if f.asset_type == "stock":
            stocks.append({"symbol":f.symbol, "exchange":"ETORO", "currency":f.currency or "USD",
                           "sector":"Favorit / unbekannt", "gruppe":"favorit", "favorite":True,
                           "broad": True})
        else:
            quote = f.currency or _okx_quote()
            crypto.append({"symbol":f.symbol, "exchange":"OKX", "currency":quote,
                           "inst_id":f"{f.symbol}-{quote}", "sector":"Krypto",
                           "gruppe":"favorit", "favorite":True})
    return stocks, crypto


def merge_stock_favorite_rows(base_rows: Iterable[dict],
                              favorite_rows: Iterable[dict] | None = None) -> list[dict]:
    """Ergaenzt nur fehlende Aktien-Favoriten zum eToro-Kandidatenfeld.

    Die Funktion veraendert weder ``config.STOCK_SYMBOLS`` noch das
    genehmigte Universum. Ein zusaetzlicher Favorit wird lediglich dem
    bestehenden Qualifikations-, Liquiditaets-, Score- und Bewaehrungspfad
    uebergeben. Bereits bekannte Aktien behalten immer ihre kuratierte
    Sektor-/Risikoklassifikation.
    """
    rows = [dict(row) for row in (base_rows or []) if isinstance(row, dict)]
    seen: set[str] = set()
    for row in rows:
        try:
            seen.add(normalize_symbol(row.get("symbol", ""), "stock"))
        except Exception:
            continue
    candidates = list(favorite_rows) if favorite_rows is not None else as_universe_rows()[0]
    for row in candidates:
        if not isinstance(row, dict):
            continue
        try:
            symbol = normalize_symbol(row.get("symbol", ""), "stock")
        except Exception:
            continue
        if symbol in seen:
            continue
        rows.append({
            "symbol": symbol,
            "exchange": "ETORO",
            "currency": str(row.get("currency") or "USD").upper(),
            "sector": "Favorit / unbekannt",
            "gruppe": "favorit",
            "favorite": True,
            "broad": True,
        })
        seen.add(symbol)
    return rows
