"""Reine Regeln fuer den Schutz-Readback bei eToro (10.8.0, Schritt 2).

Bis 10.7.1 lagen ``fresh``, ``digest`` und ``merge_breakdown`` in
``etoro_protection_repair`` -- und der Broker-Adapter importierte sie von dort,
waehrend die Reparatur den Adapter importierte: ein Import-Zyklus
``broker.etoro <-> etoro_protection_repair``. Die drei Funktionen sind reine
Regeln ohne Broker- oder Zustandszugriff; sie gehoeren unter beide Module.
``etoro_protection_repair`` exportiert sie weiter, damit bestehende Aufrufer
und Tests unveraendert bleiben.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json

from etoro_protection_evidence import number


def digest(value):
    raw = value if isinstance(value, bytes) else json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(raw).hexdigest()


def fresh(timestamp, *, now=None, maximum_seconds=90):
    now = now or datetime.now(timezone.utc)
    try:
        at = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
        if not at.tzinfo or not -5 <= (now - at).total_seconds() <= maximum_seconds:
            raise ValueError
    except (ValueError, TypeError):
        raise ValueError("ETORO_REPAIR_SNAPSHOT_STALE_OR_UNTIMED") from None
    return at


def merge_breakdown(rows, breakdown, *, instrument_id, now=None):
    """Join stop type only after all overlapping position facts agree.

    Called by the CID-bound adapter. A display or a cached historical fixture
    can never supply missing broker facts. Missing flags remain missing.
    """
    if not isinstance(breakdown, dict) or breakdown.get("accountCurrency") != "USD":
        raise ValueError("ETORO_REPAIR_BREAKDOWN_CURRENCY_UNPROVEN")
    fresh(breakdown.get("timestamp"), now=now)
    groups = breakdown.get("instruments")
    if not isinstance(groups, list):
        raise ValueError("ETORO_REPAIR_BREAKDOWN_INCOMPLETE")
    groups = [g for g in groups if isinstance(g, dict)
              and str(g.get("instrumentId")) == str(instrument_id)]
    if len(groups) != 1 or not isinstance(groups[0].get("positions"), list):
        raise ValueError("ETORO_REPAIR_BREAKDOWN_INSTRUMENT_UNPROVEN")
    if not isinstance(groups[0].get("orders"), list):
        raise ValueError("ETORO_REPAIR_PENDING_ORDER_VIEW_UNPROVEN")
    output = []
    for row in rows:
        matches = [r for r in groups[0]["positions"] if isinstance(r, dict)
                   and str(r.get("positionId")) == str(row.get("positionId"))]
        if len(matches) != 1:
            raise ValueError("ETORO_REPAIR_BREAKDOWN_POSITION_UNPROVEN")
        other = matches[0]
        if (str(other.get("instrumentId")) != str(instrument_id)
                or row.get("isBuy") is not True or other.get("direction") != "long"
                or other.get("assetCurrency") != "USD"):
            raise ValueError("ETORO_REPAIR_BREAKDOWN_IDENTITY_MISMATCH")
        # Exact Decimal equality deliberately avoids guessing a tolerance or tick.
        for field in ("units", "stopLossRate", "takeProfitRate"):
            if number(row.get(field)) != number(other.get(field)):
                raise ValueError("ETORO_REPAIR_READBACK_CHANGED")
        if row.get("stopLossType") not in (None, other.get("stopLossType")):
            raise ValueError("ETORO_REPAIR_STOP_TYPE_CONFLICT")
        merged = dict(row)
        merged["stopLossType"] = other.get("stopLossType")
        merged["_stop_type_source"] = {
            "source": "ETORO_CID_BOUND_INSTRUMENT_BREAKDOWN",
            "timestamp": breakdown["timestamp"], "response_sha256": digest(breakdown)}
        output.append(merged)
    return output, deepcopy(groups[0]["orders"])
