"""Real adapter/engine methods; every transport response is synthetic."""
import errno
import json
import threading
from types import SimpleNamespace as NS
import pytest
import requests

from broker_observation import BrokerObservation, ProtectionObservation


def response(payload):
    return NS(status_code=200, ok=True, content=b"{}", headers={}, text="",
              json=lambda: payload)


def okx_client(payload):
    from broker.okx import OKXClient
    client = OKXClient("k", "s", "p")
    calls = []
    client.session = NS(request=lambda *a, **k: (calls.append((a, k)), response(payload))[1])
    return client, calls


def test_private_rest_success_is_not_websocket_or_other_broker_success():
    from broker.okx import OKXBroker
    client, calls = okx_client({"code": "0", "data": []})
    other = BrokerObservation("etoro", "LIVE")
    client.request("GET", "/account/instruments", private=True)
    client.request("GET", "/public/time")
    broker = OKXBroker(client=client)
    state = broker.connection_components()
    assert state["observations"]["rest"]["private"]["state"] == "OK"
    assert state["observations"]["rest"]["public"]["wire_attempts"] == 1
    assert state["ws_connected"] is False
    assert other.snapshot()["rest"] == {}
    assert len(calls) == 2


def test_last_success_survives_timeout_but_current_state_is_error():
    from broker.base import VerbindungVerloren
    client, calls = okx_client({"code": "0", "data": []})
    client.request("GET", "/account/instruments", private=True)
    previous = client._observations.snapshot()["rest"]["private"]["last_success_at"]
    def fail(*a, **k):
        raise requests.Timeout("secret-url-token")
    client.session.request = fail
    with pytest.raises(VerbindungVerloren):
        client.request("GET", "/account/balance", private=True)
    row = client._observations.snapshot()["rest"]["private"]
    assert row["state"] == "ERROR" and row["last_success_at"] == previous
    assert row["last_started_at"] and row["last_error_at"] and row["in_flight"] == 0
    assert row["wire_attempts"] == 2
    assert "secret-url-token" not in json.dumps(row)


@pytest.mark.parametrize("payload", [{"code": "0"}, {"code": "0", "data": None},
                                    {"code": "0", "data": {}}, []])
def test_missing_data_is_never_a_successful_empty_snapshot(payload):
    from broker.base import BrokerFehler
    client, _ = okx_client(payload)
    with pytest.raises(BrokerFehler, match="SCHEMA_INVALID"):
        client.request("GET", "/account/balance", private=True)
    row = client._observations.snapshot()["rest"]["private"]
    assert row["state"] == "ERROR" and row["last_success_at"] is None


def test_malformed_order_reply_stays_unknown_and_has_one_transport_attempt():
    from broker.base import OrderStatusUnklar
    client, calls = okx_client({"code": "0"})
    with pytest.raises(OrderStatusUnklar):
        client.request("POST", "/trade/order", private=True, is_order=True, body={})
    assert len(calls) == 1


def test_definitive_rejection_is_a_received_response_not_a_connection_loss():
    from broker.base import BrokerFehler
    client, calls = okx_client({"code": "1", "data": [{"sCode": "51008", "sMsg": "insufficient balance"}]})
    with pytest.raises(BrokerFehler):
        client.request("POST", "/trade/order", private=True, is_order=True, body={})
    row = client._observations.snapshot()["rest"]["private"]
    assert row["state"] == "ERROR" and row["transport_state"] == "OK"
    assert row["http_status"] == 200 and row["last_response_at"] and len(calls) == 1


def test_retry_timeout_does_not_reuse_earlier_attempts_http_success():
    obs = BrokerObservation("etoro", "DEMO")
    token = obs.begin("private", "GET")
    obs.wire_started(token); obs.response_received(token, 500)
    obs.wire_started(token); obs.finish(token, TimeoutError())
    row = obs.snapshot()["rest"]["private"]
    assert row["last_response_at"] and row["http_status"] is None
    assert row["transport_state"] == "ERROR" and row["wire_attempts"] == 2


def test_out_of_order_completion_does_not_erase_later_failure():
    obs = BrokerObservation("okx", "DEMO")
    older = obs.begin("private", "GET")
    newer = obs.begin("private", "GET")
    obs.wire_started(older); obs.wire_started(newer)
    obs.finish(newer, TimeoutError())
    obs.finish(older)
    row = obs.snapshot()["rest"]["private"]
    assert row["state"] == "ERROR" and row["error_code"] == "TimeoutError"
    assert row["completed_generation"] == newer["generation"] and row["in_flight"] == 0
    assert row["last_success_at"]  # A real response, explicitly not current success.


def test_limiter_failure_is_attempted_but_not_sent():
    from broker.base import VerbindungVerloren
    client, calls = okx_client({"code": "0", "data": []})
    client._private_limit = NS(acquire=lambda **_: False)
    with pytest.raises(VerbindungVerloren): client.request("GET", "/account/balance", private=True)
    row = client._observations.snapshot()["rest"]["private"]
    assert row["last_attempt_at"] and row["last_started_at"] is None
    assert row["wire_attempts"] == 0 and calls == []


def test_etoro_post_default_never_retries_after_transport_timeout(monkeypatch):
    from broker.etoro import EtoroBroker
    from broker.base import VerbindungVerloren
    calls = []
    def fail(*a, **k): calls.append(a); raise requests.Timeout("lost")
    broker = EtoroBroker(api_key="k", user_key="u", paper=True, session=NS(request=fail))
    broker._limit_execution = NS(acquire=lambda **_: True)
    with pytest.raises(VerbindungVerloren):
        broker._request("POST", "/api/v3/trading/execution/demo/orders", payload={})
    assert len(calls) == 1
    assert broker._observations.snapshot()["rest"]["private"]["wire_attempts"] == 1


def test_protection_observation_counts_real_duration_and_retains_success(monkeypatch):
    import broker_observation as module
    clock = [100.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])
    observation = ProtectionObservation()
    assert observation.snapshot()["state"] == "UNKNOWN"
    observation.begin(); clock[0] = 102.5
    observation.finish({"ok": True, "geprueft": 4})
    first = observation.snapshot()
    assert first["duration_ms"] == 2500 and first["checked_positions"] == 4
    clock[0] = 109
    assert observation.snapshot()["age_seconds"] == 6.5
    observation.begin(); clock[0] = 129
    assert observation.snapshot()["running_seconds"] == 20
    observation.finish({"ok": True, "diagnostic_errors": ["OKX_EXIT_PRICE_UNAVAILABLE"]})
    assert observation.snapshot()["state"] == "WARN"
    assert observation.snapshot()["last_success_at"] == first["last_success_at"]


def test_engine_measures_failed_real_position_check(monkeypatch):
    from crypto_engine import CryptoEngine
    engine = CryptoEngine.__new__(CryptoEngine)
    engine.hub = NS(broker=lambda _: None)
    report = engine.pruefe_positionen()
    assert report["ok"] is False
    health = engine.functional_health()["protection"]
    assert health["state"] == "ERROR" and health["cycles"] == 1
    assert health["last_success_at"] is None


def instrument_row(symbol="BTC-USD"):
    return {"instId": symbol, "instType": "SPOT", "baseCcy": "BTC", "quoteCcy": symbol.split("-")[-1],
            "state": "live", "tickSz": "0.1", "lotSz": "0.0001", "minSz": "0.001",
            "tradeQuoteCcyList": ["USD", "USDC"]}


def test_preflight_is_read_only_scoped_and_does_not_rename_existing_instrument():
    from broker.okx import OKXBroker
    client, calls = okx_client({"code": "0", "data": [instrument_row()]})
    broker = OKXBroker(client=client)
    assert broker.instrument_contract_status()["state"] == "UNKNOWN"
    assert calls == []
    client.instruments()
    state = broker.instrument_contract_status(["BTC-USD", "SUI-USD"])
    assert state["state"] == "REVIEW_REQUIRED" and state["account_migration_affected"] is None
    assert state["routes"][0]["trade_quote_ccy_list"] == ["USD", "USDC"]
    assert state["held_missing_from_catalog"] == ["SUI-USD"]
    assert state["automatic_instrument_rename"] is False
    assert set(client._instruments) == {"BTC-USD"} and len(calls) == 1
    client._instruments_at -= 1000
    assert broker.instrument_contract_status()["state"] == "UNKNOWN"


def test_public_catalog_does_not_confirm_private_migration_contract():
    from broker.okx import OKXBroker
    client, _ = okx_client({"code": "0", "data": [instrument_row()]})
    client._key = ""
    client.instruments()
    assert OKXBroker(client=client).instrument_contract_status()["state"] == "UNKNOWN"


def test_older_instrument_response_cannot_overwrite_newer_catalog():
    from broker.base import BrokerFehler
    client, _ = okx_client({})
    started = threading.Event(); release = threading.Event(); errors = []
    def fetch(*a, **kw):
        if threading.current_thread().name == "old-catalog":
            started.set(); assert release.wait(3)
            return [instrument_row("BTC-USD")]
        return [instrument_row("BTC-USDC")]
    client.request = fetch
    def run():
        try: client.instruments(force=True)
        except Exception as exc: errors.append(exc)
    thread = threading.Thread(target=run, name="old-catalog")
    thread.start(); assert started.wait(3)
    client.instruments(force=True); release.set(); thread.join(3)
    assert not thread.is_alive() and len(errors) == 1 and isinstance(errors[0], BrokerFehler)
    assert "SUPERSEDED" in str(errors[0]) and set(client._instruments) == {"BTC-USDC"}


def test_failed_refresh_invalidates_cached_private_capabilities():
    from broker.okx import OKXBroker
    client, _ = okx_client({"code": "0", "data": [instrument_row()]})
    client.instruments()
    def fail(*a, **kw): raise TimeoutError()
    client.request = fail
    with pytest.raises(TimeoutError): client.instruments(force=True)
    assert OKXBroker(client=client).instrument_contract_status()["state"] == "UNKNOWN"
    assert client._instruments_quality["complete"] is False


@pytest.mark.parametrize("failure_point", ["position", "registry", "protection_projection", "ledger"])
def test_native_fill_survives_eio_and_protection_is_still_attempted(failure_point, monkeypatch, tmp_path):
    # Use the existing real SQLite/registry/position fixture, not a fake ledger.
    from test_v975_execution_and_repair import engine as engine_fixture, intent, result, persist
    from order_ownership import OrderOwnershipRegistry
    import crypto_engine as ce
    import trade_ledger
    engine = engine_fixture.__wrapped__(monkeypatch, tmp_path)
    readiness = []
    engine.bereitschaft = NS(melde=lambda *args: readiness.append(args))
    engine._order_registry().register_pending("SUI", intent(), "crypto")
    protected = []
    def protect(*args, **kwargs):
        protected.append((args, kwargs))
        return {"checked": True, "protection_confirmed": True, "algo_id": "native-stop", "algo_client_id": "Pbuy"}
    engine.broker.reconcile_position_protection = protect
    original = ce.atomic_write_json
    position_writes = [0]
    def write(path, data, **kw):
        if str(path).endswith("crypto_positions.json"):
            position_writes[0] += 1
            if failure_point == "position" or (failure_point == "protection_projection" and position_writes[0] > 1):
                raise OSError(errno.EIO, "injected sync failure")
        return original(path, data, **kw)
    monkeypatch.setattr(ce, "atomic_write_json", write)
    if failure_point == "registry":
        monkeypatch.setattr(OrderOwnershipRegistry, "register_orders", lambda *a, **kw: (_ for _ in ()).throw(OSError(errno.EIO, "registry sync")))
    if failure_point == "ledger":
        monkeypatch.setattr(trade_ledger, "trade_open", lambda **kw: (_ for _ in ()).throw(OSError(errno.EIO, "ledger storage")))
    with pytest.raises(OSError) as exc: persist(engine)
    assert exc.value.errno == errno.EIO
    assert len(protected) == 1 and protected[0][1]["protection_client_id"] == "Pbuy"
    assert protected[0][0][1] == pytest.approx(result().filled_quantity)
    assert engine._critical_persistence_fault is True
    assert readiness[-1][0:2] == ("buchung_vollstaendig", False)
    rows = trade_ledger.entry_lineage("okx", "SUI-USDC", "100", "A", paper=True)
    if failure_point == "ledger":
        assert rows == []
        assert engine.buch.hole("SUI").order_id == "100"
        assert engine.buch.hole("SUI").fill_ids == result().fill_ids
    else:
        assert len(rows) == 1 and rows[0]["entry_order_id"] == "100"
    assert engine._order_registry().nicht_terminale()  # Restart can replay exact old intent.


def test_etoro_failed_snapshot_is_not_published_as_complete_empty_account(monkeypatch):
    from broker.etoro import EtoroBroker
    from broker.base import BrokerFehler
    broker = EtoroBroker(api_key="k", user_key="u", paper=True)
    broker._identity_loaded = True
    broker._account_cid = "cid"
    broker._account_fingerprint = "account-A"
    monkeypatch.setattr(broker, "_require_bound_identity", lambda *a: None)
    broker._pnl = lambda **kw: {"clientPortfolio": {"positions": [], "mirrors": []},
                               "_snapshot_id": "etoro:demo:1", "_snapshot_at": "2026-09-13T10:00:00+00:00"}
    first = broker.position_snapshot()
    assert first["complete"] and first["open_ids"] == set()
    broker._pnl = lambda **kw: {"clientPortfolio": {}}
    with pytest.raises(BrokerFehler): broker.position_snapshot(force=True)
    quality = broker.connection_components()["position_snapshot"]
    assert quality["state"] == "ERROR" and quality["complete"] is False
    assert quality["last_success_at"] == "2026-09-13T10:00:00+00:00"
