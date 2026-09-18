"""9.5.8 -- Der XLM-Fall vom 03.09.2026: FOK-Verkauf fuellt 0 von 560,88.

BEFUND aus dem Logbuch:

    17:13:13  Freqtrade-Ausgang XLM: freqtrade_roi_1pct, current_price 0.18347
    17:13:13  Verkauf XLM wartet bis ... (vorheriger Versuch: 0 von 560.88
              ausgefuehrt; FOK nicht vollstaendig innerhalb der Preisgrenze;
              OKX cancelSource=13.)

cancelSource=13 heisst bei OKX: FOK/IOC konnte nicht vollstaendig ausgefuehrt
werden und wurde storniert.

URSACHE: Der Limitpreis wurde als ``best * (1 - max_slippage)`` gebildet, die
Zulassungspruefung misst aber den VWAP -- einen Durchschnitt ueber die gesamte
Menge. Liegt ``worst`` (der tiefste Bid, der zum Fuellen gebraucht wird) weiter
als max_slippage unter ``best``, besteht die Order die VWAP-Pruefung muehelos
und kann trotzdem strukturell nicht fuellen.

Es ist derselbe Fehler wie auf der Kaufseite in 9.5.4, nur spiegelverkehrt:
dort war das Limit zu niedrig, hier zu hoch. Der Kaufpfad sichert seither mit
``max(worst, ...)`` ab; der Verkaufspfad hatte keine Entsprechung.

Diese Tests pruefen VERHALTEN (welcher Limitpreis geht an OKX), nicht den
Quelltext.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

MENGE = 560.88
BEST = 0.18347
# Zweites Level, das gebraucht wird -- weiter als 1 % unter dem besten Bid.
TIEF = 0.18000
MAX_EXIT_SLIPPAGE = 0.01


from okx_test_band import DemoWithoutPriceBand


class XlmClient(DemoWithoutPriceBand):
    hat_zugangsdaten = True
    clock_offset_seconds = 0.0

    def __init__(self, bids, frei=MENGE):
        from broker.okx import OKXInstrument
        self.meta = OKXInstrument("XLM-USDC", "XLM", "USDC", "live",
                                  "0.00001", "0.000001", "1",
                                  trade_quote_ccy_list=("USDC",))
        self._bids = list(bids)
        self._frei = float(frei)
        self.gesendete_orders: list[dict] = []

    def instrument(self, _i):
        return self.meta

    def orderbook(self, _inst_id, depth=100):
        import time
        return {"bids": [[str(p), str(q)] for p, q in self._bids],
                "asks": [], "timestamp_ms": int(time.time() * 1000)}

    def balances(self):
        return {"USDC": {"cash": 500.0, "gesamt": 500.0},
                "XLM": {"cash": self._frei, "gesamt": self._frei, "frozen": 0.0}}

    def pending_algo_orders(self, inst_id="", ord_type="oco"):
        return []

    def pending_orders(self, *_a, **_k):
        return []

    def place_order(self, body):
        self.gesendete_orders.append(dict(body))
        # FOK: nur fuellen, wenn das Buch die GANZE Menge zum Limit hergibt.
        limit = float(body["px"])
        menge = float(body["sz"])
        deckung = sum(q for p, q in self._bids if p >= limit)
        if deckung + 1e-9 >= menge:
            return {"ordId": "OID-1", "clOrdId": body["clOrdId"]}
        return {"ordId": "OID-1", "clOrdId": body["clOrdId"]}

    def order_status(self, _inst_id, ord_id="", cl_ord_id=""):
        body = self.gesendete_orders[-1]
        limit = float(body["px"])
        menge = float(body["sz"])
        deckung = sum(q for p, q in self._bids if p >= limit)
        if deckung + 1e-9 >= menge:
            wert = 0.0
            rest = menge
            for p, q in sorted(self._bids, key=lambda r: -r[0]):
                nimm = min(rest, q)
                wert += nimm * p
                rest -= nimm
                if rest <= 1e-12:
                    break
            return {"instId": "XLM-USDC", "side": "sell", "state": "filled",
                    "ordId": "OID-1", "clOrdId": cl_ord_id, "sz": str(menge),
                    "accFillSz": str(menge), "avgPx": str(wert / menge)}
        return {"instId": "XLM-USDC", "side": "sell", "state": "canceled",
                "ordId": "OID-1", "clOrdId": cl_ord_id, "sz": str(menge),
                "accFillSz": "0", "cancelSource": "13"}

    def fills(self, _inst_id, limit=100, *, ord_id=""):
        """Echte tradeId-Fills -- nur, wenn die Order auch gefuellt wurde."""
        status = self.order_status(_inst_id)
        if str(status.get("state")) != "filled":
            return []
        return [{"instId": "XLM-USDC", "ordId": "OID-1", "tradeId": "T-1",
                 "side": "sell", "fillSz": status["accFillSz"],
                 "fillPx": status["avgPx"], "fee": "-0.01", "feeCcy": "USDC",
                 "ts": "1756900000000"}]

    def fills_history_paginated(self, _inst_id, max_pages=3, *, ord_id=""):
        return []

    def cancel_algo_orders(self, _rows):
        return []


def broker(bids, **kw):
    from broker.okx import OKXBroker
    b = OKXBroker(client=XlmClient(bids, **kw), quote_ccy="USDC",
                  allowed_quotes=("USDC", "USD", "EUR"))
    b.account_fingerprint = lambda: "testkonto"
    return b


def xlm():
    return SimpleNamespace(name="XLM", asset_type="crypto",
                           contract=SimpleNamespace(localSymbol="XLM-USDC"))


@pytest.fixture(autouse=True)
def _grenzen(monkeypatch):
    import config
    monkeypatch.setattr(config, "OKX_MAX_EXIT_SLIPPAGE_PCT", MAX_EXIT_SLIPPAGE,
                        raising=False)
    monkeypatch.setattr(config, "OKX_CANCEL_RELEASE_TIMEOUT", 2.0, raising=False)


# ---------------------------------------------------------------------------
# Der Kern
# ---------------------------------------------------------------------------
def test_limit_deckt_die_gemessene_buchtiefe():
    """Das Limit darf nie ueber dem tiefsten benoetigten Bid liegen."""
    b = broker([(BEST, 500.0), (TIEF, 200.0)])
    ergebnis = b.schliesse_position(xlm(), MENGE, referenzpreis=BEST)

    gesendet = b.client.gesendete_orders[-1]
    assert float(gesendet["px"]) <= TIEF + 1e-12, (
        f"Limit {gesendet['px']} liegt ueber dem noetigen Bid {TIEF} -- "
        "der FOK kann nicht vollstaendig fuellen")
    assert ergebnis.filled_quantity > 0


def test_die_alte_formel_haette_hier_null_gefuellt():
    """Belegt, dass genau dieses Buch den Fehler bis 9.5.7 ausloest."""
    altes_limit = BEST * (1.0 - MAX_EXIT_SLIPPAGE)
    deckung = sum(q for p, q in [(BEST, 500.0), (TIEF, 200.0)] if p >= altes_limit)
    assert deckung < MENGE, (
        "Der Testfall trifft den Defekt nicht mehr -- Buch anpassen")


def test_slippage_gate_bleibt_die_zulassungsschwelle():
    """Ist schon der VWAP ueber der Grenze, wird gar nichts gesendet."""
    from broker.base import BrokerFehler
    # Ganz duennes Buch: der Durchschnitt liegt weit unter der Grenze.
    b = broker([(BEST, 10.0), (0.10, 1000.0)])
    with pytest.raises(BrokerFehler) as exc:
        b.schliesse_position(xlm(), MENGE, referenzpreis=BEST)
    assert "slippage" in str(exc.value).lower()
    assert not b.client.gesendete_orders, "Trotz Ablehnung wurde gesendet"


def test_limit_wird_nicht_unter_die_buchtiefe_gesenkt():
    """Deckt best*(1-slip) das Buch bereits ab, bleibt es beim alten Wert."""
    # Alle 560,88 liegen auf dem besten Bid -- worst == best.
    b = broker([(BEST, 1000.0)])
    b.schliesse_position(xlm(), MENGE, referenzpreis=BEST)
    gesendet = b.client.gesendete_orders[-1]
    erwartet = BEST * (1.0 - MAX_EXIT_SLIPPAGE)
    tick = 0.00001                      # aus den Instrumentendaten oben
    assert float(gesendet["px"]) <= BEST
    # quantize_down rundet auf den Tick ab -- eine Tickbreite Abweichung ist
    # die Rundung, kein zusaetzlicher Abschlag.
    assert float(gesendet["px"]) >= erwartet - tick, (
        "Das Limit wurde ohne Not unter die Slippage-Grenze gesenkt")


def test_hinweis_nennt_die_zahlen_des_versuchs():
    """Nach einem 0-Fill muessen best/worst/Limit im Hinweis stehen."""
    # Buch, das selbst mit dem korrigierten Limit nicht reicht.
    b = broker([(BEST, 100.0), (TIEF, 100.0)], frei=MENGE)
    b.client._bids = [(BEST, 100.0), (TIEF, 100.0)]

    # execution_quote wuerde hier schon an der Tiefe scheitern; deshalb ein
    # Buch, das die Menge deckt, aber dessen Order der Broker ablehnt.
    b2 = broker([(BEST, 500.0), (TIEF, 200.0)])
    original = b2.client.order_status

    def storniert(*a, **k):
        antwort = dict(original(*a, **k))
        antwort.update({"state": "canceled", "accFillSz": "0",
                        "cancelSource": "13"})
        return antwort

    b2.client.order_status = storniert
    ergebnis = b2.schliesse_position(xlm(), MENGE, referenzpreis=BEST)
    assert ergebnis.filled_quantity == 0
    for teil in ("Limit war", "best", "worst"):
        assert teil in ergebnis.hinweis, f"'{teil}' fehlt im Hinweis"
