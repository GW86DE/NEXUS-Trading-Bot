import json
from fastapi.testclient import TestClient

import NEXUS_10_Diagnose as diagnosis
import trade_ledger as ledger
from test_v100_webui_backend import configured_auth
from test_v1015_etoro_settlement import ready
from test_v1015_etoro_cancellations import setup


def client(monkeypatch):
    from webui.app import app
    auth,_=configured_auth(monkeypatch)
    session,csrf=auth.issue_session('testuser')
    c=TestClient(app)
    return c,auth,session,{'X-CSRF-Token':csrf}


def test_settlement_requires_session_csrf_and_current_receipt(monkeypatch,tmp_path):
    tid,args,kw=ready(monkeypatch,tmp_path)
    c,auth,session,headers=client(monkeypatch)
    url=f'/api/etoro/settlement/{tid}'
    assert c.get(url).status_code==401
    assert c.post(url,json={}).status_code==401
    c.cookies.set(auth.COOKIE,session)
    p=c.get(url).json()
    assert p['proposed_exit_cost']=='1.00'
    payload=dict(token=p['token'],no_other_cashflows=True)
    assert c.post(url,json=payload).status_code==403
    assert c.post(url,headers=headers,json=dict(payload,no_other_cashflows='true')).status_code==409
    assert c.post(url,headers=headers,json=dict(payload,token='changed')).status_code==409
    assert ledger.trade_detail(tid)['netto_pnl'] is None
    assert c.post(url,headers=headers,json=payload).json()['updated']==1
    assert c.post(url,headers=headers,json=payload).json()['updated']==0
    assert c.get(url).json()['completed']


def test_cancel_webui_only_queues_without_broker_request(monkeypatch,tmp_path):
    broker,raw,calls,params=setup(monkeypatch,tmp_path)
    c,auth,session,headers=client(monkeypatch)
    url='/api/etoro/cancellations'
    assert c.get(url).status_code==401
    c.cookies.set(auth.COOKIE,session)
    assert c.post(url,json=params).status_code==403
    assert c.post(url,headers=headers,json=dict(params,paper='true')).status_code==409
    assert c.post(url,headers=headers,json=dict(params,account='other')).status_code==409
    result=c.post(url,headers=headers,json=params)
    assert result.status_code==200 and result.json()['state']=='REQUESTED'
    assert result.json()['actor']=='webui:testuser'
    assert c.get(url).json()['requests'][0]['state']=='REQUESTED'
    assert calls==[]


def test_diagnostic_separates_review_proceeds_and_pending_cancel():
    tables={'etoro_settlement_reviews':[{'trade_id':54,'reviewed_at':'2026-09-15',
        'actor':'private-name','proof_json':json.dumps(dict(account='one',paper=True,position_id='888',
            entry_cost='1',proposed_exit_cost='1',proposed_net='242.16',token='private-token'))}],
        'etoro_cash_receipts':[{'account':'one','paper':1,'observed_at':'2026-09-14T13:30Z'}],
        'etoro_cancellations':[{'account':'one','paper':1,'order_id':'777','state':'PENDING','label':'Angefragt','detail_json':'private-event'}],
        'etoro_proceeds_observations':[{'account':'one','paper':1,'order_id':'999','observed_at':'2026-09-14','receipt_json':'private-raw'}],
        'execution_orders':[{'broker':'etoro','state':'POSITION_CLOSED_ORDER_UNPROVEN','order_id':'old'}]}
    meta={'decision_history.sqlite':{'tables':{k:{'status':'OK','truncated':False} for k in tables}}}
    report=diagnosis.etoro_settlement_report({'decision_history.sqlite':tables},meta)
    assert report['reviews'][0]['quality']=='USER_CONFIRMED'
    assert report['reviews'][0]['net']=='242.16'
    assert report['cancellations'][0]['state']=='PENDING'
    assert report['closed_position_old_orders'][0]['order_id']=='old'
    assert 'private-' not in json.dumps(report)
    gaps=diagnosis.historical_cost_gaps([dict(broker='etoro',ausgestiegen_am='2026-09-14',
        fee_quality='USER_CONFIRMED',entry_fee_quality='CONFIRMED',exit_fee_quality='USER_CONFIRMED',netto_pnl=242.16)],
        {'status':'OK','truncated':False})
    assert not gaps['brokers']['etoro']['fee_evidence_incomplete']
