"""Regressionen fuer OKX-Zeit, Worker-Lebenszyklus und fail-closed WebUI."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest


class _Response:
    def __init__(self, status: int, payload: dict):
        self.status_code = status
        self._payload = payload

    def json(self):
        return self._payload


def _client_with_response(status: int, payload: dict):
    from broker.okx import OKXClient
    client = OKXClient("key", "secret", "phrase", demo=True)
    client.session.request = lambda *_a, **_k: _Response(status, payload)
    return client


def test_http_401_timestamp_code_is_transient_not_bad_credentials():
    from broker.base import Zeitabweichung
    client = _client_with_response(401, {
        "code": "50102", "msg": "Timestamp request expired", "data": []})
    with pytest.raises(Zeitabweichung, match="Zeitfenster"):
        client.request("GET", "/account/config", private=True)


def test_real_http_401_remains_permanent_authentication_error():
    from broker.base import AuthentifizierungsFehler
    client = _client_with_response(401, {
        "code": "50111", "msg": "Invalid OK-ACCESS-KEY", "data": []})
    with pytest.raises(AuthentifizierungsFehler):
        client.request("GET", "/account/config", private=True)


def test_server_time_offset_is_measured_at_request_midpoint(monkeypatch):
    import broker.okx as okx
    client = okx.OKXClient()
    client.request = lambda *_a, **_k: [{"ts": "120000"}]
    ticks = iter((100.0, 102.0))
    monkeypatch.setattr(okx.time, "time", lambda: next(ticks))
    assert client.server_time_ms() == 120000
    assert client.clock_offset_seconds == pytest.approx(19.0)


@pytest.mark.parametrize("demo", [True, False])
def test_large_clock_drift_blocks_demo_and_live_but_remains_retryable(demo):
    from broker.base import Zeitabweichung
    from broker.okx import OKXBroker

    class Client:
        hat_zugangsdaten = True
        clock_offset_seconds = 195.3
        def server_time_ms(self): return 1_000

    broker = OKXBroker(client=Client(), demo=demo)
    with pytest.raises(Zeitabweichung, match="automatisch erneut"):
        broker.connect()


def test_websocket_login_uses_rest_measured_time_offset(monkeypatch):
    import broker.okx_stream as stream
    sent = []
    app = SimpleNamespace(send=lambda raw: sent.append(json.loads(raw)))
    monkeypatch.setattr(stream.time, "time", lambda: 1000.9)
    ws = stream.OKXPrivateStream("key", "secret", "phrase",
                                 time_offset_seconds=19.2)
    ws._on_open(app)
    assert sent[0]["op"] == "login"
    assert sent[0]["args"][0]["timestamp"] == "1020"
    assert sent[0]["args"][0]["sign"] == ws._signature("1020")


def test_missing_application_pong_forces_reconnect(monkeypatch):
    import broker.okx_stream as stream

    class Stop:
        def is_set(self): return False
        def wait(self, _seconds): return False

    app = SimpleNamespace(closed=False, close=lambda: setattr(app, "closed", True))
    ws = stream.OKXPrivateStream("key", "secret", "phrase")
    ws._stop = Stop()
    ws._app = app
    ws._warte_auf_pong_seit = 10.0
    monkeypatch.setattr(stream.time, "monotonic", lambda: 25.0)
    ws._keepalive(app)
    assert app.closed
    assert "Kein pong" in ws.status().last_error


def test_websocket_notice_is_not_ignored():
    from broker.okx_stream import DEGRADED, OKXPrivateStream
    app = SimpleNamespace(closed=False, close=lambda: setattr(app, "closed", True))
    ws = OKXPrivateStream("key", "secret", "phrase")
    ws._on_message(app, json.dumps({
        "event": "notice", "code": "64008", "msg": "service upgrade"}))
    assert app.closed and ws.status().zustand == DEGRADED


def test_pong_does_not_make_account_balances_fresh(monkeypatch):
    import broker.okx_stream as stream
    app = SimpleNamespace()
    ws = stream.OKXPrivateStream("key", "secret", "phrase")
    ws._zustand = stream.SUBSCRIBED
    ws._balances = {"SOL": {"cash": 14.19, "gesamt": 14.19}}
    ws._last_account_at = 0.0
    ws._on_message(app, "pong")
    assert ws.fresh()
    assert ws.balances() is None


def test_account_snapshot_replaces_omitted_stale_currency():
    from broker.okx_stream import OKXPrivateStream, SUBSCRIBED
    ws = OKXPrivateStream("key", "secret", "phrase")
    ws._zustand = SUBSCRIBED
    ws._balances = {
        "BTC": {"cash": 1, "gesamt": 1, "frozen": 0},
        "SOL": {"cash": 14, "gesamt": 14, "frozen": 0},
    }
    ws._update_balances([{"details": [{
        "ccy": "SOL", "availBal": "12", "cashBal": "12", "frozenBal": "0"
    }]}], replace=True)
    assert set(ws.balances()) == {"SOL"}


def test_okx_spot_assets_are_account_assets_not_open_trades():
    import exposure_klassifizierung as exposure
    result = exposure.klassifiziere({
        "BTC": {"gesamt": 0.1, "cash": 0.1},
        "ETH": {"gesamt": 1, "cash": 1},
        "SOL": {"gesamt": 14.19, "cash": 14.19},
        "XRP": {"gesamt": 50000, "cash": 50000},
    }, positionsbuch=[{
        "symbol": "SOL", "menge": 14.19, "herkunft": "BOT",
        "verwaltung": "BEOBACHTEN",
    }], preise={"BTC": 60000, "ETH": 2000, "SOL": 90, "XRP": 1})
    assert {x["klasse"] for x in result["bestaende"]} == {exposure.ACCOUNT_ASSET}
    assert not result["einstiege_gesperrt"]


def test_crypto_worker_does_not_die_after_initial_okx_failure(monkeypatch):
    import config
    import crypto_engine
    import nexus_start

    calls = []
    class Engine:
        def __init__(self, **_kwargs): pass
        def zyklus(self):
            calls.append("cycle")
            return {"hinweis": "OKX nicht verbunden", "arbeiten": []}
    class Hub:
        def verbinde(self, _name): return False
        def broker(self, _name): return None
        def zustaende(self):
            return {"okx": {"letzter_fehler": "Zeitabweichung"}}

    monkeypatch.setattr(config, "OKX_ENABLED", True)
    monkeypatch.setattr(crypto_engine, "CryptoEngine", Engine)
    nexus_start._beenden.set()
    try:
        assert nexus_start.starte_krypto(
            Hub(), object(), object(), object(), None, einmal=False)
    finally:
        nexus_start._beenden.clear()
    assert calls == ["cycle"]


def test_runtime_requires_worker_and_broker_truth(monkeypatch, tmp_path):
    import webui.state as state
    monkeypatch.setattr(state, "ROOT", tmp_path)
    (tmp_path / "runtime_status_okx.json").write_text(json.dumps({
        "running": True, "online": False,
        "last_heartbeat": datetime.now(timezone.utc).isoformat(),
    }), encoding="utf-8")
    data = state._runtime("runtime_status_okx.json")
    assert data["worker_alive"] is True
    assert data["broker_online"] is False
    assert data["online"] is False


def test_runtime_keeps_stock_broker_connected_field(monkeypatch, tmp_path):
    import webui.state as state
    monkeypatch.setattr(state, "ROOT", tmp_path)
    (tmp_path / "runtime_status.json").write_text(json.dumps({
        "running": True, "broker_connected": True,
        "last_heartbeat": datetime.now(timezone.utc).isoformat(),
    }), encoding="utf-8")
    data = state._runtime("runtime_status.json")
    assert data["worker_alive"] is True
    assert data["broker_online"] is True
    assert data["online"] is True


def test_okx_detail_never_releases_buys_from_cached_offline_status(monkeypatch, tmp_path):
    import okx_status
    import webui.state as state
    monkeypatch.setattr(state, "ROOT", tmp_path)
    (tmp_path / "runtime_status_okx.json").write_text(json.dumps({
        "running": True, "online": False,
        "last_heartbeat": datetime.now(timezone.utc).isoformat(),
    }), encoding="utf-8")
    monkeypatch.setattr(okx_status, "lies", lambda: {
        "modus": "DEMO", "online": True,
        "handelsbereitschaft": {"trading_ready": True, "kaeufe_erlaubt": True},
    })
    monkeypatch.setattr(okx_status, "alter_sekunden", lambda _d: 1.0)
    detail = state._okx_detail()
    assert detail["online"] is False
    assert detail["trading_ready"] is False
    assert detail["kaeufe_erlaubt"] is False
    assert detail["werte_veraltet"] is True


def test_release_version_is_9012():
    import config
    # v9.1: Die Versionspruefung haengt nicht mehr an einer
    # fest eingetippten Zahl -- sie prueft die Uebereinstimmung
    # zwischen config und VERSION.txt. Genau das soll sie leisten.
    from pathlib import Path as _P
    datei = (_P(__file__).resolve().parent.parent / "VERSION.txt")
    assert config.VERSION_NEXUS == datei.read_text(encoding="utf-8").strip()
