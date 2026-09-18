"""BTC 51138 regression: real adapter and durable journal, synthetic exchange.

79419 bid / 78624 old limit / 79027 sell band are the observed incident.
Other books and bands are deliberately synthetic safety counterexamples.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
import time
from types import SimpleNamespace as NS

import pytest

from broker.base import BrokerFehler, OrderStatusUnklar
from broker.okx import OKXInstrument
from broker.okx_price_limits import PriceBand, OKXPriceBandError, bounded_limit
from test_v976_okx_market_environment import market, sui
from test_v975_execution_and_repair import engine, persist
import execution_lifecycle as lifecycle


def band(**changes):
    return dict(instId='BTC-USDC', enabled=True, buyLmt='79800', sellLmt='79027',
                ts=str(int(time.time()*1000)), **changes)


def parsed(**changes):
    row = band(); row.update(changes)
    return PriceBand.parse([row], 'BTC-USDC', now_ms=time.time()*1000, max_age_seconds=3)


def price(side='sell', **changes):
    args = dict(side=side, requested='78624', tick='1', own_limit='77036.43',
                best='79419', worst='79419', band=parsed())
    args.update(changes)
    return bounded_limit(**args)


def test_incident_limit_is_raised_to_exchange_floor_not_lowered():
    assert price() == Decimal('79027')
    assert price(tick='10') == Decimal('79030')
    assert price(own_limit='79300') == Decimal('79300')


def test_buy_cap_is_lowered_without_widening_own_limit():
    assert price('buy', requested='79900', own_limit='79850', best='79700',
                 worst='79700', tick='7', band=parsed(buyLmt='79799')) == Decimal('79793')


def test_disabled_band_keeps_original_guard():
    assert price(band=parsed(enabled=False, buyLmt='', sellLmt='')) == Decimal('78624')


@pytest.mark.parametrize('changes', [dict(sellLmt='NaN'), dict(buyLmt='Infinity'),
    dict(sellLmt='0'), dict(sellLmt='-1'), dict(sellLmt=''), dict(enabled='false'),
    dict(enabled=1), dict(instId='BTC-EUR'), dict(ts='0'), dict(ts='NaN'),
    dict(ts='1'), dict(ts='FUTURE_60_SECONDS')])
def test_unusable_band_fails_closed(changes):
    # Build the future quote at execution, not pytest collection. A full suite
    # can take longer than 60 seconds; the old fixture then became a valid
    # present-day quote and tested a different contract by accident.
    changes = dict(changes)
    if changes.get('ts') == 'FUTURE_60_SECONDS':
        changes['ts'] = str(int((time.time()+60)*1000))
    with pytest.raises(BrokerFehler): parsed(**changes)


@pytest.mark.parametrize('rows', [[], [{}], [band(), band()], None])
def test_missing_ambiguous_band_is_not_disabled(rows):
    with pytest.raises(BrokerFehler):
        PriceBand.parse(rows,'BTC-USDC',now_ms=time.time()*1000,max_age_seconds=3)


def test_no_executable_tick_does_not_turn_into_market_order():
    with pytest.raises(OKXPriceBandError): price(band=parsed(sellLmt='79419.1'))
    with pytest.raises(OKXPriceBandError): price(tick='1000')
    with pytest.raises(OKXPriceBandError):
        price('buy', requested='79900', own_limit='79850', best='79900', worst='79900')


@pytest.fixture
def btc(market, monkeypatch):
    broker, transport = market
    broker.client._instruments['BTC-USDC'] = OKXInstrument(
        'BTC-USDC','BTC','USDC','live','1','.00000001','.00001',trade_quote_ccy_list=('USDC',))
    transport.bids['DEMO'] = 79419
    transport.band = dict(enabled=True, buyLmt='79800', sellLmt='79027')
    monkeypatch.setattr(broker,'warte_auf_guthabenfreigabe',lambda *a,**k:(.00129174302,True))
    inst = NS(name='BTC',asset_type='crypto',contract=NS(localSymbol='BTC-USDC'))
    return broker, transport, inst


def sell(btc):
    b, _, inst = btc
    return b.schliesse_position(inst,.00129174302,79419,client_order_id='BTC981')


def test_real_sell_uses_two_fresh_demo_bands_and_exact_journal(btc):
    broker, transport, _ = btc
    result = sell(btc)
    assert result.terminal and result.fill_evidence_complete
    assert transport.orders[0]['px'] == '79027'
    assert transport.orders[0]['ordType'] == 'ioc'
    assert transport.orders[0]['sz'] == '0.00129174'
    assert result.execution_quote['price_band']['sellLmt'] == '79027'
    reads = [c for c in transport.calls if c[1].endswith('/price-limit')]
    assert len(reads) == 2
    assert all(c[2]['x-simulated-trading']=='1' for c in reads)
    assert all(not any(k.startswith('OK-ACCESS-') for k in c[2]) for c in reads)
    assert lifecycle.snapshot()[0]['state'] == 'FILLED'
    assert json.loads(lifecycle.snapshot()[0]['request_json'])['px'] == '79027'


def test_no_overlap_preserves_protection_before_any_cancel(btc,monkeypatch):
    b,t,_=btc; t.band['sellLmt']='80000'
    canceled=[]
    monkeypatch.setattr(b,'storniere_offene_orders',lambda *a,**k:canceled.append(1))
    with pytest.raises(OKXPriceBandError): sell(btc)
    assert not canceled and not t.orders and not lifecycle.snapshot()


def test_after_unlock_price_band_is_refreshed(btc,monkeypatch):
    b,t,_=btc
    def released(*a,**k):
        t.band['sellLmt']='79300'
        return .00129174302,True
    monkeypatch.setattr(b,'warte_auf_guthabenfreigabe',released)
    sell(btc)
    assert t.orders[0]['px']=='79300'


def test_band_read_outage_does_not_use_live_or_cached_band(btc,monkeypatch):
    b,t,_=btc
    import requests
    original=t.request
    def fail(method,url,**kw):
        if '/price-limit' in url: raise requests.Timeout('band unavailable')
        return original(method,url,**kw)
    monkeypatch.setattr(t,'request',fail)
    with pytest.raises(BrokerFehler): sell(btc)
    assert not t.orders


def test_band_request_cannot_make_book_stale(btc,monkeypatch):
    b,t,_=btc
    original=b.execution_quote
    def old(*a,**k):
        q=original(*a,**k); q['timestamp_ms']-=10000
        return q
    monkeypatch.setattr(b,'execution_quote',old)
    with pytest.raises(BrokerFehler,match='Orderbuch nach'): sell(btc)
    assert not t.orders


@pytest.mark.parametrize('code,http,expected', [('51138',200,OKXPriceBandError),
    ('51137',200,OKXPriceBandError),('51138',503,OrderStatusUnklar),
    ('50004',200,OrderStatusUnklar)])
def test_typed_rejection_does_not_mask_ambiguous_status(btc,monkeypatch,code,http,expected):
    b,t,_=btc; original=t.request
    def reject(method,url,**kw):
        if method=='POST' and url.endswith('/trade/order'):
            return NS(status_code=http,json=lambda:dict(code='1',msg='',data=[
                dict(sCode=code,sMsg='The lowest price limit for the sell leg is 79,027.')]))
        return original(method,url,**kw)
    monkeypatch.setattr(t,'request',reject)
    monkeypatch.setattr(b,'_suche_order',lambda *a:{})
    with pytest.raises(expected): sell(btc)
    row=lifecycle.snapshot()[0]
    assert row['state']==('REJECTED' if expected is OKXPriceBandError else 'UNCLEAR')
    assert bool(row['terminal']) == (expected is OKXPriceBandError)


def test_engine_band_rejection_restores_protection_and_uses_short_retry(engine,monkeypatch):
    p,_=persist(engine); p.exit_fehlversuche=3
    b=engine.broker; b.letzte_stornierung={'inst_id':p.inst_id}
    b.schliesse_position=lambda *a,**k:(_ for _ in ()).throw(OKXPriceBandError('band changed',code='51138'))
    restored=[]
    monkeypatch.setattr(engine,'_schutz_nachziehen',lambda p:restored.append(p.menge))
    assert engine._schliesse(p,.82,'roi')=='FAILED'
    assert restored==[p.menge]
    assert 0 < (datetime.fromisoformat(p.exit_retry_after)-datetime.now(timezone.utc)).total_seconds() <= 30
    assert p.exit_fehlversuche==4


@pytest.mark.parametrize('changes,allowed', [({},True),({'account':'B'},False),
    ({'environment':'LIVE'},False),({'instrument':'BTC-EUR'},False),
    ({'client_id':'other'},False),({'state':'UNCLEAR','terminal':0},False),
    ({'filled':'0.0001'},False),({'evidence_complete':0},False)])
def test_legacy_wait_only_shortens_for_exact_rejection(engine,monkeypatch,changes,allowed):
    p,_=persist(engine); p.exit_state='RETRY_WAIT'; p.exit_client_order_id='btc-attempt'
    p.exit_last_detail='OKX lehnt die Order ab (51138): price limit (Versuch 4)'
    row=dict(account=p.account_fingerprint,environment='DEMO',instrument=p.inst_id,side='SELL',
        client_id=p.exit_client_order_id,state='REJECTED',terminal=1,evidence_complete=1,filled='0',
        updated_at=(datetime.now(timezone.utc)-timedelta(minutes=1)).isoformat())
    row.update(changes)
    monkeypatch.setattr(lifecycle,'snapshot',lambda **k:[row])
    assert engine._preisgrenzen_retry_faellig(p) is allowed


def test_foreign_numbers_do_not_prove_a_price_band_rejection(engine):
    p,_=persist(engine); p.exit_state='RETRY_WAIT'; p.exit_client_order_id='x'
    p.exit_last_detail='Timeout orderId 51138'
    assert not engine._preisgrenzen_retry_faellig(p)


def test_live_price_limit_header_stays_live(market):
    b,t=market; b.client.demo=False
    b.client.price_limit('SUI-USDC')
    assert 'x-simulated-trading' not in t.calls[-1][2]
