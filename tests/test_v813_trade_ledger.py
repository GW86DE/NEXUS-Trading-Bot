"""Trade-Ledger und Strategieversion (v8.1.3, Etappe A).

Etappe A ist reine Aufzeichnung. Die Tests pruefen deshalb zwei Dinge
gleich streng: dass die Kennzahlen stimmen -- und dass ein kaputtes Ledger
niemals den Handel beruehrt.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("TRADINGBOT_TEST_STATE_DIR", "/tmp/tradingbot_tests")
Path(os.environ["TRADINGBOT_TEST_STATE_DIR"]).mkdir(parents=True, exist_ok=True)

WURZEL = Path(__file__).resolve().parent.parent


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    """Ein frisches Ledger je Test -- eigene Datenbankdatei."""
    monkeypatch.setenv("TRADINGBOT_TEST_STATE_DIR", str(tmp_path))
    import decision_analytics as da
    monkeypatch.setattr(da, "DB_PATH", tmp_path / "decision_history.sqlite")
    import trade_ledger
    original_open = trade_ledger.trade_open
    original_close = trade_ledger.trade_close
    def external_open(**kwargs):
        kwargs.setdefault("external", True)
        # These arithmetic fixtures model confirmed fee-free executions.
        # Omission in production means UNKNOWN, not zero (since 9.7).
        kwargs.setdefault("gebuehr", 0.0)
        return original_open(**kwargs)
    def confirmed_close(**kwargs):
        kwargs.setdefault("gebuehr", 0.0)
        return original_close(**kwargs)
    monkeypatch.setattr(trade_ledger, "trade_open", external_open)
    monkeypatch.setattr(trade_ledger, "trade_close", confirmed_close)
    trade_ledger.init_ledger()
    return trade_ledger


def trade(ledger, symbol, ein, aus, *, menge=1.0, gebuehr=0.0, grund="Gewinnziel",
          broker="okx", phase="", version=""):
    ledger.trade_open(broker=broker, symbol=symbol, menge=menge, einstieg_preis=ein,
                      marktphase=phase, strategie_version=version)
    return ledger.trade_close(broker=broker, symbol=symbol, ausstieg_preis=aus,
                              menge=menge, exit_grund=grund, gebuehr=gebuehr)


# ---------------------------------------------------------------------------
# Die Luecke, um die es geht: die Ausstiegsseite
# ---------------------------------------------------------------------------
def test_einstieg_und_ausstieg_ergeben_einen_geschlossenen_trade(ledger):
    ledger.trade_open(broker="okx", symbol="BTC", menge=0.5, einstieg_preis=100.0,
                      referenzpreis=99.0, gebuehr=0.05, waehrung="EUR")
    assert ledger.offener_trade("okx", "BTC") is not None

    ledger.trade_close(broker="okx", symbol="BTC", ausstieg_preis=120.0, menge=0.5,
                       exit_grund="Gewinnziel erreicht", gebuehr=0.06)

    assert ledger.offener_trade("okx", "BTC") is None
    snap = ledger.trade_snapshot()
    assert snap["gesamt"]["trades"] == 1
    assert snap["gesamt"]["summe_netto"] == pytest.approx((120 - 100) * 0.5 - 0.11)


def test_haltedauer_und_exit_grund_werden_gefuehrt(ledger):
    ledger.trade_open(broker="okx", symbol="ETH", menge=1.0, einstieg_preis=100.0,
                      zeit="2026-08-01T10:00:00+00:00")
    ledger.trade_close(broker="okx", symbol="ETH", ausstieg_preis=90.0, menge=1.0,
                       exit_grund="Stop-Loss erreicht", zeit="2026-08-01T13:30:00+00:00")
    snap = ledger.trade_snapshot()
    assert snap["gesamt"]["haltedauer_minuten_mittel"] == pytest.approx(210.0)
    assert snap["je_exit_grund"][0]["wert"] == "Stop-Loss erreicht"


def test_slippage_wird_aus_referenz_und_fill_geschaetzt(ledger):
    """Geplant 100, gekauft zu 101 -> 1 % schlechter als gedacht."""
    ledger.trade_open(broker="okx", symbol="SOL", menge=1.0, einstieg_preis=101.0,
                      referenzpreis=100.0)
    ledger.trade_close(broker="okx", symbol="SOL", ausstieg_preis=110.0, menge=1.0,
                       exit_grund="Ziel")
    snap = ledger.trade_snapshot()
    assert snap["gesamt"]["slippage_mittel_pct"] == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Kennzahlen
# ---------------------------------------------------------------------------
def test_trefferquote_erwartungswert_profitfaktor(ledger):
    trade(ledger, "AAA", 100, 110)     # +10
    trade(ledger, "BBB", 100, 110)     # +10
    trade(ledger, "CCC", 100, 95)      # -5
    g = ledger.trade_snapshot()["gesamt"]
    assert g["trefferquote_pct"] == pytest.approx(66.67, abs=0.01)
    assert g["erwartungswert"] == pytest.approx(5.0)
    assert g["profitfaktor"] == pytest.approx(4.0)   # 20 Gewinn / 5 Verlust


def test_profitfaktor_ohne_verlust_ist_nicht_definiert(ledger):
    """Kein Verlusttrade heisst nicht "unendlich gut"."""
    trade(ledger, "AAA", 100, 110)
    assert ledger.trade_snapshot()["gesamt"]["profitfaktor"] is None


def test_max_drawdown_folgt_der_ergebniskurve(ledger):
    trade(ledger, "AAA", 100, 120)    # +20  -> Hoch 20
    trade(ledger, "BBB", 100, 92)     # -8   -> 12
    trade(ledger, "CCC", 100, 95)     # -5   -> 7, Drawdown 13
    trade(ledger, "DDD", 100, 110)    # +10  -> 17
    assert ledger.trade_snapshot()["gesamt"]["max_drawdown"] == pytest.approx(13.0)


def test_gebuehrenquote_zeigt_den_anteil_am_bruttoergebnis(ledger):
    trade(ledger, "AAA", 100, 110, gebuehr=2.0)   # brutto 10, Gebuehr 2
    g = ledger.trade_snapshot()["gesamt"]
    assert g["gebuehren_quote_pct"] == pytest.approx(20.0)
    assert g["summe_netto"] == pytest.approx(8.0)


def test_kennzahlen_je_broker_symbol_phase_und_version(ledger):
    trade(ledger, "BTC", 100, 110, broker="okx", phase="RISK_ON", version="v1")
    trade(ledger, "AAPL", 100, 90, broker="etoro", phase="RISK_OFF", version="v1")
    trade(ledger, "BTC", 100, 105, broker="okx", phase="RISK_ON", version="v2")
    snap = ledger.trade_snapshot()

    je_broker = {x["wert"]: x for x in snap["je_broker"]}
    assert je_broker["okx"]["trades"] == 2 and je_broker["etoro"]["trades"] == 1
    assert {x["wert"] for x in snap["je_marktphase"]} == {"RISK_ON", "RISK_OFF"}
    assert {x["wert"] for x in snap["je_strategieversion"]} == {"v1", "v2"}
    assert {x["wert"] for x in snap["je_symbol"]} == {"BTC", "AAPL"}


def test_snapshot_je_broker_trennt_die_domaenen(ledger):
    trade(ledger, "BTC", 100, 110, broker="okx")
    trade(ledger, "AAPL", 100, 90, broker="etoro")
    assert ledger.trade_snapshot(broker="okx")["gesamt"]["trades"] == 1
    assert ledger.trade_snapshot(broker="etoro")["gesamt"]["summe_netto"] == pytest.approx(-10.0)


def test_stichprobenguete_wird_immer_mitgeliefert(ledger):
    trade(ledger, "AAA", 100, 110)
    g = ledger.trade_snapshot()["gesamt"]
    assert g["sample_quality"] == "nicht_aussagekraeftig", \
        "Ein einzelner Trade darf nie belastbar aussehen"
    for gruppe in ("je_broker", "je_symbol", "je_exit_grund", "je_strategieversion"):
        for eintrag in ledger.trade_snapshot()[gruppe]:
            assert "sample_quality" in eintrag


# ---------------------------------------------------------------------------
# Ehrlichkeit bei Luecken
# ---------------------------------------------------------------------------
def test_unbekannter_einstand_wird_nicht_als_nullergebnis_gebucht(ledger):
    """Der haeufigste Weg, eine Statistik unbrauchbar zu machen."""
    ledger.trade_close(broker="okx", symbol="XRP", ausstieg_preis=100.0, menge=1.0,
                       exit_grund="Verkauf")
    snap = ledger.trade_snapshot()
    assert snap["gesamt"]["trades"] == 0, "Ohne Einstand darf kein Trade gezaehlt werden"


def test_trade_ohne_ergebnis_wird_ausgewiesen_nicht_verrechnet(ledger):
    ledger.trade_open(broker="okx", symbol="XRP", menge=1.0, einstieg_preis=100.0)
    with ledger._connect() as con:      # Einstand nachtraeglich unbekannt machen
        con.execute("UPDATE trades SET einstieg_preis=NULL WHERE symbol='XRP'")
    ledger.trade_close(broker="okx", symbol="XRP", ausstieg_preis=110.0, menge=1.0,
                       exit_grund="Ziel")
    trade(ledger, "AAA", 100, 110)
    g = ledger.trade_snapshot()["gesamt"]
    assert g["trades"] == 2 and g["bewertbar"] == 1 and g["ohne_ergebnis"] == 1
    assert g["erwartungswert"] == pytest.approx(10.0), "Der unbekannte Trade darf nicht mitrechnen"


def test_leeres_ledger_liefert_keine_erfundenen_kennzahlen(ledger):
    g = ledger.trade_snapshot()["gesamt"]
    assert g["trades"] == 0
    assert "trefferquote_pct" not in g, "Ohne Trades gibt es keine Trefferquote"


# ---------------------------------------------------------------------------
# Teilverkauf
# ---------------------------------------------------------------------------
def test_teilverkauf_laesst_den_rest_offen(ledger):
    ledger.trade_open(broker="okx", symbol="BTC", menge=1.0, einstieg_preis=100.0,
                      gebuehr=1.0)
    ledger.trade_close(broker="okx", symbol="BTC", ausstieg_preis=110.0, menge=0.4,
                       exit_grund="Teilgewinn")
    rest = ledger.offener_trade("okx", "BTC")
    assert rest is not None and rest["menge"] == pytest.approx(0.6)
    g = ledger.trade_snapshot()["gesamt"]
    assert g["trades"] == 1
    assert g["summe_netto"] == pytest.approx(0.4 * 10 - 0.4), "anteilige Einstiegsgebuehr"


# ---------------------------------------------------------------------------
# MFE / MAE
# ---------------------------------------------------------------------------
def test_mfe_und_mae_werden_fortgeschrieben(ledger):
    ledger.trade_open(broker="okx", symbol="BTC", menge=1.0, einstieg_preis=100.0)
    for kurs in (105.0, 112.0, 96.0, 103.0):
        ledger.hoechstkurs_melden("okx", "BTC", kurs)
    offen = ledger.offener_trade("okx", "BTC")
    assert offen["mfe_pct"] == pytest.approx(12.0)
    assert offen["mae_pct"] == pytest.approx(-4.0)


# ---------------------------------------------------------------------------
# Ein kaputtes Ledger darf den Handel nie stoppen
# ---------------------------------------------------------------------------
def test_schreibfehler_wirft_nicht(ledger, monkeypatch):
    monkeypatch.setattr(ledger, "_connect",
                        lambda: (_ for _ in ()).throw(RuntimeError("Datenbank weg")))
    assert ledger.trade_open(broker="okx", symbol="BTC", menge=1.0, einstieg_preis=100.0) is None
    assert ledger.trade_close(broker="okx", symbol="BTC", ausstieg_preis=110.0) is None
    ledger.hoechstkurs_melden("okx", "BTC", 120.0)      # darf einfach nichts tun
    assert ledger.trade_snapshot()["trades"] == 0


def test_handelspfade_fangen_ledgerfehler_ab():
    """Beide Broker-Pfade rufen das Ledger nur in einem try-Block auf."""
    for datei, marker in (("crypto_engine.py", "trade_ledger.trade_open"),
                          ("crypto_engine.py", "trade_ledger.trade_close"),
                          ("live_trader.py", "trade_ledger.trade_open"),
                          ("live_trader.py", "trade_ledger.trade_close")):
        quelle = (WURZEL / datei).read_text(encoding="utf-8")
        assert marker in quelle, f"{datei}: {marker} fehlt"
        stelle = quelle.index(marker)
        davor = quelle[max(0, stelle - 400):stelle]
        assert "try:" in davor, f"{datei}: {marker} ist nicht abgesichert"


def test_kryptopfad_loest_keinen_marktdaten_download_aus():
    """Die Marktphase darf im Kaufpfad nicht auf das Netz warten."""
    quelle = (WURZEL / "crypto_engine.py").read_text(encoding="utf-8")
    assert "letzter_bekannter" in quelle
    assert "MarketRegimeClient" not in quelle, "Das waere ein Download im Kaufpfad"


# ---------------------------------------------------------------------------
# Strategieversion
# ---------------------------------------------------------------------------
def test_version_ist_stabil_und_reagiert_auf_parameter():
    import config
    import strategy_version as sv

    eins = sv.berechne()
    assert eins == sv.berechne(), "Gleiche Einstellungen muessen gleiche Version ergeben"
    assert len(eins) == 12

    alt = getattr(config, "RISK_PER_TRADE_PCT", 1.0)
    try:
        config.RISK_PER_TRADE_PCT = float(alt) + 0.5
        assert sv.berechne() != eins, "Ein geaenderter Risikoparameter muss die Version aendern"
    finally:
        config.RISK_PER_TRADE_PCT = alt
    assert sv.berechne() == eins


def test_version_ignoriert_kosmetik():
    import config
    import strategy_version as sv
    eins = sv.berechne()
    alt = getattr(config, "TELEGRAM_ENABLED", None)
    try:
        config.TELEGRAM_ENABLED = not bool(alt)
        assert sv.berechne() == eins, \
            "Telegram beeinflusst den Handel nicht und darf die Version nicht wechseln"
    finally:
        if alt is not None:
            config.TELEGRAM_ENABLED = alt


def test_unterschiede_benennen_den_konkreten_parameter():
    import strategy_version as sv
    diff = sv.unterschiede({"SMA_FAST": 20, "RSI_PERIOD": 14},
                           {"SMA_FAST": 10, "RSI_PERIOD": 14})
    assert diff == [{"parameter": "SMA_FAST", "vorher": 20, "nachher": 10}]


def test_entscheidungen_bekommen_eine_strategieversion(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADINGBOT_TEST_STATE_DIR", str(tmp_path))
    import decision_analytics as da
    monkeypatch.setattr(da, "DB_PATH", tmp_path / "decision_history.sqlite")
    da.init_db()
    decision_id = da.record({"symbol": "BTC", "status": "APPROVED", "price": 100.0,
                             "broker": "okx"})
    with da._connect() as con:
        zeile = con.execute("SELECT strategy_version FROM decisions WHERE id=?",
                            (decision_id,)).fetchone()
    import strategy_version as sv
    assert zeile["strategy_version"] == sv.aktuell()


def test_analytics_bietet_trade_snapshot_unter_dem_spezifizierten_namen(ledger):
    import decision_analytics as da
    trade(ledger, "AAA", 100, 110)
    assert da.trade_snapshot()["gesamt"]["trades"] == 1


# ---------------------------------------------------------------------------
# Etappe A bleibt Etappe A
# ---------------------------------------------------------------------------
def test_etappe_a_aktiviert_nichts():
    """Kein Shadow, kein Auto-Rollback, keine Selbstaenderung -- das ist v8.2.

    Geprueft wird der CODE, nicht der Text: die Modulbeschreibung erklaert
    ausdruecklich, was Etappe A NICHT tut, und darf diese Woerter nennen.
    """
    import ast
    baum = ast.parse((WURZEL / "trade_ledger.py").read_text(encoding="utf-8"))
    # Docstrings entfernen -- uebrig bleibt ausfuehrbarer Code.
    for knoten in ast.walk(baum):
        if isinstance(knoten, (ast.Module, ast.ClassDef, ast.FunctionDef,
                               ast.AsyncFunctionDef)) and ast.get_docstring(knoten):
            knoten.body = knoten.body[1:]
    code = ast.unparse(baum).lower()
    for verboten in ("setattr(config", "shadow", "aktiviere", "os.system",
                     "subprocess", "exec(", "eval("):
        assert verboten not in code, f"Etappe A darf {verboten} nicht enthalten"


def test_marktphase_wird_nicht_geraten(ledger):
    """Unbekannte Marktphase bleibt leer statt "NEUTRAL"."""
    ledger.trade_open(broker="okx", symbol="BTC", menge=1.0, einstieg_preis=100.0)
    offen = ledger.offener_trade("okx", "BTC")
    assert offen["marktphase"] == ""
