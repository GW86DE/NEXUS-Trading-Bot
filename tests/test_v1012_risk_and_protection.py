"""Money-path regression: new evidence never erases old loss or pending work."""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from types import SimpleNamespace
import uuid

import pytest
from test_etoro_risk_maintenance import evidence, legacy, NOW, DAY, SCOPE, BASIS
from test_etoro_protection_plan_repair import fixture


def unstarted(tmp_path, monkeypatch):
    import risk_manager as rm
    monkeypatch.setattr(rm, "_handelstag_heute", lambda: date.fromisoformat(DAY))
    raw = legacy()
    for key in ("day_start_equity", "last_equity", "realized_pnl_today", "trades_today",
            "estimated_costs_today", "gross_profit_today", "gross_loss_today", "net_profit_today",
            "net_loss_today", "unknown_pnl_trades_today", "equity_drawdown_pct"):
        raw[key] = 0
    raw["counted_trade_ids_today"] = []
    target = tmp_path/"risk_state_etoro.json"
    target.write_text(json.dumps(raw))
    risk = rm.RiskState.load(target)
    assert risk.update_equity_guard(99524.94, basis_key=BASIS, account_scope=SCOPE)
    return risk, target


def test_forward_period_fixes_known_basis_preserves_legacy_then_loss_guard(tmp_path, monkeypatch):
    import etoro_risk_period as period
    risk, target = unstarted(tmp_path, monkeypatch)
    before = target.read_bytes()
    old = json.loads(before)
    assert period.apply(risk, evidence(), now=NOW)
    saved = json.loads(target.read_text())
    assert json.loads(saved["risk_scope_key"]) == json.loads(SCOPE)
    assert saved["equity_basis_key"] == BASIS and not saved["equity_basis_review_required"]
    receipt = saved["basis_review_receipt"]
    assert receipt["method"] == period.METHOD and receipt["legacy_history_unassigned"]
    assert (target.parent/receipt["archive"]).read_bytes() == before
    assert receipt["archive_sha256"] == hashlib.sha256(before).hexdigest()
    assert json.loads((target.parent/receipt["evidence"]).read_text()) == evidence()
    for key in old:
        if key.startswith("lifetime_") or key in ("realized_receipts", "consecutive_losses", "cooldown_until", "trades_today"):
            assert saved[key] == old[key]
    assert saved["lifetime_unknown_pnl_trades"] == 6
    assert saved["realized_receipts"]["ledger:50"]["pnl"] is None
    assert saved["day_start_equity"] == saved["last_equity"] == 99524.94
    assert not period.apply(risk, evidence(), now=NOW)
    assert len(list(tmp_path.glob('*.legacy-*.bak'))) == 1
    assert not risk.basis_review()["blocks_entries"]
    assert risk.update_equity_guard(94000, basis_key=BASIS, account_scope=SCOPE)
    assert risk.equity_guard_halted and risk.day_start_equity == 99524.94


@pytest.mark.parametrize("key,value", [("day_start_equity", 100000), ("last_equity", 100000),
    ("realized_pnl_today", -1), ("unknown_pnl_trades_today", 1), ("trades_today", 1),
    ("estimated_costs_today", .01), ("counted_trade_ids_today", ["x"]),
    ("pending_persistence_operations", ["WRITE_FAILED"]), ("risk_scope_key", SCOPE),
    ("equity_basis_key", "another-basis")])
def test_existing_basis_activity_or_uncertainty_cannot_be_reset(tmp_path, monkeypatch, key, value):
    import etoro_risk_period as period
    risk, target = unstarted(tmp_path, monkeypatch)
    raw = json.loads(target.read_text()); raw[key] = value
    target.write_text(json.dumps(raw))
    assert not period.apply(risk, evidence(), now=NOW)
    assert json.loads(target.read_text())[key] == value
    assert not list(tmp_path.glob('*.legacy-*.bak'))


@pytest.mark.parametrize("kind", ["stale", "scope", "no_worker", "tamper"])
def test_forward_requires_current_matching_worker_evidence(tmp_path, monkeypatch, kind):
    import etoro_risk_period as period
    risk, target = unstarted(tmp_path, monkeypatch)
    data = evidence(); now = NOW
    if kind == "stale": now += timedelta(seconds=121)
    if kind == "scope": risk._basis_observation["observed_scope"] = '["etoro","LIVE","other"]'
    if kind == "no_worker": risk._basis_observation = {}
    if kind == "tamper": data["observations"]["account"]["data"]["equity"] += 1
    before = target.read_bytes()
    with pytest.raises(ValueError, match="UNPROVEN"):
        period.apply(risk, data, now=now)
    assert target.read_bytes() == before


def test_forward_does_not_clear_loss_halt_and_failed_archive_keeps_basis(tmp_path, monkeypatch):
    import etoro_risk_period as period
    risk, target = unstarted(tmp_path, monkeypatch)
    raw = json.loads(target.read_text()); raw["trading_halted"] = True
    target.write_text(json.dumps(raw))
    def failed(*a, **k): raise OSError("fixture disk full")
    monkeypatch.setattr(period, "atomic_write_text", failed)
    before = target.read_bytes()
    with pytest.raises(RuntimeError, match="dauerhaft"):
        period.apply(risk, evidence(), now=NOW)
    assert target.read_bytes() == before and risk.persistence_failure_reason()
    assert json.loads(before)["trading_halted"]


def test_open_operations_account_bound_both_journals_and_no_status7_guess(tmp_path):
    import broker_exit_journal as exits
    import etoro_protection_journal as protection
    from etoro_open_operations import snapshot
    assert snapshot("a", "DEMO")["pending"] == 0
    assert not exits._path().exists() and not protection.path().exists()
    pending, _ = exits.begin(broker="etoro", account_fingerprint="a", environment="DEMO",
        instrument_id="1043", position_id="p", quantity=109)
    exits.update(pending["intent_id"], "SUBMITTED", broker_order_id="380995258", detail={"statusId": 7, "positions": []})
    protection.prepare("a", "DEMO", "p", "request", {"stopLossRate": 135.9, "takeProfitRate": 138.52}, None)
    out = snapshot("a", "DEMO")
    assert out["pending"] == 2 and out["blocks_entries"]
    assert {r["kind"] for r in out["rows"]} == {"EXIT", "PROTECTION"}
    assert out["rows"][0]["broker_status_id"] == 7 and out["rows"][0]["state"] == "SUBMITTED"
    assert not snapshot("a", "LIVE")["blocks_entries"]
    assert not snapshot("b", "DEMO")["blocks_entries"]
    data = json.loads(protection.path().read_text())
    row = next(iter(data["records"].values())); row["account"] = "b"
    protection.path().write_text(json.dumps(data))
    with pytest.raises(ValueError, match="SCOPE_MISMATCH"): snapshot("a", "DEMO")


def test_dashboard_cannot_claim_ready_while_exit_or_protection_is_pending():
    from webui.diagnostics import operations_snapshot
    from test_v100_webui_backend import dashboard_fixture
    now, payload = dashboard_fixture()
    payload["etoro"]["etoro_open_operations"] = {"account_fingerprint": "test-account", "environment": "DEMO",
        "complete": True, "pending": 1, "blocks_entries": True, "rows": [{"kind": "EXIT"}],
        "detail": "Schließauftrag 380995258: Abschluss unbestätigt"}
    out = operations_snapshot(payload, now=now)["brokers"]
    assert out["etoro"]["buy"]["state"] == "BLOCKED"
    assert out["etoro"]["reconciliation"]["unresolved_count"] == 1
    assert "380995258" in out["etoro"]["reconciliation"]["detail"]
    assert out["okx"]["buy"]["state"] == "ALLOWED"


@pytest.mark.parametrize("strict", [True, False])
def test_changed_quantity_does_not_complete_old_patch_but_records_reason(strict):
    import etoro_protection_journal as j
    _, snap, _ = fixture()
    row = snap["rows"][0]; row["units"] = 100
    req = str(uuid.uuid4())
    j.prepare("a", "DEMO", "p", req, {"stopLossRate": 135.9, "takeProfitRate": 138.52}, None,
        contract={"instrument_id": "1043", "quantity": 109, "strict_contract": strict})
    j.accepted("a", "DEMO", "p", req, {"positionId": "p", "referenceId": req, "operationId": str(uuid.uuid4())})
    state = j.confirm("a", "DEMO", "p", 135.9, 138.52, "fresh", position_row=row,
        snapshot_at=datetime.now(timezone.utc).isoformat())
    assert state["state"] == "ACCEPTED"
    pending = j.pending("a", "DEMO", ["p"])[0]
    assert pending["last_reconciliation"]["reason_code"] == "ETORO_PROTECTION_QUANTITY_MISMATCH"
    assert pending["last_reconciliation"]["observed_quantity"] == 100
    with pytest.raises(ValueError, match="RECONCILIATION_REQUIRED"):
        j.prepare("a", "DEMO", "p", "new", {"stopLossRate": 136}, None)


@pytest.mark.parametrize("quote,label", [(135, "CLIENT-STOP"), (140, "CLIENT-TAKE"), (136.4, "TIME-STOP")])
def test_pending_own_position_has_risk_reducing_exits_only(quote, label, monkeypatch):
    from position_manager import PositionRecord
    from stock_exit_control import pending_exit_decision
    import config
    monkeypatch.setattr(config, "TIME_STOP_HOURS", 72)
    raw, _, _ = fixture()
    record = PositionRecord(**raw)
    record.entry_time = (datetime.now(timezone.utc)-timedelta(hours=73)).isoformat()
    broker = SimpleNamespace(paper=True, account_fingerprint=lambda: "test-account",
        latest_bid_ask=lambda _: {"bid": quote, "timestamp": "fresh"}, _quote_age_seconds=lambda _: 0)
    assert pending_exit_decision(record, broker, "PEP", 109)["label"] == label
    assert pending_exit_decision(record, broker, "PEP", 100) is None
    record.user_observe_locked = True
    assert pending_exit_decision(record, broker, "PEP", 109) is None
    record.user_observe_locked = False
    broker._quote_age_seconds = lambda _: 181
    assert pending_exit_decision(record, broker, "PEP", 109) is None


@pytest.mark.parametrize("failure", ["404", "currency", "missing_stop"])
def test_supplement_failure_is_position_scoped_and_no_patch(failure, monkeypatch):
    from broker.etoro import EtoroBroker
    from broker.base import BrokerFehler
    broker = EtoroBroker(paper=True, api_key="test", user_key="test")
    broker._bind_account_identity({"demoCid": 123})
    _, snap, _ = fixture()
    row = snap["rows"][0]
    pnl = {"_snapshot_id": "s", "_snapshot_at": datetime.now(timezone.utc).isoformat(),
        "clientPortfolio": {"positions": [row]}}
    monkeypatch.setattr(broker, "_resolve", lambda _: {"instrumentId": 1043, "symbol": "PEP"})
    monkeypatch.setattr(broker, "_pnl", lambda **kw: pnl)
    monkeypatch.setattr(broker, "_request", lambda *a, **k: pytest.fail("must not PATCH"))
    def fail(*a):
        if failure == "404": raise BrokerFehler("HTTP 404")
        raise ValueError("ETORO_REPAIR_BREAKDOWN_"+failure.upper())
    monkeypatch.setattr(broker, "_protection_breakdown_rows", fail)
    result = broker.reconcile_position_protection("PEP", 109, 135.9, 138.52,
        position_ids=["p"], strict_contract=True, update_requested=True)
    assert result["checked"] and not result["changed"] and not result["protection_confirmed"]
    assert result["reason_code"] == "ETORO_PROTECTION_SUPPLEMENT_UNPROVEN"
    monkeypatch.setattr(broker, "_protection_breakdown_rows", lambda *a: ([row], []))
    assert broker.reconcile_position_protection("PEP", 109, 135.9, 138.52,
        position_ids=["p"], strict_contract=True)["protection_confirmed"]
    def global_failure(**kw): raise BrokerFehler("GLOBAL_PNL_UNAVAILABLE")
    monkeypatch.setattr(broker, "_pnl", global_failure)
    with pytest.raises(BrokerFehler, match="GLOBAL"):
        broker.reconcile_position_protection("PEP", 109, 135.9, 138.52, position_ids=["p"], strict_contract=True)
