"""Regressionen fuer fehlende Historie; korrigiert gegen Original Freqtrade 2026.8-dev.
Signal vor Stop/ROI, 200 Startkerzen. Fillzeit bleibt ein separater Buchungsbeleg.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("TRADINGBOT_TEST_STATE_DIR", "/tmp/tradingbot_tests")
Path(os.environ["TRADINGBOT_TEST_STATE_DIR"]).mkdir(parents=True, exist_ok=True)

WURZEL = Path(__file__).resolve().parent.parent


def kerzen(n):
    idx = pd.date_range("2026-08-01", periods=n, freq="5min", tz="UTC")
    p = np.linspace(100, 110, n)
    return pd.DataFrame({"open": p, "high": p * 1.001, "low": p * 0.999,
                         "close": p, "volume": np.full(n, 1000.0)}, index=idx)


# ===========================================================================
# F1 -- ROI und Stoploss brauchen keine Kerzen
# ===========================================================================
def test_roi_greift_auch_ohne_ausreichende_kerzen():
    """Der konkrete Fall: 65 Minuten alt, +4,79 % netto, ROI-Stufe 1 %."""
    import freqtrade_sample_strategy as sample

    schliessen, grund, audit = sample.exit_decision(
        kerzen(5), entry_price=100.0, current_price=105.0,
        elapsed_minutes=65, entry_fee_pct=0.001, exit_fee_pct=0.001)

    assert schliessen is True, "ROI muss ohne Dataframe entscheiden koennen"
    assert grund == "freqtrade_roi_1pct"
    assert audit["exit_stage"] == "roi"


def test_stoploss_greift_auch_ohne_kerzen():
    import freqtrade_sample_strategy as sample

    schliessen, grund, audit = sample.exit_decision(
        pd.DataFrame(), entry_price=100.0, current_price=89.0, elapsed_minutes=5)
    assert schliessen and grund == "freqtrade_stop_loss"
    assert audit["exit_stage"] == "stoploss"
    assert audit["stoploss_price"] == pytest.approx(90.0)


def test_fehlende_kerzen_kosten_nur_das_exit_signal():
    """Ohne Kerzen kein Signal -- aber auch kein Absturz und kein Fehlurteil."""
    import freqtrade_sample_strategy as sample

    schliessen, grund, audit = sample.exit_decision(
        kerzen(5), entry_price=100.0, current_price=100.5, elapsed_minutes=5)
    assert schliessen is False and grund == ""
    assert audit["exit_stage"] == "signal_unavailable"
    assert "signal_error" in audit


# ===========================================================================
# F5 -- die Reihenfolge bestimmt den protokollierten Grund
# ===========================================================================
def test_exit_signal_gewinnt_gegen_roi(monkeypatch):
    """Original should_exit ordnet das Signal vor ROI ein."""
    import freqtrade_sample_strategy as sample

    class Auswertung:
        exit = True
        entry = False
        def audit_values(self):
            return {"exit": True}

    monkeypatch.setattr(sample, "evaluate", lambda _raw: Auswertung())
    schliessen, grund, _ = sample.exit_decision(
        kerzen(60), entry_price=100.0, current_price=103.0, elapsed_minutes=35)
    assert schliessen and grund == "freqtrade_exit_signal"


def test_signal_entscheidet_wenn_roi_nicht_erreicht_ist(monkeypatch):
    """Die Gegenprobe -- use_exit_signal bleibt wirksam, auch im Minus."""
    import freqtrade_sample_strategy as sample

    class Auswertung:
        exit = True
        entry = False
        def audit_values(self):
            return {"exit": True}

    monkeypatch.setattr(sample, "evaluate", lambda _raw: Auswertung())
    schliessen, grund, _ = sample.exit_decision(
        kerzen(60), entry_price=100.0, current_price=99.5, elapsed_minutes=35)
    assert schliessen and grund == "freqtrade_exit_signal"


def test_exit_signal_gewinnt_gegen_stoploss(monkeypatch):
    import freqtrade_sample_strategy as sample

    class Auswertung:
        exit = True
        entry = False
        def audit_values(self):
            return {"exit": True}

    monkeypatch.setattr(sample, "evaluate", lambda _raw: Auswertung())
    _, grund, _ = sample.exit_decision(
        kerzen(60), entry_price=100.0, current_price=85.0, elapsed_minutes=90)
    assert grund == "freqtrade_exit_signal"


# ===========================================================================
# F4 -- startup_candle_count wie im Original
# ===========================================================================
def test_startup_entspricht_der_offiziellen_samplestrategy():
    import freqtrade_sample_strategy as sample
    assert sample.STARTUP_CANDLES == 200


def test_engine_nimmt_die_zahl_aus_der_strategie():
    """Keine zweite, fest eingetippte Kopie derselben Zahl."""
    quelle = (WURZEL / "crypto_engine.py").read_text(encoding="utf-8")
    assert "minimum_candles = 200" not in quelle
    assert "from freqtrade_sample_strategy import STARTUP_CANDLES" in quelle


# ===========================================================================
# F3 -- der Backtest misst die Freqtrade-Reihenfolge
# ===========================================================================



# ===========================================================================
# F2 -- der Takt rastet am Kerzenraster ein
# ===========================================================================
def test_taktgeber_kann_einrasten():
    import time
    from scheduler_v7 import SCAN, Taktgeber

    takt = Taktgeber()
    takt.markiere("crypto", SCAN, jetzt=1000.0, raster_sekunden=300.0)
    with takt._lock:
        vermerkt = takt._letzter_lauf[("crypto", SCAN)]
    versatz = time.time() % 300.0
    assert vermerkt == pytest.approx(1000.0 - versatz, abs=1.0), \
        "Der Vermerk muss auf den zuletzt passierten Rasterpunkt zeigen"


def test_ohne_raster_bleibt_der_takt_unveraendert():
    """Die anderen Modi duerfen sich nicht aendern."""
    from scheduler_v7 import SCAN, Taktgeber

    takt = Taktgeber()
    takt.markiere("crypto", SCAN, jetzt=1000.0)
    with takt._lock:
        assert takt._letzter_lauf[("crypto", SCAN)] == 1000.0


def test_nur_der_freqtrade_modus_rastet_ein(monkeypatch, tmp_path):
    monkeypatch.setenv("TRADINGBOT_TEST_STATE_DIR", str(tmp_path))
    import crypto_strategy_mode as modus
    from crypto_engine import CryptoEngine

    engine = CryptoEngine.__new__(CryptoEngine)
    modus.set_mode(modus.NEXUS_STANDARD, source="test", notify=False)
    assert engine._scan_raster_sekunden() == 0.0, \
        "Der Standardmodus behaelt seinen freien Takt"
    modus.set_mode(modus.FREQTRADE_SAMPLE, source="test", notify=False)
    assert engine._scan_raster_sekunden() == 300.0, \
        "Freqtrade entscheidet auf der 5-Minuten-Kerze"


# ===========================================================================
# Fillzeit ist weiterhin ein unveraenderlicher Buchungsbeleg
# ===========================================================================
def test_fillzeitpunkt_wird_aus_den_fills_gelesen():
    from crypto_engine import _fillzeitpunkt

    class Ergebnis:
        fills = [{"ts": "1756000000000"}, {"ts": "1755999000000"}]

    zeit = _fillzeitpunkt(Ergebnis())
    assert zeit.startswith("2025-") or zeit.startswith("2026-")
    # Der FRUEHESTE Fill zaehlt -- die Haltedauer beginnt beim ersten Stueck.
    assert "1755999000000" not in zeit
    from datetime import datetime, timezone
    erwartet = datetime.fromtimestamp(1755999000.0, timezone.utc).isoformat()
    assert zeit == erwartet


def test_fehlende_fillzeit_faellt_sauber_zurueck():
    from crypto_engine import _fillzeitpunkt

    class Leer:
        fills = []

    assert _fillzeitpunkt(Leer()) == ""
    class Kaputt:
        fills = [{"ts": "keine zahl"}, {"ts": None}]
    assert _fillzeitpunkt(Kaputt()) == ""


def test_position_uebernimmt_den_fillzeitpunkt():
    quelle = (WURZEL / "crypto_engine.py").read_text(encoding="utf-8")
    stelle = quelle.index("position = KryptoPosition(")
    block = quelle[max(0, stelle - 900):stelle + 200]
    assert "_fillzeitpunkt(ergebnis)" in block
    assert "eroeffnet_am=eroeffnet" in block
