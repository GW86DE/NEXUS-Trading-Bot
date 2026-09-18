"""Crash-/Replay-Grenzen des SELL-Ledgers.

Die Tests benutzen ausschliesslich Broker-IDs. Symbol- oder Mengennaehe ist
nirgends ein Zuordnungsbeweis.
"""
from __future__ import annotations

import pytest

# 9.5.8: Diese vier Stellen erwarteten bis 9.5.7 die Meldung "Kritischer
# Ledger-Ausstieg". Fachlich unaufloesbare Zuordnungen tragen seither den
# eigenen Typ ``LedgerZuordnungUnklar`` -- weiterhin ein RuntimeError, aber
# unterscheidbar von einem technischen Fehler. Genau daran erkennt der
# Aufrufer, dass ein Neuversuch nichts bringt und der Verkauf stattdessen als
# Buchungsluecke festzuhalten ist, statt den Aktienkern zu beenden.
#
# Am Verhalten aendert sich hier nichts: es wird nach wie vor nichts gebucht,
# und ``_state(ledger) == before`` beweist das in jedem dieser Tests.


@pytest.fixture
def ledger(monkeypatch, tmp_path):
    monkeypatch.setenv("TRADINGBOT_TEST_STATE_DIR", str(tmp_path))
    import decision_analytics
    import trade_ledger

    monkeypatch.setattr(
        decision_analytics, "DB_PATH", tmp_path / "decision_history.sqlite")
    trade_ledger.init_ledger()
    return trade_ledger


def _open(ledger, *, quantity=10.0):
    trade_id = ledger.trade_open(
        broker="etoro", symbol="ADBE", menge=quantity,
        einstieg_preis=100.0, decision_id=901,
        paper=True, zeit="2026-09-01T10:00:00+00:00",
        broker_position_id="position-1", entry_order_id="entry-1",
        entry_fill_id="entry-fill-1",
        broker_account_fingerprint="account-1",
        critical=True,
    )
    assert trade_id is not None
    return int(trade_id)


def _close_args(**overrides):
    values = {
        "broker": "etoro",
        "symbol": "ADBE",
        "ausstieg_preis": 110.0,
        "menge": 10.0,
        "exit_grund": "BROKER_STOP_LOSS",
        "gebuehr": 1.25,
        "paper": True,
        "zeit": "2026-09-01T11:00:00+00:00",
        "exit_order_id": "close-1",
        "exit_fill_ids": ["close-fill-1"],
        "event_id": "close-event-1",
        "broker_position_id": "position-1",
        "broker_account_fingerprint": "account-1",
        "entry_order_id": "entry-1",
        "critical": True,
    }
    values.update(overrides)
    return values


def _state(ledger):
    with ledger._connect() as con:
        return {
            "trades": [dict(row) for row in con.execute(
                "SELECT * FROM trades ORDER BY trade_id")],
            "events": [dict(row) for row in con.execute(
                "SELECT * FROM trade_exit_events ORDER BY id")],
        }


def test_full_close_replay_is_found_before_fallback_and_never_creates_phantom(
        ledger):
    trade_id = _open(ledger)
    assert ledger.trade_close(trade_id=trade_id, **_close_args()) == trade_id
    before = _state(ledger)

    # Typische History-Anreicherung: derselbe Fill kommt spaeter mit einer
    # anderen/fehlenden optionalen orderId und ohne lokalen Entry-Anker.
    replay = _close_args(
        exit_order_id="close-enriched-later",
        entry_order_id="",
        einstieg_preis=100.0,
        eingestiegen_am="2026-09-01T10:00:00+00:00",
    )
    assert ledger.trade_close(**replay) == trade_id

    assert _state(ledger) == before
    assert len(before["trades"]) == 1
    assert not [row for row in before["trades"] if not row["ausgestiegen_am"]]


def test_replay_check_open_resolution_and_fallback_share_one_write_transaction(
        ledger, monkeypatch):
    transaction_states = []
    original = ledger._existing_exit_event_trade_id

    def observed(con, **kwargs):
        transaction_states.append(bool(con.in_transaction))
        return original(con, **kwargs)

    monkeypatch.setattr(ledger, "_existing_exit_event_trade_id", observed)
    args = _close_args(
        menge=2.0, einstieg_preis=100.0,
        eingestiegen_am="2026-09-01T10:00:00+00:00")

    trade_id = ledger.trade_close(**args)
    assert trade_id is not None
    # Auch ein vollstaendiger Replay durchlaeuft den definitiven Check unter
    # BEGIN IMMEDIATE und bleibt auf demselben atomar angelegten Fallback.
    assert ledger.trade_close(**args) == trade_id
    assert transaction_states and all(transaction_states)

    state = _state(ledger)
    assert len(state["trades"]) == 1
    assert len(state["events"]) == 1
    assert state["trades"][0]["trade_id"] == trade_id


def test_optional_order_id_change_cannot_apply_same_partial_fill_twice(ledger):
    trade_id = _open(ledger)
    first = _close_args(menge=4.0)
    assert ledger.trade_close(trade_id=trade_id, **first) == trade_id

    replay = _close_args(menge=4.0, exit_order_id="")
    assert ledger.trade_close(**replay) == trade_id

    state = _state(ledger)
    assert len(state["events"]) == 1
    open_rows = [row for row in state["trades"] if not row["ausgestiegen_am"]]
    assert len(open_rows) == 1
    assert open_rows[0]["menge"] == pytest.approx(6.0)


def test_event_id_is_canonical_when_broker_has_no_fill_id(ledger):
    trade_id = _open(ledger)
    first = _close_args(
        menge=4.0, exit_fill_ids=[], event_id="event-without-fill")
    assert ledger.trade_close(trade_id=trade_id, **first) == trade_id

    replay = _close_args(
        menge=4.0, exit_fill_ids=[], event_id="event-without-fill",
        exit_order_id="later-order-enrichment")
    assert ledger.trade_close(**replay) == trade_id
    before = _state(ledger)

    with pytest.raises(ledger.LedgerZuordnungUnklar):
        ledger.trade_close(**_close_args(
            menge=5.0, exit_fill_ids=[], event_id="event-without-fill"))
    assert _state(ledger) == before


@pytest.mark.parametrize(
    "changed",
    [
        {"menge": 5.0},
        {"ausstieg_preis": 109.0},
        {"gebuehr": 2.50},
    ],
    ids=["quantity", "price", "fee"],
)
def test_same_exit_anchor_with_changed_money_values_is_conflict(
        ledger, changed):
    trade_id = _open(ledger)
    first = _close_args(menge=4.0)
    assert ledger.trade_close(trade_id=trade_id, **first) == trade_id
    before = _state(ledger)

    replay = _close_args(menge=4.0)
    replay.update(changed)
    with pytest.raises(ledger.LedgerZuordnungUnklar):
        ledger.trade_close(**replay)

    assert _state(ledger) == before


def test_mixed_existing_and_new_fill_batch_fails_closed(ledger):
    trade_id = _open(ledger)
    assert ledger.trade_close(
        trade_id=trade_id,
        **_close_args(menge=2.0, exit_fill_ids=["fill-1"],
                      event_id="attempt-1")) == trade_id
    before = _state(ledger)

    with pytest.raises(ledger.LedgerZuordnungUnklar):
        ledger.trade_close(**_close_args(
            menge=5.0, exit_fill_ids=["fill-1", "fill-2"],
            event_id="attempt-1"))

    assert _state(ledger) == before
    assert {row["fill_id"] for row in before["events"]} == {"fill-1"}


def test_same_event_anchor_with_wholly_different_fill_list_fails_closed(ledger):
    trade_id = _open(ledger)
    assert ledger.trade_close(
        trade_id=trade_id,
        **_close_args(menge=2.0, exit_fill_ids=["fill-1"],
                      event_id="attempt-1")) == trade_id
    before = _state(ledger)

    with pytest.raises(ledger.LedgerZuordnungUnklar):
        ledger.trade_close(**_close_args(
            menge=2.0, exit_fill_ids=["fill-2"], event_id="attempt-1"))

    assert _state(ledger) == before


def test_exact_reconciliation_resolves_regular_partial_close_lineage(ledger):
    first_trade_id = _open(ledger)
    assert ledger.trade_close(
        trade_id=first_trade_id,
        **_close_args(menge=4.0, ausstieg_preis=105.0,
                      exit_order_id="close-part-1",
                      exit_fill_ids=["close-fill-part-1"],
                      event_id="close-fill-part-1")) == first_trade_id
    remainder = ledger.offener_trade(
        "etoro", "ADBE", broker_position_id="position-1",
        broker_account_fingerprint="account-1", entry_order_id="entry-1")
    assert remainder is not None
    remainder_id = int(remainder["trade_id"])
    assert ledger.trade_close(
        trade_id=remainder_id,
        **_close_args(menge=6.0, ausstieg_preis=107.0,
                      exit_order_id="close-part-2",
                      exit_fill_ids=["close-fill-part-2"],
                      event_id="close-fill-part-2")) == remainder_id

    result = ledger.reconcile_closed_trade_exact(
        broker="etoro", symbol="ADBE", paper=True, decision_id=901,
        broker_position_id="position-1",
        broker_account_fingerprint="account-1",
        entry_order_id="entry-1", entry_fill_id="entry-fill-1",
        entry_fill={"quantity": 10.0, "price": 100.0},
        close_order_id="close-part-2",
        close_fill_id="close-fill-part-2",
        exit_reason="BROKER_STOP_LOSS",
        expected_entry_price=100.0,
        expected_close_price=107.0,
        expected_quantity=10.0,
    )
    assert result == {
        "status": "LINKED_CLOSED",
        "trade_id": remainder_id,
        "exit_order_id": "close-part-2",
    }

    # Replay bleibt auf demselben letzten Lineage-Segment und erzeugt weder
    # Trade noch Event ein zweites Mal.
    before = _state(ledger)
    assert ledger.reconcile_closed_trade_exact(
        broker="etoro", symbol="ADBE", paper=True, decision_id=901,
        broker_position_id="position-1",
        broker_account_fingerprint="account-1",
        entry_order_id="entry-1", entry_fill_id="entry-fill-1",
        close_order_id="close-part-2", close_fill_id="close-fill-part-2",
        expected_entry_price=100.0, expected_close_price=107.0,
        expected_quantity=10.0,
    ) == result
    after = _state(ledger)
    assert after["events"] == before["events"]
    # Ein identischer Reconciliation-Beleg ist ein echter No-op: auch
    # Metadaten-Zeitstempel werden nicht bei jedem Poll kuenstlich veraendert.
    assert after["trades"] == before["trades"]

    with ledger._connect() as con:
        rows = [dict(row) for row in con.execute(
            "SELECT * FROM trades ORDER BY trade_id")]
        entry_events = con.execute(
            "SELECT COUNT(*) FROM trade_entry_fills").fetchone()[0]
    assert [row["trade_id"] for row in rows] == [first_trade_id, remainder_id]
    assert all(row["link_status"] == "LINKED" for row in rows)
    assert all(row["ownership_status"] == "BOT_VERIFIED" for row in rows)
    assert entry_events == 1


def test_exact_partial_lineage_reports_the_exact_open_remainder(ledger):
    first_trade_id = _open(ledger)
    ledger.trade_close(
        trade_id=first_trade_id,
        **_close_args(menge=4.0, exit_fill_ids=["partial-fill"],
                      event_id="partial-fill"))
    remainder = ledger.offener_trade(
        "etoro", "ADBE", broker_position_id="position-1",
        broker_account_fingerprint="account-1", entry_order_id="entry-1")
    assert remainder is not None

    result = ledger.reconcile_closed_trade_exact(
        broker="etoro", symbol="ADBE", paper=True, decision_id=901,
        broker_position_id="position-1",
        broker_account_fingerprint="account-1",
        entry_order_id="entry-1", expected_entry_price=100.0,
        expected_quantity=10.0,
    )
    assert result == {
        "status": "NOT_CLOSED",
        "trade_id": int(remainder["trade_id"]),
    }


def test_okx_fill_replay_uses_instrument_and_fill_without_legacy_account(
        ledger):
    """OKX-Spot hat keine positionId; ein echter Fill bleibt dennoch exakt."""
    args = {
        "broker": "okx", "symbol": "SOL", "ausstieg_preis": 95.0,
        "menge": 2.0, "exit_grund": "STOP_LOSS", "paper": True,
        "einstieg_preis": 100.0,
        "eingestiegen_am": "2026-09-01T10:00:00+00:00",
        "broker_position_id": "SOL-EUR",
        "broker_account_fingerprint": "",
        "exit_order_id": "okx-order-1",
        "exit_fill_ids": ["okx:SOL-EUR:trade-1"],
        "event_id": "okx:SOL-EUR:trade-1",
        "critical": True,
    }

    trade_id = ledger.trade_close(**args)
    assert trade_id is not None
    before = _state(ledger)
    assert ledger.trade_close(**args) == trade_id
    assert _state(ledger) == before
    assert len(before["trades"]) == 1
    assert len(before["events"]) == 1
