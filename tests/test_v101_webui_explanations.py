"""Recorded decisions, separate evidence states and actual JS renderer behavior."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess

import pytest

from decision_explanation import explain_decision


def screenshot_decision():
    return {"status": "NO_SIGNAL", "symbol": "BNB", "strategy_mode": "FREQTRADE_SAMPLE",
        "reason": "Kein Kaufsignal: RSI kreuzt 30 nicht aufwaerts; TEMA steigt nicht; Volumen 0",
        "signal_checks": {"rsi_cross_above_30": {"passed": False},
            "tema_rising": {"passed": False}, "volume_positive": {"passed": False, "volume": 0},
            "tema_at_or_below_bb_middle": {"passed": True}}}


def test_real_screenshot_pattern_explains_volume_and_missing_recovery():
    row = screenshot_decision()
    before = json.dumps(row, sort_keys=True)
    result = explain_decision(row)
    assert "kein Handelsvolumen" in result["text"]
    assert "Kurserholung noch nicht bestätigt" in result["text"]
    assert "RSI" not in result["text"] and "TEMA" not in result["text"]
    assert "Broker" not in result["text"]  # no invented connection outage
    assert json.dumps(row, sort_keys=True) == before


def test_missing_volume_is_not_reported_as_measured_zero():
    row = screenshot_decision()
    row["signal_checks"]["volume_positive"] = {"passed": False}
    text = explain_decision(row)["text"]
    assert "kein positives Handelsvolumen belegt" in text
    assert "kein Handelsvolumen gemeldet" not in text


def test_approved_buy_explains_only_proven_signal_checks():
    row = screenshot_decision()
    row.update(status="APPROVED", execution_status="READY_TO_SUBMIT", reason="")
    for check in row["signal_checks"].values():
        check["passed"] = True
    row["signal_checks"]["volume_positive"]["volume"] = 125
    result = explain_decision(row)
    assert "Erholungssignal bestätigt" in result["text"]
    assert "Handelsvolumen vorhanden" in result["text"]
    assert "Einstiegszone" in result["text"]
    assert result["kind"] == "warning"  # approval is not a filled order
    assert "signal_checks.tema_at_or_below_bb_middle" in result["evidence"]
    missing = explain_decision({"status": "APPROVED"})
    assert "Freigabegrund" in missing["text"] and "noch nicht belegt" in missing["text"]
    assert "Bedingungen erfüllt" not in missing["text"]


@pytest.mark.parametrize("reason, expected", [("stop_loss", "Verlustgrenze"),
    ("take_profit", "Gewinnziel"), ("trailing_stop", "nachgezogene Schutzgrenze"),
    ("roi", "Haltedauer"), ("manual_exit", "manueller Verkaufsauftrag")])
def test_approved_sell_explains_recorded_exit_trigger(reason, expected):
    result = explain_decision({"status": "APPROVED", "side": "SELL"}, {"exit_reason": reason})
    assert result["text"].startswith("Verkauf genehmigt, weil")
    assert expected in result["text"]
    assert "noch nicht bestätigt" in result["text"]


def test_conflicting_approval_does_not_claim_passed_checks():
    row = screenshot_decision()
    row.update(status="APPROVED", execution_status="FILLED")
    result = explain_decision(row)
    assert "widersprüchlichen Belege" in result["text"]
    assert result["kind"] == "warning"


@pytest.mark.parametrize("execution, expected", [("READY_TO_SUBMIT", "noch nicht bestätigt"),
    ("FILLED", "meldet die vollständige Ausführung"), ("PARTIAL", "nur ein Teil"),
    ("CANCELED_NO_FILL", "ohne Ausführung storniert"), ("REJECTED", "Broker hat den Auftrag jedoch abgelehnt"),
    ("UNKNOWN", "noch unklar")])
def test_approval_never_conflates_order_outcome(execution, expected):
    result = explain_decision({"status": "APPROVED", "execution_status": execution})
    assert expected in result["text"]
    assert "Gewinn" not in result["text"]


def test_sell_and_timeout_are_not_labelled_successful_buy_or_gpt_request():
    assert explain_decision({"status": "APPROVED", "side": "SELL"})["text"].startswith("Verkauf genehmigt")
    text = explain_decision({"status": "AI_HOLD", "reason": "Precheck-Zeitrahmen abgelaufen"})["text"]
    assert "nicht rechtzeitig" in text and "GPT wurde" not in text


def test_unknown_record_is_not_assigned_made_up_reason():
    text = explain_decision({"status": "BLOCKED", "reason": "new_reason_999"})["text"]
    assert "noch keinem eindeutigen" in text
    assert "Risiko" not in text and "Volumen" not in text
    assert "unklar" in explain_decision({"status": "UNKNOWN_AFTER_SUBMIT"})["text"]


def test_risk_review_remains_visible_alongside_closed_market_and_broker_local():
    from webui.diagnostics import operations_snapshot
    now = datetime.now(timezone.utc)
    context = {"account": "A", "observed_environment": "DEMO"}
    runtime = {"online": True, "worker_alive": True, "last_heartbeat": now.isoformat(),
        "stock_trading_ready": {"kaeufe_erlaubt": False, "grund": "US-Markt geschlossen", "offen": ["US-Markt geschlossen"]},
        "risk_review": {"blocks_entries": True, "detail": "Risikobasis falsch zugeordnet", "status": "REVIEW_REQUIRED"}}
    payload = {"bot": {"zustand": "aktiv"}, "broker_contexts": {"okx": context, "etoro": context},
        "etoro": runtime, "okx": dict(runtime, risk_review={}), "okx_detail": {"kaeufe_erlaubt": True}}
    brokers = operations_snapshot(payload, now=now)["brokers"]
    assert brokers["etoro"]["buy"]["blockers"] == ["US-Markt geschlossen", "Risikobasis falsch zugeordnet"]
    assert brokers["etoro"]["buy"]["state"] == "BLOCKED"
    assert brokers["okx"]["buy"]["state"] == "ALLOWED"


def test_complete_quantity_evidence_does_not_imply_known_fee(tmp_path):
    from webui.diagnostics import _fees_for_orders
    path = tmp_path / "fees.sqlite"
    with sqlite3.connect(path) as con:
        con.execute("CREATE TABLE execution_fills(order_key TEXT,quantity TEXT,fee TEXT,fee_currency TEXT)")
        con.execute("INSERT INTO execution_fills VALUES('A','1',NULL,'USD')")
        con.commit()
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    with sqlite3.connect(path.as_uri()+"?mode=ro", uri=True) as con:
        con.row_factory = sqlite3.Row
        assert _fees_for_orders(con, [{"order_key": "A", "evidence_complete": 1}])["A"]["status"] == "INCOMPLETE"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    with sqlite3.connect(path) as con:
        con.execute("UPDATE execution_fills SET fee='0'")
        con.commit()
        con.row_factory = sqlite3.Row
        assert _fees_for_orders(con, [{"order_key": "A", "evidence_complete": 1}])["A"]["status"] == "KNOWN"
        assert _fees_for_orders(con, [{"order_key": "A", "evidence_complete": 0}])["A"]["status"] == "INCOMPLETE"


def test_api_enriches_old_decision_without_database_migration(tmp_path, monkeypatch):
    from webui import state
    import decision_analytics as da
    monkeypatch.setattr(state, "ROOT", tmp_path)
    monkeypatch.setattr(da, "DB_PATH", tmp_path/state.config.DECISION_DB_FILE)
    da.init_db()
    row = screenshot_decision()
    with da._connect() as con:
        con.execute("INSERT INTO decisions(created_at_utc,local_day,symbol,status,payload_json,reason) VALUES(?,?,?,?,?,?)",
            ("2026-09-13T13:50:09+00:00", "2026-09-13", "BNB", "NO_SIGNAL", json.dumps(row), row["reason"]))
    before = hashlib.sha256((tmp_path/state.config.DECISION_DB_FILE).read_bytes()).hexdigest()
    data = state.decision_log()["rows"][0]
    assert "kein Handelsvolumen" in data["decision_explanation"]["text"]
    assert data["signal_checks"]["volume_positive"]["passed"] is False
    assert hashlib.sha256((tmp_path/state.config.DECISION_DB_FILE).read_bytes()).hexdigest() == before


def test_actual_logbook_renderer_preserves_technical_details_and_escapes_text():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node runtime unavailable; browser validation required")
    script = Path(__file__).resolve().parents[1]/"webui/static/logbook.js"
    harness = r'''
const fs=require('fs'),vm=require('vm');
const element={value:'',textContent:'',innerHTML:''};
const context={document:{getElementById:()=>({...element}),querySelector:()=>({...element})},
 api:()=>new Promise(()=>{}),URLSearchParams,
 esc:x=>String(x??'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'),
 badge:(text,kind)=>JSON.stringify({text,kind}),rowContext:()=>({environment:'DEMO'})};
vm.createContext(context);vm.runInContext(fs.readFileSync(process.argv[1],'utf8'),context);
const html=context.reason({decision_explanation:{kind:'<script>',text:'Kein Kauf: <img src=x onerror=alert(1)>'},
 reason:'RSI < 30',metrics:{volume_5m:0},signal_checks:{volume_positive:{passed:false}}});
if(html.includes('<img')||html.includes('<script>'))throw Error('Unescaped HTML');
if(!html.includes('<strong>Kein Kauf: &lt;img')||!html.includes('<details class="decision-technical">')
 ||!html.includes('RSI &lt; 30')||!html.includes('volume_5m=0.0000'))throw Error(html);
for(const execution of ['', 'READY_TO_SUBMIT','PARTIAL','PARTIALLY_FILLED','UNKNOWN','REJECTED','CANCELED_NO_FILL']){
 const badge=JSON.parse(context.entscheidungsmarke({status:'APPROVED',execution_status:execution}));
 if(badge.kind==='ok')throw Error('Unfilled approval appears green: '+execution);
 if(execution==='UNKNOWN'&&badge.text.includes('nicht ausgeführt'))throw Error('Unknown labelled definitely unfilled');
}
if(JSON.parse(context.entscheidungsmarke({status:'APPROVED',execution_status:'FILLED'})).kind!=='ok')throw Error('Confirmed fill missing');
console.log('renderer passed');
'''
    result = subprocess.run([node, "-e", harness, str(script)], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr


def test_protection_proof_cannot_cross_account_or_position_identity():
    from webui.state import protection_display_evidence
    position = {"account_fingerprint": "account-A", "broker_environment": "DEMO", "owned_position_ids": ["123"]}
    proof = {"observed": [{"position_id": "123", "stop": 135.9}], "confirmed": False}
    runtime = {"account_fingerprint": "account-A", "broker_environment": "DEMO",
        "worker_alive": True, "protection_evidence": {"1043": proof}}
    assert protection_display_evidence(position, runtime)["observed"][0]["stop"] == 135.9
    assert protection_display_evidence(dict(position, account_fingerprint="other"), runtime) == {}
    assert protection_display_evidence(dict(position, broker_environment="LIVE"), runtime) == {}
    assert protection_display_evidence(dict(position, owned_position_ids=["999"]), runtime) == {}
    stale = protection_display_evidence(position, dict(runtime, worker_alive=False))
    assert stale["runtime_fresh"] is False


def test_old_fmp_trend_schema_does_not_look_like_new_validated_context():
    from webui.state import fmp_display_context
    row = fmp_display_context("NBIS", {"NBIS": {"bezeichnung": "Fixture", "jahresanalyse":
        {"schema_version": "FMP-ANNUAL-1", "available": True, "trends": {"revenue_change": 15}}}})
    assert row["annual"]["available"] is False
    assert "trends" not in row["annual"]
    assert row["role"] == "OPTIONAL_RESEARCH_ONLY"
    assert fmp_display_context("UNKNOWN", {}) == {}


def test_actual_fmp_and_protection_renderers_keep_unavailable_values_unknown():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node runtime unavailable; browser validation required")
    script = Path(__file__).resolve().parents[1]/"webui/static/common.js"
    harness = r'''
const fs=require('fs'),vm=require('vm');
const context={document:{querySelector:()=>null,querySelectorAll:()=>[]},window:{},URL,URLSearchParams,setTimeout:()=>0,clearTimeout:()=>{}};
vm.createContext(context);vm.runInContext(fs.readFileSync(process.argv[1],'utf8'),context);
const fmp=context.fmpContextView({annual:{available:true,latest:{operating_cashflow:0},trend_period:{from:'2021',to:'2025',comparable:false},errors:['<img>']},daily:{available:false}});
if(!fmp.includes('kein belastbarer Vorjahresvergleich')||fmp.includes('<img>')||!fmp.includes('&lt;img&gt;')||!fmp.includes('Operativer Cashflow'))throw Error(fmp);
const protection=context.protectionEvidenceView({confirmed:false,requested:{stop:135.8946,take_profit:138.5207},observed:[{position_id:'123',stop:135.9,take_profit:138.52}],precision:{status:'UNPROVEN'}});
if(!protection.includes('Brokerregel nicht belegt')||!protection.includes('kein Sendebeleg')||!protection.includes('noch nicht bestätigt'))throw Error(protection);
'''
    result = subprocess.run([node, "-e", harness, str(script)], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
