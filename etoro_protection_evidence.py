"""Protection comparison without guessing a tick size from displayed prices.

The public eToro v2 PATCH/eligibility specifications do not supply a rounding
contract. Thus unproven precision never turns a near match into confirmation.
An explicit verified rule can be supplied by a future documented adapter path;
there is deliberately no default two-decimal rule or user-facing bypass.
"""
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, ROUND_DOWN, ROUND_UP
import re


def number(value):
    if isinstance(value, bool) or value is None:
        raise ValueError("ETORO_PROTECTION_NUMBER_INVALID")
    try:
        v = Decimal(str(value))
        if not v.is_finite() or v <= 0:
            raise ValueError("ETORO_PROTECTION_NUMBER_INVALID")
        return v
    except InvalidOperation as exc:
        raise ValueError("ETORO_PROTECTION_NUMBER_INVALID") from exc


def normalize(stop, take, rule, *, instrument_id, account, environment):
    """Use only explicitly supplied instrument/account-bound price evidence.

    The caller owns verification of the source document represented by sha256.
    A display's decimal count, asset type or one observed rate is not a rule.
    """
    if (not isinstance(rule, dict) or rule.get("kind") != "VERIFIED_BROKER_PRICE_RULE"
            or rule.get("instrument_id") != str(instrument_id)
            or rule.get("account") != str(account) or not str(account)
            or rule.get("environment") != environment or environment not in {"DEMO", "LIVE"}
            or not re.fullmatch(r"[a-f0-9]{64}", str(rule.get("source_sha256") or ""))
            or not str(rule.get("source_reference") or "").strip()):
        raise ValueError("ETORO_PROTECTION_PRICE_RULE_UNPROVEN")
    modes = {"HALF_UP": ROUND_HALF_UP, "DOWN": ROUND_DOWN, "UP": ROUND_UP}
    stop_rounding = rule.get("stop_rounding", rule.get("rounding"))
    take_rounding = rule.get("take_rounding", rule.get("rounding"))
    if stop_rounding not in modes or take_rounding not in modes:
        raise ValueError("ETORO_PROTECTION_ROUNDING_UNPROVEN")
    tick = number(rule.get("tick_size"))
    values = [(number(v) / tick).to_integral_value(rounding=modes[mode]) * tick
              for v, mode in ((stop, stop_rounding), (take, take_rounding))]
    if min(values) <= 0 or values[0] >= values[1]:
        raise ValueError("ETORO_PROTECTION_NORMALIZATION_INVALID")
    # Long-stop normalization may tighten protection, never silently deepen it.
    if values[0] < number(stop):
        raise ValueError("ETORO_PROTECTION_NORMALIZATION_INCREASES_RISK")
    return {"stop": float(values[0]), "take_profit": float(values[1]),
            "tick_size": str(tick), "source_reference": rule["source_reference"],
            "source_sha256": rule["source_sha256"],
            "rounding": {"stop": stop_rounding, "take_profit": take_rounding}}


def assess(rows, expected, stop, take_profit, *, quantity=None, instrument_id=None,
           normalized=None, snapshot_id="", strict_contract=False):
    """Pure evidence projection used for both the decision and diagnostics."""
    result = {"schema_version": 1, "source": "ETORO_REST_POSITION_SNAPSHOT",
        "snapshot_id": str(snapshot_id), "requested": {"stop": stop, "take_profit": take_profit},
        "normalized": normalized, "sent": None, "observed": [],
        "precision": {"status": "PROVEN" if normalized else "UNPROVEN",
            "source": (normalized or {}).get("source_reference")},
        "confirmed": False, "reason_code": "ETORO_PROTECTION_UNKNOWN"}
    try:
        desired_stop, desired_take = number(stop), number(take_profit)
        if desired_stop >= desired_take:
            raise ValueError("ETORO_PROTECTION_RANGE_INVALID")
        if normalized:
            desired_stop, desired_take = number(normalized["stop"]), number(normalized["take_profit"])
        expected = {str(x) for x in expected}
        if (not expected or len(rows) != len(expected)
                or {str(r.get("positionId") or "") for r in rows} != expected):
            raise ValueError("ETORO_PROTECTION_POSITION_MISMATCH")
        if quantity is not None:
            observed_qty = sum((number(row.get("units")) for row in rows), Decimal(0))
            tolerance = max(Decimal("0.00000001"), number(quantity) * Decimal("0.000000001"))
            if abs(observed_qty - number(quantity)) > tolerance:
                raise ValueError("ETORO_PROTECTION_QUANTITY_MISMATCH")
        for row in rows:
            if strict_contract:
                if row.get("isBuy") is not True:
                    raise ValueError("ETORO_PROTECTION_DIRECTION_UNPROVEN")
                if row.get("stopLossType") != "fixed":
                    raise ValueError("ETORO_PROTECTION_STOP_TYPE_UNPROVEN")
            if instrument_id is not None and str(row.get("instrumentId")) != str(instrument_id):
                raise ValueError("ETORO_PROTECTION_INSTRUMENT_MISMATCH")
            actual_stop = number(row.get("stopLossRate", row.get("stopLoss")))
            actual_take = number(row.get("takeProfitRate", row.get("takeProfit")))
            flags = {k: row.get(k) for k in ("isNoStopLoss", "isNoTakeProfit")}
            result["observed"].append({"position_id": str(row.get("positionId")),
                "stop": float(actual_stop), "take_profit": float(actual_take), "flags": flags,
                "stop_type": row.get("stopLossType"), "is_buy": row.get("isBuy"),
                "stop_type_source": row.get("_stop_type_source")})
            # Optional flags may be absent only in the legacy exact-price
            # compatibility path. A normalized or strict contract requires
            # both explicit false values. Present values are never guessed.
            if any(v is not None and v is not False for v in flags.values()):
                raise ValueError("ETORO_PROTECTION_DISABLED_OR_INVALID_FLAG")
            # Never infer activation from a price in these stronger contracts.
            if (normalized or strict_contract) and any(v is not False for v in flags.values()):
                raise ValueError("ETORO_PROTECTION_FLAGS_UNPROVEN")
            # Numeric serialization tolerance only, no guessed broker tick.
            if (abs(actual_stop - desired_stop) > max(Decimal("1e-9"), desired_stop * Decimal("1e-12"))
                    or abs(actual_take - desired_take) > max(Decimal("1e-9"), desired_take * Decimal("1e-12"))):
                raise ValueError("ETORO_PROTECTION_PRICE_MISMATCH" if normalized else
                                 "ETORO_PROTECTION_PRICE_RULE_UNPROVEN")
        result.update(confirmed=True, reason_code="ETORO_PROTECTION_CONFIRMED")
    except (ValueError, TypeError, KeyError) as exc:
        result["reason_code"] = str(exc)
    return result
