"""Root-cause regressions for the eToro reconciliation repair in NEXUS 9.5.1.

These tests deliberately exercise public/runtime behaviour.  They do not
search source text: every assertion follows the same persisted ID chain used
by the production broker (decision -> reference -> order -> position).
"""
from __future__ import annotations

from copy import deepcopy
import threading
import time
from types import SimpleNamespace

import pytest


ACCOUNT = "ba32f97fe3482fcbc326e51a"
DECISION = 4153277691980212224
ORDER = "378375675"
REFERENCE = "ac14a874-980a-468d-982f-57df9c4d56b9"
POSITION = "3592625451"

ADBE_FILLED_ORDER = {
    "accountId": 21577959,
    "gcid": 20343408,
    "portfolioId": 0,
    "orderId": 378375675,
    "referenceId": REFERENCE,
    "action": "open",
    "transaction": "buy",
    "type": "mkt",
    "status": {"id": 3, "name": "Filled", "errorCode": 0},
    "asset": {
        "symbol": "ADBE", "instrumentId": 1126, "currency": "USD",
        "settlementType": "REAL", "leverage": 1, "side": "long",
    },
    "requestedAmount": 14991.45,
    "requestedUnits": 51.0,
    "positionExecutions": [{
        "positionId": 3592625451,
        "state": "open",
        "remainingUnits": 51.0,
        "stopLossRate": 287.17,
        "takeProfitRate": 295.87,
        "openingData": {
            "openTime": "2026-09-01T14:38:32.127Z",
            "orderId": 378375675,
            "executionTime": "2026-09-01T14:38:32.343Z",
            "units": 51.0,
            "avgPrice": 293.92,
            "fees": 1.0,
            "taxes": 0.0,
        },
    }],
}


def _eventually(predicate, timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return bool(predicate())


def _worker_running(status: dict) -> bool:
    """Keep the test independent of a cosmetic status-field spelling."""
    return bool(status.get("running", status.get("alive", status.get("thread_alive", False))))


def _snapshot(*rows: dict, generation: str = "etoro:demo:test",
              complete: bool = True) -> dict:
    copied = [deepcopy(row) for row in rows]
    by_id = {
        str(row.get("positionId") or row.get("positionID")): row
        for row in copied
        if row.get("positionId") not in (None, "")
        or row.get("positionID") not in (None, "")
    }
    return {
        "open_ids": set(by_id),
        "rows": copied,
        "rows_by_position_id": by_id,
        "snapshot_id": generation,
        "complete": bool(complete),
        "account_fingerprint": ACCOUNT,
        "environment": "DEMO",
    }


def _history(*rows: dict, complete: bool = True) -> dict:
    return {"rows": [deepcopy(row) for row in rows], "complete": bool(complete)}


def _adbe_open_row(*, position_id: str = POSITION, order_id: str = ORDER,
                   units: float = 51.0) -> dict:
    return {
        "positionId": str(position_id),
        "orderId": str(order_id),
        "instrumentId": 1126,
        "CID": 21577959,
        "isBuy": True,
        "units": float(units),
        "openRate": 293.92,
        "stopLossRate": 287.17,
        "takeProfitRate": 295.87,
    }


def _adbe_closed_row(*, position_id: str = POSITION,
                     order_id: str = ORDER, units: float = 51.0) -> dict:
    return {
        "positionId": str(position_id),
        "orderId": str(order_id),
        "instrumentId": 1126,
        "isBuy": True,
        "units": float(units),
        "openRate": 293.92,
        "closeRate": 287.16,
        "openTimestamp": "2026-09-01T14:38:32.343Z",
        "closeTimestamp": "2026-09-01T15:18:34.000Z",
    }


class _TruthBroker:
    def __init__(self, position_snapshot: dict, history_snapshot: dict | None = None):
        self._position_snapshot = position_snapshot
        self._history_snapshot = history_snapshot or _history()
        self.position_calls = 0
        self.history_calls = 0

    def account_fingerprint(self):
        return ACCOUNT

    def position_snapshot(self, *, force=True):
        self.position_calls += 1
        return deepcopy(self._position_snapshot)

    def trade_history_snapshot(self, _min_date, *, max_pages=12, force=False):
        self.history_calls += 1
        return deepcopy(self._history_snapshot)

    # Compatibility methods are intentionally present so that a regression
    # cannot be hidden by an AttributeError before the new snapshot path runs.
    def current_position_ids(self, *, force=True):
        return set(self._position_snapshot.get("open_ids") or set())

    def trade_history(self, _min_date, **_kwargs):
        return list(self._history_snapshot.get("rows") or [])


@pytest.fixture
def rec_env(monkeypatch, tmp_path):
    monkeypatch.setenv("TRADINGBOT_TEST_STATE_DIR", str(tmp_path))
    import decision_analytics as analytics
    import etoro_reconciliation as rec

    monkeypatch.setattr(analytics, "DB_PATH", tmp_path / "decisions.sqlite")
    monkeypatch.setattr(rec, "_notify", lambda *_a, **_k: None)
    return rec, analytics


def _start_adbe(rec, analytics, *, decision_id: int = DECISION,
                quantity: float = 51.0, order_id: str = ORDER,
                reference_id: str = REFERENCE):
    analytics.record({
        "decision_id": decision_id,
        "symbol": "ADBE",
        "asset_type": "stock",
        "broker": "etoro",
        "paper": True,
        "status": "APPROVED",
        "profile": "offensiv",
        "price": 290.07,
        "qty": quantity,
        "stop": 287.1696,
        "take": 295.8707,
    })
    rec.start_intent(
        decision_id=decision_id,
        symbol="ADBE",
        paper=True,
        profile="offensiv",
        quantity=quantity,
        price=290.07,
        stop=287.1696,
        take_profit=295.8707,
        account_fingerprint=ACCOUNT,
    )
    rec.accepted(decision_id, order_id=order_id, reference_id=reference_id)
    return rec.domain_key(
        paper=True, profile="offensiv", account_fingerprint=ACCOUNT)


def _verify(rec, broker: _TruthBroker, snapshot: dict):
    return rec.verify_broker_truth(
        broker,
        paper=True,
        profile="offensiv",
        account_fingerprint=ACCOUNT,
        position_snapshot=snapshot,
    )


def test_adbe_fill_empty_snapshot_then_later_exact_position_is_confirmed(rec_env):
    rec, analytics = rec_env
    domain = _start_adbe(rec, analytics)
    rec.apply_broker_evidence(DECISION, deepcopy(ADBE_FILLED_ORDER))

    empty = _snapshot(generation="etoro:demo:378")
    broker = _TruthBroker(empty, _history())
    _verify(rec, broker, empty)
    pending = rec.record_for(DECISION)
    assert pending["state"] == "AWAITING_POSITION_CONFIRMATION"
    assert pending["position_ids"] == [POSITION]
    assert pending["fills"][0]["price"] == pytest.approx(293.92)
    assert rec.active_for_domain(domain)

    opened = _snapshot(_adbe_open_row(), generation="etoro:demo:379")
    broker._position_snapshot = opened
    changed = _verify(rec, broker, opened)
    confirmed = rec.record_for(DECISION)
    assert changed
    assert confirmed["state"] == "FILLED"
    assert confirmed["broker_position_status"] == "OPEN_CONFIRMED"
    assert confirmed["position_verified"] is True
    assert confirmed["verified_position_ids"] == [POSITION]
    assert rec.active_for_domain(domain) == []


def test_mark_unknown_is_monotonic_after_fill_and_after_open_confirmation(rec_env):
    rec, analytics = rec_env
    _start_adbe(rec, analytics)
    rec.apply_broker_evidence(DECISION, deepcopy(ADBE_FILLED_ORDER))

    rec.mark_unknown(
        DECISION, order_ids=[ORDER], reference_id=REFERENCE,
        detail="late timeout after exact fill")
    after_fill = rec.record_for(DECISION)
    assert after_fill["state"] == "AWAITING_POSITION_CONFIRMATION"
    assert after_fill["position_ids"] == [POSITION]
    assert after_fill["fills"]

    opened = _snapshot(_adbe_open_row())
    _verify(rec, _TruthBroker(opened), opened)
    rec.mark_unknown(DECISION, detail="even later submit exception")
    terminal = rec.record_for(DECISION)
    assert terminal["state"] == "FILLED"
    assert terminal["broker_position_status"] == "OPEN_CONFIRMED"
    assert terminal["verified_position_ids"] == [POSITION]


def test_late_order_evidence_cannot_reopen_a_confirmed_closed_position(rec_env):
    rec, analytics = rec_env
    domain = _start_adbe(rec, analytics)
    rec.apply_broker_evidence(DECISION, deepcopy(ADBE_FILLED_ORDER))
    closed = rec.confirm_position_closed(
        paper=True,
        account_fingerprint=ACCOUNT,
        position_id=POSITION,
        close_order_id="close-3592625451",
        fill_id="close-fill-3592625451",
        close_detail=_adbe_closed_row(),
    )
    assert closed

    rec.apply_broker_evidence(DECISION, deepcopy(ADBE_FILLED_ORDER))
    record = rec.record_for(DECISION)
    assert record["broker_position_status"] == "CLOSED_CONFIRMED"
    assert record["closed_position_ids"] == [POSITION]
    assert rec.active_for_domain(domain) == []


def test_close_arriving_before_open_confirmation_is_terminal_against_stale_pnl(rec_env):
    rec, analytics = rec_env
    domain = _start_adbe(rec, analytics)
    rec.apply_broker_evidence(DECISION, deepcopy(ADBE_FILLED_ORDER))
    rec.confirm_position_closed(
        paper=True,
        account_fingerprint=ACCOUNT,
        position_id=POSITION,
        close_order_id="close-1",
        fill_id="close-fill-1",
        close_detail=_adbe_closed_row(),
    )

    # A delayed PnL cache may still contain the position.  It must never
    # resurrect a close which was proven by an exact positionId close fill.
    stale = _snapshot(_adbe_open_row(), generation="etoro:demo:stale")
    _verify(rec, _TruthBroker(stale), stale)
    record = rec.record_for(DECISION)
    assert record["broker_position_status"] == "CLOSED_CONFIRMED"
    assert record["closed_position_ids"] == [POSITION]
    assert rec.active_for_domain(domain) == []


def test_every_execution_position_id_must_be_resolved_before_unlock(rec_env):
    rec, analytics = rec_env
    second = "3592625452"
    domain = _start_adbe(rec, analytics, quantity=71.0)
    evidence = deepcopy(ADBE_FILLED_ORDER)
    evidence["requestedUnits"] = 71.0
    evidence["positionExecutions"].append({
        "positionId": second,
        "openingData": {
            "units": 20.0,
            "avgPrice": 294.10,
            "executionTime": "2026-09-01T14:38:33.000Z",
        },
    })
    rec.apply_broker_evidence(DECISION, evidence)

    only_first = _snapshot(_adbe_open_row(units=51.0))
    broker = _TruthBroker(only_first, _history())
    _verify(rec, broker, only_first)
    midway = rec.record_for(DECISION)
    assert set(midway.get("verified_position_ids") or []) == {POSITION}
    assert set(midway.get("unresolved_position_ids") or []) == {second}
    assert rec.active_for_domain(domain), "one unresolved positionId must keep the domain locked"

    broker._history_snapshot = _history(
        _adbe_closed_row(position_id=second, units=20.0))
    _verify(rec, broker, only_first)
    final = rec.record_for(DECISION)
    assert set(final.get("verified_position_ids") or []) == {POSITION}
    assert set(final.get("closed_position_ids") or []) == {second}
    assert not final.get("unresolved_position_ids")
    assert rec.active_for_domain(domain) == []


@pytest.mark.parametrize(
    ("status_id", "status_name", "expected_execution"),
    [
        (5, "PartiallyFilled", "PARTIALLY_FILLED"),
        (10, "RejectedPartiallyFilled", "REJECTED_PARTIALLY_FILLED"),
    ],
)
def test_partial_position_and_remaining_order_have_separate_completion(
        rec_env, status_id, status_name, expected_execution):
    rec, analytics = rec_env
    domain = _start_adbe(rec, analytics)
    evidence = deepcopy(ADBE_FILLED_ORDER)
    evidence["status"] = {"id": status_id, "name": status_name}
    evidence["positionExecutions"][0]["openingData"]["units"] = 20.0
    evidence["positionExecutions"][0]["remainingUnits"] = 20.0
    rec.apply_broker_evidence(DECISION, evidence)

    assert rec.active_for_domain(domain), "the position still lacks depot/history proof"
    opened = _snapshot(_adbe_open_row(units=20.0))
    _verify(rec, _TruthBroker(opened), opened)
    record = rec.record_for(DECISION)
    assert record["broker_execution_state"] == expected_execution
    assert record["verified_position_ids"] == [POSITION]
    if status_id == 5:
        assert rec.active_for_domain(domain), 'PartiallyFilled does not cancel the rest'
        evidence['status'] = {'id':9,'name':'CanceledPartiallyFilled'}
        rec.apply_broker_evidence(DECISION,evidence)
        assert rec.record_for(DECISION)['execution_state']=='CANCELED_PARTIALLY_FILLED'
    assert rec.active_for_domain(domain) == []


@pytest.mark.parametrize("status_id,status_name", [
    (5, "PartiallyFilled"),
    (10, "RejectedPartiallyFilled"),
])
def test_partial_terminal_status_without_executions_stays_blocking_until_reconstructed(
        rec_env, status_id, status_name):
    rec, analytics = rec_env
    domain = _start_adbe(rec, analytics)
    rec.apply_broker_evidence(DECISION, {
        "orderId": ORDER,
        "referenceId": REFERENCE,
        "status": {"id": status_id, "name": status_name},
        "positionExecutions": [],
    })
    record = rec.record_for(DECISION)
    assert record["state"] not in {"REJECTED", "FAILED", "UNPROVABLE"}
    assert rec.active_for_domain(domain)

    opened = _snapshot(_adbe_open_row(units=20.0))
    _verify(rec, _TruthBroker(opened), opened)
    healed = rec.record_for(DECISION)
    assert healed["position_ids"] == [POSITION]
    assert healed["verified_position_ids"] == [POSITION]
    assert bool(rec.active_for_domain(domain)) == (status_id == 5)


def test_exact_order_id_reconstructs_missing_execution_from_open_pnl(rec_env):
    rec, analytics = rec_env
    domain = _start_adbe(rec, analytics)
    rec.mark_unknown(DECISION, order_ids=[ORDER], reference_id=REFERENCE,
                     detail="lookup has no positionExecutions yet")

    opened = _snapshot(_adbe_open_row())
    _verify(rec, _TruthBroker(opened), opened)
    record = rec.record_for(DECISION)
    assert record["position_ids"] == [POSITION]
    assert record["verified_position_ids"] == [POSITION]
    assert record["filled_quantity"] == pytest.approx(51.0)
    assert record["fills"][0]["price"] == pytest.approx(293.92)
    assert rec.active_for_domain(domain) == []


def test_exact_order_id_reconstructs_trade_that_closed_before_first_open_snapshot(rec_env):
    rec, analytics = rec_env
    domain = _start_adbe(rec, analytics)
    rec.mark_unknown(DECISION, order_ids=[ORDER], reference_id=REFERENCE,
                     detail="position was never observed open")
    empty = _snapshot(generation="etoro:demo:after-close")
    history = _history(_adbe_closed_row())
    _verify(rec, _TruthBroker(empty, history), empty)

    record = rec.record_for(DECISION)
    assert record["position_ids"] == [POSITION]
    assert record["closed_position_ids"] == [POSITION]
    assert record["broker_position_status"] == "CLOSED_CONFIRMED"
    assert rec.active_for_domain(domain) == []


def test_same_symbol_and_quantity_with_a_different_order_id_never_reconstructs(rec_env):
    rec, analytics = rec_env
    domain = _start_adbe(rec, analytics)
    rec.mark_unknown(DECISION, order_ids=[ORDER], reference_id=REFERENCE,
                     detail="only a symbol-shaped candidate exists")
    impostor = _snapshot(_adbe_open_row(
        position_id="manual-adbe-position", order_id="different-order"))
    _verify(rec, _TruthBroker(impostor), impostor)

    record = rec.record_for(DECISION)
    assert record.get("position_ids") == []
    assert record.get("verified_position_ids") == []
    assert rec.active_for_domain(domain)


def test_position_snapshot_force_uses_one_fresh_generation_not_stale_id_cache(monkeypatch):
    from broker.etoro import EtoroBroker

    broker = EtoroBroker(paper=True, api_key="api", user_key="account")
    broker._bind_account_identity({"demoCid": 21577959})
    broker._instrument_by_id = {
        1126: {"symbol": "ADBE", "_bot_asset_type": "stock"},
        1127: {"symbol": "MSFT", "_bot_asset_type": "stock"},
    }
    broker._positionskurs = lambda *_a, **_k: (300.0, "TEST", 0.0)
    payloads = [
        {"clientPortfolio": {"positions": [_adbe_open_row()]}},
        {"clientPortfolio": {"positions": [{
            **_adbe_open_row(position_id="new-position", order_id="new-order"),
            "instrumentId": 1127,
        }]}},
    ]
    calls = []

    def request(method, path, **_kwargs):
        calls.append((method, path))
        return deepcopy(payloads.pop(0))

    broker._request = request
    assert {p.broker_id for p in broker.positionen()} == {POSITION}
    assert broker.current_position_ids(force=False) == {POSITION}

    snapshot = broker.position_snapshot(force=True)
    assert snapshot["complete"] is True
    assert snapshot["open_ids"] == {"new-position"}
    assert set(snapshot["rows_by_position_id"]) == {"new-position"}
    assert snapshot["rows_by_position_id"]["new-position"]["orderId"] == "new-order"
    assert snapshot["snapshot_id"] != ""
    assert len(calls) == 2, "one initial and exactly one forced PnL request are expected"


def test_trade_history_snapshot_marks_page_limit_as_incomplete(monkeypatch):
    from broker.etoro import EtoroBroker

    broker = EtoroBroker(paper=True, api_key="api", user_key="account")
    full_page = [
        _adbe_closed_row(position_id=f"p-{index}", order_id=f"o-{index}", units=1)
        for index in range(200)
    ]
    broker._request = lambda *_a, **_k: deepcopy(full_page)

    limited = broker.trade_history_snapshot(
        "2026-09-01", max_pages=1, force=True)
    assert len(limited["rows"]) == 200
    assert limited["complete"] is False


def test_trade_history_snapshot_short_final_page_is_complete(monkeypatch):
    from broker.etoro import EtoroBroker

    broker = EtoroBroker(paper=True, api_key="api", user_key="account")
    broker._request = lambda *_a, **_k: [_adbe_closed_row()]
    result = broker.trade_history_snapshot(
        "2026-09-01", max_pages=3, force=True)
    assert result["rows"][0]["positionId"] == POSITION
    assert result["complete"] is True


def test_incomplete_history_cannot_make_a_negative_terminal_conclusion(rec_env, monkeypatch):
    rec, analytics = rec_env
    import config

    domain = _start_adbe(rec, analytics)
    rec.apply_broker_evidence(DECISION, deepcopy(ADBE_FILLED_ORDER))
    monkeypatch.setattr(config, "ETORO_POSITION_CONFIRMATION_GRACE_SECONDS", 0)
    monkeypatch.setattr(config, "ETORO_POSITION_CONFIRMATION_MIN_ABSENT_SNAPSHOTS", 1)
    empty = _snapshot(generation="etoro:demo:empty")
    broker = _TruthBroker(empty, _history(complete=False))
    _verify(rec, broker, empty)

    record = rec.record_for(DECISION)
    assert broker.history_calls == 1, (
        "negative evidence must use the completeness-aware history snapshot")
    assert record["state"] != "UNPROVABLE"
    assert rec.active_for_domain(domain)


def test_domain_blocking_detail_is_scoped_and_names_the_real_adbe_anchor(rec_env):
    rec, analytics = rec_env
    domain = _start_adbe(rec, analytics)
    rec.mark_unknown(DECISION, order_ids=[ORDER], reference_id=REFERENCE,
                     detail="waiting for exact broker truth")

    detail = rec.blocking_detail(domain)
    rendered = str(detail)
    assert "ADBE" in rendered
    assert ORDER in rendered
    assert "UNKNOWN_AFTER_SUBMIT" in rendered
    assert rec.active_for_domain(domain)

    other = rec.domain_key(
        paper=True, profile="offensiv", account_fingerprint="other-account")
    assert rec.active_for_domain(other) == []
    assert not rec.blocking_detail(other)


class _InvalidJson202:
    status_code = 202
    ok = True
    content = b"{invalid-json"
    text = "{invalid-json"
    headers = {}

    def json(self):
        raise ValueError("invalid JSON after accepted POST")


class _OneResponseSession:
    def __init__(self):
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return _InvalidJson202()

    def close(self):
        return None


def test_http_202_with_invalid_json_is_post_attempt_ambiguity_not_failed(rec_env, monkeypatch):
    rec, analytics = rec_env
    import config
    from broker.base import BrokerFehler, OrderStatusUnklar
    from broker.etoro import EtoroBroker
    from contracts import Instrument, SimpleContract
    from order_execution import submit_protected_buy

    session = _OneResponseSession()
    broker = EtoroBroker(
        paper=True, api_key="api", user_key="account", session=session)
    broker._connected = True
    broker._account_fingerprint = ACCOUNT
    broker._resolve = lambda _instrument: {
        "instrumentId": 1126, "symbol": "ADBE", "symbolFull": "ADBE"}
    broker._settlement = lambda _instrument: "real"
    broker._validate_protection = lambda *_a, **_k: None
    broker._lookup_order = lambda **_k: (_ for _ in ()).throw(
        BrokerFehler("not visible yet"))
    monkeypatch.setattr(config, "ETORO_REQUIRE_COST_QUOTE", False)
    monkeypatch.setattr(config, "ETORO_REQUIRE_LIVE_PROTECTION_QUOTE", False)
    monkeypatch.setattr("broker.etoro.time.sleep", lambda *_a, **_k: None)
    instrument = Instrument(
        "ADBE", SimpleContract("ADBE"), "stock", "USD", "Software", "ETORO")

    analytics.record({
        "decision_id": DECISION, "symbol": "ADBE", "asset_type": "stock",
        "broker": "etoro", "paper": True, "status": "APPROVED"})
    with pytest.raises(OrderStatusUnklar):
        submit_protected_buy(
            broker, instrument, 51, 290.07, 287.1696, 295.8707,
            decision_id=DECISION)

    posts = [call for call in session.calls if call[0].upper() == "POST"]
    assert len(posts) == 1
    record = rec.record_for(DECISION)
    assert record["state"] in rec.NON_TERMINAL
    assert record["state"] != "FAILED"
    assert record["reference_id"]
    assert rec.active_for_domain(record["domain"])


def test_worker_stop_and_immediate_restart_creates_a_live_new_generation(monkeypatch):
    import config
    import etoro_reconciliation as rec
    from broker.etoro import EtoroBroker

    monkeypatch.setattr(config, "ETORO_PRIVATE_WS_ENABLED", False)
    monkeypatch.setattr(config, "ETORO_RECONCILIATION_INTERVAL_SECONDS", 0.01)
    entered = threading.Event()
    release = threading.Event()
    calls = []

    def blocking_tick(*_a, **_k):
        calls.append(threading.get_ident())
        if len(calls) == 1:
            entered.set()
            release.wait(2)
        return []

    monkeypatch.setattr(rec, "background_tick", blocking_tick)
    broker = EtoroBroker(paper=True, api_key="api", user_key="account")
    broker._connected = True
    broker.ensure_runtime_services()
    assert entered.wait(3), "the first reconciliation generation never ticked"
    first = broker.reconciliation_worker_status()
    assert _worker_running(first)

    # Release shortly after disconnect begins.  This covers implementations
    # which correctly join the old generation as well as generation-token
    # implementations which replace it asynchronously.
    releaser = threading.Thread(target=lambda: (time.sleep(0.02), release.set()))
    releaser.start()
    broker.disconnect()
    releaser.join(timeout=2)
    broker._connected = True
    broker.ensure_runtime_services()
    assert _eventually(
        lambda: _worker_running(broker.reconciliation_worker_status()), timeout=3)
    second = broker.reconciliation_worker_status()
    if "generation" in first and "generation" in second:
        assert second["generation"] > first["generation"]
    broker.disconnect()
    assert _eventually(
        lambda: not _worker_running(broker.reconciliation_worker_status()), timeout=3)


def test_worker_survives_one_reconciliation_exception(monkeypatch):
    import config
    import etoro_reconciliation as rec
    from broker.etoro import EtoroBroker

    monkeypatch.setattr(config, "ETORO_PRIVATE_WS_ENABLED", False)
    monkeypatch.setattr(config, "ETORO_RECONCILIATION_INTERVAL_SECONDS", 0.01)
    recovered = threading.Event()
    calls = []

    def flaky_tick(*_a, **_k):
        calls.append(len(calls) + 1)
        if len(calls) == 1:
            raise RuntimeError("one malformed lookup must not kill the worker")
        recovered.set()
        return []

    monkeypatch.setattr(rec, "background_tick", flaky_tick)
    broker = EtoroBroker(paper=True, api_key="api", user_key="account")
    broker._connected = True
    broker.ensure_runtime_services()
    try:
        assert recovered.wait(3), "worker did not perform a tick after one exception"
        status = broker.reconciliation_worker_status()
        assert _worker_running(status)
        assert len(calls) >= 2
    finally:
        broker.disconnect()
