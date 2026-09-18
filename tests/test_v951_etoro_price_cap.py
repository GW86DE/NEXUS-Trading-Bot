"""Functional regressions for the eToro entry-price and risk cap.

The ADBE values are the broker-confirmed incident from 2026-09-01.  These
tests execute the adapter and persistent reconciliation path; they never
inspect production source text.
"""
from __future__ import annotations

from copy import deepcopy

import pytest


ACCOUNT = "ba32f97fe3482fcbc326e51a"
DECISION = 4153277691980212224
ORDER = "378375675"
REFERENCE = "ac14a874-980a-468d-982f-57df9c4d56b9"
POSITION = "3592625451"

SIGNAL_PRICE = 290.07
STOP = 287.1696
TAKE = 295.8707
QUANTITY = 51.0
LIVE_ASK = 293.95


def _instrument():
    from contracts import Instrument, SimpleContract

    return Instrument(
        "ADBE", SimpleContract("ADBE"), "stock", "USD", "Software", "ETORO")


def _broker():
    from broker.etoro import EtoroBroker

    broker = EtoroBroker(paper=True, api_key="api", user_key="account")
    broker._connected = True
    broker._account_fingerprint = ACCOUNT
    broker._resolve = lambda _instrument: {
        "instrumentId": 1126,
        "symbol": "ADBE",
        "symbolFull": "ADBE",
    }
    broker._settlement = lambda _instrument: "real"
    broker._validate_protection = lambda *_args, **_kwargs: None
    broker.reconcile_position_protection = lambda *_args, **_kwargs: {
        "protection_confirmed": True,
    }
    return broker


def _filled_order(*, price: float, reference_id: str = REFERENCE) -> dict:
    return {
        "orderId": int(ORDER),
        "referenceId": str(reference_id),
        "status": {"id": 3, "name": "Filled", "errorCode": 0},
        "positionExecutions": [{
            "positionId": int(POSITION),
            "state": "open",
            "stopLossRate": 287.17,
            "takeProfitRate": 295.87,
            "openingData": {
                "orderId": int(ORDER),
                "executionTime": "2026-09-01T14:38:32.343Z",
                "units": QUANTITY,
                "avgPrice": float(price),
                "fees": 1.0,
                "taxes": 0.0,
            },
        }],
    }


@pytest.fixture
def reconciliation_env(monkeypatch, tmp_path):
    monkeypatch.setenv("TRADINGBOT_TEST_STATE_DIR", str(tmp_path))
    import decision_analytics as analytics
    import etoro_reconciliation as reconciliation

    monkeypatch.setattr(analytics, "DB_PATH", tmp_path / "decisions.sqlite")
    monkeypatch.setattr(reconciliation, "_notify", lambda *_args, **_kwargs: None)
    analytics.init_db()
    return reconciliation, analytics


@pytest.mark.parametrize(
    ("max_slippage", "risk_multiplier", "limiting_rule"),
    [
        (0.002, 5.0, "slippage"),
        (0.05, 1.05, "risk"),
    ],
)
def test_v3_payload_uses_limit_ioc_and_the_stricter_configured_cap(
        monkeypatch, max_slippage, risk_multiplier, limiting_rule):
    """Both independent caps must be capable of setting ``limitRate``."""
    import config

    broker = _broker()
    calls = []

    def request(method, path, **kwargs):
        calls.append((method, path, deepcopy(kwargs)))
        return {
            "orderId": int(ORDER),
            "referenceId": str(kwargs.get("request_id") or ""),
        }

    broker._request = request
    broker._wait_open_order = lambda **_kwargs: _filled_order(price=SIGNAL_PRICE)
    monkeypatch.setattr(config, "ETORO_REQUIRE_COST_QUOTE", False)
    monkeypatch.setattr(config, "ETORO_REQUIRE_LIVE_PROTECTION_QUOTE", False)
    monkeypatch.setattr(config, "ETORO_MAX_ENTRY_SLIPPAGE_PCT", max_slippage)
    monkeypatch.setattr(config, "ETORO_MAX_ENTRY_RISK_MULTIPLIER", risk_multiplier)

    result = broker.kaufe_mit_absicherung(
        _instrument(), QUANTITY, SIGNAL_PRICE, STOP, TAKE)

    assert result.filled_quantity == pytest.approx(QUANTITY)
    assert len(calls) == 1
    method, path, request_kwargs = calls[0]
    assert method == "POST"
    assert path == "/api/v3/trading/execution/demo/orders"
    payload = request_kwargs["payload"]
    assert payload["orderType"] == "limitIOC"
    assert "limitRate" in payload

    slippage_cap = SIGNAL_PRICE * (1.0 + max_slippage)
    risk_cap = STOP + (SIGNAL_PRICE - STOP) * risk_multiplier
    expected = min(slippage_cap, risk_cap)
    assert payload["limitRate"] == pytest.approx(expected)
    if limiting_rule == "slippage":
        assert slippage_cap < risk_cap
        assert payload["limitRate"] == pytest.approx(slippage_cap)
    else:
        assert risk_cap < slippage_cap
        assert payload["limitRate"] == pytest.approx(risk_cap)


def test_adbe_live_ask_above_risk_cap_is_blocked_before_execution_post(monkeypatch):
    """The recorded 293.95 ask must never reach the execution endpoint."""
    import config
    from broker.base import BrokerFehler

    broker = _broker()
    execution_posts = []
    broker._rate_row = lambda *_args, **_kwargs: {
        "ask": LIVE_ASK,
        "lastExecution": 293.92,
    }

    def request(method, path, **kwargs):
        if method.upper() == "POST" and "/trading/execution/" in path:
            execution_posts.append((method, path, kwargs))
        raise AssertionError(f"unexpected network request: {method} {path}")

    broker._request = request
    monkeypatch.setattr(config, "ETORO_REQUIRE_COST_QUOTE", False)
    monkeypatch.setattr(config, "ETORO_REQUIRE_LIVE_PROTECTION_QUOTE", True)
    monkeypatch.setattr(config, "ETORO_MAX_ENTRY_SLIPPAGE_PCT", 0.003)
    monkeypatch.setattr(config, "ETORO_MAX_ENTRY_RISK_MULTIPLIER", 1.10)

    with pytest.raises(BrokerFehler, match="limitIOC-Preisdeckel"):
        broker.kaufe_mit_absicherung(
            _instrument(), QUANTITY, SIGNAL_PRICE, STOP, TAKE)

    assert execution_posts == []
    assert broker._fill_queue == []
    expected_cap = min(
        SIGNAL_PRICE * 1.003,
        STOP + (SIGNAL_PRICE - STOP) * 1.10,
    )
    assert LIVE_ASK > expected_cap


def test_fill_above_sent_cap_is_persistently_quarantined_and_not_queued(
        reconciliation_env, monkeypatch):
    """Impossible limitIOC fills stay auditable and outside the money pipeline."""
    import config
    from broker.base import OrderStatusUnklar
    from order_execution import submit_protected_buy

    reconciliation, analytics = reconciliation_env
    analytics.record({
        "decision_id": DECISION,
        "symbol": "ADBE",
        "asset_type": "stock",
        "broker": "etoro",
        "paper": True,
        "profile": "offensiv",
        "status": "APPROVED",
        "price": SIGNAL_PRICE,
        "qty": QUANTITY,
        "stop": STOP,
        "take": TAKE,
    })
    broker = _broker()
    posts = []

    def request(method, path, **kwargs):
        posts.append((method, path, deepcopy(kwargs)))
        return {
            "orderId": int(ORDER),
            "referenceId": str(kwargs.get("request_id") or ""),
        }

    broker._request = request
    broker._wait_open_order = lambda **kwargs: _filled_order(
        price=293.92, reference_id=str(kwargs.get("reference_id") or ""))
    monkeypatch.setattr(config, "ETORO_REQUIRE_COST_QUOTE", False)
    monkeypatch.setattr(config, "ETORO_REQUIRE_LIVE_PROTECTION_QUOTE", False)
    monkeypatch.setattr(config, "ETORO_MAX_ENTRY_SLIPPAGE_PCT", 0.003)
    monkeypatch.setattr(config, "ETORO_MAX_ENTRY_RISK_MULTIPLIER", 1.10)

    with pytest.raises(OrderStatusUnklar, match="oberhalb"):
        submit_protected_buy(
            broker, _instrument(), QUANTITY, SIGNAL_PRICE, STOP, TAKE,
            decision_id=DECISION)

    execution_posts = [
        call for call in posts
        if call[0].upper() == "POST" and "/trading/execution/" in call[1]
    ]
    assert len(execution_posts) == 1
    assert broker._fill_queue == []

    record = reconciliation.record_for(DECISION)
    assert record["manual_review_required"] is True
    assert record["execution_anomaly_code"] == "ENTRY_FILL_ABOVE_PRICE_CAP"
    assert "293.92000000" in record["execution_anomaly_detail"]
    assert "290.36004000" in record["execution_anomaly_detail"]
    assert reconciliation.active_for_domain(record["domain"])
