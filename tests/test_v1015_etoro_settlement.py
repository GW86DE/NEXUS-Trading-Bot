"""A review resolves one proven closure, never the canceled Sunday request."""
from copy import deepcopy
from types import SimpleNamespace
import json

import pytest

import etoro_settlement_review as review
import etoro_history_accounting as history
import trade_ledger as ledger
from ledger_result import confirmed_net
from test_v1014_etoro_history_accounting import scenario


def ready(monkeypatch, tmp_path):
    tid, record, raw, args, kw = scenario(monkeypatch, tmp_path)
    with ledger._connect() as con:
        con.execute("UPDATE trades SET ownership_status='BOT_VERIFIED' WHERE trade_id=?",(tid,))
    ledger.reconcile_entry_fees_exact(**args)
    history.record_history(**kw)
    common=dict(account=args['account'],paper=True,source='TEST_AUTHENTICATED_PNL')
    review.save_cash(**common,observed_at='2026-09-14T13:31:00Z',cash=1000,positions=['888'])
    review.save_cash(**common,observed_at='2026-09-14T13:33:00Z',cash=1419,positions=[],clean_orders=True)
    return tid,args,kw


def test_review_atomic_repeatable_without_rebooking_quantity_or_old_order(monkeypatch,tmp_path):
    tid,args,kw=ready(monkeypatch,tmp_path)
    before=ledger.trade_detail(tid)
    p=review.preview(tid)
    assert p['proposed_exit_cost']=='1.00' and p['proposed_net']=='18.00'
    assert not p['automatic_release'] and not confirmed_net(before)
    with pytest.raises(ValueError):
        review.confirm(tid,token=p['token'],actor='tester',no_other_cashflows=False)
    with pytest.raises(ValueError):
        review.confirm(tid,token='stale',actor='tester',no_other_cashflows=True)
    assert ledger.trade_detail(tid)==before
    result=review.confirm(tid,token=p['token'],actor='tester',no_other_cashflows=True)
    assert result['updated']==1 and result['quality']=='USER_CONFIRMED'
    assert review.confirm(tid,token=p['token'],actor='tester',no_other_cashflows=True)['updated']==0
    after=ledger.trade_detail(tid)
    assert confirmed_net(after) and after['netto_pnl']==18 and after['gebuehren']==2
    for k in ('menge','entry_order_id','exit_order_id','entry_fill_ids_json','exit_fill_ids_json','ausgestiegen_am'):
        assert before[k]==after[k]
    with ledger._connect() as con:
        audit=con.execute('SELECT * FROM etoro_settlement_reviews').fetchall()
        assert len(audit)==1 and json.loads(audit[0]['before_json'])['netto_pnl'] is None
    history.record_history(**kw)  # new historical metadata/hash, same closure
    assert review.preview(tid)['completed']
    assert review.confirm(tid,token=p['token'],actor='tester',no_other_cashflows=True)['updated']==0
    assert ledger.reconcile_entry_fees_exact(**args)['updated']==0
    assert ledger.trade_detail(tid)['netto_pnl']==18


@pytest.mark.parametrize('fault',['cash_hash','history_hash','identity','price','zero_quantity','currency','partial','other_trade','native_conflict'])
def test_incomplete_or_changed_evidence_never_approves(monkeypatch,tmp_path,fault):
    tid,args,kw=ready(monkeypatch,tmp_path)
    p=review.preview(tid)
    with ledger._connect() as con:
        if fault=='cash_hash':con.execute("UPDATE etoro_cash_receipts SET receipt_json=replace(receipt_json,'1419','1420')")
        if fault=='history_hash':con.execute("UPDATE etoro_history_result_receipts SET receipt_json=replace(receipt_json,'105','106')")
        if fault=='identity':con.execute("UPDATE trades SET broker_account_fingerprint='other' WHERE trade_id=?",(tid,))
        if fault=='price':con.execute('UPDATE trades SET ausstieg_preis=106 WHERE trade_id=?',(tid,))
        if fault=='zero_quantity':con.execute('UPDATE trades SET menge=0 WHERE trade_id=?',(tid,))
        if fault=='currency':con.execute("UPDATE trades SET waehrung='EUR' WHERE trade_id=?",(tid,))
        if fault=='partial':con.execute('UPDATE trades SET menge=2 WHERE trade_id=?',(tid,))
        if fault=='native_conflict':con.execute("UPDATE trades SET fee_quality='CONFIRMED',netto_pnl=17,gebuehren=3 WHERE trade_id=?",(tid,))
    if fault=='other_trade':
        other=ledger.trade_open(broker='etoro',symbol='OTHER',paper=True,asset_type='stock',waehrung='USD',
            broker_position_id='other',broker_account_fingerprint=args['account'],entry_order_id='different',
            menge=1,einstieg_preis=10,decision_id=102,critical=True)
        with ledger._connect() as con:con.execute("UPDATE trades SET eingestiegen_am='2026-09-14T13:32:00Z' WHERE trade_id=?",(other,))
    before=ledger.trade_detail(tid)
    with pytest.raises(ValueError):review.confirm(tid,token=p['token'],actor='tester',no_other_cashflows=True)
    assert ledger.trade_detail(tid)==before


def test_new_cash_point_changes_review_token(monkeypatch,tmp_path):
    tid,args,kw=ready(monkeypatch,tmp_path)
    p=review.preview(tid)
    review.save_cash(account=args['account'],paper=True,source='NEW_RECEIPT',
        observed_at='2026-09-14T13:32:30Z',cash=1420,positions=[],clean_orders=True)
    with pytest.raises(ValueError,match='geändert'):
        review.confirm(tid,token=p['token'],actor='tester',no_other_cashflows=True)
    assert not confirmed_net(ledger.trade_detail(tid))


def test_cash_sampler_ignores_floating_prices_and_reuses_existing_response(monkeypatch,tmp_path):
    tid,args,kw=ready(monkeypatch,tmp_path)
    b=SimpleNamespace(paper=True,account_fingerprint=lambda:args['account'])
    data=dict(_snapshot_at='2026-09-14T13:30:00Z',clientPortfolio=dict(
        accountCurrencyId=1,credit=1000,positions=[dict(positionId=888,units=4,profit=1)],
        mirrors=[],orders=[],ordersForOpen=[],ordersForClose=[]))
    assert review.capture_pnl(b,data)
    data['clientPortfolio']['positions'][0]['profit']=20
    data['_snapshot_at']='2026-09-14T13:30:02Z'
    assert review.capture_pnl(b,data) is None
    data['clientPortfolio']['credit']=1419;data['clientPortfolio']['positions']=[]
    assert review.capture_pnl(b,data)
    data['clientPortfolio']['accountCurrencyId']=2
    assert review.capture_pnl(b,data) is None


def test_proceeds_zero_and_ambiguous_proceeds_stay_unclassified(monkeypatch,tmp_path):
    tid,args,kw=ready(monkeypatch,tmp_path)
    for proceeds in (0,419,420):
        review.observe_proceeds(account=args['account'],paper=True,order_id='999',
            raw=dict(orderID=999,proceeds=proceeds,positions=[],accountCurrencyID=1))
    assert not confirmed_net(ledger.trade_detail(tid))
    with ledger._connect() as con:
        rows=con.execute('SELECT receipt_json FROM etoro_proceeds_observations').fetchall()
        assert len(rows)==3 and all(not json.loads(x[0])['fee_scope_confirmed'] for x in rows)


def test_review_resolves_risk_on_original_sale_day_once(monkeypatch,tmp_path):
    import risk_result_recovery as recovery
    from risk_manager import RiskState
    tid,args,kw=ready(monkeypatch,tmp_path)
    risk=RiskState.load(tmp_path/'risk.json')
    row=ledger.trade_detail(tid)
    risk.register_unknown_pnl_at('ledger:'+str(tid),row['ausgestiegen_am'])
    b=SimpleNamespace(name='etoro',ist_paper=lambda:True,kontowaehrung=lambda:'USD',account_fingerprint=lambda:args['account'])
    assert recovery.reconcile(risk,b,account_equity=10000)==[]
    p=review.preview(tid)
    review.confirm(tid,token=p['token'],actor='tester',no_other_cashflows=True)
    assert recovery.reconcile(risk,b,account_equity=10000)==['ledger:'+str(tid)]
    receipt=deepcopy(risk.realized_receipts['ledger:'+str(tid)])
    assert receipt['booked_day']=='2026-09-14'
    assert recovery.reconcile(risk,b,account_equity=10000)==[]
    assert risk.realized_receipts['ledger:'+str(tid)]==receipt


def test_original_pep_diagnostic_replays_cash_review_without_deleting_sunday(monkeypatch,tmp_path):
    from pathlib import Path
    import decision_analytics as da
    monkeypatch.setattr(da,'DB_PATH',tmp_path/'decision_history.sqlite')
    ledger.init_ledger()
    data=json.loads((Path(__file__).parent/'fixtures/pep_20260914_settlement.json').read_text())
    with ledger._connect() as con:
        for row in data['trades']:
            cols=list(row)
            con.execute('INSERT INTO trades ('+','.join(cols)+') VALUES ('+','.join('?' for _ in cols)+')',
                [json.dumps(row[k],ensure_ascii=False) if isinstance(row[k],(list,dict)) else row[k] for k in cols])
        con.execute('CREATE TABLE etoro_history_result_receipts (trade_id INTEGER,receipt_hash TEXT,observed_at TEXT,receipt_json TEXT,PRIMARY KEY(trade_id,receipt_hash))')
        for row in data['history_receipts']:
            con.execute('INSERT INTO etoro_history_result_receipts VALUES (?,?,?,?)',
                [review.encoded(row[k]) if isinstance(row[k],dict) else row[k] for k in ('trade_id','receipt_hash','observed_at','receipt_json')])
    before=ledger.trade_detail(54)
    p=review.preview(54)
    assert p['position_id']=='3597440106'
    assert review.amount(p['gross_proceeds'])==review.amount('15106.31')
    assert p['cash_delta']=='15105.31'
    assert p['entry_cost']=='1.0' and p['proposed_exit_cost']=='1.00'
    assert p['proposed_net']=='242.16' and not confirmed_net(before)
    assert review.confirm(54,token=p['token'],actor='fixture-reviewer',no_other_cashflows=True)['updated']==1
    after=ledger.trade_detail(54)
    assert after['netto_pnl']==242.16 and after['menge']==109
    assert before['exit_order_id']==after['exit_order_id']==''
    assert review.preview(54)['completed']
