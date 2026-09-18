"""Brokergetrennte Transaktionskosten- und Netto-Edge-Engine.

Live-/Demo-Neueinstiege verwenden zwingend die offizielle eToro-What-if-
Kostenabfrage. Die statische Rechnung dient ausschliesslich Backtest, ML-Label
und Offline-Fallback, falls der Fallback explizit erlaubt wurde.
"""
from __future__ import annotations
from dataclasses import dataclass
import math
import config


def _finite(x, default=0.0):
    try:
        v = float(x)
        return v if math.isfinite(v) else default
    except Exception:
        return default


def _broker(broker=None) -> str:
    """Erlaubt sind in NEXUS genau zwei Broker.

    eToro rechnet seine Gebuehren in den Spread ein und weist keine
    separate Kommission aus. OKX dagegen berechnet eine explizite
    Handelsgebuehr je Seite -- die muss in die Kostenrechnung, sonst
    ueberschaetzt der Bot seinen Netto-Vorsprung systematisch.
    """
    value = str(broker or getattr(config, "BROKER", "etoro") or "etoro").strip().lower()
    if value not in ("etoro", "okx"):
        raise ValueError(f"TradingBot {config.VERSION_NEXUS} kennt nur eToro und OKX, erhalten: {value!r}")
    return value


class CostQuoteUnavailable(RuntimeError):
    """Die fuer einen Neueinstieg erforderliche Broker-Kostenquote fehlt."""


def commission_for(quantity: float, price: float, asset_type: str = "stock",
                   currency: str = "USD", broker=None, fee_pct=None) -> float:
    name = _broker(broker)
    if name != "okx":
        # eToro: Gebuehr steckt im Spread, keine separate Kommission.
        return 0.0
    notional = abs(_finite(quantity) * _finite(price))
    if notional <= 0:
        return 0.0
    # Konservativ mit der Taker-Gebuehr rechnen: der Bot handelt mit
    # Marktorders, also ist er praktisch immer Taker.
    #
    # ``fee_pct`` ist der bei OKX GEMESSENE Satz. Ohne ihn galt bis v8.1.3
    # ausschliesslich die Annahme aus der config -- am 25.08.2026 waren das
    # 0,10 % gegen tatsaechliche 0,35 %, und die Kostenhuerde lag dadurch bei
    # 0,90 % statt bei 1,65 %.
    if fee_pct is not None:
        try:
            gemessen = float(fee_pct)
            if gemessen > 0:
                return notional * gemessen
        except (TypeError, ValueError):
            pass
    satz = float(getattr(config, "OKX_TAKER_FEE_PCT", 0.0035))
    return notional * max(0.0, satz)


def regulatory_cost_for(quantity: float, price: float, asset_type: str = "stock",
                        currency: str = "USD", broker=None, side: str | None = None) -> float:
    _broker(broker)
    return 0.0


def slippage_pct(asset_type="stock", underdog=False, broad=False):
    if (asset_type or "stock").lower() == "crypto":
        return float(getattr(config, "CRYPTO_SLIPPAGE_PCT", 0.0015))
    base = float(getattr(config, "SLIPPAGE_PCT", 0.0005))
    if broad:
        base *= float(getattr(config, "BROAD_SLIPPAGE_FACTOR", 1.5))
    elif underdog:
        base *= float(getattr(config, "UNDERDOG_SLIPPAGE_FACTOR", 1.5))
    return base


def fallback_spread_pct(asset_type="stock", underdog=False, broad=False):
    if (asset_type or "stock").lower() == "crypto":
        return float(getattr(config, "FALLBACK_SPREAD_CRYPTO_PCT", 0.0020))
    if broad:
        return float(getattr(config, "FALLBACK_SPREAD_BROAD_PCT", 0.0060))
    if underdog:
        return float(getattr(config, "FALLBACK_SPREAD_UNDERDOG_PCT", 0.0030))
    return float(getattr(config, "FALLBACK_SPREAD_STOCK_PCT", 0.0008))


def spread_pct_from_bid_ask(bid, ask, fallback=None):
    bid = _finite(bid); ask = _finite(ask)
    if bid > 0 and ask > bid:
        mid = (bid + ask) / 2.0
        return (ask - bid) / mid if mid > 0 else (fallback or 0.0)
    return fallback if fallback is not None else 0.0


@dataclass
class CostBreakdown:
    broker: str
    notional: float
    quantity: float
    price: float
    buy_commission: float
    sell_commission: float
    regulatory_cost: float
    spread_pct: float
    spread_cost: float
    slippage_pct_each_side: float
    slippage_cost: float
    total_cost: float
    total_cost_pct: float
    required_edge_pct: float
    source: str = "fallback"

    def as_text(self, currency="USD"):
        return (
            f"Broker: {self.broker.upper()}\nPositionswert: {self.notional:,.2f} {currency}\n"
            f"Kaufgebuehr/Markup: {self.buy_commission:,.2f} {currency}\n"
            f"Verkaufsgebuehr/Markup: {self.sell_commission:,.2f} {currency}\n"
            f"Steuern/Haltekosten: {self.regulatory_cost:,.4f} {currency}\n"
            f"Bid/Ask-Kosten: {self.spread_cost:,.2f} {currency} ({self.spread_pct*100:.3f} %)\n"
            f"Slippage-Puffer: {self.slippage_cost:,.2f} {currency} "
            f"({self.slippage_pct_each_side*2*100:.3f} % Roundtrip)\n"
            f"Gesamtkosten: {self.total_cost:,.2f} {currency} ({self.total_cost_pct*100:.3f} %)\n"
            f"Mindest-Edge inkl. Puffer: {self.required_edge_pct*100:.3f} %"
        )


def estimate_roundtrip(quantity: float, price: float, asset_type="stock", currency="USD",
                       bid=None, ask=None, underdog=False, broker=None, broad=False,
                       fee_pct=None) -> CostBreakdown:
    """Konservative Offline-Schaetzung; keine Live-Freigabequelle.

    ``fee_pct`` ist der beim Broker gemessene Gebuehrensatz. Wird er
    uebergeben, gilt er statt der Annahme aus der config -- sonst rechnet die
    Kostenhuerde mit einem Wert, den niemand geprueft hat.
    """
    quantity = abs(_finite(quantity)); price = max(_finite(price), 1e-12)
    notional = quantity * price
    br = _broker(broker)
    sp = spread_pct_from_bid_ask(bid, ask, fallback_spread_pct(asset_type, underdog, broad))
    slip = slippage_pct(asset_type, underdog, broad)
    spread_cost = notional * sp
    slippage_cost = notional * (2 * slip)
    # Ab v7: explizite Handelsgebuehren beider Seiten. Bei eToro sind das 0,
    # bei OKX nicht -- und ohne sie ueberschaetzt der Bot seinen Vorsprung.
    buy_commission = commission_for(quantity, price, asset_type, currency, br, fee_pct=fee_pct)
    sell_commission = commission_for(quantity, price, asset_type, currency, br, fee_pct=fee_pct)
    total = spread_cost + slippage_cost + buy_commission + sell_commission
    total_pct = total / notional if notional > 0 else 1.0
    required = total_pct * float(getattr(config, "EDGE_COST_MULTIPLIER", 1.5)) + float(getattr(config, "EDGE_SAFETY_MARGIN_PCT", 0.0015))
    source = "live_quote_local_model" if bid and ask and _finite(ask) > _finite(bid) > 0 else "offline_fallback"
    return CostBreakdown(br, notional, quantity, price, buy_commission, sell_commission, 0.0, sp,
                         spread_cost, slip, slippage_cost, total, total_pct, required, source)


def estimate_roundtrip_with_broker(broker_obj, quantity: float, price: float,
                                   asset_type="stock", currency="USD", bid=None,
                                   ask=None, underdog=False, instrument=None, broad=False) -> CostBreakdown:
    """Harte eToro-What-if-Kostenquelle fuer Neueinstiege."""
    _broker(getattr(broker_obj, "name", None))
    quote = None
    try:
        quote = broker_obj.dynamic_cost_quote(instrument, quantity, price, action="open")
    except Exception as exc:
        if bool(getattr(config, "ETORO_REQUIRE_COST_QUOTE", True)):
            raise CostQuoteUnavailable(f"eToro What-if-Kosten nicht abrufbar: {exc}") from exc
    if not quote:
        if bool(getattr(config, "ETORO_REQUIRE_COST_QUOTE", True)):
            raise CostQuoteUnavailable("eToro What-if-Kosten fehlen")
        return estimate_roundtrip(quantity, price, asset_type, currency, bid=bid, ask=ask,
                                  underdog=underdog, broad=broad, broker="etoro")

    notional = abs(_finite(quantity)) * max(_finite(price), 1e-12)
    raw = quote.get("components", {}) if isinstance(quote, dict) else {}
    c = {str(k).strip().lower(): v for k, v in raw.items()}
    def cv(name): return max(0.0, _finite(c.get(str(name).lower(), 0.0)))

    markup_one = cv("markup")
    spread_one = cv("marketspread")
    tx_one = cv("transactionfee")
    tax_one = cv("sdrt") + cv("tax")
    overnight = cv("overnightfee")
    weekend = cv("overweekendfee")
    hold_hours = max(0.0, float(getattr(config, "TIME_STOP_HOURS", 72) or 0))
    nights = int(math.ceil(hold_hours / 24.0)) if hold_hours > 0 else 0
    weekends = int(math.ceil(nights / 7.0)) if nights > 0 else 0
    holding_cost = overnight * nights + weekend * weekends

    buy_commission = markup_one + tx_one
    sell_commission = markup_one + tx_one
    regulatory = tax_one + holding_cost
    spread_cost = spread_one * 2.0
    sp = spread_cost / notional if notional > 0 else 0.0
    slip = max(0.0, float(getattr(config, "ETORO_EXTRA_SLIPPAGE_PCT", 0.0003)))
    slippage_cost = notional * 2.0 * slip
    total = buy_commission + sell_commission + regulatory + spread_cost + slippage_cost
    total_pct = total / notional if notional > 0 else 1.0
    required = total_pct * float(getattr(config, "EDGE_COST_MULTIPLIER", 1.5)) + float(getattr(config, "EDGE_SAFETY_MARGIN_PCT", 0.0015))
    stamp = str(quote.get("last_updated") or "")
    return CostBreakdown("etoro", notional, abs(_finite(quantity)), max(_finite(price), 1e-12),
                         buy_commission, sell_commission, regulatory, sp, spread_cost,
                         slip, slippage_cost, total, total_pct, required,
                         source=f"etoro_what_if:{stamp}" if stamp else "etoro_what_if")


def estimate_one_way_cost(quantity: float, price: float, asset_type="stock", currency="USD",
                          side="buy", bid=None, ask=None, underdog=False, broker=None,
                          include_execution_friction=True, broad=False) -> dict:
    quantity = abs(_finite(quantity)); price = max(_finite(price), 1e-12)
    notional = quantity * price
    br = _broker(broker)
    if include_execution_friction:
        sp = spread_pct_from_bid_ask(bid, ask, fallback_spread_pct(asset_type, underdog, broad))
        slip = slippage_pct(asset_type, underdog, broad)
        spread_cost = notional * sp / 2.0
        slippage_cost = notional * slip
    else:
        spread_cost = 0.0; slippage_cost = 0.0
    total = spread_cost + slippage_cost
    return {"broker": br, "side": side, "commission": 0.0, "regulatory": 0.0,
            "spread": spread_cost, "slippage": slippage_cost, "total": total,
            "execution_friction_included": bool(include_execution_friction)}


def economically_viable(expected_gross_move_pct: float, breakdown: CostBreakdown):
    edge = _finite(expected_gross_move_pct) - breakdown.total_cost_pct
    return {"allowed": _finite(expected_gross_move_pct) >= breakdown.required_edge_pct,
            "expected_gross_move_pct": _finite(expected_gross_move_pct),
            "net_edge_pct": edge, "required_edge_pct": breakdown.required_edge_pct,
            "cost_pct": breakdown.total_cost_pct, "breakdown": breakdown}


def reference_label_threshold(price: float, asset_type="stock", currency="USD", underdog=False, broker=None):
    ref = float(getattr(config, "ML_REFERENCE_NOTIONAL", 5000.0))
    price = max(_finite(price), 0.01)
    qty = max(ref / price, 1.0 if asset_type != "crypto" else 1e-8)
    required = estimate_roundtrip(qty, price, asset_type, currency, underdog=underdog, broker="etoro").required_edge_pct
    return max(float(getattr(config, "ML_LABEL_MIN_MOVE", 0.001)), required)
