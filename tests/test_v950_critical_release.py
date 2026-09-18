"""Geldpfad- und Ressourcenregressionen fuer NEXUS 9.5."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
import threading

import pandas as pd
import pytest


def test_etoro_existing_position_stays_paused_until_exact_protection(tmp_path):
    from position_manager import PositionManager, PositionRecord

    manager = PositionManager(tmp_path / "positions.json")
    key = "etoro:acct-a:demo:7001"
    manager.records[key] = PositionRecord(
        con_id=0, symbol="MSFT", asset_type="stock", currency="USD",
        quantity=1, avg_cost=500, entry_time="2026-08-31T20:00:00+00:00",
        source="BROKER_EXISTING", management_mode="OBSERVE",
        broker_position_ids=["7001"], observed_position_ids=["7001"],
        broker_account_fingerprint="acct-a", broker_environment="DEMO",
        ownership_status="EXTERNAL")
    contract = SimpleNamespace(conId=0, localSymbol="MSFT", symbol="MSFT")

    record = manager.register_buy(
        contract, 1, 500, "USD", "stock", 10_000, 490, 550,
        source="BOT", management_mode="PENDING_CONFIRMATION",
        position_ids=["7001"], order_ids=["order-1"],
        account_fingerprint="acct-a", broker_environment="DEMO",
        fill_id="etoro:acct-a:open:order-1:7001:fill-1")

    assert record.ownership_status == "VERIFIED"
    assert record.owned_position_id_set() == {"7001"}
    assert record.management_mode == "PENDING_CONFIRMATION"
    assert manager.set_bot_protection(key, False, "TP fehlt")
    assert manager.records[key].management_mode == "PENDING_CONFIRMATION"
    assert manager.set_bot_protection(key, True, "SL und TP exakt")
    assert manager.records[key].management_mode == "AUTO"


def test_etoro_position_id_never_crosses_account_boundary(tmp_path):
    from position_manager import PositionManager, PositionRecord

    manager = PositionManager(tmp_path / "positions.json")
    manager.records["legacy"] = PositionRecord(
        con_id=0, symbol="SPGI", asset_type="stock", currency="USD",
        quantity=1, avg_cost=430, entry_time="2026-08-31T20:00:00+00:00",
        broker_position_ids=["8001"], observed_position_ids=["8001"],
        broker_account_fingerprint="", broker_environment="DEMO")
    manager.records["account-a"] = PositionRecord(
        con_id=0, symbol="SPGI", asset_type="stock", currency="USD",
        quantity=1, avg_cost=430, entry_time="2026-08-31T20:00:00+00:00",
        broker_position_ids=["8002"], observed_position_ids=["8002"],
        broker_account_fingerprint="acct-a", broker_environment="DEMO")

    assert manager.get_by_position_id("8001", "acct-b") is None
    assert manager.get_by_position_id("8002", "acct-b") is None
    assert manager.get_by_position_id("8002", "acct-a") is not None


def test_etoro_recovery_rejects_accountless_evidence_for_active_account(
        monkeypatch, tmp_path):
    import etoro_reconciliation as rec

    monkeypatch.setenv("TRADINGBOT_TEST_STATE_DIR", str(tmp_path))
    rec.start_intent(
        decision_id=95001, symbol="MSFT", paper=True, profile="",
        quantity=1, price=500, stop=490, take_profit=550,
        account_fingerprint="")
    rec.apply_broker_evidence(95001, {
        "orderId": "o-legacy", "status": {"id": 3, "name": "Filled"},
        "positionExecutions": [{"positionId": "p-legacy", "openingData": {
            "units": 1, "avgPrice": 500, "executionTime": "2026-08-31T20:00:00Z"}}],
    })
    broker = SimpleNamespace(
        current_position_ids=lambda **_k: {"p-legacy"},
        trade_history=lambda *_a, **_k: [])

    assert rec.verify_broker_truth(
        broker, paper=True, profile="", account_fingerprint="acct-live",
        current_position_ids={"p-legacy"}) == []
    assert rec.recovered_buy_fills(
        paper=True, current_position_ids={"p-legacy"},
        account_fingerprint="acct-live") == []


def test_etoro_first_history_poll_covers_old_open_bot_trade(monkeypatch):
    import config
    import trade_ledger
    from broker.etoro import EtoroBroker

    broker = EtoroBroker(paper=True, api_key="api", user_key="account-a")
    account = broker.account_fingerprint()
    opened = datetime.now(timezone.utc) - timedelta(days=93)
    monkeypatch.setattr(trade_ledger, "offene_trades", lambda _broker="": [
        {"eingestiegen_am": opened.isoformat(),
         "broker_account_fingerprint": account},
        {"eingestiegen_am": (opened - timedelta(days=100)).isoformat(),
         "broker_account_fingerprint": "other-account"},
    ])
    monkeypatch.setattr(config, "ETORO_HISTORY_LOOKBACK_DAYS", 7)
    monkeypatch.setattr(config, "ETORO_HISTORY_RECOVERY_MAX_DAYS", 365)

    first, first_pages = broker._history_window()
    assert date.fromisoformat(first) <= opened.date()
    assert first_pages == config.ETORO_HISTORY_RECOVERY_MAX_PAGES
    broker._history_recovery_complete = True
    normal, normal_pages = broker._history_window()
    assert date.fromisoformat(normal) == date.today() - timedelta(days=7)
    assert normal_pages == config.ETORO_HISTORY_MAX_PAGES


def test_etoro_partial_close_poll_is_idempotent(monkeypatch, tmp_path):
    import broker_exit_journal as journal

    monkeypatch.setenv("TRADINGBOT_TEST_STATE_DIR", str(tmp_path))
    intent, created = journal.begin(
        broker="etoro", account_fingerprint="acct", environment="DEMO",
        instrument_id="1001", position_id="7001", quantity=10,
        client_order_id="request-1")
    assert created
    # A fill may only complete an order already bound by its submission ACK.
    # Matching the position alone is insufficient (PEP regression in 10.1.3).
    journal.update(intent["intent_id"], "SUBMITTED", broker_order_id="close-1")
    for _ in range(2):
        assert journal.confirm_from_fill(
            broker="etoro", account_fingerprint="acct", position_id="7001",
            broker_order_id="close-1", filled_quantity=4,
            fill_identity="fill-a") == intent["intent_id"]
    active = journal.active(broker="etoro", account_fingerprint="acct")
    assert active[0]["status"] == "PARTIALLY_FILLED"
    assert active[0]["filled_quantity"] == pytest.approx(4)
    journal.confirm_from_fill(
        broker="etoro", account_fingerprint="acct", position_id="7001",
        broker_order_id="close-1", filled_quantity=6,
        fill_identity="fill-b")
    assert journal.active(broker="etoro", account_fingerprint="acct") == []


def test_etoro_close_without_order_id_is_never_reposted(monkeypatch):
    from broker.base import OrderStatusUnklar
    from broker.etoro import EtoroBroker

    broker = EtoroBroker(paper=True, api_key="api", user_key="account-a")
    broker._connected = True
    broker._bind_account_identity({"demoCid": 21577959})
    broker._resolve = lambda _instrument: {"instrumentId": 1001, "symbol": "MSFT"}
    broker._pnl = lambda force=False: {
        "_snapshot_id": "snapshot-1",
        "clientPortfolio": {"positions": [{
            "positionId": "7001", "instrumentId": 1001,
            "units": 1, "isBuy": True}]}}
    posts = []

    def request(method, path, **_kwargs):
        posts.append((method, path))
        return {"accepted": True}  # absichtlich noch keine orderId

    broker._request = request
    instrument = SimpleNamespace(name="MSFT")
    with pytest.raises(OrderStatusUnklar):
        broker.schliesse_position(
            instrument, 1, 500, position_ids=["7001"], instrument_id="1001")
    with pytest.raises(OrderStatusUnklar, match="bereits aktiv"):
        broker.schliesse_position(
            instrument, 1, 500, position_ids=["7001"], instrument_id="1001")
    assert len(posts) == 1


def test_okx_same_raw_trade_id_for_link_and_ondo_is_not_duplicate(tmp_path):
    from broker.base import Fill
    from broker.okx import okx_fill_identity
    from fill_tracker import FillProgressTracker

    link_row = {"tradeId": "1", "ordId": "link-order", "instId": "LINK-USDC"}
    ondo_row = {"tradeId": "1", "ordId": "ondo-order", "instId": "ONDO-USDC"}
    link_id = okx_fill_identity(link_row, "acct")
    ondo_id = okx_fill_identity(ondo_row, "acct")
    assert link_id != ondo_id
    tracker = FillProgressTracker(tmp_path / "fills.json")
    accepted = []
    for fid, symbol, oid in ((link_id, "LINK", "link-order"),
                             (ondo_id, "ONDO", "ondo-order")):
        fill = Fill(fid, oid, symbol, "BUY", 1, 1,
                    raw_fill_id="1", account_fingerprint="acct")
        prepared, token = tracker.prepare(fill)
        assert prepared is not None
        tracker.commit(token)
        accepted.append(prepared.symbol)
    assert accepted == ["LINK", "ONDO"]


def test_ondo_legacy_terminal_entry_is_recovered_only_by_exact_order(
        monkeypatch):
    import crypto_engine
    from broker.base import OrderErgebnis

    updates = {}

    class Registry:
        def registered_orders(self, **_kwargs):
            return {"3877327800382468097": {
                "broker": "okx", "asset_type": "crypto", "role": "ENTRY",
                "identity_schema": 1, "zustand": "CANCELED", "symbol": "ONDO",
                "account_fingerprint": "acct", "decision_id": 95,
                "qty": 269.762515, "signal_price": 0.352,
                "stop": 0.32, "take": 0.38, "trade_quote_ccy": "USDC"}}
        def update_order(self, order_id, **fields):
            updates[str(order_id)] = fields
            return True

    class Book:
        def hole(self, _symbol): return None

    class Broker:
        def account_fingerprint(self): return "acct"
        def reconcile_order_evidence(self, intent):
            assert intent["ord_id"] == "3877327800382468097"
            return OrderErgebnis(
                order_ids=[intent["ord_id"]], status="filled", terminal=True,
                requested_quantity=269.762515, filled_quantity=269.762515,
                gross_filled_quantity=269.762515, avg_fill_price=0.352,
                fill_ids=["okx:acct:ONDO-USDC:3877327800382468097:1"],
                fill_evidence_complete=True, reference_id="N9ONDO", paper=True)

    position = SimpleNamespace(
        client_order_id="N9ONDO", einstieg=0.352, menge=269.762515,
        paper=True, order_id="3877327800382468097")
    engine = crypto_engine.CryptoEngine.__new__(crypto_engine.CryptoEngine)
    engine.buch = Book()
    engine._order_registry = lambda: Registry()
    engine._persistiere_okx_entry = lambda **_kwargs: (position, 501)
    engine._melde = lambda *_a, **_k: None
    monkeypatch.setattr("trade_ledger.offene_trades", lambda _broker="": [])
    monkeypatch.setattr(crypto_engine, "mark_execution", lambda *_a, **_k: None)
    monkeypatch.setattr(crypto_engine, "record_order_result", lambda *_a, **_k: None)

    repaired = engine._recover_terminal_okx_entries(Broker())
    assert repaired == [{
        "symbol": "ONDO", "order_id": "3877327800382468097",
        "trade_id": 501, "status": "FILLED_RECOVERED"}]
    assert updates["3877327800382468097"]["v950_recovery_result"] == "FILLED_RECOVERED"


def test_confirmed_okx_exit_with_failed_risk_booking_stays_pending(
        monkeypatch):
    import crypto_engine

    removed = []
    stored = []
    engine = crypto_engine.CryptoEngine.__new__(crypto_engine.CryptoEngine)
    engine.buch = SimpleNamespace(
        setze=lambda position: stored.append(position.exit_state),
        entferne=lambda symbol: removed.append(symbol), aktive=lambda: [])
    engine._gemeldeter_ueberhang = set()
    engine._melde = lambda *_a, **_k: None
    engine._buche_okx_ergebnis = lambda *_a, **_k: False
    engine.risiko = SimpleNamespace(topf=lambda _name: SimpleNamespace(
        setze_offene_positionen=lambda _value: None))
    engine.hub = SimpleNamespace(broker=lambda _name: SimpleNamespace())
    position = crypto_engine.KryptoPosition(
        symbol="LINK", inst_id="LINK-USDC", menge=10, einstieg=10,
        stop=9, take_profit=11, order_id="buy-link", client_order_id="N9LINK",
        fill_ids=["entry-fill"], ownership_verified=True,
        account_fingerprint="acct")
    monkeypatch.setattr("trade_ledger.trade_close", lambda **_kwargs: 77)

    engine._position_extern_geschlossen(position, {
        "quantity": 10, "avg_price": 11, "fees_quote": 0.1,
        "fill_ids": ["sell-fill"], "order_ids": ["sell-order"],
        "closed_at": datetime.now(timezone.utc).isoformat()}, {})
    assert position.exit_state == "ACCOUNTING_PENDING"
    assert stored[-1] == "ACCOUNTING_PENDING"
    assert removed == []


def test_freqtrade_roi_is_checked_before_candle_history(monkeypatch):
    import crypto_engine
    import freqtrade_sample_strategy as sample
    from broker.base import BrokerFehler
    from crypto_strategy_mode import FREQTRADE_SAMPLE

    calls = {"history": 0}

    class Broker:
        def gebuehrensatz(self): return 0.0035
        def historie(self, *_args, **_kwargs):
            calls["history"] += 1
            raise BrokerFehler("Historie nicht erreichbar")
        def account_fingerprint(self): return "acct"

    broker = Broker()
    engine = crypto_engine.CryptoEngine.__new__(crypto_engine.CryptoEngine)
    engine.hub = SimpleNamespace(broker=lambda _name: broker)
    engine.cfg = SimpleNamespace(
        FREQTRADE_HISTORY_DURATION="3 D", OKX_TAKER_FEE_PCT=0.0035)
    engine.buch = SimpleNamespace(setze=lambda _position: None)
    engine._instrument = lambda *_args: object()
    position = crypto_engine.KryptoPosition(
        symbol="LINK", inst_id="LINK-USDC", menge=10, einstieg=100,
        stop=90, take_profit=104,
        eroeffnet_am=(datetime.now(timezone.utc) - timedelta(minutes=65)).isoformat(),
        entry_strategy_mode=FREQTRADE_SAMPLE,
        strategy_version=sample.STRATEGY_VERSION,
        strategy_parameter_hash=sample.PARAMETER_HASH,
        strategy_parameters=sample.parameter_snapshot())

    close, reason = engine._strategy_exit(position, 102.0)
    assert close and reason == "freqtrade_roi_1pct"
    assert calls["history"] == 0


def test_fmp_reference_and_news_share_one_hard_daily_budget(
        monkeypatch, tmp_path):
    import config
    import live_settings
    from fmp_reference import FMPReferenz
    from news_sources import MultiSourceNews
    from persistent_daily_budget import PersistentDailyBudget

    monkeypatch.setenv("TRADINGBOT_TEST_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(config, "FMP_AUTOMATIC_DAILY_LIMIT", 1)
    monkeypatch.setattr(live_settings, "fmp_key", lambda: "key")
    monkeypatch.setattr(live_settings, "fmp_tageslimit", lambda: 250)

    class Response:
        status_code = 200
        ok = True
        content = b"[]"
        reason = "OK"
        def json(self): return []

    class Session:
        def __init__(self): self.headers = {}; self.calls = 0
        def get(self, *_args, **_kwargs):
            self.calls += 1
            return Response()

    reference = FMPReferenz(api_key="key", tageslimit=250)
    reference.session = Session()
    news = MultiSourceNews()
    news.session = Session()
    reference._get("/profile", {"symbol": "AAPL"}, purpose="automatic")
    with pytest.raises(RuntimeError, match="Automatikreserve"):
        news._fmp_get("/quote", {"symbol": "AAPL"}, purpose="automatic")
    assert reference.session.calls == 1 and news.session.calls == 0
    status = reference.budget.als_dict()
    assert status["verbraucht"] == 1 and status["automatic_used"] == 1


def test_fmp_budget_is_atomic_across_threads(tmp_path):
    from persistent_daily_budget import PersistentDailyBudget

    path = tmp_path / "budgets.json"
    barrier = threading.Barrier(12)
    accepted = []

    def reserve():
        budget = PersistentDailyBudget(
            "fmp", limit=5, automatic_limit=5, path=path)
        barrier.wait()
        accepted.append(budget.reserve(purpose="automatic")[0])

    threads = [threading.Thread(target=reserve) for _ in range(12)]
    for thread in threads: thread.start()
    for thread in threads: thread.join()
    assert accepted.count(True) == 5


def test_stock_reference_network_is_disabled_while_exchange_is_closed(
        monkeypatch, tmp_path):
    from stock_universe_runner import StockUniverseRunner
    from universe.manager import UniverseManager
    from universe.modelle import UniverseZustand
    import market_calendar
    import universe.stock_selector as selector_module

    observed = []

    class Selector:
        def __init__(self, *args, **kwargs): pass
        def auswahl(self, **kwargs):
            observed.append(kwargs["erlaube_referenz_abruf"])
            return {"broker": "etoro", "asset_type": "stock",
                    "katalog_gesamt": 0, "pool": 0, "rangliste": []}

    monkeypatch.setattr(market_calendar, "darf_arbeiten",
                        lambda **_kwargs: (False, "Handelsbeginn in 295 Minuten"))
    monkeypatch.setattr(selector_module, "StockUniverseSelector", Selector)
    manager = UniverseManager(UniverseZustand(tmp_path / "universe.json"))
    runner = StockUniverseRunner(manager, melder=lambda *_a, **_k: None)
    result = runner.lauf()
    assert result["ok"] is True
    assert observed == [False]


def test_crypto_analysis_is_public_fee_aware_and_chronological(monkeypatch):
    import config
    import crypto_analysis

    client = crypto_analysis._public_client()
    assert client.hat_zugangsdaten is False
    assert client._key == client._secret == client._passphrase == ""

    index = pd.date_range("2026-01-01", periods=1800, freq="5min", tz="UTC")
    values = pd.Series(range(1800), index=index, dtype=float) / 100 + 100
    frame = pd.DataFrame({
        "open": values, "high": values + 0.2, "low": values - 0.2,
        "close": values + 0.05, "volume": 1000.0}, index=index)
    folds = crypto_analysis._folds(frame, 4)
    assert len(folds) == 4
    previous_end = None
    for test_start, test_end, test in folds:
        assert test.index[-1] == frame.index[test_end - 1]
        assert test.index[0] == frame.index[test_start - 200]
        if previous_end is not None:
            assert test_start == previous_end
        previous_end = test_end

    monkeypatch.setattr(config, "OKX_TAKER_FEE_PCT", 0.0035)
    monkeypatch.setattr(config, "CRYPTO_SLIPPAGE_PCT", 0.0015)
    result = crypto_analysis._run_sample(frame.iloc[:400])
    assert result["assumptions"]["fee_pct"] == pytest.approx(0.0035)
    assert result["assumptions"]["slippage_pct"] == pytest.approx(0.0015)
    assert result["assumptions"]["completed_5m_only"] is True
