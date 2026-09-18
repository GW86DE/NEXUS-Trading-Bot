"""v9.1 -- Zustaende, Sperren und was die Oberflaeche sagt.

Sammelt die Regressionen zu den mittleren Befunden aus der 9.0.15-Pruefung.
Jeder Test haelt genau einen fest, damit er nicht zurueckkommt.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from test_v975_execution_and_repair import engine

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("TRADINGBOT_TEST_STATE_DIR", "/tmp/tradingbot_tests")
Path(os.environ["TRADINGBOT_TEST_STATE_DIR"]).mkdir(parents=True, exist_ok=True)

WURZEL = Path(__file__).resolve().parent.parent


# ===========================================================================
# 5+6 -- manuelle Verwaltung nach fehlgeschlagenem oder teilweisem Verkauf
# ===========================================================================
def test_fehlgeschlagener_verkauf_rollt_die_verwaltung_zurueck(engine, monkeypatch):
    # Real queue/engine regression replaces the obsolete source-text assertion.
    from test_v982_acceptance import manual_setup
    from broker.base import BrokerFehler
    controls, p, cmd, calls = manual_setup(engine, monkeypatch)
    previous = p.verwaltung
    def fail(*args, **kw):
        raise BrokerFehler('synthetic quote failure')
    engine.broker.execution_quote = fail
    engine._verarbeite_manuelle_auftraege(engine.broker)
    assert p.verwaltung == previous and p.exit_state == 'IDLE' and not calls
    assert controls.overview()['commands'][0]['status'] == 'FAILED'


@pytest.mark.parametrize('accounting_pending', [False, True])
def test_unklarer_verkauf_bleibt_manuell(engine, monkeypatch, accounting_pending):
    """Neither uncertain execution nor failed accounting may send another sell."""
    from broker.base import OrderErgebnis
    from test_v982_acceptance import manual_setup
    import trade_ledger
    controls, p, cmd, calls = manual_setup(engine, monkeypatch)
    quantity = p.menge
    def response(*args, **kwargs):
        calls.append(kwargs)
        return OrderErgebnis(
            status='filled' if accounting_pending else 'live',
            terminal=accounting_pending, fill_evidence_complete=accounting_pending,
            filled_quantity=quantity if accounting_pending else 0,
            avg_fill_price=.83, fees_quote=0,
            order_ids=['200'], fill_ids=['okx:A:SUI-USDC:200:1'] if accounting_pending else [],
            client_order_id=kwargs['client_order_id'], paper=True,
            account_fingerprint='A', broker_environment='DEMO')
    def failed_write(**kwargs):
        raise OSError('synthetic ledger write failure')
    engine.broker.schliesse_position = response
    if accounting_pending:
        monkeypatch.setattr(trade_ledger, 'trade_close', failed_write)
    engine._verarbeite_manuelle_auftraege(engine.broker)
    assert p.verwaltung == 'MANUELL'
    assert p.exit_state == ('ACCOUNTING_PENDING' if accounting_pending else 'UNCLEAR')
    assert p.menge == quantity
    assert controls.overview()['commands'][0]['status'] == 'UNCLEAR'
    engine._verarbeite_manuelle_auftraege(engine.broker)
    assert len(calls) == 1


def test_teilverkauf_setzt_die_sperre_und_meldet_richtig():
    """PARTIAL fiel in den else-Zweig und hiess "nicht ausgefuehrt"."""
    quelle = (WURZEL / "crypto_engine.py").read_text(encoding="utf-8")
    stelle = quelle.index('elif outcome == "PARTIAL":')
    block = quelle[stelle:stelle + 1200]
    assert "controls.add_lock(" in block, "Die angeforderte Sperre muss gesetzt werden"
    assert '"PARTIAL"' in block
    assert "teilweise" in block.lower()


# ===========================================================================
# 8 + 16 -- Wiedereinstiegssperren
# ===========================================================================
def test_unlesbare_sperre_blockiert_statt_freizugeben():
    """fail-CLOSED. Ein Lesefehler bedeutete bis 9.0.15 "keine Sperre"."""
    quelle = (WURZEL / "crypto_engine.py").read_text(encoding="utf-8")
    stelle = quelle.index("lock_reason = manual_trade_control.lock_reason")
    block = quelle[stelle:stelle + 900]
    assert 'lock_reason = ""' not in block, \
        "Ein Zweifel muss an einem Sicherheitsgate sperren, nicht freigeben"
    assert "vorsorglich blockiert" in block


def test_unlesbares_ablaufdatum_haelt_die_sperre(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADINGBOT_TEST_STATE_DIR", str(tmp_path))
    import manual_trade_control as mtc

    aktiv = mtc._active_locks([{"id": "x", "symbol": "BTC", "until": "kein datum"}])
    assert len(aktiv) == 1, "Eine Sperre darf nicht an einem Textfehler verschwinden"


def test_unbekannte_sperrdauer_wirft_einen_gefangenen_fehler(tmp_path, monkeypatch):
    """Vorher ein ungefangener KeyError -- nach einem ERFOLGREICHEN Verkauf."""
    monkeypatch.setenv("TRADINGBOT_TEST_STATE_DIR", str(tmp_path))
    import manual_trade_control as mtc

    with pytest.raises(ValueError, match="Sperrdauer"):
        mtc.add_lock(broker="okx", account_fingerprint="a", symbol="BTC",
                     duration="99X", reason="test", actor="test")


def test_tagessperre_nutzt_die_konfigurierte_zeitzone():
    quelle = (WURZEL / "manual_trade_control.py").read_text(encoding="utf-8")
    assert 'LOCAL_TIMEZONE' in quelle, \
        "Die Tagesgrenze darf nicht fest auf Europe/Berlin stehen"


def test_dateisperre_gilt_auch_unter_windows():
    """WebUI und Handelskern sind zwei Prozesse -- auch dort."""
    quelle = (WURZEL / "manual_trade_control.py").read_text(encoding="utf-8")
    assert "msvcrt" in quelle
    assert "LK_LOCK" in quelle and "LK_UNLCK" in quelle


# ===========================================================================
# 11 -- Dust
# ===========================================================================
def test_dust_misst_die_verkaufbarkeit_nicht_die_eroeffnungsschwelle():
    """15,00 ist die Schwelle fuer eine NEUE Position, nicht fuer einen Rest.

    Damit galt ein Rest von 11,55 USDC als "nicht handelbarer Staub", obwohl
    er das Hundertfache der OKX-Mindestgroesse war -- und verlor Client-Stop,
    Broker-Schutz und Ledgerzeile.
    """
    import ast, inspect
    import crypto_engine

    quelle = inspect.getsource(crypto_engine.CryptoEngine._rest_lohnt_sich)
    baum = ast.parse(quelle.lstrip())
    # Kommentare duerfen den alten Wert erklaeren -- benutzt werden darf er nicht.
    namen = {k.value for k in ast.walk(baum)
             if isinstance(k, ast.Constant) and isinstance(k.value, str)}
    argumente = {k.args[1].value for k in ast.walk(baum)
                 if isinstance(k, ast.Call) and isinstance(k.func, ast.Name)
                 and k.func.id == "getattr" and len(k.args) > 1
                 and isinstance(k.args[1], ast.Constant)}
    assert "OKX_MIN_POSITION_VALUE" not in argumente, \
        "Die Eroeffnungsschwelle ist der falsche Massstab fuer einen Rest"
    assert "OKX_DUST_VALUE_LIMIT" in argumente
    assert "min_size" in namen or "min_size" in quelle


# ===========================================================================
# 12 -- lokaler Handelstag
# ===========================================================================
def test_handelstag_wird_mit_derselben_zeitzone_gelesen_wie_geschrieben():
    """Auf einem Pi mit UTC-Systemuhr zeigte das Dashboard nachts den Vortag."""
    from datetime import datetime, timezone
    import decision_analytics as da

    # 30.08.2026 00:30 Berlin = 29.08.2026 22:30 UTC
    nacht = datetime(2026, 8, 29, 22, 30, tzinfo=timezone.utc)
    assert da.lokaler_handelstag(nacht) == "2026-08-30", \
        "Geschrieben wird mit LOCAL_TIMEZONE -- gelesen muss es genauso werden"


def test_summary_und_export_nutzen_denselben_tag():
    for datei in ("decision_analytics.py", "telegram_exports.py"):
        quelle = (WURZEL / datei).read_text(encoding="utf-8")
        assert 'datetime.now().astimezone().strftime("%Y-%m-%d")' not in quelle, \
            f"{datei} liest den Tag noch mit der Systemzeitzone"


# ===========================================================================
# 13 + 14 -- Gebuehrensatz und ROI-Anzeige
# ===========================================================================
def test_trades_seite_nutzt_den_gemessenen_gebuehrensatz():
    """Zwei Zahlen fuer dieselbe Gebuehr in derselben Oberflaeche."""
    quelle = (WURZEL / "trade_chart_data.py").read_text(encoding="utf-8")
    assert 'fee_pct = float(getattr(config, "OKX_TAKER_FEE_PCT"' not in quelle
    assert "_gemessener_taker_satz(" in quelle


def test_nur_auto_positionen_zeigen_ein_roi_ziel():
    """Der Kern prueft nur AUTO -- bei BEOBACHTEN kaeme der Ausstieg nie."""
    for datei in ("webui/state.py", "trade_chart_data.py"):
        quelle = (WURZEL / datei).read_text(encoding="utf-8")
        assert '!= "MANUELL"' not in quelle, f"{datei} filtert noch auf MANUELL statt auf AUTO"


# ===========================================================================
# 15 -- verborgene Felder
# ===========================================================================
def test_exit_zustand_wird_in_der_oberflaeche_angezeigt():
    """Retry-Cooldown und 24-Stunden-Sperre waren unsichtbar."""
    js = (WURZEL / "webui" / "static" / "trades.js").read_text(encoding="utf-8")
    assert "exitZustandText" in js
    for feld in ("exit_state", "exit_retry_after", "management_note"):
        assert feld in js, f"{feld} wird berechnet, aber nicht angezeigt"
    assert "UNKLAR" in js


# ===========================================================================
# 16 -- MANUELL im Telegram-Status
# ===========================================================================
def test_manuelle_position_erscheint_im_telegram_status():
    """Jede TP/SL-Aenderung macht eine Position MANUELL -- sie darf nicht
    dadurch aus dem Status verschwinden, inklusive Schutzwarnung."""
    import okx_status

    daten = {"positionen": [{
        "symbol": "BTC", "menge": 0.1, "einstieg": 50000, "stop": 45000,
        "ownership_verified": True, "verwaltung": "MANUELL",
        "order_id": "o1", "client_order_id": "c1", "fill_ids": ["f1"],
        "broker_schutz": False,
    }]}
    zeilen = okx_status.positionszeilen(daten)
    assert len(zeilen) == 1
    assert "BTC" in zeilen[0]
    assert "ohne Broker-Schutz" in zeilen[0], "Die Warnung darf nicht entfallen"
    assert "MANUELL" in zeilen[0]


# ===========================================================================
# 17 -- deutsche Zahlen
# ===========================================================================
def test_betraege_stehen_im_deutschen_format():
    """"1,234.57 EUR" liest sich fuer einen deutschen Leser als 1,23 EUR."""
    from meldungen import betragstext, betragstext_vz

    assert betragstext(1234.567) == "1.234,57"
    assert betragstext_vz(-1234.56) == "-1.234,56"
    assert betragstext_vz(98.0) == "+98,00"


def test_kaufmeldung_mischt_keine_formate():
    import meldungen

    text = meldungen.kauf(broker="okx", symbol="BTC", menge=0.0246913,
                          preis=50000.0, waehrung="EUR", gebuehr=1234.5678,
                          gebuehr_pct=0.001, stop=45000.0, ziel=55000.0)
    assert "1,234.57" not in text, "englisches Format in einer deutschen Meldung"
    assert "1.234,57" in text


# ===========================================================================
# 18 -- keine Flut in der Entscheidungsstatistik
# ===========================================================================
def test_unveraenderte_scansperre_wird_nur_einmal_protokolliert():
    """288 Zeilen pro Tag verdraengten die echten Ablehnungsgruende."""
    quelle = (WURZEL / "crypto_engine.py").read_text(encoding="utf-8")
    stelle = quelle.index("def _scan_blockiert")
    block = quelle[stelle:stelle + 1800]
    assert "_letzte_scan_sperre" in block
    # Und nach einem durchgelaufenen Scan wird wieder protokolliert.
    assert "self._letzte_scan_sperre = None" in quelle


# ===========================================================================
# 19 -- Unbekanntes bleibt unbekannt
# ===========================================================================
def test_unbekanntes_ergebnis_wird_nicht_als_null_gebucht():
    """0,00 saehe aus wie ausgeglichen. Eigener Fehler aus 8.1.5."""
    quelle = (WURZEL / "position_manager.py").read_text(encoding="utf-8")
    # Beide Stellen: die Brokerabfrage und die geschriebene Zeile.
    stellen = [i for i in range(len(quelle))
               if quelle.startswith('"unrealisiert":', i)]
    assert len(stellen) >= 2
    for i in stellen:
        zeile = quelle[i:quelle.index("\n", i)]
        assert "None" in zeile, f"Unbekannt darf nicht 0,00 werden: {zeile.strip()}"


# ===========================================================================
# 20 -- ehrliche Berichtszeit
# ===========================================================================
def test_tagesbericht_traegt_die_echte_uhrzeit():
    quelle = (WURZEL / "daily_report.py").read_text(encoding="utf-8")
    assert '· 18:00"' not in quelle
    assert '%d.%m.%Y · %H:%M' in quelle


def test_tagesbericht_nutzt_das_handelsbuch():
    """Sonst nennt /bericht eine dritte Zahl fuer denselben Tag."""
    quelle = (WURZEL / "daily_report.py").read_text(encoding="utf-8")
    assert "_tagesergebnis_netto()" in quelle
    assert "import tagesbuch" in quelle


# ===========================================================================
# B4 -- /pnl ueber beide Broker
# ===========================================================================
def test_pnl_deckt_beide_broker_ab():
    """"Tagesergebnis gesamt" zeigte nur eToro -- die Kryptoseite fehlte."""
    quelle = (WURZEL / "berichte.py").read_text(encoding="utf-8")
    stelle = quelle.index("def pnl_text")
    block = quelle[stelle:stelle + 2600]
    assert 'tagesbuch.tagesergebnis(\n            waehrung=waehrung' in block or \
           "gesamt = tagesbuch.tagesergebnis(" in block
    assert "davon" in block, "Die Aufschluesselung je Broker gehoert dazu"


# ===========================================================================
# Latente Punkte
# ===========================================================================
def test_zustandsdateien_loesen_absolut_auf():
    """Ein relativer Name loeste gegen das Arbeitsverzeichnis auf."""
    from bot_zustand import BotZustand
    from runtime_status import RuntimeStatus

    assert Path(BotZustand("bot_zustand.json").pfad).is_absolute()
    assert Path(RuntimeStatus("runtime_status.json").path).is_absolute()


def test_fremdwaehrungsgebuehr_faelscht_die_quotesumme_nicht():
    quelle = (WURZEL / "broker" / "okx.py").read_text(encoding="utf-8")
    assert "ccy in set(self.allowed_quotes)" not in quelle, \
        "Eine EUR-Gebuehr auf einem USD-Markt darf nicht 1:1 addiert werden"


def test_geloeschte_modusdatei_hebt_die_pause_nicht_auf(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADINGBOT_TEST_STATE_DIR", str(tmp_path))
    import crypto_strategy_mode as modus

    assert modus.current_mode() == modus.NEXUS_STANDARD    # Erststart
    modus.set_mode(modus.CRYPTO_PAUSED, source="test", notify=False)
    modus.path().unlink()
    assert modus.current_mode() == modus.CRYPTO_PAUSED, \
        "Wer die Datei loescht, darf damit keine Pause aufheben"


def test_moduswechsel_meldung_ueberlebt_einen_revisionsruecksprung():
    quelle = (WURZEL / "crypto_strategy_mode.py").read_text(encoding="utf-8")
    stelle = quelle.index("event_id=f\"crypto-strategy-mode:")
    assert "{changed_at}" in quelle[stelle:stelle + 120], \
        "Nach einer Reparatur springt revision auf 1 zurueck"
