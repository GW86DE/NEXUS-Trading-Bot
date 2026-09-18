"""10.7.0 Teil D -- Aktien-Stop mit Mindestabstand und Bezug auf den Kaufkurs.

BEFUND VOM 18.09.2026 (Diagnose 14:39 UTC, Entscheidung 3345005797905124216)
============================================================================
CSCO: Signalkurs 109,91, Stop 109,1193 (0,72 % -- Profil OFFENSIV, ATR x1,5),
Take 111,4914. Der Kurs fiel bis zur Ausfuehrung auf 109,15 (-0,69 %). Der
Stop ging unveraendert als openStopLossRate mit der Order zu eToro und lag
damit 0,028 % = 3 Cent unter dem Einstieg. eToro schloss die Position nach
1,5 Sekunden bei 109,09, bevor der Bot sie ueberhaupt bestaetigt hatte.

AMD am selben Tag: Stop 0,87 % unter dem Signal, Fill 0,47 % ueber dem Stop,
nach 26 Minuten ausgestoppt (-71,12 USD).

Zwei Fehler: kein Mindestabstand fuer Aktien (Krypto hat ihn seit 8.1.4),
und der Stop bezog sich auf den Signalkurs des Scans statt auf den Kurs, zu
dem gekauft wird.
"""
from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

CSCO_SIGNAL, CSCO_STOP, CSCO_TAKE, CSCO_ASK = 109.91, 109.1193, 111.4914, 109.15


@pytest.fixture
def plan(monkeypatch):
    import config
    monkeypatch.setattr(config, "ETORO_MIN_STOP_DISTANCE_PCT", 0.010, raising=False)
    from live_trader import aktien_stop_plan
    return aktien_stop_plan


def test_csco_bekommt_den_mindestabstand_vor_der_mengenberechnung(plan):
    p = plan("stock", CSCO_SIGNAL, CSCO_STOP, CSCO_TAKE)
    assert p["referenz"] == CSCO_SIGNAL
    assert p["stop"] == pytest.approx(CSCO_SIGNAL * 0.99, abs=1e-4)
    assert p["stop_pct"] == pytest.approx(0.010)
    assert p["take"] == pytest.approx(CSCO_TAKE, abs=1e-4), "Das Ziel bleibt Strategieziel"
    assert "Mindestabstand" in p["hinweis"]


def test_csco_stop_folgt_dem_frischen_kaufkurs(plan):
    """Mit dem frischen Briefkurs 109,15 liegt der Stop 1 % darunter -- nicht 3 Cent."""
    p = plan("stock", CSCO_SIGNAL, CSCO_STOP, CSCO_TAKE, ask=CSCO_ASK)
    assert p["referenz"] == CSCO_ASK
    assert p["stop"] == pytest.approx(CSCO_ASK * 0.99, abs=1e-4)
    abstand = (CSCO_ASK - p["stop"]) / CSCO_ASK
    assert abstand >= 0.0099, f"Abstand zum Kaufkurs nur {abstand * 100:.3f} %"
    take_pct = (CSCO_TAKE - CSCO_SIGNAL) / CSCO_SIGNAL
    assert p["take"] == pytest.approx(CSCO_ASK * (1 + take_pct), abs=1e-4)
    assert "frischen Kaufkurs" in p["hinweis"]


def test_der_alte_ablauf_haette_drei_cent_abstand_gehabt():
    """Gegenprobe ohne 10.7.0: genau der Zustand, der CSCO in 1,5 s beendete."""
    assert (CSCO_ASK - CSCO_STOP) / CSCO_ASK == pytest.approx(0.00028, abs=1e-5)


def test_weiter_stop_bleibt_unveraendert(plan):
    """AUSGEWOGEN/KONSERVATIV setzen 2-2,5 % -- da greift der Mindestabstand nicht."""
    p = plan("stock", 100.0, 98.0, 106.0)
    assert p["stop"] == pytest.approx(98.0) and p["take"] == pytest.approx(106.0)
    assert p["hinweis"] == ""


def test_steigender_kurs_verschiebt_stop_und_ziel_mit(plan):
    p = plan("stock", 100.0, 98.0, 106.0, ask=101.0)
    assert p["stop"] == pytest.approx(101.0 * 0.98, abs=1e-4)
    assert p["take"] == pytest.approx(101.0 * 1.06, abs=1e-4)


def test_krypto_und_pulsar_werden_nicht_angefasst(plan):
    """Krypto hat seinen eigenen Kostenmindestabstand; ein PULSAR-Plan wird
    im Adapter exakt verglichen und darf hier nicht veraendert werden."""
    assert plan("crypto", 100.0, 99.5, 104.0, ask=99.0) == {
        "referenz": 100.0, "stop": 99.5, "take": 104.0, "hinweis": ""}
    assert plan("stock", 100.0, 99.5, 104.0, ask=99.0, pulsar=True)["stop"] == 99.5


def test_unbrauchbare_eingaben_bleiben_unveraendert(plan):
    assert plan("stock", 100.0, 100.0, 104.0)["stop"] == 100.0       # Stop nicht unter Einstieg
    assert plan("stock", 0.0, 99.0, 104.0)["stop"] == 99.0
    assert plan("stock", 100.0, 99.0, 104.0, ask="kaputt")["referenz"] == 100.0
    assert plan("stock", 100.0, 99.0, 104.0, ask=0)["referenz"] == 100.0


def test_mindestabstand_ist_konfigurierbar(monkeypatch):
    import config
    monkeypatch.setattr(config, "ETORO_MIN_STOP_DISTANCE_PCT", 0.02, raising=False)
    from live_trader import aktien_stop_plan
    assert aktien_stop_plan("stock", 100.0, 99.0, 104.0)["stop"] == pytest.approx(98.0)


def test_kaufpfad_ist_auf_den_kaufkurs_verdrahtet():
    """Vertrag: Order, Kosten, Edge und Beleg nutzen den Kaufkurs, der Fill wird
    gegen den Stop geprueft, und der Signalkurs bleibt im Journal erhalten."""
    src = (ROOT / "live_trader.py").read_text(encoding="utf-8")
    for teil in (
        "submit_protected_buy(\n                        broker, inst, qty, entry_referenz, stop, take",
        "estimate_roundtrip_with_broker(\n                            broker, qty, entry_referenz",
        "estimate_plausible_move(\n                        entry_referenz, take",
        '"entry_reference": float(entry_referenz)',
        '"signal_price": float(signal.price)',
        "entry_reference=entry_referenz",
        "abstand_pct < min_pct * 0.5",
        'notify(f"STOP ZU NAH: {inst.name}"',
    ):
        assert teil in src, teil
    import config
    assert 0.005 <= float(config.ETORO_MIN_STOP_DISTANCE_PCT) <= 0.05
