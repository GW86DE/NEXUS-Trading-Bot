from datetime import datetime,timezone
from types import SimpleNamespace as NS
import pandas as pd
import pytest
from trading_ready import Handelsbereitschaft
from market_session import MarketSessionStatus
from stock_readiness import market,quote,history_fresh,probe
from market_calendar import sitzungsstatus


def ready():
    clock=[0.]
    r=Handelsbereitschaft('etoro',cfg=NS(STARTUP_TRADING_GRACE_MINUTES=0),jetzt=lambda:clock[0])
    for name in r.bedingungen:r.melde(name,True)
    return r,clock


def session(**values):
    return MarketSessionStatus(**{**dict(asset_type='stock',session='REGULAR',regular_open=True,
        broker_tradable=True,quote_fresh=True,source='test',detail='actual quote',
        checked_at_utc='2026-09-11T14:00:00Z',symbol='PEP',quote_age_seconds=10,
        quote_max_age_seconds=180),**values})


def test_quote_proof_expires_even_if_no_new_candle_or_worker_report_arrives():
    r,clock=ready();quote(r,session());assert r.pruefe()[0]
    clock[0]=170
    assert not r.pruefe()[0] and 'abgelaufen' in r.pruefe()[1]
    quote(r,session());assert r.pruefe()[0]


@pytest.mark.parametrize('change',[dict(quote_fresh=None),dict(quote_age_seconds=None),
    dict(quote_age_seconds=-1),dict(quote_age_seconds=181),dict(quote_age_seconds=float('nan'))])
def test_unknown_future_or_stale_quote_is_not_green(change):
    r,_=ready();quote(r,session(**change));assert not r.pruefe()[0]


def test_closed_market_normal_wait_does_not_hide_real_accounting_block():
    r,_=ready();market(r,sitzungsstatus(datetime(2026,9,11,10,tzinfo=timezone.utc)))
    r.melde('kursdaten',False)
    assert not r.pruefe()[0] and r.status()['market_wait_only']
    assert 'US-Markt geschlossen' in r.pruefe()[1]
    r.melde('buchung_vollstaendig',False,'Gebuehren fehlen')
    assert not r.status()['market_wait_only']
    assert 'Weitere Sperren' in r.pruefe()[1] and 'Gebuehren fehlen' in r.pruefe()[1]
    market(r,sitzungsstatus(datetime(2026,9,11,14,tzinfo=timezone.utc)))
    assert not r.pruefe()[0]  # Opening is never an accounting repair.


def test_fresh_history_is_not_just_thirty_rows():
    now=datetime(2026,9,11,15,5,tzinfo=timezone.utc)
    df=pd.DataFrame({'close':[100.]*30},index=pd.date_range(end='2026-09-11T14:00Z',periods=30,freq='h'))
    assert history_fresh(df,'1 hour',now=now)[0]
    old=df.copy();old.index=old.index-pd.Timedelta(days=5)
    assert not history_fresh(old,'1 hour',now=now)[0]
    future=df.copy();future.index=future.index+pd.Timedelta(hours=2)
    assert not history_fresh(future,'1 hour',now=now)[0]
    naive=df.copy();naive.index=naive.index.tz_localize(None)
    assert not history_fresh(naive,'1 hour',now=now)[0]


def test_quote_probe_is_bounded_independent_of_buy_signals(monkeypatch):
    r,_=ready();r.market_status={'offen':True};calls=[]
    monkeypatch.setattr('stock_readiness.market_session_status',lambda *a,**k:calls.append(1) or session())
    universe=[NS(name='PEP',asset_type='stock')]
    probe(r,NS(),universe,now=0);probe(r,NS(),universe,now=59)
    assert len(calls)==1
    probe(r,NS(),universe,now=60);assert len(calls)==2
    r.market_status={'offen':False};probe(r,NS(),universe,now=120)
    assert len(calls)==2
