"""9.7.1: boundary tests missing from the 9.7 release and fee-neutral fixtures.
No network calls and no synthetic replacement for installed dependencies.
"""
from __future__ import annotations
import json
from dataclasses import replace
from datetime import datetime, timezone
import pytest

@pytest.fixture
def ledger(monkeypatch, tmp_path):
    import decision_analytics as da
    import trade_ledger as tl
    monkeypatch.setattr(da, 'DB_PATH', tmp_path / 'ledger.sqlite')
    tl.init_ledger()
    return tl


def opened(tl, symbol='AAA', **kw):
    args = dict(broker='etoro', symbol=symbol, menge=10, einstieg_preis=100,
                external=True, paper=True, waehrung='USD',
                zeit='2026-09-07T07:00:00+00:00')
    args.update(kw)
    return tl.trade_open(**args)


def close(tl, symbol='AAA', **kw):
    args = dict(broker='etoro', symbol=symbol, menge=10, ausstieg_preis=110,
                paper=True, waehrung='USD', zeit='2026-09-07T08:00:00+00:00')
    args.update(kw)
    return tl.trade_close(**args)


@pytest.mark.parametrize('entry_fee,exit_fee', [(None,None),(None,0.0),(0.0,None)])
def test_unknown_fees_do_not_create_confirmed_net_or_missing_keys(ledger, entry_fee, exit_fee):
    tid=opened(ledger,gebuehr=entry_fee,referenzpreis=99)
    assert close(ledger,gebuehr=exit_fee)==tid
    row=ledger.trade_detail(tid)
    assert row['netto_pnl'] is None and row['gebuehren'] is None
    assert row['fee_quality']=='UNKNOWN' and row['brutto_pnl']==100
    stats=ledger.trade_snapshot()['gesamt']
    for key in ('summe_netto','trefferquote_pct','profitfaktor','max_drawdown',
                'gebuehren_quote_pct','erwartungswert'):
        assert key in stats and stats[key] is None
    assert stats['haltedauer_minuten_mittel']==60
    assert stats['slippage_mittel_pct']==pytest.approx(100/99,abs=0.0001)
    assert stats['bewertbar']==0 and stats['gebuehren_offen']==1
    from webui.state import trade_analysis
    ui=trade_analysis()
    assert ui['kennzahlen']['bewertbar']==0
    assert ui['kennzahlen']['gebuehren_offen']==1
    assert ui['kumulierte_ergebnisse_nach_waehrung']=={}
    assert ui['geschlossene_trades'][0]['ergebnis_status']=='FEES_UNKNOWN'


def test_explicit_zero_is_confirmed_and_included_in_both_apis(ledger):
    tid=opened(ledger,gebuehr=0)
    assert close(ledger,gebuehr=0)==tid
    assert ledger.trade_snapshot()['gesamt']['summe_netto']==100
    from webui.state import trade_analysis
    ui=trade_analysis()
    assert ui['kennzahlen']['summe_netto']==100
    assert ui['geschlossene_trades'][0]['ergebnis_status']=='CONFIRMED'


def test_partial_remainder_keeps_unknown_entry_fees_after_both_sales(ledger):
    tid=opened(ledger,gebuehr=None)
    assert close(ledger,menge=4,gebuehr=0)==tid
    rest=ledger.offener_trade('etoro','AAA')
    assert rest['menge']==6 and rest['einstieg_gebuehr'] is None
    assert close(ledger,menge=6,gebuehr=0)==rest['trade_id']
    for tid in (tid,rest['trade_id']):
        row=ledger.trade_detail(tid)
        assert row['fee_quality']=='UNKNOWN'
        assert row['netto_pnl'] is None and row['einstieg_gebuehr'] is None


def test_legacy_net_estimate_remains_in_audit_while_unknown_costs_hide_display_net(ledger):
    tid=opened(ledger)
    close(ledger)
    with ledger._connect() as con:
        con.execute('UPDATE trades SET netto_pnl=99.0 WHERE trade_id=?',(tid,))
    g=ledger.trade_snapshot()['gesamt']
    assert g['summe_netto'] is None and g['vorlaeufig']==1
    from webui.state import trade_analysis
    before = ledger.trade_detail(tid)
    ui=trade_analysis()
    assert ui['kennzahlen']['vorlaeufig']==0 and ui['kennzahlen']['bewertbar']==0
    assert ui['kennzahlen']['gebuehren_offen']==1
    assert ui['kennzahlen']['summe_netto'] is None
    assert ui['kennzahlen']['gebuehren'] is None
    assert ui['kennzahlen']['summe_nach_waehrung']=={}
    assert ui['kumulierte_ergebnisse_nach_waehrung']=={}
    row=ui['geschlossene_trades'][0]
    assert row['netto_pnl'] is None and row['ergebnis_status']=='FEES_UNKNOWN'
    assert row['netto_pnl_pct'] is None and row['gebuehren'] is None
    assert row['unconfirmed_net_audit']==99
    assert row['historical_result_scope']=='CLOSED_TRADE_COSTS_ONLY'
    assert ledger.trade_detail(tid)==before
    assert ledger.trade_detail(tid)['netto_pnl']==99
    assert ledger.trade_snapshot()['gesamt']['vorlaeufig']==1


def test_mixed_currency_cash_statistics_are_never_added(ledger):
    opened(ledger,'AAA',gebuehr=0,waehrung='EUR');close(ledger,'AAA',gebuehr=0)
    opened(ledger,'BBB',gebuehr=0,waehrung='USD');close(ledger,'BBB',gebuehr=0)
    g=ledger.trade_snapshot()['gesamt']
    assert g['summe_netto'] is None and g['erwartungswert'] is None
    assert g['profitfaktor'] is None and g['max_drawdown'] is None
    assert g['summe_nach_waehrung']=={'EUR':100,'USD':100}
    from webui.state import trade_analysis
    ui=trade_analysis()
    assert ui['kennzahlen']['summe_netto'] is None
    assert set(ui['kumulierte_ergebnisse_nach_waehrung'])=={'EUR','USD'}


@pytest.mark.parametrize('value',[float('nan'),float('inf'),'-inf',True,'broken',None])
def test_nonfinite_net_cannot_be_confirmed(value):
    from ledger_result import confirmed_net
    assert not confirmed_net({'fee_quality':'CONFIRMED','netto_pnl':value})


def test_cumulative_value_fees_and_metadata_survive_reload(tmp_path):
    from broker.base import Fill
    from fill_tracker import FillProgressTracker
    path=tmp_path/'fills.json'
    t=FillProgressTracker(path)
    f=Fill('raw','order1','AAA','BUY',4,100,quantity_is_cumulative=True,
           explicit_fees=1.0,broker_detail={'executionId':'e1','positionId':'p1'})
    prepared,token=t.prepare(f);t.commit(token)
    assert prepared.quantity==4 and prepared.price==100
    t=FillProgressTracker(path)
    second=replace(f,quantity=10,price=106,explicit_fees=3.0,
                   broker_detail={'executionId':'e2','positionId':'p1'})
    prepared,token=t.prepare(second)
    assert prepared.quantity==6 and prepared.price==110 and prepared.explicit_fees==2
    assert prepared.broker_detail==second.broker_detail
    t.commit(token)
    assert FillProgressTracker(path).prepare(second)==(None,None)


def test_legacy_quantity_only_checkpoint_cannot_invent_delta_price(tmp_path):
    from broker.base import Fill
    from fill_tracker import FillProgressTracker
    path=tmp_path/'fills.json'
    path.write_text(json.dumps({'initialized':True,'cumulative':{'order1':4},'seen_ids':[]}))
    t=FillProgressTracker(path)
    with pytest.raises(RuntimeError,match='Orderwert fehlt'):
        t.prepare(Fill('r','order1','AAA','BUY',10,106,quantity_is_cumulative=True))
    assert t.cumulative=={'order1':4.0}


def test_failed_tracker_commit_does_not_mark_execution_seen(tmp_path,monkeypatch):
    import fill_tracker
    from broker.base import Fill
    t=fill_tracker.FillProgressTracker(tmp_path/'fills.json')
    f=Fill('execution','order1','AAA','BUY',1,100)
    _,token=t.prepare(f)
    def failed(*a,**kw):
        raise OSError('disk full')
    monkeypatch.setattr(fill_tracker,'atomic_write_json',failed)
    with pytest.raises(OSError): t.commit(token)
    assert t.prepare(f)[0] is not None and not t.initialized


def _replay_setup(tl, *, partial=False):
    identity=dict(broker_position_id='POS',entry_order_id='ENTRY',broker_account_fingerprint='ACCOUNT')
    tid=opened(tl,gebuehr=0,**identity)
    assert close(tl,menge=4 if partial else 10,gebuehr=1,exit_order_id='CLOSE',
                 exit_fill_ids=['exec:1'],event_id='exec:1',**identity)==tid
    return tid,identity


def test_partial_exit_alias_does_not_consume_open_remainder(ledger):
    tid,identity=_replay_setup(ledger,partial=True)
    rest=ledger.offener_trade('etoro','AAA',broker_position_id='POS',
                              broker_account_fingerprint='ACCOUNT')
    before=ledger.trade_detail(tid)
    for _ in range(2):
        assert close(ledger,menge=4,gebuehr=1,exit_order_id='CLOSE',
            exit_fill_ids=['evidence:alias'],event_id='evidence:alias',critical=True,**identity)==tid
        assert ledger.trade_detail(tid)==before
        assert ledger.trade_detail(rest['trade_id'])==rest


@pytest.mark.parametrize('difference', ['price','fee','time','order','environment','real_execution'])
def test_conflicting_alias_is_rejected_without_financial_change(ledger,difference):
    tid,identity=_replay_setup(ledger)
    before=ledger.trade_detail(tid)
    kw=dict(gebuehr=1,exit_order_id='CLOSE',exit_fill_ids=['evidence:alias'],
            event_id='evidence:alias',critical=True,**identity)
    kw.update({'price':{'ausstieg_preis':111},'fee':{'gebuehr':2},
               'time':{'zeit':'2026-09-07T08:01:00Z'},
               'order':{'exit_order_id':'OTHER'},'environment':{'paper':False},
               'real_execution':{'exit_fill_ids':['exec:2'],'event_id':'exec:2'}}[difference])
    with pytest.raises(ledger.LedgerZuordnungUnklar): close(ledger,**kw)
    assert ledger.trade_detail(tid)==before
    with ledger._connect() as con:
        assert con.execute('SELECT COUNT(*) FROM trades').fetchone()[0]==1
        assert con.execute('SELECT COUNT(*) FROM trade_exit_events').fetchone()[0]==1


def test_partial_alias_fee_conflict_cannot_consume_remaining_position(ledger):
    tid,identity=_replay_setup(ledger,partial=True)
    with pytest.raises(ledger.LedgerZuordnungUnklar):
        close(ledger,menge=4,gebuehr=3,exit_order_id='CLOSE',
              exit_fill_ids=['evidence:alias'],event_id='evidence:alias',critical=True,**identity)
    assert ledger.offener_trade('etoro','AAA')['menge']==6
