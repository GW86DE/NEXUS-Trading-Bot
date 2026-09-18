"""Passive diagnosis regressions; synthetic fixtures, optional original ZIP replay."""
import copy
import json
import os
from pathlib import Path
import sqlite3
import subprocess

import pytest

import NEXUS_10_Diagnose as d


def test_standalone_starter_matches_source_and_runs_without_neighbour_python(tmp_path):
    standalone=os.environ.get('NEXUS_STANDALONE_DIAGNOSIS')
    if not standalone:
        pytest.skip('Standalone delivery supplied explicitly through NEXUS_STANDALONE_DIAGNOSIS')
    root=Path(__file__).resolve().parents[1]
    starter=Path(standalone)
    isolated=tmp_path/'NEXUS_10.1.1_Diagnose_Starten.sh'
    isolated.write_bytes(starter.read_bytes())
    expected=d.digest((root/'NEXUS_10_Diagnose.py').read_bytes())
    assert not (tmp_path/'NEXUS_10_Diagnose.py').exists()
    checked=subprocess.run(['bash',str(isolated),'--werkzeug-pruefen'],cwd=tmp_path,
                           text=True,capture_output=True,timeout=20)
    assert checked.returncode==0, checked.stderr
    assert expected in checked.stdout
    assert f'Diagnosewerkzeug {d.TOOL_VERSION}' in checked.stdout
    help_result=subprocess.run(['bash',str(isolated),'--help'],cwd=tmp_path,
                               text=True,capture_output=True,timeout=20)
    assert help_result.returncode==0, help_result.stderr
    assert '--sofort' in help_result.stdout and '--minuten' in help_result.stdout


def test_standalone_starter_rejects_payload_hash_mismatch_before_diagnosis(tmp_path):
    standalone=os.environ.get('NEXUS_STANDALONE_DIAGNOSIS')
    if not standalone:
        pytest.skip('Standalone delivery supplied explicitly through NEXUS_STANDALONE_DIAGNOSIS')
    root=Path(__file__).resolve().parents[1]
    expected=d.digest((root/'NEXUS_10_Diagnose.py').read_bytes())
    script=Path(standalone).read_text().replace(expected,'0'*64)
    isolated=tmp_path/'bad.sh';isolated.write_text(script)
    checked=subprocess.run(['bash',str(isolated),'--werkzeug-pruefen'],cwd=tmp_path,
                           text=True,capture_output=True,timeout=20)
    assert checked.returncode==2
    assert 'beschaedigt' in checked.stderr
    assert not any(tmp_path.glob('*.zip'))


def top(payload, saved=100, expires=200):
    return {'key':'top5','saved':saved,'expires':expires,'payload':payload}


def fallback(row, run='run', observed=150):
    return {'row':row,'source':'verlauf/objekte/pulsar_cache_top5_x.json',
            'observed_at':d.utc(observed),'run_id':run,'sha256_sanitized':d.digest(d.dumps(row).encode())}


def report(rows, evidence=None, run='run'):
    return d.pulsar_report(rows,{'requests':[]},180,fallback=evidence,run_id=run)


@pytest.mark.parametrize('rows,status', [([], 'NOT_EXPORTED'),([top(d.OVERSIZE)], 'OMITTED_OVERSIZE_CELL'),
    ([top('bad json')], 'UNREADABLE_PAYLOAD'),([top([None])], 'INVALID_CARD_ROWS')])
def test_lost_cells_are_unknown(rows,status):
    result=report(rows)
    assert result['card_count'] is None
    assert result['cards_status']==status


def test_explicit_empty_is_zero_and_complete_export_wins():
    assert report([top([])])['card_count']==0
    result=report([top([])],[fallback(top([{'symbol':'OLD'}]))])
    assert result['card_count']==0
    assert result['cards_status']=='EXPORTED'


def test_same_revision_recovered_without_rewriting_saved_time():
    result=report([top(d.OVERSIZE)],[fallback(top([{'symbol':'PEP'}]))])
    assert result['card_count']==1
    assert result['cards_status']=='RECOVERED_FROM_SAME_RUN_SAMPLE'
    assert result['top5_saved']==100
    assert result['top5_age_seconds']==80
    assert result['cards_provenance']['observed_at']==d.utc(150)


@pytest.mark.parametrize('kind',['run','saved','expires','hash','future','future_saved'])
def test_wrong_or_unverifiable_sample_cannot_confirm_end_state(kind):
    row=top([{'symbol':'PEP'}])
    evidence=fallback(row)
    if kind=='run': evidence['run_id']='other'
    if kind in {'saved','expires'}:
        row[kind]+=1
        evidence=fallback(row)
    if kind=='hash': evidence['sha256_sanitized']='0'*64
    if kind=='future': evidence['observed_at']=d.utc(999)
    if kind=='future_saved': evidence=fallback(top([{'symbol':'PEP'}],saved=190))
    result=report([top(d.OVERSIZE)],[evidence])
    assert result['card_count'] is None
    if kind in {'saved','expires'}:
        assert result['other_observed_revisions'][0]['card_count']==1


def test_sample_loader_requires_archive_reference_and_matching_hash():
    row=top([{'symbol':'PEP'}]); source='verlauf/objekte/pulsar_cache_top5_x.json'
    ref={'file':source,'sha256_sanitized':d.digest(d.dumps(row).encode())}
    samples=[{'at':d.utc(145),'objects':{'pulsar_cache_top5':ref}},
             {'at':d.utc(150),'objects':{'pulsar_cache_top5':ref}}]
    recovered=d.pulsar_sample_evidence(samples,lambda _:row,'run')
    assert len(recovered)==1 and recovered[0]['observed_at']==d.utc(150)
    ref['sha256_sanitized']='bad'
    assert d.pulsar_sample_evidence(samples,lambda _:row,'run')==[]


def test_real_sqlite_export_records_oversize_cell_loss(tmp_path):
    path=tmp_path/'pulsar_research.sqlite'
    with sqlite3.connect(path) as con:
        con.execute('CREATE TABLE cache (key TEXT,saved REAL,expires REAL,payload TEXT)')
        con.execute('INSERT INTO cache VALUES (?,?,?,?)',('top5',100,200,json.dumps([{'large':'x'*(2*d.MIB)}])))
    bundle=d.Bundle(tmp_path/'out')
    meta,rows=d.export_database(path,bundle,'ende/datenbanken/pulsar_research.sqlite')
    assert meta['status']=='PARTIAL_LIMITED'
    assert meta['tables']['cache']['omitted_cell_count']==1
    assert meta['tables']['cache']['cell_completeness']=='PARTIAL'
    assert rows['cache'][0]['payload']==d.OVERSIZE
    assert report(rows['cache'])['card_count'] is None


def event(identity,start,finish=None,phase='SUCCEEDED'):
    return {'zeit':d.utc(finish if finish is not None else start), 'ok':phase=='SUCCEEDED',
            'execution':{'local_request_id':identity,'started_at':d.utc(start),
                         'finished_at':d.utc(finish) if finish is not None else None,
                         'requested_at':d.utc(start),'phase':phase,'request_dispatched':True,
                         'response_received':phase=='SUCCEEDED'}}


def interval_meta():
    return dict(zip(('started_utc','observation_start_utc','observation_end_utc','ended_utc'),map(d.utc,(0,10,20,30))))


def calls_meta(**changes):
    table={'status':'OK','truncated':False,**changes}
    return {name:{'tables':{'calls':table}} for name in ('fmp_service.sqlite','massive_service.sqlite')}


def test_phase_boundaries_crossing_completion_and_duplicate_exports():
    audits=[event('a',5,12),event('b',10,20),event('c',20,30)]
    rows=[{'id':1,'at':5},{'id':2,'at':10},{'id':3,'at':20},{'id':4,'at':30}]
    dbs={'fmp_service.sqlite':{'calls':rows},'massive_service.sqlite':{'calls':[]}}
    phases=d.phase_counts(audits+audits,dbs,calls_meta(),d.phase_intervals(interval_meta(),10,20),
                          initial_dbs=copy.deepcopy(dbs),audit_meta={'status':'OK'})
    assert [phases[p]['gpt']['local_dispatches_in_window']['count'] for p in ('startup','observation','shutdown','total')]==[1,1,1,3]
    assert [phases[p]['gpt']['successful_results_in_window']['count'] for p in ('startup','observation','shutdown','total')]==[0,1,2,3]
    assert [phases[p]['fmp']['count'] for p in ('startup','observation','shutdown','total')]==[1,1,2,4]
    assert phases['startup']['gpt']['pending_started_requests']['count']==1


def test_missing_truncated_or_idless_sources_never_claim_complete_zero():
    intervals={'total':(0,30,True)}
    result=d.phase_counts([],{}, {}, intervals)['total']
    assert result['gpt']['local_dispatches_in_window']['count'] is None
    assert result['fmp']['count'] is None
    rows={'fmp_service.sqlite':{'calls':[{'at':5,'id':1},{'at':7}]}}
    result=d.phase_counts([],rows,calls_meta(truncated=True),intervals,audit_meta={'status':'OK','truncated':True})['total']
    assert result['fmp']['quality']=='LOWER_BOUND' and result['fmp']['count']==1
    assert result['gpt']['local_dispatches_in_window']['quality']=='LOWER_BOUND'


def test_pending_cache_and_not_started_are_separate():
    records=[event('pending',5,phase='DISPATCHED'),event('cache',6,7,'CACHE_HIT'),event('not',8,9,'NOT_STARTED')]
    records[1]['execution']['request_dispatched']=False
    records[2]['execution']['request_dispatched']=False
    result=d.phase_counts(records,{}, {}, {'total':(0,30,True)},audit_meta={'status':'OK'})['total']['gpt']
    assert result['pending_started_requests']['count']==1
    assert result['cache_uses_in_window']['count']==1
    assert result['not_started']['count']==1


def test_source_errors_respect_time_alias_and_activation():
    sources={'FMP News':{'ok':False,'historical_alias':True,'canonical_provider':'FMP','time':d.utc(15)},
             'FMP':{'ok':True,'time':d.utc(15)},'GDELT':{'ok':False,'time':d.utc(15)},
             'finanzen.net':{'ok':False,'time':d.utc(15)},'Old':{'ok':False,'time':d.utc(1)},
             'Unknown':{'ok':False}}
    r=d.provider_history(sources,{'gdelt_enabled':True,'legacy_optional_sources_enabled':False},10,20)
    assert {v['source'] for v in r['current']}=={'FMP','GDELT'}
    assert {v['source'] for v in r['historical']}=={'FMP News','finanzen.net','Old'}
    assert {v['source'] for v in r['unknown']}=={'Unknown'}


def test_input_receipt_needs_inclusion_validated_response_and_audit():
    ex=event('req',5,6)
    ai=d.ai_report([ex],0,30)
    source={'provider':'FMP','source_id':'src','included':True,'kind':'bars'}
    stage={'stage':'analysis','ok':True,'local_request_id':'req','audit_match':True,'input_hash':'a'*64,
           'input_sources':{'MU':{'status':'INPUT_OF_VALIDATED_RESPONSE','sources':[source]}}}
    pulsar={'cards':[{'stages':[stage,stage]}]}
    result=d.fmp_input_usage(pulsar,ai)
    assert result['requests_with_proven_fmp_input']==1 and len(result['input_receipts'])==1
    stage['audit_match']=False
    assert d.fmp_input_usage(pulsar,ai)['status']=='NOT_PROVEN'
    stage['audit_match']=True; source['included']=False
    assert d.fmp_input_usage(pulsar,ai)['requests_with_proven_fmp_input']==0


def test_historical_net_unknown_stays_unknown_and_is_not_open_position():
    rows=[{'broker':'etoro','symbol':'PEP','ausgestiegen_am':None,'netto_pnl':None},
          {'broker':'etoro','symbol':'MU','ausgestiegen_am':d.utc(1),'netto_pnl':None,
           'entry_fee_quality':'CONFIRMED','exit_fee_quality':'UNKNOWN','fee_quality':'UNKNOWN'}]
    r=d.historical_cost_gaps(rows,{'status':'OK','truncated':False})
    group=r['brokers']['etoro']
    assert group['closed_rows_observed']==1
    assert group['net_result_missing'][0]['netto_pnl'] is None
    assert group['complete_performance_proven'] is False


def test_summary_with_missing_audit_and_legacy_cell_loss():
    database={'pulsar_research.sqlite':{'cache':[top(d.OVERSIZE)]}}
    metadata={'pulsar_research.sqlite':{'status':'OK','tables':{'cache':{'status':'OK','truncated':False}}}}
    result=d.final_analysis({}, {},database,metadata,[],[],100,180)
    assert result['gpt']['local_dispatches_in_window'] is None
    assert result['database_exports']['pulsar_research.sqlite']['tables']['cache']['omitted_cell_count']==1
    assert metadata['pulsar_research.sqlite']['tables']['cache'].get('omitted_cell_count') is None
    findings=d.diagnostic_findings(result)
    assert any(r['code']=='TABLE_CELLS_OMITTED' for r in findings)
    assert any(r['code']=='PULSAR_CARDS_NOT_PROVEN' for r in findings)
    assert 'UNBEKANNT' in d.render_report(result,{})


def test_original_zip_replay_when_available():
    path=os.environ.get('NEXUS_REPLAY_ARCHIVE')
    if not path:
        pytest.skip('Original diagnosis ZIP supplied explicitly through NEXUS_REPLAY_ARCHIVE')
    a,m=d.replay_archive(Path(path))
    assert a['pulsar']['card_count']==5
    assert a['pulsar']['cards_status']=='RECOVERED_FROM_SAME_RUN_SAMPLE'
    assert a['collection_phases']['total']['gpt']['local_dispatches_in_window']['count']==3
    assert a['collection_phases']['observation']['gpt']['local_dispatches_in_window']['count']==0
    assert a['collection_phases']['startup']['fmp']['count']==12
    assert a['providers']['fmp_input_usage']['requests_with_proven_fmp_input']==3
    assert a['historical_cost_gaps']['brokers']['etoro']['net_result_missing_count']['count']==7
    assert {r['component'] for r in a['findings'] if r['code']=='SOURCE_REPORTED_ERROR'}=={'GDELT','Nasdaq Halts'}
    assert 'Gesamtlauf' in d.render_report(a,m)


def test_compact_top5_projection_proves_same_oversize_revision_and_fmp_input():
    source={'provider':'FMP','source_id':'fmp-bars','included':True,'kind':'bars'}
    card={'symbol':'MU','sources':[], 'packet':{'sources':[]}, 'text_source':'LUNA','thesis':'ok',
          'precheck':{'ok':True,'daten':{'thesis':{'text':'ok'},'source_ids':['fmp-bars']},
                      'execution':{'local_request_id':'req','phase':'SUCCEEDED','request_dispatched':True,'response_received':True},
                      'input_hash':'a'*64,
                      'input_sources':{'MU':{'status':'INPUT_OF_VALIDATED_RESPONSE','sources':[source]}}}}
    compact={'key':'diagnostic_top5','saved':100,'expires':200,
             'payload':{'top5_saved':100,'cards':[card]}}
    ai=d.ai_report([event('req',5,6)],0,180)
    result=d.pulsar_report([top(d.OVERSIZE),compact],ai,180)
    assert result['card_count']==1
    assert result['cards_status']=='EXPORTED_COMPACT_PROJECTION'
    assert result['cards'][0]['stages'][0]['audit_match'] is True
    usage=d.fmp_input_usage(result,ai)
    assert usage['requests_with_proven_fmp_input']==1


def test_bounded_table_is_info_not_unknown_incomplete():
    analysis={'runtime':{}, 'new_evidence':{},
              'providers':{'source_history':{'current':[]},'pipeline':{}},
              'pulsar':{'card_count':0,'cards':[]},
              'database_exports':{'x.sqlite':{'status':'PARTIAL_LIMITED','tables':{
                  'rows':{'status':'OK','truncated':True,'total_rows':10000,'rows_exported':5000,'omitted_cell_count':0}}}},
              'historical_cost_gaps':{'brokers':{}},'etoro_private_stream':{'scopes':[]},
              'broker_history_results':{}}
    findings=d.diagnostic_findings(analysis)
    assert any(r['code']=='TABLE_EXPORT_BOUNDED' and r['level']=='INFO' for r in findings)
    assert not any(r['code']=='TABLE_EXPORT_INCOMPLETE' for r in findings)
