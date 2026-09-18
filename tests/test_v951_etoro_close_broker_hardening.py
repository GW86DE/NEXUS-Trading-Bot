from __future__ import annotations

import time
from copy import deepcopy
from types import SimpleNamespace

import pytest


ACCOUNT_CID = "demo-account-close-hardening"
POSITION = "7001"
INSTRUMENT = 1126


def _bound_broker(monkeypatch, tmp_path):
    monkeypatch.setenv("TRADINGBOT_TEST_STATE_DIR", str(tmp_path))
    import config
    from broker.etoro import EtoroBroker

    monkeypatch.setattr(config, "ETORO_CLOSE_ORDER_POLL_SECONDS", 0.0)
    monkeypatch.setattr(config, "ETORO_TRADE_HISTORY_POLL_SECONDS", 0.0)
    broker = EtoroBroker(paper=True, api_key="api", user_key="user")
    broker._connected = True
    broker._bind_account_identity({"demoCid": ACCOUNT_CID})
    broker._instrument_by_id[INSTRUMENT] = {
        "symbol": "ADBE", "symbolFull": "ADBE",
        "_bot_asset_type": "stock",
    }
    broker._symbol_for_id = lambda iid: "ADBE" if int(iid) == INSTRUMENT else f"ETORO_{iid}"
    return broker


def _open_pnl(units=5.0):
    return {
        "_snapshot_id": "snapshot-close-test",
        "clientPortfolio": {"positions": [{
            "positionId": POSITION,
            "instrumentId": INSTRUMENT,
            "units": units,
            "isBuy": True,
        }]},
    }


def _history_row(*, execution_key="executionId", execution_id="exec-1",
                 close_order_id=""):
    row = {
        "positionId": POSITION,
        "instrumentId": INSTRUMENT,
        "orderId": "entry-order-1",
        "closeTimestamp": "2026-09-01T15:01:02Z",
        "closeRate": 300.5,
        "units": 5.0,
        "isBuy": True,
        execution_key: execution_id,
    }
    if close_order_id:
        row["closeOrderId"] = close_order_id
    return row


def test_close_fill_identity_is_source_independent_with_execution_id():
    from broker.etoro import _close_fill_identity

    close_info = {
        "executionId": "execution-9", "rate": 300.5, "units": 5.0}
    history = {
        "executionID": "execution-9", "closeRate": 300.5, "units": 5}
    from_info = _close_fill_identity(
        "account-a", POSITION, "close-order-7", close_info,
        "2026-09-01T15:01:02Z")
    from_history = _close_fill_identity(
        "account-a", POSITION, "", history,
        "2026-09-01T15:01:02Z")

    assert from_info == from_history
    assert "close-order-7" not in from_info
    assert from_info.endswith(":exec:execution-9")


def test_close_fill_fallback_normalizes_time_price_and_quantity():
    from broker.etoro import _close_fill_identity

    from_info = _close_fill_identity(
        "account-a", POSITION, "close-order-7",
        {"rate": "300.5000", "closedUnits": "5.000"},
        "2026-09-01T17:01:02+02:00")
    from_history = _close_fill_identity(
        "account-a", POSITION, "",
        {"closeRate": 300.5, "units": 5},
        "2026-09-01T15:01:02Z")

    assert from_info == from_history


def test_trade_history_dedupe_keeps_all_supported_execution_id_variants(
        monkeypatch, tmp_path):
    broker = _bound_broker(monkeypatch, tmp_path)
    rows = [
        _history_row(execution_key="positionExecutionID", execution_id="pe-1"),
        _history_row(execution_key="positionExecutionID", execution_id="pe-2"),
        _history_row(execution_key="dealID", execution_id="deal-3"),
    ]
    broker._request = lambda *_a, **_k: deepcopy(rows)

    result = broker.trade_history_snapshot(
        "2026-09-01", max_pages=1, force=True)

    assert len(result["rows"]) == 3


def test_empty_or_incomplete_pnl_is_never_a_complete_snapshot(
        monkeypatch, tmp_path):
    from broker.base import BrokerFehler

    broker = _bound_broker(monkeypatch, tmp_path)
    for payload in ({}, {"clientPortfolio": {}},
                    {"clientPortfolio": {"positions": None}}):
        broker._request = lambda *_a, _payload=payload, **_k: deepcopy(_payload)
        with pytest.raises(BrokerFehler, match="P&L-Snapshot"):
            broker.position_snapshot(force=True)

    broker._request = lambda *_a, **_k: {
        "clientPortfolio": {"positions": []}}
    snapshot = broker.position_snapshot(force=True)
    assert snapshot["complete"] is True
    assert snapshot["open_ids"] == set()


def test_unbound_identity_blocks_snapshot_fills_and_close(monkeypatch, tmp_path):
    from broker.base import AuthentifizierungsFehler
    from broker.etoro import EtoroBroker

    broker = EtoroBroker(paper=True, api_key="api", user_key="user")
    instrument = SimpleNamespace(name="ADBE")
    with pytest.raises(AuthentifizierungsFehler, match="Kontoidentitaet"):
        broker.position_snapshot(force=True)
    with pytest.raises(AuthentifizierungsFehler, match="Kontoidentitaet"):
        broker.fills()
    with pytest.raises(AuthentifizierungsFehler, match="Kontoidentitaet"):
        broker.schliesse_position(
            instrument, 1.0, position_ids=[POSITION],
            instrument_id=str(INSTRUMENT))


def test_close_info_and_history_emit_one_fill_and_never_preconfirm(
        monkeypatch, tmp_path):
    import broker_exit_journal as journal
    import etoro_reconciliation as reconciliation

    broker = _bound_broker(monkeypatch, tmp_path)
    account = broker.account_fingerprint()
    intent, created = journal.begin(
        broker="etoro", account_fingerprint=account, environment="DEMO",
        instrument_id=str(INSTRUMENT), position_id=POSITION, quantity=5,
        client_order_id="request-close-1")
    assert created
    journal.update(
        intent["intent_id"], "SUBMITTED", broker_order_id="close-order-1")
    broker.close_order_info = lambda _oid: {
        "orderId": "close-order-1", "instrumentId": INSTRUMENT,
        "positions": [{
            "positionId": POSITION, "instrumentId": INSTRUMENT,
            "rate": 300.5, "units": 5.0,
            "occurred": "2026-09-01T15:01:02Z",
            "executionId": "exec-cross-source",
        }],
    }
    history_row = _history_row(
        execution_key="executionID", execution_id="exec-cross-source")
    broker.trade_history_snapshot = lambda *_a, **_k: {
        "rows": [history_row], "complete": True}
    monkeypatch.setattr(
        reconciliation, "confirm_position_closed",
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("adapter must not terminalize reconciliation")))
    monkeypatch.setattr(
        journal, "confirm_from_fill",
        lambda **_k: (_ for _ in ()).throw(
            AssertionError("adapter must not terminalize exit journal")))

    fills = [fill for fill in broker.fills() if fill.side == "SELL"]

    assert len(fills) == 1
    assert fills[0].raw_fill_id == "exec-cross-source"
    assert fills[0].broker_detail["positionId"] == POSITION
    assert fills[0].broker_detail["fillId"] == fills[0].fill_id
    with pytest.raises(TypeError):
        fills[0].broker_detail["positionId"] = "mutated"
    active = journal.active(broker="etoro", account_fingerprint=account)
    assert len(active) == 1 and active[0]["status"] == "SUBMITTED"


def test_history_recovery_cursor_waits_for_downstream_commit_ack(
        monkeypatch, tmp_path):
    broker = _bound_broker(monkeypatch, tmp_path)
    broker._last_close_order_poll = time.monotonic()
    incomplete = _history_row()
    incomplete.pop("closeRate")
    broker.trade_history_snapshot = lambda *_a, **_k: {
        "rows": [incomplete], "complete": True}

    assert not any(fill.side == "SELL" for fill in broker.fills())
    assert broker._history_recovery_complete is False
    assert broker._history_recovery_pending_ack is False

    broker.trade_history_snapshot = lambda *_a, **_k: {
        "rows": [_history_row()], "complete": True}
    broker._last_history_poll = 0.0
    recovery_fills = [fill for fill in broker.fills() if fill.side == "SELL"]
    assert len(recovery_fills) == 1
    # Selbst ein vollstaendig gelesener und gequeueter Brokerlauf darf den
    # grossen Cursor noch nicht schliessen. Ledger/FillTracker quittieren ihn
    # erst nach ihrem dauerhaften Commit ueber die explizite Ack-API.
    assert broker._history_recovery_pending_ack is True
    assert broker._history_recovery_complete is False
    assert broker.ack_history_recovery_committed() is False
    assert broker.ack_history_recovery_committed(
        committed_fill_ids=[recovery_fills[0].fill_id]) is True
    assert broker._history_recovery_complete is True
    assert broker._history_recovery_pending_ack is False
    assert broker.ack_history_recovery_committed() is False


def test_failed_history_consumer_then_empty_poll_cannot_ack_cursor(
        monkeypatch, tmp_path):
    import config

    broker = _bound_broker(monkeypatch, tmp_path)
    broker._last_close_order_poll = time.monotonic()
    broker.trade_history_snapshot = lambda *_a, **_k: {
        "rows": [_history_row()], "complete": True}

    first = [fill for fill in broker.fills() if fill.side == "SELL"]
    assert len(first) == 1
    assert broker._history_recovery_pending_ack is True

    # Simulierter Crash vor Ledger/FillTracker-Commit: der naechste kurze Poll
    # liefert keinen History-Fill. Dieser leere Aufruf darf die ausstehende ID
    # des vorigen Recovery-Laufs nicht pauschal quittieren.
    monkeypatch.setattr(config, "ETORO_TRADE_HISTORY_POLL_SECONDS", 60.0)
    assert [fill for fill in broker.fills() if fill.side == "SELL"] == []
    assert broker.ack_history_recovery_committed(
        committed_fill_ids=[]) is False
    assert broker._history_recovery_complete is False

    # Erst der wiederholte History-Beleg plus erfolgreicher Downstream-Commit
    # darf den grossen Cursor schliessen.
    broker._last_history_poll = 0.0
    retried = [fill for fill in broker.fills() if fill.side == "SELL"]
    assert len(retried) == 1
    assert retried[0].fill_id == first[0].fill_id
    assert broker.ack_history_recovery_committed(
        committed_fill_ids=[retried[0].fill_id]) is True


def test_empty_complete_history_can_be_acknowledged_without_fill_ids(
        monkeypatch, tmp_path):
    broker = _bound_broker(monkeypatch, tmp_path)
    broker._last_close_order_poll = time.monotonic()
    broker.trade_history_snapshot = lambda *_a, **_k: {
        "rows": [], "complete": True}

    assert broker.fills() == []
    assert broker._history_recovery_pending_ack is True
    assert broker._history_recovery_expected_fill_ids == set()
    assert broker.ack_history_recovery_committed() is True


@pytest.mark.parametrize("target, has_units, expected_units", [
    (5.0, False, None),
    (2.0, True, 2.0),
])
def test_full_close_omits_units_but_partial_close_sends_units(
        monkeypatch, tmp_path, target, has_units, expected_units):
    broker = _bound_broker(monkeypatch, tmp_path)
    broker._resolve = lambda _instrument: {
        "instrumentId": INSTRUMENT, "symbol": "ADBE"}
    broker._pnl = lambda force=False: deepcopy(_open_pnl())
    requests = []

    def submit(method, path, **kwargs):
        requests.append((method, path, kwargs))
        return {"orderForClose": {"OrderID": f"close-{target}"}}

    broker._request = submit
    broker.schliesse_position(
        SimpleNamespace(name="ADBE"), target,
        position_ids=[POSITION], instrument_id=str(INSTRUMENT))

    payload = requests[0][2]["payload"]
    assert ("UnitsToDeduct" in payload) is has_units
    if has_units:
        assert payload["UnitsToDeduct"] == pytest.approx(expected_units)


def test_rejected_close_does_not_leave_active_submitting_intent(
        monkeypatch, tmp_path):
    import broker_exit_journal as journal
    from broker.base import BrokerFehler, AuftragAbgelehnt

    broker = _bound_broker(monkeypatch, tmp_path)
    broker._resolve = lambda _instrument: {
        "instrumentId": INSTRUMENT, "symbol": "ADBE"}
    broker._pnl = lambda force=False: deepcopy(_open_pnl())
    broker._request = lambda *_a, **_k: (_ for _ in ()).throw(
        AuftragAbgelehnt("eToro HTTP 400: rejected"))

    with pytest.raises(BrokerFehler, match="HTTP 400"):
        broker.schliesse_position(
            SimpleNamespace(name="ADBE"), 5,
            position_ids=[POSITION], instrument_id=str(INSTRUMENT))
    assert journal.active(
        broker="etoro", account_fingerprint=broker.account_fingerprint()) == []
    import execution_lifecycle
    assert execution_lifecycle.snapshot(active_only=True) == []


def test_ambiguous_close_response_remains_visible_and_is_never_reposted(
        monkeypatch, tmp_path):
    import broker_exit_journal as journal
    from broker.base import BrokerFehler, OrderStatusUnklar

    broker = _bound_broker(monkeypatch, tmp_path)
    broker._resolve = lambda _instrument: {
        "instrumentId": INSTRUMENT, "symbol": "ADBE"}
    broker._pnl = lambda force=False: deepcopy(_open_pnl())
    calls = []

    def invalid_json(*_a, **_k):
        calls.append(1)
        raise BrokerFehler("eToro lieferte ungueltiges JSON fuer close")

    broker._request = invalid_json
    with pytest.raises(OrderStatusUnklar):
        broker.schliesse_position(
            SimpleNamespace(name="ADBE"), 5,
            position_ids=[POSITION], instrument_id=str(INSTRUMENT))
    active = journal.active(
        broker="etoro", account_fingerprint=broker.account_fingerprint())
    assert len(active) == 1 and active[0]["status"] == "UNCLEAR"
    with pytest.raises(OrderStatusUnklar, match="bereits aktiv"):
        broker.schliesse_position(
            SimpleNamespace(name="ADBE"), 5,
            position_ids=[POSITION], instrument_id=str(INSTRUMENT))
    assert len(calls) == 1


def test_contradictory_close_response_is_unclear_and_never_reposted(
        monkeypatch, tmp_path):
    import broker_exit_journal as journal
    from broker.base import OrderStatusUnklar

    broker = _bound_broker(monkeypatch, tmp_path)
    broker._resolve = lambda _instrument: {
        "instrumentId": INSTRUMENT, "symbol": "ADBE"}
    broker._pnl = lambda force=False: deepcopy(_open_pnl())
    calls = []

    def contradictory(*_a, **_k):
        calls.append(1)
        return {
            "orderForClose": {"orderId": "close-but-error"},
            "errorCode": 99,
            "errorMessage": "contradictory broker response",
        }

    broker._request = contradictory
    with pytest.raises(OrderStatusUnklar) as raised:
        broker.schliesse_position(
            SimpleNamespace(name="ADBE"), 5,
            position_ids=[POSITION], instrument_id=str(INSTRUMENT))
    assert raised.value.accepted is True
    assert raised.value.order_ids == ["close-but-error"]
    active = journal.active(
        broker="etoro", account_fingerprint=broker.account_fingerprint())
    assert len(active) == 1 and active[0]["status"] == "UNCLEAR"
    with pytest.raises(OrderStatusUnklar, match="bereits aktiv"):
        broker.schliesse_position(
            SimpleNamespace(name="ADBE"), 5,
            position_ids=[POSITION], instrument_id=str(INSTRUMENT))
    assert len(calls) == 1


def test_bad_first_close_intent_does_not_starve_later_intent(
        monkeypatch, tmp_path):
    import broker_exit_journal as journal
    from broker.base import VerbindungVerloren

    broker = _bound_broker(monkeypatch, tmp_path)
    account = broker.account_fingerprint()
    first, _ = journal.begin(
        broker="etoro", account_fingerprint=account, environment="DEMO",
        instrument_id=str(INSTRUMENT), position_id="7000", quantity=1,
        client_order_id="request-bad")
    second, _ = journal.begin(
        broker="etoro", account_fingerprint=account, environment="DEMO",
        instrument_id=str(INSTRUMENT), position_id=POSITION, quantity=5,
        client_order_id="request-good")
    journal.update(first["intent_id"], "SUBMITTED", broker_order_id="bad-order")
    journal.update(second["intent_id"], "SUBMITTED", broker_order_id="good-order")

    def close_info(order_id):
        if order_id == "bad-order":
            raise VerbindungVerloren("temporary network loss")
        return {
            "orderId": "good-order", "instrumentId": INSTRUMENT,
            "positions": [{
                "positionId": POSITION, "instrumentId": INSTRUMENT,
                "rate": 300.5, "units": 5,
                "occurred": "2026-09-01T15:01:02Z",
                "dealID": "deal-good",
            }],
        }

    broker.close_order_info = close_info
    broker.trade_history_snapshot = lambda *_a, **_k: {
        "rows": [], "complete": True}
    fills = [fill for fill in broker.fills() if fill.side == "SELL"]

    assert [fill.broker_id for fill in fills] == [POSITION]


def test_close_info_with_foreign_position_is_quarantined(monkeypatch, tmp_path):
    import broker_exit_journal as journal

    broker = _bound_broker(monkeypatch, tmp_path)
    account = broker.account_fingerprint()
    intent, _ = journal.begin(
        broker="etoro", account_fingerprint=account, environment="DEMO",
        instrument_id=str(INSTRUMENT), position_id=POSITION, quantity=5,
        client_order_id="request-scope")
    journal.update(
        intent["intent_id"], "SUBMITTED", broker_order_id="scope-order")
    broker.close_order_info = lambda _oid: {
        "orderId": "scope-order", "instrumentId": INSTRUMENT,
        "positions": [{
            "positionId": "foreign-position", "instrumentId": INSTRUMENT,
            "rate": 300.5, "units": 5,
            "occurred": "2026-09-01T15:01:02Z",
            "executionId": "foreign-execution",
        }],
    }
    broker.trade_history_snapshot = lambda *_a, **_k: {
        "rows": [], "complete": True}

    assert [fill for fill in broker.fills() if fill.side == "SELL"] == []
    active = journal.active(broker="etoro", account_fingerprint=account)
    assert len(active) == 1 and active[0]["status"] == "UNCLEAR"


def test_mixed_close_info_is_quarantined_before_any_fill(monkeypatch, tmp_path):
    import broker_exit_journal as journal

    broker = _bound_broker(monkeypatch, tmp_path)
    account = broker.account_fingerprint()
    intent, _ = journal.begin(
        broker="etoro", account_fingerprint=account, environment="DEMO",
        instrument_id=str(INSTRUMENT), position_id=POSITION, quantity=5,
        client_order_id="request-mixed-scope")
    journal.update(
        intent["intent_id"], "SUBMITTED", broker_order_id="mixed-order")
    valid = {
        "positionId": POSITION, "instrumentId": INSTRUMENT,
        "rate": 300.5, "units": 5,
        "occurred": "2026-09-01T15:01:02Z",
        "executionId": "valid-but-mixed",
    }
    foreign = {
        "positionId": "foreign-position", "instrumentId": INSTRUMENT,
        "rate": 300.5, "units": 1,
        "occurred": "2026-09-01T15:01:03Z",
        "executionId": "foreign-mixed",
    }
    broker.close_order_info = lambda _oid: {
        "orderId": "mixed-order", "instrumentId": INSTRUMENT,
        "positions": [valid, foreign],
    }
    broker.trade_history_snapshot = lambda *_a, **_k: {
        "rows": [], "complete": True}

    assert [fill for fill in broker.fills() if fill.side == "SELL"] == []
    active = journal.active(broker="etoro", account_fingerprint=account)
    assert len(active) == 1 and active[0]["status"] == "UNCLEAR"
    assert "foreign-position" in str(active[0].get("detail_json") or "")
