from datetime import datetime, timezone
from types import SimpleNamespace as NS
import json
import sqlite3

import pytest


def test_late_fill_keeps_true_week_on_later_reconciliation():
    from test_v984_pulsar_and_routing import approved, claim
    from pulsar import control as c
    item, now = approved()
    claim(item, now)
    c.record_execution(item["id"], "FILLED", {"order_ids": ["buy"], "position_ids": ["pos"], "filled_at": now}, now=now)
    c.record_execution(item["id"], "FILLED", {"order_ids": ["buy"], "position_ids": ["pos"]}, now=now+8*86400)
    with c.transaction() as con:
        assert [r[0] for r in con.execute("SELECT week FROM pulsar_weeks")] == [c.week(now)]
    assert c.for_symbol("PEP")["execution"]["filled_at"] == now


def test_partial_receipt_needs_exact_domain_and_order():
    from pulsar import control as c
    from pulsar.positions import claim_partial, partial_result, reconcile_partial
    import broker_exit_journal as journal
    item = {"id": "p", "account": "acct", "environment": "DEMO",
            "plan": {"instrument_id": "123"}, "execution": {"position_ids": ["pos"]}}
    with c.transaction() as con:
        con.execute("INSERT INTO pulsar_position_control(proposal_id) VALUES('p')")
    claim_partial("p", 3)
    partial_result("p", "SUBMITTED", {"order_ids": ["sell"]})
    foreign, _ = journal.begin(broker="etoro", account_fingerprint="other", environment="DEMO",
        instrument_id="123", position_id="pos", quantity=3)
    journal.update(foreign["intent_id"], "FILLED", broker_order_id="sell", filled_quantity=3)
    reconcile_partial(item)
    with c.transaction() as con:
        assert con.execute("SELECT partial_state FROM pulsar_position_control").fetchone()[0] == "SUBMITTED"
    exact, _ = journal.begin(broker="etoro", account_fingerprint="acct", environment="DEMO",
        instrument_id="123", position_id="pos", quantity=3)
    journal.update(exact["intent_id"], "FILLED", broker_order_id="sell", filled_quantity=3)
    reconcile_partial(item)
    with c.transaction() as con:
        assert con.execute("SELECT partial_state FROM pulsar_position_control").fetchone()[0] == "FILLED"
    with pytest.raises(c.Blocked):
        claim_partial("p", 3)


def test_pulsar_routes_need_login_and_csrf(monkeypatch):
    import importlib
    from fastapi.testclient import TestClient
    module = importlib.import_module("webui.app")
    from pulsar.control import settings
    with TestClient(module.app) as client:
        for path in ("/api/pulsar", "/api/pulsar/export"):
            assert client.get(path).status_code == 401
        assert client.get("/pulsar", follow_redirects=False).headers["location"] == "/login"
        monkeypatch.setattr(module, "_session", lambda *a, **k: {"u": "test", "csrf": "bound"})
        assert client.post("/api/pulsar/settings", json={"mode": "FREIGABE"}).status_code == 403
        assert settings()["mode"] == "AUS"
        assert client.post("/api/pulsar/settings", headers={"x-csrf-token": "bound"},
                           json={"mode": "BEOBACHTEN", "tradestie": False}).status_code == 200
        data = client.get("/api/pulsar").json()
        assert data["mode"] == "BEOBACHTEN" and not data["cards"]
        assert "nonce" not in client.get("/api/pulsar/export").text
        assert client.get("/pulsar").status_code == 200


def test_migration_preserves_pulsar_wal_budget_and_week(tmp_path):
    import settings_migration as migration
    source, target = tmp_path/"old", tmp_path/"new"
    source.mkdir(); target.mkdir()
    dbs = []
    try:
        for name, table in (("decision_history.sqlite", "pulsar_weeks"), ("pulsar_research.sqlite", "usage")):
            db = sqlite3.connect(source/name); dbs.append(db)
            db.execute("PRAGMA journal_mode=WAL")
            db.execute(f"CREATE TABLE {table}(id TEXT PRIMARY KEY, reserved REAL)")
            db.execute(f"INSERT INTO {table} VALUES('retained',1.5)"); db.commit()
        copied, _ = migration.migrate_from(source, target)
        for name, table in (("decision_history.sqlite", "pulsar_weeks"), ("pulsar_research.sqlite", "usage")):
            assert name in copied
            with sqlite3.connect(target/name) as db:
                assert db.execute(f"SELECT reserved FROM {table}").fetchone()[0] == 1.5
    finally:
        for db in dbs: db.close()


@pytest.mark.parametrize("change", [{"user_observe_locked": True}, {"broker_account_fingerprint": "foreign"},
    {"broker_environment": "LIVE"}, {"owned_position_ids": []}, {"reconciliation_status": "PENDING"}])
def test_unconfirmed_broker_protection_never_allows_foreign_client_exit(change):
    from test_v984_attribution_and_display import record
    from stock_exit_control import pending_stop_price
    rec = record(); rec.broker_snapshot_id="snapshot"; rec.broker_instrument_id="123"
    broker=NS(account_fingerprint=lambda: "test-account", paper=True,
              latest_bid_ask=lambda inst: {"bid": 130, "timestamp": "fresh"}, _quote_age_seconds=lambda stamp: 1)
    assert pending_stop_price(rec, broker, NS(), 109) == 130
    for key, value in change.items(): setattr(rec, key, value)
    assert pending_stop_price(rec, broker, NS(), 109) is None


def test_closed_link_rechecks_changed_fee_evidence_once(monkeypatch):
    import etoro_reconciliation as er
    record = {"decision_id": 321, "domain": "etoro:demo:acct", "closed_position_ids": ["pos"],
              "fills": [{"position_id": "pos", "quantity": 1, "fee": None}],
              "ledger_close_backfill": {"pos": {"status": "LINKED_CLOSED"}}}
    monkeypatch.setattr(er, "_load", lambda: {"records": {"321": record}})
    calls=[]
    monkeypatch.setattr(er, "_backfill_closed_ledger", lambda *a: (calls.append(1) or {"status": "LINKED_CLOSED"}))
    def change(did, fn):
        fn(record)
        return record
    monkeypatch.setattr(er, "_mutiere", change)
    monkeypatch.setattr(er, "_sync_legacy_state", lambda *a: None)
    monkeypatch.setattr(er, "_closed_accounting_pending", lambda *a: True)
    er._retry_closed_ledger_backfills("etoro:demo:acct")
    er._retry_closed_ledger_backfills("etoro:demo:acct")
    assert len(calls) == 1
    record["fills"][0]["fee"] = .7
    er._retry_closed_ledger_backfills("etoro:demo:acct")
    assert len(calls) == 2
