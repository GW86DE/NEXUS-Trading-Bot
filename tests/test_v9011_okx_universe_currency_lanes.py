"""Regressionen fuer das breite, kontoseitig ausfuehrbare OKX-Universum."""
from __future__ import annotations

from types import SimpleNamespace

import pytest


def _cfg():
    return SimpleNamespace(
        OKX_QUOTE_CCY="EUR",
        OKX_ALLOWED_QUOTE_CCY=("EUR", "USD", "USDC"),
        OKX_REQUIRE_FUNDED_TRADE_QUOTE=True,
        CRYPTO_UNIVERSE_MIN_AGE_DAYS=30.0,
        CRYPTO_UNIVERSE_MIN_QUOTE_VOLUME=250_000.0,
        CRYPTO_UNIVERSE_MAX_SPREAD_PCT=0.012,
        CRYPTO_UNIVERSE_PRESELECTION=100,
        CRYPTO_UNIVERSE_BLOCKLIST=(),
        CRYPTO_ESTABLISHED_MIN_AGE_DAYS=365.0,
        CRYPTO_ESTABLISHED_MIN_QUOTE_VOLUME=50_000_000.0,
        CRYPTO_ESTABLISHED_MAX_SPREAD_PCT=0.0015,
        CRYPTO_CORE_SYMBOLS=("SOL",),
    )


def test_public_catalog_and_private_account_catalog_stay_separate():
    from broker.okx import OKXClient

    client = OKXClient("key", "secret", "phrase", demo=True)
    calls = []

    def request(method, path, **kwargs):
        calls.append((path, bool(kwargs.get("private"))))
        if path == "/public/instruments":
            return [{"instId": "PUBLIC-USDT", "baseCcy": "PUBLIC",
                     "quoteCcy": "USDT", "state": "live"}]
        return [{"instId": "SOL-USD", "baseCcy": "SOL", "quoteCcy": "USD",
                 "state": "live", "tradeQuoteCcyList": ["USD"]}]

    client.request = request
    assert set(client.public_instruments(force=True)) == {"PUBLIC-USDT"}
    assert set(client.instruments(force=True)) == {"SOL-USD"}
    assert calls == [("/public/instruments", False), ("/account/instruments", True)]


def test_usd_pair_is_eligible_only_with_funded_usd_lane():
    from broker.okx import OKXInstrument, OKXTicker
    from universe.crypto_selector import CryptoUniverseSelector

    selector = CryptoUniverseSelector(SimpleNamespace(), cfg=_cfg())
    selector._base_age_days = {"SOL": 1000.0}
    selector._quote_cash = {"EUR": 1000.0, "USD": 5000.0, "USDC": 0.0}
    selector._funded_quotes = {"EUR", "USD"}
    meta = OKXInstrument(
        "SOL-USD", "SOL", "USD", "live", "0.01", "0.001", "0.01",
        trade_quote_ccy_list=("USD",))
    ticker = OKXTicker(
        "SOL-USD", last=100.0, bid=99.95, ask=100.05,
        vol_24h_quote=5_000_000.0)

    assert selector._trade_lane(meta) == "USD"
    assert selector._harter_filter(meta, ticker, quote_rate=0.92) == ""

    selector._funded_quotes = {"EUR"}
    reason = selector._harter_filter(meta, ticker, quote_rate=0.92)
    assert "kein finanzierter Handelskanal" in reason


def test_usdt_stays_disabled_even_if_balance_exists():
    import config
    from broker.okx import OKXBroker, OKXInstrument
    from broker.base import BrokerFehler

    meta = OKXInstrument(
        "DOGE-USDT", "DOGE", "USDT", "live", "0.00001", "1", "1",
        trade_quote_ccy_list=("USDT",))
    client = SimpleNamespace(
        hat_zugangsdaten=True,
        instrument=lambda _iid: meta,
        balances=lambda: {"USDT": {"cash": 10_000.0}},
    )
    broker = OKXBroker(client=client, allowed_quotes=config.OKX_ALLOWED_QUOTE_CCY)
    instrument = SimpleNamespace(
        name="DOGE", contract=SimpleNamespace(localSymbol="DOGE-USDT"))
    with pytest.raises(BrokerFehler, match="tradeQuoteCcy"):
        broker.trade_quote_for_instrument(instrument, require_cash=True)


def test_position_size_uses_only_selected_currency_lane(tmp_path):
    from risk_pots import RiskPot, TopfGrenzen

    pot = RiskPot(
        "okx",
        grenzen=TopfGrenzen(
            risiko_pro_trade_pct=0.01, max_position_pct=0.10,
            max_offene_positionen=6, max_tagesverlust_pct=0.03,
            max_trades_pro_tag=20),
        state_datei=str(tmp_path / "risk.json"))
    pot.setze_kontowert(71_000.0)
    global_qty, _ = pot.positionsgroesse(100.0, 90.0)
    usd_qty, _ = pot.positionsgroesse(
        100.0, 90.0, kontowert_override=5_000.0)

    assert global_qty == pytest.approx(71.0)
    assert usd_qty == pytest.approx(5.0)
    assert usd_qty < global_qty


def test_diagnose_exposes_discovery_account_and_lanes():
    import universe_diagnose

    report = universe_diagnose.bericht({
        "broker": "okx", "katalog_gesamt": 53,
        "public_catalog_gesamt": 581, "account_catalog_gesamt": 53,
        "public_only_gesamt": 528,
        "trade_lanes": {"EUR": {"free": 1000, "funded": True},
                        "USD": {"free": 5000, "funded": True}},
        "pool": 20, "abgelehnt": [], "rangliste": [],
    })
    assert report["public_catalog_gesamt"] == 581
    assert report["account_catalog_gesamt"] == 53
    assert report["trade_lanes"]["USD"]["funded"] is True
    assert "OKX meldet 53" in universe_diagnose.textbericht(report)
