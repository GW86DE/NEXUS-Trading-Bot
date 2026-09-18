from __future__ import annotations

import time
from types import SimpleNamespace

import pytest


from okx_test_band import DemoWithoutPriceBand


class ExecutionClient(DemoWithoutPriceBand):
    hat_zugangsdaten = True
    clock_offset_seconds = 0.0

    def __init__(self):
        from broker.okx import OKXInstrument
        self.meta = OKXInstrument("DOGE-EUR", "DOGE", "EUR", "live",
                                  "0.00001", "0.1", "1")
        self.orders = []
        self.algos = []
        self.book_ts = int(time.time() * 1000)

    def instrument(self, _inst): return self.meta
    def pending_orders(self, *_a, **_k): return []
    def pending_algo_orders(self, *_a, **_k): return []
    def balances(self):
        return {"EUR": {"cash": 10000, "gesamt": 10000},
                "DOGE": {"cash": 1000, "gesamt": 1000}}
    def orderbook(self, _inst, depth=100):
        return {"timestamp_ms": self.book_ts,
                "asks": [(0.07345, 600), (0.07350, 600)],
                "bids": [(0.07340, 600), (0.07335, 600)]}
    def place_order(self, body):
        self.orders.append(dict(body))
        return {"ordId": f"o{len(self.orders)}", "clOrdId": body.get("clOrdId")}
    def order_status(self, *_a, **kw):
        oid = str(kw.get("ord_id") or f"o{len(self.orders)}")
        side = self.orders[-1]["side"]
        return {"ordId": oid, "state": "filled", "accFillSz": "1000",
                "avgPx": "0.073475" if side == "buy" else "0.073375"}
    def fills(self, *_a, **_k):
        side = self.orders[-1]["side"]
        oid = f"o{len(self.orders)}"
        return [{"tradeId": f"f{len(self.orders)}", "ordId": oid,
                 "instId": "DOGE-EUR", "side": side, "fillSz": "1000",
                 "fillPx": "0.073475" if side == "buy" else "0.073375",
                 "fee": "0", "feeCcy": "EUR", "ts": "1"}]
    def fills_history_paginated(self, *_a, **_k): return []
    def place_algo_order(self, body):
        self.algos.append(dict(body)); return {"algoId": "algo-doge"}
    def cancel_algo_orders(self, _rows): return []
    def cancel_order(self, *_a, **_k): return {}


def instrument():
    return SimpleNamespace(name="DOGE", asset_type="crypto",
                           contract=SimpleNamespace(localSymbol="DOGE-EUR"))


def test_entry_is_full_size_price_capped_fok_and_protection_is_deferred():
    from broker.okx import OKXBroker
    client = ExecutionClient()
    broker = OKXBroker(client=client, quote_ccy="EUR")
    broker._account_fingerprint = "fixture-test_v9013_okx_execution_identity"  # Exakte Testkontobindung
    result = broker.kaufe_mit_absicherung(instrument(), 1000, 0.07345, 0.07, 0.08)
    assert client.orders[0]["ordType"] == "fok"
    # 9.5.4: Frueher stand hier "0.0735" -- der exakt gemessene schlechteste
    # Brief, aufgerundet um einen Tick. Diese Zusicherung hat den Fehler
    # festgeschrieben, an dem am 02.09.2026 ETH, SOL und DOGE als FOK mit 0
    # gefuellt storniert wurden. Jetzt liegt das Limit um den gedeckelten
    # Preispuffer darueber: quantize_up(0.07350 * 1.0015) = 0.07362.
    assert client.orders[0]["px"] == "0.07362"
    assert client.orders[0]["sz"] == "1000"
    assert client.algos == []
    assert result.fill_evidence_complete is True


def test_stale_or_incomplete_book_blocks_before_any_order():
    from broker.base import BrokerFehler
    from broker.okx import OKXBroker
    client = ExecutionClient(); client.book_ts -= 10_000
    broker = OKXBroker(client=client, quote_ccy="EUR")
    broker._account_fingerprint = "fixture-test_v9013_okx_execution_identity"  # Exakte Testkontobindung
    with pytest.raises(BrokerFehler, match="nicht frisch"):
        broker.kaufe_mit_absicherung(instrument(), 1000, 0.07345, 0.07, 0.08)
    assert client.orders == []


def test_protection_has_deterministic_identity_and_no_market_exit_price():
    from broker.okx import OKXBroker
    client = ExecutionClient()
    broker = OKXBroker(client=client, quote_ccy="EUR")
    broker._account_fingerprint = "fixture-test_v9013_okx_execution_identity"  # Exakte Testkontobindung
    state = broker.reconcile_position_protection(
        instrument(), 1000, 0.07, 0.08, protection_client_id="PN9DOGE")
    body = client.algos[0]
    assert state["algo_id"] == "algo-doge"
    assert body["algoClOrdId"] == "PN9DOGE"
    assert body["tpOrdPx"] == "0.08"
    assert body["slOrdPx"] == "0.0693"
    assert body["tpOrdPx"] != "-1" and body["slOrdPx"] != "-1"


def test_fill_identity_is_scoped_by_account_instrument_and_order(monkeypatch, tmp_path):
    import decision_analytics
    import trade_ledger
    monkeypatch.setenv("TRADINGBOT_TEST_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(decision_analytics, "DB_PATH", tmp_path / "decisions.sqlite")
    common = dict(broker="okx", menge=1, einstieg_preis=10, asset_type="crypto",
                  decision_id=1, entry_fill_id="1", entry_fill_ids=["1"],
                  broker_account_fingerprint="same-account")
    first = trade_ledger.trade_open(symbol="LINK", broker_position_id="LINK-USD",
                                    entry_order_id="ord-link", **common)
    second = trade_ledger.trade_open(symbol="ONDO", broker_position_id="ONDO-USD",
                                     entry_order_id="ord-ondo", **{**common, "decision_id": 2})
    assert first and second and first != second
    assert {r["symbol"] for r in trade_ledger.offene_trades("okx")} == {"LINK", "ONDO"}


def test_dust_with_old_ledger_reference_does_not_block_entries():
    import exposure_klassifizierung as ek
    result = ek.klassifiziere(
        {"DOGE": {"gesamt": 0.06235, "cash": 0.06235}},
        positionsbuch=[], offene_orders=[],
        ledger_trades=[{"symbol": "DOGE", "menge": 0.06235}],
        preise={"DOGE": 0.073})
    assert result["einstiege_gesperrt"] is False
    assert result["nach_klasse"][ek.ACCOUNT_ASSET][0]["waehrung"] == "DOGE"


def test_one_transient_rest_failure_is_degraded_not_disconnected(monkeypatch):
    from broker.okx import OKXBroker
    client = ExecutionClient()
    client.server_time_ms = lambda: 1
    client.last_contact = lambda: None
    calls = {"n": 0}
    def account_config():
        calls["n"] += 1
        if calls["n"] == 1:
            raise TimeoutError("kurzer Timeout")
        return {"uid": "u"}
    client.account_config = account_config
    broker = OKXBroker(client=client, quote_ccy="EUR")
    broker._account_fingerprint = "fixture-test_v9013_okx_execution_identity"  # Exakte Testkontobindung
    broker._connected = True
    assert broker.health_check(force=True) is True
    assert broker.connection_components()["overall"] == "DEGRADED"
    assert broker.is_connected() is True
    assert broker.health_check(force=True) is True
    assert broker.connection_components()["overall"] == "ONLINE"
