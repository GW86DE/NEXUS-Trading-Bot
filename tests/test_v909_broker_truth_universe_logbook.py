from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace


def test_okx_multiple_fills_are_one_order_and_use_real_fees(monkeypatch):
    from broker.okx import OKXBroker, OKXInstrument

    client = SimpleNamespace()
    broker = OKXBroker(client=client, quote_ccy="EUR", allowed_quotes=("EUR", "USDC"))
    fills = [
        {"tradeId": "f1", "ordId": "o1", "fillSz": "0.4", "fillPx": "2000",
         "fee": "-0.0004", "feeCcy": "ETH", "ts": "1"},
        {"tradeId": "f2", "ordId": "o1", "fillSz": "0.6", "fillPx": "2010",
         "fee": "-0.0006", "feeCcy": "ETH", "ts": "2"},
    ]
    monkeypatch.setattr(broker, "order_fills", lambda *_a, **_k: fills)
    meta = OKXInstrument(
        "ETH-EUR", "ETH", "EUR", "live", "0.01", "0.0001", "0.001",
        trade_quote_ccy_list=("EUR",))
    result = broker._order_result_from_evidence(
        meta=meta,
        status={"ordId": "o1", "clOrdId": "N91", "state": "filled",
                "accFillSz": "1.0", "tradeQuoteCcy": "EUR"},
        cl_ord_id="N91", requested_qty=1.0, reference_price=2000)

    assert result.order_ids == ["o1"]
    assert result.fill_ids == [
        "okx:unknown:ETH-EUR:o1:f1", "okx:unknown:ETH-EUR:o1:f2"]
    assert all(row["instId"] == "ETH-EUR" for row in result.fills)
    assert result.gross_filled_quantity == 1.0
    assert result.filled_quantity == 0.999
    assert result.fill_evidence_complete is True
    assert result.fees == {"ETH": 0.001}
    assert result.fees_quote > 2.0


def test_unverified_okx_balance_is_not_a_managed_trade():
    from crypto_engine import KryptoPosition

    legacy = KryptoPosition(
        symbol="SOL", inst_id="SOL-EUR", menge=14.19, einstieg=99,
        stop=90, take_profit=120, order_id="old-order",
        referenz="old-reference", fill_ids=[], ownership_verified=False)
    assert legacy.darf_automatisch_verkaufen is False


def test_verified_okx_fill_chain_is_managed():
    from crypto_engine import KryptoPosition

    position = KryptoPosition(
        symbol="ETH", inst_id="ETH-EUR", menge=0.1, einstieg=2000,
        stop=1800, take_profit=2300, order_id="o1", referenz="N91",
        client_order_id="N91", order_tag="NEXUS", fill_ids=["f1"],
        ownership_verified=True)
    assert position.darf_automatisch_verkaufen is True


def test_spot_surplus_is_split_from_verified_bot_quantity():
    import exposure_klassifizierung as exposure

    position = SimpleNamespace(
        symbol="ETH", menge=0.1, herkunft="BOT", verwaltung="AUTO",
        darf_automatisch_verkaufen=True)
    result = exposure.klassifiziere(
        {"ETH": {"gesamt": 1.096348, "cash": 1.096348}},
        positionsbuch=[position], preise={"ETH": 4000})
    assert [x["klasse"] for x in result["bestaende"]] == [
        exposure.BOT_MANAGED, exposure.ACCOUNT_ASSET]
    assert result["einstiege_gesperrt"] is False


def test_fixed_core_contains_only_account_executable_pairs(monkeypatch, tmp_path):
    monkeypatch.setenv("TRADINGBOT_TEST_STATE_DIR", str(tmp_path))
    import okx_tradeable_core
    from broker.okx import OKXInstrument, OKXTicker

    instruments = {
        "BTC-EUR": OKXInstrument(
            "BTC-EUR", "BTC", "EUR", "live", ".1", ".00001", ".0001",
            trade_quote_ccy_list=("EUR",)),
        "ETH-EUR": OKXInstrument(
            "ETH-EUR", "ETH", "EUR", "live", ".1", ".0001", ".001",
            trade_quote_ccy_list=("EUR",)),
        "SOL-USDT": OKXInstrument(
            "SOL-USDT", "SOL", "USDT", "live", ".01", ".01", ".1",
            trade_quote_ccy_list=("USDT",)),
    }
    tickers = {
        "BTC-EUR": OKXTicker("BTC-EUR", 50000, 49990, 50010, 10, 500000, 49000),
        "ETH-EUR": OKXTicker("ETH-EUR", 2000, 1999, 2001, 50, 100000, 1900),
        "SOL-USDT": OKXTicker("SOL-USDT", 100, 99.9, 100.1, 500, 50000, 95),
    }
    selected, state = okx_tradeable_core.select(
        instruments=instruments, tickers=tickers,
        pair_reasons={"BTC-EUR": "", "ETH-EUR": "", "SOL-USDT": "keine Bot-Cashwaehrung"},
        quote_rates={"EUR": 1.0, "USDT": 0.9}, preferred=("BTC", "SOL"),
        limit=20, allowed_quotes=("EUR", "USDC"))
    assert selected == {"BTC", "ETH"}
    assert state["actual"] == 2 and state["missing_slots"] == 18
    assert all(item["allowed_trade_quotes"] for item in state["items"])


def test_universe_manager_uses_runtime_core_instead_of_static_placeholders(
        monkeypatch, tmp_path):
    monkeypatch.setenv("TRADINGBOT_TEST_STATE_DIR", str(tmp_path))
    from universe.manager import UniverseManager
    from universe.modelle import UniverseZustand

    manager = UniverseManager(UniverseZustand(tmp_path / "universe.json"))
    manager.lauf({
        "broker": "okx", "rangliste": [],
        "eligible_symbols": ["BTC", "ETH"],
        "core_symbols": ["BTC", "ETH"],
        "ineligible_reasons": {},
    })
    members = manager.zustand.fuer_broker("okx")
    assert {m.symbol for m in members} == {"BTC", "ETH"}
    assert all(m.handelbar for m in members)


def test_trade_ledger_keeps_real_fill_ids(monkeypatch, tmp_path):
    import config
    monkeypatch.setattr(config, "DECISION_DB_FILE", str(tmp_path / "decisions.sqlite"))
    import trade_ledger

    trade_id = trade_ledger.trade_open(
        broker="okx", symbol="ETH", menge=1, einstieg_preis=2000,
        asset_type="crypto", waehrung="EUR", decision_id=99,
        entry_order_id="o1", entry_fill_id="f1", entry_fill_ids=["f1", "f2"],
        entry_fills=[
            {"tradeId": "f1", "ordId": "o1", "fillSz": "0.4", "fillPx": "2000"},
            {"tradeId": "f2", "ordId": "o1", "fillSz": "0.6", "fillPx": "2010"}],
        client_order_id="N999", order_tag="NEXUS",
        ownership_status="VERIFIED_BROKER_FILL_CHAIN")
    assert trade_id
    row = trade_ledger.offener_trade("okx", "ETH")
    assert json.loads(row["entry_fill_ids_json"]) == ["f1", "f2"]
    assert row["client_order_id"] == "N999"


def test_logbook_uses_explicit_dom_nodes_and_supports_no_signal():
    root = Path(__file__).resolve().parents[1]
    js = (root / "webui/static/logbook.js").read_text(encoding="utf-8")
    html = (root / "webui/templates/logbook.html").read_text(encoding="utf-8")
    assert "status.value" not in js
    assert "document.getElementById('status')" in js
    assert "NO_SIGNAL" in js and "NO_SIGNAL" in html


def test_etoro_private_stream_parses_private_content_without_authorizing_trade():
    from broker.etoro_stream import EtoroPrivateStream

    events = []
    stream = EtoroPrivateStream("api", "user", events.append)
    stream._on_message(None, json.dumps({
        "messages": [{"topic": "private", "id": "m1",
                      "type": "Trading.OrderForOpen.Update",
                      "content": json.dumps({"RequestGuid": "r1", "OrderID": 7,
                                             "StatusID": 5, "ExecutedUnits": 0.4})}]}))
    assert events[0]["message_id"] == "m1"
    assert events[0]["content"]["RequestGuid"] == "r1"
    assert stream.status().last_private_event
