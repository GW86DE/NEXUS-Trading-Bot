"""Exact own-stock client stop while broker protection is unconfirmed."""
import math
from datetime import datetime, timezone


def pending_exit_decision(record, broker, instrument, quantity):
    """Risk-reducing exits keep working while a position's PATCH is unresolved.

    No new PATCH or AUTO takeover is authorized. Manual/OBSERVE positions and
    incomplete ownership chains never enter this path. Broker native protection
    and the journal remain independent of this current-quote exit decision.
    """
    if (not record or record.management_mode != "PENDING_CONFIRMATION"
            or record.source != "BOT" or record.user_observe_locked
            or not record.ownership_chain_complete or not record.owned_position_id_set()
            or record.reconciliation_status != "CONFIRMED_OPEN"
            or record.broker_account_fingerprint != broker.account_fingerprint()
            or record.broker_environment != ("DEMO" if broker.paper else "LIVE")
            or not record.broker_instrument_id or not record.broker_snapshot_id):
        return None
    values = (record.planned_stop, record.planned_take, record.avg_cost, quantity, record.quantity)
    if any(isinstance(v, bool) or not math.isfinite(float(v)) or float(v) <= 0 for v in values):
        return None
    if (not record.planned_stop < record.planned_take
            or abs(quantity-record.quantity) > max(1e-8, record.quantity*1e-8)):
        return None
    quote = broker.latest_bid_ask(instrument) or {}
    age = broker._quote_age_seconds(quote.get("timestamp"))
    price = float(quote.get("bid") or 0)
    if (age is None or not 0 <= age <= 180 or not math.isfinite(price)
            or price <= 0):
        return None
    if price <= record.planned_stop:
        return {"price": price, "label": "CLIENT-STOP", "reason": "Client-Stop bei unbestaetigtem Broker-Schutz"}
    if price >= record.planned_take:
        return {"price": price, "label": "CLIENT-TAKE", "reason": "Gewinnziel bei unbestaetigtem Broker-Schutz erreicht"}
    import config
    if getattr(config, "TIME_STOP_ENABLED", True) and getattr(record, "entry_time", None):
        try:
            opened = datetime.fromisoformat(record.entry_time.replace("Z", "+00:00"))
            hours = (datetime.now(timezone.utc)-opened).total_seconds()/3600
            if (opened.tzinfo and hours >= float(getattr(config, "TIME_STOP_HOURS", 72))
                    and price/record.avg_cost-1 < float(getattr(config, "TIME_STOP_MIN_RETURN_PCT", .005))):
                return {"price": price, "label": "TIME-STOP", "reason": f"Time-Stop nach {hours:.0f}h bei offenem Schutzabgleich"}
        except (TypeError, ValueError):
            pass
    return None


def pending_stop_price(record, broker, instrument, quantity):
    """Compatibility API retains its stop-only meaning."""
    decision = pending_exit_decision(record, broker, instrument, quantity)
    return decision["price"] if decision and decision["label"] == "CLIENT-STOP" else None
