"""9.5.8 -- Der KO-Absturz vom 04.09.2026.

BEFUND aus dem Logbuch (15:58 UTC):

    live_trader.py:964   capture_new_fills -> trade_ledger.trade_close(...)
    trade_ledger.py:857  RuntimeError: Geschlossene exakte Entry-Lineage
                         besitzt keinen eindeutig passenden Exitbeleg
    trade_ledger.py:1025 RuntimeError: Kritischer Ledger-Ausstieg
                         fehlgeschlagen (etoro KO)
    _CriticalFillAccountingError -> NOTIFY [Trading-Bot ABGESTUERZT]
    nexus_start: Aktienkern mit unbehandeltem Fehler beendet

Zwei getrennte Fehler stecken darin:

1. Eine fachlich unaufloesbare Zuordnung wurde wie ein technischer Fehler
   behandelt und stoppte den gesamten Aktienhandel. Ein Neuversuch bringt
   dabei nichts -- es kommen keine neuen Daten. Der Fill blieb unquittiert,
   kam beim naechsten Poll wieder, und der Kern starb erneut.

2. ``starte_aktien`` hatte keine Neustartschleife. Der Faden war endgueltig
   tot, waehrend der lebende Kryptofaden Prozess und Watchdog gruen hielt.

Die Tests pruefen beides am Verhalten.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture(autouse=True)
def _eigenes_ledger(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADINGBOT_TEST_STATE_DIR", str(tmp_path))
    for name in ("trade_ledger", "decision_analytics"):
        sys.modules.pop(name, None)
    import trade_ledger
    trade_ledger.init_ledger()
    yield trade_ledger
    for name in ("trade_ledger", "decision_analytics"):
        sys.modules.pop(name, None)


KONTO = "fp-test"
POSITION = "POS-3311"
ENTRY_ORDER = "ORD-77"


def _lege_geschlossenen_ko_trade_an(tl):
    """Genau die Lage vom 04.09.: KO ist gekauft, verkauft und gebucht."""
    tid = tl.trade_open(
        broker="etoro", symbol="KO", menge=12.0, einstieg_preis=61.5,
        asset_type="stock", waehrung="USD", paper=False, external=True,
        broker_position_id=POSITION, entry_order_id=ENTRY_ORDER,
        broker_account_fingerprint=KONTO)
    assert tid, "Voraussetzung: der Trade muss angelegt sein"
    geschlossen = tl.trade_close(
        broker="etoro", symbol="KO", ausstieg_preis=63.2, menge=12.0,
        exit_grund="take_profit", gebuehr=0.4,
        exit_order_id="ORD-88", exit_fill_ids=["exec:99001"],
        event_id="exec:99001", broker_position_id=POSITION,
        entry_order_id=ENTRY_ORDER, broker_account_fingerprint=KONTO,
        paper=False)
    assert geschlossen == tid
    return tid


# ---------------------------------------------------------------------------
# 1. Der Ledger unterscheidet fachlich von technisch
# ---------------------------------------------------------------------------
def test_bestaetigter_alias_wird_idempotent_wiedererkannt(_eigenes_ledger):
    """9.7.1: same close order/economics are a replay, not a new exit."""
    tl = _eigenes_ledger
    tid = _lege_geschlossenen_ko_trade_an(tl)
    before = tl.trade_detail(tid)
    for _ in range(2):
        replay = tl.trade_close(
            broker="etoro", symbol="KO", ausstieg_preis=63.2, menge=12.0,
            exit_grund="take_profit", gebuehr=0.4, exit_order_id="ORD-88",
            exit_fill_ids=["evidence:abcdef123456"],
            event_id="evidence:abcdef123456", broker_position_id=POSITION,
            entry_order_id=ENTRY_ORDER, broker_account_fingerprint=KONTO,
            paper=False, critical=True)
        assert replay == tid
        assert tl.trade_detail(tid) == before
    with tl._connect() as con:
        assert con.execute("SELECT COUNT(*) FROM trades WHERE symbol='KO'").fetchone()[0] == 1
        # One original receipt and one alias receipt, not new financial trades.
        assert con.execute("SELECT COUNT(*) FROM trade_exit_events").fetchone()[0] == 2


def test_der_eigene_typ_ist_kein_gewoehnlicher_ledgerfehler(_eigenes_ledger):
    """Nur so kann der Aufrufer die beiden Faelle trennen."""
    tl = _eigenes_ledger
    assert issubclass(tl.LedgerZuordnungUnklar, RuntimeError)
    assert tl.LedgerZuordnungUnklar is not RuntimeError


def test_es_entsteht_kein_zweiter_finanztrade(_eigenes_ledger):
    """Der wichtigste Teil: fail-closed bleibt fail-closed."""
    tl = _eigenes_ledger
    _lege_geschlossenen_ko_trade_an(tl)
    vorher = len(tl.alle_trades()) if hasattr(tl, "alle_trades") else None

    with pytest.raises(tl.LedgerZuordnungUnklar):
        tl.trade_close(
            broker="etoro", symbol="KO", ausstieg_preis=99.0, menge=12.0,
            exit_fill_ids=["evidence:zweiter"], event_id="evidence:zweiter",
            broker_position_id=POSITION, entry_order_id=ENTRY_ORDER,
            broker_account_fingerprint=KONTO, paper=False, critical=True)

    import sqlite3
    from decision_analytics import db_pfad
    con = sqlite3.connect(f"file:{db_pfad()}?mode=ro", uri=True)
    try:
        anzahl = con.execute(
            "SELECT COUNT(*) FROM trades WHERE symbol='KO'").fetchone()[0]
        preise = [r[0] for r in con.execute(
            "SELECT ausstieg_preis FROM trades WHERE symbol='KO'")]
    finally:
        con.close()
    assert anzahl == 1, "Es wurde ein Phantomtrade angelegt"
    assert preise == [63.2], "Der bereits gebuchte Ausstieg wurde veraendert"
    if vorher is not None:
        assert len(tl.alle_trades()) == vorher


def test_technischer_fehler_bleibt_ein_gewoehnlicher_fehler(_eigenes_ledger,
                                                            monkeypatch):
    """Datenbank kaputt -> weiterhin fail-closed, KEIN Zuordnungsbefund."""
    import sqlite3
    tl = _eigenes_ledger

    def kaputt(*_a, **_k):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(tl, "_connect", kaputt)
    with pytest.raises(RuntimeError) as exc:
        tl.trade_close(broker="etoro", symbol="KO", ausstieg_preis=63.2,
                       menge=12.0, exit_fill_ids=["x"], event_id="x",
                       broker_position_id=POSITION, entry_order_id=ENTRY_ORDER,
                       broker_account_fingerprint=KONTO, critical=True)
    assert not isinstance(exc.value, tl.LedgerZuordnungUnklar), (
        "Ein technischer Fehler darf nicht als fachlich unaufloesbar gelten -- "
        "sonst wuerde ein Fill quittiert, den ein Neuversuch haette buchen koennen")


# ---------------------------------------------------------------------------
# 2. Der Aufrufer macht daraus keinen Prozessabbruch
# ---------------------------------------------------------------------------
def test_aufrufer_erkennt_den_befund(_eigenes_ledger):
    import live_trader
    import trade_ledger
    assert live_trader._ledger_zuordnung_unklar(
        trade_ledger.LedgerZuordnungUnklar("etoro KO: unklar"))
    assert not live_trader._ledger_zuordnung_unklar(RuntimeError("Platte voll"))
    assert not live_trader._ledger_zuordnung_unklar(ValueError("irgendwas"))


# ---------------------------------------------------------------------------
# 3. Der Aktienkern startet nach einem Fehler neu
# ---------------------------------------------------------------------------
def test_aktienkern_startet_nach_fehler_neu(monkeypatch):
    import types
    import nexus_start

    versuche = {"n": 0}

    def run():
        versuche["n"] += 1
        if versuche["n"] < 3:
            raise RuntimeError("Kritischer Ledger-Ausstieg fehlgeschlagen (etoro KO)")
        nexus_start._beenden.set()          # dritter Lauf endet sauber

    monkeypatch.setitem(sys.modules, "live_trader",
                        types.SimpleNamespace(run=run))
    monkeypatch.setattr(nexus_start, "AKTIEN_NEUSTART_BASIS", 0.01)
    monkeypatch.setattr(nexus_start, "AKTIEN_NEUSTART_MAXIMUM", 0.02)
    gemeldet: list[str] = []
    monkeypatch.setattr(nexus_start, "_melder",
                        lambda *_a, **_k: (lambda text, **kw: gemeldet.append(text)))
    nexus_start._beenden.clear()
    try:
        nexus_start.starte_aktien()
    finally:
        nexus_start._beenden.clear()

    assert versuche["n"] == 3, (
        f"Der Aktienkern wurde nicht neu gestartet (nur {versuche['n']} Lauf/Laeufe)")
    assert len(gemeldet) == 2, "Jeder Abbruch muss gemeldet werden"
    assert "Neustart" in gemeldet[0]


def test_sauberes_ende_startet_nicht_neu(monkeypatch):
    """Ein gewolltes Ende (SIGTERM/Strg-C) darf keine Schleife ausloesen."""
    import types
    import nexus_start

    laeufe = {"n": 0}

    def run():
        laeufe["n"] += 1                    # kehrt normal zurueck

    monkeypatch.setitem(sys.modules, "live_trader",
                        types.SimpleNamespace(run=run))
    monkeypatch.setattr(nexus_start, "_melder",
                        lambda *_a, **_k: (lambda *a, **k: None))
    nexus_start._beenden.clear()
    try:
        nexus_start.starte_aktien()
    finally:
        nexus_start._beenden.clear()
    assert laeufe["n"] == 1
