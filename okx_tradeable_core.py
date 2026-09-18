"""Stabiler OKX-Kern aus wirklich ausfuehrbaren Kontomaerkten.

Der statische Wunschzettel in ``config.CRYPTO_CORE_SYMBOLS`` ist nur eine
Prioritaetsliste. Brokerwahrheit liefern ausschliesslich der authentifizierte
OKX-Instrumentkatalog, ``tradeQuoteCcyList`` und die aktuellen harten Filter.
Ein einmal gewaehlter Kernwert bleibt stabil, solange er diese Bedingungen
weiter erfuellt; freie Plaetze werden deterministisch nach Paarumsatz gefuellt.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import config
from safe_persistence import atomic_write_json


def _root() -> Path:
    return Path(os.getenv("TRADINGBOT_TEST_STATE_DIR", "").strip() or
                Path(__file__).resolve().parent)


def state_path() -> Path:
    return _root() / getattr(config, "OKX_TRADEABLE_CORE_FILE",
                            "okx_tradeable_core_20.json")


def load() -> dict:
    try:
        raw = json.loads(state_path().read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def select(*, instruments: dict, tickers: dict, pair_reasons: dict,
           quote_rates: dict, preferred=(), limit: int = 20,
           allowed_quotes=(), funded_quotes=None) -> tuple[set[str], dict]:
    """Waehlt bis zu ``limit`` stabile Basen aus voll geeigneten Paaren."""
    limit = max(0, int(limit))
    choices: dict[str, dict] = {}
    for inst_id, meta in (instruments or {}).items():
        iid = str(inst_id).upper()
        if str((pair_reasons or {}).get(iid) or ""):
            continue
        ticker = (tickers or {}).get(inst_id) or (tickers or {}).get(iid)
        if ticker is None:
            continue
        base = str(getattr(meta, "base_ccy", "") or "").upper()
        quote = str(getattr(meta, "quote_ccy", "") or "").upper()
        rate = float((quote_rates or {}).get(quote, 0.0) or 0.0)
        volume = float(getattr(ticker, "vol_24h_quote", 0.0) or 0.0) * rate
        if not base or rate <= 0 or volume <= 0:
            continue
        trade_quotes = tuple(dict.fromkeys(
            str(x).upper() for x in
            (getattr(meta, "trade_quote_ccy_list", ()) or (quote,)) if str(x).strip()))
        allowed_order = tuple(dict.fromkeys(
            str(x).upper() for x in allowed_quotes if str(x).strip()))
        selected_trade_quote = next(
            (ccy for ccy in allowed_order
             if ccy in set(trade_quotes)
             and (funded_quotes is None or ccy in set(funded_quotes))), "")
        item = {
            "base": base, "pair": iid, "market_quote": quote,
            "trade_quote_ccy_list": list(trade_quotes),
            "allowed_trade_quotes": sorted(set(trade_quotes).intersection(
                {str(x).upper() for x in allowed_quotes})),
            "selected_trade_quote": selected_trade_quote,
            "volume_24h_normalized_eur": volume,
        }
        old = choices.get(base)
        if old is None or (-volume, iid) < (
                -float(old["volume_24h_normalized_eur"]), str(old["pair"])):
            choices[base] = item

    previous = [str(x.get("base") or "").upper()
                for x in (load().get("items") or []) if x.get("base")]
    preferred = [str(x).upper() for x in preferred if str(x).strip()]
    ranked = [x["base"] for x in sorted(
        choices.values(),
        key=lambda row: (-float(row["volume_24h_normalized_eur"]),
                         row["base"], row["pair"]))]
    order: list[str] = []
    for base in [*previous, *preferred, *ranked]:
        if base in choices and base not in order:
            order.append(base)
    selected = order[:limit]
    items = []
    for rank, base in enumerate(selected, 1):
        items.append({**choices[base], "rank": rank})
    snapshot = {
        "schema": 1,
        "updated_at_utc": datetime.now(timezone.utc).isoformat(),
        "selection": "stable account-executable OKX SPOT core",
        "target": limit,
        "actual": len(items),
        "missing_slots": max(0, limit - len(items)),
        "allowed_bot_cash": [str(x).upper() for x in allowed_quotes],
        "funded_bot_cash": (None if funded_quotes is None else
                            [str(x).upper() for x in allowed_quotes
                             if str(x).upper() in set(funded_quotes)]),
        "eligible_bases": len(choices),
        "items": items,
    }
    atomic_write_json(state_path(), snapshot)
    return set(selected), snapshot


__all__ = ["select", "load", "state_path"]
