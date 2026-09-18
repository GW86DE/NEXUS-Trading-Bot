from __future__ import annotations
import json, sqlite3
from datetime import datetime, timezone
import pytest


def test_unknown_account_gap_blocks_every_etoro_domain():
    from etoro_accounting_resolution import project
    data={'records':{},'unzugeordnete_verkaeufe':[{'symbol':'KO','broker_position_id':'P1','domaene':'UNKNOWN'}]}
    ledger={'trades':[],'exits':[],'error':''}
    for domain in ('etoro:demo:ACC1','etoro:live:ACC2','etoro:demo'):
        report=project(data,ledger,domain=domain,now=datetime(2026,9,7,tzinfo=timezone.utc))
        assert report['blocking'], domain
        assert report['blocking'][0]['domaene']=='unknown'


def test_closed_entry_lineage_replay_is_idempotent_but_new_late_fill_is_blocked(tmp_path,monkeypatch):
    import decision_analytics as da, trade_ledger as tl
    monkeypatch.setattr(da,'DB_PATH',tmp_path/'ledger.sqlite')
    tl.init_ledger()
    base=dict(broker='etoro',symbol='KO',menge=10,einstieg_preis=100,external=True,paper=True,
              waehrung='USD',broker_position_id='POS1',entry_order_id='ORD1',broker_account_fingerprint='ACC1',
              entry_fill_id='F1',entry_fill_ids=['F1'],entry_fills=[{'fill_id':'F1','quantity':10,'price':100}],critical=True)
    tid=tl.trade_open(**base)
    assert tl.trade_close(broker='etoro',symbol='KO',menge=10,ausstieg_preis=90,paper=True,
                          broker_position_id='POS1',entry_order_id='ORD1',broker_account_fingerprint='ACC1',
                          event_id='X1',exit_fill_ids=['X1'])==tid
    before=tl.trade_detail(tid).copy()
    assert tl.trade_open(**base)==tid
    assert tl.trade_detail(tid)['menge']==before['menge']==10
    late=dict(base);late.update(menge=5,einstieg_preis=120,entry_fill_id='F2',entry_fill_ids=['F2'],
                                entry_fills=[{'fill_id':'F2','quantity':5,'price':120}])
    with pytest.raises(tl.LedgerZuordnungUnklar): tl.trade_open(**late)
    after=tl.trade_detail(tid)
    for key in ('menge','einstieg_preis','brutto_pnl','netto_pnl','gebuehren','ausgestiegen_am'):
        assert after[key]==before[key]


def test_late_fill_after_partial_exit_does_not_mutate_open_remainder(tmp_path,monkeypatch):
    import decision_analytics as da, trade_ledger as tl
    monkeypatch.setattr(da,'DB_PATH',tmp_path/'ledger.sqlite');tl.init_ledger()
    args=dict(broker='etoro',symbol='ABC',menge=10,einstieg_preis=100,external=True,paper=True,waehrung='USD',
              broker_position_id='P',entry_order_id='O',broker_account_fingerprint='A',entry_fill_id='F1',
              entry_fill_ids=['F1'],entry_fills=[{'fill_id':'F1','quantity':10,'price':100}],critical=True)
    tid=tl.trade_open(**args)
    tl.trade_close(broker='etoro',symbol='ABC',menge=4,ausstieg_preis=110,paper=True,broker_position_id='P',
                   entry_order_id='O',broker_account_fingerprint='A',event_id='X',exit_fill_ids=['X'])
    rest=tl.offener_trade('etoro','ABC',broker_account_fingerprint='A')
    assert rest and rest['menge']==6
    late=dict(args);late.update(menge=2,einstieg_preis=120,entry_fill_id='F2',entry_fill_ids=['F2'],
                                entry_fills=[{'fill_id':'F2','quantity':2,'price':120}])
    with pytest.raises(tl.LedgerZuordnungUnklar): tl.trade_open(**late)
    rest2=tl.offener_trade('etoro','ABC',broker_account_fingerprint='A')
    assert rest2['trade_id']==rest['trade_id'] and rest2['menge']==6 and rest2['einstieg_preis']==100


def test_fill_tracker_empty_file_is_not_an_error(tmp_path):
    from fill_tracker import FillProgressTracker
    p=tmp_path/'fill_progress.json';p.write_bytes(b'')
    t=FillProgressTracker(p)
    # Empty means no durable checkpoint yet, not corrupt JSON.
    assert not t.initialized


def test_fill_tracker_recovery_uses_ledger_resolver_and_zero_fills_returns_baseline(tmp_path,monkeypatch):
    import decision_analytics as da
    from fill_tracker import FillProgressTracker
    db=tmp_path/'decision_history.sqlite';monkeypatch.setattr(da,'DB_PATH',db)
    con=sqlite3.connect(db);con.executescript('CREATE TABLE trade_entry_fills(fill_id TEXT,order_id TEXT,quantity REAL,price REAL,fee REAL); CREATE TABLE trade_exit_events(fill_id TEXT,order_id TEXT,quantity REAL);');con.close()
    p=tmp_path/'fill_progress.json';p.write_text('{')
    t=FillProgressTracker(p);assert t.storage_error
    result=t.recover_from_ledger('decision_history.sqlite')
    assert result['reason']=='ledger_empty' and not t.initialized and not t.storage_error
    assert list(tmp_path.glob('fill_progress.json.corrupt-*'))


def test_restart_schedule_starts_with_45_then_90():
    import nexus_start as ns
    assert ns.AKTIEN_NEUSTART_BASIS==45.0 and ns.AKTIEN_NEUSTART_MAXIMUM==90.0
