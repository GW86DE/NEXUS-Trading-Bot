"""9.5.5 -- OKX-Fehlercodes im Klartext.

Georgs Vorgabe nach dem Vorfall vom 02.09.2026: "Lass uns aus dem
Verbindungsproblem mit OKX lernen und schreibe bei dem Problem zusaetzlich
verstaendlich, was der Fehlercode heisst, damit kuenftig solche Fehler direkt
geklaert werden."

Damals stand 25 Minuten lang nur "OKX-Serverfehler HTTP 503" im Log. Ob es an
den Zugangsdaten lag, an der Uhr, am Konto oder an OKX selbst, ging daraus
nicht hervor -- geklaert wurde es erst mit einem eigens gebauten Skript.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def test_die_beiden_codes_aus_dem_vorfall_sind_erklaert():
    """50001 und 50101 sind genau die, die Georg gesehen hat."""
    from okx_fehlercodes import klartext, klasse, WARTUNG, ZUGANG

    assert klasse("50001") == WARTUNG
    assert "OKX" in klartext("50001") and "nicht an den Zugangsdaten" in klartext("50001")

    assert klasse("50101") == ZUGANG
    assert "Umgebung" in klartext("50101")


def test_unbekannter_code_wird_nicht_erfunden():
    """Eine erfundene Erklaerung waere schlimmer als keine."""
    from okx_fehlercodes import klartext, klasse, ergaenze

    assert klartext("99999") == ""
    assert klasse("99999") == ""
    # Die Originalmeldung bleibt unveraendert erhalten.
    assert ergaenze("OKX-Fehler 99999: irgendwas", "99999") == \
        "OKX-Fehler 99999: irgendwas"


def test_jede_klasse_sagt_was_zu_tun_ist():
    """Der Klartext muss handlungsleitend sein, nicht nur uebersetzt."""
    from okx_fehlercodes import FEHLERCODES

    assert len(FEHLERCODES) >= 20
    for code, (kl, text) in FEHLERCODES.items():
        assert kl, f"{code} ohne Klasse"
        assert len(text) > 40, f"{code}: Erklaerung zu duenn"
        assert text.endswith("."), f"{code}: kein ganzer Satz"


def test_zeitfehler_nennt_die_wahrscheinliche_ursache():
    """50102 ist der Fall, bei dem die Pi-Uhr zu pruefen ist."""
    from okx_fehlercodes import klartext, klasse, ZEIT

    assert klasse("50102") == ZEIT
    assert "UTC" in klartext("50102")


def test_unklarer_ausgang_warnt_vor_der_doppelorder():
    """50004 darf nie als Misserfolg durchgehen."""
    from okx_fehlercodes import klartext, klasse, UNKLAR

    assert klasse("50004") == UNKLAR
    text = klartext("50004")
    assert "WEDER" in text and "Doppelorder" in text


def test_brokerfehler_traegt_den_klartext():
    """Der Weg von der OKX-Antwort bis in die Fehlermeldung."""
    from broker.okx import _mit_klartext

    meldung = _mit_klartext(
        "OKX-Serverfehler HTTP 503 (GET /account/balance, privat, Demo, "
        "OKX-Code 50001: Service temporarily unavailable).", "50001")
    assert "WARTUNG" in meldung
    assert "warten und erneut versuchen" in meldung
    # Der Aufrufort aus 9.5.3 bleibt erhalten.
    assert "/account/balance" in meldung
