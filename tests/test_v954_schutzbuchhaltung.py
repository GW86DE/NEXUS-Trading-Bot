"""9.5.4 -- Buchhaltung des Broker-Schutzes.

Befund vom 02.09.2026 (echte Daten, tests/fixtures/echt_runtime_status_okx.json):
BNB, LINK und XLM standen alle drei gleichzeitig auf

    broker_schutz = True,  protection_status = "PENDING",  protection_algo_id = ""

Das ist in sich widerspruechlich. Ohne algoId kann NEXUS die eigene
Schutzorder weder wiederfinden noch pruefen noch vor einem Verkauf
stornieren -- die Flagge behauptet Sicherheit, die nicht belegbar ist. Bei
XLM lag tatsaechlich gar keine Schutzorder im Konto (560,88 XLM ungesichert).

Drei Ursachen, die hier verhaltensmaessig abgesichert werden:
  1. Der Wiederaufsetzpfad warf die algoId weg.
  2. Der gesamte Schutzblock haing an einer erfolgreichen Ledger-Zeile.
  3. Der periodische Abgleich sah Positionen mit broker_schutz=True nie an.
"""
from __future__ import annotations

import json
import pathlib



WURZEL = pathlib.Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# Der Befund selbst -- an den echten Pi-Daten, nicht an erfundenen
# ---------------------------------------------------------------------------
def test_echter_befund_zeigt_den_widerspruch():
    """Belegt die Ausgangslage, damit der Test nicht von Annahmen lebt."""
    rohdaten = json.loads(
        (WURZEL / "tests" / "fixtures" / "echt_runtime_status_okx.json")
        .read_text(encoding="utf-8"))

    gefunden = []

    def sammeln(knoten):
        if isinstance(knoten, dict):
            if "broker_schutz" in knoten and "protection_status" in knoten:
                gefunden.append(knoten)
            for wert in knoten.values():
                sammeln(wert)
        elif isinstance(knoten, list):
            for wert in knoten:
                sammeln(wert)

    sammeln(rohdaten)
    widerspruechlich = [
        p for p in gefunden
        if p.get("broker_schutz") and not str(p.get("protection_algo_id") or "")]
    assert widerspruechlich, "Fixture enthaelt den Befund nicht mehr"
    assert {p["symbol"] for p in widerspruechlich} == {"BNB", "LINK", "XLM"}
    for p in widerspruechlich:
        assert p["protection_status"] == "PENDING"


# ---------------------------------------------------------------------------
# Ursache 1: der Wiederaufsetzpfad warf die algoId weg
# ---------------------------------------------------------------------------
def test_wiederaufsetzen_bucht_belege_vor_jedem_schutzauftrag():
    from broker.okx import OKXBroker, OKXInstrument

    meta = OKXInstrument("LINK-USDC", "LINK", "USDC", "live",
                         "0.001", "0.0001", "0.01")

    class Client:
        hat_zugangsdaten = True
        clock_offset_seconds = 0.0

        def instrument(self, _i): return meta
        def order_status(self, *_a, **_k):
            return {"ordId": "3877307136791490561", "state": "filled",
                    "accFillSz": "9.08808", "avgPx": "10.78", "side": "buy",
                    "clOrdId": "TBE9x", "instId": "LINK-USDC"}
        def fills(self, *_a, **_k):
            return [{"tradeId": "f1", "ordId": "3877307136791490561",
                     "instId": "LINK-USDC", "side": "buy", "fillSz": "9.08808",
                     "fillPx": "10.78", "fee": "0", "feeCcy": "USDC", "ts": "1"}]
        def fills_history_paginated(self, *_a, **_k): return []

    broker = OKXBroker(client=Client(), quote_ccy="USDC")
    # Since 9.8.3 this adapter method is read-only. Protection/ID persistence
    # is checked at the engine boundary in test_v983_doge_protection.py.
    def premature_protection(*args, **kwargs):
        raise AssertionError("Protection must follow durable position accounting")
    broker.reconcile_position_protection = premature_protection

    ergebnis = broker.reconcile_order_evidence({
        "inst_id": "LINK-USDC", "cl_ord_id": "TBE9x",
        "ord_id": "3877307136791490561", "qty": 9.08808,
        "signal_price": 10.78, "stop": 9.7, "take": 11.8})

    assert ergebnis is not None
    assert ergebnis.fill_evidence_complete
    assert not ergebnis.stop_order_platziert
    assert not ergebnis.protection_algo_id



# ---------------------------------------------------------------------------
# Ursache 3: der periodische Abgleich sah geschuetzte Positionen nie an
# ---------------------------------------------------------------------------
class SchutzBroker:
    """Broker, der protokolliert, ob er ueberhaupt gefragt wurde."""

    name = "OKX"
    quote_ccy = "USDC"

    def __init__(self, bestaetigt=True, algo_id="3879429521221038081"):
        from broker.okx import OKXInstrument
        self.abgleiche = []
        self._bestaetigt = bestaetigt
        self._algo_id = algo_id
        meta = OKXInstrument("XLM-USDC", "XLM", "USDC", "live",
                             "0.0001", "0.000001", "1")

        class Client:
            hat_zugangsdaten = True
            def instrument(self, _i): return meta
        self.client = Client()

    def is_connected(self): return True

    def positionen(self):
        # Der Bestand liegt im Konto -- sonst laeuft die Position in die
        # Fehlbestandspruefung und der Schutzabgleich wird nie erreicht.
        class Bestand:
            symbol = "XLM"
            quantity = 560.880025
            market_price = 0.1748
            currency = "USDC"
        return [Bestand()]
    def latest_bid_ask(self, _i): return {"bid": 10.7, "ask": 10.8, "last": 10.75}

    def reconcile_position_protection(self, instrument, menge, stop, ziel, **_k):
        self.abgleiche.append(getattr(instrument, "name", str(instrument)))
        if not self._bestaetigt:
            return {"checked": True, "protection_confirmed": False,
                    "algo_id": "", "detail": "keine Schutzorder im Konto"}
        return {"checked": True, "protection_confirmed": True,
                "algo_id": self._algo_id, "algo_client_id": "TBP9",
                "detail": "Broker-Schutz aktiv"}


def baue_motor(tmp_path, monkeypatch, broker):
    monkeypatch.setenv("TRADINGBOT_TEST_STATE_DIR", str(tmp_path))
    import crypto_engine as ce
    monkeypatch.setattr(ce, "_state_root", lambda: tmp_path)
    from risk_pots import RiskPotManager
    from universe.manager import UniverseManager
    from universe.modelle import UniverseZustand

    gemeldet: list[str] = []

    class Hub:
        def broker(self, name): return broker if name == "okx" else None
        def verbinde(self, _name): return True
        def zustaende(self): return {}

    engine = ce.CryptoEngine(
        hub=Hub(), risiko=RiskPotManager(["etoro", "okx"]),
        universum=UniverseManager(UniverseZustand(tmp_path / "u.json")),
        melder=lambda text, **k: gemeldet.append(text))
    engine.buch = ce.KryptoPositionsbuch(tmp_path / "p.json")
    return ce, engine, gemeldet


def xlm_position(ce):
    """Die echte XLM-Position vom 02.09.2026, Zahlen aus dem Fixture."""
    return ce.KryptoPosition(
        symbol="XLM", inst_id="XLM-USDC", menge=560.880025,
        einstieg=0.1748, stop=0.1660, take_profit=0.1920,
        broker_schutz=True,            # behauptet Schutz ...
        protection_status="PENDING",   # ... widerspricht sich selbst ...
        protection_algo_id="",         # ... und ist nicht belegbar
        order_id="3877307250507460609", referenz="c-xlm",
        client_order_id="c-xlm", order_tag="NEXUS", fill_ids=["f-xlm"],
        ownership_verified=True)


def test_unbelegter_schutz_wird_ueberhaupt_geprueft(tmp_path, monkeypatch):
    """Der Kern: bis 9.5.3 lief der Abgleich nur bei broker_schutz=False."""
    broker = SchutzBroker(bestaetigt=True)
    ce, engine, _ = baue_motor(tmp_path, monkeypatch, broker)
    engine.buch.setze(xlm_position(ce))

    engine.pruefe_positionen()

    assert broker.abgleiche, (
        "Eine Position mit broker_schutz=True und leerer algoId wurde gar "
        "nicht abgeglichen -- genau deshalb blieben BNB, LINK und XLM "
        "dauerhaft in diesem Zustand stehen")
    buch = engine.buch.hole("XLM")
    assert buch.protection_algo_id == "3879429521221038081"
    assert buch.protection_status == "ACTIVE"
    assert buch.broker_schutz is True


def test_nicht_bestaetigter_schutz_verliert_die_flagge_und_meldet(tmp_path, monkeypatch):
    """XLM lag real ohne Schutzorder im Konto. Das muss sichtbar werden."""
    broker = SchutzBroker(bestaetigt=False)
    ce, engine, gemeldet = baue_motor(tmp_path, monkeypatch, broker)
    engine.buch.setze(xlm_position(ce))

    engine.pruefe_positionen()

    buch = engine.buch.hole("XLM")
    assert buch.broker_schutz is False, (
        "Ohne Bestaetigung darf die Flagge keine Sicherheit behaupten")
    assert buch.protection_status == "MISSING"
    text = "\n".join(gemeldet)
    assert "nicht belegbar" in text and "ungeschuetzt" in text, (
        f"Georg muss davon erfahren. Gemeldet wurde: {text!r}")


def test_belegter_schutz_wird_nicht_unnoetig_neu_abgeglichen(tmp_path, monkeypatch):
    """Kein zusaetzlicher Brokerverkehr fuer sauber gefuehrte Positionen."""
    broker = SchutzBroker(bestaetigt=True)
    ce, engine, _ = baue_motor(tmp_path, monkeypatch, broker)
    position = xlm_position(ce)
    position.protection_algo_id = "3879429521221038081"
    position.protection_status = "ACTIVE"
    engine.buch.setze(position)

    engine.pruefe_positionen()

    assert broker.abgleiche == [], (
        "Ein belegter Schutz braucht keinen erneuten Abgleich")
