"""Order-scoped evidence regressions, synthetic transport (no broker writes)."""
from copy import deepcopy
from types import SimpleNamespace as NS
import pytest
from broker.okx import OKXClient, OKXBroker, OKXInstrument
from broker.base import BrokerFehler
from test_v983_doge_protection import incident, evidence_broker, evidence_intent


def test_fills_filter_order_at_server(monkeypatch):
    c = OKXClient('', '', '')
    calls = []
    monkeypatch.setattr(c, 'request', lambda *a, **k: calls.append((a, k)) or [])
    c.fills('DOGE-USD', ord_id='3907950705910681600')
    c.fills_history('DOGE-USD', ord_id='3907950705910681600', after='previous-bill')
    assert all(k['params']['ordId'] == '3907950705910681600' for a, k in calls)
    assert calls[1][1]['params']['after'] == 'previous-bill'
    assert all(a[0] == 'GET' for a, k in calls)


def test_complete_positive_order_does_not_need_entire_instrument_archive(incident, monkeypatch):
    b = evidence_broker(incident, incident['fills'][:1])
    b.client.fills_history_paginated = lambda *a, **k: deepcopy(incident['fills'])
    b.client._last_fills_history_complete = False
    monkeypatch.setattr('broker.okx.time.sleep', lambda _: None)
    result = b.reconcile_order_evidence(evidence_intent(incident))
    assert result.fill_evidence_complete
    assert result.filled_quantity == pytest.approx(531.6552180855)
    assert len(result.fill_ids) == 6


def test_zero_execution_still_needs_negative_evidence(incident, monkeypatch):
    b = evidence_broker(incident, [])
    b.client._last_fills_history_complete = False
    b.client.order_status = lambda *a, **k: dict(incident['order'], state='canceled', accFillSz='0')
    monkeypatch.setattr('broker.okx.time.sleep', lambda _: None)
    result = b.reconcile_order_evidence(evidence_intent(incident))
    assert not result.fill_evidence_complete
    assert result.filled_quantity == 0


def test_repeated_full_archive_page_is_not_complete(monkeypatch):
    c = OKXClient('', '', '')
    batch = [dict(instId='DOGE-USD', ordId='o', tradeId=str(i), billId=str(i)) for i in range(100)]
    monkeypatch.setattr(c, 'fills_history', lambda *a, **k: deepcopy(batch))
    assert len(c.fills_history_paginated('DOGE-USD', max_pages=4)) == 100
    assert not c._last_fills_history_complete


def test_conflicting_duplicate_fill_is_not_silently_overwritten(incident, monkeypatch):
    rows = deepcopy(incident['fills'])
    rows.append(dict(rows[0], fillPx='999'))
    b = evidence_broker(incident, rows)
    monkeypatch.setattr('broker.okx.time.sleep', lambda _: None)
    with pytest.raises(BrokerFehler, match='[Ww]iderspr'):
        b.reconcile_order_evidence(evidence_intent(incident))


def test_wrong_instrument_cannot_overwrite_exact_order_fill(incident, monkeypatch):
    rows = deepcopy(incident['fills'])
    rows.append(dict(rows[0], instId='DOGE-EUR'))
    b = evidence_broker(incident, rows)
    monkeypatch.setattr('broker.okx.time.sleep', lambda _: None)
    # Same exact order with conflicting instrument is a corrupt response, not an alias.
    with pytest.raises(BrokerFehler):
        b.reconcile_order_evidence(evidence_intent(incident))


def test_fill_time_orders_executions_not_late_record_time(incident):
    rows = deepcopy(incident['fills'])
    for i, row in enumerate(rows):
        row['ts'] = str(1000 + i)
        row['fillTime'] = str(100 - i)
    b = evidence_broker(incident, rows)
    result = b.order_fills('DOGE-USD', incident['order']['ordId'], expected_qty=533.522547)
    assert [r['fillTime'] for r in result] == [str(x) for x in range(95, 101)]


def test_algo_cancel_uses_broker_row_instrument_not_callers_alias():
    sent = []
    c = NS(hat_zugangsdaten=True, clock_offset_seconds=0,
           pending_orders=lambda *a, **k: [], algo_order_details=lambda *a: {},
           cancel_algo_orders=lambda rows: sent.extend(rows))
    b = OKXBroker(client=c)
    b.offene_schutzorders = lambda *a, **k: [dict(algoId='exact', instId='DOGE-USD',
        algoClOrdId='ours', side='sell')]
    b.storniere_offene_orders('DOGE-USDC', protection_algo_id='exact', protection_client_id='ours')
    assert sent == [dict(instId='DOGE-USD', algoId='exact')]


def test_algo_id_and_client_conflict_does_not_cancel_foreign_order():
    sent = []
    c = NS(hat_zugangsdaten=True, clock_offset_seconds=0,
           pending_orders=lambda *a, **k: [], algo_order_details=lambda *a: {},
           cancel_algo_orders=lambda rows: sent.extend(rows))
    b = OKXBroker(client=c)
    b.offene_schutzorders = lambda *a, **k: [dict(algoId='foreign', instId='DOGE-USD',
        algoClOrdId='ours', side='sell')]
    b.storniere_offene_orders('DOGE-USD', protection_algo_id='exact', protection_client_id='ours')
    assert not sent


def test_archive_duplicate_conflict_cannot_be_hidden_by_page_deduplication():
    from broker.okx import OKXClient
    from broker.base import BrokerFehler
    from copy import deepcopy
    first = dict(instId='DOGE-USD',ordId='A',tradeId='1',fillSz='1',fillPx='.1',fee='-.01',feeCcy='USD')
    client=OKXClient.__new__(OKXClient)
    client.fills_history=lambda *a,**k:[deepcopy(first),dict(first,fillPx='.2')]
    with pytest.raises(BrokerFehler,match='Fillbeleg'):
        client.fills_history_paginated('DOGE-USD',ord_id='A')
