from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
import json
from types import SimpleNamespace as NS

import pytest

import execution_lifecycle as lifecycle
from broker.base import OrderErgebnis, OrderStatusUnklar


def reserve(**overrides):
    args=dict(broker='okx',account='account-a',environment='DEMO',instrument='SUI-USDC',
        side='SELL',client_id='sell-1',quantity='124.77',request={'sz':'124.77'})
    args.update(overrides)
    return lifecycle.reserve(**args)


def result(q=124.77, *, terminal=True, complete=True, client='sell-1', order='9001', state='filled'):
    return OrderErgebnis(order_ids=[order],client_order_id=client,
        account_fingerprint='account-a',broker_environment='DEMO',
        filled_quantity=q,gross_filled_quantity=q,requested_quantity=124.77,
        remaining_quantity=max(0,124.77-q),terminal=terminal,status=state,
        fill_evidence_complete=complete,
        fills=[dict(tradeId='fill-1',ordId=order,instId='SUI-USDC',side='sell',
            fillSz=str(q),fillPx='.8175',fee='-.3',feeCcy='USDC')] if q else [])


@pytest.mark.parametrize('quantity',[0,-1,'NaN','Infinity'])
def test_invalid_reservation_has_no_operation(quantity):
    with pytest.raises(lifecycle.ExecutionConflict): reserve(quantity=quantity)
    assert lifecycle.snapshot()==[]


def test_submit_is_reserved_across_threads_and_reloads():
    def attempt(n):
        try:return reserve(client_id=f'sell-{n}')
        except OrderStatusUnklar:return None
    with ThreadPoolExecutor(max_workers=8) as pool:
        accepted=list(pool.map(attempt,range(8)))
    assert len([x for x in accepted if x])==1
    assert len(lifecycle.snapshot(active_only=True))==1


@pytest.mark.parametrize('field,value',[('account','other-account'),('environment','LIVE'),('instrument','BTC-USDC')])
def test_different_domains_remain_independent(field,value):
    reserve();reserve(**{field:value,'client_id':'separate-order' if field=='instrument' else 'sell-1'})
    assert len(lifecycle.snapshot())==2


def test_same_client_cannot_change_identity():
    reserve()
    with pytest.raises(lifecycle.ExecutionConflict): reserve(quantity=2)


def test_unknown_never_expires_or_allows_another_sell():
    key=reserve();lifecycle.rejected(key)
    with lifecycle._connect() as con:
        con.execute("UPDATE execution_orders SET created_at='2020-01-01',updated_at='2020-01-01'")
    with pytest.raises(OrderStatusUnklar): reserve(client_id='new-sell')


def test_proven_no_fill_allows_a_new_attempt():
    reserve();lifecycle.observe(result(0,state='canceled'),broker='okx')
    reserve(client_id='new-sell')


@pytest.mark.parametrize('terminal,expected',[(False,'PARTIALLY_FILLED'),(True,'PARTIALLY_FILLED_CANCELED')])
def test_partial_execution_is_not_filled(terminal,expected):
    r=result(40,terminal=terminal)
    assert lifecycle.result_state(r,124.77)==expected
    reserve();lifecycle.observe(r,broker='okx')
    assert lifecycle.snapshot()[0]['state']==expected


def test_fill_and_local_accounting_are_separate():
    reserve();lifecycle.observe(result(),broker='okx')
    assert lifecycle.snapshot()[0]['terminal']==1
    assert lifecycle.snapshot()[0]['accounted']==0
    with pytest.raises(OrderStatusUnklar): reserve(client_id='another-sell')
    # A claimed receipt without any ledger cannot unlock the position.
    lifecycle.confirm_accounted(broker='okx',account='account-a',environment='DEMO',order_id='9001')
    assert lifecycle.snapshot()[0]['accounted']==0


def test_duplicate_fill_is_idempotent_but_changed_price_is_rejected():
    reserve();r=result();lifecycle.observe(r,broker='okx');lifecycle.observe(r,broker='okx')
    with lifecycle._connect() as con:
        assert con.execute('SELECT COUNT(*) FROM execution_fills').fetchone()[0]==1
    r.fills[0]['fillPx']='.9'
    with pytest.raises(lifecycle.ExecutionConflict): lifecycle.observe(r,broker='okx')
    with lifecycle._connect() as con:
        assert con.execute('SELECT price FROM execution_fills').fetchone()[0]=='0.8175'


def test_stale_status_cannot_erase_terminal_fill():
    reserve();lifecycle.observe(result(),broker='okx')
    lifecycle.observe(result(0,terminal=False,complete=False,state='live'),broker='okx')
    row=lifecycle.snapshot()[0]
    assert row['filled']=='124.77' and row['terminal']==1


@pytest.mark.parametrize('field,value',[('ordId','wrong'),('instId','BTC-USDC'),('side','buy')])
def test_conflicting_fill_rolls_back_entire_observation(field,value):
    reserve();r=result();r.fills[0][field]=value
    with pytest.raises(lifecycle.ExecutionConflict): lifecycle.observe(r,broker='okx')
    assert lifecycle.snapshot()[0]['filled']=='0'


def test_complete_result_needs_matching_fill_sum():
    reserve();r=result();r.fills[0]['fillSz']='1'
    with pytest.raises(lifecycle.ExecutionConflict): lifecycle.observe(r,broker='okx')


def test_ledger_commit_unlocks_exact_full_sell():
    import trade_ledger as ledger
    ledger.trade_open(broker='okx',symbol='SUI',menge=124.77,einstieg_preis=.7843,
        gebuehr=.3437,decision_id=123,broker_account_fingerprint='account-a',
        broker_position_id='SUI-USDC',entry_order_id='buy-1')
    reserve();lifecycle.observe(result(),broker='okx')
    receipt=ledger.trade_close(broker='okx',symbol='SUI',menge=124.77,ausstieg_preis=.8175,
        gebuehr=.3,broker_account_fingerprint='account-a',broker_position_id='SUI-USDC',
        entry_order_id='buy-1',exit_order_id='9001',exit_fill_ids=['fill-1'],critical=True)
    assert receipt
    assert lifecycle.snapshot()[0]['accounted']==1
    reserve(client_id='next-sell')


def test_risk_receipt_survives_midnight(monkeypatch):
    from risk_manager import RiskState
    risk=RiskState.load()
    assert risk.register_realized_pnl(5,1000,gross_pnl=6,trade_id='exact-fill')
    risk.current_date=date.today()-timedelta(days=1);risk.save()
    risk=RiskState.load()
    assert not risk.register_realized_pnl(5,1000,gross_pnl=6,trade_id='exact-fill')
    assert risk.lifetime_realized_pnl==5


def test_changed_risk_receipt_is_not_silently_double_booked():
    from risk_manager import RiskState
    risk=RiskState.load();risk.register_realized_pnl(5,1000,gross_pnl=6,trade_id='exact-fill')
    with pytest.raises(RuntimeError): risk.register_realized_pnl(50,1000,gross_pnl=60,trade_id='exact-fill')
    assert risk.lifetime_realized_pnl==5


def test_upgrade_snapshot_preserves_active_execution_and_fill_evidence(tmp_path):
    import sqlite3
    from settings_migration import _kopiere
    reserve();lifecycle.observe(result(40,terminal=False),broker='okx')
    with lifecycle._connect() as con:
        path=con.execute('PRAGMA database_list').fetchone()[2]
        original=con.execute('SELECT * FROM execution_orders').fetchall()
    destination=tmp_path/'copied.sqlite'
    _kopiere('decision_history.sqlite',__import__('pathlib').Path(path),destination)
    with sqlite3.connect(destination) as copy:
        assert copy.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        assert copy.execute('SELECT * FROM execution_orders').fetchall()==[tuple(r) for r in original]
        assert copy.execute('SELECT quantity FROM execution_fills').fetchone()[0]=='40'
        assert copy.execute('SELECT COUNT(*) FROM execution_events').fetchone()[0]>=2
    with pytest.raises(OrderStatusUnklar):reserve(client_id='after-upgrade')


def test_readonly_sui_export_includes_new_order_lifecycle_without_other_instruments():
    from Nexus_SUI_Diagnose import read_database
    from pathlib import Path
    reserve();lifecycle.observe(result(),broker='okx')
    reserve(instrument='BTC-USDC',client_id='btc-request')
    with lifecycle._connect() as con:
        path=Path(con.execute('PRAGMA database_list').fetchone()[2])
    exported=read_database(path)
    assert len(exported['execution_orders']['rows'])==1
    assert exported['execution_orders']['rows'][0]['instrument']=='SUI-USDC'
    ref=exported['execution_orders']['rows'][0]['execution_ref']
    assert exported['execution_fills']['rows'][0]['execution_ref']==ref
    assert exported['execution_events']['rows'][0]['execution_ref']==ref
    assert 'BTC' not in json.dumps(exported)


@pytest.mark.parametrize('conflict',['overfill','multiple_orders'])
def test_primary_order_cannot_absorb_unrelated_or_excess_execution(conflict):
    reserve();r=result(130 if conflict=='overfill' else 124.77)
    if conflict=='multiple_orders':r.order_ids.append('other-order')
    with pytest.raises(lifecycle.ExecutionConflict):lifecycle.observe(r,broker='okx')
    assert lifecycle.snapshot()[0]['filled']=='0'
    with lifecycle._connect() as con:
        assert con.execute('SELECT COUNT(*) FROM execution_fills').fetchone()[0]==0
