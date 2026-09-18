"""User-visible evidence stays distinct; paid usage and pending events survive updates."""
from pathlib import Path
import json
import sqlite3
import subprocess


ROOT = Path(__file__).resolve().parents[1]


def js_eval(filename, expression):
    script = """const fs=require('fs'),vm=require('vm');
const c={document:{querySelector:()=>({addEventListener(){}}),querySelectorAll:()=>[]},nexusPoll:()=>{}, URL,URLSearchParams,
 location:{hash:'',search:''},history:{replaceState(){}},window:{addEventListener(){}},
 esc:v=>String(v??'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'),
 knownNumber:v=>typeof v==='number'&&Number.isFinite(v),
 observationTime:v=>v??'nicht belegt',badge:v=>String(v)};
vm.createContext(c);vm.runInContext(fs.readFileSync(process.argv[1],'utf8'),c);
process.stdout.write(vm.runInContext(process.argv[2],c));
"""
    result = subprocess.run(['node', '-e', script, str(ROOT/filename), expression], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return result.stdout


def test_x_discovery_shows_sample_and_pipeline_not_trade_permission():
    data = {'discovery': {'candidate_count': 1, 'candidates': [
        {'symbol': '<script>bad</script>', 'sampled_post_count': 2, 'topics': ['EARNINGS'], 'observed_at': 100}]}}
    pipeline = {'selection_state': 'STALE', 'source_pipeline': {'observed_at': 100,
        'x_received': 1, 'x_profile_validated': 1, 'x_new_selected': 1, 'x_researched': 0}}
    html = js_eval('webui/static/sources.js', 'renderXDiscovery('+json.dumps(data)+','+json.dumps(pipeline)+')')
    assert '<script>' not in html and '&lt;script&gt;' in html
    assert 'Stichprobe' in html and 'recherchiert' in html and 'Älterer Stand' in html
    assert 'Unternehmen und Anlass zu prüfen' in html


def test_counts_gap_is_visible_as_problem_instead_of_zero_or_warming():
    data = {'counts_coverage': [{'symbol': 'PEP', 'state': 'MISSING_BUCKETS', 'observed_days': 4,
        'expected_days': 6, 'consecutive_incomplete_days': 2, 'missing_days': ['2026-09-12'],
        'detail': 'Fehlende Tageswerte bleiben unbekannt'}]}
    html = js_eval('webui/static/sources.js', 'renderXNormalization('+json.dumps(data)+')')
    assert '2026-09-12' in html and 'unvollständige Tagesabdeckung' in html
    assert '4 von 6' in html and 'unbekannt' in html


def test_pulsar_x_sample_does_not_claim_measured_mentions():
    data = {'attention': {'source': 'X', 'mentions': None, 'x_discovery': {'sampled_post_count': 3}},
        'attention_coverage': {'detail': '0 gemessen, 0 zensiert, 14 unbekannt'}}
    html = js_eval('webui/static/pulsar.js', 'pulsarAttentionView('+json.dumps(data)+')')
    assert '3 Posts in der Entdeckungsstichprobe' in html
    assert 'Tageszahl: unbekannt' in html and '14 unbekannt' in html
    assert '3 Erwähnungen' not in html


def test_broker_reported_pnl_is_separate_from_confirmed_costs():
    row = {'broker': 'etoro', 'ausgestiegen_am': '2026-09-14', 'broker_result_quality': 'BROKER_HISTORY_COST_SCOPE_CONFLICT',
        'broker_reported_net_pnl': 244.16, 'broker_reported_fees': 0, 'broker_result_currency': 'USD',
        'waehrung': 'USD', 'broker_result_assessment': {'confirmed_entry_costs': 1, 'reason': '<script>scope conflict</script>'}}
    html = js_eval('webui/static/trades.js', 'brokerErgebnisBeleg('+json.dumps(row)+')')
    assert '244,16' in html and '1,00' in html and '0,00' in html
    assert 'separat' in html and '&lt;script&gt;' in html and '<script>' not in html


def test_multiple_news_origins_are_not_shown_as_verified_event_or_sell_instruction():
    row = {'status': 'MULTISOURCE_RISK_HINT', 'raw_crisis_score': 30, 'actionable_crisis_score': 22,
           'verified_event': False, 'origin_count': 2}
    html = js_eval('webui/static/sources.js', 'renderNewsRiskEvidence('+json.dumps(row)+')')
    assert 'Bestätigtes Ereignis: nein' in html and 'keine Verkaufsanweisung' in html
    assert '30' in html and '22' in html


def test_upgrade_copies_committed_wal_inbox_and_quota_without_reset(tmp_path):
    from settings_migration import migrate_from, STRICT_STATE_FILES
    source, target = tmp_path/'old', tmp_path/'new'
    source.mkdir(); target.mkdir()
    connections = []
    try:
        for filename in ('etoro_stream_inbox.sqlite', 'etoro_http_budget.sqlite'):
            assert filename in STRICT_STATE_FILES
            con = sqlite3.connect(source/filename)
            connections.append(con)
            con.execute('PRAGMA journal_mode=WAL')
            con.execute('PRAGMA wal_autocheckpoint=0')
            con.execute('CREATE TABLE retained (id INTEGER PRIMARY KEY, receipt TEXT)')
            con.execute('INSERT INTO retained VALUES (1,?)', (filename+'-receipt',))
            con.commit()
            assert (source/(filename+'-wal')).stat().st_size > 0
        copied, _ = migrate_from(source, target)
        for filename in ('etoro_stream_inbox.sqlite', 'etoro_http_budget.sqlite'):
            assert filename in copied
            with sqlite3.connect(target/filename) as con:
                assert con.execute('SELECT receipt FROM retained').fetchone()[0] == filename+'-receipt'
                assert con.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    finally:
        for con in connections:
            con.close()
