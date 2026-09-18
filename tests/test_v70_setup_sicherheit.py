"""Tests der Einrichtung und des Geheimnisschutzes (v7.0 NEXUS).

Anlass war ein echter Vorfall: Weil das Einrichtungsmenue kein Feld fuer den
OpenAI-Schluessel hatte, wurde der Schluessel in das Feld 'Luna-Modell'
eingetragen. Danach stand er bei jedem Verbindungstest im Klartext auf dem
Bildschirm.

Diese Tests halten beide Fehlerursachen fest:
    1. ein Modellfeld darf keinen Schluessel annehmen,
    2. keine Statusausgabe darf ein Geheimnis unmaskiert zeigen.
"""
from __future__ import annotations

import importlib
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("TRADINGBOT_TEST_STATE_DIR", "/tmp/tradingbot_tests")
Path(os.environ["TRADINGBOT_TEST_STATE_DIR"]).mkdir(parents=True, exist_ok=True)

import nexus_setup  # noqa: E402
import venv_guard  # noqa: E402

# Zusammengesetzt, damit ein statischer Release-Scanner den Testwert nicht
# mit einem versehentlich ausgelieferten echten Schluessel verwechselt.
BEISPIELSCHLUESSEL = "sk-" + "proj-DUMMY-NICHT-ECHT-" + "a" * 36


# ---------------------------------------------------------------------------
# Erkennung von Geheimnissen
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("wert", [
    BEISPIELSCHLUESSEL,
    "sk-abc123def456",
    "SK-GROSSGESCHRIEBEN-TROTZDEM-EIN-SCHLUESSEL",
    "xoxb-1234-5678-abcdefg",
    "ghp_abcdefghijklmnopqrstuvwxyz0123456789",
    "a" * 41,                       # lang und ohne Leerzeichen
])
def test_schluessel_werden_erkannt(wert):
    assert nexus_setup.ist_geheimnisverdaechtig(wert) is True


@pytest.mark.parametrize("wert", [
    "gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.6-sol", "gpt-4.1-nano", "",
])
def test_modellnamen_werden_nicht_als_schluessel_erkannt(wert):
    assert nexus_setup.ist_geheimnisverdaechtig(wert) is False


# ---------------------------------------------------------------------------
# Maskierung
# ---------------------------------------------------------------------------
def test_maskierung_zeigt_niemals_den_ganzen_wert():
    ausgabe = nexus_setup.maskiere(BEISPIELSCHLUESSEL)
    assert BEISPIELSCHLUESSEL not in ausgabe
    # Der mittlere, eigentlich geheime Teil darf nicht auftauchen.
    assert "5nEh5YR5w9KzmYVU" not in ausgabe
    assert str(len(BEISPIELSCHLUESSEL)) in ausgabe


def test_maskierung_kurzer_werte_zeigt_gar_nichts():
    ausgabe = nexus_setup.maskiere("geheim12")
    assert "geheim" not in ausgabe
    assert ausgabe.startswith("*")


def test_maskierung_leerer_werte_ist_eindeutig():
    assert "nicht gesetzt" in nexus_setup.maskiere("")


# ---------------------------------------------------------------------------
# Modellfeld
# ---------------------------------------------------------------------------
def test_modellfeld_lehnt_schluessel_ab_und_fragt_erneut(monkeypatch, capsys):
    eingaben = iter([BEISPIELSCHLUESSEL, "gpt-5.6-luna"])
    monkeypatch.setattr("builtins.input", lambda *a, **k: next(eingaben))

    ergebnis = nexus_setup._modellfeld("Luna-Modell", "gpt-5.6-luna")
    ausgabe = capsys.readouterr().out

    assert ergebnis == "gpt-5.6-luna"
    assert "API-Schluessel" in ausgabe
    assert BEISPIELSCHLUESSEL not in ausgabe


def test_modellfeld_nimmt_gueltigen_namen_direkt(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda *a, **k: "gpt-5.6-terra")
    assert nexus_setup._modellfeld("Terra-Modell", "gpt-5.6-terra") == "gpt-5.6-terra"


# ---------------------------------------------------------------------------
# Verdeckte Eingabe
# ---------------------------------------------------------------------------
def test_geheime_eingabe_bestaetigt_die_zeichenzahl(monkeypatch, capsys):
    monkeypatch.setattr(nexus_setup.getpass, "getpass", lambda *a, **k: "MeinSecret123456")
    wert = nexus_setup._geheim("Demo Secret")
    ausgabe = capsys.readouterr().out

    assert wert == "MeinSecret123456"
    assert "unsichtbar" in ausgabe
    assert "16 Zeichen" in ausgabe
    assert "MeinSecret123456" not in ausgabe


def test_leere_eingabe_behaelt_den_bisherigen_wert(monkeypatch, capsys):
    monkeypatch.setattr(nexus_setup.getpass, "getpass", lambda *a, **k: "")
    wert = nexus_setup._geheim("Demo Secret", vorhanden="ALTERWERT")
    assert wert == "ALTERWERT"
    assert "unveraendert" in capsys.readouterr().out


def test_terminal_ohne_verdeckte_eingabe_faellt_sichtbar_zurueck(monkeypatch, capsys):
    """Lieber sichtbar eingeben als gar nicht -- aber mit Warnung."""
    def kaputt(*args, **kwargs):
        raise OSError("kein tty")

    monkeypatch.setattr(nexus_setup.getpass, "getpass", kaputt)
    monkeypatch.setattr("builtins.input", lambda *a, **k: "Ersatzeingabe")
    wert = nexus_setup._geheim("Demo Secret")
    ausgabe = capsys.readouterr().out

    assert wert == "Ersatzeingabe"
    assert "SICHTBAR" in ausgabe


# ---------------------------------------------------------------------------
# Aufraeumen einer bereits verunreinigten Datei
# ---------------------------------------------------------------------------
def test_schluessel_im_modellfeld_wird_entfernt(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(nexus_setup, "ROOT", tmp_path)
    datei = tmp_path / "ai_router_settings.json"
    datei.write_text(json.dumps({
        "luna_model": BEISPIELSCHLUESSEL,
        "terra_model": BEISPIELSCHLUESSEL,
        "enabled": True,
    }), encoding="utf-8")

    anzahl = nexus_setup.aufraeumen()
    daten = json.loads(datei.read_text(encoding="utf-8"))
    ausgabe = capsys.readouterr().out

    assert anzahl == 2
    assert daten["luna_model"] == "gpt-5.6-luna"
    assert daten["terra_model"] == "gpt-5.6-terra"
    assert daten["enabled"] is True, "Andere Einstellungen bleiben unberuehrt"
    assert "widerrufen" in ausgabe
    assert BEISPIELSCHLUESSEL not in ausgabe


def test_aufraeumen_laesst_saubere_dateien_in_ruhe(tmp_path, monkeypatch):
    monkeypatch.setattr(nexus_setup, "ROOT", tmp_path)
    datei = tmp_path / "ai_router_settings.json"
    original = {"luna_model": "gpt-5.6-luna", "terra_model": "gpt-5.6-terra"}
    datei.write_text(json.dumps(original), encoding="utf-8")

    assert nexus_setup.aufraeumen() == 0
    assert json.loads(datei.read_text(encoding="utf-8")) == original


# ---------------------------------------------------------------------------
# Schutz in der Konfiguration
# ---------------------------------------------------------------------------
def test_config_verwirft_schluessel_im_modellfeld():
    """Zweite Verteidigungslinie: auch eine von Hand verbogene Datei leakt nicht."""
    import config
    pruefer = config._modellname_pruefen
    assert pruefer(BEISPIELSCHLUESSEL, "gpt-5.6-luna") == "gpt-5.6-luna"
    assert pruefer("gpt-5.6-luna", "gpt-5.6-terra") == "gpt-5.6-luna"
    assert pruefer("", "gpt-5.6-terra") == "gpt-5.6-terra"


def test_config_modelle_enthalten_niemals_ein_geheimnis(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADINGBOT_TEST_STATE_DIR", str(tmp_path))
    import config
    importlib.reload(config)
    for feld in ("AI_LUNA_MODEL", "AI_TERRA_MODEL", "AI_ATTENTION_MODEL"):
        assert not nexus_setup.ist_geheimnisverdaechtig(getattr(config, feld))


# ---------------------------------------------------------------------------
# Python-Umgebung
# ---------------------------------------------------------------------------
def test_venv_guard_ist_still_wenn_alles_da_ist():
    """pandas und requests sind hier vorhanden -- es darf nichts passieren."""
    assert venv_guard.fehlende_pakete() == []
    venv_guard.sicherstellen()      # darf nicht abbrechen


def test_venv_guard_meldet_fehlende_pakete(monkeypatch):
    monkeypatch.setattr(venv_guard, "PFLICHTPAKETE", ("gibtesnicht_xyz",))
    assert venv_guard.fehlende_pakete() == ["gibtesnicht_xyz"]


def test_venv_guard_bricht_mit_klarer_anleitung_ab(monkeypatch, capsys):
    monkeypatch.setattr(venv_guard, "PFLICHTPAKETE", ("gibtesnicht_xyz",))
    monkeypatch.setattr(venv_guard, "venv_python", lambda: None)
    with pytest.raises(SystemExit) as info:
        venv_guard.sicherstellen()
    ausgabe = capsys.readouterr().out

    assert info.value.code == 78
    assert "Nexus_Starten.sh" in ausgabe
    assert "Pi_Installieren.sh" in ausgabe
    assert "Traceback" not in ausgabe


# ---------------------------------------------------------------------------
# Dienststart
# ---------------------------------------------------------------------------
def test_dienst_startet_beide_seiten_wenn_okx_aktiv(monkeypatch):
    import config
    import pi_service
    monkeypatch.setattr(config, "OKX_ENABLED", True, raising=False)

    gerufen = []
    fake = type("M", (), {"laufe": staticmethod(lambda **k: gerufen.append("nexus"))})
    monkeypatch.setitem(sys.modules, "nexus_start", fake)
    pi_service._starte_handel()
    assert gerufen == ["nexus"]


def test_dienst_faellt_ohne_okx_auf_den_aktienkern_zurueck(monkeypatch):
    import config
    import pi_service
    monkeypatch.setattr(config, "OKX_ENABLED", False, raising=False)

    # Seit dem gemeinsamen Supervisor startet auch bei ausgeschaltetem OKX
    # der Universums-Hilfsfaden. Dieser Routingtest darf keinen echten
    # minutenlangen Researchworker in die folgenden Ledger-Tests entlassen.
    # Aktienstart/Supervisor bleiben echt; nur die sachfremde Arbeit ist ein
    # begrenzter Testdouble, dessen Aufruf und Ende nachgewiesen werden.
    import threading
    import nexus_start
    before = set(threading.enumerate())
    own_stop = threading.Event()
    monkeypatch.setattr(nexus_start, "_beenden", own_stop)
    helper_called = threading.Event()
    monkeypatch.setattr(nexus_start, "starte_aktien_universum",
                        lambda *_a: helper_called.set())
    gerufen = []
    fake = type("M", (), {"run": staticmethod(lambda: gerufen.append("aktien"))})
    monkeypatch.setitem(sys.modules, "live_trader", fake)
    try:
        pi_service._starte_handel()
        assert gerufen == ["aktien"]
        assert helper_called.wait(1.0), "Universumsworker muss gestartet werden"
    finally:
        own_stop.set()
        created = [t for t in threading.enumerate() if t not in before
                   and t.name in {"aktienkern", "aktienuniversum"}]
        for thread in created:
            thread.join(timeout=2.0)
        assert not any(t.is_alive() for t in created), "Testworker muss enden"
