"""Regressionen fuer NEXUS 9.0.1: 20+30-Universum und Trade-Seite."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pandas as pd


@dataclass
class Meta:
    base_ccy: str
    quote_ccy: str = "EUR"
    state: str = "live"
    inst_type: str = "SPOT"


@dataclass
class VolumeTicker:
    vol_24h_quote: float


def _dynamic_markets(first: int = 0, count: int = 40):
    inst = {f"C{i:02d}-EUR": Meta(f"C{i:02d}") for i in range(first, first + count)}
    tick = {name: VolumeTicker(1_000_000 - i * 1000) for i, name in enumerate(inst)}
    return inst, tick


def test_fixed_core_is_exactly_20_and_total_target_is_50():
    import config
    assert len(config.CRYPTO_CORE_SYMBOLS) == 20
    assert len(set(config.CRYPTO_CORE_SYMBOLS)) == 20
    assert config.CRYPTO_CORE_LIMIT == 20
    assert config.CRYPTO_UNIVERSE_DYNAMIC_LIMIT == 30
    assert config.CRYPTO_UNIVERSE_ACTIVE_LIMIT == 50
    assert not (set(config.CRYPTO_CORE_SYMBOLS) & {"USDC", "USDT", "EUR"})
    # Aufnahmefilter fuer Beobachtung sind weiter als der echte Geldpfad.
    assert config.CRYPTO_UNIVERSE_MAX_SPREAD_PCT > config.MAX_SPREAD_CRYPTO_PCT


def test_dynamic_bootstrap_persists_exactly_30_and_survives_restart(monkeypatch, tmp_path):
    monkeypatch.setenv("TRADINGBOT_TEST_STATE_DIR", str(tmp_path))
    import crypto_dynamic_30 as dynamic
    monkeypatch.setattr(dynamic, "_notify", lambda *_args, **_kwargs: None)
    inst, tick = _dynamic_markets(count=40)
    now = datetime(2026, 8, 27, 12, tzinfo=timezone.utc)
    state = dynamic.collect_and_update(inst, tick, now=now)
    assert state["status"] == "CURRENT" and len(state["items"]) == 30
    assert [x["base"] for x in state["items"][:3]] == ["C00", "C01", "C02"]
    assert dynamic.current_bases() == {f"C{i:02d}" for i in range(30)}
    # Ein erneuter Lauf am selben Tag sammelt nicht doppelt und rotiert nicht.
    again = dynamic.collect_and_update(inst, tick, now=now + timedelta(minutes=15))
    assert not again["ran"] and dynamic.current_bases() == {f"C{i:02d}" for i in range(30)}


def test_monthly_rotation_uses_own_30_day_history_and_keeps_old_if_incomplete(monkeypatch, tmp_path):
    monkeypatch.setenv("TRADINGBOT_TEST_STATE_DIR", str(tmp_path))
    import crypto_dynamic_30 as dynamic
    from safe_persistence import atomic_write_json
    monkeypatch.setattr(dynamic, "_notify", lambda *_args, **_kwargs: None)
    now = datetime(2026, 10, 1, 4, tzinfo=timezone.utc)
    old = [{"base": f"OLD{i:02d}", "pair": f"OLD{i:02d}-EUR"} for i in range(30)]
    atomic_write_json(dynamic.state_path(), {"schema": 1, "status": "CURRENT", "items": old})
    samples = []
    for day in range(20):
        measured = now - timedelta(days=day)
        items = [{"base": f"NEW{i:02d}", "pair": f"NEW{i:02d}-EUR", "quote": "EUR",
                  "measured_at_utc": measured.isoformat(),
                  "volume_24h_normalized_eur": 2_000_000 - i * 1000}
                 for i in range(35)]
        samples.append({"measured_at_utc": measured.isoformat(), "items": items})
    atomic_write_json(dynamic.history_path(), {"schema": 1, "samples": samples})
    state = dynamic.monthly_update(now=now, min_days=20)
    assert state["status"] == "CURRENT" and len(state["items"]) == 30
    assert {x["base"] for x in state["items"]} == {f"NEW{i:02d}" for i in range(30)}

    # Unvollstaendige neue Historie darf die belegte Mitgliedschaft nicht ersetzen.
    atomic_write_json(dynamic.history_path(), {"schema": 1, "samples": samples[:2]})
    stale = dynamic.monthly_update(now=now + timedelta(days=31), min_days=20)
    assert stale["status"] == "STALE"
    assert {x["base"] for x in stale["items"]} == {f"NEW{i:02d}" for i in range(30)}


def test_selector_prefers_eur_before_quantity_cost_comparison(monkeypatch, tmp_path):
    monkeypatch.setenv("TRADINGBOT_TEST_STATE_DIR", str(tmp_path))
    import time
    import crypto_dynamic_30 as dynamic
    from broker.okx import OKXInstrument, OKXTicker
    from universe.crypto_selector import CryptoUniverseSelector
    monkeypatch.setattr(dynamic, "_notify", lambda *_args, **_kwargs: None)
    listed = int((time.time() - 500 * 86400) * 1000)
    instruments = {
        "ZZZ-EUR": OKXInstrument("ZZZ-EUR", "ZZZ", "EUR", "live", "0.01", "0.01", "0.1", list_time_ms=listed),
        "ZZZ-USDC": OKXInstrument("ZZZ-USDC", "ZZZ", "USDC", "live", "0.01", "0.01", "0.1", list_time_ms=listed),
        "USDC-EUR": OKXInstrument("USDC-EUR", "USDC", "EUR", "live", "0.0001", "0.01", "1", list_time_ms=listed),
    }
    stamp = int(time.time() * 1000)
    tickers = {
        "ZZZ-EUR": OKXTicker("ZZZ-EUR", 10, 9.99, 10.01, 1000, 300_000, 9.5, timestamp_ms=stamp),
        "ZZZ-USDC": OKXTicker("ZZZ-USDC", 11, 10.99, 11.01, 1000, 2_000_000, 10.5, timestamp_ms=stamp),
        "USDC-EUR": OKXTicker("USDC-EUR", .9, .8999, .9001, 1000, 1_000_000, .9, timestamp_ms=stamp),
    }
    class Client:
        def instruments(self): return instruments
        def tickers(self): return tickers
    pool, _ = CryptoUniverseSelector(Client()).eligible_pool()
    zzz = next(x for x in pool if x.symbol == "ZZZ")
    assert zzz.inst_id == "ZZZ-EUR"
    assert zzz.zusatz["origin"] == "FIXED_CORE_20"


def test_all_fixed_core_remain_visible_but_are_blocked_without_current_market(tmp_path):
    import config
    from universe.manager import UniverseManager
    from universe.modelle import UniverseZustand
    manager = UniverseManager(UniverseZustand(tmp_path / "universe.json"))
    manager.lauf({"broker": "okx", "rangliste": [], "eligible_symbols": [],
                  "ineligible_reasons": {x: "kein live Spot-Paar" for x in config.CRYPTO_CORE_SYMBOLS}})
    members = manager.zustand.fuer_broker("okx")
    assert len(members) == 20
    assert all(m.kern_blockiert and not m.handelbar for m in members)


def test_historical_candles_use_public_history_endpoint_and_drop_open_candle(monkeypatch):
    from broker.okx import OKXClient
    client = OKXClient()
    calls = []
    def request(method, path, **kwargs):
        calls.append((method, path, kwargs))
        return [
            ["1000", "1", "2", ".5", "1.5", "9", "0", "13", "1"],
            ["2000", "1.5", "2.5", "1", "2", "10", "0", "18", "0"],
        ]
    monkeypatch.setattr(client, "request", request)
    frame = client.historical_candles("btc-eur", bar="15m", limit=200, end_ms=9999)
    assert len(frame) == 1
    method, path, kwargs = calls[0]
    assert method == "GET" and path == "/market/history-candles"
    assert kwargs["params"] == {"instId": "BTC-EUR", "bar": "15m", "limit": 100, "after": 9999}
    assert "private" not in kwargs


def test_trade_analysis_never_turns_unknown_open_pnl_into_zero(monkeypatch, tmp_path):
    import decision_analytics
    import trade_ledger
    from webui.state import trade_analysis
    monkeypatch.setattr(decision_analytics, "DB_PATH", tmp_path / "trades.sqlite")
    trade_id = trade_ledger.trade_open(
        broker="okx", symbol="BTC", menge=1, einstieg_preis=100,
        waehrung="EUR", decision_id=123, paper=True,
        entry_strategy_mode="FREQTRADE_SAMPLE", zeit=datetime.now(timezone.utc))
    assert trade_id
    data = trade_analysis(broker="okx", tage=30)
    assert data["kennzahlen"]["offen"] == 1
    assert data["kennzahlen"]["offenes_ergebnis"] is None
    assert data["offene_trades"][0]["entry_strategy_mode"] == "FREQTRADE_SAMPLE"


def test_chart_contains_actual_buy_and_sell_markers(monkeypatch, tmp_path):
    import decision_analytics
    import trade_chart_data
    import trade_ledger
    monkeypatch.setattr(decision_analytics, "DB_PATH", tmp_path / "chart.sqlite")
    entry = datetime.now(timezone.utc) - timedelta(hours=2)
    trade_id = trade_ledger.trade_open(
        broker="okx", symbol="ETH", menge=2, einstieg_preis=100,
        waehrung="EUR", decision_id=456, paper=True, zeit=entry, broker_position_id="ETH-EUR",
        entry_strategy_mode="NEXUS_AUSGEWOGEN", strategie_version="9.0.1")
    trade_ledger.trade_close(broker="okx", symbol="ETH", ausstieg_preis=105, menge=2,
                             zeit=entry + timedelta(hours=1), exit_grund="roi")
    index = pd.date_range(entry - timedelta(hours=1), periods=20, freq="15min", tz="UTC")
    frame = pd.DataFrame({"open": 100.0, "high": 106.0, "low": 99.0,
                          "close": 103.0, "volume": 10.0, "quote_volume": 1030.0}, index=index)
    class Client:
        def historical_candles(self, *_args, **_kwargs): return frame
    monkeypatch.setattr(trade_chart_data, "_CLIENT_FACTORY", lambda: Client())
    trade_chart_data._CACHE.clear()
    out = trade_chart_data.chart_for_trade(trade_id, force=True)
    assert out["ok"] and out["nur_abgeschlossene_kerzen"]
    assert out["kauf"]["preis"] == 100 and out["verkauf"]["preis"] == 105
    assert out["entry_strategy_mode"] == "NEXUS_AUSGEWOGEN"


def test_trade_page_is_german_responsive_and_only_queues_local_reconciliation():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    html = (root / "webui/templates/trades.html").read_text(encoding="utf-8")
    js = (root / "webui/static/trades.js").read_text(encoding="utf-8")
    css = (root / "webui/static/app.css").read_text(encoding="utf-8")
    app = (root / "webui/app.py").read_text(encoding="utf-8")
    assert "Handel" in html and "Ausgeführte Verkäufe" in html
    assert "KAUF" in js and "VERKAUF" in js and "nur abgeschlossen" in js
    assert "@media(max-width:600px)" in css and ".trade-layout" in css
    assert '@app.get("/trades"' in app and '@app.get("/api/trades/{trade_id}/candles")' in app
    assert '@app.post("/api/trades/{trade_id}/reconciliation")' in app
    assert "OKX-Kern prüft sie" in app
