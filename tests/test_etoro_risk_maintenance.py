"""Risk maintenance consumes GET evidence; no real credentials/network/orders."""
from copy import deepcopy
from datetime import datetime, timezone, timedelta, date
import hashlib
import json
from pathlib import Path
import zipfile

import pytest

NOW = datetime(2026, 9, 13, 18, 0, tzinfo=timezone.utc)
DAY = "2026-09-13"
SCOPE = '["etoro","DEMO","fixture-account"]'
BASIS = "broker_kontowert:v2:etoro:USD"


class FakeBroker:
    paper = True

    def __init__(self):
        self.calls = []
        self.identity = "fixture-account"
        self.currency = "USD"
        self.history_complete = True
        self.rows = [{"positionId": 3597440106, "instrumentId": 1043,
                      "units": 109, "isBuy": True, "stopLossRate": 135.9}]

    def _request(self, method, path):
        assert (method, path) == ("GET", "/api/v1/me")
        self.calls.append((method, path))
        return {"demoCid": 123, "private_name": "must-not-be-exported"}

    def _bind_account_identity(self, profile):
        assert profile["demoCid"] == 123

    def account_fingerprint(self):
        return self.identity

    def _get_aggregate(self, force):
        assert force
        self.calls.append(("GET", "aggregate"))
        return {"accountCurrency": self.currency,
                "accountTotals": {"accountTotalValue": 99524.94, "accountAvailableCash": 84700}}

    def _pnl(self, force):
        assert force
        self.calls.append(("GET", "pnl"))
        return {"clientPortfolio": {"positions": deepcopy(self.rows), "mirrors": []}}

    def _validate_pnl_schema(self, data):
        assert isinstance(data["clientPortfolio"]["positions"], list)

    def _all_positions(self, data):
        return data["clientPortfolio"]["positions"]

    def trade_history_snapshot(self, min_date, *, force, max_pages):
        assert min_date == "2026-09-12" and force and max_pages == 12
        self.calls.append(("GET", "history"))
        return {"rows": [], "complete": self.history_complete,
                "min_date": min_date, "truncated": not self.history_complete}


def evidence():
    from etoro_risk_maintenance import collect
    return collect(FakeBroker(), clock=lambda: NOW)


def legacy():
    # Representative complete 13 Sep schema, with a deliberately nonzero
    # current-day loss to prove that maintenance preserves today's loss.
    return {"risk_schema_version": 2, "current_date": DAY,
        "basis_review_receipt": {}, "consecutive_losses": 1, "cooldown_until": None,
        "counted_cost_ids_today": [], "counted_trade_ids_today": ["test-loss"], "crypto_exposure": 0.0,
        "equity_basis_changed_at": "2026-08-30T06:57:40.867158+00:00",
        "equity_drawdown_pct": 0.0, "equity_guard_halted": False,
        "estimated_costs_today": 0.0, "gross_loss_today": 12.0, "gross_profit_today": 0.0,
        "lifetime_estimated_costs": 0.0, "lifetime_gross_loss": 677.5086847694167,
        "lifetime_gross_profit": 176.3407009270327, "lifetime_net_loss": 755.204677205457,
        "lifetime_net_profit": 175.59828010053272, "net_loss_today": 12.0, "net_profit_today": 0.0,
        "trades_today": 1, "trading_halted": False, "unknown_pnl_ids_today": [], "unknown_pnl_trades_today": 0,
        "equity_basis_key": "handelbares_kapital:v3:okx:USDC", "risk_scope_key": "",
        "risk_scope_origin": "LEGACY_UNASSIGNED", "equity_basis_review_required": True,
        "equity_basis_review_reason": "RISK_EQUITY_BASIS_REVIEW",
        "day_start_equity": 99524.94, "last_equity": 99524.94,
        "realized_pnl_today": -12, "lifetime_realized_pnl": -579.6063971049242,
        "lifetime_unknown_pnl_trades": 6, "open_positions": 1,
        "realized_receipts": {"ledger:50": {"status": "UNKNOWN", "pnl": None}},
        "pending_persistence_operations": []}


def test_fresh_collection_read_only_and_never_fabricates_new_period():
    from etoro_risk_maintenance import collect, plan_new_period
    broker = FakeBroker()
    result = collect(broker, clock=lambda: NOW)
    assert len(broker.calls) == 5 and all(x[0] == "GET" for x in broker.calls)
    assert result["assessment"]["status"] == "CURRENT_ACCOUNT_CONFIRMED"
    assert result["day_start_utc"] == "2026-09-12T22:00:00+00:00"
    assert "must-not-be-exported" not in json.dumps(result)
    raw = legacy(); before = deepcopy(raw)
    plan = plan_new_period(raw, result, now=NOW)
    assert not plan["changes"] and not plan["new_period_allowed"]
    assert "RISK_DAY_START_EQUITY_UNPROVEN" in plan["reason_codes"]
    assert "RISK_CASHFLOW_COMPLETENESS_UNPROVEN" in plan["reason_codes"]
    assert "RISK_COST_COMPLETENESS_UNPROVEN" in plan["reason_codes"]
    assert raw == before and raw["realized_pnl_today"] == -12


@pytest.mark.parametrize("mutation", ["currency", "history", "nan", "duplicate", "side", "identity"])
def test_incomplete_or_ambiguous_api_evidence_blocks(mutation):
    from etoro_risk_maintenance import collect
    broker = FakeBroker()
    if mutation == "currency": broker.currency = None
    if mutation == "history": broker.history_complete = False
    if mutation == "nan": broker.rows[0]["units"] = float("nan")
    if mutation == "duplicate": broker.rows *= 2
    if mutation == "side": broker.rows[0].pop("isBuy")
    if mutation == "identity":
        original = broker.trade_history_snapshot
        def changed(*args, **kwargs):
            result = original(*args, **kwargs); broker.identity = "other"; return result
        broker.trade_history_snapshot = changed
    result = collect(broker, clock=lambda: NOW)
    assert result["assessment"]["status"] == "REVIEW_REQUIRED"
    assert not result["new_period_allowed"]


@pytest.mark.parametrize("change", ["stale", "future", "scope", "tampered", "missing"])
def test_evidence_freshness_scope_and_integrity(change):
    from etoro_risk_maintenance import review_evidence
    data = evidence(); current = NOW
    if change == "stale": current += timedelta(seconds=121)
    if change == "future": current -= timedelta(seconds=1)
    if change == "scope": data["observed_scope"][1] = "LIVE"
    if change == "tampered": data["observations"]["account"]["data"]["equity"] = 999999
    if change == "missing": data["observations"].pop("positions")
    assert review_evidence(data, now=current)["status"] == "REVIEW_REQUIRED"


def test_collector_transport_rejects_every_mutation_and_other_environment(monkeypatch):
    from etoro_risk_maintenance import readonly_broker
    from broker.etoro import EtoroBroker
    called = []
    monkeypatch.setattr(EtoroBroker, "_request", lambda self, method, path, **kw: called.append((method, path)))
    broker = readonly_broker(environment="DEMO")
    for method, path in [("PATCH", "/api/v1/trading/execution/demo/positions/1"),
                         ("POST", "/api/v1/trading/info/demo/aggregate-portfolio"),
                         ("GET", "/api/v1/trading/info/real/pnl")]:
        with pytest.raises(ValueError, match="READ_ONLY"): broker._request(method, path)
    assert called == []
    broker._request("GET", "/api/v1/me")
    assert called == [("GET", "/api/v1/me")]


def _archive(tmp_path, raw):
    checkpoint = {**deepcopy(raw), "risk_scope_key": SCOPE, "equity_basis_key": BASIS,
        "risk_scope_origin": "BROKER_OBSERVED", "equity_basis_review_required": False,
        "equity_basis_review_reason": ""}
    path = tmp_path / "retained.zip"
    member = "ende/zustand/risk_state_etoro.json"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(member, json.dumps({"status": "OK", "truncated": False,
            "changed_during_read": False, "data": checkpoint}))
    return path, member


def test_cli_archive_apply_preserves_losses_receipts_and_okx(tmp_path, monkeypatch, capsys):
    import etoro_risk_maintenance as maintenance
    import risk_basis_review as rb
    import risk_manager as rm
    monkeypatch.setattr(maintenance, "_now", lambda: NOW)
    monkeypatch.setattr(rm, "_handelstag_heute", lambda: date.fromisoformat(DAY))
    target = tmp_path / "risk_state_etoro.json"
    original = json.dumps(legacy()).encode(); target.write_bytes(original)
    okx = tmp_path / "risk_state_okx.json"; okx.write_text("untouched")
    proof, member = _archive(tmp_path, legacy())
    fresh = tmp_path / "fresh.json"; fresh.write_text(json.dumps({"evidence": evidence()}))
    args = [str(target), "--checkpoint-archive", str(proof), "--checkpoint-member", member,
        "--fresh-evidence", str(fresh), "--day", DAY, "--apply",
        "--expected-sha256", hashlib.sha256(original).hexdigest()]
    rb.main(args)
    saved = json.loads(target.read_text())
    assert saved["realized_pnl_today"] == -12 and saved["lifetime_unknown_pnl_trades"] == 6
    assert saved["realized_receipts"]["ledger:50"]["pnl"] is None
    assert not saved["equity_basis_review_required"]
    assert saved["basis_review_receipt"]["source_provenance"]["archive_sha256"] == hashlib.sha256(proof.read_bytes()).hexdigest()
    rb.main(args)
    assert "ALREADY_APPLIED" in capsys.readouterr().out
    assert okx.read_text() == "untouched"


def test_cli_rejects_relabelled_loose_checkpoint(tmp_path):
    import risk_basis_review as rb
    target = tmp_path / "risk_state_etoro.json"; target.write_text(json.dumps(legacy()))
    _, member = _archive(tmp_path, legacy())
    checkpoint, _ = rb.archived_checkpoint(tmp_path / "retained.zip", member)
    loose = tmp_path / "relabelled.json"; loose.write_text(json.dumps(checkpoint))
    before = target.read_bytes()
    with pytest.raises(SystemExit):
        rb.main([str(target), "--checkpoint", str(loose), "--basis", BASIS, "--scope", SCOPE,
                 "--day", DAY, "--apply", "--expected-sha256", hashlib.sha256(before).hexdigest()])
    assert target.read_bytes() == before


def test_archive_rejects_unassigned_original_and_incomplete_snapshot(tmp_path):
    from risk_basis_review import archived_checkpoint
    path = tmp_path / "original.zip"; member = "risk_state_etoro.json"
    with zipfile.ZipFile(path, "w") as archive: archive.writestr(member, json.dumps(legacy()))
    with pytest.raises(ValueError, match="HISTORICAL_SCOPE_UNPROVEN"): archived_checkpoint(path, member)
    with zipfile.ZipFile(path, "w") as archive: archive.writestr(member, json.dumps({"data": legacy(), "status": "OK", "truncated": True}))
    with pytest.raises(ValueError, match="INCOMPLETE"): archived_checkpoint(path, member)


def test_next_day_still_requires_review_and_preserves_unknown_history(monkeypatch, tmp_path):
    import risk_manager as rm
    monkeypatch.setattr(rm, "_handelstag_heute", lambda: date(2026, 9, 14))
    target = tmp_path / "risk_state_etoro.json"; target.write_text(json.dumps(legacy()))
    state = rm.RiskState.load(target)
    assert state.update_equity_guard(99524.94, basis_key=BASIS, account_scope=SCOPE)
    assert state.basis_review()["blocks_entries"]
    assert state.lifetime_unknown_pnl_trades == 6 and state.realized_receipts["ledger:50"]["pnl"] is None


def test_collector_export_roundtrip_rechecks_freshness_and_never_overwrites(tmp_path, monkeypatch, capsys):
    import etoro_risk_maintenance as maintenance
    monkeypatch.setattr(maintenance, "_now", lambda: NOW)
    monkeypatch.setattr(maintenance, "readonly_broker", lambda **kwargs: FakeBroker())
    target = tmp_path / "risk_state_etoro.json"; target.write_text(json.dumps(legacy()))
    original = target.read_bytes()
    output = tmp_path / "export.json"
    assert maintenance.main(["--collect", "--state", str(target), "--output", str(output)]) == 0
    assert maintenance.main(["--evidence", str(output), "--state", str(target)]) == 0
    assert target.read_bytes() == original
    saved = output.read_bytes()
    with pytest.raises(FileExistsError):
        maintenance.main(["--evidence", str(output), "--state", str(target), "--output", str(output)])
    assert output.read_bytes() == saved
    monkeypatch.setattr(maintenance, "_now", lambda: NOW + timedelta(seconds=121))
    assert maintenance.main(["--evidence", str(output), "--state", str(target)]) == 2
    assert "RISK_EVIDENCE_STALE_OR_FUTURE" in capsys.readouterr().out


def test_state_change_during_collection_is_a_blocking_evidence_reason(tmp_path, monkeypatch):
    import etoro_risk_maintenance as maintenance
    monkeypatch.setattr(maintenance, "_now", lambda: NOW)
    target = tmp_path / "risk_state_etoro.json"; target.write_text(json.dumps(legacy()))
    broker = FakeBroker()
    original = broker.trade_history_snapshot
    def changed(*args, **kwargs):
        target.write_text(json.dumps({**legacy(), "open_positions": 2}))
        return original(*args, **kwargs)
    broker.trade_history_snapshot = changed
    monkeypatch.setattr(maintenance, "readonly_broker", lambda **kwargs: broker)
    output = tmp_path / "export.json"
    assert maintenance.main(["--collect", "--state", str(target), "--output", str(output)]) == 2
    result = json.loads(output.read_text())
    assert result["state_changed_during_collection"]
    assert "RISK_STATE_CHANGED_DURING_COLLECTION" in result["current_account_review"]["reason_codes"]


def test_raw_http_exception_text_is_not_exported():
    from etoro_risk_maintenance import collect
    broker = FakeBroker()
    def failed(*args, **kwargs):
        raise RuntimeError("secret-user-key-and-http-body")
    broker._get_aggregate = failed
    result = collect(broker, clock=lambda: NOW)
    assert "secret-user-key" not in json.dumps(result)
    assert result["observations"]["account"]["error_code"] == "RuntimeError"


def test_interrupted_durable_apply_is_reconfirmed_on_retry(tmp_path, monkeypatch):
    import risk_basis_review as rb
    import safe_persistence as persistence
    import risk_manager as rm
    import etoro_risk_maintenance as maintenance
    monkeypatch.setattr(rm, "_handelstag_heute", lambda: date.fromisoformat(DAY))
    monkeypatch.setattr(maintenance, "_now", lambda: NOW)
    raw = legacy(); target = tmp_path / "risk_state_etoro.json"
    original = json.dumps(raw).encode(); target.write_bytes(original)
    archive, member = _archive(tmp_path, raw)
    checkpoint, _ = rb.archived_checkpoint(archive, member)
    proof = tmp_path / "proof.json"; proof.write_text(json.dumps(checkpoint))
    args = dict(expected_sha256=hashlib.sha256(original).hexdigest(), expected_basis=BASIS,
                expected_scope=SCOPE, today=DAY, fresh_evidence=evidence(),
                archive_path=archive, archive_member=member)
    actual = persistence.atomic_write_json
    def replaced_but_unconfirmed(path, data, **kwargs):
        actual(path, data, **kwargs)
        raise OSError("simulated directory durability failure")
    monkeypatch.setattr(persistence, "atomic_write_json", replaced_but_unconfirmed)
    with pytest.raises(OSError): rb.apply_checkpoint(target, proof, **args)
    with pytest.raises(OSError): rb.apply_checkpoint(target, proof, **args)
    monkeypatch.setattr(persistence, "atomic_write_json", actual)
    assert rb.apply_checkpoint(target, proof, **args)["status"] == "ALREADY_APPLIED"


@pytest.mark.parametrize("field,value", [("estimated_costs_today", float("inf")),
    ("trades_today", -1), ("lifetime_unknown_pnl_trades", True), ("open_positions", 1.5)])
def test_equal_invalid_financial_state_never_qualifies_for_repair(field, value):
    from risk_basis_review import plan
    raw = {**legacy(), field: value}
    proof = {**deepcopy(raw), "risk_scope_key": SCOPE, "equity_basis_key": BASIS,
             "equity_basis_review_required": False, "equity_basis_review_reason": ""}
    result = plan(raw, proof, expected_basis=BASIS, expected_scope=SCOPE, today=DAY)
    assert result["status"] == "REVIEW_REQUIRED" and result["changes"] == {}


def test_identically_sparse_financial_states_never_default_missing_pnl_to_zero():
    from risk_basis_review import plan
    raw = legacy(); raw.pop("realized_pnl_today"); raw.pop("realized_receipts")
    proof = {**deepcopy(raw), "risk_scope_key": SCOPE, "equity_basis_key": BASIS,
             "equity_basis_review_required": False, "equity_basis_review_reason": ""}
    result = plan(raw, proof, expected_basis=BASIS, expected_scope=SCOPE, today=DAY)
    assert result["reason_codes"] == ["RISK_CHECKPOINT_FINANCIAL_FIELDS_MISSING"]


def test_direct_apply_also_requires_fresh_archive_provenance(tmp_path):
    from risk_basis_review import apply_checkpoint
    target = tmp_path / "risk_state_etoro.json"; target.write_text(json.dumps(legacy()))
    proof = tmp_path / "relabelled.json"; proof.write_text(json.dumps(legacy()))
    before = target.read_bytes()
    with pytest.raises(ValueError, match="ARCHIVE_AND_FRESH"):
        apply_checkpoint(target, proof, expected_sha256=hashlib.sha256(before).hexdigest(),
                         expected_basis=BASIS, expected_scope=SCOPE, today=DAY)
    assert target.read_bytes() == before


@pytest.mark.parametrize("name,value", [("positions", {"complete": True, "rows": ["bad"]}),
    ("closed_trade_history", {"complete": True, "rows": [None], "min_date": "2026-09-12"}),
    ("account", {"currency": "USD", "equity": 99524.94})])
def test_rehashed_invalid_imported_rows_and_missing_cash_are_not_confirmed(name, value):
    import etoro_risk_maintenance as maintenance
    result = evidence()
    result["observations"][name]["data"] = value
    result["observations"][name]["sha256"] = maintenance._digest(value)
    assert maintenance.review_evidence(result, now=NOW)["status"] == "REVIEW_REQUIRED"


def test_invalid_raw_history_row_is_rejected_before_adapter_drops_it(monkeypatch):
    from etoro_risk_maintenance import readonly_broker
    from broker.etoro import EtoroBroker
    monkeypatch.setattr(EtoroBroker, "_request", lambda *args, **kwargs: {"items": [None]})
    broker = readonly_broker(environment="DEMO")
    with pytest.raises(ValueError, match="HISTORY_RESPONSE_ROWS"):
        broker.trade_history_snapshot("2026-09-12", force=True)


def test_get_payload_is_rejected_even_on_allowed_path():
    from etoro_risk_maintenance import readonly_broker
    broker = readonly_broker(environment="DEMO")
    with pytest.raises(ValueError, match="READ_ONLY"):
        broker._request("GET", "/api/v1/me", payload={"unexpected": 1})


def test_config_read_does_not_delete_expired_live_arming_file(tmp_path, monkeypatch):
    import config
    import live_arming
    import runpy
    target = tmp_path / "live_trading_arm.json"
    original = json.dumps({"purpose": "independent-live-trading-arm", "nonce": "fixture",
                           "expires_at": "2000-01-01T00:00:00+00:00"})
    target.write_text(original)
    monkeypatch.setattr(live_arming, "ARM_FILE", target)
    loaded = runpy.run_path(config.__file__)
    assert loaded["LIVE_ARMED"] is False and "abgelaufen" in loaded["LIVE_ARM_STATUS"]
    assert target.read_text() == original
    # Normal operational cleanup remains available to the daemon.
    assert live_arming.arm_status()[0] is False
    assert not target.exists()
