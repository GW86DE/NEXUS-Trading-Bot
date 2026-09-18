"""Durable pending-OPEN cancellation queue. Only the broker worker sends DELETE.

The cancellation UUID is never a lookup reference: v3 cancellation returns an
empty referenceId. Recovery always reads the original orderId and never repeats
a possibly submitted DELETE. Native SL/TP and close intents are not cancelled.
"""
from __future__ import annotations
import hashlib
import json
import time
import uuid

import trade_ledger as ledger
import etoro_reconciliation as rec
from etoro_order_status import lookup_status


def table(con):
    con.execute('''CREATE TABLE IF NOT EXISTS etoro_cancellations (
        account TEXT NOT NULL, paper INTEGER NOT NULL, order_id TEXT NOT NULL,
        decision_id INTEGER NOT NULL, request_id TEXT NOT NULL, actor TEXT NOT NULL,
        state TEXT NOT NULL, label TEXT NOT NULL, created_at REAL NOT NULL,
        checked_at REAL NOT NULL DEFAULT 0, next_at REAL NOT NULL DEFAULT 0,
        detail_json TEXT NOT NULL DEFAULT '{}', PRIMARY KEY(account,paper,order_id))''')


def _record(account, paper, oid):
    rows = [r for r in (rec._load().get('records') or {}).values()
        if r.get('account_fingerprint') == account and r.get('paper') is paper
        and str(oid) in {str(x) for x in r.get('order_ids') or []}]
    if len(rows) != 1:
        raise ValueError('Kein eindeutig zugeordneter NEXUS-Kaufauftrag')
    return rows[0]


def enqueue(*, account, paper, order_id, actor):
    oid = str(order_id)
    if not account or not isinstance(paper, bool) or not oid.isdigit() or not str(actor).strip():
        raise ValueError('Konto, Umgebung, Order-ID und Auftraggeber erforderlich')
    r = _record(account,paper,oid)
    if (r.get('execution_state') in {'FILLED','REJECTED','CANCELLED','EXPIRED','CANCELED_PARTIALLY_FILLED','REJECTED_PARTIALLY_FILLED'}
            or r.get('position_state') == 'CLOSED_CONFIRMED'):
        raise ValueError('Dieser Auftrag ist bereits beendet')
    ledger.init_ledger()
    with ledger._connect() as con:
        table(con)
        con.execute('INSERT OR IGNORE INTO etoro_cancellations (account,paper,order_id,decision_id,request_id,actor,state,label,created_at) VALUES (?,?,?,?,?,?,?,?,?)',
            (account,int(paper),oid,int(r['decision_id']),str(uuid.uuid4()),str(actor)[:160],
             'REQUESTED','Stornierung vorgemerkt',time.time()))
        return dict(con.execute('SELECT * FROM etoro_cancellations WHERE account=? AND paper=? AND order_id=?',(account,int(paper),oid)).fetchone())


def overview():
    ledger.init_ledger()
    with ledger._connect() as con:
        table(con)
        result = [dict(r) for r in con.execute('SELECT * FROM etoro_cancellations ORDER BY created_at DESC LIMIT 100')]
    for r in result:
        r.pop('detail_json',None)
        r['paper'] = bool(r['paper'])
    return result


def available():
    result = []
    for r in (rec._load().get('records') or {}).values():
        if (r.get('execution_state') in rec.NON_TERMINAL
                and len(r.get('order_ids') or []) == 1 and r.get('account_fingerprint')
                and isinstance(r.get('paper'), bool) and r.get('position_state') != 'CLOSED_CONFIRMED'):
            result.append(dict(account=r['account_fingerprint'], paper=r['paper'],
                order_id=str(r['order_ids'][0]), symbol=r.get('symbol'),
                label=r.get('broker_status_label') or r.get('execution_state'),
                filled_quantity=r.get('filled_quantity'), remaining_quantity=r.get('remaining_quantity')))
    return result[:100]


def process_one(broker, now=None):
    now = time.time() if now is None else float(now)
    account, paper = str(broker.account_fingerprint() or ''), broker.paper
    if not account or not isinstance(paper,bool):
        return None
    ledger.init_ledger()
    with ledger._connect() as con:
        table(con); con.commit(); con.execute('BEGIN IMMEDIATE')
        row = con.execute("SELECT * FROM etoro_cancellations WHERE account=? AND paper=? AND state IN ('REQUESTED','SENDING','PENDING','UNCERTAIN') AND next_at<=? ORDER BY checked_at,created_at LIMIT 1",(account,int(paper),now)).fetchone()
        if not row:
            return None
        row = dict(row)
        con.execute('UPDATE etoro_cancellations SET next_at=?,checked_at=? WHERE account=? AND paper=? AND order_id=?',(now+120,now,account,int(paper),row['order_id']))
    state, label, detail = 'UNCERTAIN','Auftragsausgang wird geprüft',{}
    sent = row['state'] != 'REQUESTED'
    try:
        current = _record(account,paper,row['order_id'])
        path = '/api/v2/trading/info/demo/orders:lookup' if paper else '/api/v2/trading/info/orders:lookup'
        raw = broker._request('GET',path,params={'orderId':row['order_id']})
        cid = str(raw.get('accountId') or '')
        expected = hashlib.sha256(f"etoro|{'demo' if paper else 'live'}|cid:{cid}".encode()).hexdigest()[:24]
        if (not cid.isdigit() or expected != account or str(raw.get('orderId')) != row['order_id']
                or raw.get('action') != 'open' or raw.get('transaction') != 'buy'
                or broker.account_fingerprint() != account or broker.paper is not paper):
            raise ValueError('Stornoabgleich: Konto oder Kaufauftrag widerspricht dem Beleg')
        phase = lookup_status(raw.get('status'))
        if not phase['known']:
            raise ValueError(phase['label'])
        # Exact account/entry ID checks in the existing path preserve fills.
        rec.apply_broker_evidence(int(current['decision_id']),raw)
        detail = dict(status=raw.get('status'),order_id=row['order_id'],
            position_ids=[str(x.get('positionId')) for x in raw.get('positionExecutions') or []])
        if phase['terminal']:
            state, label = 'DONE', phase['label']
        elif phase['code'] == 6:
            state, label = 'PENDING', phase['label']
        elif row['state'] == 'REQUESTED':
            # Persist BEFORE the write. A crash/timeout now causes read-only recovery.
            with ledger._connect() as con:
                changed = con.execute("UPDATE etoro_cancellations SET state='SENDING',label='Stornoausgang noch unbestätigt' WHERE account=? AND paper=? AND order_id=? AND state='REQUESTED'",(account,int(paper),row['order_id'])).rowcount
            if not changed:
                return None
            sent = True
            target = f"/api/v3/trading/execution/{'demo/' if paper else ''}orders/{row['order_id']}"
            response = broker._request('DELETE',target,request_id=row['request_id'],safe_retry=False)
            returned = str(response.get('orderId') or '') if isinstance(response,dict) else ''
            if returned != row['order_id']:
                raise ValueError('Stornoantwort ohne passende Order-ID; Ausgang wird nachgelesen')
            state, label = 'PENDING','Storno angefragt; Brokerbestätigung ausstehend'
        else:
            state, label = 'PENDING', 'Storno noch unbestätigt; '+phase['label']
    except Exception as exc:
        from provider_safety import redact
        detail = dict(error=redact(str(exc)))
        state = 'UNCERTAIN' if sent else 'REQUESTED'
        label = 'Stornoausgang ungeklärt; kein erneutes Senden' if sent else 'Vorprüfung ausstehend; Storno noch nicht gesendet'
    with ledger._connect() as con:
        con.execute('UPDATE etoro_cancellations SET state=?,label=?,detail_json=?,next_at=? WHERE account=? AND paper=? AND order_id=? AND checked_at=?',
            (state,label,json.dumps(detail,ensure_ascii=False),now+(300 if state=='UNCERTAIN' else 30),account,int(paper),row['order_id'],now))
    return dict(order_id=row['order_id'],state=state,label=label)
