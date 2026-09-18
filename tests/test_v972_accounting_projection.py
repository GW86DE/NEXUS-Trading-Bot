"""Evidence-based resolution, not deletion of financial warnings."""
from copy import deepcopy
from datetime import datetime, timezone
import json
import sqlite3
import pytest
from etoro_accounting_resolution import project, read_ledger

NOW = datetime(2026,9,7,10,tzinfo=timezone.utc)
DOMAIN = 'etoro:demo:accountA'

@pytest.fixture
def case():
    record = {'decision_id':1,'symbol':'ABC','paper':True,'domain':DOMAIN,'account_fingerprint':'accountA',
        'order_ids':['order1'],'position_ids':['pos1'],'closed_position_ids':['pos1'],
        'fills':[{'position_id':'pos1','quantity':2,'price':100}],
        'ledger_close_backfill':{'pos1':{'status':'LINKED_CLOSED','trade_id':1}},
        'close_evidence_by_position_id':{'pos1':{'_terminal_close_confirmed':True,'quantity':2,
            'closePrice':110,'closed_at_utc':'2026-09-01T14:00:00Z'}}}
    gap = {'symbol':'ABC','broker_position_id':'pos1','domaene':DOMAIN,'zeit':'2026-09-07T09:00:00Z'}
    trade = {'trade_id':1,'broker':'etoro','symbol':'ABC','paper':1,'broker_account_fingerprint':'accountA',
        'broker_position_id':'pos1','entry_order_id':'order1','menge':2,'einstieg_preis':100,
        'ausstieg_preis':110,'ausgestiegen_am':'2026-09-01T14:00:00+00:00','netto_pnl':20,
        'einstieg_gebuehr':0,'gebuehren':0,'fee_quality':'CONFIRMED','waehrung':'USD'}
    event = {'trade_id':1,'broker':'etoro','instrument':'pos1','broker_account_fingerprint':'accountA',
        'fill_id':'exit1','quantity':2,'price':110}
    return {'records':{'1':record},'unzugeordnete_verkaeufe':[gap]}, {'trades':[trade],'exits':[event],'error':''}


def view(case, **kw):
    return project(*case, domain=kw.pop('domain',DOMAIN), now=NOW, **kw)


def test_read_only_and_repeatable(case):
    before=deepcopy(case)
    a=view(case);b=view(case)
    assert a==b and case==before
    assert len(a['resolved'])==1 and not a['blocking']
    assert a['resolved'][0]['trade_ids']==[1]

@pytest.mark.parametrize('field,value', [('broker_position_id','other'),('entry_order_id','other'),
    ('broker_account_fingerprint','other'),('paper',0),('symbol','OTHER'),('menge',3),
    ('einstieg_preis',99),('ausstieg_preis',111),('ausgestiegen_am',None),('superseded_by',2)])
def test_fake_success_does_not_resolve(case,field,value):
    case[1]['trades'][0][field]=value
    assert len(view(case)['blocking'])==1 and not view(case)['resolved']

@pytest.mark.parametrize('mutation',['missing_trade','missing_event','different_event_account','live_gap','conflicting_evidence','recent_fees','undated','malformed'])
def test_safety_boundaries(case,mutation):
    data,ledger=case
    if mutation=='missing_trade':ledger['trades']=[]
    if mutation=='missing_event':ledger['exits']=[]
    if mutation=='different_event_account':ledger['exits'][0]['broker_account_fingerprint']='other'
    if mutation=='live_gap':
        data['unzugeordnete_verkaeufe'][0]['domaene']='etoro:live:accountA'
        assert not view(case)['items'];return
    if mutation=='conflicting_evidence':data['unzugeordnete_verkaeufe'][0]['evidence_conflict']=True
    if mutation=='recent_fees':
        ledger['trades'][0].update(fee_quality='UNKNOWN',ausgestiegen_am='2026-09-07T09:00:00Z')
        data['records']['1']['close_evidence_by_position_id']['pos1']['closed_at_utc']='2026-09-07T09:00:00Z'
    if mutation=='undated':ledger['trades'][0]['ausgestiegen_am']='2026-09-01T14:00:00'
    if mutation=='malformed':data['unzugeordnete_verkaeufe']={}
    assert view(case)['blocking']


def test_observation_day_does_not_become_trade_day(case):
    case[1]['trades'][0]['fee_quality']='UNKNOWN'
    result=view(case)
    assert not result['blocking']
    assert result['resolved'][0]['result_status']=='FEES_OR_RESULT_PENDING'
    assert case[1]['trades'][0]['fee_quality']=='UNKNOWN'

@pytest.mark.parametrize('field,value',[('quantity',3),('price',112),('closed_at_utc','2026-09-01T15:00:00Z'),('fee',7),('fee_currency','EUR')])
def test_new_conflicting_payload_not_ignored(case,field,value):
    evidence={'quantity':2,'price':110,'closed_at_utc':'2026-09-01T14:00:00Z','fee':0,'fee_currency':'USD'}
    evidence[field]=value
    case[0]['unzugeordnete_verkaeufe'][0]['close_evidence']=[evidence]
    assert view(case)['blocking']


def test_legacy_is_audit_not_a_trade(case):
    case[1]['trades']=[]
    r=case[0]['records']['1'];r.update(domain='etoro:demo',account_fingerprint='',legacy_unbound_closed=True)
    r['ledger_close_backfill']['pos1']={'status':'LEDGER_BACKFILL_LEGACY_UNBOUND'}
    result=view(case)
    assert len(result['legacy'])==1 and not result['blocking']
    assert result['legacy'][0]['trade_ids']==[]
    assert r['account_fingerprint']==''


def test_recent_new_legacy_fill_stays_pending(case):
    r=case[0]['records']['1'];r.update(domain='etoro:demo',account_fingerprint='',legacy_unbound_closed=True)
    r['ledger_close_backfill']['pos1']={'status':'LEDGER_BACKFILL_LEGACY_UNBOUND'}
    case[0]['unzugeordnete_verkaeufe'][0]['close_evidence']=[{'quantity':2,'price':111}]
    assert view(case)['blocking']


def test_only_proven_alias_matches(case):
    data=case[0];data['unzugeordnete_verkaeufe'][0]['domaene']='etoro:demo:old'
    data['konto_aliase']={'old':'accountA'}
    assert not view(case)['items']
    data['konto_alias_belege']={'old':{'neu':'accountA','beleg':'order_id=order1'}}
    assert len(view(case)['resolved'])==1


def test_corrupt_and_missing_db_not_created(tmp_path):
    path=tmp_path/'missing.sqlite';assert read_ledger(path)['error'];assert not path.exists()
    path.write_text('garbage');assert read_ledger(path)['error']


def test_corrupt_state_blocks_even_without_gap():
    report=project({'storage_error':'error'}, {'trades':[],'exits':[],'error':''},domain=DOMAIN,now=NOW)
    assert report['blocking'][0]['status']=='STORAGE_ERROR'


def test_gap_writer_is_domain_scoped(tmp_path,monkeypatch):
    import etoro_reconciliation as rec
    monkeypatch.setenv('TRADINGBOT_TEST_STATE_DIR',str(tmp_path))
    for domain in (DOMAIN,'etoro:live:accountA','etoro:demo:other'):
        rec.melde_buchungsluecke(symbol='ABC',broker_position_id='pos1',domain=domain)
    data=json.loads(rec.path().read_text())
    assert len(data['unzugeordnete_verkaeufe'])==3
    rec.melde_buchungsluecke(symbol='ABC',broker_position_id='pos1',domain=DOMAIN)
    assert len(json.loads(rec.path().read_text())['unzugeordnete_verkaeufe'])==3


def test_malformed_register_cannot_be_acknowledged(tmp_path,monkeypatch):
    import etoro_reconciliation as rec
    monkeypatch.setenv('TRADINGBOT_TEST_STATE_DIR',str(tmp_path))
    rec.path().write_text('{')
    with pytest.raises(Exception):
        rec.melde_buchungsluecke(symbol='ABC',broker_position_id='pos1',domain=DOMAIN)
    assert rec.path().read_text()=='{'


def test_legacy_never_released_if_ledger_cannot_be_read(case):
    case[1].update(error='missing db',trades=[])
    r=case[0]['records']['1'];r.update(domain='etoro:demo',account_fingerprint='',legacy_unbound_closed=True)
    r['ledger_close_backfill']['pos1']={'status':'LEDGER_BACKFILL_LEGACY_UNBOUND'}
    assert view(case)['blocking'] and not view(case)['legacy']


def test_legacy_does_not_hide_an_existing_bound_trade(case):
    r=case[0]['records']['1'];r.update(domain='etoro:demo',account_fingerprint='',legacy_unbound_closed=True)
    r['ledger_close_backfill']['pos1']={'status':'LEDGER_BACKFILL_LEGACY_UNBOUND'}
    assert view(case)['blocking'] and not view(case)['legacy']


def test_journal_price_contradiction_stays_pending(case):
    case[1]['exits'][0]['price']=115
    assert view(case)['blocking']


def test_stored_resolution_is_not_authoritative(case):
    case[0]['unzugeordnete_verkaeufe'][0]['resolution']={'status':'RESOLVED','blocks_entries':False}
    case[1]['trades'][0]['menge']=3
    assert view(case)['blocking']


def test_explicit_broker_position_contradiction_stays_pending(case):
    case[0]['records']['1']['position_state']='OPEN_CONFIRMED'
    assert view(case)['blocking']


def test_malformed_record_not_ignored(case):
    case[0]['records']['bad']=[]
    assert view(case)['storage_error'] and view(case)['blocking']
