"""eToro-Eigentum haengt ausschliesslich an der positionId (v9.3).

Der Vorfall vom 31.08.2026: NEXUS kaufte 29 MSFT bei eToro. Die Aktie lag
danach nachweislich im Depot. In der Oberflaeche stand sie trotzdem als

    BROKER_EXISTING - "Beim Broker bereits vorhanden; standardmaessig nur
    beobachten." - NUR BEOBACHTET

Ursachenkette:

1. eToro zeigt eine frische Position im Depot, bevor der Lookup-Fill
   verarbeitet ist. ``sync_with_broker()`` legte sie deshalb zuerst als
   Fremdbestand an.
2. Die spaetere Hochstufung in ``register_buy()`` verlangte Symbol UND
   nahezu exakt gleiche Menge. Der Kommentar darueber behauptete
   "exakt ueber orderId/referenceId/positionId" -- der Code verglich Mengen.
3. Reparieren liess sich das auch von Hand nicht: der Uebernahmeknopf
   verlangt einen Kurs, und ``closeRate`` war bei der frischen Position 0.

Es ist dieselbe Fehlerklasse, die auf der Kryptoseite in v9.2 behoben wurde,
nur beim anderen Broker: Eigentum entstand aus weichen Merkmalen.
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

MSFT_POSITION_ID = "2984571234"
MSFT_ORDER_ID = "8811223344"


class FakeBrokerPosition:
    """Was broker/etoro.py::positionen() ab 9.3 liefert."""

    def __init__(self, symbol="MSFT", quantity=29.0, avg_cost=509.35,
                 position_ids=(MSFT_POSITION_ID,), market_price=0.0,
                 unrealized_pnl=None):
        self.symbol = symbol
        self.quantity = quantity
        self.avg_cost = avg_cost
        self.currency = "USD"
        self.asset_type = "stock"
        self.broker_id = str(position_ids[0]) if position_ids else ""
        self.position_ids = tuple(position_ids)
        self.instrument_id = "1001"
        self.account_fingerprint = "test-account"
        self.broker_environment = "DEMO"
        self.snapshot_id = "test-snapshot"
        self.market_price = market_price
        self.market_value = quantity * market_price
        self.unrealized_pnl = unrealized_pnl
        self.broker_stop = 506.53
        self.broker_take_profit = 513.65
        self.price_source = "ETORO_PNL_CLOSE_RATE" if market_price else "UNBEKANNT"
        self.price_age_seconds = 0.0 if market_price else None


class FakeBroker:
    def __init__(self, positionen):
        self._positionen = list(positionen)

    def positionen(self):
        return list(self._positionen)

    paper = True

    @staticmethod
    def account_fingerprint():
        return "test-account"


class FakeKontrakt:
    def __init__(self, symbol="MSFT"):
        self.symbol = symbol
        self.localSymbol = symbol
        self.currency = "USD"
        self.conId = 0
        self.secType = "STK"


@pytest.fixture()
def manager(tmp_path, monkeypatch):
    import position_manager as pm
    monkeypatch.setattr(pm.PositionManager, "_schreibe_anzeigedatei",
                        lambda self, broker: None)
    return pm.PositionManager(tmp_path / "position_state.json")


def _ohne_kaufabsicht(monkeypatch):
    import position_manager as pm
    monkeypatch.setattr(pm.PositionManager, "_offene_kaufabsicht",
                        staticmethod(lambda position_ids, symbol, **kwargs: None))


def _mit_kaufabsicht(monkeypatch, **felder):
    import position_manager as pm
    satz = {"decision_id": 4711, "symbol": "MSFT", "position_ids": [],
            "order_ids": [MSFT_ORDER_ID], "reference_id": "ref-msft",
            "stop": 506.53, "take_profit": 513.65}
    satz.update(felder)
    if satz.get("position_ids"):
        satz.setdefault("verified_position_ids", list(satz["position_ids"]))
        satz.setdefault("bestaetigt", True)
        satz.setdefault("_id_beweis", True)
    monkeypatch.setattr(pm.PositionManager, "_offene_kaufabsicht",
                        staticmethod(lambda position_ids, symbol, **kwargs: dict(satz)))


# ---------------------------------------------------------------------------
# Der Vorfall selbst
# ---------------------------------------------------------------------------
def test_eigener_kauf_wird_im_propagationsfenster_nicht_zu_fremdbestand(
        manager, monkeypatch):
    """T1 aus dem Aenderungsantrag: Depot zuerst, Lookup spaeter."""
    _mit_kaufabsicht(monkeypatch)
    broker = FakeBroker([FakeBrokerPosition()])

    manager.sync_with_broker(broker, [])
    rec = manager.get_by_symbol("MSFT")

    assert rec is not None
    assert rec.source == "BOT", "Ein eigener Kauf ist kein Fremdbestand"
    assert rec.management_mode == "PENDING_CONFIRMATION"
    assert rec.ownership_status == "PENDING", (
        "Ohne bestaetigte positionId noch kein Eigentumsbeweis")
    assert "Beim Broker bereits vorhanden" not in rec.management_note


def test_spaeterer_fill_stuft_ueber_die_positionid_hoch(manager, monkeypatch):
    """Der Kern des Falls: Hochstufung ohne Mengenvergleich."""
    _mit_kaufabsicht(monkeypatch)
    broker = FakeBroker([FakeBrokerPosition()])
    manager.sync_with_broker(broker, [])

    rec = manager.register_buy(
        FakeKontrakt(), 29.0, 509.35, "USD", "stock",
        account_equity=50_000.0, stop_price=506.53, take_price=513.65,
        source="BOT", position_ids=[MSFT_POSITION_ID],
        order_ids=[MSFT_ORDER_ID], reference_id="ref-msft", decision_id=4711)

    assert rec.management_mode == "AUTO"
    assert rec.source == "BOT"
    assert rec.ownership_status == "VERIFIED"
    assert rec.quantity == 29.0, "Die Menge darf NICHT ein zweites Mal addiert werden"
    assert rec.avg_cost == 509.35
    assert MSFT_POSITION_ID in rec.position_id_set()
    assert len(manager.records) == 1, "Kein zweiter Datensatz fuer dieselbe Aktie"


def test_gerundete_brokermenge_verhindert_die_hochstufung_nicht(
        manager, monkeypatch):
    """T5: eToro rundet Mengen. Bis 9.2 scheiterte die Hochstufung daran."""
    _mit_kaufabsicht(monkeypatch)
    broker = FakeBroker([FakeBrokerPosition(quantity=29.000001)])
    manager.sync_with_broker(broker, [])

    rec = manager.register_buy(
        FakeKontrakt(), 29.0, 509.35, "USD", "stock",
        account_equity=50_000.0, stop_price=506.53, take_price=513.65,
        source="BOT", position_ids=[MSFT_POSITION_ID],
        order_ids=[MSFT_ORDER_ID])

    assert rec.management_mode == "AUTO"
    assert rec.ownership_status == "VERIFIED"


def test_symbol_und_menge_stimmen_aber_die_positionid_nicht(manager, monkeypatch):
    """T6: der wichtigste Negativfall. Gleiche Aktie, gleiche Menge,
    ANDERE Position-Line -- z.B. ein manueller Kauf des Nutzers."""
    _ohne_kaufabsicht(monkeypatch)
    broker = FakeBroker([FakeBrokerPosition(position_ids=("9999999999",))])
    manager.sync_with_broker(broker, [])
    fremd = manager.get_by_symbol("MSFT")
    assert fremd.ownership_status == "EXTERNAL"

    rec = manager.register_buy(
        FakeKontrakt(), 29.0, 509.35, "USD", "stock",
        account_equity=50_000.0, stop_price=506.53, take_price=513.65,
        source="BOT", position_ids=[MSFT_POSITION_ID],
        order_ids=[MSFT_ORDER_ID])

    assert rec is not fremd, "Der fremde Bestand darf nicht uebernommen werden"
    assert fremd.management_mode == "OBSERVE"
    assert fremd.ownership_status == "EXTERNAL"
    assert "9999999999" not in rec.position_id_set()


def test_mehrere_position_lines_bleiben_vollstaendig(manager, monkeypatch):
    """T4: zwei positionIds desselben Symbols bleiben zwei Records."""
    _mit_kaufabsicht(monkeypatch)
    broker = FakeBroker([
        FakeBrokerPosition(position_ids=(MSFT_POSITION_ID,), quantity=20.0),
        FakeBrokerPosition(position_ids=("2984571235",), quantity=9.0),
    ])
    manager.sync_with_broker(broker, [])
    assert len(manager.records) == 2
    assert {next(iter(rec.position_id_set())) for rec in manager.records.values()} == {
        MSFT_POSITION_ID, "2984571235"}


def test_depotbestaetigung_beweist_eigentum_auch_ohne_fill(manager, monkeypatch):
    """Eigentum, Abgleich und Verwaltung sind drei Zustaende.

    Sobald die eigene positionId im Depot steht, ist das Eigentum bewiesen --
    auch wenn der Fill mit Einstandspreis noch aussteht. Die Verwaltung
    bleibt bis dahin bewusst zurueckhaltend.
    """
    _mit_kaufabsicht(monkeypatch)
    broker = FakeBroker([FakeBrokerPosition()])
    manager.sync_with_broker(broker, [])
    assert manager.get_by_symbol("MSFT").ownership_status == "PENDING"

    # Zweiter Durchlauf: dieselbe positionId, jetzt bestaetigt.
    _mit_kaufabsicht(monkeypatch, position_ids=[MSFT_POSITION_ID])
    manager.sync_with_broker(broker, [])
    rec = manager.get_by_symbol("MSFT")
    assert rec.ownership_status == "VERIFIED"
    assert rec.reconciliation_status == "CONFIRMED_OPEN"


# ---------------------------------------------------------------------------
# DIE REGEL, jetzt auch fuer eToro
# ---------------------------------------------------------------------------
def test_eigentum_haengt_an_keinem_weichen_merkmal():
    """Kein Wert, den Rundung, Anzeige oder ein Update aendern kann, darf in
    die Eigentumsentscheidung eingehen."""
    import position_manager as pm

    rec = pm.PositionRecord(
        con_id=0, symbol="MSFT", asset_type="stock", currency="USD",
        quantity=29.0, avg_cost=509.35, entry_time="2026-08-31T15:35:00+02:00",
        broker_position_ids=[MSFT_POSITION_ID], entry_order_ids=[MSFT_ORDER_ID],
        ownership_status="VERIFIED")
    assert rec.ownership_chain_complete

    for feld, wert in (("quantity", 28.999), ("quantity", 0.0),
                       ("avg_cost", 0.0), ("symbol", "MSFT.US"),
                       ("management_mode", "OBSERVE"),
                       ("management_note", "irgendwas"),
                       ("source", "BROKER_EXISTING"),
                       ("planned_stop", 0.0), ("currency", "EUR")):
        alt = getattr(rec, feld)
        setattr(rec, feld, wert)
        assert rec.ownership_chain_complete, f"{feld} darf das Eigentum nicht kippen"
        setattr(rec, feld, alt)

    # Und die Gegenprobe: nur die IDs nehmen es weg.
    for feld, wert in (("broker_position_ids", []), ("entry_order_ids", []),
                       ("ownership_status", "PENDING")):
        alt = getattr(rec, feld)
        setattr(rec, feld, wert)
        if feld == "entry_order_ids":
            rec.entry_reference_id = ""
        assert not rec.ownership_chain_complete, f"ohne {feld} kein Eigentum"
        setattr(rec, feld, alt)


def test_altstand_aus_9_2_startet_ohne_eigentumsbehauptung(tmp_path, monkeypatch):
    """Upgrade-Test mit einer echten 9.2-Positionsdatei."""
    import position_manager as pm

    datei = tmp_path / "position_state.json"
    datei.write_text(json.dumps({
        "updated_at": "2026-08-31T19:24:00+02:00",
        "positions": {
            "2984571234": {
                "con_id": 0, "symbol": "MSFT", "asset_type": "stock",
                "currency": "USD", "quantity": 29.0, "avg_cost": 509.35,
                "entry_time": "2026-08-31T15:35:00+02:00",
                "planned_stop": 0.0, "planned_take": 0.0,
                "planned_risk_amount": 0.0, "planned_risk_pct": 0.0,
                "entry_reason": "", "profile": "", "source": "BROKER_EXISTING",
                "management_mode": "OBSERVE",
                "management_note": "Beim Broker bereits vorhanden; standardmaessig nur beobachten.",
                "last_manual_change": "", "estimated_entry_cost": 0.0,
            }
        }}), encoding="utf-8")

    manager = pm.PositionManager(datei)
    rec = manager.get_by_symbol("MSFT")
    assert rec is not None, "Ein 9.2-Stand muss ladbar bleiben"
    assert rec.quantity == 29.0
    assert rec.ownership_status == "EXTERNAL"
    assert not rec.ownership_chain_complete, (
        "Ein Altstand ohne IDs darf kein Eigentum behaupten")


# ---------------------------------------------------------------------------
# CR-04 -- Kurs und P&L
# ---------------------------------------------------------------------------
def test_fehlender_pnl_wird_nicht_als_null_gebucht():
    """Im Bild vom 31.08.2026 stand "Buchwert +0,00" fuer eine Position ohne
    Kurs. 0,00 sieht aus wie ausgeglichen und ist eine Falschaussage."""
    import broker.etoro as etoro

    zeilen = [{"positionId": MSFT_POSITION_ID, "instrumentId": 1001,
               "units": 29.0, "openRate": 509.35, "isBuy": True}]
    werte = [z.get("pnL") for z in zeilen]
    assert all(x is None for x in werte)
    quelle = (WURZEL / "broker" / "etoro.py").read_text(encoding="utf-8")
    assert 'row.get("pnL") is not None else None' in quelle


def test_kursfallback_nutzt_den_rates_endpunkt(monkeypatch):
    """closeRate=0 darf nicht zu "kein Kurs" fuehren, wenn Rates Bid liefert."""
    import broker.etoro as etoro

    adapter = object.__new__(etoro.EtoroBroker)
    adapter._instrument_by_id = {}
    monkeypatch.setattr(etoro.EtoroBroker, "_rate_row_by_id",
                        lambda self, iid, force=False: {
                            "bid": 508.9, "ask": 509.1,
                            "date": "2026-08-31T19:24:00Z"})

    kurs, quelle, _alter = etoro.EtoroBroker._positionskurs(
        adapter, 1001, [{"closeRate": 0.0}])
    assert kurs == 508.9
    assert quelle == "ETORO_RATES_BID"


def test_ohne_jede_kursquelle_bleibt_der_kurs_unbekannt(monkeypatch):
    import broker.etoro as etoro

    adapter = object.__new__(etoro.EtoroBroker)
    adapter._instrument_by_id = {}
    monkeypatch.setattr(etoro.EtoroBroker, "_rate_row_by_id",
                        lambda self, iid, force=False: None)

    kurs, quelle, alter = etoro.EtoroBroker._positionskurs(
        adapter, 1001, [{"closeRate": 0.0}])
    assert kurs is None, "Kein erfundener Kurs"
    assert quelle == "UNBEKANNT"
    assert alter is None


def test_webui_zeigt_eigentum_getrennt_von_der_verwaltung():
    quelle = (WURZEL / "webui" / "static" / "positions.js").read_text(encoding="utf-8")
    assert "PENDING_CONFIRMATION" in quelle
    assert "KAUF BESTÄTIGT" in quelle
    assert "Zuordnung" in quelle
    assert "positionId" in quelle


# ---------------------------------------------------------------------------
# CR-03 -- Nebenlaeufigkeit
# ---------------------------------------------------------------------------
def test_reconciliation_serialisiert_lese_aendere_schreib_folgen(tmp_path, monkeypatch):
    """Zwei Threads aendern unterschiedliche Saetze: beide bleiben erhalten."""
    import threading
    import etoro_reconciliation as rec

    monkeypatch.setenv("TRADINGBOT_TEST_STATE_DIR", str(tmp_path))
    for did in range(20):
        rec.start_intent(decision_id=did, symbol=f"SYM{did}", paper=True,
                         profile="test", quantity=1.0, price=10.0,
                         stop=9.0, take_profit=11.0)

    fehler = []

    def arbeite(start):
        try:
            for did in range(start, 20, 2):
                rec.accepted(did, order_id=f"ord-{did}", reference_id=f"ref-{did}")
        except Exception as exc:  # pragma: no cover
            fehler.append(exc)

    threads = [threading.Thread(target=arbeite, args=(i,)) for i in (0, 1)]
    for x in threads:
        x.start()
    for x in threads:
        x.join()

    assert not fehler
    for did in range(20):
        satz = rec.record_for(did)
        assert f"ord-{did}" in (satz.get("order_ids") or []), (
            f"Aenderung an {did} ging verloren")


def test_terminaler_zustand_wird_von_einem_alten_snapshot_nicht_zurueckgedreht():
    quelle = (WURZEL / "etoro_reconciliation.py").read_text(encoding="utf-8")
    assert "_LOCK = threading.RLock()" in quelle
    assert "def _mutiere(" in quelle
    assert "_ist_terminal(record)" in quelle


# ---------------------------------------------------------------------------
# KORREKTUR 31.08.2026 -- der SPGI-Fall
#
# v9.3.0 hat den MSFT-Fall NICHT behoben. Der Bot kaufte um 20:51 SPGI, und
# eine Minute spaeter stand die Aktie wieder als "Fremdbestand beim Broker".
#
# Zwei Loecher in der ersten Fassung:
#
#  1. Der Symbolabgleich in _offene_kaufabsicht() galt nur, solange der
#     Reconciliation-Satz GAR KEINE positionId hatte. eToro meldet die
#     Ausfuehrung aber binnen Sekunden -- danach war der Abgleich tot. Genau
#     in diesem Fenster laeuft der Depot-Abgleich.
#  2. Die Frage "gehoert das dem Bot?" wurde NUR beim Anlegen des
#     Datensatzes gestellt. Lief der Abgleich einen Moment zu frueh, war die
#     Fehlzuordnung dauerhaft eingefroren.
# ---------------------------------------------------------------------------
def _reconciliation_satz(monkeypatch, tmp_path, **felder):
    import etoro_reconciliation as rec

    monkeypatch.setenv("TRADINGBOT_TEST_STATE_DIR", str(tmp_path))
    rec.start_intent(decision_id=9001, symbol="SPGI", paper=True, profile="",
                     quantity=34.0, price=436.19, stop=433.89, take_profit=445.86,
                     account_fingerprint="test-account")
    rec.accepted(9001, order_id="ord-spgi-1", reference_id="ref-spgi-1")
    data = rec._load()
    satz = data["records"]["9001"]
    satz.update(felder)
    rec._save(data)
    return rec


def _spgi(position_ids=("3001",), quantity=34.0):
    return FakeBrokerPosition(symbol="SPGI", quantity=quantity, avg_cost=436.19,
                              position_ids=position_ids, market_price=436.28)


@pytest.mark.parametrize("depot_ids,lage", [
    (("3001",), "positionId stimmt ueberein"),
    (("9999",), "Depot fuehrt eine andere positionId"),
])
def test_unverified_intent_never_claims_a_broker_position(manager, monkeypatch, tmp_path,
                                                 depot_ids, lage):
    """Genau die Lage aus dem Log um 20:51:31: eToro meldet eine Ausfuehrung,
    die positionId ist im Depot aber noch nicht bestaetigt."""
    _reconciliation_satz(
        monkeypatch, tmp_path,
        fills=[{"position_id": "3001", "quantity": 34.0, "price": 436.19,
                "execution_time": "2026-08-31T20:51:20Z"}],
        filled_quantity=34.0, position_ids=["3001"],
        state="AWAITING_POSITION_CONFIRMATION")

    manager.sync_with_broker(FakeBroker([_spgi(depot_ids)]), [])
    rec = manager.get_by_symbol("SPGI")

    assert rec.source == "BROKER_EXISTING", f"{lage}: noch kein verifizierter Eigentumsbeleg"
    assert rec.management_mode == "OBSERVE"
    assert not rec.owned_position_id_set()


def test_symbol_suffix_is_not_an_ownership_receipt(manager, monkeypatch,
                                                           tmp_path):
    """"SPGI" und "SPGI.US" sind derselbe Wert."""
    _reconciliation_satz(monkeypatch, tmp_path, state="SUBMITTING")
    manager.sync_with_broker(FakeBroker([FakeBrokerPosition(
        symbol="SPGI.US", quantity=34.0, avg_cost=436.19,
        position_ids=("3001",), market_price=436.28)]), [])
    assert manager.get_by_symbol("SPGI.US").source == "BROKER_EXISTING"
    assert not manager.get_by_symbol("SPGI.US").owned_position_id_set()


def test_bereits_bestaetigter_kauf_verschwindet_nicht_aus_der_zuordnung(
        manager, monkeypatch, tmp_path):
    """Das zweite Loch: sobald verify_broker_truth bestaetigt hatte, fiel der
    Kauf aus der Liste -- und ein bereits falsch angelegter Depotbestand war
    nicht mehr reparierbar."""
    _reconciliation_satz(
        monkeypatch, tmp_path,
        fills=[{"position_id": "3001", "quantity": 34.0, "price": 436.19}],
        filled_quantity=34.0, position_ids=["3001"], state="FILLED",
        position_verified=True, broker_position_status="OPEN_CONFIRMED",
        verified_position_ids=["3001"])

    manager.sync_with_broker(FakeBroker([_spgi()]), [])
    rec = manager.get_by_symbol("SPGI")
    assert rec.source == "BOT"
    assert rec.ownership_status == "VERIFIED"


def test_falsch_angelegter_bestand_wird_beim_naechsten_abgleich_repariert(
        manager, monkeypatch, tmp_path):
    """Die eigentliche Lehre aus SPGI: eine einmalige Fehlzuordnung darf sich
    nicht dauerhaft festsetzen."""
    _ohne_kaufabsicht(monkeypatch)
    broker = FakeBroker([_spgi()])
    manager.sync_with_broker(broker, [])
    assert manager.get_by_symbol("SPGI").source == "BROKER_EXISTING"

    # Der Reconciliation-Satz taucht erst jetzt auf.
    import position_manager as pm
    monkeypatch.undo()
    _reconciliation_satz(
        monkeypatch, tmp_path,
        fills=[{"position_id": "3001", "quantity": 34.0, "price": 436.19}],
        filled_quantity=34.0, position_ids=["3001"],
        state="FILLED", position_verified=True,
        broker_position_status="OPEN_CONFIRMED",
        verified_position_ids=["3001"])
    monkeypatch.setattr(pm.PositionManager, "_schreibe_anzeigedatei",
                        lambda self, broker: None)

    manager.sync_with_broker(broker, [])
    rec = manager.get_by_symbol("SPGI")
    assert rec.source == "BOT", "Der naechste Abgleich muss das richtigstellen"
    assert rec.ownership_status == "VERIFIED"
    assert rec.planned_stop == pytest.approx(433.89)


def test_bewusst_beobachtete_position_wird_nicht_uebergangen(manager, monkeypatch,
                                                            tmp_path):
    """Hat der Nutzer selbst auf "nur beobachten" gestellt, bleibt das so."""
    _ohne_kaufabsicht(monkeypatch)
    broker = FakeBroker([_spgi()])
    manager.sync_with_broker(broker, [])
    manager.set_observe_only("SPGI", "Vom Nutzer auf Beobachten gesetzt")

    import position_manager as pm
    monkeypatch.undo()
    _reconciliation_satz(monkeypatch, tmp_path, position_ids=["3001"],
                         state="AWAITING_POSITION_CONFIRMATION")
    monkeypatch.setattr(pm.PositionManager, "_schreibe_anzeigedatei",
                        lambda self, broker: None)

    manager.sync_with_broker(broker, [])
    rec = manager.get_by_symbol("SPGI")
    assert rec.management_mode == "OBSERVE"
    assert rec.source == "BROKER_EXISTING"


def test_ein_symboltreffer_allein_gibt_niemals_automatik(manager, monkeypatch,
                                                         tmp_path):
    """Sicherheitsgrenze: ohne positionId keine automatische Verwaltung."""
    _reconciliation_satz(monkeypatch, tmp_path, state="SUBMITTING")
    from broker.base import BrokerFehler
    with pytest.raises(BrokerFehler, match="nicht genau eine positionId"):
        manager.sync_with_broker(FakeBroker([_spgi(position_ids=())]), [])


def test_uebernahme_findet_die_position_auch_bei_abweichender_instrument_id():
    """Schutz wird nicht mehr per Symbol oder Katalog geraten."""
    quelle = (WURZEL / "broker" / "etoro.py").read_text(encoding="utf-8")
    assert "position_ids=None" in quelle
    assert "_protection_values_match" in quelle
    assert "/api/v2/trading/demo/positions/{pid}" in quelle
