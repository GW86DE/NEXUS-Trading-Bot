"""Regressionen der systemischen API-/Waehrungskorrektur in 9.0.7."""
from __future__ import annotations

from types import SimpleNamespace

import pytest


def test_uncertain_order_is_not_a_connection_loss():
    from broker.base import BrokerFehler, OrderStatusUnklar, VerbindungVerloren
    from live_trader import _connection_failure

    assert issubclass(OrderStatusUnklar, BrokerFehler)
    assert not issubclass(OrderStatusUnklar, VerbindungVerloren)
    broker = SimpleNamespace(is_connected=lambda: True)
    assert not _connection_failure(broker, OrderStatusUnklar("accepted, pending"))


def test_etoro_partial_fill_status_five_waits_for_rest_cancellation(monkeypatch):
    from broker.etoro import EtoroBroker
    import broker.etoro as adapter

    broker = EtoroBroker(paper=True, api_key="key", user_key="user")
    calls = []
    replies = iter([
        {"status": {"id": 5, "name": "PartiallyFilled"}, "positionExecutions": []},
        {"status": {"id": 9, "name": "CanceledPartiallyFilled"}, "positionExecutions": []},
    ])
    broker._lookup_order = lambda **kwargs: calls.append(kwargs) or next(replies)
    monkeypatch.setattr(adapter.time, "sleep", lambda _seconds: None)
    result = broker._wait_open_order(order_id="o-5", reference_id="r-5")
    assert result["status"]["id"] == 9
    assert calls == [{"order_id": "o-5"}, {"order_id": "o-5"}]


def test_background_reconciliation_confirms_exact_position(monkeypatch, tmp_path):
    monkeypatch.setenv("TRADINGBOT_TEST_STATE_DIR", str(tmp_path))
    import decision_analytics as analytics
    import etoro_reconciliation as rec

    monkeypatch.setattr(analytics, "DB_PATH", tmp_path / "decisions.sqlite")
    monkeypatch.setattr(rec, "_notify", lambda *_a, **_k: None)
    analytics.record({"decision_id": 90701, "symbol": "CRM", "broker": "etoro",
                      "asset_type": "stock", "status": "APPROVED", "paper": True})
    rec.start_intent(
        decision_id=90701, symbol="CRM", paper=True, profile="balanced",
        quantity=4, price=261, stop=250, take_profit=275,
        account_fingerprint="account-crm")
    rec.accepted(90701, order_id="o-crm", reference_id="r-crm")

    class Broker:
        @staticmethod
        def account_fingerprint(): return "account-crm"
        def _lookup_order(self, **_kwargs):
            return {
                "orderId": "o-crm", "referenceId": "r-crm",
                "status": {"id": 3, "name": "Filled"},
                "positionExecutions": [{"positionId": "p-crm", "openingData": {
                    "units": 4, "avgPrice": 261, "executionTime": "2026-08-28T16:20:20Z"}}],
            }
        def current_position_ids(self, **_kwargs): return {"p-crm"}
        def trade_history(self, *_args, **_kwargs): return []

    changed = rec.background_tick(Broker(), paper=True, profile="balanced")
    assert any(x.get("state") == "FILLED" for x in changed)
    assert rec.status()["active"] == []


def test_late_exact_fill_promotes_observe_without_doubling(tmp_path):
    from position_manager import PositionManager

    class Contract:
        conId = "p-nvda"
        symbol = localSymbol = "NVDA.US"
        currency = "USD"

    manager = PositionManager(tmp_path / "positions.json")
    manager.upsert_existing_position(Contract(), 6, 0, "USD", "stock")
    record = manager.register_buy(
        Contract(), 6, 226.5, "USD", "stock", 100_000, 220, 240,
        source="BOT", management_mode="AUTO")
    assert record.quantity == 6
    assert record.avg_cost == pytest.approx(226.5)
    assert record.source == "BOT" and record.management_mode == "AUTO"


def test_okx_uses_private_account_instruments_for_trade_quote_list():
    from broker.okx import OKXClient

    client = OKXClient("key", "secret", "phrase", demo=True)
    calls = []
    client.request = lambda method, path, **kwargs: calls.append(
        (method, path, kwargs)) or [{
            "instId": "ADA-USD", "baseCcy": "ADA", "quoteCcy": "USD",
            "state": "live", "tickSz": "0.0001", "lotSz": "1", "minSz": "1",
            "tradeQuoteCcyList": ["EUR", "USDC"],
        }]
    instruments = client.instruments(force=True)
    assert calls == [("GET", "/account/instruments", {
        "params": {"instType": "SPOT"}, "private": True})]
    assert instruments["ADA-USD"].trade_quote_ccy_list == ("EUR", "USDC")


def test_okx_cross_rate_is_observed_not_stablecoin_guess():
    from broker.okx import OKXBroker, OKXTicker
    import time

    class Client:
        hat_zugangsdaten = True
        def tickers(self):
            return {
                "BTC-USD": OKXTicker("BTC-USD", last=60_000, timestamp_ms=int(time.time()*1000)),
                "BTC-EUR": OKXTicker("BTC-EUR", last=55_200, timestamp_ms=int(time.time()*1000)),
            }

    broker = OKXBroker(client=Client(), allowed_quotes=("EUR", "USDC"))
    assert broker.quote_conversion_rate("USD", "EUR") == pytest.approx(0.92)
    assert broker.quote_conversion_rate("USD", "USDC") is None


def test_spot_surplus_is_account_asset_not_unknown_exposure():
    import exposure_klassifizierung as exposure

    result = exposure.klassifiziere(
        {"ETH": {"gesamt": 1.096348, "cash": 1.096348}},
        positionsbuch=[{"symbol": "ETH", "menge": 0.1,
                        "herkunft": "BOT", "verwaltung": "AUTO"}],
        preise={"ETH": 4000})
    classes = [x["klasse"] for x in result["bestaende"]]
    assert classes == [exposure.BOT_MANAGED, exposure.ACCOUNT_ASSET]
    assert not result["einstiege_gesperrt"]


def test_default_okx_cash_currencies_are_separate_eur_usd_usdc_lanes():
    import config

    assert config.OKX_PRIMARY_QUOTE_CCY == "EUR"
    assert config.OKX_ALLOWED_QUOTE_CCY == ("EUR", "USDC")  # New entries; existing USD trades stay bound.
    assert "USDT" not in config.OKX_ALLOWED_QUOTE_CCY
