"""Dashboard-Karte "UNIVERSUM" zeigt das echte Universum (v8.1.3).

Anlass: Georgs Frage zum Screenshot -- "wie viel Aktien sind jetzt im
Universum? 0 oder 80?". Die Karte zaehlte den statischen Katalog aus der
config und stand bei Krypto strukturell immer auf 0, weil
``config.CRYPTO_SYMBOLS`` in v8 leer ist (die Coins kommen von OKX).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("TRADINGBOT_TEST_STATE_DIR", "/tmp/tradingbot_tests")
Path(os.environ["TRADINGBOT_TEST_STATE_DIR"]).mkdir(parents=True, exist_ok=True)

WURZEL = Path(__file__).resolve().parent.parent


@pytest.fixture
def universum(tmp_path, monkeypatch):
    """Ein Manager auf einem eigenen Zustand, in beiden Modulen sichtbar."""
    from universe.manager import UniverseManager
    from universe.modelle import UniverseZustand

    manager = UniverseManager(UniverseZustand(tmp_path / "u.json"))
    import universe_overview
    monkeypatch.setattr(universe_overview, "_manager", lambda: manager)
    return manager


def fuelle(manager, broker, symbole, zustand):
    from universe.modelle import UniverseMitglied
    for i, symbol in enumerate(symbole):
        manager.zustand.setze(UniverseMitglied(
            broker=broker, symbol=symbol, zustand=zustand,
            letzter_rang=i + 1, letzter_score=1.0))


# ---------------------------------------------------------------------------
# Der eigentliche Fehler
# ---------------------------------------------------------------------------
def test_karte_zaehlt_krypto_statt_immer_null(universum):
    """Kernpunkt: Krypto darf nicht mehr strukturell 0 sein."""
    from universe.modelle import AKTIV
    import universe_overview

    fuelle(universum, "okx", ["BTC", "ETH", "SOL", "ADA", "AVAX"], AKTIV)
    text = universe_overview.dashboard_text()

    assert "0 Krypto" not in text
    assert "Krypto 5 aktiv" in text, text
    assert "5 Kern + 0" in text, "Kern und Dynamik muessen getrennt sichtbar sein"


def test_karte_zaehlt_aktien_aus_dem_universum_nicht_aus_der_config(universum):
    from universe.modelle import AKTIV
    import universe_overview

    fuelle(universum, "etoro", ["AAPL", "MSFT", "NVDA", "XYZ"], AKTIV)
    daten = universe_overview.kennzahlen()["etoro"]

    assert daten["aktiv"] == 4
    assert daten["kern"] == 3, "AAPL, MSFT, NVDA stehen in der Kernliste"
    assert daten["dynamisch"] == 1, "XYZ ist der dynamische Teil"


def test_beobachtung_zaehlt_nicht_als_aktiv(universum):
    """"Im Universum" heisst beobachtet -- Bewaehrung ist noch nicht aktiv."""
    from universe.modelle import AKTIV, BEOBACHTUNG
    import universe_overview

    fuelle(universum, "okx", ["BTC", "ETH"], AKTIV)
    fuelle(universum, "okx", ["ADA", "DOT"], BEOBACHTUNG)
    daten = universe_overview.kennzahlen()["okx"]

    assert daten["aktiv"] == 2
    assert daten["beobachtung"] == 2
    assert daten["gesamt"] == 4
    assert "2 in Bewaehrung" in universe_overview.dashboard_text()


# ---------------------------------------------------------------------------
# Ehrlich bleiben, wenn nichts bekannt ist
# ---------------------------------------------------------------------------
def test_leeres_universum_wird_nicht_als_katalog_ausgegeben(universum):
    """Vor dem ersten Lauf darf keine Katalogzahl wie ein Universum aussehen."""
    import universe_overview
    text = universe_overview.dashboard_text()
    assert "noch kein Lauf" in text
    assert "aktiv" not in text


def test_unlesbarer_zustand_meldet_unbekannt_statt_null(monkeypatch):
    import universe_overview
    monkeypatch.setattr(universe_overview, "_manager", lambda: None)
    daten = universe_overview.kennzahlen()["okx"]
    assert daten["aktiv"] is None, "unbekannt darf nicht wie 0 aussehen"
    assert daten["fehler"]


def test_limits_kommen_aus_den_regeln(universum):
    import universe_overview
    daten = universe_overview.kennzahlen()
    assert daten["etoro"]["kern_limit"] == 75
    assert daten["etoro"]["dynamisch_limit"] == 25
    assert daten["okx"]["kern_limit"] == 20


# ---------------------------------------------------------------------------
# Die GUI benutzt wirklich diese Quelle
# ---------------------------------------------------------------------------
def test_gui_liest_nicht_mehr_den_statischen_katalog():
    quelle = (WURZEL / "gui_app.py").read_text(encoding="utf-8")
    assert "universum_kartentext()" in quelle, "Karte nutzt die neue Quelle nicht"
    assert 'f"{us} US + {eu} EU · {crypto} Krypto"' not in quelle, \
        "Der statische Katalogtext ist noch aktiv"
    assert 'runtime.get("configured_crypto"' not in quelle, \
        "configured_crypto stammt aus dem eToro-Prozess und ist dort immer 0"


def test_gui_karte_ueberlebt_einen_fehler(monkeypatch):
    """Eine kaputte Universumsdatei darf die GUI nicht abschiessen."""
    pytest.importorskip("tkinter", reason="Kopflose Testumgebung ohne Tk")
    import gui_app
    import universe_overview
    monkeypatch.setattr(universe_overview, "dashboard_text",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("kaputt")))
    assert gui_app.universum_kartentext() == "nicht verfügbar"


# ---------------------------------------------------------------------------
# WebUI: gleiche Quelle, richtige Kernflagge fuer beide Broker
# ---------------------------------------------------------------------------
def test_webui_markiert_auch_aktien_als_kern(tmp_path, monkeypatch):
    from universe.modelle import AKTIV
    import config
    monkeypatch.setattr(config, "UNIVERSE_STATE_FILE", str(tmp_path / "u.json"), raising=False)

    from universe.manager import UniverseManager
    from universe.modelle import UniverseZustand
    manager = UniverseManager(UniverseZustand(tmp_path / "u.json"))
    fuelle(manager, "etoro", ["AAPL", "XYZ"], AKTIV)
    manager.zustand.speichern()

    import webui.state as state
    daten = state.universe()
    zeilen = {z["symbol"]: z for z in daten["broker"]["etoro"]["werte"]}
    assert zeilen["AAPL"]["kern"] is True, "Kernaktien muessen als KERN erkennbar sein"
    assert zeilen["XYZ"]["kern"] is False
    assert len(daten["kernwerte_aktien"]) == 75
    assert len(daten["kernwerte_krypto"]) == 20
    assert set(daten["kernwerte_krypto"]) == set(config.CRYPTO_CORE_SYMBOLS)


def test_uebersicht_liefert_kern_und_dynamik(universum):
    from universe.modelle import AKTIV
    fuelle(universum, "okx", ["BTC", "ETH", "SOL", "ADA"], AKTIV)
    u = universum.uebersicht("okx")
    assert u["kern"] == 4 and u["dynamisch"] == 0
    assert u["kern_limit"] == 20 and u["dynamisch_limit"] == 30


# ---------------------------------------------------------------------------
# Zustandsdatei gehoert in das Zustandsverzeichnis
# ---------------------------------------------------------------------------
def test_universumszustand_landet_nicht_neben_dem_quelltext(tmp_path, monkeypatch):
    """Derselbe Fehler wie frueher in news_sources.py: relativer Pfad neben
    dem Quelltext statt im Zustandsverzeichnis. Folge waren Laufzeitdateien
    im Release-Baum."""
    monkeypatch.setenv("TRADINGBOT_TEST_STATE_DIR", str(tmp_path))
    from universe.modelle import UniverseZustand
    zustand = UniverseZustand("universe_state.json")
    assert zustand.datei.parent == tmp_path
    assert zustand.datei.parent != WURZEL, "Der Release-Baum ist kein Zustandsverzeichnis"
