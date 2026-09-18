"""Documented v2 orders:lookup states. Never apply to legacy v1 statusID.

https://api-portal.etoro.com/api-reference/trading--demo/submit-an-order-for-asynchronous-processing
https://api-portal.etoro.com/api-reference/trading--demo/request-cancellation-of-a-pending-order
"""
from __future__ import annotations

STATES = {
    1: ("Received", "Empfangen", False, "RECONCILING"),
    2: ("Placed", "Platziert", False, "RECONCILING"),
    3: ("Filled", "Ausgeführt", True, "FILLED"),
    4: ("Rejected", "Abgelehnt", True, "REJECTED"),
    5: ("PartiallyFilled", "Teilweise ausgeführt", False, "PARTIALLY_FILLED"),
    6: ("PendingCancel", "Stornierung wird bearbeitet", False, "RECONCILING"),
    7: ("Canceled", "Storniert", True, "CANCELLED"),
    9: ("CanceledPartiallyFilled", "Teilweise ausgeführt; Rest storniert", True, "CANCELED_PARTIALLY_FILLED"),
    10: ("RejectedPartiallyFilled", "Teilweise ausgeführt; Rest abgelehnt", True, "REJECTED_PARTIALLY_FILLED"),
    11: ("WaitingForMarket", "Wartet auf Marktöffnung", False, "RECONCILING"),
    12: ("PendingTriggeredRate", "Wartet auf Auslösekurs", False, "RECONCILING"),
}

def _token(value):
    return ''.join(c for c in str(value or '').upper() if c.isalnum()).replace('CANCELLED', 'CANCELED')

def lookup_status(status):
    """Unknown or contradictory evidence stays nonterminal, with a reason."""
    unknown = dict(code=None, name="Unknown", label="Auftragsstatus ungeklärt",
                   terminal=False, execution="RECONCILING", known=False)
    if not isinstance(status, dict):
        return unknown
    sid, name = status.get('id'), str(status.get('name') or '')
    if isinstance(sid, bool):
        return unknown
    if sid in (None, 0, ''):
        sid = next((k for k, v in STATES.items() if _token(name) == _token(v[0])), None)
    if not isinstance(sid, int) or sid not in STATES:
        return unknown
    canonical, label, terminal, execution = STATES[sid]
    if name and _token(name) != _token(canonical):
        return dict(unknown, label="Widerspruch zwischen Statuscode und Statusname")
    return dict(code=sid, name=canonical, label=label, terminal=terminal,
                execution=execution, known=True)
