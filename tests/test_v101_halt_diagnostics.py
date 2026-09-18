from datetime import datetime, timezone
import xml.etree.ElementTree as ET
import pytest
from nasdaq_halt_feed import inspect_feed, parse, FeedIncomplete

NOW = datetime(2026, 9, 9, 14, tzinfo=timezone.utc)

def feed(items, build='Wed, 09 Sep 2026 14:00:00 GMT'):
    return ET.fromstring(f'<rss><channel><lastBuildDate>{build}</lastBuildDate>{items}</channel></rss>')

def item(symbol='ABC', extra=''):
    return (f'<item><IssueSymbol>{symbol}</IssueSymbol><HaltDate>09/09/2026</HaltDate>'
            '<HaltTime>09:30:00</HaltTime><ReasonCode>T1</ReasonCode>' + extra + '</item>')

def test_mixed_feed_keeps_valid_evidence_but_never_implies_complete():
    root = feed(item() + '<item><title>XYZ trading halt</title></item>')
    result = inspect_feed(root, NOW)
    assert result['items'][0].symbols == ['ABC']
    r = result['diagnostics']
    assert r['active_count'] == 1 and r['invalid_count'] == 1
    assert not r['complete'] and not r['absence_is_evidence']
    assert r['invalid_rows'][0]['code'] == 'INVALID_STRUCTURED_SYMBOL'
    captures = []
    with pytest.raises(FeedIncomplete):
        parse(root, NOW, on_diagnostics=captures.append)
    assert captures[0]['active_rows'][0]['symbol'] == 'ABC'

def test_resumption_and_timezone_do_not_create_new_halt():
    root=feed(item(extra='<ResumptionDate>09/09/2026</ResumptionDate><ResumptionTradeTime>09:45:00</ResumptionTradeTime>'))
    assert parse(root,NOW)==[]
    assert inspect_feed(root,NOW)['diagnostics']['resumed_count']==1

def test_stale_feed_contains_diagnosis_but_no_trading_entitlement():
    records=[]
    with pytest.raises(FeedIncomplete):
        parse(feed(item(), 'Wed, 09 Sep 2026 13:00:00 GMT'),NOW,on_diagnostics=records.append)
    assert records[0]['feed_errors']==['STALE_OR_FUTURE_FEED_TIMESTAMP']

def test_bounded_invalid_evidence_and_total_counter():
    report=inspect_feed(feed('<item><title>not identity</title></item>'*400),NOW)['diagnostics']
    assert report['total_rows']==400 and report['checked_rows']==300
    assert len(report['invalid_rows'])==30 and report['invalid_count']==300
    assert report['truncated'] and not report['complete']

def test_resume_before_halt_is_uncertain_not_closed():
    report=inspect_feed(feed(item(extra='<ResumptionDate>09/09/2026</ResumptionDate><ResumptionTradeTime>08:00:00</ResumptionTradeTime>')),NOW)['diagnostics']
    assert report['invalid_rows'][0]['code']=='RESUMPTION_BEFORE_HALT'
    assert report['resumed_count']==0
def test_empty_feed_without_clock_cannot_claim_current_market_inventory():
    import xml.etree.ElementTree as ET
    from datetime import datetime, timezone
    from nasdaq_halt_feed import inspect_feed
    report = inspect_feed(ET.fromstring('<rss><channel/></rss>'), datetime.now(timezone.utc))['diagnostics']
    assert report['status'] == 'TIME_UNKNOWN'
    assert report['feed_freshness'] == 'UNKNOWN'
    assert report['absence_is_evidence'] is False
    assert report['completeness_scope'] == 'STRUCTURED_ROWS_ONLY'
