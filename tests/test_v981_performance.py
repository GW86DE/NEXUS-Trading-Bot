from datetime import datetime, timezone
from pathlib import Path
import importlib
import json

from fastapi.testclient import TestClient
import pytest

from trade_performance import aggregate

NOW=datetime(2026,9,9,12,tzinfo=timezone.utc)


def row(tid=1,**kw):
    return {**dict(trade_id=tid,broker='okx',paper=1,broker_account_fingerprint='A',
        waehrung='USDC',asset_type='crypto',symbol='BTC',einstieg_preis=100,menge=1,
        einstieg_gebuehr=1,ausgestiegen_am='2026-09-09T08:00:00+00:00',
        netto_pnl=10,fee_quality='CONFIRMED',superseded_by=None,reconciliation_status='CLOSED'),**kw}


def test_all_time_and_today_are_independent_of_chart_window():
    d=aggregate([row(1,ausgestiegen_am='2026-01-01T00:00:00Z'),row(2,netto_pnl=-2)],now=NOW,days=30)
    g=d['groups'][0]
    assert g['total']['net']==8 and g['today']['net']==-2
    assert g['total']['percent']==pytest.approx(100*8/202)
    assert g['today']['percent']==pytest.approx(-200/101)
    assert len(g['series'])==30 and g['series'][0]['cumulative']['net']==10


@pytest.mark.parametrize('changes',[{'broker':'etoro'},{'paper':0},
    {'broker_account_fingerprint':'B'},{'waehrung':'EUR'}])
def test_money_and_percentage_never_cross_domains(changes):
    d=aggregate([row(1),row(2,netto_pnl=-20,**changes)],now=NOW)
    assert sorted(g['total']['net'] for g in d['groups'])==[-20,10]


def test_unknown_is_not_zero_or_complete_performance():
    g=aggregate([row(1),row(2,netto_pnl=None)],now=NOW)['groups'][0]
    assert g['total']['net']==10 and not g['total']['complete']
    assert g['today']['unknown']==1
    g=aggregate([row(netto_pnl=None)],now=NOW)['groups'][0]
    assert g['today']['net'] is None and g['today']['percent'] is None


def test_estimated_fees_and_nan_are_not_confirmed():
    g=aggregate([row(1,fee_quality='ESTIMATED'),row(2,netto_pnl=float('nan'))],now=NOW)['groups'][0]
    assert g['total']['known']==0 and g['total']['net'] is None


def test_capital_missing_keeps_cash_but_no_percent():
    g=aggregate([row(einstieg_preis=None)],now=NOW)['groups'][0]
    assert g['total']['net']==10 and g['total']['percent'] is None


def test_berlin_day_boundary_not_utc_midnight():
    g=aggregate([row(ausgestiegen_am='2026-09-08T22:30:00Z')],now=NOW)['groups'][0]
    assert g['today']['net']==10


def test_dst_day_is_calendar_day_not_24_hour_bucket():
    now=datetime(2026,10,25,23,tzinfo=timezone.utc)
    g=aggregate([row(1,ausgestiegen_am='2026-10-25T00:30:00Z'),
                 row(2,ausgestiegen_am='2026-10-25T01:30:00Z')],now=now,days=2)['groups'][0]
    assert g['series'][0]['daily']['net']==20


def test_open_superseded_unbound_and_foreign_assets_not_added():
    d=aggregate([row(1,ausgestiegen_am=None),row(2,superseded_by=9),
        row(3,broker_account_fingerprint=''),row(4,reconciliation_status='ACCOUNT_ASSET_CONFIRMED')],now=NOW)
    assert d['groups'][0]['total']['net']==0 and d['groups'][0]['open_count']==1
    assert d['excluded_rows']==1


def test_performance_read_does_not_create_missing_database(tmp_path,monkeypatch):
    import decision_analytics,trade_performance
    missing=tmp_path/'missing.sqlite'
    monkeypatch.setattr(decision_analytics,'db_pfad',lambda:missing)
    assert trade_performance.snapshot()['groups']==[]
    assert not missing.exists()


def test_performance_api_requires_session_and_valid_range(monkeypatch):
    module=importlib.import_module('webui.app')
    with TestClient(module.app) as c:
        assert c.get('/api/performance').status_code==401
        monkeypatch.setattr(module,'_session',lambda *a,**k:{'u':'test'})
        assert c.get('/api/performance?days=366').status_code==400
        assert c.get('/api/performance?days=30').json()['days']==30


def test_chart_keeps_null_gaps_and_has_accessible_table():
    root=Path(__file__).resolve().parents[1]
    js=(root/'webui/static/performance.js').read_text(encoding="utf-8")
    assert 'r[mode][unit]' in js
    assert 'r[mode].complete?r[mode][unit]:null' not in js
    assert 'Bestätigte Teilsummen' in js
    assert 'if(!known(p.value)){flush();return;}' in js
    assert 'aria-labelledby="performance-svg-title performance-svg-desc"' in js
    assert 'performance-table' in (root/'webui/templates/dashboard.html').read_text(encoding="utf-8")
    assert 'pxAmendType' not in js


def test_totals_include_more_than_five_hundred_trades_and_weight_partial_exit_capital():
    g=aggregate([row(i) for i in range(600)],now=NOW)['groups'][0]
    assert g['total']['net']==6000 and g['total']['known']==600
    parts=[row(1,menge=.4,einstieg_gebuehr=.4,netto_pnl=4),
           row(2,menge=.6,einstieg_gebuehr=.6,netto_pnl=-1)]
    g=aggregate(parts,now=NOW)['groups'][0]
    assert g['total']['capital']==101 and g['total']['percent']==pytest.approx(300/101)
