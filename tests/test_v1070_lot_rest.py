"""10.7.0 Teil C -- Lot-Rest erklaert den Abgang; eine Aufloesung fuer alle Wege.

BEFUND VOM 18.09.2026 (Diagnose 14:39 UTC)
==========================================
XRP, BTC und ETH wurden zwischen 13:47 und 13:51 UTC ueber die Take-Profit-
Orders verkauft. Die Trades 81-83 waren danach CLOSED mit bestaetigten Fills
(je rund +0,85 EUR). Trotzdem sperrten drei Bestandsluecken aus der Nacht
weiter jeden OKX-Kauf: ``trade_close`` rechnete "verkauft + 4 ULP >= Abgang",
und 18,3284 (Lot) ist kleiner als 18,3284532 (gebucht). Der Rest von
0,0000532 XRP lag zwei Zeilen weiter oben gerade als eigener Trade 85 vor.

Dieselbe Fehlerklasse hatte schon 10.2.1 Rev 2 (0,00847 DOGE) und 10.3.0
getroffen -- jeweils an einer anderen Stelle. Seit 10.7.0 beantwortet
``okx_receipt_math.quantity_explained`` die Mengenfrage, und
``okx_accounting.resolve_balance_gap_on`` ist die einzige Aufloesung.
"""
from __future__ import annotations

from contextlib import closing
from decimal import Decimal

import pytest

from okx_receipt_math import EvidenceError, quantity_explained
from test_v975_execution_and_repair import engine, persist, close_ledger  # noqa: F401


# ---------------------------------------------------------------------------
# Die Mengenfrage
# ---------------------------------------------------------------------------
def test_lot_rest_erklaert_den_abgang():
    """Die echten XRP-Zahlen: gebucht 18,3284532, verkauft 18,3284, Rest 0,0000532."""
    assert quantity_explained("18.328453200000002", "18.3284", "0.0000532")
    assert quantity_explained("0.00030163806", "0.00030163", "0.00000000806")      # BTC
    assert quantity_explained("0.0091382526", "0.0091382", "0.0000000526")         # ETH


def test_ohne_rest_bleibt_der_lot_abgang_unerklaert():
    """Genau das war der Zustand bis 10.6.0 -- und er ist weiterhin richtig,
    solange niemand den Rest belegt."""
    assert not quantity_explained("18.328453200000002", "18.3284")
    assert not quantity_explained("18.328453200000002", "18.3284", 0)


def test_exakter_verkauf_und_ueberdeckung_gelten():
    assert quantity_explained("7334.18847", "7334.18847")
    assert quantity_explained("100", "100", "0.5")
    assert quantity_explained("10", "10.0000000000001")


def test_teilverkauf_ohne_restzeile_erklaert_nichts():
    assert not quantity_explained("100", "60")
    assert not quantity_explained("100", "60", "30")


def test_unbrauchbare_zahlen_werden_abgelehnt():
    with pytest.raises(EvidenceError):
        quantity_explained("x", "1")
    with pytest.raises(EvidenceError):
        quantity_explained("1", "-2", "0")


# ---------------------------------------------------------------------------
# Verkaufspfad: die Luecke schliesst sich beim Teilverkauf mit Lot-Rest
# ---------------------------------------------------------------------------
def _gap_row(tid):
    import trade_ledger as tl
    with closing(tl._connect()) as con:
        return con.execute("SELECT * FROM okx_balance_gaps WHERE trade_id=?", (tid,)).fetchone()


def _lineage(tid):
    import trade_ledger as tl
    head = tl.trade_detail(tid)
    return tl.entry_lineage("okx", head["broker_position_id"], head["entry_order_id"],
                            head["broker_account_fingerprint"], paper=bool(head["paper"]))


def test_verkauf_mit_lot_rest_schliesst_die_luecke(engine):
    import okx_accounting as buchung
    import trade_ledger as tl
    position, tid = persist(engine)
    row = tl.trade_detail(tid)
    buchung.mark_balance_gap(row, 0.0, row["menge"])
    assert _gap_row(tid)["status"] == "PENDING"

    # Die Boerse verkauft 124,77 vom gebuchten 124,771765; 0,001765 bleiben.
    close_ledger(position, tid, 124.77)

    reste = [r for r in _lineage(tid) if str(r.get("notiz") or "").startswith("Rest nach Teilverkauf")]
    assert len(reste) == 1 and reste[0]["menge"] == pytest.approx(0.001765)
    gap = _gap_row(tid)
    assert gap["status"] == "RESOLVED", "Der Rest ist verbucht, nicht verschwunden"
    assert gap["resolution"].startswith("EXACT_EXIT_FILLS:")
    assert "residual=0.001765" in gap["resolution"]
    zustand = buchung.status("A", "DEMO")
    assert zustand["complete"] and not zustand["blocked_symbols"], "Kaeufe muessen wieder frei sein"


def test_vollverkauf_ohne_rest_schliesst_wie_bisher(engine):
    import okx_accounting as buchung
    import trade_ledger as tl
    position, tid = persist(engine)
    row = tl.trade_detail(tid)
    buchung.mark_balance_gap(row, 0.0, row["menge"])
    close_ledger(position, tid, row["menge"])
    gap = _gap_row(tid)
    assert gap["status"] == "RESOLVED" and "residual=" not in gap["resolution"]


# ---------------------------------------------------------------------------
# Reparatur: der Zustand vom 18.09. loest sich von selbst
# ---------------------------------------------------------------------------
def test_reparatur_schliesst_altlast_mit_lot_rest(engine):
    """Nachgestellt: Luecke PENDING, Trade laengst geschlossen, Rest als Zeile."""
    import okx_accounting as buchung
    import trade_ledger as tl
    position, tid = persist(engine)
    row = tl.trade_detail(tid)
    buchung.mark_balance_gap(row, 0.0, row["menge"])
    close_ledger(position, tid, 124.77)
    # Die Luecke kuenstlich wieder oeffnen -- so sah es auf dem Pi aus, weil
    # der alte Verkaufspfad den Rest nicht kannte.
    with tl._LOCK, closing(tl._connect()) as con, con:
        con.execute("UPDATE okx_balance_gaps SET status='PENDING',resolution='' WHERE trade_id=?", (tid,))
    assert "SUI" in buchung.status("A", "DEMO")["blocked_symbols"]

    repariert = buchung.repair_explained_gaps("A", "DEMO")

    assert repariert == [tid]
    gap = _gap_row(tid)
    assert gap["status"] == "RESOLVED" and gap["resolution"].startswith("LOT_RESIDUAL_LINEAGE:")
    assert not buchung.status("A", "DEMO")["blocked_symbols"]
    assert buchung.repair_explained_gaps("A", "DEMO") == [], "Zweiter Lauf tut nichts"


def test_reparatur_fasst_nie_verkaufte_trades_nicht_an(engine):
    import okx_accounting as buchung
    import trade_ledger as tl
    position, tid = persist(engine)
    row = tl.trade_detail(tid)
    buchung.mark_balance_gap(row, 0.0, row["menge"])
    assert buchung.repair_explained_gaps("A", "DEMO") == []
    assert _gap_row(tid)["status"] == "PENDING", "Ohne Verkauf gibt es nichts zu erklaeren"


def test_reparatur_verlangt_verkaufsbelege(engine):
    import okx_accounting as buchung
    import trade_ledger as tl
    position, tid = persist(engine)
    row = tl.trade_detail(tid)
    buchung.mark_balance_gap(row, 0.0, row["menge"])
    close_ledger(position, tid, row["menge"])
    with tl._LOCK, closing(tl._connect()) as con, con:
        con.execute("UPDATE okx_balance_gaps SET status='PENDING',resolution='' WHERE trade_id=?", (tid,))
        con.execute("UPDATE trades SET exit_fill_ids_json='[]', exit_order_id='' WHERE trade_id=?", (tid,))
    assert buchung.repair_explained_gaps("A", "DEMO") == []
    assert _gap_row(tid)["status"] == "PENDING", "Geschlossen ohne Fills ist kein Beweis"


def test_reparatur_ignoriert_fremde_kontodomaene(engine):
    import okx_accounting as buchung
    import trade_ledger as tl
    position, tid = persist(engine)
    row = tl.trade_detail(tid)
    buchung.mark_balance_gap(row, 0.0, row["menge"])
    close_ledger(position, tid, 124.77)
    with tl._LOCK, closing(tl._connect()) as con, con:
        con.execute("UPDATE okx_balance_gaps SET status='PENDING',resolution='' WHERE trade_id=?", (tid,))
    assert buchung.repair_explained_gaps("B", "DEMO") == []
    assert buchung.repair_explained_gaps("A", "LIVE") == []
    assert _gap_row(tid)["status"] == "PENDING"
