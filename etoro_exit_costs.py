"""Position-bound exit observations; only explicit profit exits have a cost gate.

This module never submits or modifies an order. Native broker SL/TP remain
independent. Bid and entry execution already contain price spreads; those
components are not deducted a second time from estimated cash profit.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import math


def number(value, *, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("Zahlenbeleg fehlt")
    result = float(value)
    if not math.isfinite(result) or result < 0 or (positive and result == 0):
        raise ValueError("Zahlenbeleg ungueltig")
    return result


def timestamp(value):
    if isinstance(value, bool):
        raise ValueError("Zeitstempel ungueltig")
    if isinstance(value, (int, float)):
        value = number(value, positive=True)
        return datetime.fromtimestamp(value / 1000 if value > 10_000_000_000 else value, timezone.utc)
    out = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if out.tzinfo is None:
        raise ValueError("Zeitstempel ohne Zeitzone")
    return out.astimezone(timezone.utc)


def fresh_quote(broker, instrument, *, now=None):
    """No exchange-clock inference; future/missing/stale broker times fail closed."""
    fixed_now = now
    raw = broker.latest_bid_ask(instrument) or {}
    now = now or datetime.now(timezone.utc)
    bid = number(raw.get("bid"), positive=True)
    ask = number(raw["ask"], positive=True) if raw.get("ask") not in (None, 0) else None
    at = timestamp(raw.get("timestamp"))
    if (ask is not None and ask < bid) or not 0 <= (now - at).total_seconds() <= 180:
        raise ValueError("Brokerkurs fehlt, ist veraltet oder zeitlich widerspruechlich")
    tradable = raw.get("broker_tradable")
    if not isinstance(tradable, bool):
        tradable = None
        probe = getattr(broker, "market_session_status", None)
        if callable(probe):
            session = probe(instrument) or {}
            # The tradability flag must refer to a current broker observation.
            try:
                session_at = timestamp(session.get("timestamp"))
                if 0 <= (now - session_at).total_seconds() <= 180:
                    flag = session.get("broker_tradable")
                    tradable = flag if isinstance(flag, bool) else None
            except (TypeError, ValueError, OverflowError):
                pass
    if not 0 <= ((fixed_now or datetime.now(timezone.utc)) - at).total_seconds() <= 180:
        raise ValueError("Brokerkurs waehrend Handelbarkeitspruefung veraltet")
    return {"bid": bid, "ask": ask, "timestamp": at.isoformat(),
            "broker_tradable": tradable, "source": str(raw.get("source") or "eToro")}


def own_position(record, broker):
    return bool(record and record.source == "BOT" and record.ownership_chain_complete
        and not record.user_observe_locked and record.owned_position_id_set()
        and record.management_mode in {"AUTO", "PENDING_CONFIRMATION"}
        and record.reconciliation_status == "CONFIRMED_OPEN"
        and record.broker_account_fingerprint == broker.account_fingerprint()
        and record.broker_environment == ("DEMO" if broker.paper else "LIVE")
        and record.broker_instrument_id and record.broker_snapshot_id)


def entry_cost_from_receipt(raw, record, quantity):
    """Only an exact native open execution can establish entry cash costs.

    The first version deliberately supports one long, unleveraged real-stock
    position. Ambiguous multi-position cost allocations remain unknown.
    """
    pids = record.owned_position_id_set()
    orders = {str(x) for x in record.entry_order_ids}
    asset = raw.get("asset") or {}
    cid = str(raw.get("accountId") or "")
    account = hashlib.sha256(f"etoro|{'demo' if record.broker_environment == 'DEMO' else 'live'}|cid:{cid}".encode()).hexdigest()[:24]
    if (len(pids) != 1 or len(orders) != 1 or not cid.isdigit()
            or account != record.broker_account_fingerprint
            or str(record.currency or "").upper() != "USD"
            or str(raw.get("orderId")) not in orders
            or raw.get("action") != "open" or raw.get("transaction") != "buy"
            or str(raw.get("orderCurrency") or "").upper() != "USD"
            or str(asset.get("currency") or "").upper() != "USD"
            or str(asset.get("instrumentId") or "") != str(record.broker_instrument_id)
            or str(asset.get("settlementType") or "").lower() != "real"
            or asset.get("side") != "long" or asset.get("leverage") != 1):
        raise ValueError("Einstiegskosten nicht exakt derselben Position/Kontodomaene zugeordnet")
    executions = raw.get("positionExecutions") or []
    if len(executions) != 1 or str(executions[0].get("positionId")) not in pids:
        raise ValueError("Entry-Ausfuehrung nicht eindeutig")
    ex = executions[0]
    op = ex.get("openingData") or {}
    if (str(ex.get("state") or "").lower() != "open"
            or str(op.get("orderId")) not in orders):
        raise ValueError("Offene Entry-Ausfuehrung nicht belegt")
    units, price = number(op.get("units"), positive=True), number(op.get("avgPrice"), positive=True)
    remaining = number(record.quantity, positive=True)
    if (quantity > remaining + 1e-8 or remaining > units + 1e-8
            or not math.isclose(price, float(record.avg_cost), rel_tol=1e-9, abs_tol=1e-8)):
        raise ValueError("Entry-Menge oder Einstand widerspricht Positionsbeleg")
    timestamp(op.get("executionTime"))
    fees = number(op.get("fees")) + number(op.get("taxes"))
    return {"entry_price": price, "entry_fee": fees * quantity / units,
            "currency": "USD", "source": "ETORO_V2_OPENING_DATA"}


def _entry_cost(broker, record, quantity):
    orders = list(record.entry_order_ids)
    if len(orders) != 1:
        raise ValueError("Mehrere Einstiegsorders: Kostenzuordnung offen")
    path = "/api/v2/trading/info/demo/orders:lookup" if broker.paper else "/api/v2/trading/info/orders:lookup"
    raw = broker._request("GET", path, params={"orderId": str(orders[0])})
    return entry_cost_from_receipt(raw, record, quantity)


def parse_close_costs(raw, *, instrument_id, fetched_at, now=None):
    """Strict what-if response; missing components do not become zero costs."""
    now = now or datetime.now(timezone.utc)
    if not isinstance(raw, dict) or str(raw.get("instrumentId")) != str(instrument_id):
        raise ValueError("Verkaufskosten fuer anderes/unbekanntes Instrument")
    generated = timestamp(raw.get("lastUpdated"))
    fetched = timestamp(fetched_at)
    if not all(0 <= (now - at).total_seconds() <= 180 for at in (generated, fetched)):
        raise ValueError("Verkaufskosten nicht aktuell")
    components = {}
    for item in raw.get("costs") or []:
        if not isinstance(item, dict) or str(item.get("currency") or "").upper() != "USD":
            raise ValueError("Kostenwaehrung fehlt oder widerspricht USD")
        key = str(item.get("costType") or "").lower()
        if key in components:
            raise ValueError("Doppelte Kostenkomponente")
        components[key] = number(item.get("amount"))
    required = {"markup", "marketspread", "transactionfee", "overnightfee", "overweekendfee", "sdrt"}
    if set(components) != required:
        raise ValueError("Vollstaendige aktuelle Kostenkomponenten nicht belegt")
    # A current exit quote cannot establish previously accrued holding costs.
    # This version supports real x1 positions, which must have explicit zeros.
    if components["overnightfee"] != 0 or components["overweekendfee"] != 0:
        raise ValueError("Haltekosten brauchen zusaetzlichen positionsbezogenen Abrechnungsbeleg")
    if components["markup"] != 0:
        raise ValueError("Markup-Abrechnung gegenueber aktuellem Broker-Bid nicht eindeutig belegt")
    return {"components": components, "exit_fee_estimate": components["transactionfee"] + components["sdrt"],
            "currency": "USD", "last_updated": generated.isoformat(), "fetched_at": fetched.isoformat(),
            "source": "eToro what-if close", "spread_in_price": True}


def assess(broker, instrument, record, quantity, *, exit_kind="MONITOR", quote=None, now=None):
    """Return an explicit order permission; no profit gate on risk exits."""
    fixed_now = now
    now = now or datetime.now(timezone.utc)
    safety = exit_kind in {"STOP_LOSS", "TIME_STOP", "NEWS_RISK", "STRATEGY_EXIT"}
    out = {"position_id": ",".join(sorted(record.owned_position_id_set())),
        "symbol": str(instrument.name), "exit_kind": exit_kind, "decision": "UNKNOWN",
        "net_profit_estimate": None, "currency": "USD", "entry_fee": None,
        "exit_fee_estimate": None, "execution_buffer": None, "quote_price": None,
        "quote_at": None, "cost_at": None, "reason": "", "broker_tp_independent": True,
        "allow_order": False, "assessed_at": now.isoformat(), "broker_tradable": None}
    try:
        qty = number(quantity, positive=True)
        if not own_position(record, broker) or qty > number(record.quantity, positive=True) + 1e-8:
            raise ValueError("Eigene offene Position nicht exakt bestaetigt")
        quote = quote or fresh_quote(broker, instrument, now=fixed_now)
        now = fixed_now or datetime.now(timezone.utc)
        bid = number(quote.get("bid"), positive=True)
        age = (now - timestamp(quote.get("timestamp"))).total_seconds()
        if not 0 <= age <= 180:
            raise ValueError("Brokerkurs nicht aktuell")
        out.update(quote_price=bid, quote_at=quote["timestamp"], broker_tradable=quote.get("broker_tradable"))
        if quote.get("broker_tradable") is False:
            raise ValueError("Broker meldet Position/Instrument nicht handelbar; keine neue Schliessorder")
        if safety:
            out.update(decision="SAFETY_BYPASS", allow_order=True,
                reason="Risikoausstieg wartet nicht auf Nettogewinn oder Kostenbelege"
                    + ("; Handelbarkeitsflag fehlt, frischer Brokerkurs vorhanden" if quote.get("broker_tradable") is None else ""))
            return out
        if exit_kind != "PROFIT":
            out["reason"] = "Broker-SL/TP bleiben unveraendert und unabhaengig; Kosten werden vor Software-Gewinnmitnahme geprueft"
            return out
        if quote.get("broker_tradable") is not True:
            raise ValueError("Aktuelle Broker-Handelbarkeit fuer Gewinnmitnahme nicht belegt")
        entry = _entry_cost(broker, record, qty)
        out["entry_fee"] = entry["entry_fee"]
        costs = broker.dynamic_close_cost_quote(instrument, next(iter(record.owned_position_id_set())), qty)
        # Cost/entry lookups may take time. Re-read the executable bid and
        # broker state after them instead of permitting an expired observation.
        quote = fresh_quote(broker, instrument, now=fixed_now)
        now = fixed_now or datetime.now(timezone.utc)
        bid = number(quote["bid"], positive=True)
        if not 0 <= (now - timestamp(quote["timestamp"])).total_seconds() <= 180:
            raise ValueError("Brokerkurs nach Kostenpruefung nicht mehr aktuell")
        out.update(quote_price=bid, quote_at=quote["timestamp"], broker_tradable=quote["broker_tradable"])
        if quote["broker_tradable"] is not True:
            raise ValueError("Broker-Handelbarkeit nach Kostenpruefung nicht bestaetigt")
        fee = number(costs.get("exit_fee_estimate"))
        if (costs.get("currency") != "USD" or costs.get("spread_in_price") is not True
                or not all(0 <= (now - timestamp(costs.get(field))).total_seconds() <= 180
                    for field in ("last_updated", "fetched_at"))):
            raise ValueError("Verkaufskosten nicht aktuell/vollstaendig")
        import config
        buffer_pct = number(float(getattr(config, "ETORO_EXTRA_SLIPPAGE_PCT", .0003)))
        buffer = bid * qty * buffer_pct
        net = (bid - entry["entry_price"]) * qty - entry["entry_fee"] - fee - buffer
        out.update(exit_fee_estimate=fee, execution_buffer=buffer, net_profit_estimate=net,
            cost_at=costs["last_updated"], decision="ALLOW" if net > 0 else "BLOCK_PROFIT",
            allow_order=net > 0, reason="Positive Nettoschaetzung nach expliziten Kosten und Ausfuehrungspuffer"
                if net > 0 else "Kursziel erreicht, aber keine positive Nettoschaetzung nach Kosten/Puffer")
    except ValueError as exc:
        out["reason"] = str(exc)[:240]
    except Exception as exc:
        out["reason"] = "Aktueller Ausstiegsbeleg nicht verfuegbar: " + type(exc).__name__
    return out


def quote_exit_decision(record, quantity, quote, *, now=None):
    """Fixed pending protection/time exits before any historical candle read.

    Confirmed native SL/TP are monitored, not duplicated by a second close.
    PULSAR callers retain their separate frozen holding-time policy.
    """
    now = now or datetime.now(timezone.utc)
    price = number(quote.get("bid"), positive=True)
    if (not 0 <= (now - timestamp(quote.get("timestamp"))).total_seconds() <= 180
            or quote.get("broker_tradable") is False
            or not math.isclose(number(quantity, positive=True), number(record.quantity, positive=True), rel_tol=1e-9)):
        return None
    if record.management_mode == "PENDING_CONFIRMATION":
        stop, take = number(record.planned_stop, positive=True), number(record.planned_take, positive=True)
        if stop >= take:
            return None
        if price <= stop:
            return {"kind": "STOP_LOSS", "label": "CLIENT-STOP", "reason": "Client-Stop bei unbestaetigtem Broker-Schutz"}
    import config
    if getattr(config, "TIME_STOP_ENABLED", True) and getattr(record, "entry_time", None):
        opened = timestamp(record.entry_time)
        hours = (now-opened).total_seconds()/3600
        if (hours >= float(getattr(config, "TIME_STOP_HOURS", 72))
                and price/number(record.avg_cost, positive=True)-1 < float(getattr(config, "TIME_STOP_MIN_RETURN_PCT", .005))):
            return {"kind": "TIME_STOP", "label": "TIME-STOP", "reason": f"Time-Stop nach {hours:.0f}h, frischer Brokerkurs"}
    if record.management_mode == "PENDING_CONFIRMATION" and price >= take:
        return {"kind": "PROFIT", "label": "CLIENT-TAKE", "reason": "Vorhandenes Kursziel bei unbestaetigtem Broker-Schutz erreicht"}
    return None


def publish(runtime, status):
    previous = dict(runtime.data.get("etoro_exit_costs") or {})
    rows = [r for r in previous.get("positions", []) if r.get("position_id") != status["position_id"]]
    runtime.update(etoro_exit_costs={"positions": [*rows, status], "updated_at": status["assessed_at"]})
