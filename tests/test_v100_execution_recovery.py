"""N03/N05/N06: deterministic economic receipts, replay and crash boundaries."""
import json
from pathlib import Path
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import pytest

from broker.base import Fill, OrderErgebnis, OrderStatusUnklar
import execution_lifecycle as life
from fill_tracker import FillProgressTracker


def reserve(**changes):
    args = dict(broker="okx", account="account-a", environment="DEMO",
        instrument="SUI-USDC", side="SELL", client_id="client-1",
        quantity=10, request={}, position_id="SUI-USDC")
    args.update(changes)
    return life.reserve(**args)


def observation(qty=10, *, terminal=True, complete=True, fills=None, **changes):
    args = dict(order_ids=["order-1"], client_order_id="client-1",
        account_fingerprint="account-a", broker_environment="DEMO",
        status="filled", terminal=terminal, filled_quantity=qty,
        gross_filled_quantity=qty, requested_quantity=10,
        remaining_quantity=max(0, 10-qty), fill_evidence_complete=complete,
        fills=fills if fills is not None else ([native_fill("fill-1", qty)] if qty else []))
    args.update(changes)
    return OrderErgebnis(**args)


def native_fill(identity, quantity, **changes):
    args = dict(tradeId=identity, ordId="order-1", instId="SUI-USDC",
        side="sell", fillSz=str(quantity), fillPx="10", fee="-.01", feeCcy="USDC")
    args.update(changes)
    return args


def row():
    return life.snapshot()[0]


def test_terminal_native_evidence_survives_weaker_same_quantity_snapshot():
    reserve()
    good = observation(execution_evidence={"source": "native-fills", "receipt": "verified"})
    life.observe(good, broker="okx")
    life.observe(observation(complete=False, fills=[], execution_evidence={"source": "brief-rest"}), broker="okx")
    assert row()["evidence_complete"] == 1
    assert json.loads(row()["evidence_json"])["receipt"] == "verified"
    with life._connect() as con:
        assert con.execute("SELECT COUNT(*) FROM execution_fills").fetchone()[0] == 1
        detail = json.loads(con.execute("SELECT detail_json FROM execution_events ORDER BY id DESC LIMIT 1").fetchone()[0])
    assert detail["preserved_stronger_evidence"] is True
    assert detail["observed_evidence_complete"] is False


def test_incomplete_fee_snapshot_cannot_overwrite_proven_net_quantity():
    reserve(side="BUY")
    good = observation(filled_quantity=9.9, fills=[native_fill("fill-1", 10, side="buy", fee="-.1", feeCcy="SUI")])
    life.observe(good, broker="okx")
    weak = observation(complete=False, fills=[])
    life.observe(weak, broker="okx")
    assert row()["net_filled"] == "9.9"
    assert row()["evidence_complete"] == 1


def test_numeric_formatting_change_does_not_erase_accounting_receipt():
    reserve()
    life.observe(observation(10.0), broker="okx")
    with life._connect() as con:
        con.execute("UPDATE execution_orders SET accounted=1")
    life.observe(observation(10, complete=False, fills=[]), broker="okx")
    assert row()["accounted"] == 1 and row()["evidence_complete"] == 1


def test_complete_late_base_fee_enriches_evidence_and_invalidates_old_accounting():
    reserve(side="BUY")
    first = observation(fills=[native_fill("fill-1", 10, side="buy", fee=None, feeCcy="")])
    life.observe(first, broker="okx")
    with life._connect() as con:
        con.execute("UPDATE execution_orders SET accounted=1")
    corrected = observation(filled_quantity=9.9,
        fills=[native_fill("fill-1", 10, side="buy", fee="-.1", feeCcy="SUI")])
    life.observe(corrected, broker="okx")
    assert row()["net_filled"] == "9.9" and row()["accounted"] == 0
    with life._connect() as con:
        assert con.execute("SELECT COUNT(*) FROM execution_fills").fetchone()[0] == 1
        assert con.execute("SELECT fee FROM execution_fills").fetchone()[0] == "-0.1"


def test_late_ack_does_not_turn_partial_fill_back_into_open():
    key = reserve()
    life.observe(observation(4, terminal=False), broker="okx")
    life.accepted(key, "order-1")
    assert row()["state"] == "PARTIALLY_FILLED"
    assert row()["filled"] == "4"


@pytest.mark.parametrize("late_quantity,expected", [(4, "PARTIALLY_FILLED_CANCELED"), (10, "FILLED")])
def test_cancel_then_delayed_native_fill_keeps_terminal_fact(late_quantity, expected):
    reserve()
    life.observe(observation(0, status="canceled"), broker="okx")
    life.observe(observation(late_quantity, terminal=False, complete=False), broker="okx")
    assert row()["terminal"] == 1 and row()["state"] == expected
    assert float(row()["filled"]) == late_quantity
    assert row()["accounted"] == 0
    with pytest.raises(OrderStatusUnklar):
        reserve(client_id="second-post")


def test_late_fill_after_proven_rejection_is_conflict_not_implicit_reopening():
    reserve()
    life.observe(observation(0, status="rejected"), broker="okx")
    with pytest.raises(life.ExecutionConflict):
        life.observe(observation(4), broker="okx")
    assert row()["state"] == "REJECTED"
    with life._connect() as con:
        assert con.execute("SELECT COUNT(*) FROM execution_fills").fetchone()[0] == 0


@pytest.mark.parametrize("sequence", [(0, 1, 2, 2, 0), (2, 0, 1, 2), (1, 0, 2, 1)])
def test_out_of_order_and_duplicate_replay_converges_to_native_fill_total(sequence):
    reserve()
    fills = [native_fill("f1", 2), native_fill("f2", 3), native_fill("f3", 5)]
    observations = [observation(sum([2, 3, 5][:i+1]), terminal=i == 2,
                               fills=fills[:i+1]) for i in range(3)]
    for index in sequence:
        life.observe(observations[index], broker="okx")
    assert row()["state"] == "FILLED"
    assert row()["filled"] == "10" and row()["evidence_complete"] == 1
    with life._connect() as con:
        assert con.execute("SELECT COUNT(*) FROM execution_fills").fetchone()[0] == 3


def test_conflicting_duplicate_preserves_all_previous_economic_facts():
    reserve(); life.observe(observation(), broker="okx")
    before = row()
    with pytest.raises(life.ExecutionConflict):
        life.observe(observation(fills=[native_fill("fill-1", 10, fillPx="20")]), broker="okx")
    assert row() == before


def test_incomplete_larger_quantity_does_not_inherit_prior_completeness():
    reserve()
    life.observe(observation(4), broker="okx")
    life.observe(observation(10, complete=False,
        fills=[native_fill("fill-1", 4), native_fill("fill-2", 6, fee=None)]), broker="okx")
    assert row()["filled"] == "10" and row()["evidence_complete"] == 0


def test_two_stale_trackers_keep_both_native_execution_ids(tmp_path):
    path = tmp_path / "fills.json"
    first, second = FillProgressTracker(path), FillProgressTracker(path)
    first.commit(("id", "A")); second.commit(("id", "B"))
    assert FillProgressTracker(path).seen_ids == {"A", "B"}
    assert first.prepare(Fill("B", "O", "SUI", "BUY", 1, 10)) == (None, None)


@pytest.mark.parametrize("changed", [
    {"seen_ids": "native-fill"}, {"seen_ids": {"native-fill": True}},
    {"seen_ids": [42]}, {"initialized": "false"},
    {"cumulative": []}, {"cumulative": {"order": True}},
    {"schema_version": 100}, {"schema_version": True},
])
def test_semantically_corrupt_tracker_blocks_and_preserves_original(tmp_path, changed):
    path = tmp_path / "fills.json"
    data = {"schema_version": 2, "initialized": True,
            "seen_ids": ["native-fill"], "cumulative": {}}
    data.update(changed)
    original = json.dumps(data)
    path.write_text(original)
    tracker = FillProgressTracker(path)
    assert tracker.storage_error
    with pytest.raises(RuntimeError):
        tracker.prepare(Fill("native-fill", "O", "SUI", "BUY", 1, 10))
    with pytest.raises(RuntimeError):
        tracker.commit(("id", "new-fill"))
    assert path.read_text() == original


def test_legacy_tracker_missing_new_metadata_preserves_native_ids(tmp_path):
    path = tmp_path / "fills.json"
    path.write_text(json.dumps({"seen_ids": ["legacy-fill"], "cumulative": {"O": 4.0}}))
    tracker = FillProgressTracker(path)
    assert not tracker.storage_error and tracker.initialized
    assert tracker.prepare(Fill("legacy-fill", "O", "SUI", "BUY", 1, 10)) == (None, None)


def test_parallel_tracker_writers_do_not_lose_checkpoints(tmp_path):
    path = tmp_path / "fills.json"
    trackers = [FillProgressTracker(path) for _ in range(12)]
    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(lambda item: item[1].commit(("id", str(item[0]))), enumerate(trackers)))
    assert FillProgressTracker(path).seen_ids == {str(i) for i in range(12)}


def test_unchanged_history_poll_does_not_reparse_checkpoint_per_fill(tmp_path, monkeypatch):
    tracker = FillProgressTracker(tmp_path / "fills.json")
    tracker.commit(("id", "processed"))
    original = Path.open
    reads = []
    def counted(path, *args, **kwargs):
        if path == tracker.path: reads.append(path)
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", counted)
    for _ in range(100):
        assert tracker.prepare(Fill("processed", "O", "SUI", "BUY", 1, 10)) == (None, None)
    assert reads == []


def test_tracker_merge_is_process_safe(tmp_path):
    path = tmp_path / "fills.json"
    script = """import sys
from fill_tracker import FillProgressTracker
t=FillProgressTracker(sys.argv[1])
for i in range(8): t.commit(('id',sys.argv[2]+str(i)))
"""
    children = [subprocess.Popen([sys.executable, "-c", script, str(path), prefix]) for prefix in ("A", "B")]
    assert [p.wait(timeout=15) for p in children] == [0, 0]
    assert FillProgressTracker(path).seen_ids == {p+str(i) for p in ("A", "B") for i in range(8)}


def test_older_cumulative_commit_cannot_replace_newer_value_or_fee(tmp_path):
    path = tmp_path / "fills.json"
    first, second = FillProgressTracker(path), FillProgressTracker(path)
    first.commit(("cumulative", "O", 10, 1060, 3))
    second.commit(("cumulative", "O", 4, 400, 1))
    loaded = FillProgressTracker(path)
    assert loaded.cumulative == {"O": 10, "O:value": 1060, "O:fees": 3}
    prepared, _ = loaded.prepare(Fill("raw", "O", "SUI", "BUY", 12, 110,
        quantity_is_cumulative=True, explicit_fees=4))
    assert prepared.quantity == 2 and prepared.price == 130 and prepared.explicit_fees == 1


def test_same_quantity_with_conflicting_total_is_not_silently_merged(tmp_path):
    tracker = FillProgressTracker(tmp_path / "fills.json")
    tracker.commit(("cumulative", "O", 10, 1000, 1))
    with pytest.raises(RuntimeError, match="Widerspruechlicher"):
        tracker.commit(("cumulative", "O", 10, 1100, 1))
    assert FillProgressTracker(tracker.path).cumulative["O:value"] == 1000


def test_late_seed_cannot_mark_offline_fill_seen(tmp_path):
    path = tmp_path / "fills.json"
    first, old = FillProgressTracker(path), FillProgressTracker(path)
    first.seed([Fill("baseline", "O", "SUI", "BUY", 1, 10)])
    assert old.seed([Fill("offline", "O", "SUI", "BUY", 1, 10)]) is False
    assert old.prepare(Fill("offline", "O", "SUI", "BUY", 1, 10))[0] is not None


def test_tracker_does_not_prune_old_native_ids_by_sort_order(tmp_path):
    path = tmp_path / "fills.json"
    identities = {str(i) for i in range(5010)}
    path.write_text(json.dumps({"initialized": True, "seen_ids": list(identities), "cumulative": {}}))
    FillProgressTracker(path).commit(("id", "new"))
    assert FillProgressTracker(path).seen_ids == identities | {"new"}


def test_failed_durable_checkpoint_can_be_retried_without_false_ack(tmp_path, monkeypatch):
    import fill_tracker
    tracker = FillProgressTracker(tmp_path / "fills.json")
    original = fill_tracker.atomic_write_json
    def failed(*args, **kwargs):
        raise OSError(5, "injected fsync EIO")
    monkeypatch.setattr(fill_tracker, "atomic_write_json", failed)
    with pytest.raises(OSError): tracker.commit(("id", "execution"))
    assert not tracker.initialized and "execution" not in tracker.seen_ids
    monkeypatch.setattr(fill_tracker, "atomic_write_json", original)
    tracker.commit(("id", "execution"))
    assert FillProgressTracker(tracker.path).seen_ids == {"execution"}


def setup_ledger_exit():
    import trade_ledger as ledger
    trade_id = ledger.trade_open(broker="okx", symbol="SUI", menge=10,
        einstieg_preis=9, gebuehr=0, decision_id=1,
        broker_account_fingerprint="account-a", broker_position_id="SUI-USDC",
        entry_order_id="entry-1", critical=True)
    reserve(); life.observe(observation(), broker="okx")
    args = dict(broker="okx", symbol="SUI", menge=10, ausstieg_preis=10,
        gebuehr=.01, trade_id=trade_id, broker_account_fingerprint="account-a",
        broker_position_id="SUI-USDC", entry_order_id="entry-1",
        exit_order_id="order-1", exit_fill_ids=["fill-1"], critical=True)
    return ledger, trade_id, args


def test_lifecycle_projection_failure_rolls_back_ledger_exit_and_receipt(monkeypatch):
    ledger, trade_id, args = setup_ledger_exit()
    original = life.confirm_accounted_on
    def crash(con, **kwargs):
        original(con, **kwargs)
        assert con.execute("SELECT accounted FROM execution_orders").fetchone()[0] == 1
        assert con.execute("PRAGMA synchronous").fetchone()[0] == 2
        raise RuntimeError("injected before shared COMMIT")
    monkeypatch.setattr(life, "confirm_accounted_on", crash)
    with pytest.raises(RuntimeError): ledger.trade_close(**args)
    assert ledger.trade_detail(trade_id)["ausgestiegen_am"] is None
    assert row()["accounted"] == 0
    with life._connect() as con:
        assert con.execute("SELECT COUNT(*) FROM trade_exit_events").fetchone()[0] == 0
    monkeypatch.setattr(life, "confirm_accounted_on", original)
    assert ledger.trade_close(**args) == trade_id
    assert row()["accounted"] == 1


def test_process_death_before_shared_commit_leaves_no_half_accounted_exit():
    ledger, trade_id, args = setup_ledger_exit()
    script = """import json, os, sys
import trade_ledger as ledger
import execution_lifecycle as life
original=life.confirm_accounted_on
def crash(con, **kwargs):
    original(con, **kwargs)
    os._exit(73)
life.confirm_accounted_on=crash
ledger.trade_close(**json.loads(sys.argv[1]))
"""
    child = subprocess.run([sys.executable, "-c", script, json.dumps(args)], timeout=15)
    assert child.returncode == 73
    assert ledger.trade_detail(trade_id)["ausgestiegen_am"] is None
    assert row()["accounted"] == 0
    assert ledger.trade_close(**args) == trade_id
    assert row()["accounted"] == 1
    assert ledger.trade_close(**args) == trade_id
    with life._connect() as con:
        assert con.execute("SELECT COUNT(*) FROM trade_exit_events").fetchone()[0] == 1
        assert con.execute("SELECT SUM(netto_pnl) FROM trades WHERE ausgestiegen_am IS NOT NULL").fetchone()[0] == pytest.approx(9.99)


def test_open_projection_failure_rolls_back_new_trade(monkeypatch):
    import trade_ledger as ledger
    reserve(side="BUY")
    life.observe(observation(fills=[native_fill("fill-1", 10, side="buy")]), broker="okx")
    args = dict(broker="okx", symbol="SUI", menge=10, einstieg_preis=10,
        gebuehr=.01, decision_id=1, broker_account_fingerprint="account-a",
        broker_position_id="SUI-USDC", entry_order_id="order-1",
        client_order_id="client-1", critical=True)
    original = life.confirm_accounted_on
    def crash(con, **kwargs):
        original(con, **kwargs)
        raise RuntimeError("injected before entry commit")
    monkeypatch.setattr(life, "confirm_accounted_on", crash)
    with pytest.raises(RuntimeError): ledger.trade_open(**args)
    assert ledger.offene_trades() == [] and row()["accounted"] == 0
    monkeypatch.setattr(life, "confirm_accounted_on", original)
    assert ledger.trade_open(**args)
    assert row()["accounted"] == 1


def test_atomic_projection_does_not_ack_same_order_id_in_other_environment():
    ledger, trade_id, args = setup_ledger_exit()
    reserve(environment="LIVE")
    life.observe(observation(broker_environment="LIVE"), broker="okx")
    ledger.trade_close(**args)
    by_env = {r["environment"]: r for r in life.snapshot()}
    assert by_env["DEMO"]["accounted"] == 1
    assert by_env["LIVE"]["accounted"] == 0


def test_missing_broker_order_id_uses_exact_entry_client_not_symbol_quantity():
    import trade_ledger as ledger
    reserve(side="BUY")
    life.observe(observation(order_ids=[],
        fills=[native_fill("fill-1", 10, side="buy", ordId="")]), broker="okx")
    args = dict(broker="okx", symbol="SUI", menge=10, einstieg_preis=10,
        gebuehr=0, decision_id=1, broker_account_fingerprint="account-a",
        broker_position_id="SUI-USDC", critical=True)
    ledger.trade_open(**args, client_order_id="different-entry")
    life.confirm_accounted(broker="okx", account="account-a", environment="DEMO",
        order_id="", client_id="client-1")
    assert row()["accounted"] == 0
    ledger.trade_open(**args, client_order_id="client-1")
    assert row()["accounted"] == 1


def test_legacy_ledger_without_execution_table_remains_supported():
    import trade_ledger as ledger
    trade_id = ledger.trade_open(broker="etoro", symbol="AAA", menge=1,
        einstieg_preis=20, gebuehr=0, decision_id=1, broker_position_id="position-old",
        entry_order_id="entry-old", broker_account_fingerprint="legacy", critical=True)
    assert trade_id
    assert life.snapshot() == []
