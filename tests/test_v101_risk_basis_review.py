"""Real 13 Sep legacy shape, anonymized identity; no broker/network calls."""
from copy import deepcopy
from datetime import date, datetime, timezone
from pathlib import Path
import hashlib
import json
import pytest
import zipfile

DAY = "2026-09-13"
SCOPE = '["etoro","DEMO","fixture-account"]'
BASIS = "broker_kontowert:v2:etoro:USD"


def legacy():
    # Complete representative fields from the retained 13 Sep diagnostic;
    # no RiskState defaults fill unknown economics during the test.
    return {"risk_schema_version": 2, "current_date": DAY,
        "basis_review_receipt": {}, "consecutive_losses": 0, "cooldown_until": None,
        "counted_cost_ids_today": [], "counted_trade_ids_today": [], "crypto_exposure": 0.0,
        "equity_basis_changed_at": "2026-08-30T06:57:40.867158+00:00",
        "equity_drawdown_pct": 0.0, "equity_guard_halted": False,
        "estimated_costs_today": 0.0, "gross_loss_today": 0.0, "gross_profit_today": 0.0,
        "lifetime_estimated_costs": 0.0, "lifetime_gross_loss": 677.5086847694167,
        "lifetime_gross_profit": 176.3407009270327, "lifetime_net_loss": 755.204677205457,
        "lifetime_net_profit": 175.59828010053272, "net_loss_today": 0.0, "net_profit_today": 0.0,
        "trades_today": 0, "trading_halted": False, "unknown_pnl_ids_today": [], "unknown_pnl_trades_today": 0,
        "equity_basis_key": "handelbares_kapital:v3:okx:USDC",
        "risk_scope_key": "", "risk_scope_origin": "LEGACY_UNASSIGNED",
        "equity_basis_review_required": True, "equity_basis_review_reason": "RISK_EQUITY_BASIS_REVIEW",
        "day_start_equity": 99524.94, "last_equity": 99524.94,
        "realized_pnl_today": 0, "lifetime_realized_pnl": -579.6063971049242,
        "lifetime_unknown_pnl_trades": 6, "open_positions": 1,
        "realized_receipts": {"ledger:50": {"status": "UNKNOWN", "pnl": None}},
        "pending_persistence_operations": []}


def checkpoint(raw):
    return {**deepcopy(raw), "equity_basis_key": BASIS, "risk_scope_key": SCOPE,
        "risk_scope_origin": "BROKER_OBSERVED", "equity_basis_review_required": False,
        "equity_basis_review_reason": ""}


@pytest.fixture(autouse=True)
def _maintenance_day(monkeypatch):
    import risk_manager as rm
    import etoro_risk_maintenance as maintenance
    monkeypatch.setattr(rm, "_handelstag_heute", lambda: date.fromisoformat(DAY))
    monkeypatch.setattr(maintenance, "_now", lambda: datetime(2026, 9, 13, 18, tzinfo=timezone.utc))


def _apply_checkpoint(target, proof, **args):
    """Independent retained source and fresh read fixture for the hardened API."""
    import etoro_risk_maintenance as maintenance
    from risk_basis_review import apply_checkpoint
    stamp = "2026-09-13T18:00:00+00:00"
    data = {"identity_before": {"scope": json.loads(SCOPE)},
        "identity_after": {"scope": json.loads(SCOPE)},
        "account": {"currency": "USD", "equity": 99524.94, "available_cash": 84700},
        "positions": {"complete": True, "rows": []},
        "closed_trade_history": {"complete": True, "rows": [], "min_date": "2026-09-12"}}
    fresh = {"kind": maintenance.KIND, "schema_version": 1, "started_at": stamp,
        "completed_at": stamp, "day": DAY, "local_timezone": "Europe/Berlin",
        "day_start_utc": "2026-09-12T22:00:00+00:00", "environment": "DEMO",
        "observed_basis": BASIS, "observed_scope": json.loads(SCOPE),
        "observations": {name: {"status": "OK", "requested_at": stamp, "received_at": stamp,
            "data": value, "sha256": maintenance._digest(value)} for name, value in data.items()}}
    archive = proof.with_suffix(".zip")
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("risk_state_etoro.json", proof.read_bytes())
    return apply_checkpoint(target, proof, **args, fresh_evidence=fresh,
                            archive_path=archive, archive_member="risk_state_etoro.json")


def test_real_legacy_is_not_automatically_rebased_on_equal_current_balance():
    from risk_basis_review import status
    raw = legacy(); before = deepcopy(raw)
    view = status(raw, observed_basis=BASIS, observed_scope=SCOPE, observed_equity=99524.94)
    assert view["blocks_entries"] and not view["automatic_rebase_allowed"]
    assert "RISK_HISTORICAL_ACCOUNT_UNPROVEN" in view["reason_codes"]
    assert raw == before


@pytest.mark.parametrize("change", [{"risk_scope_key": '["etoro","LIVE","fixture-account"]'},
    {"risk_scope_key": '["etoro","DEMO","other"]'}, {"current_date": "2026-09-12"},
    {"day_start_equity": 1}, {"lifetime_realized_pnl": 0}, {"realized_receipts": {}},
    {"equity_basis_review_required": True}, {"pending_persistence_operations": ["pending"]}])
def test_checkpoint_must_match_every_economic_value_and_proven_scope(change):
    from risk_basis_review import plan
    raw=legacy(); proof=checkpoint(raw); proof.update(change)
    view=plan(raw, proof, expected_basis=BASIS, expected_scope=SCOPE, today=DAY)
    assert view["status"] == "REVIEW_REQUIRED" and not view["changes"]


def test_valid_checkpoint_repairs_only_metadata_with_backup_and_idempotence(tmp_path):
    from risk_basis_review import apply_checkpoint, METADATA
    raw=legacy(); original=json.dumps(raw).encode(); target=tmp_path/'risk_state_etoro.json'
    target.write_bytes(original); proof=tmp_path/'independent.json'; proof.write_text(json.dumps(checkpoint(raw)))
    args=dict(expected_sha256=hashlib.sha256(original).hexdigest(), expected_basis=BASIS,
              expected_scope=SCOPE, today=DAY)
    result=_apply_checkpoint(target, proof, **args)
    assert result['status']=='APPLIED'
    from pathlib import Path
    assert Path(result['backup']).read_bytes()==original
    updated=json.loads(target.read_text())
    assert {k:v for k,v in raw.items() if k not in METADATA} == {k:v for k,v in updated.items() if k not in METADATA}
    assert updated['realized_receipts']['ledger:50']['pnl'] is None
    assert _apply_checkpoint(target,proof,**args)['status']=='ALREADY_APPLIED'
    assert len(list(tmp_path.glob('*.bak')))==1


@pytest.mark.parametrize('failure', ['backup','replace','changed'])
def test_failure_never_partly_migrates_economic_state(tmp_path, monkeypatch, failure):
    import risk_basis_review as rb
    import safe_persistence as persistence
    raw=legacy(); target=tmp_path/'risk_state_etoro.json'; original=json.dumps(raw).encode(); target.write_bytes(original)
    proof=tmp_path/'proof.json'; proof.write_text(json.dumps(checkpoint(raw)))
    args=dict(expected_sha256=hashlib.sha256(original).hexdigest(), expected_basis=BASIS, expected_scope=SCOPE,today=DAY)
    if failure=='changed':target.write_text(json.dumps({**raw,'open_positions':2}))
    else:
        def fail(*a,**kw):raise OSError('injected fsync failure')
        monkeypatch.setattr(persistence,'atomic_write_text' if failure=='backup' else 'atomic_write_json',fail)
    before=target.read_bytes()
    with pytest.raises((ValueError,OSError)): _apply_checkpoint(target,proof,**args)
    assert target.read_bytes()==before and json.loads(before)['equity_basis_review_required']


def test_runtime_guard_publishes_both_account_and_basis_review_without_reset(monkeypatch,tmp_path):
    import risk_manager as rm
    monkeypatch.setattr(rm,'_handelstag_heute',lambda:date.fromisoformat(DAY))
    target=tmp_path/'etoro.json'; target.write_text(json.dumps(legacy()))
    risk=rm.RiskState.load(target)
    assert risk.update_equity_guard(99524.94,basis_key=BASIS,account_scope=SCOPE)
    assert risk.basis_review()['blocks_entries']
    assert risk.lifetime_unknown_pnl_trades==6 and risk.realized_receipts['ledger:50']['pnl'] is None
    assert risk.equity_basis_key=='handelbares_kapital:v3:okx:USDC'


def test_review_receipt_survives_next_risk_transaction(monkeypatch,tmp_path):
    import risk_manager as rm
    monkeypatch.setattr(rm,'_handelstag_heute',lambda:date.fromisoformat(DAY))
    target=tmp_path/'risk.json'; raw=checkpoint(legacy());raw['basis_review_receipt']={'method':'IDENTICAL_ECONOMIC_CHECKPOINT_V1'}
    target.write_text(json.dumps(raw)); risk=rm.RiskState.load(target)
    risk.set_open_positions(2)
    assert json.loads(target.read_text())['basis_review_receipt']==raw['basis_review_receipt']
