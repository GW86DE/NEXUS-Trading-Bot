"""Counterexample-driven PEP result accounting and native TP cost recovery."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

import etoro_fee_recovery as fees
import etoro_history_accounting as history
import etoro_reconciliation as rec
import trade_ledger as ledger
from ledger_result import confirmed_net
from test_v990_fix1_recovery import closed_ledger
from test_v1013_etoro_result_scope import close_order


def scenario(monkeypatch, tmp_path):
    tid, record, raw, args = closed_ledger(monkeypatch, tmp_path)
    opened = raw["positionExecutions"][0]["openingData"]["executionTime"]
    snapshot = dict(complete=True, account_fingerprint=record["account_fingerprint"],
        environment="DEMO", snapshot_at="2026-09-14T19:46:55+00:00", rows=[dict(
        positionId=888, orderId=777, instrumentId=1043, units=4, openRate=100,
        closeRate=105, openTimestamp=opened, closeTimestamp="2026-09-14T13:32:26.867Z",
        isBuy=True, leverage=1, fees=0, netProfit=20)])
    with ledger._connect() as con:
        con.execute("UPDATE trades SET ausgestiegen_am=?,exit_order_id='' WHERE trade_id=?",
                    (snapshot["rows"][0]["closeTimestamp"], tid))
    kw = dict(account=args["account"], paper=True, position_id="888", entry_order_id="777", snapshot=snapshot)
    return tid, record, raw, args, kw


def test_history_arithmetic_match_cannot_erase_known_entry_charge(monkeypatch, tmp_path):
    tid, record, raw, args, kw = scenario(monkeypatch, tmp_path)
    ledger.reconcile_entry_fees_exact(**args)
    first = history.record_history(**kw)
    assert first["quality"] == "BROKER_HISTORY_COST_SCOPE_CONFLICT"
    assert first["arithmetic_delta"] == 0
    assert first["confirmed_entry_costs"] == 1
    assert first["fee_scope_confirmed"] is False and first["risk_release"] is False
    row = ledger.trade_detail(tid)
    assert row["broker_reported_net_pnl"] == 20 and row["broker_reported_fees"] == 0
    assert row["einstieg_gebuehr"] == 1 and row["netto_pnl"] is None and not confirmed_net(row)
    assert row["exit_order_id"] == "" and row["ausstieg_preis"] == 105
    assert history.record_history(**kw)["updated"] == 0
    with ledger._connect() as con:
        assert con.execute("SELECT COUNT(*) FROM etoro_history_result_receipts").fetchone()[0] == 1


@pytest.mark.parametrize("fault", ["account", "environment", "position", "entry", "partial",
    "duplicate", "open", "direction", "leverage", "price", "time", "incomplete"])
def test_foreign_partial_or_ambiguous_history_never_updates_ledger(monkeypatch, tmp_path, fault):
    tid, record, raw, args, kw = scenario(monkeypatch, tmp_path)
    snap = kw["snapshot"]
    item = snap["rows"][0]
    if fault == "account": snap["account_fingerprint"] = "other"
    if fault == "environment": snap["environment"] = "LIVE"
    if fault == "position": item["positionId"] = 999
    if fault == "entry": item["orderId"] = 123
    if fault == "partial": item["units"] = 2
    if fault == "duplicate": snap["rows"].append(deepcopy(item))
    if fault == "open":
        with ledger._connect() as con: con.execute("UPDATE trades SET ausgestiegen_am=NULL WHERE trade_id=?", (tid,))
    if fault == "direction": item["isBuy"] = False
    if fault == "leverage": item["leverage"] = 2
    if fault == "price": item["closeRate"] = 106
    if fault == "time": item["closeTimestamp"] = "2026-09-14T13:33:26.867Z"
    if fault == "incomplete": snap["complete"] = False
    before = ledger.trade_detail(tid)
    with pytest.raises(ValueError): history.record_history(**kw)
    assert ledger.trade_detail(tid) == before


@pytest.mark.parametrize("net,fee,quality", [
    (20,0,"BROKER_HISTORY_COST_SCOPE_UNPROVEN"),
    (18,1,"BROKER_HISTORY_ARITHMETIC_MISMATCH"),
    (None,0,"BROKER_HISTORY_FIELDS_INCOMPLETE"),
    (20,None,"BROKER_HISTORY_FIELDS_INCOMPLETE")])
def test_consistency_is_not_independent_proof(monkeypatch,tmp_path,net,fee,quality):
    tid, record, raw, args, kw = scenario(monkeypatch,tmp_path)
    kw["snapshot"]["rows"][0].update(netProfit=net,fees=fee)
    result=history.record_history(**kw)
    assert result["quality"] == quality and not confirmed_net(ledger.trade_detail(tid))


def test_fee_worker_keeps_actual_pep_style_conflict_visible(monkeypatch, tmp_path):
    tid, record, raw, args, kw = scenario(monkeypatch,tmp_path)
    rec._save({"records": {str(record["decision_id"]): record}})
    calls=[]
    broker=SimpleNamespace(paper=True,account_fingerprint=lambda:record["account_fingerprint"],
        _lookup_order=lambda **opts:(calls.append(opts["order_id"]) or deepcopy(raw)),
        trade_history_snapshot=lambda *a,**k:deepcopy(kw["snapshot"]))
    monkeypatch.setattr(fees,"_stream_close_hints",lambda *_:{})
    result=fees.recover_one(broker,paper=True,profile="test",now=1000,position_snapshot={
        "complete":True,"account_fingerprint":record["account_fingerprint"],"environment":"DEMO","positions":[]})
    report=result["fee_recovery"]
    assert calls == ["777"]
    assert report["history_result"]["quality"] == "BROKER_HISTORY_COST_SCOPE_CONFLICT"
    assert report["missing_evidence"] == ["EXIT_COST_SCOPE_AND_EXTERNAL_COMMISSION"]
    assert ledger.trade_detail(tid)["einstieg_gebuehr"] == 1
    assert not confirmed_net(ledger.trade_detail(tid))


@pytest.mark.parametrize("fault", [None,"canceled","foreign","wrong_id","partial","entry_order","foreign_history"])
def test_late_native_tp_hint_requires_real_rest_close_cost_receipt(monkeypatch,tmp_path,fault):
    tid, record, raw, args, kw = scenario(monkeypatch,tmp_path)
    rec._save({"records": {str(record["decision_id"]): record}})
    close=close_order(record)
    if fault == "canceled": close["status"]["name"]="Canceled"
    if fault == "foreign": close["accountId"]=999
    if fault == "wrong_id": close["orderId"]=1000
    if fault == "partial": close["requestedUnits"]=2
    if fault == "entry_order": close=raw
    if fault == "foreign_history": kw["snapshot"]["account_fingerprint"]="other"
    calls=[]
    def lookup(**opts):
        calls.append(opts["order_id"])
        return deepcopy(raw if opts["order_id"] == "777" else close)
    broker=SimpleNamespace(paper=True,account_fingerprint=lambda:record["account_fingerprint"],
        _lookup_order=lookup,trade_history_snapshot=lambda *a,**k:deepcopy(kw["snapshot"]))
    monkeypatch.setattr(fees,"_stream_close_hints",lambda *_:{"999":"native-message"})
    snap={"complete":True,"account_fingerprint":record["account_fingerprint"],"environment":"DEMO","positions":[]}
    result=fees.recover_one(broker,paper=True,profile="test",now=1000,position_snapshot=snap)
    row=ledger.trade_detail(tid)
    assert calls==["777","999"]
    assert row["menge"]==4 and row["ausstieg_preis"]==105
    if fault:
        assert row["exit_order_id"] == ""
        assert not confirmed_net(row) and row["netto_pnl"] is None
        assert result["fee_recovery"]["native_close_rejection"]
    else:
        assert row["exit_order_id"] == "999"  # actual TP receipt, never the old request
        assert result["fee_recovery"]["exit_projection"]["verified_close_order_id"] == "999"
        assert row["netto_pnl"] == pytest.approx(17.8)
        assert result["fee_recovery"]["status"] == "COSTS_CONFIRMED"
        assert result["fee_recovery"]["native_close_source"] == "DURABLE_STREAM_HINT_VERIFIED_BY_REST"
        assert fees.recover_one(broker,paper=True,profile="test",now=100000,position_snapshot=snap) is None


def test_native_close_can_reuse_audited_alias_without_duplicate_quantity(monkeypatch,tmp_path):
    tid, record, raw, args, kw = scenario(monkeypatch,tmp_path)
    record["close_evidence_by_position_id"]={"888":{"fillId":"sell-fill"}}
    with ledger._connect() as con:
        con.execute("UPDATE trades SET exit_fill_ids_json=? WHERE trade_id=?",
                    (json.dumps(["legacy-alias","sell-fill"]),tid))
    events=fees._position_close_events(record,kw["snapshot"],close_order(record))
    assert len(events)==1 and events[0]["fillId"]=="sell-fill" and events[0]["closedUnits"]==4
    exits=fees.close_cost_receipts(record,close_order(record),events)
    ledger.reconcile_fees_exact(**args,exit_fills=exits)
    assert ledger.trade_detail(tid)["menge"]==4
    assert ledger.trade_detail(tid)["netto_pnl"]==pytest.approx(17.8)


def test_documented_close_order_without_position_id_is_only_a_rest_lookup_hint(monkeypatch,tmp_path):
    import etoro_stream_inbox as inbox
    tid, record, raw, args, kw = scenario(monkeypatch,tmp_path)
    scope=dict(account_fingerprint=record["account_fingerprint"],paper=True,cid="12345")
    good={"message_type":"Trading.OrderForCloseMultiple.Update", "content":{
        "CID":12345,"InstrumentID":1043,"OrderID":999,"RequestedUnits":4,"ExecutedUnits":4}}
    key=inbox.persist_event(good,**scope)["event_key"]
    bad=deepcopy(good); bad["content"].update(CID=999,OrderID=998)
    inbox.persist_event(bad,**scope)
    other=deepcopy(good); other["content"].update(InstrumentID=2,OrderID=997)
    inbox.persist_event(other,**scope)
    buy=deepcopy(good); buy["message_type"]="Trading.OrderForOpen.Update"; buy["content"]["OrderID"]=996
    inbox.persist_event(buy,**scope)
    assert fees._stream_close_hints(record,kw["snapshot"])=={"999":key}
    assert not confirmed_net(ledger.trade_detail(tid))  # received != proven execution
    rec._save({"records":{str(record["decision_id"]):record}})
    calls=[]
    def lookup(**opts):
        calls.append(opts["order_id"])
        return deepcopy(raw if opts["order_id"]=="777" else close_order(record))
    broker=SimpleNamespace(paper=True,account_fingerprint=lambda:record["account_fingerprint"],
        _lookup_order=lookup,trade_history_snapshot=lambda *a,**k:deepcopy(kw["snapshot"]))
    snap={"complete":True,"account_fingerprint":record["account_fingerprint"],"environment":"DEMO","positions":[]}
    report=fees.recover_one(broker,paper=True,profile="test",now=1000,position_snapshot=snap)["fee_recovery"]
    assert calls==["777","999"] and report["status"]=="COSTS_CONFIRMED"
    assert ledger.trade_detail(tid)["netto_pnl"]==pytest.approx(17.8)
    assert inbox.metrics(account_fingerprint=record["account_fingerprint"],paper=True)["matched"]==1


@pytest.mark.parametrize("already_accounted", [False, True])
def test_verified_native_close_id_is_bound_atomically_and_only_once(monkeypatch,tmp_path,already_accounted):
    tid, record, raw, args, kw = scenario(monkeypatch,tmp_path)
    order=close_order(record)
    events=fees._position_close_events(record,kw["snapshot"],order)
    exits=fees.close_cost_receipts(record,order,events)
    evidence={"source":"ETORO_V2_FILLED_CLOSE_ORDER_TOTAL_COSTS","order":order}
    if already_accounted:
        ledger.reconcile_fees_exact(**args,exit_fills=exits)
        assert ledger.trade_detail(tid)["exit_order_id"] == ""
    result=ledger.reconcile_fees_exact(**args,exit_fills=exits,cost_evidence=evidence)
    assert result == {"updated":1,"verified_close_order_id":"999"}
    row=ledger.trade_detail(tid)
    assert row["exit_order_id"] == "999" and row["menge"] == 4
    assert row["netto_pnl"] == pytest.approx(17.8)
    assert ledger.reconcile_fees_exact(**args,exit_fills=exits,cost_evidence=evidence)["updated"] == 0
    assert ledger.trade_detail(tid) == row
    other=deepcopy(evidence); other["order"]["orderId"]=1000
    other_exits=deepcopy(exits); other_exits[0]["order_id"]="1000"
    with pytest.raises(ledger.LedgerZuordnungUnklar,match="Exit-ID"):
        ledger.reconcile_fees_exact(**args,exit_fills=other_exits,cost_evidence=other)
    assert ledger.trade_detail(tid) == row
    with ledger._connect() as con:
        assert con.execute("SELECT COUNT(*) FROM etoro_closed_cost_audit").fetchone()[0] == 1
        bound=con.execute("SELECT exit_order_id,receipt_json FROM etoro_close_order_binding_audit").fetchall()
        assert len(bound) == 1 and bound[0][0] == "999"
        assert json.loads(bound[0][1])["cost_evidence"]["order"]["orderId"] == 999


@pytest.mark.parametrize("leg", ["entry","close","ledger"])
def test_boolean_leverage_cannot_confirm_native_cost_receipts(monkeypatch,tmp_path,leg):
    tid, record, raw, args, kw = scenario(monkeypatch,tmp_path)
    order=close_order(record)
    events=fees._position_close_events(record,kw["snapshot"],order)
    if leg == "entry":
        raw["asset"]["leverage"]=True
        with pytest.raises(ValueError): fees.entry_receipts(record,raw)
    elif leg == "close":
        order["asset"]["leverage"]=True
        with pytest.raises(ValueError): fees.close_cost_receipts(record,order,events)
    else:
        exits=fees.close_cost_receipts(record,order,events)
        order["asset"]["leverage"]=True
        with pytest.raises(ledger.LedgerZuordnungUnklar):
            ledger.reconcile_fees_exact(**args,exit_fills=exits,cost_evidence={
                "source":"ETORO_V2_FILLED_CLOSE_ORDER_TOTAL_COSTS","order":order})
    assert not confirmed_net(ledger.trade_detail(tid))
