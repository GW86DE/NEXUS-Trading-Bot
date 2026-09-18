"""10.4.0: Existenzrisiko-Block, Quote-Kursbestaetigung, PULSAR-DB-Lock,
Scan-Uebersicht und Kerzenansicht (ECharts, OKX + eToro).

Kernaussagen:
- PULSAR blockt einen Hype-Kandidaten NUR bei Existenzrisiko (Insolvenz,
  Chapter 11, going concern, Delisting, Handelsaussetzung, Betrugsermittlung,
  isActivelyTrading=false). Eine schwache Bilanz ist AUSDRUECKLICH kein Block.
- Ein frischer FMP-Starter-Quote bestaetigt den Ausbruch am selben Tag;
  alt, zu duenn oder unvollstaendig bestaetigt er nicht (UNKNOWN bleibt UNKNOWN).
- Das PULSAR-Schema wird einmal je Prozess/Pfad angelegt, nie mehr im Hot-Path.
- Je Scannerzyklus entsteht eine Uebersichtszeile; HOLD/SELL ohne Position
  werden gezaehlt, nicht einzeln journalisiert.
- Die Kerzenansicht liefert fuer OKX und eToro Zeitrahmen, Achsenklemmung
  (1./99. Perzentil der Koerper) und bis 500 Kerzen; eToro-Kerzen stammen
  aus dem Kern-Snapshot, die WebUI ruft den Broker nie selbst.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import time

import pandas as pd
import pytest

from pulsar import evidence
from test_v1030_pulsar_hype import NOW, _bars, _card, _reddit_attention


# ---------------------------------------------------------------------------
# 1. Existenzrisiko-Block
# ---------------------------------------------------------------------------
def _news(title, text="", age_days=3):
    stamp = datetime.fromtimestamp(NOW - age_days*86400, timezone.utc)
    return {"provider": "FMP", "kind": "news", "data": [
        {"symbol": "HYPE", "title": title, "text": text, "publishedDate": stamp.strftime("%Y-%m-%d %H:%M:%S"),
         "site": "example", "url": "https://example.invalid/a"}]}


def test_insolvenzantrag_in_news_blockiert_hype_kandidaten():
    card = _card(sources=[_news("Hype Corp files for Chapter 11 bankruptcy protection")])
    out = evidence.evaluate(card, now=NOW)
    assert out["eligible"] is False
    assert any(b.startswith("Existenzrisiko") and "Insolvenzantrag" in b for b in out["blocks"])
    assert out["hype"]["existence_risk"]["blocked"] is True
    assert out["hype"]["existence_risk"]["findings"][0]["quelle"] == "FMP"
    check = next(c for c in out["checks"] if c["name"] == "existenzrisiko")
    assert check["status"] == "BLOCKIERT"


@pytest.mark.parametrize("title,art", [
    ("Auditor raises going concern doubt for Hype Corp", "Going-Concern-Warnung"),
    ("NYSE delisting notice received", "Delisting"),
    ("Trading halted pending news", "Handelsaussetzung"),
    ("SEC investigation into accounting at Hype Corp", "Betrugsermittlung"),
    ("Hype Corp stellt Insolvenzantrag", "Insolvenz"),
])
def test_alle_existenzrisiko_arten_werden_erkannt(title, art):
    block, findings = evidence._existence_risk(_card(sources=[_news(title)]), now=NOW)
    assert block and art in block
    assert findings[0]["art"] == art


def test_alte_nachricht_ueber_90_tage_zaehlt_nicht():
    card = _card(sources=[_news("Hype Corp files for bankruptcy", age_days=120)])
    block, findings = evidence._existence_risk(card, now=NOW)
    assert block is None and findings == []
    assert evidence.evaluate(card, now=NOW)["eligible"] is True


def test_profil_nicht_aktiv_gehandelt_blockiert():
    profile = {"provider": "FMP", "kind": "profile", "data": {"symbol": "HYPE", "isActivelyTrading": False}}
    out = evidence.evaluate(_card(sources=[profile]), now=NOW)
    assert out["eligible"] is False
    assert any("Delisting" in b for b in out["blocks"])
    # Ein fremdes Profil oder ein fehlendes Feld ist kein Beleg.
    fremd = {"provider": "FMP", "kind": "profile", "data": {"symbol": "OTHER", "isActivelyTrading": False}}
    assert evidence._existence_risk(_card(sources=[fremd]), now=NOW)[0] is None
    leer = {"provider": "FMP", "kind": "profile", "data": {"symbol": "HYPE"}}
    assert evidence._existence_risk(_card(sources=[leer]), now=NOW)[0] is None


def test_schwache_bilanz_ist_ausdruecklich_kein_block():
    """AMC/GME-Muster: negativer Gewinn und Cashflow bleiben nur Information."""
    annual = {"provider": "FMP", "kind": "annual_financials",
              "data": {"schema_version": 2, "latest": {"net_income": -250_000_000, "free_cashflow": -90_000_000,
                                                       "equity": -1_500_000_000, "revenue": 4_000_000_000}}}
    news = _news("Hype Corp reports wider quarterly loss, debt load remains heavy")
    out = evidence.evaluate(_card(sources=[annual, news]), now=NOW)
    assert out["eligible"] is True and out["state"] == "HYPE_KANDIDAT"
    assert out["blocks"] == []
    assert out["hype"]["existence_risk"]["blocked"] is False
    assert "Gewinn negativ" in out["hype"]["finance_note"]
    assert "Eigenkapital negativ" in out["hype"]["finance_note"]
    check = next(c for c in out["checks"] if c["name"] == "existenzrisiko")
    assert check["status"] == "KEIN_BEFUND"


def test_primaerdokument_mit_going_concern_blockiert():
    doc = {"provider": "SEC", "kind": "primary_document",
           "data": {"form": "10-Q", "published_at": datetime.fromtimestamp(NOW-5*86400, timezone.utc).isoformat(),
                    "text": "There is substantial doubt about the Company's ability to continue as a going concern."}}
    block, findings = evidence._existence_risk(_card(sources=[doc]), now=NOW)
    assert block and "Going-Concern-Warnung" in block
    assert findings[0]["quelle"] == "SEC/Original" and findings[0]["beleg"] == "10-Q"


def test_luna_anweisung_verlangt_reject_nur_bei_existenzrisiko():
    from pulsar import analysis
    text = analysis.INSTRUCTION
    assert "Existenzrisiko" in text
    assert "KEIN REJECT-Grund" in text
    assert analysis.REVISION.endswith("existence-risk")


def test_telegram_alarm_zeigt_existenzrisiko_und_bilanzhinweis():
    from pulsar import telegram
    card = _card()
    card.update(evidence.evaluate(card, now=NOW))
    card["hype"]["finance_note"] = "Bilanz (nur Information): Gewinn negativ"
    text = telegram.hype_text(card, mode="BEOBACHTUNG")
    assert "Existenzrisiko: KEIN Befund" in text
    assert "Gewinn negativ" in text
    card["hype"]["existence_risk"] = {"blocked": True, "findings": [{"art": "Delisting", "quelle": "FMP", "beleg": "x"}]}
    assert "BLOCKIERT" in telegram.hype_text(card, mode="FREIGABE")


# ---------------------------------------------------------------------------
# 2. Kursbestaetigung per FMP-Quote
# ---------------------------------------------------------------------------
def _quote(price=10.7, prev=10.0, volume=2_500_000.0, age=120, symbol="HYPE"):
    return {"provider": "FMP", "kind": "quote",
            "data": {"symbol": symbol, "price": price, "previousClose": prev, "volume": volume,
                     "timestamp": NOW - age}}


def test_frischer_quote_bestaetigt_ausbruch_am_selben_tag():
    bars = _bars(gain=0.0, volume_multiple=1.0)  # gestern: nichts passiert
    out = evidence.evaluate(_card(bars=bars, sources=[_quote()]), now=NOW)
    assert out["eligible"] is True
    assert out["hype"]["price"]["kind"] == "QUOTE"
    assert out["hype"]["price"]["gain"] == pytest.approx(0.07)
    assert "FMP-Quote" in out["hype"]["price"]["detail"]
    # 10.5.0: Kursbestaetigung allein reicht nicht -- mit nur 1,2x Tagesvolumen fehlt
    # die Volumenbestaetigung (>= 2x), damit bleibt es bei einer von drei.
    knapp = evidence.evaluate(_card(bars=bars, sources=[_quote(volume=1_200_000.0)]), now=NOW)
    assert knapp["hype"]["price"]["kind"] == "QUOTE" and knapp["eligible"] is False
    assert knapp["hype"]["confirmed_count"] == 1


def test_quote_mit_zu_wenig_tagesvolumen_bestaetigt_nicht():
    bars = _bars(gain=0.0, volume_multiple=1.0)
    out = evidence.evaluate(_card(bars=bars, sources=[_quote(volume=400_000.0)]), now=NOW)
    assert out["eligible"] is False
    assert any("Quote nicht bestaetigt" in m for m in out["missing"])


def test_quote_ohne_kursplus_bestaetigt_nicht_und_tageskerze_ersetzt_ihn_nicht():
    """Ein frischer Quote ist die genaueste Aussage ueber HEUTE."""
    out = evidence.evaluate(_card(sources=[_quote(price=10.1)]), now=NOW)
    assert out["eligible"] is False
    assert out["hype"]["price"] is None
    assert any("Quote nicht bestaetigt: 1.0 %" in m for m in out["missing"])


def test_alter_oder_unvollstaendiger_quote_faellt_auf_tageskerze_zurueck():
    out = evidence.evaluate(_card(sources=[_quote(age=2*3600)]), now=NOW)
    assert out["hype"]["price"]["kind"] == "TAGESKERZE"
    ohne_volumen = _quote()
    ohne_volumen["data"]["volume"] = None
    out = evidence.evaluate(_card(sources=[ohne_volumen]), now=NOW)
    assert out["hype"]["price"]["kind"] == "TAGESKERZE"
    ohne_zeit = _quote()
    ohne_zeit["data"]["timestamp"] = "2026-09-17"
    assert evidence.evaluate(_card(sources=[ohne_zeit]), now=NOW)["hype"]["price"]["kind"] == "TAGESKERZE"


def test_quote_eines_anderen_symbols_wird_ignoriert():
    assert evidence._quote_source(_card(sources=[_quote(symbol="OTHER")])) is None
    assert evidence._quote_source(_card(sources=[_quote()]))["symbol"] == "HYPE"


def test_worker_fordert_quote_als_kartenquelle_an():
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "pulsar" / "worker.py").read_text(encoding="utf-8")
    assert '"quote"' in src and "isActivelyTrading" in src


# ---------------------------------------------------------------------------
# 3. PULSAR-DB-Lock: Schema einmal je Prozess, nie im Hot-Path
# ---------------------------------------------------------------------------
def test_schema_wird_einmal_angelegt_und_nach_loeschung_erneut(monkeypatch, tmp_path):
    import decision_analytics
    from pulsar import control
    path = tmp_path / "pulsar_lock.sqlite"
    monkeypatch.setattr(decision_analytics, "DB_PATH", path)
    control._SCHEMA_READY.discard(str(path))
    calls = []
    original = control._ensure_schema

    def spy(con, p):
        # Merkt, ob der Aufruf noch das teure Schema-Setup ausfuehren wird.
        calls.append((str(p), str(p) in control._SCHEMA_READY))
        return original(con, p)
    monkeypatch.setattr(control, "_ensure_schema", spy)
    import sqlite3
    for _ in range(3):
        with control.transaction() as con:
            con.execute("SELECT mode FROM pulsar_control").fetchone()
    # Nur der erste Zugriff baut das Schema (executescript); danach nur noch
    # die Leseabfrage auf sqlite_master.
    assert calls == [(str(path), False), (str(path), True), (str(path), True)]
    con = sqlite3.connect(path)
    assert con.execute("SELECT COUNT(*) FROM pulsar_control").fetchone()[0] == 1
    con.close()
    # Datei ersetzt (Wiederherstellung/Test): die Leseabfrage erkennt das und
    # baut das Schema neu, statt mit "no such table" zu scheitern.
    path.unlink()
    with control.transaction() as con:
        assert con.execute("SELECT mode FROM pulsar_control").fetchone()[0] == "AUS"


def test_transaction_ohne_schemaaufbau_im_hot_path_kollidiert_nicht_mit_lesender_sicherung(monkeypatch, tmp_path):
    """Nachstellung 17.09.: Diagnose haelt eine Lesesperre, PULSAR entscheidet."""
    import sqlite3
    import decision_analytics
    from pulsar import control
    path = tmp_path / "pulsar_busy.sqlite"
    monkeypatch.setattr(decision_analytics, "DB_PATH", path)
    with control.transaction():
        pass
    # Produktion: decision_analytics setzt WAL; Leser blockieren dann keinen Schreiber.
    setup = sqlite3.connect(path)
    assert str(setup.execute("PRAGMA journal_mode=WAL").fetchone()[0]).lower() == "wal"
    setup.close()
    reader = sqlite3.connect(path, timeout=0.1)
    reader.execute("BEGIN")
    reader.execute("SELECT COUNT(*) FROM pulsar_control").fetchone()  # SHARED-Sperre bleibt offen
    try:
        with control.transaction() as con:
            con.execute("UPDATE pulsar_control SET heartbeat=? WHERE id=1", (time.time(),))
    finally:
        reader.rollback()
        reader.close()


# ---------------------------------------------------------------------------
# 4. Scan-Uebersicht
# ---------------------------------------------------------------------------
def test_scan_zyklus_zaehlt_und_gruppiert_nach_gate(tmp_path, monkeypatch):
    import scan_uebersicht as su
    monkeypatch.setenv("TRADINGBOT_TEST_STATE_DIR", str(tmp_path))
    z = su.Zyklus("etoro", 7)
    for name in ("AAPL", "MSFT", "AMZN"):
        z.hold(name, "HOLD")
    z.hold("TSLA", "SELL")
    z.blockiert("GOOGL", "market_session", "Boerse geschlossen")
    z.blockiert("NVDA", "cash_reserve", "Reserve unterschritten")
    z.blockiert("META", "market_session", "Kurs veraltet")
    z.freigegeben("AMD")
    z.fehler("INTC", "Historie leer")
    s = z.zusammenfassung()
    assert s["geprueft"] == 9 and s["kein_signal"] == 3 and s["sell_ohne_position"] == 1
    assert s["kaufwunsch_blockiert"] == 3 and s["kauf_freigegeben"] == 1 and s["fehler"] == 1
    assert s["blockiert_nach_gate"][0] == {"gate": "Boerse geschlossen / Kurs veraltet", "anzahl": 2, "symbole": ["GOOGL", "META"]}
    assert s["freigegeben_symbole"] == ["AMD"] and s["fehler_symbole"] == ["INTC"]
    text = z.text()
    assert text.startswith("etoro: 9 Instrumente geprueft") and "1 Kauf freigegeben (AMD)" in text
    su.speichern(z)
    su.speichern(su.Zyklus("okx", 8))
    data = su.lesen(limit=5)
    assert set(data["broker"]) == {"etoro", "okx"}
    assert data["broker"]["etoro"][0]["zyklus"] == 7 and data["broker"]["etoro"][0]["text"] == text
    raw = json.loads(su.pfad().read_text(encoding="utf-8"))
    assert raw["schema"] == 1 and raw["aktualisiert_utc"]


def test_scan_uebersicht_haelt_nur_48_zyklen_und_bleibt_bei_defekter_datei_stumm(tmp_path, monkeypatch, caplog):
    import scan_uebersicht as su
    monkeypatch.setenv("TRADINGBOT_TEST_STATE_DIR", str(tmp_path))
    su.pfad().write_text("{kaputt", encoding="utf-8")
    for i in range(50):
        su.speichern(su.Zyklus("okx", i))
    rows = su.lesen(limit=48)["broker"]["okx"]
    assert len(rows) == 48 and rows[0]["zyklus"] == 49 and rows[-1]["zyklus"] == 2
    # Ein Fehler in der Uebersicht darf den Handelspfad nie treffen.
    monkeypatch.setattr(su, "atomic_write_json", None, raising=False)
    monkeypatch.setattr(su, "_laden", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    su.speichern(su.Zyklus("okx", 99))


def test_live_trader_bindet_scan_uebersicht_und_chartspeicher_ein():
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "live_trader.py").read_text(encoding="utf-8")
    assert "scan_zyklus = _ScanZyklus(getattr(broker, \"name\", \"?\"), cycle)" in src
    assert "scan_zyklus.hold(inst.name, signal.action)" in src
    assert "scan_zyklus.freigegeben(inst.name)" in src
    assert "scan_zyklus.blockiert(" in src
    assert "_scan_speichern(scan_zyklus)" in src
    assert "merke_scan(broker, inst, df, config.BAR_SIZE)" in src
    assert "ergaenze_fuer_trades(broker, instrument_by_symbol)" in src


def test_logbuch_api_liefert_scan_uebersicht(monkeypatch, tmp_path):
    import scan_uebersicht as su
    monkeypatch.setenv("TRADINGBOT_TEST_STATE_DIR", str(tmp_path))
    su.speichern(su.Zyklus("etoro", 3))
    from fastapi.testclient import TestClient
    from test_v100_webui_backend import configured_auth
    from webui.app import app
    auth, _ = configured_auth(monkeypatch)
    session, _csrf = auth.issue_session("testuser")
    client = TestClient(app)
    assert client.get("/api/logs/scan-uebersicht").status_code == 401
    client.cookies.set(auth.COOKIE, session)
    response = client.get("/api/logs/scan-uebersicht?limit=2")
    assert response.status_code == 200
    assert response.json()["broker"]["etoro"][0]["zyklus"] == 3
    gross = client.get("/api/logs/scan-uebersicht?limit=999")  # wird auf 48 gekappt, kein Fehler
    assert gross.status_code == 200 and len(gross.json()["broker"]["etoro"]) <= 48


def test_logbuch_seite_zeigt_scan_uebersicht_vor_dem_journal():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    html = (root / "webui/templates/logbook.html").read_text(encoding="utf-8")
    js = (root / "webui/static/logbook.js").read_text(encoding="utf-8")
    assert 'id="scan-uebersicht"' in html
    assert html.index("scan-uebersicht") < html.index("page-head")
    assert "/api/logs/scan-uebersicht" in js and "loadScanUebersicht" in js
    diag = (root / "NEXUS_10_Diagnose.py").read_text(encoding="utf-8")
    assert "scan_uebersicht.json" in diag


# ---------------------------------------------------------------------------
# 5. Kerzenansicht: eToro-Kerzenspeicher und Chartdaten
# ---------------------------------------------------------------------------
class _Instrument:
    def __init__(self, name, asset_type="stock"):
        self.name, self.asset_type, self.use_rth = name, asset_type, True


class _Broker:
    name = "etoro"

    def __init__(self, frames):
        self.frames, self.calls = frames, []

    def account_fingerprint(self):
        return "acct-demo-1"

    def ist_paper(self):
        return True

    def historie(self, instrument, dauer, kerzengroesse, nur_handelszeiten=True):
        self.calls.append((instrument.name, dauer, kerzengroesse))
        return self.frames[kerzengroesse]


def _frame(start, periods, freq, price=100.0):
    """Leicht schwingende Kerzen (Koerper +-5 %, Dochte +-0,2 %), keine Ausreisser."""
    import math
    index = pd.date_range(start, periods=periods, freq=freq, tz="UTC")
    opens = [price*(1+0.05*math.sin(i/9.0)) for i in range(periods)]
    closes = [price*(1+0.05*math.sin((i+1)/9.0)) for i in range(periods)]
    return pd.DataFrame({"open": opens, "high": [max(o, c)*1.002 for o, c in zip(opens, closes)],
                         "low": [min(o, c)*0.998 for o, c in zip(opens, closes)], "close": closes,
                         "volume": 1000.0}, index=index)


def test_merke_scan_sichert_stundenkerzen_nur_fuer_etoro_aktien_und_schreibt_inkrementell(tmp_path, monkeypatch):
    import etoro_chart_store as store
    monkeypatch.setenv("TRADINGBOT_TEST_STATE_DIR", str(tmp_path))
    start = datetime.now(timezone.utc) - timedelta(days=3)
    frame = _frame(start, 60, "1h")
    broker = _Broker({})
    store.merke_scan(broker, _Instrument("GOOGL"), frame, "1 hour")
    assert store.verfuegbare_bars("acct-demo-1", "DEMO", "GOOGL") == ["1h"]
    assert store.lade("acct-demo-1", "DEMO", "GOOGL", "1h")["rows_total"] == 60
    # Zweiter Scan mit denselben 60 plus 2 neuen Kerzen: nur die Spitze wird
    # geschrieben (2 neue + die letzten 3 zur Korrektur), nicht 62 Zeilen.
    assert store.speichere("acct-demo-1", "DEMO", "GOOGL", "1h", _frame(start, 62, "1h")) == 5
    assert store.lade("acct-demo-1", "DEMO", "GOOGL", "1h")["rows_total"] == 62
    # Krypto und andere Broker werden nicht gesichert; kein Abruf.
    store.merke_scan(broker, _Instrument("BTC-USDT", "crypto"), frame, "1 hour")
    okx = _Broker({}); okx.name = "okx"
    store.merke_scan(okx, _Instrument("AAPL"), frame, "1 hour")
    assert store.verfuegbare_bars("acct-demo-1", "DEMO", "BTC-USDT") == []
    assert broker.calls == []
    assert store.pfad().parent == tmp_path


def test_ergaenze_fuer_trades_laedt_15m_und_tageskerzen_nur_fuer_trades_mit_ruhezeit(tmp_path, monkeypatch):
    import etoro_chart_store as store
    import trade_ledger
    monkeypatch.setenv("TRADINGBOT_TEST_STATE_DIR", str(tmp_path))
    store._LAST_FETCH.clear()
    now = datetime.now(timezone.utc)
    trade_ledger.trade_open(broker="etoro", symbol="GOOGL", menge=1, einstieg_preis=180, waehrung="USD",
                            decision_id=11, paper=True, asset_type="stock", zeit=now - timedelta(hours=5),
                            broker_position_id="p1", entry_order_id="o1", broker_account_fingerprint="acct-demo-1")
    trade_ledger.trade_open(broker="etoro", symbol="ALT", menge=1, einstieg_preis=10, waehrung="USD",
                            decision_id=12, paper=True, asset_type="stock", zeit=now - timedelta(days=30),
                            broker_position_id="p2", entry_order_id="o2", broker_account_fingerprint="acct-demo-1")
    trade_ledger.trade_close(broker="etoro", symbol="ALT", ausstieg_preis=11, menge=1,
                             zeit=now - timedelta(days=20), exit_grund="roi")
    frames = {"15 mins": _frame(now - timedelta(days=6), 400, "15min"), "1 day": _frame(now - timedelta(days=400), 300, "1D")}
    broker = _Broker(frames)
    instruments = {"GOOGL": _Instrument("GOOGL"), "ALT": _Instrument("ALT")}
    assert store.ergaenze_fuer_trades(broker, instruments, now=time.time()) == 2
    assert [c[0] for c in broker.calls] == ["GOOGL", "GOOGL"]
    assert {c[2] for c in broker.calls} == {"15 mins", "1 day"}
    assert store.verfuegbare_bars("acct-demo-1", "DEMO", "GOOGL") == ["15m", "1d"]
    # Innerhalb der Ruhezeit kein zweiter Abruf; danach wieder.
    assert store.ergaenze_fuer_trades(broker, instruments, now=time.time() + 60) == 0
    assert store.ergaenze_fuer_trades(broker, instruments, now=time.time() + store.REFRESH_SECONDS + 1) == 2
    # Budgetdeckel je Aufruf.
    store._LAST_FETCH.clear()
    assert store.ergaenze_fuer_trades(broker, instruments, now=time.time() + 5000, max_fetches=1) == 1


def test_chart_fuer_etoro_trade_kommt_aus_dem_kern_snapshot_mit_zeitrahmen_und_achse(tmp_path, monkeypatch):
    import decision_analytics
    import etoro_chart_store as store
    import trade_chart_data
    import trade_ledger
    monkeypatch.setenv("TRADINGBOT_TEST_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(decision_analytics, "DB_PATH", tmp_path / "chart.sqlite")
    now = datetime.now(timezone.utc)
    entry = now - timedelta(hours=30)
    trade_id = trade_ledger.trade_open(broker="etoro", symbol="GOOGL", menge=2, einstieg_preis=180.0, waehrung="USD",
                                       decision_id=21, paper=True, asset_type="stock", zeit=entry,
                                       broker_position_id="p9", entry_order_id="o9", broker_account_fingerprint="acct-demo-1",
                                       entry_strategy_mode="NEXUS_STANDARD")
    trade_chart_data._CACHE.clear()
    out = trade_chart_data.chart_for_trade(trade_id, force=True)
    assert out["ok"] is False and "Noch keine eToro-Kerzen" in out["fehler"]
    hourly = _frame(now - timedelta(days=20), 480, "1h", price=180.0)
    # Ein einzelner Docht-Ausreisser darf die Skala nicht stauchen.
    hourly.iloc[100, hourly.columns.get_loc("high")] = 260.0
    store.speichere("acct-demo-1", "DEMO", "GOOGL", "1h", hourly)
    store.speichere("acct-demo-1", "DEMO", "GOOGL", "1d", _frame(now - timedelta(days=300), 300, "1D", price=180.0))
    trade_chart_data._CACHE.clear()
    out = trade_chart_data.chart_for_trade(trade_id, force=True)
    assert out["ok"] and out["bar"] == "1h" and out["available_bars"] == ["1h", "1d"]
    assert out["nur_abgeschlossene_kerzen"] and out["kauf"]["preis"] == 180.0 and out["verkauf"] is None
    assert out["candles"][-1]["zeit"] > out["candles"][0]["zeit"]
    assert len(out["candles"]) <= trade_chart_data.MAX_CANDLES
    assert out["axis"]["clipped"] == 1 and out["axis"]["max"] < 260.0
    assert out["data_source"].startswith("eToro") and out["data_age_seconds"] is not None
    assert out["display_context"]["broker"] == "etoro" and out["display_context"]["environment"] == "DEMO"
    tag = trade_chart_data.chart_for_trade(trade_id, force=True, bar="1d")
    assert tag["bar"] == "1d" and tag["bar_seconds"] == 86400 and tag["candles"]
    unbekannt = trade_chart_data.chart_for_trade(trade_id, force=True, bar="3h")
    assert unbekannt["ok"] and unbekannt["bar"] == "1h"  # Automatik statt Fehler


def test_axis_bounds_klemmt_auf_perzentile_und_haelt_schutzlinien_im_bild():
    import trade_chart_data
    candles = [{"open": 10.0, "close": 10.1, "high": 10.15, "low": 9.95} for _ in range(200)]
    candles[50]["high"] = 14.0
    candles[120]["low"] = 6.0
    axis = trade_chart_data._axis_bounds(candles, [9.4])
    assert axis["clipped"] == 2
    assert 9.3 < axis["min"] < 9.4 and 10.1 < axis["max"] < 10.3
    assert trade_chart_data._axis_bounds([], []) == {"min": None, "max": None, "clipped": 0}


def test_okx_chart_paginiert_bis_500_kerzen_und_nimmt_zeitrahmen(monkeypatch, tmp_path):
    import decision_analytics
    import trade_chart_data
    import trade_ledger
    monkeypatch.setattr(decision_analytics, "DB_PATH", tmp_path / "chart.sqlite")
    entry = datetime.now(timezone.utc) - timedelta(hours=40)
    trade_id = trade_ledger.trade_open(broker="okx", symbol="ETH", menge=2, einstieg_preis=100, waehrung="EUR",
                                       decision_id=456, paper=True, zeit=entry, broker_position_id="ETH-EUR")
    trade_ledger.trade_close(broker="okx", symbol="ETH", ausstieg_preis=105, menge=2,
                             zeit=entry + timedelta(hours=30), exit_grund="roi")
    calls = []

    class Client:
        def historical_candles(self, instrument, *, bar, limit, end_ms, nur_abgeschlossen=True):
            calls.append((bar, limit, end_ms))
            end = datetime.fromtimestamp(end_ms/1000, timezone.utc)
            step = {"5m": 5, "15m": 15, "1H": 60, "4H": 240, "1Dutc": 1440}[bar]
            index = pd.date_range(end=end - timedelta(minutes=step), periods=limit, freq=f"{step}min", tz="UTC")
            return pd.DataFrame({"open": 100.0, "high": 106.0, "low": 99.0, "close": 103.0, "volume": 10.0}, index=index)
    monkeypatch.setattr(trade_chart_data, "_CLIENT_FACTORY", lambda: Client())
    trade_chart_data._CACHE.clear()
    out = trade_chart_data.chart_for_trade(trade_id, force=True, bar="5m")
    assert out["ok"] and out["bar"] == "5m" and out["available_bars"] == ["5m", "15m", "1h", "4h", "1d"]
    assert len(calls) == 5 and all(c[0] == "5m" for c in calls)  # 5 Seiten a 100 = Deckel 500
    assert len(out["candles"]) <= trade_chart_data.MAX_CANDLES
    assert out["kauf"]["preis"] == 100 and out["verkauf"]["preis"] == 105
    assert out["axis"]["min"] is not None
    calls.clear()
    out = trade_chart_data.chart_for_trade(trade_id, force=True)  # Automatik: 30 h -> 15m oder 1h
    assert out["bar"] in {"15m", "1h"} and len(calls) <= 5


def test_candles_api_nimmt_nur_bekannte_zeitrahmen():
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "webui/app.py").read_text(encoding="utf-8")
    assert 'bar: str = ""' in src and 'run_in_threadpool(chart_for_trade, trade_id, bar=safe_bar)' in src
    assert '"5m", "15m", "1h", "4h", "1d"' in src


def test_kerzenansicht_frontend_nutzt_lokales_echarts_ohne_cdn():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    html = (root / "webui/templates/trades.html").read_text(encoding="utf-8")
    js = (root / "webui/static/trades.js").read_text(encoding="utf-8")
    lib = root / "webui/static/echarts.min.js"
    assert lib.exists() and lib.stat().st_size > 900_000
    assert (root / "webui/static/echarts.LICENSE.txt").read_text(encoding="utf-8").startswith("Apache License") or \
        "Apache" in (root / "webui/static/echarts.LICENSE.txt").read_text(encoding="utf-8")[:400]
    assert '<script src="/static/echarts.min.js"></script>' in html
    assert "cdn" not in html.lower() and "https://" not in js.split("zeichneKerzenECharts")[1].split("function zeichneKerzenSVG")[0]
    assert 'id="trade-chart-bars"' in html and 'id="trade-chart-axisnote"' in html
    for needle in ("zeichneKerzenECharts", "zeichneKerzenSVG", "dataZoom", "candlestick", "?bar=", "available_bars",
                   "Docht-Ausreißer", "KAUF", "VERKAUF", "nur abgeschlossen", "typeof echarts"):
        assert needle in js, needle
    # Die Volltest-Pflichtliste kennt die neuen Dateien.
    import volltest
    for name in ("webui/static/echarts.min.js", "etoro_chart_store.py", "scan_uebersicht.py",
                 "tests/test_v1040_scan_chart_existence.py"):
        assert name in volltest.REQUIRED_RELEASE_FILES, name


def test_webui_content_security_policy_bleibt_self():
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "webui/app.py").read_text(encoding="utf-8")
    assert "script-src 'self'" in src
