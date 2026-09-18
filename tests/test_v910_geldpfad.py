"""v9.1 -- drei Varianten derselben Frage: was gilt, wenn es mittendrin aufhoert?

B1  Der Broker storniert beim Verkauf ZUERST die Schutzorder. Stirbt der
    Prozess danach, war die Position bis 9.0.15 dauerhaft ungeschuetzt: das
    Buch stand weiter auf broker_schutz=True, und der einzige periodische
    Abgleich lief hinter "if not position.broker_schutz". Er griff nie wieder.

B2  Der Fill-Nachweis wurde nur geholt, wenn WENIGER im Konto lag als im Buch.
    Lag Fremdbestand desselben Coins im Konto, galt weiter die veraltete
    Buchmenge -- und der naechste Stop verkaufte sie ein zweites Mal, aus dem
    Bestand des Nutzers.

B3  "0 gefuellt" galt als bewiesener Fehlschlag. Das vom Broker berechnete
    Feld ``terminal`` wurde nie gelesen, also landete auch eine Order mit
    offenem Ausgang im 5-Minuten-Retry statt in der 24-Stunden-Sperre.
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


# ===========================================================================
# B1 -- das Schutzfenster
# ===========================================================================
def test_schutzverlust_wird_vor_dem_brokeraufruf_persistiert():
    """Der Vermerk muss auf der Platte stehen, BEVOR storniert wird.

    Sonst hilft er bei genau dem Fall nicht, fuer den er da ist: dem
    Prozessabbruch zwischen Storno und Verkauf.
    """
    quelle = (WURZEL / "crypto_engine.py").read_text(encoding="utf-8")
    start = quelle.index("def _schliesse")
    block = quelle[start:quelle.index("broker.schliesse_position", start)]
    assert 'position.broker_schutz = False' in block, \
        "Der Schutz gilt ab dem Brokeraufruf als verloren"
    assert 'position.protection_status = "LOST"' in block
    # Und der Vermerk muss auch gespeichert werden, nicht nur im RAM stehen.
    assert "self.buch.setze(position)" in block


# 9.5.4: Dieser Test hat frueher den Quelltext nach der wortwoertlichen
# if-Bedingung durchsucht. Damit pruefte er die Schreibweise, nicht das
# Verhalten -- und schlug fehl, sobald die Bedingung korrekt erweitert wurde.
# Der Nachfolger steht in tests/test_v954_schutzbuchhaltung.py und treibt
# pruefe_positionen() wirklich an.


def test_geheilte_position_behaelt_belegten_retryzustand_und_schutz(tmp_path, monkeypatch):
    # The timing wrapper added in v10 must not turn this into a test of where
    # an assignment is written. Exercise the public check and persisted state.
    from test_v954_schutzbuchhaltung import SchutzBroker, baue_motor, xlm_position
    from broker.base import OrderErgebnis
    broker = SchutzBroker(bestaetigt=True)
    broker.external_exit_evidence = lambda **kwargs: {}
    broker.reconcile_exit_evidence = lambda **kwargs: OrderErgebnis(
        order_ids=["exit-confirmed-canceled"], status="CANCELED", terminal=True,
        fill_evidence_complete=True, filled_quantity=0, gross_filled_quantity=0)
    ce, engine, gemeldet = baue_motor(tmp_path, monkeypatch, broker)
    position = xlm_position(ce)
    position.exit_state = "SUBMITTING"
    position.exit_client_order_id = "exit-client"
    position.exit_order_ids = ["exit-confirmed-canceled"]
    position.broker_schutz = False
    engine.buch.setze(position)
    report = engine.pruefe_positionen()
    persisted = ce.KryptoPositionsbuch(engine.buch.datei).hole("XLM")
    assert report["ok"] is True and broker.abgleiche
    # A positively confirmed zero-fill cancel keeps the bounded retry pause;
    # a text-search assertion for IDLE was obsolete even before the v10 wrapper.
    assert persisted.exit_state == "RETRY_WAIT" and persisted.exit_retry_after
    assert persisted.broker_schutz is True
    assert "XLM" in report["schutz_ergaenzt"]
    assert "terminal ohne Fill bestaetigt" in persisted.exit_last_detail


def test_exit_state_hat_jetzt_einen_auswertenden_leser():
    """Der Kern des Befunds: das Feld wurde geschrieben und nie gelesen."""
    quelle = (WURZEL / "crypto_engine.py").read_text(encoding="utf-8")
    # Ein reiner Schreibzugriff ("exit_state =") zaehlt nicht.
    lesend = [z for z in quelle.splitlines()
              if "exit_state" in z and "exit_state =" not in z
              and "exit_state:" not in z]
    assert lesend, "exit_state muss den Handel beeinflussen, nicht nur die Anzeige"


# ===========================================================================
# B2 -- Fremdbestand
# ===========================================================================
def test_ueberhang_holt_den_fill_nachweis():
    """Der Zweig "mehr im Konto als im Buch" war der blinde Fleck."""
    quelle = (WURZEL / "crypto_engine.py").read_text(encoding="utf-8")
    start = quelle.index("MEHR im Konto als im Buch")
    block = quelle[start:start + 1600]
    assert "_externer_verkaufsbeweis(position, position.menge)" in block, \
        "Ohne Nachweis wird eine bereits verkaufte Menge ein zweites Mal verkauft"
    assert "_position_extern_geschlossen(" in block
    assert "continue" in block


def test_beide_abweichungsrichtungen_pruefen_den_broker():
    """Weniger UND mehr im Konto muessen beide gegen echte Fills laufen."""
    quelle = (WURZEL / "crypto_engine.py").read_text(encoding="utf-8")
    start = quelle.index("abweichung > MENGEN_TOLERANZ")
    block = quelle[start:quelle.index("ueberhang = konto - position.menge", start)]
    assert block.count("_externer_verkaufsbeweis(") >= 2, \
        f"Nur {block.count('_externer_verkaufsbeweis(')} Nachweis(e) -- beide Richtungen noetig"


# ===========================================================================
# B3 -- unklar ist nicht dasselbe wie fehlgeschlagen
# ===========================================================================
def test_offener_ausgang_kommt_in_die_lange_sperre():
    quelle = (WURZEL / "crypto_engine.py").read_text(encoding="utf-8")
    stelle = quelle.index('protokoll.abschliessen("NICHT_AUSGEFUEHRT"')
    block = quelle[stelle:quelle.index("# 9.5.8: Ab hier", stelle)]
    assert 'getattr(ergebnis, "terminal", False)' in block
    assert 'exit_state = "UNCLEAR"' in block
    assert "hours=24" in block
    assert 'return "UNCLEAR"' in block


def test_bewiesener_fehlschlag_behaelt_den_kurzen_retry():
    """Die Gegenprobe -- sonst waere jede abgelehnte Order 24 Stunden gesperrt."""
    quelle = (WURZEL / "crypto_engine.py").read_text(encoding="utf-8")
    stelle = quelle.index('protokoll.abschliessen("NICHT_AUSGEFUEHRT"')
    block = quelle[stelle:quelle.index("# 9.5.8: Ab hier", stelle)]
    assert "self._exit_fehlversuch(" in block
    assert 'return "FAILED"' in block


def test_broker_liefert_das_terminal_feld():
    """Ohne dieses Feld waere die Unterscheidung nicht zu treffen."""
    from broker.base import OrderErgebnis
    assert "terminal" in OrderErgebnis.__dataclass_fields__, \
        "Der Ausstiegspfad braucht die Beweislage des Brokers"
