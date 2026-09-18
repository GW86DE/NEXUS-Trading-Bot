"""9.5.8 -- Eskalation statt Endlosschleife beim Verkauf.

BEFUND XLM, 03.09.2026: Ausgangssignal -> FOK 0 gefuellt -> Schutz abgelehnt
-> 5 Minuten warten -> Ausgangssignal -> ... unbegrenzt. Es gab weder einen
Zaehler noch eine wachsende Wartezeit noch einen Abbruch. Nur die
Telegram-Meldung war auf 900 s gedrosselt -- die Sperre begrenzte also die
FREQUENZ der Meldung, nicht die Dauer des Zustands.

Aufgegeben wird der Verkauf bewusst NICHT: eine Position ohne funktionierenden
Ausstieg still liegenzulassen waere schlimmer als ein weiterer Versuch.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class BuchAttrappe:
    def __init__(self):
        self.geschrieben = []

    def setze(self, position):
        self.geschrieben.append(position)


def _engine(monkeypatch, **cfg):
    import crypto_engine
    e = crypto_engine.CryptoEngine.__new__(crypto_engine.CryptoEngine)
    e.buch = BuchAttrappe()
    e.cfg = SimpleNamespace(
        OKX_EXIT_RETRY_MINUTES=cfg.get("basis", 5.0),
        OKX_EXIT_RETRY_MAX_MINUTES=cfg.get("maximum", 60.0),
        OKX_EXIT_ESKALATION_VERSUCHE=cfg.get("schwelle", 5),
        OKX_EXIT_ALERT_COOLDOWN_SECONDS=cfg.get("cooldown", 900.0))
    e.meldungen = []
    e._melde = lambda text, **kw: e.meldungen.append(text)
    return e


def _position():
    import crypto_engine
    return crypto_engine.KryptoPosition(
        symbol="XLM", inst_id="XLM-USDC", menge=560.88, einstieg=0.1798,
        stop=0.16173, take_profit=0.20)


def _minuten_bis(iso: str) -> float:
    ziel = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    return (ziel - datetime.now(timezone.utc)).total_seconds() / 60.0


def test_wartezeit_waechst_mit_jedem_fehlversuch(monkeypatch):
    e = _engine(monkeypatch, basis=5.0, maximum=60.0)
    p = _position()
    erwartet = [5.0, 10.0, 20.0, 40.0, 60.0, 60.0]
    for i, soll in enumerate(erwartet, start=1):
        e._exit_fehlversuch(p, f"Versuch {i}: 0 von 560.88 ausgefuehrt")
        ist = _minuten_bis(p.exit_retry_after)
        assert ist == pytest.approx(soll, abs=0.2), (
            f"Nach {i} Fehlversuchen wurde {ist:.1f} min gewartet, "
            f"erwartet {soll:.0f} min")


def test_zaehler_wird_gefuehrt(monkeypatch):
    e = _engine(monkeypatch)
    p = _position()
    for _ in range(3):
        e._exit_fehlversuch(p, "0 von 560.88 ausgefuehrt")
    assert p.exit_fehlversuche == 3
    assert "Versuch 3" in p.exit_last_detail


def test_eigener_zaehler_stoert_die_unklar_klaerung_nicht(monkeypatch):
    """``exit_recovery_checks`` zaehlt etwas anderes und darf unberuehrt bleiben."""
    e = _engine(monkeypatch)
    p = _position()
    p.exit_recovery_checks = 2          # laufende Klaerung eines UNKLAR-Exits
    e._exit_fehlversuch(p, "0 von 560.88 ausgefuehrt")
    assert p.exit_recovery_checks == 2, (
        "Der Fehlversuchszaehler hat den Klaerungszaehler ueberschrieben")
    assert p.exit_fehlversuche == 1


def test_ab_der_schwelle_wird_immer_gemeldet(monkeypatch):
    """Eine Drossel darf einen dauerhaft blockierten Ausstieg nicht verbergen."""
    e = _engine(monkeypatch, schwelle=3, cooldown=99999.0)
    p = _position()
    for _ in range(6):
        e._exit_fehlversuch(p, "0 von 560.88 ausgefuehrt")
    # Erste Meldung sofort, danach greift die Drossel bis zur Schwelle,
    # ab Versuch 3 wieder jede.
    assert len(e.meldungen) >= 4, (
        f"Nur {len(e.meldungen)} Meldungen -- die Eskalation blieb stumm")
    assert any("ACHTUNG" in m for m in e.meldungen)
    assert any("Handarbeit" in m or "pruefen" in m for m in e.meldungen)


def test_unter_der_schwelle_bleibt_die_drossel(monkeypatch):
    e = _engine(monkeypatch, schwelle=99, cooldown=99999.0)
    p = _position()
    for _ in range(4):
        e._exit_fehlversuch(p, "0 von 560.88 ausgefuehrt")
    assert len(e.meldungen) == 1, (
        "Unterhalb der Schwelle wurde die Meldungsdrossel ausgehebelt")


def test_erfolgreicher_verkauf_setzt_den_zaehler_zurueck(monkeypatch):
    """Sonst startet der naechste Fehlversuch bei der hochgezaehlten Wartezeit."""
    e = _engine(monkeypatch)
    p = _position()
    for _ in range(4):
        e._exit_fehlversuch(p, "0 von 560.88 ausgefuehrt")
    assert p.exit_fehlversuche == 4
    p.exit_fehlversuche = 0             # das macht _schliesse nach einem Fill
    e._exit_fehlversuch(p, "neuer Fehlschlag")
    assert _minuten_bis(p.exit_retry_after) == pytest.approx(5.0, abs=0.2)
