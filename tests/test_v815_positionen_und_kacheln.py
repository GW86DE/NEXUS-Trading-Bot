"""v8.1.5 -- Positionsuebergabe und die Dashboard-Kacheln.

ZWEI MELDUNGEN VOM 25.08.2026
=============================
1. "Im Screenshot ...202016.png steht zwei mal wie viel positionen im OKX
    konto sind aber nie wie viele auf eToro konto."

   Stimmte: die Karte "Offene Krypto-Positionen" und die Karte "OKX
   Positionen" zeigten beide OKX. Fuer die Aktienseite gab es ueberhaupt
   keine Datei, aus der die Oberflaeche haette lesen koennen.

2. "Zusaetzlich wollte ich heute in der GUI eine aktie die ich selber gekauft
    habe dem Bot uebergeben das er sie handeln soll. die funktion gibt es
    dort ja aber es wurde blockiert weil sie zu sehr im minus war."

   Die alte Pruefung war ``stop < avg_cost < take`` -- gemessen am EINSTAND.
   Bei einer Position im Minus liegt der Einstand weit ueber dem Kurs; ein
   Stop darunter haette ueber dem Marktpreis gelegen und sofort ausgeloest.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("TRADINGBOT_TEST_STATE_DIR", "/tmp/tradingbot_tests")
Path(os.environ["TRADINGBOT_TEST_STATE_DIR"]).mkdir(parents=True, exist_ok=True)

WURZEL = Path(__file__).resolve().parent.parent


# ===========================================================================
# Die Uebergabe einer selbst gekauften Position
# ===========================================================================
@pytest.fixture
def depot(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADINGBOT_TEST_STATE_DIR", str(tmp_path))
    from position_manager import PositionManager, PositionRecord

    manager = PositionManager(str(tmp_path / "position_state.json"))

    def lege_an(symbol="FLR.US", menge=100.0, einstand=50.0, modus="OBSERVE",
                herkunft="MANUAL"):
        manager.records[symbol] = PositionRecord(
            con_id=symbol, symbol=symbol, asset_type="stock", currency="USD",
            quantity=menge, avg_cost=einstand,
            entry_time="2026-08-01T10:00:00+00:00",
            source=herkunft, management_mode=modus)
        return manager.records[symbol]

    return manager, lege_an, tmp_path


def test_position_tief_im_minus_kann_uebergeben_werden(depot):
    """Georgs Fall. Einstand 50, Kurs 35 -- 30 % im Minus.

    Nach der alten Regel haette der Stop unter 50 UND das Ziel ueber 50
    liegen muessen. Bei Kurs 35 ist das nicht erfuellbar, ohne dass der Stop
    sofort ausloest.
    """
    manager, lege_an, _ = depot
    lege_an(einstand=50.0)

    ok, detail = manager.request_takeover("FLR.US", stop=33.0, take=40.0,
                                          aktueller_kurs=35.0)

    assert ok is True, detail
    rec = manager.get_by_symbol("FLR.US")
    assert rec.management_mode == "PENDING_TAKEOVER"
    assert rec.planned_stop == 33.0 and rec.planned_take == 40.0
    # Die Uebernahme macht sie nicht handelbar -- der Kern muss erst den
    # Broker-Schutz bestaetigen.
    assert rec.management_mode != "AUTO"
    # Und sie sagt ehrlich, was ein ausgeloester Stop kosten wuerde.
    assert "im Minus" in detail
    assert "-1,700.00" in detail or "-1700" in detail.replace(",", "")


def test_der_einstand_entscheidet_nicht_mehr(depot):
    """Ein Stop oberhalb des Einstands ist erlaubt, wenn der Kurs hoeher liegt.

    Die Gegenprobe zur alten Regel: eine Position im PLUS. Ein Stop, der den
    Gewinn sichert, liegt ueber dem Einstand -- frueher verboten, obwohl es
    genau das Richtige ist.
    """
    manager, lege_an, _ = depot
    lege_an(einstand=50.0)

    ok, detail = manager.request_takeover("FLR.US", stop=68.0, take=80.0,
                                          aktueller_kurs=70.0)

    assert ok is True, detail
    assert manager.get_by_symbol("FLR.US").planned_stop == 68.0
    assert "im Minus" not in detail


def test_stop_ueber_dem_kurs_wird_abgelehnt(depot):
    """Ein Stop ueber dem Marktpreis loest sofort aus -- das ist kein Schutz."""
    manager, lege_an, _ = depot
    lege_an()

    ok, detail = manager.request_takeover("FLR.US", stop=36.0, take=40.0,
                                          aktueller_kurs=35.0)
    assert ok is False
    assert "sofort ausloesen" in detail
    assert manager.get_by_symbol("FLR.US").management_mode == "OBSERVE"


def test_ziel_unter_dem_kurs_wird_abgelehnt(depot):
    manager, lege_an, _ = depot
    lege_an()

    ok, detail = manager.request_takeover("FLR.US", stop=33.0, take=34.0,
                                          aktueller_kurs=35.0)
    assert ok is False
    assert "schon erreicht" in detail


def test_zu_enger_stop_wird_abgelehnt(depot):
    """Ein Stop innerhalb der Gebuehren verliert im Auslosefall sicher Geld."""
    manager, lege_an, _ = depot
    lege_an()

    ok, detail = manager.request_takeover("FLR.US", stop=34.999, take=40.0,
                                          aktueller_kurs=35.0)
    assert ok is False
    assert "zu eng" in detail
    assert "gebuehren" in detail.lower()


def test_ohne_kurs_wird_nicht_auf_den_einstand_ausgewichen(depot):
    """Genau dieses Ausweichen war der Fehler."""
    manager, lege_an, _ = depot
    lege_an()

    ok, detail = manager.request_takeover("FLR.US", stop=33.0, take=40.0)
    assert ok is False
    assert "Kurs unbekannt" in detail
    assert manager.get_by_symbol("FLR.US").management_mode == "OBSERVE"


def test_uebernommene_position_bekommt_die_herkunft_user_managed(depot):
    """Sie gehoert nicht ins Kaufuniversum -- und muss es auch nicht."""
    manager, lege_an, _ = depot
    lege_an(herkunft="MANUAL")

    manager.request_takeover("FLR.US", stop=33.0, take=40.0, aktueller_kurs=35.0)
    # Waehrend der asynchronen Schutzpruefung bleibt die Herkunft unveraendert;
    # dadurch kann eine Ablehnung vollstaendig zurueckrollen.
    assert manager.get_by_symbol("FLR.US").source == "MANUAL"
    manager.confirm_takeover("FLR.US")
    assert manager.get_by_symbol("FLR.US").source == "USER_MANAGED"


def test_gui_verlangt_keine_universumszugehoerigkeit_mehr():
    """Die zweite Blockade aus Georgs Meldung."""
    quelle = (WURZEL / "gui_app.py").read_text(encoding="utf-8")
    assert "ausserhalb des konfigurierten Universums" not in quelle
    assert "außerhalb des konfigurierten Universums" not in quelle


def test_kein_aufrufer_misst_stop_und_ziel_noch_am_einstand():
    """Waechter gegen einen Rueckfall.

    Wer ``request_takeover`` aufruft, muss den aktuellen Kurs mitgeben.
    """
    import ast

    for datei in ("gui_app.py", "positions_auftraege.py"):
        baum = ast.parse((WURZEL / datei).read_text(encoding="utf-8"))
        for knoten in ast.walk(baum):
            if (isinstance(knoten, ast.Call)
                    and isinstance(knoten.func, ast.Attribute)
                    and knoten.func.attr == "request_takeover"):
                namen = {k.arg for k in knoten.keywords}
                assert "aktueller_kurs" in namen, (
                    f"{datei}: request_takeover ohne aktuellen Kurs aufgerufen")


# ===========================================================================
# Auftraege aus der Oberflaeche
# ===========================================================================
@pytest.fixture
def auftraege(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADINGBOT_TEST_STATE_DIR", str(tmp_path))
    import positions_auftraege
    return positions_auftraege


def test_auftrag_wird_am_echten_kurs_ausgefuehrt(auftraege, depot):
    """Prozent statt Absolutwerte -- der Kurs auf der Seite ist Sekunden alt."""
    manager, lege_an, _ = depot
    lege_an(einstand=50.0)

    auftraege.anfordern("FLR.US", "uebernehmen", stop_pct=5.0, ziel_pct=10.0)
    assert len(auftraege.offene()) == 1

    # Der Kern sieht 35.00 -- Stop 33.25, Ziel 38.50.
    assert auftraege.verarbeite(manager, {"FLR.US": 35.0}) == 1
    rec = manager.get_by_symbol("FLR.US")
    assert rec.planned_stop == pytest.approx(33.25)
    assert rec.planned_take == pytest.approx(38.50)
    assert rec.management_mode == "PENDING_TAKEOVER"
    assert auftraege.offene() == []

    fertig = auftraege.uebersicht()["erledigt"][0]
    assert fertig["ok"] is True and fertig["symbol"] == "FLR.US"


def test_auftrag_ohne_kurs_bleibt_offen_statt_zu_scheitern(auftraege, depot):
    """Kein Kurs ist kein Fehler -- der naechste Durchlauf hat vielleicht einen."""
    manager, lege_an, _ = depot
    lege_an()

    auftraege.anfordern("FLR.US", "uebernehmen", stop_pct=5.0, ziel_pct=10.0)
    assert auftraege.verarbeite(manager, {}) == 0
    assert len(auftraege.offene()) == 1, "Der Auftrag muss erhalten bleiben"
    assert manager.get_by_symbol("FLR.US").management_mode == "OBSERVE"

    assert auftraege.verarbeite(manager, {"FLR.US": 35.0}) == 1
    assert manager.get_by_symbol("FLR.US").management_mode == "PENDING_TAKEOVER"


def test_abgelehnter_auftrag_nennt_den_grund(auftraege, depot):
    manager, lege_an, _ = depot
    lege_an()

    # 60 % Stop ist ausserhalb der Eingabegrenzen -> gar nicht erst annehmen.
    with pytest.raises(ValueError, match="Stop"):
        auftraege.anfordern("FLR.US", "uebernehmen", stop_pct=60.0, ziel_pct=10.0)

    # Ein zulaessiger, aber unmoeglicher Auftrag scheitert beim Kern -- mit Grund.
    auftraege.anfordern("UNBEKANNT", "uebernehmen", stop_pct=3.0, ziel_pct=6.0)
    auftraege.verarbeite(manager, {"UNBEKANNT": 10.0})
    fertig = auftraege.uebersicht()["erledigt"][0]
    assert fertig["ok"] is False
    assert "nicht gefunden" in fertig["detail"]


def test_zweiter_auftrag_ersetzt_den_ersten(auftraege, depot):
    """Sonst arbeitet der Kern eine veraltete Absicht ab."""
    manager, _lege_an, _ = depot
    auftraege.anfordern("FLR.US", "uebernehmen", stop_pct=3.0, ziel_pct=6.0)
    auftraege.anfordern("FLR.US", "uebernehmen", stop_pct=8.0, ziel_pct=20.0)

    offen = auftraege.offene()
    assert len(offen) == 1
    assert offen[0]["stop_pct"] == 8.0


def test_alter_auftrag_verfaellt_mit_hinweis(auftraege, depot, monkeypatch):
    """Ein Auftrag darf nicht stumm liegen bleiben, wenn der Kern nicht laeuft."""
    auftraege.anfordern("FLR.US", "uebernehmen", stop_pct=3.0, ziel_pct=6.0)
    monkeypatch.setattr(auftraege, "_alter_minuten", lambda *_: 999.0)

    assert auftraege.offene() == []
    fertig = auftraege.uebersicht()["erledigt"][0]
    assert fertig["ok"] is False
    assert "Verfallen" in fertig["detail"]


def test_beobachten_nimmt_dem_bot_die_verwaltung(auftraege, depot):
    manager, lege_an, _ = depot
    lege_an(modus="AUTO")

    auftraege.anfordern("FLR.US", "beobachten")
    auftraege.verarbeite(manager, {})
    assert manager.get_by_symbol("FLR.US").management_mode == "OBSERVE"


def test_unbekannte_aktion_wird_abgelehnt(auftraege):
    with pytest.raises(ValueError, match="Unbekannte Aktion"):
        auftraege.anfordern("FLR.US", "verkaufen")


def test_die_oberflaeche_fasst_das_positionsbuch_nicht_an():
    """Zwei Prozesse duerfen nie gleichzeitig position_state.json schreiben.

    Die WebUI legt nur Auftraege ab; ausgefuehrt wird im Handelskern.
    """
    import ast

    quelle = (WURZEL / "webui" / "app.py").read_text(encoding="utf-8")
    baum = ast.parse(quelle)
    verboten = {"request_takeover", "set_observe_only", "confirm_takeover",
                "sync_with_broker"}
    treffer = [k.func.attr for k in ast.walk(baum)
               if isinstance(k, ast.Call) and isinstance(k.func, ast.Attribute)
               and k.func.attr in verboten]
    assert not treffer, f"webui/app.py greift direkt ins Positionsbuch: {treffer}"
    assert "PositionManager" not in quelle


# ===========================================================================
# Die Dashboard-Kacheln
# ===========================================================================
def test_aktienpositionen_werden_fuer_die_anzeige_geschrieben(depot):
    """Die Datei, die es fuer die Aktienseite nie gab."""
    manager, lege_an, tmp_path = depot
    lege_an(symbol="FLR.US", menge=100.0, einstand=50.0)
    lege_an(symbol="DVLT", menge=10.0, einstand=12.0, modus="AUTO", herkunft="BOT")

    class Broker:
        def positionen(self):
            class P:
                def __init__(self, s, kurs, pnl):
                    self.symbol = s
                    self.market_price = kurs
                    self.market_value = kurs * 100
                    self.unrealized_pnl = pnl
            return [P("FLR.US", 35.0, -1500.0), P("DVLT", 11.0, -10.0)]

    manager._schreibe_anzeigedatei(Broker())
    daten = json.loads((tmp_path / "stock_positions.json").read_text(encoding="utf-8"))

    assert daten["kurse_verfuegbar"] is True
    symbole = {p["symbol"]: p for p in daten["positionen"]}
    assert set(symbole) == {"FLR.US", "DVLT"}
    assert symbole["FLR.US"]["unrealisiert"] == pytest.approx(-1500.0)
    assert symbole["FLR.US"]["verwaltung"] == "OBSERVE"
    assert symbole["DVLT"]["verwaltung"] == "AUTO"


def test_fehlender_kurs_wird_nicht_zu_null(depot):
    """0 saehe aus wie ein Totalverlust und waere eine Falschaussage."""
    manager, lege_an, tmp_path = depot
    lege_an(symbol="FLR.US")

    class StummerBroker:
        def positionen(self):
            raise RuntimeError("keine Verbindung")

    manager._schreibe_anzeigedatei(StummerBroker())
    daten = json.loads((tmp_path / "stock_positions.json").read_text(encoding="utf-8"))

    assert daten["kurse_verfuegbar"] is False
    eintrag = daten["positionen"][0]
    assert eintrag["kurs"] is None
    assert eintrag["unrealisiert"] is None
    # Die Position selbst geht dabei nicht verloren.
    assert eintrag["symbol"] == "FLR.US" and eintrag["menge"] == 100.0


def test_dashboard_zeigt_okx_nicht_mehr_doppelt():
    """Der konkrete Screenshot-Befund."""
    quelle = (WURZEL / "webui" / "static" / "dashboard.js").read_text(encoding="utf-8")
    assert "Offene Krypto-Positionen" not in quelle
    assert "['etoro','okx'].map(key=>brokerOverview(key,d))" in quelle
    assert 'function okxKarten' not in quelle
    assert 'function etoroPositionen' not in quelle


def test_dashboard_gruppiert_die_kacheln():
    quelle = (WURZEL / "webui" / "static" / "dashboard.js").read_text(encoding="utf-8")
    for section in ('overview-state', 'cards', 'system-overview'):
        assert f"#{section}" in quelle
    assert 'mode_mismatch' in quelle and 'worker_alive' in quelle


def test_das_raster_bleibt_auto_fit():
    """Georg: "achte weiter drauf das in der WebUi alle kacheln gut
    angeordnet sind das ist bis jetzt gut"."""
    css = (WURZEL / "webui" / "static" / "app.css").read_text(encoding="utf-8")
    assert "repeat(auto-fit,minmax(280px,1fr))" in css
    # Die Gruppenzeile spannt ueber die volle Breite und bricht das Raster nicht.
    assert ".dashboard-gruppe{grid-column:1/-1" in css


def test_dashboard_liefert_die_aktienpositionen_mit():
    quelle = (WURZEL / "webui" / "state.py").read_text(encoding="utf-8")
    assert '"stock_positions"' in quelle


def test_positionsseite_ist_angemeldeten_vorbehalten():
    quelle = (WURZEL / "webui" / "app.py").read_text(encoding="utf-8")
    assert '@app.get("/positions"' in quelle
    for route in ('@app.get("/api/positions")', '@app.post("/api/positions/{aktion}")'):
        assert route in quelle
        block = quelle.split(route)[1][:260]
        assert "_session(request)" in block, f"{route} muss eine Anmeldung verlangen"
    # Schreibende Aktionen zusaetzlich mit CSRF-Schutz.
    block = quelle.split('@app.post("/api/positions/{aktion}")')[1][:260]
    assert "_csrf(request, session)" in block


def test_navigation_kennt_die_positionsseite():
    for name in ("dashboard", "universe", "settings", "logbook", "trades"):
        seite = (WURZEL / "webui" / "templates" / f"{name}.html").read_text(encoding="utf-8")
        assert 'href="/trades"' in seite, f"{name}.html verlinkt die Seite nicht"
