from __future__ import annotations

from types import SimpleNamespace

import pytest


def test_unbelegter_basiswechsel_beim_update_loescht_keine_tagesbremse(
        tmp_path, monkeypatch):
    """Konservative Migration des OKX-Falls vom 29.08.2026.

    2.199,44 EUR wurden in ETH umgewandelt. Ein alter Startwert mit Position
    darf ohne Broker-/Kapitalflussbeleg nicht automatisch verschwinden.
    Der alte Test speicherte seinen Ausgangszustand nicht; die transaktionale
    Aktualisierung las deshalb einen leeren Diskzustand und pruefte den
    behaupteten Migrationsfall gar nicht. v10 erhaelt den Tagesstopp.
    """
    monkeypatch.setenv("TRADINGBOT_TEST_STATE_DIR", str(tmp_path))
    monkeypatch.setattr("config.MAX_UNREALIZED_DAILY_LOSS_PCT", 0.03)
    from risk_pots import RiskPot

    topf = RiskPot("okx", state_datei=str(tmp_path / "risk_state_okx.json"))
    topf.state.day_start_equity = 73242.93517648
    topf.state.last_equity = 71043.4986432
    topf.state.equity_drawdown_pct = -0.030029333586651283
    topf.state.equity_guard_halted = True
    topf.state.equity_basis_key = ""
    topf.state.save()

    topf.setze_kontowert(
        71043.4986432,
        basis_key="handelbares_kapital:v2:okx:EUR:",
    )

    assert topf.state.equity_guard_halted is True
    assert topf.state.equity_drawdown_pct == pytest.approx(-0.030029333586651283)
    assert topf.state.day_start_equity == pytest.approx(73242.93517648)
    assert topf.darf_kaufen()[0] is False


def test_cash_zu_eth_aendert_nur_basis_und_ist_kein_equity_verlust(
        tmp_path, monkeypatch):
    monkeypatch.setenv("TRADINGBOT_TEST_STATE_DIR", str(tmp_path))
    monkeypatch.setattr("config.MAX_UNREALIZED_DAILY_LOSS_PCT", 0.03)
    from risk_pots import RiskPot

    topf = RiskPot("okx", state_datei=str(tmp_path / "risk_state_okx.json"))
    topf.setze_kontowert(
        73242.94, basis_key="handelbares_kapital:v2:okx:EUR:")
    topf.setze_kontowert(
        73242.94, basis_key="handelbares_kapital:v2:okx:EUR:ETH")

    assert topf.state.equity_guard_halted is False
    assert topf.state.day_start_equity == pytest.approx(73242.94)
    assert topf.state.equity_drawdown_pct == 0.0


def test_echter_drei_prozent_verlust_auf_gleicher_basis_bleibt_gesperrt(
        tmp_path, monkeypatch):
    monkeypatch.setenv("TRADINGBOT_TEST_STATE_DIR", str(tmp_path))
    monkeypatch.setattr("config.MAX_UNREALIZED_DAILY_LOSS_PCT", 0.03)
    from risk_pots import RiskPot

    topf = RiskPot("okx", state_datei=str(tmp_path / "risk_state_okx.json"))
    basis = "handelbares_kapital:v2:okx:EUR:ETH"
    topf.setze_kontowert(100000.0, basis_key=basis)
    topf.setze_kontowert(96999.0, basis_key=basis)

    assert topf.state.equity_guard_halted is True
    erlaubt, grund = topf.darf_kaufen()
    assert erlaubt is False and "Equity-Bremse" in grund


def test_status_zeigt_aktive_equity_bremse_wahrheitsgemaess(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADINGBOT_TEST_STATE_DIR", str(tmp_path))
    from risk_pots import RiskPot

    topf = RiskPot("okx", state_datei=str(tmp_path / "risk_state_okx.json"))
    topf.kontowert = 71043.50
    topf.state.equity_guard_halted = True
    topf.state.equity_drawdown_pct = -0.03
    topf.state.save()

    status = topf.uebersicht()
    assert status["equity_bremse"] is True
    assert status["darf_kaufen"] is False
    assert "Equity-Bremse" in status["grund"]


def test_okx_handelbares_kapital_akzeptiert_symbolisierte_botposition(monkeypatch):
    from broker.okx import OKXBroker

    broker = OKXBroker(client=SimpleNamespace(), quote_ccy="EUR")
    monkeypatch.setattr(broker, "_balances_stream_or_rest", lambda: {
        "EUR": {"cash": 71043.4986432, "gesamt": 71043.4986432}
    })

    # Seit 10.1.9 gehoert die Markt-/Preiswaehrung explizit zum
    # Positionswert. Symbolgleichheit allein darf keine Waehrung erfinden.
    wert = broker.handelbares_kapital((("ETH", 1.0433771, 2107.997705987605, "EUR"),))
    assert wert == pytest.approx(73242.93517648)


def test_globale_scanblockade_wird_als_entscheidung_sichtbar(monkeypatch):
    from crypto_engine import CryptoEngine
    import decision_journal

    erfasst = []
    monkeypatch.setattr(decision_journal, "record_decision",
                        lambda **payload: erfasst.append(payload) or 1)
    engine = object.__new__(CryptoEngine)
    engine.cfg = SimpleNamespace(OKX_DEMO=True)

    result = engine._scan_blockiert(
        "okx: Equity-Bremse aktiv (-3.0 % Rueckgang)",
        gate="risk_gate", strategy_mode="FREQTRADE_SAMPLE")

    assert result["scan_blocked"] is True and result["gescannt"] == 0
    assert erfasst[0]["symbol"] == "OKX_SCAN"
    assert erfasst[0]["status"] == "BLOCKED"
    assert erfasst[0]["blocked_by"] == "risk_gate"
    assert "Equity-Bremse" in erfasst[0]["reason"]
    assert erfasst[0]["execution_status"] == ""
