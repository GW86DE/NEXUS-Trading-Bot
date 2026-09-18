"""9.5.4 -- Preisfenster beim Einstieg.

Befund vom 02.09.2026: ETH, SOL und DOGE bestanden alle Gates, wurden als
FOK-Order gesendet und mit 0 gefuellt storniert. Ursache war nicht ein zu
enges Slippage-Budget, sondern dass das Budget gar nicht als Spielraum
benutzt wurde: der Limitpreis war der exakt gemessene schlechteste
Orderbuchpreis, aufgerundet um einen einzigen Tick. Bewegte sich der Markt
zwischen Messung und Matching um einen Tick nach oben, konnte FOK nicht mehr
vollstaendig fuellen -- und storniert dann komplett.

Diese Tests treiben die echte Order-Konstruktion und pruefen den erzeugten
Order-Body. Es wird kein Quelltext nach Zeichenketten durchsucht.
"""
from __future__ import annotations

import time
from types import SimpleNamespace

import pytest


from okx_test_band import DemoWithoutPriceBand


class Buch(DemoWithoutPriceBand):
    """Minimaler OKX-Client mit steuerbarem Orderbuch."""

    hat_zugangsdaten = True
    clock_offset_seconds = 0.0

    def __init__(self, asks, bids=None, tick="0.01", lot="0.000001"):
        from broker.okx import OKXInstrument
        self.meta = OKXInstrument("ETH-USDC", "ETH", "USDC", "live",
                                  tick, lot, "0.0001")
        self.asks = list(asks)
        self.bids = list(bids or [(a[0] * 0.999, a[1]) for a in asks])
        self.orders = []
        self.book_ts = int(time.time() * 1000)

    def instrument(self, _inst): return self.meta
    def pending_orders(self, *_a, **_k): return []
    def pending_algo_orders(self, *_a, **_k): return []
    def balances(self):
        return {"USDC": {"cash": 100000, "gesamt": 100000},
                "ETH": {"cash": 10, "gesamt": 10}}
    def orderbook(self, _inst, depth=100):
        return {"timestamp_ms": self.book_ts, "asks": self.asks, "bids": self.bids}
    def place_order(self, body):
        self.orders.append(dict(body))
        return {"ordId": f"o{len(self.orders)}", "clOrdId": body.get("clOrdId")}
    def order_status(self, *_a, **kw):
        return {"ordId": str(kw.get("ord_id") or "o1"), "state": "filled",
                "accFillSz": str(self.orders[-1]["sz"]), "avgPx": self.orders[-1]["px"]}
    def fills(self, *_a, **_k):
        o = self.orders[-1]
        return [{"tradeId": "f1", "ordId": f"o{len(self.orders)}", "instId": "ETH-USDC",
                 "side": o["side"], "fillSz": o["sz"], "fillPx": o["px"],
                 "fee": "0", "feeCcy": "USDC", "ts": "1"}]
    def fills_history_paginated(self, *_a, **_k): return []
    def place_algo_order(self, body): return {"algoId": "algo-eth"}
    def cancel_algo_orders(self, _rows): return []
    def cancel_order(self, *_a, **_k): return {}


def eth():
    return SimpleNamespace(name="ETH", asset_type="crypto",
                           contract=SimpleNamespace(localSymbol="ETH-USDC"))


def kauf_limit(client, menge=0.05):
    from broker.okx import OKXBroker
    broker = OKXBroker(client=client, quote_ccy="USDC")
    broker._account_fingerprint = "fixture-test_v954_einstiegs_preisfenster"  # Exakte Testkontobindung
    broker.kaufe_mit_absicherung(eth(), menge, float(client.asks[0][0]),
                                 float(client.asks[0][0]) * 0.9,
                                 float(client.asks[0][0]) * 1.1)
    return float(client.orders[0]["px"])


def test_limit_liegt_ueber_dem_gemessenen_schlechtesten_preis():
    """Der Kern des Fehlers: das Limit war der Momentanpreis selbst."""
    client = Buch(asks=[(2380.00, 5.0)])
    limit = kauf_limit(client)
    # Ohne Puffer waere es 2380.00 gewesen -- ein Tick Bewegung genuegte,
    # um die FOK-Order komplett zu stornieren.
    assert limit > 2380.00
    assert limit == pytest.approx(2380.00 * 1.0015, rel=0, abs=0.01)


def test_puffer_ist_auf_das_restbudget_gedeckelt():
    """Der Puffer darf die Slippage-Grenze niemals ueberschreiten."""
    import config
    grenze = float(config.OKX_MAX_ENTRY_SLIPPAGE_PCT)
    # Tiefes, teures Buch: die gemessene Slippage frisst fast das ganze Budget.
    client = Buch(asks=[(2380.00, 0.01), (2380.00 * (1 + grenze * 0.98), 5.0)])
    limit = kauf_limit(client, menge=1.0)
    # Zugesagt wird: nie mehr als die Slippage-Grenze ueber dem besten Brief
    # (plus die unvermeidbare Aufrundung auf einen Tick).
    tick = float(client.meta.tick_size)
    assert limit <= 2380.00 * (1.0 + grenze) + tick, (
        f"Das Limit {limit} hat die Slippage-Grenze "
        f"{2380.00 * (1.0 + grenze):.2f} verlassen")


def test_zu_teures_buch_wird_weiterhin_blockiert():
    """Das Preisfenster ist breiter, aber es ist nicht offen."""
    from broker.base import BrokerFehler
    import config
    grenze = float(config.OKX_MAX_ENTRY_SLIPPAGE_PCT)
    client = Buch(asks=[(2380.00, 0.01), (2380.00 * (1 + grenze * 3), 5.0)])
    with pytest.raises(BrokerFehler, match="Slippage"):
        kauf_limit(client, menge=1.0)
    assert client.orders == [], "Bei Ueberschreitung darf nichts gesendet werden"


def test_order_bleibt_fok_und_volle_menge():
    """Der Puffer aendert nur den Preis, nicht die Ausfuehrungsart."""
    client = Buch(asks=[(2380.00, 5.0)])
    kauf_limit(client, menge=0.05)
    body = client.orders[0]
    assert body["ordType"] == "fok"
    assert float(body["sz"]) == pytest.approx(0.05)
    assert body["side"] == "buy"


def test_ein_tick_marktbewegung_haette_vorher_storniert():
    """Der eigentliche Beweis: das neue Limit haelt eine Tickbewegung aus.

    Nachgestellt wird die Situation vom 02.09.: gemessen wird bei 2380.00,
    ausgefuehrt wird gegen ein um einen Tick hoeheres Buch.
    """
    client = Buch(asks=[(2380.00, 5.0)])
    limit = kauf_limit(client)
    tick = float(client.meta.tick_size)
    markt_beim_matching = 2380.00 + tick
    assert limit >= markt_beim_matching, (
        f"Limit {limit} deckt eine Tickbewegung auf {markt_beim_matching} nicht ab")


def test_teures_restlevel_darf_den_harten_preisdeckel_nicht_anheben():
    """Auch ein guenstiger VWAP erlaubt keinen einzelnen Fill ueber dem Cap."""
    import config
    from broker.base import BrokerFehler
    grenze = float(config.OKX_MAX_ENTRY_SLIPPAGE_PCT)
    teuer = 2380.00 * (1.0 + grenze * 2)
    client = Buch(asks=[(2380.00, 0.999), (teuer, 5.0)])
    with pytest.raises(BrokerFehler, match="Preis"):
        kauf_limit(client, menge=1.0)
    assert client.orders == []
