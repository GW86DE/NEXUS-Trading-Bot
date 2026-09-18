"""Real REST adapter against two synthetic matching environments, no network.

SUI quantity and live bid come from the incident. The different demo bid is
an explicit counterexample, NOT a reconstruction of the missing demo book.
"""
import json
import time
from types import SimpleNamespace as NS
from urllib.parse import urlsplit, parse_qs

import pytest

from broker.base import BrokerFehler, OrderStatusUnklar
from broker.okx import OKXBroker, OKXClient, OKXInstrument


class Reply:
    status_code = 200

    def __init__(self, rows):
        self.rows = rows

    def json(self):
        return {"code": "0", "data": self.rows}


class TwoMarkets:
    """Only HTTP is replaced; prices and matching use the received headers."""
    def __init__(self):
        self.calls = []
        self.orders = []
        self.headers = {}
        self.bids = {"LIVE": .8175, "DEMO": .8050}
        self.depth = {"LIVE": 1000, "DEMO": 1000}
        self.book_error = False
        self.band = None

    def request(self, method, url, headers=None, data=None, timeout=None):
        path = urlsplit(url).path
        env = "DEMO" if (headers or {}).get("x-simulated-trading") == "1" else "LIVE"
        self.calls.append((method, path, dict(headers or {})))
        if path == '/api/v5/public/price-limit':
            return Reply([{**dict(instId=parse_qs(urlsplit(url).query)['instId'][0],
                ts=str(int(time.time()*1000)), enabled=False, buyLmt='', sellLmt=''),
                **(self.band or {})}])
        if path.startswith('/api/v5/market/'):
            if self.book_error:
                import requests
                raise requests.ConnectionError('synthetic market outage')
            if path.endswith('/books'):
                return Reply([dict(ts=str(int(time.time()*1000)),
                    bids=[[str(self.bids[env]), str(self.depth[env]), '0', '1']],
                    asks=[[str(self.bids[env]+.0001), '1000', '0', '1']])])
            return Reply([])
        if path == '/api/v5/trade/order':
            if method == 'POST':
                body = json.loads(data)
                assert body['ordType'] == ('fok' if body['side']=='buy' else 'ioc')
                qty = float(body['sz'])
                executable = min(qty,self.depth[env]) if self.bids[env] >= float(body['px']) else 0
                if body['ordType']=='fok' and executable<qty: executable=0
                filled=executable>=qty
                row = dict(body, ordId=str(9000+len(self.orders)),
                    state='filled' if filled else 'canceled',
                    accFillSz=str(executable),
                    avgPx=str(self.bids[env]) if executable else '',
                    cancelSource='' if filled else ('14' if body['ordType']=='ioc' else '13'))
                self.orders.append(row)
                return Reply([dict(ordId=row['ordId'], clOrdId=body['clOrdId'], sCode='0')])
            return Reply([self.orders[-1]])
        if path == '/api/v5/trade/fills':
            row = self.orders[-1]
            if float(row['accFillSz']) <= 0:
                return Reply([])
            return Reply([dict(instId=row['instId'], ordId=row['ordId'], tradeId='201',
                clOrdId=row['clOrdId'], side='sell', fillSz=row['accFillSz'],
                fillPx=row['avgPx'], fee='-.35', feeCcy='USDC', ts='1788854400000')])
        if path == '/api/v5/account/balance':
            return Reply([dict(details=[dict(ccy='SUI', availBal='124.771765',
                cashBal='124.771765', frozenBal='0')])])
        return Reply([])


@pytest.fixture
def market(monkeypatch):
    import config
    client = OKXClient('test-key', 'test-secret', 'test-passphrase', demo=True)
    transport = TwoMarkets()
    client.session = transport
    client._instruments = {'SUI-USDC': OKXInstrument('SUI-USDC', 'SUI', 'USDC',
        'live', '.0001', '.01', '1', trade_quote_ccy_list=('USDC',))}
    client._instruments_at = time.time()+86400
    for name in ('_public_limit', '_private_limit', '_order_limit'):
        setattr(client, name, NS(acquire=lambda **kw: True))
    broker = OKXBroker(client=client, demo=True, quote_ccy='USDC', allowed_quotes=('USDC',))
    broker.account_fingerprint = lambda: 'test-demo-account'
    monkeypatch.setattr(config, 'OKX_MAX_EXIT_SLIPPAGE_PCT', .01)
    monkeypatch.setattr(config, 'OKX_MAX_EXIT_SLIPPAGE_HARD_PCT', .03)
    monkeypatch.setattr('broker.okx.time.sleep', lambda _: None)
    return broker, transport


def sui():
    return NS(name='SUI', asset_type='crypto', contract=NS(localSymbol='SUI-USDC'))


@pytest.mark.parametrize('path', ['/market/books', '/market/ticker', '/market/tickers',
                                 '/market/candles', '/market/history-candles'])
def test_demo_market_requests_route_without_credentials(market, path):
    broker, transport = market
    broker.client._key = broker.client._secret = broker.client._passphrase = ''
    broker.client.request('GET', path, params={'instId': 'SUI-USDC'})
    headers = transport.calls[-1][2]
    assert headers.get('x-simulated-trading') == '1'
    assert not any(k.startswith('OK-ACCESS-') for k in headers)


@pytest.mark.parametrize('path,private', [('/market/books', False),
    ('/account/balance', True), ('/public/instruments', False), ('/public/time', False)])
def test_live_stays_live(market, path, private):
    broker, transport = market
    broker.client.demo = False
    broker.client.request('GET', path, private=private)
    assert 'x-simulated-trading' not in transport.calls[-1][2]


@pytest.mark.parametrize('path', ['/public/instruments', '/public/time'])
def test_reference_catalogue_and_clock_keep_the_existing_live_route(market, path):
    broker, transport = market
    broker.client.request('GET', path)
    assert 'x-simulated-trading' not in transport.calls[-1][2]


def test_sui_sell_uses_the_same_environment_as_the_book(market):
    broker, transport = market
    result = broker.schliesse_position(sui(), 124.771765, .8179,
        client_order_id='SuiAttempt1', account_fingerprint='test-demo-account')
    assert result.terminal and result.fill_evidence_complete
    assert result.filled_quantity == pytest.approx(124.77)
    assert result.avg_fill_price == pytest.approx(.8050)
    assert len(transport.orders) == 1
    assert transport.orders[0]['sz'] == '124.77'
    assert result.execution_quote['market_data_environment'] == 'DEMO'
    assert result.execution_evidence['submitted_order']['clOrdId'] == 'SuiAttempt1'
    assert result.execution_evidence['submitted_order']['px'] == transport.orders[0]['px']


def test_demo_depth_shortage_blocks_before_any_protection_cancel(market):
    broker, transport = market
    transport.depth['DEMO'] = 50
    with pytest.raises(BrokerFehler, match='Orderbuchtiefe'):
        broker.schliesse_position(sui(), 124.771765, .8179)
    assert not transport.orders
    assert all(method == 'GET' for method, _, _ in transport.calls)


def test_demo_market_outage_never_falls_back_to_live(market):
    broker, transport = market
    transport.book_error = True
    with pytest.raises(BrokerFehler):
        broker.execution_quote(sui(), 124.77, side='sell')
    assert len(transport.calls) == 1
    assert transport.calls[0][2]['x-simulated-trading'] == '1'
    assert not transport.orders


def test_broker_client_environment_mismatch_is_blocked_before_request(market):
    broker, transport = market
    broker.client.demo = False
    with pytest.raises(BrokerFehler, match='Umgebung'):
        broker.schliesse_position(sui(), 124.771765, .8179)
    assert not transport.calls


def test_execution_evidence_contains_matching_book_depth_and_environment(market):
    broker, _ = market
    q = broker.execution_quote(sui(), 124.77, side='sell')
    assert q['market_data_environment'] == 'DEMO'
    assert q['book_levels'] == [[.8050, 1000.0]]
    assert q['source'] == '/api/v5/market/books'
    assert q['timestamp_ms'] > 0


def test_rounding_may_not_break_hard_price_floor(market):
    broker, transport = market
    transport.bids['DEMO'] = .805
    # A .3 tick leaves no valid sell price between .805 * .97 and .805.
    broker.client._instruments['SUI-USDC'].tick_size = '.3'
    with pytest.raises(BrokerFehler, match='Tick|Preis'):
        broker.schliesse_position(sui(), 124.771765, .8179)
    assert not transport.orders


@pytest.mark.parametrize('bid,qty', [(float('nan'), 1000), (float('inf'), 1000),
                                    (0, 1000), (.805, -1), (.805, float('nan'))])
def test_bad_book_never_removes_protection_or_submits(market, bid, qty):
    broker, transport = market
    transport.bids['DEMO'] = bid
    transport.depth['DEMO'] = qty
    with pytest.raises(BrokerFehler, match='Orderbuch'):
        broker.schliesse_position(sui(), 124.771765, .8179)
    assert not transport.orders
    assert all(method == 'GET' for method, _, _ in transport.calls)


def test_duplicate_sell_client_id_is_unknown_until_exact_order_is_found(market, monkeypatch):
    from broker.okx import _DOPPELTE_CLORDID
    broker, transport = market
    def duplicate(body):
        raise BrokerFehler(_DOPPELTE_CLORDID + ': duplicated client order ID')
    monkeypatch.setattr(broker.client, 'place_order', duplicate)
    monkeypatch.setattr(broker, '_suche_order', lambda *a: {})
    with pytest.raises(OrderStatusUnklar) as caught:
        broker.schliesse_position(sui(), 124.771765, .8179, client_order_id='AlreadySent')
    assert caught.value.reference_id == 'AlreadySent'
    assert not transport.orders


def test_duplicate_client_id_adopts_existing_sell_without_second_post(market, monkeypatch):
    from broker.okx import _DOPPELTE_CLORDID
    broker, transport = market
    original = broker.client.place_order
    calls = []
    def duplicate(body):
        calls.append(body)
        original(body)  # an accepted order whose initial acknowledgement was lost
        raise BrokerFehler(_DOPPELTE_CLORDID + ': duplicated client order ID')
    monkeypatch.setattr(broker.client, 'place_order', duplicate)
    result = broker.schliesse_position(sui(), 124.771765, .8179, client_order_id='ExistingSell')
    assert result.terminal and result.fill_evidence_complete
    assert result.filled_quantity == pytest.approx(124.77)
    assert len(calls) == len(transport.orders) == 1


@pytest.mark.parametrize('field,value', [('ordId','other'), ('clOrdId','other'),
    ('instId','SUI-EUR'), ('side','buy')])
def test_wrong_status_identity_cannot_be_booked_as_our_sell(market, monkeypatch, field, value):
    broker, transport = market
    original = broker.client.order_status
    def wrong(*a, **kw):
        return {**original(*a, **kw), field: value}
    monkeypatch.setattr(broker.client, 'order_status', wrong)
    with pytest.raises(OrderStatusUnklar) as caught:
        broker.schliesse_position(sui(), 124.771765, .8179, client_order_id='ExactSell')
    assert caught.value.reference_id == 'ExactSell'
    assert caught.value.order_ids == ['9000']
    assert len(transport.orders) == 1


def test_error_after_accepted_sell_is_not_a_retryable_rejection(market, monkeypatch):
    broker, transport = market
    def broken(**kw):
        raise BrokerFehler('synthetic evidence error after acceptance')
    monkeypatch.setattr(broker, '_order_result_from_evidence', broken)
    with pytest.raises(OrderStatusUnklar) as caught:
        broker.schliesse_position(sui(), 124.771765, .8179, client_order_id='AcceptedSell')
    assert caught.value.reference_id == 'AcceptedSell'
    assert caught.value.order_ids == ['9000']
    assert caught.value.accepted
    assert len(transport.orders) == 1


def test_trade_display_and_cache_separate_demo_and_live_for_same_pair(market, monkeypatch):
    import trade_chart_data as chart
    _, transport = market
    def factory():
        client = OKXClient(demo=True)
        client.session = transport
        return client
    monkeypatch.setattr(chart, '_CLIENT_FACTORY', factory)
    monkeypatch.setattr(chart, '_LIVE_CACHE', {})
    row = dict(broker='okx', symbol='SUI', broker_position_id='SUI-USDC',
               waehrung='USDC', menge=124.77, einstieg_preis=.7843, einstieg_gebuehr=.34)
    result = chart.live_metrics_for_trades([{**row, 'paper': True}, {**row, 'paper': False}])
    assert [r['current_price'] for r in result] == pytest.approx([.805, .8175])
    assert [r['market_data_environment'] for r in result] == ['DEMO', 'LIVE']
    assert len(chart._LIVE_CACHE) == 2


@pytest.mark.parametrize('extra', [{}, {'paper': True, 'environment': 'LIVE'}])
def test_unknown_display_environment_never_guesses_a_market(market, monkeypatch, extra):
    import trade_chart_data as chart
    broker, transport = market
    monkeypatch.setattr(chart, '_CLIENT_FACTORY', lambda: broker.client)
    row = dict(broker='okx', symbol='SUI', broker_position_id='SUI-USDC', menge=124.77)
    result = chart.live_metrics_for_trades([{**row, **extra}])[0]
    assert result['current_price'] is None
    assert result['live_result_quality'] == 'ENVIRONMENT_UNKNOWN'
    assert not transport.calls


@pytest.mark.parametrize('environment,demo', [('LIVE', False), ('DEMO', True)])
def test_historical_chart_client_uses_stored_environment(market, monkeypatch, environment, demo):
    import trade_chart_data as chart
    broker, transport = market
    monkeypatch.setattr(chart, '_CLIENT_FACTORY', lambda: broker.client)
    client = chart._client_for_environment(environment)
    client.orderbook('SUI-USDC')
    assert client.demo is demo
    assert (transport.calls[-1][2].get('x-simulated-trading') == '1') is demo
