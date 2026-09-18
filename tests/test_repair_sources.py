"""D18/D22 regression checks. All HTTP is mocked; no provider access."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo
import json
import xml.etree.ElementTree as ET

import pytest
import requests
import news_sources as ns
from nasdaq_halt_feed import trading_evidence


def feed(rows, build=''):
    root = ET.Element('rss')
    channel = ET.SubElement(root, 'channel')
    if build:
        ET.SubElement(channel, 'lastBuildDate').text = build
    for row in rows:
        item = ET.SubElement(channel, 'item')
        for k, v in row.items():
            ET.SubElement(item, k).text = v
    return root


def halt(symbol='ABC', **fields):
    return {'IssueSymbol': symbol, 'HaltDate': '09/09/2026', 'HaltTime': '09:30:00',
            'ReasonCode': 'T1', **fields}


@pytest.fixture
def clock(monkeypatch, tmp_path):
    now = [datetime(2026, 9, 9, 14, tzinfo=timezone.utc)]
    monkeypatch.setattr(ns, '_now_utc', lambda: now[0])
    monkeypatch.setattr(ns.time, 'time', lambda: now[0].timestamp())
    monkeypatch.setattr(ns, 'STATUS_FILE', tmp_path/'sources.json')
    monkeypatch.setattr(ns, '_GLOBAL_CACHE', {})
    return now


@pytest.fixture
def recorded_feed():
    data = json.loads((Path(__file__).parent/'fixtures/repair_nasdaq_diagnostic.json').read_text())['data']
    rows = []
    for r in data['active_rows']:
        start = datetime.fromisoformat(r['halt_at']).astimezone(ZoneInfo('America/New_York'))
        rows.append(halt(r['symbol'], HaltDate=start.strftime('%m/%d/%Y'),
                         HaltTime=start.strftime('%H:%M:%S'), ReasonCode=r['reason_code']))
    rows.extend(r['fields'] for r in data['invalid_rows'])
    return feed(rows), datetime.fromisoformat(data['observed_at'])


def client_with_feed(monkeypatch, root):
    calls = []
    monkeypatch.setattr(ns.MultiSourceNews, 'provider_configuration', lambda self: {'Nasdaq Halts': True})
    client = ns.MultiSourceNews()
    def get(*args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(content=ET.tostring(root))
    monkeypatch.setattr(client, '_public_get', get)
    return client, calls


def test_recorded_23_rows_reach_consumer_as_eight_positive_evidences(monkeypatch, clock, recorded_feed):
    root, observed = recorded_feed
    clock[0] = observed
    client, calls = client_with_feed(monkeypatch, root)
    bundle = client.fetch_symbol('HUBC', focused=True)
    report = bundle.source_coverage['Nasdaq Halts']
    assert report['total_rows'] == 23 and report['usable_count'] == 8
    assert report['invalid_count'] == 15 and report['invalid_reason_counts'] == {'MULTIDAY_HALT_WITHOUT_FRESH_FEED': 15}
    assert not report['complete'] and not report['absence_is_evidence']
    assert 'Nasdaq Halts' in bundle.sources_partial and 'Nasdaq Halts' not in bundle.sources_ok
    assert [x.symbols for x in bundle.items] == [['HUBC']]
    assert len(calls) == 1  # market pool and focused consumer reuse one current feed
    from news_filter import NachrichtenFilter
    filt = NachrichtenFilter()
    filt._multi = client
    monkeypatch.setattr(__import__('news_filter'), 'news_rule', lambda name, default: 100 if name == 'NEWS_BLOCK_SCORE' else default)
    result = filt.pruefe('HUBC')
    assert result.geprueft and result.aktiver_halt and result.kauf_blockiert
    other = filt.pruefe('MSFT')
    assert not other.geprueft and not other.aktiver_halt
    assert not other.quellen_abdeckung['Nasdaq Halts']['absence_is_evidence']
    assert 'unauffaellig' not in other.kurzfassung()


def test_partial_feed_does_not_turn_other_news_into_halt_clearance(monkeypatch, clock):
    client, _ = client_with_feed(monkeypatch, feed([halt(), {'title': 'unstructured'}]))
    bundle = client.fetch_symbol('MSFT')
    from news_filter import Nachrichtenlage
    result = Nachrichtenlage(symbol='MSFT', geprueft=True, quellen=['Other'],
                             quellen_abdeckung=bundle.source_coverage)
    assert 'unvollstaendig' in result.kurzfassung()
    assert not result.aktiver_halt


def test_partial_source_status_expires_with_its_positive_evidence(monkeypatch, clock):
    client, _ = client_with_feed(monkeypatch, feed([halt(), {'title': 'unstructured'}]))
    client.fetch_symbol('ABC')
    status = client.health_snapshot()['Nasdaq Halts']
    assert status['state'] == 'partial' and status['current_effect'] == 'SOURCE_PARTIAL'
    assert status['impact'].startswith('1 gueltige')
    clock[0] += timedelta(seconds=91)
    status = client.health_snapshot()['Nasdaq Halts']
    assert status['state'] == 'stale' and status['current_effect'] == 'UNKNOWN'
    assert status['error_scope'] == 'HISTORICAL'


@pytest.mark.parametrize('build', ['Wed, 09 Sep 2026 13:00:00 GMT', 'Wed, 09 Sep 2026 15:00:00 GMT'])
def test_download_does_not_freshen_stale_or_future_feed(clock, build):
    result = trading_evidence(feed([halt()], build), clock[0])
    assert result['items'] == []
    assert result['diagnostics']['active_count'] == 1
    assert result['diagnostics']['usable_count'] == 0
    assert not result['diagnostics']['complete']


def test_old_multiday_halt_requires_current_feed_and_resume_quote_is_not_trade(clock):
    row = halt(HaltDate='09/01/2026', ResumptionQuoteTime='09:35:00')
    assert trading_evidence(feed([row]), clock[0])['items'] == []
    current = trading_evidence(feed([row], 'Wed, 09 Sep 2026 14:00:00 GMT'), clock[0])
    assert current['items'][0].metadata['active_halt']


def test_resumption_supersedes_same_event_but_not_a_later_halt(clock):
    rows = [halt(), halt(ResumptionDate='09/09/2026', ResumptionTradeTime='09:45:00'), halt(HaltTime='09:55:00')]
    result = trading_evidence(feed(rows), clock[0])
    assert len(result['items']) == 1
    assert result['items'][0].published_at.minute == 55
    assert result['diagnostics']['resumed_count'] == 1


def test_known_resumption_invalidates_both_consumer_and_feed_cache(monkeypatch, clock):
    root = feed([halt(ResumptionDate='09/09/2026', ResumptionTradeTime='10:00:20')])
    client, calls = client_with_feed(monkeypatch, root)
    from news_filter import NachrichtenFilter
    filt = NachrichtenFilter(cache_minuten=30)
    filt._multi = client
    assert filt.pruefe('ABC').kauf_blockiert
    clock[0] += timedelta(seconds=21)
    result = filt.pruefe('ABC')
    assert not result.aktiver_halt and not result.kauf_blockiert and result.schlagzeilen == []
    assert len(calls) == 1


def test_new_resumption_reloaded_after_60_seconds_instead_of_30_minutes(monkeypatch, clock):
    root = feed([halt()])
    client, calls = client_with_feed(monkeypatch, root)
    from news_filter import NachrichtenFilter
    filt = NachrichtenFilter(cache_minuten=30)
    filt._multi = client
    assert filt.pruefe('ABC').kauf_blockiert
    item = root.find('./channel/item')
    ET.SubElement(item, 'ResumptionDate').text = '09/09/2026'
    ET.SubElement(item, 'ResumptionTradeTime').text = '10:00:30'
    clock[0] += timedelta(seconds=61)
    assert not filt.pruefe('ABC').kauf_blockiert
    assert len(calls) == 2


def test_article_cap_and_text_dedupe_cannot_remove_structured_halts(monkeypatch, clock):
    client, _ = client_with_feed(monkeypatch, feed([halt('AAA'), halt('BBB'), halt('CCC')]))
    monkeypatch.setattr(ns.config, 'NEWS_MARKET_MAX_ARTICLES', 1)
    bundle = client.fetch_market()
    assert len(bundle.items) == 3
    original = bundle.items[0]
    vague = ns.NewsItem('Other', original.headline, published_at=clock[0], symbols=original.symbols)
    assert len(client._dedupe([vague, original])) == 2


def test_gdelt_429_pause_survives_new_client_and_ui_reads_do_not_retry(monkeypatch, clock):
    monkeypatch.setattr(ns.MultiSourceNews, 'provider_configuration', lambda self: {'GDELT': True})
    calls = []
    def failed():
        calls.append(1)
        response = requests.Response(); response.status_code = 429
        raise requests.HTTPError('429 Too Many Requests', response=response)
    first = ns.MultiSourceNews()
    first._provider_call('GDELT', failed, ns.NewsBundle())
    second = ns.MultiSourceNews()
    second._provider_call('GDELT', failed, ns.NewsBundle())
    before = ns.STATUS_FILE.read_bytes()
    status = second.health_snapshot()['GDELT']
    assert status['backoff_seconds'] == 7200 and status['error_scope'] == 'CURRENT'
    assert status['current_effect'] == 'SOURCE_PAUSED' and 'Core' in status['impact']
    assert ns.STATUS_FILE.read_bytes() == before and len(calls) == 1
    clock[0] += timedelta(seconds=7201)
    status = second.health_snapshot()['GDELT']
    assert status['state'] == 'stale' and status['error_scope'] == 'HISTORICAL'
    assert status['current_effect'] == 'UNKNOWN' and not status['healthy']
    second._mark('GDELT', True, 'Wieder erreichbar', 3)
    status = second.health_snapshot()['GDELT']
    assert status['healthy'] and status['error_scope'] == 'HISTORICAL'
    assert status['last_error_detail'] == '429 Too Many Requests'
    assert status['current_effect'] == 'NONE'


def test_tradestie_tls_failure_is_persistent_bounded_and_never_disables_verification(monkeypatch, tmp_path):
    from pulsar import research
    monkeypatch.setattr(research, 'path', lambda: tmp_path/'research.sqlite')
    now = 1789304460.
    calls = []
    class Client:
        def get(self, *args, **kwargs):
            calls.append(kwargs)
            raise requests.exceptions.SSLError('certificate verify failed')
    with pytest.raises(requests.exceptions.SSLError):
        research.fetch_social('tradestie', session=Client(), now=now)
    with pytest.raises(research.Blocked):
        research.fetch_social('tradestie', session=Client(), now=now+1)
    status = research.social_source_status(now=now+1)['tradestie']
    assert len(calls) == 1 and all(x.get('verify', True) is True for x in calls)
    assert status['failure_kind'] == 'tls' and status['backoff_seconds'] == 3599
    assert status['error_scope'] == 'CURRENT' and 'Core' in status['impact']
    assert status['last_attempt'] == now
    with research.db() as con:
        assert con.execute('SELECT count(*) FROM usage').fetchone()[0] == 2
    with pytest.raises(requests.exceptions.SSLError):
        research.fetch_social('tradestie', session=Client(), now=now+3601)
    assert research.social_source_status(now=now+3601)['tradestie']['backoff_seconds'] == 7200


def test_social_recovery_keeps_error_history_without_claiming_current_failure(monkeypatch, tmp_path):
    from pulsar import research
    monkeypatch.setattr(research, 'path', lambda: tmp_path/'research.sqlite')
    now = 1789304460.
    research._social_status('tradestie', False, now, exc=requests.exceptions.SSLError('TLS problem'))
    research._social_status('tradestie', True, now+3601)
    status = research.social_source_status(now=now+3602)['tradestie']
    assert status['healthy'] and status['error_scope'] == 'HISTORICAL'
    assert status['last_error_detail'] == 'TLS problem' and status['current_effect'] == 'NONE'
