"""eToro-Marktqualitaet: Bid/Ask, Datenalter und Spread-Grenzen."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
import logging

import config
from cost_engine import fallback_spread_pct, spread_pct_from_bid_ask

logger = logging.getLogger(__name__)


@dataclass
class QuoteQuality:
    symbol: str
    bid: float = 0.0
    ask: float = 0.0
    spread_pct: float = 0.0
    age_seconds: float | None = None
    checked: bool = False
    source: str = "fallback"
    reason: str = ""

    @property
    def mid(self):
        return (self.bid + self.ask) / 2 if self.bid > 0 and self.ask > 0 else 0.0


class MarketQualityClient:
    def __init__(self):
        self.cache = {}

    @staticmethod
    def _timestamp_age(ts, now):
        if not ts:
            return None
        try:
            dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
            return max(0, (now - dt).total_seconds())
        except Exception as exc:
            logger.debug("Quote-Zeitstempel unlesbar: %s", exc)
            return None

    def latest_quote(self, symbol, asset_type="stock", underdog=False, broker=None, instrument=None, broad=False):
        fallback = fallback_spread_pct(asset_type, underdog, broad)
        now = datetime.now(timezone.utc)
        cache_key = (str(symbol).upper(), asset_type)
        cached = self.cache.get(cache_key)
        if cached and (now - cached[0]).total_seconds() < float(getattr(config, "MARKET_QUOTE_CACHE_SECONDS", 10)):
            return cached[1]

        if broker is not None and instrument is not None and hasattr(broker, "latest_bid_ask"):
            try:
                raw = broker.latest_bid_ask(instrument)
                if raw:
                    bid = float(raw.get("bid") or 0)
                    ask = float(raw.get("ask") or 0)
                    if bid > 0 and ask > bid:
                        age = self._timestamp_age(raw.get("timestamp"), now)
                        out = QuoteQuality(
                            symbol, bid, ask, spread_pct_from_bid_ask(bid, ask, fallback),
                            age, True, str(raw.get("source") or "etoro_quote"), "",
                        )
                        self.cache[cache_key] = (now, out)
                        return out
            except Exception as exc:
                logger.debug("eToro Live-Bid/Ask nicht verfuegbar fuer %s: %s", symbol, exc)

        out = QuoteQuality(symbol, spread_pct=fallback, checked=False, source="fallback",
                           reason="keine eToro-Live-Quote verfuegbar")
        self.cache[cache_key] = (now, out)
        return out

    @staticmethod
    def acceptable(q: QuoteQuality, underdog=False, asset_type="stock", broad=False):
        if asset_type == "crypto":
            limit = float(getattr(config, "MAX_SPREAD_CRYPTO_PCT", 0.006))
        elif broad:
            limit = float(getattr(config, "MAX_SPREAD_BROAD_PCT", 0.008))
        elif underdog:
            limit = float(getattr(config, "MAX_SPREAD_UNDERDOG_PCT", 0.008))
        else:
            limit = float(getattr(config, "MAX_SPREAD_STOCK_PCT", 0.0035))
        live_bid_ask = bool(q.checked and q.bid > 0 and q.ask > q.bid)
        if live_bid_ask and q.age_seconds is not None and q.age_seconds > float(getattr(config, "MAX_QUOTE_AGE_SECONDS", 90)):
            return False, f"Quote veraltet ({q.age_seconds:.0f}s)"
        if q.spread_pct > limit:
            return False, f"Spread {q.spread_pct*100:.3f}% > Limit {limit*100:.3f}%"
        if broad and asset_type == "stock" and getattr(config, "REQUIRE_LIVE_QUOTE_FOR_BROAD_ENTRY", True) and not live_bid_ask:
            return False, "Broad-Aktie ohne echten eToro-Live-Bid/Ask"
        if getattr(config, "REQUIRE_LIVE_QUOTE_FOR_ENTRY", False) and not live_bid_ask and asset_type == "stock":
            return False, "eToro-Live-Bid/Ask fehlt"
        if getattr(config, "REQUIRE_LIVE_CRYPTO_QUOTE_FOR_ENTRY", False) and not live_bid_ask and asset_type == "crypto":
            return False, "eToro-Live-Krypto-Bid/Ask fehlt"
        return True, "ok"
