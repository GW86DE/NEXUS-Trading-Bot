from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import pytest

import numpy as np
import pandas as pd


def _ohlcv(rows=260):
    index = pd.date_range("2026-01-01", periods=rows, freq="5min", tz="UTC")
    close = 100 + np.sin(np.arange(rows) / 9.0) * 2 + np.arange(rows) * 0.01
    return pd.DataFrame({"open": close-0.1, "high": close+0.4,
                         "low": close-0.4, "close": close,
                         "volume": np.full(rows, 1000.0)}, index=index)


def _prepared(*, entry=False, exit_signal=False):
    df = _ohlcv(200)
    df["rsi"] = 50.0; df["tema"] = 99.0; df["bb_middle"] = 100.0; df["atr"] = 1.0
    if entry:
        df.loc[df.index[-2], "rsi"] = 29.0
        df.loc[df.index[-1], "rsi"] = 31.0
        df.loc[df.index[-2], "tema"] = 98.0
        df.loc[df.index[-1], "tema"] = 99.0
    if exit_signal:
        df.loc[df.index[-2], "rsi"] = 69.0
        df.loc[df.index[-1], "rsi"] = 71.0
        df.loc[df.index[-2], "tema"] = 102.0
        df.loc[df.index[-1], "tema"] = 101.0
        df.loc[df.index[-1], "bb_middle"] = 100.0
    return df


def test_official_sample_entry_and_exit_conditions(monkeypatch):
    import freqtrade_sample_strategy as sample
    monkeypatch.setattr(sample, "indicators", lambda _raw: _prepared(entry=True))
    result = sample.evaluate(_ohlcv())
    assert result.entry and not result.exit
    monkeypatch.setattr(sample, "indicators", lambda _raw: _prepared(exit_signal=True))
    result = sample.evaluate(_ohlcv())
    assert result.exit and not result.entry


def test_sample_uses_completed_5m_startup_and_stable_hash():
    import freqtrade_sample_strategy as sample
    # Original SampleStrategy im beigefuegten Freqtrade-Stand: 200.
    assert sample.TIMEFRAME == "5m" and sample.STARTUP_CANDLES == 200
    assert sample.evaluate(_ohlcv()).candle_time.endswith("+00:00")
    snapshot = sample.parameter_snapshot()
    assert snapshot["stoploss"] == -0.10
    assert snapshot["minimal_roi"] == {"0": 0.04, "30": 0.02, "60": 0.01}
    assert snapshot["parameter_hash"] == sample.PARAMETER_HASH


def test_roi_schedule_and_exit_signal_priority(monkeypatch):
    import freqtrade_sample_strategy as sample
    assert sample.roi_threshold(0) == .04
    assert sample.roi_threshold(29.9) == .04
    assert sample.roi_threshold(30) == .02
    assert sample.roi_threshold(60) == .01
    monkeypatch.setattr(sample, "indicators", lambda _raw: _prepared(exit_signal=False))
    close, reason, audit = sample.exit_decision(
        _ohlcv(), entry_price=100, current_price=102.1, elapsed_minutes=30)
    assert close and reason == "freqtrade_roi_2pct" and audit["roi_threshold"] == .02


def test_roi_uses_net_profit_after_entry_and_exit_fees(monkeypatch):
    import freqtrade_sample_strategy as sample
    monkeypatch.setattr(sample, "indicators", lambda _raw: _prepared(exit_signal=False))
    gross_target = sample.roi_exit_price(
        100, .02, entry_fee_pct=.001, exit_fee_pct=.001)
    close, reason, audit = sample.exit_decision(
        _ohlcv(), entry_price=100, current_price=gross_target,
        elapsed_minutes=30, entry_fee_pct=.001, exit_fee_pct=.001)
    assert not close  # Reference uses round(profit, 8) > ROI, not >=.
    assert audit["net_profit_ratio"] == pytest.approx(.02)
    assert gross_target > 102
    close, reason, _ = sample.exit_decision(
        _ohlcv(), entry_price=100, current_price=gross_target+.001,
        elapsed_minutes=30, entry_fee_pct=.001, exit_fee_pct=.001)
    assert close and reason == "freqtrade_roi_2pct"


def test_runtime_mode_is_persistent_and_corruption_pauses_only_crypto(monkeypatch, tmp_path):
    monkeypatch.setenv("TRADINGBOT_TEST_STATE_DIR", str(tmp_path))
    import crypto_strategy_mode as mode
    out = mode.set_mode(mode.FREQTRADE_SAMPLE, source="test", notify=False)
    assert out["active_mode"] == mode.FREQTRADE_SAMPLE
    assert mode.current_mode() == mode.FREQTRADE_SAMPLE
    snapshot = mode.entry_snapshot()
    assert snapshot["entry_strategy_mode"] == mode.FREQTRADE_SAMPLE
    # GEAENDERT IN v9.2: nicht mehr gegen eine eingetippte Versionsnummer.
    # Der Test prueft, dass der Snapshot die Identitaet des installierten
    # Moduls traegt -- ein Versionswechsel ist eine bewusste Entscheidung und
    # soll nicht an dieser Stelle scheitern.
    import freqtrade_sample_strategy as sample
    assert snapshot["strategy_version"] == sample.STRATEGY_VERSION
    assert snapshot["parameter_hash"] == sample.PARAMETER_HASH
    mode.path().write_text("{broken", encoding="utf-8")
    assert mode.current_mode() == mode.CRYPTO_PAUSED


def test_switch_history_does_not_mutate_prior_entry_snapshot(monkeypatch, tmp_path):
    monkeypatch.setenv("TRADINGBOT_TEST_STATE_DIR", str(tmp_path))
    import crypto_strategy_mode as mode
    mode.set_mode(mode.FREQTRADE_SAMPLE, source="test", notify=False)
    entry = mode.entry_snapshot()
    mode.set_mode(mode.NEXUS_STANDARD, source="test", notify=False)
    assert entry["entry_strategy_mode"] == mode.FREQTRADE_SAMPLE
    import freqtrade_sample_strategy as sample
    assert entry["strategy_version"] == sample.STRATEGY_VERSION
    assert mode.current_mode() == mode.NEXUS_STANDARD


def test_open_position_uses_entry_mode_after_global_switch(monkeypatch, tmp_path):
    monkeypatch.setenv("TRADINGBOT_TEST_STATE_DIR", str(tmp_path))
    import crypto_strategy_mode as mode
    import freqtrade_sample_strategy as sample
    from crypto_engine import CryptoEngine, KryptoPosition
    mode.set_mode(mode.NEXUS_STANDARD, source="test", notify=False)
    snapshot = {"entry_strategy_mode": mode.FREQTRADE_SAMPLE,
                "strategy_name": sample.STRATEGY_NAME,
                "strategy_version": sample.STRATEGY_VERSION,
                "parameter_hash": sample.PARAMETER_HASH}
    position = KryptoPosition(
        "BTC", "BTC-EUR", 1, 100, 90, 104,
        eroeffnet_am=(datetime.now(timezone.utc)-timedelta(minutes=31)).isoformat(),
        entry_strategy_mode=mode.FREQTRADE_SAMPLE,
        strategy_name=sample.STRATEGY_NAME,
        strategy_version=sample.STRATEGY_VERSION,
        strategy_parameter_hash=sample.PARAMETER_HASH,
        strategy_parameters=snapshot,
    )
    class Broker:
        def historie(self, *_args, **_kwargs): return _ohlcv()
    class Hub:
        def broker(self, name): return Broker() if name == "okx" else None
    engine = CryptoEngine.__new__(CryptoEngine)
    engine.hub = Hub(); engine.cfg = type("Cfg", (), {"FREQTRADE_HISTORY_DURATION": "3 D"})()
    engine.buch = type("Book", (), {"setze": lambda self, p: None})()
    engine._instrument = lambda *_args: object()
    monkeypatch.setattr(sample, "exit_decision", lambda *a, **k: (True, "freqtrade_roi_2pct", {}))
    close, reason = engine._strategy_exit(position, 102.1)
    assert close and reason == "freqtrade_roi_2pct"
    assert mode.current_mode() == mode.NEXUS_STANDARD


def test_legacy_position_without_strategy_proof_becomes_observe(monkeypatch, tmp_path):
    monkeypatch.setenv("TRADINGBOT_TEST_STATE_DIR", str(tmp_path))
    path = tmp_path / "positions.json"
    path.write_text(json.dumps({"version": 1, "positionen": [{
        "symbol": "ETH", "inst_id": "ETH-EUR", "menge": 1,
        "einstieg": 100, "stop": 90, "take_profit": 110,
        "herkunft": "BOT", "verwaltung": "AUTO", "decision_id": 7,
    }]}), encoding="utf-8")
    from crypto_engine import KryptoPositionsbuch
    position = KryptoPositionsbuch(path).hole("ETH")
    assert position.verwaltung == "BEOBACHTEN"
    assert not position.darf_automatisch_verkaufen
    assert position.entry_strategy_mode == ""


def test_v831_migration_binds_only_proven_bot_auto_positions(tmp_path):
    source = tmp_path / "TradingBot_v8.3.1_NEXUS"
    target = tmp_path / "TradingBot_v9.0_NEXUS"
    source.mkdir(); target.mkdir()
    (source / "VERSION.txt").write_text("8.3.1-NEXUS\n", encoding="utf-8")
    rows = [
        {"symbol": "BTC", "herkunft": "BOT", "verwaltung": "AUTO",
         "decision_id": 17},
        {"symbol": "ETH", "herkunft": "BROKER", "verwaltung": "BEOBACHTEN",
         "decision_id": None},
    ]
    (source / "crypto_positions.json").write_text(
        json.dumps({"version": 1, "positionen": rows}), encoding="utf-8")
    import settings_migration
    settings_migration.migrate_from(source, target)
    migrated = json.loads((target / "crypto_positions.json").read_text(encoding="utf-8"))
    btc, eth = migrated["positionen"]
    assert btc["entry_strategy_mode"] == "NEXUS_STANDARD"
    assert btc["strategy_version"] == "NEXUS-8.3.1-STANDARD-MIGRATED"
    assert "entry_strategy_mode" not in eth


def test_freqtrade_mode_skips_universe_ai_attention(monkeypatch, tmp_path):
    monkeypatch.setenv("TRADINGBOT_TEST_STATE_DIR", str(tmp_path))
    import crypto_strategy_mode as mode
    from universe.manager import UniverseManager
    mode.set_mode(mode.FREQTRADE_SAMPLE, source="test", notify=False)
    manager = UniverseManager.__new__(UniverseManager)
    manager.ai = type("AI", (), {
        "bewerte_universe_kandidat": lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("AI must not be called"))
    })()
    member = type("Member", (), {"broker": "okx"})()
    assert manager._ki_urteil(member, {}) == ("", "")


def test_crypto_pause_blocks_entries_before_scan_but_not_etoro(monkeypatch, tmp_path):
    monkeypatch.setenv("TRADINGBOT_TEST_STATE_DIR", str(tmp_path))
    import crypto_strategy_mode as mode
    from crypto_engine import CryptoEngine
    mode.set_mode(mode.CRYPTO_PAUSED, source="test", notify=False)
    class Hub:
        def broker(self, name): return object() if name == "okx" else None
    engine = CryptoEngine.__new__(CryptoEngine); engine.hub = Hub()
    result = engine.scan()
    assert result["gescannt"] == 0 and result["strategy_mode"] == mode.CRYPTO_PAUSED
    assert "eToro" in result["hinweis"]


def test_strategy_identity_is_queryable_in_decision_and_trade_history(monkeypatch, tmp_path):
    import decision_analytics as analytics
    monkeypatch.setattr(analytics, "DB_PATH", tmp_path / "history.sqlite")
    import freqtrade_sample_strategy as sample
    decision_id = analytics.record({
        "decision_id": 9001, "symbol": "BTC", "asset_type": "crypto",
        "broker": "okx", "status": "APPROVED",
        "entry_strategy_mode": "FREQTRADE_SAMPLE",
        "strategy_version": sample.STRATEGY_VERSION,
        "strategy_parameter_hash": sample.PARAMETER_HASH,
    })
    row = analytics.latest(1)[0]
    assert row["strategy_mode"] == "FREQTRADE_SAMPLE"
    assert row["strategy_parameter_hash"] == sample.PARAMETER_HASH
    import trade_ledger
    trade_id = trade_ledger.trade_open(
        broker="okx", symbol="BTC", menge=1, einstieg_preis=100,
        decision_id=decision_id, strategie_version=sample.STRATEGY_VERSION,
        entry_strategy_mode="FREQTRADE_SAMPLE",
        strategy_parameter_hash=sample.PARAMETER_HASH,
        strategy_parameters=sample.parameter_snapshot())
    trade = trade_ledger.offener_trade("okx", "BTC")
    assert trade_id and trade["entry_strategy_mode"] == "FREQTRADE_SAMPLE"
    assert trade["strategy_parameter_hash"] == sample.PARAMETER_HASH
    assert json.loads(trade["strategy_parameters_json"])["minimal_roi"]["30"] == .02


def test_backtest_executes_signal_on_next_open_without_lookahead(monkeypatch):
    import freqtrade_sample_backtest as backtest
    df = _ohlcv(205)
    df.loc[:, ["open", "high", "low", "close"]] = 100.0
    df.loc[df.index[201], "high"] = 105.0
    def signals(raw):
        out = raw.copy(); out["enter_long"] = False; out["exit_long"] = False
        out.loc[out.index[200], "enter_long"] = True
        return out
    monkeypatch.setattr(backtest, "_signals", signals)
    result = backtest.run_backtest(df, fee_pct=0, slippage_pct=0)
    assert result["trades"] == 1
    trade = result["trade_rows"][0]
    assert trade["entry_time"] == str(df.index[201])
    assert trade["entry_price"] == 100 and trade["exit_price"] == 104
    assert trade["exit_reason"] == "roi_4pct"


def test_backtest_same_candle_stop_precedes_roi(monkeypatch):
    import freqtrade_sample_backtest as backtest
    df = _ohlcv(205)
    df.loc[:, ["open", "high", "low", "close"]] = 100.0
    df.loc[df.index[201], "high"] = 105.0
    df.loc[df.index[201], "low"] = 89.0
    def signals(raw):
        out = raw.copy(); out["enter_long"] = False; out["exit_long"] = False
        out.loc[out.index[200], "enter_long"] = True
        return out
    monkeypatch.setattr(backtest, "_signals", signals)
    result = backtest.run_backtest(df, fee_pct=0, slippage_pct=0)
    assert result["trade_rows"][0]["exit_reason"] == "stop_loss"
    assert result["trade_rows"][0]["exit_price"] == 90


def test_webui_runtime_switch_requires_confirmation_and_never_restart(monkeypatch, tmp_path):
    monkeypatch.setenv("TRADINGBOT_TEST_STATE_DIR", str(tmp_path))
    import crypto_strategy_mode as mode
    from webui import settings_store
    monkeypatch.setattr(settings_store, "ROOT", tmp_path)
    with pytest.raises(ValueError):
        settings_store.set_crypto_strategy_mode(
            "freqtrade", source="webui:test", confirm="")
    result = settings_store.set_crypto_strategy_mode(
        "freqtrade", source="webui:test", confirm="FREQTRADE AKTIVIEREN")
    assert result["crypto_strategy"]["active_mode"] == mode.FREQTRADE_SAMPLE
    assert mode.current_mode() == mode.FREQTRADE_SAMPLE
    result = settings_store.set_crypto_strategy_mode(
        "paused", source="webui:test")
    assert result["crypto_strategy"]["active_mode"] == mode.CRYPTO_PAUSED


def test_telegram_crypto_aliases_and_session_confirmation(monkeypatch, tmp_path):
    monkeypatch.setenv("TRADINGBOT_TEST_STATE_DIR", str(tmp_path))
    import config, crypto_strategy_mode as mode, telegram_steuerung as control
    monkeypatch.setattr(config, "TELEGRAM_CHAT_ID", "42")
    monkeypatch.setattr(config, "TELEGRAM_ALLOWED_USER_ID", "7")
    monkeypatch.setattr(control, "STATE", tmp_path / "telegram_state.json")
    monkeypatch.setattr(control, "STATUS", tmp_path / "telegram_status.json")
    monkeypatch.setattr(control, "AUDIT", tmp_path / "telegram_audit.jsonl")
    monkeypatch.setattr(control, "answer_callback_query", lambda *a, **k: True)
    monkeypatch.setattr(control, "edit_telegram_message", lambda *a, **k: True)
    assert control.parse_command("/cryptopause") == ("CRYPTO_PAUSE", [])
    assert control.parse_command("/crypto freqtrade") == ("CRYPTO", ["FREQTRADE"])
    sent = []
    ctl = control.TelegramSteuerung(lambda _command: "unused")
    result = ctl.process_update(
        {"update_id": 1, "message": {"chat": {"id": 42}, "from": {"id": 7},
                                      "text": "/crypto freqtrade"}},
        send_func=lambda text, **k: sent.append(text),
        send_buttons_func=lambda text, keyboard, **k: sent.append((text, keyboard)))
    assert result == "confirm_required" and mode.current_mode() == mode.NEXUS_STANDARD
    callback_data = sent[-1][1][0][0]["callback_data"]
    ctl.process_update(
        {"update_id": 2, "callback_query": {"id": "cb", "from": {"id": 7},
         "data": callback_data, "message": {"chat": {"id": 42}, "message_id": 9}}},
        send_func=lambda text, **k: sent.append(text),
        send_buttons_func=lambda text, keyboard, **k: sent.append((text, keyboard)))
    assert mode.current_mode() == mode.FREQTRADE_SAMPLE
