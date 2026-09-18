"""Read-only comparison of funded spot routes for one concrete base quantity.

No symbol substitution in existing positions. Instrument IDs and settlement
currencies come exclusively from the authenticated account catalogue. A
roundtrip quote is a cost estimate at the observation time, never a promise
about a future sale. Cash conversion orders are deliberately not generated.
"""
from __future__ import annotations

import math
from contracts import Instrument, SimpleContract
from broker.base import BrokerFehler


def _number(value, *, positive=False):
    if isinstance(value, bool):
        return None
    try:
        n = float(value)
    except (ValueError, TypeError):
        return None
    return n if math.isfinite(n) and (n > 0 if positive else n >= 0) else None


def routes(broker, symbol):
    if not broker.client.hat_zugangsdaten:
        raise BrokerFehler("Marktwahl braucht den authentifizierten OKX-Kontokatalog")
    balances = broker.client.balances()
    primary = str(getattr(broker, "quote_ccy", "") or "EUR").upper()
    result = []
    for meta in broker.client.instruments().values():
        if not meta.ist_live or str(meta.base_ccy).upper() != str(symbol).upper():
            continue
        accepted = set(meta.trade_quote_ccy_list or (meta.quote_ccy,))
        # 10.1.10: Alle ausdruecklich freigegebenen Abrechnungswaehrungen sind
        # moegliche Lanes (z. B. USD nach Nutzerfreigabe wegen der OKX-USD-
        # Instrumentumstellung); nicht mehr fest EUR/USDC.
        for lane in broker.allowed_quotes:
            lane = str(lane).upper()
            cash = _number((balances.get(lane) or {}).get("cash"), positive=True)
            if lane not in accepted or cash is None:
                continue
            instrument = Instrument(name=str(symbol).upper(), asset_type="crypto",
                contract=SimpleContract(str(symbol).upper(), meta.quote_ccy,
                                        localSymbol=meta.inst_id),
                currency=meta.quote_ccy, sector="crypto", exchange="OKX")
            result.append((instrument, meta, lane, cash))
    return sorted(result, key=lambda x: (x[2] != primary, x[1].inst_id))


def initial_route(broker, symbol, current):
    available = routes(broker, symbol)
    if not available:
        lanes = "/".join(broker.allowed_quotes)
        raise BrokerFehler(f"{symbol}: kein kontoseitig freigegebener, finanzierter {lanes}-Markt")
    # Within a settlement lane keep the selector's already qualified market.
    preferred = next((r for r in available if r[2] == available[0][2]
                      and r[1].inst_id == current), available[0])
    return {"inst_id": preferred[1].inst_id, "trade_quote_ccy": preferred[2]}


def compare_routes(broker, symbol, quantity, cfg, *, reserved=None):
    """Compare equal base quantities using fee-inclusive buy AND sell VWAPs."""
    qty = _number(quantity, positive=True)
    if qty is None:
        raise BrokerFehler("Kostenvergleich ohne positive Planmenge")
    rows, errors = [], []
    for instrument, meta, lane, cash in routes(broker, symbol):
        try:
            if qty < float(meta.min_size):
                raise BrokerFehler("Planmenge unter Marktminimum")
            buy = broker.execution_quote(instrument, qty, side="buy")
            sell = broker.execution_quote(instrument, qty, side="sell")
            fees = broker.client.trade_fee(inst_id=meta.inst_id)
            fee = _number(fees.get("taker"))
            if fee is None or not fees.get("taker_known", False):
                raise BrokerFehler("kein expliziter kontoseitiger Taker-Gebuehrensatz")
            quote_eur = _number(broker.quote_conversion_rate(meta.quote_ccy, "EUR"), positive=True)
            quote_lane = _number(broker.quote_conversion_rate(meta.quote_ccy, lane), positive=True)
            if quote_eur is None or quote_lane is None:
                raise BrokerFehler("beobachtete Waehrungsumrechnung fehlt")
            ask, bid = float(buy["vwap"]), float(sell["vwap"])
            if any(_number(v, positive=True) is None for v in
                   (ask, bid, buy["best"], sell["best"])):
                raise BrokerFehler("Orderbuchpreise fehlen oder sind ungueltig")
            if any(_number(v) is None for v in (buy["slippage_pct"], sell["slippage_pct"])):
                raise BrokerFehler("Orderbuchkosten fehlen oder sind ungueltig")
            mid = (float(buy["best"]) + float(sell["best"])) / 2
            spread = (float(buy["best"]) - float(sell["best"])) / mid
            if spread < 0 or spread > float(getattr(cfg, "MAX_SPREAD_CRYPTO_PCT", .006)):
                raise BrokerFehler("Spread ausserhalb der Kaufgrenze")
            impact = float(getattr(cfg, "CRYPTO_MAX_BOOK_IMPACT_PCT", .005))
            if max(buy["slippage_pct"], sell["slippage_pct"]) > impact:
                raise BrokerFehler("Planmenge ueberschreitet die Orderbuchgrenze")
            reserved_cash = max(0., float(reserved(lane) if reserved else 0.))
            usable = max(0., cash - reserved_cash) * (1 - float(getattr(cfg, "OKX_CASH_RESERVE_PCT", .05)))
            if qty * ask * (1 + fee) * quote_lane > usable:
                raise BrokerFehler("Planmenge nicht aus freiem Guthaben finanzierbar")
            cost = qty * (ask * (1 + fee) - bid * (1 - fee)) * quote_eur
            basis = qty * mid * quote_eur
            rows.append({"inst_id": meta.inst_id, "trade_quote_ccy": lane,
                "quantity": qty, "buy_vwap": ask, "sell_vwap": bid,
                "taker": fee, "cost_eur": cost, "cost_bps": cost / basis * 10000,
                "market_quote_ccy": meta.quote_ccy, "quote_rate_to_eur": quote_eur,
                "quote_rate_to_settlement": quote_lane,
                "book_timestamp_ms": min(buy["timestamp_ms"], sell["timestamp_ms"]),
                "environment": "DEMO" if broker.demo else "LIVE"})
        except (BrokerFehler, ValueError, TypeError, KeyError) as exc:
            errors.append({"inst_id": meta.inst_id, "trade_quote_ccy": lane, "reason": str(exc)})
    primary_ccy = str(getattr(broker, "quote_ccy", "") or "EUR").upper()
    primary = min((r for r in rows if r["trade_quote_ccy"] == primary_ccy),
                  key=lambda r: r["cost_bps"], default=None)
    alternative = min((r for r in rows if r["trade_quote_ccy"] != primary_ccy),
                      key=lambda r: r["cost_bps"], default=None)
    chosen = primary or alternative
    reason = (f"{primary_ccy} nach Ausfuehrungs- und Kostenpruefung" if primary
              else f"kein geeigneter finanzierter {primary_ccy}-Markt")
    advantage = max(0., float(getattr(cfg, "OKX_USDC_MIN_COST_ADVANTAGE_BPS", 15)))
    if primary and alternative and primary["cost_bps"] - alternative["cost_bps"] >= advantage:
        chosen = alternative
        reason = (f"{alternative['trade_quote_ccy']} mindestens {advantage:g} "
                  "Basispunkte guenstiger")
    if chosen is None:
        raise BrokerFehler("Kein geeigneter Kostenpfad: " + "; ".join(
            f"{r['inst_id']}/{r['trade_quote_ccy']}: {r['reason']}" for r in errors))
    return {**chosen, "reason": reason, "comparison": rows, "excluded": errors}
