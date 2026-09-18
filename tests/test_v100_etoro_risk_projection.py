"""Risk receipt failures must not be swallowed by the generic fill handler."""
from datetime import datetime, timezone
import errno
import json
from types import SimpleNamespace as NS

import pytest


def pipeline(monkeypatch, tmp_path):
    # Reuse existing synthetic broker/position transport, but give capture an
    # actual file-backed RiskState. Ledger receipt values are explicit fixtures.
    from test_v951_fill_commit_pipeline import _sell_pipeline
    from risk_manager import RiskState
    import trade_ledger
    critical, run, events = _sell_pipeline(monkeypatch, tmp_path)
    cells = dict(zip(run.__code__.co_freevars, run.__closure__))
    risk = RiskState.load(tmp_path / "risk_state_etoro.json")
    cells["risk"].cell_contents = risk
    receipt = {"trade_id": 71, "broker": "etoro", "broker_account_fingerprint": "account-1",
               "paper": True, "broker_position_id": "p-1", "ausgestiegen_am": datetime.now(timezone.utc).isoformat(),
               "waehrung": "USD", "fee_quality": "CONFIRMED", "netto_pnl": -10.5,
               "brutto_pnl": -10.0, "superseded_by": None}
    monkeypatch.setattr(trade_ledger, "trade_detail", lambda _: dict(receipt))
    return critical, run, events, risk, cells, receipt


def test_realized_risk_write_failure_aborts_before_journal_and_tracker(monkeypatch, tmp_path):
    critical, run, events, risk, cells, receipt = pipeline(monkeypatch, tmp_path)
    import risk_manager
    original = risk_manager.atomic_write_json
    def fail_result(path, payload, **kw):
        if (payload.get("realized_receipts", {}).get("ledger:71", {}).get("status") == "CONFIRMED"):
            raise OSError(errno.EIO, "risk fsync")
        return original(path, payload, **kw)
    monkeypatch.setattr(risk_manager, "atomic_write_json", fail_result)
    with pytest.raises(critical, match="RISK_RECEIPT_UNCONFIRMED"): run()
    assert events == ["manager", "ledger"]
    disk = json.loads(risk._target().read_text())
    assert disk["realized_receipts"]["ledger:71"]["status"] == "UNKNOWN"
    assert disk["realized_pnl_today"] == 0
    assert risk.persistence_failure_reason()
    # A normal count refresh may persist the pending operation but cannot heal
    # the omitted financial receipt. Only its exact replay can clear the gate.
    monkeypatch.setattr(risk_manager, "atomic_write_json", original)
    risk.set_open_positions(0)
    assert risk.persistence_failure_reason()
    events.clear()
    assert run() == 1
    assert risk.realized_pnl_today == -10.5
    assert risk.realized_receipts["ledger:71"]["status"] == "CONFIRMED"
    assert not risk.persistence_failure_reason()
    assert events == ["manager", "ledger", "journal", "reconciliation", "tracker"]


@pytest.mark.parametrize("operation", ["adopt_receipt_alias", "register_unknown_pnl_at"])
def test_receipt_alias_or_unknown_marker_failure_never_commits_fill(monkeypatch, tmp_path, operation):
    critical, run, events, risk, _, _ = pipeline(monkeypatch, tmp_path)
    def fail(*a, **kw): raise RuntimeError("risk receipt unavailable")
    monkeypatch.setattr(risk, operation, fail)
    with pytest.raises(critical, match="RISK_RECEIPT_UNCONFIRMED"): run()
    assert events == ["manager", "ledger"]


def test_persisted_unknown_receipt_recovers_even_with_no_new_broker_fill(monkeypatch, tmp_path):
    from risk_manager import RiskState
    from live_trader import capture_new_fills
    critical, run, events, risk, _, receipt = pipeline(monkeypatch, tmp_path)
    original = risk.register_realized_pnl
    monkeypatch.setattr(risk, "register_realized_pnl", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("crash boundary")))
    with pytest.raises(critical): run()
    restarted = RiskState.load(risk._target())
    assert restarted.realized_receipts["ledger:71"]["status"] == "UNKNOWN"
    broker = NS(name="etoro", fills=lambda: [], account_fingerprint=lambda: "account-1",
                ist_paper=lambda: True, kontowaehrung=lambda: "USD")
    assert capture_new_fills(broker, NS(), {}, NS(), None, restarted, 10000, {}, notify_enabled=False) == 0
    assert restarted.realized_pnl_today == -10.5
    assert restarted.realized_receipts["ledger:71"]["status"] == "CONFIRMED"


@pytest.mark.parametrize("change", [{"broker_account_fingerprint": "other-account"},
    {"paper": False}, {"broker_position_id": "p-other"}, {"broker": "okx"}])
def test_risk_receipt_must_match_exact_broker_account_environment_position(monkeypatch, tmp_path, change):
    critical, run, events, risk, _, receipt = pipeline(monkeypatch, tmp_path)
    receipt.update(change)
    with pytest.raises(critical, match="RISK_RECEIPT_SCOPE_MISMATCH"): run()
    assert risk.realized_pnl_today == 0
    assert "journal" not in events and "tracker" not in events


def test_empty_poll_cannot_return_buy_permission_with_unresolved_risk_failure(monkeypatch):
    from live_trader import capture_new_fills, _CriticalFillAccountingError
    import risk_result_recovery
    monkeypatch.setattr(risk_result_recovery, "reconcile", lambda *a, **kw: [])
    risk = NS(persistence_failure_reason=lambda: "RISK_PERSISTENCE_UNCONFIRMED")
    with pytest.raises(_CriticalFillAccountingError, match="RISK_RECOVERY_INCOMPLETE"):
        capture_new_fills(NS(fills=lambda: []), NS(), {}, NS(), None, risk, 10000, {}, notify_enabled=False)
