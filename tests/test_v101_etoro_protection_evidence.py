from copy import deepcopy
import json
import pytest


def pep():
    return dict(positionId='fixture-pep-position',instrumentId=1043,units=109,isBuy=True,
        stopLossRate=135.9,takeProfitRate=138.52,isNoStopLoss=False,isNoTakeProfit=False)


def rule():
    return dict(kind='VERIFIED_BROKER_PRICE_RULE',instrument_id='1043',account='fixture',
        environment='DEMO',tick_size='0.01',stop_rounding='UP',take_rounding='DOWN',source_reference='explicit-test-rule',
        source_sha256='a'*64)


def test_real_pep_without_rule_remains_unconfirmed_and_explains_required_proof():
    from etoro_protection_evidence import assess
    p=pep();view=assess([p],{p['positionId']},135.8946,138.5207,quantity=109,instrument_id=1043,snapshot_id='snapshot')
    assert not view['confirmed']
    assert view['reason_code']=='ETORO_PROTECTION_PRICE_RULE_UNPROVEN'
    assert view['precision']['status']=='UNPROVEN' and view['sent'] is None
    assert view['observed'][0]['stop']==135.9


def test_hypothetical_proven_per_leg_rule_can_normalize_pep_numbers():
    from etoro_protection_evidence import normalize,assess
    p=pep();normalized=normalize(135.8946,138.5207,rule(),instrument_id=1043,account='fixture',environment='DEMO')
    view=assess([p],{p['positionId']},135.8946,138.5207,quantity=109,instrument_id=1043,normalized=normalized)
    assert normalized['stop']==135.9 and normalized['take_profit']==138.52
    assert view['confirmed'] and view['precision']['status']=='PROVEN'


@pytest.mark.parametrize('change',[{'environment':'LIVE'},{'account':'other'},{'instrument_id':'999'},
    {'source_sha256':''},{'source_reference':''},{'kind':'DISPLAY_DECIMALS'},{'stop_rounding':'GUESS'}])
def test_wrong_or_missing_normalization_provenance_is_rejected(change):
    from etoro_protection_evidence import normalize
    with pytest.raises(ValueError):normalize(135.8946,138.5207,{**rule(),**change},instrument_id=1043,account='fixture',environment='DEMO')


def test_rounding_may_not_deepen_long_stop():
    from etoro_protection_evidence import normalize
    with pytest.raises(ValueError,match='INCREASES_RISK'):
        normalize(135.8946,138.5207,{**rule(),'stop_rounding':'DOWN'},instrument_id=1043,account='fixture',environment='DEMO')


@pytest.mark.parametrize('change',[{'units':108},{'positionId':'other'},{'instrumentId':999},
    {'isNoStopLoss':True},{'isNoTakeProfit':'false'},{'stopLossRate':0},{'stopLossRate':135.91}])
def test_wrong_native_protection_or_identity_cannot_confirm(change):
    from etoro_protection_evidence import assess
    p=pep();p.update(change)
    assert not assess([p],{'fixture-pep-position'},135.9,138.52,quantity=109,instrument_id=1043)['confirmed']


def test_duplicate_position_rows_do_not_fake_complete_snapshot():
    from etoro_protection_evidence import assess
    p=pep();assert not assess([p,deepcopy(p)],{p['positionId']},135.9,138.52)['confirmed']


def test_patch_timeout_restart_does_not_send_second_request(monkeypatch,tmp_path):
    from datetime import datetime, timezone
    from broker.etoro import EtoroBroker
    monkeypatch.setenv('TRADINGBOT_TEST_STATE_DIR',str(tmp_path))
    b=EtoroBroker(paper=True,api_key='test',user_key='test')
    b._bind_account_identity({'demoCid':123456})
    b._resolve=lambda _:dict(instrumentId=1043,symbol='PEP')
    p=pep();snapshot={'_snapshot_id':'s1','positions':[p]}
    b._pnl=lambda **kw:snapshot
    b._all_positions=lambda d:d['positions']
    calls=[]
    def timeout(*args,**kw):calls.append(kw);raise TimeoutError('offline fixture')
    b._request=timeout
    args=dict(position_ids=[p['positionId']],instrument_id='1043',update_requested=True)
    with pytest.raises(TimeoutError):b.reconcile_position_protection('PEP',109,135.95,139,**args)
    with pytest.raises(ValueError,match='RECONCILIATION_REQUIRED'):
        b.reconcile_position_protection('PEP',109,135.95,139,**args)
    assert len(calls)==1
    # Missing response followed by an authoritative matching snapshot recovers.
    p.update(stopLossRate=135.95,takeProfitRate=139)
    snapshot['_snapshot_id']='s2'
    # New journal contracts require a post-submit timestamp, as real _pnl supplies.
    snapshot['_snapshot_at']=datetime.now(timezone.utc).isoformat()
    result=b.reconcile_position_protection('PEP',109,135.95,139,**args)
    assert result['protection_confirmed'] and not result['changed'] and len(calls)==1
    data=json.loads((tmp_path/'etoro_protection_journal.json').read_text())
    row=next(iter(data['records'].values()))
    assert row['state']=='CONFIRMED' and row['payload']['stopLossRate']==135.95


def test_quantity_mismatch_prevents_patch(monkeypatch,tmp_path):
    from broker.etoro import EtoroBroker
    monkeypatch.setenv('TRADINGBOT_TEST_STATE_DIR',str(tmp_path))
    b=EtoroBroker(paper=True,api_key='test',user_key='test');b._resolve=lambda _:dict(instrumentId=1043,symbol='PEP')
    b._pnl=lambda **kw:{'positions':[pep()]};b._all_positions=lambda d:d['positions']
    b._request=lambda *a,**kw:pytest.fail('must not send PATCH')
    view=b.reconcile_position_protection('PEP',108,135.9,138.52,position_ids=['fixture-pep-position'],update_requested=True)
    assert view['reason_code']=='ETORO_PROTECTION_QUANTITY_MISMATCH'


def test_proven_rejection_and_confirmed_replay_are_not_unknown_writes(monkeypatch,tmp_path):
    import etoro_protection_journal as journal
    monkeypatch.setenv('TRADINGBOT_TEST_STATE_DIR',str(tmp_path))
    payload=dict(stopLossRate=135.9,takeProfitRate=138.52,stopLossType='fixed')
    journal.prepare('a','DEMO','p','r1',payload,None)
    journal.rejected('a','DEMO','p','r1','AuftragAbgelehnt')
    journal.prepare('a','DEMO','p','r2',payload,None)
    receipt=journal.confirm('a','DEMO','p',135.9,138.52,'snapshot')
    assert receipt['state']=='CONFIRMED'
    content=journal.path().read_bytes()
    assert journal.confirm('a','DEMO','p',135.9,138.52,'new-snapshot')['state']=='CONFIRMED'
    assert journal.path().read_bytes()==content


def test_unknown_flag_cannot_confirm_pending_patch(monkeypatch,tmp_path):
    import etoro_protection_journal as journal
    monkeypatch.setenv('TRADINGBOT_TEST_STATE_DIR',str(tmp_path))
    journal.prepare('a','DEMO','p','r',dict(stopLossRate=135.9,takeProfitRate=138.52),None)
    assert journal.confirm('a','DEMO','p',135.9,138.52,'snapshot',flags={'isNoStopLoss':True})['state']=='SUBMITTING'


def test_protection_journal_write_failure_prevents_broker_patch(monkeypatch,tmp_path):
    from broker.etoro import EtoroBroker
    import etoro_protection_journal as journal
    monkeypatch.setenv('TRADINGBOT_TEST_STATE_DIR',str(tmp_path))
    b=EtoroBroker(paper=True,api_key='test',user_key='test');b._bind_account_identity({'demoCid':123})
    b._resolve=lambda _:dict(instrumentId=1043,symbol='PEP')
    b._pnl=lambda **kw:{'_snapshot_id':'s','positions':[pep()]};b._all_positions=lambda d:d['positions']
    b._request=lambda *a,**kw:pytest.fail('no PATCH without durable intent')
    def fail(*a,**kw):raise OSError('disk full')
    monkeypatch.setattr(journal,'atomic_write_json',fail)
    with pytest.raises(OSError):b.reconcile_position_protection('PEP',109,136,139,position_ids=['fixture-pep-position'],update_requested=True)
