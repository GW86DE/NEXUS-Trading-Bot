"""Receipt, response-contract and restart regressions from the 13 September audit."""
from copy import deepcopy
from types import SimpleNamespace as NS
import hashlib
import json
import time

import pytest

import etoro_fee_recovery as recovery
import etoro_reconciliation as rec
import trade_ledger as ledger
from pulsar import analysis, control, research, worker
from test_v985_pulsar_flow_and_pages import Router, answer, fixtures, seed_profiles
from test_fix3_pulsar_timeouts import router as real_router, response


def native():
    account = hashlib.sha256(b"etoro|demo|cid:12345").hexdigest()[:24]
    stamp = "2026-09-01T14:02:49.597Z"
    record = dict(decision_id=101, broker="etoro", symbol="ABC", paper=True, reference_id="offline-reference",
        account_fingerprint=account, domain="etoro:demo:"+account,
        order_ids=["777"], position_ids=["888"], closed_position_ids=["888"],
        state="CLOSED_BEFORE_IMPORT", position_state="CLOSED_CONFIRMED", quantity=4, price=100,
        fills=[dict(position_id="888", quantity=4, price=100, execution_time=stamp)])
    raw = dict(accountId=12345, orderId=777, action="open", transaction="buy", orderCurrency="usd",
        asset=dict(currency="USD", leverage=1, side="long"), status=dict(id=3,name="Filled"),
        positionExecutions=[dict(positionId=888, state="closed", openingData=dict(
            orderId=777, executionTime=stamp, units=4, avgPrice=100, fees=1, taxes=0, marketSpread=2))])
    return record, raw


def closed_ledger(monkeypatch, tmp_path):
    import decision_analytics as da
    monkeypatch.setattr(da, "DB_PATH", tmp_path/"decision_history.sqlite")
    record, raw = native()
    fill = recovery.entry_receipts(record, raw)[0]
    identity = dict(broker="etoro", symbol="ABC", paper=True, asset_type="stock", waehrung="USD",
        broker_position_id="888", broker_account_fingerprint=record["account_fingerprint"], entry_order_id="777")
    tid = ledger.trade_open(**identity, menge=4, einstieg_preis=100, decision_id=101,
        entry_fill_id=fill["fill_id"], entry_fills=[{**fill,"fee":None,"fee_currency":""}], critical=True)
    ledger.trade_close(**identity, menge=4, ausstieg_preis=105, exit_order_id="999",
        exit_fill_ids=["sell-fill"], critical=True)
    args = dict(broker="etoro", account=record["account_fingerprint"], paper=True,
        position_id="888", entry_order_id="777", entry_fills=[fill])
    return tid, record, raw, args


def test_entry_fee_projection_is_partial_atomic_and_idempotent(monkeypatch, tmp_path):
    tid, record, raw, args = closed_ledger(monkeypatch, tmp_path)
    assert ledger.reconcile_entry_fees_exact(**args)["updated"] == 1
    assert ledger.reconcile_entry_fees_exact(**args)["updated"] == 0
    with ledger._connect() as con:
        row = dict(con.execute("SELECT * FROM trades WHERE trade_id=?", (tid,)).fetchone())
        fee = con.execute("SELECT fee FROM trade_entry_fills WHERE trade_id=?", (tid,)).fetchone()[0]
    assert row["einstieg_gebuehr"] == fee == 1
    assert row["entry_fee_quality"] == "CONFIRMED"
    assert row["fee_quality"] != "CONFIRMED" and row["netto_pnl"] is None
    assert row["brutto_pnl"] == 20  # Spread is already in the fill price.
    with pytest.raises(ledger.LedgerZuordnungUnklar):
        ledger.reconcile_entry_fees_exact(**{**args,"entry_fills":[{**args["entry_fills"][0],"fee":2}]})
    with ledger._connect() as con:
        assert dict(con.execute("SELECT * FROM trades WHERE trade_id=?", (tid,)).fetchone()) == row


@pytest.mark.parametrize("legacy_alias", ["exact", "wrong_time", "foreign_account"])
def test_legacy_amd_alias_is_one_execution_and_old_provisional_net_is_audited(monkeypatch,tmp_path,legacy_alias):
    tid, record, raw, args = closed_ledger(monkeypatch,tmp_path)
    fill = args["entry_fills"][0]
    account = args["account"] if legacy_alias != "foreign_account" else "other"
    stamp = fill["filled_at"] if legacy_alias != "wrong_time" else "2026-09-02T14:02:49.597Z"
    alias = f"etoro:{account}:open:777:888:{stamp}"
    with ledger._connect() as con:
        con.execute('UPDATE trades SET entry_fill_ids_json=?,netto_pnl=20 WHERE trade_id=?',
            (json.dumps([fill['fill_id'],alias]),tid))
    if legacy_alias != "exact":
        with pytest.raises(ledger.LedgerZuordnungUnklar): ledger.reconcile_entry_fees_exact(**args)
        with ledger._connect() as con:
            assert con.execute('SELECT netto_pnl FROM trades WHERE trade_id=?',(tid,)).fetchone()[0]==20
    else:
        ledger.reconcile_entry_fees_exact(**args)
        with ledger._connect() as con:
            row=con.execute('SELECT netto_pnl,einstieg_gebuehr FROM trades WHERE trade_id=?',(tid,)).fetchone()
            assert row[0] is None and row[1]==1
            assert con.execute('SELECT COUNT(*) FROM trade_entry_fills WHERE trade_id=?',(tid,)).fetchone()[0]==1
            audit=con.execute('SELECT before_json FROM etoro_entry_cost_audit WHERE trade_id=?',(tid,)).fetchone()[0]
            assert json.loads(audit)['netto_pnl']==20


@pytest.mark.parametrize("fault", ["account", "environment", "order", "position", "quantity", "price", "currency", "taxes"])
def test_native_receipt_rejects_cross_binding_and_missing_costs(fault):
    record, raw = native()
    op = raw["positionExecutions"][0]["openingData"]
    if fault == "account": raw["accountId"] = 54321
    if fault == "environment": record["paper"] = False
    if fault == "order": op["orderId"] = 123
    if fault == "position": raw["positionExecutions"][0]["positionId"] = 123
    if fault == "quantity": op["units"] = 8
    if fault == "price": op["avgPrice"] = 101
    if fault == "currency": raw["orderCurrency"] = "EUR"
    if fault == "taxes": del op["taxes"]
    with pytest.raises(ValueError): recovery.entry_receipts(record, raw)


def test_closed_record_has_get_only_recovery_without_zero_exit_fee(monkeypatch, tmp_path):
    tid, record, raw, args = closed_ledger(monkeypatch, tmp_path)
    rec._save({"records": {str(record["decision_id"]): record}})
    calls = []
    broker = NS(paper=True, account_fingerprint=lambda:record["account_fingerprint"],
        _lookup_order=lambda **kw:(calls.append("lookup") or deepcopy(raw)),
        trade_history_snapshot=lambda *a,**kw:(calls.append("history") or dict(complete=True,rows=[
            dict(positionId=888,orderId=777,units=4,openRate=100,closeRate=105,fees=0,netProfit=20)])))
    snapshot = dict(complete=True, account_fingerprint=record["account_fingerprint"], environment="DEMO",positions=[])
    result = recovery.recover_one(broker, paper=True, profile="test", position_snapshot=snapshot, now=1000)
    assert result["fee_recovery"]["status"] == "ENTRY_CONFIRMED_EXIT_EXTERNAL_COSTS_PENDING", result["fee_recovery"]
    assert calls == ["lookup", "history"]
    assert recovery.recover_one(broker, paper=True, profile="test", position_snapshot=snapshot, now=1001) is None
    assert calls == ["lookup", "history"]  # durable cooldown, even with a new caller
    with ledger._connect() as con:
        row = con.execute("SELECT einstieg_gebuehr,fee_quality,netto_pnl FROM trades WHERE trade_id=?", (tid,)).fetchone()
    assert row[0] == 1 and row[1] != "CONFIRMED" and row[2] is None


@pytest.mark.parametrize("changed", ["broker_environment", "stored_account"])
def test_domain_change_during_get_never_projects_costs(monkeypatch,tmp_path,changed):
    tid, record, raw, args = closed_ledger(monkeypatch,tmp_path)
    rec._save({"records":{str(record["decision_id"]):record}})
    broker=NS(paper=True,account_fingerprint=lambda:record["account_fingerprint"],
              _lookup_order=lambda **kw:deepcopy(raw))
    def history(*a,**kw):
        if changed=="broker_environment": broker.paper=False
        else: rec._mutiere(record["decision_id"],lambda r:r.update(account_fingerprint="other"))
        return dict(complete=True,rows=[dict(positionId=888,orderId=777,units=4,openRate=100,closeRate=105,fees=0)])
    broker.trade_history_snapshot=history
    snapshot=dict(complete=True,account_fingerprint=record["account_fingerprint"],environment="DEMO",positions=[])
    recovery.recover_one(broker,paper=True,profile="test",position_snapshot=snapshot,now=1000)
    with ledger._connect() as con:
        row=con.execute('SELECT einstieg_gebuehr,entry_fee_quality FROM trades WHERE trade_id=?',(tid,)).fetchone()
    assert row[0] is None and row[1]!="CONFIRMED"


@pytest.mark.parametrize("fault", [None,"blank_claim","wrong_reference","missing_symbol"])
def test_actual_router_response_contract_costs_and_diagnostics(monkeypatch, fault):
    import ai_router
    client = real_router(monkeypatch)
    packets = [{"symbol":s,"sources":[{"id":hashlib.sha256(s.encode()).hexdigest(),"data":{"mentions":40}}]}
               for s in ("AAA","BBB")]
    original = deepcopy(packets)
    calls = []
    def transport(*a, **kw):
        calls.append(kw["json"])
        payload, schema, refs = analysis._precheck_contract(packets)
        assert kw["json"]["text"]["format"]["schema"] == schema
        notes = {p["symbol"]:answer(p) for p in payload["candidates"]}
        if fault == "blank_claim": notes["AAA"]["thesis"]["text"] = " "
        if fault == "wrong_reference": notes["AAA"]["thesis"]["source_ids"] = ["foreign"]
        if fault == "missing_symbol": del notes["BBB"]
        return response(json.dumps({"notes":notes}))
    monkeypatch.setattr(ai_router, "_post_bounded", transport)
    result = analysis.precheck(packets, client)
    assert len(calls) == 1 and packets == original
    assert research.usage_summary()["day"]["ai"] == pytest.approx(.00008)
    if fault:
        assert not result["ok"] and "Standardrouting" not in result["grund"]
        failure = research.cached("ai:precheck:last_failure")["data"]
        if fault == "blank_claim":
            assert failure["router_ok"] and failure["response"]["notes"]["AAA"]["thesis"]["text"] == " "
        assert not analysis.precheck(packets, client)["ok"] and len(calls) == 1
    else:
        assert result["ok"]
        for packet, note in zip(packets, result["daten"]["notes"]):
            assert note["thesis"]["source_ids"] == [packet["sources"][0]["id"]]
        assert analysis.precheck(packets, client)["cached"] and len(calls) == 1
    assert control.proposals() == []


def test_weekend_text_retry_is_bounded_and_reuses_market_receipts(monkeypatch):
    import ai_router
    rows, market = fixtures()
    now = [time.time()]
    monkeypatch.setattr(worker.time,"time",lambda:now[0])
    monkeypatch.setattr(worker,"refresh_held_data",lambda:None)
    calls = []
    monkeypatch.setattr(research,"discover",lambda:(calls.append("discover") or rows))
    profiles = {r["symbol"]: market(r["symbol"])["profile"] for r in rows}
    seed_profiles(monkeypatch, profiles)
    def gather(symbol, *, profile_evidence):
        assert profile_evidence["profile"] == profiles[symbol]
        calls.append(symbol)
        return market(symbol)
    monkeypatch.setattr(worker,"gather_market",gather)
    router = Router()
    gpt_inputs = []
    def fail(task, payload, schema, **kw):
        assert task == "pulsar_precheck" and len(payload["candidates"]) == 5
        gpt_inputs.append(deepcopy(payload))
        calls.append("GPT")
        return ai_router.AIAntwort(grund="offline failure")
    router.frage = fail
    monkeypatch.setattr(ai_router,"AIRouter",lambda:router)
    control.set_mode("BEOBACHTEN")
    w = worker.Worker(); w.session = control.start_session()
    w.cycle(control.settings())
    assert calls.count("GPT") == 1 and not w._retry_review(control.settings())
    saved_markets = deepcopy(research.cached("ai:precheck:work")["data"]["markets"])
    assert set(saved_markets) == set(profiles)
    for expected in (2,3):
        now[0] += 901
        # A replacement Worker reads the persisted job, not an in-memory timer.
        w = worker.Worker(); w.session = control.start_session()
        assert research.cached("ai:precheck:work")["data"]["markets"] == saved_markets
        assert w._retry_review(control.settings())
        assert calls.count("GPT") == expected
    now[0] += 901
    assert not w._retry_review(control.settings()) and calls.count("discover") == 1
    assert len([x for x in calls if x not in ("GPT","discover")]) == 5
    assert len(gpt_inputs) == 3 and gpt_inputs[0] == gpt_inputs[1] == gpt_inputs[2]
    assert research.cached("status")["data"]["phase"] == "VORPRUEFUNG_OFFEN"


def test_sui_legacy_annotation_requires_exact_closed_ledger_and_preserves_file(monkeypatch,tmp_path):
    import manual_trade_control as manual
    import decision_analytics as da
    monkeypatch.setattr(da,"DB_PATH",tmp_path/"decision_history.sqlite")
    identity = dict(broker="okx", symbol="SUI", paper=True, broker_position_id="SUI-USDC",
        broker_account_fingerprint="acct", entry_order_id="buy", waehrung="USDC")
    tid = ledger.trade_open(**identity, menge=4,einstieg_preis=100,decision_id=102,entry_fill_id="entry",critical=True)
    ledger.trade_close(**identity,menge=4,ausstieg_preis=105,exit_order_id="sell",exit_fill_ids=["fill"],critical=True)
    command = dict(trade_id=tid, account_fingerprint="acct", instrument="SUI-USDC", action="SELL")
    commands = [{**command,"id":"old","status":"FAILED","created_at":"2026-09-07T21:35:00Z"},
                {**command,"id":"new","status":"SUCCEEDED","created_at":"2026-09-08T11:14:00Z"}]
    path = tmp_path/manual.DATEI; path.write_text(json.dumps({"commands":commands}))
    before = path.read_bytes()
    result = manual.overview()
    assert result["commands"][1]["resolved_by"] == "new" and path.read_bytes() == before
    commands[1]["account_fingerprint"] = "other"
    path.write_text(json.dumps({"commands":commands}))
    assert "resolved_by" not in manual.overview()["commands"][1]
