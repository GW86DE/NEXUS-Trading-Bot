"""Read-only UI behavior for the open diagnosis findings and PEP repair."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
from types import SimpleNamespace

import pytest

from webui.display_evidence import (startup_repetition, fmp_packet_receipts,
    saved_fmp_packet_usage, position_risk_display, closed_result_display)


def startup_row(**changes):
    row = {"id": 1, "broker": "okx", "symbol": "BTC", "status": "BLOCKED",
        "execution_status": "BLOCKED", "local_day": "2026-09-13", "strategy_mode": "FREQTRADE_SAMPLE",
        "strategy_version": "fixture", "reason": "Anlaufsperre: neue Kaeufe frei in 5 min 20 s (20:15)",
        "display_context": {"bound": True, "broker": "okx", "environment": "DEMO", "account": "fixture-A"}}
    row.update(changes)
    return row


def test_grouping_changes_countdown_only_and_preserves_all_other_identity():
    row = startup_row()
    payload = {"inst_id": "BTC-EUR", "strategy_parameters": {"timeframe": "5m"}}
    before = deepcopy(row)
    base = startup_repetition(row, payload)
    later = startup_repetition(startup_row(reason="Anlaufsperre: neue Kaeufe frei in 4 min 50 s (20:15)"), payload)
    assert base["key"] == later["key"] and row == before
    for field, value in (("symbol", "ETH"), ("strategy_mode", "NEXUS_STANDARD"),
            ("local_day", "2026-09-14"), ("blocked_by", "OTHER_GATE"),
            ("reason", "Anlaufsperre: neue Kaeufe frei in 4 min 50 s (20:16)"),
            ("reason", "Anlaufsperre: Risikobasis nicht bestätigt")):
        assert startup_repetition(startup_row(**{field: value}), payload)["key"] != base["key"]
    for changes in ({"inst_id": "BTC-USDC"}, {"strategy_parameters": {"timeframe": "15m"}}):
        assert startup_repetition(row, {**payload, **changes})["key"] != base["key"]
    for changes in ({"account": "fixture-B"}, {"environment": "LIVE"}, {"broker": "etoro"}):
        changed = startup_row(display_context={**row["display_context"], **changes})
        assert startup_repetition(changed, payload)["key"] != base["key"]
    assert startup_repetition(startup_row(orders=[{"id": "order"}]), payload) == {}
    assert startup_repetition(startup_row(execution_status="UNKNOWN_AFTER_SUBMIT"), payload) == {}


def test_historical_groups_explicitly_unknown_and_never_mix_known_accounts():
    known = startup_row()
    unknown = startup_row(display_context={"bound": False, "broker": "okx", "environment": "DEMO", "account": ""})
    payload = {"timeframe": "5m"}
    meta = startup_repetition(unknown, payload)
    assert meta["account_bound"] is False and meta["instrument_proven"] is False
    assert meta["key"] != startup_repetition(known, payload)["key"]
    assert startup_repetition(unknown, {}) == {}


def fmp_card():
    return {"symbol": "MU", "precheck": {"ok": True, "input_hash": "a"*64,
        "input_sources": {"MU": {"status": "INPUT_OF_VALIDATED_RESPONSE", "sources": [
            {"provider": "FMP", "source_id": "fmp-annual-1", "kind": "annual", "included": True,
             "included_rows": 5, "as_of": "2025-12-31"}]}}}}


def test_fmp_packet_evidence_does_not_need_usage_counters_or_invent_trade_impact():
    card = fmp_card()
    result = fmp_packet_receipts([card, deepcopy(card)])
    assert result["input_packets"] == 1 and result["source_count"] == 1
    assert result["trade_effect"] == "NOT_ESTABLISHED"
    for change in ("not_included", "failed", "missing_hash", "missing_validation"):
        bad = fmp_card()
        if change == "not_included": bad["precheck"]["input_sources"]["MU"]["sources"][0]["included"] = False
        if change == "failed": bad["precheck"]["ok"] = False
        if change == "missing_hash": bad["precheck"]["input_hash"] = ""
        if change == "missing_validation": bad["precheck"]["input_sources"]["MU"]["status"] = "PENDING"
        assert fmp_packet_receipts([bad])["state"] == "NOT_EVIDENCED"


def test_fmp_readonly_sqlite_and_large_or_corrupt_evidence_remains_unknown(tmp_path):
    path = tmp_path / "research.sqlite"
    assert saved_fmp_packet_usage(path)["complete"] is False and not path.exists()
    with sqlite3.connect(path) as con:
        con.execute("CREATE TABLE cache(key TEXT PRIMARY KEY,payload TEXT)")
        con.execute("INSERT INTO cache VALUES('top5',?)", (json.dumps([fmp_card()]),))
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    result = saved_fmp_packet_usage(path)
    assert result["input_packets"] == 1 and result["complete"] is True
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    with sqlite3.connect(path) as con:
        con.execute("UPDATE cache SET payload=?", ('x'*8_000_001,))
    result = saved_fmp_packet_usage(path)
    assert result["state"] == "NOT_EVIDENCED" and result["complete"] is False


def test_closed_costs_unknown_preserve_original_audit_and_current_position():
    old = {"ausgestiegen_am": "2026-09-09", "netto_pnl": 0, "fee_quality": "UNKNOWN", "gebuehren": 0}
    shown = closed_result_display(old)
    assert shown["netto_pnl"] is None and shown["gebuehren"] is None
    assert shown["unconfirmed_net_audit"] == 0 and old["netto_pnl"] == 0
    current = {**old, "ausgestiegen_am": None, "protection_status": "ACTIVE"}
    assert closed_result_display(current) == current
    known = {**old, "fee_quality": "KNOWN"}
    assert closed_result_display(known)["gebuehren"] == 0


def test_risk_projection_cannot_leak_other_account_or_fake_position_failure():
    position = {"account_fingerprint": "fixture-A", "broker_environment": "DEMO"}
    runtime = {**position, "worker_alive": True, "risk_review": {"blocks_entries": True, "detail": "Risikobasis prüfen"}}
    assert position_risk_display(position, runtime)["state"] == "BLOCKED"
    assert position_risk_display({**position, "account_fingerprint": "fixture-B"}, runtime)["state"] == "UNKNOWN"
    assert position_risk_display(position, {**runtime, "worker_alive": False})["state"] == "STALE"


def test_universe_report_uses_actual_broker_without_spot_claim_for_stocks():
    from universe_diagnose import textbericht
    assert "OKX meldet" in textbericht({"broker": "okx"})
    etoro = textbericht({"broker": "etoro", "katalog_gesamt": 42})
    assert "eToro: 42" in etoro and "OKX" not in etoro and "SPOT" not in etoro
    assert "Broker nicht belegt" in textbericht({})


def test_pep_price_mismatch_explanation_does_not_claim_missing_ownership_or_stop():
    from decision_explanation import explain_decision
    text = explain_decision({"status": "BLOCKED", "reason": "ETORO_PROTECTION_PRICE_RULE_UNPROVEN"})["text"]
    assert "Schutzwerte" in text and "Schutzplan" in text
    assert "Bestand" not in text and "kein Schutz" not in text


def test_rejected_crypto_decision_records_only_local_context(monkeypatch):
    import crypto_engine
    import crypto_strategy_mode
    monkeypatch.setattr(crypto_strategy_mode, "current_mode", lambda: "FREQTRADE_SAMPLE")
    monkeypatch.setattr(crypto_strategy_mode, "entry_snapshot", lambda mode: {"timeframe": "5m", "strategy_version": "fixture"})
    engine = SimpleNamespace(broker=SimpleNamespace(demo=False, account_fingerprint=lambda: "fixture-A"),
        universum=SimpleNamespace(zustand=SimpleNamespace(hole=lambda broker,symbol: SimpleNamespace(inst_id="BTC-EUR"))),
        _exposure_gesperrte_waehrungen=["BTC"], _exposure_sperre=["BTC: Fixtureblock"],
        _abschluss=lambda protocol,*args,**kwargs: protocol.daten)
    data = crypto_engine.CryptoEngine.pruefe_kandidat(engine, "BTC")
    assert data["broker_environment"] == "LIVE" and data["paper"] is False
    assert data["account_fingerprint"] == "fixture-A" and data["inst_id"] == "BTC-EUR"
    assert data["signal_timeframe"] == "5m"


def test_actual_renderers_group_keep_details_and_escape_evidence(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node required for actual renderer validation")
    root = Path(__file__).resolve().parents[1]
    harness = r'''
const fs=require('fs'),vm=require('vm');
const element={value:'',textContent:'',innerHTML:'',addEventListener:()=>{}};
const ctx={document:{getElementById:()=>({...element}),querySelector:()=>null,querySelectorAll:()=>[]},window:{},URL,URLSearchParams,
setTimeout:()=>0,clearTimeout:()=>{},setInterval:()=>0,clearInterval:()=>{}};
vm.createContext(ctx);vm.runInContext(fs.readFileSync(process.argv[1]+'/webui/static/common.js','utf8'),ctx);
ctx.api=()=>new Promise(()=>{});vm.runInContext(fs.readFileSync(process.argv[1]+'/webui/static/logbook.js','utf8'),ctx);
const base={id:1,reason:'Anlaufsperre <img>',created_at_local:'19:01',symbol:'BTC',status:'BLOCKED',
decision_explanation:{text:'Kauf wartet wegen der Anlaufsperre.',kind:'warning'},
startup_repetition:{key:'a',timeframe:'5m',account_bound:false,instrument_proven:false}};
const groups=ctx.groupStartupDecisions([base,{...base,id:2,created_at_local:'19:02'},{...base,id:3,startup_repetition:{key:'b'}},{id:4}]);
if(groups.length!==3||groups[0].rows.length!==2)throw Error('grouping failed');
const grouped=ctx.startupGroupRow(groups[0]);
if(!grouped.includes('Entscheidung 1')||!grouped.includes('Entscheidung 2')||!grouped.includes('Kontozuordnung in diesen Altbelegen nicht belegt')||grouped.includes('<img>'))throw Error(grouped);
const plan=ctx.protectionEvidenceView({},[{decided_at:'2026-09-13T19:00:00Z',method:'ADOPT_EXISTING_BROKER_PROTECTION',original_plan:{stop:135.8946,take_profit:138.5207},effective_plan:{stop:135.90,take_profit:138.52},reason:'<img>',position_ids:['fixture'],quantity:109}]);
if(!plan.includes('135,8946')||!plan.includes('135,9')||!plan.includes('wirksamer Schutzplan')||plan.includes('<img>'))throw Error(plan);
const quality=ctx.candleQualityView([{instrument:'BTC-EUR',environment:'DEMO',freshness:'CURRENT',recent_active_rows:0,recent_rows:12,zero_volume_rows:30,rows:30,flat_close:true}]);
if(!quality.includes('Zeitlich aktuell')||!quality.includes('Handelsaktivität')||!quality.includes('keine Handelsaktivität')||!quality.includes('keine bestätigte Verbindungsstörung'))throw Error(quality);
const historical=ctx.providerHistoryView({error_scope:'HISTORICAL',error_age_seconds:7200,last_error_detail:'<img>',impact:'Keine aktuelle Sperre'});
if(!historical.includes('Historischer Quellenfehler')||!historical.includes('kein Beleg einer aktuellen Handelsblockade')||historical.includes('<img>'))throw Error(historical);
'''
    result = subprocess.run([node, "-e", harness, str(root)], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
