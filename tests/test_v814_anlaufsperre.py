"""Anlaufsperre und TRADING_READY (v8.1.4).

Anlass: Am 25.08.2026 hat der Bot 2,5 Sekunden nach dem Start eine Marktorder
ausgeloest -- bevor der Kontostand je gegen das Positionsbuch abgeglichen war.
Eine Anlaufsperre gab es in keiner Version; der Gedanke stand als P0 im
Backlog (HB-007), gebaut wurde er nie.
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


class Uhr:
    """Steuerbare Zeitquelle -- kein sleep im Test."""

    def __init__(self):
        self.jetzt = 1000.0

    def __call__(self):
        return self.jetzt

    def weiter(self, sekunden):
        self.jetzt += sekunden


@pytest.fixture
def bereitschaft(monkeypatch):
    import config
    monkeypatch.setattr(config, "STARTUP_TRADING_GRACE_MINUTES", 15.0, raising=False)
    from trading_ready import Handelsbereitschaft
    uhr = Uhr()
    return Handelsbereitschaft("okx", cfg=config, jetzt=uhr), uhr


def alles_erfuellen(b):
    for name in list(b.bedingungen):
        b.melde(name, True, "Test")


# ---------------------------------------------------------------------------
# Die Sperre selbst
# ---------------------------------------------------------------------------
def test_direkt_nach_dem_start_wird_nicht_gekauft(bereitschaft):
    b, _ = bereitschaft
    alles_erfuellen(b)
    erlaubt, grund = b.darf_kaufen()
    assert erlaubt is False
    assert "Anlaufsperre" in grund


def test_nach_ablauf_der_wartezeit_wird_gekauft(bereitschaft):
    b, uhr = bereitschaft
    alles_erfuellen(b)
    uhr.weiter(15 * 60 + 1)
    erlaubt, grund = b.darf_kaufen()
    assert erlaubt is True, grund


def test_wartezeit_allein_reicht_nicht(bereitschaft):
    """TRADING_READY und Wartezeit -- beides muss stimmen."""
    b, uhr = bereitschaft
    alles_erfuellen(b)
    b.melde("reconciliation", False, "noch nicht gelaufen")
    uhr.weiter(15 * 60 + 1)

    erlaubt, grund = b.darf_kaufen()
    assert erlaubt is False
    assert "Positionen einmal mit dem Konto abgeglichen" in grund


def test_reconciliation_ist_pflichtbedingung():
    """Genau diese Bedingung haette den Vorfall vom 25.08. verhindert."""
    from trading_ready import STANDARD_BEDINGUNGEN
    namen = {n for n, _ in STANDARD_BEDINGUNGEN}
    assert "reconciliation" in namen
    assert "universum" in namen
    assert "signalkerze" in namen
    assert "gebuehren" in namen


def test_verlorene_bedingung_sperrt_wieder(bereitschaft):
    b, uhr = bereitschaft
    alles_erfuellen(b)
    uhr.weiter(15 * 60 + 1)
    assert b.darf_kaufen()[0] is True

    b.melde("broker_verbunden", False, "Verbindung verloren")
    assert b.darf_kaufen()[0] is False


def test_status_nennt_die_offenen_bedingungen(bereitschaft):
    b, _ = bereitschaft
    b.melde("broker_verbunden", True)
    zustand = b.status()
    assert zustand["trading_ready"] is False
    assert zustand["kaeufe_erlaubt"] is False
    assert len(zustand["offen"]) > 0
    assert zustand["restwartezeit_sekunden"] > 800


def test_freigabe_uhrzeit_wird_genannt(bereitschaft):
    b, uhr = bereitschaft
    assert b.freigabe_uhrzeit() != "jetzt"
    uhr.weiter(15 * 60 + 1)
    assert b.freigabe_uhrzeit() == "jetzt"


def test_wartezeit_ist_einstellbar(monkeypatch):
    import config
    from trading_ready import Handelsbereitschaft
    monkeypatch.setattr(config, "STARTUP_TRADING_GRACE_MINUTES", 3.0, raising=False)
    b = Handelsbereitschaft("okx", cfg=config, jetzt=Uhr())
    assert b.wartezeit_sekunden == pytest.approx(180.0)


# ---------------------------------------------------------------------------
# Die Sperre gilt NUR fuer neue Kaeufe
# ---------------------------------------------------------------------------
def test_nur_der_kaufweg_fragt_die_sperre():
    """Verkauf, Stop und Schutzorder duerfen nie auf die Sperre warten.

    Eine bestehende Position darf nach einem Neustart keine Sekunde
    ungeschuetzt sein.
    """
    quelle = (WURZEL / "crypto_engine.py").read_text(encoding="utf-8")
    assert quelle.count("bereitschaft.darf_kaufen()") == 1, \
        "Die Sperre darf an genau einer Stelle abgefragt werden: im Kaufweg"

    kaufweg = quelle.index("def pruefe_kandidat")
    stelle = quelle.index("bereitschaft.darf_kaufen()")
    assert stelle > kaufweg, "Die Abfrage steht nicht im Kaufweg"

    # In der Positionsueberwachung darf sie nicht vorkommen.
    start = quelle.index("def pruefe_positionen")
    ende = quelle.index("def ", start + 10)
    assert "darf_kaufen" not in quelle[start:ende], \
        "Die Positionsueberwachung darf nie auf die Anlaufsperre warten"


def test_kaufweg_bricht_mit_klarem_grund_ab(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADINGBOT_TEST_STATE_DIR", str(tmp_path))
    import config
    monkeypatch.setattr(config, "STARTUP_TRADING_GRACE_MINUTES", 15.0, raising=False)
    import crypto_engine as ce
    monkeypatch.setattr(ce, "_state_root", lambda: tmp_path)

    from risk_pots import RiskPotManager
    from universe.manager import UniverseManager
    from universe.modelle import UniverseZustand

    class Hub:
        def broker(self, name):
            return None

        def verbinde(self, name):
            return False

    engine = ce.CryptoEngine(
        hub=Hub(), risiko=RiskPotManager(["etoro", "okx"]),
        universum=UniverseManager(UniverseZustand(tmp_path / "u.json")))

    ergebnis = engine.pruefe_kandidat("BTC")
    assert ergebnis.get("gekauft") is False
    assert "Anlaufsperre" in str(ergebnis.get("grund", ""))
    assert "frei in" in str(ergebnis.get("grund", "")), \
        "Der Grund muss die Restwartezeit nennen"


def test_konfiguration_hat_die_vorgabe_15_minuten():
    import config
    assert config.STARTUP_TRADING_GRACE_MINUTES == pytest.approx(15.0)
