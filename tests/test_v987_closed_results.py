"""Exact late sale accounting with real SQLite, synthetic GET responses only."""
from copy import deepcopy
from decimal import Decimal
from types import SimpleNamespace as NS
import json
import pytest
import trade_ledger as tl
from broker.okx import OKXBroker, OKXInstrument, okx_fill_identity
from broker.base import BrokerFehler
from test_v975_execution_and_repair import engine, persist, result as entry_result
import okx_closed_reconciliation as cr


@pytest.fixture
def closed(engine, monkeypatch):
    position, tid = persist(engine)
    tl.mark_reconciled_closed(tid, grund='Broker-Schutzorder ausgeloest', zeit='2026-09-10T09:00:00+00:00')
    tl.set_protection(tid, algo_id='protect', status='ACTIVE')
    buy = entry_result().fills
    sell = [dict(instId='SUI-USDC',ordId='200',tradeId='9',side='sell',fillSz='124.77',
                 fillPx='.83',fee='-.1035591',feeCcy='USDC',tradeQuoteCcy='USDC',
                 fillTime='1789027200000',ts='1789027201000')]
    calls=[]
    def status(inst, **kw):
        oid=kw['ord_id']; calls.append(('GET-order',inst,oid))
        rows=buy if oid=='100' else sell
        return dict(instId=inst,ordId=oid,side='buy' if oid=='100' else 'sell',
                    clOrdId='buy' if oid=='100' else 'algo-child',tradeQuoteCcy='USDC',state='filled',
                    accFillSz=str(sum(Decimal(f['fillSz']) for f in rows)))
    def fills(inst, **kw):
        calls.append(('GET-fills',inst,kw.get('ord_id')))
        return deepcopy(buy if kw.get('ord_id')=='100' else sell)
    algo = dict(algoId='protect',instId='SUI-USDC',side='sell',tradeQuoteCcy='USDC',ordIdList=['200'])
    client = NS(hat_zugangsdaten=True,clock_offset_seconds=0,
        instrument=lambda _: OKXInstrument('SUI-USDC','SUI','USDC',lot_size='.01',trade_quote_ccy_list=('USDC',)),
        order_status=status,fills=fills,fills_history_paginated=lambda *a,**k:[],
        algo_order_details=lambda _:deepcopy(algo))
    broker=OKXBroker(client=client,demo=True,quote_ccy='USDC',allowed_quotes=('USDC',))
    broker._account_fingerprint='A'
    monkeypatch.setattr('broker.okx.time.sleep',lambda _:None)
    return dict(row=tl.trade_detail(tid),broker=broker,buy=buy,sell=sell,algo=algo,calls=calls,tid=tid)


def test_closed_sale_recovered_without_reopening_and_dust_is_not_a_loss(closed):
    x=closed; plan=cr.prepare(x['row'],x['broker'])
    assert plan['quantity']==124.77 and Decimal(plan['residual'])==Decimal('.001765')
    assert plan['net']==pytest.approx((.83-.7843)*124.77 - (.438235*.7843*124.77/124.771765) - .1035591)
    assert cr.apply(x['row'],plan)
    row=tl.trade_detail(x['tid'])
    assert row['fee_quality']=='BROKER_CONFIRMED' and row['protection_status']=='TRADE_CLOSED'
    assert row['ausstieg_preis']==.83 and row['exit_order_id']=='200'
    assert row['menge']==124.77 and not tl.offene_trades('okx')
    assert row['ausgestiegen_am']=='2026-09-10T08:00:00+00:00'
    assert len(tl.trade_liste(broker='okx'))==1
    assert not cr.apply(x['row'],plan)
    assert tl.trade_detail(x['tid'])==row
    with tl._connect() as con:
        audit=con.execute('SELECT * FROM okx_closed_result_receipts').fetchone()
        assert json.loads(audit['original_json'])['ausstieg_preis'] is None
        assert Decimal(json.loads(audit['receipt_json'])['residual_cost'])>0
        assert con.execute('SELECT COUNT(*) FROM trade_exit_events').fetchone()[0]==1
    assert all(c[0].startswith('GET') for c in x['calls'])


@pytest.mark.parametrize('change',[{'broker_account_fingerprint':'B'},{'paper':0},
    {'entry_order_id':''},{'ausgestiegen_am':None},{'ownership_status':'UNKNOWN'},
    {'einstieg_preis':.7},{'menge':1},{'ausstieg_preis':.9},
    {'exit_native_json':'{"native_currency":"EUR"}'},
    {'exit_fill_ids_json':'["unrelated"]'}])
def test_unproven_or_conflicting_closed_lineage_not_changed(closed,change):
    x=closed; before=tl.trade_detail(x['tid']); row={**x['row'],**change}
    with pytest.raises((ValueError,BrokerFehler)):
        cr.prepare(row,x['broker'])
    assert tl.trade_detail(x['tid'])==before


@pytest.mark.parametrize('change',[{'algoId':'other'},{'instId':'SUI-EUR'},
    {'side':'buy'},{'ordIdList':[]},{'tradeQuoteCcy':'EUR'}])
def test_old_protection_confirmation_is_not_sale_proof(closed,change):
    x=closed;x['algo'].update(change)
    with pytest.raises(ValueError):cr.prepare(x['row'],x['broker'])
    assert tl.trade_detail(x['tid'])==x['row']


@pytest.mark.parametrize('change',[{'fee':None},{'feeCcy':'BTC'},{'fillSz':'125'},
    {'fillSz':'100'},{'fillPx':'NaN'},{'tradeQuoteCcy':'EUR'},
    {'fillTime':'1000'},{'fillTime':'9999999999999'}])
def test_invalid_fill_fees_quantity_currency_time_remain_open_for_accounting(closed,change):
    x=closed;x['sell'][0].update(change)
    with pytest.raises((ValueError,BrokerFehler,ArithmeticError)):
        cr.prepare(x['row'],x['broker'])
    assert tl.trade_detail(x['tid'])==x['row']


def test_conflicting_claim_rolls_back_financial_projection_and_audit(closed):
    x=closed;plan=cr.prepare(x['row'],x['broker']);cr.init()
    with tl._connect() as con:
        con.execute('INSERT INTO trade_native_exit_fills VALUES(?,?,?,?,?)',('okx','A','DEMO',plan['fill_ids'][0],999))
    with pytest.raises(ValueError,match='beansprucht'):cr.apply(x['row'],plan)
    assert tl.trade_detail(x['tid'])==x['row']
    with tl._connect() as con:
        assert con.execute('SELECT COUNT(*) FROM okx_closed_result_receipts').fetchone()[0]==0


def test_concurrent_trade_change_prevents_stale_plan_apply(closed):
    x=closed;plan=cr.prepare(x['row'],x['broker'])
    with tl._connect() as con:con.execute('UPDATE trades SET notiz=? WHERE trade_id=?',('concurrent',x['tid']))
    with pytest.raises(ValueError,match='veraendert'):cr.apply(x['row'],plan)
    assert tl.trade_detail(x['tid'])['ausstieg_preis'] is None


def test_changed_receipt_cannot_rebook_a_confirmed_result(closed):
    x=closed;plan=cr.prepare(x['row'],x['broker']);cr.apply(x['row'],plan)
    changed=deepcopy(plan);changed['net']+=1
    with pytest.raises(ValueError,match='Abweichender'):cr.apply(x['row'],changed)


def test_same_currency_rebate_is_a_negative_cost(closed):
    x=closed;x['sell'][0]['fee']='.2'
    plan=cr.prepare(x['row'],x['broker'])
    assert plan['exit_fee']==-.2
    assert plan['net']>plan['gross']-plan['entry_fee']


def test_base_sell_fee_is_not_deducted_twice(closed):
    x=closed;x['sell'][0].update(fillSz='124.76',fee='-.01',feeCcy='SUI')
    plan=cr.prepare(x['row'],x['broker'])
    assert plan['quantity']==pytest.approx(124.77)
    assert plan['exit_fee']==pytest.approx(.0083)
    expected_cash=124.76*.83
    allocated_buy_cost=plan['quantity']*.7843+plan['entry_fee']
    assert plan['net']==pytest.approx(expected_cash-allocated_buy_cost)


def test_missing_receipt_is_visible_and_does_not_starve_other_checks(closed):
    x=closed;x['algo']['ordIdList']=[]
    out=cr.run_one(x['broker'])
    assert out['status']=='PENDING' and out['trade_id']==x['tid']
    assert cr.run_one(x['broker'])['status']=='IDLE'
    with tl._connect() as con:
        assert con.execute('SELECT detail FROM okx_closed_result_checks').fetchone()[0]
    assert tl.trade_detail(x['tid'])==x['row']


def test_worker_transport_forbids_every_write_method():
    c=cr._ReadOnlyClient('','','');c.deadline=0;c.calls=0
    for method in ['POST','PUT','DELETE','PATCH']:
        with pytest.raises(BrokerFehler,match='Schreibaktion'):c.request(method,'/trade/order')
    with pytest.raises(BrokerFehler,match='Lesebudget'):c.request('GET','/trade/order')
    c.session.close()


def test_risk_receipt_is_completed_on_actual_trade_day_not_repair_day(monkeypatch):
    from risk_manager import RiskState
    from datetime import date
    state=RiskState(current_date=date(2026,9,11))
    state.register_unknown_pnl_trade('ledger:1')
    assert state.trades_today==1 and state.unknown_pnl_trades_today==1
    assert state.resolve_unknown_result_at('ledger:1',-10,-9,1000,'2026-09-10T08:00:00Z')
    assert state.realized_pnl_today==0 and state.lifetime_realized_pnl==-10
    assert state.unknown_pnl_trades_today==0 and state.trades_today==0
    assert state.lifetime_unknown_pnl_trades==0
    assert state.realized_receipts['ledger:1']['booked_day']=='2026-09-10'
    assert not state.resolve_unknown_result_at('ledger:1',-10,-9,1000,'2026-09-10T08:00:00Z')
    assert state.lifetime_realized_pnl==-10


def test_confirmed_money_cannot_be_replaced_by_different_result():
    from risk_manager import RiskState
    s=RiskState();s.register_unknown_pnl_trade('ledger:1')
    s.resolve_unknown_result_at('ledger:1',-10,-9,1000,'2026-09-10T08:00:00Z')
    with pytest.raises(RuntimeError):s.resolve_unknown_result_at('ledger:1',100,101,1000,'2026-09-10T08:00:00Z')
    assert s.lifetime_realized_pnl==-10


def test_result_recovery_propagates_same_currency_receipt_without_double_count(closed):
    from risk_manager import RiskState
    from risk_result_recovery import reconcile
    x=closed;state=RiskState();key=f"ledger:{x['tid']}"
    state.register_unknown_pnl_trade(key)
    b=x['broker'];b.kontowaehrung=lambda:'USDC'
    cr.run_one(b)
    assert reconcile(state,b,account_equity=1000)==[key]
    first=state.lifetime_realized_pnl
    assert first==pytest.approx(tl.trade_detail(x['tid'])['netto_pnl'])
    assert reconcile(state,b,account_equity=1000)==[]
    assert state.lifetime_realized_pnl==first


def test_background_reader_is_separate_bounded_and_does_not_hold_position_loop(monkeypatch):
    import threading,time
    from broker.okx import OKXClient
    source=OKXClient('dummy-key','dummy-secret','dummy-passphrase',demo=True)
    broker=OKXBroker(client=source,demo=True,quote_ccy='USD',allowed_quotes=('USD',));broker._account_fingerprint='reader-test'
    started=threading.Event();release=threading.Event();seen=[]
    def job(reader):
        seen.append(reader);started.set();release.wait(2)
        return dict(status='IDLE')
    monkeypatch.setattr(cr,'run_one',job)
    cr._JOBS.clear()
    try:
        assert cr.schedule(broker)['status']=='RUNNING'
        assert started.wait(2)
        reader=seen[0]
        assert reader.client.session is not source.session
        assert reader.client._private_limit is source._private_limit
        assert reader.demo and reader.account_fingerprint()=='reader-test'
        with pytest.raises(BrokerFehler):reader.client.request('POST','/trade/order')
        assert cr.schedule(broker)['status']=='RUNNING' and len(seen)==1
        release.set();cr._JOBS[('reader-test',True)]['thread'].join(timeout=2)
        assert cr.schedule(broker)['status']=='IDLE' and len(seen)==1
    finally:
        release.set();source.session.close();cr._JOBS.clear()


@pytest.mark.parametrize('calls,deadline',[(24,99999999999),(0,0)])
def test_reader_budget_rejects_before_transport(calls,deadline):
    reader=cr._ReadOnlyClient.__new__(cr._ReadOnlyClient);reader.calls=calls;reader.deadline=deadline
    with pytest.raises(BrokerFehler,match='Lesebudget'):reader.request('GET','/trade/order')
