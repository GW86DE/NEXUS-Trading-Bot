"""Bounded GET-only fee recovery for an exactly bound, already closed trade.

Legacy history ``fees`` and ``netProfit`` do not establish external closing
commission. Persist them as receipts, never silently promote them to total fees.
"""
from __future__ import annotations

import hashlib
import math
import time

import etoro_reconciliation as rec
import trade_ledger as ledger
from ledger_result import fees_confirmed

RECOVERY_SCHEMA = 3


def _same(a, b):
    try:
        x, y = float(a), float(b)
        return math.isfinite(x) and math.isfinite(y) and abs(x-y) <= max(1e-8, abs(y)*1e-7)
    except (ValueError, TypeError):
        return False


def entry_receipts(record, raw):
    """Validate a native v2 receipt, including its returned account identity."""
    if not isinstance(raw, dict) or "accountId" not in raw or "positionExecutions" not in raw:
        raise ValueError("ETORO_ENTRY_RECEIPT_SCHEMA_UNPROVEN: nativer v2-Kostenbeleg fehlt; kein Kontowiderspruch behauptet")
    account = str(record.get("account_fingerprint") or "")
    paper = record.get("paper")
    orders = record.get("order_ids") or []
    if not isinstance(paper, bool) or not account or len(orders) != 1:
        raise ValueError("Keine eindeutige Entry-Kontokette")
    cid = str(raw.get("accountId") or "")
    expected = hashlib.sha256(f"etoro|{'demo' if paper else 'live'}|cid:{cid}".encode()).hexdigest()[:24]
    asset = raw.get("asset") or {}
    if (not cid.isdigit() or expected != account
            or str(raw.get("orderId")) != str(orders[0])
            or raw.get("action") != "open" or raw.get("transaction") != "buy"
            or str(raw.get("orderCurrency") or "").upper() != "USD"
            or str(asset.get("currency") or "").upper() != "USD"
            or isinstance(asset.get("leverage"), bool)
            or asset.get("leverage") != 1 or asset.get("side") != "long"):
        raise ValueError("Orderbeleg widerspricht Konto/Umgebung/Entry/Waehrung")
    executions = raw.get("positionExecutions") or []
    positions = {str(x) for x in record.get("closed_position_ids") or []}
    if (not positions or positions != {str(e.get("positionId")) for e in executions}
            or len(executions) != len(positions)):
        raise ValueError("Positionen im Entrybeleg widersprechen der gespeicherten Kette")
    result = []
    for ex in executions:
        pid = str(ex["positionId"])
        op = ex.get("openingData") or {}
        if str(op.get("orderId")) != str(orders[0]) or ex.get("state") != "closed":
            raise ValueError("OpeningData/Abschlussstatus nicht exakt bestaetigt")
        previous = [f for f in record.get("fills", []) if str(f.get("position_id")) == pid]
        if (len(previous) != 1 or not _same(previous[0].get("quantity"), op.get("units"))
                or not _same(previous[0].get("price"), op.get("avgPrice"))
                or previous[0].get("execution_time") != op.get("executionTime")):
            raise ValueError("Entry-Ausfuehrung widerspricht dem gespeicherten Fill")
        fid = str(op.get("executionId") or ex.get("executionId") or
                  f"etoro-entry:{orders[0]}:{pid}:{op.get('executionTime')}")
        fee = rec._explicit_fee({**op, "fee_currency": "USD"})
        if fee is None:
            raise ValueError("Explizite Entrygebuehren/Steuern fehlen")
        result.append({"position_id": pid, "fill_id": fid, "ordId": str(orders[0]),
            "quantity": float(op["units"]), "price": float(op["avgPrice"]),
            "filled_at": op["executionTime"], "fee": fee, "fee_currency": "USD",
            "fee_source": "ETORO_V2_OPENING_DATA"})
    return result


def _native_order(broker, order_id):
    """Fees require v2; the generic order lookup may silently fall back to v1."""
    request = getattr(broker, "_request", None)
    if callable(request):
        path = ("/api/v2/trading/info/demo/orders:lookup" if broker.paper
                else "/api/v2/trading/info/orders:lookup")
        return request("GET", path, params={"orderId": str(order_id)})
    # Lightweight offline adapters remain supported. entry_receipts still
    # validates the complete returned v2 schema, never a v1-shaped substitute.
    return broker._lookup_order(order_id=order_id)


def _safe_receipt(value, broker):
    from provider_safety import redact
    secrets = [getattr(broker, key, "") for key in ("api_key", "user_key", "_api_key", "_user_key")]
    def clean(item):
        if isinstance(item, dict):
            return {str(key): ("[MASKIERT]" if any(word in str(key).lower()
                for word in ("token", "secret", "password", "authorization", "api_key", "user_key"))
                else clean(data)) for key, data in item.items()}
        if isinstance(item, list):
            return [clean(data) for data in item]
        if isinstance(item, str):
            return redact(item, secrets=secrets, max_chars=250000)
        return item
    return clean(value)


def close_cost_receipts(record, raw, events):
    """Bind documented v2 totalCosts to one actually filled close order.

    The order's position executions must agree with independently retained
    native close-fill identities. Entry orders and cancelled close requests
    can never establish exit costs. Multi-order/position allocation is left
    unresolved until a complete per-execution cost breakdown exists.
    """
    account = str(record.get("account_fingerprint") or "")
    paper = record.get("paper")
    cid = str(raw.get("accountId") or "")
    expected = hashlib.sha256(f"etoro|{'demo' if paper else 'live'}|cid:{cid}".encode()).hexdigest()[:24]
    oid = str(raw.get("orderId") or "")
    asset, status = raw.get("asset") or {}, raw.get("status") or {}
    positions = {str(x) for x in record.get("closed_position_ids") or []}
    total = raw.get("totalCosts")
    if (not isinstance(paper, bool) or not cid.isdigit() or expected != account
            or len(positions) != 1 or not oid or oid in {str(x) for x in record.get("order_ids") or []}
            or raw.get("action") != "close" or raw.get("transaction") != "sell"
            or str(status.get("name") or "").lower() != "filled"
            or status.get("errorCode") not in (None, 0)
            or str(raw.get("orderCurrency") or "").upper() != "USD"
            or str(asset.get("currency") or "").upper() != "USD"
            or asset.get("side") != "long" or isinstance(asset.get("leverage"), bool)
            or asset.get("leverage") != 1
            or str(asset.get("settlementType") or "").upper() != "REAL"
            or isinstance(total, bool) or not isinstance(total, (int, float))
            or not math.isfinite(total) or total < 0
            or {str(x) for x in raw.get("positionsToClose") or []} != positions):
        raise ValueError("ETORO_EXIT_COST_ORDER_UNPROVEN")
    executions = raw.get("positionExecutions") or []
    if (not executions or {str(x.get("positionId")) for x in executions} != positions
            or any(x.get("state") != "closed" for x in executions)):
        raise ValueError("ETORO_EXIT_COST_EXECUTIONS_UNPROVEN")
    receipts = []
    for event in events:
        if (str(event.get("position_id") or event.get("positionId") or "") not in positions
                or rec._explicit_close_order_id(event) != oid
                or not rec._explicit_close_fill_id(event)):
            raise ValueError("ETORO_EXIT_COST_FILL_IDENTITY_UNPROVEN")
        qty = event.get("closedUnits", event.get("quantity"))
        price = event.get("closeRate", event.get("closePrice"))
        if (isinstance(qty, bool) or isinstance(price, bool)
                or not _same(qty, qty) or not _same(price, price)
                or float(qty) <= 0 or float(price) <= 0):
            raise ValueError("ETORO_EXIT_COST_FILL_VALUES_UNPROVEN")
        receipts.append({"fill_id": rec._explicit_close_fill_id(event), "order_id": oid,
                         "quantity": float(qty), "price": float(price), "fee_currency": "USD"})
    quantity = sum(f["quantity"] for f in receipts)
    if (not receipts or len({f["fill_id"] for f in receipts}) != len(receipts)
            or not _same(quantity, record.get("filled_quantity", record.get("quantity")))
            or not _same(quantity, raw.get("requestedUnits"))):
        raise ValueError("ETORO_EXIT_COST_FULL_QUANTITY_UNPROVEN")
    # totalCosts belongs to this exact terminal order. Market spread is
    # already reflected by entry/exit prices and is not deducted separately.
    for fill in receipts:
        fill["fee"] = float(total) * fill["quantity"] / quantity
        fill["fee_source"] = "ETORO_V2_FILLED_CLOSE_ORDER_TOTAL_COSTS"
    return receipts


def _close_events(record):
    result = []
    for pid, value in (record.get("close_evidence_by_position_id") or {}).items():
        if isinstance(value, dict):
            result.append({**value, "position_id": str(pid)})
    return result


def _stream_close_hints(record, history=None):
    """Discover IDs, not executions, from the durable account-bound inbox."""
    from etoro_stream_inbox import closure_candidates, closure_order_hints
    pid = str(record["closed_position_ids"][0])
    result = {}
    bound = dict(account_fingerprint=record["account_fingerprint"], paper=record["paper"])
    messages = closure_candidates(**bound, position_id=pid, limit=50)
    history_matches = [r for r in (history or {}).get("rows", []) if str(r.get("positionId")) == pid]
    instrument = str(history_matches[0].get("instrumentId") or "") if len(history_matches) == 1 else ""
    if (instrument and history.get("account_fingerprint") == bound["account_fingerprint"]
            and history.get("environment") == ("DEMO" if bound["paper"] else "LIVE")):
        messages += closure_order_hints(**bound, instrument_id=instrument, limit=50)
    for message in sorted(messages, key=lambda m: str(m.get("received_at_utc") or ""), reverse=True):
        body = message.get("content") or {}
        if not isinstance(body, dict):
            continue
        oid = rec._explicit_close_order_id(body)
        kind = str(message.get("message_type") or "")
        if not oid and kind.startswith("Trading.OrderForClose"):
            oid = str(body.get("OrderID") or body.get("orderId") or "")
        message_pid = str(body.get("PositionID") or body.get("positionId") or "")
        instrument_hint = (not message_pid and instrument and kind.startswith("Trading.OrderForClose")
            and str(body.get("InstrumentID") or body.get("instrumentId") or "") == instrument)
        if (oid and oid not in {str(x) for x in record.get("order_ids", [])}
                and (message_pid == pid or instrument_hint)):
            result[oid] = message.get("event_key")
    return result


def _position_close_events(record, history, raw):
    """Bind native filled-close REST order to an existing position close fill.

    Used when the broker TP/SL order ID was discovered after the quantity was
    already booked. The old user close request is never reused. An inbox hint
    alone cannot reach the ledger; the exact REST account/order/position,
    executed status and full quantity are verified by close_cost_receipts.
    """
    pid = str(record["closed_position_ids"][0])
    if (history.get("complete") is not True or history.get("truncated") is True
            or history.get("account_fingerprint") != record["account_fingerprint"]
            or history.get("environment") != ("DEMO" if record["paper"] else "LIVE")):
        raise ValueError("ETORO_NATIVE_CLOSE_HISTORY_SCOPE_UNPROVEN")
    rows = ledger.entry_lineage("etoro", pid, record["order_ids"][0],
                                record["account_fingerprint"], paper=record["paper"])
    matches = [r for r in history.get("rows", []) if str(r.get("positionId")) == pid]
    if len(rows) != 1 or len(matches) != 1:
        raise ValueError("ETORO_NATIVE_CLOSE_FULL_POSITION_REQUIRED")
    row, item = rows[0], matches[0]
    from etoro_history_accounting import _stamp
    close_stamp = _stamp(item.get("closeTimestamp"))
    observed_stamp = _stamp(history.get("snapshot_at"))
    ledger_stamp = _stamp(row.get("ausgestiegen_am"))
    if (item.get("isBuy") is not True or isinstance(item.get("leverage"), bool)
            or item.get("leverage") != 1
            or close_stamp is None or observed_stamp is None or observed_stamp < close_stamp
            or ledger_stamp is not None and abs((ledger_stamp-close_stamp).total_seconds()) > 0.001):
        raise ValueError("ETORO_NATIVE_CLOSE_TIME_OR_DIRECTION_UNPROVEN")
    import json
    ids = json.loads(row.get("exit_fill_ids_json") or "[]")
    # Older replay consumers retained two aliases for the same exact history
    # execution. Choose its persisted canonical identity, never a second fill.
    detail = (record.get("close_evidence_by_position_id") or {}).get(pid) or {}
    canonical = rec._explicit_close_fill_id(detail)
    fid = canonical if canonical and canonical in ids else (ids[0] if len(ids) == 1 else "")
    if (not fid or not row.get("ausgestiegen_am")
            or not _same(row.get("menge"), item.get("units"))
            or not _same(row.get("ausstieg_preis"), item.get("closeRate"))
            or not _same(row.get("einstieg_preis"), item.get("openRate"))
            or str(item.get("orderId")) != str(record["order_ids"][0])):
        raise ValueError("ETORO_NATIVE_CLOSE_LEDGER_BINDING_UNPROVEN")
    oid = str(raw.get("orderId") or "")
    # A previously proven DIFFERENT close order cannot be overwritten from a
    # new hint. Empty IDs are normal for broker TP/SL history-only closures.
    existing = str(row.get("exit_order_id") or "")
    if existing and existing != oid:
        raise ValueError("ETORO_NATIVE_CLOSE_ORDER_CONFLICT")
    return [{"position_id": pid, "closeOrderId": oid, "fillId": fid,
             "closedUnits": float(item["units"]), "closeRate": float(item["closeRate"])}]


def recover_one(broker, *, paper, profile, position_snapshot=None, now=None):
    """One due closed order per tick; no retries in a loop and no order writes."""
    now = time.time() if now is None else float(now)
    account = str(broker.account_fingerprint() or "")
    if (not account or getattr(broker, "paper", None) is not paper
            or not rec._snapshot_complete_for_domain(position_snapshot,
                paper=paper, account_fingerprint=account)):
        return None
    data = rec._load()
    if data.get("storage_error"):
        return None
    candidates = []
    for record in (data.get("records") or {}).values():
        if (record.get("account_fingerprint") != account or record.get("paper") is not paper
                or record.get("position_state") != "CLOSED_CONFIRMED"
                or len(record.get("order_ids") or []) != 1
                or len(record.get("closed_position_ids") or []) != 1):
            continue
        previous = record.get("fee_recovery") or {}
        if previous.get("schema") == RECOVERY_SCHEMA and float(previous.get("next_at", 0)) > now:
            continue
        pid = record["closed_position_ids"][0]
        rows = ledger.entry_lineage("etoro", pid, record["order_ids"][0], account, paper=paper)
        if (not rows or any(not r["ausgestiegen_am"] for r in rows)
                or all(fees_confirmed(r) for r in rows)):
            continue
        candidates.append((float(previous.get("last_at", 0)), record))
    if not candidates:
        return None
    record = min(candidates, key=lambda x: (x[0], int(x[1]["decision_id"])))[1]
    did = record["decision_id"]
    report = {"last_at": now, "next_at": now+1800, "status": "PENDING", "schema": RECOVERY_SCHEMA}
    # Reserve before network access so a second process cannot duplicate work.
    claimed = []
    def claim(current):
        if (current.get("account_fingerprint") != account or current.get("paper") is not paper
                or current.get("position_state") != "CLOSED_CONFIRMED"
                or current.get("order_ids") != record.get("order_ids")
                or current.get("closed_position_ids") != record.get("closed_position_ids")):
            return False
        prior = current.get("fee_recovery") or {}
        if prior.get("schema") == RECOVERY_SCHEMA and float(prior.get("next_at", 0)) > now:
            return False
        current["fee_recovery"] = dict(report)
        claimed.append(True)
        return True
    rec._mutiere(did, claim)
    if not claimed:
        return None
    try:
        raw = _native_order(broker, record["order_ids"][0])
        # Retain the actual failed schema too. A generic validation string
        # without its sanitized body made the PEP cause impossible to inspect.
        report["order_receipt"] = _safe_receipt(raw, broker)
        report["receipt_source"] = "ETORO_V2_ORDERS_LOOKUP"
        receipts = entry_receipts(record, raw)
        history = broker.trade_history_snapshot(min(f["filled_at"] for f in receipts)[:10])
        matches = [r for r in history.get("rows", [])
                   if str(r.get("positionId")) == str(record["closed_position_ids"][0])]
        # History confirms execution, not the external fee completeness.
        if (not history.get("complete") or not matches
                or any(str(r.get("orderId")) != str(record["order_ids"][0]) for r in matches)
                or not _same(sum(float(r.get("units") or 0) for r in matches), sum(f["quantity"] for f in receipts))
                or any(not _same(r.get("openRate"), receipts[0]["price"]) for r in matches)):
            raise ValueError("Geschlossene Historie stimmt nicht vollstaendig mit Entrybeleg ueberein")
        if str(broker.account_fingerprint()) != account or broker.paper is not paper:
            raise ValueError("Brokerdomaene waehrend des Nachlaufs geaendert")
        current = (rec._load().get("records") or {}).get(str(did), {})
        if (current.get("account_fingerprint") != account or current.get("paper") is not paper
                or current.get("position_state") != "CLOSED_CONFIRMED"):
            raise ValueError("Gespeicherte Kontokette waehrend des Nachlaufs geaendert")
        entry_receipts(current, raw)
        rec.apply_broker_evidence(did, raw)
        projection = ledger.reconcile_entry_fees_exact(broker="etoro", account=account,
            paper=paper, position_id=str(record["closed_position_ids"][0]),
            entry_order_id=str(record["order_ids"][0]), entry_fills=receipts)
        report.update(status="ENTRY_CONFIRMED_EXIT_EXTERNAL_COSTS_PENDING", next_at=now+6*3600,
            detail="Einstiegskosten bestaetigt; externe Abschlusskosten und Nettoergebnis weiterhin offen",
            entry_projection=projection, order_receipt=_safe_receipt(raw, broker),
            history_receipts=_safe_receipt(matches, broker))
        # Preserve the broker's named netProfit next to computed P&L, including
        # the real PEP counterexample: 0 history fees versus 1 proven entry fee.
        # This is evidence processing, never permission to bypass the risk gate.
        try:
            from etoro_history_accounting import record_history
            report["history_result"] = record_history(account=account, paper=paper,
                position_id=str(record["closed_position_ids"][0]),
                entry_order_id=str(record["order_ids"][0]), snapshot=history)
            if report["history_result"]["quality"] == "BROKER_HISTORY_COST_SCOPE_CONFLICT":
                report["detail"] = report["history_result"]["reason"]
        except (ValueError, ledger.LedgerZuordnungUnklar) as exc:
            report["history_result"] = {"quality": "BROKER_HISTORY_RESULT_UNPROVEN", "reason": str(exc)}
        events = _close_events(current)
        close_orders = {rec._explicit_close_order_id(event) for event in events}
        close_raw = None
        if not events or "" in close_orders:
            try:
                hints = _stream_close_hints(current, history)
            except (ImportError, OSError, ValueError):
                hints = {}
            report["native_close_candidates"] = len(hints)
            # At most one new candidate lookup in this pass. Invalid/rejected
            # hints rotate on later scheduled passes; no POST or cancellation.
            attempted = set((record.get("fee_recovery") or {}).get("attempted_close_ids") or [])
            due = [oid for oid in hints if oid not in attempted]
            if not due and hints:
                attempted.clear()
                due = list(hints)
            if due:
                oid = due[0]
                attempted.add(oid)
                report["attempted_close_ids"] = sorted(attempted)[-50:]
                try:
                    candidate = _native_order(broker, oid)
                    report["close_order_receipt"] = _safe_receipt(candidate, broker)
                    if str(candidate.get("orderId") or "") != oid:
                        raise ValueError("ETORO_NATIVE_CLOSE_LOOKUP_ID_CONFLICT")
                    candidate_events = _position_close_events(current, history, candidate)
                    close_cost_receipts(current, candidate, candidate_events)
                    close_raw, events, close_orders = candidate, candidate_events, {oid}
                    report["native_close_source"] = "DURABLE_STREAM_HINT_VERIFIED_BY_REST"
                    report["native_close_event_key"] = hints[oid]
                except Exception as exc:
                    from provider_safety import redact
                    report["native_close_rejection"] = redact(str(exc))
        if events and len(close_orders) == 1 and "" not in close_orders:
            close_oid = next(iter(close_orders))
            if close_raw is None:
                close_raw = _native_order(broker, close_oid)
            report["close_order_receipt"] = _safe_receipt(close_raw, broker)
            exits = close_cost_receipts(current, close_raw, events)
            if str(broker.account_fingerprint()) != account or broker.paper is not paper:
                raise ValueError("Brokerdomaene waehrend des Kostennachlaufs geaendert")
            # Re-read exact lineage after the network call; another writer
            # may have reconciled a correction while the request was in flight.
            latest = (rec._load().get("records") or {}).get(str(did), {})
            if (latest.get("account_fingerprint") != account or latest.get("paper") is not paper
                    or _close_events(latest) != _close_events(current)
                    or latest.get("position_state") != "CLOSED_CONFIRMED"):
                raise ValueError("Abschlussbelege waehrend des Kostennachlaufs geaendert")
            completed = ledger.reconcile_fees_exact(broker="etoro", account=account,
                paper=paper, position_id=str(record["closed_position_ids"][0]),
                entry_order_id=str(record["order_ids"][0]), entry_fills=receipts, exit_fills=exits,
                cost_evidence={"source": "ETORO_V2_FILLED_CLOSE_ORDER_TOTAL_COSTS",
                    "order": _safe_receipt(close_raw, broker)})
            report.update(status="COSTS_CONFIRMED", exit_projection=completed,
                detail="Einstiegs- und Abschlusskosten anhand exakter Brokerbelege bestaetigt",
                next_at=now+24*3600)
            if report.get("native_close_event_key"):
                try:
                    from etoro_stream_inbox import mark_processed
                    mark_processed(report["native_close_event_key"], matched=[did])
                except (OSError, ValueError):
                    report["native_close_inbox_ack_pending"] = True
            try:
                report["history_result"] = record_history(account=account, paper=paper,
                    position_id=str(record["closed_position_ids"][0]),
                    entry_order_id=str(record["order_ids"][0]), snapshot=history)
            except (ValueError, ledger.LedgerZuordnungUnklar):
                # A history display projection cannot undo complete, stronger
                # native execution-cost receipts already atomically committed.
                pass
        else:
            report["missing_evidence"] = ["EXIT_COST_SCOPE_AND_EXTERNAL_COMMISSION"]
            report["lookup_route_missing"] = "ACTUAL_CLOSE_ORDER_ID"
            report["next_action"] = ("Broker-Abrechnung fuer diese Positions-ID pruefen: tatsaechliche "
                "Schliessorder und berechnete Verkaufsgebuehren/Steuern in USD benoetigt; "
                "der fruehere stornierte Auftrag ist kein Kostenbeleg. Vorliegende "
                "Historienwerte bleiben sichtbar, ihr Kostenumfang ist nicht bewiesen.")
    except Exception as exc:
        from provider_safety import redact
        report["detail"] = redact(str(exc))
        report["next_at"] = now+1800
    def remember(current):
        if (current.get("account_fingerprint") != account or current.get("paper") is not paper
                or (current.get("fee_recovery") or {}).get("last_at") != now):
            return False
        current["fee_recovery"] = report
        return True
    return rec._mutiere(did, remember)
