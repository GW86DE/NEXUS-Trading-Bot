from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import sqlite3
import time

import pytest


def test_research_reader_does_not_lock_writer(monkeypatch, tmp_path):
    from pulsar import research
    monkeypatch.setenv('TRADINGBOT_TEST_STATE_DIR', str(tmp_path))
    research.cache_put('sample', {'n': 1}, 3600)
    con=sqlite3.connect(research.path())
    try:
        con.execute('BEGIN')
        assert con.execute("SELECT payload FROM cache WHERE key='sample'").fetchone()
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(research.cache_put, 'sample', {'n': 2}, 3600).result(timeout=3)
        assert research.cached('sample')['data']['n']==2
        with research.db(readonly=True) as reader:
            with pytest.raises(sqlite3.OperationalError):
                reader.execute("DELETE FROM cache")
    finally:
        con.close()


def test_low_rank_shared_x_candidate_keeps_research_slot():
    from pulsar.source_coordination import research_order
    from test_v1014_pulsar_x_candidates import seed, rows
    result, info=research_order(rows(['AAA','BBB','CCC','DDD','EEE','PEP']), [seed('PEP')], now=time.time())
    assert 'PEP' in [r['symbol'] for r in result[:5]]
    assert sum(r['symbol']=='PEP' for r in result)==1
    assert info['x_duplicates_merged']==1
    assert next(r for r in result if r['symbol']=='PEP')['discovery_origin']=='REDDIT_AND_X'


def test_scoped_account_action_single_retry_and_no_newer_rejection_erasure(monkeypatch,tmp_path):
    import okx_account_action as a
    monkeypatch.setenv('TRADINGBOT_TEST_STATE_DIR',str(tmp_path))
    a.record('one','DEMO',instrument='XRP-EUR')
    original=a.status('one','DEMO')
    assert original['blocked'] and not a.status('one','LIVE')['blocked']
    assert not a.status('two','DEMO')['blocked']
    with pytest.raises(ValueError):a.before_buy('one','DEMO')
    a.allow_retry('one','DEMO',revision=original['revision'],actor='tester')
    revision=a.before_buy('one','DEMO')
    with pytest.raises(ValueError):a.before_buy('one','DEMO')
    a.record('one','DEMO',instrument='XRP-EUR')
    a.finish_retry('one','DEMO',revision,accepted=True)
    assert a.status('one','DEMO')['blocked']
    current=a.status('one','DEMO')
    a.allow_retry('one','DEMO',revision=current['revision'],actor='tester')
    revision=a.before_buy('one','DEMO');a.finish_retry('one','DEMO',revision,accepted=True)
    assert not a.status('one','DEMO')['required']


def test_preupdate_rejection_import_requires_exact_account_and_environment(monkeypatch,tmp_path):
    import okx_account_action as a
    monkeypatch.setenv('TRADINGBOT_TEST_STATE_DIR',str(tmp_path))
    row={'broker':'okx','account_fingerprint':'one','broker_environment':'DEMO',
         'execution_status':'FEHLGESCHLAGEN','reason':'OKX lehnt die Order ab (54092): Action required',
         'inst_id':'XRP-EUR','time':datetime.now(timezone.utc).isoformat()}
    (tmp_path/'decision_journal.jsonl').write_text(json.dumps(row)+'\n')
    a.restore_observed_rejection('one','DEMO')
    a.restore_observed_rejection('one','LIVE')
    assert a.status('one','DEMO')['blocked'] and not a.status('one','LIVE')['blocked']
    rev=a.status('one','DEMO')['revision']
    a.allow_retry('one','DEMO',revision=rev,actor='tester')
    a._restored.clear();a.restore_observed_rejection('one','DEMO')
    assert a.status('one','DEMO')['state']=='RETRY_ALLOWED'


def test_runtime_54092_stops_only_entries(monkeypatch,tmp_path):
    from broker.okx import OKXBroker
    from broker.base import BrokerFehler
    from types import SimpleNamespace
    import execution_lifecycle as e
    import okx_account_action as a
    monkeypatch.setenv('TRADINGBOT_TEST_STATE_DIR',str(tmp_path))
    monkeypatch.setattr(e,'reserve',lambda **kw:'key')
    rejected=[];monkeypatch.setattr(e,'rejected',lambda *args,**kw:rejected.append(kw))
    monkeypatch.setattr(e,'accepted',lambda *args,**kw:None)
    calls=[]
    def place(body):
        calls.append(body)
        if body['side']=='buy':raise BrokerFehler('OKX lehnt die Order ab (54092): Action required')
        return {'ordId':'sell-receipt'}
    broker=object.__new__(OKXBroker);broker.demo=True
    broker.client=SimpleNamespace(place_order=place)
    broker.account_fingerprint=lambda:'one'
    import freqtrade_candles
    monkeypatch.setattr(freqtrade_candles,'require_signal_current',lambda *args:None)
    body=dict(side='buy',instId='XRP-EUR',clOrdId='test',sz='1')
    with pytest.raises(BrokerFehler):broker._submit_primary_order(body)
    assert a.status('one','DEMO')['blocked'] and rejected[0]['evidence']['broker_code']=='54092'
    with pytest.raises(BrokerFehler):broker._submit_primary_order(body)
    assert len(calls)==1
    assert broker._submit_primary_order(dict(body,side='sell'))['ordId']=='sell-receipt'


def test_final_execution_receipt_preserves_original_decision(monkeypatch,tmp_path):
    import decision_analytics as a
    # Existing test bootstrap routes the ledger; scope this test's DB explicitly.
    monkeypatch.setattr(a,'DB_PATH',tmp_path/'decisions.sqlite',raising=False)
    ident=a.record(dict(symbol='RECEIPT',broker='okx',status='APPROVED',reason='Gates passed'))
    assert ident
    a.record(dict(decision_id=ident,symbol='RECEIPT',broker='okx',status='FEHLGESCHLAGEN',
                  execution_status='FEHLGESCHLAGEN',reason='Broker 54092',blocked_by='BROKER'))
    row=next(r for r in a.latest(500) if r['id']==ident)
    assert row['decision_status']=='APPROVED' and row['status']=='FEHLGESCHLAGEN'
    assert row['decision_reason']=='Gates passed' and row['reason']=='Broker 54092'


def test_retry_endpoint_requires_login_csrf_exact_revision_and_fresh_runtime(monkeypatch,tmp_path):
    import okx_account_action as a
    from webui import state
    from test_v1015_webui_diagnosis import client
    monkeypatch.setenv('TRADINGBOT_TEST_STATE_DIR',str(tmp_path))
    a.record('one','DEMO',instrument='XRP-EUR')
    monkeypatch.setattr(state,'_runtime',lambda _:dict(worker_alive=True,account_action=a.status('one','DEMO')))
    c,auth,session,headers=client(monkeypatch)
    url='/api/okx/account-confirmation/retry'
    data=dict(acknowledged=True,revision=a.status('one','DEMO')['revision'])
    assert c.post(url,json=data).status_code==401
    c.cookies.set(auth.COOKIE,session)
    assert c.post(url,json=data).status_code==403
    assert c.post(url,json=dict(data,acknowledged='yes'),headers=headers).status_code==409
    assert c.post(url,json=dict(data,revision='old'),headers=headers).status_code==409
    r=c.post(url,json=data,headers=headers)
    assert r.status_code==200 and r.json()['state']=='RETRY_ALLOWED'
    assert r.json()['actor']=='testuser'
    assert c.post(url,json=data,headers=headers).status_code==409


@pytest.mark.parametrize('identity', [2**53+1, 2**62-1, '4522997527888746497'])
def test_large_decision_ids_remain_exact(identity):
    from decision_snapshot import apply_snapshot
    row = dict(decision_id=identity,symbol='PEP',broker='etoro')
    for _ in range(4):
        row=apply_snapshot(row)
        assert row['decision_id']==int(identity)


def test_diagnosis_counts_execution_failure_and_preserves_decision_reason():
    from NEXUS_10_Diagnose import decisions_report
    stamp='2026-09-15T07:20:15+00:00'
    row={'created_at_utc':stamp,'status':'APPROVED','execution_status':'FEHLGESCHLAGEN',
         'reason':'Initial gate approval','execution_result_json':json.dumps({'reason':'Broker 54092'}),
         'payload_json':'{}','symbol':'XRP','broker':'okx'}
    result=decisions_report([row],datetime.fromisoformat(stamp).timestamp()-1,datetime.fromisoformat(stamp).timestamp()+1)
    assert result['status_counts']=={'FEHLGESCHLAGEN':1}
    assert result['checks'][0]['decision_status']=='APPROVED'
    assert result['checks'][0]['reason']=='Broker 54092'


def test_zero_volume_is_distinguished_in_raw_broker_receipt(monkeypatch,tmp_path):
    import candle_observation as c
    observed=[]
    monkeypatch.setattr(c,'_persist',lambda receipt:observed.append(receipt) or True)
    rows=[[str(int(time.time()*1000)), '1','2','1','2', v, v, v,'1'] for v in ['0','0.000','12']]
    receipt=c.record_response(base_url='https://www.okx.com',environment='DEMO',
        endpoint='/market/candles',instrument='XRP-EUR',timeframe='5m',
        started=time.time()-1,received=time.time(),http_status=200,code='0',rows=rows)
    assert receipt['raw_zero_volume_rows']==2 and receipt['hashed_rows']==3
    assert receipt['volume_provenance']=='UNMODIFIED_BROKER_RESPONSE'
    assert observed[0]['sample'][1]['values'][5]=='0.000'
