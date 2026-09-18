"""9.5.4 -- Staubrest nach einem Komplettverkauf schliesst die Position.

Befund vom 02.09.2026: BNB wurde um 16:50:24 UTC vollstaendig verkauft
(0,15443 von 0,15443757 zu durchschnittlich 684,25; Einstieg 694,60;
Ergebnis -1,60 USDC). Im Konto blieben 0,00000757 BNB liegen.

Dieser Rest ist rechnerisch positiv. Der Abgleich zwischen Konto und Buch
hat bis 9.5.3 nur auf "Guthaben > 0" geprueft und die Position deshalb nicht
geschlossen, sondern auf den Staub heruntergeschrieben. Dort blieb sie
haengen: unter der OKX-Mindestmenge ist sie nie wieder verkaeuflich, sie
belegt einen Positionsplatz und blockiert den Wiedereinstieg.

Der Verkauf lief ueber die brokerseitige Schutzorder und damit nie durch
``_schliesse`` -- die dort vorhandene Staubgrenze griff also nicht.
"""
from __future__ import annotations

import pytest


class Bestand:
    def __init__(self, symbol, menge, preis, waehrung="USDC"):
        self.symbol = symbol
        self.quantity = menge
        self.market_price = preis
        self.currency = waehrung


class KontoBroker:
    name = "OKX"
    quote_ccy = "USDC"

    def __init__(self, bestaende):
        from broker.okx import OKXInstrument
        self._bestaende = list(bestaende)
        # Echte OKX-Handelsregeln fuer BNB-USDC: Mindestmenge 0,001.
        meta = OKXInstrument("BNB-USDC", "BNB", "USDC", "live",
                             "0.1", "0.00001", "0.001")

        class Client:
            hat_zugangsdaten = True
            def instrument(self, _i): return meta
        self.client = Client()

    def is_connected(self): return True
    def positionen(self): return list(self._bestaende)
    def latest_bid_ask(self, _i): return {"bid": 684.2, "ask": 684.3, "last": 684.25}
    def reconcile_position_protection(self, *_a, **_k):
        return {"checked": True, "protection_confirmed": True,
                "algo_id": "algo-bnb", "detail": ""}


@pytest.fixture
def motor(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADINGBOT_TEST_STATE_DIR", str(tmp_path))
    import crypto_engine as ce
    monkeypatch.setattr(ce, "_state_root", lambda: tmp_path)
    from risk_pots import RiskPotManager
    from universe.manager import UniverseManager
    from universe.modelle import UniverseZustand

    gemeldet: list[str] = []

    def bauen(bestaende):
        broker = KontoBroker(bestaende)

        class Hub:
            def broker(self, name): return broker if name == "okx" else None
            def verbinde(self, _n): return True
            def zustaende(self): return {}

        engine = ce.CryptoEngine(
            hub=Hub(), risiko=RiskPotManager(["etoro", "okx"]),
            universum=UniverseManager(UniverseZustand(tmp_path / "u.json")),
            melder=lambda text, **k: gemeldet.append(text))
        engine.buch = ce.KryptoPositionsbuch(tmp_path / "p.json")
        return ce, engine, broker

    return bauen, gemeldet


def bnb_position(ce):
    """Die echte BNB-Position, Zahlen aus tests/fixtures/echt_runtime_status_okx.json."""
    return ce.KryptoPosition(
        symbol="BNB", inst_id="BNB-USDC", menge=0.15443757,
        einstieg=694.6, stop=622.53, take_profit=724.4212624184647,
        broker_schutz=True, protection_status="ACTIVE",
        protection_algo_id="algo-bnb",
        order_id="3877276078171697153", referenz="N93764847086281095168",
        client_order_id="N93764847086281095168", order_tag="NEXUS",
        fill_ids=["fill-bnb"], ownership_verified=True,
        decision_id=3764847086281095168)


STAUB = 0.00000757


def test_staub_schliesst_die_position_nicht_sofort(motor):
    """Ein einzelner Schnappschuss darf nie eine Position schliessen."""
    bauen, gemeldet = motor
    import crypto_engine as ce
    ce_mod, engine, _ = bauen([Bestand("BNB", STAUB, 684.25)])
    engine.buch.setze(bnb_position(ce))

    engine.pruefe_positionen()

    assert engine.buch.hole("BNB") is not None, (
        "Erst nach zwei Schnappschuessen in Folge darf geschlossen werden")
    text = "\n".join(gemeldet)
    assert "nicht handelbarer Rest" in text, (
        f"Die Lage muss benannt werden, gemeldet wurde: {text!r}")


def test_staub_beobachtung_ohne_verkaufsbeleg_behaelt_die_position(motor, monkeypatch):
    """Zwei kleine Kontostaende ersetzen keine belegte Verkaufs-/Eigentumskette."""
    bauen, gemeldet = motor
    import crypto_engine as ce
    ce_mod, engine, _ = bauen([Bestand("BNB", STAUB, 684.25)])
    import config
    monkeypatch.setattr(config, "OKX_POSITION_MISSING_CONFIRM_SECONDS", 0.0,
                        raising=False)
    monkeypatch.setattr(engine.cfg, "OKX_POSITION_MISSING_CONFIRM_SECONDS", 0.0,
                        raising=False)
    engine.buch.setze(bnb_position(ce))

    engine.pruefe_positionen()   # erster Schnappschuss
    engine.pruefe_positionen()   # zweiter Schnappschuss -> bestaetigt

    position=engine.buch.hole("BNB")
    assert position is not None and position.menge == pytest.approx(.15443757)
    assert position.broker_state == "BROKER_STATE_UNKNOWN"
    import okx_accounting
    zustand = okx_accounting.status('any-demo-account','DEMO')
    # 10.7.0: der Buchungsvorfall sperrt BNB, nicht die Domaene.
    assert 'BNB' in zustand['blocked_symbols'] and zustand['complete']



def test_echte_position_ueberlebt_den_abgleich(motor):
    """Gegenprobe: eine handelbare Menge wird niemals als Staub geschlossen."""
    bauen, _ = motor
    import crypto_engine as ce
    ce_mod, engine, _ = bauen([Bestand("BNB", 0.15443757, 684.25)])
    engine.buch.setze(bnb_position(ce))

    engine.pruefe_positionen()
    engine.pruefe_positionen()

    buch = engine.buch.hole("BNB")
    assert buch is not None and buch.menge == pytest.approx(0.15443757)


def test_kleine_aber_handelbare_restmenge_bleibt_gefuehrt(motor):
    """Die Grenze ist die Handelbarkeit, nicht die Eroeffnungsschwelle.

    0,003 BNB sind rund 2 USD -- weit unter OKX_MIN_POSITION_VALUE (15), aber
    das Dreifache der Mindestmenge. Das ist verkaeuflich und bleibt gefuehrt.
    """
    bauen, _ = motor
    import crypto_engine as ce
    ce_mod, engine, _ = bauen([Bestand("BNB", 0.003, 684.25)])
    engine.buch.setze(bnb_position(ce))

    engine.pruefe_positionen()
    engine.pruefe_positionen()

    assert engine.buch.hole("BNB") is not None, (
        "Eine verkaeufliche Restmenge darf nicht als Staub verschwinden")
