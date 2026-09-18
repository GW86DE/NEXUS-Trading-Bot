"""Ein freigegebener Kauf ist noch kein ausgefuehrter Kauf (v9.3, CR-05).

Beim WLD-Beispiel war die Handelsentscheidung korrekt freigegeben. Die
FOK-Order wurde von OKX aber mit ``0 / 270,94`` storniert -- richtig so, denn
FOK heisst: ganz oder gar nicht, und nicht oberhalb der Preisgrenze.

In der Oberflaeche stand trotzdem gruen ``APPROVED``. Das sieht aus wie ein
erfolgreicher Kauf. Die technischen Belege (``cancelSource``, ``sCode``,
Limitpreis, verfuegbare Menge) landeten nur als angehaengter Freitext im
Hinweis und waren spaeter nicht mehr auswertbar.
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


def ergebnis(**felder):
    from broker.base import OrderErgebnis
    basis = {"requested_quantity": 270.94, "filled_quantity": 0.0,
             "terminal": True, "raw_status": "canceled"}
    basis.update(felder)
    return OrderErgebnis(**basis)


def test_fok_ohne_fill_ist_kein_kauf():
    r = ergebnis()
    assert r.execution_status == "CANCELED_NO_FILL"
    assert r.final_status == "NOT_EXECUTED"


def test_vollstaendig_gefuellte_fok_ist_ein_kauf():
    r = ergebnis(filled_quantity=270.94, raw_status="filled")
    assert r.execution_status == "FILLED"
    assert r.final_status == "EXECUTED"


def test_unerwarteter_teilfill_wird_nicht_als_erfolg_verbucht():
    """Ein Teilfill bei FOK ist eine Sicherheitsstoerung. Der Fill wird
    uebernommen -- als voller Erfolg gilt er nicht."""
    r = ergebnis(filled_quantity=100.0, raw_status="filled")
    assert r.execution_status == "PARTIAL"
    assert r.final_status == "EXECUTED", "Die 100 Stueck gehoeren dem Bot"


def test_nicht_terminaler_ausgang_bleibt_unklar():
    """Kein negativer Schluss aus einer unvollstaendigen Datenlage."""
    r = ergebnis(terminal=False, raw_status="live")
    assert r.execution_status == "UNKNOWN"
    assert r.final_status == "UNKLAR"


def test_ablehnung_und_verfall_sind_unterscheidbar():
    assert ergebnis(raw_status="rejected").execution_status == "REJECTED"
    assert ergebnis(raw_status="expired").execution_status == "EXPIRED"


def test_cancel_source_wird_strukturiert_gespeichert():
    """cancelSource erscheint erst im finalen Orderstatus -- er muss
    trotzdem erhalten bleiben."""
    quelle = (WURZEL / "broker" / "okx.py").read_text(encoding="utf-8")
    assert "execution_evidence={" in quelle
    for feld in ('"cancel_source"', '"s_code"', '"s_msg"', '"limit_price"',
                 '"requested_qty"', '"filled_qty"', '"ord_type"', '"ord_id"'):
        assert feld in quelle, f"{feld} fehlt in der Ausfuehrungsevidenz"


def test_klartext_nennt_menge_limit_und_brokergrund():
    import crypto_engine as ce

    beleg = {"ord_type": "FOK", "limit_price": 1.2345, "requested_qty": 270.94,
             "cancel_source": "1", "s_code": "51008",
             "s_msg": "Order failed. Insufficient liquidity"}
    text = ce.CryptoEngine._nichtausfuehrungs_text(ergebnis(), beleg)

    assert "nicht ausgefuehrt" in text
    assert "0/270.94" in text
    assert "cancelSource=1" in text
    assert "51008" in text


def test_die_oberflaeche_faerbt_nur_einen_echten_kauf_gruen():
    logbuch = (WURZEL / "webui" / "static" / "logbook.js").read_text(encoding="utf-8")
    assert "function entscheidungsmarke" in logbuch
    # Unknown outcomes must not be labelled as definitely unfilled; partial fills remain explicit.
    assert "APPROVED · Ausführung prüfen" in logbuch
    assert "APPROVED · teilweise ausgeführt" in logbuch
    assert "e === 'FILLED'" in logbuch, (
        "Gruen darf ausschliesslich an einer tatsaechlichen Ausfuehrung haengen")

    dashboard = (WURZEL / "webui" / "static" / "dashboard.js").read_text(encoding="utf-8")
    assert "execution_status" in dashboard
    assert "r.status==='APPROVED'?'ok':'warn'" not in dashboard, (
        "Die alte Faerbung ohne Ausfuehrungspruefung darf nicht zurueckkommen")


def test_keine_offene_position_wird_vorgetaeuscht():
    """Nach einer Nichtausfuehrung darf weder eine Position noch eine
    Schutzorder behauptet werden."""
    r = ergebnis()
    assert r.filled_quantity == 0.0
    assert not r.stop_order_platziert
    assert not r.take_order_platziert
    assert not r.position_ids or r.final_status == "NOT_EXECUTED"
