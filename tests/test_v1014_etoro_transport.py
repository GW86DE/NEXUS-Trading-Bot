"""HTTP attempt accounting and durable scoped WS hints, with no broker calls."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from types import SimpleNamespace as NS

import pytest
import requests


CID = "21577959"
ACCOUNT = hashlib.sha256(f"etoro|demo|cid:{CID}".encode()).hexdigest()[:24]


class Session:
    def __init__(self, replies):
        self.replies = iter(replies)
        self.calls = []

    def request(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        reply = next(self.replies)
        if isinstance(reply, Exception):
            raise reply
        return reply


def response(status=200, headers=None):
    r = requests.Response()
    r.status_code, r._content = status, b'{}'
    r.headers.update(headers or {})
    return r


def broker(session):
    from broker.etoro import EtoroBroker
    return EtoroBroker(paper=True, api_key="offline-api", user_key="offline-user", session=session)


def test_every_retry_reserves_a_separate_http_attempt(monkeypatch):
    import broker.etoro as etoro
    reservations = []
    session = Session([response(503), requests.ConnectionError("offline"), response()])
    b = broker(session)
    b._limit_read = NS(acquire=lambda **kw: reservations.append(kw) or True)
    monkeypatch.setattr(etoro.time, "sleep", lambda _: None)
    assert b._request("GET", "/read") == {}
    assert len(session.calls) == len(reservations) == 3


def test_post_is_not_retried_even_with_safe_retry_true(monkeypatch):
    from broker.base import VerbindungVerloren
    session = Session([requests.ConnectionError("offline"), response()])
    b = broker(session)
    with pytest.raises(VerbindungVerloren):
        b._request("POST", "/api/v2/trading/execution/demo/market-open-orders", payload={}, safe_retry=True)
    assert len(session.calls) == 1


@pytest.mark.parametrize("header", ["60", "Mon, 14 Sep 2026 20:01:00 GMT"])
def test_broker_60_second_cooldown_is_retained_across_adapter_instances(monkeypatch, header):
    from broker.base import BrokerFehler
    import broker.etoro as etoro
    import etoro_transport_budget as budget
    wall = 1789416000.0  # 2026-09-14T20:00:00Z
    monkeypatch.setattr(etoro.time, "time", lambda: wall)
    monkeypatch.setattr(budget.time, "time", lambda: wall)
    session = Session([response(429, {"Retry-After": header}), response()])
    first = broker(session)
    with pytest.raises(BrokerFehler):
        first._request("GET", "/read")
    assert len(session.calls) == 1
    second = broker(Session([response()]))
    assert not second._limit_read.acquire(timeout=0)
    with sqlite3.connect(budget._path()) as con:
        deadline = con.execute("SELECT until_at FROM http_cooldowns").fetchone()[0]
    assert deadline == pytest.approx(wall + 60)


def test_costs_have_their_own_twenty_per_minute_bucket():
    session = Session([response()])
    b = broker(session)
    used = []
    b._limit_read = NS(acquire=lambda **_: pytest.fail("cost POST used generic read budget"))
    b._limit_costs = NS(acquire=lambda **_: used.append("costs") or True)
    b._request("POST", "/api/v2/trading/info/demo/costs", payload={})
    assert used == ["costs"]
    assert broker(Session([]))._limit_costs.maximum == 18


def test_credentials_share_windows_but_accounts_and_environments_do_not():
    a, b = broker(Session([])), broker(Session([]))
    assert a._limit_read.acquire(timeout=0)
    assert not b._limit_read.acquire(timeout=0)
    from broker.etoro import EtoroBroker
    other = EtoroBroker(paper=True, api_key="different-api", user_key="different-user", session=Session([]))
    live = EtoroBroker(paper=False, api_key="offline-api", user_key="offline-user", session=Session([]))
    assert other._limit_read.acquire(timeout=0)
    assert live._limit_read.acquire(timeout=0)


@pytest.fixture
def reconciliation(monkeypatch):
    import etoro_reconciliation as rec
    data = {"records": {}}
    monkeypatch.setattr(rec, "_load", lambda: data)
    monkeypatch.setattr(rec, "_save", lambda _: None)
    monkeypatch.setattr(rec, "record_execution_event", lambda *_, **kw: None)
    return rec, data


def event(kind="Trading.Position.Closed", **content):
    return {"message_id": "broker-message", "message_type": kind,
            "received_at_utc": "2026-09-14T13:32:26+00:00",
            "content": {"PositionID": "3597440106", "InstrumentID": "1043", **content}}


def record():
    return {"decision_id": 54, "broker": "etoro", "paper": True,
            "account_fingerprint": ACCOUNT, "reference_id": "entry-reference",
            "position_ids": ["3597440106"], "order_ids": ["380127623"],
            "submit_state": "ACCEPTED", "execution_state": "FILLED",
            "position_state": "OPEN_CONFIRMED"}


def apply(rec, ev, **kwargs):
    return rec.apply_stream_event(ev, account_fingerprint=ACCOUNT, paper=True, cid=CID, **kwargs)


def test_unmatched_close_is_durable_redacted_and_replayed_after_late_import(reconciliation):
    rec, data = reconciliation
    import etoro_stream_inbox as inbox
    ev = event(NetProfit=244.16, CloseRate=138.59, apiKey="must-not-survive")
    assert apply(rec, ev) == []
    hints = inbox.closure_candidates(account_fingerprint=ACCOUNT, paper=True, position_id="3597440106")
    assert hints[0]["content"]["apiKey"] == "[REDACTED]"
    assert hints[0]["content"]["NetProfit"] == 244.16
    assert inbox.metrics(account_fingerprint=ACCOUNT, paper=True)["unmatched"] == 1
    data["records"]["54"] = record()
    pending = inbox.replay_pending(account_fingerprint=ACCOUNT, paper=True)
    assert apply(rec, pending[0], _replay=True) == [54]
    assert data["records"]["54"]["position_state"] == "OPEN_CONFIRMED"  # REST must confirm
    assert inbox.metrics(account_fingerprint=ACCOUNT, paper=True)["received"] == 1
    assert inbox.metrics(account_fingerprint=ACCOUNT, paper=True)["matched"] == 1


def test_close_order_id_never_contaminates_entry_ids(reconciliation):
    rec, data = reconciliation
    data["records"]["54"] = record()
    assert apply(rec, event("Trading.OrderForClose.Update", OrderID="380995258")) == [54]
    r = data["records"]["54"]
    assert r["order_ids"] == ["380127623"]
    assert r["stream_close_order_ids"] == ["380995258"]
    assert r["position_state"] == "OPEN_CONFIRMED"
    assert r["execution_state"] == "FILLED"


def test_open_event_can_add_order_only_from_its_own_reference(reconciliation):
    rec, data = reconciliation
    r = record()
    r.update(submit_state="POST_MAY_HAVE_BEEN_SENT", execution_state="UNKNOWN", position_state="NOT_SEEN", order_ids=[])
    data["records"]["54"] = r
    assert apply(rec, event("Trading.OrderForOpen.Update", OrderID="new-entry", RequestGuid="entry-reference")) == [54]
    assert r["order_ids"] == ["new-entry"]
    assert r["submit_state"] == "ACCEPTED"
    assert r["execution_state"] == "UNKNOWN"


@pytest.mark.parametrize("fault", ["cid", "unbound", "record_account", "environment"])
def test_foreign_or_unbound_events_cannot_update_matching_position(reconciliation, fault):
    rec, data = reconciliation
    import etoro_stream_inbox as inbox
    r = record()
    data["records"]["54"] = r
    ev = event(OrderID="wrong-close")
    if fault == "cid":
        ev["content"]["CID"] = "other"
    elif fault == "record_account":
        r["account_fingerprint"] = "other-account"
    elif fault == "environment":
        r["paper"] = False
    original = json.dumps(r, sort_keys=True)
    result = rec.apply_stream_event(ev) if fault == "unbound" else apply(rec, ev)
    assert result == []
    assert json.dumps(r, sort_keys=True) == original
    if fault == "cid":
        assert inbox.metrics(account_fingerprint=ACCOUNT, paper=True)["quarantined"] == 1
        assert inbox.closure_candidates(account_fingerprint=ACCOUNT, paper=True, position_id="3597440106") == []


def test_duplicate_delivery_has_one_durable_event_and_no_double_booking(reconciliation):
    rec, data = reconciliation
    import etoro_stream_inbox as inbox
    data["records"]["54"] = record()
    assert apply(rec, event()) == [54]
    assert apply(rec, {**event(), "received_at_utc": "2026-09-14T13:34:00+00:00"}) == [54]
    metrics = inbox.metrics(account_fingerprint=ACCOUNT, paper=True)
    assert metrics["received"] == 2
    assert metrics["unique"] == metrics["matched"] == 1
    assert data["records"]["54"]["order_ids"] == ["380127623"]


def test_stream_counts_received_matched_unmatched_error_and_malformed():
    from broker.etoro_stream import EtoroPrivateStream
    results = iter([[54], [], RuntimeError("offline")])
    def callback(ev):
        r = next(results)
        if isinstance(r, Exception):
            raise r
        return r
    stream = EtoroPrivateStream("offline", "offline", callback)
    for _ in range(3):
        stream._on_message(None, json.dumps({"messages": [{"topic": "private", "id": "m", "type": "Trading.Position.Closed", "content": {"PositionID": 1}}]}))
    stream._on_message(None, "[]")
    s = stream.status()
    assert (s.private_events_received, s.private_events_matched, s.private_events_unmatched,
            s.private_events_errors, s.malformed_messages) == (3, 1, 1, 1, 1)


@pytest.mark.parametrize("fault", ["corrupt_database", "metrics", "one_event"])
def test_optional_inbox_failure_never_blocks_rest_worker(monkeypatch, fault):
    import etoro_stream_inbox as inbox
    import etoro_reconciliation as rec
    from threading import Event
    b = broker(Session([]))
    b._identity_loaded = True
    b._account_cid = CID
    b._account_fingerprint = ACCOUNT
    b._connected = True
    called = Event()
    processed = []
    if fault == "corrupt_database":
        inbox._path().write_bytes(b"this is not SQLite")
    else:
        monkeypatch.setattr(inbox, "replay_pending", lambda **_: [{"bad": True}, {"good": True}] if fault == "one_event" else [])
        def metric(**_):
            if fault == "metrics":
                raise sqlite3.DatabaseError("offline corrupt metrics")
            return {"received": 2}
        monkeypatch.setattr(inbox, "metrics", metric)
        def apply_pending(ev, **_):
            if ev.get("bad"):
                raise ValueError("offline bad event")
            processed.append(ev)
            return []
        monkeypatch.setattr(rec, "apply_stream_event", apply_pending)
    def rest_tick(*_, **kwargs):
        called.set()
        b._reconcile_stop.set()
        return []
    monkeypatch.setattr(rec, "background_tick", rest_tick)
    monkeypatch.setattr(rec, "active_for_domain", lambda _: [])
    b._start_reconciliation_worker()
    assert called.wait(2), "optional stream failure prevented authoritative REST"
    b._reconcile_thread.join(timeout=2)
    state = b.reconciliation_worker_status()
    assert state["last_success_at_utc"]
    assert not state["last_error"]
    assert state["stream_inbox"]["rest_fallback"]
    assert state["stream_inbox"]["replay_error"]
    if fault == "one_event":
        assert processed == [{"good": True}]
