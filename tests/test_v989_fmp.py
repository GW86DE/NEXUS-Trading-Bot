"""FMP subscription transitions, shared state and evidence counterexamples."""
from copy import deepcopy
from datetime import datetime,timedelta,timezone
import json
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
import pytest

from fmp_service import Store, request, encoded, FMPTarifFehlt, FMPPaused, MAX_BYTES
from fmp_reference import FMPReferenz


class Response:
    def __init__(self,data,status=200,headers=None):
        self.data=data;self.status_code=status;self.headers=headers or {};self.closed=False
        self.content=json.dumps(data).encode()
    def json(self):return self.data
    def close(self):self.closed=True


class Session:
    def __init__(self,*responses):self.responses=list(responses);self.calls=[];self.headers={}
    def get(self,url,**kwargs):
        self.calls.append((url,kwargs))
        if not self.responses:raise AssertionError('unexpected network call')
        response=self.responses.pop(0)
        if isinstance(response,Exception):raise response
        return response


@pytest.fixture
def plan(monkeypatch):
    import live_settings
    state={'fmp_plan':'AUTO','fmp_subscription':'STARTER'}
    monkeypatch.setattr(live_settings,'lies',lambda name:state if name=='news_sources_credentials.json' else {})
    monkeypatch.setattr(live_settings,'fmp_key',lambda:'FAKE-KEY')
    return state


def test_shared_cache_is_restart_safe_and_no_key_in_url(plan):
    response=Response([{'symbol':'ABC','companyName':'ABC'}]);session=Session(response)
    a=Store('FAKE-KEY');b=Store('FAKE-KEY')
    assert request(a,session,'FAKE-KEY','/profile',{'symbol':'ABC'})==request(b,Session(),'FAKE-KEY','/profile',{'symbol':'ABC'})
    assert a.status()['verbraucht']==1 and response.closed
    url,kwargs=session.calls[0]
    assert 'FAKE-KEY' not in url and 'apikey' not in kwargs['params']
    assert kwargs['headers']['apikey']=='FAKE-KEY'
    assert 'FAKE-KEY' not in a.path.read_bytes().decode(errors='ignore')


def test_concurrent_cache_consumers_make_one_request(plan):
    session=Session(Response([{'symbol':'ABC'}]))
    def run(_):return request(Store('FAKE-KEY'),session,'FAKE-KEY','/profile',{'symbol':'ABC'})
    with ThreadPoolExecutor(max_workers=4) as pool:result=list(pool.map(run,range(4)))
    assert len(session.calls)==1 and len(result)==4


def test_free_has_real_shared_250_limit_not_per_instance(plan):
    plan['fmp_plan']='FREE';a=Store('FAKE-KEY')
    with a.db() as con:
        con.executemany('INSERT INTO calls(at,automatic,origin,capability,bytes,done) VALUES(?,?,?,?,?,1)',[(time.time()-61,0,'test','reference',10)]*249)
    token=a.reserve('manual','reference','test');a.finish(token,10)
    with pytest.raises(FMPPaused,match='Tagesbudget'):Store('FAKE-KEY').reserve('manual','reference','test')
    assert a.status()['verbraucht']==250


def test_free_80_automatic_reserves_position_supply(plan):
    plan['fmp_plan']='FREE';a=Store('FAKE-KEY')
    # Distribute across prior minutes but preserve today's usage.
    now=time.time();start=datetime.fromtimestamp(now,timezone.utc).replace(hour=0,minute=0,second=0,microsecond=0).timestamp()
    with a.db() as con:
        con.executemany('INSERT INTO calls(at,automatic,origin,capability,bytes,done) VALUES(?,?,?,?,?,1)',[(max(start,now-600),1,'pulsar','history',20)]*80)
    with pytest.raises(FMPPaused):a.reserve('automatic','history','pulsar')
    token=a.reserve('position','history','held');a.finish(token,20)
    assert a.status()['verbraucht']==81


def test_starter_300_per_sliding_minute(plan):
    a=Store('KEY')
    for _ in range(300):
        token=a.reserve('automatic','reference','test');a.finish(token,0)
    with pytest.raises(FMPPaused,match='Minutenlimit'):Store('KEY').reserve('manual','quote','test')


def test_cancel_reverts_without_erasing_used_counts(plan):
    a=Store('KEY')
    with a.db() as con:
        con.executemany('INSERT INTO calls(at,automatic,origin,capability,bytes,done) VALUES(?,?,?,?,?,1)',[(time.time(),1,'pulsar','news',20)]*270)
    plan['fmp_plan']='FREE'
    assert Store('OTHER-KEY').status()['verbraucht']==270
    with pytest.raises(FMPPaused):a.reserve('position','history','test')


def test_endpoint_denial_does_not_disable_other_data(plan):
    a=Store('KEY');s=Session(Response({'error':'not entitled'},402),Response([{'symbol':'ABC'}]))
    with pytest.raises(FMPTarifFehlt):request(a,s,'KEY','/news/stock',{'symbols':'ABC'})
    assert a.status()['effective']=='STARTER'
    assert request(a,s,'KEY','/profile',{'symbol':'ABC'})
    with pytest.raises(FMPTarifFehlt):request(Store('KEY'),Session(),'KEY','/news/stock',{'symbols':'ABC'})
    assert a.status()['verbraucht']==2


def test_auto_multiple_denials_lower_quotas_and_expiry_allows_reprobe(plan,monkeypatch):
    a=Store('KEY')
    for path in ('/news/stock','/income-statement'):
        with pytest.raises(FMPTarifFehlt):request(a,Session(Response({},402)),'KEY',path)
    assert a.status()['effective']=='FREE'
    assert a.permits('/profile') and not a.permits('/news/stock')
    with a.db() as con:con.execute('UPDATE capabilities SET checked=checked-86401,retry=retry-86401')
    assert a.permits('/news/stock') and a.status()['effective']=='STARTER'


def test_successful_paid_endpoint_does_not_infer_whole_tariff(plan):
    plan['fmp_subscription']='FREE';a=Store('KEY')
    request(a,Session(Response([])),'KEY','/news/stock',purpose='manual')
    assert a.status()['effective']=='FREE'


@pytest.mark.parametrize('status',[402,403])
def test_refused_capability_persists_without_global_auth_failure(plan,status):
    a=Store('KEY')
    with pytest.raises(FMPTarifFehlt):request(a,Session(Response({},status)),'KEY','/income-statement')
    assert Store('KEY').status()['gesperrt'] is False
    assert not Store('KEY').permits('/income-statement')


def test_429_retry_after_and_timeout_do_not_mean_cancel(plan):
    a=Store('KEY')
    with pytest.raises(FMPPaused):request(a,Session(Response({},429,{'Retry-After':'7'})),'KEY','/profile')
    s=a.status();assert 0<s['blocked_until']-time.time()<=7 and s['effective']=='STARTER'
    assert not a.capability('/profile')


def test_provider_key_errors_are_redacted(plan):
    key='VERY-SECRET-TEST';a=Store(key)
    with pytest.raises(RuntimeError) as error:request(a,Session(Response({'Error Message':key},401)),key,'/profile')
    assert key not in str(error.value) and key not in json.dumps(a.status())


def test_rolling_bandwidth_blocks_after_downgrade(plan):
    a=Store('KEY')
    with a.db() as con:
        con.execute('INSERT INTO calls(at,automatic,origin,capability,bytes,done) VALUES(?,?,?,?,?,1)',(time.time()-86400,1,'test','history',501_000_000))
    plan['fmp_plan']='FREE'
    with pytest.raises(FMPPaused,match='Datenvolumen'):a.reserve('manual','history','test')


def test_corrupt_legacy_usage_blocks_instead_of_zeroing(plan,tmp_path):
    (tmp_path/'api_daily_budgets.json').write_text('{broken')
    with pytest.raises(ValueError):Store('KEY').status()


def test_legacy_usage_import_is_once_and_shared_across_keys(plan,tmp_path):
    p=tmp_path/'api_daily_budgets.json';raw=json.loads(p.read_text());raw['budgets']['fmp']['used']=42;p.write_text(json.dumps(raw))
    assert Store('KEY').status()['verbraucht']==42
    raw['budgets']['fmp']['used']=84;p.write_text(json.dumps(raw))
    assert Store('NEWKEY').status()['verbraucht']==42


def test_cached_paid_news_not_served_as_free(plan):
    a=Store('KEY');request(a,Session(Response([])),'KEY','/news/stock')
    plan['fmp_plan']='FREE'
    with pytest.raises(FMPTarifFehlt):request(a,Session(),'KEY','/news/stock')
    assert a.status()['verbraucht']==1


@pytest.mark.parametrize('path',['/historical-chart/15min','/earnings-calendar'])
def test_starter_never_probes_premium_endpoints(plan,path):
    a=Store('KEY')
    with pytest.raises(FMPTarifFehlt):request(a,Session(),'KEY',path)
    assert a.status()['verbraucht']==0


def bars(count=40):
    from zoneinfo import ZoneInfo
    today=datetime.now(ZoneInfo("America/New_York")).date()
    return [{'date':(today-timedelta(days=n)).isoformat(),'symbol':'ABC','open':10,'high':11,'low':9,'close':10,'volume':2000000} for n in range(1,count+1)]


def test_history_shared_different_requested_lengths_and_restart(plan):
    a=FMPReferenz('KEY');s=Session(Response(bars()));a.session=s
    assert len(a.tageskerzen('ABC',20))==20
    b=FMPReferenz('KEY');b.session=Session()
    assert len(b.tageskerzen('ABC',95))==40 and len(s.calls)==1
    assert 'from' in s.calls[0][1]['params'] and 'to' in s.calls[0][1]['params']


def test_history_rejects_unfinished_wrong_symbol_and_invalid_ohlc(plan):
    raw=bars(3);raw[0]['symbol']='OTHER';raw[1]['high']=1;raw[2]['date']=datetime.now(timezone.utc).date().isoformat()
    c=FMPReferenz('KEY');c.session=Session(Response(raw))
    with pytest.raises(RuntimeError,match='keine abgeschlossenen'):c.tageskerzen('ABC')
    assert not c.store.cached('history:ABC')


def annual_fixture():
    end=(datetime.now(timezone.utc).date()-timedelta(days=180)).isoformat()
    filed=(datetime.now(timezone.utc).date()-timedelta(days=120)).isoformat()
    profile={'symbol':'ABC','currency':'USD','cik':'123'}
    base={'symbol':'ABC','reportedCurrency':'USD','cik':'123','period':'FY','date':end,'filingDate':filed,'fiscalYear':end[:4]}
    data={'income':[{**base,'netIncome':100,'revenue':1000}], 'cashflow':[{**base,'netIncome':100,'operatingCashFlow':150}], 'balance':[{**base,'totalDebt':20}]}
    return profile,data


def test_annual_facts_remain_provenanced_but_are_no_score_component(plan):
    # 10.3.0: Finanzdaten sind kein Score-Baustein mehr; die normalisierten
    # Jahresfakten mit Herkunft bleiben fuer Anzeige/Recherche erhalten.
    from fmp_data import normalize_financials
    from pulsar.evidence import evaluate
    from pulsar.financials import choose_financials
    profile,data=annual_fixture();facts=normalize_financials('ABC',profile,data)
    assert facts['available']
    sources=[{'id':'annual','provider':'FMP','kind':'annual_financials','observed_at':time.time(),'data':facts}]
    chosen,detail=choose_financials(sources,'ABC',time.time(),None)
    assert chosen and chosen['profit']==100 and 'Jahres' in detail
    result=evaluate({'symbol':'ABC','sources':sources})
    assert result['score'] is None and not result['eligible']


@pytest.mark.parametrize('field,value',[('cik','999'),('reportedCurrency','EUR'),('symbol','OTHER'),('period','Q1'),('date','2020-01-01')])
def test_annual_mismatches_never_score(plan,field,value):
    from fmp_data import normalize_financials
    p,d=annual_fixture();d['cashflow'][0][field]=value
    assert not normalize_financials('ABC',p,d)['available']


def test_financial_same_period_contradiction_remains_open(plan):
    from fmp_data import normalize_financials
    from pulsar.financials import choose_financials
    p,d=annual_fixture();facts=normalize_financials('ABC',p,d);latest=facts['latest']
    start=(datetime.fromisoformat(latest['end'])-timedelta(days=364)).date().isoformat()
    fact={'start':start,'end':latest['end'],'filed':latest['filed'],'unit':'USD','value':999}
    sec={'symbol':'ABC','cik':123,'symbol_identity_verified':True,'net_income':fact,'operating_cashflow':{**fact,'value':150}}
    sources=[{'provider':'FMP','kind':'annual_financials','observed_at':time.time(),'data':facts}]
    chosen,why=choose_financials(sources,'ABC',time.time(),sec)
    assert chosen is None and 'widersprechen' in why


def test_stale_annual_fetch_never_counts_current(plan):
    from fmp_data import normalize_financials
    from pulsar.financials import annual_valid
    p,d=annual_fixture();facts=normalize_financials('ABC',p,d)
    assert not annual_valid(facts,'ABC',time.time()-3*86400,time.time())


def test_starter_setup_preserves_keys_other_sources_and_usage(tmp_path):
    from fmp_setup import starter
    from credential_store import save_credentials,load_credentials
    p=tmp_path/'news_sources_credentials.json';save_credentials(p,{'fmp_api_key':'FAKE','enabled':{'finnhub':False}})
    usage=(tmp_path/'api_daily_budgets.json').read_bytes();starter(tmp_path);saved=load_credentials(p,{})
    assert saved['fmp_api_key']=='FAKE' and saved['fmp_plan']=='AUTO' and saved['fmp_subscription']=='STARTER'
    assert saved['enabled']=={'fmp':True,'finnhub':False}
    assert (tmp_path/'api_daily_budgets.json').read_bytes()==usage


def test_held_positions_refresh_even_without_top5_or_enabled_pulsar(plan,monkeypatch,tmp_path):
    from pulsar import control,research,worker
    import fmp_reference,fmp_data
    # Default PULSAR is AUS. Held NEXUS positions still need their daily data.
    (tmp_path/'stock_positions.json').write_text(json.dumps({'positionen':[{'symbol':'ABC','menge':2}]}))
    client=FMPReferenz('FAKE-KEY');client.session=Session(Response(bars()),Response([]))
    monkeypatch.setattr(fmp_reference,'client',lambda:client)
    monkeypatch.setattr(fmp_data,'refresh_market_context',lambda c:None)
    assert control.settings()['mode']=='AUS'
    worker.refresh_held_data()
    assert research.cached('fmp:bars:ABC')['data']
    assert client.store.status()['automatic_used']==0
    assert client.store.status()['verbraucht']==2


def test_pulsar_and_nexus_share_profile_and_news_requests(plan,monkeypatch):
    from pulsar import worker,control,research
    from news_sources import MultiSourceNews
    import fmp_reference
    from sec_fundamentals import SecFundamentals
    monkeypatch.setattr(control,'settings',lambda:{'mode':'BEOBACHTEN'})
    monkeypatch.setattr(SecFundamentals,'enabled',lambda s:False)
    client=FMPReferenz('FAKE-KEY')
    client.session=Session(Response([{'symbol':'ABC','currency':'USD','cik':123}]),Response(bars()),
                           Response([{'symbol':'ABC','price':10}]),Response([]),
                           *[Response([]) for _ in range(5)])
    monkeypatch.setattr(fmp_reference,'client',lambda:client)
    profile=client.profil('ABC')
    packet=worker.gather_market('ABC')
    n=MultiSourceNews();n.session=Session()
    assert n._fmp_get('/news/stock',{'symbols':'ABC','limit':20})==[]
    assert packet['profile']==profile and packet['bars']
    urls=[url for url,kw in client.session.calls]
    assert sum(url.endswith('/profile') for url in urls)==1
    assert not any('historical-chart' in url or 'earnings-calendar' in url for url in urls)
    # FMP traffic has its account-wide budget, not a second 200-call choke point.
    assert research.usage_summary()['day'].get('data',0)==0
    assert any(r['origin']=='pulsar' for r in client.store.status()['month_usage'])


def test_free_pulsar_uses_existing_data_sources_without_paid_probes(plan,monkeypatch):
    from pulsar import worker,control
    from sec_fundamentals import SecFundamentals
    import fmp_reference
    plan['fmp_plan']='FREE'
    monkeypatch.setattr(control,'settings',lambda:{'mode':'BEOBACHTEN'})
    monkeypatch.setattr(SecFundamentals,'enabled',lambda s:False)
    client=FMPReferenz('FAKE-KEY')
    client.session=Session(Response([{'symbol':'ABC','currency':'USD'}]),Response(bars()),Response([{'symbol':'ABC','price':10}]))
    monkeypatch.setattr(fmp_reference,'client',lambda:client)
    data=worker.gather_market('ABC')
    assert not data['errors'] and data['bars']
    assert len(client.session.calls)==3
    assert all('/news/' not in u and 'statement' not in u for u,k in client.session.calls)


def test_no_fmp_does_not_hide_sec_financials(plan,monkeypatch):
    from pulsar import worker,control
    from sec_fundamentals import SecFundamentals
    import fmp_reference,live_settings
    monkeypatch.setattr(control,'settings',lambda:{'mode':'BEOBACHTEN'})
    monkeypatch.setattr(live_settings,'fmp_key',lambda:'')
    monkeypatch.setattr(fmp_reference,'client',lambda:FMPReferenz(''))
    monkeypatch.setattr(SecFundamentals,'enabled',lambda s:True)
    monkeypatch.setattr(SecFundamentals,'snapshot',lambda s,symbol:{'symbol':symbol,'available':True})
    data=worker.gather_market('ABC')
    assert any(s['provider']=='SEC' for s in data['sources'])


def test_split_adjustment_requires_full_rebuild(plan):
    c=FMPReferenz('KEY');c.session=Session(Response(bars()))
    c.tageskerzen('ABC');old=c.store.cached('history:ABC',stale=True)
    c.store.save('history:ABC',old['data'],-1)
    corrected=bars(5)
    for row in corrected:
        for field in ('open','high','low','close'):row[field]/=2
    c.session=Session(Response(corrected))
    with pytest.raises(RuntimeError,match='Neuaufbau'):c.tageskerzen('ABC')
    assert c.store.cached('history:ABC',stale=True)['data']['rows']==[]


def test_failed_history_refresh_does_not_renew_old_fetch_time(plan):
    c=FMPReferenz('KEY');c.session=Session(Response(bars()));c.tageskerzen('ABC')
    hit=c.store.cached('history:ABC',stale=True)
    c.store.save('history:ABC',hit['data'],-1,saved=hit['saved']-3600)
    c.session=Session(Response([],500))
    with pytest.raises(RuntimeError):c.tageskerzen('ABC')
    after=c.store.cached('history:ABC',stale=True)
    assert after['saved']==hit['saved']-3600 and after['expires']<time.time()


def test_recent_company_primary_lead_saves_an_extra_gpt_search(monkeypatch):
    from pulsar import sources
    from types import SimpleNamespace
    calls=[]
    monkeypatch.setattr(sources,'sec_documents',lambda *a,**kw:([],[]))
    monkeypatch.setattr(sources,'corporate_documents',lambda symbol,market,search,**kw:([{'id':'primary'}],[]))
    monkeypatch.setattr(sources,'web_research',lambda *a,**kw:pytest.fail('Unnecessary GPT search'))
    monkeypatch.setattr(sources,'classify_event',lambda *a,**kw:{'source_ids':['primary']})
    result=sources.enrich('ABC',{'sources':[{'provider':'FMP','kind':'news','data':[{'url':'https://example.test/press'}]}]},SimpleNamespace())
    assert result['catalyst']['source_ids']==['primary']
    assert not result['search']['ok'] and 'keine zusätzliche' in result['search']['detail']


def test_optional_context_preserves_broker_facts_and_one_gpt_call(monkeypatch):
    import fmp_kontext,second_opinion
    from types import SimpleNamespace
    monkeypatch.setattr(fmp_kontext,'research_context',lambda symbol:{'annual':{'net_income':12}})
    seen=[]
    class Router:
        def frage(self,kind,facts,*a,**kw):
            seen.append(deepcopy(facts));return SimpleNamespace(ok=False,grund='test')
    facts={'symbol':'ABC','preis':123,'menge':2,'stop':120}
    second_opinion.hole(Router(),facts)
    assert len(seen)==1 and seen[0]['preis']==123 and seen[0]['menge']==2
    assert seen[0]['fmp_reference_context']['annual']['net_income']==12
    assert 'fmp_reference_context' not in facts


def test_settings_persist_and_show_local_state_without_requests(monkeypatch,tmp_path):
    from webui import settings_store
    import live_settings
    monkeypatch.setattr(settings_store,'ROOT',tmp_path);monkeypatch.setattr(live_settings,'ROOT',tmp_path)
    live_settings.verwerfe_cache()
    settings_store.save({'news':{'fmp_plan':'AUTO','fmp_subscription':'STARTER','fmp_daily_limit':220,'fmp_api_key':'FAKE'}})
    state=settings_store.snapshot()['news']
    assert state['fmp_plan']=='AUTO' and state['fmp_subscription']=='STARTER'
    assert state['fmp_daily_limit']==220 and state['fmp_api_key_set']
    assert state['fmp_status']['tarif']['effective']=='STARTER'
    settings_store.save({'news':{'fmp_plan':'FREE'}})
    assert settings_store.snapshot()['news']['fmp_status']['tarif']['limit']==220


def test_database_online_migration_preserves_usage_caps_and_cache(plan,tmp_path):
    from settings_migration import _kopiere
    a=Store('KEY');request(a,Session(Response([])),'KEY','/news/stock')
    target=tmp_path/'migrated';target.mkdir()
    _kopiere('fmp_service.sqlite',a.path,target/'fmp_service.sqlite')
    with sqlite3.connect(target/'fmp_service.sqlite') as con:
        assert con.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        assert con.execute('SELECT COUNT(*) FROM calls').fetchone()[0]==1
        assert con.execute('SELECT COUNT(*) FROM cache').fetchone()[0]==1


def test_no_paid_rights_bypass_from_http_redirect(plan):
    a=Store('KEY');s=Session(Response({},302,{'Location':'https://other.test/'}))
    with pytest.raises(RuntimeError,match='302'):request(a,s,'KEY','/profile')
    assert s.calls[0][1]['allow_redirects'] is False
    assert a.capability('/profile')=={}


def test_oversize_stream_is_stopped_and_reservation_kept(plan):
    a=Store('KEY');r=Response({})
    r.iter_content=lambda **kw:iter([b' '*65536]*130)
    with pytest.raises(RuntimeError,match='8 MiB'):request(a,Session(r),'KEY','/profile')
    assert r.closed and a.status()['verbraucht']==1 and a.status()['bandwidth_bytes']>=MAX_BYTES


def test_incomplete_newer_annual_period_does_not_use_old_profit(plan):
    from fmp_data import normalize_financials
    p,d=annual_fixture();older=deepcopy(d)
    for values in older.values():
        for row in values:
            row['date']=(datetime.fromisoformat(row['date'])-timedelta(days=365)).date().isoformat()
            row['fiscalYear']=str(int(row['fiscalYear'])-1)
    d['cashflow'][0]['reportedCurrency']='EUR'
    for key in d:d[key].extend(older[key])
    result=normalize_financials('ABC',p,d)
    assert not result['available'] and result['errors']


def test_optional_endpoint_denials_do_not_downgrade_starter(plan):
    a=Store('KEY')
    for path in ('/news/forex-latest','/news/press-releases'):
        a.mark(path,False,'refused',time.time()+86400)
    assert a.status()['effective']=='STARTER'
