"""Bridge into the existing eToro core: research -> intent -> normal gates.

No endpoint or callback calls this module's execution path. Broker submission
continues exclusively through live_trader / order_execution.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import math
import time

from . import control, research


def entry_window(now=None):
    from market_calendar import sitzungsstatus
    state = sitzungsstatus(now)
    at = state["jetzt_ny"]
    return bool(state["offen"] and at >= state["oeffnung"] + timedelta(minutes=30)
                and at <= state["schluss"] - timedelta(minutes=60))


def candidate_instruments(broker, known):
    if control.settings()["mode"] != "FREIGABE" or not entry_window():
        return []
    from contracts import build_universe
    selected = []
    for card in research.latest_cards():
        if not card.get("eligible") or not research.stable_candidate(card["symbol"]):
            continue
        from instrument_identity import canonical_key
        existing = known.get(canonical_key(card["symbol"], "stock"))
        if existing:
            selected.append(existing)
            continue
        # Identity is still resolved by the existing broker, never by the feed.
        instruments = build_universe([{"symbol": card["symbol"], "currency": "USD",
            "sector": card.get("sector", ""), "broad": True}], [], [])
        qualified, _ = broker.qualifiziere(instruments)
        for inst in qualified:
            meta = broker.instrument_metadata(inst)
            if meta.get("asset_type") == "stock":
                selected.append(inst)
    return selected


def context(broker, instrument):
    account = broker.account_fingerprint()
    environment = "DEMO" if broker.paper else "LIVE"
    owned = control.for_symbol(instrument.name)
    if owned and (owned["account"] != account or owned["environment"] != environment
                  or owned["status"] in control.EXPOSURE):
        raise control.Blocked("Instrument bereits durch einen PULSAR-Trade/Intent gebunden")
    if control.settings()["mode"] != "FREIGABE":
        return None
    card = research.stable_candidate(instrument.name)
    if owned and not card:
        raise control.Blocked("PULSAR-Freigabe wartet auf frische vollstaendige Quellenpruefung")
    if not card:
        return None
    if not entry_window():
        raise control.Blocked("Ausserhalb des PULSAR-Einstiegsfensters")
    # 10.3.0: Die Hype-Spur ersetzt Score-80 und Terra-Doppelpruefung.
    # stable_candidate liefert nur eligible Hype-Karten (Social-Spike +
    # Kursbestaetigung + Identitaet + Luna-Warnfilter, zweifach gemessen).
    if not card.get("eligible") or card.get("state") != "HYPE_KANDIDAT":
        raise control.Blocked("PULSAR-Hype-Kriterien nicht erfuellt")
    return {"card": card, "proposal": owned, "account": account, "environment": environment}


def reserved_cash(account, environment, exclude=""):
    # The broker/order registry handles already transmitted orders. Here only
    # reserve unsubmitted human approvals so other strategies cannot spend them.
    return sum(float(r["plan"].get("capital_reserved") or r["plan"]["quantity"]*r["plan"]["price"])
               for r in control.proposals(active_only=True)
               if r["account"] == account and r["environment"] == environment
               and r["id"] != exclude and r["status"] in control.PRE_SUBMIT)


def stops(ctx, price):
    if ctx["proposal"]:
        plan = ctx["proposal"]["plan"]
        return float(plan["stop"]), float(plan["take"])
    bars = ctx["card"].get("bars") or []
    bars = sorted(bars, key=lambda r: r["date"])
    ranges = []
    for before, row in zip(bars[-15:-1], bars[-14:]):
        high, low, close = float(row["high"]), float(row["low"]), float(before["close"])
        if not all(math.isfinite(v) and v > 0 for v in (high, low, close)) or high < low:
            raise control.Blocked("PULSAR-Tageskerzen ungueltig")
        ranges.append(max(high-low, abs(high-close), abs(low-close)))
    if len(ranges) < 14:
        raise control.Blocked("PULSAR braucht 14 abgeschlossene Tages-ATR-Belege")
    atr = sum(ranges)/14
    distance = max(.08*price, 2*atr)
    if distance > .15*price:
        raise control.Blocked("Struktureller PULSAR-Stop weiter als 15 Prozent")
    stop = price-distance
    return stop, price+6*distance


def size(ctx, normal_quantity, price, stop, equity):
    maximum = math.floor(min(float(normal_quantity), equity*.03/price,
                             equity*.0025/(price-stop)))
    if ctx["proposal"]:
        requested = ctx["proposal"]["plan"]["quantity"]
        if maximum < requested:
            raise control.Blocked("Aktuelles Risiko traegt die persoenlich bestaetigte Menge nicht")
        return requested
    if maximum < 1:
        raise control.Blocked("PULSAR-Risikobudget reicht nicht fuer ein Stueck")
    return maximum


def loss_gate(account, environment, equity):
    from .presentation import snapshot
    closed = [r for r in snapshot()["archive"] if r["broker_account_fingerprint"] == account
              and ("DEMO" if r["paper"] else "LIVE") == environment]
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    pnl = 0.
    for row in closed:
        try:
            stamp = datetime.fromisoformat(str(row["ausgestiegen_am"]).replace("Z", "+00:00"))
            stamp = stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            raise control.Blocked("PULSAR-Abschlusszeit unklar")
        if stamp < cutoff:
            continue
        if row.get("fee_quality") != "CONFIRMED" or row.get("netto_pnl") is None or row.get("waehrung") != "USD":
            raise control.Blocked("PULSAR-Ergebnisabgleich offen")
        pnl += float(row["netto_pnl"])
    if pnl <= -float(equity)*control.RULES["loss_30d_pct"]:
        raise control.Blocked("PULSAR-Verlustgrenze ueber 30 Tage erreicht")


def before_submit(ctx, broker, instrument, *, price, quantity, stop, take, equity, cash,
                  decision_id, estimated_fees, earnings_days):
    """False means nominated/waiting; True means atomically claimed for core."""
    if not entry_window():
        raise control.Blocked("PULSAR-Einstiegsfenster ist inzwischen geschlossen")
    from .requirements import earnings_clear
    if not earnings_clear(ctx["card"], earnings_days):
        raise control.Blocked("Earnings-Termin fehlt oder liegt zu nahe am Einstieg")
    loss_gate(ctx["account"], ctx["environment"], equity)
    fresh_card = research.stable_candidate(instrument.name)
    if not fresh_card or fresh_card["evidence_hash"] != ctx["card"]["evidence_hash"]:
        raise control.Blocked("PULSAR-Quellenpaket wurde seit der Pruefung veraendert")
    proposal = ctx["proposal"]
    if proposal:
        if proposal["status"] != "APPROVED":
            return False
        control.claim(proposal["id"], decision_id=decision_id, account=ctx["account"],
            environment=ctx["environment"], price=price, quantity=quantity, stop=stop, take=take,
            equity=equity, cash=cash, evidence_hash=fresh_card["evidence_hash"],
            estimated_fees=estimated_fees)
        ctx["claimed_id"] = proposal["id"]
        return True
    meta = broker.instrument_metadata(instrument)
    allowed, reason = broker.instrument_handelbar(instrument)
    if not allowed or meta.get("asset_type") != "stock":
        raise control.Blocked(reason or "PULSAR verlangt eine eindeutig qualifizierte Aktie")
    # The existing adapter's settlement validation has already established REAL.
    if str(broker._settlement(instrument)).lower() not in {"real", "underlying", "asset"}:
        raise control.Blocked("PULSAR darf kein CFD-Settlement nutzen")
    if not math.isfinite(float(estimated_fees)) or estimated_fees < 0:
        raise control.Blocked("PULSAR-Kostenschaetzung ungueltig")
    cost_budget = float(estimated_fees)*1.05
    max_entry_price = price*(1+control.RULES["entry_band_pct"])
    control.check_cost_budget(quantity, max_entry_price, stop, equity, cost_budget, cash)
    plan = {"symbol": instrument.name, "broker": "etoro", "instrument_id": str(meta["instrument_id"]),
            "settlement": "REAL", "leverage": 1, "currency": instrument.currency,
            "account": ctx["account"], "environment": ctx["environment"], "rules": dict(control.RULES),
            "evidence_hash": fresh_card["evidence_hash"], "assessment_id": fresh_card["id"],
            "quantity": float(quantity), "price": float(price), "stop": float(stop), "take": float(take),
            "equity": float(equity), "R": float(price-stop),
            "max_entry_price": max_entry_price, "cost_budget": cost_budget,
            "estimated_costs": float(estimated_fees),
            "capital_reserved": quantity*max_entry_price+cost_budget,
            "partial_at_2R": quantity >= 3 and quantity*(price-stop)*2 > 2*max(0., estimated_fees),
            # 10.3.0: Squeeze-Zeitskala -- ein Hype lebt Tage, nicht Monate.
            "max_hold_sessions": int(control.RULES["max_hold_sessions"]), "review_sessions": 5}
    from live_settings import telegram_runtime
    runtime = telegram_runtime()
    item = control.nominate(plan, chat=runtime.get("chat_id", ""), user=runtime.get("user_id", ""))
    from .telegram import notify_nomination
    notify_nomination(item)
    ctx["proposal"] = {**item, "status": "WAITING_APPROVAL"}
    return False


def result(ctx, result):
    if not ctx or not ctx.get("claimed_id"):
        return
    qty = float(getattr(result, "filled_quantity", 0) or 0)
    order_ids = [str(x) for x in getattr(result, "order_ids", [])]
    position_ids = [str(x) for x in getattr(result, "position_ids", [])]
    evidence = {"order_ids": order_ids, "position_ids": position_ids,
                "filled_quantity": qty, "reference_id": str(getattr(result, "reference_id", "") or "")}
    planned = (ctx.get("proposal") or {}).get("plan", {}).get("quantity", qty)
    status = ("PARTIALLY_FILLED" if qty < planned else "FILLED") if qty > 0 and order_ids and position_ids else "SUBMITTED"
    control.record_execution(ctx["claimed_id"], status, evidence)


def failed(ctx, error):
    if ctx and ctx.get("claimed_id"):
        control.record_execution(ctx["claimed_id"], "UNKNOWN", {"error": type(error).__name__})


def reconcile():
    """Use the existing journal and ledger, never infer closure from absence."""
    import etoro_reconciliation
    records = (etoro_reconciliation._load().get("records") or {}).values()
    from .presentation import snapshot
    from .positions import reconcile_partial
    for item in control.proposals():
        reconcile_partial(item)
    ledger = snapshot()
    trades = ledger["open_trades"] + ledger["archive"]
    for item in control.proposals(active_only=True):
        if item["status"] not in control.EXPOSURE or not item["decision_id"]:
            continue
        rows = [r for r in records if int(r.get("decision_id") or 0) == item["decision_id"]
                and r.get("account_fingerprint") == item["account"]
                and ("DEMO" if r.get("paper") else "LIVE") == item["environment"]]
        if len(rows) != 1:
            continue
        row = rows[0]
        orders = list(row.get("order_ids") or [])
        pids = list(row.get("verified_position_ids") or [])
        fills = [r for r in row.get("fills", []) if float(r.get("quantity") or 0) > 0]
        evidence = {"order_ids": orders, "position_ids": pids, "reference_id": row.get("reference_id", "")}
        if fills and orders and pids:
            stamps = []
            for f in fills:
                try:
                    stamp = datetime.fromisoformat(str(f["execution_time"]).replace("Z", "+00:00"))
                    if stamp.tzinfo is not None:
                        stamps.append(stamp.timestamp())
                except (ValueError, TypeError, KeyError):
                    pass
            if stamps:
                evidence["filled_at"] = min(stamps)
            local = [r for r in trades if r["decision_id"] == item["decision_id"]]
            closed = bool(local) and all(r.get("ausgestiegen_am") for r in local)
            state = "CLOSED" if closed and row.get("position_state") == "CLOSED_CONFIRMED" else "FILLED"
            if item["status"] != state or item["execution"] != evidence:
                control.record_execution(item["id"], state, evidence)
        elif str(row.get("execution_state") or "").upper() in {"REJECTED", "CANCELED", "CANCELLED", "EXPIRED"} and not fills:
            control.record_execution(item["id"], "REJECTED", evidence)
