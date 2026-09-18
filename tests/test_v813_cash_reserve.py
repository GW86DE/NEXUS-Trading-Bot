"""Cash-Reserve: Order verkleinern statt blockieren (v8.1.3).

Anlass: Der Bot meldete "Kauf von BTC blockiert. Cash Reserven reichen nicht"
und liess die gesamte Order fallen, obwohl eine kleinere, regelkonforme Order
moeglich gewesen waere. Die Reserve bleibt hart -- der Bot nutzt das
verfuegbare Kapital aber flexibler.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("TRADINGBOT_TEST_STATE_DIR", "/tmp/tradingbot_tests")
Path(os.environ["TRADINGBOT_TEST_STATE_DIR"]).mkdir(parents=True, exist_ok=True)


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


class FakeBroker:
    name = "OKX"
    quote_ccy = "EUR"

    def __init__(self, cash=1000.0):
        self.client = FakeClient()
        self._cash = cash

    def verfuegbares_cash(self, quote_ccy=""):
        return self._cash


@pytest.fixture
def engine(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADINGBOT_TEST_STATE_DIR", str(tmp_path))
    import config
    monkeypatch.setattr(config, "OKX_CASH_RESERVE_PCT", 0.05, raising=False)
    monkeypatch.setattr(config, "OKX_TAKER_FEE_PCT", 0.001, raising=False)
    monkeypatch.setattr(config, "OKX_MIN_POSITION_VALUE", 15.0, raising=False)
    monkeypatch.setattr(config, "STARTUP_TRADING_GRACE_MINUTES", 0.0, raising=False)

    import crypto_engine as ce
    monkeypatch.setattr(ce, "_state_root", lambda: tmp_path)

    from risk_pots import RiskPotManager
    from universe.manager import UniverseManager
    from universe.modelle import UniverseZustand

    broker = FakeBroker()

    class Hub:
        def broker(self, name):
            return broker if name == "okx" else None

        def verbinde(self, name):
            return True

    motor = ce.CryptoEngine(
        hub=Hub(), risiko=RiskPotManager(["etoro", "okx"]),
        universum=UniverseManager(UniverseZustand(tmp_path / "u.json")))
    motor.buch = ce.KryptoPositionsbuch(tmp_path / "p.json")
    return motor, broker


# ---------------------------------------------------------------------------
# Der eigentliche Fehler
# ---------------------------------------------------------------------------
def test_order_wird_verkleinert_statt_blockiert(engine):
    """Georgs Beispiel: geplant 50, nutzbar rund 32 -> kaufen mit 32."""
    motor, broker = engine
    broker._cash = 34.0          # 5 % Reserve -> 32,30 nutzbar, minus Gebuehr
    preis = 100.0
    geplant = 0.5                # 50 Einheiten Gegenwert

    menge, hinweis, grund = motor._menge_an_cash_anpassen("BTC", geplant, preis, "EUR")

    assert grund == "", "Die Order darf nicht blockiert werden"
    assert 0 < menge < geplant, "Die Menge muss kleiner geworden sein"
    wert = menge * preis
    # frei 34 - 5 % Reserve = 32,30 ; davon Gebuehr heraus -> rund 32,27
    assert 32.0 <= wert <= 32.3, f"unerwarteter Wert {wert}"
    assert "reduziert" in hinweis
    assert "50.00" in hinweis and "32." in hinweis


def test_ausreichendes_cash_laesst_die_menge_unveraendert(engine):
    motor, broker = engine
    broker._cash = 10_000.0
    menge, hinweis, grund = motor._menge_an_cash_anpassen("BTC", 0.5, 100.0, "EUR")
    assert menge == pytest.approx(0.5)
    assert hinweis == "" and grund == ""


def test_reserve_bleibt_unangetastet(engine):
    """Die harte Sicherheitsreserve darf nie verplant werden."""
    motor, broker = engine
    broker._cash = 200.0
    menge, _, grund = motor._menge_an_cash_anpassen("BTC", 5.0, 100.0, "EUR")
    assert grund == ""
    assert menge * 100.0 <= 200.0 * 0.95, "Reserve wurde angegriffen"


def test_gebuehr_ist_zusaetzlich_gedeckt(engine):
    """Order plus Taker-Gebuehr muss in das nutzbare Cash passen."""
    motor, broker = engine
    broker._cash = 200.0
    menge, _, grund = motor._menge_an_cash_anpassen("BTC", 5.0, 100.0, "EUR")
    assert grund == ""
    wert = menge * 100.0
    assert wert * 1.001 <= 200.0 * 0.95 + 1e-6, "Gebuehr passt nicht mehr ins Budget"


# ---------------------------------------------------------------------------
# Die Grenzen bleiben hart
# ---------------------------------------------------------------------------
def test_unter_mindestordergroesse_wird_abgelehnt(engine):
    """Verkleinern ja -- aber nicht unter 15 EUR."""
    motor, broker = engine
    broker._cash = 12.0
    menge, hinweis, grund = motor._menge_an_cash_anpassen("BTC", 0.5, 100.0, "EUR")
    assert menge == 0.0
    assert "Mindestordergroesse" in grund
    assert "15.00" in grund, "Der Grund muss die konkrete Grenze nennen"
    assert hinweis == ""


def test_ablehnungsgrund_nennt_konkrete_zahlen(engine):
    """Statt 'Reserven reichen nicht' die tatsaechlichen Betraege."""
    motor, broker = engine
    broker._cash = 12.0
    _, _, grund = motor._menge_an_cash_anpassen("BTC", 0.5, 100.0, "EUR")
    assert "12.00" in grund, "freies Guthaben fehlt in der Meldung"
    assert "Reserve" in grund


def test_kein_cash_ergibt_saubere_ablehnung(engine):
    motor, broker = engine
    broker._cash = 0.0
    menge, _, grund = motor._menge_an_cash_anpassen("BTC", 0.5, 100.0, "EUR")
    assert menge == 0.0 and grund


def test_nicht_abrufbares_guthaben_kauft_nicht(engine):
    """Unbekanntes Guthaben ist kein Freibrief."""
    motor, broker = engine
    broker.verfuegbares_cash = lambda quote_ccy="": None
    menge, _, grund = motor._menge_an_cash_anpassen("BTC", 0.5, 100.0, "EUR")
    assert menge == 0.0 and "nicht abrufbar" in grund


def test_menge_bleibt_auf_dem_okx_raster(engine):
    """Verkleinern darf keine Menge erzeugen, die OKX ablehnt."""
    from decimal import Decimal
    motor, broker = engine
    broker._cash = 137.77
    menge, _, grund = motor._menge_an_cash_anpassen("BTC", 5.0, 100.0, "EUR")
    assert grund == ""
    rest = Decimal(str(menge)) % Decimal("0.00001")
    assert rest == 0, f"{menge} liegt nicht auf dem Lot-Raster"
    assert menge >= float(FakeMeta.min_size)


def test_ohne_broker_wird_nicht_gekauft(engine):
    motor, _ = engine

    class LeererHub:
        def broker(self, name):
            return None

    motor.hub = LeererHub()
    menge, _, grund = motor._menge_an_cash_anpassen("BTC", 0.5, 100.0, "EUR")
    assert menge == 0.0 and "nicht verbunden" in grund


# ---------------------------------------------------------------------------
# Konfiguration
# ---------------------------------------------------------------------------
def test_reserve_ist_einstellbar():
    """Der Wert stand vorher nur als Standard im Code und war nicht setzbar."""
    import config
    assert hasattr(config, "OKX_CASH_RESERVE_PCT")
    assert 0.0 <= config.OKX_CASH_RESERVE_PCT < 0.5


def test_hoehere_reserve_verkleinert_staerker(engine, monkeypatch):
    motor, broker = engine
    import config
    broker._cash = 1000.0

    monkeypatch.setattr(config, "OKX_CASH_RESERVE_PCT", 0.05, raising=False)
    klein, _, _ = motor._menge_an_cash_anpassen("BTC", 50.0, 100.0, "EUR")
    monkeypatch.setattr(config, "OKX_CASH_RESERVE_PCT", 0.30, raising=False)
    gross, _, _ = motor._menge_an_cash_anpassen("BTC", 50.0, 100.0, "EUR")

    assert gross < klein, "Eine hoehere Reserve muss die Order staerker verkleinern"


# ---------------------------------------------------------------------------
# eToro-Seite
# ---------------------------------------------------------------------------
def test_etoro_verkleinert_ebenfalls_und_meldet_es():
    """Der Aktienpfad konnte das schon -- er hat es nur nicht gesagt."""
    quelle = (Path(__file__).resolve().parent.parent / "live_trader.py").read_text(encoding="utf-8")
    assert "qty = min(float(qty), max_cash_qty)" in quelle, "Verkleinerung fehlt"
    assert "wegen Cash-Reserve reduziert" in quelle, "Meldung ueber die Verkleinerung fehlt"
    assert "CASH_REDUKTION" in quelle, "Protokolleintrag fehlt"


# ---------------------------------------------------------------------------
# Fail closed: ohne Instrumentdaten wird nicht gekauft
# ---------------------------------------------------------------------------
def test_ohne_lotgroesse_wird_nicht_gekauft(engine):
    """Vorher kam die Menge UNGERUNDET zurueck -- weder auf dem Lotraster
    noch gegen minSz geprueft. OKX haette die Order abgelehnt."""
    import crypto_engine as ce
    motor, broker = engine

    def kaputt(inst_id):
        raise ce.BrokerFehler("Instrumentdaten nicht abrufbar")

    broker.client.instrument = kaputt
    broker._cash = 1000.0
    menge, _, grund = motor._menge_an_cash_anpassen("BTC", 50.0, 100.0, "EUR")
    assert menge == 0.0, "Ohne Lotgroesse darf keine Menge entstehen"
    assert grund, "Die Ablehnung muss begruendet sein"


def test_verkleinerung_flutet_nicht_den_telegram_kanal():
    """Gemeldet wird die Verkleinerung erst beim tatsaechlichen Kauf."""
    quelle = (Path(__file__).resolve().parent.parent / "crypto_engine.py").read_text(encoding="utf-8")
    pruef_stelle = quelle.index("cash_hinweis, cash_grund = self._menge_an_cash_anpassen")
    # Alles vor dem bestaetigten Fill ist Pruefpfad -- dort darf nicht
    # gemeldet werden, weil der Kandidat die Kaskade noch nicht bestanden hat.
    fill_stelle = quelle.index("gefuellt = float(ergebnis.filled_quantity")
    pruefpfad = quelle[pruef_stelle:fill_stelle]
    assert "cash_hinweis" not in pruefpfad.split("if not cash_ok")[-1] or \
        "self._melde(f\"Krypto {symbol}: {cash_hinweis}\")" not in pruefpfad, \
        "Die Verkleinerungsmeldung im Pruefpfad wuerde je Kandidat und Scan feuern"
    # Ab v8.1.4 traegt die Kaufmeldung die Hinweise als Liste mit.
    assert "hinweise=hinweise" in quelle[fill_stelle:], \
        "Beim echten Kauf muss die Verkleinerung mitgemeldet werden"
    assert "cash_hinweis" in quelle[fill_stelle:], \
        "Der Cash-Hinweis muss in der Kaufmeldung landen"
