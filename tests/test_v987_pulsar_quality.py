"""PULSAR proof, discovery, real SQLite retention and financial-unit contracts."""
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace as NS
import hashlib
import json
import time

import pytest
from pulsar import research, research_history as history, worker, evidence, control
from sec_fundamentals import SecFundamentals
from test_v986_pulsar_research import ready_card
from test_v985_pulsar_flow_and_pages import fixtures


def raw(con, symbol, source, stamp, count, key=None):
    key = key or f'{source}:{symbol}:{stamp}'
    con.execute('INSERT INTO observations VALUES(?,?,?,?,?)',
                (key, source, stamp, symbol, json.dumps({'mentions': count})))


def test_unknown_previous_is_not_zero_or_measured_growth():
    rows = [{'symbol':'UNKNOWN','mentions':1000,'mentions_24h_ago':None},
            {'symbol':'KNOWN','mentions':200,'mentions_24h_ago':20},
            {'symbol':'ZERO','mentions':200,'mentions_24h_ago':0}]
    assert research.attention_order(rows)[0]['symbol']=='KNOWN'
    unknown=research.discovery_state(rows[0])
    assert unknown['growth_score']==0 and unknown['state']=='NEU_OHNE_VERGLEICH'
    assert research.discovery_state(rows[2])['reported_previous_zero']
    assert research.discovery_state(rows[2])['growth_score']>0


def test_new_instrument_has_discovery_slot_but_no_invented_history_or_nomination():
    now=time.time()
    rows=[dict(symbol='S'+str(i),mentions=200,mentions_24h_ago=20) for i in range(8)]
    rows += [dict(symbol='NEW',mentions=80,mentions_24h_ago=None)]
    assert research.attention_order(rows)[4]['symbol']=='NEW'
    card=ready_card(now);card['baseline']=research.baseline('NEW',now=now)
    result=evidence.evaluate(card,now=now)
    assert card['baseline']['days']==0 and not card['baseline']['absence_inferred']
    # 10.3.0: Entdeckung bleibt erlaubt, aber ohne Kurs-/Volumenbestaetigung
    # gibt es keine Hype-Qualifikation und weiterhin keinen Punktestand.
    assert result['discovery_allowed'] and not result['eligible']
    assert result['score'] is None


def test_full_fetch_success_is_not_instrument_coverage_or_absence_evidence(monkeypatch):
    now=time.time();control.set_mode('BEOBACHTEN')
    monkeypatch.setattr(research,'fetch_social',lambda source,**kw:[dict(symbol='ONE',source=source,
        feed=kw['feed'],mentions=30 if kw['feed']=='all-stocks' else 1,observed_at=now)])
    found=research.discover(now=now)
    assert found[0]['mentions']==30
    status=research.cached('coverage',now=now)['data']
    assert status['transport_complete'] and status['instrument_coverage']=='UNKNOWN'
    assert not status['absence_is_evidence']
    assert research.baseline('ABSENT',now=now)['days']==0


def test_hour_compaction_deduplicates_samples_but_not_dates_and_never_retrofills():
    now=1789128000.
    with research.db() as con:
        for day in range(2,16):
            for n in range(20):
                stamp=now-day*86400+n*10
                raw(con,'EXAM','apewisdom',stamp,20)
        raw(con,'EXAM','apewisdom:investing',now-2*86400,1)
        raw(con,'NEW','apewisdom',now,200)
    before=research.baseline('EXAM',now=now)
    assert before['days']==14 and before['hours']==14 and before['median_mentions']==20
    assert research.baseline('EXAM','apewisdom:investing',now=now)['median_mentions']==1
    assert not research.baseline('NEW',now=now)['ready_14d']
    stats=history.maintain(now=now,force=True)
    assert stats['compacted']>0 and stats['raw_removed']==stats['compacted']
    assert research.baseline('EXAM',now=now)==before
    assert history.maintain(now=now,force=True)['raw_removed']==0
    assert research.baseline('NEW',now=now)['days']==0


def test_missing_afternoon_and_provider_outage_do_not_become_a_quiet_day():
    now=1789128000.
    with research.db() as con:
        raw(con,'EXAM','apewisdom',now-2*86400,100)
    b=research.baseline('EXAM',now=now)
    assert b['days']==1 and b['hours']==1 and b['censored_days']==0
    assert not b['ready_14d'] and b['coverage']=='PRESENT_ROWS_ONLY'
    # Twenty scans within that hour have not created twenty dates.
    with research.db() as con:
        for n in range(20):raw(con,'EXAM','apewisdom',now-2*86400+n,100,str(n))
    assert research.baseline('EXAM',now=now)['days']==1


def test_future_current_and_out_of_window_samples_are_not_in_baseline():
    now=1789128000.
    with research.db() as con:
        for i,stamp in enumerate((now+86400,now,now-3600,now-40*86400)):
            raw(con,'EXAM','apewisdom',stamp,9999,str(i))
    assert research.baseline('EXAM',now=now)['days']==0


def test_retention_is_bounded_preserves_audit_and_does_not_open_trade_database(tmp_path):
    now=time.time();control.set_mode('BEOBACHTEN')
    dbfile=tmp_path/'decision_history.sqlite'
    original=hashlib.sha256(dbfile.read_bytes()).hexdigest()
    token=research.reserve('ai',.01,now=now)
    research.save_assessment({'symbol':'EXAM','evidence_hash':'proof','detail':'audit'},now=now-100*86400)
    with research.db() as con:
        for i in range(10):raw(con,'EXAM','apewisdom',now-(50+i)*86400,10,str(i))
    first=history.maintain(now=now,force=True,batch=3)
    assert first['raw_removed']==3 and first['backlog'] and first['audit_preserved'] and not first['vacuum_run']
    with research.db() as con:
        assert con.execute('SELECT COUNT(*) FROM observations').fetchone()[0]==7
        assert con.execute('SELECT COUNT(*) FROM assessments').fetchone()[0]==1
        assert con.execute('SELECT status FROM usage WHERE id=?',(token,)).fetchone()[0]=='RESERVED'
    assert hashlib.sha256(dbfile.read_bytes()).hexdigest()==original
    assert history.status()['page_count']>0


def test_invalid_in_window_sample_is_preserved_and_cannot_count_as_history():
    now=time.time()
    with research.db() as con:raw(con,'BAD','apewisdom',now-10*86400,-1)
    stats=history.maintain(now=now,force=True)
    assert stats['invalid_preserved']==1 and not stats['raw_removed']
    assert research.baseline('BAD',now=now)['days']==0
    assert research.baseline('BAD',now=now)['invalid_samples']==1


def test_half_batch_failure_rolls_back_compaction_and_deletions(monkeypatch):
    now=time.time()
    with research.db() as con:
        for i in range(3):raw(con,'EXAM','apewisdom',now-(10+i)*86400,20,str(i))
    real=history.record; calls=[]
    def fail(*args):
        calls.append(1)
        if len(calls)==2:raise RuntimeError('disk failure')
        return real(*args)
    monkeypatch.setattr(history,'record',fail)
    with pytest.raises(RuntimeError):history.maintain(now=now,force=True)
    with research.db() as con:
        assert con.execute('SELECT COUNT(*) FROM observations').fetchone()[0]==3
        assert con.execute('SELECT COUNT(*) FROM attention_hours').fetchone()[0]==0


@pytest.mark.parametrize('fault',['identity','cik','unit','period','date','future','nan','unknown','bool'])
def test_financial_data_requires_instrument_unit_and_matching_period(fault):
    # 10.3.0: Finanzdaten sind kein Score-Baustein mehr; die strenge
    # SEC-Belegpruefung bleibt als Bibliotheksfunktion fuer die Anzeige.
    card=ready_card();sec=card['sources'][1]['data']
    if fault=='identity':sec['symbol_identity_verified']=False
    if fault=='cik':sec['cik']=0
    if fault=='unit':sec['net_income']['unit']='EUR'
    if fault=='period':sec['net_income']['start']='2026-04-01'
    if fault=='date':sec['operating_cashflow']['filed']='bad'
    if fault=='future':sec['operating_cashflow']['filed']='2099-01-01'
    if fault=='nan':sec['net_income']['value']=float('nan')
    if fault=='unknown':sec['net_income'].pop('unit')
    if fault=='bool':sec['net_income']['value']=True
    ok,_=evidence.valid_financials(sec,'EXAM',time.time())
    assert not ok
    assert not evidence.evaluate(card)['eligible']


def test_sec_latest_keeps_actual_selected_unit_and_same_duration_pair():
    def item(value,end,start='2026-01-01',filed='2026-08-01'):
        return dict(val=value,start=start,end=end,filed=filed,form='10-Q')
    f={'NetIncomeLoss':{'units':{'EUR':[item(5,'2025-12-31',start='2025-01-01')],
        'USD':[item(100,'2026-06-30'),item(200,'2026-06-30',start='2026-04-01')]}},
       'NetCashProvidedByUsedInOperatingActivities':{'units':{'USD':[item(300,'2026-06-30')]}}}
    assert SecFundamentals._latest(f,['NetIncomeLoss'])['unit']=='USD'
    profit,cash=SecFundamentals._matched_results(f)
    assert profit['start']==cash['start']=='2026-01-01' and profit['value']==100
    f['NetIncomeLoss']['units']['USD'].append(item(999,'2099-01-01',filed='2099-02-01'))
    assert SecFundamentals._latest(f,['NetIncomeLoss'])['value']!=999


@pytest.mark.parametrize('currency',[None,'EUR','USDC','GBp'])
def test_usd_thresholds_are_not_used_for_unproved_or_foreign_currency(currency):
    rows,market=fixtures();m=market('AAA');m['profile']['currency']=currency
    card=worker.build_card(rows[0],m)
    assert card['market']['turnover_usd'] is None
    assert any('Waehrungsparitaet' in reason for reason in card['blocks'])


def test_foreign_profile_does_not_qualify_a_ticker():
    rows,market=fixtures();m=market('AAA');m['profile']['symbol']='OTHER'
    card=worker.build_card(rows[0],m)
    assert any('Ticker' in reason for reason in card['blocks'])


def test_old_rule_card_cannot_supply_new_temporal_qualification():
    now=time.time();card=ready_card(now);card.update(evidence.evaluate(card,now=now))
    research.save_assessment({**card,'rules_version':'PULSAR-1.1'},now=now-3600)
    research.save_assessment(card,now=now)
    assert research.stable_candidate('EXAM',now=now) is None


@pytest.mark.parametrize("previous,ratio", [(None,None),(0,None),(20,5.0)])
def test_legacy_cached_growth_is_recomputed_from_actual_counts(previous,ratio):
    rows,market=fixtures();row=rows[0]
    row.update(mentions=100,mentions_24h_ago=previous,growth_ratio=999)
    card=worker.build_card(row,market(row['symbol']))
    assert card['attention']['growth_ratio']==ratio
    social=next(s for s in card['packet']['sources'] if s['provider']=='apewisdom')
    assert social['data']['growth_ratio']==ratio
    assert row['growth_ratio']==999  # Original observation not mutated.
