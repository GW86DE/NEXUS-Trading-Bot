"""9.5.4 -- Schutzorder im Konto ohne Position im Buch.

Befund vom 02.09.2026: im OKX-Konto lag eine lebende OCO-Schutzorder

    algoId 3873284120214450177, ETH-EUR, sz 1.043377,
    SL 1894.9, TP 2205.1  (rund 2530 EUR)

zu der NEXUS keine Position fuehrte. Sie war an keiner Stelle sichtbar:
``verwaiste_orders_aufraeumen`` sieht nur Schutzorders OHNE Guthaben an, und
hier lag Guthaben vor.

Richtig ist weder Uebernehmen (das waere ein Bestand ohne Eigentumsbeweis --
der Fehler vom 25.08.2026 mit 0,94 fremden BTC) noch Stornieren (das liesse
einen echten Bestand ungeschuetzt). Richtig ist: sichtbar machen und den
Wiedereinstieg in genau diesen Wert sperren.
"""
from __future__ import annotations

import pytest

# Belegt aus dem Gesundheitscheck vom 02.09.2026: algoId, instId, sz,
# slTriggerPx, tpTriggerPx, state. Die algoClOrdId war in der Ausgabe NICHT
# enthalten -- sie ist hier bewusst weggelassen statt geraten. Aus ihr laesst
# sich sonst faelschlich schliessen, ob die Order von NEXUS stammt.
ETH_ALGO = {"algoId": "3873284120214450177",
            "instId": "ETH-EUR", "side": "sell", "sz": "1.043377",
            "slTriggerPx": "1894.9", "tpTriggerPx": "2205.1", "state": "live"}
LINK_ALGO = {"algoId": "3879429521221038081", "algoClOrdId": "TBP9link",
             "instId": "LINK-USDC", "side": "sell", "sz": "9.08808",
             "slTriggerPx": "9.7", "tpTriggerPx": "11.8", "state": "live"}


class AlgoBroker:
    name = "OKX"
    quote_ccy = "USDC"

    def __init__(self, algos, fehler=""):
        from broker.okx import OKXBroker, OKXInstrument
        meta = OKXInstrument("LINK-USDC", "LINK", "USDC", "live",
                             "0.001", "0.0001", "0.01")
        self._algos = list(algos)
        self._fehler = fehler
        aussen = self

        class Client:
            hat_zugangsdaten = True
            def instrument(self, _i): return meta
            def pending_algo_orders(self, inst_id="", ord_type="oco"):
                from broker.base import BrokerFehler
                if aussen._fehler:
                    raise BrokerFehler(aussen._fehler)
                return aussen._algos if ord_type == "oco" else []

        self.client = Client()
        self.alle_schutzorders = lambda: OKXBroker.alle_schutzorders(self)
        self.ALGO_ARTEN = OKXBroker.ALGO_ARTEN

    def is_connected(self): return True
    def positionen(self):
        class B:
            symbol = "LINK"; quantity = 9.08808
            market_price = 10.78; currency = "USDC"
        return [B()]
    def latest_bid_ask(self, _i): return {"bid": 10.7, "ask": 10.8, "last": 10.75}
    def reconcile_position_protection(self, *_a, **_k):
        return {"checked": True, "protection_confirmed": True,
                "algo_id": "3879429521221038081", "detail": ""}


@pytest.fixture
def motor(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADINGBOT_TEST_STATE_DIR", str(tmp_path))
    import crypto_engine as ce
    monkeypatch.setattr(ce, "_state_root", lambda: tmp_path)
    from risk_pots import RiskPotManager
    from universe.manager import UniverseManager
    from universe.modelle import UniverseZustand

    gemeldet: list[str] = []

    def bauen(algos, fehler=""):
        broker = AlgoBroker(algos, fehler=fehler)

        class Hub:
            def broker(self, name): return broker if name == "okx" else None
            def verbinde(self, _n): return True
            def zustaende(self): return {}

        engine = ce.CryptoEngine(
            hub=Hub(), risiko=RiskPotManager(["etoro", "okx"]),
            universum=UniverseManager(UniverseZustand(tmp_path / "u.json")),
            melder=lambda text, **k: gemeldet.append(text))
        engine.buch = ce.KryptoPositionsbuch(tmp_path / "p.json")
        engine.buch.setze(ce.KryptoPosition(
            symbol="LINK", inst_id="LINK-USDC", menge=9.08808, einstieg=10.5,
            stop=9.7, take_profit=11.8, broker_schutz=True,
            protection_status="ACTIVE",
            protection_algo_id="3879429521221038081",
            order_id="3877307136791490561", referenz="c-link",
            client_order_id="c-link", order_tag="NEXUS",
            fill_ids=["f-link"], ownership_verified=True))
        return ce, engine, broker

    return bauen, gemeldet


def test_verwaiste_schutzorder_wird_erkannt(motor):
    bauen, _ = motor
    _, engine, _ = bauen([ETH_ALGO, LINK_ALGO])

    bericht = engine.pruefe_positionen()

    verwaist = bericht["verwaiste_schutzorders"]
    assert [r["symbol"] for r in verwaist] == ["ETH"], (
        "LINK hat eine Position im Buch und ist nicht verwaist; ETH schon")
    eintrag = verwaist[0]
    assert eintrag["algo_id"] == "3873284120214450177"
    assert eintrag["menge"] == pytest.approx(1.043377)
    assert eintrag["stop"] == pytest.approx(1894.9)
    assert eintrag["take_profit"] == pytest.approx(2205.1)


def test_verwaiste_schutzorder_wird_gemeldet(motor):
    bauen, gemeldet = motor
    _, engine, _ = bauen([ETH_ALGO, LINK_ALGO])

    engine.pruefe_positionen()

    text = "\n".join(gemeldet)
    assert "3873284120214450177" in text and "ETH" in text
    assert "uebernimmt diesen Bestand NICHT" in text, (
        "Die Meldung muss klarstellen, dass nichts uebernommen wird")


def test_meldung_wiederholt_sich_nicht_bei_unveraenderter_lage(motor):
    """Eine Dauerlage darf nicht bei jedem Takt melden."""
    bauen, gemeldet = motor
    _, engine, _ = bauen([ETH_ALGO, LINK_ALGO])

    engine.pruefe_positionen()
    anzahl = len([t for t in gemeldet if "3873284120214450177" in t])
    engine.pruefe_positionen()
    engine.pruefe_positionen()

    assert len([t for t in gemeldet if "3873284120214450177" in t]) == anzahl == 1


def test_wiedereinstieg_in_das_betroffene_symbol_ist_gesperrt(motor):
    """Ein Kauf wuerde gegen eine fremde, scharfe SL/TP-Order laufen."""
    bauen, _ = motor
    _, engine, _ = bauen([ETH_ALGO, LINK_ALGO])
    engine.pruefe_positionen()

    gesperrt = {str(r.get("symbol") or "").upper()
                for r in engine._verwaiste_schutz}
    assert "ETH" in gesperrt
    assert "LINK" not in gesperrt, (
        "Gesperrt wird gezielt das betroffene Symbol, nicht das Universum")


def test_nichts_wird_storniert_oder_uebernommen(motor):
    """Der Bestand gehoert dem Bot nicht -- er fasst ihn nicht an."""
    bauen, _ = motor
    ce, engine, broker = bauen([ETH_ALGO, LINK_ALGO])

    engine.pruefe_positionen()

    assert engine.buch.hole("ETH") is None, (
        "Ein Bestand ohne Eigentumsbeweis darf nie ins Buch wandern")
    assert not hasattr(broker, "_stornierungen"), "Es darf nicht storniert werden"


def test_unlesbare_schutzorders_sperren_statt_zu_schweigen(motor):
    """Nicht abrufbar heisst nicht 'keine'.

    9.5.5: Bis 9.5.4 wurde dafuer eine Platzhalterzeile mit dem Symbol
    "<SCHUTZORDERS-UNLESBAR>" erzeugt. Der Kaufpfad filtert Kandidaten aber
    nach Symbolnamen -- gesperrt wurde damit nur ein Symbol, das es gar nicht
    gibt. Jetzt gibt es eine echte Sperrflagge.
    """
    bauen, _ = motor
    _, engine, _ = bauen([], fehler="OKX 50001 Service temporarily unavailable")

    engine._verwaiste_schutzorders()

    assert engine._schutzorders_unlesbar, (
        "Eine nicht abrufbare Liste darf nicht als leere Liste durchgehen")
    assert "50001" in engine._schutzorders_unlesbar


def test_verwaiste_order_auf_anderem_quote_markt_wird_erkannt(motor):
    """Der Kern des ETH-Falls: gleiche Basiswaehrung, anderer Markt.

    Bis 9.5.4 wurde nur die Basiswaehrung verglichen. Eine gefuehrte Position
    ETH-USDC hat damit die verwaiste Schutzorder auf ETH-EUR verdeckt -- genau
    die Konstellation aus dem Quotewechsel EUR nach USDC.
    """
    bauen, _ = motor
    ce, engine, _ = bauen([ETH_ALGO, LINK_ALGO])
    engine.buch.setze(ce.KryptoPosition(
        symbol="ETH", inst_id="ETH-USDC", menge=0.04, einstieg=2409.0,
        stop=2168.1, take_profit=2522.9, broker_schutz=True,
        protection_status="ACTIVE", protection_algo_id="algo-eth-usdc",
        order_id="o-eth", referenz="c-eth", client_order_id="c-eth",
        order_tag="NEXUS", fill_ids=["f-eth"], ownership_verified=True))

    verwaist = engine._verwaiste_schutzorders()

    instrumente = {r["instrument"] for r in verwaist}
    assert "ETH-EUR" in instrumente, (
        "Die ETH-EUR-Waise wurde von der ETH-USDC-Position verdeckt")
