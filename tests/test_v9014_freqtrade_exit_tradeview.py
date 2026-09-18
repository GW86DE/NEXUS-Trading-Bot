from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest


def test_doge_protection_is_anchored_to_real_fill_not_foreign_quote():
    from crypto_engine import fill_anchored_stop_take
    from freqtrade_sample_strategy import net_profit_ratio

    fill = 0.07552981586888308
    stop, take = fill_anchored_stop_take(
        strategy_mode="FREQTRADE_SAMPLE", fill_price=fill,
        filled_quantity=28267.9, entry_fee_quote=7.472742,
        exit_fee_pct=0.0035,
        # Absichtlich fremde/ungeeignete Planwerte: sie duerfen den
        # Freqtrade-Fillschutz nicht beeinflussen.
        signal_price=0.0851, planned_stop=0.01, planned_take=0.0851)

    assert stop == pytest.approx(fill * 0.90)
    assert stop < fill < take
    entry_fee_pct = 7.472742 / (fill * 28267.9)
    assert net_profit_ratio(
        entry_price=fill, current_price=take,
        entry_fee_pct=entry_fee_pct, exit_fee_pct=0.0035) == pytest.approx(0.04)


def test_link_after_three_hours_needs_fee_adjusted_one_percent():
    from freqtrade_sample_strategy import net_profit_ratio, roi_exit_price, roi_threshold

    entry = 11.429
    assert roi_threshold(180) == pytest.approx(0.01)
    assert net_profit_ratio(
        entry_price=entry, current_price=11.48,
        entry_fee_pct=0.0035, exit_fee_pct=0.0035) < 0
    assert roi_exit_price(
        entry, 0.01, entry_fee_pct=0.0035,
        exit_fee_pct=0.0035) == pytest.approx(11.6243768339)


def test_trade_view_uses_exact_inst_id_full_size_vwap_and_active_roi(monkeypatch):
    import trade_chart_data as chart

    opened = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
    row = {
        "trade_id": 42, "broker": "okx", "symbol": "LINK",
        "paper": True,
        "broker_position_id": "LINK-USDC", "waehrung": "USDC",
        "menge": 10.0, "einstieg_preis": 11.429,
        "einstieg_gebuehr": 0.40, "eingestiegen_am": opened,
        "entry_strategy_mode": "FREQTRADE_SAMPLE", "ausgestiegen_am": None,
    }

    class Client:
        def tickers(self):
            return {"LINK-USDC": SimpleNamespace(
                bid=11.40, timestamp_ms=int(datetime.now().timestamp() * 1000))}

        def orderbook(self, instrument, depth=100):
            assert instrument == "LINK-USDC"
            return {"bids": [(11.50, 5.0), (11.40, 10.0)], "asks": [],
                    "timestamp_ms": int(datetime.now().timestamp() * 1000)}

    monkeypatch.setattr(chart, "_CLIENT_FACTORY", lambda: Client())
    monkeypatch.setattr(chart, "_position_lines", lambda *_a, **_k: {
        "inst_id": "LINK-USDC", "stop": 10.2861, "take_profit": 11.939,
        "trade_quote_ccy": "USDC", "verwaltung": "AUTO"})
    chart._LIVE_CACHE.clear()
    result = chart.live_metrics_for_trades([row])[0]

    assert result["instrument"] == "LINK-USDC"
    assert result["market_quote_ccy"] == "USDC"
    assert result["settlement_currency"] == "USDC"
    assert result["current_executable"] is True
    assert result["current_price"] == pytest.approx(11.45)
    assert result["active_roi_pct"] == pytest.approx(1.0)
    assert result["active_roi_price"] > 11.60


def test_unreadable_okx_protection_is_pending_not_missing():
    from broker.base import BrokerFehler
    from broker.okx import OKXBroker, OKXInstrument

    class Client:
        def instrument(self, _inst):
            return OKXInstrument("LINK-USDC", "LINK", "USDC", "live",
                                 "0.001", "0.001", "0.1")

        def pending_algo_orders(self, *_a, **_k):
            raise BrokerFehler("kurzer API-Fehler")

    broker = OKXBroker(client=Client(), quote_ccy="USDC")
    state = broker.reconcile_position_protection(
        SimpleNamespace(contract=SimpleNamespace(localSymbol="LINK-USDC")),
        10.0, 10.0, 12.0)
    assert state["checked"] is False
    assert state["protection_confirmed"] is False


def test_periodic_protection_status_is_mirrored_to_trade_ledger(monkeypatch, tmp_path):
    import decision_analytics
    import trade_ledger
    from crypto_engine import CryptoEngine, KryptoPosition

    monkeypatch.setattr(decision_analytics, "DB_PATH", tmp_path / "ledger.sqlite")
    trade_id = trade_ledger.trade_open(
        broker="okx", symbol="LINK", menge=9.0, einstieg_preis=11.429,
        broker_position_id="LINK-USDC", entry_order_id="order-1",
        entry_fill_id="fill-1", decision_id=14,
        reconciliation_status="CONFIRMED_OPEN")
    position = KryptoPosition(
        symbol="LINK", inst_id="LINK-USDC", menge=9.0, einstieg=11.429,
        stop=10.2861, take_profit=11.94, protection_algo_id="algo-14",
        protection_client_order_id="protect-14", protection_status="ACTIVE")
    CryptoEngine._ledger_schutz_synchronisieren(position, "bei OKX bestaetigt")

    stored = trade_ledger.offener_trade("okx", "LINK")
    assert stored["trade_id"] == trade_id
    assert stored["protection_status"] == "ACTIVE"
    assert stored["protection_algo_id"] == "algo-14"


def test_risk_day_uses_configured_local_timezone(monkeypatch):
    import risk_manager
    real_datetime = datetime

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            instant = real_datetime(2026, 8, 30, 22, 30, tzinfo=timezone.utc)
            return instant.astimezone(tz) if tz else instant.replace(tzinfo=None)

    monkeypatch.setattr(risk_manager, "datetime", FixedDateTime)
    monkeypatch.setattr(risk_manager.config, "LOCAL_TIMEZONE", "Europe/Berlin")
    assert risk_manager._handelstag_heute().isoformat() == "2026-08-31"
