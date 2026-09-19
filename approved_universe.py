"""Persistente, ausschliesslich menschlich freigegebene Universums-Erweiterungen.

Sicherheitsgrenzen:
- Dieses Modul kennt keine KI und keine Orders.
- Ein Eintrag kann nur aus einer bestandenen technischen Pruefung kommen.
- Freigaben werden erst beim naechsten Prozessstart in config.STOCK_SYMBOLS
  gemischt. Ein Telegram-Klick kann deshalb niemals unmittelbar handeln.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

from instrument_identity import normalize_user_symbol
from safe_persistence import atomic_write_json

ROOT = Path(__file__).resolve().parent
logger = logging.getLogger(__name__)
_LOCK = threading.RLock()


def _state_root() -> Path:
    return Path(os.getenv("TRADINGBOT_TEST_STATE_DIR", "").strip() or ROOT)


def path() -> Path:
    return _state_root() / "approved_universe.json"


def _empty() -> dict:
    return {"version": 1, "stocks": [], "updated_at_utc": None}


def load() -> dict:
    p = path()
    with _LOCK:
        try:
            if p.exists():
                data = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(data, dict) and isinstance(data.get("stocks", []), list):
                    data.setdefault("version", 1)
                    return data
        except Exception as exc:
            # Eine kaputte Freigabedatei darf nie still neue Werte aktivieren.
            logger.warning("Freigegebenes Universum unlesbar; keine Research-Erweiterung wird geladen: %s",exc)
            return _empty()
    return _empty()


def approved_stock_rows() -> list[dict]:
    out = []
    seen = set()
    katalog_cache = _katalog_index()
    for row in load().get("stocks", []) or []:
        try:
            symbol = normalize_user_symbol(row.get("symbol", ""), "stock")
        except Exception:
            continue
        if symbol in seen:
            continue
        # Dynamisch aufgenommene Research-Werte bleiben bewusst BROAD und
        # konservativ, bis sie irgendwann manuell in den kuratierten Kern
        # uebernommen wuerden.
        #
        # v9.3: Steht das Symbol aber im kanonischen Katalog, gelten DESSEN
        # Metadaten. Bis 9.2 wurde ein dort bekannter Underdog hier pauschal
        # auf underdog=False gesetzt -- er haette damit das strengere
        # Underdog-Screening und den kleineren Positionsfaktor verloren.
        katalog = katalog_cache.get(symbol, {})
        out.append({
            "symbol": symbol,
            "exchange": "SMART",
            "currency": "USD",
            "sector": str(katalog.get("sector") or row.get("sector") or "UNCLASSIFIED"),
            "broad": not bool(katalog.get("underdog")),
            "underdog": bool(katalog.get("underdog")),
            "gruppe": "human_approved_research",
            "display_name": str(row.get("company") or ""),
            "proposal_id": str(row.get("proposal_id") or ""),
            "etoro_symbol_full": str(row.get("etoro_symbol_full") or ""),
        })
        seen.add(symbol)
    return out


def _katalog_index() -> dict:
    """Symbol -> Metadaten aus dem kanonischen Aktienkatalog (v9.3).

    10.8.0 (Schritt 2): Der Katalog wird aus dem bereits geladenen config
    gelesen, ohne config zu importieren. config baut STOCK_SYMBOLS ueber
    dieses Modul auf -- der frueher hier stehende ``import config`` war die
    Rueckrichtung desselben Import-Zyklus. Ist config (noch) nicht geladen,
    ist der Index leer, genau wie bei einem fehlenden Katalog.
    """
    import sys
    rows = getattr(sys.modules.get("config"), "STOCK_CATALOG_SYMBOLS", None) or []
    return {str(x.get("symbol", "")).upper(): dict(x) for x in rows
            if isinstance(x, dict) and x.get("symbol")}


def merge_approved_stocks(base_rows: list[dict]) -> list[dict]:
    """Fuegt beim Prozessstart menschlich freigegebene Aktien dedupliziert an."""
    out = [dict(x) for x in (base_rows or [])]
    seen = {str(x.get("symbol", "")).upper().replace(".US", "") for x in out}
    for row in approved_stock_rows():
        symbol = str(row.get("symbol", "")).upper()
        if symbol and symbol not in seen:
            out.append(row)
            seen.add(symbol)
    return out


def add_approved_stock(*, proposal_id: str, symbol: str, company: str, sector: str,
                       etoro_symbol_full: str, technical_review: dict,
                       approved_by: str = "telegram") -> tuple[bool, str]:
    """Idempotente persistente Aufnahme; keine Runtime-Aktivierung."""
    symbol = normalize_user_symbol(symbol, "stock")
    with _LOCK:
        data = load()
        rows = list(data.get("stocks", []) or [])
        for row in rows:
            try:
                existing = normalize_user_symbol(row.get("symbol", ""), "stock")
            except Exception:
                continue
            if existing == symbol:
                if str(row.get("proposal_id") or "") == str(proposal_id):
                    return True, "bereits aufgenommen"
                return False, f"{symbol} ist bereits im menschlich freigegebenen Universum"
        rows.append({
            "proposal_id": str(proposal_id),
            "symbol": symbol,
            "company": str(company or "")[:200],
            "sector": str(sector or "UNCLASSIFIED")[:120],
            "etoro_symbol_full": str(etoro_symbol_full or "")[:80],
            "approved_at_utc": datetime.now(timezone.utc).isoformat(),
            "approved_by": str(approved_by or "human")[:80],
            "technical_review": dict(technical_review or {}),
        })
        data["stocks"] = rows
        data["updated_at_utc"] = datetime.now(timezone.utc).isoformat()
        atomic_write_json(path(), data)
    return True, "aufgenommen; aktiv ab naechstem Bot-Start"
