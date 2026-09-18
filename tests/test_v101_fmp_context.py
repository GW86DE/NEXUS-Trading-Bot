"""Actual diagnostic financial replay and deterministic provider-free scheduling."""
from copy import deepcopy
from datetime import datetime, timezone
import importlib.util
from pathlib import Path
import pytest
import fmp_data

spec=importlib.util.spec_from_file_location('v101_financial_fixture',Path(__file__).with_name('v101_financial_fixture.py'))
fixture=importlib.util.module_from_spec(spec);spec.loader.exec_module(fixture)
NOW=datetime(2026,9,13,14,1,tzinfo=timezone.utc).timestamp()


def normalize(symbol='MU',data=None):
    raw=deepcopy(data or fixture.DATA[symbol])
    return fmp_data.normalize_financials(symbol,raw.pop('profile'),raw,now=NOW)


def test_real_nbis_gap_cannot_be_reported_as_annual_growth():
    out=normalize('NBIS')
    assert out['available'] is True  # Latest FY is independently valid.
    assert [x['fiscal_year'] for x in out['years']]==['2025','2021']
    assert out['trends']=={}
    assert out['trend_period']['comparable'] is False
    assert out['trend_period']['reason']=='PERIOD_GAP'
    assert out['trend_period']['from']=='2021-12-31'
    assert out['trend_period']['to']=='2025-12-31'
    assert any('2024-12-31' in e for e in out['errors'])


def test_real_mu_mapping_and_neighbor_years_preserve_actual_values():
    out=normalize()
    assert out['schema_version']=='FMP-ANNUAL-2'
    assert out['trend_period']['comparable']
    assert out['trends']['revenue_change']==pytest.approx(.4885110111106685)
    items={m['kind']:m for m in out['metrics']}
    assert items['metrics']['values']['ev_to_ebitda']==pytest.approx(7.667531905688946)
    assert items['ratios']['values']['ev_to_ebitda']==pytest.approx(7.667531905688946)
    assert items['metrics']['values']['net_debt_to_ebitda']==pytest.approx(.30478044559809647)
    assert items['ratios']['values']['interest_coverage']==pytest.approx(20.69182389937107)
    assert items['metrics']['basis']=='HISTORICAL_ANNUAL_NOT_CURRENT_VALUATION'


def test_conflicting_alias_and_foreign_currency_do_not_supply_a_metric():
    data=deepcopy(fixture.DATA['MU'])
    data['metrics'][0]['enterpriseValueOverEBITDA']=999
    out=normalize(data=data)
    assert next(m for m in out['metrics'] if m['kind']=='metrics')['values']['ev_to_ebitda'] is None
    assert 'METRIC_ALIAS_CONFLICT:ev_to_ebitda' in out['errors']
    data['metrics'][0]['reportedCurrency']='EUR'
    assert not any(m['kind']=='metrics' for m in normalize(data=data)['metrics'])


def test_nonpositive_ev_multiple_not_a_cheapness_value():
    data=deepcopy(fixture.DATA['MU']);data['metrics'][0]['evToEBITDA']=-3
    out=normalize(data=data)
    assert next(m for m in out['metrics'] if m['kind']=='metrics')['values']['ev_to_ebitda'] is None


def test_old_derived_cache_is_explicitly_pending_revalidation():
    facts={'schema_version':'FMP-ANNUAL-1','available':True,'trends':{'revenue_change':-0.88}}
    before=deepcopy(facts)
    context=fmp_data.annual_context(facts)
    assert not context['available'] and 'trends' not in context
    assert context['errors']==['DERIVED_SCHEMA_REVALIDATION_REQUIRED']
    assert facts==before


@pytest.mark.parametrize('symbol,flag',[('SPY','isEtf'),('QQQ','isEtf'),('FUND','isFund')])
def test_etf_classification_skips_five_company_endpoints(symbol,flag):
    class Client:
        def profil(self, requested, **kw):
            assert requested==symbol
            return {'symbol':symbol,flag:True}
        def _get(self,*args,**kwargs):pytest.fail('No company endpoints for ETF/Fund')
    out=fmp_data.financials(Client(),symbol)
    assert out['status']=='NOT_APPLICABLE' and not out['available']


class MemoryStore:
    def __init__(self,expiry):self.value={'expires':expiry,'saved':expiry-3600,'data':{}}
    def cached(self,key,**kw):return self.value
    def save(self,key,value,ttl,*,saved=None):
        self.value={'expires':saved+ttl,'saved':saved,'data':deepcopy(value)}


class ContextClient:
    konfiguriert=True
    def __init__(self,clock,expiry):self.clock=clock;self.store=MemoryStore(expiry);self.calls=[]
    def starter(self):return True
    def erlaubt(self,path):return False
    def quote(self,symbol):
        self.calls.append((self.clock[0],symbol))
        return {'price':10,'timestamp':self.clock[0]}


def test_context_due_is_independent_of_holdings_interval(monkeypatch):
    from pulsar import worker
    import fmp_reference
    clock=[3594.]
    client=ContextClient(clock,3600.)
    monkeypatch.setattr(worker.time,'time',lambda:clock[0])
    monkeypatch.setattr(fmp_reference,'client',lambda:client)
    w=worker.Worker();w.last_holdings=3594.
    w._refresh_market_context_if_due()
    assert not client.calls and w.next_market_refresh==3600.
    clock[0]=3599.;w._refresh_market_context_if_due();assert not client.calls
    clock[0]=3600.;w._refresh_market_context_if_due()
    assert client.calls==[(3600.,'EURUSD'),(3600.,'BTCUSD')]
    assert w.next_market_refresh==7200. and w.last_holdings==3594.
    clock[0]=3605.;w._refresh_market_context_if_due();assert len(client.calls)==2


def test_context_one_stale_component_does_not_hide_fresh_crypto(monkeypatch):
    clock=[NOW];client=ContextClient(clock,NOW-1)
    monkeypatch.setattr(fmp_data.time,'time',lambda:clock[0])
    client.quote=lambda symbol:{'price':100,'timestamp':NOW-90000 if symbol=='EURUSD' else NOW}
    due=fmp_data.refresh_market_context(client,now=NOW)
    data=client.store.value['data']
    assert due==NOW+3600
    assert data['components']['EURUSD']['status']=='UNAVAILABLE'
    assert data['components']['BTCUSD']['status']=='OK'
    assert data['quotes'][0]['symbol']=='BTCUSD'
    assert data['errors'][0].startswith('EURUSD:')


@pytest.mark.parametrize('stamp_offset,expected',[(0,'OK'),(-901,'UNAVAILABLE'),(1,'UNAVAILABLE')])
def test_context_quote_freshness_uses_receipt_after_http(monkeypatch,stamp_offset,expected):
    clock=[NOW];client=ContextClient(clock,NOW-1)
    monkeypatch.setattr(fmp_data.time,'time',lambda:clock[0])
    def quote(symbol):
        clock[0]+=2  # Network response arrives after the scheduler's start time.
        return {'price':100,'timestamp':clock[0]+stamp_offset}
    client.quote=quote
    due=fmp_data.refresh_market_context(client,now=NOW)
    data=client.store.value['data']
    assert due==NOW+3600 and data['observed_at']==NOW
    assert data['components']['EURUSD']['status']==expected
    assert data['components']['BTCUSD']['status']==expected
    if expected=='OK':
        assert [row['timestamp'] for row in data['quotes']]==[NOW+2,NOW+4]
        assert data['errors']==[]
    else:
        assert data['quotes']==[] and len(data['errors'])==2


def test_real_annual_errors_and_facts_reach_card_packet(monkeypatch):
    from pulsar import worker
    monkeypatch.setattr(worker.research,'baseline',lambda *a,**kw:{})
    raw=normalize('NBIS')
    attention={'symbol':'NBIS','mentions':5,'mentions_24h_ago':2,'source':'apewisdom','observed_at':NOW,'url':'https://example.test','evidence_id':'attention'}
    source={'id':'nbis-annual','provider':'FMP','kind':'annual_financials','data':raw}
    market={'sources':[source],'profile':fixture.DATA['NBIS']['profile'],'bars':[],'annual_financials':raw,'daily_metrics':{'available':True,'as_of':'2026-09-11','sma200':20}}
    card=worker.build_card(attention,market,now=NOW)
    annual=next(s['data'] for s in card['packet']['sources'] if s.get('kind')=='annual_financials')
    assert annual['errors']==raw['errors'] and annual['trend_period']==raw['trend_period']
    assert card['fmp_context']['annual']['trend_period']['reason']=='PERIOD_GAP'
    assert card['fmp_context']['daily']['sma200']==20
    assert card['packet']['packet_revision']=='pulsar-evidence-2'
    assert source['data']==raw


def news_rows(now=NOW):
    stamp=datetime.fromtimestamp(now,timezone.utc).isoformat()
    return [{'title':f'News {i}','text':'Known actual text','url':f'https://issuer.test/article-{i}',
             'publishedDate':stamp} for i in range(3)]


def test_usable_fmp_news_uses_massive_cache_only(monkeypatch):
    from pulsar import worker
    from massive_service import MassivePaused
    monkeypatch.setattr(worker.time,'time',lambda:NOW)
    monkeypatch.setattr(worker.research,'cached',lambda *a,**kw:None)
    class Client:
        def news(self,symbol,limit,*,cache_only=False):
            assert cache_only is True
            raise MassivePaused('no cache',60,'MASSIVE_CACHE_MISS')
    rows,stamp,status=worker._massive_supplement(Client(),'MU',news_rows())
    assert rows==[] and stamp is None and status=='NOT_REQUESTED_FMP_SUFFICIENT'


def test_massive_cache_keeps_real_fetch_time_and_no_reservation(monkeypatch):
    from pulsar import worker
    monkeypatch.setattr(worker.research,'cached',lambda *a,**kw:None)
    monkeypatch.setattr(worker.time,'time',lambda:NOW)
    class Client:
        def news(self,*a,**kw):assert kw['cache_only'];return [{'title':'Cached fact'}]
        def status(self):return {'transport':{'saved_at':NOW-600,'source':'cache'}}
    rows,stamp,status=worker._massive_supplement(Client(),'MU',news_rows())
    assert stamp==NOW-600 and status=='CACHE'
    assert rows==[{'title':'Cached fact'}]


def test_fmp_duplicate_future_stale_or_title_only_news_do_not_fill_gap():
    from pulsar import worker
    duplicate=news_rows();duplicate[2]['url']=duplicate[0]['url']+'?utm_campaign=test'
    assert not worker._sufficient_fmp_news(duplicate,now=NOW)
    assert not worker._sufficient_fmp_news(news_rows(NOW+60),now=NOW)
    assert not worker._sufficient_fmp_news(news_rows(NOW-73*3600),now=NOW)
    title_only=news_rows();title_only[0]['text']=''
    assert not worker._sufficient_fmp_news(title_only,now=NOW)
    assert worker._sufficient_fmp_news(news_rows(),now=NOW)


def test_massive_data_gap_uses_bounded_existing_request_path(monkeypatch):
    from pulsar import worker
    cached={};seen=[]
    monkeypatch.setattr(worker.research,'cached',lambda k,**kw:cached.get(k))
    def request(key,ttl,fn):
        seen.append((key,ttl));value=fn();cached[key]={'data':value,'saved':NOW};return value
    monkeypatch.setattr(worker,'_request',request)
    class Client:
        def news(self,symbol,limit):assert symbol=='MU' and limit==5;return [{'title':'Fresh'}]
    assert worker._massive_supplement(Client(),'MU',[])[2]=='SUPPLEMENT_FOR_DATA_GAP'
    assert seen==[('massive:news:MU',7200)]


def test_massive_local_rate_pause_keeps_short_retry_and_zero_actual_budget(monkeypatch):
    from pulsar import worker
    from massive_service import MassivePaused
    saved={};settled=[]
    monkeypatch.setattr(worker.research,'cached',lambda *a,**kw:None)
    monkeypatch.setattr(worker.research,'reserve',lambda *a:'token')
    monkeypatch.setattr(worker.research,'settle',lambda *a:settled.append(a))
    monkeypatch.setattr(worker.research,'cache_put',lambda k,v,ttl:saved.update(key=k,data=v,ttl=ttl))
    monkeypatch.setattr(worker.control,'settings',lambda:{'mode':'BEOBACHTEN'})
    def paused():raise MassivePaused('local pacing',15,'MASSIVE_RATE_PAUSED')
    with pytest.raises(MassivePaused):worker._request('massive:news:MU',7200,paused)
    assert saved['ttl']==15 and saved['data']['code']=='MASSIVE_RATE_PAUSED'
    assert settled==[('token',0)]


def test_input_receipt_distinguishes_included_and_omitted_facts():
    from pulsar.analysis import input_sources
    payload={'candidates':[{'symbol':'MU','view_revision':'evidence-view-2','sources':[
        {'id':'r1','provider':'FMP','kind':'bars','full_data_sha256':'originalhash','view_truncated':True,
         'as_of':'2026-09-11','included_rows':1,'data':[{'date':'2026-09-11','volume':10}]},
        {'id':'r2','provider':'FMP','kind':'news','data':{'details_omitted':True}}]}]}
    receipt=input_sources(payload,{'MU':{'r1':'full-source-id','r2':'other-id'}})['MU']
    assert receipt['status']=='INPUT_OF_VALIDATED_RESPONSE'
    assert receipt['sources'][0]['source_id']=='full-source-id'
    assert receipt['sources'][0]['as_of']=='2026-09-11'
    assert receipt['sources'][0]['included'] is True
    assert receipt['sources'][1]['included'] is False


def test_old_precheck_retry_job_is_retained_but_not_replayed(monkeypatch):
    from pulsar import worker
    work={'revision':3,'cards':[{'packet':{'sources':[]}}],'attempts':0,'next_at':NOW-1,'expires_at':NOW+3600}
    before=deepcopy(work)
    monkeypatch.setattr(worker.research,'cached',lambda *a,**kw:{'data':work})
    monkeypatch.setattr(worker.time,'time',lambda:NOW)
    w=worker.Worker()
    monkeypatch.setattr(w,'_review_cards',lambda *a,**kw:pytest.fail('Old inputs cannot start paid review'))
    assert not w._retry_review({'mode':'BEOBACHTEN','revision':3})
    assert work==before


@pytest.mark.parametrize('last,now,expected',[
    ('2026-09-13T12:14:00+00:00','2026-09-13T14:01:00+00:00','2026-09-14T00:14:00+00:00'),
    ('2026-09-14T22:55:00+00:00','2026-09-14T23:01:00+00:00','2026-09-15T11:00:00+00:00'),
    ('2026-09-11T22:55:00+00:00','2026-09-11T23:01:00+00:00','2026-09-12T10:55:00+00:00'),
])
def test_scan_schedule_obeys_real_weekday_weekend_window(last,now,expected):
    from pulsar.worker import scan_schedule
    to_ts=lambda value:datetime.fromisoformat(value).timestamp()
    schedule=scan_schedule(to_ts(last),now=to_ts(now))
    assert schedule['next_scan_at']==to_ts(expected)
    assert schedule['last_scan_started_at']==to_ts(last)
