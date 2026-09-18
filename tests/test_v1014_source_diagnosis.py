"""Passive diagnostic evidence: discovery is separate from execution/accounting."""
import hashlib
import json
import sqlite3

import NEXUS_10_Diagnose as d


def cache(key, payload, saved=100, expires=200):
    return {'key': key, 'payload': json.dumps(payload), 'saved': saved, 'expires': expires}


def test_new_x_metadata_keeps_coverage_and_candidates_but_not_unknown_content():
    value = {'state': 'OK', 'discovery': {'automatic': True, 'candidate_count': 1,
        'candidates': [{'symbol': 'PEP', 'evidence_ids': ['receipt'], 'sampled_post_count': 2,
            'direct_trade_effect': False, 'future_private_text': 'must-not-export'}],
        'source_accounts': [{'handle': 'publicagency', 'role': 'POLICY', 'reference': 'https://example.org'}]},
        'events': [{'event_id': 'event', 'handle': 'private-author', 'text': 'private-content'}],
        'counts_coverage': [{'symbol': 'PEP', 'state': 'INCOMPLETE', 'missing_days': ['2026-09-12'],
            'has_next_page': True, 'invalid_rows': 1}],
        'normalization': {'changed_at': 100, 'previous_control_query_hash': 'prior', 'control_query_hash': 'new'},
        'attention': [{'symbol': 'PEP', 'missing_control_days': 3, 'inconsistent_control_days': 1,
            'daily_samples': [{'day': '2026-09-12', 'count': 4, 'coverage': 'VOLLSTAENDIG'}]}]}
    clean = d.x_receipts_only(value)
    assert clean['discovery']['candidates'][0]['sampled_post_count'] == 2
    assert clean['discovery']['source_accounts'][0]['handle'] == 'publicagency'
    assert clean['counts_coverage'][0]['missing_days'] == ['2026-09-12']
    assert clean['normalization']['previous_control_query_hash'] == 'prior'
    assert clean['attention'][0]['daily_samples'][0]['count'] == 4
    assert all(secret not in json.dumps(clean) for secret in ('private-author', 'private-content', 'must-not-export'))


def test_pipeline_shows_sampler_selection_and_card_coordination_separately():
    rows = [cache('candidate_selection', {'source_pipeline': {'x_received': 4,
        'x_valid_samples': 3, 'x_new_selected': 2, 'x_researched': 1,
        'research_status': 'COMPLETED', 'research_slots': {'reddit': 3, 'x': 2, 'shared_limit': 5},
        'future_raw_payload': 'not-allowed'}}),
        cache('top5', [{'symbol': 'PEP', 'source_coordination': {'status': 'INDEPENDENT_EVENT_CHECK_PENDING',
            'roles': {'X': 'DISCOVERY_HINT', 'Reddit': 'NOT_OBSERVED', 'FMP_profile': 'IDENTITY_RECEIPT'},
            'news_rows': 2, 'distinct_article_urls': 1, 'duplicate_news_rows': 1,
            'x_claim_confirmed': False, 'independent_event_sources': None, 'crisis_authorized': False,
            'article_groups': [{'url': 'raw-not-exported'}]},
            'attention_coverage': {'days': 2, 'censored_days': 6, 'unknown_days': 20}}])]
    result = d.source_pipeline_report({}, rows, {'discovery': {'candidate_count': 4}}, now=110)
    pipe = result['pulsar_pipeline']
    assert pipe['selection_state'] == 'OBSERVED'
    assert pipe['source_pipeline']['x_received'] == 4 and pipe['source_pipeline']['x_researched'] == 1
    card = pipe['card_sources'][0]
    assert card['attention_coverage']['unknown_days'] == 20
    assert card['source_coordination']['roles']['FMP_profile'] == 'IDENTITY_RECEIPT'
    assert card['source_coordination']['independent_event_sources'] is None
    assert not card['source_coordination']['crisis_authorized']
    assert 'raw-not-exported' not in json.dumps(result) and 'not-allowed' not in json.dumps(result)
    assert d.source_pipeline_report({}, rows, {}, now=300)['pulsar_pipeline']['selection_state'] == 'STALE'
    missing = d.source_pipeline_report({}, [], {}, now=300)['pulsar_pipeline']
    assert missing['selection_state'] == 'NOT_OBSERVED' and 'x_received' not in missing['source_pipeline']


def test_passive_ui_snapshot_prioritizes_selection_receipt_without_writes(tmp_path, monkeypatch):
    import market_intelligence
    from webui.market_sources import snapshot
    monkeypatch.setenv('TRADINGBOT_TEST_STATE_DIR', str(tmp_path))
    path = tmp_path/'pulsar_research.sqlite'
    with sqlite3.connect(path) as con:
        con.execute('CREATE TABLE cache (key TEXT,saved REAL,expires REAL,payload TEXT)')
        for index in range(60):
            con.execute('INSERT INTO cache VALUES (?,?,?,?)', (f'social:fixture:{index}', 1, 2, '[]'))
        con.execute('INSERT INTO cache VALUES (?,?,?,?)', ('candidate_selection', 100, 200,
            json.dumps({'source_pipeline': {'x_received': 4, 'x_researched': 2}})))
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    monkeypatch.setattr(market_intelligence, 'public_status', lambda: {'state': 'DISABLED'})
    result = snapshot()
    assert result['pulsar_pipeline']['source_pipeline']['x_researched'] == 2
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    assert not (tmp_path/'market_intelligence.sqlite').exists()


def test_private_stream_export_keeps_committed_wal_metadata_and_omits_all_payload_columns(tmp_path):
    path = tmp_path/'etoro_stream_inbox.sqlite'
    with sqlite3.connect(path) as con:
        con.execute('PRAGMA journal_mode=WAL')
        con.execute('CREATE TABLE etoro_stream_events(event_key TEXT,broker TEXT,account_fingerprint TEXT,'
            'environment TEXT,received_at_utc TEXT,state TEXT,validation TEXT,deliveries INTEGER,'
            'event_json TEXT,last_error TEXT,future_private_payload TEXT)')
        con.execute('INSERT INTO etoro_stream_events VALUES(?,?,?,?,?,?,?,?,?,?,?)',
            ('receipt', 'etoro', 'account-one', 'DEMO', d.utc(100), 'UNMATCHED', 'VALID', 2,
             '{"token":"private-token","body":"private-event"}', 'private-error', 'future-secret'))
        con.commit()
        before = hashlib.sha256(path.read_bytes()).hexdigest()
        bundle = d.Bundle(tmp_path/'out')
        meta, rows = d.export_database(path, bundle, 'ende/datenbanken/etoro_stream_inbox.sqlite')
        assert hashlib.sha256(path.read_bytes()).hexdigest() == before
        assert meta['source_journal_mode'] == 'wal'
        exported = rows['etoro_stream_events'][0]
        assert exported['deliveries'] == 2 and exported['event_key'] == 'receipt'
        assert not {'event_json', 'last_error', 'future_private_payload'} & exported.keys()
        body = (bundle.work/meta['tables']['etoro_stream_events']['export_file']).read_text()
        assert all(value not in body for value in ('private-token', 'private-event', 'private-error', 'future-secret'))
        assert meta['tables']['etoro_stream_events']['schema'] is None


def test_private_stream_counts_remain_account_scoped_and_truncation_is_visible():
    rows = [{'broker': 'etoro', 'account_fingerprint': 'one', 'environment': 'DEMO', 'state': 'MATCHED',
             'validation': 'VALID', 'deliveries': 2, 'received_at_utc': d.utc(100)},
            {'broker': 'etoro', 'account_fingerprint': 'two', 'environment': 'LIVE', 'state': 'QUARANTINED',
             'validation': 'ACCOUNT_MISMATCH', 'deliveries': 1, 'received_at_utc': d.utc(90)}]
    out = d.private_stream_report(rows, {'status': 'OK', 'truncated': True})
    assert len(out['scopes']) == 2 and out['events_count']['quality'] == 'LOWER_BOUND'
    assert out['scopes'][0]['deliveries_observed'] == 2
    assert out['scopes'][1]['states'] == {'QUARANTINED': 1}
    assert out['raw_payloads_exported'] is False
    assert d.private_stream_report([], {})['events_count']['count'] is None


def test_broker_history_profit_does_not_become_locally_confirmed_net_profit():
    rows = [{'trade_id': 54, 'broker': 'etoro', 'symbol': 'PEP', 'broker_position_id': '3597440106',
        'netto_pnl': None, 'broker_reported_net_pnl': 244.16, 'broker_reported_fees': 0,
        'broker_result_currency': 'USD', 'broker_result_quality': 'BROKER_HISTORY_COST_SCOPE_CONFLICT',
        'broker_result_detail_json': json.dumps({'computed_gross_pnl': 244.16,
            'confirmed_entry_costs': 1, 'computed_net_pnl': None, 'fee_scope_confirmed': False,
            'risk_release': False, 'raw': 'private-broker-payload'})}]
    out = d.broker_history_results(rows, {'status': 'OK', 'truncated': False})
    report = out['rows'][0]
    assert report['broker_reported_fees'] == 0 and report['broker_reported_net_pnl'] == 244.16
    assert report['netto_pnl'] is None and report['assessment']['confirmed_entry_costs'] == 1
    assert not report['assessment']['risk_release'] and not report['assessment']['fee_scope_confirmed']
    assert 'private-broker-payload' not in json.dumps(out)


def test_diagnostic_findings_distinguish_incomplete_counts_from_control_conflict():
    x = {'state': 'OK', 'counts_coverage': [{'symbol': 'PEP', 'state': 'MISSING_DAYS',
        'missing_days': ['2026-09-12'], 'invalid_rows': 0}],
        'attention': [{'symbol': 'AGI', 'normalization_status': 'CONTROL_INCONSISTENT',
            'inconsistent_control_days': 1, 'normalized_ratio': None}]}
    analysis = d.final_analysis({}, {'market_intelligence_status.json': {'data': x}},
        {}, {}, [], [], 0, 300)
    findings = {row['code']: row for row in d.diagnostic_findings(analysis) if row['component'] == 'X'}
    assert findings['X_COUNTS_COVERAGE_INCOMPLETE']['symbol'] == 'PEP'
    assert findings['X_COUNTS_CONTROL_INCONSISTENT']['symbol'] == 'AGI'
    assert findings['X_COUNTS_CONTROL_INCONSISTENT']['evidence']['normalized_ratio'] is None


def test_news_risk_projection_keeps_multi_origin_gate_receipts_without_article_text(tmp_path, monkeypatch):
    import market_intelligence
    from webui.market_sources import snapshot
    monkeypatch.setenv('TRADINGBOT_TEST_STATE_DIR', str(tmp_path))
    monkeypatch.setattr(market_intelligence, 'public_status', lambda: {'state': 'DISABLED'})
    risk = {'status': 'MULTISOURCE_RISK_HINT', 'raw_crisis_score': 30,
        'actionable_crisis_score': 20, 'buy_pause_supported': True, 'verified_event': False,
        'origin_count': 2, 'categories': [{'category': 'BANKING',
            'origins': ['publisher-one.example', 'publisher-two.example'], 'multiple_origins': True,
            'risk_texts': ['private-article']}], 'excluded_reasons': {'SOCIAL_LINEAGE': 2},
        'risk_texts': ['private-article'], 'raw_payload': 'another-private-field'}
    (tmp_path/'runtime_status.json').write_text(json.dumps({'news_risk_evidence': risk}))
    observed = snapshot()['news_risk_evidence']
    assert observed['raw_crisis_score'] == 30 and observed['actionable_crisis_score'] == 20
    assert observed['excluded_reasons'] == {'SOCIAL_LINEAGE': 2}
    assert observed['buy_pause_supported'] and not observed['verified_event']
    assert observed['categories'][0]['origins'] == ['publisher-one.example', 'publisher-two.example']
    assert 'private-article' not in json.dumps(observed) and 'another-private-field' not in json.dumps(observed)
    analysis = d.final_analysis({}, {'runtime_status.json': {'data': {'news_risk_evidence': risk}}},
        {}, {}, [], [], 0, 300)
    assert analysis['providers']['pipeline']['news_risk_evidence'] == observed
