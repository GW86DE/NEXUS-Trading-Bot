"""Read-only eToro overlays, bound to account, position and entry order."""
from datetime import datetime, timezone
import math
import time

from broker_display_context import match_position


def _number(value, *, positive=False):
    try:
        n = float(value)
        return n if not isinstance(value, bool) and math.isfinite(n) and (not positive or n > 0) else None
    except (ValueError, TypeError):
        return None


def enrich(row, snapshot, *, now=None):
    if row.get("broker") != "etoro" or row.get("ausgestiegen_am"):
        return
    positions = [dict(p, broker="etoro") for p in snapshot.get("positionen", [])]
    p = match_position(row, positions)
    if not p or str(p.get("waehrung") or "").upper() != str(row.get("waehrung") or "").upper():
        return
    now = time.time() if now is None else now
    try:
        stamp = datetime.fromisoformat(str(snapshot["updated_at"]).replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        snapshot_age = now - stamp.timestamp()
    except (ValueError, KeyError, TypeError):
        snapshot_age = None
    row.update(planned_stop=_number(p.get("planned_stop", p.get("stop")), positive=True),
        planned_take_profit=_number(p.get("planned_take_profit", p.get("ziel")), positive=True),
        stop_price=None, broker_take_profit=None, current_executable=False,
        market_quote_ccy=row.get("waehrung"), open_result_label="Offenes Bruttoergebnis · geschätzt")
    fresh_snapshot = snapshot_age is not None and -5 <= snapshot_age <= 180
    if fresh_snapshot:
        row["stop_price"] = _number(p.get("broker_stop"), positive=True)
        row["broker_take_profit"] = _number(p.get("broker_take_profit"), positive=True)
    row["protection_observed_at"] = snapshot.get("updated_at", "")
    row["protection_snapshot_fresh"] = fresh_snapshot
    price = _number(p.get("kurs"), positive=True)
    price_age = _number(p.get("kursalter"))
    if price is None:
        return
    row["reference_price"] = price
    row["reference_source"] = p.get("kursquelle") or "eToro-Positionssnapshot"
    if price_age is not None and snapshot_age is not None:
        row["reference_at"] = datetime.fromtimestamp(now-snapshot_age-price_age, timezone.utc).isoformat()
    if not fresh_snapshot or price_age is None or not -5 <= price_age + snapshot_age <= 180:
        return
    row.update(current_price=price, current_source=row["reference_source"] + " · Referenzkurs",
               current_at=row.get("reference_at", ""))
    entry, quantity = _number(row.get("einstieg_preis"), positive=True), _number(row.get("menge"), positive=True)
    if entry and quantity:
        row["open_display_pnl"] = (price-entry) * quantity
        row["open_display_pct"] = 100 * (price/entry-1)
    # Missing historic/current costs never become a confirmed zero or net PnL.
