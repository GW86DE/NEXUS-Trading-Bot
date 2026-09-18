from datetime import datetime, timezone
from types import SimpleNamespace

import manual_trade_control as controls
import crypto_engine as ce


def proven_position(**overrides):
    data = dict(
        symbol="LINK", inst_id="LINK-USDC", menge=9.08808, einstieg=11.429,
        stop=10.26, take_profit=11.939, order_id="entry-order",
        client_order_id="entry-client", fill_ids=["entry-fill"],
        ownership_verified=True, protection_algo_id="algo-1",
        protection_client_order_id="protect-client", protection_status="ACTIVE",
        broker_schutz=True, entry_strategy_mode="FREQTRADE_SAMPLE",
        strategy_name="SampleStrategy", strategy_version="v",
        strategy_parameter_hash="h", strategy_parameters={},
        eroeffnet_am=datetime.now(timezone.utc).isoformat())
    data.update(overrides)
    return ce.KryptoPosition(**data)


def test_manual_mode_disables_strategy_exit_but_keeps_hard_protection():
    p = proven_position()
    p.manuell("webui:test", "TP/SL geaendert")
    assert p.verwaltung == ce.VERWALTUNG_MANUELL
    assert p.darf_automatisch_verkaufen is False
    assert p.darf_schutz_ausfuehren is True


def test_lock_is_base_asset_and_survives_quote_change(monkeypatch, tmp_path):
    monkeypatch.setenv("TRADINGBOT_TEST_STATE_DIR", str(tmp_path))
    controls.add_lock(broker="okx", account_fingerprint="acct", symbol="ONDO",
                      duration="6H", reason="manual sell", actor="tester")
    assert "Wiedereinstiegssperre" in controls.lock_reason("okx", "acct", "ONDO")
    assert controls.lock_reason("okx", "other", "ONDO") == ""


def test_webui_command_requires_exact_confirmed_owned_trade(monkeypatch, tmp_path):
    import decision_analytics
    import trade_ledger
    monkeypatch.setenv("TRADINGBOT_TEST_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(decision_analytics, "DB_PATH", tmp_path / "ledger.sqlite")
    trade_id = trade_ledger.trade_open(
        broker="okx", symbol="LINK", menge=9.0, einstieg_preis=11.429,
        decision_id=15,
        broker_position_id="LINK-USDC", entry_order_id="entry-order",
        entry_fill_id="entry-fill", entry_fill_ids=["entry-fill"],
        client_order_id="entry-client", ownership_status="VERIFIED",
        broker_account_fingerprint="acct", reconciliation_status="CONFIRMED_OPEN")
    with __import__("pytest").raises(ValueError):
        controls.request(trade_id, "SELL", confirmation="SELL BTC")
    command = controls.request(trade_id, "SELL", confirmation="SELL LINK",
                               lock="6H", actor="webui:test")
    assert command["instrument"] == "LINK-USDC"
    assert command["account_fingerprint"] == "acct"
    assert len(controls.pending()) == 1


def test_stale_processing_command_becomes_unclear_not_retried(monkeypatch, tmp_path):
    import json
    from datetime import timedelta
    monkeypatch.setenv("TRADINGBOT_TEST_STATE_DIR", str(tmp_path))
    old = (datetime.now(timezone.utc) - timedelta(minutes=3)).isoformat()
    (tmp_path / controls.DATEI).write_text(json.dumps({"commands": [{
        "id": "x", "status": "PROCESSING", "created_at": old,
        "updated_at": old}]}), encoding="utf-8")
    assert controls.pending() == []
    assert controls.overview()["commands"][0]["status"] == "UNCLEAR"


def test_okx_manual_protection_is_amended_and_read_back():
    from broker.okx import OKXBroker, OKXInstrument

    class Client:
        def __init__(self):
            self.changed = False
        def instrument(self, _inst):
            return OKXInstrument("LINK-USDC", "LINK", "USDC", "live",
                                 "0.001", "0.001", "0.1")
        def pending_algo_orders(self, _inst="", ord_type="oco"):
            if ord_type != "oco": return []
            return [{"algoId": "algo-1", "algoClOrdId": "protect-client",
                     "instId": "LINK-USDC", "sz": "9.000",
                     "slTriggerPx": "10.500" if self.changed else "10.260",
                     "tpTriggerPx": "12.200" if self.changed else "11.939"}]
        def pending_orders(self, *_a, **_k): return []
        def amend_algo_order(self, body):
            assert body["algoId"] == "algo-1"
            assert body["newSlTriggerPx"] == "10.5"
            assert body["newTpTriggerPx"] == "12.2"
            self.changed = True
            return {"algoId": "algo-1", "sCode": "0"}

    client = Client()
    broker = OKXBroker(client=client, quote_ccy="USDC")
    broker._account_fingerprint = "fixture-test_v9015_manual_exit_reconciliation"  # Exakte Testkontobindung
    instrument = SimpleNamespace(
        name="LINK", contract=SimpleNamespace(localSymbol="LINK-USDC"))
    state = broker.amend_position_protection(
        instrument, 9.0, 10.5, 12.2, algo_id="algo-1", trade_quote_ccy="USDC")
    assert state["protection_confirmed"] is True
    assert state["stop"] == 10.5
    assert state["take_profit"] == 12.2


def test_external_sell_evidence_closes_already_persisted_v9014_dust(monkeypatch, tmp_path):
    monkeypatch.setenv("TRADINGBOT_TEST_STATE_DIR", str(tmp_path))
    p = proven_position(symbol="ONDO", inst_id="ONDO-USDC", menge=0.002515,
                        einstieg=0.352, entry_fee_quote=0.33)
    book = ce.KryptoPositionsbuch(tmp_path / "crypto_positions.json")
    book.setze(p)
    engine = object.__new__(ce.CryptoEngine)
    engine.buch = book
    engine._gemeldeter_ueberhang = set()
    engine.melder = None
    engine.hub = SimpleNamespace(broker=lambda _name: SimpleNamespace(
        kontowaehrung=lambda: "USDC", quote_conversion_rate=lambda a, b: 1.0))
    engine.risiko = SimpleNamespace(topf=lambda _name: SimpleNamespace(
        buche_ergebnis=lambda *a, **k: None,
        setze_offene_positionen=lambda *a, **k: None))
    import trade_ledger
    from risk_pots import RiskPotManager
    engine.risiko = RiskPotManager(["okx"])
    trade_ledger.trade_open(broker="okx",symbol=p.symbol,menge=269.762515,
        einstieg_preis=p.einstieg,gebuehr=.33,waehrung="USDC",asset_type="crypto",
        paper=p.paper,decision_id=1,entry_order_id=p.order_id,
        client_order_id=p.client_order_id,entry_fill_ids=p.fill_ids,
        broker_position_id=p.inst_id,broker_account_fingerprint=p.account_fingerprint,
        ownership_status="VERIFIED",critical=True)
    evidence = {"quantity": 269.76, "avg_price": 0.3535,
                "fees_quote": 0.3337, "fill_ids": ["sell-fill"],
                "order_ids": ["sell-order"], "closed_at": datetime.now(timezone.utc).isoformat()}
    report = {}
    engine._position_extern_geschlossen(
        p, evidence, report, residual=0.002515, close_quantity=269.762515)
    assert book.hole("ONDO") is None
    assert report["geschlossen"] == ["ONDO"]


def test_exit_failure_gets_persistent_retry_cooldown(monkeypatch, tmp_path):
    monkeypatch.setenv("TRADINGBOT_TEST_STATE_DIR", str(tmp_path))
    p = proven_position()
    book = ce.KryptoPositionsbuch(tmp_path / "crypto_positions.json")
    book.setze(p)
    engine = object.__new__(ce.CryptoEngine)
    engine.buch = book
    engine.cfg = SimpleNamespace(OKX_EXIT_RETRY_MINUTES=5,
                                 OKX_EXIT_ALERT_COOLDOWN_SECONDS=900)
    engine.melder = None
    engine._exit_fehlversuch(p, "FOK nicht erfuellt")
    saved = book.hole("LINK")
    assert saved.exit_state == "RETRY_WAIT"
    assert saved.exit_retry_after
    first_notice = saved.exit_last_notice_at
    engine._exit_fehlversuch(saved, "FOK nicht erfuellt")
    assert book.hole("LINK").exit_last_notice_at == first_notice


def test_okx_exit_waits_for_balance_release_before_sending(monkeypatch):
    """Ein OKX-Storno darf nicht sofort als freies Guthaben gelten (LINK-Fall)."""
    from broker.base import OrderErgebnis
    from broker.okx import OKXBroker, OKXInstrument
    from okx_test_band import DemoWithoutPriceBand

    class Client(DemoWithoutPriceBand):
        def __init__(self):
            self.balance_reads = 0
            self.sent = []

        def instrument(self, _inst):
            return OKXInstrument("LINK-USDC", "LINK", "USDC", "live",
                                 "0.001", "0.001", "0.1")

        def pending_orders(self, *_args, **_kwargs):
            return []

        def balances(self):
            self.balance_reads += 1
            # OKX meldet das Storno bereits, gibt die reservierte Menge aber
            # erst mit kurzer Verzoegerung wieder als verfuegbar aus.
            cash = 0.0 if self.balance_reads < 3 else 9.088
            return {"LINK": {"cash": cash}}

        def place_order(self, body):
            self.sent.append(dict(body))
            assert self.balance_reads >= 3
            return {"ordId": "exit-1"}

    client = Client()
    broker = OKXBroker(client=client, quote_ccy="USDC")
    broker._account_fingerprint = "fixture-test_v9015_manual_exit_reconciliation"  # Exakte Testkontobindung
    instrument = SimpleNamespace(
        name="LINK", contract=SimpleNamespace(localSymbol="LINK-USDC"))
    quote = {"timestamp_ms": int(__import__('time').time()*1000),
             "slippage_pct": 0.001, "best": 11.56, "worst": 11.55,
             "vwap": 11.555}
    monkeypatch.setattr(broker, "execution_quote", lambda *_a, **_k: dict(quote))
    monkeypatch.setattr(broker, "storniere_offene_orders", lambda *_a, **_k: None)
    monkeypatch.setattr(broker, "offene_schutzorders", lambda *_a, **_k: [])
    monkeypatch.setattr(broker, "_warte_auf_fill", lambda *_a, **_k: {
        "ordId": "exit-1", "state": "filled", "accFillSz": "9.088",
        "avgPx": "11.55"})
    monkeypatch.setattr(broker, "_order_result_from_evidence", lambda **_k:
                        OrderErgebnis(status="filled", filled_quantity=9.088,
                                      avg_fill_price=11.55, terminal=True))
    monkeypatch.setattr("broker.okx.time.sleep", lambda _seconds: None)

    result = broker.schliesse_position(instrument, 9.088, 11.429)

    assert result.filled_quantity == 9.088
    assert client.balance_reads == 3
    assert len(client.sent) == 1
    assert client.sent[0]["ordType"] == "ioc"
