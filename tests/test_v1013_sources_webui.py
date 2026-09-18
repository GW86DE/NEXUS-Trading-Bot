"""Source monitoring is passive; evidence states cannot grant trading authority."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess

import pytest
import NEXUS_10_Diagnose as diagnosis


def cache(key, payload, saved=100):
    return {"key": key, "saved": saved, "expires": 200, "payload": json.dumps(payload)}


def test_pipeline_distinguishes_aggregate_rows_and_unknown_posts():
    rows = [cache("source_status:apewisdom", {"ok": True, "last_attempt": 100, "last_success": 100,
                                             "error_at": 50, "detail": "old failure"}),
            cache("social:apewisdom", [{"symbol": "PEP", "mentions": 5}]),
            cache("coverage", {"symbols": 1}),
            cache("top5", [{"symbol": "PEP", "packet": {"sources": [{"provider": "apewisdom", "id": "hash-1"}]}}])]
    result = diagnosis.source_pipeline_report({}, rows, {}, now=110)
    ape = next(r for r in result["sources"] if r["provider"] == "apewisdom")
    assert ape["state"] == "OK" and ape["error_scope"] == "HISTORICAL"
    assert ape["aggregate_rows_in_cache"] == 1
    assert ape["posts_received"] is None and ape["authors_observed"] is None
    assert ape["cards_with_source"] == 1 and ape["source_ids"] == ["hash-1"]
    assert result["reddit"]["platform_count"] == 1
    assert result["reddit"]["direct_api_observed"] is False


def test_missing_cache_is_unknown_not_zero_and_stale_error_not_current():
    result = diagnosis.source_pipeline_report({"FMP": {"ok": False, "time": diagnosis.utc(10),
        "error_at": diagnosis.utc(10)}}, [], {}, now=5000)
    row = result["sources"][0]
    assert row["state"] == "STALE" and row["error_scope"] == "UNVERIFIED"
    assert row["cards_with_source"] is None and row["returned_items_last_request"] is None
    assert result["cards_observed"] is None and result["x"]["state"] == "NOT_OBSERVED"


def test_diagnosis_captures_x_metrics_hashes_not_post_content_or_secrets(tmp_path):
    secret = "specific-long-private-X-token"
    payload = {"counts": {"posts_received": 3, "processed_posts": 2}, "bearer_token": secret,
        "events": [{"event_id": "hash123", "posts": 2, "text": "raw-post-content", "author_id": "private-author"}],
        "posts": [{"text": "raw-post-content"}], "requests": [{"body_hash": "hash456"}]}
    (tmp_path / "market_intelligence_status.json").write_text(json.dumps(payload))
    bundle = diagnosis.Bundle(tmp_path / "out")
    result = diagnosis.capture_states(tmp_path, bundle, "start")
    data = result["market_intelligence_status.json"]["data"]
    encoded = json.dumps(data)
    assert "raw-post-content" not in encoded and "private-author" not in encoded and secret not in encoded
    assert data["counts"]["posts_received"] == 3 and data["events"][0]["posts"] == 2
    assert data["requests"][0]["body_hash"] == "hash456"
    assert "market_intelligence.sqlite" not in diagnosis.DATABASES
    assert "market_intelligence_status.json" in diagnosis.SAMPLE_FILES


def test_source_snapshot_reads_without_creating_pulsar_or_x_database(tmp_path, monkeypatch):
    from market_intelligence import store
    from webui.market_sources import snapshot
    monkeypatch.setattr(store, "ROOT", tmp_path)
    before = set(tmp_path.iterdir())
    result = snapshot()
    assert result["passive"] is True and result["x"]["state"] == "DISABLED"
    assert set(tmp_path.iterdir()) == before
    assert not (tmp_path / "pulsar_research.sqlite").exists()
    assert not (tmp_path / "market_intelligence.sqlite").exists()


def test_period_proof_export_is_named_redacted_and_hashes_original_bytes(tmp_path):
    original = json.dumps({"lifetime_unknown_pnl_trades": 7, "api_key": "period-secret-token"}).encode()
    digest = hashlib.sha256(original).hexdigest()
    archive = "risk_state_etoro.json.legacy-" + digest + ".bak"
    evidence = "etoro_risk_period_" + digest + ".json"
    (tmp_path / archive).write_bytes(original)
    (tmp_path / evidence).write_text(json.dumps({"observed_scope": {"environment": "DEMO"}}))
    (tmp_path / "unreferenced-private.bak").write_text("must-not-export")
    bundle = diagnosis.Bundle(tmp_path / "out")
    result = diagnosis.capture_risk_period_proofs(tmp_path, bundle, "start", {
        "basis_review_receipt": {"archive": archive, "evidence": evidence, "archive_sha256": digest}})
    assert result["status"] == "OK"
    row = result["data"]["references"][0]
    assert row["sha256_raw"] == digest and row["archive_hash_matches"] is True
    exported = (bundle.work / row["export_file"]).read_text()
    assert "period-secret-token" not in exported and '"lifetime_unknown_pnl_trades": 7' in exported
    assert (tmp_path / archive).read_bytes() == original
    assert not list(bundle.work.rglob("*unreferenced*"))


def test_period_proof_export_rejects_paths_and_records_missing_large_and_symlink(tmp_path):
    digest = "a" * 64
    archive = "risk_state_etoro.json.legacy-" + digest + ".bak"
    evidence = "etoro_risk_period_" + digest + ".json"
    def capture(suffix, name=archive, limit=128):
        return diagnosis.capture_risk_period_proofs(tmp_path, diagnosis.Bundle(tmp_path / suffix), "start", {
            "basis_review_receipt": {"archive": name, "evidence": evidence, "archive_sha256": digest}}, limit=limit)
    result = capture("one", "../"+archive)
    assert result["data"]["references"][0]["status"] == "INVALID_REFERENCE"
    assert result["data"]["references"][1]["present"] is False
    (tmp_path / archive).write_bytes(b" " * 129)
    row = capture("two")["data"]["references"][0]
    assert row["present"] is True and row["status"] == "OMITTED_TOO_LARGE"
    assert row["sha256_raw"] is None and not row["exported"]
    (tmp_path / archive).unlink()
    (tmp_path / archive).symlink_to(tmp_path / evidence)
    (tmp_path / evidence).write_text("{}")
    row = capture("three")["data"]["references"][0]
    assert row["status"] == "ERROR" and not row["exported"]


def test_pulsar_x_card_distinguishes_unconfirmed_and_omitted_context():
    root = Path(__file__).resolve().parents[1]
    code = """const fs=require('fs'),vm=require('vm');
    const c={document:{querySelector:()=>null},nexusPoll:()=>{},
      esc:v=>String(v??'').replace(/</g,'&lt;').replace(/>/g,'&gt;'),
      knownNumber:v=>typeof v==='number'&&Number.isFinite(v)};
    vm.createContext(c);vm.runInContext(fs.readFileSync(process.argv[1],'utf8'),c);
    const html=vm.runInContext("pulsarXContextView({symbol:'PEP',attention:{},argument_clusters:[{title:'<script>bad</script>'}],ai_context_status:'OMITTED_INPUT_LIMIT'})",c);
    if(html.includes('<script>')||!html.includes('&lt;script&gt;')||!html.includes('unbekannt')||!html.includes('nicht an die GPT')||!html.includes('Unbestätigt'))process.exit(1);
    if(vm.runInContext('pulsarXContextView({})',c)!=='')process.exit(2);
    """
    result = subprocess.run(["node", "-e", code, str(root / "webui/static/pulsar.js")], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_source_snapshot_keeps_existing_sqlite_bytes(tmp_path, monkeypatch):
    import market_intelligence
    from webui.market_sources import snapshot
    path = tmp_path / "pulsar_research.sqlite"
    with sqlite3.connect(path) as con:
        con.execute("CREATE TABLE cache (key TEXT,saved REAL,expires REAL,payload TEXT)")
        con.execute("INSERT INTO cache VALUES (?,?,?,?)", ("top5", 100, 200, "[]"))
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    monkeypatch.setattr(market_intelligence, "public_status", lambda: {"state": "DISABLED"})
    out = snapshot()
    assert out["cards_observed"] == 0 and out["cache_quality"] == "BOUNDED_CACHE_SNAPSHOT"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_x_settings_require_auth_csrf_and_bound_field_set(monkeypatch):
    from fastapi.testclient import TestClient
    from webui.app import app
    import market_intelligence
    from webui import market_sources
    from test_v100_webui_backend import configured_auth
    auth, _ = configured_auth(monkeypatch)
    token, csrf = auth.issue_session("testuser")
    calls = []
    monkeypatch.setattr(market_sources, "snapshot", lambda: {"x": {"state": "DISABLED"}})
    monkeypatch.setattr(market_intelligence, "save_settings", lambda changes, bearer_token=None: calls.append((changes, bearer_token)) or {"enabled": False})
    client = TestClient(app)
    assert client.get("/sources", follow_redirects=False).status_code == 303
    assert client.get("/api/market-intelligence").status_code == 401
    assert client.post("/api/market-intelligence/settings", json={}).status_code == 401
    client.cookies.set(auth.COOKIE, token)
    assert client.get("/sources").status_code == 200
    response = client.get("/api/market-intelligence")
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    assert client.post("/api/market-intelligence/settings", json={}).status_code == 403
    headers = {"X-CSRF-Token": csrf}
    for payload in ([], {"url": "https://other.example"}, {"bearer_token": []}):
        assert client.post("/api/market-intelligence/settings", json=payload, headers=headers).status_code == 400
    assert not calls
    response = client.post("/api/market-intelligence/settings", headers=headers,
        json={"enabled": False, "monthly_budget_eur": 15, "bearer_token": "private-token"})
    assert response.status_code == 200 and "private-token" not in response.text
    assert calls == [({"enabled": False, "monthly_budget_eur": 15}, "private-token")]


def test_risk_gate_wins_over_green_readiness_without_affecting_okx():
    from test_v100_webui_backend import dashboard_fixture
    from webui.diagnostics import operations_snapshot
    now, data = dashboard_fixture()
    data["etoro"]["risk_manager_buy_gate"] = {"blocked": True, "reason": "7 Ergebnisbelege offen", "pending_results": 7,
        "assessed_at": now.isoformat()}
    result = operations_snapshot(data, now=now)["brokers"]
    assert result["etoro"]["buy"]["state"] == "BLOCKED"
    assert result["etoro"]["buy"]["detail"] == "7 Ergebnisbelege offen"
    assert result["etoro"]["protection"]["state"] == "OK"
    assert result["okx"]["buy"]["state"] == "ALLOWED"


def test_runtime_projection_does_not_keep_handelsbereit_when_risk_blocked(monkeypatch):
    from webui import state
    monkeypatch.setattr(state, "_json", lambda *_: {"running": True, "online": True,
        "last_heartbeat": datetime.now(timezone.utc).isoformat(),
        "stock_trading_ready": {"kaeufe_erlaubt": True, "trading_ready": True, "grund": "handelsbereit"},
        "risk_manager_buy_gate": {"blocked": True, "reason": "Kostenbelege offen"}})
    result = state._runtime("runtime_status.json")
    assert result["stock_trading_ready"]["kaeufe_erlaubt"] is False
    assert result["stock_trading_ready"]["trading_ready"] is False
    assert result["stock_trading_ready"]["grund"] == "Kostenbelege offen"


def test_rendered_pages_always_have_diagnose_and_sources_navigation():
    from webui.app import HERE, _page
    for template in HERE.joinpath("templates").glob("*.html"):
        if template.name == "login.html":
            continue
        body = _page(template.name, {"u": "user", "csrf": "csrf"}).body.decode()
        nav = body.split('</nav>')[0]
        assert 'href="/diagnosis"' in nav and 'href="/sources"' in nav


def test_source_renderers_escape_input_and_show_unknown_counts():
    root = Path(__file__).resolve().parents[1]
    code = """const fs=require('fs'),vm=require('vm');
    const c={document:{querySelector:()=>null},nexusPoll:()=>{},
      esc:v=>String(v??'').replace(/</g,'&lt;').replace(/>/g,'&gt;'),
      knownNumber:v=>typeof v==='number'&&Number.isFinite(v),
      observationTime:v=>v??'nicht belegt',badge:(v,k)=>String(v)};
    vm.createContext(c);vm.runInContext(fs.readFileSync(process.argv[1],'utf8'),c);
    const html=vm.runInContext("renderXEvents([{title:'<script>bad</script>',affected_symbols:['PEP'],status:'HIGH_PRIORITY_UNCONFIRMED'}])+renderXStatus({})",c);
    if(html.includes('<script>')||!html.includes('&lt;script&gt;')||!html.includes('unbekannt')||!html.includes('PEP'))process.exit(1);
    """
    result = subprocess.run(["node", "-e", code, str(root / "webui/static/sources.js")], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
