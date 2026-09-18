from types import SimpleNamespace as NS
import pytest
from broker.base import Fill,BrokerFehler


def fill(**changes):
    return Fill(**{**dict(fill_id='txnf',order_id='379811422',symbol='TXN',side='BUY',quantity=57,
        price=259.78,broker_id='3596233679',asset_type='stock',currency='USD',
        broker_detail={'instrumentId':1634}),**changes})


def test_txn_target_is_typed_and_never_uses_position_as_instrument():
    from live_trader import _etoro_fill_instrument
    target,iid=_etoro_fill_instrument(fill(),{'instrument_id':1634})
    assert target.name=='TXN' and target.asset_type=='stock'
    assert iid=='1634' and target.contract.conId=='1634'
    assert iid!=fill().broker_id


def test_missing_instrument_hint_uses_typed_symbol_resolution_not_position_id():
    from live_trader import _etoro_fill_instrument
    target,iid=_etoro_fill_instrument(fill(broker_detail={}),{})
    assert target.name=='TXN' and iid=='' and target.contract.conId==''


@pytest.mark.parametrize('detail,meta', [({'instrumentId':1634},{'instrument_id':999}),
    ({'instrumentId':'NaN'},{}),({'instrumentId':-1},{})])
def test_conflicting_identity_fails_before_protection_lookup(detail,meta):
    from live_trader import _etoro_fill_instrument
    with pytest.raises(BrokerFehler):_etoro_fill_instrument(fill(broker_detail=detail),meta)


def test_real_etoro_resolver_accepts_fill_target_and_checks_exact_position(monkeypatch):
    from live_trader import _etoro_fill_instrument
    from broker.etoro import EtoroBroker
    b=EtoroBroker.__new__(EtoroBroker)
    target,iid=_etoro_fill_instrument(fill(),{})
    b._instrument_cache={};b._instrument_by_id={};b._instrument_candidates={'TXN':[
        dict(instrumentId=1634,symbol='TXN',symbolFull='TXN',_detected_asset_type='stock')]}
    b._load_all_instruments=lambda:None
    b._pnl=lambda **k:{'positions':[dict(positionId='3596233679',instrumentId=1634,isBuy=True)]}
    b._all_positions=lambda p:p['positions']
    # Missing stops is a normal unconfirmed protection result, not Asset-Typ ?.
    result=b.reconcile_position_protection(target,57,233,280,position_ids=['3596233679'],instrument_id=iid)
    assert isinstance(result,dict)
