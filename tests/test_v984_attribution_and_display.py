"""Exact ownership, pinned currency and honest chart regression cases."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS
import json

import pytest


def evidence():
    return dict(_id_beweis=True, bestaetigt=True, verified_position_ids=['3597440106'],
        position_ids=['3597440106'], order_ids=['380127623'], reference_id='PEP-ref',
        symbol='PEP', account_fingerprint='test-account', paper=True, offen=False,
        decision_id=123, stop=135.8946, take_profit=138.5207)


def record():
    from position_manager import PositionRecord
    return PositionRecord(0, 'PEP', 'stock', 'USD', 109, 136.35, '2026-09-09T15:20:19Z',
        source='BOT', management_mode='PENDING_CONFIRMATION', ownership_status='VERIFIED',
        reconciliation_status='CONFIRMED_OPEN', owned_position_ids=['3597440106'],
        observed_position_ids=['3597440106'], broker_position_ids=['3597440106'],
        broker_account_fingerprint='test-account', broker_environment='DEMO',
        entry_order_ids=['380127623'], entry_reference_id='PEP-ref', decision_id=123,
        planned_stop=135.8946, planned_take=138.5207)


def test_pep_repeated_snapshot_is_idempotent_and_never_invents_protection(tmp_path):
    from position_manager import PositionManager
    manager = PositionManager(tmp_path/'p.json'); rec = record(); manager.records['PEP']=rec
    assert manager._pruefe_eigenen_kauf_nach(rec, ['3597440106'], eigen=evidence())
    assert 'Einstand bestaetigt' in rec.management_note
    assert not manager._pruefe_eigenen_kauf_nach(rec, ['3597440106'], eigen=evidence())
    assert rec.quantity == 109 and rec.avg_cost == 136.35
    assert rec.management_mode == 'PENDING_CONFIRMATION'
    assert rec.protection_status != 'ACTIVE'
    manager.set_bot_protection('PEP', False, 'Brokerwerte weichen vom Plan ab')
    assert not manager._pruefe_eigenen_kauf_nach(rec, ['3597440106'], eigen=evidence())
    manager.set_bot_protection('PEP', True, 'exact broker readback')
    manager._pruefe_eigenen_kauf_nach(rec, ['3597440106'], eigen=evidence())
    assert rec.management_mode == 'AUTO' and rec.protection_status == 'ACTIVE'
    rec.user_observe_locked=True;rec.management_mode='OBSERVE'
    assert not manager.set_bot_protection('PEP', True)
    assert rec.management_mode == 'OBSERVE'


@pytest.mark.parametrize('account,env,pid', [('other','DEMO','3597440106'),
    ('test-account','LIVE','3597440106'),('test-account','DEMO','manual-PEP')])
def test_matching_symbol_never_claims_another_position(monkeypatch,account,env,pid):
    import etoro_reconciliation as er
    from position_manager import PositionManager
    monkeypatch.setattr(er, 'eigene_kaeufe', lambda:[{**evidence(),'offen':True}])
    assert PositionManager._offene_kaufabsicht([pid], 'PEP',
        account_fingerprint=account, broker_environment=env) is None


def test_verified_open_ownership_survives_48_hours(monkeypatch):
    import etoro_reconciliation as er
    old = dict(state='FILLED', position_verified=True, position_state='OPEN_CONFIRMED',
        broker_position_status='OPEN_CONFIRMED', verified_position_ids=['p'],
        order_ids=['o'], created_at_utc=(datetime.now(timezone.utc)-timedelta(days=30)).isoformat())
    monkeypatch.setattr(er, '_load', lambda:{'records':{'1':old}})
    assert er.eigene_kaeufe()[0]['bestaetigt'] is True


def test_false_pending_demotion_removes_plan_but_preserves_audit(tmp_path,monkeypatch):
    from position_manager import PositionManager
    manager=PositionManager(tmp_path/'p.json');rec=record()
    rec.owned_position_ids=[];rec.ownership_status='PENDING'
    monkeypatch.setattr(manager,'_kaufabsicht_kompatibel', lambda *a,**k:None)
    assert manager._pruefe_eigenen_kauf_nach(rec, ['manual'])
    assert rec.source=='BROKER_EXISTING' and rec.management_mode=='OBSERVE'
    assert not rec.entry_order_ids and not rec.planned_stop and not rec.decision_id
    assert rec.attribution_history[0]['order_ids']==['380127623']
    assert not manager._pruefe_eigenen_kauf_nach(rec,['manual'])


def test_stock_overlay_requires_account_environment_position_and_entry_order():
    from broker_display_context import match_position
    row=dict(broker='etoro', broker_account_fingerprint='test-account', paper=True,
        broker_position_id='3597440106', entry_order_id='380127623')
    pos=dict(broker='etoro', account_fingerprint='test-account', broker_environment='DEMO',
        owned_position_ids=['3597440106'], order_ids=['380127623'],verwaltung='PENDING_CONFIRMATION')
    assert match_position(row,[pos])['verwaltung']=='PENDING_CONFIRMATION'
    for field,value in [('broker_environment','LIVE'),('account_fingerprint','other'),
                        ('order_ids',['other']),('owned_position_ids',['other'])]:
        assert not match_position(row,[{**pos,field:value}])


def test_missing_fees_allow_gross_curve_but_never_confirmed_net_or_cross_account_sum():
    from broker_display_context import trade_groups
    base=dict(broker='etoro', broker_account_fingerprint='A', paper=True,waehrung='USD',
        ausgestiegen_am='2026-09-10T12:00:00Z',brutto_pnl=-64.4,netto_pnl=None,fee_quality='UNKNOWN')
    groups=trade_groups([{**base,'trade_id':1},{**base,'trade_id':2,'broker_account_fingerprint':'B','brutto_pnl':10}])
    assert len(groups)==2
    assert all(not g['curve'] and g['summe_netto'] is None for g in groups)
    assert sorted(g['gross_curve'][0]['wert'] for g in groups)==[-64.4,10]


def test_usdc_sizing_cannot_turn_into_usd_submit():
    from broker.okx import OKXBroker, OKXInstrument
    from broker.base import BrokerFehler
    client=NS(balances=lambda:{'USDC':{'cash':100},'USD':{'cash':500}})
    broker=OKXBroker(client=client,quote_ccy='USD',allowed_quotes=('EUR','USDC','USD'))
    meta=OKXInstrument('DOGE-USD','DOGE','USD',trade_quote_ccy_list=('USD','USDC'))
    assert broker._entry_trade_quote(meta,{'trade_quote_ccy':'USDC'})=='USDC'
    client.balances=lambda:{'USD':{'cash':500}}
    with pytest.raises(BrokerFehler):broker._entry_trade_quote(meta,{'trade_quote_ccy':'USDC'})
    # Existing USD exits/protection are not subject to the new-buy whitelist.
    broker.allowed_quotes=('EUR','USDC')
    assert broker._protection_quote(meta,'USD')=='USD'


def test_logbook_uses_recorded_account_never_current_config(tmp_path, monkeypatch):
    import decision_analytics as analytics
    from webui import state
    monkeypatch.setattr(analytics, 'DB_PATH', tmp_path/'decision_history.sqlite')
    monkeypatch.setattr(state, 'ROOT', tmp_path)
    import config
    monkeypatch.setattr(config, 'DECISION_DB_FILE', 'decision_history.sqlite')
    base = dict(status='BLOCKED', broker='etoro', paper=True, symbol='PEP',
                reason='Ergebnisabgleich ausstehend')
    bound = analytics.record({**base, 'account_fingerprint':'recorded-acct', 'environment':'DEMO'})
    legacy = analytics.record(base)
    conflict = analytics.record({**base, 'account_fingerprint':'other-acct', 'environment':'LIVE'})
    rows = {r['id']:r['display_context'] for r in state.decision_log()['rows']}
    assert rows[bound]['account']=='recorded-acct' and rows[bound]['bound']
    assert not rows[legacy]['account'] and not rows[legacy]['bound']
    assert rows[conflict]['environment']=='CONFLICT' and not rows[conflict]['bound']
