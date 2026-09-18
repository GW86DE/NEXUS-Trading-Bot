from copy import deepcopy
from datetime import datetime,timezone
from pathlib import Path
import sqlite3
import pytest
from broker_display_context import context,environment,match_position,trade_groups


def row(id=1,broker='etoro',paper=True,account='A',ccy='USD',pnl=3):
    return {'trade_id':id,'broker':broker,'paper':paper,'broker_account_fingerprint':account,
        'waehrung':ccy,'symbol':'TEST','broker_position_id':'TEST-USD','entry_order_id':'entry1',
        'einstieg_preis':100,'menge':1,'ausgestiegen_am':'2026-09-01T12:00:00Z','netto_pnl':pnl,
        'fee_quality':'CONFIRMED','gebuehren':0,'einstieg_gebuehr':0}


def test_currency_broker_environment_and_account_never_mix():
    rows=[row(),row(2,broker='okx'),row(3,paper=False),row(4,account='B'),row(5,ccy='EUR')]
    groups=trade_groups(rows)
    assert len(groups)==5
    assert [g['summe_netto'] for g in groups]==[3]*5
    assert all(g['curve'][0]['wert']==3 for g in groups)


def test_same_domain_accumulates_from_rows_not_cached_totals():
    groups=trade_groups([row(pnl=-5),row(2,pnl=12)])
    assert len(groups)==1 and [p['wert'] for p in groups[0]['curve']]==[-5,7]
    assert groups[0]['summe_netto']==7


def test_unknown_account_is_not_fictional_shared_account():
    assert len(trade_groups([row(account=''),row(2,account='')]))==2


def test_unknown_fee_not_in_confirmed_curve():
    a=row();b=row(2);b['fee_quality']='UNKNOWN'
    g=trade_groups([a,b])[0]
    assert g['summe_netto']==3 and len(g['curve'])==1 and g['ohne_bestaetigtes_netto']==1


def test_unknown_and_conflicting_environment_are_not_live_defaults():
    assert environment({})=='UNKNOWN'
    assert environment({'paper':True,'environment':'LIVE'})=='CONFLICT'
    assert context({'mode':'PAPER'})['environment']=='DEMO'


def position():
    return {'symbol':'TEST','inst_id':'TEST-USD','order_id':'entry1','paper':True,
        'account_fingerprint':'A','verwaltung':'AUTO','stop':90}

@pytest.mark.parametrize('mutation',[{'broker':'etoro'},{'paper':False},{'broker_account_fingerprint':'B'},
    {'broker_position_id':'TEST-EUR'},{'entry_order_id':'entry2'},{'broker_account_fingerprint':''}])
def test_overlay_never_links_just_by_symbol(mutation):
    t=row(broker='okx');t.update(mutation)
    assert not match_position(t,[position()])


def test_overlay_requires_unambiguous_identity():
    t=row(broker='okx');assert match_position(t,[position()])['stop']==90
    assert not match_position(t,[position(),position()])


def test_api_keeps_same_currency_across_brokers_separate(monkeypatch):
    import trade_ledger
    from webui.state import trade_analysis
    monkeypatch.setattr(trade_ledger,'trade_liste',lambda **_: [row(),row(2,broker='okx')])
    d=trade_analysis()
    assert len(d['context_groups'])==2
    assert d['kennzahlen']['summe_netto'] is None
    assert d['kumulierte_ergebnisse_nach_waehrung']=={}
    assert len(d['geschlossene_trades'])==2


def test_read_endpoints_require_auth():
    from fastapi.testclient import TestClient
    from webui.app import app
    with TestClient(app) as client:
        for route in ('/api/accounting','/api/broker-contexts','/api/logs/scoped'):
            assert client.get(route).status_code==401


def test_decision_counts_broker_and_mode(tmp_path,monkeypatch):
    from webui import state
    import config
    monkeypatch.setattr(state,'ROOT',tmp_path)
    monkeypatch.setattr(config,'DECISION_DB_FILE','decisions.sqlite')
    import zoneinfo
    day=datetime.now(zoneinfo.ZoneInfo(config.LOCAL_TIMEZONE)).date().isoformat()
    con=sqlite3.connect(tmp_path/'decisions.sqlite')
    con.execute('CREATE TABLE decisions(broker,paper,status,local_day)')
    con.executemany('INSERT INTO decisions VALUES(?,?,?,?)', [('etoro',1,'APPROVED',day),('okx',1,'BLOCKED',day),('etoro',0,'APPROVED',day)])
    con.commit();con.close()
    d=state.decision_summary_scoped()
    assert d['etoro']['candidates']==2 and d['okx']['candidates']==1
    assert d['etoro']['modes']=={'DEMO':1,'LIVE':1}


def test_log_filter_is_logger_scoped_not_ticker(monkeypatch):
    from webui import state
    rows=['2026-09-07 10:00:00 ERROR live_trader: TEST fehlgeschlagen',
          'Traceback (most recent call last):','  File "live_trader.py", line 1',
          '2026-09-07 10:00:01 INFO crypto_engine: Guthaben',
          '2026-09-07 10:00:02 INFO main: system bereit']
    monkeypatch.setattr(state,'system_log',lambda **_:rows)
    assert len(state.system_log_scoped(broker='etoro'))==3
    assert len(state.system_log_scoped(broker='okx'))==1
    assert len(state.system_log_scoped(broker='system'))==1


def test_all_authenticated_pages_load_common_scope_ui():
    root=Path(__file__).resolve().parents[1]
    for name in ('dashboard','trades','analysis','universe','settings','logbook'):
        s=(root/f'webui/templates/{name}.html').read_text()
        assert 'scope-note' in s and '/static/common.js' in s
    common=(root/'webui/static/common.js').read_text()
    assert '/api/broker-contexts' in common and 'configured_environment' in common and 'observed_environment' in common


def test_no_instrument_guessed_from_ticker():
    import trade_chart_data as chart
    with pytest.raises(ValueError,match='nicht.*geraten'):
        chart._instrument({'symbol':'ETH','waehrung':'EUR'})


def test_candle_currency_cannot_be_mixed(monkeypatch):
    import trade_chart_data as chart
    t=row(broker='okx');t['broker_position_id']='TEST-EUR'
    t['eingestiegen_am']='2026-09-01T11:00:00Z'
    monkeypatch.setattr(chart,'trade_detail',lambda _:t)
    result=chart.chart_for_trade(1,force=True)
    assert not result['ok'] and 'Umrechnungskurs' in result['fehler']
