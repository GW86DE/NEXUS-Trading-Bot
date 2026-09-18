"""Gezielte Regressionen fuer die Stabilitaetskorrekturen in NEXUS 9.0.2."""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest


def test_new_buy_rejects_plain_usd_without_usdc_settlement_permission():
    import config
    from broker.okx import OKXBroker, OKXInstrument

    meta = OKXInstrument("TRX-USD", "TRX", "USD", "live", "0.0001", "1", "1")

    class Client:
        hat_zugangsdaten = True
        def instrument(self, inst_id):
            return meta if inst_id == "TRX-USD" else None

    broker = OKXBroker(client=Client(), allowed_quotes=config.OKX_ALLOWED_QUOTE_CCY)
    broker._account_fingerprint = "fixture-test_v902_stability_fixes"  # Exakte Testkontobindung
    instrument = SimpleNamespace(
        asset_type="crypto", name="TRX",
        contract=SimpleNamespace(localSymbol="TRX-USD"))
    assert config.OKX_ALLOWED_QUOTE_CCY == ("EUR", "USDC")
    ok, reason = broker.instrument_handelbar(instrument)
    assert not ok and reason


def test_okx_orders_carry_the_selected_usd_trade_quote_currency():
    from broker.okx import OKXBroker, OKXInstrument
    from okx_test_band import DemoWithoutPriceBand

    meta = OKXInstrument("LTC-USD", "LTC", "USD", "live", "0.01", "0.001", "0.01")

    class Client(DemoWithoutPriceBand):
        hat_zugangsdaten = True
        def __init__(self):
            self.orders = []
            self.algos = []
        def instrument(self, inst_id): return meta
        def place_order(self, body):
            self.orders.append(dict(body)); return {"ordId": str(len(self.orders))}
        def order_status(self, *_args, **_kwargs):
            # The second order is the SELL, not a replay of BUY order 1.
            body = self.orders[-1]
            return {"ordId": str(len(self.orders)), "state": "filled",
                    "clOrdId": body["clOrdId"], "side": body["side"],
                    "instId": body["instId"], "accFillSz": "1.25", "avgPx": "80"}
        def fills(self, *_args, **_kwargs):
            return [{"tradeId": "f"+str(len(self.orders)), "ordId": str(len(self.orders)),
                     "instId": "LTC-USD", "side": self.orders[-1]["side"],
                     "fillSz": "1.25", "fillPx": "80", "fee": "0",
                     "feeCcy": "USD", "ts": "1"}]
        def fills_history_paginated(self, *_args, **_kwargs): return []
        def place_algo_order(self, body):
            self.algos.append(dict(body)); return {"algoId": "a1"}
        def pending_orders(self, *_args, **_kwargs): return []
        def pending_algo_orders(self, *_args, **_kwargs): return []
        def orderbook(self, *_args, **_kwargs):
            import time
            return {"timestamp_ms": int(time.time() * 1000),
                    "asks": [(80, 10)], "bids": [(81, 10)]}
        def balances(self): return {"LTC": {"cash": 1.25, "gesamt": 1.25},
                                    "USD": {"cash": 5000, "gesamt": 5000}}

    client = Client()
    broker = OKXBroker(client=client, quote_ccy="EUR",
                       allowed_quotes=("EUR", "USD", "USDC"))
    broker._account_fingerprint = "fixture-test_v902_stability_fixes"  # Exakte Testkontobindung
    instrument = SimpleNamespace(
        asset_type="crypto", name="LTC", currency="USD",
        contract=SimpleNamespace(localSymbol="LTC-USD"))
    bought = broker.kaufe_mit_absicherung(instrument, 1.25, 80, 75, 90)
    assert bought.filled_quantity == pytest.approx(1.25)
    assert client.orders[0]["instId"] == "LTC-USD"
    assert client.orders[0]["tradeQuoteCcy"] == "USD"
    assert client.orders[0]["ordType"] == "fok"
    broker.reconcile_position_protection(instrument, 1.25, 75, 90,
                                         protection_client_id="PLTC")
    # 9.8.3: OKX now documents tradeQuoteCcy for SPOT algo orders too.
    # Source: https://my.okx.com/docs-v5/en/#order-book-trading-algo-trading-post-place-algo-order
    assert client.algos[0]["tradeQuoteCcy"] == "USD"
    assert client.algos[0]["instId"].endswith("-USD"), (
        "die Algo-Order verkauft in der Quote des instId")

    # A SELL may begin only after the BUY is durably accounted.
    import trade_ledger as ledger
    import execution_lifecycle as life
    ledger.trade_open(broker='okx', symbol='LTC', menge=1.25, einstieg_preis=80,
        gebuehr=0, external=True, paper=True, waehrung='USD', critical=True,
        broker_position_id='LTC-USD', broker_account_fingerprint=broker.account_fingerprint(),
        entry_order_id='1', client_order_id=bought.client_order_id, entry_fill_ids=bought.fill_ids)
    life.confirm_accounted(broker='okx', account=broker.account_fingerprint(),
        environment='DEMO', order_id='1', client_id=bought.client_order_id)
    sold = broker.schliesse_position(instrument, 1.25, 81)
    assert bought.order_ids == ["1"] and sold.order_ids == ["2"]
    assert sold.fill_evidence_complete and sold.filled_quantity == pytest.approx(1.25)
    assert client.orders[-1]["side"] == "sell"
    assert client.orders[-1]["tradeQuoteCcy"] == "USD"


def test_quote_conversion_uses_observed_usd_eur_rate(monkeypatch, tmp_path):
    monkeypatch.setenv("TRADINGBOT_TEST_STATE_DIR", str(tmp_path))
    import crypto_dynamic_30
    from broker.okx import OKXInstrument, OKXTicker
    from universe.crypto_selector import CryptoUniverseSelector

    monkeypatch.setattr(crypto_dynamic_30, "_notify", lambda *_a, **_k: None)
    listed = int((time.time() - 500 * 86400) * 1000)
    stamp = int(time.time() * 1000)
    instruments = {
        "TRX-USD": OKXInstrument(
            "TRX-USD", "TRX", "USD", "live", "0.0001", "1", "1",
            list_time_ms=listed, trade_quote_ccy_list=("EUR", "USDC")),
        "USD-EUR": OKXInstrument("USD-EUR", "USD", "EUR", "live", "0.0001", "1", "1", list_time_ms=listed),
    }
    tickers = {
        "TRX-USD": OKXTicker("TRX-USD", .3, .299, .301, 1_000_000, 2_000_000, .28, timestamp_ms=stamp),
        "USD-EUR": OKXTicker("USD-EUR", .92, .919, .921, 1_000_000, 2_000_000, .91, timestamp_ms=stamp),
    }

    class Cfg:
        OKX_QUOTE_CCY = "EUR"
        OKX_ALLOWED_QUOTE_CCY = ("EUR", "USDC")
        CRYPTO_UNIVERSE_MIN_AGE_DAYS = 30
        CRYPTO_UNIVERSE_MIN_QUOTE_VOLUME = 250_000
        CRYPTO_UNIVERSE_MAX_SPREAD_PCT = .012
        CRYPTO_UNIVERSE_PRESELECTION = 100
        CRYPTO_UNIVERSE_BLOCKLIST = ()
        CRYPTO_ESTABLISHED_MIN_AGE_DAYS = 365
        CRYPTO_ESTABLISHED_MIN_QUOTE_VOLUME = 50_000_000
        CRYPTO_ESTABLISHED_MAX_SPREAD_PCT = .0015
        CRYPTO_CORE_SYMBOLS = ("TRX",)

    class Client:
        def instruments(self): return instruments
        def tickers(self): return tickers

    pool, _ = CryptoUniverseSelector(Client(), cfg=Cfg).eligible_pool()
    trx = next(x for x in pool if x.symbol == "TRX")
    assert trx.inst_id == "TRX-USD"


def test_ledger_only_balance_is_residual_and_blocks_new_entries():
    import exposure_klassifizierung as ek
    result = ek.klassifiziere(
        {"BTC": {"gesamt": .25, "cash": .25}, "USD": {"gesamt": 5000}},
        positionsbuch=[], offene_orders=[],
        ledger_trades=[{"symbol": "BTC", "menge": .3}],
        preise={"BTC": 60_000})
    btc = next(x for x in result["bestaende"] if x["waehrung"] == "BTC")
    usd = next(x for x in result["bestaende"] if x["waehrung"] == "USD")
    assert btc["klasse"] == ek.RESIDUAL and btc["sperrt_einstiege"]
    assert usd["klasse"] == ek.CASH
    assert result["einstiege_gesperrt"]


def test_orphaned_ledger_trade_stays_open_without_sell_receipt(monkeypatch):
    import crypto_engine
    import trade_ledger

    engine = crypto_engine.CryptoEngine.__new__(crypto_engine.CryptoEngine)
    engine.buch = SimpleNamespace(alle=lambda: [])
    engine._fehlender_ledger_bestand = {}
    engine._melde = lambda *_a, **_k: None
    rows = [{"trade_id": 7, "symbol": "BTC", "menge": .1}]
    closed = []
    monkeypatch.setattr(trade_ledger, "offene_trades", lambda _broker="": list(rows))
    monkeypatch.setattr(
        trade_ledger, "mark_reconciled_closed",
        lambda trade_id, **_kwargs: closed.append(trade_id) is None or True)

    first = engine._offene_ledger_abgleichen({})
    assert first["geschlossen"] == [] and closed == []
    second = engine._offene_ledger_abgleichen({})
    assert second["geschlossen"] == [] and closed == []


def test_reconciled_close_leaves_price_and_pnl_unknown(monkeypatch, tmp_path):
    import decision_analytics
    import trade_ledger
    monkeypatch.setattr(decision_analytics, "DB_PATH", tmp_path / "ledger.sqlite")
    trade_id = trade_ledger.trade_open(
        broker="okx", symbol="SOL", menge=2, einstieg_preis=100,
        decision_id=123, asset_type="crypto")
    assert trade_ledger.mark_reconciled_closed(
        trade_id, grund="Brokerbestand nicht mehr vorhanden")
    row = trade_ledger.trade_detail(trade_id)
    assert row["ausgestiegen_am"]
    assert row["ausstieg_preis"] is None and row["netto_pnl"] is None


def test_telegram_claim_prevents_parallel_double_send(monkeypatch, tmp_path):
    import config
    import notifier
    monkeypatch.setenv("TRADINGBOT_TEST_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(config, "NOTIFY_TELEGRAM", True)
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setattr(config, "TELEGRAM_CHAT_ID", "1")
    monkeypatch.setattr(config, "TELEGRAM_COALESCE_SECONDS", 0.0)
    calls = []

    def slow_send(*_args, **_kwargs):
        calls.append(1)
        time.sleep(.05)
        return True, "ok", 0.0

    monkeypatch.setattr(notifier, "_send_one", slow_send)
    notifier._enqueue("identische Ausfuehrung", "critical")
    results = []
    workers = [threading.Thread(
        target=lambda: results.append(notifier.flush_telegram_queue(1))) for _ in range(2)]
    for worker in workers: worker.start()
    for worker in workers: worker.join()
    assert sum(results) == 1
    assert len(calls) == 1


def test_telegram_exact_message_is_coalesced_for_30_seconds(monkeypatch, tmp_path):
    import config
    import notifier
    monkeypatch.setenv("TRADINGBOT_TEST_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(config, "NOTIFY_TELEGRAM", True)
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setattr(config, "TELEGRAM_CHAT_ID", "1")
    monkeypatch.setattr(config, "TELEGRAM_MIN_INTERVAL_SECONDS", 0.0)
    monkeypatch.setattr(notifier, "_send_one", lambda *_a, **_k: (True, "ok", 0.0))
    # GEAENDERT IN v9.1: Der Rueckgabewert sagt jetzt die Wahrheit. Eine
    # Nachricht im 30-Sekunden-Sammelfenster ist EINGEREIHT, nicht
    # fehlgeschlagen -- der Worker stellt sie zu. Vorher lieferte
    # send_telegram dafuer False und fuehrte Aufrufer in die Irre, die den
    # Wert auswerten. Das Sammelfenster selbst bleibt unveraendert: es
    # verhindert, dass derselbe Text in Sekundenabstand mehrfach ankommt.
    assert notifier.send_telegram("gleicher Text", priority="critical"), \
        "Eingereiht ist nicht fehlgeschlagen"
    assert notifier.send_telegram("gleicher Text", priority="critical"), \
        "Auch das erkannte Duplikat wird zugestellt"
    state = json.loads((Path(tmp_path) / config.TELEGRAM_QUEUE_FILE).read_text())
    assert len(state["queue"]) == 1
    assert state["queue"][0]["not_before"] - state["queue"][0]["created_at"] == pytest.approx(30.0)


@pytest.mark.parametrize("symbol,stop,fill_price", [
    ("UNH", 394.832, 394.77),
    ("AMD", 471.228, 471.22),
])
def test_etoro_owned_close_price_match_does_not_invent_broker_stop(symbol, stop, fill_price):
    from live_trader import _etoro_exit_attribution
    broker = SimpleNamespace(name="eToro", paper=True, account_fingerprint=lambda: "acct")
    fill = SimpleNamespace(execution_reason="", symbol=symbol, broker_id="position-1")
    rec = SimpleNamespace(management_mode="AUTO", source="BOT",
                          owned_position_ids=["position-1"], broker_account_fingerprint="acct",
                          broker_environment="DEMO",
                          planned_stop=stop, planned_take=stop * 1.05)
    owned, meta = _etoro_exit_attribution(
        broker=broker, fill=fill, rec_before=rec, meta={},
        sell_bot_owned=False, price=fill_price)
    assert owned
    assert meta["label"] == "BROKER-VERKAUF"
    assert not meta["protective_exit_attributed"]
    assert meta["protective_price_match"] == "STOP-LOSS"
    assert meta["exit_cause_evidence"] == "UNPROVEN"


def test_unmatched_etoro_close_does_not_invent_manual_execution():
    from live_trader import _etoro_exit_attribution
    broker = SimpleNamespace(name="eToro")
    fill = SimpleNamespace(execution_reason="")
    rec = SimpleNamespace(management_mode="AUTO", source="BOT",
                          planned_stop=90, planned_take=110)
    owned, meta = _etoro_exit_attribution(
        broker=broker, fill=fill, rec_before=rec, meta={},
        sell_bot_owned=False, price=100)
    assert not owned
    assert meta["label"] == "BROKER-VERKAUF"
    assert meta["exit_cause_evidence"] == "UNPROVEN"


def test_partial_sell_preserves_entry_strategy_snapshot(monkeypatch, tmp_path):
    import decision_analytics
    import trade_ledger
    monkeypatch.setattr(decision_analytics, "DB_PATH", tmp_path / "partial.sqlite")
    trade_ledger.trade_open(
        broker="okx", symbol="ETH", menge=2, einstieg_preis=100,
        decision_id=42, entry_strategy_mode="FREQTRADE_SAMPLE",
        strategy_parameter_hash="abc", strategy_parameters={"roi": .04})
    trade_ledger.trade_close(
        broker="okx", symbol="ETH", ausstieg_preis=105, menge=1,
        exit_grund="roi")
    rest = trade_ledger.offener_trade("okx", "ETH")
    assert rest["entry_strategy_mode"] == "FREQTRADE_SAMPLE"
    assert rest["strategy_parameter_hash"] == "abc"
    assert json.loads(rest["strategy_parameters_json"])["roi"] == .04
