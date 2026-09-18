"""Kuratierte Underdogs muessen tatsaechlich handelbar sein (v9.3, CR-06).

Befund bei der Pruefung von v9.2, nachgerechnet:

    Katalog: 250 | fester Kern: 75 | aktiv: 75
    Underdogs gesamt:       25
    davon im Katalog:       25
    davon im festen Kern:    0
    davon aktiv handelbar:   0

``UNDERDOGS_AKTIV`` stand auf True, und keiner der 25 Werte konnte gehandelt
werden. Ursache war der blinde Schnitt ``STOCK_CATALOG_SYMBOLS[:75]`` bei der
Katalogreihenfolge EU-Kern -> US-Kern -> Underdogs; die Underdogs stehen dort
ab Position 190.

Georgs Vorgabe fuer 9.3: 65 Standard + 10 Underdogs + 25 dynamisch.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("TRADINGBOT_TEST_STATE_DIR", "/tmp/tradingbot_tests")
Path(os.environ["TRADINGBOT_TEST_STATE_DIR"]).mkdir(parents=True, exist_ok=True)


def symbole(rows) -> set:
    out = set()
    for e in rows or []:
        if isinstance(e, dict):
            out.add(str(e.get("symbol", "")).upper())
        elif isinstance(e, (list, tuple)):
            out.add(str(e[0]).upper())
        else:
            out.add(str(e).upper())
    return out


def test_bei_eingeschaltetem_schalter_sind_underdogs_wirklich_aktiv():
    """Der Kern des Befunds: vorher waren es null."""
    import config

    if not config.UNDERDOGS_AKTIV:
        pytest.skip("Underdogs sind bewusst ausgeschaltet")
    kern = [x for x in config.STOCK_FIXED_CORE_SYMBOLS if x.get("underdog")]
    assert len(kern) > 0, (
        "UNDERDOGS_AKTIV=True, aber kein Underdog im festen Kern -- "
        "genau der Zustand, den v9.3 behebt")
    assert len(kern) == config.STOCK_FIXED_UNDERDOG_SLOTS


def test_die_aufteilung_entspricht_der_vorgabe():
    """65 Standard + 10 Underdogs + 25 dynamisch."""
    import config

    assert config.STOCK_FIXED_UNDERDOG_SLOTS == 10
    assert config.STOCK_FIXED_STANDARD_SLOTS == 65
    assert config.STOCK_DYNAMIC_SLOTS == 25
    assert (config.STOCK_FIXED_STANDARD_SLOTS
            + config.STOCK_FIXED_UNDERDOG_SLOTS) == config.STOCK_FIXED_CORE_LIMIT


def test_das_aktivlimit_wird_trotz_kategorien_eingehalten():
    import config

    assert len(config.STOCK_FIXED_CORE_SYMBOLS) == config.STOCK_FIXED_CORE_LIMIT
    assert len(config.STOCK_SYMBOLS) <= 100


def test_underdogs_behalten_ihr_kennzeichen_auf_dem_ganzen_weg():
    """Ein Underdog darf nie als normaler Broad-Wert etikettiert werden --
    sonst verliert er das strengere Screening und den kleineren
    Positionsfaktor."""
    import config
    import watchlist

    kuratiert = {s for s, _ in watchlist.UNDERDOG_KANDIDATEN}
    katalog = {str(x.get("symbol", "")).upper(): x
               for x in config.STOCK_CATALOG_SYMBOLS if isinstance(x, dict)}
    for symbol in kuratiert:
        assert symbol in katalog, f"{symbol} fehlt im Katalog"
        assert katalog[symbol].get("underdog") is True, (
            f"{symbol} hat sein Underdog-Kennzeichen verloren")

    for eintrag in config.STOCK_FIXED_CORE_SYMBOLS:
        if str(eintrag.get("symbol", "")).upper() in kuratiert:
            assert eintrag.get("underdog") is True


def test_ein_standardwert_wird_nicht_versehentlich_zum_underdog():
    import config
    import watchlist

    kuratiert = {s for s, _ in watchlist.UNDERDOG_KANDIDATEN}
    for eintrag in config.STOCK_CATALOG_SYMBOLS:
        if not isinstance(eintrag, dict) or not eintrag.get("underdog"):
            continue
        assert str(eintrag.get("symbol", "")).upper() in kuratiert, (
            f"{eintrag.get('symbol')} ist kein kuratierter Underdog")


def test_freie_underdog_plaetze_fallen_an_standardwerte():
    """Der Kern bleibt immer vollstaendig, auch wenn zu wenige Underdogs da
    sind."""
    import config

    katalog = ([{"symbol": f"STD{i}"} for i in range(50)]
               + [{"symbol": "UD1", "underdog": True}])
    kern = config._festen_kern_bilden(katalog, 8, 5)
    assert len(kern) == 13, "Der Kern bleibt vollstaendig: 8 + 5 Plaetze"
    assert sum(1 for x in kern if x.get("underdog")) == 1
    assert sum(1 for x in kern if not x.get("underdog")) == 12, (
        "Die 4 unbesetzten Underdog-Plaetze gehen an Standardwerte")


def test_ausgeschaltete_underdogs_belegen_keine_plaetze():
    import config

    katalog = ([{"symbol": f"STD{i}"} for i in range(80)]
               + [{"symbol": f"UD{i}", "underdog": True} for i in range(25)])
    kern = config._festen_kern_bilden(katalog, 75, 0)
    assert len(kern) == 75
    assert not any(x.get("underdog") for x in kern)


def test_katalogmetadaten_schlagen_die_pauschale_broad_kennzeichnung(monkeypatch):
    """Ein im Katalog bekannter Underdog darf beim dynamischen Weg nicht auf
    underdog=False zurueckgesetzt werden."""
    import approved_universe

    monkeypatch.setattr(approved_universe, "load",
                        lambda: {"stocks": [{"symbol": "CROX", "sector": "Test"}]})
    rows = approved_universe.approved_stock_rows()
    assert rows, "Der freigegebene Wert muss materialisiert werden"
    assert rows[0]["underdog"] is True, (
        "CROX steht als Underdog im Katalog und bleibt einer")
    assert rows[0]["broad"] is False


def test_unbekannter_research_wert_bleibt_broad(monkeypatch):
    import approved_universe

    monkeypatch.setattr(approved_universe, "load",
                        lambda: {"stocks": [{"symbol": "ZZZZ", "sector": "Test"}]})
    rows = approved_universe.approved_stock_rows()
    assert rows[0]["underdog"] is False
    assert rows[0]["broad"] is True


def test_die_webui_weist_underdogs_getrennt_aus():
    import universe_overview

    daten = universe_overview.broker_kennzahlen("etoro")
    for feld in ("underdogs_eingeschaltet", "underdogs_im_katalog",
                 "underdogs_kernplaetze", "underdogs_im_kern",
                 "underdogs_aktiv", "underdogs_blockiert"):
        assert feld in daten, f"{feld} fehlt in den Kennzahlen"

    quelle = (Path(__file__).resolve().parent.parent / "webui" / "static"
              / "universe.js").read_text(encoding="utf-8")
    assert "underdogZeile" in quelle
    assert "underdogs_im_katalog" in quelle


def test_underdogs_umgehen_kein_screening():
    """Das strengere Screening bleibt zwingend.

    Die Aktivierung darf keine einzige Pruefung aufweichen. Der harte Riegel
    steht in live_trader.py: ein Underdog ohne bestandenes Screening wird
    nicht gekauft, egal wie das Chartbild aussieht.
    """
    import underdog_screening  # noqa: F401  (muss weiterhin existieren)

    quelle = (Path(__file__).resolve().parent.parent
              / "live_trader.py").read_text(encoding="utf-8")
    assert 'if ist_underdog and inst.name not in underdog_freigabe["symbole"]:' in quelle, (
        "Der Screening-Riegel fuer Underdogs muss unveraendert bestehen")
    assert 'blocked=underdog_screening' in quelle
    # Und die Marktqualitaet wird fuer Underdogs weiterhin strenger bewertet.
    assert "marktqualitaet.acceptable(quote, ist_underdog" in quelle
