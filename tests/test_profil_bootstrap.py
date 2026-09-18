"""Regression: gespeichertes Risiko-Profil muss beim Prozessstart wirksam sein.

Der Fehler in v6.0: 'aktives_profil.txt' wurde geschrieben und angezeigt,
aber nie auf config angewandt. Nach jedem Neustart des Pi galten wieder die
Basiswerte aus config.py -- bei KONSERVATIV also 8 statt 2 offene Positionen.
Die gesamte Testsuite blieb dabei gruen, weil kein Test den Kaltstart geprueft
hat. Genau das holen die Tests hier nach.
"""
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

import config
import profiles

ROOT = Path(__file__).resolve().parents[1]
PROFIL_SCHLUESSEL = sorted(
    {k for p in profiles.PROFILES.values() for k in p["werte"]}
)


@pytest.fixture
def config_wiederherstellen(monkeypatch):
    """config ist ein Singleton -- alle beruehrten Werte zuruecksetzen."""
    for key in PROFIL_SCHLUESSEL + [
        "ACTIVE_PROFILE", "PROFILE_SOURCE", "PROFILE_BOOTSTRAP_OK",
        "PROFILE_BOOTSTRAP_ERROR", "BACKTEST_POSITION_PCT", "PROFILE_FILE",
    ]:
        if hasattr(config, key):
            monkeypatch.setattr(config, key, getattr(config, key))
    monkeypatch.delenv("RISK_PROFILE", raising=False)
    yield


# ---------------------------------------------------------------------------
# 1. Kaltstart in einem echten Subprozess -- das ist der Pi-Neustart.
#    Kein monkeypatch kann dieses Ergebnis beschoenigen.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("profil", sorted(profiles.PROFILES))
def test_kaltstart_wendet_gespeichertes_profil_an(profil):
    skript = textwrap.dedent(
        """
        import json, config
        print(json.dumps({k: getattr(config, k, None) for k in %r}))
        """ % (PROFIL_SCHLUESSEL + ["ACTIVE_PROFILE", "PROFILE_BOOTSTRAP_OK"],)
    )
    umgebung = dict(os.environ, RISK_PROFILE=profil, PYTHONPATH=str(ROOT))
    ergebnis = subprocess.run(
        [sys.executable, "-c", skript],
        cwd=str(ROOT), env=umgebung, capture_output=True, text=True, timeout=120,
    )
    assert ergebnis.returncode == 0, ergebnis.stderr
    import json
    gelesen = json.loads(ergebnis.stdout.strip().splitlines()[-1])

    assert gelesen["ACTIVE_PROFILE"] == profil
    assert gelesen["PROFILE_BOOTSTRAP_OK"] is True
    for key, soll in profiles.PROFILES[profil]["werte"].items():
        assert gelesen[key] == soll, (
            f"{key}: Prozess startete mit {gelesen[key]}, Profil {profil} "
            f"verlangt {soll}"
        )


def test_konservativ_begrenzt_positionen_wirklich(config_wiederherstellen, tmp_path):
    """Der konkrete Schadensfall: 8 offene Positionen statt 2."""
    datei = tmp_path / "aktives_profil.txt"
    datei.write_text("konservativ", encoding="utf-8")
    config.PROFILE_FILE = datei

    config._profil_bootstrap()

    assert config.MAX_OPEN_POSITIONS == 2
    assert config.MAX_POSITION_PCT == 0.03
    assert config.ATR_STOP_MULTIPLIER == 2.5
    assert config.PROFILE_BOOTSTRAP_OK is True


# ---------------------------------------------------------------------------
# 2. Fehlerfaelle
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("inhalt", ["ultra", "OFFENSIV_XXL", "", "   \n"])
def test_unbekanntes_profil_sperrt_und_setzt_konservativ(
    config_wiederherstellen, tmp_path, inhalt
):
    """Beschaedigte Datei: nicht raten, sondern engstellen und melden.

    Leerer Inhalt zaehlt hier bewusst als beschaedigt und nicht als
    'keine Datei' -- eine vorhandene, aber leere Datei deutet auf einen
    abgebrochenen Schreibvorgang hin.
    """
    datei = tmp_path / "aktives_profil.txt"
    datei.write_text(inhalt, encoding="utf-8")
    config.PROFILE_FILE = datei

    config._profil_bootstrap()

    assert config.PROFILE_BOOTSTRAP_OK is False
    erwartet = "Unbekanntes Profil" if inhalt.strip() else "leer"
    assert erwartet in config.PROFILE_BOOTSTRAP_ERROR
    assert config.MAX_OPEN_POSITIONS == 2      # Notbremse KONSERVATIV
    assert config.RISK_PER_TRADE_PCT == 0.005


def test_fehlende_datei_nutzt_default_ohne_sperre(config_wiederherstellen, tmp_path):
    """Erstinstallation ist kein Fehler -- Default anwenden, aber benennen."""
    config.PROFILE_FILE = tmp_path / "gibt_es_nicht.txt"

    config._profil_bootstrap()

    assert config.PROFILE_BOOTSTRAP_OK is True
    assert config.ACTIVE_PROFILE == profiles.DEFAULT_PROFILE
    assert config.PROFILE_SOURCE == "keine Datei"


def test_import_ueberlebt_kaputte_profiles(config_wiederherstellen, monkeypatch, tmp_path):
    """Ein Fehler im Bootstrap darf config nicht unimportierbar machen."""
    config.PROFILE_FILE = tmp_path / "aktives_profil.txt"
    config.PROFILE_FILE.write_text("ausgewogen", encoding="utf-8")
    monkeypatch.setattr(
        profiles, "apply_profile",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("kaputt")),
    )
    with pytest.raises(RuntimeError):
        config._profil_bootstrap()


# ---------------------------------------------------------------------------
# 3. Abgeleitete Werte duerfen nicht auf dem Basiswert haengenbleiben
# ---------------------------------------------------------------------------
def test_backtest_positionsgroesse_folgt_dem_profil(config_wiederherstellen, tmp_path):
    """BACKTEST_POSITION_PCT wird in config.py aus MAX_POSITION_PCT kopiert.

    Ohne Nachziehen rechnet der Backtest mit anderen Positionsgroessen als
    der Live-Trader -- und der Vergleich mit Buy-and-Hold waere wertlos.
    """
    datei = tmp_path / "aktives_profil.txt"
    datei.write_text("offensiv", encoding="utf-8")
    config.PROFILE_FILE = datei

    config._profil_bootstrap()

    assert config.MAX_POSITION_PCT == 0.15
    assert config.BACKTEST_POSITION_PCT == config.MAX_POSITION_PCT


# ---------------------------------------------------------------------------
# 4. Strukturschutz: kein Modul darf config-Werte beim Import kopieren
# ---------------------------------------------------------------------------
def test_kein_modul_kopiert_config_werte_beim_import():
    """'from config import X' friert den Wert ein und haengt den Bootstrap ab.

    Aktuell nutzen alle Module 'config.X'. Dieser Test haelt das so fest.
    """
    treffer = []
    for pfad in ROOT.glob("*.py"):
        for nr, zeile in enumerate(pfad.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
            if zeile.lstrip().startswith("from config import"):
                treffer.append(f"{pfad.name}:{nr}")
    assert not treffer, (
        "Diese Stellen kopieren config-Werte beim Import und ignorieren "
        f"danach jede Profilumschaltung: {treffer}"
    )
