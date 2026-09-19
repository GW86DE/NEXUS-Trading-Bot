"""10.8.1 -- Restzeilen, Schnappschuss und Kerzencursor.

BEFUND VOM 19.09.2026 (Diagnosepaket 12:01 UTC)
===============================================
Um 05:20:10 UTC war der OKX-Guthabenstand vier Minuten lang nicht lesbar.
Der Positionsabgleich lief mit LEEREM Schnappschuss weiter: ``positionen()``
lieferte eine leere Liste, und die offene XRP-Restzeile 85 (0,0000532 XRP,
Lot-Rest des Take-Profit-Verkaufs vom 18.09.) bekam SOFORT einen
Bestandsbeleg mit ``observed_balance = "0"``. Positionen im Buch schuetzt
seit 10.6.0 die Zwei-Messungen-Regel -- Ledgerzeilen ohne Position schuetzte
nichts. Ab 05:24 wies das Konto wieder 50.000,0000532 XRP aus; der Beleg
blieb trotzdem offen, weil es fuer Restzeilen keinen Rueckweg gab, und die
Staubregel den Rest am GESAMTBESTAND (50.000 XRP = 115.000 EUR) statt an der
Botmenge (ein Zehntel Cent) mass. XRP war bis zum Abend gesperrt.

Nebenbefunde derselben Diagnose:
  * Nach dem ETH-Verkauf von Trade 90 (10:21 UTC) hielt die aeltere
    ETH-Restzeile 86 denselben registrierten EXIT fuer ihren eigenen und
    lief in jedem Takt in ``LedgerZuordnungUnklar`` -- mit Traceback.
  * XRP wurde 1055-mal in 2,5 Stunden abgelehnt (alle 8 s), weil eine Sperre
    VOR der Signalpruefung den Kerzencursor nicht setzte. Das Journal war
    833 MB gross.

SECHS KORREKTUREN, KEIN UMBAU
-----------------------------
1. Ein nicht lesbarer Schnappschuss ist keine Messung: Abbruch des Takts.
2. Zwei-Messungen-Regel auch fuer Ledgerzeilen ohne Position.
3. Rueckweg "Bestand wieder da" fuer Ledgerzeilen (loest Trade 85).
4. Staubregel auf die Restmenge der Zeile, nicht auf den Kontobestand.
5. Fill-Historie nur in der eigenen Abstammungslinie, Ablehnung gemerkt.
6. Eine Entscheidung je Kerze; JSONL-Spiegel rotiert und wird migriert.
"""
from __future__ import annotations

import json
import logging
from contextlib import closing
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS

import pytest

import trade_ledger as tl
import okx_accounting as gate
from broker.base import BrokerFehler
from test_v975_execution_and_repair import engine, persist, close_ledger  # noqa: F401
from test_v987_closed_results import closed  # noqa: F401
from test_v988_accounting import split, rules, add_quote  # noqa: F401

KONTO = "244895ca0a404f80142b79f5"


def vor(sekunden: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=sekunden)).isoformat()


def _gap_row(tid):
    with closing(tl._connect()) as con:
        return con.execute("SELECT * FROM okx_balance_gaps WHERE trade_id=?", (tid,)).fetchone()


def _bestand(menge, preis):
    return NS(quantity=menge, market_price=preis)


# ===========================================================================
# 1. Ein nicht lesbarer Schnappschuss ist keine Messung
# ===========================================================================
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

    def __init__(self, bestaende, *, schnappschuss_fehler=None, positionen_fehler=None):
        from broker.okx import OKXInstrument
        self._bestaende = list(bestaende)
        self._schnappschuss_fehler = schnappschuss_fehler
        self._positionen_fehler = positionen_fehler
        self.schnappschuesse = 0
        meta = OKXInstrument("ETH-EUR", "ETH", "EUR", "live",
                             "0.01", "0.00000001", "0.0001")

        class Client:
            hat_zugangsdaten = True
            def instrument(self, _i): return meta
        self.client = Client()

    def is_connected(self): return True
    def account_fingerprint(self): return KONTO
    def positionen(self, **_k):
        if self._positionen_fehler:
            raise BrokerFehler(self._positionen_fehler)
        return list(self._bestaende)
    def guthaben_schnappschuss(self):
        self.schnappschuesse += 1
        if self._schnappschuss_fehler:
            raise BrokerFehler(self._schnappschuss_fehler)
        return {b.symbol: {"frei": 0.0, "gesamt": b.quantity} for b in self._bestaende}
    def historical_fills(self, *_a, **_k): return []
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

    def bauen(bestaende, **broker_felder):
        broker = KontoBroker(bestaende, **broker_felder)

        class Hub:
            def broker(self, name): return broker if name == "okx" else None
            def verbinde(self, _n): return True
            def zustaende(self): return {}

        motor = ce.CryptoEngine(
            hub=Hub(), risiko=RiskPotManager(["etoro", "okx"]),
            universum=UniverseManager(UniverseZustand(tmp_path / "u.json")),
            melder=lambda text, **k: gemeldet.append(text))
        motor.buch = ce.KryptoPositionsbuch(tmp_path / "p.json")
        return ce, motor, broker

    return bauen, gemeldet


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
        account_fingerprint=KONTO, paper=True,
        decision_id=3764847086281095168)
    werte.update(felder)
    return ce.KryptoPosition(**werte)


def _xrp_restzeile(paper=True):
    """Die Restzeile 85 der Nacht vom 18.09.2026, ohne Position im Buch."""
    return tl.trade_open(
        broker="okx", symbol="XRP", menge=0.0000532, einstieg_preis=2.2857,
        asset_type="crypto", waehrung="EUR", paper=paper,
        broker_position_id="XRP-EUR", entry_order_id="3932009456893034496",
        broker_account_fingerprint=KONTO, decision_id=3932009456893034496,
        zeit="2026-09-17T22:50:00+00:00", critical=True,
        notiz="Rest nach Teilverkauf (Lot-Rest)")


def test_nicht_lesbarer_schnappschuss_bricht_den_takt_ohne_beleg_ab(motor):
    """Der Ablauf vom 19.09.2026, 05:20:10 UTC."""
    bauen, gemeldet = motor
    import crypto_engine as ce
    _, motor_, broker = bauen([], schnappschuss_fehler="HTTP 503 -- OKX nicht erreichbar")
    motor_.buch.setze(eth_position(ce))
    tid = _xrp_restzeile()
    aufrufe = []
    motor_._offene_ledger_abgleichen = lambda bestaende: aufrufe.append(bestaende) or {"ok": True}

    bericht = motor_.pruefe_positionen()

    assert bericht["ok"] is False and bericht.get("abgebrochen") is True
    assert "OKX_BALANCE_SNAPSHOT_UNAVAILABLE" in bericht["diagnostic_errors"]
    assert aufrufe == [], "Ohne Messung gibt es keinen Ledgerabgleich"
    position = motor_.buch.hole("ETH")
    assert position.consecutive_missing_snapshots == 0 and position.broker_state == "", (
        "Ein nicht lesbarer Schnappschuss ist keine Fehlmessung")
    assert _gap_row(tid) is None, "Kein Bestandsbeleg aus einem ungueltigen Schnappschuss"
    assert tl.trade_detail(tid)["reconciliation_status"] != "BROKER_STATE_UNKNOWN"
    assert gate.status(KONTO, "DEMO")["complete"] and not gate.status(KONTO, "DEMO")["blocked_symbols"]
    bedingung = motor_.bereitschaft.bedingungen["reconciliation"]
    assert bedingung.erfuellt is False and "abgebrochen" in bedingung.detail


def test_nicht_abrufbare_bestaende_brechen_ebenso_ab(motor):
    bauen, _ = motor
    import crypto_engine as ce
    _, motor_, _ = bauen([Bestand("ETH", ETH_MENGE, 2131.3)],
                         positionen_fehler="Ticker nicht lesbar")
    motor_.buch.setze(eth_position(ce))
    bericht = motor_.pruefe_positionen()
    assert bericht["ok"] is False and bericht.get("abgebrochen") is True
    assert "OKX_POSITIONS_UNAVAILABLE" in bericht["diagnostic_errors"]
    assert motor_.bereitschaft.bedingungen["reconciliation"].erfuellt is False


def test_vollstaendiger_takt_gibt_die_bereitschaft_wieder_frei(motor):
    bauen, _ = motor
    import crypto_engine as ce
    _, motor_, broker = bauen([Bestand("ETH", ETH_MENGE, 2131.3)],
                              schnappschuss_fehler="kurzer Ausfall")
    motor_.buch.setze(eth_position(ce))
    assert motor_.pruefe_positionen()["ok"] is False
    broker._schnappschuss_fehler = None
    bericht = motor_.pruefe_positionen()
    assert bericht["ok"] is True and not bericht.get("abgebrochen")
    bedingung = motor_.bereitschaft.bedingungen["reconciliation"]
    assert "abgebrochen" not in bedingung.detail and "geprueft" in bedingung.detail
    assert motor_.buch.hole("ETH").consecutive_missing_snapshots == 0


def test_leerer_aber_gueltiger_schnappschuss_bleibt_eine_messung(motor):
    """10.6.0-Verhalten unveraendert: ein leeres Konto ist eine (erste) Messung."""
    bauen, _ = motor
    import crypto_engine as ce
    _, motor_, _ = bauen([])
    motor_.buch.setze(eth_position(ce))
    bericht = motor_.pruefe_positionen()
    assert bericht["ok"] is True
    assert motor_.buch.hole("ETH").consecutive_missing_snapshots == 1


# ===========================================================================
# 2. Zwei Messungen auch fuer Ledgerzeilen ohne Position
# ===========================================================================
def _ledgerzeile_ohne_position(engine):
    position, tid = persist(engine)
    engine.buch.entferne(position.symbol)
    engine.broker.historical_fills = lambda *a, **kw: []
    return tid


def test_erste_fehlmessung_einer_ledgerzeile_bucht_keinen_beleg(engine):
    tid = _ledgerzeile_ohne_position(engine)
    bericht = engine._offene_ledger_abgleichen({})
    eintrag = next(r for r in bericht["residual"] if r["trade_id"] == tid)
    assert eintrag["status"] == "BESTAND_FEHLT_UNBESTAETIGT" and eintrag["messungen"] == 1
    assert _gap_row(tid) is None, "Eine Messung ist kein Beweis"
    assert tl.trade_detail(tid)["reconciliation_status"] != "BROKER_STATE_UNKNOWN"
    assert gate.status("A", "DEMO")["complete"] and not gate.status("A", "DEMO")["blocked_symbols"]
    assert engine._fehlender_ledger_bestand[tid][0] == 1


def test_zwei_fehlmessungen_in_neun_sekunden_buchen_nichts(engine):
    tid = _ledgerzeile_ohne_position(engine)
    engine._offene_ledger_abgleichen({})
    engine._fehlender_ledger_bestand[tid] = (1, vor(9))
    bericht = engine._offene_ledger_abgleichen({})
    eintrag = next(r for r in bericht["residual"] if r["trade_id"] == tid)
    assert eintrag["status"] == "BESTAND_FEHLT_UNBESTAETIGT" and eintrag["messungen"] == 2
    assert _gap_row(tid) is None, "Neun Sekunden sind dieselbe Stoerung"


def test_zwei_fehlmessungen_mit_mindestabstand_buchen_den_beleg(engine):
    tid = _ledgerzeile_ohne_position(engine)
    engine._offene_ledger_abgleichen({})
    engine._fehlender_ledger_bestand[tid] = (1, vor(31))
    bericht = engine._offene_ledger_abgleichen({})
    eintrag = next(r for r in bericht["residual"] if r["trade_id"] == tid)
    assert eintrag["status"] == "BROKER_STATE_UNKNOWN"
    assert _gap_row(tid)["status"] == "PENDING"
    assert tl.trade_detail(tid)["reconciliation_status"] == "BROKER_STATE_UNKNOWN"
    assert tl.trade_detail(tid)["ausgestiegen_am"] is None, "Fehlender Bestand ist kein Verkauf"
    assert "SUI" in gate.status("A", "DEMO")["blocked_symbols"]


def test_wiedergesehener_bestand_setzt_den_fehlzaehler_zurueck(engine):
    tid = _ledgerzeile_ohne_position(engine)
    engine._offene_ledger_abgleichen({})
    assert tid in engine._fehlender_ledger_bestand
    bericht = engine._offene_ledger_abgleichen({"SUI": _bestand(124.771765, 0.83)})
    eintrag = next(r for r in bericht["residual"] if r["trade_id"] == tid)
    assert eintrag["status"] == "RESIDUAL_EXPOSURE"
    assert tid not in engine._fehlender_ledger_bestand
    assert _gap_row(tid) is None


def test_frist_null_verlangt_weiterhin_zwei_messungen(engine):
    tid = _ledgerzeile_ohne_position(engine)
    engine.cfg.OKX_POSITION_MISSING_CONFIRM_SECONDS = 0
    engine._offene_ledger_abgleichen({})
    assert _gap_row(tid) is None
    engine._offene_ledger_abgleichen({})
    assert _gap_row(tid)["status"] == "PENDING"


# ===========================================================================
# 3./4. Rueckweg fuer Restzeilen und Staubregel auf die Restmenge
# ===========================================================================
def _restzeile_wie_85(split, engine):
    """Restzeile mit offenem Beleg, Position nicht im Buch, Kopf sauber verkauft."""
    import okx_closed_reconciliation as cr
    x = split
    cr.apply(x["row"], cr.prepare(x["row"], x["broker"]))
    with tl._LOCK, closing(tl._connect()) as con, con:
        con.execute("UPDATE trades SET ausgestiegen_am=NULL, reconciliation_status='RESIDUAL_EXPOSURE' "
                    "WHERE trade_id=?", (x["rest_id"],))
    rest = tl.trade_detail(x["rest_id"])
    gate.mark_balance_gap(rest, 0.0, rest["menge"])
    assert _gap_row(x["rest_id"])["status"] == "PENDING"
    assert "SUI" in gate.status("A", "DEMO")["blocked_symbols"]
    engine.buch.entferne("SUI")
    from broker.okx import OKXInstrument
    x["broker"].client.instrument = lambda _i: OKXInstrument(
        "SUI-USDC", "SUI", "USDC", lot_size=".01", min_size=".01", trade_quote_ccy_list=("USDC",))
    engine.hub = NS(broker=lambda _: x["broker"])
    return x["rest_id"], float(rest["menge"])


def test_restzeile_mit_beleg_heilt_nach_zwei_bestaetigten_messungen(split, engine):
    """Trade 85 am 19.09.2026: 50.000,0000532 XRP im Konto, Rest 0,0000532."""
    rest_id, menge = _restzeile_wie_85(split, engine)
    fremdbestand = _bestand(5758.70, 0.83)          # Botmenge 0,001765 -> 0,15 Cent

    erster = engine._offene_ledger_abgleichen({"SUI": fremdbestand})
    eintrag = next(r for r in erster["residual"] if r["trade_id"] == rest_id)
    assert eintrag["status"] == "RESIDUAL_EXPOSURE" and eintrag["bestandsbeleg"] == "PENDING"
    assert erster["geschlossen"] == [], "Erst der bestaetigte Rueckweg, dann der Staubabschluss"
    assert _gap_row(rest_id)["status"] == "PENDING"
    assert tl.trade_detail(rest_id)["ausgestiegen_am"] is None
    assert engine._wiedergesehener_ledger_bestand[rest_id][0] == 1

    engine._wiedergesehener_ledger_bestand[rest_id] = (1, vor(121))
    zweiter = engine._offene_ledger_abgleichen({"SUI": fremdbestand})

    beleg = _gap_row(rest_id)
    assert beleg["status"] == "RESOLVED" and beleg["resolution"].startswith("BALANCE_RESTORED:")
    assert "ledgerzeile" in beleg["resolution"]
    assert zweiter["geschlossen"] == [rest_id], "Die Restmenge ist Staub und wird belegt abgeschlossen"
    rest = tl.trade_detail(rest_id)
    assert rest["accounting_kind"] == "RESIDUAL" and rest["ausgestiegen_am"]
    assert rest["ausstieg_preis"] is None and rest["netto_pnl"] is None, "Kein erfundener Verkauf"
    zustand = gate.status("A", "DEMO")
    assert zustand["complete"] and not zustand["blocked_symbols"], "SUI ist wieder frei"
    assert any("Bestandsbeleg der Ledgerzeile" in m and "wieder frei" in m for m in engine.messages)


def test_rueckweg_in_neun_sekunden_loest_nichts(split, engine):
    rest_id, _ = _restzeile_wie_85(split, engine)
    fremdbestand = _bestand(5758.70, 0.83)
    engine._offene_ledger_abgleichen({"SUI": fremdbestand})
    engine._wiedergesehener_ledger_bestand[rest_id] = (1, vor(9))
    engine._offene_ledger_abgleichen({"SUI": fremdbestand})
    assert _gap_row(rest_id)["status"] == "PENDING"
    assert tl.trade_detail(rest_id)["ausgestiegen_am"] is None


def test_rueckweg_verlangt_die_volle_botmenge(split, engine):
    rest_id, menge = _restzeile_wie_85(split, engine)
    zu_wenig = _bestand(menge / 2, 0.83)
    engine._offene_ledger_abgleichen({"SUI": zu_wenig})
    engine._wiedergesehener_ledger_bestand[rest_id] = (1, vor(121))
    engine._offene_ledger_abgleichen({"SUI": zu_wenig})
    assert _gap_row(rest_id)["status"] == "PENDING", "Halber Bestand ist keine Rueckkehr"
    assert tl.trade_detail(rest_id)["ausgestiegen_am"] is None
    assert rest_id not in engine._wiedergesehener_ledger_bestand


def test_staubregel_misst_die_restmenge_nicht_den_kontobestand(split, engine):
    """Ohne offenen Beleg: der Rest wird trotz 5.758 SUI Fremdbestand als Staub belegt."""
    rest_id, _ = _restzeile_wie_85(split, engine)
    with tl._LOCK, closing(tl._connect()) as con, con:
        con.execute("DELETE FROM okx_balance_gaps WHERE trade_id=?", (rest_id,))
    bericht = engine._offene_ledger_abgleichen({"SUI": _bestand(5758.70, 0.83)})
    assert bericht["geschlossen"] == [rest_id]
    assert tl.trade_detail(rest_id)["accounting_kind"] == "RESIDUAL"


def test_verkaeuflicher_rest_bleibt_exposure_und_heilt_nur_den_beleg(engine):
    """Ein Rest ueber der Staubgrenze wird nie automatisch geschlossen."""
    position, tid = persist(engine)
    close_ledger(position, tid, 120.0)                 # Rest 4,771765 SUI = 3,96 USDC
    rest = next(r for r in tl.offene_trades("okx") if r["trade_id"] != tid)
    gate.mark_balance_gap(rest, 0.0, rest["menge"])
    assert _gap_row(rest["trade_id"])["status"] == "PENDING"
    engine.buch.entferne("SUI")
    engine.broker.historical_fills = lambda *a, **kw: []
    bestand = _bestand(4.771765, 0.83)
    engine._offene_ledger_abgleichen({"SUI": bestand})
    engine._wiedergesehener_ledger_bestand[rest["trade_id"]] = (1, vor(121))
    bericht = engine._offene_ledger_abgleichen({"SUI": bestand})
    eintrag = next(r for r in bericht["residual"] if r["trade_id"] == rest["trade_id"])
    assert eintrag["status"] == "RESIDUAL_EXPOSURE" and "bestandsbeleg" not in eintrag
    assert bericht["geschlossen"] == []
    assert _gap_row(rest["trade_id"])["status"] == "RESOLVED"
    assert tl.trade_detail(rest["trade_id"])["ausgestiegen_am"] is None, "Nie automatisch verkauft"


def test_pending_balance_gap_liest_nur(engine):
    position, tid = persist(engine)
    row = tl.trade_detail(tid)
    assert gate.pending_balance_gap(row) is None
    gate.mark_balance_gap(row, 0.0, row["menge"])
    beleg = gate.pending_balance_gap(row)
    assert beleg and beleg["trade_id"] == tid and beleg["status"] == "PENDING"
    assert gate.pending_balance_gap({"trade_id": 0}) is None
    assert gate.pending_balance_gap({}) is None


# ===========================================================================
# 5. Fill-Historie nur in der eigenen Abstammungslinie
# ===========================================================================
def _eth_lage(engine):
    """Trade 90 verkauft (Exit 200 gebucht), aeltere Restzeile derselben Waehrung offen."""
    position, tid = persist(engine)
    close_ledger(position, tid, 124.771765)
    rest_id = tl.trade_open(
        broker="okx", symbol="SUI", menge=0.001, einstieg_preis=0.78, asset_type="crypto",
        waehrung="USDC", paper=True, broker_position_id="SUI-USDC", entry_order_id="90",
        broker_account_fingerprint="A", decision_id=90, zeit="2026-09-01T08:00:00+00:00",
        critical=True, notiz="Rest nach Teilverkauf (Lot-Rest)")
    engine.buch.entferne("SUI")
    engine.broker.historical_fills = lambda *a, **kw: [
        dict(instId="SUI-USDC", ordId="200", tradeId="1", side="sell", fillSz="124.771765",
             fillPx=".83", fee="-.1", feeCcy="USDC", fillTime="1789027200000")]
    return tid, rest_id


def test_exit_einer_anderen_linie_wird_nicht_auf_die_restzeile_gebucht(engine, caplog):
    tid, rest_id = _eth_lage(engine)
    engine._order_registry().register_orders(
        ["200"], "SUI", {"broker": "okx", "asset_type": "crypto", "role": "EXIT",
                         "entry_order_id": "100", "decision_id": 123,
                         "account_fingerprint": "A"}, asset_type="crypto")
    with caplog.at_level(logging.WARNING):
        bericht = engine._offene_ledger_abgleichen({"SUI": _bestand(0.001, 0.83)})
    assert bericht["history_recovered"] == []
    assert tl.trade_detail(rest_id)["ausgestiegen_am"] is None
    assert not [r for r in caplog.records if "Historienfill" in r.getMessage()]


def test_bereits_gebundener_fill_wird_vor_der_buchung_ausgeschlossen(engine, caplog):
    """Altregistrierung ohne Linienangabe: der Fill gehoert laut Ledger Trade 90."""
    tid, rest_id = _eth_lage(engine)
    engine._order_registry().register_orders(
        ["200"], "SUI", {"broker": "okx", "asset_type": "crypto", "role": "EXIT",
                         "account_fingerprint": "A"}, asset_type="crypto")
    assert tl.gebuchte_exit_fills(broker="okx", account="A", instrument="SUI-USDC",
                                  fill_ids=["okx:A:SUI-USDC:200:1"]) == {"okx:A:SUI-USDC:200:1": tid}
    with caplog.at_level(logging.WARNING):
        bericht = engine._offene_ledger_abgleichen({"SUI": _bestand(0.001, 0.83)})
    assert bericht["history_recovered"] == []
    assert tl.trade_detail(rest_id)["ausgestiegen_am"] is None
    assert not [r for r in caplog.records if "Historienfill" in r.getMessage()]


def test_abgelehnte_historienbuchung_wird_gemerkt_und_nur_einmal_gewarnt(engine, caplog, monkeypatch):
    tid, rest_id = _eth_lage(engine)
    engine.broker.historical_fills = lambda *a, **kw: [
        dict(instId="SUI-USDC", ordId="200", tradeId="2", side="sell", fillSz="0.001",
             fillPx=".83", fee="-.0001", feeCcy="USDC", fillTime="1789027200000")]
    engine._order_registry().register_orders(
        ["200"], "SUI", {"broker": "okx", "asset_type": "crypto", "role": "EXIT",
                         "account_fingerprint": "A"}, asset_type="crypto")
    versuche = []

    def ablehnen(**kw):
        versuche.append(kw.get("trade_id"))
        raise tl.LedgerZuordnungUnklar("Broker-Exitanker gehoert zu einer anderen expliziten trade_id")
    monkeypatch.setattr(tl, "trade_close", ablehnen)

    with caplog.at_level(logging.WARNING):
        engine._offene_ledger_abgleichen({"SUI": _bestand(0.001, 0.83)})
        engine._offene_ledger_abgleichen({"SUI": _bestand(0.001, 0.83)})
    assert versuche == [rest_id], "Nach der Ablehnung kein zweiter Versuch"
    assert (rest_id, "200") in engine._historie_abgelehnt
    warnungen = [r for r in caplog.records if "abgelehnt" in r.getMessage()]
    assert len(warnungen) == 1 and warnungen[0].exc_info is None, "Einmal, ohne Traceback"


def test_gebuchte_exit_fills_ohne_ids_oder_anker(engine):
    assert tl.gebuchte_exit_fills(broker="okx", account="A", instrument="SUI-USDC", fill_ids=[]) == {}
    assert tl.gebuchte_exit_fills(broker="okx", account="A", instrument="", fill_ids=["x"]) == {}


# ===========================================================================
# 6. Eine Entscheidung je Kerze; Journal-Spiegel rotiert
# ===========================================================================
def test_abschluss_liefert_entscheidungs_id_und_vor_signal(engine):
    from decision_source import Entscheidungsprotokoll, RISK_GATE
    protokoll = Entscheidungsprotokoll("XRP", asset_type="crypto", broker="okx")
    protokoll.blockiert(RISK_GATE, "XRP: offener Bestands-/Buchungsbeleg")
    ergebnis = engine._abschluss(protokoll, "ABGELEHNT", "offener Beleg")
    assert ergebnis["gekauft"] is False and ergebnis["decision_id"]
    assert ergebnis["vor_signal"] is True

    mit_kerze = Entscheidungsprotokoll("XRP", asset_type="crypto", broker="okx")
    mit_kerze.messwerte(candle_timestamps={"5m": "2026-09-19T10:00:00+00:00"})
    mit_kerze.blockiert(RISK_GATE, "Spread zu gross")
    ergebnis = engine._abschluss(mit_kerze, "ABGELEHNT", "Spread")
    assert ergebnis["vor_signal"] is False


def _scan_vorbereiten(engine, monkeypatch):
    import crypto_engine as ce
    import crypto_strategy_mode as csm
    from freqtrade_candles import candle_cutoff
    monkeypatch.setattr(csm, "current_mode", lambda: csm.FREQTRADE_SAMPLE)
    engine.risiko = NS(topf=engine.risiko.topf, darf_kaufen=lambda _d: (True, ""))
    engine.universum = NS(handelbare_symbole=lambda _b: ["XRP", "SOL"])
    engine._verwaiste_schutz = []
    engine._schutzorders_unlesbar = ""
    engine._scan_candle_cutoff = candle_cutoff()
    engine.cfg.OKX_DEMO = True
    return ce


def test_vor_signal_sperre_setzt_den_kerzencursor(engine, monkeypatch):
    """XRP am 19.09.2026: 1055 Ablehnungen in 2,5 h -> eine je Kerze."""
    _scan_vorbereiten(engine, monkeypatch)
    aufrufe = []

    def gesperrt(symbol, **_k):
        aufrufe.append(symbol)
        return {"gekauft": False, "grund": f"{symbol}: offener Bestandsbeleg",
                "protokoll": {}, "decision_id": 500 + len(aufrufe), "vor_signal": True}
    engine.pruefe_kandidat = gesperrt

    erster = engine.scan()
    assert erster["gescannt"] == 2 and set(erster["abgelehnt"]) == {"XRP", "SOL"}
    cursor = engine._freqtrade_cursor()
    assert cursor.seen("XRP", engine._scan_candle_cutoff - timedelta(minutes=5))
    zweiter = engine.scan()
    assert zweiter["gescannt"] == 0 and sorted(aufrufe) == ["SOL", "XRP"], (
        "Dieselbe Kerze wird nicht ein zweites Mal entschieden")


def test_sperre_nach_der_signalpruefung_setzt_keinen_cursor(engine, monkeypatch):
    _scan_vorbereiten(engine, monkeypatch)
    engine.pruefe_kandidat = lambda symbol, **_k: {
        "gekauft": False, "grund": "Spread", "protokoll": {}, "decision_id": 7, "vor_signal": False}
    engine.scan()
    assert not engine._freqtrade_cursor().seen("XRP", engine._scan_candle_cutoff - timedelta(minutes=5))


def test_ohne_entscheidungs_id_kein_cursor(engine, monkeypatch):
    _scan_vorbereiten(engine, monkeypatch)
    engine.pruefe_kandidat = lambda symbol, **_k: {
        "gekauft": False, "grund": "x", "protokoll": {}, "decision_id": None, "vor_signal": True}
    engine.scan()
    assert not engine._freqtrade_cursor().seen("XRP", engine._scan_candle_cutoff - timedelta(minutes=5))


def test_journal_spiegel_rotiert_ab_der_obergrenze(tmp_path, monkeypatch):
    import config
    import decision_journal as journal
    spiegel = tmp_path / "decision_journal.jsonl"
    monkeypatch.setattr(journal, "PATH", spiegel)
    monkeypatch.setattr(config, "DECISION_JOURNAL_MAX_MB", 1, raising=False)
    assert journal.max_bytes() == 1024 * 1024
    spiegel.write_bytes(b'{"alt": true}\n' * 80_000)          # ~1,1 MB
    assert spiegel.stat().st_size > journal.max_bytes()

    entscheidung = journal.record_decision(
        symbol="XRP", asset_type="crypto", broker="okx", status="BLOCKED",
        reason="Test", paper=True)
    assert entscheidung
    rotiert = spiegel.with_suffix(".1.jsonl")
    assert rotiert.exists() and rotiert.stat().st_size > journal.max_bytes()
    zeilen = spiegel.read_text(encoding="utf-8").splitlines()
    assert len(zeilen) == 1 and json.loads(zeilen[0])["symbol"] == "XRP"


def test_journal_obergrenze_hat_ein_minimum(monkeypatch):
    import config
    import decision_journal as journal
    monkeypatch.setattr(config, "DECISION_JOURNAL_MAX_MB", 0.001, raising=False)
    assert journal.max_bytes() == 1024 * 1024
    monkeypatch.setattr(config, "DECISION_JOURNAL_MAX_MB", "kaputt", raising=False)
    assert journal.max_bytes() == 20 * 1024 * 1024


def _grosser_spiegel():
    zeilen = [json.dumps({"n": i, "fuellung": "x" * 40}) for i in range(30_000)]
    return ("\n".join(zeilen) + "\n").encode("utf-8")           # ~1,9 MB


def test_migration_kuerzt_den_spiegel_auf_ganze_zeilen(tmp_path, monkeypatch):
    import config
    import settings_migration as sm
    monkeypatch.setattr(config, "DECISION_JOURNAL_MAX_MB", 1, raising=False)
    quelle = tmp_path / "alt"; ziel = tmp_path / "neu"
    quelle.mkdir(); ziel.mkdir()
    inhalt = _grosser_spiegel()
    (quelle / "decision_journal.jsonl").write_bytes(inhalt)
    (ziel / "decision_journal.jsonl").write_bytes(inhalt)      # alter Kopierweg: 1:1
    copied, skipped = [], []

    sm._migrate_v1081_journal_kompakt(quelle, ziel, copied, skipped)

    gekuerzt = (ziel / "decision_journal.jsonl").read_bytes()
    assert len(gekuerzt) <= 1024 * 1024 < len(inhalt)
    assert gekuerzt.endswith(b"\n") and inhalt.endswith(gekuerzt)
    erste = json.loads(gekuerzt.split(b"\n", 1)[0])
    assert erste["n"] > 0, "Gekuerzt wird an einer Zeilengrenze"
    assert json.loads(gekuerzt.splitlines()[-1])["n"] == 29_999
    assert (quelle / "decision_journal.jsonl").read_bytes() == inhalt, "Die Quelle bleibt unangetastet"
    assert copied and "gekuerzt" in copied[0] and not skipped

    sm._migrate_v1081_journal_kompakt(quelle, ziel, copied, skipped)
    assert len(copied) == 2 and "uebernommen" in copied[1], "Zweiter Lauf kuerzt nichts mehr, nennt aber die Herkunft"


def test_kopierweg_uebernimmt_nur_den_schwanz_des_spiegels(tmp_path, monkeypatch):
    """Der 833-MB-Fall: die Zieldatei entsteht gar nicht erst in voller Groesse."""
    import config
    import settings_migration as sm
    monkeypatch.setattr(config, "DECISION_JOURNAL_MAX_MB", 1, raising=False)
    quelle = tmp_path / "alt"; ziel = tmp_path / "neu"
    quelle.mkdir(); ziel.mkdir()
    inhalt = _grosser_spiegel()
    (quelle / "decision_journal.jsonl").write_bytes(inhalt)

    sm._kopiere("decision_journal.jsonl", quelle / "decision_journal.jsonl", ziel / "decision_journal.jsonl")

    kopie = (ziel / "decision_journal.jsonl").read_bytes()
    assert len(kopie) <= 1024 * 1024 and inhalt.endswith(kopie) and kopie.startswith(b"{")
    assert json.loads(kopie.splitlines()[-1])["n"] == 29_999
    copied, skipped = [], []
    sm._migrate_v1081_journal_kompakt(quelle, ziel, copied, skipped)
    assert len(copied) == 1 and "uebernommen" in copied[0] and not skipped
    assert (ziel / "decision_journal.jsonl").read_bytes() == kopie

    klein = tmp_path / "klein"; klein.mkdir()
    (klein / "decision_journal.jsonl").write_text('{"n": 1}\n', encoding="utf-8")
    sm._kopiere("decision_journal.jsonl", klein / "decision_journal.jsonl", ziel / "k.jsonl")
    assert (ziel / "k.jsonl").read_text(encoding="utf-8") == '{"n": 1}\n', "Kleine Spiegel 1:1"


def test_migration_laesst_kleine_spiegel_in_ruhe(tmp_path):
    import settings_migration as sm
    quelle = tmp_path / "alt"; ziel = tmp_path / "neu"
    quelle.mkdir(); ziel.mkdir()
    (quelle / "decision_journal.jsonl").write_text('{"n": 1}\n', encoding="utf-8")
    (ziel / "decision_journal.jsonl").write_text('{"n": 1}\n', encoding="utf-8")
    copied, skipped = [], []
    sm._migrate_v1081_journal_kompakt(quelle, ziel, copied, skipped)
    assert (ziel / "decision_journal.jsonl").read_text(encoding="utf-8") == '{"n": 1}\n'
    assert not copied and not skipped
    sm._migrate_v1081_journal_kompakt(quelle, tmp_path / "fehlt", copied, skipped)
    assert not copied and not skipped


def test_migration_ist_verdrahtet_und_diagnose_kennt_die_rotation():
    import inspect
    import settings_migration as sm
    import NEXUS_10_Diagnose as diagnose
    quelle = inspect.getsource(sm.migrate_from)
    assert "_migrate_v1081_journal_kompakt(source, target, copied, skipped)" in quelle
    assert quelle.index("_migrate_v1081_journal_kompakt") < quelle.index("_force_paper(target)")
    assert "decision_journal.jsonl" in sm.PERSISTENT_FILES
    assert "decision_journal.1.jsonl" in diagnose.AUDITS
    assert sm._journal_obergrenze() == 20 * 1024 * 1024


# ===========================================================================
# Pins
# ===========================================================================
def test_konfigurationspins():
    import config
    assert config.DECISION_JOURNAL_MAX_MB == 20
    assert config.OKX_POSITION_MISSING_CONFIRM_SECONDS == 30
    assert config.OKX_POSITION_RESTORED_CONFIRM_SECONDS == 120
    assert config.DECISION_JOURNAL_FILE == "decision_journal.jsonl"


def test_engine_kennt_die_neuen_zaehler(motor):
    bauen, _ = motor
    _, motor_, _ = bauen([])
    assert motor_._fehlender_ledger_bestand == {}
    assert motor_._wiedergesehener_ledger_bestand == {}
    assert motor_._historie_abgelehnt == set()
    assert motor_._rest_abgelehnt == {}
