"""Regressionen fuer die WebUI-Korrekturen in NEXUS 9.4.1."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_logo_has_hard_desktop_limit_and_keeps_mobile_size():
    css = (ROOT / "webui/static/app.css").read_text(encoding="utf-8")
    templates = list((ROOT / "webui/templates").glob("*.html"))
    assert ".topbar .brand{max-height:52px}" in css
    assert "inline-size:46px!important" in css and "max-inline-size:46px" in css
    assert "inline-size:38px!important" in css and "max-width:600px" in css
    for template in templates:
        html = template.read_text(encoding="utf-8")
        if "topbar" in html:
            assert 'width="46" height="46"' in html


def test_analysis_page_navigation_and_local_allowlist_are_complete():
    html = (ROOT / "webui/templates/analysis.html").read_text(encoding="utf-8")
    app = (ROOT / "webui/app.py").read_text(encoding="utf-8")
    jobs = (ROOT / "webui/analysis_jobs.py").read_text(encoding="utf-8")
    for label in ("Analyse &amp; Training", "Letzte Läufe", "Ausgabe"):
        assert label in html
    for route in ('@app.get("/analysis"', '@app.get("/api/analysis"',
                  '@app.post("/api/analysis/{task}"'):
        assert route in app
    for script in ("run_backtest.py", "run_walkforward.py", "profile_vergleich.py",
                   "sweep.py", "train_model.py", "webui_news_check.py",
                   "underdog_screening.py", "run_crypto_backtest.py",
                   "run_crypto_walkforward.py"):
        assert script in jobs
    assert '"crypto_backtest"' in jobs and '"crypto_walkforward"' in jobs
    assert "shell=True" not in jobs
    assert "subprocess.Popen(command" in jobs


def test_analysis_layout_has_desktop_tablet_and_mobile_breakpoints():
    css = (ROOT / "webui/static/app.css").read_text(encoding="utf-8")
    assert "grid-template-columns:repeat(2,minmax(0,1fr))" in css  # Desktop
    assert "@media(max-width:1050px)" in css                       # Tablet
    assert "@media(max-width:600px)" in css                        # Mobil
    assert ".analysis-tool{align-items:flex-start;flex-direction:column}" in css


def test_profit_chart_renderer_handles_empty_single_and_multiple_series():
    """Der Renderer bleibt lokal und benoetigt auf dem Pi kein Node-Paket."""
    js = (ROOT / "webui/static/trades.js").read_text(encoding="utf-8")
    assert "Noch keine bestätigten Nettoergebnisse" in js
    assert "rows.length===1" in js       # Einzelwert: Punkt, keine defekte Linie
    assert "<circle" in js and "<polyline" in js
    assert "Number.isFinite(Number(r.wert))" in js
    assert "context_groups" in js
    assert "profit-legend" in js


def test_trade_endpoint_empty_and_single_dataset(monkeypatch):
    import trade_ledger
    from webui.state import trade_analysis

    monkeypatch.setattr(trade_ledger, "trade_liste", lambda **_kwargs: [])
    empty = trade_analysis(tage=30)
    assert empty["kumulierte_ergebnisse"] == []
    assert empty["kumulierte_ergebnisse_nach_waehrung"] == {}

    closed = {"trade_id": 1, "broker": "okx", "symbol": "BTC", "waehrung": "EUR",
              "eingestiegen_am": "2026-08-30T10:00:00Z", "einstieg_preis": 100,
              "menge": 1, "ausgestiegen_am": "2026-08-31T10:00:00Z",
              "ausstieg_preis": 104, "netto_pnl": 4, "gebuehren": 0, "fee_quality": "CONFIRMED"}
    monkeypatch.setattr(trade_ledger, "trade_liste", lambda **_kwargs: [closed])
    single = trade_analysis(tage=30)
    assert single["kumulierte_ergebnisse"] == single["kumulierte_ergebnisse_nach_waehrung"]["EUR"]
    assert single["kumulierte_ergebnisse"][0]["wert"] == 4


def test_trade_endpoint_keeps_positive_negative_curves_separate(monkeypatch, tmp_path):
    import decision_analytics
    import trade_ledger
    from webui.state import trade_analysis

    monkeypatch.setattr(decision_analytics, "DB_PATH", tmp_path / "curves.sqlite")
    start = datetime.now(timezone.utc) - timedelta(days=2)
    trades = [
        ("okx", "BTC", "EUR", 100, 96, 1),
        ("okx", "ETH", "EUR", 100, 108, 2),
        ("etoro", "AAPL", "USD", 100, 103, 3),
    ]
    for broker, symbol, currency, entry, exit_price, decision_id in trades:
        assert trade_ledger.trade_open(broker=broker, symbol=symbol, menge=1,
            einstieg_preis=entry, waehrung=currency, decision_id=decision_id, gebuehr=0.0,
            zeit=start + timedelta(hours=decision_id), paper=True, broker_account_fingerprint="scoped-test")
        assert trade_ledger.trade_close(broker=broker, symbol=symbol, menge=1,
            ausstieg_preis=exit_price, waehrung=currency, gebuehr=0.0,
            zeit=start + timedelta(hours=decision_id + 1), paper=True, broker_account_fingerprint="scoped-test")

    data = trade_analysis(tage=30)
    assert data["kumulierte_ergebnisse"] == []  # keine Addition fremder Waehrungen
    assert set(data["kumulierte_ergebnisse_nach_waehrung"]) == {"EUR", "USD"}
    eur = data["kumulierte_ergebnisse_nach_waehrung"]["EUR"]
    assert eur[0]["wert"] < 0 and eur[-1]["wert"] > 0
    assert len(data["kumulierte_ergebnisse_nach_waehrung"]["USD"]) == 1
