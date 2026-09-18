"""Regressionstests der OKX-EEA-Anbindung (v8.1.1 NEXUS).

Alle Tests laufen OHNE Netzwerk: die REST-Schicht wird durch einen
Fake-Transport ersetzt. Damit ist der Geldpfad testbar, ohne dass jemals
eine echte Order entstehen kann.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("TRADINGBOT_TEST_STATE_DIR", "/tmp/tradingbot_tests")
Path(os.environ["TRADINGBOT_TEST_STATE_DIR"]).mkdir(parents=True, exist_ok=True)

from broker.base import AuthentifizierungsFehler, BrokerFehler, OrderStatusUnklar  # noqa: E402
from broker.okx import (  # noqa: E402
    OKXBroker, OKXClient, OKXInstrument, OKXTicker,
    format_decimal, make_client_order_id, normalize_inst_id,
    quantize_down, quantize_price,
)


# ---------------------------------------------------------------------------
# Fake-Transport
# ---------------------------------------------------------------------------
class FakeAntwort:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status
        self.content = b"x"

    def json(self):
        return self._payload


class FakeSession:
    """Ersetzt requests.Session und protokolliert jeden Aufruf."""

    def __init__(self):
        self.headers = {}
        self.aufrufe = []
        self.antworten = {}
        self.fehler = None

    def antworte(self, pfad_teil, payload, status=200):
        self.antworten[pfad_teil] = FakeAntwort(payload, status)

    def request(self, method, url, headers=None, data=None, timeout=None):
        self.aufrufe.append({"method": method, "url": url, "headers": dict(headers or {}),
                             "data": data})
        if self.fehler is not None:
            fehler, self.fehler = self.fehler, None
            raise fehler
        # Laengster passender Schluessel gewinnt. Sonst wuerde '/trade/order'
        # auch die Statusabfrage '/trade/order?instId=...' abfangen.
        for teil in sorted(self.antworten, key=len, reverse=True):
            if teil in url:
                return self.antworten[teil]
        if '/public/price-limit?' in url:
            from urllib.parse import urlsplit, parse_qs
            from okx_test_band import DemoWithoutPriceBand
            return FakeAntwort({'code':'0','data':DemoWithoutPriceBand.price_limit(
                parse_qs(urlsplit(url).query)['instId'][0])})
        return FakeAntwort({"code": "0", "data": []})

    def close(self):
        pass


def baue_client(demo=True) -> tuple[OKXClient, FakeSession]:
    client = OKXClient("KEY", "SECRET", "PHRASE", demo=demo)
    session = FakeSession()
    client.session = session
    return client, session


# ---------------------------------------------------------------------------
# Signatur und Kopfzeilen
# ---------------------------------------------------------------------------
def test_signatur_entspricht_okx_spezifikation():
    """base64(HMAC-SHA256(secret, timestamp + METHOD + path + body))."""
    client, _ = baue_client()
    ts = "2026-08-23T10:11:12.345Z"
    pfad = "/api/v5/account/balance"
    erwartet = base64.b64encode(
        hmac.new(b"SECRET", f"{ts}GET{pfad}".encode(), hashlib.sha256).digest()).decode()
    assert client._signature(ts, "GET", pfad, "") == erwartet


def test_oeffentlicher_katalog_bleibt_auch_im_demo_modus_live():
    client, session = baue_client(demo=True)
    session.antworte("/public/time", {"code": "0", "data": [{"ts": "1"}]})
    client.server_time_ms()
    assert "x-simulated-trading" not in session.aufrufe[-1]["headers"]


def test_live_modus_setzt_keine_simulationskopfzeile():
    client, session = baue_client(demo=False)
    session.antworte("/public/time", {"code": "0", "data": [{"ts": "1"}]})
    client.server_time_ms()
    assert "x-simulated-trading" not in session.aufrufe[-1]["headers"]


def test_privater_aufruf_ohne_zugangsdaten_scheitert_sofort():
    client = OKXClient("", "", "", demo=True)
    client.session = FakeSession()
    with pytest.raises(AuthentifizierungsFehler):
        client.balances()


def test_geheimnisse_tauchen_nicht_in_fehlermeldungen_auf():
    client, session = baue_client()
    session.fehler = __import__("requests").exceptions.ConnectTimeout("kaputt")
    session.antworte("/public/time", {"code": "0", "data": []})
    with pytest.raises(Exception) as info:
        client.server_time_ms()
    text = str(info.value)
    assert "SECRET" not in text and "PHRASE" not in text and "KEY" not in text


# ---------------------------------------------------------------------------
# Mengen- und Preisrundung
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("wert,schritt,erwartet", [
    (1.23456789, "0.00000001", 1.23456789),
    (0.123456, "0.0001", 0.1234),
    (5.9, "1", 5.0),
    (0.00009, "0.0001", 0.0),
])
def test_menge_wird_immer_abgerundet(wert, schritt, erwartet):
    """Abrunden ist Pflicht: eine Order darf nie groesser werden als geplant."""
    assert quantize_down(wert, schritt) == pytest.approx(erwartet, abs=1e-12)


def test_preis_wird_auf_ticksize_gerundet():
    assert quantize_price(27123.4567, "0.1") == pytest.approx(27123.5)
    assert quantize_price(0.000016543, "0.0000001") == pytest.approx(0.0000165)


def test_formatierung_ohne_exponentialschreibweise():
    """OKX lehnt '1e-08' ab -- die Formatierung muss dezimal bleiben."""
    text = format_decimal(0.00000001, "0.00000001")
    assert "e" not in text.lower()
    assert text.startswith("0.0000000")


def test_client_order_id_ist_okx_konform():
    cid = make_client_order_id()
    assert cid.isalnum() and 1 <= len(cid) <= 32


@pytest.mark.parametrize("eingabe,erwartet", [
    ("btc", "BTC-EUR"), ("BTC/EUR", "BTC-EUR"),
    ("eth-usdt", "ETH-USDT"), ("SOL_EUR", "SOL-EUR"),
])
def test_symbolnormalisierung(eingabe, erwartet):
    assert normalize_inst_id(eingabe, "EUR") == erwartet


# ---------------------------------------------------------------------------
# Fehlerklassifizierung
# ---------------------------------------------------------------------------
def test_netzwerkfehler_bei_order_ist_unklar_nicht_fehlgeschlagen():
    """Der wichtigste Test des Moduls.

    Ein Transportabbruch nach einem POST heisst NICHT, dass die Order weg
    ist. Wird das als Misserfolg gewertet, kauft der Bot doppelt.
    """
    client, session = baue_client()
    session.fehler = __import__("requests").exceptions.ReadTimeout("weg")
    with pytest.raises(OrderStatusUnklar):
        client.place_order({"instId": "BTC-EUR"})


def test_fachliche_ablehnung_ist_eindeutig_kein_unklarer_zustand():
    client, session = baue_client()
    session.antworte("/trade/order", {"code": "1", "msg": "abgelehnt",
                                      "data": [{"sCode": "51008", "sMsg": "Guthaben reicht nicht"}]})
    with pytest.raises(BrokerFehler) as info:
        client.place_order({"instId": "BTC-EUR"})
    assert not isinstance(info.value, OrderStatusUnklar)
    assert "51008" in str(info.value)


def test_authentifizierungsfehler_wird_nicht_als_netzproblem_behandelt():
    client, session = baue_client()
    session.antworte("/account/balance", {"code": "50113", "msg": "invalid sign", "data": []})
    with pytest.raises(AuthentifizierungsFehler):
        client.balances()


# ---------------------------------------------------------------------------
# Marktdaten
# ---------------------------------------------------------------------------
def test_laufende_kerze_wird_verworfen():
    """confirm='0' bedeutet: die Kerze laeuft noch und darf kein Signal geben."""
    client, session = baue_client()
    session.antworte("/market/candles", {"code": "0", "data": [
        # ts, o, h, l, c, vol, volCcy, volCcyQuote, confirm
        ["1755950000000", "2", "3", "1", "2.5", "10", "25", "25", "0"],   # laeuft
        ["1755949100000", "1", "2", "1", "1.5", "12", "18", "18", "1"],   # fertig
        ["1755948200000", "1", "2", "1", "1.2", "11", "13", "13", "1"],   # fertig
    ]})
    df = client.candles("BTC-EUR", bar="15m")
    assert len(df) == 2
    assert df["close"].iloc[-1] == pytest.approx(1.5)
    assert df.index.is_monotonic_increasing


def test_ticker_spanne_und_tagesveraenderung():
    t = OKXTicker.from_api({"instId": "BTC-EUR", "last": "100", "bidPx": "99.5",
                            "askPx": "100.5", "open24h": "90", "vol24h": "5",
                            "volCcy24h": "500", "ts": "1755950000000"})
    assert t.spread_pct == pytest.approx(0.01, rel=1e-3)
    assert t.change_24h_pct == pytest.approx(0.1111, rel=1e-2)


def test_ticker_ohne_orderbuch_liefert_keine_spanne():
    t = OKXTicker.from_api({"instId": "X-EUR", "last": "1", "bidPx": "", "askPx": ""})
    assert t.spread_pct == 0.0


def test_instrument_alter_wird_aus_listtime_berechnet():
    import time
    vor_100_tagen = int((time.time() - 100 * 86400) * 1000)
    inst = OKXInstrument.from_api({"instId": "AAA-EUR", "baseCcy": "AAA", "quoteCcy": "EUR",
                                   "state": "live", "listTime": str(vor_100_tagen)})
    assert 99 <= inst.alter_tage <= 101
    assert inst.ist_live


# ---------------------------------------------------------------------------
# Orderpfad
# ---------------------------------------------------------------------------
def _broker_mit_fake(demo=True):
    client, session = baue_client(demo=demo)
    client._instruments = {"BTC-EUR": OKXInstrument(
        inst_id="BTC-EUR", base_ccy="BTC", quote_ccy="EUR", state="live",
        tick_size="0.1", lot_size="0.00000001", min_size="0.00001",
        list_time_ms=1_600_000_000_000)}
    client._instruments_at = 9e18      # Cache nie ablaufen lassen
    broker = OKXBroker(demo=demo, client=client)
    broker._account_fingerprint = "fixture-test_v70_okx"  # Exakte Testkontobindung
    return broker, session


def test_kauf_verwendet_fok_preisgrenze_und_basismenge():
    broker, session = _broker_mit_fake()
    session.antworte("/trade/order", {"code": "0", "data": [{"ordId": "1"}]})
    session.antworte("/trade/order?", {"code": "0", "data": [
        {"ordId": "1", "state": "filled", "accFillSz": "0.05", "avgPx": "60000"}]})
    session.antworte("/trade/fills?", {"code": "0", "data": [
        {"tradeId": "f1", "ordId": "1", "instId": "BTC-EUR",
         "fillSz": "0.05", "fillPx": "60000", "fee": "-0.00005",
         "feeCcy": "BTC", "ts": "1"}]})
    session.antworte("/trade/order-algo", {"code": "0", "data": [{"algoId": "a1"}]})
    session.antworte("/account/balance", {"code": "0", "data": [{"details": [
        {"ccy": "EUR", "availBal": "10000", "cashBal": "10000", "frozenBal": "0"}
    ]}]})
    session.antworte("/trade/orders-pending", {"code": "0", "data": []})
    session.antworte("/trade/orders-algo-pending", {"code": "0", "data": []})
    session.antworte("/market/books", {"code": "0", "data": [{
        "ts": str(int(time.time() * 1000)),
        "bids": [["59990", "1", "0", "1"]],
        "asks": [["60010", "1", "0", "1"]]}]})

    class Instrument:
        name = "BTC"
        asset_type = "crypto"

    ergebnis = broker.kaufe_mit_absicherung(Instrument(), 0.05, 60000, 57000, 66000)
    order_aufruf = next(a for a in session.aufrufe if a["method"] == "POST" and "/trade/order" in a["url"])
    body = json.loads(order_aufruf["data"])
    assert body["ordType"] == "fok"
    # 9.5.4: Frueher "60010" -- exakt der gemessene Brief. Ein Tick
    # Marktbewegung reichte, damit die FOK-Order komplett storniert wurde.
    # Jetzt liegt das Limit um den gedeckelten Puffer darueber und bleibt
    # zugleich unter der Slippage-Grenze ueber dem besten Brief.
    assert body["px"] == "60100.1"
    assert 60010.0 < float(body["px"]) <= 60010.0 * (1.0 + 0.006)
    assert "tgtCcy" not in body
    assert body["sz"] == "0.05"
    assert body["tdMode"] == "cash"
    assert body["clOrdId"]
    assert ergebnis.gross_filled_quantity == pytest.approx(0.05)
    assert ergebnis.filled_quantity == pytest.approx(0.04995)
    assert ergebnis.fill_ids == [f"okx:{broker.account_fingerprint()}:BTC-EUR:1:f1"]
    assert ergebnis.paper is True


def test_kauf_unter_mindestgroesse_wird_abgelehnt():
    broker, _ = _broker_mit_fake()

    class Instrument:
        name = "BTC"
        asset_type = "crypto"

    with pytest.raises(BrokerFehler) as info:
        broker.kaufe_mit_absicherung(Instrument(), 0.000001, 60000, 57000, 66000)
    assert "Mindestgroesse" in str(info.value)


def test_schutzorder_ist_oco_mit_stop_und_ziel():
    broker, session = _broker_mit_fake()
    session.antworte("/trade/order-algo", {"code": "0", "data": [{"algoId": "a1"}]})
    meta = broker.client._instruments["BTC-EUR"]
    snapshots = iter([[], [{
        "algoId": "a1", "instId": "BTC-EUR", "sz": "0.05",
        "slTriggerPx": "57000", "tpTriggerPx": "66000",
    }]])
    broker.offene_schutzorders = lambda *_a, **_k: next(snapshots, [])
    ergebnis = broker._setze_schutz(meta, 0.05, 57000, 66000)
    assert ergebnis["stop"] and ergebnis["take"]
    algo = next(a for a in session.aufrufe if "/trade/order-algo" in a["url"])
    body = json.loads(algo["data"])
    assert body["ordType"] == "oco"
    assert body["side"] == "sell"
    assert body["slTriggerPx"] == "57000"
    assert body["tpTriggerPx"] == "66000"
    assert body["slOrdPx"] == "56430"
    assert body["tpOrdPx"] == "66000"


def test_okx_kann_echten_broker_stop_fuer_krypto():
    """Der zentrale Vorteil gegenueber der eToro-Kryptoanbindung."""
    broker, _ = _broker_mit_fake()
    assert broker.unterstuetzt_krypto_stop() is True


def test_paper_und_live_nutzen_getrennte_schluessel(monkeypatch):
    import config
    monkeypatch.setattr(config, "OKX_DEMO_API_KEY", "DEMO", raising=False)
    monkeypatch.setattr(config, "OKX_DEMO_API_SECRET", "DEMOS", raising=False)
    monkeypatch.setattr(config, "OKX_DEMO_API_PASSPHRASE", "DEMOP", raising=False)
    monkeypatch.setattr(config, "OKX_API_KEY", "LIVE", raising=False)
    monkeypatch.setattr(config, "OKX_API_SECRET", "LIVES", raising=False)
    monkeypatch.setattr(config, "OKX_API_PASSPHRASE", "LIVEP", raising=False)

    demo = OKXBroker(demo=True)
    live = OKXBroker(demo=False)
    assert demo.client._key == "DEMO"
    assert live.client._key == "LIVE"
    assert demo.ist_paper() and not live.ist_paper()


def test_verkauf_storniert_zuerst_die_schutzorder():
    """Sonst blockiert die Algo-Order das Guthaben und der Verkauf scheitert."""
    broker, session = _broker_mit_fake()
    session.antworte("/trade/orders-pending", {"code": "0", "data": []})
    session.antworte("/trade/orders-algo-pending", {"code": "0", "data": [
        {"algoId": "a1", "instId": "BTC-EUR", "sz": "0.05"}]})
    session.antworte("/trade/cancel-algos", {"code": "0", "data": [{"algoId": "a1"}]})
    session.antworte("/account/balance", {"code": "0", "data": [
        {"totalEq": "1000", "details": [{"ccy": "BTC", "availBal": "0.05", "cashBal": "0.05"}]}]})
    session.antworte("/trade/order", {"code": "0", "data": [{"ordId": "9"}]})
    session.antworte("/trade/order?", {"code": "0", "data": [
        {"ordId": "9", "state": "filled", "accFillSz": "0.05", "avgPx": "59900"}]})
    session.antworte("/trade/fills?", {"code": "0", "data": [
        {"tradeId": "s1", "ordId": "9", "instId": "BTC-EUR",
         "side": "sell", "fillSz": "0.05", "fillPx": "59900", "fee": "0",
         "feeCcy": "EUR", "ts": "1"}]})
    session.antworte("/market/books", {"code": "0", "data": [{
        "ts": str(int(time.time() * 1000)),
        "bids": [["59900", "1", "0", "1"]],
        "asks": [["59910", "1", "0", "1"]]}]})

    class Instrument:
        name = "BTC"
        asset_type = "crypto"

    # Der neue Verkaufspfad wartet nach dem Storno auf eine frische,
    # tatsaechlich leere Schutzorderliste. Der einfache Fake-Transport kann
    # Antworten nicht sequenzieren, deshalb wird genau dieser Brokerzustand
    # hier explizit nachgebildet: beim Storno vorhanden, danach verschwunden.
    schutz_snapshots = iter([[{"algoId": "a1", "instId": "BTC-EUR", "sz": "0.05"}], []])
    broker.offene_schutzorders = lambda *_a, **_k: next(schutz_snapshots, [])

    ergebnis = broker.schliesse_position(
        Instrument(), 0.05, 60000, protection_algo_id="a1")
    assert ergebnis.filled_quantity == pytest.approx(0.05)
    reihenfolge = [a["url"] for a in session.aufrufe]
    storno = next(i for i, u in enumerate(reihenfolge) if "cancel-algos" in u)
    verkauf = next(i for i, u in enumerate(reihenfolge)
                   if "/trade/order" in u and "algo" not in u and "pending" not in u)
    assert storno < verkauf


def test_reconcile_erkennt_fehlenden_schutz():
    broker, session = _broker_mit_fake()
    session.antworte("/trade/orders-algo-pending", {"code": "0", "data": []})
    session.antworte("/trade/order-algo", {"code": "0", "data": [{"algoId": "neu"}]})

    class Instrument:
        name = "BTC"
        asset_type = "crypto"

    snapshots = iter([[], [], [{
        "algoId": "neu", "instId": "BTC-EUR", "sz": "0.05",
        "slTriggerPx": "57000", "tpTriggerPx": "66000",
    }]])
    broker.offene_schutzorders = lambda *_a, **_k: next(snapshots, [])
    ergebnis = broker.reconcile_position_protection(
        Instrument(), 0.05, 57000, 66000,
        protection_client_id="test-protection")
    assert ergebnis["checked"] and ergebnis["changed"] and ergebnis["protection_confirmed"]


def test_instrument_ausserhalb_spot_wird_abgelehnt():
    broker, _ = _broker_mit_fake()

    class Aktie:
        name = "AAPL"
        asset_type = "stock"

    ok, grund = broker.instrument_handelbar(Aktie())
    assert not ok and "Krypto-Spot" in grund
