"""Geldpfad-Test der Krypto-Handelsmaschine (v7.0 NEXUS).

Der komplette Kaufweg wird gegen einen Fake-Broker durchgespielt:

    Universum -> Signal -> Stop/Ziel -> Groesse -> Kosten -> Candidate Gate
    -> Order -> Broker-Schutz -> Positionsbuch -> Quellenprotokoll

Es entsteht dabei zu keinem Zeitpunkt eine echte Order.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("TRADINGBOT_TEST_STATE_DIR", "/tmp/tradingbot_tests")
Path(os.environ["TRADINGBOT_TEST_STATE_DIR"]).mkdir(parents=True, exist_ok=True)

from broker.base import BrokerFehler, Fill, OrderErgebnis, OrderStatusUnklar, Position  # noqa: E402
from broker.okx import OKXInstrument  # noqa: E402


# ---------------------------------------------------------------------------
# Fake-Bausteine
# ---------------------------------------------------------------------------
def _rahmen(schluss: list[float]) -> pd.DataFrame:
    index = pd.date_range("2026-08-01", periods=len(schluss), freq="15min", tz="UTC")
    return pd.DataFrame({
        "open": [c - 0.05 for c in schluss],
        "high": [c + 0.25 for c in schluss],
        "low": [c - 0.25 for c in schluss],
        "close": schluss,
        "volume": [1000.0] * len(schluss),
    }, index=index)


def kaufsignal_kerzen(start=100.0) -> pd.DataFrame:
    """Aufwaertstrend, kurzer Ruecksetzer, Drehung nach oben.

    Genau dieses Muster verlangt der Einstiegsmodus 'trend': der schnelle
    Schnitt liegt ueber dem langsamen, der RSI ist unter die
    Ruecksetzerschwelle gefallen und dreht auf dem letzten Balken wieder
    nach oben. Ein reiner Dauerauftrieb erzeugt KEIN Signal -- deshalb
    steht hier nicht einfach eine Gerade.
    """
    werte = [start]
    for _ in range(160):
        werte.append(werte[-1] + 0.5)      # Aufwaertstrend aufbauen
    for _ in range(6):
        werte.append(werte[-1] - 1.2)      # Ruecksetzer, RSI faellt unter 45
    werte.append(werte[-1] + 1.4)          # Drehung nach oben
    return _rahmen(werte)


def steigende_kerzen(n=200, start=100.0, schritt=0.4) -> pd.DataFrame:
    return kaufsignal_kerzen(start=start)


def fallende_kerzen(n=200, start=200.0, schritt=0.4) -> pd.DataFrame:
    """Abwaertstrend -- darf niemals ein Kaufsignal erzeugen."""
    return _rahmen([max(1.0, start - i * schritt) for i in range(n)])


class FakeClient:
    """Fake der OKX-REST-Schicht -- deckt Handel UND Universumslauf ab."""

    SYMBOLE = ("BTC", "ETH")

    def instrument(self, inst_id):
        basis = str(inst_id).split("-")[0].upper()
        if basis not in self.SYMBOLE:
            return None
        return OKXInstrument(inst_id=f"{basis}-EUR", base_ccy=basis, quote_ccy="EUR",
                             state="live", tick_size="0.01", lot_size="0.00001",
                             min_size="0.0001", list_time_ms=1_500_000_000_000)

    def instruments(self, **_):
        return {f"{s}-EUR": self.instrument(f"{s}-EUR") for s in self.SYMBOLE}

    def tickers(self):
        from broker.okx import OKXTicker
        import time as _t
        return {f"{s}-EUR": OKXTicker(inst_id=f"{s}-EUR", last=100.0, bid=99.95, ask=100.05,
                                       vol_24h_base=1e6, vol_24h_quote=5e8, open_24h=98.0,
                                       timestamp_ms=int(_t.time() * 1000))
                for s in self.SYMBOLE}

    def orderbook(self, inst_id, depth=20):
        return {"bids": [(99.95, 50.0)] * 10, "asks": [(100.05, 50.0)] * 10}

    def candles(self, inst_id, bar="15m", limit=96, nur_abgeschlossen=True):
        return kaufsignal_kerzen()

    def balances(self):
        owner = getattr(self, "owner", None)
        if owner is None:
            return {}
        return {
            symbol: {"gesamt": menge, "cash": menge}
            for symbol, menge in owner.bestaende.items()
            if menge > 0
        }


class FakeBroker:
    """Fake-OKX-Adapter, der jede Order protokolliert statt sie zu senden."""

    name = "OKX"
    quote_ccy = "EUR"

    def __init__(self, *, kerzen=None, cash=10_000.0, paper=True):
        self.client = FakeClient()
        self.client.owner = self
        self.kerzen = kerzen if kerzen is not None else steigende_kerzen()
        self.cash = cash
        self.paper = paper
        self.kaeufe = []
        self.verkaeufe = []
        self.bestaende: dict[str, float] = {}
        self.preise: dict[str, float] = {}
        self.kauf_fehler = None
        self.schutz_bestaetigt = True

    # Explicit synthetic identity; no production account inference.
    def account_fingerprint(self): return 'test-v70'
    @property
    def demo(self): return bool(self.paper)

    # -- Zustand ------------------------------------------------------------
    def is_connected(self):
        return True

    def ist_paper(self):
        return self.paper

    def kontowert(self):
        return self.cash + sum(self.bestaende.get(s, 0.0) * self.preise.get(s, 0.0)
                               for s in self.bestaende)

    def verfuegbares_cash(self, quote_ccy=""):
        return self.cash

    def beschreibung(self):
        return "OKX Spot (DEMO)"

    # -- Marktdaten ---------------------------------------------------------
    def historie(self, instrument, dauer, kerzengroesse, nur_handelszeiten=True):
        groesse = str(kerzengroesse).strip().lower()
        if groesse.startswith("5 min") or "hour" in groesse:
            return _rahmen([100.0 + i * 0.25 for i in range(200)])
        return self.kerzen

    def latest_bid_ask(self, instrument):
        letzter = float(self.kerzen["close"].iloc[-1])
        return {"bid": letzter * 0.9995, "ask": letzter * 1.0005, "last": letzter}

    def positionen(self):
        out = []
        for symbol, menge in self.bestaende.items():
            preis = self.preise.get(symbol, 0.0)
            out.append(Position(symbol=symbol, quantity=menge, avg_cost=0.0,
                                currency="EUR", asset_type="crypto",
                                broker_id=f"{symbol}-EUR", market_price=preis,
                                market_value=menge * preis))
        return out

    def hat_offene_order(self, instrument, seite="BUY"):
        return False

    # -- Orders -------------------------------------------------------------
    def kaufe_mit_absicherung(self, instrument, menge, referenzpreis, stop, take_profit):
        if self.kauf_fehler is not None:
            fehler, self.kauf_fehler = self.kauf_fehler, None
            raise fehler
        symbol = str(instrument.name).upper()
        self.kaeufe.append({"symbol": symbol, "menge": menge, "preis": referenzpreis,
                            "stop": stop, "take": take_profit})
        self.bestaende[symbol] = self.bestaende.get(symbol, 0.0) + menge
        self.preise[symbol] = referenzpreis
        self.cash -= menge * referenzpreis
        fill_id = f"trade-{len(self.kaeufe)}"
        client_order_id = f"NEXUS{len(self.kaeufe)}"
        return OrderErgebnis(order_ids=["1"], status="filled", terminal=True, filled_quantity=menge,
                             avg_fill_price=referenzpreis, stop_order_platziert=True,
                             take_order_platziert=True, reference_id=client_order_id,
                             client_order_id=client_order_id, order_tag="NEXUS",
                             gross_filled_quantity=menge,
                             fills=[{"tradeId": fill_id, "ordId": "1",
                                     "fillSz": str(menge), "fillPx": str(referenzpreis),
                                     "fee": "0", "feeCcy": "EUR"}],
                             fill_ids=[fill_id], fill_evidence_complete=True,
                             trade_quote_ccy="EUR", paper=self.paper)

    def schliesse_position(self, instrument, menge, referenzpreis=0.0, **identity):
        symbol = str(instrument.name).upper()
        self.verkaeufe.append({"symbol": symbol, "menge": menge, "preis": referenzpreis})
        vorhanden = self.bestaende.get(symbol, 0.0)
        verkauft = min(menge, vorhanden)
        rest = vorhanden - verkauft
        if rest <= 0:
            self.bestaende.pop(symbol, None)
        else:
            self.bestaende[symbol] = rest
        self.cash += verkauft * referenzpreis
        inst_id = str(getattr(getattr(instrument, "contract", None),
                              "localSymbol", "") or f"{symbol}-EUR")
        fill_id = f"okx:test:{inst_id}:2:sell-1"
        return OrderErgebnis(
            order_ids=["2"], status="filled", filled_quantity=verkauft,
            gross_filled_quantity=verkauft, avg_fill_price=referenzpreis,
            reference_id=str(identity.get("client_order_id") or "ref2"),
            client_order_id=str(identity.get("client_order_id") or "ref2"),
            requested_quantity=menge, remaining_quantity=max(0.0, menge - verkauft),
            terminal=True, raw_status="filled", fill_ids=[fill_id],
            fills=[{"tradeId": "sell-1", "ordId": "2",
                    "instId": inst_id, "fillSz": str(verkauft),
                    "fillPx": str(referenzpreis), "fee": "0", "feeCcy": "EUR"}],
            fill_evidence_complete=True, trade_quote_ccy="EUR",
            account_fingerprint=str(identity.get("account_fingerprint") or "test"),
            paper=self.paper)

    def reconcile_position_protection(self, instrument, quantity, stop, take_profit):
        return {"checked": True, "changed": self.schutz_bestaetigt,
                "protection_confirmed": self.schutz_bestaetigt, "detail": "Test"}

    def storniere_offene_orders(self, instrument):
        return 0


class FakeHub:
    def __init__(self, broker):
        self._broker = broker

    def broker(self, name):
        return self._broker if name == "okx" else None

    def verbinde(self, name):
        return True


# ---------------------------------------------------------------------------
# Aufbau
# ---------------------------------------------------------------------------
@pytest.fixture
def maschine(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADINGBOT_TEST_STATE_DIR", str(tmp_path))
    import config
    monkeypatch.setattr(config, "OKX_MIN_POSITION_VALUE", 0.0, raising=False)
    monkeypatch.setattr(config, "ENTRY_MODE", "trend", raising=False)
    monkeypatch.setattr(config, "USE_ML_FILTER", False, raising=False)
    # Ab v8.1.4 gibt es eine Anlaufsperre: 15 Minuten nach dem Start finden
    # keine neuen Einstiege statt. Diese Tests pruefen den Kaufweg selbst,
    # nicht die Sperre -- dafuer gibt es tests/test_v814_anlaufsperre.py.
    monkeypatch.setattr(config, "STARTUP_TRADING_GRACE_MINUTES", 0.0, raising=False)

    import crypto_engine as ce
    monkeypatch.setattr(ce, "_state_root", lambda: tmp_path)

    from risk_pots import RiskPotManager
    from universe.manager import UniverseManager
    from universe.modelle import AKTIV, UniverseMitglied, UniverseZustand

    broker = FakeBroker()
    hub = FakeHub(broker)
    risiko = RiskPotManager(["etoro", "okx"])
    risiko.topf("okx").setze_kontowert(10_000.0)

    universum = UniverseManager(UniverseZustand(tmp_path / "universe_state.json"))
    universum.zustand.setze(UniverseMitglied(
        symbol="BTC", broker="okx", asset_type="crypto", inst_id="BTC-EUR",
        zustand=AKTIV, letzter_rang=1, letzter_score=0.85))

    meldungen = []
    engine = ce.CryptoEngine(hub=hub, risiko=risiko, universum=universum,
                             melder=lambda text, wichtig=False: meldungen.append(text))
    engine.buch = ce.KryptoPositionsbuch(tmp_path / "crypto_positions.json")
    for name in list(engine.bereitschaft.bedingungen):
        engine.bereitschaft.melde(name, True, "Testumgebung")
    return engine, broker, meldungen


# ---------------------------------------------------------------------------
# Kaufweg
# ---------------------------------------------------------------------------
def test_kauf_wird_ausgefuehrt_und_gebucht(maschine):
    engine, broker, meldungen = maschine
    ergebnis = engine.pruefe_kandidat("BTC")

    assert ergebnis["gekauft"] is True, ergebnis["grund"]
    assert len(broker.kaeufe) == 1
    kauf = broker.kaeufe[0]
    assert kauf["stop"] < kauf["preis"] < kauf["take"]

    position = engine.buch.hole("BTC")
    assert position is not None
    assert position.menge == pytest.approx(kauf["menge"])
    assert position.broker_schutz is True
    # Ab v8.1.4 einheitliches Meldungsformat "KAUF · OKX · SYMBOL".
    text = "\n".join(meldungen)
    assert "KAUF" in text and "BTC" in text
    assert "Stop" in text and "Ziel" in text, "Stop und Ziel gehoeren in die Meldung"
    assert "Gebuehr" in text, "Die Gebuehr gehoert in die Meldung"


def test_ohne_kaufsignal_wird_nicht_gekauft(maschine):
    engine, broker, _ = maschine
    broker.kerzen = fallende_kerzen()
    ergebnis = engine.pruefe_kandidat("BTC")
    assert ergebnis["gekauft"] is False
    assert broker.kaeufe == []
    assert "Kaufsignal" in ergebnis["grund"] or "Signal" in ergebnis["grund"]


def test_nicht_handelbares_universumsmitglied_wird_abgelehnt(maschine):
    engine, broker, _ = maschine
    from universe.modelle import BEOBACHTUNG
    mitglied = engine.universum.zustand.hole("okx", "BTC")
    mitglied.zustand = BEOBACHTUNG
    engine.universum.zustand.setze(mitglied)

    ergebnis = engine.pruefe_kandidat("BTC")
    assert ergebnis["gekauft"] is False
    assert "Universum" in ergebnis["grund"] or "universum" in ergebnis["grund"]
    assert broker.kaeufe == []


def test_beobachteter_wert_wird_nie_gehandelt(maschine):
    """Bewaehrung heisst beobachten, nicht kaufen."""
    engine, broker, _ = maschine
    from universe.modelle import BEOBACHTUNG, UniverseMitglied
    engine.universum.zustand.setze(UniverseMitglied(
        symbol="ETH", broker="okx", asset_type="crypto", inst_id="ETH-EUR",
        zustand=BEOBACHTUNG, letzter_rang=2, letzter_score=0.8))
    assert "ETH" not in engine.universum.handelbare_symbole("okx")
    ergebnis = engine.pruefe_kandidat("ETH")
    assert ergebnis["gekauft"] is False


def test_tagesverlustgrenze_verhindert_den_kauf(maschine):
    engine, broker, _ = maschine
    engine.topf.state.realized_pnl_today = -10_000.0
    engine.topf.state.save()
    ergebnis = engine.pruefe_kandidat("BTC")
    assert ergebnis["gekauft"] is False
    assert broker.kaeufe == []


def test_zu_hoher_spread_blockiert(maschine, monkeypatch):
    engine, broker, _ = maschine
    letzter = float(broker.kerzen["close"].iloc[-1])
    monkeypatch.setattr(broker, "latest_bid_ask",
                        lambda instrument: {"bid": letzter * 0.95, "ask": letzter * 1.05,
                                            "last": letzter})
    ergebnis = engine.pruefe_kandidat("BTC")
    assert ergebnis["gekauft"] is False
    assert broker.kaeufe == []


def test_unklarer_kaufzustand_erzeugt_keine_position(maschine):
    """Der gefaehrlichste Fall: die Order kann angekommen sein."""
    engine, broker, meldungen = maschine
    broker.kauf_fehler = OrderStatusUnklar("Timeout", reference_id="ABC123")
    ergebnis = engine.pruefe_kandidat("BTC")

    assert ergebnis["gekauft"] is False
    assert engine.buch.hole("BTC") is None
    assert any("UNKLAR" in m for m in meldungen)
    # Und ganz sicher kein blinder zweiter Versuch:
    assert len(broker.kaeufe) == 0


def test_bereits_offene_position_wird_nicht_verdoppelt(maschine):
    engine, broker, _ = maschine
    engine.pruefe_kandidat("BTC")
    assert len(broker.kaeufe) == 1
    zweiter = engine.pruefe_kandidat("BTC")
    assert zweiter["gekauft"] is False
    assert len(broker.kaeufe) == 1


def test_quellenprotokoll_wird_geschrieben(maschine, tmp_path, monkeypatch):
    monkeypatch.setenv("TRADINGBOT_TEST_STATE_DIR", str(tmp_path))
    engine, _, _ = maschine
    engine.pruefe_kandidat("BTC")

    import decision_source as ds
    eintraege = ds.lies(limit=5, symbol="BTC")
    assert eintraege, "Jede Entscheidung muss ein Quellenprotokoll haben"
    quellen = {q["quelle"] for q in eintraege[0]["quellen"]}
    assert "TECHNIK" in quellen and "UNIVERSE" in quellen and "RISK_GATE" in quellen
    assert eintraege[0]["hauptquelle"]


# ---------------------------------------------------------------------------
# Positionsueberwachung
# ---------------------------------------------------------------------------
def test_stop_loss_schliesst_die_position(maschine):
    engine, broker, meldungen = maschine
    engine.pruefe_kandidat("BTC")
    position = engine.buch.hole("BTC")

    # Kurs faellt unter den Stop.
    broker.preise["BTC"] = position.stop * 0.98
    bericht = engine.pruefe_positionen()

    assert "BTC" in bericht["geschlossen"]
    assert engine.buch.hole("BTC") is None
    assert len(broker.verkaeufe) == 1
    # Ab v8.1.4 einheitliches Meldungsformat "VERKAUF · OKX · SYMBOL".
    assert any("VERKAUF" in m and "BTC" in m for m in meldungen)
    assert any("vollstaendig ausgefuehrt" in m for m in meldungen), \
        "Eine Vollausfuehrung muss als solche benannt werden"


def test_gewinnziel_schliesst_die_position(maschine):
    engine, broker, _ = maschine
    engine.pruefe_kandidat("BTC")
    position = engine.buch.hole("BTC")
    broker.preise["BTC"] = position.take_profit * 1.01
    bericht = engine.pruefe_positionen()
    assert "BTC" in bericht["geschlossen"]


def test_verschwundenes_guthaben_wird_abgeglichen(maschine):
    """Die Schutzorder hat gegriffen -- das Buch muss nachziehen."""
    engine, broker, _ = maschine
    engine.pruefe_kandidat("BTC")
    broker.bestaende.pop("BTC", None)
    # Ab v8.1.4 braucht ein Nullstand zwei Zyklen -- ein einzelner
    # unvollstaendiger Guthaben-Schnappschuss darf nichts abrechnen.
    engine.pruefe_positionen()
    p = engine.buch.hole("BTC")
    p.first_missing_at = (datetime.now(timezone.utc) - timedelta(seconds=31)).isoformat()
    engine.buch.setze(p)
    bericht = engine.pruefe_positionen()
    assert "BTC" in bericht["broker_state_unknown"]
    assert engine.buch.hole("BTC").broker_state == "BROKER_STATE_UNKNOWN"


def test_fehlender_broker_schutz_wird_nachgezogen(maschine):
    engine, broker, _ = maschine
    engine.pruefe_kandidat("BTC")
    position = engine.buch.hole("BTC")
    position.broker_schutz = False
    engine.buch.setze(position)
    broker.preise["BTC"] = position.einstieg     # weder Stop noch Ziel

    bericht = engine.pruefe_positionen()
    assert "BTC" in bericht["schutz_ergaenzt"]
    assert engine.buch.hole("BTC").broker_schutz is True


def test_positionsbuch_ueberlebt_neustart(maschine, tmp_path):
    engine, _, _ = maschine
    engine.pruefe_kandidat("BTC")

    import crypto_engine as ce
    neues_buch = ce.KryptoPositionsbuch(tmp_path / "crypto_positions.json")
    position = neues_buch.hole("BTC")
    assert position is not None and position.stop > 0, \
        "Ohne persistenten Stop wuesste der Bot nach einem Neustart nicht, wo er absichert"


# ---------------------------------------------------------------------------
# Zyklus
# ---------------------------------------------------------------------------
def test_zyklus_fuehrt_faellige_arbeiten_aus(maschine):
    engine, _, _ = maschine
    ergebnis = engine.zyklus()
    # Beim ersten Lauf ist alles faellig.
    assert set(ergebnis["arbeiten"]) == {"positionen", "universum", "scan"}


def test_zyklus_wiederholt_nicht_sofort(maschine):
    engine, _, _ = maschine
    engine.zyklus()
    zweiter = engine.zyklus()
    assert zweiter["arbeiten"] == []


def test_status_ist_vollstaendig(maschine):
    engine, _, _ = maschine
    status = engine.status()
    for feld in ("zeit", "verbunden", "modus", "positionen", "universum", "risiko", "takt"):
        assert feld in status
    assert status["modus"] == "DEMO"
