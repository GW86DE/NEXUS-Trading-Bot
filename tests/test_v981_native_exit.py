from copy import deepcopy
from decimal import Decimal
import json
import sqlite3
from types import SimpleNamespace as NS

import pytest

from okx_external_settlement import native_receipt, record_native_exit
from test_v975_execution_and_repair import engine,persist
from test_v976_okx_market_environment import market


def raw():
    return [dict(instId='SUI-EUR',ordId='manual',tradeId='6',side='sell',fillSz='10.89',
        fillPx='.6839',fee='-.0260668485',feeCcy='EUR',ts='1788875040748'),
        dict(instId='SUI-EUR',ordId='manual',tradeId='7',side='sell',fillSz='113.88',
        fillPx='.6829',fee='-.272190282',feeCcy='EUR',ts='1788875040748')]


def receipt(rows=None,**kw):
    return native_receipt(rows or raw(),**{**dict(account='A',environment='DEMO',entry_instrument='SUI-USDC',
        expected_quantity='124.771765',lot_size='.01',manual_confirmed=True),**kw})


def test_actual_manual_sui_numbers_keep_usdc_entry_and_eur_proceeds_separate():
    r=receipt()
    assert r['gross_proceeds']=='85.216323'
    assert r['net_proceeds']=='84.9180658695'
    assert r['residual']=='0.001765'
    assert r['fees_quote']==pytest.approx(.2982571305)
    assert r['quantity']==124.77 and len(r['fill_ids'])==2
    assert r['native_currency']=='EUR'
    assert 'netto_pnl' not in r and 'profit' not in r


def test_replayed_fills_dedup_but_conflicts_are_rejected():
    assert receipt(raw()+raw())==receipt()
    rows=raw(); changed=dict(rows[0],fillSz='11')
    with pytest.raises(ValueError):receipt(rows+[changed])


@pytest.mark.parametrize('change',[dict(fillSz='200'),dict(fee=None),dict(fillPx='NaN'),
    dict(ordId='other'),dict(instId='SUI-USDC'),dict(tradeId=''),dict(side='buy')])
def test_no_symbol_only_proportional_or_mixed_currency_allocation(change):
    rows=raw();rows[0].update(change)
    with pytest.raises(ValueError):receipt(rows)


def test_base_fee_consumption_is_not_deducted_twice_from_quote_cash():
    rows=[dict(instId='SUI-EUR',ordId='m',tradeId='1',side='sell',fillSz='1',
        fillPx='2',fee='-.01',feeCcy='SUI',ts='1788875040748')]
    r=receipt(rows,expected_quantity='1.01')
    assert r['quantity']==1.01 and r['net_proceeds']=='2'
    assert r['fees_quote']==.02


def test_native_close_is_atomic_exact_and_idempotent(engine):
    import trade_ledger as ledger
    p,tid=persist(engine)
    ev=receipt(expected_quantity=str(p.menge))
    args=dict(entry_order_id=p.order_id,entry_instrument=p.inst_id,account='A',paper=True,
              evidence=ev,residual=float(ev['residual']))
    assert record_native_exit(**args)==tid
    first=ledger.trade_detail(tid)
    assert first['ausstieg_preis'] is None and first['netto_pnl'] is None
    assert first['waehrung']=='USDC' and first['exit_order_id']=='manual'
    assert first['exit_grund']=='Vom Nutzer selbst bei OKX verkauft'
    assert first['protection_status']=='CLOSED'
    assert record_native_exit(**args)==tid
    assert ledger.trade_detail(tid)==first
    with ledger._connect() as c:
        assert c.execute('SELECT COUNT(*) FROM trade_native_exit_fills').fetchone()[0]==2
    with pytest.raises(ValueError):record_native_exit(**{**args,'account':'B'})
    changed=deepcopy(ev); changed['fills'][0]['fillPx']='.7'
    with pytest.raises(ValueError):record_native_exit(**{**args,'evidence':changed})
    assert ledger.trade_detail(tid)==first


def test_conflicting_fill_claim_rolls_back_entire_close(engine):
    import trade_ledger as ledger
    p,tid=persist(engine); ev=receipt(expected_quantity=str(p.menge))
    ledger.init_ledger()
    with ledger._connect() as c:
        c.execute('INSERT INTO trade_native_exit_fills VALUES(?,?,?,?,?)',('okx','A','DEMO',ev['fill_ids'][1],999))
    with pytest.raises(sqlite3.IntegrityError):
        record_native_exit(entry_order_id=p.order_id,entry_instrument=p.inst_id,account='A',paper=True,
                           evidence=ev,residual=float(ev['residual']))
    assert ledger.trade_detail(tid)['ausgestiegen_am'] is None
    with ledger._connect() as c:
        assert c.execute('SELECT COUNT(*) FROM trade_native_exit_fills').fetchone()[0]==1


def test_closed_legacy_false_stop_label_can_be_corrected_without_profit(engine):
    import trade_ledger as ledger
    p,tid=persist(engine)
    ledger.mark_reconciled_closed(tid,grund='Broker-Schutzorder ausgeloest')
    ev=receipt(expected_quantity=str(p.menge))
    record_native_exit(entry_order_id=p.order_id,entry_instrument=p.inst_id,account='A',paper=True,
                       evidence=ev,residual=float(ev['residual']))
    after=ledger.trade_detail(tid); doc=json.loads(after['exit_native_json'])
    assert doc['original']['exit_grund']=='Broker-Schutzorder ausgeloest'
    assert after['netto_pnl'] is None and after['exit_grund']=='Vom Nutzer selbst bei OKX verkauft'


def test_cross_pair_adapter_requires_whole_terminal_order(market,monkeypatch):
    b,_=market
    monkeypatch.setattr(b,'historical_fills',lambda *a,**k:raw())
    monkeypatch.setattr(b.client,'fills',lambda *a,**k:[])
    monkeypatch.setattr(b.client,'order_status',lambda *a,**k:dict(instId='SUI-EUR',ordId='manual',
        side='sell',state='filled',accFillSz='124.77',tradeQuoteCcy='EUR'))
    ev=b.external_exit_evidence(inst_id='SUI-USDC',since='2026-09-07T16:00:06Z',expected_qty=124.771765,
        expected_order_ids=['canceled-own-order'],fremdverkauf_erlauben=True,entry_order_id='entry')
    assert ev['native_currency']=='EUR' and ev['beweisart']=='FREMDVERKAUF'
    assert not ev['manual_confirmed']
    monkeypatch.setattr(b.client,'order_status',lambda *a,**k:dict(instId='SUI-EUR',ordId='manual',
        side='sell',state='filled',accFillSz='125'))
    assert not b.external_exit_evidence(inst_id='SUI-USDC',since='2026-09-07T16:00:06Z',
        expected_qty=124.771765,fremdverkauf_erlauben=True,entry_order_id='entry')


@pytest.mark.parametrize('terminal,complete', [(False,True),(True,False)])
def test_unresolved_own_sell_blocks_foreign_fallback_and_missing_balance_close(engine, terminal, complete):
    from broker.base import OrderErgebnis
    import trade_ledger
    p,tid=persist(engine);p.exit_client_order_id='own';p.exit_state='UNCLEAR'
    engine.broker.reconcile_exit_evidence=lambda **kw:OrderErgebnis(
        order_ids=['own-id'],terminal=terminal,fill_evidence_complete=complete,
        filled_quantity=1,gross_filled_quantity=1,fill_ids=['real-partial'])
    def forbidden(**kw):pytest.fail('Unresolved own sell must be reconciled first')
    engine.broker.external_exit_evidence=forbidden
    assert engine._externer_verkaufsbeweis(p,p.menge)=={}
    report={};engine._position_verschwunden(p,report)
    assert report['buchung_ausstehend']==['SUI']
    assert engine.buch.hole('SUI') is p and p.exit_state=='UNCLEAR'
    assert trade_ledger.trade_detail(tid)['ausgestiegen_am'] is None


def test_archived_fill_is_not_proof_that_an_unknown_order_is_terminal(market,monkeypatch):
    b,_=market;calls=[]
    def unavailable(inst_id,**kw):calls.append(kw);return {}
    monkeypatch.setattr(b.client,'order_status',unavailable)
    monkeypatch.setattr(b,'historical_fills',lambda *a,**kw:[dict(raw()[0],
        instId='SUI-USDC',clOrdId='own')])
    assert b.reconcile_exit_evidence(inst_id='SUI-USDC',client_order_id='own',expected_qty=124.77) is None
    assert calls==[dict(ord_id='',cl_ord_id='own'),dict(ord_id='manual')]


@pytest.mark.parametrize('change',[dict(instId='BTC-USDC'),dict(ordId='other'),dict(clOrdId='other')])
def test_recovered_status_cannot_contradict_requested_order_identity(market,monkeypatch,change):
    from broker.base import BrokerFehler
    b,_=market
    monkeypatch.setattr(b.client,'order_status',lambda *a,**kw:{**dict(
        instId='SUI-USDC',ordId='own',clOrdId='client',side='sell',state='canceled',accFillSz='0'),**change})
    with pytest.raises(BrokerFehler,match='Auftragsidentitaet'):
        b.reconcile_exit_evidence(inst_id='SUI-USDC',client_order_id='client',order_id='own',expected_qty=1)


def test_conflicting_history_and_recent_fill_is_not_overwritten(market,monkeypatch):
    b,_=market;f=dict(raw()[0],instId='SUI-USDC')
    monkeypatch.setattr(b,'historical_fills',lambda *a,**kw:[f])
    monkeypatch.setattr(b.client,'fills',lambda *a,**kw:[dict(f,fillPx='.8')])
    assert not b.external_exit_evidence(inst_id='SUI-USDC',since='2026-09-07T16:00:06Z',
        expected_qty=10.89,expected_order_ids=['manual'])


def test_no_relative_tolerance_can_hide_more_than_a_lot(market,monkeypatch):
    b,_=market;f=dict(raw()[0],instId='SUI-USDC',fillSz='999.5')
    monkeypatch.setattr(b,'historical_fills',lambda *a,**kw:[f])
    monkeypatch.setattr(b.client,'fills',lambda *a,**kw:[])
    assert not b.external_exit_evidence(inst_id='SUI-USDC',since='2026-09-07T16:00:06Z',
        expected_qty=1000,expected_order_ids=['manual'])


def historical_report():
    from test_v975_execution_and_repair import result
    return dict(account_fingerprint='A',expected_account_fingerprint='A',mode='DEMO',
        account_match=True,collection_finished=True,entry_order_id='100',requests=[
        dict(method='GET',ok=True,path='/trade/fills-history',response={'data':result().fills+raw()}),
        dict(method='GET',ok=True,path='/trade/order',response={'data':[dict(ordId='manual',
            instId='SUI-EUR',state='filled',side='sell',accFillSz='124.77',tradeQuoteCcy='EUR')]})],
        streams=[dict(path='/trade/fills-history',params={'instId':iid},complete_within_endpoint=True)
                 for iid in ['SUI-USDC','SUI-EUR']])


@pytest.mark.parametrize('change',[{'mode':'LIVE'},{'account_match':False},
    {'collection_finished':False},{'expected_account_fingerprint':'B'},
    {'entry_order_id':'foreign'},{'streams':[]}])
def test_historical_repair_rejects_wrong_scope_or_incomplete_report(engine,change):
    from Nexus_Externe_Verkaufe import prepare
    import trade_ledger
    _,tid=persist(engine);trade_ledger.mark_reconciled_closed(tid,grund='old')
    before=trade_ledger.trade_detail(tid)
    with pytest.raises(ValueError):
        prepare({**historical_report(),**change},before,'manual','.01',True)
    assert trade_ledger.trade_detail(tid)==before


def test_historical_preview_is_read_only_and_compares_actual_utc_instants(engine):
    from Nexus_Externe_Verkaufe import prepare
    import trade_ledger
    _,tid=persist(engine);trade_ledger.mark_reconciled_closed(tid,grund='old')
    before=trade_ledger.trade_detail(tid)
    # Local 15:00 at +02 is before the 13:44 UTC sell, despite lexical order.
    sample={**before,'eingestiegen_am':'2026-09-08T15:00:00+02:00'}
    r=prepare(historical_report(),sample,'manual','.01',True)
    assert r['manual_confirmed'] and r['net_proceeds']=='84.9180658695'
    assert trade_ledger.trade_detail(tid)==before
    with pytest.raises(ValueError):
        prepare(historical_report(),{**sample,'eingestiegen_am':'2026-09-08T16:00:00+02:00'},'manual','.01',True)


def test_btc_diagnostic_selection_does_not_export_sui_or_other_trade_rows(tmp_path):
    from Nexus_SUI_Diagnose import select_sui,read_database
    items=[{'inst_id':'BTC-USDC','symbol':'BTC'},{'symbol':'SUI'}]
    assert select_sui(items,'BTC')==items[:1]
    db=tmp_path/'probe.sqlite'
    with sqlite3.connect(db) as c:
        c.execute('CREATE TABLE trades(trade_id INTEGER,symbol TEXT)')
        c.executemany('INSERT INTO trades VALUES(?,?)',[(1,'BTC'),(2,'SUI')])
    assert read_database(db,'BTC')['trades']['rows']==[{'trade_id':1,'symbol':'BTC'}]
