"""Release boundary: preserve paid usage/proofs and show the actual BUY gate."""
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest


def test_upgrade_preserves_risk_archives_and_paid_x_usage(tmp_path):
    from settings_migration import migrate_from
    source, target = tmp_path/'old', tmp_path/'new'
    source.mkdir(); target.mkdir()
    archive = 'risk_state_etoro.json.legacy-20260914-deadbeef.bak'
    proof = 'etoro_risk_period_20260914.json'
    (source/archive).write_text('{"lifetime_unknown_pnl_trades":6}\n')
    (source/proof).write_text('{"archive":"'+archive+'","source":"fixture"}\n')
    with sqlite3.connect(source/'market_intelligence.sqlite') as con:
        con.execute('CREATE TABLE spent(month TEXT, amount INTEGER)')
        con.execute("INSERT INTO spent VALUES('2026-09',14000000)")
    copied, _ = migrate_from(source, target)
    assert archive in copied and proof in copied
    assert (target/archive).read_bytes() == (source/archive).read_bytes()
    assert (target/proof).read_bytes() == (source/proof).read_bytes()
    with sqlite3.connect(target/'market_intelligence.sqlite') as con:
        assert con.execute('SELECT amount FROM spent').fetchone()[0] == 14000000


def test_risk_archive_symlink_is_refused_before_copy(tmp_path):
    from settings_migration import migrate_from
    source, target = tmp_path/'old', tmp_path/'new'
    source.mkdir(); target.mkdir()
    elsewhere = tmp_path/'outside.json'; elsewhere.write_text('{}')
    (source/'etoro_risk_period_fixture.json').symlink_to(elsewhere)
    with pytest.raises(ValueError, match='regulaere Datei'):
        migrate_from(source, target)
    assert not list(target.iterdir())


def test_ui_gate_uses_actual_current_risk_decision(monkeypatch):
    import live_trader
    import etoro_risk_period
    from risk_manager import RiskState
    risk = RiskState()
    risk.equity_basis_review_required = False
    risk.realized_receipts = {'ledger:54': {'status': 'UNKNOWN'}}
    risk.lifetime_unknown_pnl_trades = 7
    risk.unknown_pnl_trades_today = 1
    monkeypatch.setattr(risk, 'reset_if_new_day', lambda: None)
    monkeypatch.setattr(etoro_risk_period, 'result_scope_summary', lambda _: {
        'active_unknown':1, 'historical_unknown':6, 'lifetime_unknown':7,
        'review_required':False, 'method':'ARCHIVED_PERIOD'})
    captured = {}
    runtime = SimpleNamespace(update=lambda **kw: captured.update(kw))
    gate = live_trader.report_risk_buy_gate(runtime, risk)
    assert gate['blocked'] and gate['pending_results'] == 1
    assert gate['historical_pending_results'] == 6
    assert gate['reason'] == live_trader.kaufsperre_grund(risk)
    assert captured['risk_manager_buy_gate'] == gate


def test_broken_risk_assessment_never_announces_buy_ready(monkeypatch):
    import live_trader
    def fail(_):
        raise OSError('fixture')
    monkeypatch.setattr(live_trader, 'kaufsperre_grund', fail)
    gate = live_trader.report_risk_buy_gate(SimpleNamespace(update=lambda **kw: None), object())
    assert gate['blocked'] and gate['pending_results'] is None
    assert 'OSError' in gate['reason']
