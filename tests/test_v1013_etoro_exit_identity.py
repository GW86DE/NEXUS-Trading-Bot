"""PEP Sunday cancel / Monday position close regression, entirely offline."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace

import pytest

import broker_exit_journal as journal
import execution_lifecycle as lifecycle
from broker.base import OrderStatusUnklar


ACCOUNT = '664e7dfbc13e0cd60e493592'
PID = '3597440106'
OID = '380995258'
I = '1043'
NOW = datetime(2026, 9, 14, 13, 50, tzinfo=timezone.utc)


def setup_order(*, account=ACCOUNT, env='DEMO', oid=OID, client='pep-sunday', quantity=109):
    intent, _ = journal.begin(broker='etoro', account_fingerprint=account,
        environment=env, instrument_id=I, position_id=PID, quantity=quantity,
        client_order_id=client)
    journal.update(intent['intent_id'], 'SUBMITTED', broker_order_id=oid,
        detail={'orderID': oid, 'statusID': 7, 'positions': [], 'proceeds': 0})
    with journal._connect() as con:
        con.execute("UPDATE broker_exit_intents SET created_at='2026-09-13T20:06:23Z' WHERE intent_id=?", (intent['intent_id'],))
    key = lifecycle.reserve(broker='etoro', account=account, environment=env,
        instrument=I, side='SELL', client_id=client, quantity=quantity,
        request={}, position_id=PID)
    lifecycle.accepted(key, oid)
    return intent['intent_id'], key


def evidence():
    scope = dict(account_fingerprint=ACCOUNT, environment='DEMO', complete=True,
                 snapshot_at=NOW.isoformat())
    snap = dict(scope, snapshot_id='bound-portfolio', rows=[], open_ids=set(), position_ids=set())
    hist = dict(scope, truncated=False, rows=[dict(positionId=PID, instrumentId=1043,
        orderId='380127623', isBuy=True, units=109, closeRate=138.59,
        closeTimestamp='2026-09-14T13:32:26.867Z')])
    return snap, hist


def repair(snap=None, hist=None):
    s, h = evidence()
    return journal.reconcile_position_closures(account_fingerprint=ACCOUNT,
        environment='DEMO', snapshot=s if snap is None else snap,
        history=h if hist is None else hist, now=NOW)


def intent_row(iid):
    with journal._connect() as con:
        return dict(con.execute('SELECT * FROM broker_exit_intents WHERE intent_id=?', (iid,)).fetchone())


def test_position_only_history_never_confirms_old_order():
    iid, _ = setup_order()
    assert journal.confirm_from_fill(broker='etoro', account_fingerprint=ACCOUNT,
        environment='DEMO', instrument_id=I, position_id=PID,
        filled_quantity=109, fill_identity='position-close') == ''
    assert intent_row(iid)['status'] == 'SUBMITTED'
    assert intent_row(iid)['filled_quantity'] == 0


def test_pep_migration_keeps_old_order_unknown_and_allows_no_second_sell():
    iid, key = setup_order()
    # This is exactly the pre-upgrade bug: a position-only receipt had
    # changed the exit journal but not the execution-order projection.
    journal.update(iid, 'FILLED', filled_quantity=109,
        detail={'positionId': PID, 'close_order_id': '', 'closeRate': 138.59,
                'units': 109, 'closeTimestamp': '2026-09-14T13:32:26.867Z'})
    with journal._connect() as con:
        con.execute('INSERT INTO broker_exit_fill_events(intent_id,fill_identity,quantity,created_at) VALUES(?,?,?,?)',
                    (iid, 'old-position-fill', 109, NOW.isoformat()))
    assert len(repair()) == 1
    assert len(repair()) == 1  # replay also repairs an interrupted second DB projection
    saved = intent_row(iid)
    assert saved['status'] == 'POSITION_CLOSED_ORDER_UNPROVEN'
    assert saved['filled_quantity'] == 0
    proof = json.loads(saved['detail_json'])
    assert proof['position_closed_quantity'] == 109 and proof['order_outcome'] == 'UNPROVEN'
    old = lifecycle.lookup(broker='etoro', account=ACCOUNT, environment='DEMO', client_id='pep-sunday')
    assert old['state'] == saved['status'] and old['terminal'] == 0 and old['evidence_complete'] == 0
    assert old['filled'] == '0' and old['accounted'] == 0
    assert lifecycle.snapshot(broker='etoro', active_only=True) == []
    from etoro_open_operations import snapshot
    assert snapshot(ACCOUNT, 'DEMO')['pending'] == 0
    with journal._connect() as con:
        audits = con.execute('SELECT * FROM broker_exit_reconciliation_events').fetchall()
        assert len(audits) == 1
        assert json.loads(audits[0]['detail_json'])['previous_intent']['filled_quantity'] == 109
    with lifecycle._connect() as con:
        assert con.execute('SELECT COUNT(*) FROM execution_fills').fetchone()[0] == 0
        assert con.execute("SELECT COUNT(*) FROM execution_events WHERE kind='POSITION_CLOSED_ORDER_UNPROVEN'").fetchone()[0] == 1
    assert journal.begin(broker='etoro', account_fingerprint=ACCOUNT, environment='DEMO',
        instrument_id=I, position_id=PID, quantity=109, client_order_id='accidental-retry')[1] is False
    with pytest.raises(OrderStatusUnklar):
        lifecycle.reserve(broker='etoro', account=ACCOUNT, environment='DEMO',
            instrument=I, side='SELL', client_id='accidental-retry', quantity=109, position_id=PID, request={})
    # The order-identity projection itself permits independent BUYs; the
    # separate risk gate continues to require actual financial receipts.
    lifecycle.assert_clear(broker='etoro', account=ACCOUNT, environment='DEMO', instrument=I, side='BUY')


@pytest.mark.parametrize('change', ['open_position', 'partial_history', 'foreign_instrument', 'missing_time', 'before_request'])
def test_no_terminal_exposure_from_partial_or_wrong_evidence(change):
    iid, _ = setup_order()
    snap, hist = evidence()
    if change == 'open_position':
        snap.update(rows=[dict(positionId=PID)], open_ids={PID}, position_ids={PID})
    elif change == 'partial_history': hist['rows'][0]['units'] = 20
    elif change == 'foreign_instrument': hist['rows'][0]['instrumentId'] = 9999
    elif change == 'missing_time': hist['rows'][0].pop('closeTimestamp')
    elif change == 'before_request': hist['rows'][0]['closeTimestamp'] = '2026-09-12T13:32:26Z'
    assert repair(snap, hist) == []
    assert intent_row(iid)['status'] == 'SUBMITTED'


@pytest.mark.parametrize('which,field,value', [('snapshot','account_fingerprint','other'),
    ('history','environment','LIVE'), ('snapshot','complete',False), ('history','complete',False),
    ('snapshot','snapshot_at','2026-09-14T13:00:00Z'), ('history','snapshot_at','2026-09-14T13:00:00Z')])
def test_fresh_complete_account_and_environment_required(which, field, value):
    iid, _ = setup_order()
    snap, hist = evidence()
    (snap if which == 'snapshot' else hist)[field] = value
    with pytest.raises(ValueError): repair(snap, hist)
    assert intent_row(iid)['status'] == 'SUBMITTED'


def test_exact_other_order_never_completes_sunday_order():
    iid, _ = setup_order()
    assert journal.confirm_from_fill(broker='etoro', account_fingerprint=ACCOUNT,
        position_id=PID, broker_order_id='different-actual-exit', filled_quantity=109,
        fill_identity='native') == ''
    snap, hist = evidence()
    hist['rows'][0]['closeOrderId'] = 'different-actual-exit'
    assert len(repair(snap, hist)) == 1
    assert intent_row(iid)['status'] == 'POSITION_CLOSED_ORDER_UNPROVEN'


def test_exact_native_partial_fills_are_idempotent_and_account_isolated():
    iid, _ = setup_order()
    fields = dict(broker='etoro', account_fingerprint=ACCOUNT, environment='DEMO',
        instrument_id=I, position_id=PID, broker_order_id=OID)
    assert journal.confirm_from_fill(**dict(fields, account_fingerprint='other'), filled_quantity=109, fill_identity='foreign') == ''
    for _ in range(2): journal.confirm_from_fill(**fields, filled_quantity=40, fill_identity='native-1')
    assert intent_row(iid)['status'] == 'PARTIALLY_FILLED' and intent_row(iid)['filled_quantity'] == 40
    journal.confirm_from_fill(**fields, filled_quantity=69, fill_identity='native-2')
    assert intent_row(iid)['status'] == 'FILLED' and intent_row(iid)['filled_quantity'] == 109
    assert repair() == []  # actual order-specific fills are never downgraded


def test_numeric_seven_never_means_cancel_and_explicit_cancel_never_reposts():
    iid, _ = setup_order()
    fields = dict(account_fingerprint=ACCOUNT, environment='DEMO', intent_id=iid, broker_order_id=OID)
    with pytest.raises(ValueError):
        journal.confirm_cancelled(**fields, detail={'orderID': OID, 'statusID': 7, 'positions': []})
    assert journal.confirm_cancelled(**fields, detail={'orderID': OID, 'CID':21577959, 'status': {'name': 'Cancelled'}, 'positions': []})
    assert intent_row(iid)['status'] == 'CANCELED'
    assert lifecycle.lookup(broker='etoro', account=ACCOUNT, environment='DEMO', client_id='pep-sunday')['state'] == 'CANCELED'
    assert journal.begin(broker='etoro', account_fingerprint=ACCOUNT, environment='DEMO', instrument_id=I,
        position_id=PID, quantity=109, client_order_id='new-retry')[1] is False
    with pytest.raises(OrderStatusUnklar): lifecycle.assert_clear(broker='etoro', account=ACCOUNT,
        environment='DEMO', instrument=I, position_id=PID, side='SELL')


def test_migration_retries_lifecycle_after_interrupted_projection(monkeypatch):
    iid, _ = setup_order()
    original = lifecycle.observe_etoro_position_closed
    monkeypatch.setattr(lifecycle, 'observe_etoro_position_closed', lambda _: (_ for _ in ()).throw(RuntimeError('simulated crash')))
    with pytest.raises(RuntimeError): repair()
    assert intent_row(iid)['status'] == 'POSITION_CLOSED_ORDER_UNPROVEN'
    assert lifecycle.lookup(broker='etoro', account=ACCOUNT, environment='DEMO', client_id='pep-sunday')['state'] == 'OPEN'
    monkeypatch.setattr(lifecycle, 'observe_etoro_position_closed', original)
    repair()
    assert lifecycle.lookup(broker='etoro', account=ACCOUNT, environment='DEMO', client_id='pep-sunday')['state'] == 'POSITION_CLOSED_ORDER_UNPROVEN'


def test_other_accounts_and_environments_are_never_migrated():
    own, _ = setup_order()
    foreign, _ = setup_order(account='other', client='other')
    live, _ = setup_order(env='LIVE', client='live')
    repair()
    assert intent_row(own)['status'] == 'POSITION_CLOSED_ORDER_UNPROVEN'
    assert intent_row(foreign)['status'] == intent_row(live)['status'] == 'SUBMITTED'


def test_duplicate_history_is_not_double_counted_and_conflict_is_rejected():
    iid, _ = setup_order()
    snap, hist = evidence()
    hist['rows'][0].update(units=60, executionID='native-123')
    hist['rows'].append(deepcopy(hist['rows'][0]))
    assert repair(snap, hist) == []  # 60 twice is not 120
    hist['rows'][1]['units'] = 49
    assert repair(snap, hist) == []  # same native event cannot have two quantities
    assert intent_row(iid)['status'] == 'SUBMITTED'


def test_cancel_projection_recovers_after_interrupted_second_db(monkeypatch):
    iid, _ = setup_order()
    original = lifecycle.observe_etoro_cancelled
    monkeypatch.setattr(lifecycle, 'observe_etoro_cancelled', lambda **_: (_ for _ in ()).throw(RuntimeError('crash')))
    with pytest.raises(RuntimeError):
        journal.confirm_cancelled(account_fingerprint=ACCOUNT, environment='DEMO', intent_id=iid,
            broker_order_id=OID, detail={'orderID':OID, 'CID':21577959, 'statusName':'Cancelled', 'positions':[]})
    monkeypatch.setattr(lifecycle, 'observe_etoro_cancelled', original)
    for _ in range(2): journal.replay_cancelled_projections(account_fingerprint=ACCOUNT, environment='DEMO')
    assert lifecycle.lookup(broker='etoro', account=ACCOUNT, environment='DEMO', client_id='pep-sunday')['state'] == 'CANCELED'


def test_later_exact_order_receipt_can_upgrade_unknown_outcome():
    iid, _ = setup_order()
    repair()
    journal.confirm_from_fill(broker='etoro', account_fingerprint=ACCOUNT, environment='DEMO',
        instrument_id=I, position_id=PID, broker_order_id=OID, filled_quantity=109,
        fill_identity='later-native-order-proof', detail={'closeOrderId':OID})
    assert intent_row(iid)['status'] == 'FILLED'
    assert repair() == []


@pytest.mark.parametrize('broker_reason,label,proven', [('', 'BROKER-VERKAUF', False),
    ('TakeProfit', 'TAKE-PROFIT', True), ('StopLoss', 'STOP-LOSS', True),
    ('manual stop modification', 'BROKER-VERKAUF', False)])
def test_pep_above_take_price_does_not_prove_tp_cause(broker_reason, label, proven):
    from live_trader import _etoro_exit_attribution
    broker = SimpleNamespace(name='etoro', paper=True, account_fingerprint=lambda: ACCOUNT)
    rec = SimpleNamespace(management_mode='AUTO', source='BOT', owned_position_ids=[PID],
        broker_account_fingerprint=ACCOUNT, broker_environment='DEMO', planned_stop=135.9, planned_take=138.52)
    fill = SimpleNamespace(execution_reason=broker_reason, broker_id=PID)
    owned, result = _etoro_exit_attribution(broker=broker, fill=fill, rec_before=rec,
        meta={}, sell_bot_owned=False, price=138.59)
    assert owned and result['label'] == label and result['protective_exit_attributed'] is proven
    assert result['protective_price_match'] == 'TAKE-PROFIT'


def test_adapter_history_poll_repairs_previously_misattributed_pep(monkeypatch):
    import time
    from broker.etoro import EtoroBroker
    iid, _ = setup_order()
    journal.update(iid, 'FILLED', filled_quantity=109, detail={'close_order_id':'', 'positionId':PID})
    broker = EtoroBroker(paper=True, api_key='offline', user_key='offline')
    assert broker._bind_account_identity({'demoCid':21577959}) == ACCOUNT
    broker._last_close_order_poll = float('inf')
    broker._last_history_poll = 0
    broker._history_recovery_complete = True
    broker._symbol_for_id = lambda _: 'PEP'
    snap, hist = evidence()
    broker._pnl_snapshot = (time.monotonic(), {'offline':True})
    broker.position_snapshot = lambda **_: deepcopy(snap)
    broker.trade_history_snapshot = lambda *_, **__: deepcopy(hist)
    original = journal.reconcile_position_closures
    monkeypatch.setattr(journal, 'reconcile_position_closures', lambda **kw: original(**kw, now=NOW))
    fills = broker.fills()
    assert len(fills) == 1 and fills[0].order_id == '' and fills[0].quantity == 109
    assert intent_row(iid)['status'] == 'POSITION_CLOSED_ORDER_UNPROVEN'
    assert lifecycle.lookup(broker='etoro', account=ACCOUNT, environment='DEMO', client_id='pep-sunday')['state'] == 'POSITION_CLOSED_ORDER_UNPROVEN'
