"""Acceptance cases for the reproduced 9.8.0/9.8.1 defects. Offline only."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS
import json
import time
import xml.etree.ElementTree as ET
import pytest
from test_v975_execution_and_repair import engine, persist
from test_v981_okx_price_limits import btc, sell
from test_v976_okx_market_environment import market


@pytest.fixture
def ledger(monkeypatch, tmp_path):
    import decision_analytics as da
    import trade_ledger as tl
    monkeypatch.setattr(da, 'DB_PATH', tmp_path / 'acceptance.sqlite')
    tl.init_ledger()
    return tl


def entry(tl, **changes):
    args = dict(broker='okx', symbol='BTC', menge=1, einstieg_preis=100,
        gebuehr=0, paper=True, external=True, broker_position_id='BTC-USDC',
        broker_account_fingerprint='review-A', entry_order_id='review-entry',
        waehrung='USDC', critical=True)
    args.update(changes)
    return tl.trade_open(**args)


def close(tl, tid, **changes):
    args = dict(broker='okx', symbol='BTC', menge=1, ausstieg_preis=90,
        gebuehr=0, paper=True, trade_id=tid, broker_position_id='BTC-USDC',
        broker_account_fingerprint='review-A', entry_order_id='review-entry',
        waehrung='USDC', critical=True)
    args.update(changes)
    return tl.trade_close(**args)


@pytest.mark.parametrize('quantity', [0, -1, None, '', 'NaN', 'Infinity', float('nan')])
def test_bad_exit_quantity_never_changes_money(ledger, quantity):
    tid = entry(ledger)
    before = ledger.trade_detail(tid)
    with pytest.raises(ledger.LedgerZuordnungUnklar):
        close(ledger, tid, menge=quantity)
    assert ledger.trade_detail(tid) == before


@pytest.mark.parametrize('exact_id', [False, True])
def test_live_sale_cannot_close_demo_trade(ledger, exact_id):
    tid = entry(ledger)
    with pytest.raises(ledger.LedgerZuordnungUnklar):
        close(ledger, tid if exact_id else None, paper=False)
    assert not ledger.trade_detail(tid)['ausgestiegen_am']


def test_identical_entry_ids_can_exist_in_separate_environments(ledger):
    demo = entry(ledger, entry_fill_id='fill')
    live = entry(ledger, paper=False, entry_fill_id='fill')
    assert demo != live
    close(ledger, live, paper=False, exit_order_id='exit', exit_fill_ids=['exit-fill'])
    assert not ledger.trade_detail(demo)['ausgestiegen_am']
    close(ledger, demo, exit_order_id='exit', exit_fill_ids=['exit-fill'])
    assert ledger.trade_detail(demo)['ausgestiegen_am']


def test_closed_entry_replay_without_fill_anchor_does_not_reopen(ledger):
    tid = entry(ledger)
    close(ledger, tid)
    with pytest.raises(ledger.LedgerZuordnungUnklar):
        entry(ledger)
    with ledger._connect() as con:
        assert con.execute('SELECT count(*) FROM trades').fetchone()[0] == 1


def manual_setup(engine, monkeypatch):
    import manual_trade_control as controls
    from broker.base import OrderErgebnis
    p, tid = persist(engine)
    monkeypatch.setattr(engine, '_externer_verkaufsbeweis', lambda *a: {})
    engine.broker.positionen = lambda: [NS(symbol='SUI', quantity=p.menge)]
    engine.broker.execution_quote = lambda *a, **k: {'vwap': .83}
    calls = []
    def nofill(*a, **k):
        calls.append(k)
        return OrderErgebnis(status='canceled', terminal=True, fill_evidence_complete=True,
            order_ids=['200'], client_order_id=k['client_order_id'], paper=True,
            account_fingerprint='A', broker_environment='DEMO')
    engine.broker.schliesse_position = nofill
    cmd = controls.request(tid, 'SELL', confirmation='SELL SUI', actor='test')
    return controls, p, cmd, calls


def test_manual_queue_executes_once_and_recovers_auto_on_no_fill(engine, monkeypatch):
    controls, p, cmd, calls = manual_setup(engine, monkeypatch)
    previous = p.verwaltung
    engine._verarbeite_manuelle_auftraege(engine.broker)
    assert len(calls) == 1
    assert p.verwaltung == previous and p.exit_state == 'RETRY_WAIT'
    assert controls.overview()['commands'][0]['status'] == 'FAILED'
    engine._verarbeite_manuelle_auftraege(engine.broker)
    assert len(calls) == 1


def test_manual_quote_failure_keeps_auto(engine, monkeypatch):
    from broker.base import BrokerFehler
    controls, p, _, calls = manual_setup(engine, monkeypatch)
    previous = p.verwaltung
    def fail(*a, **k):
        raise BrokerFehler('book unavailable')
    engine.broker.execution_quote = fail
    engine._verarbeite_manuelle_auftraege(engine.broker)
    assert p.verwaltung == previous and p.exit_state == 'IDLE' and not calls


def test_reservation_failure_precedes_any_protection_cancel(btc, monkeypatch):
    import execution_lifecycle as lc
    from broker.base import BrokerFehler
    b, t, _ = btc
    calls = []
    monkeypatch.setattr(b, 'storniere_offene_orders', lambda *a, **k: calls.append('cancel'))
    def fail(**k):
        raise OSError('synthetic disk failure')
    monkeypatch.setattr(lc, 'reserve', fail)
    with pytest.raises(BrokerFehler):
        sell(btc)
    assert not calls and not t.orders and not lc.snapshot()


def test_prepared_recovery_invalidates_stale_submitter(ledger):
    import execution_lifecycle as lc
    request = {'instId': 'BTC-USDC', 'side': 'sell', 'clOrdId': 'recover', 'sz': '1'}
    key = lc.reserve(broker='okx', account='A', environment='DEMO', instrument='BTC-USDC',
        side='SELL', client_id='recover', quantity=1, request=request, prepared=True)
    assert lc.abandon_prepared(key)
    with pytest.raises(lc.ExecutionConflict):
        lc.begin_submission(key, request)
    assert not lc.snapshot(active_only=True)


def test_submitting_is_not_released_by_prepared_recovery(ledger):
    import execution_lifecycle as lc
    request = {'instId': 'BTC-USDC', 'side': 'sell', 'clOrdId': 'recover', 'sz': '1'}
    key = lc.reserve(broker='okx', account='A', environment='DEMO', instrument='BTC-USDC',
        side='SELL', client_id='recover', quantity=1, request=request, prepared=True)
    lc.begin_submission(key, request)
    assert not lc.abandon_prepared(key)
    assert len(lc.snapshot(active_only=True)) == 1


def test_unknown_results_block_both_risk_entry_points(tmp_path):
    from risk_manager import RiskState, kaufsperre_grund
    from risk_pots import RiskPot
    state = RiskState()
    state.register_unknown_pnl_trade(trade_id='unknown')
    assert 'Ergebnisabgleich' in kaufsperre_grund(state)
    pot = RiskPot('okx')
    pot.state = state
    assert not pot.darf_kaufen()[0]
    assert not state.trading_halted and not state.equity_guard_halted
    state.register_realized_pnl(-3, 1000, trade_id='unknown')
    assert state.lifetime_unknown_pnl_trades == 0
    assert state.realized_pnl_today == -3
    state.register_realized_pnl(-3, 1000, trade_id='unknown')
    assert state.realized_pnl_today == -3 and state.trades_today == 1


@pytest.mark.parametrize('symbol', ['OR', 'NOW', 'ALL', 'A', 'IT'])
def test_ordinary_words_never_become_stock_news(monkeypatch, symbol):
    import config
    from news_sources import MultiSourceNews, NewsItem, NewsBundle
    monkeypatch.setattr(config, 'NEWS_COMPANY_NAME_MATCH_ENABLED', False)
    source = MultiSourceNews()
    article = NewsItem('GDELT Market', 'A fraud investigation now affects all banks or units; it expands',
                      published_at=datetime.now(timezone.utc))
    source._attach_symbols([article], [symbol])
    source.fetch_market = lambda **k: NewsBundle(items=[article], sources_ok=['GDELT'])
    assert not source._market_items_for_symbol(symbol)[0]


def test_explicit_ticker_and_structured_provider_identity_work(monkeypatch):
    from news_sources import MultiSourceNews, NewsItem
    import config
    monkeypatch.setattr(config, 'NEWS_COMPANY_NAME_MATCH_ENABLED', False)
    source = MultiSourceNews()
    rows = [NewsItem('Finnhub', '$NOW announces earnings'), NewsItem('MASSIVE', 'NYSE:OR earnings')]
    source._attach_symbols(rows, ['NOW', 'OR'])
    assert rows[0].symbols == ['NOW'] and rows[1].symbols == ['OR']


@pytest.mark.parametrize('dt', [None, 'bad', datetime(2026, 9, 9), datetime.now(timezone.utc)+timedelta(days=1)])
def test_unknown_or_future_news_date_is_not_fresh(dt):
    from news_sources import _within_hours
    assert not _within_hours(dt, 48)


def test_repeated_news_signal_is_capped():
    from news_filter import NachrichtenFilter
    _, hits = NachrichtenFilter._bewerte([f'company {i} faces fraud charge {i*i}' for i in range(20)])
    assert len([x for x in hits if x[0] == 'fraud']) == 2


def test_provider_secrets_masked_in_old_status_and_failures(monkeypatch, tmp_path, caplog):
    import news_sources as ns
    monkeypatch.setattr(ns, 'STATUS_FILE', tmp_path/'news.json')
    monkeypatch.setattr(__import__('live_settings'), 'alpha_vantage_key', lambda:'secret-value-123')
    client = ns.MultiSourceNews()
    monkeypatch.setattr(client, 'provider_configuration', lambda:{'Alpha Vantage': True})
    def fail():
        raise RuntimeError('We have detected your API key as secret-value-123 &apikey=secret-value-123')
    b = ns.NewsBundle()
    client._provider_call('Alpha Vantage', fail, b)
    assert 'secret-value-123' not in json.dumps(client.health_snapshot())
    assert 'secret-value-123' not in ns.STATUS_FILE.read_text() + caplog.text + str(b.sources_failed)


def test_retry_after_date_and_seconds_are_honored():
    import requests
    from news_sources import MultiSourceNews
    c = MultiSourceNews()
    response = requests.Response(); response.status_code=429; response.headers['Retry-After']='180'
    assert c._failure_backoff('GDELT', requests.HTTPError('429', response=response)) == 180


def test_fed_is_macro_only_and_undated_entries_are_ignored(monkeypatch):
    from news_sources import MultiSourceNews
    c = MultiSourceNews()
    monkeypatch.setattr(c, 'provider_configuration', lambda:{'Federal Reserve':True})
    xml = b'<rss><channel><item><title>NOW fraud</title></item></channel></rss>'
    monkeypatch.setattr(c, '_public_get', lambda *a,**k: NS(content=xml))
    assert c._fed() == []


def test_nasdaq_structured_identity_and_resumption():
    from nasdaq_halt_feed import parse
    now = datetime(2026,9,9,14,tzinfo=timezone.utc)
    xml = '''<rss xmlns:n="http://www.nasdaqtrader.com/"><channel><item><title>NOT a ticker</title>
      <n:IssueSymbol>NOW</n:IssueSymbol><n:HaltDate>09/09/2026</n:HaltDate><n:HaltTime>09:30:00</n:HaltTime>
      <n:ReasonCode>T1</n:ReasonCode><n:ResumptionDate>09/09/2026</n:ResumptionDate>
      <n:ResumptionTradeTime>{resume}</n:ResumptionTradeTime></item></channel></rss>'''
    assert parse(ET.fromstring(xml.format(resume='10:30:00')), now)[0].symbols == ['NOW']
    assert parse(ET.fromstring(xml.format(resume='09:45:00')), now) == []
    with pytest.raises(ValueError):
        parse(ET.fromstring('<rss><channel><item><title>NOW trading halt</title></item></channel></rss>'), now)


def ai_setup(monkeypatch, tmp_path):
    import ai_control, ai_router
    monkeypatch.setattr(ai_control, 'PATH', tmp_path / 'control.json')
    ai_control.set_mode('AUTO')
    cfg=NS(AI_ROUTER_ENABLED=True, OPENAI_API_KEY='synthetic', AI_LUNA_MAX_CALLS_PER_DAY=40,
           AI_TERRA_MAX_CALLS_PER_DAY=8, AI_MAX_COST_PER_DAY_USD=.5, AI_SECOND_OPINION_TIMEOUT_SECONDS=.05)
    return ai_control, ai_router, ai_router.AIRouter(cfg)


def test_ai_off_blocks_cache_and_http(monkeypatch,tmp_path):
    control, module, router = ai_setup(monkeypatch,tmp_path)
    calls=[]
    monkeypatch.setattr(module.requests, 'post', lambda *a,**k: calls.append(k))
    control.set_mode('OFF')
    assert not router.frage('news_relevanz',{}, {'type':'object'}).ok
    assert not router.aktiv and not calls


@pytest.mark.parametrize('response', [{}, {'output_text':'{}'}, {'output_text':'{"answer":23}'}, {'output_text':'[]'}])
def test_empty_or_schema_invalid_ai_reply_is_not_cached(monkeypatch,tmp_path,response):
    _, module, router = ai_setup(monkeypatch,tmp_path)
    monkeypatch.setattr(module.requests, 'post', lambda *a,**k: NS(content=b'{}',ok=True,status_code=200,json=lambda:response))
    assert not router.frage('news_relevanz',{}, {'type':'object','required':['answer'], 'properties':{'answer':{'type':'string'}}}).ok
    assert not router.cache._daten
    assert router.budget.status()['reserviert_usd'] > 0


def test_second_opinion_wait_is_bounded(monkeypatch,tmp_path):
    _, module, router = ai_setup(monkeypatch,tmp_path)
    def slow(*a,**k):
        time.sleep(.20)
        return NS(content=b'{}',ok=True,status_code=200,json=lambda:{'output_text':'{"answer":"ok"}'})
    monkeypatch.setattr(module.requests,'post',slow)
    start=time.monotonic()
    assert not router.frage('second_opinion',{}, {'type':'object'}).ok
    assert time.monotonic()-start < .18
    time.sleep(.22)  # drain the synthetic worker; it must never populate a cache
    assert not router.cache._daten


def test_budget_reserves_last_slot_once_across_instances(tmp_path):
    from ai_router import AIBudget
    cfg=NS(AI_LUNA_MAX_CALLS_PER_DAY=1, AI_MAX_COST_PER_DAY_USD=.05)
    budgets=[AIBudget(tmp_path/'budget.json',cfg) for _ in range(8)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        values=list(pool.map(lambda b:b.reserviere('luna',.04),budgets))
    assert sum(bool(x[0]) for x in values) == 1
    assert budgets[0].lies()['luna']['anfragen'] == 1
    assert budgets[0].lies()['luna']['kosten'] == .04


def test_budget_bookings_not_lost_across_instances(tmp_path):
    from ai_router import AIBudget
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda _:AIBudget(tmp_path/'budget.json').buche('luna',kosten=.01), range(20)))
    d=AIBudget(tmp_path/'budget.json').lies()
    assert d['luna']['anfragen']==20 and d['luna']['kosten']==.2


def test_corrupt_deleted_and_zero_budget_fail_closed(tmp_path):
    from ai_router import AIBudget
    b=AIBudget(tmp_path/'budget.json');b.buche('luna',kosten=.01)
    b.datei.write_text('{')
    assert not b.reserviere('luna',.001)[0]
    b.datei.unlink()
    assert not AIBudget(b.datei).reserviere('luna',.001)[0]
    z=AIBudget(tmp_path/'zero.json',NS(AI_LUNA_MAX_CALLS_PER_DAY=0, AI_MAX_COST_PER_DAY_USD=.5))
    assert not z.reserviere('luna',.001)[0]


def test_aggregate_fee_result_without_full_entry_chain_cannot_confirm_money(ledger):
    from broker.base import OrderErgebnis
    from risk_manager import RiskState
    import risk_result_recovery as rr
    tid = entry(ledger)
    close(ledger, tid, gebuehr=None, exit_order_id='sale', exit_fill_ids=['exit'])
    state = RiskState()
    state.register_unknown_pnl_trade(trade_id=f'ledger:{tid}')
    result = OrderErgebnis(order_ids=['sale'], terminal=True, fill_evidence_complete=True,
        filled_quantity=1, avg_fill_price=90, fees_quote=2, fill_ids=['exit'],
        account_fingerprint='review-A', broker_environment='DEMO', trade_quote_ccy='USDC')
    broker = NS(name='okx', demo=True, account_fingerprint=lambda:'review-A',
        kontowaehrung=lambda:'USDC', reconcile_exit_evidence=lambda **k:result)
    rr._LAST.clear()
    assert rr.reconcile(state, broker, account_equity=10000) == []
    assert state.realized_pnl_today == 0 and state.lifetime_unknown_pnl_trades == 1
    assert ledger.trade_detail(tid)['fee_quality'] != 'BROKER_CONFIRMED'
    assert rr.reconcile(state, broker, account_equity=10000) == []
    assert state.realized_pnl_today == 0



@pytest.mark.parametrize('account,paper,currency', [('other',True,'USDC'), ('review-A',False,'USDC'), ('review-A',True,'EUR')])
def test_pending_risk_receipt_never_crosses_domain(ledger,account,paper,currency):
    from risk_manager import RiskState
    from risk_result_recovery import reconcile
    tid=entry(ledger); close(ledger,tid)
    state=RiskState();state.register_unknown_pnl_trade(trade_id=f'ledger:{tid}')
    b=NS(name='okx',demo=paper,account_fingerprint=lambda:account,kontowaehrung=lambda:currency)
    assert reconcile(state,b,account_equity=10000)==[]
    assert state.lifetime_unknown_pnl_trades==1 and state.realized_pnl_today==0


def test_confirmed_position_allows_recheck_but_not_delete(engine):
    import okx_reconciliation_actions as actions
    p,tid=persist(engine)
    action=actions.anfordern(tid,'RECHECK',symbol='SUI',actor='test')
    assert action['account']=='A' and action['environment']=='DEMO'
    with pytest.raises(ValueError):
        actions.anfordern(tid,'DELETE_LOCAL',symbol='SUI',actor='test')


def test_recheck_rejects_account_switch_before_any_broker_read(engine):
    import okx_reconciliation_actions as actions
    p,tid=persist(engine)
    actions.anfordern(tid,'RECHECK',symbol='SUI',actor='test')
    calls=[]
    engine.broker.account_fingerprint=lambda:'B'
    engine.broker.positionen=lambda:calls.append('read')
    assert actions.verarbeite(engine,engine.broker)[0]['status']=='FAILED'
    assert not calls and engine.buch.hole('SUI') is p


def test_recovery_missing_order_records_reason_without_unblocking(ledger):
    import execution_lifecycle as life
    key=life.reserve(broker='okx',account='A',environment='DEMO',instrument='BTC-USDC',
        side='SELL',client_id='lost',quantity=1,request={})
    b=NS(account_fingerprint=lambda:'A',demo=True,read_primary_order_status=lambda *a,**k:{})
    life.recover_okx(b)
    row=life.snapshot(active_only=True)[0]
    assert row['order_key']==key and row['last_checked_at']
    assert 'Kein eindeutiger' in row['check_detail'] and not row['terminal']


def test_archive_uses_exact_identity_and_never_interprets_missing_as_cancel():
    from broker.okx import OKXClient
    client=OKXClient.__new__(OKXClient)
    calls=[]
    def request(method,path,**k):
        calls.append((method,path))
        return [{'instId':'BTC-USDC','side':'sell','ordId':'different','clOrdId':'other','state':'filled'},
                {'instId':'BTC-USDC','side':'sell','ordId':'our-order','clOrdId':'ours','state':'canceled'}]
    client.request=request
    assert client.order_history_match('BTC-USDC',cl_ord_id='ours',side='sell')['ordId']=='our-order'
    assert client.order_history_match('BTC-USDC',cl_ord_id='not-found',side='sell')=={}
    assert all(m=='GET' for m,_ in calls)


def test_legacy_self_blocked_manual_request_can_be_requested_again(engine):
    import manual_trade_control as controls
    p,tid=persist(engine)
    cmd=controls.request(tid,'SELL',confirmation='SELL SUI')
    controls.update(cmd['id'],'UNCLEAR','Verkaufsstatus noch unklar')
    p.manuell('webui','Manueller Verkauf ueber NEXUS angefordert')
    p.exit_state='MANUAL_EXIT_PENDING';engine.buch.setze(p)
    engine._verarbeite_manuelle_auftraege(engine.broker)
    assert p.exit_state=='MANUAL' and p.verwaltung=='MANUELL'
    assert controls.overview()['commands'][0]['status']=='FAILED'
    assert controls.request(tid,'SELL',confirmation='SELL SUI')['status']=='PENDING'


def test_interrupted_manual_intention_restores_saved_management(engine):
    import manual_trade_control as controls
    p,tid=persist(engine)
    cmd=controls.request(tid,'SELL',confirmation='SELL SUI')
    controls.claim(cmd['id'])
    data=controls._read(controls.DATEI,{})
    data['commands'][0]['updated_at']=(datetime.now(timezone.utc)-timedelta(minutes=3)).isoformat()
    controls._write(controls.DATEI,data)
    old=p.verwaltung
    p.manual_exit_command_id=cmd['id'];p.manual_exit_previous={'verwaltung':old,'verwaltungsnotiz':'','exit_state':'IDLE'}
    p.manuell('test','before submission');p.exit_state='MANUAL_REQUESTED';engine.buch.setze(p)
    engine._verarbeite_manuelle_auftraege(engine.broker)
    assert p.verwaltung==old and p.exit_state=='IDLE'
    assert controls.overview()['commands'][0]['status']=='FAILED'


def test_budget_is_atomic_across_real_processes(tmp_path):
    import subprocess,sys
    path=tmp_path/'shared.json'
    code="from ai_budget import AIBudget; from types import SimpleNamespace as NS; import sys; b=AIBudget(sys.argv[1], NS(AI_LUNA_MAX_CALLS_PER_DAY=1,AI_MAX_COST_PER_DAY_USD=.05)); print(bool(b.reserviere('luna',.04)[0]))"
    children=[subprocess.Popen([sys.executable,'-c',code,str(path)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True) for _ in range(4)]
    results=[p.communicate(timeout=25) for p in children]
    assert all(p.returncode==0 for p in children), results
    assert sum(out.strip()=='True' for out,err in results)==1
    assert json.loads(path.read_text())['luna']['anfragen']==1


def test_no_calendar_key_and_provider_error_are_not_no_upcoming_dates(monkeypatch,tmp_path):
    import earnings_calendar_status as cal
    import live_settings
    monkeypatch.setattr(live_settings,'alpha_vantage_key',lambda:'')
    assert not cal.status()['available'] and cal.calendar('')==[]
    calls=[]
    def get(*a,**k):
        calls.append(1)
        return NS(raise_for_status=lambda:None,text='{"Information":"Your API key is secret-987 exceeded quota"}')
    monkeypatch.setattr(cal.requests,'get',get)
    monkeypatch.setattr(live_settings,'alpha_vantage_key',lambda:'secret-987')
    assert cal.calendar('secret-987')==[] and not cal.status()['available']
    assert 'secret-987' not in cal._path().read_text()
    assert cal.calendar('secret-987')==[] and len(calls)==1


def test_public_feed_cache_is_shared_between_client_instances(tmp_path):
    from public_news_http import get
    calls=[]
    def fetch(*a,**k):
        calls.append(1)
        return NS(status_code=200,raise_for_status=lambda:None,content=b'<rss/>',headers={'Content-Type':'text/xml'})
    session=NS(get=fetch)
    a=get(session,'https://example.test/rss',root=tmp_path,ttl=60,timeout=2)
    b=get(NS(get=lambda *a,**k:pytest.fail('cache should avoid HTTP')),'https://example.test/rss',root=tmp_path,ttl=60,timeout=2)
    assert a.content==b.content==b'<rss/>' and len(calls)==1


def test_live_news_threshold_changes_behavior_and_strategy_hash(monkeypatch,tmp_path):
    import live_settings as ls
    import strategy_version as sv
    from news_filter import Nachrichtenlage
    monkeypatch.setattr(ls,'ROOT',tmp_path);ls._CACHE.clear()
    first=sv.berechne()
    (tmp_path/'news_rules_settings.json').write_text(json.dumps({'NEWS_BLOCK_SCORE':7}))
    ls._CACHE.clear()
    assert not Nachrichtenlage(symbol='ABC',punkte=6,geprueft=True).kauf_blockiert
    assert sv.berechne()!=first


@pytest.mark.parametrize('fraction', [1, .5])
def test_manual_fill_path_books_quantity_and_risk_exactly_once(engine,monkeypatch,fraction):
    from broker.base import OrderErgebnis
    from broker.okx import OKXInstrument
    import trade_ledger as tl
    from risk_manager import RiskState
    controls,p,cmd,calls=manual_setup(engine,monkeypatch)
    qty=p.menge*fraction; initial=p.menge
    engine._gemeldeter_ueberhang=set()
    state=RiskState()
    pot=engine.topf;pot.state=state;pot.kontowert=10000
    pot.buche_ergebnis=lambda pnl,brutto,trade_id:state.register_realized_pnl(pnl,10000,gross_pnl=brutto,trade_id=trade_id)
    engine.broker.kontowaehrung=lambda:'USDC';engine.broker.quote_ccy='USDC'
    engine.broker.client=NS(instrument=lambda *a:OKXInstrument('SUI-USDC','SUI','USDC','live','.0001','.000001','.01'))
    engine.universum=NS(zustand=NS(hole=lambda *a:NS(inst_id='SUI-USDC')))
    def filled(*a,**k):
        calls.append(k)
        return OrderErgebnis(status='filled' if fraction==1 else 'canceled',terminal=True,fill_evidence_complete=True,
            filled_quantity=qty,gross_filled_quantity=qty,requested_quantity=initial,remaining_quantity=initial-qty,
            avg_fill_price=.83,fees_quote=0,fill_ids=['okx:A:SUI-USDC:200:1'],order_ids=['200'],
            client_order_id=k['client_order_id'],paper=True,account_fingerprint='A',broker_environment='DEMO')
    engine.broker.schliesse_position=filled
    engine._verarbeite_manuelle_auftraege(engine.broker)
    command=controls.overview()['commands'][0]
    assert command['status']==('SUCCEEDED' if fraction==1 else 'PARTIAL')
    rows=tl.entry_lineage('okx','SUI-USDC','100','A',paper=True)
    assert sum(r['menge'] for r in rows if r['ausgestiegen_am'])==pytest.approx(qty)
    assert state.realized_pnl_today==pytest.approx(sum(r['netto_pnl'] for r in rows if r['ausgestiegen_am']))
    if fraction==1:
        assert engine.buch.hole('SUI') is None
    else:
        remaining=engine.buch.hole('SUI')
        assert remaining.menge==pytest.approx(initial-qty) and remaining.broker_schutz
    engine._verarbeite_manuelle_auftraege(engine.broker)
    assert len(calls)==1 and state.trades_today==1


def test_manual_completion_after_crash_does_not_extend_reentry_lock(engine):
    import manual_trade_control as controls
    from test_v975_execution_and_repair import close_ledger
    p,tid=persist(engine)
    cmd=controls.request(tid,'SELL',confirmation='SELL SUI');controls.claim(cmd['id'])
    close_ledger(p,tid,p.menge);engine.buch.entferne('SUI')
    engine._verarbeite_manuelle_auftraege(engine.broker)
    first=controls.overview()
    assert first['commands'][0]['status']=='SUCCEEDED' and len(first['locks'])==1
    controls.update(cmd['id'],'UNCLEAR','simulate interruption after lock receipt')
    engine._verarbeite_manuelle_auftraege(engine.broker)
    assert controls.overview()['locks']==first['locks']
