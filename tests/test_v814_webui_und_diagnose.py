"""WebUI-Korrekturen und Universumsdiagnose (v8.1.4)."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("TRADINGBOT_TEST_STATE_DIR", "/tmp/tradingbot_tests")
Path(os.environ["TRADINGBOT_TEST_STATE_DIR"]).mkdir(parents=True, exist_ok=True)

WURZEL = Path(__file__).resolve().parent.parent
WEBUI = WURZEL / "webui"


# ---------------------------------------------------------------------------
# Versionsnummer
# ---------------------------------------------------------------------------
def test_keine_hartkodierte_version_in_der_webui():
    """Bis v8.1.3 stand "8.1.2 NEXUS" an neun Stellen fest im Code.

    Geprueft wird, was der Nutzer SIEHT. Erklaerende Kommentare duerfen eine
    alte Version nennen ("bis v8.1.3 stand hier ...") -- die rendert nichts.
    """
    import re

    def ohne_kommentare(text: str, dateiart: str) -> str:
        zeilen = []
        for zeile in text.split("\n"):
            blank = zeile.strip()
            if dateiart == "js" and blank.startswith("//"):
                continue
            if dateiart == "py" and blank.startswith("#"):
                continue
            zeilen.append(zeile)
        roh = "\n".join(zeilen)
        if dateiart == "py":
            # Docstrings entfernen -- sie erklaeren, sie zeigen nicht an.
            import ast
            try:
                baum = ast.parse(roh)
                for knoten in ast.walk(baum):
                    if isinstance(knoten, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                           ast.AsyncFunctionDef)) and ast.get_docstring(knoten):
                        knoten.body = knoten.body[1:]
                roh = ast.unparse(baum)
            except SyntaxError:
                pass
        return roh

    treffer = []
    for datei in list(WEBUI.rglob("*.py")) + list(WEBUI.rglob("*.html")) + list(WEBUI.rglob("*.js")):
        if "__pycache__" in str(datei):
            continue
        art = datei.suffix.lstrip(".")
        text = ohne_kommentare(datei.read_text(encoding="utf-8"), art)
        for fund in re.findall(r"\b8\.\d+\.\d+\b", text):
            treffer.append(f"{datei.name}: {fund}")
    assert treffer == [], f"Sichtbare hartkodierte Versionen gefunden: {treffer}"


def test_webui_zeigt_die_version_aus_der_config():
    pytest.importorskip("fastapi")
    import config
    import webui.app as app
    assert app._version() in str(config.VERSION_NEXUS)
    assert app._version() != "?"


def test_alle_seiten_haben_den_platzhalter():
    for name in ("dashboard.html", "logbook.html", "settings.html", "universe.html"):
        text = (WEBUI / "templates" / name).read_text(encoding="utf-8")
        assert "{{VERSION}}" in text, f"{name} hat keinen Versionsplatzhalter"


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------
def test_dashboard_raster_passt_sich_an():
    """Vier feste Spalten fuer inzwischen zwoelf Karten reissen das Raster auf."""
    css = (WEBUI / "static" / "app.css").read_text(encoding="utf-8")
    assert "repeat(auto-fit,minmax(280px,1fr))" in css
    assert "grid-template-columns:repeat(4,1fr)" not in css


def test_dashboard_zeigt_ortszeit():
    """Die Entscheidungen stehen als UTC in der Datenbank."""
    js = (WEBUI / "static" / "dashboard.js").read_text(encoding="utf-8")
    assert "function ortszeit" in js
    assert "toLocaleString('de-DE'" in js
    assert "(r.created_at_utc||'').replace('T',' ')" not in js, \
        "Der rohe UTC-Text darf nicht mehr angezeigt werden"


def test_dashboard_zeigt_mehr_als_sechs_zeilen():
    quelle = (WEBUI / "state.py").read_text(encoding="utf-8")
    assert "latest(15)" in quelle


def test_dashboard_hat_okx_karten():
    js = (WEBUI / "static" / "dashboard.js").read_text(encoding="utf-8")
    assert "function brokerOverview" in js
    for teil in ("Neue Käufe", "Botpositionen", "kaeufe_erlaubt", "worker_alive", "mode_mismatch"):
        assert teil in js, f"Karte {teil} fehlt"


# ---------------------------------------------------------------------------
# KI-Test
# ---------------------------------------------------------------------------
def test_ki_test_endpunkt_existiert():
    quelle = (WEBUI / "app.py").read_text(encoding="utf-8")
    assert '"/api/ai/test"' in quelle
    js = (WEBUI / "static" / "settings.js").read_text(encoding="utf-8")
    assert "function testAI" in js
    html = (WEBUI / "templates" / "settings.html").read_text(encoding="utf-8")
    assert "testAI()" in html


def test_second_opinion_ist_ueber_die_webui_schaltbar():
    html = (WEBUI / "templates" / "settings.html").read_text(encoding="utf-8")
    assert 'data-field="second_opinion.mode"' in html
    for wert in ("aus", "nur_live", "immer"):
        assert f'value="{wert}"' in html, f"Betriebsart {wert} fehlt"


def test_second_opinion_einstellungen_wirken_sofort(tmp_path, monkeypatch):
    """Wie die Nachrichtenschalter seit v8.1.2: ohne Neustart."""
    monkeypatch.setenv("TRADINGBOT_TEST_STATE_DIR", str(tmp_path))
    import json
    import live_settings
    import second_opinion as so

    # live_settings liest neben dem Quelltext, nicht im Arbeitsverzeichnis.
    monkeypatch.setattr(live_settings, "ROOT", tmp_path)
    live_settings.verwerfe_cache()
    (tmp_path / "second_opinion_settings.json").write_text(
        json.dumps({"mode": "immer", "pending_expiry_minutes": 20}), encoding="utf-8")
    live_settings.verwerfe_cache()
    assert so.modus() == "immer"
    assert so.verfallszeit_minuten() == pytest.approx(20.0)


# ---------------------------------------------------------------------------
# Universumsdiagnose
# ---------------------------------------------------------------------------
def test_diagnose_zaehlt_die_filter():
    """2 von 581 -- aber welcher Filter hat die 579 verworfen?"""
    import universe_diagnose as ud
    abgelehnt = (
        [("X-USDT", "handelt gegen USDT, erlaubt sind EUR, USDC")] * 400
        + [("A-EUR", "Tagesumsatz 12.345 unter Minimum 2.000.000")] * 150
        + [("B-EUR", "Spanne 1.20 % ueber Grenze 0.60 %")] * 29
    )
    diagnose = ud.bericht({"broker": "okx", "katalog_gesamt": 581, "pool": 2,
                           "abgelehnt": abgelehnt, "rangliste": []})
    assert diagnose["abgelehnt_gesamt"] == 579
    assert diagnose["groesster_filter"] == "andere Quotewaehrung"
    gruppen = {g["gruppe"]: g["anzahl"] for g in diagnose["gruppen"]}
    assert gruppen["andere Quotewaehrung"] == 400
    assert gruppen["Tagesumsatz zu klein"] == 150
    assert gruppen["Spanne zu weit"] == 29


def test_diagnose_liefert_ein_beispiel_je_gruppe():
    import universe_diagnose as ud
    diagnose = ud.bericht({"abgelehnt": [("BTC-USDT", "handelt gegen USDT")],
                           "katalog_gesamt": 1, "pool": 0, "rangliste": []})
    assert "BTC-USDT" in diagnose["gruppen"][0]["beispiel"]


def test_diagnose_textbericht_ist_lesbar():
    import universe_diagnose as ud
    text = ud.textbericht(ud.bericht({
        "katalog_gesamt": 581, "pool": 2, "rangliste": [],
        "abgelehnt": [("A", "Tagesumsatz 1 unter Minimum 2")] * 5}))
    assert "581 Instrumente" in text and "2 im Pool" in text
    assert "Tagesumsatz zu klein" in text


def test_diagnose_aendert_keine_schwelle():
    """Erst messen, dann reden -- das Modul darf nichts verstellen."""
    import ast
    baum = ast.parse((WURZEL / "universe_diagnose.py").read_text(encoding="utf-8"))
    code = ast.unparse(baum)
    for verboten in ("setattr", "config.", "MIN_QUOTE_VOLUME", "MAX_SPREAD"):
        assert verboten not in code, f"{verboten} gehoert nicht in die Diagnose"


def test_diagnose_wirft_nie():
    import universe_diagnose as ud
    assert ud.protokolliere({"abgelehnt": "kaputt"}) is not None
    assert ud.zaehle(None) == []
    assert ud.gruppiere(None) == "sonstiges"


def test_kryptolauf_erzeugt_eine_diagnose():
    quelle = (WURZEL / "crypto_engine.py").read_text(encoding="utf-8")
    assert "universe_diagnose.protokolliere(auswahl)" in quelle
    assert '"diagnose": diagnose' in quelle
