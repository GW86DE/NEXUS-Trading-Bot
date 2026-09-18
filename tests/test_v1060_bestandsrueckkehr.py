"""10.6.0 -- Bestaetigte Fehlmessung und Rueckweg aus der Bestandsluecke.

BEFUND VOM 18.09.2026 (Diagnosepaket 06:11 UTC)
===============================================
In der Nacht kaufte der Bot drei Coins: XRP 22:50, BTC 23:00, ETH 00:55 UTC.
Am 18.09. um 02:34:42 und 02:34:54 UTC -- neun Sekunden auseinander -- lagen
zwei unvollstaendige Guthaben-Schnappschuesse vor. Beide meldeten fuer alle
drei Coins kein Guthaben.

Der Positionsabgleich buchte daraufhin drei Bestandsluecken mit
``observed_balance = "0.0"`` und setzte BROKER_STATE_UNKNOWN. Danach waren
sechs Buchungsbelege offen, und JEDER weitere OKX-Kauf war gesperrt. Die
Oberflaeche meldete "0 offene Positionen".

Verloren war nichts: Das Guthaben wies die gekauften Mengen unveraendert aus
(XRP 18,3284 von 50.018,3284; BTC 0,00030163 von 1,00030164; ETH 0,0091382
von 10,00913835), und die OCO-Schutzorders waren um 02:34 zuletzt als ACTIVE
bestaetigt.

ZWEI KONSTRUKTIONSFEHLER
------------------------
1. Die Fehlmessung wurde nie bestaetigt. ``OKX_POSITION_MISSING_CONFIRM_SECONDS``
   stand seit Langem in der Konfiguration, wurde aber nirgends abgefragt. Zwei
   Zyklen im Abstand von neun Sekunden sind dieselbe Stoerung, nicht zwei
   unabhaengige Beobachtungen. Die Aktienseite (position_manager) prueft beides
   seit jeher.

2. Es gab keinen Rueckweg. Alle drei Aufloesungswege einer Bestandsluecke
   setzten einen VERKAUF voraus: exakte Exit-Fills, Abrechnung extern
   geschlossener Positionen, manuelle Aussenbuchung. Fuer "der Bestand war nie
   weg" existierte kein einziger Pfad zurueck.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest


def vor(sekunden: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=sekunden)).isoformat()


class Bestand:
    def __init__(self, symbol, menge, preis, waehrung="EUR"):
        self.symbol = symbol
        self.quantity = menge
        self.market_price = preis
        self.currency = waehrung


class KontoBroker:
    """Nur so viel OKX, wie der Positionsabgleich wirklich anfasst."""

    name = "OKX"
    quote_ccy = "EUR"
    demo = True

    def __init__(self, bestaende, *, snapshot=True):
        from broker.okx import OKXInstrument
        self._bestaende = list(bestaende)
        self._snapshot = snapshot
        meta = OKXInstrument("ETH-EUR", "ETH", "EUR", "live",
                             "0.01", "0.00000001", "0.0001")

        class Client:
            hat_zugangsdaten = True
            def instrument(self, _i): return meta
        self.client = Client()

    def is_connected(self): return True
    def account_fingerprint(self): return "244895ca0a404f80142b79f5"
    def positionen(self, **_k): return list(self._bestaende)
    def guthaben_schnappschuss(self):
        if not self._snapshot:
            return {}
        return {b.symbol: {"frei": 0.0, "gesamt": b.quantity} for b in self._bestaende}
    def latest_bid_ask(self, _i): return {"bid": 2129.1, "ask": 2131.3, "last": 2130.0}
    def reconcile_position_protection(self, *_a, **_k):
        return {"checked": True, "protection_confirmed": True,
                "algo_id": "algo-eth", "algo_client_id": "Peth", "detail": ""}


@pytest.fixture
def motor(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADINGBOT_TEST_STATE_DIR", str(tmp_path))
    import crypto_engine as ce
    monkeypatch.setattr(ce, "_state_root", lambda: tmp_path)
    from risk_pots import RiskPotManager
    from universe.manager import UniverseManager
    from universe.modelle import UniverseZustand

    gemeldet: list[str] = []

    def bauen(bestaende, *, snapshot=True):
        broker = KontoBroker(bestaende, snapshot=snapshot)

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


# Die echte ETH-Position der Nacht vom 17./18.09.2026.
ETH_MENGE = 0.0091382526


def eth_position(ce, **felder):
    werte = dict(
        symbol="ETH", inst_id="ETH-EUR", menge=ETH_MENGE,
        einstieg=2131.3, stop=1918.17, take_profit=2232.1223602609134,
        broker_schutz=True, protection_status="ACTIVE",
        protection_algo_id="algo-eth", protection_client_order_id="Peth",
        order_id="3877276078171697153", referenz="N93764847086281095168",
        client_order_id="N93764847086281095168", order_tag="NEXUS",
        fill_ids=["fill-eth"], ownership_verified=True,
        account_fingerprint="244895ca0a404f80142b79f5", paper=True,
        decision_id=3764847086281095168)
    werte.update(felder)
    return ce.KryptoPosition(**werte)


def zaehler_setzen(engine, symbol, **felder):
    position = engine.buch.hole(symbol)
    for name, wert in felder.items():
        setattr(position, name, wert)
    engine.buch.setze(position)
    return position


# ---------------------------------------------------------------------------
# 1. Fehlmessung muss bestaetigt werden
# ---------------------------------------------------------------------------
def test_eine_einzelne_fehlmessung_bucht_nichts(motor):
    bauen, gemeldet = motor
    import crypto_engine as ce
    _, engine, _ = bauen([])                     # Guthaben meldet gar nichts
    engine.buch.setze(eth_position(ce))

    engine.pruefe_positionen()

    position = engine.buch.hole("ETH")
    assert position is not None
    assert position.broker_state == "", "Ein Schnappschuss ist kein Beweis"
    assert position.consecutive_missing_snapshots == 1
    assert position.first_missing_at
    assert any("NICHT" in text and "bestaetigt" in text for text in gemeldet), (
        f"Der Verdacht muss gemeldet werden: {gemeldet!r}")


def test_zwei_messungen_in_neun_sekunden_buchen_nichts(motor):
    """Der echte Ablauf vom 18.09.2026, 02:34:42 und 02:34:54 UTC."""
    bauen, _ = motor
    import crypto_engine as ce
    _, engine, _ = bauen([])
    engine.buch.setze(eth_position(ce))

    engine.pruefe_positionen()
    zaehler_setzen(engine, "ETH", first_missing_at=vor(9))
    engine.pruefe_positionen()

    position = engine.buch.hole("ETH")
    assert position.consecutive_missing_snapshots == 2
    assert position.broker_state == "", (
        "Zwei Messungen im Abstand von neun Sekunden sind dieselbe Stoerung")
    import okx_accounting
    assert okx_accounting.status("244895ca0a404f80142b79f5", "DEMO")["complete"], (
        "Ohne bestaetigten Befund darf kein Kauf gesperrt werden")


def test_zwei_messungen_mit_mindestabstand_buchen(motor):
    bauen, _ = motor
    import crypto_engine as ce
    _, engine, _ = bauen([])
    engine.buch.setze(eth_position(ce))

    engine.pruefe_positionen()
    zaehler_setzen(engine, "ETH", first_missing_at=vor(31))
    engine.pruefe_positionen()

    position = engine.buch.hole("ETH")
    assert position.broker_state == "BROKER_STATE_UNKNOWN"
    assert position.menge == pytest.approx(ETH_MENGE), "Die Menge bleibt erhalten"


def test_frist_ist_einstellbar_und_null_bedeutet_sofort(motor, monkeypatch):
    bauen, _ = motor
    import crypto_engine as ce
    _, engine, _ = bauen([])
    monkeypatch.setattr(engine.cfg, "OKX_POSITION_MISSING_CONFIRM_SECONDS", 0.0,
                        raising=False)
    engine.buch.setze(eth_position(ce))

    engine.pruefe_positionen()
    assert engine.buch.hole("ETH").broker_state == "", "Zwei Messungen bleiben Pflicht"
    engine.pruefe_positionen()
    assert engine.buch.hole("ETH").broker_state == "BROKER_STATE_UNKNOWN"


def test_kaputter_zeitstempel_bestaetigt_nicht(motor):
    bauen, _ = motor
    import crypto_engine as ce
    _, engine, _ = bauen([])
    engine.buch.setze(eth_position(ce))

    engine.pruefe_positionen()
    zaehler_setzen(engine, "ETH", first_missing_at="kein Zeitstempel")
    engine.pruefe_positionen()

    assert engine.buch.hole("ETH").broker_state == "", (
        "Ohne belastbaren Zeitstempel wird weiter beobachtet, nicht gebucht")


def test_wiedergesehener_bestand_setzt_den_fehlzaehler_zurueck(motor):
    bauen, _ = motor
    import crypto_engine as ce
    _, engine, broker = bauen([])
    engine.buch.setze(eth_position(ce))

    engine.pruefe_positionen()
    assert engine.buch.hole("ETH").consecutive_missing_snapshots == 1
    broker._bestaende = [Bestand("ETH", ETH_MENGE, 2131.3)]
    engine.pruefe_positionen()

    position = engine.buch.hole("ETH")
    assert position.consecutive_missing_snapshots == 0 and not position.first_missing_at


# ---------------------------------------------------------------------------
# 2. Rueckweg aus der Bestandsluecke
# ---------------------------------------------------------------------------
def test_erste_wiedersicht_nimmt_noch_nicht_zurueck(motor):
    bauen, _ = motor
    import crypto_engine as ce
    _, engine, _ = bauen([Bestand("ETH", ETH_MENGE, 2131.3)])
    engine.buch.setze(eth_position(ce, broker_state="BROKER_STATE_UNKNOWN",
                                   protection_status="UNKNOWN_BROKER_STATE",
                                   broker_schutz=False))

    engine.pruefe_positionen()

    position = engine.buch.hole("ETH")
    assert position.broker_state == "BROKER_STATE_UNKNOWN"
    assert position.consecutive_present_snapshots == 1


def test_zwei_bestaetigte_wiedersichten_geben_die_position_frei(motor):
    bauen, gemeldet = motor
    import crypto_engine as ce
    _, engine, _ = bauen([Bestand("ETH", ETH_MENGE, 2131.3)])
    engine.buch.setze(eth_position(ce, broker_state="BROKER_STATE_UNKNOWN",
                                   protection_status="UNKNOWN_BROKER_STATE",
                                   broker_schutz=False))

    engine.pruefe_positionen()
    zaehler_setzen(engine, "ETH", first_present_at=vor(121))
    engine.pruefe_positionen()

    position = engine.buch.hole("ETH")
    assert position.broker_state == "", "Die Position muss wieder frei sein"
    assert position.menge == pytest.approx(ETH_MENGE)
    assert position.consecutive_present_snapshots == 0
    assert any("wieder" in text and "vollstaendig" in text for text in gemeldet), gemeldet


def test_freigabe_glaubt_dem_alten_schutz_nicht(motor):
    """Der Schutz wird neu bestaetigt, nicht aus dem Gedaechtnis uebernommen."""
    bauen, _ = motor
    import crypto_engine as ce
    _, engine, broker = bauen([Bestand("ETH", ETH_MENGE, 2131.3)])
    gefragt = []
    echte_pruefung = broker.reconcile_position_protection

    def merken(*a, **k):
        gefragt.append(a)
        return echte_pruefung(*a, **k)

    broker.reconcile_position_protection = merken
    engine.buch.setze(eth_position(ce, broker_state="BROKER_STATE_UNKNOWN",
                                   protection_status="UNKNOWN_BROKER_STATE",
                                   broker_schutz=False))

    engine.pruefe_positionen()
    zaehler_setzen(engine, "ETH", first_present_at=vor(121))
    engine.pruefe_positionen()

    position = engine.buch.hole("ETH")
    assert gefragt, "Der Broker muss nach dem Schutz gefragt worden sein"
    assert position.protection_status == "ACTIVE" and position.broker_schutz is True


def test_ohne_guthaben_schnappschuss_wird_nichts_zurueckgenommen(motor):
    bauen, _ = motor
    import crypto_engine as ce
    _, engine, _ = bauen([Bestand("ETH", ETH_MENGE, 2131.3)], snapshot=False)
    engine.buch.setze(eth_position(ce, broker_state="BROKER_STATE_UNKNOWN",
                                   protection_status="UNKNOWN_BROKER_STATE",
                                   broker_schutz=False))

    engine.pruefe_positionen()
    zaehler_setzen(engine, "ETH", first_present_at=vor(300),
                   consecutive_present_snapshots=5)
    engine.pruefe_positionen()

    assert engine.buch.hole("ETH").broker_state == "BROKER_STATE_UNKNOWN", (
        "Ohne belastbaren Kontoschnappschuss bleibt die Sperre")


def test_teilbestand_gibt_die_position_nicht_frei(motor):
    bauen, _ = motor
    import crypto_engine as ce
    _, engine, _ = bauen([Bestand("ETH", ETH_MENGE / 2, 2131.3)])
    engine.buch.setze(eth_position(ce, broker_state="BROKER_STATE_UNKNOWN",
                                   protection_status="UNKNOWN_BROKER_STATE",
                                   broker_schutz=False))

    engine.pruefe_positionen()
    zaehler_setzen(engine, "ETH", first_present_at=vor(300),
                   consecutive_present_snapshots=5)
    engine.pruefe_positionen()

    assert engine.buch.hole("ETH").broker_state == "BROKER_STATE_UNKNOWN"


def test_entsperren_dauert_laenger_als_sperren(motor):
    """Beide Fristen sind konfigurierbar; die Rueckgabe ist die vorsichtigere."""
    import config
    assert (float(config.OKX_POSITION_RESTORED_CONFIRM_SECONDS)
            > float(config.OKX_POSITION_MISSING_CONFIRM_SECONDS))


# ---------------------------------------------------------------------------
# 3. Ledgerseite: die Bestandsluecke selbst schliessen
# ---------------------------------------------------------------------------
# Das Fixture legt eine echte OKX-Ledgerzeile mit vollstaendiger Kontokette an
# (Konto 'A', DEMO, SUI-USDC, Einstiegsorder '100').
from test_v975_execution_and_repair import engine, persist  # noqa: E402,F401


@pytest.fixture
def geluecktes_konto(engine):
    """Eine offene Ledgerzeile mit gemeldeter Bestandsluecke -- wie am 18.09."""
    import okx_accounting as buchung
    import trade_ledger as tl
    position, tid = persist(engine)
    row = tl.trade_detail(tid)
    buchung.mark_balance_gap(row, 0.0, row["menge"])
    tl.set_reconciliation_status(tid, "BROKER_STATE_UNKNOWN",
                                 notiz="Bestand fehlt ohne Verkaufsbeleg")
    return {"row": tl.trade_detail(tid), "tid": tid, "menge": row["menge"],
            "buchung": buchung, "tl": tl}


def test_gemeldete_luecke_sperrt_den_coin_nicht_die_domaene(geluecktes_konto):
    buchung = geluecktes_konto["buchung"]
    zustand = buchung.status("A", "DEMO")
    arten = {gap["kind"] for gap in zustand["gaps"]}
    # 10.7.0: Reichweite SYMBOL -- nur SUI ist gesperrt, andere Kaeufe frei.
    assert zustand["complete"] and zustand["blocked_symbols"] == ["SUI"]
    assert {"BALANCE_REDUCTION", "BROKER_STATE_UNKNOWN"} <= arten
    assert all(g["scope"] == "SYMBOL" and g["ablauf"] == "BELEG" for g in zustand["gaps"])


def test_wiedergekehrter_bestand_loest_die_luecke_und_gibt_kaeufe_frei(geluecktes_konto):
    buchung, tl = geluecktes_konto["buchung"], geluecktes_konto["tl"]
    menge = geluecktes_konto["menge"]

    assert buchung.resolve_balance_gap_restored(
        geluecktes_konto["row"], menge, detail="snapshots=2")
    tl.set_reconciliation_status(geluecktes_konto["tid"], "CONFIRMED_OPEN",
                                 notiz="Bestand wieder vollstaendig")

    zustand = buchung.status("A", "DEMO")
    assert zustand["complete"] and not zustand["blocked_symbols"], f"Kaeufe muessen wieder frei sein: {zustand}"


def test_die_aufloesung_bleibt_nachlesbar(geluecktes_konto):
    from contextlib import closing
    buchung, tl = geluecktes_konto["buchung"], geluecktes_konto["tl"]
    buchung.resolve_balance_gap_restored(geluecktes_konto["row"],
                                         geluecktes_konto["menge"])
    with closing(tl._connect()) as con:
        gap = con.execute("SELECT * FROM okx_balance_gaps WHERE trade_id=?",
                          (geluecktes_konto["tid"],)).fetchone()
    assert gap["status"] == "RESOLVED"
    assert gap["resolution"].startswith("BALANCE_RESTORED:")
    assert str(geluecktes_konto["menge"]) in gap["resolution"]


def test_ein_teilbestand_loest_nichts_auf(geluecktes_konto):
    from okx_receipt_math import EvidenceError
    buchung = geluecktes_konto["buchung"]
    with pytest.raises(EvidenceError):
        buchung.resolve_balance_gap_restored(geluecktes_konto["row"],
                                             geluecktes_konto["menge"] / 2)
    assert "SUI" in buchung.status("A", "DEMO")["blocked_symbols"]


def test_eine_geschlossene_zeile_wird_nicht_wiederbelebt(geluecktes_konto):
    from okx_receipt_math import EvidenceError
    buchung, tl = geluecktes_konto["buchung"], geluecktes_konto["tl"]
    from test_v975_execution_and_repair import close_ledger
    close_ledger(None, geluecktes_konto["tid"], geluecktes_konto["menge"])
    with pytest.raises(EvidenceError):
        buchung.resolve_balance_gap_restored(geluecktes_konto["row"],
                                             geluecktes_konto["menge"])


def test_fremde_kontodomaene_wird_abgewiesen(geluecktes_konto):
    from okx_receipt_math import EvidenceError
    buchung = geluecktes_konto["buchung"]
    fremd = dict(geluecktes_konto["row"])
    fremd["broker_account_fingerprint"] = "fremdes-konto"
    with pytest.raises(EvidenceError):
        buchung.resolve_balance_gap_restored(fremd, geluecktes_konto["menge"])
