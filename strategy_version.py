"""Strategieversion als Fingerabdruck der wirksamen Parameter (v8.1.3).

WARUM DAS NOETIG IST
====================
Etappe A der Strategy Evolution Engine zeichnet Trades auf. Ohne die Frage
"unter welchen Einstellungen ist dieser Trade entstanden?" ist die
Aufzeichnung spaeter wertlos: Aendert Georg einen Stop-Faktor, mischen sich
alte und neue Ergebnisse in derselben Statistik, und jede Auswertung
vergleicht Aepfel mit Birnen.

Die Version ist deshalb ein Hash ueber genau die Parameter, die das
Handelsverhalten bestimmen. Gleiche Parameter -> gleiche Version, auch nach
einem Neustart oder auf einem anderen Geraet. Ein geaenderter Parameter ->
neue Version.

WAS NICHT IN DEN HASH GEHOERT
=============================
Alles, was das Handelsverhalten nicht veraendert: Schluessel, Pfade,
Telegram-Einstellungen, Anzeigeoptionen, Berichtszeiten. Sonst wechselt die
Version bei jeder Kosmetikaenderung und die Statistik zersplittert in lauter
Einzelstichproben, die niemals aussagekraeftig werden.

BEWUSST NUR BESCHREIBEND
========================
Dieses Modul liest Parameter und rechnet einen Hash. Es aendert nichts, es
aktiviert nichts, und es kann keinen Handel ausloesen. Die Aktivierungslogik
kommt fruehestens in v8.2 -- und auch dort nur mit Freigabe.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from typing import Any

logger = logging.getLogger(__name__)

# Parameter, die das Handelsverhalten bestimmen. Bewusst eine feste,
# sortierte Liste statt "alles aus der config": nur so bleibt der Hash
# stabil, wenn irgendwo eine neue Anzeigeoption dazukommt.
STRATEGIE_PARAMETER: tuple[str, ...] = (
    # -- Ein- und Ausstieg -------------------------------------------------
    "ACTIVE_PROFILE",
    "SMA_FAST", "SMA_SLOW", "RSI_PERIOD", "RSI_BUY_MAX", "RSI_SELL_MIN",
    "ATR_PERIOD", "ATR_STOP_FACTOR", "ATR_TAKE_FACTOR",
    "MIN_RR_RATIO", "TRAILING_STOP_ENABLED", "TRAILING_STOP_FACTOR",
    "TIME_STOP_ENABLED", "TIME_STOP_DAYS",
    # -- Risiko ------------------------------------------------------------
    "RISK_PER_TRADE_PCT", "MAX_OPEN_POSITIONS", "MAX_POSITION_PCT",
    "DAILY_LOSS_LIMIT_PCT", "MAX_DRAWDOWN_PCT", "CRYPTO_QUOTE_MAX_PCT",
    # -- Kosten und Edge ---------------------------------------------------
    "MIN_NET_EDGE_PCT", "COST_MODEL_ENABLED",
    "OKX_TAKER_FEE_PCT", "OKX_MIN_POSITION_VALUE", "OKX_CASH_RESERVE_PCT",
    # -- Universum ---------------------------------------------------------
    "STOCK_CORE_LIMIT", "STOCK_UNIVERSE_DYNAMIC_LIMIT",
    "STOCK_UNIVERSE_ACTIVE_LIMIT", "STOCK_UNIVERSE_FOCUS_LIMIT",
    "CRYPTO_CORE_LIMIT", "CRYPTO_UNIVERSE_ACTIVE_LIMIT",
    "CRYPTO_UNIVERSE_FOCUS_LIMIT", "CRYPTO_UNIVERSE_MIN_QUOTE_VOLUME",
    "CRYPTO_UNIVERSE_MAX_SPREAD_PCT", "CRYPTO_UNIVERSE_MIN_AGE_DAYS",
    # -- Signalquellen -----------------------------------------------------
    "NEWS_FILTER_ENABLED", "NEWS_BLOCK_SCORE", "NEWS_BLOCK_SCORE_UNDERDOG",
    "NEWS_SELL_ON_CRISIS", "NEWS_EXIT_THRESHOLD", "NEWS_MARKET_THRESHOLD",
    "NEWS_MAX_SIGNAL_REPETITIONS", "EVENT_INTELLIGENCE_ENABLED",
    "EARNINGS_ENTRY_MODE", "ML_ENABLED", "ML_MIN_PROBABILITY",
    "AI_ENABLED", "AI_VETO_ENABLED",
)

_LOCK = threading.RLock()
_CACHE: dict[str, Any] = {"hash": "", "parameter": {}, "quelle": ""}


def _wert(cfg, name: str) -> Any:
    """Ein Parameterwert in stabiler, vergleichbarer Form."""
    wert = getattr(cfg, name, None)
    if wert is None:
        return None
    if isinstance(wert, bool):
        return bool(wert)
    if isinstance(wert, (int, float)):
        # Fliesskomma auf 6 Stellen: sonst erzeugt 0.1+0.2 eine neue Version.
        return round(float(wert), 6)
    if isinstance(wert, (list, tuple, set)):
        return sorted(str(x) for x in wert)
    if isinstance(wert, dict):
        return {str(k): str(v) for k, v in sorted(wert.items())}
    return str(wert)


def parameter(cfg=None) -> dict:
    """Die wirksamen Strategieparameter als Klartext-Wörterbuch.

    Fehlende Parameter werden ausgelassen und nicht als 0 gefuehrt --
    "gibt es nicht" und "steht auf null" sind verschiedene Dinge.
    """
    if cfg is None:
        import config as cfg  # lokal, damit Testcode die config ersetzen kann
    daten = {}
    for name in STRATEGIE_PARAMETER:
        wert = _wert(cfg, name)
        if getattr(cfg, "__name__", "") == "config" and name.startswith("NEWS_"):
            from live_settings import news_rule
            wert = news_rule(name, wert)
        if wert is not None:
            daten[name] = wert
    return daten


def berechne(cfg=None) -> str:
    """Der Versionshash: 12 Hexstellen ueber die Parameter.

    12 Stellen sind kurz genug fuer Telegram und eine Tabellenspalte und
    weit jenseits jeder realistischen Kollisionsgefahr bei einer Handvoll
    Strategieversionen pro Jahr.
    """
    daten = parameter(cfg)
    roh = json.dumps(daten, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(roh.encode("utf-8")).hexdigest()[:12]


def aktuell(cfg=None) -> str:
    """Der Hash der laufenden Konfiguration -- mit Zwischenspeicher.

    Die Einstellungen koennen sich zur Laufzeit aendern (Hot Reload seit
    v8.1.2). Deshalb wird bei jedem Aufruf neu gerechnet, aber nur der
    guenstige Teil: das Einsammeln der Parameter. Faellt dabei etwas aus,
    gilt der letzte bekannte Hash statt eines leeren Feldes.
    """
    try:
        neu = berechne(cfg)
    except Exception as exc:
        logger.debug("Strategieversion nicht berechenbar: %s", exc)
        with _LOCK:
            return str(_CACHE.get("hash") or "")
    with _LOCK:
        if neu != _CACHE.get("hash"):
            if _CACHE.get("hash"):
                logger.info("Strategieversion gewechselt: %s -> %s", _CACHE["hash"], neu)
            _CACHE["hash"] = neu
            _CACHE["parameter"] = parameter(cfg)
    return neu


def beschreibung(cfg=None) -> dict:
    """Version plus Parameter -- fuer Berichte und spaetere Versionsvergleiche."""
    return {"version": aktuell(cfg), "parameter": parameter(cfg)}


def unterschiede(a: dict, b: dict) -> list[dict]:
    """Welche Parameter unterscheiden zwei Versionen?

    Wird in Etappe B gebraucht, um einen Vorschlag als konkreten
    Parameterwechsel darzustellen statt als Fliesstext.
    """
    namen = sorted(set(a or {}) | set(b or {}))
    return [{"parameter": n, "vorher": (a or {}).get(n), "nachher": (b or {}).get(n)}
            for n in namen if (a or {}).get(n) != (b or {}).get(n)]


__all__ = ["STRATEGIE_PARAMETER", "parameter", "berechne", "aktuell",
           "beschreibung", "unterschiede"]
