"""Regressionen fuer den eToro-Scanner-Patch in NEXUS 9.0.8."""
from __future__ import annotations

import inspect

import pytest


def test_live_trader_imports_uncertain_order_exception():
    """Der except-Zweig darf den eigentlichen Brokerfehler nie maskieren."""
    import live_trader
    from broker.base import OrderStatusUnklar

    assert live_trader.OrderStatusUnklar is OrderStatusUnklar


def test_read_limiter_spaces_requests_instead_of_bursting(monkeypatch):
    from broker import etoro

    clock = {"now": 100.0}
    sleeps: list[float] = []
    monkeypatch.setattr(etoro.time, "monotonic", lambda: clock["now"])

    def advance(seconds: float):
        sleeps.append(seconds)
        clock["now"] += seconds

    monkeypatch.setattr(etoro.time, "sleep", advance)
    limiter = etoro._RollingLimiter(54, min_interval=1.15)

    assert limiter.acquire(timeout=2.0)
    assert limiter.acquire(timeout=2.0)
    assert sleeps
    assert clock["now"] == pytest.approx(101.15)


def test_scanner_loads_position_only_after_signal_evaluation():
    """HOLD-Instrumente duerfen keinen eigenen Depotabruf mehr erzeugen."""
    import live_trader

    source = inspect.getsource(live_trader.run)
    loop = source[source.index("for inst in block:"):]
    history_at = loop.index("df = broker.historie(")
    signal_at = loop.index("signal = generate_signal(")
    position_at = loop.index("pos, avg = current_position(broker, inst)")

    assert history_at < signal_at < position_at
