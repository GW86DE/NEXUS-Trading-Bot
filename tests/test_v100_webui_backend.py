"""Behavioral diagnosis/auth tests; all broker and provider IO stays offline."""
import copy
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import importlib
import json
import sqlite3
import time

import pytest


def configured_auth(monkeypatch):
    from webui import auth
    credentials = {}
    monkeypatch.setattr(auth, "load_credentials", lambda *_: dict(credentials))
    monkeypatch.setattr(auth, "save_credentials", lambda _, value: (credentials.clear(), credentials.update(value)))
    auth.configure("testuser", "local-offline-password")
    return auth, credentials


def test_password_reset_revokes_all_previous_sessions(monkeypatch):
    auth, credentials = configured_auth(monkeypatch)
    first, csrf = auth.issue_session("testuser")
    assert auth.decode_session(first)["csrf"] == csrf
    old_secret = credentials["session_secret"]
    auth.configure("testuser", "new-local-offline-password")
    assert credentials["session_secret"] != old_secret
    assert auth.decode_session(first) is None
    assert auth.authenticate("testuser", "new-local-offline-password")
    assert not auth.authenticate("testuser", "local-offline-password")
    assert auth.decode_session(auth.issue_session("testuser")[0])["u"] == "testuser"


def test_unconfigured_ui_rejects_cookie_signed_with_empty_key(monkeypatch):
    from webui import auth
    monkeypatch.setattr(auth, "load_credentials", lambda *_: {})
    payload = auth._b64(json.dumps({"u": "", "exp": int(time.time())+3600,
        "csrf": "made-up-csrf", "nonce": "made-up-nonce"}).encode())
    signature = auth._b64(hmac.new(b"", payload.encode(), hashlib.sha256).digest())
    assert auth.decode_session(payload+"."+signature) is None
    with pytest.raises(ValueError):
        auth.issue_session("")


@pytest.mark.parametrize("field", ["username", "password_hash", "session_secret"])
def test_missing_credential_field_invalidates_session(monkeypatch, field):
    auth, credentials = configured_auth(monkeypatch)
    token, _ = auth.issue_session("testuser")
    credentials.pop(field)
    assert auth.decode_session(token) is None


@pytest.mark.parametrize("value", [["invalid"], "invalid", 12])
def test_semantically_invalid_credentials_remain_unconfigured(monkeypatch, value):
    from webui import auth
    monkeypatch.setattr(auth, "load_credentials", lambda *_: value)
    assert auth.configured() is False
    assert auth.authenticate("testuser", "password") is False
    with pytest.raises(ValueError):
        auth.issue_session("testuser")


def dashboard_fixture():
    now = datetime(2026, 9, 13, 12, tzinfo=timezone.utc)
    stamp = now.isoformat()
    context = {"account": "test-account", "observed_environment": "DEMO"}
    runtime = {"last_heartbeat": stamp, "online": True, "worker_alive": True,
        "stock_trading_ready": {"kaeufe_erlaubt": True, "grund": "bereit"},
        "connection_components": {"ws_connected": False, "ws_authenticated": False,
            "observations": {"rest": {"private": {"state": "OK", "last_success_at": stamp}}}},
        "functional_health": {"protection": {"state": "OK", "last_completed_at": stamp,
            "checked_positions": 2}}}
    return now, {"bot": {"zustand": "aktiv"}, "broker_contexts": {"okx": copy.deepcopy(context), "etoro": context},
        "etoro": copy.deepcopy(runtime), "okx": copy.deepcopy(runtime),
        "okx_detail": {"kaeufe_erlaubt": True}, "etoro_reconciliation": {"active": []}}


def test_broker_offline_does_not_block_other_domain_and_ws_is_separate():
    from webui.diagnostics import operations_snapshot
    now, payload = dashboard_fixture()
    payload["etoro"].update(online=False, worker_alive=False)
    result = operations_snapshot(payload, now=now)["brokers"]
    assert result["etoro"]["buy"]["state"] == "BLOCKED"
    assert result["okx"]["buy"]["state"] == "ALLOWED"
    assert result["okx"]["rest"]["state"] == "OK"
    assert result["okx"]["websocket"]["state"] == "WARN"
    assert result["okx"]["sell"]["state"] == "UNKNOWN"  # no invented blanket sell permission


def test_stale_snapshot_does_not_keep_green_health_or_buy_permission():
    from webui.diagnostics import operations_snapshot
    now, payload = dashboard_fixture()
    result = operations_snapshot(payload, now=now+timedelta(minutes=4))["brokers"]["okx"]
    assert result["buy"]["state"] == "BLOCKED"
    assert result["rest"]["state"] == result["websocket"]["state"] == result["protection"]["state"] == "STALE"


def test_future_heartbeat_and_invalid_runtime_do_not_report_online(monkeypatch):
    from webui import state
    future = (datetime.now(timezone.utc)+timedelta(days=1)).isoformat()
    monkeypatch.setattr(state, "_json", lambda *_: {"running": True, "online": True, "last_heartbeat": future})
    assert state._runtime("test")["worker_alive"] is False
    assert state._runtime("test")["online"] is False
    monkeypatch.setattr(state, "_json", lambda *_: ["invalid"])
    assert state._runtime("test")["state_error"] == "RUNTIME_FORMAT_INVALID"


@pytest.mark.parametrize("context", [{}, {"account": "A", "observed_environment": "UNKNOWN"},
    {"account": "A", "observed_environment": "DEMO", "mode_mismatch": True}])
def test_ambiguous_account_environment_blocks_only_its_buy_projection(context):
    from webui.diagnostics import operations_snapshot
    now, payload = dashboard_fixture()
    payload["broker_contexts"]["etoro"] = context
    result = operations_snapshot(payload, now=now)["brokers"]
    assert result["etoro"]["buy"]["state"] == "BLOCKED"
    assert result["okx"]["buy"]["state"] == "ALLOWED"


def test_empty_reconciliation_and_missing_channels_are_unknown():
    from webui.diagnostics import operations_snapshot
    now, payload = dashboard_fixture()
    payload["etoro"]["connection_components"] = {}
    result = operations_snapshot(payload, now=now)["brokers"]["etoro"]
    assert result["reconciliation"]["state"] == result["rest"]["state"] == "UNKNOWN"


@pytest.mark.parametrize("field,value", [("account_fingerprint", "other-account"), ("environment", "LIVE")])
def test_mixed_runtime_account_snapshot_never_grants_buys(field, value):
    from webui.diagnostics import operations_snapshot
    now, payload = dashboard_fixture()
    payload["etoro"]["connection_components"][field] = value
    result = operations_snapshot(payload, now=now)["brokers"]
    assert result["etoro"]["buy"]["state"] == "BLOCKED"
    assert result["etoro"]["rest"]["state"] == "UNKNOWN"
    assert result["okx"]["buy"]["state"] == "ALLOWED"


def test_okx_detail_from_another_environment_is_not_used_for_buy_readiness():
    from webui.diagnostics import operations_snapshot
    now, payload = dashboard_fixture()
    payload["okx_detail"]["modus"] = "LIVE"
    result = operations_snapshot(payload, now=now)["brokers"]
    assert result["okx"]["buy"]["state"] == "BLOCKED"
    assert result["okx"]["rest"]["state"] == "UNKNOWN"
    assert result["etoro"]["buy"]["state"] == "ALLOWED"


def test_rest_business_rejection_is_not_rendered_as_connection_loss():
    from webui.diagnostics import operations_snapshot
    now, payload = dashboard_fixture()
    payload["etoro"]["connection_components"]["observations"]["rest"]["private"].update(
        state="ERROR", transport_state="OK", http_status=200, last_error_at=now.isoformat())
    result = operations_snapshot(payload, now=now)["brokers"]["etoro"]["rest"]
    assert result["transport_state"] == "OK" and result["http_status"] == 200
    assert "kein Beleg für einen Verbindungsabbruch" in result["detail"]


def test_history_is_read_only_and_missing_schema_is_not_healthy_empty(tmp_path, monkeypatch):
    import decision_analytics
    from webui.diagnostics import execution_history
    path = tmp_path/"no-schema.sqlite"
    monkeypatch.setattr(decision_analytics, "db_pfad", lambda: path)
    assert execution_history()["complete"] is False
    assert not path.exists()
    with sqlite3.connect(path) as con:
        con.execute("CREATE TABLE old_state(id INTEGER)")
    before = path.read_bytes()
    result = execution_history()
    assert result["total"] is None and result["error"] == "EXECUTION_HISTORY_UNAVAILABLE"
    assert path.read_bytes() == before
    path.write_bytes(b"not sqlite")
    assert execution_history()["complete"] is False


def test_cold_diagnosis_import_does_not_create_database(tmp_path):
    import os
    import subprocess
    import sys
    env = dict(os.environ, TRADINGBOT_TEST_STATE_DIR=str(tmp_path))
    code = """import sys,json
from pathlib import Path
from webui.diagnostics import execution_history
assert 'decision_analytics' not in sys.modules
print(json.dumps(execution_history()))
assert 'decision_analytics' not in sys.modules
"""
    result = subprocess.run([sys.executable, "-c", code], env=env, text=True,
        capture_output=True, check=True, timeout=15)
    assert json.loads(result.stdout)["complete"] is False
    assert not list(tmp_path.glob("*.sqlite*"))


def test_ai_audit_survives_version_copy_byte_for_byte(tmp_path):
    import settings_migration as migration
    source, target = tmp_path/"old", tmp_path/"new"
    source.mkdir(); target.mkdir()
    payload = '{"phase":"TIMED_OUT","local_request_id":"offline-fixture"}\n'
    name = "ai_usage_audit.jsonl"
    (source/name).write_text(payload, encoding="utf-8")
    assert name in migration.PERSISTENT_FILES
    migration._kopiere(name, source/name, target/name)
    assert (source/name).read_bytes() == (target/name).read_bytes()


def test_order_history_pagination_and_scoped_detail_do_not_expose_raw_payloads():
    from webui.diagnostics import execution_history, execution_detail
    # Different positions in eToro; OKX one active instrument permits no second
    # live exit, so independent instruments keep lifecycle invariants real.
    import execution_lifecycle as life
    for i in range(3):
        life.reserve(broker="etoro", account="A", environment="DEMO", instrument="1234",
            side="SELL", position_id=str(i), client_id=f"client-{i}", quantity=1,
            request={"secret": "private-request-body"})
    first = execution_history(broker="etoro", limit=2)
    second = execution_history(broker="etoro", limit=2, page=2)
    assert first["total"] == 3 and first["pages"] == 2 and len(second["orders"]) == 1
    assert len({r["client_id"] for r in first["orders"]+second["orders"]}) == 3
    detail = execution_detail(broker="etoro", account="A", environment="DEMO", client_id="client-0")
    assert detail["order"]["position_id"] == "0" and detail["events"][0]["kind"] == "SUBMITTING"
    assert "private-request-body" not in json.dumps(detail)
    assert detail["truncated"] == {"fills": False, "events": False}
    with pytest.raises(LookupError):
        execution_detail(broker="etoro", account="B", environment="DEMO", client_id="client-0")
    with pytest.raises(LookupError):
        execution_detail(broker="etoro", account="A", environment="LIVE", client_id="client-0")


def test_diagnostic_routes_require_real_authentication_and_have_no_write_method(monkeypatch):
    from fastapi.testclient import TestClient
    auth, _ = configured_auth(monkeypatch)
    module = importlib.import_module("webui.app")
    with TestClient(module.app) as client:
        for route in ("/api/executions/history", "/api/executions/detail", "/api/ai-diagnostics"):
            assert client.get(route).status_code == 401
        token, _ = auth.issue_session("testuser")
        client.cookies.set(auth.COOKIE, token)
        assert client.get("/api/executions/history").status_code == 200
        assert client.get("/api/executions/detail?broker=okx").status_code == 400
        assert client.get("/api/ai-diagnostics").status_code == 200
        assert client.post("/api/executions/history").status_code == 405
        assert client.post("/api/control/activate", json={"confirm": "AKTIVIEREN"}).status_code == 403


def test_decision_search_is_literal_and_strategy_reason_are_separate(tmp_path, monkeypatch):
    from webui import state
    import decision_analytics as da
    monkeypatch.setattr(state, "ROOT", tmp_path)
    monkeypatch.setattr(da, "DB_PATH", tmp_path/state.config.DECISION_DB_FILE)
    da.init_db()
    with da._connect() as con:
        for symbol, reference, strategy, reason in [("SUI", "order_1", "FT", "RISIKO"), ("AAPL", "orderX1", "NEXUS", "SIGNAL")]:
            con.execute("INSERT INTO decisions(created_at_utc,local_day,symbol,status,payload_json,broker_reference_id,strategy_mode,reason) VALUES(?,?,?,?,?,?,?,?)",
                ("2026-09-13T12:00:00+00:00", "2026-09-13", symbol, "BLOCKED", "{}", reference, strategy, reason))
    assert [x["symbol"] for x in state.decision_log(search="order_1")["rows"]] == ["SUI"]
    assert state.decision_log(search="%' OR 1=1 --")["total"] == 0
    assert state.decision_log(strategy="FT", reason="SIGNAL")["total"] == 0
    assert state.decision_log(strategy="FT", reason="RISIKO")["total"] == 1
