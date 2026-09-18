from __future__ import annotations

import json
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest


def _adapter(monkeypatch, rows):
    from broker.etoro import EtoroBroker

    broker = EtoroBroker(paper=True, api_key="api", user_key="account-a")
    broker._bind_account_identity({"demoCid": 21577959})
    snapshot = {
        "_snapshot_id": "etoro:demo:1",
        "clientPortfolio": {"positions": rows},
    }
    monkeypatch.setattr(broker, "_pnl", lambda force=False: snapshot)
    monkeypatch.setattr(broker, "_symbol_for_id", lambda iid: "MSFT")
    monkeypatch.setattr(
        broker, "_positionskurs", lambda iid, selected: (510.0, "TEST", 0.0))
    return broker


def _row(pid, *, iid=1001, units=1.0, stop=490.0, take=550.0):
    return {
        "positionID": pid, "instrumentID": iid, "units": units,
        "openRate": 500.0, "closeRate": 510.0, "isBuy": True,
        "stopLossRate": stop, "takeProfitRate": take,
    }


def test_real_api_positionID_is_normalized(monkeypatch):
    broker = _adapter(monkeypatch, [_row(7001)])
    positions = broker.positionen()
    assert len(positions) == 1
    assert positions[0].position_ids == ("7001",)
    assert positions[0].instrument_id == "1001"
    assert positions[0].account_fingerprint == broker.account_fingerprint()


def test_open_row_without_position_id_fails_closed(monkeypatch):
    from broker.base import BrokerFehler

    row = _row(7001)
    row.pop("positionID")
    broker = _adapter(monkeypatch, [row])
    with pytest.raises(BrokerFehler, match="ohne normalisierbare positionId"):
        broker.positionen()


def test_same_symbol_stays_one_record_per_position_id(monkeypatch, tmp_path):
    from position_manager import PositionManager

    manager = PositionManager(tmp_path / "positions.json")
    monkeypatch.setattr(manager, "_schreibe_anzeigedatei", lambda broker: None)
    monkeypatch.setattr(manager, "_offene_kaufabsicht", lambda *a, **k: None)
    positions = [
        SimpleNamespace(symbol="MSFT", quantity=1.0, avg_cost=500.0,
                        currency="USD", asset_type="stock", broker_id="7001",
                        position_ids=("7001",), instrument_id="1001",
                        account_fingerprint="acct", broker_environment="DEMO",
                        snapshot_id="snap-1"),
        SimpleNamespace(symbol="MSFT", quantity=2.0, avg_cost=501.0,
                        currency="USD", asset_type="stock", broker_id="7002",
                        position_ids=("7002",), instrument_id="1001",
                        account_fingerprint="acct", broker_environment="DEMO",
                        snapshot_id="snap-1"),
    ]
    broker = SimpleNamespace(
        paper=True, positionen=lambda: positions,
        account_fingerprint=lambda: "acct")
    current = manager.sync_with_broker(broker, [])
    assert set(current) == {
        "etoro:acct:demo:7001", "etoro:acct:demo:7002"}
    assert len(manager.records) == 2
    assert {tuple(x.position_id_set()) for x in manager.records.values()} == {
        ("7001",), ("7002",)}


def test_observed_position_never_self_confirms(monkeypatch, tmp_path):
    import etoro_reconciliation as reconciliation
    from position_manager import PositionManager

    monkeypatch.setenv("TRADINGBOT_TEST_STATE_DIR", str(tmp_path))
    reconciliation.start_intent(
        decision_id=94001, symbol="MSFT", paper=True, profile="",
        quantity=1, price=500, stop=490, take_profit=550,
        account_fingerprint="acct")
    reconciliation.apply_broker_evidence(94001, {
        "orderId": "o-1", "referenceId": "r-1",
        "status": {"id": 3, "name": "Filled"},
        "positionExecutions": [{
            "positionID": "7001",
            "openingData": {"units": 1, "avgPrice": 500},
        }],
    })
    manager = PositionManager(tmp_path / "positions.json")
    monkeypatch.setattr(manager, "_schreibe_anzeigedatei", lambda broker: None)
    pos = SimpleNamespace(
        symbol="MSFT", quantity=1.0, avg_cost=500.0, currency="USD",
        asset_type="stock", broker_id="7001", position_ids=("7001",),
        instrument_id="1001", account_fingerprint="acct",
        broker_environment="DEMO", snapshot_id="snap-1")
    broker = SimpleNamespace(
        paper=True, positionen=lambda: [pos], account_fingerprint=lambda: "acct")
    manager.sync_with_broker(broker, [])
    manager.sync_with_broker(broker, [])
    record = next(iter(manager.records.values()))
    assert record.ownership_status == "EXTERNAL"
    assert record.owned_position_id_set() == set()
    assert record.management_mode != "AUTO"


def test_exact_verified_chain_repairs_previous_external_record(monkeypatch, tmp_path):
    import etoro_reconciliation as reconciliation
    from position_manager import PositionManager

    manager = PositionManager(tmp_path / "positions.json")
    monkeypatch.setattr(manager, "_schreibe_anzeigedatei", lambda broker: None)
    pos = SimpleNamespace(
        symbol="SPGI", quantity=1.0, avg_cost=430.0, currency="USD",
        asset_type="stock", broker_id="8001", position_ids=("8001",),
        instrument_id="2001", account_fingerprint="acct",
        broker_environment="DEMO", snapshot_id="snap-1")
    broker = SimpleNamespace(
        paper=True, positionen=lambda: [pos], account_fingerprint=lambda: "acct")
    monkeypatch.setattr(reconciliation, "eigene_kaeufe", lambda: [])
    manager.sync_with_broker(broker, [])
    assert next(iter(manager.records.values())).source == "BROKER_EXISTING"

    monkeypatch.setattr(reconciliation, "eigene_kaeufe", lambda: [{
        "decision_id": 12, "symbol": "SPGI", "position_ids": ["8001"],
        "verified_position_ids": ["8001"], "order_ids": ["order-12"],
        "reference_id": "ref-12", "bestaetigt": True, "offen": False,
        "paper": True, "account_fingerprint": "acct", "stop": 420,
        "take_profit": 460,
    }])
    manager.sync_with_broker(broker, [])
    record = next(iter(manager.records.values()))
    assert record.source == "BOT"
    assert record.ownership_status == "VERIFIED"
    assert record.owned_position_id_set() == {"8001"}


def test_rejected_takeover_rolls_back_and_does_not_lock_repair(monkeypatch, tmp_path):
    from position_manager import PositionManager, PositionRecord

    manager = PositionManager(tmp_path / "positions.json")
    manager.records["etoro:acct:demo:9001"] = PositionRecord(
        con_id=0, symbol="MSFT", asset_type="stock", currency="USD",
        quantity=1, avg_cost=500, entry_time="2026-08-31T20:00:00+00:00",
        source="BROKER_EXISTING", management_mode="OBSERVE",
        broker_position_ids=["9001"], observed_position_ids=["9001"],
        broker_environment="DEMO", broker_account_fingerprint="acct",
        ownership_status="EXTERNAL")
    ok, _ = manager.request_takeover(
        "etoro:acct:demo:9001", 490, 550, aktueller_kurs=510)
    assert ok
    manager.reject_takeover("etoro:acct:demo:9001", "Broker lehnte Schutz ab")
    record = manager.records["etoro:acct:demo:9001"]
    assert (record.source, record.management_mode) == (
        "BROKER_EXISTING", "OBSERVE")
    assert record.last_manual_change == ""
    assert record.user_observe_locked is False


def test_protection_patch_and_confirmation_are_bound_to_exact_id(monkeypatch):
    import config
    from datetime import datetime, timezone

    rows = [_row(7001), _row(7002)]
    broker = _adapter(monkeypatch, rows)
    monkeypatch.setattr(broker, "_resolve", lambda instrument: {
        "instrumentId": 1001, "symbol": "MSFT"})
    calls = []
    original_pnl = broker._pnl
    # Real _pnl provides a new received-at timestamp on a forced post-PATCH read.
    monkeypatch.setattr(broker, "_pnl", lambda **kwargs: {
        **original_pnl(**kwargs), "_snapshot_at": datetime.now(timezone.utc).isoformat()})

    def request(method, path, **kwargs):
        calls.append((method, path, kwargs.get("payload")))
        if method == "PATCH":
            rows[0]["stopLossRate"] = 480.0
            rows[0]["takeProfitRate"] = 560.0
        return {"operationId": "11111111-2222-4333-8444-555555555555",
                "positionId": 7001, "referenceId": kwargs["request_id"]}

    monkeypatch.setattr(broker, "_request", request)
    monkeypatch.setattr(config, "ETORO_PROTECTION_CONFIRM_SECONDS", 0.01)
    result = broker.reconcile_position_protection(
        SimpleNamespace(name="MSFT"), 1, 480, 560,
        position_ids=["7001"], update_requested=True)
    assert result["protection_confirmed"] is True
    assert calls == [("PATCH", "/api/v2/trading/demo/positions/7001",
                      {"stopLossRate": 480.0, "takeProfitRate": 560.0,
                       "stopLossType": "fixed"})]


def test_sell_closes_only_explicit_position_id(monkeypatch):
    broker = _adapter(monkeypatch, [_row(7001), _row(7002)])
    monkeypatch.setattr(broker, "_resolve", lambda instrument: {
        "instrumentId": 1001, "symbol": "MSFT"})
    calls = []

    def request(method, path, **kwargs):
        calls.append(path)
        return {"orderForClose": {"orderID": 77}}

    monkeypatch.setattr(broker, "_request", request)
    result = broker.schliesse_position(
        SimpleNamespace(name="MSFT"), 1, 510, position_ids=["7002"])
    assert calls == [
        "/api/v1/trading/execution/demo/market-close-orders/positions/7002"]
    assert result.position_ids == ["7002"]


def test_sell_fill_updates_only_matching_position_record(tmp_path):
    from position_manager import PositionManager, PositionRecord

    manager = PositionManager(tmp_path / "positions.json")
    for pid in ("7001", "7002"):
        manager.records[f"etoro:acct:demo:{pid}"] = PositionRecord(
            con_id=0, symbol="MSFT", asset_type="stock", currency="USD",
            quantity=1, avg_cost=500, entry_time="2026-08-31T20:00:00+00:00",
            source="BOT", management_mode="AUTO",
            observed_position_ids=[pid], owned_position_ids=[pid],
            broker_account_fingerprint="acct", broker_environment="DEMO",
            ownership_status="VERIFIED", entry_order_ids=[f"buy-{pid}"])
    manager.register_sell(
        SimpleNamespace(symbol="MSFT", localSymbol="MSFT", conId=0),
        1, 510, position_ids=["7002"], account_fingerprint="acct")
    assert "etoro:acct:demo:7001" in manager.records
    assert "etoro:acct:demo:7002" not in manager.records


def test_parallel_buy_reservation_allows_exactly_one(monkeypatch, tmp_path):
    import etoro_reconciliation as reconciliation
    from broker.base import BrokerFehler

    monkeypatch.setenv("TRADINGBOT_TEST_STATE_DIR", str(tmp_path))
    barrier = threading.Barrier(2)
    outcomes = []

    def worker(decision_id):
        barrier.wait()
        try:
            reconciliation.reserve_and_start_intent(
                decision_id=decision_id, symbol=f"S{decision_id}", paper=True,
                profile="", quantity=1, price=10, stop=9, take_profit=12,
                account_fingerprint="acct")
            outcomes.append("ok")
        except BrokerFehler:
            outcomes.append("blocked")

    threads = [threading.Thread(target=worker, args=(did,))
               for did in (94011, 94012)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(outcomes) == ["blocked", "ok"]


def test_webui_logo_and_version_are_integrated():
    root = Path(__file__).resolve().parents[1]
    logo = root / "webui" / "static" / "nexus-logo.png"
    assert logo.exists() and logo.stat().st_size > 10_000
    for template in (root / "webui" / "templates").glob("*.html"):
        assert "/static/nexus-logo.png" in template.read_text(encoding="utf-8")
    # 9.5.2: gegen die Konfiguration pruefen statt gegen ein Literal -- sonst
    # muss dieser Test bei jedem Versionswechsel angefasst werden und
    # verliert damit seine Aussage.
    import config as _config
    assert ((root / "VERSION.txt").read_text(encoding="utf-8").strip()
            == _config.VERSION_NEXUS)
