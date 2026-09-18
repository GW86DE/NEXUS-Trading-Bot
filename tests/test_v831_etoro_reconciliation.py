from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest


class LookupBroker:
    name = "etoro"
    paper = True

    def __init__(self, answers):
        self.answers = list(answers); self.calls = []

    def _lookup_order(self, **kwargs):
        self.calls.append(kwargs)
        value = self.answers.pop(0)
        if isinstance(value, Exception): raise value
        return value


def _setup(monkeypatch, tmp_path, did=83101):
    monkeypatch.setenv("TRADINGBOT_TEST_STATE_DIR", str(tmp_path))
    import decision_analytics as da
    monkeypatch.setattr(da, "DB_PATH", tmp_path / "decisions.sqlite")
    da.init_db()
    da.record({"decision_id": did, "symbol": "FLR", "asset_type": "stock",
               "broker": "etoro", "paper": True, "status": "APPROVED",
               "profile": "balanced", "price": 52.67, "qty": 10,
               "stop": 51.0, "take": 55.0})
    import etoro_reconciliation as rec
    rec.start_intent(decision_id=did, symbol="FLR", paper=True, profile="balanced",
                     quantity=10, price=52.67, stop=51, take_profit=55)
    rec.accepted(did, order_id="o-555", reference_id="r-555")
    return da, rec


def test_accepted_then_order_404_reference_retry_confirms(monkeypatch, tmp_path):
    from broker.base import BrokerFehler
    da, rec = _setup(monkeypatch, tmp_path)
    evidence = {"orderId": "o-555", "referenceId": "r-555",
                "status": {"id": 3, "name": "Filled"},
                "positionExecutions": [{"positionId": "p-9", "openingData": {
                    "units": 10, "avgPrice": 52.5, "executionTime": "2026-08-26T20:00:00Z"}}]}
    broker = LookupBroker([BrokerFehler("HTTP 404"), evidence])
    out = rec.reconcile_one(broker, 83101, attempts=1, delays=(0,))
    assert broker.calls == [{"order_id": "o-555"}, {"reference_id": "r-555"}]
    assert out["state"] == "AWAITING_POSITION_CONFIRMATION"
    assert out["broker_execution_state"] == "FILLED" and out["position_ids"] == ["p-9"]
    assert da.latest(1)[0]["execution_status"] == "AWAITING_POSITION_CONFIRMATION"


@pytest.mark.parametrize("paper", [True, False])
def test_permanent_404_stays_reconciling_and_blocks_only_same_domain(monkeypatch, tmp_path, paper):
    from broker.base import BrokerFehler
    monkeypatch.setenv("TRADINGBOT_TEST_STATE_DIR", str(tmp_path))
    import decision_analytics as da, etoro_reconciliation as rec
    monkeypatch.setattr(da, "DB_PATH", tmp_path / "decisions.sqlite")
    da.record({"decision_id": 90, "symbol": "AAPL", "broker": "etoro", "status": "APPROVED"})
    rec.start_intent(decision_id=90, symbol="AAPL", paper=paper, profile="p1",
                     quantity=1, price=100, stop=95, take_profit=110)
    rec.accepted(90, order_id="o90", reference_id="r90")
    broker = LookupBroker([BrokerFehler("404")] * 4); broker.paper = paper
    out = rec.reconcile_one(broker, 90, attempts=2, delays=(0, 0))
    assert out["state"] == "UNKNOWN_AFTER_SUBMIT"
    with pytest.raises(BrokerFehler):
        rec.assert_domain_available(paper=paper, profile="p1")
    rec.assert_domain_available(paper=not paper, profile="p1")


def test_restart_is_idempotent_and_never_posts(monkeypatch, tmp_path):
    _, rec = _setup(monkeypatch, tmp_path)
    evidence = {"orderId": "o-555", "status": {"id": 3, "name": "Filled"},
                "positionExecutions": [{"positionId": "p-exact", "openingData": {"units": 10, "avgPrice": 52.5}}]}
    broker = LookupBroker([evidence])
    account = "test-etoro-demo-account-v831"
    broker.account_fingerprint = lambda: account
    broker.position_snapshot = lambda **_kwargs: {
        "position_ids": {"p-exact"}, "open_ids": {"p-exact"},
        "rows": [{"positionId": "p-exact", "orderId": "o-555"}],
        "complete": True, "account_fingerprint": account,
        "environment": "DEMO",
    }
    broker.current_position_ids = lambda **_kwargs: {"p-exact"}
    broker.trade_history = lambda *_args, **_kwargs: []
    recovered = rec.recover_all(broker, paper=True, profile="balanced")
    assert any(row["state"] == "FILLED" for row in recovered)
    assert all(set(c) <= {"order_id", "reference_id"} for c in broker.calls)
    assert rec.recover_all(broker, paper=True, profile="balanced") == []


def test_profile_switch_cannot_bypass_unresolved_account_order(monkeypatch, tmp_path):
    from broker.base import BrokerFehler
    _, rec = _setup(monkeypatch, tmp_path)
    with pytest.raises(BrokerFehler):
        rec.assert_domain_available(paper=True, profile="offensive")
    assert rec.domain_key(paper=True, profile="balanced") == rec.domain_key(
        paper=True, profile="conservative")


def test_corrupt_storage_fails_closed_without_crashing_recovery(monkeypatch, tmp_path):
    monkeypatch.setenv("TRADINGBOT_TEST_STATE_DIR", str(tmp_path))
    import config, etoro_reconciliation as rec
    (tmp_path / config.ETORO_RECONCILIATION_FILE).write_text("{broken", encoding="utf-8")
    broker = LookupBroker([])
    out = rec.recover_all(broker, paper=True, profile="balanced")
    assert out == [{"state": "RECONCILING", "reason": "reconciliation storage unreadable"}]
    assert broker.calls == []


def test_partial_fill_remainder_rejected_is_not_erased(monkeypatch, tmp_path):
    da, rec = _setup(monkeypatch, tmp_path)
    out = rec.apply_broker_evidence(83101, {
        "orderId": "o-555", "status": {"id": 10, "name": "RejectedPartiallyFilled"},
        "positionExecutions": [{"positionId": "p-part", "openingData": {"units": 4, "avgPrice": 52.4}}],
    })
    assert out["state"] == "AWAITING_POSITION_CONFIRMATION"
    assert out["broker_execution_state"] == "REJECTED_PARTIALLY_FILLED"
    assert out["filled_quantity"] == 4 and out["remaining_quantity"] == 6
    with sqlite3.connect(da.DB_PATH) as con:
        row = con.execute("SELECT status,filled_qty,remaining_qty FROM decision_orders WHERE decision_id=?", (83101,)).fetchone()
    assert row == ("REJECTED_PARTIALLY_FILLED", 4.0, 6.0)


def test_recovered_buy_fill_and_exact_ownership_survive_restart(monkeypatch, tmp_path):
    _, rec = _setup(monkeypatch, tmp_path)
    rec.apply_broker_evidence(83101, {
        "orderId": "o-555", "referenceId": "r-555",
        "status": {"id": 3, "name": "Filled"},
        "positionExecutions": [{"positionId": "p-late", "openingData": {
            "units": 10, "avgPrice": 52.5,
            "executionTime": "2026-08-26T20:00:00Z"}}],
    })
    fills = rec.recovered_buy_fills(paper=True)
    assert len(fills) == 1
    assert fills[0].fill_id == (
        "etoro:unresolved:open:o-555:p-late:2026-08-26T20:00:00Z")
    from order_ownership import OrderOwnershipRegistry
    registry = OrderOwnershipRegistry(tmp_path / "bot_order_registry.json")
    assert registry.is_bot_order("o-555")
    meta = registry.metadata("o-555")
    assert meta["decision_id"] == 83101 and meta["stop"] == 51


def test_exact_ownership_rejects_symbol_or_quantity_guess(monkeypatch, tmp_path):
    from broker.base import BrokerFehler
    _, rec = _setup(monkeypatch, tmp_path)
    with pytest.raises(BrokerFehler):
        rec.apply_broker_evidence(83101, {"orderId": "other-order",
            "status": {"id": 3, "name": "Filled"},
            "positionExecutions": [{"positionId": "p", "openingData": {"units": 10, "avgPrice": 52.5}}]})


def test_decision_snapshot_survives_later_system_error(monkeypatch, tmp_path):
    da, _rec = _setup(monkeypatch, tmp_path)
    before = da.latest(1)[0]["payload"]["decision_snapshot"]
    da.record_system_error(decision_id=83101, symbol="FLR", asset_type="stock",
                           broker="etoro", gate="lookup", exc=RuntimeError("404"), paper=True)
    after = da.latest(1)[0]
    assert after["status"] == "APPROVED"
    assert after["payload"]["decision_snapshot"] == before


def test_telegram_event_ids_deduplicate_across_restart(monkeypatch, tmp_path):
    monkeypatch.setenv("TRADINGBOT_TEST_STATE_DIR", str(tmp_path))
    import config, notifier
    monkeypatch.setattr(config, "NOTIFY_TELEGRAM", True)
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setattr(config, "TELEGRAM_CHAT_ID", "")
    notifier.send_telegram("⚠️ unklar", priority="critical", event_id="stable-1")
    notifier.send_telegram("⚠️ unklar", priority="critical", event_id="stable-1")
    state = json.loads((tmp_path / config.TELEGRAM_QUEUE_FILE).read_text(encoding="utf-8"))
    assert [x["event_id"] for x in state["queue"]] == ["stable-1"]


def test_telegram_exactly_once_for_uncertain_confirmed_failed(monkeypatch, tmp_path):
    monkeypatch.setenv("TRADINGBOT_TEST_STATE_DIR", str(tmp_path))
    import config, notifier, etoro_reconciliation as rec
    monkeypatch.setattr(config, "NOTIFY_TELEGRAM", True)
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setattr(config, "TELEGRAM_CHAT_ID", "")
    record = {"decision_id": 77, "symbol": "FLR", "filled_quantity": 1}
    for phase in ("uncertain", "confirmed", "failed"):
        rec._notify(record, phase); rec._notify(record, phase)
    state = json.loads((tmp_path / config.TELEGRAM_QUEUE_FILE).read_text(encoding="utf-8"))
    ids = [x["event_id"] for x in state["queue"]]
    assert len(ids) == len(set(ids)) == 3


def test_observe_position_displays_readonly_price_pnl_and_confirmed_protection(monkeypatch, tmp_path):
    from broker.base import Position
    from position_manager import PositionManager
    manager = PositionManager(tmp_path / "positions.json")
    class Contract:
        conId = "p1"; symbol = localSymbol = "FLR.US"; currency = "USD"
    manager.register_buy(Contract(), 2, 50, "USD", "stock", 1000, 45, 60,
                         source="BROKER_EXISTING", management_mode="OBSERVE")
    class Broker:
        def positionen(self):
            return [Position("FLR.US", 2, 50, "USD", "stock", "p1",
                             market_price=48, market_value=96, unrealized_pnl=-4,
                             broker_stop=44, broker_take_profit=61)]
    manager._schreibe_anzeigedatei(Broker())
    row = json.loads((tmp_path / "stock_positions.json").read_text(encoding="utf-8"))["positionen"][0]
    assert row["verwaltung"] == "OBSERVE" and row["kurs"] == 48 and row["unrealisiert"] == -4
    assert row["stop"] == 44 and row["ziel"] == 61 and row["schutz_quelle"] == "eToro read-only"
    assert not manager.is_auto_managed("FLR.US")


def test_created_time_primary_sort_even_if_updated_later(monkeypatch, tmp_path):
    monkeypatch.setenv("TRADINGBOT_TEST_STATE_DIR", str(tmp_path))
    import decision_analytics as da
    monkeypatch.setattr(da, "DB_PATH", tmp_path / "decisions.sqlite")
    da.record({"decision_id": 999, "created_at": "2026-08-01T00:00:00Z", "symbol": "OLD", "status": "APPROVED"})
    da.record({"decision_id": 1, "created_at": "2026-08-02T00:00:00Z", "symbol": "NEW", "status": "APPROVED"})
    da.mark_execution(999, "FILLED", ["late"])
    assert [x["symbol"] for x in da.latest(2)] == ["NEW", "OLD"]


def test_csv_export_uses_created_desc_then_id_desc(monkeypatch, tmp_path):
    import csv
    monkeypatch.setenv("TRADINGBOT_TEST_STATE_DIR", str(tmp_path))
    import decision_analytics as da, telegram_exports as exports
    monkeypatch.setattr(da, "DB_PATH", tmp_path / "decisions.sqlite")
    monkeypatch.setattr(exports, "EXPORT_DIR", tmp_path / "exports")
    da.record({"decision_id": 9, "created_at": "2026-08-01T10:00:00Z", "symbol": "OLD", "status": "APPROVED"})
    da.record({"decision_id": 1, "created_at": "2026-08-01T11:00:00Z", "symbol": "NEW", "status": "APPROVED"})
    with exports.export_decisions_csv("2026-08-01").open(encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    assert [x["symbol"] for x in rows] == ["NEW", "OLD"]
