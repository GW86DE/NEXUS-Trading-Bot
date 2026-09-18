"""Der Aktien-Universumslauf (v8.1.3).

Anlass: Selektor und Manager gab es seit v7 -- den Aufruf nicht. Der
Aktien-Universumszustand blieb dadurch dauerhaft leer, der feste Kern kam
nie an, und der Instrumentenfilter lief nie.
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
def runner(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADINGBOT_TEST_STATE_DIR", str(tmp_path))
    from universe.manager import UniverseManager
    from universe.modelle import UniverseZustand
    import stock_universe_runner as sur

    manager = UniverseManager(UniverseZustand(tmp_path / "u.json"))
    gemeldet: list[str] = []
    lauf = sur.StockUniverseRunner(manager, melder=lambda text, **k: gemeldet.append(text))
    return lauf, manager, gemeldet


def leere_auswahl(monkeypatch, rangliste=()):
    """Selektor ersetzen -- kein Netz, keine FMP-Abfrage im Test."""
    import universe.stock_selector as ss

    class FakeSelektor:
        def __init__(self, *a, **k):
            pass

        def auswahl(self, instrumente=None, **_kwargs):
            return {"broker": "etoro", "asset_type": "stock",
                    "katalog_gesamt": 250, "pool": len(rangliste),
                    "rangliste": list(rangliste)}

    monkeypatch.setattr(ss, "StockUniverseSelector", FakeSelektor)


# ---------------------------------------------------------------------------
# Der eigentliche Fehler: der Lauf fand nie statt
# ---------------------------------------------------------------------------
def test_lauf_setzt_den_festen_kern(runner, monkeypatch):
    lauf, manager, _ = runner
    leere_auswahl(monkeypatch)

    ergebnis = lauf.lauf()

    import config
    erwartet = min(len(config.STOCK_CORE_SYMBOLS), config.STOCK_CORE_LIMIT)
    assert ergebnis["ok"] is True
    assert ergebnis["beobachtet"] == erwartet
    assert manager.zustand.hole("etoro", "AAPL") is not None


def test_lauf_ohne_datenquelle_setzt_trotzdem_den_kern(runner, monkeypatch):
    """Eine ausgefallene Referenzquelle darf die Aktienseite nicht leeren."""
    lauf, manager, _ = runner
    import universe.stock_selector as ss

    class KaputterSelektor:
        def __init__(self, *a, **k):
            raise RuntimeError("FMP nicht erreichbar")

    monkeypatch.setattr(ss, "StockUniverseSelector", KaputterSelektor)

    ergebnis = lauf.lauf()
    assert ergebnis["ok"] is True
    assert ergebnis["beobachtet"] > 0, "Der Kern muss auch ohne Bewertung stehen"
    assert "FMP" in lauf.letzter_fehler


def test_dynamische_aktie_wird_ohne_rueckfrage_beobachtet(runner, monkeypatch):
    """GEAENDERT IN v8.1.5 -- auf Georgs ausdrueckliche Vorgabe.

    Bis v8.1.4 wurde nur vorgeschlagen und auf seine Telegram-Freigabe
    gewartet. Jetzt nimmt der Lauf selbst auf -- aber in BEOBACHTUNG, nicht
    direkt in den handelbaren Teil.
    """
    from universe.modelle import AKTIV, BEOBACHTUNG, TIER_KANDIDAT, UniverseKandidat, UniverseScore

    lauf, manager, gemeldet = runner
    kandidat = UniverseKandidat(symbol="ZZTOP", broker="etoro", asset_type="stock")
    leere_auswahl(monkeypatch, [{
        "kandidat": kandidat, "score": UniverseScore(gesamt=0.9), "rang": 1,
        "tier": TIER_KANDIDAT, "tier_begruendung": "hoher Umsatz",
        "sicherheitsabgang": "",
    }])

    ergebnis = lauf.lauf()

    mitglied = manager.zustand.hole("etoro", "ZZTOP")
    assert mitglied is not None and mitglied.zustand == BEOBACHTUNG
    assert mitglied.zustand != AKTIV, "Beobachtet heisst noch nicht handelbar"
    assert mitglied.handelbar is False
    assert ergebnis["beobachtung"] == 1
    assert ergebnis["vorschlaege"] == 0        # nichts haengt mehr an einer Freigabe

    text = "\n".join(gemeldet)
    assert "ZZTOP" in text
    assert "Bewährung" in text or "Bewaehrung" in text


def test_aufnahmemeldung_nennt_die_grenzen(runner):
    """"Ohne Rueckfrage" darf nicht wie "ohne Regeln" klingen."""
    import config
    import stock_universe_runner as sur

    text = sur.StockUniverseRunner.bewaehrungstext(["ZZTOP"])
    assert "ZZTOP" in text
    assert f"{config.STOCK_UNIVERSE_PROBATION_HOURS:.0f} h" in text
    assert str(config.STOCK_CORE_LIMIT) in text
    assert str(config.STOCK_UNIVERSE_DYNAMIC_LIMIT) in text
    assert str(config.STOCK_UNIVERSE_MAX_CHANGES_PER_RUN) in text
    assert "noch nicht handelbar" in text.lower()


def test_vorschlagstext_verspricht_keine_aufnahme():
    """Was hier landet, haengt an einem Deckel -- nicht mehr an einer Freigabe."""
    import stock_universe_runner as sur
    text = sur.StockUniverseRunner.vorschlagstext(
        [{"symbol": "ZZTOP", "rang": 1, "score": 0.9, "begruendung": "Umsatz"}])
    assert "nicht aufgenommen" in text.lower()
    assert "kein platz frei" in text.lower()
    assert "freigabe" not in text.lower(), \
        "Es gibt keine Freigabe mehr -- der Text darf keine versprechen"


# ---------------------------------------------------------------------------
# Takt
# ---------------------------------------------------------------------------
def test_lauf_wartet_nicht_auf_die_boersenoeffnung(runner, monkeypatch):
    """Sonst waere das Universum am Wochenende leer -- genau dann schaut man hin."""
    lauf, _, _ = runner
    from scheduler_v7 import Taktgeber

    aufrufe = []

    class Takt(Taktgeber):
        def faellig(self, asset_type, arbeit, **kw):
            aufrufe.append(kw.get("markt_pruefen"))
            return True, "Test"

    lauf.takt = Takt()
    assert lauf.faellig() is True
    assert aufrufe == [False], "Der Universumslauf darf nicht am Marktfenster haengen"


def test_zyklus_ueberspringt_wenn_nicht_faellig(runner, monkeypatch):
    lauf, manager, _ = runner
    leere_auswahl(monkeypatch)
    monkeypatch.setattr(lauf, "faellig", lambda: False)
    ergebnis = lauf.zyklus()
    assert ergebnis.get("uebersprungen")
    assert manager.zustand.fuer_broker("etoro") == []


# ---------------------------------------------------------------------------
# Keine zweite Brokerverbindung
# ---------------------------------------------------------------------------
def test_lauf_oeffnet_keine_eigene_brokerverbindung():
    quelle = (WURZEL / "stock_universe_runner.py").read_text(encoding="utf-8")
    assert "StockUniverseSelector(broker=None" in quelle, \
        "Der Lauf darf keine zweite eToro-Sitzung aufmachen"
    for verboten in ("BrokerHub", "verbinde(", "platziere", "schliesse_position"):
        assert verboten not in quelle, f"{verboten} gehoert nicht in den Universumslauf"


def test_supervisor_haengt_nicht_am_hilfsfaden():
    """Der Universumsfaden darf den Start-/Stoppablauf nicht veraendern."""
    quelle = (WURZEL / "nexus_start.py").read_text(encoding="utf-8")
    stelle = quelle.index("starte_aktien_universum, args=")
    umfeld = quelle[stelle:stelle + 300]
    assert "threads.append" not in umfeld, \
        "Der Hilfsfaden darf den Supervisor nicht am Leben halten"


def test_dienst_startet_den_universumslauf():
    quelle = (WURZEL / "nexus_start.py").read_text(encoding="utf-8")
    assert "def starte_aktien_universum" in quelle
    assert "name=\"aktienuniversum\"" in quelle
