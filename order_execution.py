"""Transaktionaler eToro-Kaufpfad mit persistenter Zwischenphase."""
from __future__ import annotations
from dataclasses import dataclass
import logging

from broker.base import OrderStatusUnklar, VerbindungVerloren, OrderErgebnis
from decision_analytics import mark_execution, record_order_result, record_execution_event

logger = logging.getLogger(__name__)


@dataclass
class ExecutionResult:
    result: OrderErgebnis | None
    status: str
    uncertain: bool = False


def _persist_result(decision_id, status: str, result: OrderErgebnis) -> None:
    mark_execution(
        decision_id,
        status,
        getattr(result, "order_ids", []) or [],
        reference_id=getattr(result, "reference_id", "") or "",
        position_ids=getattr(result, "position_ids", []) or [],
        fill_price=getattr(result, "avg_fill_price", None),
        fill_qty=getattr(result, "filled_quantity", None),
        broker_paper=getattr(result, "paper", None),
    )


def submit_protected_buy(broker, instrument, qty, price, stop, take, *, decision_id=None) -> ExecutionResult:
    """Sendet einen Kauf exakt einmal.

    Der lokale Zustand wird vor dem Netzwerk-POST auf SUBMITTING gesetzt. Ist
    nach einem POST unklar, ob eToro die Order angenommen hat, wird
    UNKNOWN_AFTER_SUBMIT inklusive referenceId gespeichert und die Exception
    weitergereicht. Dieser Zustand darf nur durch Broker-Resync aufgeloest
    werden, niemals durch einen zweiten POST.
    """
    is_etoro = str(getattr(broker, "name", "") or "").lower() == "etoro"
    profile = str(getattr(__import__("config"), "ACTIVE_PROFILE", "") or "")
    context_set = False
    if is_etoro and decision_id:
        import etoro_reconciliation as reconciliation
        paper = bool(getattr(broker, "paper", getattr(__import__("config"), "PAPER_TRADING", True)))
        account_fingerprint = str(
            broker.account_fingerprint()
            if callable(getattr(broker, "account_fingerprint", None)) else "")
        reconciliation.reserve_and_start_intent(
            decision_id=int(decision_id), symbol=str(getattr(instrument, "name", "") or ""),
            paper=paper, profile=profile, quantity=qty, price=price, stop=stop,
            take_profit=take, account_fingerprint=account_fingerprint,
        )
        broker._nexus_submit_context = {
            "decision_id": int(decision_id), "profile": profile,
            "account_fingerprint": account_fingerprint}
        context_set = True
    else:
        mark_execution(decision_id, "SUBMITTING", [])
    try:
        result = broker.kaufe_mit_absicherung(instrument, qty, price, stop, take)
    except OrderStatusUnklar as exc:
        if is_etoro and decision_id:
            # ``mark_unknown`` ist ab Schema v3 ein monotoner Merge. Hat der
            # Adapter bereits Order, Fill oder positionId bewiesen (ADBE),
            # bleibt dieser staerkere Beleg erhalten und nur der noch offene
            # Positionsabgleich wird vermerkt.
            reconciliation.mark_unknown(
                int(decision_id), order_ids=getattr(exc, "order_ids", []) or [],
                reference_id=getattr(exc, "reference_id", "") or "", detail=str(exc))
        else:
            mark_execution(decision_id, "UNKNOWN_AFTER_SUBMIT",
                           getattr(exc, "order_ids", []) or [],
                           reference_id=getattr(exc, "reference_id", "") or "")
        raise
    except VerbindungVerloren as exc:
        if is_etoro and decision_id:
            current = reconciliation.record_for(int(decision_id))
            # Nach der persistenten POST-Grenze ist auch ein unerwarteter
            # VerbindungVerloren-Fehler ambig: niemals freigeben/erneut senden.
            if str(current.get("submit_state") or "").upper() in {
                    "POST_MAY_HAVE_BEEN_SENT", "ACCEPTED"}:
                reconciliation.mark_unknown(int(decision_id), detail=str(exc))
            else:
                reconciliation.mark_submit_failed(
                    int(decision_id), state="FAILED_BEFORE_SUBMIT", detail=str(exc))
        else:
            mark_execution(decision_id, "FAILED_BEFORE_SUBMIT", [])
        raise
    except Exception as exc:
        if is_etoro and decision_id:
            current = reconciliation.record_for(int(decision_id))
            # Ein HTTP-202 mit leerem/ungueltigem JSON oder ein Schemafehler
            # nach dem Sendebeginn ist kein beweisbarer Fehlschlag. Die
            # referenceId bleibt der Recovery-Anker und die Domaene gesperrt.
            if str(current.get("submit_state") or "").upper() in {
                    "POST_MAY_HAVE_BEEN_SENT", "ACCEPTED"}:
                reconciliation.mark_unknown(int(decision_id), detail=str(exc))
            else:
                reconciliation.mark_submit_failed(
                    int(decision_id), state="FAILED", detail=str(exc))
        else:
            mark_execution(decision_id, "FAILED", [])
        raise
    finally:
        if context_set:
            # Never let one order's decision metadata leak into a later order.
            try:
                delattr(broker, "_nexus_submit_context")
            except AttributeError:
                pass
    if is_etoro and decision_id:
        # Die Orchestrierung besitzt den Reconciliation-Abschluss selbst.
        # Tests und alternative Adapter duerfen nicht von einem versteckten
        # ``_nexus_submit_context``-Seiteneffekt abhaengen.
        reconciliation.apply_execution_result(int(decision_id), result)
    raw_status = str(getattr(result, "status", "") or "").upper().replace(" ", "_")
    filled = float(getattr(result, "filled_quantity", 0) or 0)
    remaining = float(getattr(result, "remaining_quantity", 0) or 0)
    if filled > 0 and (remaining > 1e-10 or "PARTIAL" in raw_status):
        status = "REJECTED_PARTIALLY_FILLED" if "REJECT" in raw_status else "PARTIALLY_FILLED"
    else:
        status = "FILLED" if filled > 0 else "SUBMITTED"
    _persist_result(decision_id, status, result)
    record_execution_event(decision_id, status, {
        "order_ids": list(getattr(result, "order_ids", []) or []),
        "reference_id": str(getattr(result, "reference_id", "") or ""),
        "position_ids": list(getattr(result, "position_ids", []) or []),
        "filled_quantity": filled, "remaining_quantity": remaining,
    }, event_id=f"execution:{decision_id}:{status}:{filled:g}")
    record_order_result(
        decision_id, result, broker=str(getattr(broker, "name", "") or "unknown"),
        symbol=str(getattr(instrument, "name", "") or ""), requested_qty=qty,
        requested_price=price, currency=str(getattr(instrument, "currency", "") or ""),
    )
    return ExecutionResult(result, status, False)
