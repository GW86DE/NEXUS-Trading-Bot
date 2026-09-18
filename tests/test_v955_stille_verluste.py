"""9.5.5 -- Wege, auf denen Geld oder eine Position stumm verschwand.

Alle vier hier abgesicherten Befunde stammen aus der Pruefung der Ketten
"Kaufentscheidung bis Buchung" fuer eToro und OKX. Gemeinsam ist ihnen, dass
etwas Wichtiges ausfiel, OHNE dass es irgendwo auffiel.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ---------------------------------------------------------------------------
# B1: defekter Buch-Eintrag wurde still verworfen und dann geloescht
# ---------------------------------------------------------------------------
def test_unlesbarer_positionseintrag_wird_nicht_geloescht(tmp_path, monkeypatch):
    """Der einzige Verlustpfad, der gar keine Spur hinterliess.

    Ein Eintrag, den KryptoPosition(**eintrag) nicht annimmt, wurde beim Laden
    uebersprungen -- und beim naechsten Speichern endgueltig aus der Datei
    entfernt, weil gespeichert wird, was im Speicher steht. Kein
    Ledgerabschluss, keine Risikobuchung, keine Meldung. Die Schutzorder beim
    Broker blieb bestehen.
    """
    monkeypatch.setenv("TRADINGBOT_TEST_STATE_DIR", str(tmp_path))
    import crypto_engine as ce

    datei = tmp_path / "crypto_positions.json"
    datei.write_text(json.dumps({"version": 2, "positionen": [
        {"symbol": "GUT", "inst_id": "GUT-USDC", "menge": 1.0, "einstieg": 10.0,
         "stop": 9.0, "take_profit": 11.0},
        {"symbol": "KAPUTT", "inst_id": "KAPUTT-USDC", "menge": 2.5,
         "einstieg": 20.0, "unbekanntes_feld_aus_der_zukunft": True},
    ]}), encoding="utf-8")

    buch = ce.KryptoPositionsbuch(datei)
    assert buch.hole("GUT") is not None
    assert buch.hole("KAPUTT") is None, "Der Eintrag ist wirklich nicht lesbar"

    # Irgendeine Aenderung -> speichern
    buch.setze(buch.hole("GUT"))

    roh = json.loads(datei.read_text(encoding="utf-8"))
    symbole = {str(p.get("symbol")) for p in roh["positionen"]}
    assert "KAPUTT" in symbole, (
        "Ein nicht lesbarer Eintrag darf nicht aus der Datei verschwinden -- "
        "sonst ist ein Bestand spurlos weg")
    assert "GUT" in symbole


# ---------------------------------------------------------------------------
# B12: die eigene Schutzorder wurde am falschen Praefix gesucht
# ---------------------------------------------------------------------------
def test_eigene_schutzorder_wird_am_praefix_erkannt():
    """NEXUS sendet "PN9...", gesucht wurde nach "TBP"/"N9".

    Damit hat verwaiste_orders_aufraeumen die eigenen Schutzorders NIE
    erkannt; der Aufraeumlauf lief seit jeher ins Leere.
    """
    from broker.okx import _NEXUS_ALGO_PRAEFIXE

    # So entsteht die Kennung im Kaufpfad: "P" + cl_ord_id, cl_ord_id = "N9..."
    echte_kennung = "P" + "N9140903310170043856"
    assert echte_kennung.startswith(_NEXUS_ALGO_PRAEFIXE), (
        f"{echte_kennung} wird nicht als eigene Schutzorder erkannt")
    # Der Rueckfall make_client_order_id("TBP9") ebenfalls.
    assert "TBP9abc".startswith(_NEXUS_ALGO_PRAEFIXE)
    # Eine fremde Order weiterhin nicht.
    assert not "FREMD123".startswith(_NEXUS_ALGO_PRAEFIXE)


# ---------------------------------------------------------------------------
# eToro: critical=True musste auch fachliche Ablehnungen fail-closed behandeln
# ---------------------------------------------------------------------------
def test_kritischer_ledgereinstieg_ohne_decision_id_wirft(tmp_path, monkeypatch):
    """Vorher kam stumm None zurueck -- der Kauf lief ohne Ledgerzeile weiter."""
    monkeypatch.setenv("TRADINGBOT_TEST_STATE_DIR", str(tmp_path))
    import decision_analytics
    monkeypatch.setattr(decision_analytics, "DB_PATH",
                        tmp_path / "decision_history.sqlite", raising=False)
    import trade_ledger
    trade_ledger.init_ledger()

    # Ohne critical bleibt es bei der bisherigen, milden Ablehnung.
    assert trade_ledger.trade_open(
        broker="etoro", symbol="AAPL", menge=1.0, einstieg_preis=100.0,
        decision_id=0) is None

    with pytest.raises(RuntimeError, match="Kritischer Ledger-Einstieg"):
        trade_ledger.trade_open(
            broker="etoro", symbol="AAPL", menge=1.0, einstieg_preis=100.0,
            decision_id=0, critical=True)


# ---------------------------------------------------------------------------
# eToro: das Zeitfenster in eigene_kaeufe() wirkte gar nicht
# ---------------------------------------------------------------------------
def test_alter_kauf_beansprucht_keine_heutige_position(tmp_path, monkeypatch):
    """max_alter_stunden stand in der Signatur, wurde aber nie benutzt."""
    monkeypatch.setenv("TRADINGBOT_TEST_STATE_DIR", str(tmp_path))
    import importlib
    import etoro_reconciliation as er
    importlib.reload(er)

    from datetime import datetime, timedelta, timezone
    alt = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    neu = datetime.now(timezone.utc).isoformat()

    daten = er._load()
    daten.setdefault("records", {})
    for kennung, zeit, symbol in (("111", alt, "ALTKAUF"), ("222", neu, "NEUKAUF")):
        daten["records"][kennung] = {
            "decision_id": int(kennung), "symbol": symbol, "state": "SUBMITTING",
            "created_at_utc": zeit, "updated_at_utc": zeit,
            "order_ids": [f"o{kennung}"], "reference_id": f"r{kennung}",
            "position_ids": [], "verified_position_ids": [],
        }
    er._save(daten)

    symbole = {e["symbol"] for e in er.eigene_kaeufe(max_alter_stunden=48.0)}
    assert "NEUKAUF" in symbole
    assert "ALTKAUF" not in symbole, (
        "Ein 30 Tage alter, nie geklaerter Kauf darf keine heutige Position "
        "mehr als eigene beanspruchen")

    # Ohne Fenster (0) bleibt das alte Verhalten erreichbar.
    assert "ALTKAUF" in {e["symbol"] for e in er.eigene_kaeufe(max_alter_stunden=0)}
