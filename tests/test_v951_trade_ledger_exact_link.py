"""Exakte, idempotente Verknuepfung geschlossener eToro-Ledgertrades.

Die Regressionstests benutzen die reale ADBE-Identitaetskette vom
01.09.2026.  Sie rufen den produktiven Funktionspfad auf und pruefen die
persistierten Tabellen; Quelltext-Fragmentsuchen sind absichtlich tabu.
"""
from __future__ import annotations

import json

import pytest


ACCOUNT = "ba32f97fe3482fcbc326e51a"
DECISION = 4153277691980212224
ENTRY_ORDER = "378375675"
POSITION = "3592625451"
ENTRY_TIME = "2026-09-01T14:38:32.343Z"
CLOSE_TIME = "2026-09-01T15:18:34.000Z"
ENTRY_PRICE = 293.92
CLOSE_PRICE = 287.16
QUANTITY = 51.0
REAL_CLOSE_ORDER = "378400001"
ENTRY_FILL = f"etoro-entry:{ENTRY_ORDER}:{POSITION}:{ENTRY_TIME}"
CLOSE_FILL = f"etoro-close:history:{POSITION}:{CLOSE_TIME}"


@pytest.fixture
def ledger(monkeypatch, tmp_path):
    monkeypatch.setenv("TRADINGBOT_TEST_STATE_DIR", str(tmp_path))
    import decision_analytics
    import trade_ledger

    monkeypatch.setattr(
        decision_analytics, "DB_PATH", tmp_path / "decision_history.sqlite")
    trade_ledger.init_ledger()
    return trade_ledger


def _make_closed_trade(trade_ledger, *, decision_id=None) -> int:
    """Erzeugt den in 9.5.0 beobachteten geschlossenen Altsatz."""
    corrupt_close_fill = (
        f"etoro:{ACCOUNT}:close:{POSITION}:{ENTRY_ORDER}:evidence:legacy")
    trade_id = trade_ledger.trade_open(
        broker="etoro",
        symbol="ADBE",
        menge=QUANTITY,
        einstieg_preis=ENTRY_PRICE,
        referenzpreis=290.07,
        gebuehr=1.25,
        asset_type="stock",
        waehrung="USD",
        decision_id=decision_id,
        paper=True,
        zeit=ENTRY_TIME,
        external=decision_id is None,
        broker_position_id=POSITION,
        entry_order_id=ENTRY_ORDER,
        broker_account_fingerprint=ACCOUNT,
    )
    assert trade_id is not None
    assert trade_ledger.trade_close(
        broker="etoro",
        symbol="ADBE",
        ausstieg_preis=CLOSE_PRICE,
        menge=QUANTITY,
        exit_grund="BROKER_STOP_LOSS",
        gebuehr=1.50,
        paper=True,
        zeit=CLOSE_TIME,
        exit_order_id=ENTRY_ORDER,
        exit_fill_ids=[corrupt_close_fill],
        event_id=corrupt_close_fill,
        trade_id=trade_id,
        broker_position_id=POSITION,
        broker_account_fingerprint=ACCOUNT,
        entry_order_id=ENTRY_ORDER,
    ) == trade_id
    return int(trade_id)


def _call_exact(trade_ledger, **overrides) -> dict:
    args = {
        "broker": "etoro",
        "symbol": "ADBE",
        "paper": True,
        "decision_id": DECISION,
        "broker_position_id": POSITION,
        "broker_account_fingerprint": ACCOUNT,
        "entry_order_id": ENTRY_ORDER,
        "entry_fill_id": ENTRY_FILL,
        "entry_fill": {
            "quantity": QUANTITY,
            "price": ENTRY_PRICE,
            "filled_at": ENTRY_TIME,
        },
        "close_order_id": REAL_CLOSE_ORDER,
        "close_fill_id": CLOSE_FILL,
        "exit_reason": "BROKER_STOP_LOSS",
        "expected_entry_price": ENTRY_PRICE,
        "expected_close_price": CLOSE_PRICE,
        "expected_quantity": QUANTITY,
    }
    args.update(overrides)
    return trade_ledger.reconcile_closed_trade_exact(**args)


def _database_state(trade_ledger) -> dict:
    """Alle durch den Migrationspfad beschreibbaren Ledgertabellen."""
    with trade_ledger._connect() as con:
        return {
            "trades": [dict(row) for row in con.execute(
                "SELECT * FROM trades ORDER BY trade_id")],
            "entry_fills": [dict(row) for row in con.execute(
                "SELECT * FROM trade_entry_fills ORDER BY id")],
            "exit_events": [dict(row) for row in con.execute(
                "SELECT * FROM trade_exit_events ORDER BY id")],
        }


def _trade_row(trade_ledger, trade_id: int) -> dict:
    with trade_ledger._connect() as con:
        row = con.execute(
            "SELECT * FROM trades WHERE trade_id=?", (trade_id,)).fetchone()
    assert row is not None
    return dict(row)


def test_not_found_does_not_change_any_ledger_table(ledger):
    _make_closed_trade(ledger)
    before = _database_state(ledger)

    result = _call_exact(
        ledger,
        broker_position_id="position-that-does-not-exist",
        entry_order_id="order-that-does-not-exist",
    )

    assert result == {"status": "NOT_FOUND"}
    assert _database_state(ledger) == before


@pytest.mark.parametrize(
    "identity_override",
    [
        {"broker_account_fingerprint": "different-etoro-account"},
        {"broker_position_id": "3592625452"},
        {"entry_order_id": "378375676"},
    ],
    ids=["wrong-account", "wrong-position-id", "wrong-entry-order-id"],
)
def test_each_wrong_exact_identity_component_is_not_found_without_mutation(
        ledger, identity_override):
    _make_closed_trade(ledger)
    before = _database_state(ledger)

    result = _call_exact(ledger, **identity_override)

    assert result == {"status": "NOT_FOUND"}
    assert _database_state(ledger) == before


def test_existing_different_decision_id_is_a_non_mutating_conflict(ledger):
    trade_id = _make_closed_trade(ledger, decision_id=DECISION + 1)
    before = _database_state(ledger)

    result = _call_exact(ledger)

    assert result == {"status": "DECISION_CONFLICT", "trade_id": trade_id}
    assert _database_state(ledger) == before


@pytest.mark.parametrize(
    "value_override",
    [
        {"expected_entry_price": ENTRY_PRICE + 1.0},
        {"expected_close_price": CLOSE_PRICE - 1.0},
        {"expected_quantity": QUANTITY - 1.0},
    ],
    ids=["entry-price", "close-price", "quantity"],
)
def test_contradicting_broker_price_or_quantity_is_rejected_without_mutation(
        ledger, value_override):
    trade_id = _make_closed_trade(ledger)
    before = _database_state(ledger)

    result = _call_exact(ledger, **value_override)

    assert result == {
        "status": "BROKER_VALUE_CONFLICT",
        "trade_id": trade_id,
    }
    assert _database_state(ledger) == before


def test_explicit_real_close_order_id_replaces_legacy_entry_order_anchor(ledger):
    trade_id = _make_closed_trade(ledger)
    before = _trade_row(ledger, trade_id)
    assert before["exit_order_id"] == ENTRY_ORDER

    result = _call_exact(ledger)

    assert result == {
        "status": "LINKED_CLOSED",
        "trade_id": trade_id,
        "exit_order_id": REAL_CLOSE_ORDER,
    }
    after = _trade_row(ledger, trade_id)
    assert after["decision_id"] == DECISION
    assert after["exit_order_id"] == REAL_CLOSE_ORDER
    assert json.loads(after["exit_fill_ids_json"]) == [CLOSE_FILL]
    with ledger._connect() as con:
        events = [dict(row) for row in con.execute(
            "SELECT * FROM trade_exit_events WHERE trade_id=? ORDER BY id",
            (trade_id,),
        )]
    assert len(events) == 1
    assert events[0]["order_id"] == REAL_CLOSE_ORDER
    assert events[0]["fill_id"] == CLOSE_FILL
    assert events[0]["event_id"] == CLOSE_FILL


def test_exact_replay_creates_no_second_trade_exit_event_or_pnl(ledger):
    trade_id = _make_closed_trade(ledger)

    first = _call_exact(ledger)
    with ledger._connect() as con:
        after_first = dict(con.execute(
            """SELECT COUNT(*) AS trades, SUM(brutto_pnl) AS brutto_pnl,
                      SUM(gebuehren) AS gebuehren, SUM(netto_pnl) AS netto_pnl
               FROM trades"""
        ).fetchone())
        first_exit_events = int(con.execute(
            "SELECT COUNT(*) FROM trade_exit_events").fetchone()[0])
        first_entry_fills = int(con.execute(
            "SELECT COUNT(*) FROM trade_entry_fills").fetchone()[0])

    second = _call_exact(ledger)
    with ledger._connect() as con:
        after_second = dict(con.execute(
            """SELECT COUNT(*) AS trades, SUM(brutto_pnl) AS brutto_pnl,
                      SUM(gebuehren) AS gebuehren, SUM(netto_pnl) AS netto_pnl
               FROM trades"""
        ).fetchone())
        second_exit_events = int(con.execute(
            "SELECT COUNT(*) FROM trade_exit_events").fetchone()[0])
        second_entry_fills = int(con.execute(
            "SELECT COUNT(*) FROM trade_entry_fills").fetchone()[0])

    expected = {
        "status": "LINKED_CLOSED",
        "trade_id": trade_id,
        "exit_order_id": REAL_CLOSE_ORDER,
    }
    assert first == expected
    assert second == expected
    assert after_first == after_second
    assert after_second["trades"] == 1
    assert first_exit_events == second_exit_events == 1
    assert first_entry_fills == second_entry_fills == 1

