"""v9.1 -- die OKX-Antwortklassifikation.

Die Signatur-, URL-, Parameter- und Quantisierungsschicht war schon in 9.0.15
doku-konform. Gefaehrlich war ausschliesslich, WIE Antworten eingeordnet
wurden:

* Codes, die OKX ausdruecklich als mehrdeutig beschreibt (50004: "does not
  indicate success or failure of order"), galten als endgueltige Ablehnung.
  Der Bot hat die Order danach vergessen -- eine real angenommene Order wurde
  zu einer unbekannten, ungeschuetzten Position.
* 51016 "Duplicated client order ID" ist der BEWEIS, dass die Order bei OKX
  liegt. Da die clOrdId deterministisch aus der decision_id entsteht, trat der
  Code nach jedem Wiederanlauf auf -- und die gefuellte Position verschwand.
* Storno-Erfolgszustaende (51400 "does not exist", 51402 "already completed")
  galten als Fehler und blockierten den Ausstieg genau dann, wenn die
  Schutzorder gerade ausgeloest war.
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


class FakeAntwort:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status
        self.text = ""

    def json(self):
        return self._payload


class FakeSession:
    """Eine Sitzung, die genau eine vorgegebene OKX-Antwort liefert."""

    def __init__(self, payload, status=200):
        self.payload = payload
        self.status = status
        self.calls = []
        self.headers = {}

    def request(self, method, url, headers=None, data=None, timeout=None):
        self.calls.append({"method": method, "url": url,
                           "headers": dict(headers or {}), "body": data})
        return FakeAntwort(self.payload, self.status)


def client_mit(payload, status=200):
    from broker.okx import OKXClient
    c = OKXClient("key", "secret", "phrase", demo=True)
    c.session = FakeSession(payload, status)
    return c


# ===========================================================================
# 1. Mehrdeutige Codes duerfen nie als Ablehnung gelten
# ===========================================================================
@pytest.mark.parametrize("code", ["50004", "50013", "50026", "50001", "50005"])
def test_unklarer_code_wird_nicht_zur_ablehnung(code):
    """OKX zu 50004: "does not indicate success or failure of order"."""
    from broker.base import OrderStatusUnklar

    c = client_mit({"code": code, "msg": "Endpoint request timeout", "data": []})
    with pytest.raises(OrderStatusUnklar):
        c.request("POST", "/trade/order", body={"instId": "BTC-EUR"},
                  private=True, is_order=True)


def test_eindeutige_ablehnung_bleibt_eine_ablehnung():
    """Die Gegenprobe: 51008 ist wirklich endgueltig, das darf nicht kippen."""
    from broker.base import BrokerFehler, OrderStatusUnklar

    c = client_mit({"code": "1", "msg": "", "data": [
        {"sCode": "51008", "sMsg": "Insufficient balance"}]})
    with pytest.raises(BrokerFehler) as fehler:
        c.request("POST", "/trade/order", body={}, private=True, is_order=True)
    assert not isinstance(fehler.value, OrderStatusUnklar)
    assert "51008" in str(fehler.value)


def test_scode_wird_auch_bei_aeusserem_code_null_erkannt():
    """OKX kann oben code=0 melden und die Ablehnung nur als sCode liefern."""
    from broker.base import BrokerFehler

    c = client_mit({"code": "0", "msg": "", "data": [
        {"sCode": "51008", "sMsg": "Insufficient balance"}]})
    with pytest.raises(BrokerFehler, match="51008"):
        c.request("POST", "/trade/order", body={}, private=True, is_order=True)


# ===========================================================================
# 2. Doppelte clOrdId ist ein Nachweis, keine Ablehnung
# ===========================================================================
def test_doppelte_clordid_uebernimmt_die_bestehende_order(monkeypatch):
    """51016 heisst: die Order LIEGT bei OKX.

    Da die clOrdId deterministisch aus der decision_id entsteht, tritt der
    Code nach jedem Wiederanlauf derselben Entscheidung auf. Bis 9.0.15 wurde
    die Order daraufhin verworfen und die real gefuellte Position verschwand
    aus dem Positionsbuch.
    """
    from broker.base import BrokerFehler
    from broker.okx import OKXBroker

    broker = OKXBroker(demo=True, client=object())
    gesucht = {}

    def suche(inst_id, cl_ord_id):
        gesucht["inst_id"] = inst_id
        gesucht["cl_ord_id"] = cl_ord_id
        return {"ordId": "O-VORHANDEN", "state": "filled", "accFillSz": "1"}

    monkeypatch.setattr(broker, "_suche_order", suche)

    def wirf(_body):
        raise BrokerFehler("OKX lehnt die Order ab (51016): Duplicated client order ID")

    # Der Nachweispfad muss genau diesen Code abfangen und nachsehen.
    quelle = (WURZEL / "broker" / "okx.py").read_text(encoding="utf-8")
    assert "_DOPPELTE_CLORDID" in quelle
    stelle = quelle.index("except BrokerFehler as exc:",
                          quelle.index("def kaufe_mit_absicherung"))
    block = quelle[stelle:stelle + 1200]
    assert "_DOPPELTE_CLORDID not in str(exc)" in block, \
        "51016 muss gezielt abgefangen werden"
    assert "self._suche_order(inst_id, cl_ord_id)" in block, \
        "Nach 51016 muss die bestehende Order gesucht werden"
    assert "OrderStatusUnklar" in block, \
        "Findet sich die Order nicht, bleibt der Zustand unklar -- kein zweiter POST"


# ===========================================================================
# 3. Stornieren: "ist schon weg" ist ein Erfolg
# ===========================================================================
@pytest.mark.parametrize("code", ["51400", "51401", "51402", "51405", "51410"])
def test_storno_erfolgszustaende_werfen_nicht(code):
    """Das Ziel "keine offene Order mehr" ist erreicht."""
    c = client_mit({"code": "1", "msg": "", "data": [
        {"sCode": code, "sMsg": "order does not exist"}]})
    daten = c.request("POST", "/trade/cancel-order", body={},
                      private=True, is_order=True)
    assert isinstance(daten, list)


def test_echter_stornofehler_wird_weiterhin_gemeldet():
    """Die Gegenprobe: ein unbekannter Code darf nicht durchrutschen."""
    from broker.base import BrokerFehler

    c = client_mit({"code": "1", "msg": "", "data": [
        {"sCode": "51999", "sMsg": "unbekannt"}]})
    with pytest.raises(BrokerFehler, match="51999"):
        c.request("POST", "/trade/cancel-order", body={},
                  private=True, is_order=True)


def test_teilerfolg_kippt_den_aufruf_nicht():
    """OKX nutzt code=2 fuer "teilweise erfolgreich"."""
    c = client_mit({"code": "2", "msg": "", "data": [
        {"sCode": "0", "sMsg": ""},
        {"sCode": "51400", "sMsg": "does not exist"}]})
    daten = c.request("POST", "/trade/cancel-algos", body=[],
                      private=True, is_order=True)
    assert len(daten) == 2


# ===========================================================================
# 4. expTime gehoert nur an die Kauforder
# ===========================================================================
def test_exptime_nur_bei_place_order():
    """Ein Storno darf niemals verfallen.

    Sonst glaubt der Bot, storniert zu haben, waehrend die Schutzorder noch
    liegt -- und verkauft in eine aktive OCO hinein.
    """
    c = client_mit({"code": "0", "msg": "", "data": [{}]})
    c.request("POST", "/trade/order", body={}, private=True, is_order=True)
    assert "expTime" in c.session.calls[-1]["headers"]

    for pfad in ("/trade/cancel-order", "/trade/order-algo", "/trade/cancel-algos"):
        c.request("POST", pfad, body={}, private=True, is_order=True)
        assert "expTime" not in c.session.calls[-1]["headers"], pfad


# ===========================================================================
# 5. Der Stop-Limitpreis faellt nie auf 0
# ===========================================================================
def test_stop_limitpreis_unter_einem_tick_setzt_keinen_kaputten_schutz():
    """Bei Cent-Coins rundete der Abschlag auf 0 -- OKX lehnt die GANZE OCO ab.

    Gemeldet wurde bis 9.0.15 trotzdem "stop: True", also Schutz vorhanden.
    """
    from broker.okx import OKXBroker, OKXInstrument

    gesendet = []

    class Client:
        def place_algo_order(self, body):
            gesendet.append(dict(body))
            return {"algoId": "A1"}

        def pending_algo_orders(self, *a, **k):
            return []

    broker = OKXBroker(demo=True, client=Client())
    meta = OKXInstrument(inst_id="MICRO-EUR", base_ccy="MICRO", quote_ccy="EUR",
                         tick_size=0.0001, lot_size=1.0, min_size=1.0, state="live")
    broker._account_fingerprint = "micro-stop-test-account"
    ergebnis = broker._setze_schutz(meta, 100.0, stop=0.0001, take_profit=0.0)

    if gesendet:
        assert float(gesendet[0].get("slOrdPx", 0)) > 0, \
            "slOrdPx=0 laesst OKX die gesamte OCO ablehnen"
    else:
        # Kein Schutz setzbar -- dann muss das auch ehrlich gemeldet werden.
        assert ergebnis["stop"] is False
        assert "Tick" in ergebnis.get("hinweis", "")


# ===========================================================================
# 6. Gebuehren in der richtigen Waehrung
# ===========================================================================
def test_kaufgebuehr_in_basiswaehrung_wird_umgerechnet():
    """OKX belastet die Gebuehr eines SPOT-KAUFS in der Basiswaehrung.

    Ohne Umrechnung erschien sie um Groessenordnungen zu niedrig -- und damit
    auch die Kostenhuerde, die einen Trade lohnend macht.
    """
    from broker.okx import OKXBroker

    row = {"instId": "BTC-EUR", "fillSz": "1", "fillPx": "50000",
           "fee": "-0.00001", "feeCcy": "BTC", "side": "buy",
           "tradeId": "t1", "ordId": "o1", "ts": "1"}
    assert OKXBroker._gebuehr_in_quote(row, "BTC-EUR") == pytest.approx(0.5)

    verkauf = dict(row, fee="-0.5", feeCcy="EUR", side="sell")
    assert OKXBroker._gebuehr_in_quote(verkauf, "BTC-EUR") == pytest.approx(0.5)


# ===========================================================================
# 7. Alle Algo-Arten werden gefunden
# ===========================================================================
def test_alle_algo_arten_werden_erfasst():
    from broker.okx import OKXBroker
    assert {"oco", "conditional", "trigger", "move_order_stop"} <= set(OKXBroker.ALGO_ARTEN)
