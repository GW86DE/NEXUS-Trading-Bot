from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
from types import SimpleNamespace

import pytest

import etoro_exit_costs as exits
from position_manager import PositionRecord


NOW = datetime(2026, 9, 14, 11, 30, tzinfo=timezone.utc)  # before US regular session
ACCOUNT = hashlib.sha256(b"etoro|demo|cid:21577959").hexdigest()[:24]


def position():
    return PositionRecord(con_id="1043", symbol="PEP", asset_type="stock", currency="USD",
        quantity=109, avg_cost=136.35, entry_time="2026-09-14T10:00:00Z",
        planned_stop=135.90, planned_take=138.52, source="BOT", management_mode="AUTO",
        ownership_status="VERIFIED", reconciliation_status="CONFIRMED_OPEN",
        broker_position_ids=["3597440106"], observed_position_ids=["3597440106"],
        owned_position_ids=["3597440106"], broker_instrument_id="1043",
        broker_account_fingerprint=ACCOUNT, broker_environment="DEMO", broker_snapshot_id="fresh",
        entry_order_ids=["380127623"])


def entry():
    return {"orderId": 380127623, "accountId": 21577959, "action": "open", "transaction": "buy",
        "orderCurrency": "USD", "asset": {"currency": "USD", "instrumentId": 1043,
            "settlementType": "REAL", "leverage": 1, "side": "long"},
        "positionExecutions": [{"positionId": 3597440106, "state": "open", "openingData": {
            "orderId": 380127623, "units": 109., "avgPrice": 136.35,
            "executionTime": "2026-09-14T10:00:00Z", "fees": 1., "taxes": 0.,
            "marketSpread": 2.18, "markup": 0.}}]}


def cost_raw(fee=1.):
    values = {"markup": 0., "marketSpread": 8., "transactionFee": fee,
        "overnightFee": 0., "overWeekendFee": 0., "sdrt": 0.}
    return {"instrumentId": 1043, "lastUpdated": NOW.isoformat(),
        "costs": [{"costType": k, "amount": v, "currency": "USD"} for k, v in values.items()]}


def broker(*, bid=138.52, tradable=True, costs=None, raw=None):
    def get(*args, **kwargs):
        return deepcopy(raw if raw is not None else entry())
    def close_cost(*args):
        return exits.parse_close_costs(costs or cost_raw(), instrument_id=1043,
            fetched_at=NOW.isoformat(), now=NOW)
    return SimpleNamespace(name="etoro", paper=True, account_fingerprint=lambda: ACCOUNT,
        latest_bid_ask=lambda inst: {"bid": bid, "ask": bid+.02, "timestamp": NOW.isoformat(),
            "broker_tradable": tradable}, _request=get, dynamic_close_cost_quote=close_cost)


def test_profit_uses_bid_entry_cash_fees_exit_cash_fees_buffer_without_second_spread(monkeypatch):
    import config
    monkeypatch.setattr(config, "ETORO_EXTRA_SLIPPAGE_PCT", .0003)
    out = exits.assess(broker(), SimpleNamespace(name="PEP"), position(), 109, exit_kind="PROFIT", now=NOW)
    assert out["allow_order"] and out["decision"] == "ALLOW"
    assert out["net_profit_estimate"] == pytest.approx((138.52-136.35)*109-1-1-138.52*109*.0003)
    assert out["broker_tp_independent"] is True


def test_partial_entry_fee_allocates_only_sold_quantity_but_exit_fee_is_full_order_fee():
    out = exits.assess(broker(), SimpleNamespace(name="PEP"), position(), 36, exit_kind="PROFIT", now=NOW)
    assert out["entry_fee"] == pytest.approx(36/109)
    assert out["exit_fee_estimate"] == 1.


def test_high_current_costs_block_target_but_do_not_move_tp():
    rec = position()
    out = exits.assess(broker(costs=cost_raw(300.)), SimpleNamespace(name="PEP"), rec, 109,
        exit_kind="PROFIT", now=NOW)
    assert out["decision"] == "BLOCK_PROFIT" and not out["allow_order"]
    assert rec.planned_take == 138.52 and rec.planned_stop == 135.90


@pytest.mark.parametrize("kind", ["STOP_LOSS", "TIME_STOP", "NEWS_RISK", "STRATEGY_EXIT"])
def test_safety_never_waits_for_fee_service_even_when_unprofitable(kind):
    b = broker(bid=120., tradable=None)
    b._request = b.dynamic_close_cost_quote = lambda *a, **k: pytest.fail("No fee request on risk exit")
    out = exits.assess(b, SimpleNamespace(name="PEP"), position(), 109, exit_kind=kind, now=NOW)
    assert out["decision"] == "SAFETY_BYPASS" and out["allow_order"]
    assert out["net_profit_estimate"] is None


@pytest.mark.parametrize("kind", ["PROFIT", "STOP_LOSS"])
def test_explicit_broker_closed_does_not_enqueue_close(kind):
    out = exits.assess(broker(tradable=False), SimpleNamespace(name="PEP"), position(), 109,
        exit_kind=kind, now=NOW)
    assert not out["allow_order"] and out["decision"] == "UNKNOWN"


def test_missing_flag_is_unknown_for_discretionary_profit():
    out = exits.assess(broker(tradable=None), SimpleNamespace(name="PEP"), position(), 109,
        exit_kind="PROFIT", now=NOW)
    assert not out["allow_order"]


def test_protective_exit_does_not_need_an_ask():
    b = broker(bid=120.)
    b.latest_bid_ask = lambda _: {"bid": 120., "timestamp": NOW.isoformat()}
    out = exits.assess(b, SimpleNamespace(name="PEP"), position(), 109, exit_kind="STOP_LOSS", now=NOW)
    assert out["allow_order"] and out["decision"] == "SAFETY_BYPASS"


def test_quote_aging_during_cost_io_is_not_used_to_allow_profit(monkeypatch):
    class Clock(datetime):
        current = NOW
        @classmethod
        def now(cls, tz=None):
            return cls.current
    monkeypatch.setattr(exits, "datetime", Clock)
    b = broker()
    b.latest_bid_ask = lambda _: {"bid": 140., "ask": 140.02,
        "timestamp": (NOW-timedelta(seconds=179)).isoformat(), "broker_tradable": True}
    def slow_cost(*args):
        Clock.current = NOW+timedelta(seconds=2)
        return exits.parse_close_costs(cost_raw(), instrument_id=1043, fetched_at=NOW.isoformat(), now=NOW)
    b.dynamic_close_cost_quote = slow_cost
    out = exits.assess(b, SimpleNamespace(name="PEP"), position(), 109, exit_kind="PROFIT")
    assert not out["allow_order"] and out["decision"] == "UNKNOWN"


def test_quote_aging_during_optional_tradability_probe_is_rejected(monkeypatch):
    class Clock(datetime):
        current = NOW
        @classmethod
        def now(cls, tz=None):
            return cls.current
    monkeypatch.setattr(exits, "datetime", Clock)
    b = broker()
    b.latest_bid_ask = lambda _: {"bid": 140., "timestamp": (NOW-timedelta(seconds=179)).isoformat()}
    def probe(_):
        Clock.current = NOW+timedelta(seconds=2)
        return {"broker_tradable": True, "timestamp": NOW.isoformat()}
    b.market_session_status = probe
    with pytest.raises(ValueError, match="veraltet"):
        exits.fresh_quote(b, SimpleNamespace(name="PEP"))


@pytest.mark.parametrize("stamp", [None, "2026-09-14T11:30:00", (NOW-timedelta(seconds=181)).isoformat(),
    (NOW+timedelta(seconds=1)).isoformat()])
def test_invalid_or_future_quote_does_not_trigger_exit(stamp):
    b = broker()
    b.latest_bid_ask = lambda _: {"bid": 140., "ask": 140.02, "timestamp": stamp, "broker_tradable": True}
    out = exits.assess(b, SimpleNamespace(name="PEP"), position(), 109, exit_kind="STOP_LOSS", now=NOW)
    assert not out["allow_order"]


@pytest.mark.parametrize("field,value", [("accountId", 3), ("orderId", 380995258), ("action", "close")])
def test_wrong_entry_identity_stays_unknown(field, value):
    raw = entry(); raw[field] = value
    out = exits.assess(broker(raw=raw), SimpleNamespace(name="PEP"), position(), 109, exit_kind="PROFIT", now=NOW)
    assert out["decision"] == "UNKNOWN" and out["entry_fee"] is None


@pytest.mark.parametrize("value", [None, True, -1., float("nan"), "1.0"])
def test_missing_malformed_entry_fee_never_becomes_zero(value):
    raw = entry(); raw["positionExecutions"][0]["openingData"]["fees"] = value
    with pytest.raises(ValueError):
        exits.entry_cost_from_receipt(raw, position(), 109)


def test_cost_response_missing_component_currency_or_stale_time_fails_closed():
    base = cost_raw()
    cases = [deepcopy(base) for _ in range(4)]
    cases[0]["costs"].pop()
    cases[1]["costs"][0]["currency"] = "EUR"
    cases[2]["lastUpdated"] = (NOW-timedelta(minutes=4)).isoformat()
    cases[3]["costs"][0]["amount"] = .25  # markup cash/price basis unproven
    for raw in cases:
        with pytest.raises(ValueError):
            exits.parse_close_costs(raw, instrument_id=1043, fetched_at=NOW.isoformat(), now=NOW)


def test_native_tp_crossing_is_not_duplicated_as_software_close():
    rec = position()
    assert exits.quote_exit_decision(rec, 109, {"bid": 140., "timestamp": NOW.isoformat(),
        "broker_tradable": True}, now=NOW) is None
    assert rec.planned_stop == 135.90 and rec.planned_take == 138.52
    rec.management_mode = "PENDING_CONFIRMATION"
    assert exits.quote_exit_decision(rec, 109, {"bid": 140., "timestamp": NOW.isoformat(),
        "broker_tradable": True}, now=NOW)["kind"] == "PROFIT"


def test_time_exit_before_regular_session_needs_no_candles(monkeypatch):
    import config
    monkeypatch.setattr(config, "TIME_STOP_ENABLED", True)
    monkeypatch.setattr(config, "TIME_STOP_HOURS", 72)
    rec = position(); rec.entry_time = (NOW-timedelta(hours=73)).isoformat()
    action = exits.quote_exit_decision(rec, 109, {"bid": 136.40, "timestamp": NOW.isoformat(),
        "broker_tradable": True}, now=NOW)
    assert action["kind"] == "TIME_STOP"


def test_due_time_stop_precedes_small_profit_target(monkeypatch):
    import config
    monkeypatch.setattr(config, "TIME_STOP_ENABLED", True)
    monkeypatch.setattr(config, "TIME_STOP_HOURS", 72)
    rec = position(); rec.entry_time = (NOW-timedelta(hours=80)).isoformat()
    rec.management_mode = "PENDING_CONFIRMATION"
    rec.avg_cost = 100.; rec.planned_stop = 99.; rec.planned_take = 100.2
    action = exits.quote_exit_decision(rec, 109, {"bid": 100.3, "timestamp": NOW.isoformat(),
        "broker_tradable": True}, now=NOW)
    assert action["kind"] == "TIME_STOP"


def test_rejected_close_cost_request_uses_cooldown_and_never_submits_order(monkeypatch):
    from broker.etoro import EtoroBroker
    from broker.base import BrokerFehler, NichtUnterstuetzt
    b = EtoroBroker(paper=True, api_key="offline", user_key="offline")
    monkeypatch.setattr(b, "_resolve", lambda _: {"instrumentId": 1043})
    monkeypatch.setattr(b, "account_fingerprint", lambda: ACCOUNT)
    requests = []
    def request(method, path, **kwargs):
        requests.append((method, path, kwargs))
        raise BrokerFehler("Close action not supported")
    monkeypatch.setattr(b, "_request", request)
    for _ in range(2):
        with pytest.raises(NichtUnterstuetzt):
            b.dynamic_close_cost_quote(SimpleNamespace(name="PEP"), "3597440106", 109.)
    assert len(requests) == 1 and requests[0][1] == "/api/v2/trading/info/demo/costs"
    assert requests[0][2]["payload"]["positionIds"] == [3597440106]


def test_pulsar_cost_wait_does_not_consume_partial_claim(monkeypatch):
    from pulsar import positions
    calls = []
    now = datetime.now(timezone.utc)
    b = SimpleNamespace(latest_bid_ask=lambda _: {"bid": 140, "timestamp": now.isoformat()},
        _quote_age_seconds=lambda _: 0)
    monkeypatch.setattr(positions, "reconcile_partial", lambda _: None)
    monkeypatch.setattr(positions, "decision", lambda *a, **k: {"action": "PARTIAL", "quantity": 36,
        "stop": 135.90, "reason": "PULSAR: ein Drittel bei +2R"})
    monkeypatch.setattr(positions, "claim_partial", lambda *a: calls.append(a))
    result = positions.manage(b, SimpleNamespace(name="PEP"), position(), {"id": "p"}, can_sell=True,
        close=lambda *a: pytest.fail("No close with unknown costs"), register_exit=lambda *a: None,
        assess_exit=lambda kind, qty: {"allow_order": False, "decision": "UNKNOWN", "reason": "Kosten fehlen"})
    assert result["action"] == "WAIT" and calls == []


def test_pulsar_rechecks_frozen_target_after_cost_quote(monkeypatch):
    from pulsar import positions
    now = datetime.now(timezone.utc)
    b = SimpleNamespace(latest_bid_ask=lambda _: {"bid": 140., "timestamp": now.isoformat()},
        _quote_age_seconds=lambda _: 0)
    monkeypatch.setattr(positions, "reconcile_partial", lambda _: None)
    monkeypatch.setattr(positions, "decision", lambda *a, **k: {"action": "PARTIAL", "quantity": 36,
        "stop": 135.90, "reason": "PULSAR: ein Drittel bei +2R"})
    monkeypatch.setattr(positions, "claim_partial", lambda *a: pytest.fail("Target no longer reached"))
    result = positions.manage(b, SimpleNamespace(name="PEP"), position(),
        {"id": "p", "plan": {"price": 136.35, "R": 1.}}, can_sell=True,
        close=lambda *a: pytest.fail("No close below frozen target"), register_exit=lambda *a: None,
        assess_exit=lambda kind, qty: {"allow_order": True, "decision": "ALLOW", "quote_price": 137.})
    assert result["action"] == "WAIT" and not result["exit_assessment"]["allow_order"]


def test_profit_cost_wait_does_not_delay_existing_pulsar_stop_tightening(monkeypatch):
    from pulsar import positions
    now = datetime.now(timezone.utc)
    updates = []
    def protection(inst, qty, stop, take, **kwargs):
        updates.append((stop, take))
        return {"protection_confirmed": True}
    b = SimpleNamespace(latest_bid_ask=lambda _: {"bid": 140., "timestamp": now.isoformat()},
        reconcile_position_protection=protection)
    rec = position()
    monkeypatch.setattr(positions, "reconcile_partial", lambda _: None)
    monkeypatch.setattr(positions, "decision", lambda *a, **k: {"action": "PARTIAL", "quantity": 36,
        "stop": 136.35, "reason": "PULSAR: ein Drittel bei +2R"})
    result = positions.manage(b, SimpleNamespace(name="PEP"), rec,
        {"id": "p", "plan": {"price": 136.35, "R": 1., "take": 138.52}}, can_sell=True,
        close=lambda *a: pytest.fail("Profit costs unknown"), register_exit=lambda *a: None,
        assess_exit=lambda *a: {"allow_order": False, "reason": "Kosten fehlen"})
    assert result["action"] == "WAIT" and updates == [(136.35, 138.52)]
    assert rec.planned_stop == 136.35 and rec.planned_take == 138.52
