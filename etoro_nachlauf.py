"""Nachlaufschritte des eToro-Abgleichs (10.8.0, Schritt 2).

Bis 10.7.1 rief ``etoro_reconciliation.background_tick`` am Ende selbst den
Stornoabgleich (``etoro_cancellations``) und den Gebuehrennachlauf
(``etoro_fee_recovery``) auf -- beide Module importieren aber den Abgleich:
Import-Zyklus ``etoro_reconciliation <-> etoro_cancellations/etoro_fee_recovery``.

Jetzt kennt der Abgleich seine Nachlaufschritte nicht mehr. Der Aufrufer des
Ticks (der Reconciliation-Worker im eToro-Adapter) uebergibt ``SCHRITTE``; der
Tick fuehrt sie in dieser Reihenfolge nach der Brokerwahrheit aus, mit
demselben Schnappschuss und denselben Fehlergrenzen wie bisher. Jeder Schritt
faengt seine Fehler selbst und protokolliert mit dem bisherigen Wortlaut.
"""
from __future__ import annotations

import logging

logger = logging.getLogger("etoro_reconciliation")


def storno(broker, *, paper, profile, position_snapshot):
    try:
        from etoro_cancellations import process_one
        process_one(broker)
    except Exception as exc:
        logger.warning("Stornoabgleich wartet weiter: %s", type(exc).__name__)
    return []


def gebuehren(broker, *, paper, profile, position_snapshot):
    try:
        from etoro_fee_recovery import recover_one
        recovered = recover_one(broker, paper=paper, profile=profile,
                                position_snapshot=position_snapshot)
        return [recovered] if recovered else []
    except Exception as exc:
        logger.warning("eToro-Gebuehrennachlauf wartet weiter: %s", type(exc).__name__)
        return []


SCHRITTE = (storno, gebuehren)
