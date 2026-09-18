"""N01/N02: economic day continuity and honest durable-write boundaries.

All account IDs, values and I/O faults are synthetic. No live broker is used.
"""
from __future__ import annotations

import errno
import json
import os
from datetime import timedelta
from types import SimpleNamespace

import pytest


def _state(tmp_path, monkeypatch):
    import config
    from risk_manager import RiskState
    monkeypatch.setattr(config, "MAX_UNREALIZED_DAILY_LOSS_PCT", .03)
    return RiskState.load(tmp_path / "risk_state_okx.json")


def test_original_symbol_set_counterexample_keeps_true_daily_loss(tmp_path, monkeypatch):
    s = _state(tmp_path, monkeypatch)
    for equity, symbols in [(1000, ""), (980, ""), (980, "SUI"), (960.4, "SUI")]:
        s.update_equity_guard(equity, basis_key=f"handelbares_kapital:v2:okx:EUR:{symbols}")
    assert s.day_start_equity == 1000
    assert s.equity_drawdown_pct == pytest.approx(-.0396)
    assert s.equity_guard_halted is True
    s.refresh()
    assert s.day_start_equity == 1000 and s.equity_guard_halted


@pytest.mark.parametrize("symbols", ["", "SUI", "ETH,SUI", "ETH"])
def test_position_turnover_does_not_clear_existing_latch(tmp_path, monkeypatch, symbols):
    s = _state(tmp_path, monkeypatch)
    s.update_equity_guard(1000, basis_key="handelbares_kapital:v2:okx:EUR:")
    s.update_equity_guard(960, basis_key="handelbares_kapital:v2:okx:EUR:")
    s.update_equity_guard(1005, basis_key=f"handelbares_kapital:v2:okx:EUR:{symbols}")
    assert s.day_start_equity == 1000 and s.equity_guard_halted
    assert not s.equity_basis_review_required


def test_currency_or_method_change_is_review_not_new_equity(tmp_path, monkeypatch):
    s = _state(tmp_path, monkeypatch)
    s.update_equity_guard(1000, basis_key="handelbares_kapital:v3:okx:EUR")
    assert s.update_equity_guard(500, basis_key="handelbares_kapital:v3:okx:USD")
    assert s.equity_basis_review_required and s.day_start_equity == 1000
    assert s.last_equity == 1000  # Values in different currencies are not compared.
    assert "RISK_EQUITY_BASIS_REVIEW" in s.equity_basis_review_reason


def test_scope_switch_is_local_and_survives_restart_and_midnight(tmp_path, monkeypatch):
    from risk_manager import RiskState, _handelstag_heute
    from risk_pots import RiskPot
    one = RiskPot("okx")
    two = RiskPot("etoro")
    one.setze_kontowert(1000, basis_key="cash:EUR", account_scope="okx:DEMO:A")
    two.setze_kontowert(2000, basis_key="cash:USD", account_scope="etoro:DEMO:B")
    one.setze_kontowert(100000, basis_key="cash:EUR", account_scope="okx:LIVE:A")
    assert not one.darf_kaufen()[0] and two.darf_kaufen()[0]
    restored = RiskState.load(one.state_datei)
    assert restored.equity_basis_review_required
    assert restored.risk_scope_key == "okx:DEMO:A" and restored.day_start_equity == 1000
    monkeypatch.setattr("risk_manager._handelstag_heute", lambda: _handelstag_heute() + timedelta(days=1))
    restored.reset_if_new_day()
    assert restored.equity_basis_review_required


def test_normal_local_day_rollover_resets_only_daily_equity_guard(tmp_path, monkeypatch):
    from risk_manager import _handelstag_heute
    s = _state(tmp_path, monkeypatch)
    s.update_equity_guard(1000, basis_key="cash:EUR", account_scope="okx:DEMO:A")
    s.update_equity_guard(960, basis_key="cash:EUR", account_scope="okx:DEMO:A")
    previous = _handelstag_heute()
    monkeypatch.setattr("risk_manager._handelstag_heute", lambda: previous + timedelta(days=1))
    s.update_equity_guard(960, basis_key="cash:EUR", account_scope="okx:DEMO:A")
    assert s.day_start_equity == 960 and not s.equity_guard_halted
    assert s.risk_scope_key == "okx:DEMO:A"


def test_legacy_migration_backs_up_exact_state_and_preserves_halts_and_receipts(tmp_path):
    from risk_manager import RiskState, _handelstag_heute
    target = tmp_path / "risk_state_okx.json"
    old = {"current_date": str(_handelstag_heute()), "day_start_equity": 1000,
           "last_equity": 960, "equity_guard_halted": True,
           "equity_drawdown_pct": -.04, "equity_basis_key": "handelbares_kapital:v2:okx:EUR:SUI",
           "realized_receipts": {"FILL-1": {"status": "UNKNOWN"}},
           "lifetime_unknown_pnl_trades": 1, "lifetime_realized_pnl": 123.45}
    original = json.dumps(old, indent=3)
    target.write_text(original, encoding="utf-8")
    s = RiskState.load(target)
    assert s.risk_schema_version == 2 and s.risk_scope_origin == "LEGACY_UNASSIGNED"
    assert s.day_start_equity == 1000 and s.equity_guard_halted
    assert s.realized_receipts == old["realized_receipts"]
    assert s.lifetime_realized_pnl == 123.45 and s.lifetime_unknown_pnl_trades == 1
    backups = list(tmp_path.glob("*.pre-v10-*.bak"))
    assert len(backups) == 1 and backups[0].read_text(encoding="utf-8") == original
    RiskState.load(target)
    assert list(tmp_path.glob("*.pre-v10-*.bak")) == backups


def test_migration_backup_failure_preserves_original_and_blocks(tmp_path, monkeypatch):
    from risk_manager import RiskState, kaufsperre_grund
    target = tmp_path / "risk_state_okx.json"
    original = '{"day_start_equity": 1000, "realized_pnl_today": -20}'
    target.write_text(original, encoding="utf-8")
    def fail(*args, **kwargs):
        raise OSError(errno.ENOSPC, "synthetic disk full")
    monkeypatch.setattr("risk_manager.atomic_write_text", fail)
    s = RiskState.load(target)
    assert target.read_text(encoding="utf-8") == original
    assert "RISK_STATE_UNREADABLE" in kaufsperre_grund(s)
    with pytest.raises(RuntimeError, match="dauerhaft"):
        s.update_equity_guard(1000, basis_key="cash:EUR")
    with pytest.raises(RuntimeError, match="dauerhaft"):
        s.save()
    assert target.read_text(encoding="utf-8") == original
    assert not list(tmp_path.glob("*.pre-v10-*.bak"))


def test_corrupt_risk_state_is_never_overwritten_with_zero_state(tmp_path):
    from risk_manager import RiskState, kaufsperre_grund
    target = tmp_path / "risk_state_okx.json"
    target.write_text('{"realized_receipts": damaged', encoding="utf-8")
    s = RiskState.load(target)
    assert s.trading_halted and s.persistence_failure_reason()
    assert "RISK_STATE_UNREADABLE" in kaufsperre_grund(s)
    assert target.read_text(encoding="utf-8") == '{"realized_receipts": damaged'


def test_manager_basis_is_stable_with_position_membership_and_scope(tmp_path):
    from risk_pots import RiskPotManager
    broker = SimpleNamespace(handelbares_kapital=lambda positions: 1000,
                             kontowaehrung=lambda: "EUR", account_fingerprint=lambda: "A", demo=True)
    manager = RiskPotManager(["okx"])
    hub = SimpleNamespace(broker=lambda name: broker)
    manager.aktualisiere_kontowerte(hub, lambda name: [])
    first = manager.topf("okx").state.equity_basis_key
    manager.aktualisiere_kontowerte(hub, lambda name: [("SUI", 5, 10)])
    state = manager.topf("okx").state
    assert first == state.equity_basis_key == "handelbares_kapital:v3:okx:EUR"
    assert json.loads(state.risk_scope_key) == ["okx", "DEMO", "A"]


@pytest.mark.parametrize("number", [errno.EIO, errno.ENOSPC, errno.EROFS, errno.EINVAL])
def test_file_fsync_failure_is_not_success_or_retry(tmp_path, monkeypatch, number):
    import safe_persistence as persistence
    path = tmp_path / "critical.json"
    path.write_text('{"old": true}')
    calls = []
    def fail(fd):
        calls.append(fd)
        raise OSError(number, "synthetic sync failure")
    monkeypatch.setattr(persistence.os, "fsync", fail)
    with pytest.raises(OSError) as raised:
        persistence.atomic_write_json(path, {"new": True}, durable=True)
    assert raised.value.errno == number and len(calls) == 1
    assert json.loads(path.read_text()) == {"old": True}
    assert not list(tmp_path.glob(".*.tmp"))


@pytest.mark.skipif(os.name != "posix", reason="Directory-fsync boundary is POSIX only")
def test_directory_fsync_failure_reports_ambiguous_visible_write_once(tmp_path, monkeypatch):
    import safe_persistence as persistence
    path = tmp_path / "critical.json"
    path.write_text('{"old": true}')
    real = persistence.os.fsync
    calls = []
    def second_fails(fd):
        calls.append(fd)
        if len(calls) == 2:
            raise OSError(errno.EIO, "synthetic directory failure")
        return real(fd)
    monkeypatch.setattr(persistence.os, "fsync", second_fails)
    with pytest.raises(OSError, match="directory failure"):
        persistence.atomic_write_json(path, {"new": True})
    assert len(calls) == 2
    assert json.loads(path.read_text()) == {"new": True}


def test_reconstructible_telemetry_explicitly_skips_sync(tmp_path, monkeypatch):
    import safe_persistence as persistence
    def forbidden(fd):
        raise AssertionError("durable=False must not call fsync")
    monkeypatch.setattr(persistence.os, "fsync", forbidden)
    persistence.atomic_write_json(tmp_path / "telemetry.json", {"status": "RUNNING"}, durable=False)
    assert json.loads((tmp_path / "telemetry.json").read_text())["status"] == "RUNNING"


def test_best_effort_reports_false_on_durable_failure(tmp_path, monkeypatch):
    import safe_persistence as persistence
    def fail(fd):
        raise OSError(errno.EIO, "synthetic EIO")
    monkeypatch.setattr(persistence.os, "fsync", fail)
    assert persistence.best_effort_json(tmp_path / "telemetry.json", {"value": 1}) is False


def test_transient_replace_is_retried_before_visibility(tmp_path, monkeypatch):
    import safe_persistence as persistence
    real = persistence.os.replace
    calls = []
    def busy_once(source, target):
        calls.append(str(source))
        if len(calls) == 1:
            raise OSError(errno.EBUSY, "synthetic transient lock")
        return real(source, target)
    monkeypatch.setattr(persistence.os, "replace", busy_once)
    persistence.atomic_write_text(tmp_path / "critical.txt", "new", base_delay=0)
    assert (tmp_path / "critical.txt").read_text() == "new"
    assert len(calls) == 2 and calls[0] != calls[1]


def test_risk_write_failure_cannot_be_cleared_by_status_refresh(tmp_path, monkeypatch):
    import safe_persistence as persistence
    from risk_pots import RiskPot
    pot = RiskPot("okx")
    pot.setze_kontowert(1000, basis_key="cash:EUR")
    real = persistence.os.fsync
    def fail(fd):
        raise OSError(errno.EIO, "synthetic EIO")
    monkeypatch.setattr(persistence.os, "fsync", fail)
    pot.setze_kontowert(960, basis_key="cash:EUR")
    pot.state.refresh()  # Old disk state is readable, but its write was not confirmed.
    assert not pot.darf_kaufen()[0]
    assert "RISK_PERSISTENCE_UNCONFIRMED" in pot.uebersicht()["grund"]
    monkeypatch.setattr(persistence.os, "fsync", real)
    pot.setze_kontowert(960, basis_key="cash:EUR")
    assert not pot._persistence_error and pot.state.equity_guard_halted
    assert not pot.darf_kaufen()[0]  # Durability recovered, economic daily stop remains.


def test_risk_write_rollback_preserves_receipt_for_later_replay(tmp_path, monkeypatch):
    from risk_manager import RiskState
    import safe_persistence as persistence
    s = _state(tmp_path, monkeypatch)
    real = persistence.os.fsync
    def fail(fd):
        raise OSError(errno.ENOSPC, "synthetic full disk")
    monkeypatch.setattr(persistence.os, "fsync", fail)
    with pytest.raises(RuntimeError):
        s.register_realized_pnl(-5, 1000, gross_pnl=-4, trade_id="FILL-1")
    assert "FILL-1" not in s.realized_receipts and s.realized_pnl_today == 0
    monkeypatch.setattr(persistence.os, "fsync", real)
    s.register_realized_pnl(-5, 1000, gross_pnl=-4, trade_id="FILL-1")
    s.register_realized_pnl(-5, 1000, gross_pnl=-4, trade_id="FILL-1")
    restored = RiskState.load(s._target())
    assert restored.realized_pnl_today == -5 and restored.trades_today == 1


def test_independent_loaded_risk_objects_do_not_overwrite_newer_equity_stop(tmp_path, monkeypatch):
    from risk_manager import RiskState
    first = _state(tmp_path, monkeypatch)
    second = RiskState.load(first._target())
    first.update_equity_guard(1000, basis_key="cash:EUR")
    first.update_equity_guard(960, basis_key="cash:EUR")
    second.update_equity_guard(970, basis_key="cash:EUR")
    assert second.equity_guard_halted and second.day_start_equity == 1000


def test_concurrent_loaders_recheck_schema_before_migration_backup(tmp_path, monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from pathlib import Path
    from risk_manager import RiskState, _handelstag_heute
    target = tmp_path / "risk_state_okx.json"
    original = json.dumps({"current_date": str(_handelstag_heute()),
                           "day_start_equity": 1000, "equity_guard_halted": True})
    target.write_text(original, encoding="utf-8")
    barrier = threading.Barrier(2)
    local = threading.local()
    real = Path.read_text
    def synchronised_read(path, *args, **kwargs):
        value = real(path, *args, **kwargs)
        if path == target and not getattr(local, "initial_read", False):
            local.initial_read = True
            barrier.wait(timeout=5)
        return value
    monkeypatch.setattr(Path, "read_text", synchronised_read)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(RiskState.load, target) for _ in range(2)]
        states = [future.result(timeout=8) for future in futures]
    assert all(s.risk_schema_version == 2 and s.equity_guard_halted for s in states)
    backups = list(tmp_path.glob("*.pre-v10-*.bak"))
    assert len(backups) == 1 and real(backups[0], encoding="utf-8") == original


def test_failed_realized_receipt_is_not_acknowledged_by_unrelated_success(tmp_path, monkeypatch):
    import risk_manager
    s = _state(tmp_path, monkeypatch)
    real = risk_manager.atomic_write_json
    def fail(*args, **kwargs):
        raise OSError(errno.EIO, "synthetic receipt-write failure")
    monkeypatch.setattr(risk_manager, "atomic_write_json", fail)
    with pytest.raises(RuntimeError):
        s.register_realized_pnl(-5, 1000, gross_pnl=-4, trade_id="ledger:FILL-A")
    monkeypatch.setattr(risk_manager, "atomic_write_json", real)
    s.set_open_positions(0)
    assert "RISK_PERSISTENCE_UNCONFIRMED" in risk_manager.kaufsperre_grund(s)
    assert s.realized_pnl_today == 0 and "ledger:FILL-A" not in s.realized_receipts
    s.update_equity_guard(1000, basis_key="cash:EUR")
    restored = risk_manager.RiskState.load(s._target())
    assert restored.persistence_failure_reason() and restored.pending_persistence_operations
    restored.register_realized_pnl(-5, 1000, gross_pnl=-4, trade_id="ledger:FILL-A")
    assert not restored.persistence_failure_reason()
    assert restored.realized_pnl_today == -5 and restored.trades_today == 1


def test_multiple_failed_receipts_require_each_exact_replay(tmp_path, monkeypatch):
    import risk_manager
    s = _state(tmp_path, monkeypatch)
    real = risk_manager.atomic_write_json
    def fail(*args, **kwargs):
        raise OSError(errno.EIO, "synthetic receipt-write failure")
    monkeypatch.setattr(risk_manager, "atomic_write_json", fail)
    for key in ("ledger:FILL-A", "ledger:FILL-B"):
        with pytest.raises(RuntimeError):
            s.register_realized_pnl(-5, 1000, gross_pnl=-4, trade_id=key)
    assert len(s.pending_persistence_operations) == 2
    monkeypatch.setattr(risk_manager, "atomic_write_json", real)
    s.register_realized_pnl(-5, 1000, gross_pnl=-4, trade_id="ledger:FILL-A")
    assert s.persistence_failure_reason() and len(s.pending_persistence_operations) == 1
    s.register_realized_pnl(-5, 1000, gross_pnl=-4, trade_id="ledger:FILL-B")
    assert not s.persistence_failure_reason() and s.realized_pnl_today == -10


def test_unknown_receipt_cannot_clear_failed_confirmation(tmp_path, monkeypatch):
    import risk_manager
    s = _state(tmp_path, monkeypatch)
    s.register_unknown_pnl_trade("ledger:FILL-A")
    real = risk_manager.atomic_write_json
    def fail(*args, **kwargs):
        raise OSError(errno.EIO, "synthetic receipt-write failure")
    monkeypatch.setattr(risk_manager, "atomic_write_json", fail)
    with pytest.raises(RuntimeError):
        s.register_realized_pnl(-5, 1000, gross_pnl=-4, trade_id="ledger:FILL-A")
    monkeypatch.setattr(risk_manager, "atomic_write_json", real)
    s.register_unknown_pnl_trade("ledger:FILL-A")
    assert s.persistence_failure_reason() and s.realized_receipts["ledger:FILL-A"]["status"] == "UNKNOWN"
    s.register_realized_pnl(-5, 1000, gross_pnl=-4, trade_id="ledger:FILL-A")
    assert not s.persistence_failure_reason() and s.realized_receipts["ledger:FILL-A"]["status"] == "CONFIRMED"


def test_etoro_stock_worker_uses_proven_scope_and_blocks_only_changed_domain(tmp_path, monkeypatch):
    import live_trader
    from risk_manager import RiskState, kaufsperre_grund
    from broker.etoro import EtoroBroker
    from risk_pots import RiskPot
    broker = EtoroBroker(paper=True)
    broker._identity_loaded = True
    broker._account_fingerprint = "synthetic-account-A"
    broker._account_cid = "synthetic-cid"
    monkeypatch.setattr(broker, "kontowaehrung", lambda: "USD")
    s = RiskState.load(tmp_path / "risk_state_etoro.json")
    okx = RiskPot("okx")
    okx.setze_kontowert(2000, basis_key="cash:EUR", account_scope="okx:DEMO:B")
    live_trader.update_account_equity_guard(s, broker, 1000)
    assert json.loads(s.risk_scope_key) == ["etoro", "DEMO", "synthetic-account-A"]
    assert s.equity_basis_key == "broker_kontowert:v2:etoro:USD"
    broker.paper = False
    live_trader.update_account_equity_guard(s, broker, 50000)
    assert "RISK_EQUITY_BASIS_REVIEW" in kaufsperre_grund(s)
    assert s.day_start_equity == 1000 and okx.darf_kaufen()[0]


def test_etoro_stock_worker_rejects_provisional_unproven_identity(tmp_path):
    import live_trader
    from risk_manager import RiskState
    broker = SimpleNamespace(name="etoro", _identity_loaded=False,
                             account_fingerprint=lambda: "provisional", kontowaehrung=lambda: "USD",
                             ist_paper=lambda: True)
    s = RiskState.load(tmp_path / "risk_state_etoro.json")
    with pytest.raises(RuntimeError, match="RISK_ACCOUNT_SCOPE_UNKNOWN"):
        live_trader.update_account_equity_guard(s, broker, 1000)
    assert s.day_start_equity == 0 and s.risk_scope_key == ""
