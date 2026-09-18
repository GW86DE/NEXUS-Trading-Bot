"""Historical PEP incident: preserve the books, scope only the proven cohort."""
from copy import deepcopy
from datetime import date, datetime, timedelta
import json

import pytest

import etoro_risk_period as period
import etoro_fee_recovery as fees
import risk_manager as rm
import trade_ledger as ledger
from test_etoro_risk_maintenance import evidence, NOW, DAY, SCOPE
from test_v1012_risk_and_protection import unstarted
from test_v990_fix1_recovery import closed_ledger, native


def scoped(tmp_path, monkeypatch):
    risk, target = unstarted(tmp_path, monkeypatch)
    assert period.apply(risk, evidence(), now=NOW)
    return risk, target


def test_archived_cohort_and_current_pep_never_share_gate(tmp_path, monkeypatch):
    risk, path = scoped(tmp_path, monkeypatch)
    risk.register_unknown_pnl_trade("ledger:54")
    original = deepcopy(risk._payload())
    assert period.initialize_result_scope(risk)
    summary = period.result_scope_summary(risk)
    assert summary["historical_unknown"] == 6 and summary["active_unknown"] == 1
    assert "1 Verkaufsergebnis" in rm.kaufsperre_grund(risk)
    assert risk.lifetime_unknown_pnl_trades == 7
    assert risk._payload() == {**original, "result_period_scope": risk.result_period_scope}
    assert not period.initialize_result_scope(risk)
    reloaded = rm.RiskState.load(path)
    assert period.result_scope_summary(reloaded) == summary
    reloaded.register_realized_pnl(243.16, 100000, gross_pnl=244.16, trade_id="ledger:54")
    out = period.result_scope_summary(reloaded)
    assert out["historical_unknown"] == 6 and out["active_unknown"] == 0
    assert reloaded.lifetime_unknown_pnl_trades == 6
    assert reloaded.realized_receipts["ledger:50"]["status"] == "UNKNOWN"


def test_current_unknown_blocks_today_stays_booked_and_releases_after_midnight(tmp_path, monkeypatch):
    """10.7.0 (Entscheidung Georg, 18.09.2026): Ein unbekanntes Ergebnis sperrt
    am Verkaufstag; ab dem Folgetag bleibt es Buchhaltung, nicht Sperre.
    Bis 10.6.0 hiess dieser Test "survives midnight" -- ein Gebuehrenloch vom
    08.09. hielt am 18.09. den Handel an."""
    risk, path = scoped(tmp_path, monkeypatch)
    risk.register_unknown_pnl_trade("current-sale")
    assert period.initialize_result_scope(risk)
    heute = period.result_scope_summary(risk)
    assert heute["active_unknown"] == 1 and heute["open_unknown"] == 1
    assert "Ergebnisabgleich ausstehend" in rm.kaufsperre_grund(risk)
    monkeypatch.setattr(rm, "_handelstag_heute", lambda: date.fromisoformat(DAY)+timedelta(days=1))
    risk.refresh()
    assert risk.unknown_pnl_trades_today == 0
    morgen = period.result_scope_summary(risk)
    assert morgen["active_unknown"] == 0, "Die Sperre endet mit dem Handelstag"
    assert morgen["open_unknown"] == 1, "Der offene Beleg bleibt in der Buchhaltung"
    assert risk.lifetime_unknown_pnl_trades == 7
    assert "Ergebnisabgleich" not in rm.kaufsperre_grund(risk)
    # Die Tagesverlustsperre ist davon unabhaengig und bleibt.
    risk.set_trading_halted(True)
    assert "Tagesverlustlimit" in rm.kaufsperre_grund(risk)
    assert risk.trading_halted


@pytest.mark.parametrize("fault", ["scope", "archive", "evidence", "cohort", "missing_file"])
def test_scope_requires_original_identity_and_proof(tmp_path, monkeypatch, fault):
    risk, path = scoped(tmp_path, monkeypatch)
    receipt = risk.basis_review_receipt
    if fault == "missing_file":
        (path.parent/receipt["archive"]).unlink()
        assert not period.initialize_result_scope(risk)
    else:
        assert period.initialize_result_scope(risk)
        proof = risk.result_period_scope
        if fault == "scope": risk.risk_scope_key = '["etoro","LIVE","other"]'
        if fault == "archive": proof["original_state"] += " "
        if fault == "evidence": proof["account_evidence"]["observations"]["account"]["data"]["equity"] += 1
        if fault == "cohort": del risk.realized_receipts["ledger:50"]
        assert period.result_scope_summary(risk)["review_required"]
    assert risk.lifetime_unknown_pnl_trades == 6


def test_resolved_archived_receipt_decreases_both_offset_and_total(tmp_path, monkeypatch):
    risk, path = scoped(tmp_path, monkeypatch)
    assert period.initialize_result_scope(risk)
    # Actual fixtures omit booked_day for the old result. To test date-aware
    # old completion use the preserving register path, not a guessed timestamp.
    risk.register_realized_pnl(-2, 100000, gross_pnl=-1, trade_id="ledger:50")
    # A missing original day being replaced with today's day is ambiguous,
    # so the proof does not silently excuse this new current-day booking.
    assert period.result_scope_summary(risk)["review_required"]


def test_dated_old_result_completion_preserves_period_and_today(tmp_path, monkeypatch):
    risk, path = unstarted(tmp_path, monkeypatch)
    risk.realized_receipts["ledger:50"]["booked_day"] = "2026-09-08"
    risk.save()
    assert period.apply(risk, evidence(), now=NOW)
    assert period.initialize_result_scope(risk)
    before_today = risk.realized_pnl_today
    before_losses = risk.consecutive_losses
    risk.register_realized_pnl(-2, 100000, gross_pnl=-1, trade_id="ledger:50")
    summary = period.result_scope_summary(risk)
    assert not summary["review_required"] and summary["active_unknown"] == 0
    assert summary["historical_unknown"] == risk.lifetime_unknown_pnl_trades == 5
    assert risk.realized_pnl_today == before_today and risk.consecutive_losses == before_losses


def test_valid_new_scope_never_clears_existing_loss_or_cooldown(tmp_path, monkeypatch):
    risk, path = scoped(tmp_path, monkeypatch)
    risk.trading_halted = True
    risk.equity_guard_halted = True
    risk.consecutive_losses = 5
    risk.cooldown_until = datetime.now()+timedelta(hours=1)
    risk.realized_pnl_today = -130
    risk.save()
    previous = deepcopy(risk._payload())
    assert period.initialize_result_scope(risk)
    for key in ("trading_halted", "equity_guard_halted", "consecutive_losses", "cooldown_until", "realized_pnl_today"):
        assert risk._payload()[key] == previous[key]
    assert "Tagesverlustlimit" in rm.kaufsperre_grund(risk)


def close_order(record):
    return {"accountId":12345,"orderId":999,"action":"close","transaction":"sell",
        "status":{"name":"Filled","errorCode":0},"orderCurrency":"USD",
        "asset":{"currency":"USD","side":"long","leverage":1,"settlementType":"REAL"},
        "totalCosts":1.2,"positionsToClose":[888],"requestedUnits":4,
        "positionExecutions":[{"positionId":888,"state":"closed"}]}


def test_exact_close_costs_complete_net_once_and_do_not_charge_spread(tmp_path, monkeypatch):
    tid, record, raw, args = closed_ledger(monkeypatch, tmp_path)
    events=[{"positionId":888,"closeOrderId":999,"fillId":"sell-fill","closedUnits":4,"closeRate":105}]
    exits=fees.close_cost_receipts(record, close_order(record), events)
    assert exits[0]["fee"] == 1.2
    result=ledger.reconcile_fees_exact(**args, exit_fills=exits, cost_evidence={"source":"fixture"})
    assert result["updated"] == 1
    assert ledger.reconcile_fees_exact(**args, exit_fills=exits)["updated"] == 0
    row=ledger.trade_detail(tid)
    assert row["fee_quality"] == "CONFIRMED" and row["netto_pnl"] == pytest.approx(17.8)
    assert row["brutto_pnl"] == 20 and row["gebuehren"] == pytest.approx(2.2)
    with ledger._connect() as con:
        assert con.execute("SELECT COUNT(*) FROM etoro_closed_cost_audit").fetchone()[0] == 1


@pytest.mark.parametrize("fault", ["cancelled", "entry_order", "account", "partial", "cost_missing", "cost_bool", "missing_close_id", "other_position"])
def test_old_cancelled_order_and_unbound_costs_never_complete(fault):
    record, raw = native()
    order=close_order(record)
    events=[{"positionId":888,"closeOrderId":999,"fillId":"sell-fill","closedUnits":4,"closeRate":105}]
    if fault == "cancelled": order["status"]["name"]="Cancelled"
    if fault == "entry_order": order["orderId"]=777
    if fault == "account": order["accountId"]=9999
    if fault == "partial": events[0]["closedUnits"]=2
    if fault == "cost_missing": del order["totalCosts"]
    if fault == "cost_bool": order["totalCosts"]=False
    if fault == "missing_close_id": del events[0]["closeOrderId"]
    if fault == "other_position": events[0]["positionId"]=999
    with pytest.raises(ValueError): fees.close_cost_receipts(record, order, events)


def test_v1_schema_is_not_misreported_as_wrong_account():
    record, _ = native()
    with pytest.raises(ValueError, match="SCHEMA_UNPROVEN"):
        fees.entry_receipts(record, {"CID":12345,"orderId":777})


def test_fee_lookup_does_not_fallback_to_v1():
    class Broker:
        paper=True
        def _request(self, method, path, **kwargs):
            assert method == "GET" and path == "/api/v2/trading/info/demo/orders:lookup"
            assert kwargs["params"] == {"orderId":"777"}
            raise ValueError("v2 unavailable")
        def _lookup_order(self, **kwargs): pytest.fail("must not fall back to v1")
    with pytest.raises(ValueError, match="v2 unavailable"): fees._native_order(Broker(), "777")


def test_full_cost_recovery_projects_only_actual_order_and_is_idempotent(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import etoro_reconciliation as rec
    tid, record, raw, args = closed_ledger(monkeypatch, tmp_path)
    record["close_evidence_by_position_id"] = {"888": {
        "closeOrderId":"999", "fillId":"sell-fill", "closedUnits":4,"closeRate":105}}
    rec._save({"records": {str(record["decision_id"]): record}})
    calls=[]
    broker=SimpleNamespace(paper=True,account_fingerprint=lambda:record["account_fingerprint"],
        _lookup_order=lambda **kw:(calls.append(kw["order_id"]) or
            deepcopy(raw) if kw["order_id"] == "777" else close_order(record)),
        trade_history_snapshot=lambda *a,**kw:dict(complete=True, rows=[dict(
            positionId=888,orderId=777,units=4,openRate=100,closeRate=105,fees=0,netProfit=20)]))
    snapshot=dict(complete=True, account_fingerprint=record["account_fingerprint"],environment="DEMO",positions=[])
    result=fees.recover_one(broker,paper=True,profile="fixture",position_snapshot=snapshot,now=1000)
    assert result["fee_recovery"]["status"] == "COSTS_CONFIRMED", result
    assert ledger.trade_detail(tid)["netto_pnl"] == pytest.approx(17.8)
    assert fees.recover_one(broker,paper=True,profile="fixture",position_snapshot=snapshot,now=100001) is None


def test_completed_close_cost_cannot_overwrite_verified_entry_fee(tmp_path, monkeypatch):
    tid, record, raw, args = closed_ledger(monkeypatch, tmp_path)
    ledger.reconcile_entry_fees_exact(**args)
    changed=deepcopy(args)
    changed["entry_fills"][0]["fee"] = 2
    with pytest.raises(ledger.LedgerZuordnungUnklar, match="Einstiegskosten"):
        ledger.reconcile_fees_exact(**changed,exit_fills=[{
            "fill_id":"sell-fill","quantity":4,"price":105,"fee":1,"fee_currency":"USD"}])
    row=ledger.trade_detail(tid)
    assert row["einstieg_gebuehr"] == 1 and row["netto_pnl"] is None
