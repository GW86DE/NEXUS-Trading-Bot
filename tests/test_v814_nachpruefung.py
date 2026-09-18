"""Regressionstests zu den Befunden der unabhaengigen Nachpruefung (v8.1.4).

Jeder Test haelt genau einen Befund fest, damit er nicht zurueckkommt.
"""
from __future__ import annotations

import math
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("TRADINGBOT_TEST_STATE_DIR", "/tmp/tradingbot_tests")
Path(os.environ["TRADINGBOT_TEST_STATE_DIR"]).mkdir(parents=True, exist_ok=True)

WURZEL = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# 1+2 · Fehlgeschlagener Verkauf: Schutz gilt als weg, und es wird gemeldet
# ---------------------------------------------------------------------------
def test_fehlgeschlagener_verkauf_setzt_den_schutz_zurueck():
    """schliesse_position storniert die Schutzorder ZUERST.

    Schlaegt der Verkauf danach fehl, ist die Position ungeschuetzt -- bis
    v8.1.3 blieb broker_schutz aber True, und der einzige
    Wiederherstellungspfad griff nie.
    """
    quelle = (WURZEL / "crypto_engine.py").read_text(encoding="utf-8")
    start = quelle.index("def _schliesse")
    ende = quelle.index("\n    def ", start + 20)
    block = quelle[start:ende]
    vor_post = block[:block.index("broker.schliesse_position")]
    assert 'position.exit_state = "SUBMITTING"' in vor_post
    assert 'position.broker_schutz = False' in vor_post
    assert 'position.protection_status = "LOST"' in vor_post
    assert "self.buch.setze(position)" in vor_post, \
        "Der pessimistische Schutzstatus muss vor dem Broker-POST auf Platte stehen"
    assert block.count("_schutz_als_verloren_merken(position)") >= 2
    assert "def _schutz_als_verloren_merken" in quelle


def test_fehlgeschlagener_verkauf_ist_nicht_stumm():
    quelle = (WURZEL / "crypto_engine.py").read_text(encoding="utf-8")
    start = quelle.index("except BrokerFehler as exc:", quelle.index("def _schliesse"))
    block = quelle[start:start + 900]
    assert "self._exit_fehlversuch(" in block
    helper = quelle[quelle.index("def _exit_fehlversuch"):]
    assert "self._melde(" in helper, "Ein abgelehnter Verkauf muss gemeldet werden"
    assert 'klasse="KRITISCH"' in helper


def test_nicht_ausgefuehrter_verkauf_wird_gemeldet():
    """ERWEITERT IN v9.1: "0 gefuellt" wird jetzt nach Beweislage getrennt.

    Nur ein vom Broker BESTAETIGTER Endzustand (``terminal``) ist ein
    bewiesener Fehlschlag und darf in den kurzen Retry. Bleibt der Zustand
    offen, gilt UNKLAR mit 24 Stunden Sperre -- eine moeglicherweise
    ausgefuehrte Order wird nie blind wiederholt. Beide Zweige melden, und
    beide behandeln den Schutz als verloren.
    """
    quelle = (WURZEL / "crypto_engine.py").read_text(encoding="utf-8")
    stelle = quelle.index('protokoll.abschliessen("NICHT_AUSGEFUEHRT"')
    block = quelle[stelle:quelle.index("# 9.5.8: Ab hier", stelle)]
    assert "_schutz_als_verloren_merken" in block
    assert 'getattr(ergebnis, "terminal", False)' in block,         "Ohne die Beweislage wird eine offene Order faelschlich wiederholt"
    assert 'exit_state = "UNCLEAR"' in block
    assert "hours=24" in block, "Ein unklarer Verkauf braucht die lange Sperre"
    assert "self._exit_fehlversuch(" in block,         "Der bewiesene Fehlschlag behaelt den kurzen Retry"


# ---------------------------------------------------------------------------
# 3+4+5 · Der gemessene Gebuehrensatz wirkt ueberall
# ---------------------------------------------------------------------------
def test_kostenhuerde_nutzt_den_gemessenen_satz():
    """Sonst rechnet die zentrale Netto-Edge-Pruefung mit der Annahme."""
    from cost_engine import estimate_roundtrip
    niedrig = estimate_roundtrip(1.0, 100.0, asset_type="crypto", currency="EUR",
                                 broker="okx", fee_pct=0.0010)
    hoch = estimate_roundtrip(1.0, 100.0, asset_type="crypto", currency="EUR",
                              broker="okx", fee_pct=0.0035)
    assert hoch.required_edge_pct > niedrig.required_edge_pct
    assert hoch.buy_commission == pytest.approx(0.35)


def test_kaufpfad_uebergibt_den_gemessenen_satz():
    quelle = (WURZEL / "crypto_engine.py").read_text(encoding="utf-8")
    stelle = quelle.index("kosten = estimate_roundtrip(")
    block = quelle[stelle:stelle + 400]
    assert "fee_pct=self._taker_satz()" in block


def test_cash_anpassung_nutzt_den_gemessenen_satz():
    quelle = (WURZEL / "crypto_engine.py").read_text(encoding="utf-8")
    assert 'getattr(self.cfg, "OKX_TAKER_FEE_PCT", 0.001)' not in quelle, \
        "0.001 war genau der als falsch erkannte Wert"
    assert "gebuehr_pct = max(0.0, self._taker_satz())" in quelle


def test_gebuehrensatz_faellt_auf_den_hoeheren_wert_zurueck():
    """Ein zu niedrig angenommener Satz macht schlechte Trades gut."""
    quelle = (WURZEL / "broker" / "okx.py").read_text(encoding="utf-8")
    start = quelle.index("def gebuehrensatz")
    block = quelle[start:start + 1400]
    assert block.count("max(wert, angenommen)") >= 3, \
        "Bei Ausfall und Cache muss der konservativere Wert gelten"


# ---------------------------------------------------------------------------
# 6 · Keine Waehrungsmischung im handelbaren Kapital
# ---------------------------------------------------------------------------
def test_handelbares_kapital_mischt_keine_waehrungen():
    quelle = (WURZEL / "broker" / "okx.py").read_text(encoding="utf-8")
    start = quelle.index("def handelbares_kapital")
    block = quelle[start:quelle.index("def kontowaehrung")]
    # 10.1.9 darf mehrere *freigegebene* Cash-Lanes beruecksichtigen,
    # aber nur nach expliziter Bewertung in derselben Risikowaehrung.
    # Ein rohes EUR+USDC-Addieren oder eine Stablecoin-Paritaetsannahme
    # waere weiterhin ein Sicherheitsfehler.
    assert "quote_conversion_rate(currency, basis)" in block
    assert "RISK_CAPITAL_FX_UNKNOWN" in block
    assert "total += value" in block
    assert "unsupported_positive_balance_currencies" in block


def test_kontowaehrung_ist_die_handelswaehrung():
    quelle = (WURZEL / "broker" / "okx.py").read_text(encoding="utf-8")
    start = quelle.index("def kontowaehrung")
    block = quelle[start:start + 300]
    assert 'return "USD"' not in block, \
        "Die Positionsgroesse rechnet in der Handelswaehrung, nicht in USD"


# ---------------------------------------------------------------------------
# 7 · Die Fremdbestandssperre faellt nicht offen aus
# ---------------------------------------------------------------------------
def test_ueberhang_ohne_kurs_bleibt_konto_asset():
    """Ein fehlender Kurs aendert den Eigentumsnachweis nicht."""
    import exposure_klassifizierung as ek
    ergebnis = ek.klassifiziere(
        {"BTC": {"gesamt": 1.0, "cash": 1.0}},
        positionsbuch=[{"symbol": "BTC", "menge": 0.06, "herkunft": "BOT",
                        "verwaltung": "AUTO", "ownership_verified": True,
                        "order_id": "o1", "fill_ids": ["f1"]}],
        preise={},                      # kein Kurs verfuegbar
        cfg=None)
    assert ergebnis["einstiege_gesperrt"] is False
    konto = next(x for x in ergebnis["bestaende"] if x["klasse"] == ek.ACCOUNT_ASSET)
    assert konto["menge"] == pytest.approx(0.94)


# ---------------------------------------------------------------------------
# 8+9 · Die Freigabe gilt genau der gezeigten Order
# ---------------------------------------------------------------------------
def test_groessere_menge_verfaellt_trotz_freigabe():
    """Der Nutzer hat eine bestimmte Menge gesehen."""
    import second_opinion as so
    eintrag = {"fakten": {"preis": 100.0, "menge": 10.0, "wert": 1000.0, "stop": 95.0}}
    gueltig, grund = so.grundlage_noch_gueltig(
        eintrag, preis=100.0, cash=99999, bestand_vorhanden=False,
        risiko_offen=True, menge=20.0)
    assert gueltig is False and "statt der freigegebenen" in grund


def test_gleiche_menge_bleibt_gueltig():
    import second_opinion as so
    eintrag = {"fakten": {"preis": 100.0, "menge": 10.0, "wert": 1000.0, "stop": 95.0}}
    gueltig, _ = so.grundlage_noch_gueltig(
        eintrag, preis=100.0, cash=99999, bestand_vorhanden=False,
        risiko_offen=True, menge=10.0, stop=95.0)
    assert gueltig is True


def test_verschobener_stop_verfaellt():
    import second_opinion as so
    eintrag = {"fakten": {"preis": 100.0, "menge": 10.0, "wert": 1000.0, "stop": 95.0}}
    gueltig, grund = so.grundlage_noch_gueltig(
        eintrag, preis=100.0, cash=99999, bestand_vorhanden=False,
        risiko_offen=True, menge=10.0, stop=90.0)
    assert gueltig is False and "Stop liegt jetzt" in grund


def test_freigabeparameter_haben_obergrenzen(monkeypatch):
    """Eine versehentliche 100 wuerde die Kurspruefung stilllegen."""
    import config
    import second_opinion as so
    monkeypatch.setattr(config, "AI_PENDING_MAX_DRIFT_PCT", 100.0, raising=False)
    monkeypatch.setattr(config, "AI_PENDING_EXPIRY_MINUTES", 99999.0, raising=False)
    assert so.max_kursabweichung() <= 0.05
    assert so.verfallszeit_minuten() <= 240.0


# ---------------------------------------------------------------------------
# 10 · Die Bedingungen messen wirklich etwas
# ---------------------------------------------------------------------------
def test_signalkerze_misst_die_kerzengroesse_nicht_den_timer():
    import config
    from trading_ready import Handelsbereitschaft

    class Uhr:
        def __init__(self): self.t = 0.0
        def __call__(self): return self.t

    uhr = Uhr()
    b = Handelsbereitschaft("okx", cfg=config, jetzt=uhr)
    assert b.kerzen_vollstaendig(900) is False
    uhr.t = 899
    assert b.kerzen_vollstaendig(900) is False
    uhr.t = 901
    assert b.kerzen_vollstaendig(900) is True


def test_kerzengroesse_wird_richtig_gelesen():
    import crypto_engine as ce
    assert ce._kerzengroesse_sekunden("15 mins") == 900.0
    assert ce._kerzengroesse_sekunden("1 hour") == 3600.0
    assert ce._kerzengroesse_sekunden("") == 900.0


def test_kursdaten_haengen_nicht_am_zyklenzaehler():
    quelle = (WURZEL / "crypto_engine.py").read_text(encoding="utf-8")
    assert 'melde("kursdaten", self.zyklen > 0)' not in quelle, \
        "Ab dem ersten Zyklus wahr zu sein misst nichts"
    assert "CRYPTO_TICKER_MAX_AGE_SECONDS" in quelle


# ---------------------------------------------------------------------------
# 11 · status() hat keine Nebenwirkung
# ---------------------------------------------------------------------------
def test_status_verbraucht_die_freigabemeldung_nicht(monkeypatch, tmp_path):
    import config
    from trading_ready import Handelsbereitschaft
    monkeypatch.setenv("TRADINGBOT_TEST_STATE_DIR", str(tmp_path))

    class Uhr:
        def __init__(self): self.t = 0.0
        def __call__(self): return self.t

    gemeldet = []
    uhr = Uhr()
    b = Handelsbereitschaft("okx", cfg=config, melder=gemeldet.append, jetzt=uhr)
    for name in list(b.bedingungen):
        b.melde(name, True)
    uhr.t = 10_000.0        # Anlaufsperre abgelaufen

    b.status()          # Statusabfrage
    b.kurzfassung()     # Anzeige
    assert gemeldet == [], "Eine Statusabfrage darf nichts melden"

    b.darf_kaufen()     # der Handelspfad
    assert len(gemeldet) == 1, "Erst der Handelspfad meldet die Freigabe"


# ---------------------------------------------------------------------------
# 12+13 · Nullstand und NaN
# ---------------------------------------------------------------------------
def test_nullstand_ersetzt_keinen_verkaufsbeleg():
    # Behavioral repeated-snapshot regression with real ledger and positions.
    from crypto_engine import KryptoPosition
    p = KryptoPosition("BTC", "BTC-EUR", 1, 100, 90, 120, ownership_verified=True,
        order_id="buy", client_order_id="client", fill_ids=["fill"])
    p.broker_state = "BROKER_STATE_UNKNOWN"
    assert p.ist_bewiesene_botposition and p.blocks_reentry
    assert not p.darf_schutz_ausfuehren


def test_ist_positiv_faengt_nan_und_unendlich():
    import crypto_engine as ce
    assert ce._ist_positiv(1.0) is True
    assert ce._ist_positiv(0.0) is False
    assert ce._ist_positiv(-1.0) is False
    assert ce._ist_positiv(float("nan")) is False
    assert ce._ist_positiv(float("inf")) is False
    assert ce._ist_positiv(None) is False
    assert ce._ist_positiv("keine Zahl") is False


# ---------------------------------------------------------------------------
# 14 · Kein Beitrag mit Gewicht unter der Datenquelle "Second Opinion"
# ---------------------------------------------------------------------------
def test_second_opinion_beitraege_haben_immer_gewicht_null():
    quelle = (WURZEL / "crypto_engine.py").read_text(encoding="utf-8")
    stelle = 0
    while True:
        stelle = quelle.find('datenquelle="Second Opinion"', stelle + 1)
        if stelle < 0:
            break
        umfeld = quelle[max(0, stelle - 400):stelle]
        assert "gewicht=0.0" in umfeld, \
            "Unter dieser Datenquelle darf nichts das Stimmungsbild verschieben"


# ---------------------------------------------------------------------------
# 15 · Stornierung meldet Fehlschlaege und kennt beide Algo-Arten
# ---------------------------------------------------------------------------
def test_stornierung_kennt_alle_algo_arten():
    """Eine 'conditional'-Schutzorder wurde weder storniert noch erkannt.

    ERWEITERT IN v9.1: Auch 'trigger' und 'move_order_stop' gehoeren dazu.
    Eine im OKX-Web von Hand angelegte Trigger-Verkaufsorder war fuer
    hat_offene_order() unsichtbar -- der Bot waere trotz blockiertem Bestand
    neu eingestiegen. Die Liste wird hier auf Vollstaendigkeit geprueft, nicht
    mehr auf einen festen Text.
    """
    from broker.okx import OKXBroker
    arten = set(OKXBroker.ALGO_ARTEN)
    assert {"oco", "conditional"} <= arten, "die urspruenglichen Arten bleiben Pflicht"
    assert {"trigger", "move_order_stop"} <= arten, (
        "manuell angelegte Trigger-Orders muessen gefunden und storniert werden")
    quelle = (WURZEL / "broker" / "okx.py").read_text(encoding="utf-8")
    assert "def offene_schutzorders" in quelle


def test_stornierungsfehler_wird_gemeldet():
    okx = (WURZEL / "broker" / "okx.py").read_text(encoding="utf-8")
    assert "self.letzte_stornierung" in okx
    engine = (WURZEL / "crypto_engine.py").read_text(encoding="utf-8")
    assert "letzte_stornierung" in engine
    assert "Schutzorder nicht stornierbar" in engine
