"""Regression fuer den Post-BUY-/unabhaengigen SELL-Abrechnungsrace."""
from __future__ import annotations

from types import SimpleNamespace

import pytest


def test_critical_sell_accounting_after_buy_submit_preserves_buy_ownership():
    from instrument_identity import canonical_key
    from live_trader import (
        _CriticalFillAccountingError,
        _cleanup_candidate_failure_state,
    )

    instrument = SimpleNamespace(name="ADBE", asset_type="stock")
    pending_key = f"PENDING_BUY:{canonical_key('ADBE', 'stock')}"
    pending = {
        "owner": "BOT",
        "decision_id": 951001,
        "reference_id": "accepted-buy-reference",
        "account_fingerprint": "current-etoro-account",
    }
    order_meta = {
        pending_key: dict(pending),
        "accepted-buy-order": dict(pending),
    }

    class OwnershipRegistry:
        def __init__(self):
            self.pending = {canonical_key("ADBE", "stock"): dict(pending)}
            self.clear_calls = []

        def clear_pending(self, symbol, asset_type):
            self.clear_calls.append((symbol, asset_type))
            self.pending.pop(canonical_key(symbol, asset_type), None)

    registry = OwnershipRegistry()
    cooldowns = {}
    accounting_error = _CriticalFillAccountingError(
        "unabhaengiger eToro-SELL konnte nicht ins Ledger geschrieben werden")

    # Dies ist exakt die Ausnahmegrenze des unmittelbaren Fill-Polls nach einem
    # bereits akzeptierten BUY. Der SELL-Fehler darf nicht in die generische
    # Kandidatenbereinigung fallen.
    with pytest.raises(_CriticalFillAccountingError) as raised:
        _cleanup_candidate_failure_state(
            accounting_error,
            instrument=instrument,
            order_meta=order_meta,
            ownership_registry=registry,
            last_data_error=cooldowns,
            failed_at=1234.0,
        )

    assert raised.value is accounting_error
    assert order_meta[pending_key] == pending
    assert order_meta["accepted-buy-order"] == pending
    assert registry.pending[canonical_key("ADBE", "stock")] == pending
    assert registry.clear_calls == []
    assert cooldowns == {}


def test_normal_candidate_failure_still_cleans_pending_and_sets_cooldown():
    from instrument_identity import canonical_key
    from live_trader import _cleanup_candidate_failure_state

    instrument = SimpleNamespace(name="ADBE", asset_type="stock")
    pending_key = f"PENDING_BUY:{canonical_key('ADBE', 'stock')}"
    order_meta = {pending_key: {"owner": "BOT"}}

    class OwnershipRegistry:
        def __init__(self):
            self.clear_calls = []

        def clear_pending(self, symbol, asset_type):
            self.clear_calls.append((symbol, asset_type))

    registry = OwnershipRegistry()
    cooldowns = {}

    _cleanup_candidate_failure_state(
        RuntimeError("Kaufkandidat lokal fehlgeschlagen"),
        instrument=instrument,
        order_meta=order_meta,
        ownership_registry=registry,
        last_data_error=cooldowns,
        failed_at=1234.0,
    )

    assert pending_key not in order_meta
    assert registry.clear_calls == [("ADBE", "stock")]
    assert cooldowns == {"ADBE": 1234.0}
