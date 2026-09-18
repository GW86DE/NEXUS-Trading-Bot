from types import SimpleNamespace as NS
import json
import pytest
from pulsar import control, positions


def test_costs_and_worst_accepted_price_are_both_inside_pulsar_budget():
    # Gross-only risk is 25 USD and would pass the 0.25% budget.
    with pytest.raises(control.Blocked, match="Risiko"):
        control.check_cost_budget(2.5, 100.3, 90, 10000, .01)
    with pytest.raises(control.Blocked, match="Positionskapital"):
        control.check_cost_budget(2, 100.3, 90, 10000, 1, cash=201)
    control.check_cost_budget(2,100.3,90,10000,1,cash=201.6)
    with pytest.raises(control.Blocked):
        control.check_cost_budget(2,100.3,90,10000,float('nan'))


@pytest.mark.parametrize('status,filled,account,expected',[
    ('CANCELLED',1.,'acct','PARTIAL_TERMINAL'),
    ('PARTIALLY_FILLED',1.,'acct','SUBMITTED'),
    ('CANCELLED',1.,'other','SUBMITTED'),
    ('CANCELLED',4.,'acct','SUBMITTED'),
    ('REJECTED',0.,'acct','REJECTED')])
def test_terminal_partial_requires_exact_native_receipts_and_is_not_resubmitted(status,filled,account,expected):
    import broker_exit_journal as j
    item={'id':'proposal','account':'acct','environment':'DEMO','plan':{'instrument_id':'123'},'execution':{'position_ids':['pid']}}
    with control.transaction() as con:
        con.execute("INSERT INTO pulsar_position_control(proposal_id,partial_state,partial_quantity,partial_orders) VALUES(?,?,?,?)",('proposal','SUBMITTED',3.,'["ord"]'))
    intent,_=j.begin(broker='etoro',account_fingerprint=account,environment='DEMO',instrument_id='123',position_id='pid',quantity=3.)
    j.update(intent['intent_id'],status,broker_order_id='ord',filled_quantity=filled)
    positions.reconcile_partial(item)
    positions.reconcile_partial(item)
    with control.transaction() as con:
        row=con.execute('SELECT * FROM pulsar_position_control WHERE proposal_id=?',('proposal',)).fetchone()
    assert row['partial_state']==expected and row['partial_quantity']==3.
    with pytest.raises(control.Blocked):positions.claim_partial('proposal',3.)


def test_sui_failure_remains_receipt_but_later_exact_success_is_visible(monkeypatch,tmp_path):
    import manual_trade_control as m
    monkeypatch.setenv('TRADINGBOT_TEST_STATE_DIR',str(tmp_path))
    identity=dict(trade_id=45,account_fingerprint='acct',environment='DEMO',instrument='SUI-USD',action='SELL')
    failed={**identity,'id':'old','status':'FAILED','created_at':'2026-09-07T21:35:00Z'}
    success={**identity,'id':'new','status':'SUCCEEDED','created_at':'2026-09-08T11:14:00Z'}
    path=tmp_path/m.DATEI
    path.write_text(json.dumps({'commands':[failed,success]}))
    before=path.read_bytes()
    assert m.overview()['commands'][1]['resolved_by']=='new'
    assert path.read_bytes()==before
    success['account_fingerprint']='other'
    path.write_text(json.dumps({'commands':[failed,success]}))
    assert 'resolved_by' not in m.overview()['commands'][1]


def test_okx_entry_deadline_is_rechecked_after_durable_reservation(monkeypatch):
    from broker.okx import OKXBroker
    from broker.base import BrokerFehler
    import execution_lifecycle as lifecycle
    import freqtrade_candles as c
    import pandas as pd
    now=[pd.Timestamp('2026-09-13T12:05:01Z').timestamp()]
    monkeypatch.setattr(c.time,'time',lambda:now[0])
    calls=[]
    def reserve(**kw):
        calls.append('reserved');now[0]+=600
        return 'key'
    monkeypatch.setattr(lifecycle,'reserve',reserve)
    monkeypatch.setattr(lifecycle,'rejected',lambda key,**kw:calls.append(('rejected',kw['proven'])))
    broker=OKXBroker.__new__(OKXBroker);broker.demo=True
    broker.account_fingerprint=lambda:'acct'
    broker._nexus_submit_context={'strategy_mode':'FREQTRADE_SAMPLE','signal_instrument':'BTC-USD','signal_candle':'2026-09-13T12:00:00Z'}
    broker.client=NS(place_order=lambda _:calls.append('SENT'))
    with pytest.raises(BrokerFehler,match='veraltet'):
        broker._submit_primary_order(dict(instId='BTC-USD',side='buy',clOrdId='intent',sz='1'))
    assert calls==['reserved',('rejected',True)]


def test_corrupt_history_never_disables_stop_or_roi():
    import sqlite3
    import freqtrade_sample_strategy as ft
    from crypto_engine import CryptoEngine
    from datetime import datetime,timezone
    engine=CryptoEngine.__new__(CryptoEngine)
    engine.hub=NS(broker=lambda _:NS())
    engine._entry_fee_pct=lambda _:0
    engine._taker_satz=lambda:0
    engine._instrument=lambda *a:None
    def fail(*a,**kw):raise sqlite3.DatabaseError('corrupt cache')
    engine._freqtrade_history=fail
    position=NS(entry_strategy_mode='FREQTRADE_SAMPLE',strategy_version=ft.STRATEGY_VERSION,
        strategy_parameter_hash=ft.PARAMETER_HASH,eroeffnet_am=datetime.now(timezone.utc).isoformat(),
        einstieg=100,symbol='BTC',inst_id='BTC-USD')
    assert engine._strategy_exit(position,89)==(True,'freqtrade_stop_loss')
    assert engine._strategy_exit(position,105)==(True,'freqtrade_roi_4pct')
