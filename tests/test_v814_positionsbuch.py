"""Positionsbuch gegen Fremdbestaende und Teilausfuehrungen (v8.1.4).

Diese Tests halten den Vorfall vom 25.08.2026 fest:

* Der Bot verkaufte 0,94141 BTC, obwohl seine Position 0,06305312 BTC war --
  er hatte den gesamten Kontostand als seine Position uebernommen.
* Ein Teilverkauf von 1,66715 aus 15,85775 SOL loeschte die ganze Position
  aus dem Buch; 14,19 SOL blieben ohne Stop im Konto liegen.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("TRADINGBOT_TEST_STATE_DIR", "/tmp/tradingbot_tests")
Path(os.environ["TRADINGBOT_TEST_STATE_DIR"]).mkdir(parents=True, exist_ok=True)

WURZEL = Path(__file__).resolve().parent.parent


class FakeMeta:
    lot_size = "0.00001"
    min_size = "0.0001"
    base_ccy = "BTC"
    quote_ccy = "EUR"
    inst_id = "BTC-EUR"

    @property
    def ist_live(self):
        return True


class FakeClient:
    def instrument(self, inst_id):
        return FakeMeta()

    def balances(self):
        return {}


class FakeBestand:
    def __init__(self, symbol, menge, preis):
        self.symbol = symbol
        self.quantity = menge
        self.market_price = preis
        self.currency = "EUR"


class FakeErgebnis:
    def __init__(self, menge, preis):
        self.filled_quantity = menge
        self.gross_filled_quantity = menge
        self.avg_fill_price = preis
        self.reference_id = "NEXUS-TEST-EXIT"
        self.client_order_id = "NEXUS-TEST-EXIT"
        self.status = "filled"
        self.hinweis = ""
        self.paper = True
        self.order_ids = ["1"]
        self.fill_ids = ["okx:test:BTC-EUR:1:sell-1"]
        self.fills = [{"tradeId": "sell-1", "ordId": "1",
                       "instId": "BTC-EUR", "fillSz": str(menge),
                       "fillPx": str(preis), "fee": "0", "feeCcy": "EUR"}]
        self.fill_evidence_complete = True
        self.terminal = True
        self.raw_status = "filled"
        self.fees_quote = 0.0
        self.trade_quote_ccy = "EUR"
        self.stop_order_platziert = True


class FakeBroker:
    name = "OKX"
    quote_ccy = "EUR"

    def __init__(self, bestaende, fill=None):
        self.client = FakeClient()
        self._bestaende = bestaende
        self.verkaeufe = []
        self._fill = fill
        self.schutz_aufrufe = []

    def is_connected(self):
        return True

    def positionen(self):
        return list(self._bestaende)

    def latest_bid_ask(self, instrument):
        return {"bid": 100.0, "ask": 100.2, "last": 100.1}

    def schliesse_position(self, instrument, menge, preis, **_identity):
        self.verkaeufe.append({"menge": menge, "preis": preis})
        gefuellt = self._fill if self._fill is not None else menge
        return FakeErgebnis(gefuellt, preis)

    def reconcile_position_protection(self, instrument, menge, stop, ziel):
        self.schutz_aufrufe.append({"menge": menge, "stop": stop})
        return {"protection_confirmed": True}


@pytest.fixture
def motor(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADINGBOT_TEST_STATE_DIR", str(tmp_path))
    import config
    monkeypatch.setattr(config, "OKX_MIN_POSITION_VALUE", 15.0, raising=False)

    import crypto_engine as ce
    monkeypatch.setattr(ce, "_state_root", lambda: tmp_path)

    from risk_pots import RiskPotManager
    from universe.manager import UniverseManager
    from universe.modelle import UniverseZustand

    gemeldet: list[str] = []

    def bauen(bestaende, fill=None):
        broker = FakeBroker(bestaende, fill=fill)

        class Hub:
            def broker(self, name):
                return broker if name == "okx" else None

            def verbinde(self, name):
                return True

            def zustaende(self):
                return {}

        engine = ce.CryptoEngine(
            hub=Hub(), risiko=RiskPotManager(["etoro", "okx"]),
            universum=UniverseManager(UniverseZustand(tmp_path / "u.json")),
            melder=lambda text, **k: gemeldet.append(text))
        engine.buch = ce.KryptoPositionsbuch(tmp_path / "p.json")
        return engine, broker

    return bauen, gemeldet


def position(ce, symbol="BTC", menge=0.06305312, einstieg=69237.4, stop=68834.6):
    return ce.KryptoPosition(symbol=symbol, inst_id=f"{symbol}-EUR", menge=menge,
                             einstieg=einstieg, stop=stop, take_profit=70028.9,
                             broker_schutz=True, order_id=f"order-{symbol}",
                             referenz=f"client-{symbol}",
                             client_order_id=f"client-{symbol}",
                             order_tag="NEXUS", fill_ids=[f"fill-{symbol}"],
                             ownership_verified=True)


# ---------------------------------------------------------------------------
# Der Vorfall: Fremdbestand wurde mitverkauft
# ---------------------------------------------------------------------------
def test_mehr_im_konto_als_im_buch_wird_nicht_uebernommen(motor):
    """Der Kernfehler vom 25.08.2026, exakt mit den echten Zahlen."""
    import crypto_engine as ce
    bauen, gemeldet = motor
    engine, broker = bauen([FakeBestand("BTC", 1.00446312, 69100.0)])
    engine.buch.setze(position(ce))

    engine.pruefe_positionen()

    buch = engine.buch.hole("BTC")
    assert buch.menge == pytest.approx(0.06305312), \
        "Der Kontostand darf NICHT als Position uebernommen werden"
    text = "\n".join(gemeldet)
    assert "mehr im Konto" not in text
    assert "gesperrt" not in text


def test_fremdbestand_wird_beim_stop_nicht_mitverkauft(motor):
    """Selbst wenn der Stop feuert: verkauft wird nur die eigene Menge."""
    import crypto_engine as ce
    bauen, _ = motor
    # Kurs unter dem Stop, Konto enthaelt zusaetzlich 0,94141 BTC Fremdbestand
    engine, broker = bauen([FakeBestand("BTC", 1.00446312, 68000.0)])
    engine.buch.setze(position(ce))

    engine.pruefe_positionen()

    assert len(broker.verkaeufe) == 1
    assert broker.verkaeufe[0]["menge"] == pytest.approx(0.06305312), \
        f"verkauft wurden {broker.verkaeufe[0]['menge']}, erlaubt war 0.06305312"


def test_weniger_im_konto_verlangt_fill_beleg_statt_mengenkorrektur(motor):
    import crypto_engine as ce
    bauen, gemeldet = motor
    engine, broker = bauen([FakeBestand("BTC", 0.03, 69100.0)])
    engine.buch.setze(position(ce))

    engine.pruefe_positionen()

    assert engine.buch.hole("BTC").menge == pytest.approx(0.06305312)
    assert engine.buch.hole("BTC").exit_state == 'ACCOUNTING_PENDING'
    assert any("Mengenabgang" in m for m in gemeldet)


def test_ein_einzelner_nullstand_schliesst_noch_nichts(motor):
    """Ein unvollstaendiger Guthaben-Schnappschuss darf keine Position abrechnen."""
    import crypto_engine as ce
    bauen, gemeldet = motor
    engine, broker = bauen([])
    engine.buch.setze(position(ce))

    engine.pruefe_positionen()

    assert engine.buch.hole("BTC") is not None, \
        "Nach einem einzigen Nullstand darf die Position nicht verschwinden"
    assert any("erneut geprueft" in m for m in gemeldet)


def test_kein_guthaben_ohne_ledgeranker_entfernt_keine_position(motor):
    """Bis 8.1.3 verschwand die Position still -- ohne Ledger, ohne Ergebnis."""
    import crypto_engine as ce
    bauen, gemeldet = motor
    engine, broker = bauen([])
    engine.buch.setze(position(ce))

    engine.pruefe_positionen()      # erster Nullstand: nur merken
    p = engine.buch.hole("BTC")
    p.first_missing_at = (datetime.now(timezone.utc) - timedelta(seconds=31)).isoformat()
    engine.buch.setze(p)
    engine.pruefe_positionen()      # zweiter: ohne Ledgeranker bleibt sie gesperrt

    assert engine.buch.hole("BTC") is not None
    text = "\n".join(gemeldet)
    assert "Position bleibt vorerst im Buch" in text
    assert "Schutzorder ausgeloest" not in text
    from okx_accounting import status
    zustand = status("test", "DEMO")
    # 10.7.0: ohne Ledgeranker sperrt der Vorfall BTC, nicht die Domaene.
    assert 'BTC' in zustand['blocked_symbols'] and zustand['complete']


def test_fremde_position_wird_bei_nullstand_nicht_abgerechnet(motor):
    """Eine FREMDE Position darf der Bot nicht abrechnen.

    PRAEZISIERT IN v9.2. Der Test hat vorher eine Position mit VOLLSTAENDIGER
    Broker-ID-Kette gebaut und sie nur "Fremdbestand" genannt -- geprueft wurde
    damit nicht, was der Name sagt, sondern nur, dass eine pausierte Position
    unangetastet bleibt. Genau diese Vermischung war die Ursache des Vorfalls
    vom 31.08.2026: der Verwaltungsmodus entschied ueber das Eigentum.

    Ab v9.2 entscheidet die Broker-ID-Kette. Der Test baut deshalb eine
    wirklich fremde Position -- ohne Nachweis.
    """
    import crypto_engine as ce
    bauen, gemeldet = motor
    engine, broker = bauen([])
    p = position(ce)
    p.ownership_verified = False
    p.fill_ids = []
    p.pausiere("Fremdbestand")
    assert not p.ownership_chain_complete
    engine.buch.setze(p)

    engine.pruefe_positionen()
    engine.pruefe_positionen()

    assert engine.buch.hole("BTC") is not None, \
        "Eine fremde Position darf der Bot nicht abrechnen"
    assert not gemeldet, "Konto-Assets sollen keine wiederkehrende Warnung erzeugen"


def test_bewiesene_aber_pausierte_position_bleibt_ueberwacht(motor):
    """NEU IN v9.2 -- die Gegenprobe zum Vorfall vom 31.08.2026.

    Eine Position mit vollstaendiger Broker-ID-Kette bleibt eine Botposition,
    auch wenn ihre Strategie pausiert ist. Bis 9.1 wurde sie komplett
    uebersprungen: kein Mengenabgleich, keine Erkennung externer Verkaeufe,
    keine Pruefung des Broker-Schutzes. Echtes Geld, das niemand mehr ansieht.
    """
    import crypto_engine as ce
    bauen, _gemeldet = motor
    engine, broker = bauen([])
    p = position(ce)
    p.pausiere("Strategie nicht reproduzierbar", status="STRATEGY_MIGRATION_REQUIRED")

    assert p.ownership_chain_complete, "Die ID-Kette bleibt vollstaendig"
    assert p.safety_monitoring_enabled, "Ueberwachung laeuft weiter"
    assert p.blocks_reentry, "Der Wiedereinstieg bleibt gesperrt"
    assert not p.darf_schutz_ausfuehren, "Verkauft wird sie trotzdem nicht"
    assert not p.strategy_execution_enabled

    engine.buch.setze(p)
    bericht = engine.pruefe_positionen()
    assert "BTC" not in (bericht.get("nur_beobachtet") or []), \
        "Eine bewiesene Botposition darf nicht als reines Konto-Asset gelten"


def test_nan_kontostand_wird_uebersprungen(motor):
    """NaN laeuft durch jede Fallunterscheidung hindurch."""
    import crypto_engine as ce
    bauen, _ = motor
    engine, broker = bauen([FakeBestand("BTC", float("nan"), 69100.0)])
    engine.buch.setze(position(ce))

    engine.pruefe_positionen()

    buch = engine.buch.hole("BTC")
    assert buch is not None and buch.menge == pytest.approx(0.06305312), \
        "Ein unbrauchbarer Kontostand darf das Buch nicht veraendern"
    assert broker.verkaeufe == []


# ---------------------------------------------------------------------------
# Teilausfuehrung
# ---------------------------------------------------------------------------
def test_teilverkauf_laesst_den_rest_im_buch(motor):
    """SOL 11:54 -- 1,66715 von 15,85775 verkauft, 14,19 blieben liegen."""
    import crypto_engine as ce
    bauen, gemeldet = motor
    engine, broker = bauen([FakeBestand("SOL", 15.85775, 95.0)], fill=1.66715)
    engine.buch.setze(position(ce, "SOL", menge=15.85775, einstieg=99.95, stop=99.38))

    engine.pruefe_positionen()

    rest = engine.buch.hole("SOL")
    assert rest is not None, "Der Rest darf nicht aus dem Buch verschwinden"
    assert rest.menge == pytest.approx(15.85775 - 1.66715, abs=1e-6)
    text = "\n".join(gemeldet)
    assert "1.66715 von 15.85775 ausgefuehrt" in text, \
        "Die Menge muss exakt stehen, nicht auf 6 Stellen gerundet"
    assert "bleiben gefuehrt und geschuetzt" in text


def test_teilverkauf_zieht_den_schutz_fuer_den_rest_nach(motor):
    """Die alte Schutzorder galt fuer eine Menge, die es nicht mehr gibt."""
    import crypto_engine as ce
    bauen, _ = motor
    engine, broker = bauen([FakeBestand("SOL", 15.85775, 95.0)], fill=1.66715)
    engine.buch.setze(position(ce, "SOL", menge=15.85775, einstieg=99.95, stop=99.38))

    engine.pruefe_positionen()

    assert broker.schutz_aufrufe, "Fuer die Restmenge muss der Schutz neu gesetzt werden"
    assert broker.schutz_aufrufe[-1]["menge"] == pytest.approx(14.1906, abs=1e-3)


def test_zu_kleiner_rest_wird_nicht_weiter_gefuehrt(motor):
    """Unter der Mindestordergroesse kann OKX gar nicht mehr verkaufen."""
    import crypto_engine as ce
    bauen, gemeldet = motor
    engine, broker = bauen([FakeBestand("BTC", 0.06305312, 68000.0)], fill=0.06305)
    engine.buch.setze(position(ce))

    engine.pruefe_positionen()

    assert engine.buch.hole("BTC") is None
    assert any("unter der Mindestgroesse" in m for m in gemeldet)


# ---------------------------------------------------------------------------
# Herkunft und Verwaltung
# ---------------------------------------------------------------------------
def test_pausierte_position_wird_nie_verkauft(motor):
    import crypto_engine as ce
    bauen, _ = motor
    engine, broker = bauen([FakeBestand("BTC", 0.06305312, 60000.0)])
    p = position(ce)
    p.pausiere("Testgrund")
    engine.buch.setze(p)

    engine.pruefe_positionen()

    assert broker.verkaeufe == [], "Eine pausierte Position darf der Bot nicht schliessen"
    assert engine.buch.hole("BTC") is not None


def test_fremde_position_wird_nie_verkauft(motor):
    import crypto_engine as ce
    bauen, _ = motor
    engine, broker = bauen([FakeBestand("BTC", 0.06305312, 60000.0)])
    p = position(ce)
    p.herkunft = ce.HERKUNFT_BROKER
    engine.buch.setze(p)

    engine.pruefe_positionen()

    assert broker.verkaeufe == []


def test_neue_position_ohne_brokerbeweis_ist_nicht_automatisch_verwaltbar():
    import crypto_engine as ce
    p = ce.KryptoPosition(symbol="BTC", inst_id="BTC-EUR", menge=1.0,
                          einstieg=100.0, stop=95.0, take_profit=110.0)
    assert p.herkunft == ce.HERKUNFT_BOT
    assert p.verwaltung == ce.VERWALTUNG_AUTO
    assert p.darf_automatisch_verkaufen is False


# ---------------------------------------------------------------------------
# Quellcode-Wachen gegen Rueckfall
# ---------------------------------------------------------------------------
def test_kontostand_wird_nirgends_mehr_als_position_uebernommen():
    """Geprueft wird der CODE, nicht der Kommentar -- die Erklaerung im
    Quelltext darf die alte Zeile ausdruecklich zitieren."""
    import ast
    baum = ast.parse((WURZEL / "crypto_engine.py").read_text(encoding="utf-8"))
    for knoten in ast.walk(baum):
        if isinstance(knoten, (ast.Module, ast.ClassDef, ast.FunctionDef,
                               ast.AsyncFunctionDef)) and ast.get_docstring(knoten):
            knoten.body = knoten.body[1:]
    code = ast.unparse(baum)
    assert "position.menge = bestand.quantity" not in code, \
        "Genau diese Zeile hat am 25.08.2026 einen Fremdbestand verkauft"


def test_verkauf_ist_auf_die_buchmenge_gedeckelt():
    quelle = (WURZEL / "crypto_engine.py").read_text(encoding="utf-8")
    assert "verkaufsmenge = float(position.menge)" in quelle
    assert "menge > verkaufsmenge" in quelle, "Der Deckel gegen zu grosse Fills fehlt"
