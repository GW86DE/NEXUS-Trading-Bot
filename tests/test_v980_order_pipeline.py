"""Fault injection around real adapters, SQLite, positions and risk receipts."""
from datetime import date, timedelta
from types import SimpleNamespace as NS

import pytest

import execution_lifecycle as lifecycle
from broker.base import BrokerFehler, OrderStatusUnklar, OrderErgebnis
from test_v976_okx_market_environment import market, sui
from test_v975_execution_and_repair import engine, persist


def test_sui_ioc_partial_then_rest_books_only_executed_units(engine,market,monkeypatch):
    import trade_ledger
    from risk_pots import RiskPotManager
    p,tid=persist(engine)
    broker,transport=market
    broker.account_fingerprint=lambda:'A'
    engine.hub=NS(broker=lambda _:broker)
    engine.risiko=RiskPotManager(['okx'])
    engine._gemeldeter_ueberhang=set()
    engine.universum=NS(zustand=NS(hole=lambda *a:None))
    protections=[]
    broker.reconcile_position_protection=lambda _i,q,*a,**k:(protections.append(q) or
        dict(checked=True,protection_confirmed=True,algo_id='rest-protect',algo_client_id='P'))
    broker.kontowaehrung=lambda:'USDC'
    broker.quote_conversion_rate=lambda a,b:1.0 if a==b else None
    request=transport.request
    def matching(method,url,**kwargs):
        if method=='POST' and url.endswith('/trade/order'):
            # Book showed enough depth, but only 40 units remain on arrival.
            transport.depth['DEMO']=40 if not transport.orders else 1000
        return request(method,url,**kwargs)
    transport.request=matching
    assert engine._schliesse(p,.8175,'test-exit')=='PARTIAL'
    rest=engine.buch.hole('SUI')
    assert rest.menge==pytest.approx(124.771765-40)
    assert protections[-1]==pytest.approx(rest.menge)
    rows=lifecycle.snapshot()
    assert rows[0]['state']=='PARTIALLY_FILLED_CANCELED'
    assert rows[0]['accounted']==1 and rows[0]['terminal']==1
    assert transport.orders[0]['ordType']=='ioc'
    first_receipt=engine.topf.state.lifetime_realized_pnl
    transport.depth['DEMO']=1000
    assert engine._schliesse(rest,.8175,'test-exit')=='CLOSED'
    assert len(transport.orders)==2
    assert float(transport.orders[1]['sz'])==pytest.approx(84.77)
    closed=[r for r in trade_ledger.trade_liste(tage=1) if r['ausgestiegen_am']]
    assert sum(r['menge'] for r in closed)==pytest.approx(124.77)
    assert engine.topf.state.lifetime_realized_pnl==pytest.approx(sum(r['netto_pnl'] for r in closed))
    assert engine.topf.state.lifetime_realized_pnl!=first_receipt
    assert all(r['accounted'] for r in lifecycle.snapshot())
    assert sum(r['menge'] for r in trade_ledger.offene_trades('okx'))==pytest.approx(.001765)


def test_http_timeout_and_reload_never_submit_a_second_sell(market):
    broker,transport=market
    original=transport.request
    def lost_response(method,url,**kwargs):
        if method=='POST' and url.endswith('/trade/order'):
            original(method,url,**kwargs)
            import requests
            raise requests.Timeout('response lost after exchange acceptance')
        if method=='GET' and __import__('urllib.parse',fromlist=['urlsplit']).urlsplit(url).path=='/api/v5/trade/order':
            import requests
            raise requests.Timeout('lookup temporarily unavailable')
        return original(method,url,**kwargs)
    transport.request=lost_response
    with pytest.raises(OrderStatusUnklar):broker.schliesse_position(sui(),124.77,.8175,client_order_id='attempt1')
    assert len(transport.orders)==1
    # Rebuild the durable journal's reader; only a broker read may clear it.
    with pytest.raises(OrderStatusUnklar):broker.schliesse_position(sui(),124.77,.8175,client_order_id='attempt2')
    assert len(transport.orders)==1
    transport.request=original
    lifecycle.recover_okx(broker)
    row=lifecycle.snapshot()[0]
    assert row['filled']=='124.77' and row['accounted']==0
    with pytest.raises(OrderStatusUnklar):broker.schliesse_position(sui(),124.77,.8175,client_order_id='attempt3')
    assert len(transport.orders)==1


def test_crash_before_http_leaves_safe_reservation(market,monkeypatch):
    broker,transport=market
    body={'instId':'SUI-USDC','side':'sell','clOrdId':'before','sz':'1'}
    def crash(_body):raise KeyboardInterrupt('process killed')
    monkeypatch.setattr(broker.client,'place_order',crash)
    with pytest.raises(KeyboardInterrupt):broker._submit_primary_order(body)
    assert lifecycle.snapshot()[0]['state']=='SUBMITTING'
    with pytest.raises(OrderStatusUnklar):broker._submit_primary_order({**body,'clOrdId':'after'})
    assert transport.orders==[]


@pytest.mark.parametrize('code',['50004','50001','50013'])
def test_ambiguous_okx_codes_are_not_rejections(market,monkeypatch,code):
    broker,_=market
    monkeypatch.setattr(broker.client,'place_order',lambda _b:(_ for _ in ()).throw(BrokerFehler('OKX '+code)))
    with pytest.raises(OrderStatusUnklar):broker._submit_primary_order({'instId':'SUI-USDC','side':'sell','clOrdId':'c','sz':'1'})
    assert lifecycle.snapshot()[0]['terminal']==0


@pytest.mark.parametrize('fee,ccy,expected_complete,expected_fee',[(None,'USDC',False,None),('-.2','EUR',True,None),('.1','USDC',True,-.1)])
def test_unknown_foreign_fee_and_rebate_stay_distinct(market,monkeypatch,fee,ccy,expected_complete,expected_fee):
    broker,_=market
    monkeypatch.setattr(broker,'order_fills',lambda *a,**k:[dict(tradeId='f',ordId='o',instId='SUI-USDC',side='sell',fillSz='1',fillPx='.8',fee=fee,feeCcy=ccy)])
    r=broker._order_result_from_evidence(meta=broker.client.instrument('SUI-USDC'),
        status=dict(ordId='o',side='sell',state='filled',accFillSz='1',tradeQuoteCcy='USDC'),
        cl_ord_id='c',requested_qty=1,reference_price=.8)
    assert r.fill_evidence_complete is expected_complete
    assert r.fees_quote==expected_fee


def test_base_fee_on_sell_consumes_additional_inventory(market,monkeypatch):
    broker,_=market
    monkeypatch.setattr(broker,'order_fills',lambda *a,**k:[dict(tradeId='f',ordId='o',instId='SUI-USDC',side='sell',fillSz='1',fillPx='.8',fee='-.01',feeCcy='SUI')])
    r=broker._order_result_from_evidence(meta=broker.client.instrument('SUI-USDC'),
        status=dict(ordId='o',side='sell',state='filled',accFillSz='1',tradeQuoteCcy='USDC'),
        cl_ord_id='c',requested_qty=1,reference_price=.8)
    assert r.filled_quantity==pytest.approx(1.01)
    assert r.fees_quote==pytest.approx(.008)


def test_filled_zero_is_not_a_terminal_no_fill_proof(market,monkeypatch):
    broker,_=market
    monkeypatch.setattr(broker,'order_fills',lambda *a,**k:[])
    r=broker._order_result_from_evidence(meta=broker.client.instrument('SUI-USDC'),
        status=dict(ordId='o',state='filled',accFillSz='0'),cl_ord_id='c',requested_qty=1,reference_price=.8)
    assert not r.terminal and not r.fill_evidence_complete


def test_unknown_risk_receipt_survives_restart_and_can_be_completed():
    from risk_manager import RiskState
    risk=RiskState.load();assert risk.register_unknown_pnl_trade('receipt')
    risk=RiskState.load();assert not risk.register_unknown_pnl_trade('receipt')
    assert risk.register_realized_pnl(3,1000,gross_pnl=4,trade_id='receipt')
    assert risk.trades_today==1 and risk.lifetime_unknown_pnl_trades==0
    assert risk.lifetime_realized_pnl==3
    assert not risk.register_realized_pnl(3,1000,gross_pnl=4,trade_id='receipt')


@pytest.mark.parametrize('pnl,gross', [(3,4),(-3,-2)])
def test_late_receipt_does_not_inflate_next_days_profit(monkeypatch,pnl,gross):
    import risk_manager
    from risk_manager import RiskState
    # The machine's date can already be tomorrow while the configured
    # trading timezone is still today. Advance the bot's actual clock; never
    # derive its previous trading day from the host's date.today().
    booked_day=risk_manager._handelstag_heute()
    monkeypatch.setattr(risk_manager,'_handelstag_heute',lambda:booked_day)
    risk=RiskState.load();risk.register_unknown_pnl_trade('receipt')
    assert risk.realized_receipts['receipt']['booked_day']==str(booked_day)
    next_day=booked_day+timedelta(days=1)
    monkeypatch.setattr(risk_manager,'_handelstag_heute',lambda:next_day)
    risk=RiskState.load()
    assert not risk.register_unknown_pnl_trade('receipt')
    assert risk.register_realized_pnl(pnl,1000,gross_pnl=gross,trade_id='receipt')
    assert risk.realized_pnl_today==0 and risk.trades_today==0
    assert risk.gross_profit_today==risk.gross_loss_today==0
    assert risk.net_profit_today==risk.net_loss_today==0
    assert risk.consecutive_losses==0 and risk.cooldown_until is None
    assert risk.current_date==next_day
    assert risk.realized_receipts['receipt']['booked_day']==str(booked_day)
    assert risk.lifetime_realized_pnl==pnl and risk.lifetime_unknown_pnl_trades==0
    assert not risk.register_realized_pnl(pnl,1000,gross_pnl=gross,trade_id='receipt')
    assert RiskState.load().lifetime_realized_pnl==pnl


def test_etoro_two_close_fills_are_accounted_after_both_ledger_commits(monkeypatch,tmp_path):
    from test_v951_etoro_close_broker_hardening import _bound_broker,_open_pnl,POSITION,INSTRUMENT
    import trade_ledger as ledger
    b=_bound_broker(monkeypatch,tmp_path);account=b.account_fingerprint()
    b._resolve=lambda _i:dict(instrumentId=INSTRUMENT,symbol='ADBE')
    b._pnl=lambda **k:_open_pnl()
    b._request=lambda *a,**k:{'orderForClose':{'orderId':'close-1'}}
    b.schliesse_position(NS(name='ADBE'),2,position_ids=[POSITION],instrument_id=str(INSTRUMENT))
    b.close_order_info=lambda _o:dict(orderId='close-1',instrumentId=INSTRUMENT,positions=[
        dict(positionId=POSITION,instrumentId=INSTRUMENT,rate=300,units=1,fees=.1,
             occurred=f'2026-09-08T12:00:0{i}Z',executionId=f'exec{i}') for i in (1,2)])
    b.trade_history_snapshot=lambda *a,**k:dict(rows=[],complete=True)
    fills=[f for f in b.fills() if f.side=='SELL']
    assert len(fills)==2
    assert lifecycle.snapshot()[0]['accounted']==0
    ledger.trade_open(broker='etoro',symbol='ADBE',menge=5,einstieg_preis=290,gebuehr=.5,
        broker_position_id=POSITION,broker_account_fingerprint=account,entry_order_id='entry-1',
        decision_id=123,paper=True)
    for i,f in enumerate(fills):
        ledger.trade_close(broker='etoro',symbol='ADBE',menge=f.quantity,ausstieg_preis=f.price,
            gebuehr=f.explicit_fees,broker_position_id=POSITION,broker_account_fingerprint=account,
            entry_order_id='entry-1',exit_order_id=f.order_id,exit_fill_ids=[f.fill_id],
            paper=True,critical=True)
        assert lifecycle.snapshot()[0]['accounted']==int(i==1)
    assert ledger.offene_trades('etoro')[0]['menge']==3
    for f in fills:
        lifecycle.observe_etoro_exit(f,environment='DEMO',instrument=INSTRUMENT)
    with lifecycle._connect() as con:
        assert con.execute('SELECT COUNT(*) FROM execution_fills').fetchone()[0]==2


def test_etoro_partial_buy_keeps_rest_pending_until_confirmed_cancellation():
    import trade_ledger as ledger
    lifecycle.reserve(broker='etoro',account='A',environment='DEMO',instrument='100',
        side='BUY',client_id='entry-request',quantity=2,request={})
    record=dict(reference_id='entry-request',account_fingerprint='A',paper=True,
        order_ids=['entry-1'],execution_state='PARTIALLY_FILLED',
        fills=[dict(position_id='p1',quantity=1,price=100,execution_time='2026-09-08T12:00:00Z')])
    lifecycle.observe_etoro_entry(record,{})
    assert lifecycle.snapshot()[0]['terminal']==0
    assert lifecycle.snapshot()[0]['accounted']==0
    ledger.trade_open(broker='etoro',symbol='ABC',menge=1,einstieg_preis=100,gebuehr=None,
        broker_position_id='p1',broker_account_fingerprint='A',entry_order_id='entry-1',
        decision_id=123,paper=True)
    assert lifecycle.snapshot()[0]['accounted']==0
    record.update(execution_state='CANCELED_PARTIALLY_FILLED', order_terminal=True)
    lifecycle.observe_etoro_entry(record,{})
    assert lifecycle.snapshot()[0]['terminal']==1
    assert lifecycle.snapshot()[0]['accounted']==1
    assert len(ledger.offene_trades('etoro'))==1
    assert ledger.offene_trades('etoro')[0]['menge']==1
    lifecycle.reserve(broker='etoro',account='A',environment='DEMO',instrument='100',
        side='BUY',client_id='next-request',quantity=1,request={})


@pytest.mark.parametrize('stage',['reserve','accepted'])
def test_storage_failure_never_becomes_permission_to_send_again(market,monkeypatch,stage):
    broker,transport=market
    def broken(*a,**k):raise OSError('disk unavailable')
    body=dict(instId='SUI-USDC',side='sell',clOrdId='disk1',sz='1',ordType='ioc',px='.8')
    with monkeypatch.context() as m:
        m.setattr(lifecycle,stage,broken)
        with pytest.raises((OSError,OrderStatusUnklar)):broker._submit_primary_order(body)
    assert len(transport.orders)==(1 if stage=='accepted' else 0)
    if stage=='accepted':
        assert lifecycle.snapshot()[0]['state']=='SUBMITTING'
        with pytest.raises(OrderStatusUnklar):broker._submit_primary_order({**body,'clOrdId':'disk2'})
        assert len(transport.orders)==1


@pytest.mark.parametrize('fee,ccy,known',[('0','USDC',True),(None,'USDC',False),('-.1','EUR',True)])
def test_external_sell_uses_real_fee_evidence(market,monkeypatch,fee,ccy,known):
    broker,_=market
    fill=dict(instId='SUI-USDC',side='sell',ordId='external',tradeId='x',fillSz='1',fillPx='.8',
              fee=fee,feeCcy=ccy,ts='1788854400000')
    monkeypatch.setattr(broker,'historical_fills',lambda *a,**k:[fill])
    monkeypatch.setattr(broker.client,'fills',lambda *a,**k:[fill])
    evidence=broker.external_exit_evidence(inst_id='SUI-USDC',since='2026-09-01T00:00:00Z',expected_qty=1,expected_order_ids=['external'])
    assert bool(evidence.get('confirmed')) is known
    if known:assert evidence['fees_quote']==(0 if fee=='0' else None)
