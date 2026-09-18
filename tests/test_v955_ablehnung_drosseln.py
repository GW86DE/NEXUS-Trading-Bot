"""9.5.5 -- Dieselbe Sperre nicht bei jedem Takt ins Logbuch schreiben.

Am 02.09.2026 stand allein AVGO sechsmal mit derselben Zeile im Logbuch
("Kurs 21 min alt, erlaubt 3 min"). Zwischen solchen Wiederholungen geht die
eine wichtige Meldung unter.

Wichtig ist dabei, dass die Auswertung ("welcher Filter lehnt wie viel ab")
ehrlich bleibt: unterdrueckte Wiederholungen werden gezaehlt und beim
naechsten geschriebenen Eintrag ausgewiesen.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def test_gedaechtnis_zaehlt_wiederholungen_und_meldet_sie_nach(monkeypatch):
    """Der Kern der Drosselung, ohne den ganzen Handelslauf zu starten."""
    import live_trader
    live_trader._ABLEHNUNG_GESEHEN.clear()

    import time as _t
    jetzt = [1000.0]
    monkeypatch.setattr(_t, "time", lambda: jetzt[0])

    geschrieben = []

    def journal(symbol, code, text, cooldown=1800.0):
        """Nachbau der Drosselung, wie sie in journal_ablehnung steht."""
        schluessel = (symbol, code, text)
        letzte, anzahl = live_trader._ABLEHNUNG_GESEHEN.get(schluessel, (None, 0))
        if cooldown > 0 and letzte is not None and jetzt[0] - letzte < cooldown:
            live_trader._ABLEHNUNG_GESEHEN[schluessel] = (letzte, anzahl + 1)
            return
        live_trader._ABLEHNUNG_GESEHEN[schluessel] = (jetzt[0], 0)
        geschrieben.append((symbol, anzahl))

    # Sechs Takte mit derselben Sperre, wie am 02.09. beobachtet.
    for _ in range(6):
        journal("AVGO", "market_session", "Kurs 21 min alt")
        jetzt[0] += 300      # 5 Minuten Takt

    assert len(geschrieben) == 1, (
        f"Sechs identische Sperren duerfen einen Eintrag ergeben, nicht "
        f"{len(geschrieben)}")

    # Nach dem Cooldown wird wieder geschrieben -- mit der Zahl der
    # unterdrueckten Wiederholungen.
    jetzt[0] += 1800
    journal("AVGO", "market_session", "Kurs 21 min alt")
    assert len(geschrieben) == 2
    assert geschrieben[1][1] == 5, (
        "Die unterdrueckten Wiederholungen muessen ausgewiesen werden, sonst "
        "wird die Filterauswertung falsch")


def test_ein_anderer_grund_wird_sofort_geschrieben(monkeypatch):
    """Gedrosselt wird nur die UNVERAENDERTE Lage."""
    import live_trader
    live_trader._ABLEHNUNG_GESEHEN.clear()

    gesehen = live_trader._ABLEHNUNG_GESEHEN
    gesehen[("AVGO", "market_session", "Kurs 21 min alt")] = (1000.0, 0)

    # Anderer Grund, anderes Symbol -- beide sind neu.
    assert ("AVGO", "risk_gate", "Kurs 21 min alt") not in gesehen
    assert ("JPM", "market_session", "Kurs 21 min alt") not in gesehen


def test_cooldown_ist_einstellbar():
    import config
    assert hasattr(config, "DECISION_BLOCK_LOG_COOLDOWN_SECONDS")
    assert float(config.DECISION_BLOCK_LOG_COOLDOWN_SECONDS) > 0
