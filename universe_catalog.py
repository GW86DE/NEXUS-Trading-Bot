"""Broad, broker-neutral reserve universe for v5.8.

The shipped JSON files are a *configured observation universe*, not a promise
that every selected broker can trade every instrument. Broker qualification
still decides the active/tradable subset at runtime.

Safety principles:
- The curated v5.6 core universe is always retained first.
- Only a small configured slice of the Broad reserve is activated.
- Broad additions are tagged ``broad=True`` and ``sector='UNCLASSIFIED'``.
- Unknown-sector candidates get a smaller position cap in the trading core.
- A broker may reject unsupported symbols without breaking the bot.
"""
from __future__ import annotations
from pathlib import Path
import json

ROOT = Path(__file__).resolve().parent
STOCK_FILE = ROOT / "broad_stocks.json"
CRYPTO_FILE = ROOT / "broad_crypto.json"


def _load_json(path: Path, default):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, type(default)) else default
    except Exception:
        return default


_BROAD_NAME_EXCLUDES = (
    "merger corp", "acquisition corp", "acquisition co", "blank check",
    "warrant", "rights", " units", "unit ", "depositary warrant",
    "trust common", "income trust", "technology trust", "beneficial interest",
)


def _eligible_broad_row(row: dict) -> bool:
    """Best-effort-Strukturfilter fuer die kleine aktive Broad-Auswahl.

    Die Reserve bleibt vollstaendig im Paket. Offensichtliche SPAC-/Shell-,
    Warrant-/Unit- und Fonds-/Trust-Bezeichnungen werden jedoch nicht in die
    aktive 35er-Ergaenzung aufgenommen. Das ist nur eine erste Sicherheits-
    huerde; Live-Quote, Liquiditaet und alle Handelsfilter bleiben Pflicht.
    """
    if not isinstance(row, dict):
        return False
    name = str(row.get("name", "") or "").strip().lower()
    if not name:
        return True
    return not any(token in name for token in _BROAD_NAME_EXCLUDES)


def expand_stocks(base_rows: list[dict], target: int = 250) -> list[dict]:
    target = max(1, int(target or 250))
    out = [dict(x) for x in base_rows]
    seen = {(str(x.get("symbol", "")).upper(), str(x.get("currency", "USD")).upper()) for x in out}
    for row in _load_json(STOCK_FILE, []):
        if len(out) >= target:
            break
        if not _eligible_broad_row(row):
            continue
        sym = str(row.get("symbol", "")).strip().upper()
        cur = str(row.get("currency", "USD") or "USD").upper()
        if not sym or (sym, cur) in seen:
            continue
        item = {
            "symbol": sym,
            "exchange": str(row.get("exchange", "SMART") or "SMART"),
            "currency": cur,
            "sector": str(row.get("sector", "UNCLASSIFIED") or "UNCLASSIFIED"),
            "broad": True,
            "gruppe": "broad",
            "display_name": str(row.get("name", "") or ""),
        }
        out.append(item)
        seen.add((sym, cur))
    return out[:target]


def expand_crypto(base_symbols: list[str], exchange: str, target: int = 100) -> list[dict]:
    target = max(1, int(target or 100))
    symbols = []
    seen = set()
    for s in list(base_symbols) + list(_load_json(CRYPTO_FILE, [])):
        sym = str(s or "").strip().upper().replace("/USD", "")
        if not sym or sym in seen:
            continue
        symbols.append(sym); seen.add(sym)
        if len(symbols) >= target:
            break
    return [
        {"symbol": s, "exchange": exchange, "currency": "USD", "broad": s not in set(str(x).upper() for x in base_symbols)}
        for s in symbols[:target]
    ]


def configured_counts(stock_rows: list[dict], crypto_rows: list[dict]) -> dict:
    us = sum(1 for x in stock_rows if str(x.get("currency", "USD")).upper() == "USD")
    eu = len(stock_rows) - us
    return {"stocks": len(stock_rows), "us": us, "eu": eu, "crypto": len(crypto_rows)}
