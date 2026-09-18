from concurrent.futures import ThreadPoolExecutor
import json

import pytest
from market_intelligence import service as x, store, candidate_research as cr
from test_v1014_x_discovery import NOW, post, profile


@pytest.fixture
def active(monkeypatch, tmp_path):
    monkeypatch.setattr(store, 'ROOT', tmp_path)
    monkeypatch.setattr(x.time, 'time', lambda: NOW)
    x.save_settings({'enabled': True, 'pricing_acknowledged': True}, bearer_token='fake-offline-token')
    return tmp_path


def candidate(symbol, **kwargs):
    return {'attention': {'symbol': symbol}, 'profile_evidence': {'source': profile(symbol, **kwargs)},
            'selection': {'discovery_origin': 'REDDIT'}}


def reserve(now=NOW):
    with store.db(readonly=True) as con:
        p = cr.plan(con, now)
    return x._reserve('search', p['query'], cr.CONTEXT, p['slot'], now)


def complete(req, rows, now=NOW):
    received, processed, duplicates, coverage = x._posts(req, {'data': rows}, now)
    x._finish(req, now, status='OK', received=received, processed=processed,
              duplicates=duplicates, coverage=coverage)


def result(symbol, now=NOW):
    with store.db(readonly=True) as con:
        return cr.for_symbol(con, symbol, now)


def test_other_source_candidates_drive_open_search_and_typed_identity(active):
    accepted = cr.enqueue([candidate('PEP', companyName='PepsiCo Inc.'), candidate('MU'),
                           candidate('SPY', isEtf=True), candidate('BAD', isFund=None)], now=NOW)
    assert accepted == ['PEP', 'MU']
    with store.db(readonly=True) as con:
        p = cr.plan(con, NOW)
    assert '$PEP' in p['query'] and '$MU' in p['query'] and '"PepsiCo Inc."' in p['query']
    assert 'from:' not in p['query'] and 'SPY' not in p['query']
    assert result('PEP')['state'] == 'QUEUED' and result('PEP')['sentiment'] == 'UNKNOWN'


def test_symbol_association_samples_no_trade_or_crisis_authority(active):
    cr.enqueue([candidate('PEP', companyName='PepsiCo Inc.'), candidate('MU')], now=NOW)
    req = reserve()
    complete(req, [post('11', 'PEP', '101', text='$PEP bullish earnings beat'),
                   post('12', 'PEP', '102', text='$PEP bullish earnings beat'),
                   post('13', 'MU', '103', text='$MU bearish cuts guidance'),
                   post('14', 'PEP', '101', text='PepsiCo Inc. downside earnings miss')])
    pep, mu = result('PEP'), result('MU')
    assert pep['sentiment'] == 'MIXED' and pep['usable_posts'] == 2
    assert pep['excluded_duplicates_or_spam'] == 1
    assert mu['sentiment'] == 'NEGATIVE_HINT' and mu['usable_posts'] == 1
    assert not pep['trade_effect'] and not pep['primary_source_confirmed']
    context = x.for_symbol('PEP', now=NOW)
    assert context['candidate_research']['sentiment'] == 'MIXED'
    assert context['source_family'] == 'X'
    assert all(e['post_id'] != '13' for e in pep['evidence'])


def test_no_hits_and_expired_hits_are_unknown_not_neutral(active):
    cr.enqueue([candidate('PEP')], now=NOW)
    req = reserve(); complete(req, [])
    assert result('PEP')['state'] == 'NO_MATCHING_SAMPLE'
    assert result('PEP')['sentiment'] == 'UNKNOWN'
    assert result('PEP', NOW+cr.DAY+1)['usable_posts'] == 0


def test_atomic_slot_reservation_and_next_slot_fairness(active):
    cr.enqueue([candidate(s) for s in ['AAA','BBB','CCC','DDD','EEE']], now=NOW)
    with store.db(readonly=True) as con:
        p = cr.plan(con, NOW)
    with ThreadPoolExecutor(max_workers=5) as pool:
        receipts = list(pool.map(lambda _: x._reserve('search', p['query'], cr.CONTEXT, p['slot'], NOW), range(5)))
    assert sum(r is not None for r in receipts) == 1
    cr.enqueue([candidate(s) for s in ['AAA','BBB','CCC','DDD','EEE']], now=NOW+60)
    with store.db(readonly=True) as con:
        assert cr.plan(con, NOW+60) is None
        next_plan = cr.plan(con, NOW+cr.INTERVAL)
    assert next_plan['symbols'] == ['CCC', 'DDD']


def test_seven_searches_share_original_fifteen_euro_cap(active):
    # 10.3.0: 5 Kandidaten-Suchen + 2 Makro-/Accountsuchen pro Tag; die
    # frueheren taeglichen X-Zaehlungsabrufe (14-Tage-Baseline) sind entfallen.
    start = (int(NOW//cr.DAY)+1)*cr.DAY
    for slot in range(5):
        now = start+slot*cr.INTERVAL
        cr.enqueue([candidate('PEP', now=now)], now=now)
        assert reserve(now)
        if slot < 2:
            assert x._reserve('search', 'general '+str(slot), '__STOCK_DISCOVERY__', str(slot), now)
    assert x._reserve('search', 'one too many', '__STOCK_DISCOVERY__', 'extra', start+86300) is None
    assert 14.0 <= x.get_settings()['estimated_month_eur'] <= 15.0
    assert x._reserve('counts', '$PEP', 'PEP', 'next', NOW+cr.DAY) is None


def test_disabled_x_keeps_queue_but_does_not_collect_or_advise(active):
    cr.enqueue([candidate('PEP')], now=NOW)
    x.save_settings({'enabled': False})
    assert reserve() is None and x.for_symbol('PEP', now=NOW) == {}


def test_packet_keeps_targeted_research_and_security_fields(active):
    from pulsar.ai_packet import project
    cr.enqueue([candidate('PEP')], now=NOW)
    req=reserve();complete(req,[post('81','PEP',text='$PEP bullish earnings beat')])
    context=x.for_symbol('PEP',now=NOW)
    packet=project({'symbol':'PEP','sources':[{'id':'a'*64,'provider':'X','kind':'social_research_hint','data':context}]})
    data=packet['sources'][0]['data']
    assert data['candidate_research']['sentiment']=='POSITIVE_HINT'
    assert data['trade_effect'] is False and data['primary_source_confirmed'] is False
    assert '$PEP bullish' not in json.dumps(packet)


def test_diagnosis_shows_queries_and_counts_without_raw_text(active):
    from NEXUS_10_Diagnose import x_receipts_only
    cr.enqueue([candidate('PEP')],now=NOW)
    req=reserve();complete(req,[post('82','PEP',text='$PEP bullish secret raw text')])
    report=x_receipts_only(x.public_status())
    c=report['candidate_research']['candidates'][0]
    assert c['query']=='($PEP) -is:retweet' and c['usable_posts']==1
    assert 'secret raw text' not in json.dumps(report)
    assert 'author' not in json.dumps(report)



def test_empty_new_response_does_not_recount_previous_request(active):
    cr.enqueue([candidate('PEP')],now=NOW)
    complete(reserve(),[post('88','PEP',text='$PEP bullish')])
    assert result('PEP')['usable_posts']==1
    next_time=NOW+cr.INTERVAL
    cr.enqueue([candidate('PEP',now=next_time)],now=next_time)
    complete(reserve(next_time),[],now=next_time)
    assert result('PEP',next_time)['matched_posts']==0
    assert result('PEP',next_time)['sentiment']=='UNKNOWN'


def test_negated_keyword_is_not_bullish_sentiment(active):
    cr.enqueue([candidate('PEP')],now=NOW)
    complete(reserve(),[post('89','PEP',text='$PEP not bullish')])
    assert result('PEP')['sentiment']=='UNCLASSIFIED'
