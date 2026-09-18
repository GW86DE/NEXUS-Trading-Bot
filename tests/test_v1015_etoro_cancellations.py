from copy import deepcopy
from types import SimpleNamespace
import pytest

import etoro_cancellations as cancel
import etoro_reconciliation as rec
import trade_ledger as ledger
from etoro_order_status import lookup_status,STATES
from test_v990_fix1_recovery import closed_ledger


@pytest.mark.parametrize('sid',list(STATES))
def test_documented_states(sid):
    name,label,terminal,state=STATES[sid]
    p=lookup_status({'id':sid,'name':name})
    assert p['known'] and p['terminal']==terminal and p['execution']==state


@pytest.mark.parametrize('bad',[None,{}, {'id':8}, {'id':True}, {'id':3,'name':'WaitingForMarket'}, {'statusID':7}])
def test_unknown_or_legacy_never_infers_completion(bad):
    p=lookup_status(bad)
    assert not p['known'] and not p['terminal']


def setup(monkeypatch,tmp_path):
    tid,record,raw,args=closed_ledger(monkeypatch,tmp_path)
    record.update(position_state='NOT_SEEN',execution_state='RECONCILING',state='RECONCILING',
                  closed_position_ids=[],position_ids=[],fills=[])
    rec._save({'records':{'101':record}})
    raw.update(positionExecutions=[],status=dict(id=11,name='WaitingForMarket'))
    calls=[]
    def request(method,path,**kw):
        calls.append((method,path,kw))
        if method=='GET':return deepcopy(raw)
        return dict(orderId=777,referenceId='')
    b=SimpleNamespace(paper=True,account_fingerprint=lambda:args['account'],_request=request)
    # Queue tests exercise durable send recovery; ledger fill projection has
    # independent integration tests in test_v951_etoro_rootfix.
    monkeypatch.setattr(rec,'apply_broker_evidence',lambda *a:record)
    params=dict(account=args['account'],paper=True,order_id='777',actor='tester')
    return b,raw,calls,params


def test_accepted_cancel_requires_original_order_lookup_and_can_lose_race(monkeypatch,tmp_path):
    b,raw,calls,params=setup(monkeypatch,tmp_path)
    first=cancel.enqueue(**params)
    assert cancel.enqueue(**params)['request_id']==first['request_id']
    assert cancel.process_one(b,now=1000)['state']=='PENDING'
    assert calls[1][0]=='DELETE' and '/api/v3/' in calls[1][1]
    assert calls[1][2]['request_id']==first['request_id'] and not calls[1][2]['safe_retry']
    raw['status']=dict(id=6,name='PendingCancel')
    assert cancel.process_one(b,now=1031)['state']=='PENDING'
    raw['status']=dict(id=3,name='Filled')
    assert cancel.process_one(b,now=1062)['label']=='Ausgeführt'
    assert [c[0] for c in calls]==['GET','DELETE','GET','GET']
    assert all(c[2]['params']=={'orderId':'777'} for c in calls if c[0]=='GET')


def test_lost_cancel_ack_is_read_only_after_restart(monkeypatch,tmp_path):
    b,raw,calls,params=setup(monkeypatch,tmp_path)
    original=b._request
    def request(method,path,**kw):
        result=original(method,path,**kw)
        if method=='DELETE':raise TimeoutError('lost acknowledgement')
        return result
    b._request=request
    cancel.enqueue(**params)
    assert cancel.process_one(b,now=1000)['state']=='UNCERTAIN'
    b=SimpleNamespace(paper=True,account_fingerprint=b.account_fingerprint,_request=original)
    assert cancel.process_one(b,now=1301)['state']=='PENDING'
    raw['status']=dict(id=9,name='CanceledPartiallyFilled')
    assert cancel.process_one(b,now=1332)['label']=='Teilweise ausgeführt; Rest storniert'
    assert sum(c[0]=='DELETE' for c in calls)==1


@pytest.mark.parametrize('bad',['foreign','404','closed','conflicting'])
def test_no_write_on_unclear_or_terminal_preflight(monkeypatch,tmp_path,bad):
    b,raw,calls,params=setup(monkeypatch,tmp_path)
    if bad=='foreign':raw['accountId']=54321
    if bad=='404':b._request=lambda *a,**k:(_ for _ in ()).throw(ValueError('not found'))
    if bad=='closed':raw['status']=dict(id=7,name='Canceled')
    if bad=='conflicting':raw['status']=dict(id=7,name='Placed')
    cancel.enqueue(**params)
    result=cancel.process_one(b,now=1000)
    assert result['state']==('DONE' if bad=='closed' else 'REQUESTED')
    assert not any(c[0]=='DELETE' for c in calls)
