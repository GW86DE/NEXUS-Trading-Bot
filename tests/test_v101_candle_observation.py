from datetime import datetime
import fcntl
import gzip
import json
from pathlib import Path
from types import SimpleNamespace as NS

import pandas as pd
import pytest

import candle_observation as observation
from broker.okx import OKXClient
from broker.base import BrokerFehler
import freqtrade_candles as candles
import freqtrade_sample_strategy as strategy


def bars(n=500, volume=0):
    return pd.DataFrame({'open':100.,'high':100.,'low':100.,'close':100.,'volume':float(volume)},
                        index=pd.date_range('2026-09-11T20:20:00Z', periods=n, freq='5min'))


def quality(frame, **kw):
    return observation.quality(frame,base_url='https://test.invalid',environment='DEMO',instrument='BTC-EUR',**kw)


@pytest.fixture(autouse=True)
def isolate(monkeypatch,tmp_path):
    monkeypatch.setenv('TRADINGBOT_TEST_STATE_DIR',str(tmp_path))
    observation._QUALITY.clear()


def test_current_timestamp_does_not_hide_inactive_market():
    frame=candles.normalize(bars())
    q=quality(frame,now=frame.index[-1].timestamp()+301)
    assert q['freshness']=='CURRENT' and q['zero_volume_rows']==500
    assert q['real_rows']==500 and q['synthetic_gap_rows']==0
    assert q['recent_active_rows']==0 and q['flat_close'] is True
    assert set(q['reasons'])=={'NO_RECENT_CANDLE_VOLUME','FLAT_CANDLE_HISTORY'}
    assert q['confirmation']=='UNKNOWN'  # No fabricated raw confirmation count.
    assert strategy.evaluate(frame).entry is False


def test_synthetic_gap_is_distinct_from_real_zero_volume():
    frame=bars(5,volume=20).drop(bars(5).index[2])
    q=quality(candles.normalize(frame),now=bars(5).index[-1].timestamp()+301)
    assert (q['rows'],q['real_rows'],q['synthetic_gap_rows'],q['zero_volume_rows'])==(5,4,1,1)
    assert 'SYNTHETIC_GAP_CANDLES' in q['reasons']


def test_missing_invalid_and_open_candle_are_not_current():
    assert quality(None)['freshness']=='MISSING'
    frame=bars(3)
    assert quality(frame,now=frame.index[-1].timestamp()+1)['freshness']=='FUTURE_OR_OPEN'
    frame.iloc[-1,4]=float('nan')
    q=quality(frame)
    assert q['freshness']=='INVALID' and q['zero_volume_rows'] is None


def test_snapshot_scopes_environment_and_ages_without_new_fetches():
    frame=bars(3,volume=1);now=frame.index[-1].timestamp()+301
    q=quality(frame,now=now);observation.publish(q)
    assert observation.snapshot(base_url='https://test.invalid',environment='LIVE',now=now)==[]
    result=observation.snapshot(base_url='https://test.invalid',environment='DEMO',now=now+901)
    assert result[0]['freshness']=='STALE' and 'CANDLES_STALE' in result[0]['reasons']
    assert result[0]['observed_at']==q['observed_at']
    assert observation.snapshot(base_url='https://other.invalid',environment='DEMO',now=now)==[]


class Session:
    headers={}
    def __init__(self,rows):self.rows=rows;self.calls=[]
    def request(self,*args,**kw):
        self.calls.append((args,kw))
        return NS(status_code=200,json=lambda:{'code':'0','data':self.rows})


def client(rows,demo=True):
    c=OKXClient('NEVER-EXPORT-KEY','NEVER-EXPORT-SECRET','NEVER-EXPORT-PASS',demo=demo)
    c.session=Session(rows)
    return c


def row(ts=1789307700000,confirm='1'):
    return [str(ts),'100','101','99','100','0','0','0',confirm]


def test_actual_response_receipt_is_bounded_and_has_no_credentials(tmp_path):
    c=client([row(1789307700000-i*300000) for i in range(50)]+[row(1789308000000,'0')])
    frame=c.candles('BTC-EUR',bar='5m',limit=100)
    assert len(c.session.calls)==1 and len(frame)==50
    assert c.session.calls[0][1]['headers']['x-simulated-trading']=='1'
    payload=json.loads((tmp_path/'candle_observations.json').read_text())
    receipt=payload['receipts'][0]
    assert receipt['response_rows']==51 and len(receipt['sample'])==10
    assert receipt['confirmed_rows']==50 and receipt['unconfirmed_rows']==1
    assert receipt['http_status']==200 and receipt['environment']=='DEMO'
    assert receipt['endpoint']=='/api/v5/market/candles'
    assert frame.attrs['source_receipt']['receipt_id']==receipt['receipt_id']
    assert 'NEVER-EXPORT' not in json.dumps(payload) and 'headers' not in receipt
    assert (tmp_path/'candle_observations.json').stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize('field,value',[(5,'bad'),(5,''),(5,float('nan')),(5,-1),(4,0),(2,98),(7,'unknown')])
def test_invalid_confirmed_values_fail_locally_and_never_become_zero(field,value):
    bad=row();bad[field]=value;c=client([bad])
    with pytest.raises(BrokerFehler,match='CANDLE_VALUES_INVALID'):
        c.candles('BTC-EUR',bar='5m')
    assert len(c.session.calls)==1
    good=client([row()]).candles('ETH-EUR',bar='5m')
    assert good.iloc[0]['volume']==0  # Real zero remains a valid observed number.


@pytest.mark.parametrize('bad',[row()[:8],row()[:4],row()[:8]+['maybe'],{'apiKey':'NEVER-EXPORT'}])
def test_missing_or_unknown_confirmation_does_not_disappear_silently(bad,tmp_path):
    with pytest.raises(BrokerFehler,match='CANDLE_(SCHEMA_INVALID|CONFIRMATION_UNKNOWN)'):
        client([bad]).candles('BTC-EUR')
    content=(tmp_path/'candle_observations.json').read_text()
    assert 'NEVER-EXPORT' not in content


def test_receipt_write_failure_cannot_block_history(monkeypatch):
    def fail(_):raise OSError('readonly filesystem')
    monkeypatch.setattr(observation,'_persist',fail)
    c=client([row()]);frame=c.candles('BTC-EUR',bar='5m')
    assert len(frame)==1 and len(c.session.calls)==1


def test_receipt_storage_is_globally_bounded_and_lock_is_nonblocking(tmp_path,monkeypatch):
    monkeypatch.setattr(observation,'MAX_RECEIPTS',3)
    kwargs=dict(base_url='https://test.invalid',environment='DEMO',endpoint='/market/candles',
                timeframe='5m',started=10,received=11,http_status=200,code='0',rows=[row()])
    for i in range(5):observation.record_response(instrument=f'ASSET{i}-EUR',**kwargs)
    path=tmp_path/'candle_observations.json';before=path.read_bytes()
    assert len(json.loads(before)['receipts'])==3
    with path.with_suffix('.lock').open('r+') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        observation.record_response(instrument='BLOCKED-EUR',**kwargs)
        assert path.read_bytes()==before


def test_cache_observation_causes_no_extra_requests_and_no_fabricated_receipt(tmp_path):
    data=bars();count=[]
    class Client:
        demo=True;base_url='https://test.invalid'
        def candles(self,*args,**kwargs):count.append('latest');return data.tail(300)
        def historical_candles(self,*args,**kwargs):
            count.append('history');return data.loc[data.index<pd.Timestamp(kwargs['end_ms'],unit='ms',tz='UTC')].tail(100)
    history=candles.History(Client(),tmp_path/'candles.sqlite')
    cutoff=data.index[-1]+pd.Timedelta(minutes=5)
    first=history.load('BTC-EUR',cutoff=cutoff);n=len(count)
    again=history.load('BTC-EUR',cutoff=cutoff)
    assert len(count)==n and len(again)==500
    assert first.attrs['candle_quality']['zero_volume_rows']==500
    assert again.attrs['source_receipt'] is None
    assert again.attrs['candle_quality']['real_rows']==500


def test_active_timeframe_and_independent_safety_wait(monkeypatch):
    from crypto_strategy_mode import signal_timeframe
    from trading_ready import Handelsbereitschaft
    assert signal_timeframe('FREQTRADE_SAMPLE',standard_timeframe='15 mins')=='5m'
    assert signal_timeframe('NEXUS_STANDARD',standard_timeframe='15 mins')=='15 mins'
    assert signal_timeframe('CRYPTO_PAUSED') is None
    clock=[1000.]
    ready=Handelsbereitschaft('okx',cfg=NS(STARTUP_TRADING_GRACE_MINUTES=15),jetzt=lambda:clock[0])
    for name in ready.bedingungen:ready.melde(name,True)
    clock[0]+=301
    assert ready.kerzen_vollstaendig(300)
    assert ready.pruefe()[0] is False
    wait=ready.status()['safety_wait']
    assert wait['configured_seconds']==900 and wait['remaining_seconds']==599 and wait['active']
    clock[0]+=600
    assert ready.pruefe()[0] is True and not ready.status()['safety_wait']['active']


CASES=json.loads(gzip.decompress((Path(__file__).parent/'fixtures/candles_20260913.json.gz').read_bytes()))['cases']
@pytest.mark.parametrize('case',CASES,ids=[c['instrument'] for c in CASES])
def test_nine_original_diagnostic_decisions_replay_unchanged(case):
    raw=pd.DataFrame(case['rows'],columns=['ts','open','high','low','close','volume'])
    raw.index=pd.to_datetime(raw.pop('ts'),unit='ms',utc=True)
    frame=candles.normalize(raw,cutoff=pd.Timestamp(case['cursor']+300,unit='s',tz='UTC'))
    result=strategy.evaluate(frame);expected=case['expected']
    assert result.entry is False and expected['status']=='NO_SIGNAL'
    for actual,key in [(result.rsi,'rsi_5m'),(result.tema,'tema_5m'),(result.bb_middle,'bb_middle_5m'),(result.volume,'volume_5m')]:
        assert actual==pytest.approx(expected[key],abs=1e-10)
    assert result.entry_reason==expected['signal_reason']
    assert pd.Timestamp(result.candle_time).timestamp()==case['cursor']
    assert case['cursor']+300 <= datetime.fromisoformat(expected['time']).timestamp()


def test_engine_readiness_uses_active_mode_but_keeps_safety_timer(monkeypatch):
    import crypto_engine
    import crypto_strategy_mode
    import okx_accounting
    from trading_ready import Handelsbereitschaft
    clock=[1000.]
    cfg=NS(STARTUP_TRADING_GRACE_MINUTES=15,CRYPTO_BAR_SIZE='15 mins')
    ready=Handelsbereitschaft('okx',cfg=cfg,jetzt=lambda:clock[0])
    broker=NS(client=NS(instruments=lambda:{'BTC-EUR':NS(lot_size=1,min_size=1)},tickers=lambda:{}))
    engine=crypto_engine.CryptoEngine.__new__(crypto_engine.CryptoEngine)
    engine.hub=NS(broker=lambda _:broker);engine.cfg=cfg;engine.bereitschaft=ready
    engine._taker_satz=lambda:0.0035;engine._offene_order_symbole=lambda:[]
    monkeypatch.setattr(okx_accounting,'broker_status',lambda _:{'complete':True,'detail':'test'})
    clock[0]+=301
    monkeypatch.setattr(crypto_strategy_mode,'current_mode',lambda:'FREQTRADE_SAMPLE')
    engine._melde_bereitschaft()
    assert ready.bedingungen['signalkerze'].erfuellt
    assert '5 min' in ready.bedingungen['signalkerze'].detail
    assert ready.status()['safety_wait']['remaining_seconds']==599
    monkeypatch.setattr(crypto_strategy_mode,'current_mode',lambda:'NEXUS_STANDARD')
    engine._melde_bereitschaft()
    assert not ready.bedingungen['signalkerze'].erfuellt
    assert '15 min' in ready.bedingungen['signalkerze'].detail


def test_engine_quality_uses_full_contract_identity_and_never_symbol_only(monkeypatch):
    import crypto_engine
    data=bars();cutoff=data.index[-1]+pd.Timedelta(minutes=5)
    broker=NS(client=NS(base_url='https://test.invalid'),demo=True,
              historie=lambda *args:data)
    engine=crypto_engine.CryptoEngine.__new__(crypto_engine.CryptoEngine)
    engine.hub=NS(broker=lambda _:broker)
    monkeypatch.setattr(candles,'latest_signal_valid',lambda _:True)
    instrument=NS(name='BTC',contract=NS(localSymbol='BTC-EUR'))
    frame=engine._freqtrade_history(instrument,cutoff=cutoff)
    assert frame.attrs['candle_quality']['instrument']=='BTC-EUR'
    observed=observation.snapshot(base_url='https://test.invalid',environment='DEMO')
    assert [q['instrument'] for q in observed]==['BTC-EUR']
