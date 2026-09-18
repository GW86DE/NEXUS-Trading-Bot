"""Upgrade-Replay fuer die realen eToro-Laufzeitdaten vom 01.09.2026.

Diese Tests bilden die beiden unterschiedlichen Altlasten bewusst gemeinsam
ab: historische, bereits geschlossene v2-Datensaetze ohne Kontofingerabdruck
und die vollstaendig beweisbare ADBE-Kette. Ein Upgrade darf die alten
Closed-Saetze weder einem Konto zuschreiben noch daraus eine ewige Kaufsperre
machen. Die exakte ADBE-Kette muss dagegen positionsgenau heilbar bleiben.
"""
from __future__ import annotations

import json

import pytest


ACCOUNT = "ba32f97fe3482fcbc326e51a"
ADBE_DECISION = 4153277691980212224
ADBE_ORDER = "378375675"
ADBE_REFERENCE = "ac14a874-980a-468d-982f-57df9c4d56b9"
ADBE_POSITION = "3592625451"
ADBE_ENTRY_TIME = "2026-09-01T14:38:32.343Z"
ADBE_CLOSE_TIME = "2026-09-01T15:13:35.230000+00:00"

LEGACY_DECISION = 3174572892869043200
LEGACY_ORDER = "377432172"
LEGACY_REFERENCE = "8caceb07-75b4-4b10-a531-fae417ba9e0e"
LEGACY_POSITION = "3590107110"


def _legacy_closed_record() -> dict:
    """Realer MSFT-v2-Typ: geschlossen, aber noch ohne Accountbindung."""
    return {
        "decision_id": LEGACY_DECISION,
        "symbol": "MSFT",
        "broker": "etoro",
        "paper": True,
        "profile": "offensiv",
        "domain": "etoro:demo",
        "quantity": 29.0,
        "price": 499.20,
        "state": "CLOSED_BEFORE_IMPORT",
        "order_ids": [LEGACY_ORDER],
        "reference_id": LEGACY_REFERENCE,
        "position_ids": [LEGACY_POSITION],
        "closed_position_ids": [LEGACY_POSITION],
        "fills": [{
            "position_id": LEGACY_POSITION,
            "quantity": 29.0,
            "price": 499.20,
            "execution_time": "2026-08-27T19:33:49.359506+00:00",
            "execution_id": "",
        }],
        "filled_quantity": 29.0,
        "remaining_quantity": 0.0,
    }


def _adbe_record() -> dict:
    """Unveraenderte Identitaetskette des hochgeladenen ADBE-Vorfalls."""
    return {
        "decision_id": ADBE_DECISION,
        "symbol": "ADBE",
        "broker": "etoro",
        "paper": True,
        "profile": "offensiv",
        "domain": f"etoro:demo:{ACCOUNT}",
        "account_fingerprint": ACCOUNT,
        "quantity": 51.0,
        "price": 290.07,
        "stop": 287.1696,
        "take_profit": 295.8707,
        "state": "UNKNOWN_AFTER_SUBMIT",
        "created_at_utc": "2026-09-01T14:38:30.452691+00:00",
        "updated_at_utc": "2026-09-01T14:38:35.136096+00:00",
        "order_ids": [ADBE_ORDER],
        "reference_id": ADBE_REFERENCE,
        "position_ids": [ADBE_POSITION],
        "fills": [{
            "position_id": ADBE_POSITION,
            "quantity": 51.0,
            "price": 293.92,
            "execution_time": ADBE_ENTRY_TIME,
            "execution_id": "",
        }],
        "filled_quantity": 51.0,
        "remaining_quantity": 0.0,
        "broker_execution_state": "FILLED",
        "position_verified": False,
        "broker_position_status": "PROPAGATION_PENDING",
    }


def _write_reconciliation(tmp_path, *records: dict) -> None:
    (tmp_path / "etoro_reconciliation.json").write_text(
        json.dumps({
            "schema_version": 2,
            "records": {
                str(item["decision_id"]): item for item in records
            },
        }),
        encoding="utf-8",
    )


@pytest.fixture
def actual_upgrade_env(monkeypatch, tmp_path):
    monkeypatch.setenv("TRADINGBOT_TEST_STATE_DIR", str(tmp_path))
    import decision_analytics as analytics
    import etoro_reconciliation as reconciliation
    import trade_ledger

    monkeypatch.setattr(
        analytics, "DB_PATH", tmp_path / "decision_history.sqlite")
    monkeypatch.setattr(reconciliation, "_notify", lambda *_a, **_k: None)
    return reconciliation, analytics, trade_ledger


def test_accountless_legacy_closed_record_stays_terminal_and_unowned(
        actual_upgrade_env, tmp_path):
    reconciliation, _analytics, _trade_ledger = actual_upgrade_env
    _write_reconciliation(tmp_path, _legacy_closed_record())

    known_account_domain = reconciliation.domain_key(
        paper=True, profile="offensiv", account_fingerprint=ACCOUNT)
    migrated = reconciliation.record_for(LEGACY_DECISION)

    assert migrated["state"] == "CLOSED_BEFORE_IMPORT"
    assert reconciliation.active_for_domain(known_account_domain) == []
    assert not migrated.get("account_fingerprint")
    assert migrated["domain"] == "etoro:demo"
    assert migrated.get("position_verified") is not True
    assert migrated.get("verified_position_ids") in (None, [])

    # Auch ein persistierter Schema-Roundtrip darf aus dem kontolosen
    # Altbeleg weder eine Sperre noch vermeintliches Bot-Eigentum erzeugen.
    reconciliation._save(reconciliation._load())
    roundtrip = reconciliation.record_for(LEGACY_DECISION)
    assert roundtrip["state"] == "CLOSED_BEFORE_IMPORT"
    assert reconciliation.active_for_domain(known_account_domain) == []
    assert not roundtrip.get("account_fingerprint")


def test_actual_adbe_chain_heals_next_to_legacy_closed_without_pnl_rewrite(
        actual_upgrade_env, tmp_path):
    reconciliation, analytics, trade_ledger = actual_upgrade_env
    _write_reconciliation(tmp_path, _legacy_closed_record(), _adbe_record())

    analytics.record({
        "decision_id": ADBE_DECISION,
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
    trade_id = trade_ledger.trade_open(
        broker="etoro",
        symbol="ADBE",
        menge=51.0,
        einstieg_preis=293.92,
        asset_type="stock",
        waehrung="USD",
        paper=True,
        zeit="2026-09-01T14:38:48+00:00",
        external=True,
        broker_position_id=ADBE_POSITION,
        entry_order_id=ADBE_ORDER,
        broker_account_fingerprint=ACCOUNT,
    )
    assert trade_id is not None
    corrupt_close_fill = (
        f"etoro:{ACCOUNT}:close:{ADBE_POSITION}:{ADBE_ORDER}:evidence:legacy")
    assert trade_ledger.trade_close(
        broker="etoro",
        symbol="ADBE",
        ausstieg_preis=287.16,
        menge=51.0,
        exit_grund="eToro-Ausfuehrung exakt wiederhergestellt",
        paper=True,
        zeit=ADBE_CLOSE_TIME,
        exit_order_id=ADBE_ORDER,
        exit_fill_ids=[corrupt_close_fill],
        event_id=corrupt_close_fill,
        trade_id=trade_id,
        broker_position_id=ADBE_POSITION,
        broker_account_fingerprint=ACCOUNT,
        entry_order_id=ADBE_ORDER,
    ) == trade_id

    immutable_financial_fields = (
        "eingestiegen_am", "einstieg_preis", "einstieg_referenz", "menge",
        "einstieg_gebuehr", "ausgestiegen_am", "ausstieg_preis",
        "ausstieg_referenz", "brutto_pnl", "gebuehren",
        "slippage_geschaetzt", "netto_pnl", "haltedauer_minuten",
        "mfe_pct", "mae_pct",
    )
    with trade_ledger._connect() as con:
        before = dict(con.execute(
            "SELECT * FROM trades WHERE trade_id=?", (trade_id,)).fetchone())
    before_values = {
        name: before[name] for name in immutable_financial_fields
    }

    changed = reconciliation.confirm_position_closed(
        ADBE_POSITION,
        account_fingerprint=ACCOUNT,
        paper=True,
        decision_id=ADBE_DECISION,
        close_detail={
            "positionId": ADBE_POSITION,
            # eToro-History orderId ist der Entry-Anker, keine Close-orderId.
            "orderId": ADBE_ORDER,
            "instrumentId": 1126,
            "units": 51.0,
            "openRate": 293.92,
            "closeRate": 287.16,
            "openTimestamp": ADBE_ENTRY_TIME,
            "closeTimestamp": ADBE_CLOSE_TIME,
        },
    )
    assert changed

    record = reconciliation.record_for(ADBE_DECISION)
    assert record["state"] == "CLOSED_BEFORE_IMPORT"
    assert record["ledger_close_backfill"][ADBE_POSITION][
        "status"] == "LINKED_CLOSED"
    assert record["closed_position_ids"] == [ADBE_POSITION]

    domain = reconciliation.domain_key(
        paper=True, profile="offensiv", account_fingerprint=ACCOUNT)
    assert reconciliation.active_for_domain(domain) == []

    with trade_ledger._connect() as con:
        after = dict(con.execute(
            "SELECT * FROM trades WHERE trade_id=?", (trade_id,)).fetchone())
        entry_fills = con.execute(
            "SELECT * FROM trade_entry_fills WHERE trade_id=?", (trade_id,)
        ).fetchall()
        exit_events = con.execute(
            "SELECT * FROM trade_exit_events WHERE trade_id=?", (trade_id,)
        ).fetchall()

    assert after["decision_id"] == ADBE_DECISION
    assert after["broker_account_fingerprint"] == ACCOUNT
    assert after["broker_position_id"] == ADBE_POSITION
    assert after["entry_order_id"] == ADBE_ORDER
    assert after["client_order_id"] == ADBE_REFERENCE
    assert after["ownership_status"] == "BOT_VERIFIED"
    assert after["exit_order_id"] == ""
    assert after["exit_grund"] == "STOP_LOSS"
    assert {
        name: after[name] for name in immutable_financial_fields
    } == before_values
    assert len(entry_fills) == 1
    assert len(exit_events) == 1

    # Die gleichzeitig gelesene MSFT-Altposition bleibt kontolos und wird
    # durch den ADBE-Erfolg niemals als Eigentum dieses Accounts markiert.
    legacy = reconciliation.record_for(LEGACY_DECISION)
    assert not legacy.get("account_fingerprint")
    assert legacy.get("position_verified") is not True
