"""9.5.5 -- Werte mit dauerhaft veralteten Kursen parken und nachruecken.

BEFUND vom 02.09.2026, an echten Daten belegt:

eToro lieferte fuer einen Teil der Aktien Kurse mit konstant rund 20 Minuten
Rueckstand. Der Beweis, dass es nicht am Bot lag:

  * Die Zeitachse der Daten lief mit Faktor 1,00 bei festem Versatz von
    20,3 min -- eine verzoegerte Wiedergabe, kein kaputter Zeitstempel.
  * "date" ist das EINZIGE Zeitfeld der eToro-Antwort; ein falsches Feld kann
    NEXUS also gar nicht auswerten.
  * AAPL lief durch denselben Code taufrisch (0,0 min).
  * JPM wich um 0,174 % von einer zweiten Kursquelle ab, bei einer
    Handelsspanne von 0,020 % -- das Neunfache. AAPL traf sie auf 0,000 %.

Diese Werte blieben im aktiven Universum, belegten Kandidatenplaetze und
wurden bei jedem Versuch erneut von der Kursaltergrenze abgelehnt. Im Logbuch
stand allein AVGO sechsmal mit derselben Zeile.

Richtig ist NICHT, die Altersgrenze aufzuweichen -- mit einem 20 Minuten alten
Kurs waeren Stop und Positionsgroesse geraten. Richtig ist, diese Werte aus
dem aktiven Universum zu nehmen und nachruecken zu lassen.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from universe.manager import UniverseManager
from universe.modelle import (AKTIV, KURSE_VERALTET, TIER_ETABLIERT,
                              UniverseKandidat, UniverseScore, UniverseZustand)


def kandidat(symbol):
    return UniverseKandidat(
        symbol=symbol, broker="etoro", asset_type="stock", inst_id=symbol,
        preis=100.0, bid=99.9, ask=100.1, spread_pct=0.002,
        volumen_quote_24h=500_000_000.0, change_24h_pct=0.01, alter_tage=2000.0)


def eintrag(symbol, rang, score=0.7):
    return {"kandidat": kandidat(symbol),
            "score": UniverseScore(gesamt=score, teile={}, stufe="quality"),
            "rang": rang, "tier": TIER_ETABLIERT, "tier_begruendung": "Test"}


@pytest.fixture
def manager(tmp_path, monkeypatch):
    """Ein Universum mit AVGO als festem Kernwert -- so wie in der Anlage."""
    import config
    monkeypatch.setattr(config, "STOCK_FIXED_CORE_SYMBOLS", ("AVGO",),
                        raising=False)
    return UniverseManager(UniverseZustand(tmp_path / "u.json"))


def lauf(manager, symbole, veraltet=None):
    return manager.lauf({
        "broker": "etoro", "asset_type": "stock",
        "rangliste": [eintrag(s, i + 1) for i, s in enumerate(symbole)],
        "veraltete_kurse": dict(veraltet or {})})


def aktiv_machen(manager, *symbole):
    for symbol in symbole:
        m = manager.zustand.hole("etoro", symbol)
        assert m is not None, f"{symbol} fehlt im Universum"
        m.wechsle(AKTIV, "Testfreigabe")
        manager.zustand.setze(m)


def test_ein_einzelner_aussetzer_parkt_nichts(manager):
    """Ein Lauf mit altem Kurs ist kein Grund, einen Wert zu entfernen."""
    lauf(manager, ["AVGO", "JPM"])
    aktiv_machen(manager, "AVGO", "JPM")

    lauf(manager, ["AVGO", "JPM"], veraltet={"AVGO": 1220.0})

    m = manager.zustand.hole("etoro", "AVGO")
    assert m is not None and m.zustand == AKTIV, (
        "Nach einem einzigen Lauf darf noch nichts geparkt sein")
    assert m.veraltete_kurse_in_folge == 1


def test_zwei_laeufe_in_folge_parken(manager):
    """Der echte Fall: konstant 20 Minuten Rueckstand ueber mehrere Laeufe."""
    lauf(manager, ["AVGO", "JPM"])
    aktiv_machen(manager, "AVGO", "JPM")

    lauf(manager, ["AVGO", "JPM"], veraltet={"AVGO": 1220.0})
    lauf(manager, ["AVGO", "JPM"], veraltet={"AVGO": 1224.0})

    m = manager.zustand.hole("etoro", "AVGO")
    assert m is None or not m.handelbar, (
        "AVGO liefert seit zwei Laeufen 20 Minuten alte Kurse und darf keine "
        "Einstiege mehr erzeugen")
    jpm = manager.zustand.hole("etoro", "JPM")
    assert jpm is not None and jpm.handelbar, (
        "JPM ist unauffaellig und muss handelbar bleiben")


def test_frische_kurse_loesen_die_sperre_von_selbst(manager):
    """Ohne Handgriff: liefert der Wert wieder, ist er wieder dabei."""
    lauf(manager, ["AVGO"])
    lauf(manager, ["AVGO"], veraltet={"AVGO": 1220.0})
    lauf(manager, ["AVGO"], veraltet={"AVGO": 1224.0})
    gesperrt = manager.zustand.hole("etoro", "AVGO")
    assert gesperrt is not None and gesperrt.kern_blockiert
    assert KURSE_VERALTET in gesperrt.abganggrund

    lauf(manager, ["AVGO"])          # wieder frisch

    m = manager.zustand.hole("etoro", "AVGO")
    assert m is not None and not m.kern_blockiert, (
        "Die Sperre muss sich von selbst loesen")
    assert m.veraltete_kurse_in_folge == 0


def test_kernwert_wird_gesperrt_statt_entfernt(manager):
    """Die Kernzusammensetzung wird nicht heimlich umgeschrieben."""
    lauf(manager, ["AVGO"])
    lauf(manager, ["AVGO"], veraltet={"AVGO": 1220.0})
    lauf(manager, ["AVGO"], veraltet={"AVGO": 1224.0})

    m = manager.zustand.hole("etoro", "AVGO")
    assert m is not None, "Ein Kernwert wird gesperrt, nicht entfernt"
    assert m.kern_blockiert is True
    assert m.handelbar is False
    assert "20 min alt" in m.abganggrund or "min alt" in m.abganggrund


def test_gesperrter_kernwert_macht_platz_fuer_einen_nachruecker(manager):
    """Das Nachruecken: die handelbare Gesamtzahl darf nicht einbrechen."""
    lauf(manager, ["AVGO"])
    from universe.manager import UniverseRegeln
    regeln = UniverseRegeln.fuer("etoro", manager.cfg)

    vorher = manager._freie_plaetze("etoro", regeln, set())
    lauf(manager, ["AVGO"], veraltet={"AVGO": 1220.0})
    lauf(manager, ["AVGO"], veraltet={"AVGO": 1224.0})
    nachher = manager._freie_plaetze("etoro", regeln, set())

    assert nachher == vorher + 1, (
        "Fuer jeden gesperrten Kernwert muss ein dynamischer Wert nachruecken "
        "koennen, sonst schrumpft das handelbare Universum")


# ---------------------------------------------------------------------------
# Regression: die Kursalter-Erweiterung darf den Spread nicht verlieren
# ---------------------------------------------------------------------------
def test_kursabruf_fuellt_spread_UND_kursalter():
    """Beim Einbau des Kursalters war die Spread-Berechnung kurzzeitig toter
    Code -- und die gesamte Testsuite blieb dabei gruen.

    Der Spread geht in die Bewertung ein; sein stiller Ausfall haette das
    Aktienuniversum unbemerkt verschlechtert. Deshalb hier ausdruecklich.
    """
    from datetime import datetime, timezone
    from universe.stock_selector import StockUniverseSelector

    class Broker:
        name = "eToro"
        def latest_bid_ask(self, _inst):
            return {"bid": 99.90, "ask": 100.10, "last": 100.0,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "source": "eToro Public API rates"}

    selektor = StockUniverseSelector(broker=Broker())
    k = kandidat("AVGO")
    k.bid = k.ask = k.spread_pct = 0.0

    selektor._fuelle_quote(k, object())

    assert k.spread_pct > 0, "Der Spread muss weiterhin berechnet werden"
    assert k.spread_pct == pytest.approx(0.2 / 100.0, rel=1e-6)
    assert k.zusatz.get("quote_age_seconds") is not None, (
        "Und das Kursalter muss dabei sein")
    assert k.zusatz["quote_age_seconds"] < 60


def test_kurs_ohne_zeitstempel_gilt_nicht_als_veraltet():
    """Eine fehlende Messung darf keinen Wert aus dem Universum werfen."""
    from universe.stock_selector import StockUniverseSelector

    class Broker:
        name = "eToro"
        def latest_bid_ask(self, _inst):
            return {"bid": 99.9, "ask": 100.1, "last": 100.0}   # ohne timestamp

    selektor = StockUniverseSelector(broker=Broker())
    k = kandidat("AVGO")
    selektor._fuelle_quote(k, object())

    assert k.zusatz.get("quote_age_seconds") is None
    assert selektor._veraltete_kurse(
        [{"kandidat": k}]) == {}, "Ohne Messung wird nicht geparkt"
