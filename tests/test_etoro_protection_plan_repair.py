"""Offline behaviour of explicit BOT-plan adoption and asynchronous protection."""
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import json
import uuid

import pytest

from etoro_protection_repair import create_plan, apply_plan, merge_breakdown, digest


def fixture(now=None):
    from position_manager import PositionRecord
    now = now or datetime.now(timezone.utc)
    record = asdict(PositionRecord(con_id="etoro:1043:p", symbol="PEP", asset_type="stock", currency="USD",
        quantity=109, avg_cost=136.35, entry_time="2026-09-09T15:20:00Z", planned_stop=135.8946,
        planned_take=138.5207, planned_risk_amount=49.6386, planned_risk_pct=.001,
        source="BOT", management_mode="PENDING_CONFIRMATION", ownership_status="VERIFIED",
        reconciliation_status="CONFIRMED_OPEN", broker_position_ids=["p"], observed_position_ids=["p"],
        owned_position_ids=["p"], broker_instrument_id="1043", broker_account_fingerprint="test-account",
        broker_environment="DEMO", broker_snapshot_id="old", entry_order_ids=["o"],
        entry_reference_id="entry", protection_status="UNCONFIRMED"))
    row = {"positionId": "p", "instrumentId": 1043, "units": 109, "isBuy": True,
        "stopLossRate": 135.90, "takeProfitRate": 138.52, "isNoStopLoss": False,
        "isNoTakeProfit": False, "stopLossType": "fixed", "_stop_type_source": {
            "source": "ETORO_CID_BOUND_INSTRUMENT_BREAKDOWN", "timestamp": now.isoformat(),
            "response_sha256": "a"*64}}
    snap = {"source": "ETORO_PNL_AND_INSTRUMENT_BREAKDOWN", "complete": True,
        "snapshot_id": "snapshot-1", "snapshot_at": now.isoformat(), "account_fingerprint": "test-account",
        "environment": "DEMO", "rows": [row], "pending_orders_complete": True, "pending_orders": [],
        "pending_protection_updates": []}
    original = json.dumps({"positions": {"r": record}, "applied_fill_events": {"fill-original": {"units": 109}}}).encode()
    return record, snap, original


def planned(tmp_path):
    now = datetime.now(timezone.utc)
    rec, snap, original = fixture(now)
    target = tmp_path / "position_state.json"
    target.write_bytes(original)
    plan = create_plan(original, "r", snap, stop="135.90", take="138.52", reason="Explizite neue Schutzentscheidung", now=now)
    after = deepcopy(snap)
    after.update(snapshot_at=(now+timedelta(seconds=2)).isoformat(), snapshot_id="snapshot-2")
    after["rows"][0]["_stop_type_source"]["timestamp"] = after["snapshot_at"]
    return target, plan, after, now+timedelta(seconds=3), original


def apply(target, plan, snap, now):
    return apply_plan(target, plan, expected_decision_id=plan["decision_id"],
        fetch_snapshot=lambda record: snap, assert_writers_stopped=lambda: None, now=now)


def test_real_pep_numbers_adopt_without_patch_preserve_origin_and_restart(tmp_path):
    from position_manager import PositionManager
    target, plan, snap, now, original = planned(tmp_path)
    assert target.read_bytes() == original
    result = apply(target, plan, snap, now)
    assert result["status"] == "APPLIED" and result["broker_action"] == "NONE"
    manager = PositionManager(target)
    record = manager.records["r"]
    assert record.source == "BOT" and record.ownership_status == "VERIFIED"
    assert record.ownership_chain_complete
    assert record.planned_stop == 135.9 and record.planned_take == 138.52
    assert record.protection_plan_history[0]["original_plan"]["stop"] == 135.8946
    assert record.protection_plan_history[0]["sent"] is None
    assert record.protection_plan_history[0]["evidence"]["sent"] is None
    assert record.entry_order_ids == ["o"] and record.entry_reference_id == "entry"
    assert record.management_mode == "PENDING_CONFIRMATION"
    manager.save()
    assert len(PositionManager(target).records["r"].protection_plan_history) == 1
    result2 = apply(target, plan, snap, now)
    assert result2["status"] == "ALREADY_APPLIED"
    assert json.loads(target.read_text())["applied_fill_events"] == json.loads(original)["applied_fill_events"]
    assert target.with_name(target.name+".protection-plan-"+digest(original)[:20]+".bak").read_bytes() == original


@pytest.mark.parametrize("field,value", [("account_fingerprint", "other"), ("environment", "LIVE"),
    ("complete", False), ("pending_orders_complete", False), ("pending_orders", [{"orderId": "pending"}]),
    ("pending_protection_updates", [{"state": "SUBMITTING"}]),
    ("snapshot_at", "2020-01-01T00:00:00Z")])
def test_changed_scope_incomplete_or_stale_snapshot_cannot_apply(tmp_path, field, value):
    target, plan, snap, now, original = planned(tmp_path)
    snap[field] = value
    with pytest.raises(ValueError):
        apply(target, plan, snap, now)
    assert target.read_bytes() == original


@pytest.mark.parametrize("field,value", [("positionId", "other"), ("instrumentId", 999), ("units", 108),
    ("isBuy", False), ("isBuy", None), ("isNoStopLoss", True), ("isNoTakeProfit", None),
    ("isNoStopLoss", "false"), ("stopLossType", "trailing"), ("stopLossType", None),
    ("stopLossRate", 135.91), ("takeProfitRate", 138.521)])
def test_changed_position_or_protection_is_rejected(tmp_path, field, value):
    target, plan, snap, now, original = planned(tmp_path)
    snap["rows"][0][field] = value
    with pytest.raises(ValueError):
        apply(target, plan, snap, now)
    assert target.read_bytes() == original


def test_actual_local_state_change_and_review_tampering_are_rejected(tmp_path):
    target, plan, snap, now, original = planned(tmp_path)
    target.write_bytes(original+b" ")
    with pytest.raises(ValueError, match="STATE_CHANGED"):
        apply(target, plan, snap, now)
    target.write_bytes(original)
    plan["effective_plan"]["stop"] = "136"
    with pytest.raises(ValueError, match="PLAN_CHANGED"):
        apply(target, plan, snap, now)


def test_stale_plan_cached_same_snapshot_and_deepened_stop_are_rejected(tmp_path):
    target, plan, snap, now, original = planned(tmp_path)
    snap["snapshot_at"] = plan["snapshot_at"]
    with pytest.raises(ValueError, match="NEW_READBACK"):
        apply(target, plan, snap, now)
    with pytest.raises(ValueError, match="STALE"):
        apply(target, plan, snap, now+timedelta(minutes=16))
    with pytest.raises(ValueError, match="RISK_INCREASE"):
        create_plan(original, "r", snap, stop="135.80", take="138.52", reason="Keine Risikoausweitung", now=now)


def test_persistence_failure_retains_original_and_writer_failure_blocks_before_read(tmp_path, monkeypatch):
    import safe_persistence
    target, plan, snap, now, original = planned(tmp_path)
    def disk_full(*args, **kwargs):
        raise OSError("disk full")
    monkeypatch.setattr(safe_persistence, "atomic_write_json", disk_full)
    with pytest.raises(OSError):
        apply(target, plan, snap, now)
    assert target.read_bytes() == original
    with pytest.raises(OSError):
        apply_plan(target, plan, expected_decision_id=plan["decision_id"], fetch_snapshot=lambda _: pytest.fail("no read"),
            assert_writers_stopped=disk_full, now=now)


def test_breakdown_merge_matches_identity_and_preserves_flag_absence():
    now = datetime.now(timezone.utc)
    _, snap, _ = fixture(now)
    row = snap["rows"][0]
    row.pop("stopLossType")
    row.pop("isNoStopLoss")
    other = {**row, "direction": "long", "assetCurrency": "USD", "stopLossType": "fixed"}
    data = {"accountCurrency": "USD", "timestamp": now.isoformat(),
        "instruments": [{"instrumentId": 1043, "positions": [other], "orders": []}]}
    rows, orders = merge_breakdown([row], data, instrument_id=1043, now=now)
    assert rows[0]["stopLossType"] == "fixed" and "isNoStopLoss" not in rows[0]
    assert rows[0]["_stop_type_source"]["response_sha256"] == digest(data)
    other["units"] = 108
    with pytest.raises(ValueError, match="READBACK_CHANGED"):
        merge_breakdown([row], data, instrument_id=1043, now=now)


def test_ack_202_is_not_confirmation_and_timeout_cannot_resubmit(tmp_path, monkeypatch):
    import etoro_protection_journal as j
    monkeypatch.setenv("TRADINGBOT_TEST_STATE_DIR", str(tmp_path))
    req = str(uuid.uuid4())
    payload = {"stopLossRate": 135.9, "takeProfitRate": 138.52, "stopLossType": "fixed"}
    j.prepare("a", "DEMO", "p", req, payload, None,
        contract={"instrument_id": "1043", "quantity": 109, "strict_contract": True})
    with pytest.raises(ValueError, match="ACK_SCOPE"):
        j.accepted("a", "DEMO", "p", req, {"positionId": "wrong", "referenceId": req, "operationId": str(uuid.uuid4())})
    with pytest.raises(ValueError, match="RECONCILIATION"):
        j.prepare("a", "DEMO", "p", "new", payload, None)
    j.accepted("a", "DEMO", "p", req, {"positionId": "p", "referenceId": req, "operationId": str(uuid.uuid4())})
    assert list(json.loads(j.path().read_text())["records"].values())[0]["state"] == "ACCEPTED"
    _, snap, _ = fixture()
    row = snap["rows"][0]
    row["isNoStopLoss"] = True
    assert j.confirm("a", "DEMO", "p", 135.9, 138.52, "s", position_row=row,
        snapshot_at=(datetime.now(timezone.utc)+timedelta(seconds=1)).isoformat())["state"] == "ACCEPTED"
    row["isNoStopLoss"] = False
    assert j.confirm("a", "DEMO", "p", 135.9, 138.52, "s", position_row=row,
        snapshot_at=(datetime.now(timezone.utc)+timedelta(seconds=1)).isoformat())["state"] == "CONFIRMED"


def test_adapter_readback_is_cid_bound_and_reuses_same_snapshot_cache(monkeypatch):
    from broker.etoro import EtoroBroker
    now = datetime.now(timezone.utc)
    _, snap, _ = fixture(now)
    row = deepcopy(snap["rows"][0]); row.pop("stopLossType")
    data = {"accountCurrency": "USD", "timestamp": now.isoformat(), "instruments": [
        {"instrumentId": 1043, "orders": [], "positions": [{**row,
            "direction": "long", "assetCurrency": "USD", "stopLossType": "fixed"}]}]}
    broker = EtoroBroker(paper=True, api_key="test", user_key="test")
    broker._bind_account_identity({"demoCid": 123})
    calls = []
    def get(method, path, **kwargs):
        calls.append((method, path, kwargs))
        assert method == "GET" and kwargs["bound_cid_header"] is True
        return data
    monkeypatch.setattr(broker, "_request", get)
    pnl = {"_snapshot_id": "s", "_snapshot_at": now.isoformat()}
    assert broker._protection_breakdown_rows(pnl, [row], 1043)[0][0]["stopLossType"] == "fixed"
    broker._protection_breakdown_rows(pnl, [row], 1043)
    assert len(calls) == 1


def test_default_cli_reads_canonical_position_manager_state_without_broker(tmp_path, monkeypatch, capsys):
    import config
    import etoro_protection_repair as repair
    from broker.etoro import EtoroBroker
    target, plan, snap, now, original = planned(tmp_path)
    monkeypatch.setattr(config, "POSITION_STATE_FILE", str(target))
    monkeypatch.setattr(EtoroBroker, "_request", lambda *a, **k: pytest.fail("default must not read broker"))
    assert repair.canonical_state_path() == target
    assert repair.main([]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["source_sha256"] == digest(original) and report["status"] == "READ_ONLY"
    assert target.read_bytes() == original


def test_apply_cli_running_writer_aborts_before_broker(tmp_path, monkeypatch, capsys):
    import config
    import etoro_protection_repair as repair
    from broker.etoro import EtoroBroker
    from nexus_update import Host, UpdateError
    target, plan, snap, now, original = planned(tmp_path)
    path = tmp_path / "review.json"; path.write_text(json.dumps(plan))
    monkeypatch.setattr(config, "POSITION_STATE_FILE", str(target))
    def writer(*args):
        raise UpdateError("Writer laeuft")
    monkeypatch.setattr(Host, "other_writers", writer)
    monkeypatch.setattr(EtoroBroker, "_request", lambda *a, **k: pytest.fail("writer must block before broker"))
    assert repair.main(["--read-broker", "--apply", "--plan", str(path),
        "--decision-id", plan["decision_id"], "--workers-stopped"]) == 2
    assert "Writer laeuft" in capsys.readouterr().out
    assert target.read_bytes() == original


def test_pulsar_exit_and_later_stop_update_keep_effective_plan(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import decision_analytics
    from pulsar import positions, research
    monkeypatch.setattr(decision_analytics, "db_pfad", lambda: tmp_path / "decisions.sqlite")
    now = datetime.now(timezone.utc)
    rec = SimpleNamespace(avg_cost=136.35, entry_time=now.isoformat(), quantity=109,
        planned_stop=135.9, planned_take=138.52, protection_plan_history=[{"decision_id": "repair"}],
        owned_position_id_set=lambda: {"p"}, broker_instrument_id="1043")
    item = {"id": "repair-plan", "plan": {"price": 136.35, "R": .4554, "stop": 135.8946,
        "take": 138.5207, "quantity": 109, "partial_at_2R": False}}
    assert positions.decision(item, rec, 135.897, now=now)["action"] == "CLOSE"
    monkeypatch.setattr(positions, "reconcile_partial", lambda _: None)
    monkeypatch.setattr(positions, "decision", lambda *a, **k: {"action": "HOLD", "stop": 136})
    monkeypatch.setattr(research, "cached", lambda *a, **k: None)
    calls = []
    def protection(inst, qty, stop, take, **kwargs):
        calls.append((stop, take, kwargs))
        return {"protection_confirmed": True}
    broker = SimpleNamespace(latest_bid_ask=lambda _: {"bid": 137, "timestamp": now.isoformat()},
        _quote_age_seconds=lambda _: 0, historie=lambda *a, **k: None,
        reconcile_position_protection=protection)
    positions.manage(broker, SimpleNamespace(name="PEP"), rec, item, can_sell=True,
        close=lambda *a: pytest.fail("no sale"), register_exit=lambda *a: None)
    assert calls[0][0:2] == (136, 138.52)
    assert calls[0][2]["strict_contract"] is True
    assert item["plan"]["take"] == 138.5207 and rec.planned_stop == 136


@pytest.mark.parametrize("value", [{"positionId": "p"}, ["p"], None, False, ""])
def test_malformed_pending_order_collection_never_becomes_empty(value):
    from broker.etoro import EtoroBroker
    from broker.base import BrokerFehler
    with pytest.raises(BrokerFehler, match="PENDING_ORDER_VIEW_UNPROVEN"):
        EtoroBroker._validate_protection_order_view({"clientPortfolio": {"positions": [], "ordersForClose": value}})


def test_old_matching_stop_during_pending_change_cannot_reactivate_auto(tmp_path, monkeypatch):
    from broker.etoro import EtoroBroker
    import etoro_protection_journal as journal
    monkeypatch.setenv("TRADINGBOT_TEST_STATE_DIR", str(tmp_path))
    broker = EtoroBroker(paper=True, api_key="test", user_key="test")
    account = broker._bind_account_identity({"demoCid": 123})
    broker._resolve = lambda _: {"instrumentId": 1043, "symbol": "PEP"}
    _, snap, _ = fixture()
    row = snap["rows"][0]
    pnl = {"_snapshot_id": "after-submit", "_snapshot_at": datetime.now(timezone.utc).isoformat(),
        "clientPortfolio": {"positions": [row]}}
    broker._pnl = lambda **kwargs: pnl
    broker._request = lambda *a, **k: pytest.fail("no network or retry")
    req = str(uuid.uuid4())
    journal.prepare(account, "DEMO", "p", req, {"stopLossRate": 136, "takeProfitRate": 138.52}, None,
        contract={"instrument_id": "1043", "quantity": 109, "strict_contract": True})
    journal.accepted(account, "DEMO", "p", req, {"operationId": str(uuid.uuid4()), "positionId": "p", "referenceId": req})
    pnl["_snapshot_at"] = datetime.now(timezone.utc).isoformat()
    out = broker.reconcile_position_protection("PEP", 109, 135.90, 138.52, position_ids=["p"])
    assert not out["protection_confirmed"] and "RECONCILIATION_REQUIRED" in out["reason_code"]
    # Later request is observed: journal resolves, but older desired price still fails.
    row["stopLossRate"] = 136
    pnl["_snapshot_at"] = datetime.now(timezone.utc).isoformat()
    out = broker.reconcile_position_protection("PEP", 109, 135.90, 138.52, position_ids=["p"])
    assert not out["protection_confirmed"] and not journal.pending(account, "DEMO", ["p"])
    assert broker.reconcile_position_protection("PEP", 109, 136, 138.52,
        position_ids=["p"])["protection_confirmed"]
