"""Frozen PULSAR exit policy executed only by the existing position loop."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import math
import json

from . import control, research


def owned_proposal(record):
    if (not record or not record.ownership_chain_complete
            or record.user_observe_locked or str(record.source).upper() != "BOT"):
        return None
    candidates = [p for p in control.proposals(active_only=True)
        if p["decision_id"] == record.decision_id
        and p["account"] == record.broker_account_fingerprint
        and p["environment"] == record.broker_environment
        and p["symbol"] == record.symbol
        and p["status"] in {"FILLED", "PARTIALLY_FILLED"}
        and record.owned_position_id_set()
        <= {str(x) for x in p["execution"].get("position_ids", [])}]
    return candidates[0] if len(candidates) == 1 else None


def sessions_held(entry_time, now=None):
    from market_calendar import ist_handelstag
    now = now or datetime.now(timezone.utc)
    start = datetime.fromisoformat(str(entry_time).replace("Z", "+00:00"))
    if start.tzinfo is None:
        raise control.Blocked("PULSAR-Einstiegszeit ohne Zeitzone")
    day = start.astimezone(ZoneInfo("America/New_York")).date()
    end = now.astimezone(ZoneInfo("America/New_York")).date()
    count = 0
    while day < end:
        day += timedelta(days=1)
        count += int(ist_handelstag(day)[0])
        if count > 1000:
            break
    return count


def daily_atr(bars, now=None):
    day = (now or datetime.now(timezone.utc)).astimezone(ZoneInfo("America/New_York")).date().isoformat()
    completed = sorted([r for r in bars if str(r.get("date", ""))[:10] < day], key=lambda r: r["date"])[-15:]
    if len(completed) != 15:
        return None
    spans = []
    for previous, bar in zip(completed, completed[1:]):
        try:
            high, low, close = (float(v) for v in (bar["high"], bar["low"], previous["close"]))
            if not all(math.isfinite(v) and v > 0 for v in (high, low, close)) or high < low:
                return None
        except (KeyError, ValueError, TypeError):
            return None
        spans.append(max(high-low, abs(high-close), abs(low-close)))
    return sum(spans)/14


def decision(item, record, price, bars15=None, daily=None, *, now=None):
    """Persist only management state; all money/quantity receipts remain in the ledger."""
    now = now or datetime.now(timezone.utc)
    plan = item["plan"]
    if not math.isfinite(price) or price <= 0 or record.avg_cost <= 0:
        raise control.Blocked("PULSAR-Positionsbewertung fehlt")
    entry, R = float(plan["price"]), float(plan["R"])
    # A later explicit protection decision supersedes the original native
    # price plan. Original entry/R remain the frozen strategy reference.
    effective_stop = (float(record.planned_stop) if getattr(record, "protection_plan_history", [])
                      else float(plan["stop"]))
    held = sessions_held(record.entry_time, now)
    with control.transaction() as con:
        con.execute("INSERT OR IGNORE INTO pulsar_position_control(proposal_id,stop) VALUES(?,?)",
                    (item["id"], effective_stop))
        state = dict(con.execute("SELECT * FROM pulsar_position_control WHERE proposal_id=?", (item["id"],)).fetchone())
        stop = max(float(state["stop"]), effective_stop)
        highest = float(state["high15m"])
        if bars15 is not None and not bars15.empty:
            import pandas as pd
            stamps = pd.to_datetime(bars15.index, utc=True, errors="coerce")
            entry_at = pd.Timestamp(record.entry_time)
            completed = bars15[(stamps >= entry_at) & (stamps + pd.Timedelta(minutes=15) <= pd.Timestamp(now))]
            if len(completed):
                highs = [float(v) for v in completed["high"] if math.isfinite(float(v)) and float(v) > 0]
                highest = max([highest, *highs])
        if max(price, highest) >= entry+R:
            stop = max(stop, float(record.avg_cost))
        atr = daily_atr(daily or [], now)
        if highest >= entry+2*R and atr:
            stop = max(stop, highest-3*atr)
        # Never lower a prior stop, including after restart or missing bars.
        con.execute("UPDATE pulsar_position_control SET high15m=?,stop=? WHERE proposal_id=?",
                    (highest, stop, item["id"]))
        # 10.3.0: Haltedauer kommt aus dem eingefrorenen Plan (Hype-Spur: 10
        # Handelstage, Review nach 5); Altplaene ohne Feld behalten 20/10.
        limit = int(plan.get("max_hold_sessions") or 20)
        review_after = int(plan.get("review_sessions") or 10)
        if held >= review_after and not state["review_at"]:
            con.execute("UPDATE pulsar_position_control SET review_at=? WHERE proposal_id=?", (now.timestamp(), item["id"]))
            control._audit(con, item["id"], f"DAY_{review_after}_REVIEW", "Halteplan pruefen; kein automatischer KI-Aufruf", now.timestamp())
        if price <= stop:
            return {"action": "CLOSE", "quantity": record.quantity, "stop": stop, "reason": "PULSAR Client-Stop"}
        if held >= limit:
            return {"action": "CLOSE", "quantity": record.quantity, "stop": stop, "reason": f"PULSAR: {limit} Handelstage"}
        partial = math.floor(float(plan["quantity"])/3)
        if (plan.get("partial_at_2R") and price >= entry+2*R and partial >= 1
                and record.quantity > partial and not state["partial_state"]):
            return {"action": "PARTIAL", "quantity": partial, "stop": stop, "reason": "PULSAR: ein Drittel bei +2R"}
        return {"action": "HOLD", "quantity": 0, "stop": stop, "reason": "PULSAR-Halteplan"}


def claim_partial(pid, quantity):
    with control.transaction() as con:
        result = con.execute("UPDATE pulsar_position_control SET partial_state='SUBMITTING',partial_quantity=? "
                             "WHERE proposal_id=? AND partial_state=''", (quantity, pid))
        if result.rowcount != 1:
            raise control.Blocked("PULSAR-Teilausstieg wurde bereits versucht")
        control._audit(con, pid, "PARTIAL_SUBMIT", str(quantity))


def partial_result(pid, state, evidence):
    if state not in {"SUBMITTED", "UNKNOWN"}:
        raise ValueError("Teilorder erst durch native Ausfuehrungsbelege abschliessen")
    with control.transaction() as con:
        con.execute("UPDATE pulsar_position_control SET partial_state=?,partial_orders=? WHERE proposal_id=? AND partial_state='SUBMITTING'",
                    (state, control.encode(sorted(str(x) for x in evidence.get("order_ids", []))), pid))
        control._audit(con, pid, "PARTIAL_"+state, control.encode(evidence))


def reconcile_partial(item):
    """Only exact native exit intents can finish the one-shot partial request."""
    with control.transaction() as con:
        raw = con.execute("SELECT * FROM pulsar_position_control WHERE proposal_id=?", (item["id"],)).fetchone()
    if not raw or raw["partial_state"] not in {"SUBMITTING", "SUBMITTED", "UNKNOWN"}:
        return
    order_ids = json.loads(raw["partial_orders"])
    if not order_ids:
        return  # Never match an uncertain timeout by amount or symbol.
    import broker_exit_journal
    with broker_exit_journal._LOCK, broker_exit_journal._connect() as native:
        rows = [dict(r) for r in native.execute(
            "SELECT * FROM broker_exit_intents WHERE broker='etoro' AND account_fingerprint=? "
            "AND environment=? AND instrument_id=? AND broker_order_id IN ("+
            ",".join("?" for _ in order_ids)+")",
            (item["account"], item["environment"], item["plan"]["instrument_id"], *order_ids))]
    if (not rows or {r["broker_order_id"] for r in rows} != set(order_ids)
            or not {r["position_id"] for r in rows} <= set(item["execution"].get("position_ids", []))
            or abs(sum(r["requested_quantity"] for r in rows)-raw["partial_quantity"]) > 1e-8):
        return
    state = ""
    if all(r["status"] == "FILLED" and r["filled_quantity"] >= r["requested_quantity"] for r in rows):
        state = "FILLED"
    elif all(r["status"] in {"REJECTED", "CANCELLED", "CANCELED"} and r["filled_quantity"] == 0 for r in rows):
        state = "REJECTED"
    elif (all(r["status"] in {"FILLED", "REJECTED", "CANCELLED", "CANCELED"} for r in rows)
          and all(math.isfinite(float(r["filled_quantity"])) and 0 <= r["filled_quantity"] <= r["requested_quantity"]+1e-8 for r in rows)
          and 0 < sum(r["filled_quantity"] for r in rows) < raw["partial_quantity"]):
        state = "PARTIAL_TERMINAL"
    if state:
        with control.transaction() as con:
            con.execute("UPDATE pulsar_position_control SET partial_state=? WHERE proposal_id=? AND partial_state IN ('SUBMITTING','SUBMITTED','UNKNOWN')",
                        (state, item["id"]))
            control._audit(con, item["id"], "PARTIAL_"+state, "Exaktes natives Exitjournal")


def _sync_native_stop(broker, inst, record, item, stop):
    if stop <= float(record.planned_stop or 0):
        return
    effective_take = (float(record.planned_take) if getattr(record, "protection_plan_history", [])
                      else float(item["plan"]["take"]))
    # Native adapter reads back exact position IDs before confirming a PATCH.
    response = broker.reconcile_position_protection(inst, record.quantity,
        stop, effective_take,
        position_ids=sorted(record.owned_position_id_set()), update_requested=False,
        instrument_id=record.broker_instrument_id,
        strict_contract=bool(getattr(record, "protection_plan_history", [])))
    if not response.get("protection_confirmed"):
        response = broker.reconcile_position_protection(inst, record.quantity,
            stop, effective_take,
            position_ids=sorted(record.owned_position_id_set()), update_requested=True,
            instrument_id=record.broker_instrument_id,
            strict_contract=bool(getattr(record, "protection_plan_history", [])))
    if response.get("protection_confirmed"):
        record.planned_stop = stop


def manage(broker, inst, record, item, *, can_sell, close, register_exit,
           assess_exit=None, publish_exit=None):
    """The caller is live_trader's position loop; no separate execution worker."""
    reconcile_partial(item)
    from etoro_exit_costs import timestamp
    quote = broker.latest_bid_ask(inst) or {}
    try:
        age = (datetime.now(timezone.utc) - timestamp(quote.get("timestamp"))).total_seconds()
    except (ValueError, TypeError, OverflowError):
        age = None
    price = float(quote.get("bid") or 0)
    if age is None or not 0 <= age <= 180 or not math.isfinite(price) or price <= 0:
        raise control.Blocked("PULSAR-Ausgangskurs fehlt oder ist zu alt")
    # Fixed stop/time exits must not wait for optional historical data.
    action = decision(item, record, price)
    if action["action"] == "HOLD":
        bars = broker.historie(inst, "5 D", "15 mins", nur_handelszeiten=True)
        daily = research.cached("fmp:bars:"+inst.name, stale=True)
        daily_bars = daily["data"] if daily and now_age(daily["saved"]) <= 2*86400 else []
        action = decision(item, record, price, bars, daily_bars)
    if not can_sell:
        return action
    if action["action"] in {"CLOSE", "PARTIAL"}:
        partial = action["action"] == "PARTIAL"
        if partial:
            # The existing tightening policy does not wait for profit costs.
            _sync_native_stop(broker, inst, record, item, action["stop"])
        # Only the existing +2R partial is a targeted profit exit. Stop/time
        # exits keep their frozen policy and never wait for positive net profit.
        kind = "PROFIT" if partial else ("STOP_LOSS" if "Client-Stop" in action["reason"] else "TIME_STOP")
        if assess_exit is None:
            from etoro_exit_costs import assess
            assessment = assess(broker, inst, record, action["quantity"], exit_kind=kind)
        else:
            assessment = assess_exit(kind, action["quantity"])
        if publish_exit:
            publish_exit(assessment)
        if not assessment.get("allow_order"):
            return {**action, "action": "WAIT", "exit_assessment": assessment,
                    "reason": assessment.get("reason") or "Aktueller Ausstiegsbeleg fehlt"}
        price = assessment["quote_price"]
        if partial and price < float(item["plan"]["price"]) + 2 * float(item["plan"]["R"]):
            assessment.update(allow_order=False, decision="BLOCK_PROFIT",
                reason="Frischer Brokerkurs liegt wieder unter dem bestehenden +2R-Teilziel")
            if publish_exit:
                publish_exit(assessment)
            return {**action, "action": "WAIT", "exit_assessment": assessment,
                    "reason": assessment["reason"]}
        # Waiting for costs must not claim the irreversible one-shot partial.
        if partial:
            claim_partial(item["id"], action["quantity"])
        try:
            result = close(action["quantity"], price)
            register_exit(result, action["reason"])
            if partial:
                partial_result(item["id"], "SUBMITTED", {"order_ids": result.order_ids})
        except Exception as exc:
            if partial:
                partial_result(item["id"], "UNKNOWN", {"error": type(exc).__name__})
            raise
    else:
        _sync_native_stop(broker, inst, record, item, action["stop"])
    return action


def now_age(stamp):
    return datetime.now(timezone.utc).timestamp()-stamp
