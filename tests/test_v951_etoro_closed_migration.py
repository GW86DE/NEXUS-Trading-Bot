"""Regressionen fuer geschlossene eToro-Altdaten und Teilverkaeufe.

Die Tests verwenden die reale ADBE-ID-Kette vom 01.09.2026.  Sie pruefen
Funktionsablaeufe und persistierte Ergebnisse; Quelltext-Fragmentsuchen sind
ausdruecklich nicht Teil der Absicherung.
"""
from __future__ import annotations

import json
import sys
from copy import deepcopy
from types import SimpleNamespace

import pytest


ACCOUNT = "ba32f97fe3482fcbc326e51a"
DECISION = 4153277691980212224
ORDER = "378375675"
REFERENCE = "ac14a874-980a-468d-982f-57df9c4d56b9"
POSITION = "3592625451"
ENTRY_TIME = "2026-09-01T14:38:32.343Z"
ENTRY_FILL_ID = f"etoro-entry:{ORDER}:{POSITION}:{ENTRY_TIME}"
CLOSE_TIME = "2026-09-01T15:18:34.000Z"
CLOSE_FILL_ID = f"etoro-close:history:{POSITION}:{CLOSE_TIME}"


ADBE_FILLED_ORDER = {
    "accountId": 21577959,
    "gcid": 20343408,
    "portfolioId": 0,
    "orderId": 378375675,
    "referenceId": REFERENCE,
    "action": "open",
    "transaction": "buy",
    "type": "mkt",
    "status": {"id": 3, "name": "Filled", "errorCode": 0},
    "asset": {
        "symbol": "ADBE",
        "instrumentId": 1126,
        "currency": "USD",
        "settlementType": "REAL",
        "leverage": 1,
        "side": "long",
    },
    "requestedAmount": 14991.45,
    "requestedUnits": 51.0,
    "positionExecutions": [{
        "positionId": 3592625451,
        "state": "open",
        "remainingUnits": 51.0,
        "stopLossRate": 287.17,
        "takeProfitRate": 295.87,
        "openingData": {
            "openTime": "2026-09-01T14:38:32.127Z",
            "orderId": 378375675,
            "executionTime": ENTRY_TIME,
            "units": 51.0,
            "avgPrice": 293.92,
            "fees": 1.0,
            "taxes": 0.0,
        },
    }],
}


def _open_snapshot(*, units: float = 51.0) -> dict:
    row = {
        "positionId": POSITION,
        "orderId": ORDER,
        "instrumentId": 1126,
        "isBuy": True,
        "units": float(units),
        "openRate": 293.92,
        "stopLossRate": 287.17,
        "takeProfitRate": 295.87,
    }
    return {
        "open_ids": {POSITION},
        "rows": [dict(row)],
        "rows_by_position_id": {POSITION: dict(row)},
        "snapshot_id": "etoro:demo:adbe-still-open",
        "complete": True,
        "account_fingerprint": ACCOUNT,
        "environment": "DEMO",
    }


def _closed_history_row() -> dict:
    # Im eToro-Trade-History-Endpunkt ist orderId die ENTRY-Order, nicht die
    # Close-Order.  Genau diese historische Verwechslung muss die Migration
    # erkennen und korrigieren.
    return {
        "positionId": POSITION,
        "orderId": ORDER,
        "entry_order_id": ORDER,
        "close_order_id": "",
        "instrumentId": 1126,
        "isBuy": True,
        "units": 51.0,
        "openRate": 293.92,
        "closeRate": 287.16,
        "openTimestamp": ENTRY_TIME,
        "closeTimestamp": CLOSE_TIME,
        "closeReason": "StopLoss",
    }


@pytest.fixture
def adbe_env(monkeypatch, tmp_path):
    monkeypatch.setenv("TRADINGBOT_TEST_STATE_DIR", str(tmp_path))
    import decision_analytics as analytics
    import etoro_reconciliation as reconciliation
    import trade_ledger

    monkeypatch.setattr(analytics, "DB_PATH", tmp_path / "decision_history.sqlite")
    monkeypatch.setattr(reconciliation, "_notify", lambda *_a, **_k: None)
    trade_ledger.init_ledger()
    return reconciliation, analytics, trade_ledger


def _start_adbe_reconciliation(reconciliation, analytics) -> str:
    analytics.record({
        "decision_id": DECISION,
        "symbol": "ADBE",
        "asset_type": "stock",
        "broker": "etoro",
        "paper": True,
        "status": "APPROVED",
        "profile": "offensiv",
        "price": 290.07,
        "qty": 51.0,
        "stop": 287.1696,
        "take": 295.8707,
    })
    reconciliation.start_intent(
        decision_id=DECISION,
        symbol="ADBE",
        paper=True,
        profile="offensiv",
        quantity=51.0,
        price=290.07,
        stop=287.1696,
        take_profit=295.8707,
        account_fingerprint=ACCOUNT,
    )
    reconciliation.accepted(
        DECISION, order_id=ORDER, reference_id=REFERENCE)
    reconciliation.apply_broker_evidence(
        DECISION, deepcopy(ADBE_FILLED_ORDER))
    return reconciliation.domain_key(
        paper=True, profile="offensiv", account_fingerprint=ACCOUNT)


def _make_closed_legacy_trade(trade_ledger, *, account: str = ACCOUNT) -> int:
    corrupt_exit_fill = (
        f"etoro:{account}:close:{POSITION}:{ORDER}:evidence:legacy")
    trade_id = trade_ledger.trade_open(
        broker="etoro",
        symbol="ADBE",
        menge=51.0,
        einstieg_preis=293.92,
        referenzpreis=290.07,
        gebuehr=1.25,
        asset_type="stock",
        waehrung="USD",
        paper=True,
        zeit=ENTRY_TIME,
        external=True,
        broker_position_id=POSITION,
        entry_order_id=ORDER,
        broker_account_fingerprint=account,
    )
    assert trade_id is not None
    assert trade_ledger.trade_close(
        broker="etoro",
        symbol="ADBE",
        ausstieg_preis=287.16,
        menge=51.0,
        exit_grund="BROKER_STOP_LOSS",
        paper=True,
        zeit=CLOSE_TIME,
        exit_order_id=ORDER,
        exit_fill_ids=[corrupt_exit_fill],
        event_id=corrupt_exit_fill,
        trade_id=trade_id,
        broker_position_id=POSITION,
        broker_account_fingerprint=account,
        entry_order_id=ORDER,
    ) == trade_id
    # Reale 9.5.0-Korruption: der geschlossene Altsatz besitzt keine
    # decision-/Entry-Fill-Verknuepfung und fuehrt die Entry-orderId zugleich
    # faelschlich als exit_order_id. Die Ergebniswerte sind dagegen bereits
    # brokerseitig verbucht und duerfen nicht erneut berechnet werden.
    with trade_ledger._connect() as con:
        con.execute(
            """UPDATE trades
               SET decision_id=NULL, link_status='LEGACY_UNLINKED',
                   entry_fill_id='', entry_fill_ids_json='[]',
                   exit_order_id=?,
                   ausstieg_referenz=287.25,
                   brutto_pnl=-344.76, gebuehren=2.75,
                   slippage_geschaetzt=0.12345, netto_pnl=-347.51,
                   haltedauer_minuten=40.03, mfe_pct=0.81, mae_pct=-2.31
               WHERE trade_id=?""",
            (ORDER, trade_id),
        )
    return int(trade_id)


def _trade_row(trade_ledger, trade_id: int) -> dict:
    with trade_ledger._connect() as con:
        row = con.execute(
            "SELECT * FROM trades WHERE trade_id=?", (trade_id,)).fetchone()
    assert row is not None
    return dict(row)


def test_closed_adbe_legacy_trade_is_relinked_without_recomputing_result(adbe_env):
    reconciliation, analytics, trade_ledger = adbe_env
    _start_adbe_reconciliation(reconciliation, analytics)
    target_id = _make_closed_legacy_trade(trade_ledger)
    decoy_id = _make_closed_legacy_trade(
        trade_ledger, account="different-etoro-account")

    immutable_fields = (
        "eingestiegen_am",
        "einstieg_preis",
        "einstieg_referenz",
        "menge",
        "einstieg_gebuehr",
        "ausgestiegen_am",
        "ausstieg_preis",
        "ausstieg_referenz",
        "exit_grund",
        "brutto_pnl",
        "gebuehren",
        "slippage_geschaetzt",
        "netto_pnl",
        "haltedauer_minuten",
        "mfe_pct",
        "mae_pct",
    )
    before = _trade_row(trade_ledger, target_id)
    before_values = {name: before[name] for name in immutable_fields}
    decoy_before = _trade_row(trade_ledger, decoy_id)

    # Zweimal ausfuehren: Migration und Fill-Backfill muessen restart-/replay-
    # sicher sein und denselben vorhandenen Trade aktualisieren.
    for _ in range(2):
        changed = reconciliation.confirm_position_closed(
            paper=True,
            account_fingerprint=ACCOUNT,
            position_id=POSITION,
            close_detail=_closed_history_row(),
        )
        assert changed

    after = _trade_row(trade_ledger, target_id)
    assert after["decision_id"] == DECISION
    assert after["link_status"] == "LINKED"
    assert after["entry_fill_id"] == ENTRY_FILL_ID
    assert json.loads(after["entry_fill_ids_json"]) == [ENTRY_FILL_ID]
    assert after["exit_order_id"] == ""
    assert json.loads(after["exit_fill_ids_json"]) == [CLOSE_FILL_ID]
    assert {name: after[name] for name in immutable_fields} == before_values

    with trade_ledger._connect() as con:
        trades = con.execute(
            """SELECT trade_id FROM trades
               WHERE broker='etoro' AND broker_account_fingerprint=?
                 AND broker_position_id=? AND entry_order_id=?""",
            (ACCOUNT, POSITION, ORDER),
        ).fetchall()
        fills = con.execute(
            """SELECT * FROM trade_entry_fills
               WHERE trade_id=? ORDER BY id""", (target_id,)).fetchall()
        exit_events = con.execute(
            """SELECT * FROM trade_exit_events
               WHERE trade_id=? ORDER BY id""", (target_id,)).fetchall()
    assert [int(row["trade_id"]) for row in trades] == [target_id]
    assert len(fills) == 1
    fill = dict(fills[0])
    assert fill["broker"] == "etoro"
    assert fill["broker_account_fingerprint"] == ACCOUNT
    assert fill["instrument"] == POSITION
    assert fill["order_id"] == ORDER
    assert fill["fill_id"] == ENTRY_FILL_ID
    assert fill["quantity"] == pytest.approx(51.0)
    assert fill["price"] == pytest.approx(293.92)
    assert fill["filled_at"] == ENTRY_TIME

    assert len(exit_events) == 1
    exit_event = dict(exit_events[0])
    assert exit_event["broker"] == "etoro"
    assert exit_event["broker_account_fingerprint"] == ACCOUNT
    assert exit_event["instrument"] == POSITION
    assert exit_event["order_id"] == ""
    assert exit_event["fill_id"] == CLOSE_FILL_ID
    assert exit_event["event_id"] == CLOSE_FILL_ID
    assert exit_event["quantity"] == pytest.approx(51.0)
    assert exit_event["price"] == pytest.approx(287.16)

    # Ein zufaellig gleicher positionId/orderId-Satz aus einem anderen Konto
    # ist kein Migrationsziel und bleibt vollstaendig unangetastet.
    assert _trade_row(trade_ledger, decoy_id) == decoy_before


def test_partial_close_fill_with_same_position_id_open_does_not_terminalize(
        adbe_env, monkeypatch, tmp_path):
    reconciliation, analytics, trade_ledger = adbe_env
    domain = _start_adbe_reconciliation(reconciliation, analytics)
    open_snapshot = _open_snapshot(units=51.0)

    class TruthBroker:
        name = "etoro"
        paper = True

        @staticmethod
        def account_fingerprint():
            return ACCOUNT

        @staticmethod
        def trade_history_snapshot(_min_date, *, max_pages=12, force=False):
            return {"rows": [], "complete": True}

    reconciliation.verify_broker_truth(
        TruthBroker(),
        paper=True,
        profile="offensiv",
        account_fingerprint=ACCOUNT,
        position_snapshot=open_snapshot,
    )
    confirmed = reconciliation.record_for(DECISION)
    assert confirmed["broker_position_status"] == "OPEN_CONFIRMED"
    assert confirmed["open_position_ids"] == [POSITION]
    assert reconciliation.active_for_domain(domain) == []

    from broker.base import Fill
    from fill_tracker import FillProgressTracker
    from instrument_identity import canonical_key
    # live_trader importiert den Marktregime-Downloader, obwohl dieser im
    # Fillpfad nicht benutzt wird. Das schlanke Release-Testenvironment bringt
    # yfinance absichtlich nicht mit; ein Modul-Dummy verhindert hier nur
    # einen sachfremden Importfehler und ersetzt keinerlei getestete Funktion.
    try:
        __import__("yfinance")
    except ModuleNotFoundError:
        monkeypatch.setitem(sys.modules, "yfinance", SimpleNamespace())
    from live_trader import capture_new_fills
    from position_manager import PositionManager

    contract = SimpleNamespace(
        conId=1126, localSymbol="ADBE", symbol="ADBE", currency="USD")
    manager = PositionManager(tmp_path / "position_state.json")
    manager.register_buy(
        contract,
        51.0,
        293.92,
        "USD",
        "stock",
        50_000.0,
        287.1696,
        295.8707,
        source="BOT",
        management_mode="AUTO",
        position_ids=[POSITION],
        order_ids=[ORDER],
        reference_id=REFERENCE,
        decision_id=DECISION,
        instrument_id="1126",
        account_fingerprint=ACCOUNT,
        broker_environment="DEMO",
        snapshot_id=open_snapshot["snapshot_id"],
        fill_id=ENTRY_FILL_ID,
    )
    ledger_id = trade_ledger.trade_open(
        broker="etoro",
        symbol="ADBE",
        menge=51.0,
        einstieg_preis=293.92,
        asset_type="stock",
        waehrung="USD",
        decision_id=DECISION,
        paper=True,
        zeit=ENTRY_TIME,
        broker_position_id=POSITION,
        entry_order_id=ORDER,
        entry_fill_id=ENTRY_FILL_ID,
        entry_fill_ids=[ENTRY_FILL_ID],
        broker_account_fingerprint=ACCOUNT,
        reconciliation_status="CONFIRMED_OPEN",
    )
    assert ledger_id is not None

    partial_fill = Fill(
        fill_id="etoro-partial-close-fill-adbe-10",
        order_id="etoro-partial-close-order-adbe",
        symbol="ADBE",
        side="SELL",
        quantity=10.0,
        price=291.50,
        currency="USD",
        asset_type="stock",
        broker_id=POSITION,
        timestamp="2026-09-01T15:05:00.000Z",
        explicit_fees=0.0,
        account_fingerprint=ACCOUNT,
    )

    class FillBroker:
        name = "etoro"
        paper = True

        def __init__(self):
            self._fills = [partial_fill]

        def fills(self):
            rows, self._fills = self._fills, []
            return rows

        @staticmethod
        def account_fingerprint():
            return ACCOUNT

        @staticmethod
        def ist_paper():
            return True

        @staticmethod
        def on_fill_housekeeping(_fill):
            return None

        @staticmethod
        def position_snapshot(*, force=True):
            raise AssertionError(
                "Der bereits uebergebene Zyklus-Snapshot muss wiederverwendet werden")

    close_calls = []
    real_confirm = reconciliation.confirm_position_closed

    def observe_terminal_close(*args, **kwargs):
        close_calls.append((args, kwargs))
        return real_confirm(*args, **kwargs)

    monkeypatch.setattr(
        reconciliation, "confirm_position_closed", observe_terminal_close)
    risk = SimpleNamespace(
        realized_pnl_today=0.0,
        unknown_pnl_trades_today=0,
        register_realized_pnl=lambda *_a, **_k: None,
        register_unknown_pnl_trade=lambda *_a, **_k: None,
        register_estimated_cost=lambda *_a, **_k: None,
    )
    instrument = SimpleNamespace(
        contract=contract, asset_type="stock", currency="USD")
    processed = capture_new_fills(
        FillBroker(),
        manager,
        {
            partial_fill.order_id: {
                "owner": "BOT",
                "broker": "etoro",
                "paper": True,
                "account_fingerprint": ACCOUNT,
                "label": "TEILVERKAUF",
                "reason": "Broker bestaetigt Teilverkauf",
            },
        },
        FillProgressTracker(tmp_path / "fill_progress.json"),
        None,
        risk,
        50_000.0,
        {canonical_key("ADBE", "stock"): instrument},
        notify_enabled=False,
        position_snapshot=open_snapshot,
    )

    assert processed == 1
    assert close_calls == []
    remaining = manager.get_by_position_id(
        POSITION, account_fingerprint=ACCOUNT)
    assert remaining is not None
    assert remaining.quantity == pytest.approx(41.0)

    record = reconciliation.record_for(DECISION)
    assert record["state"] == "FILLED"
    assert record["position_state"] == "OPEN_CONFIRMED"
    assert record["broker_position_status"] == "OPEN_CONFIRMED"
    assert record["open_position_ids"] == [POSITION]
    assert record["closed_position_ids"] == []
    assert record["verified_position_ids"] == [POSITION]
    assert reconciliation.active_for_domain(domain) == []

    with trade_ledger._connect() as con:
        closed_qty = con.execute(
            """SELECT menge FROM trades
               WHERE broker_position_id=? AND ausgestiegen_am IS NOT NULL""",
            (POSITION,),
        ).fetchone()
        open_qty = con.execute(
            """SELECT menge FROM trades
               WHERE broker_position_id=? AND ausgestiegen_am IS NULL""",
            (POSITION,),
        ).fetchone()
    assert float(closed_qty[0]) == pytest.approx(10.0)
    assert float(open_qty[0]) == pytest.approx(41.0)


def test_history_partial_close_never_beats_fresh_open_pnl(adbe_env):
    reconciliation, analytics, _trade_ledger = adbe_env
    domain = _start_adbe_reconciliation(reconciliation, analytics)
    partial = _closed_history_row()
    partial["units"] = 10.0
    partial["closedUnits"] = 10.0
    partial["closeTimestamp"] = "2026-09-01T15:00:00.000Z"

    class PartialHistoryBroker:
        paper = True

        @staticmethod
        def position_snapshot(force=True):
            return _open_snapshot(units=41.0)

        @staticmethod
        def trade_history_snapshot(_min_date, force=True):
            return {"rows": [dict(partial)], "complete": True}

    for _ in range(2):
        reconciliation.verify_broker_truth(
            PartialHistoryBroker(), paper=True, profile="offensiv",
            account_fingerprint=ACCOUNT)

    record = reconciliation.record_for(DECISION)
    assert record["position_state"] == "OPEN_CONFIRMED"
    assert record["open_position_ids"] == [POSITION]
    assert record["closed_position_ids"] == []
    assert record["state"] == "FILLED"
    assert reconciliation.active_for_domain(domain) == []


def test_falsely_history_closed_record_repairs_to_open_on_next_pnl(adbe_env):
    reconciliation, analytics, _trade_ledger = adbe_env
    _start_adbe_reconciliation(reconciliation, analytics)

    def _poison(record):
        record["open_position_ids"] = []
        record["closed_position_ids"] = [POSITION]
        record["ledger_close_backfill"] = {
            POSITION: {
                "status": "LEDGER_BACKFILL_PENDING",
                "position_id": POSITION,
            }
        }
        reconciliation._sync_legacy_state(record)
        return True

    poisoned = reconciliation._mutiere(DECISION, _poison)
    assert poisoned["position_state"] == "CLOSED_CONFIRMED"

    class OpenAgainBroker:
        paper = True

        @staticmethod
        def position_snapshot(force=True):
            return _open_snapshot(units=41.0)

        @staticmethod
        def trade_history_snapshot(_min_date, force=True):
            partial = _closed_history_row()
            partial["units"] = 10.0
            return {"rows": [partial], "complete": True}

    reconciliation.verify_broker_truth(
        OpenAgainBroker(), paper=True, profile="offensiv",
        account_fingerprint=ACCOUNT)
    repaired = reconciliation.record_for(DECISION)
    assert repaired["position_state"] == "OPEN_CONFIRMED"
    assert repaired["open_position_ids"] == [POSITION]
    assert repaired["closed_position_ids"] == []
    assert POSITION not in repaired.get("ledger_close_backfill", {})


def test_broker_close_crash_window_closes_exact_existing_open_ledger(adbe_env):
    reconciliation, analytics, trade_ledger = adbe_env
    domain = _start_adbe_reconciliation(reconciliation, analytics)
    trade_id = trade_ledger.trade_open(
        broker="etoro", symbol="ADBE", menge=51.0,
        einstieg_preis=293.92, referenzpreis=290.07,
        asset_type="stock", waehrung="USD", decision_id=DECISION,
        paper=True, zeit=ENTRY_TIME, broker_position_id=POSITION,
        entry_order_id=ORDER, entry_fill_id=ENTRY_FILL_ID,
        entry_fill_ids=[ENTRY_FILL_ID],
        entry_fills=[{
            "fill_id": ENTRY_FILL_ID, "ordId": ORDER,
            "quantity": 51.0, "price": 293.92, "filled_at": ENTRY_TIME,
        }],
        client_order_id=REFERENCE, ownership_status="BOT_VERIFIED",
        broker_account_fingerprint=ACCOUNT,
        reconciliation_status="CONFIRMED_OPEN")
    assert trade_id

    changed = reconciliation.confirm_position_closed(
        paper=True, account_fingerprint=ACCOUNT,
        position_id=POSITION, close_detail=_closed_history_row())
    assert changed

    row = _trade_row(trade_ledger, int(trade_id))
    assert row["ausgestiegen_am"]
    assert row["decision_id"] == DECISION
    assert row["link_status"] == "LINKED"
    assert row["exit_order_id"] == ""
    record = reconciliation.record_for(DECISION)
    assert record["state"] == "CLOSED_BEFORE_IMPORT"
    assert record["ledger_close_backfill"][POSITION]["status"] == "LINKED_CLOSED"
    assert reconciliation.active_for_domain(domain) == []


def test_broker_closed_but_ledger_pending_keeps_domain_blocked(
        adbe_env, monkeypatch):
    """Der Kauf bleibt gesperrt -- aber unter dem richtigen Namen (9.5.2).

    Bis 9.5.1 zaehlte eine reine Buchungsluecke als "ungeklaerte Order".
    Das ist fachlich falsch: beim Broker ist nichts mehr offen, es kann
    nichts doppelt gekauft werden. Genau diese Verwechslung liess CRM am
    02.09.2026 als zweite "ungeklaerte Order" erscheinen.

    Die Schutzwirkung bleibt trotzdem bestehen, und zwar aus dem RICHTIGEN
    Grund: solange ein abgeschlossener Trade unverbucht ist, kennt die
    Tagesverlustgrenze ihren eigenen Wert nicht. Dafuer gibt es jetzt
    ``pnl_unvollstaendig()`` und die Bedingung ``buchung_vollstaendig``.
    """
    reconciliation, analytics, _trade_ledger = adbe_env
    domain = _start_adbe_reconciliation(reconciliation, analytics)
    monkeypatch.setattr(
        reconciliation, "_backfill_closed_ledger",
        lambda *_a, **_k: {
            "status": "LEDGER_BACKFILL_PENDING",
            "position_id": POSITION,
            "error": "simulierter DB-Abbruch",
        })

    changed = reconciliation.confirm_position_closed(
        paper=True, account_fingerprint=ACCOUNT,
        position_id=POSITION, close_detail=_closed_history_row())
    assert changed
    record = reconciliation.record_for(DECISION)
    assert record["position_state"] == "CLOSED_CONFIRMED"
    assert record["state"] == "CLOSED_ACCOUNTING_PENDING"
    assert "simulierter DB-Abbruch" in str(
        record["ledger_close_backfill"][POSITION])

    # Keine offene Brokerorder mehr -- also auch kein Doppelkaufrisiko.
    assert reconciliation.active_for_domain(domain) == []
    assert reconciliation.blocking_detail(domain) == ""

    # Aber die Buchungsluecke ist sichtbar UND sperrt weiterhin den Kauf.
    luecken = reconciliation.buchungsluecken(domain)
    assert [x["symbol"] for x in luecken] == ["ADBE"]
    unvollstaendig, text = reconciliation.pnl_unvollstaendig(domain)
    assert unvollstaendig is True
    assert "PNL_INCOMPLETE" in text and "ADBE" in text

    import trading_ready
    bereitschaft = trading_ready.Handelsbereitschaft("etoro")
    for name in bereitschaft.bedingungen:
        bereitschaft.melde(name, True, "")
    bereitschaft.melde("buchung_vollstaendig", not unvollstaendig, text)
    erlaubt, grund = bereitschaft.darf_kaufen()
    assert erlaubt is False, (
        "Eine unverbuchte Schliessung muss den Kauf weiterhin sperren")
    assert "buchung_vollstaendig" in [
        b.name for b in bereitschaft.offene_bedingungen()]
    # Der Sammelgrund kann bei einem frischen Objekt noch die Anlaufsperre
    # nennen; entscheidend ist das Detail der Bedingung selbst.
    assert "PNL_INCOMPLETE" in bereitschaft.bedingungen[
        "buchung_vollstaendig"].detail
    assert isinstance(grund, str) and grund


def test_close_without_account_never_mutates_two_demo_accounts(adbe_env):
    reconciliation, analytics, _trade_ledger = adbe_env
    _start_adbe_reconciliation(reconciliation, analytics)
    other_decision = DECISION + 1
    other_account = "different-etoro-demo-account"
    analytics.record({
        "decision_id": other_decision, "symbol": "ADBE",
        "asset_type": "stock", "broker": "etoro", "paper": True,
        "status": "APPROVED", "profile": "offensiv", "price": 290.07,
        "qty": 51.0, "stop": 287.1696, "take": 295.8707,
    })
    reconciliation.start_intent(
        decision_id=other_decision, symbol="ADBE", paper=True,
        profile="offensiv", quantity=51.0, price=290.07,
        stop=287.1696, take_profit=295.8707,
        account_fingerprint=other_account)
    reconciliation.accepted(
        other_decision, order_id="other-order",
        reference_id="other-reference")
    other_evidence = deepcopy(ADBE_FILLED_ORDER)
    other_evidence["orderId"] = "other-order"
    other_evidence["referenceId"] = "other-reference"
    other_evidence["positionExecutions"][0]["openingData"][
        "orderId"] = "other-order"
    reconciliation.apply_broker_evidence(other_decision, other_evidence)

    assert reconciliation.confirm_position_closed(
        paper=True, position_id=POSITION,
        close_detail=_closed_history_row()) == []
    assert reconciliation.record_for(DECISION)["closed_position_ids"] == []
    assert reconciliation.record_for(other_decision)[
        "closed_position_ids"] == []


def test_legacy_open_record_binds_only_after_exact_current_account_proof(adbe_env):
    reconciliation, analytics, _trade_ledger = adbe_env
    analytics.record({
        "decision_id": DECISION, "symbol": "ADBE", "asset_type": "stock",
        "broker": "etoro", "paper": True, "status": "APPROVED",
        "profile": "offensiv", "price": 290.07, "qty": 51.0,
        "stop": 287.1696, "take": 295.8707,
    })
    reconciliation.start_intent(
        decision_id=DECISION, symbol="ADBE", paper=True,
        profile="offensiv", quantity=51.0, price=290.07,
        stop=287.1696, take_profit=295.8707,
        account_fingerprint="")
    reconciliation.accepted(
        DECISION, order_id=ORDER, reference_id=REFERENCE)
    reconciliation.apply_broker_evidence(
        DECISION, deepcopy(ADBE_FILLED_ORDER))

    class CurrentAccountBroker:
        @staticmethod
        def trade_history_snapshot(_min_date, force=True):
            return {"rows": [], "complete": True}

    changed = reconciliation.verify_broker_truth(
        CurrentAccountBroker(), paper=True, profile="offensiv",
        account_fingerprint=ACCOUNT, position_snapshot=_open_snapshot())
    assert changed
    record = reconciliation.record_for(DECISION)
    assert record["account_fingerprint"] == ACCOUNT
    assert record["domain"] == reconciliation.domain_key(
        paper=True, profile="offensiv", account_fingerprint=ACCOUNT)
    assert record["open_position_ids"] == [POSITION]
    assert record["position_state"] == "OPEN_CONFIRMED"


def test_unbound_complete_snapshot_cannot_terminalize_history_position(adbe_env):
    reconciliation, analytics, _trade_ledger = adbe_env
    domain = _start_adbe_reconciliation(reconciliation, analytics)

    class ClosedHistoryBroker:
        @staticmethod
        def trade_history_snapshot(_min_date, force=True):
            return {"rows": [_closed_history_row()], "complete": True}

    assert reconciliation.verify_broker_truth(
        ClosedHistoryBroker(), paper=True, profile="offensiv",
        account_fingerprint=ACCOUNT,
        position_snapshot={"complete": True, "position_ids": []}) == []
    record = reconciliation.record_for(DECISION)
    assert record["closed_position_ids"] == []
    assert record["position_state"] != "CLOSED_CONFIRMED"
    assert reconciliation.active_for_domain(domain)


def test_multiple_history_partial_closes_commit_every_fill_before_unlock(adbe_env):
    reconciliation, analytics, trade_ledger = adbe_env
    domain = _start_adbe_reconciliation(reconciliation, analytics)
    trade_id = trade_ledger.trade_open(
        broker="etoro", symbol="ADBE", menge=51.0,
        einstieg_preis=293.92, asset_type="stock", waehrung="USD",
        decision_id=DECISION, paper=True, zeit=ENTRY_TIME,
        broker_position_id=POSITION, entry_order_id=ORDER,
        entry_fill_id=ENTRY_FILL_ID, entry_fill_ids=[ENTRY_FILL_ID],
        broker_account_fingerprint=ACCOUNT,
        reconciliation_status="CONFIRMED_OPEN")
    assert trade_id

    first = _closed_history_row()
    first.update({
        "closedUnits": 20.0, "units": 20.0, "closeRate": 290.0,
        "closeTimestamp": "2026-09-01T15:00:00.000Z",
    })
    second = _closed_history_row()
    second.update({
        "closedUnits": 31.0, "units": 31.0, "closeRate": 288.0,
        "closeTimestamp": "2026-09-01T15:10:00.000Z",
    })

    class MultiCloseHistoryBroker:
        @staticmethod
        def trade_history_snapshot(_min_date, force=True):
            return {"rows": [dict(first), dict(second)], "complete": True}

    empty = {
        "open_ids": set(), "position_ids": set(), "rows": [],
        "rows_by_position_id": {}, "snapshot_id": "etoro:demo:closed",
        "complete": True, "account_fingerprint": ACCOUNT,
        "environment": "DEMO",
    }
    reconciliation.verify_broker_truth(
        MultiCloseHistoryBroker(), paper=True, profile="offensiv",
        account_fingerprint=ACCOUNT, position_snapshot=empty)
    record = reconciliation.record_for(DECISION)
    assert record["state"] == "CLOSED_BEFORE_IMPORT"
    assert record["ledger_close_backfill"][POSITION][
        "status"] == "LINKED_CLOSED"
    assert reconciliation.active_for_domain(domain) == []

    with trade_ledger._connect() as con:
        rows = [dict(row) for row in con.execute(
            """SELECT menge, ausstieg_preis, brutto_pnl, netto_pnl, fee_quality, einstieg_gebuehr FROM trades
               WHERE broker='etoro' AND broker_account_fingerprint=?
                 AND broker_position_id=? ORDER BY trade_id""",
            (ACCOUNT, POSITION)).fetchall()]
        events = [dict(row) for row in con.execute(
            """SELECT quantity, price, fill_id FROM trade_exit_events
               WHERE broker='etoro' AND broker_account_fingerprint=?
                 AND instrument=? ORDER BY id""",
            (ACCOUNT, POSITION)).fetchall()]
    assert [(row["menge"], row["ausstieg_preis"]) for row in rows] == [
        pytest.approx((20.0, 290.0)), pytest.approx((31.0, 288.0))]
    # The history fixtures contain no exit fees. The fills and gross results
    # must be complete, but inventing net values would hide the missing fees.
    assert [row["brutto_pnl"] for row in rows] == pytest.approx([
        (290.0 - 293.92) * 20, (288.0 - 293.92) * 31])
    assert all(row["netto_pnl"] is None for row in rows)
    assert all(row["fee_quality"] == "UNKNOWN" for row in rows)
    assert all(row["einstieg_gebuehr"] is None for row in rows)
    assert [(row["quantity"], row["price"]) for row in events] == [
        pytest.approx((20.0, 290.0)), pytest.approx((31.0, 288.0))]

    # Wiederholung nach einem Neustart darf weder Trades noch Exitereignisse
    # duplizieren und die Domain bleibt erst nach dem bestaetigten Commit frei.
    reconciliation._retry_closed_ledger_backfills(domain)
    with trade_ledger._connect() as con:
        assert con.execute(
            "SELECT COUNT(*) FROM trades WHERE broker_position_id=?",
            (POSITION,)).fetchone()[0] == 2
        assert con.execute(
            "SELECT COUNT(*) FROM trade_exit_events WHERE instrument=?",
            (POSITION,)).fetchone()[0] == 2
