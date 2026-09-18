"""DOGE 09 Sep: actual fills; synthetic failures for boundary regressions.

No test claims that OKX accepted a new protection order or explains 51634.
All account/order responses are offline fixtures or explicit test doubles.
"""
from copy import deepcopy
from decimal import Decimal
import json
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from broker.base import BrokerFehler, OrderStatusUnklar
from broker.okx import OKXBroker, OKXClient, OKXInstrument
from test_v975_execution_and_repair import engine, persist
from test_v954_schutzbuchhaltung import SchutzBroker, baue_motor, xlm_position


@pytest.fixture
def incident():
    return json.loads((Path(__file__).parent / 'fixtures/doge_20260909_confirmed.json').read_text())


def evidence_broker(incident, fills=None):
    rows = incident['fills'] if fills is None else fills
    client = NS(hat_zugangsdaten=True, clock_offset_seconds=0,
                instrument=lambda i: OKXInstrument.from_api(incident['instrument']),
                order_status=lambda *a, **k: deepcopy(incident['order']),
                fills=lambda *a, **k: deepcopy(rows),
                fills_history_paginated=lambda *a, **k: [])
    b = OKXBroker(client=client, quote_ccy='USD', allowed_quotes=('USD',))
    b._account_fingerprint = 'doge-offline-account'
    b.reconcile_position_protection = lambda *a, **k: pytest.fail('Early protection POST')
    return b


def evidence_intent(incident):
    order = incident['order']
    return dict(inst_id=order['instId'], ord_id=order['ordId'], cl_ord_id=order['clOrdId'],
                qty=533.522547, signal_price=.0892342103405614, stop=.08031078930650516,
                take=.09345663032272133, trade_quote_ccy='USD')


def test_actual_six_fills_form_one_net_buy(incident):
    fills = incident['fills']
    assert len(fills) == len({r['tradeId'] for r in fills}) == 6
    assert {r['ordId'] for r in fills} == {incident['order']['ordId']}
    assert {r['side'] for r in fills} == {'buy'}
    assert sum(Decimal(r['fillSz']) for r in fills) == Decimal('533.522547')
    assert -sum(Decimal(r['fee']) for r in fills) == Decimal('1.8673289145')
    cost = sum(Decimal(r['fillSz']) * Decimal(r['fillPx']) for r in fills)
    assert cost == Decimal('47.60846318043')
    result = evidence_broker(incident).reconcile_order_evidence(evidence_intent(incident))
    assert result.terminal and result.fill_evidence_complete
    assert result.filled_quantity == pytest.approx(531.6552180855, abs=1e-10)
    assert result.trade_quote_ccy == 'USD'
    assert len(result.fill_ids) == 6
    assert not result.stop_order_platziert


@pytest.mark.parametrize('count', [0, 5])
def test_missing_execution_evidence_does_not_fabricate_a_complete_buy(incident, monkeypatch, count):
    monkeypatch.setattr('broker.okx.time.sleep', lambda _: None)
    result = evidence_broker(incident, incident['fills'][:count]).reconcile_order_evidence(evidence_intent(incident))
    assert not result.fill_evidence_complete
    assert not result.stop_order_platziert


def test_recovered_entry_persists_before_protection_and_replay_keeps_ids(engine):
    import trade_ledger as tl
    calls = []
    def protection(*args, **kwargs):
        calls.append(kwargs)
        p = engine.buch.aktive()[0]
        assert p.menge > 0 and p.fill_ids
        assert tl.offene_trades(broker='okx')
        return dict(checked=True, protection_confirmed=True, algo_id='exact-protection',
                    algo_client_id='Pbuy', detail='Confirmed test exchange response')
    engine.broker.reconcile_position_protection = protection
    p, tid = persist(engine)
    assert p.protection_algo_id == tl.trade_detail(tid)['protection_algo_id'] == 'exact-protection'
    persist(engine)
    assert len(calls) == 1


@pytest.mark.parametrize('quote_result', ['error', 'zero'])
def test_price_failure_does_not_skip_broker_protection(tmp_path, monkeypatch, quote_result):
    broker = SchutzBroker()
    def quote(*a, **k):
        if quote_result == 'error':
            raise BrokerFehler('Test: book unavailable')
        return {'vwap': 0}
    broker.execution_quote = quote
    ce, motor, _ = baue_motor(tmp_path, monkeypatch, broker)
    p = xlm_position(ce)
    p.broker_schutz = False
    motor.buch.setze(p)
    motor.pruefe_positionen()
    assert len(broker.abgleiche) == 1
    assert motor.buch.aktive()[0].broker_schutz


def algo_broker(incident, monkeypatch):
    meta = OKXInstrument.from_api(incident['instrument'])
    state = {'posts': [], 'orders': [], 'error': None, 'read_error': False}
    def post(body):
        state['posts'].append(dict(body))
        if state['error']:
            raise state['error']
        state['orders'] = [dict(body, algoId='new-protection')]
        return {'algoId': 'new-protection'}
    client = NS(hat_zugangsdaten=True, clock_offset_seconds=0,
                instrument=lambda _: meta, place_algo_order=post)
    b = OKXBroker(client=client, quote_ccy='USDC', allowed_quotes=('USD', 'USDC'))
    b._account_fingerprint = 'doge-protection-test'
    def pending(*a, **k):
        if state['read_error'] and state['posts']:
            raise BrokerFehler('Test: read unavailable after acceptance')
        return deepcopy(state['orders'])
    b.offene_schutzorders = pending
    monkeypatch.setattr('broker.okx.time.sleep', lambda _: None)
    return b, meta, state


def protect(b, meta, **changes):
    kwargs = dict(trade_quote_ccy='USD', protection_client_id='PN93014580555928578048')
    kwargs.update(changes)
    return b._setze_schutz(meta, 531.6552180855, .08031078930650516, .09345663032272133, **kwargs)


def test_usd_protection_uses_position_currency_and_valid_quantities(incident, monkeypatch):
    b, meta, state = algo_broker(incident, monkeypatch)
    result = protect(b, meta)
    assert result['protection_confirmed']
    body = state['posts'][0]
    assert body['tradeQuoteCcy'] == 'USD'  # Broker's preferred USDC is irrelevant.
    assert body['sz'] == '531.655218'
    assert body['slTriggerPx'] == '0.08031'
    assert body['tpTriggerPx'] == '0.09346'
    assert body['ordType'] == 'oco' and body['side'] == 'sell'
    protect(b, meta)
    assert len(state['posts']) == 1


def test_invalid_settlement_does_not_send_protection(incident, monkeypatch):
    b, meta, state = algo_broker(incident, monkeypatch)
    with pytest.raises(BrokerFehler, match='Abrechnungswaehrung'):
        protect(b, meta, trade_quote_ccy='USDC')
    assert not state['posts']


def test_existing_protection_in_wrong_currency_is_not_counted_or_duplicated(incident, monkeypatch):
    b, meta, state = algo_broker(incident, monkeypatch)
    state['orders'] = [dict(instId='DOGE-USD', algoId='old', algoClOrdId='PN93014580555928578048',
                            side='sell', sz='531.655218', tradeQuoteCcy='USDC')]
    result = b.reconcile_position_protection('DOGE-USD', 531.6552180855, .08031, .09346,
                trade_quote_ccy='USD', protection_client_id='PN93014580555928578048')
    assert not result['protection_confirmed']
    assert 'Abrechnungswaehrung' in result['detail']
    assert not state['posts']


def test_rejection_delays_identical_posts_but_not_readback(incident, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr('broker.okx.time.monotonic', lambda: clock[0])
    b, meta, state = algo_broker(incident, monkeypatch)
    state['error'] = BrokerFehler('OKX lehnt die Order ab (51634): ohne Meldung')
    first = protect(b, meta)
    assert first['checked'] and not first.get('protection_confirmed')
    for _ in range(4):
        clock[0] += 60
        assert protect(b, meta) == first
    assert len(state['posts']) == 1
    clock[0] += 61
    protect(b, meta)
    assert len(state['posts']) == 2
    state['orders'] = [dict(state['posts'][-1], algoId='arrived')]
    assert protect(b, meta)['protection_confirmed']
    assert len(state['posts']) == 2


@pytest.mark.parametrize('error', [OrderStatusUnklar('Test: POST timeout'), None])
def test_unknown_or_unreadable_protection_is_never_a_definite_rejection(incident, monkeypatch, error):
    b, meta, state = algo_broker(incident, monkeypatch)
    state['error'] = error
    state['read_error'] = error is None
    result = protect(b, meta)
    assert not result['checked'] and not result.get('protection_confirmed')
    assert not b._protection_rejections
    if error is None:
        assert result['algo_id'] == 'new-protection'
        assert 'angenommen' in result['hinweis']


@pytest.mark.parametrize('detail,parent,expected', [(' ', 'parent explanation', 'parent explanation'),
    (None, '', 'ohne Meldung'), ('specific explanation', 'parent explanation', 'specific explanation')])
def test_blank_detail_preserves_parent_error_and_safe_request_context(monkeypatch, caplog, detail, parent, expected):
    c = OKXClient('secret-key-to-mask', 'secret-signing-key', 'private-passphrase')
    payload = dict(code='1', msg=parent, data=[dict(sCode='51634', sMsg=detail,
                    unexpected='secret-key-to-mask')])
    monkeypatch.setattr(c.session, 'request', lambda *a, **k: NS(status_code=200, json=lambda: payload))
    with pytest.raises(BrokerFehler, match=expected):
        c.place_algo_order(dict(instId='DOGE-USD', tradeQuoteCcy='USD', sz='531.655218',
                               apiKey='secret-key-to-mask'))
    assert c.last_algo_error['request']['tradeQuoteCcy'] == 'USD'
    assert c.last_algo_error['data'][0]['sCode'] == '51634'
    assert 'secret-key-to-mask' not in caplog.text
    assert 'apiKey' not in caplog.text and 'unexpected' not in caplog.text


def test_echoed_credentials_are_redacted_before_error_or_log(monkeypatch, caplog):
    c = OKXClient('redact-key', 'redact-secret', 'redact-pass')
    payload = dict(code='1', msg='redact-key redact-secret',
                   data=[dict(sCode='51634', sMsg='redact-pass')])
    monkeypatch.setattr(c.session, 'request', lambda *a, **k: NS(status_code=200, json=lambda: payload))
    with pytest.raises(BrokerFehler) as exc:
        c.place_algo_order(dict(instId='DOGE-USD'))
    for secret in ('redact-key', 'redact-secret', 'redact-pass'):
        assert secret not in str(exc.value) + caplog.text


def test_failed_protection_return_emits_critical_warning(engine):
    engine.broker.reconcile_position_protection = lambda *a, **k: dict(
        checked=True, protection_confirmed=False, detail='OKX 51634')
    p, _ = persist(engine)
    assert not p.broker_schutz and p.protection_status == 'MISSING'
    assert any('Broker-Schutz nicht bestaetigt' in m and '51634' in m for m in engine.messages)


def test_unknown_post_is_not_repeated_after_adapter_restart(incident, monkeypatch, tmp_path):
    import decision_analytics as da
    monkeypatch.setattr(da, 'DB_PATH', tmp_path / 'restart.sqlite')
    b, meta, state = algo_broker(incident, monkeypatch)
    state['error'] = OrderStatusUnklar('Test: response lost')
    assert not protect(b, meta)['checked']
    restarted, _, fresh = algo_broker(incident, monkeypatch)
    result = protect(restarted, meta)
    assert not result['checked'] and not fresh['posts']
    # Changed prices must not bypass an unresolved submission.
    result = restarted._setze_schutz(meta, 531.6552180855, .081, .094,
                trade_quote_ccy='USD', protection_client_id='PN93014580555928578048')
    assert not result['checked'] and not fresh['posts']
    # A later positive, exact broker read resolves the persisted reservation.
    fresh['orders'] = [dict(state['posts'][0], algoId='late-confirmation')]
    assert protect(restarted, meta)['protection_confirmed']
    assert not fresh['posts']


def test_protection_journal_has_one_atomic_sender_and_separate_environments(monkeypatch, tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    import decision_analytics as da
    import okx_protection_journal as journal
    monkeypatch.setattr(da, 'DB_PATH', tmp_path / 'claims.sqlite')
    args = dict(account='A', environment='DEMO', instrument='DOGE-USD', client_id='Pentry',
                request={'sz': '531.655218'})
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: journal.begin(**args), range(4)))
    assert sum(r is None for r in results) == 1
    assert journal.begin(**{**args, 'environment': 'LIVE'}) is None
    assert journal.begin(**{**args, 'account': 'B'}) is None


def test_protection_storage_failure_prevents_post(incident, monkeypatch):
    import okx_protection_journal as journal
    b, meta, state = algo_broker(incident, monkeypatch)
    def fail(**kw):
        raise BrokerFehler('Test: storage unavailable')
    monkeypatch.setattr(journal, 'begin', fail)
    with pytest.raises(BrokerFehler, match='storage unavailable'):
        protect(b, meta)
    assert not state['posts']


def test_missing_account_identity_prevents_protection_post(incident, monkeypatch):
    b, meta, state = algo_broker(incident, monkeypatch)
    b._account_fingerprint = ''
    with pytest.raises(BrokerFehler, match='Konto-/Orderzuordnung'):
        protect(b, meta)
    assert not state['posts']
