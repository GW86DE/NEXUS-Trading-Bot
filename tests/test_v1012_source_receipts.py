"""Stored evidence, bounded observation coverage and current pause semantics."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import xml.etree.ElementTree as ET

import pytest


def fmp_source():
    from pulsar.worker import _source_id
    source = {'provider': 'FMP', 'kind': 'profile', 'symbol': 'AGI', 'observed_at': 1789325155,
        'url': 'https://financialmodelingprep.com/stable/profile',
        'data': {'symbol': 'AGI', 'companyName': 'Alamos <script>alert(1)</script>',
            'isEtf': False, 'isFund': False, 'fixture_text': 'fixture-provider-secret'}}
    source['id'] = _source_id(source)
    return source


def store_source(source):
    from pulsar import research
    with research.db() as con:
        con.execute("INSERT INTO cache VALUES ('top5',1,2,?)", (json.dumps([{'symbol': 'AGI', 'sources': [source]}]),))
    return research.path()


def test_source_viewer_uses_exact_stored_receipt_no_http_no_secrets_or_html(monkeypatch):
    from webui.source_receipts import source_receipt, html_receipt
    import requests, live_settings
    source = fmp_source(); p = store_source(source)
    monkeypatch.setattr(requests, 'get', lambda *a, **kw: pytest.fail('no fresh request'))
    monkeypatch.setattr(live_settings, 'fmp_key', lambda: 'fixture-provider-secret')
    before = hashlib.sha256(p.read_bytes()).hexdigest()
    view = source_receipt('AGI', source['id'])
    assert view['verified'] and view['redacted'] and not view['truncated']
    assert 'Alamos' in view['text'] and 'fixture-provider-secret' not in view['text']
    html = html_receipt('AGI', source['id'])
    assert '<script>' not in html and '&lt;script&gt;' in html
    assert source['id'] in html and 'Prüfsumme' in html
    assert hashlib.sha256(p.read_bytes()).hexdigest() == before


def test_source_viewer_refuses_other_symbol_unknown_id_and_tampered_facts():
    from webui.source_receipts import source_receipt
    source = fmp_source(); p = store_source(source)
    for symbol, identity in [('MU', source['id']), ('AGI', 'b'*64)]:
        with pytest.raises(FileNotFoundError): source_receipt(symbol, identity)
    with pytest.raises(ValueError): source_receipt('../secret', source['id'])
    source['data']['isEtf'] = True
    with sqlite3.connect(p) as con:
        con.execute("UPDATE cache SET payload=?", (json.dumps([{'symbol': 'AGI', 'sources': [source]}]),))
    with pytest.raises(ValueError, match='Pruefsumme'): source_receipt('AGI', source['id'])


def test_source_history_fallback_and_missing_database_remain_read_only():
    from pulsar import research
    from webui.source_receipts import source_receipt
    source = fmp_source()
    with pytest.raises(FileNotFoundError): source_receipt('AGI', source['id'])
    assert not research.path().exists()
    with research.db() as con:
        con.execute("INSERT INTO assessments VALUES('a',1,'AGI','h',?)",
            (json.dumps({'symbol': 'AGI', 'sources': [source]}),))
    assert source_receipt('AGI', source['id'])['verified']


def test_fmp_receipt_route_requires_authentication(monkeypatch):
    from fastapi.testclient import TestClient
    from webui.app import app
    from test_v100_webui_backend import configured_auth
    source = fmp_source(); store_source(source)
    auth, _ = configured_auth(monkeypatch)
    client = TestClient(app)
    path = '/api/pulsar/source/AGI/'+source['id']
    assert client.get(path).status_code == 401
    client.cookies.set(auth.COOKIE, auth.issue_session('testuser')[0])
    response = client.get(path)
    assert response.status_code == 200 and response.headers['cache-control'] == 'no-store'


def test_observed_page_absence_does_not_create_mentions_or_14day_eligibility():
    from pulsar import research, research_history as h
    now = datetime(2026, 9, 14, 12, tzinfo=timezone.utc).timestamp()
    with research.db() as con:
        for day in range(1, 15):
            stamp = now-day*86400
            h.record_coverage(con, 'apewisdom', 1, stamp, [{'symbol': 'MU', 'mentions': 20}])
            h.record(con, 'apewisdom', 'MU', stamp, {'mentions': 20}, 'actual')
        result = h.baseline(con, 'AGI', 'apewisdom', now)
        assert result['source_subset_days'] == 14 and result['censored_days'] == 14
        assert result['days'] == 0 and result['median_mentions'] is None and not result['ready_14d']
        assert not result['absence_inferred']
        present = h.baseline(con, 'MU', 'apewisdom', now)
        assert present['ready_14d'] and present['median_mentions'] == 20 and present['censored_days'] == 0
        assert h.baseline(con, 'MU', 'apewisdom:stocks', now)['source_subset_days'] == 0


def test_fresh_channel_publication_date_can_verify_old_halt_but_stale_build_cannot():
    from test_repair_sources import feed, halt
    from nasdaq_halt_feed import trading_evidence
    now = datetime(2026, 9, 14, 14, tzinfo=timezone.utc)
    root = feed([halt()])
    ET.SubElement(root.find('channel'), 'pubDate').text = 'Mon, 14 Sep 2026 14:00:00 GMT'
    result = trading_evidence(root, now); items, report = result['items'], result['diagnostics']
    assert len(items) == 1 and report['feed_timestamp_source'] == 'pubDate'
    ET.SubElement(root.find('channel'), 'lastBuildDate').text = 'Sun, 13 Sep 2026 14:00:00 GMT'
    result = trading_evidence(root, now); items, report = result['items'], result['diagnostics']
    assert not items and report['feed_freshness'] == 'STALE_OR_FUTURE'
    root = feed([halt()]); result = trading_evidence(root, now)
    items, report = result['items'], result['diagnostics']
    assert not items and report['unconfirmed_multiday_rows'][0]['symbol'] == 'ABC'
    assert not report['absence_is_evidence']


def test_active_backoff_survives_historical_trigger_classification_and_is_reported():
    import NEXUS_10_Diagnose as d
    statuses = {'GDELT': {'ok': False, 'time': d.utc(1), 'next_retry_at': d.utc(50)}}
    history = d.provider_history(statuses, {'gdelt_enabled': True}, 10, 20)
    assert history['historical'][0]['reported_error']
    assert history['active_pauses'][0]['trigger_before_window']
    assert not d.provider_history(statuses, {'gdelt_enabled': False}, 10, 20)['active_pauses']
    analysis = d.final_analysis({}, {}, {}, {}, [], [], 10, 20)
    analysis['providers']['source_history'] = history
    report = d.render_report(analysis, {})
    assert 'Aktuell wirksame Quellenpausen: 1' in report and 'GDELT: pausiert bis' in report
    monday = datetime(2026, 9, 14, 14, tzinfo=timezone.utc).timestamp()
    note = d.pulsar_report([], {'requests': []}, monday)['schedule']['note']
    assert 'Werktag' in note and '15 Minuten' in note and 'Wochenende' not in note
