"""Shared result-quality rules; unknown fees never become confirmed zero fees."""
from __future__ import annotations
import math
from typing import Any, Mapping

# CASH_DELTA_CONFIRMED (10.3.1): aus zwei authentifizierten eToro-Barbestands-
# belegen mit identischem uebrigem Positionsbestand automatisch abgeleitet.
CONFIRMED_FEES = frozenset({"CONFIRMED", "BROKER_CONFIRMED", "KNOWN", "USER_CONFIRMED",
                            "CASH_DELTA_CONFIRMED"})
# EXPECTED_UNVERIFIED (10.7.0, Freigabe Georg 18.09.2026): Abschlussgebuehr als
# Erwartungswert aus bestaetigten Abrechnungen desselben Kontos. Bewusst NICHT
# in CONFIRMED_FEES: Es ist kein Beleg. Aber das Ergebnis ist beziffert und
# gekennzeichnet -- es darf den Handel nicht mehr anhalten und wird vom
# naechsten Barbestandsbeleg ersetzt.
MODELLED_FEES = frozenset({"EXPECTED_UNVERIFIED"})

def finite_number(value: Any) -> float | None:
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None

def fees_confirmed(row: Mapping[str, Any]) -> bool:
    return str(row.get("fee_quality") or "UNKNOWN").upper() in CONFIRMED_FEES

def fees_modelled(row: Mapping[str, Any]) -> bool:
    return str(row.get("fee_quality") or "UNKNOWN").upper() in MODELLED_FEES

def confirmed_net(row: Mapping[str, Any]) -> bool:
    return fees_confirmed(row) and finite_number(row.get("netto_pnl")) is not None

def modelled_net(row: Mapping[str, Any]) -> bool:
    return fees_modelled(row) and finite_number(row.get("netto_pnl")) is not None

def usable_net(row: Mapping[str, Any]) -> bool:
    """Beziffert genug fuer Risikozustand und Freigabe: belegt ODER gekennzeichnet erwartet."""
    return confirmed_net(row) or modelled_net(row)

def result_status(row: Mapping[str, Any]) -> str:
    if confirmed_net(row):
        return "CONFIRMED"
    if modelled_net(row):
        return "EXPECTED"
    if finite_number(row.get("netto_pnl")) is not None:
        return "PROVISIONAL"
    if not fees_confirmed(row):
        return "FEES_UNKNOWN"
    return "RESULT_UNKNOWN"


def is_residual(row: Mapping[str, Any]) -> bool:
    """Inventory has no realized result. Proof validity is checked by the money gate."""
    return str(row.get("accounting_kind") or "TRADE").upper() == "RESIDUAL"


def entry_capital(row: Mapping[str, Any]) -> float | None:
    exact = finite_number(row.get("entry_cost_basis"))
    if exact is not None:
        return exact if exact > 0 else None
    price,qty,fee=(finite_number(row.get(k)) for k in ("einstieg_preis","menge","einstieg_gebuehr"))
    if price is None or qty is None or fee is None or min(price,qty)<=0:
        return None
    value=price*qty+fee
    return value if value>0 else None
