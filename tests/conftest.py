"""Gemeinsame Testvorbereitung (neu in v8.1.5).

WARUM ES DIESE DATEI GIBT
=========================
Die Testsuite hatte keine gemeinsame Vorbereitung. Jede Datei setzte
``TRADINGBOT_TEST_STATE_DIR`` selbst -- manche erst in einer Funktion, manche
gar nicht. Module, die ihren Zustandspfad beim IMPORT festlegen, hatten zu
diesem Zeitpunkt noch keinen Testordner gesehen und schrieben deshalb in den
Release-Baum. Betroffen waren sechs Testdateien.

Das war nicht nur unsauber. Es hat zwei echte Fehlerbilder erzeugt:

1. ``volltest.py`` meldete nach jedem Testlauf zu Recht "Laufzeitdatei im
   Release-Baum". Vor jedem Packen musste jemand von Hand aufraeumen -- und
   wer es vergass, packte eine fremde Entscheidungsdatenbank mit ins Release.

2. Das KI-Tagesbudget in ``ai_router_usage.json`` sammelte sich ueber
   Testlaeufe hinweg im Release-Baum auf. Irgendwann war das Terra-Budget
   aufgebraucht, und ``test_universe_research`` schlug fehl -- mit einem
   Fehlerbild, das mit dem gepruefen Verhalten nichts zu tun hatte. Genau so
   entstehen Tests, denen man nicht mehr glaubt.

pytest laedt ``conftest.py`` VOR den Testmodulen. Nur hier laesst sich die
Variable so frueh setzen, dass auch Module sie sehen, die ihren Pfad beim
Import bestimmen.

Einzelne Tests duerfen weiterhin ``monkeypatch.setenv`` benutzen, um sich ein
eigenes Verzeichnis zu geben -- das hier ist nur der sichere Grundzustand.
"""
from __future__ import annotations

import os
import tempfile
import json
from datetime import datetime, timezone
from pathlib import Path
import pytest

# Even a direct pytest invocation must not read a configured installation.
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from offline_validation import IsolationError, refuse_unsafe_direct_pytest
try:
    refuse_unsafe_direct_pytest(Path(__file__).resolve().parent.parent)
except IsolationError as exc:
    raise pytest.UsageError(str(exc)) from None

# Muss VOR jedem Import eines Botmoduls stehen.
_STANDARD = Path(tempfile.mkdtemp(prefix="tradingbot_tests_"))
os.environ["TRADINGBOT_TEST_STATE_DIR"] = str(_STANDARD)


@pytest.fixture(autouse=True)
def _isolierter_laufzeitzustand(tmp_path, monkeypatch):
    """Jeder Test bekommt eigene Broker-/Budget-/Ledger-Dateien."""
    monkeypatch.setenv("TRADINGBOT_TEST_STATE_DIR", str(tmp_path))
    # Existing provider-isolation fixtures predate the Fed source. Dedicated
    # Fed tests opt in explicitly and use fake responses; the network tripwire
    # remains enabled for every test.
    import config
    monkeypatch.setattr(config, "NEWS_SOURCE_FEDERAL_RESERVE_ENABLED", False, raising=False)
    # Im Test ist der Providerverbrauch definitionsgemaess bekannt: null.
    # Damit pruefen API-Fakes die eigentliche Funktion und nicht den
    # einmaligen Produktionsschutz fuer einen unbekannten ersten Upgradetag.
    budget = {
        "version": 1,
        "budgets": {"fmp": {
            "day": datetime.now(timezone.utc).date().isoformat(),
            "used": 0, "automatic_used": 0,
            "blocked_until": 0.0, "block_reason": "",
        }},
    }
    (tmp_path / "api_daily_budgets.json").write_text(
        json.dumps(budget), encoding="utf-8")
    yield

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
