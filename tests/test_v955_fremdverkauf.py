"""9.5.5 -- Ein selbst ausgeloester Verkauf muss abgerechnet werden.

DER GEMELDETE FALL: "Auch wurde der ETH Verkauf nicht berechnet. Also Gewinn
oder Verlust. Ich hatte ihn direkt verkauft."

URSACHE, im Code belegt: ``external_exit_evidence`` begann mit

    allowed_orders = {...}
    if not allowed_orders:
        return {}

Belegbar war damit ausschliesslich ein Verkauf, dessen ordId NEXUS vorher
schon kannte -- ein eigener Exit oder eine ausgeloeste eigene Schutzorder.
Eine Order, die der Nutzer selbst in der OKX-App sendet, steht in keiner
dieser Quellen. Sie war strukturell nie beweisbar, und die Position wurde
anschliessend mit "Ergebnis bleibt unbekannt" geschlossen.

Das ist kein Aufweichen des Eigentumsbeweises: die Fills stammen aus dem
Konto des Bots, tragen echte tradeIds und werden bei OKX abgefragt.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class VerkaufsClient:
    hat_zugangsdaten = True
    clock_offset_seconds = 0.0

    def __init__(self, fremde_ord_id="FREMD-AUS-DER-APP"):
        from broker.okx import OKXInstrument
        self.meta = OKXInstrument("ETH-EUR", "ETH", "EUR", "live",
                                  "0.01", "0.000001", "0.0001")
        self._ord = fremde_ord_id

    def instrument(self, _i): return self.meta

    def order_status(self, inst_id, **_kw):
        return dict(instId=inst_id, ordId=self._ord, state='filled', side='sell',
                    accFillSz='1.043377', tradeQuoteCcy='EUR')

    def fills(self, _inst, limit=100, *, ord_id=""):
        # Ein Verkauf, den der Nutzer selbst gesendet hat: echte tradeId,
        # aber eine ordId, die NEXUS nie gesehen hat.
        return [{"tradeId": "t-manuell-1", "ordId": self._ord,
                 "instId": "ETH-EUR", "side": "sell", "fillSz": "1.043377",
                 "fillPx": "2050.0", "fee": "-1.07", "feeCcy": "EUR",
                 "ts": str(int(time.time() * 1000))}]

    def fills_history_paginated(self, *_a, **_k): return []
    def balances(self): return {"EUR": {"cash": 5000.0, "gesamt": 5000.0}}


def broker():
    from broker.okx import OKXBroker
    b = OKXBroker(client=VerkaufsClient(), quote_ccy="EUR")
    b.account_fingerprint = lambda: "testkonto"
    return b


SEIT = "2026-08-01T00:00:00+00:00"


def test_ohne_bekannte_order_id_gab_es_frueher_keinen_beweis():
    """Das alte Verhalten bleibt erreichbar und unveraendert streng."""
    beweis = broker().external_exit_evidence(
        inst_id="ETH-EUR", since=SEIT, expected_qty=1.043377,
        expected_order_ids=[])
    assert beweis == {}, (
        "Ohne ausdrueckliche Erlaubnis bleibt es beim strengen Verhalten")


def test_selbst_verkauft_wird_mit_echten_fills_belegt():
    """Der Kern: der Verkauf aus der OKX-App ist jetzt abrechenbar."""
    beweis = broker().external_exit_evidence(
        inst_id="ETH-EUR", since=SEIT, expected_qty=1.043377,
        expected_order_ids=[], fremdverkauf_erlauben=True, entry_order_id='entry')

    assert beweis.get("confirmed") is True
    assert beweis["quantity"] == pytest.approx(1.043377)
    assert beweis["avg_price"] == pytest.approx(2050.0)
    assert beweis["raw_fill_ids"] == ["t-manuell-1"], (
        "Der Beweis muss auf echten tradeIds stehen, nicht auf einer Annahme")
    assert beweis["beweisart"] == "FREMDVERKAUF", (
        "Ein Fremdverkauf darf nie als eigener Ausstieg verbucht werden")


def test_eigener_exit_bleibt_als_eigener_gekennzeichnet():
    """Die Unterscheidung muss in beide Richtungen tragen."""
    beweis = broker().external_exit_evidence(
        inst_id="ETH-EUR", since=SEIT, expected_qty=1.043377,
        expected_order_ids=["FREMD-AUS-DER-APP"], fremdverkauf_erlauben=True)

    assert beweis.get("confirmed") is True
    assert beweis["beweisart"] == "EIGENER_EXIT"


def test_ergebnis_ist_rechenbar():
    """Aus dem Beweis muss ein echter Gewinn/Verlust folgen -- kein NULL."""
    beweis = broker().external_exit_evidence(
        inst_id="ETH-EUR", since=SEIT, expected_qty=1.043377,
        expected_order_ids=[], fremdverkauf_erlauben=True, entry_order_id='entry')

    einstieg = 2000.0
    menge = float(beweis["quantity"])
    gebuehr = float(beweis["fees_quote"])
    brutto = (float(beweis["avg_price"]) - einstieg) * menge
    netto = brutto - gebuehr

    assert brutto == pytest.approx(52.16885, abs=1e-4)
    assert gebuehr > 0, "Die Gebuehr muss aus den Fills kommen"
    assert netto < brutto
