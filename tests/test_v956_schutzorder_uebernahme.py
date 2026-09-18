"""9.5.6 -- Die LINK-Schleife vom 03.09.2026.

BEFUND aus dem Systemprotokoll, im Minutentakt ueber Stunden:

    09:55:03 WARNING broker.okx: OKX-Schutzorder LINK-USDC abgelehnt:
      OKX lehnt die Order ab (51008): Order failed. Your available LINK
      balance is insufficient, and your available margin (in USD) is too low
      for borrowing.
    09:56:10 ... dieselbe Zeile
    09:57:16 ... dieselbe Zeile

URSACHE: ``reconcile_position_protection`` filtert die vorhandenen
Schutzorders ausschliesslich ueber die EIGENE algoId beziehungsweise
algoClOrdId. Ist die Kennung verloren -- genau der Befund aus 9.5.4, bei dem
BNB, LINK und XLM mit leerer algoId dastanden --, findet der Filter nichts.
Die Position gilt als ungeschuetzt, NEXUS sendet eine ZWEITE Schutzorder, und
OKX lehnt sie ab, weil die bereits liegende Order das Guthaben festhaelt.

NEXUS blockierte sich mit seiner eigenen Schutzorder und sah sie nicht. Der
Zustand konnte sich nie von selbst aufloesen.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Die echte LINK-Position vom 02./03.09.2026.
MENGE = 9.08808
STOP = 9.7
TAKE = 11.8
FREMDE_ALGO_ID = "3879429521221038081"


class SchutzClient:
    hat_zugangsdaten = True
    clock_offset_seconds = 0.0

    def __init__(self, algos, guthaben_reicht=False):
        from broker.okx import OKXInstrument
        self.meta = OKXInstrument("LINK-USDC", "LINK", "USDC", "live",
                                  "0.001", "0.00001", "0.01")
        self._algos = list(algos)
        self._guthaben_reicht = guthaben_reicht
        self.gesendete_schutzorders = []

    def instrument(self, _i): return self.meta
    def pending_orders(self, *_a, **_k): return []
    def pending_algo_orders(self, inst_id="", ord_type="oco"):
        return list(self._algos) if ord_type == "oco" else []
    def balances(self):
        return {"USDC": {"cash": 500.0, "gesamt": 500.0},
                "LINK": {"cash": 0.0 if not self._guthaben_reicht else MENGE,
                         "gesamt": MENGE, "frozen": MENGE}}

    def place_algo_order(self, body):
        from broker.base import BrokerFehler
        self.gesendete_schutzorders.append(dict(body))
        if not self._guthaben_reicht:
            # Genau die echte OKX-Antwort aus dem Logbuch.
            raise BrokerFehler(
                "OKX lehnt die Order ab (51008): Order failed. Your available "
                "LINK balance is insufficient, and your available margin "
                "(in USD) is too low for borrowing.")
        return {"algoId": "neu-1"}

    def cancel_algo_orders(self, _rows): return []
    def algo_order_details(self, _algo_id): return {}


def liegende_order(algo_id=FREMDE_ALGO_ID, sz=MENGE, sl=STOP, tp=TAKE):
    return {"algoId": algo_id, "algoClOrdId": "", "instId": "LINK-USDC",
            "side": "sell", "sz": str(sz), "slTriggerPx": str(sl),
            "tpTriggerPx": str(tp), "state": "live", "ordType": "oco"}


def broker(algos, **kw):
    from broker.okx import OKXBroker
    b = OKXBroker(client=SchutzClient(algos, **kw), quote_ccy="USDC")
    b.account_fingerprint = lambda: "testkonto"
    return b


def instrument():
    from types import SimpleNamespace
    return SimpleNamespace(name="LINK", asset_type="crypto",
                           contract=SimpleNamespace(localSymbol="LINK-USDC"))


def test_passende_order_ohne_kennung_wird_uebernommen():
    """Der Kern: keine zweite Order, sondern die vorhandene anerkennen."""
    b = broker([liegende_order()])

    zustand = b.reconcile_position_protection(
        instrument(), MENGE, STOP, TAKE, protection_algo_id="",
        protection_client_id="")

    assert zustand["protection_confirmed"] is True
    assert zustand["algo_id"] == FREMDE_ALGO_ID, (
        "Die Kennung der vorhandenen Order muss uebernommen werden")
    assert zustand.get("uebernommen") is True
    assert b.client.gesendete_schutzorders == [], (
        "Es darf KEINE zweite Schutzorder gesendet werden -- genau daran "
        "scheiterte LINK im Minutentakt")


def test_fremde_teilorder_erklaert_den_fehlbestand_nicht():
    """9.5.7: Der Riegel greift nur, wenn liegende Orders den Fehlbestand
    wirklich erklaeren.

    Hier haelt eine FREMDE Order nur die Haelfte. Dass trotzdem nichts frei
    ist, hat also eine andere Ursache. Dann soll OKX klar antworten, statt
    dass NEXUS still auf Schutz verzichtet -- ein stummer Verzicht waere die
    gefaehrlichere Variante.
    """
    b = broker([liegende_order(sz=MENGE / 2)])

    zustand = b.reconcile_position_protection(
        instrument(), MENGE, STOP, TAKE, protection_algo_id="",
        protection_client_id="")

    assert zustand["protection_confirmed"] is False
    assert b.client.gesendete_schutzorders, (
        "Eine Teilorder ueber die Haelfte erklaert den vollen Fehlbestand "
        "nicht -- der Versuch muss stattfinden")


def test_fremde_order_ohne_kennung_und_mit_anderem_stop():
    """Eine fremde Order (keine NEXUS-Kennung, andere Werte) bleibt fremd.

    9.5.7: Uebernommen wird sie nicht. Gesendet wird aber auch nichts, denn
    ihr Guthaben ist gebunden -- eine zweite Order koennte gar nicht liegen.
    """
    b = broker([liegende_order(sl=STOP * 0.8)])

    zustand = b.reconcile_position_protection(
        instrument(), MENGE, STOP, TAKE, protection_algo_id="",
        protection_client_id="")

    assert zustand["protection_confirmed"] is False
    assert not zustand.get("uebernommen")
    assert b.client.gesendete_schutzorders == []


def test_mehrdeutigkeit_wird_nicht_uebernommen():
    """Zwei passende Orders: es wird nichts geraten."""
    b = broker([liegende_order(algo_id="a1"), liegende_order(algo_id="a2")])

    zustand = b.reconcile_position_protection(
        instrument(), MENGE, STOP, TAKE, protection_algo_id="",
        protection_client_id="")

    assert zustand["protection_confirmed"] is False
    assert not zustand.get("uebernommen")


def test_bekannte_kennung_geht_weiterhin_den_direkten_weg():
    """Die Uebernahme ist ein Rueckfall, kein Ersatz."""
    b = broker([liegende_order(algo_id="meine-1")])

    zustand = b.reconcile_position_protection(
        instrument(), MENGE, STOP, TAKE, protection_algo_id="meine-1",
        protection_client_id="")

    assert zustand["protection_confirmed"] is True
    assert zustand["algo_id"] == "meine-1"
    assert not zustand.get("uebernommen"), (
        "Ueber die eigene Kennung gefunden ist keine Uebernahme")
