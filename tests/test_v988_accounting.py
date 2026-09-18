"""9.8.8 counterexamples: account gates, remainder inventory, full receipts.

All broker answers here are synthetic. No test reads a user's configuration,
performs a network operation, or modifies an uploaded production database.
"""
from copy import deepcopy
from datetime import datetime,timezone
from decimal import Decimal
import json
import sqlite3
from types import SimpleNamespace as NS
import pytest
import trade_ledger as tl
import okx_closed_reconciliation as cr
import okx_residual_inventory as inv
import okx_accounting as gate
from broker.base import BrokerFehler
from broker.okx import okx_fill_identity
from okx_receipt_math import EvidenceError,prove_order,summarize,number
from test_v987_closed_results import closed
from test_v975_execution_and_repair import engine,persist


def geldschranke_zu(status, base='SUI'):
    """10.7.0: Ein Bestands-/Buchungsbeleg sperrt seinen Coin, nicht die Domaene."""
    return (not status['complete']) or base in set(status.get('blocked_symbols') or [])


def add_quote(x):
    for f in x['buy']:f['tradeQuoteCcy']='USDC'
    with tl._connect() as con:
        for f in x['buy']:
            con.execute("UPDATE trade_entry_fills SET raw_json=? WHERE order_id='100' AND json_extract(raw_json,'$.tradeId')=?",
                (json.dumps(f),str(f['tradeId'])))


@pytest.fixture
def split(closed):
    x=closed;add_quote(x)
    with tl._connect() as con:
        con.execute("UPDATE trades SET ausgestiegen_am=NULL,reconciliation_status='CONFIRMED_OPEN' WHERE trade_id=?",(x['tid'],))
    tl.trade_close(broker='okx',symbol='SUI',ausstieg_preis=.83,menge=124.77,gebuehr=None,
        waehrung='USDC',paper=True,trade_id=x['tid'],broker_position_id='SUI-USDC',
        broker_account_fingerprint='A',entry_order_id='100',exit_order_id='200',
        exit_fill_ids=['okx:A:SUI-USDC:200:9'],critical=True,zeit='2026-09-10T08:00:00Z')
    rest=tl.offene_trades('okx')[0]
    tl.mark_reconciled_closed(rest['trade_id'],grund='Nicht handelbarer OKX-Rundungsrest (Staub)',zeit='2026-09-10T08:00:01Z')
    x['rest_id']=rest['trade_id'];x['row']=tl.trade_detail(x['tid'])
    return x


def test_missing_json_risk_counter_cannot_open_buy_gate(closed):
    # 10.7.0: Das unbekannte Ergebnis vom 10.09. sperrt heute nicht mehr
    # (Ablauf TAGESRESET), bleibt aber als offener Beleg gefuehrt und wird
    # unveraendert in den Risikozustand uebernommen.
    zustand = gate.status('A','DEMO')
    assert zustand['complete'] is True
    assert [g['kind'] for g in zustand['expired']] == ['RESULT']
    assert zustand['gaps'][0]['sperrt'] is False and zustand['gaps'][0]['ablauf'] == 'TAGESRESET'
    from risk_manager import RiskState
    state=RiskState();assert state.unknown_pnl_trades_today==0
    before=gate.status('A','DEMO')
    gate.sync_unknown_results(state,closed['broker'])
    assert 'ledger:'+str(closed['tid']) in state.realized_receipts
    assert state.lifetime_unknown_pnl_trades==1
    gate.sync_unknown_results(state,closed['broker'])
    assert state.lifetime_unknown_pnl_trades==1 and gate.status('A','DEMO')==before


@pytest.mark.parametrize('account,environment',[('B','DEMO'),('A','LIVE'),('B','LIVE')])
def test_ledger_gap_never_blocks_a_different_known_domain(closed,account,environment):
    assert gate.status(account,environment)['complete']


@pytest.mark.parametrize('account,environment',[('','DEMO'),('A','UNKNOWN'),('','')])
def test_missing_domain_is_not_trading_ready(closed,account,environment):
    assert not gate.status(account,environment)['complete']


def test_corrupt_schema_fails_closed():
    c=sqlite3.connect(':memory:');c.row_factory=sqlite3.Row;c.execute('CREATE TABLE trades(trade_id INTEGER)')
    assert not gate.status_on_connection(c,'A','DEMO')['complete'];c.close()


def test_buy_reservation_reads_ledger_but_sell_and_other_broker_remain_allowed(closed):
    import execution_lifecycle as life
    kw=dict(account='A',environment='DEMO',instrument='BTC-USDC',quantity=1,request={})
    # 10.7.0: Der offene Beleg gehoert zu SUI. Ein SUI-Kauf ist gesperrt, ein
    # BTC-Kauf, jeder Verkauf und der andere Broker bleiben frei.
    gate.mark_balance_gap(closed['row'],0,closed['row']['menge'])
    with pytest.raises(BrokerFehler,match='SUI'):
        life.reserve(broker='okx',side='BUY',client_id='blocked',**dict(kw,instrument='SUI-USDC'))
    with tl._connect() as con:
        gate.require_tradable(con,'A','DEMO','BTC-USDC')      # anderer Coin: frei
        with pytest.raises(BrokerFehler,match='SUI'):
            gate.require_tradable(con,'A','DEMO','SUI-USDC')
    assert life.reserve(broker='okx',side='SELL',client_id='protect',**kw)
    assert life.reserve(broker='etoro',side='BUY',client_id='etoro',**kw)
    with tl._connect() as con:
        assert not con.execute("SELECT 1 FROM execution_orders WHERE client_id='blocked'").fetchone()


def test_new_gap_between_preparation_and_post_prevents_submission(engine):
    import execution_lifecycle as life
    kw=dict(broker='okx',account='A',environment='DEMO',instrument='BTC-USDC',side='BUY',client_id='new',quantity=1,request={},prepared=True)
    key=life.reserve(**kw)
    p,tid=persist(engine)
    tl.mark_reconciled_closed(tid,grund='Ohne Fillbeleg')
    with pytest.raises(BrokerFehler):
        life.begin_submission(key,{'instId':'BTC-USDC','side':'buy','clOrdId':'new','sz':'1'})
    with tl._connect() as con:
        assert con.execute('SELECT state FROM execution_orders WHERE order_key=?',(key,)).fetchone()[0]=='PREPARED'


def test_split_sale_and_dust_can_be_proven_without_duplicate_fee(split):
    x=split;plan=cr.prepare(x['row'],x['broker'])
    assert len(plan['lineage_snapshot'])==2 and plan['residual']=='0'
    assert cr.apply(x['row'],plan)
    assert inv.classify(x['rest_id'])
    row=tl.trade_detail(x['tid']);rest=tl.trade_detail(x['rest_id'])
    assert rest['accounting_kind']=='RESIDUAL' and rest['netto_pnl'] is None and rest['ausstieg_preis'] is None
    assert len(tl.trade_liste(broker='okx'))==1
    assert not inv.classify(x['rest_id'])
    assert gate.status('A','DEMO')['complete']
    assert row['entry_cost_basis']+rest['entry_cost_basis']==pytest.approx(125.21*.7843)
    assert row['einstieg_gebuehr']+rest['einstieg_gebuehr']==pytest.approx(.438235*.7843)
    with tl._connect() as con:
        assert con.execute('SELECT count(*) FROM okx_entry_allocations').fetchone()[0]==2
        original=json.loads(con.execute('SELECT original_json FROM okx_residual_inventory').fetchone()[0])
        assert original['accounting_kind']=='TRADE'
    public=inv.public_inventory(broker='okx');assert len(public)==1
    assert public[0]['quantity_text']=='0,001765' and not inv.public_inventory(broker='etoro')


def test_dust_label_without_broker_sell_proof_is_not_a_waiver(split):
    with pytest.raises(EvidenceError,match='Verkaufs'):
        inv.classify(split['rest_id'])
    # 10.7.0: Das Label allein ist weiterhin kein Beweis -- die beiden Zeilen
    # bleiben als offene Ergebnisbelege gefuehrt. Sperren tun sie nur am
    # Verkaufstag (hier 10.09.2026), danach nicht mehr.
    zustand = gate.status('A','DEMO')
    assert sorted(g['kind'] for g in zustand['gaps']) == ['RESULT','RESULT']
    assert all(g['ablauf'] == 'TAGESRESET' for g in zustand['gaps'])
    assert zustand['complete']


def test_unknown_regular_close_is_not_relabelled_as_dust(split):
    x=split;cr.apply(x['row'],cr.prepare(x['row'],x['broker']))
    with tl._connect() as con:
        con.execute("UPDATE trades SET exit_grund='Extern verschwunden' WHERE trade_id=?",(x['rest_id'],))
    with pytest.raises(EvidenceError,match='kein dokumentierter'):
        inv.classify(x['rest_id'])


@pytest.mark.parametrize('column,value',[('account','B'),('environment','LIVE'),('instrument','SUI-EUR'),('entry_order_id','wrong'),('quantity','999'),('proof_hash','bad')])
def test_residual_proof_corruption_cannot_open_money_gate(split,column,value):
    x=split;cr.apply(x['row'],cr.prepare(x['row'],x['broker']));inv.classify(x['rest_id'])
    with tl._connect() as con:con.execute(f'UPDATE okx_residual_inventory SET {column}=? WHERE trade_id=?',(value,x['rest_id']))
    assert geldschranke_zu(gate.status('A','DEMO'))


def test_residual_status_flag_without_any_evidence_is_rejected(closed):
    with tl._connect() as con:con.execute("UPDATE trades SET accounting_kind='RESIDUAL' WHERE trade_id=?",(closed['tid'],))
    assert geldschranke_zu(gate.status('A','DEMO'))


def rules(**kw):
    return dict(inst_id='SUI-USDC',lot_size='.01',min_size='.01',observed_balance='.001765',
        checked_at=datetime.now(timezone.utc).isoformat(),**kw)


def test_open_remainder_requires_fresh_minimum_rules_and_correct_balance(split):
    x=split;cr.apply(x['row'],cr.prepare(x['row'],x['broker']))
    with tl._connect() as con:con.execute('UPDATE trades SET ausgestiegen_am=NULL WHERE trade_id=?',(x['rest_id'],))
    with pytest.raises(EvidenceError):inv.classify(x['rest_id'],allow_open=True)
    assert inv.classify(x['rest_id'],allow_open=True,market_rules=rules())


@pytest.mark.parametrize('change',[{'lot_size':'.000001','min_size':'.000001'},
    {'observed_balance':'.0001'},{'inst_id':'SUI-EUR'},
    {'checked_at':'2020-01-01T00:00:00Z'},{'lot_size':'NaN'},{'min_size':'0'}])
def test_open_inventory_never_retired_from_value_or_stale_rules(split,change):
    x=split;cr.apply(x['row'],cr.prepare(x['row'],x['broker']))
    with tl._connect() as con:con.execute('UPDATE trades SET ausgestiegen_am=NULL WHERE trade_id=?',(x['rest_id'],))
    with pytest.raises((EvidenceError,ValueError)):
        inv.classify(x['rest_id'],allow_open=True,market_rules={**rules(),**change})
    assert tl.trade_detail(x['rest_id'])['ausgestiegen_am'] is None


def test_existing_foreign_account_balance_not_added_to_owned_remainder(split):
    x=split;cr.apply(x['row'],cr.prepare(x['row'],x['broker']))
    with tl._connect() as con:con.execute('UPDATE trades SET ausgestiegen_am=NULL WHERE trade_id=?',(x['rest_id'],))
    inv.classify(x['rest_id'],allow_open=True,market_rules={**rules(),'observed_balance':'250'})
    assert inv.public_inventory()[0]['quantity']=='.001765' or Decimal(inv.public_inventory()[0]['quantity'])==Decimal('.001765')


def test_remainder_attached_to_unsplit_close_preserves_entire_entry_quantity(closed):
    x=closed;add_quote(x);cr.apply(x['row'],cr.prepare(x['row'],x['broker']))
    with tl._connect() as con:
        entry,family=inv.entry_from_ledger(con,tl.trade_detail(x['tid']))
    assert Decimal(entry['quantity'])==Decimal('124.771765')
    assert Decimal(str(family[0]['menge']))+Decimal(inv.public_inventory()[0]['quantity'])==Decimal(entry['quantity'])


def test_risk_write_failure_after_ledger_commit_can_recover_once(closed,monkeypatch):
    from risk_manager import RiskState
    import risk_result_recovery as rr
    x=closed;state=RiskState();state.save();x['broker'].kontowaehrung=lambda:'USDC'
    original=state.register_unknown_pnl_at
    monkeypatch.setattr(state,'register_unknown_pnl_at',lambda *a:(_ for _ in ()).throw(OSError('disk')))
    cr.apply(x['row'],cr.prepare(x['row'],x['broker']))
    with pytest.raises(OSError):rr.reconcile(state,x['broker'],account_equity=1000)
    assert tl.trade_detail(x['tid'])['netto_pnl'] is not None
    monkeypatch.setattr(state,'register_unknown_pnl_at',original)
    assert rr.reconcile(state,x['broker'],account_equity=1000)==['ledger:'+str(x['tid'])]
    amount=state.lifetime_realized_pnl
    assert rr.reconcile(state,x['broker'],account_equity=1000)==[] and state.lifetime_realized_pnl==amount


def test_native_profit_does_not_invent_conversion_into_risk_currency(closed):
    from risk_manager import RiskState
    import risk_result_recovery as rr
    x=closed;state=RiskState();state.save();x['broker'].kontowaehrung=lambda:'EUR'
    cr.apply(x['row'],cr.prepare(x['row'],x['broker']))
    assert rr.reconcile(state,x['broker'],account_equity=1000)==[]
    assert state.realized_receipts['ledger:'+str(x['tid'])]['status']=='UNKNOWN'
    assert state.lifetime_realized_pnl==0


def test_unknown_exit_remains_open_and_does_not_register_a_realized_result(engine,monkeypatch):
    from risk_manager import RiskState
    p,tid=persist(engine);state=RiskState();engine.topf.state=state
    engine.bereitschaft=NS(melde=lambda *a:None);engine._gemeldeter_ueberhang=set()
    monkeypatch.setattr(engine,'_externer_verkaufsbeweis',lambda *a:None)
    monkeypatch.setattr(engine,'_letzter_kurs',lambda p:.83)
    engine._position_verschwunden(p,{})
    assert engine.buch.hole('SUI') is p
    row=tl.trade_detail(tid);assert row['ausgestiegen_am'] is None
    assert row['reconciliation_status']=='BROKER_STATE_UNKNOWN'
    assert not state.realized_receipts
    assert geldschranke_zu(gate.status('A','DEMO'))


def test_close_write_error_preserves_position_and_persistent_balance_gap(engine,monkeypatch):
    p,tid=persist(engine)
    engine.bereitschaft=NS(melde=lambda *a:None)
    monkeypatch.setattr(engine,'_externer_verkaufsbeweis',lambda *a:None)
    monkeypatch.setattr(engine,'_letzter_kurs',lambda p:.83)
    # Earlier import-regression tests intentionally reload modules. Inject
    # at the runtime import used by the worker, not a stale collection alias.
    import importlib
    from risk_manager import RiskState
    runtime_ledger=importlib.import_module('trade_ledger')
    engine.topf.state=RiskState()
    called=[]
    def disk_failure(*a,**kw):
        called.append(True)
        raise OSError('disk')
    monkeypatch.setattr(runtime_ledger,'set_reconciliation_status',disk_failure)
    with pytest.raises(OSError):
        engine._position_verschwunden(p,{})
    assert called==[True], 'The real worker must reach the injected failure'
    assert engine.buch.hole('SUI') is p and not tl.trade_detail(tid)['ausgestiegen_am']
    assert engine._accounting_write_fault and geldschranke_zu(gate.status('A','DEMO'))


def test_partial_balance_does_not_modify_ledger_or_protection_anchor(engine):
    p,tid=persist(engine);before=tl.trade_detail(tid);p.protection_algo_id='persisted-old-algo'
    gate.mark_balance_gap(before,20,p.menge)
    assert tl.trade_detail(tid)==before and p.protection_algo_id=='persisted-old-algo'
    assert any(g['kind']=='BALANCE_REDUCTION' for g in gate.status('A','DEMO')['gaps'])
    gate.mark_balance_gap(before,10,p.menge)
    with tl._connect() as con:assert con.execute('SELECT count(*) FROM okx_balance_gaps').fetchone()[0]==1


@pytest.mark.parametrize('value',['NaN','Infinity','-Infinity',None,True,''])
def test_nonfinite_or_missing_financial_input_rejected(value):
    with pytest.raises(EvidenceError):number(value)


def test_empty_order_quote_is_supplemented_only_by_explicit_fill_quote(closed,monkeypatch):
    x=closed;add_quote(x);old=x['broker'].client.order_status
    monkeypatch.setattr(x['broker'].client,'order_status',lambda *a,**kw:{**old(*a,**kw),'tradeQuoteCcy':''})
    plan=cr.prepare(x['row'],x['broker']);assert plan['currency']=='USDC'
    assert cr.apply(x['row'],plan)


@pytest.mark.parametrize('side',['buy','sell'])
def test_both_order_and_fill_missing_currency_still_blocked(closed,monkeypatch,side):
    x=closed;add_quote(x);old=x['broker'].client.order_status
    monkeypatch.setattr(x['broker'].client,'order_status',lambda *a,**kw:{**old(*a,**kw),'tradeQuoteCcy':''})
    for f in x[side if side=='buy' else 'sell']:f.pop('tradeQuoteCcy',None)
    with pytest.raises((EvidenceError,BrokerFehler)):cr.prepare(x['row'],x['broker'])


@pytest.mark.parametrize('change',[{'tradeQuoteCcy':'EUR'},{'instId':'SUI-EUR'},{'side':'buy'},
    {'fillPx':'.80'},{'fillSz':'124.76'},{'fee':'0'},{'feeCcy':'SUI'},{'fillTime':'1789027200001'}])
def test_conflicting_duplicate_fill_is_never_deduplicated_as_harmless(closed,change):
    x=closed;dup={**x['sell'][0],**change};x['sell'].append(dup)
    with pytest.raises((EvidenceError,BrokerFehler)):cr.prepare(x['row'],x['broker'])


def test_consistent_cross_endpoint_duplicate_fill_not_counted_twice(closed):
    x=closed;status=x['broker'].client.order_status('SUI-USDC',ord_id='200')
    dup={**x['sell'][0],'billId':'another-transport-record','ts':'1789027205000'}
    p=prove_order(status,[*x['sell'],dup],inst='SUI-USDC',oid='200',side='sell',base='SUI',currency='USDC',account='A')
    assert p['quantity']=='124.77' and len(p['fill_ids'])==1


def test_transaction_rolls_back_claims_receipts_and_money_on_sql_error(closed):
    x=closed;plan=cr.prepare(x['row'],x['broker']);cr.init()
    with tl._connect() as con:
        con.execute("CREATE TRIGGER reject_money BEFORE UPDATE OF netto_pnl ON trades BEGIN SELECT RAISE(ABORT,'forced_write_error'); END")
    with pytest.raises(sqlite3.IntegrityError):cr.apply(x['row'],plan)
    assert tl.trade_detail(x['tid'])==x['row']
    with tl._connect() as con:
        for t in ['okx_closed_result_receipts','trade_native_exit_fills','trade_exit_events','okx_residual_inventory']:
            assert con.execute('SELECT count(*) FROM '+t).fetchone()[0]==0


def test_changed_split_sibling_prevents_stale_allocation(split):
    x=split;plan=cr.prepare(x['row'],x['broker'])
    with tl._connect() as con:con.execute('UPDATE trades SET menge=1 WHERE trade_id=?',(x['rest_id'],))
    with pytest.raises(EvidenceError,match='Allokation'):cr.apply(x['row'],plan)


def test_historical_protection_replacement_preserves_old_id(closed):
    import okx_protection_journal as journal
    x=closed;args=dict(account='A',environment='DEMO',instrument='SUI-USDC',client_id='Pbuy')
    journal.begin(**args,request={'instId':'SUI-USDC'})
    journal.update(**args,state='CONFIRMED',algo_id='old-protection')
    journal.begin(**args,request={'instId':'SUI-USDC','replacement':True})
    journal.update(**args,state='CONFIRMED',algo_id='new-protection')
    tl.set_protection(x['tid'],algo_id='',client_order_id='Pbuy',status='UNKNOWN')
    assert set(cr.historical_algo_ids(tl.trade_detail(x['tid'])))=={'old-protection','new-protection'}
