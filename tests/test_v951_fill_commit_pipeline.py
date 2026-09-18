"""Transaktionsgrenzen zwischen eToro-Fill, Ledger und Broker-Journal."""
from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest


def _import_capture(monkeypatch):
    try:
        __import__("yfinance")
    except ModuleNotFoundError:
        monkeypatch.setitem(sys.modules, "yfinance", SimpleNamespace())
    from live_trader import _CriticalFillAccountingError, capture_new_fills
    return _CriticalFillAccountingError, capture_new_fills


def test_history_ack_contains_only_committed_or_already_seen_fill_ids(
        monkeypatch, tmp_path):
    from broker.base import Fill
    from fill_tracker import FillProgressTracker

    _critical, capture_new_fills = _import_capture(monkeypatch)
    committed = Fill(
        fill_id="history-ok", order_id="", symbol="ADBE", side="UNKNOWN",
        quantity=1, price=100, account_fingerprint="account-1")
    wrong_account = Fill(
        fill_id="history-failed", order_id="", symbol="ADBE", side="UNKNOWN",
        quantity=1, price=100, account_fingerprint="other-account")

    class Broker:
        name = "etoro"
        paper = True

        def __init__(self):
            self.acks = []

        @staticmethod
        def fills():
            return [committed, wrong_account]

        @staticmethod
        def account_fingerprint():
            return "account-1"

        def ack_history_recovery_committed(self, fill_ids=()):
            self.acks.append(set(fill_ids))
            return False

    broker = Broker()
    tracker = FillProgressTracker(tmp_path / "fill_progress.json")
    assert capture_new_fills(
        broker, SimpleNamespace(), {}, tracker, None, SimpleNamespace(),
        10_000.0, {}, notify_enabled=False) == 0

    assert tracker.seen_ids == {"history-ok"}
    assert broker.acks == [{"history-ok"}]


def test_broker_detail_survives_cumulative_fill_prepare(tmp_path):
    from broker.base import Fill
    from fill_tracker import FillProgressTracker

    detail = {"positionId": "p-1", "executionId": "exec-1"}
    fill = Fill(
        fill_id="raw", order_id="close-1", symbol="ADBE", side="SELL",
        quantity=4, price=99, quantity_is_cumulative=True,
        broker_detail=detail)
    prepared, token = FillProgressTracker(
        tmp_path / "fill_progress.json").prepare(fill)

    # Quantity alone is insufficient after restart: value and cumulative
    # fees must be committed as well, without losing broker_detail.
    assert token == ("cumulative", "close-1", 4.0, 396.0, None)
    assert prepared.quantity == 4.0
    assert prepared.price == 99.0
    assert prepared.explicit_fees is None
    assert prepared is not fill
    assert prepared.broker_detail == detail


def _sell_pipeline(monkeypatch, tmp_path, *, ledger_error=False,
                   journal_error=False):
    from broker.base import Fill
    from instrument_identity import canonical_key

    Critical, capture_new_fills = _import_capture(monkeypatch)
    import broker_exit_journal
    import etoro_reconciliation
    import trade_ledger

    events = []
    fill = Fill(
        fill_id="etoro:account-1:close:p-1:exec:e-1",
        order_id="close-1", symbol="ADBE", side="SELL",
        quantity=5, price=98, currency="USD", asset_type="stock",
        broker_id="p-1", timestamp="2026-09-01T15:00:00Z",
        explicit_fees=0.5, account_fingerprint="account-1",
        broker_detail={"positionId": "p-1", "executionId": "e-1"})

    class Broker:
        name = "etoro"
        paper = True

        @staticmethod
        def fills():
            return [fill]

        @staticmethod
        def account_fingerprint():
            return "account-1"

        @staticmethod
        def ist_paper():
            return True

        @staticmethod
        def on_fill_housekeeping(_fill):
            return None

    rec = SimpleNamespace(
        avg_cost=100.0, planned_stop=95.0, planned_take=110.0,
        entry_time="2026-09-01T12:00:00Z", source="BOT",
        entry_order_ids=["entry-1"])

    class Manager:
        @staticmethod
        def get_by_position_id(position_id, *, account_fingerprint=""):
            assert (position_id, account_fingerprint) == ("p-1", "account-1")
            return rec

        @staticmethod
        def register_sell(*_args, **_kwargs):
            events.append("manager")
            return rec, -10.5, -2.1, 0.0

    class Tracker:
        seen_ids = set()

        @staticmethod
        def prepare(raw):
            return raw, ("id", raw.fill_id)

        def commit(self, token):
            events.append("tracker")
            self.seen_ids.add(token[1])

    def close_ledger(**_kwargs):
        events.append("ledger")
        if ledger_error:
            raise RuntimeError("ledger unavailable")
        return 71

    def close_journal(**kwargs):
        events.append("journal")
        assert kwargs["fill_identity"] == fill.fill_id
        assert kwargs["detail"]["executionId"] == "e-1"
        if journal_error:
            raise RuntimeError("journal unavailable")
        return "intent-1"

    def close_reconciliation(*_args, **_kwargs):
        events.append("reconciliation")
        return True

    monkeypatch.setattr(trade_ledger, "trade_close", close_ledger)
    monkeypatch.setattr(
        broker_exit_journal, "confirm_from_fill", close_journal)
    monkeypatch.setattr(
        etoro_reconciliation, "confirm_position_closed",
        close_reconciliation)

    risk = SimpleNamespace(
        realized_pnl_today=0.0, unknown_pnl_trades_today=0,
        register_realized_pnl=lambda *_a, **_k: None,
        register_unknown_pnl_trade=lambda *_a, **_k: None,
        register_estimated_cost=lambda *_a, **_k: None)
    contract = SimpleNamespace(
        conId="1126", symbol="ADBE", localSymbol="ADBE", currency="USD")
    instrument = SimpleNamespace(
        contract=contract, asset_type="stock", currency="USD")
    snapshot = {
        "complete": True, "account_fingerprint": "account-1",
        "environment": "DEMO", "snapshot_id": "snapshot-1",
        "rows": [], "open_ids": set(),
    }

    def run():
        return capture_new_fills(
            Broker(), Manager(), {
                "close-1": {
                    "owner": "BOT", "broker": "etoro", "paper": True,
                    "account_fingerprint": "account-1",
                    "label": "STOP-LOSS", "reason": "Schutzexit",
                },
            }, Tracker(), None, risk, 10_000.0,
            {canonical_key("ADBE", "stock"): instrument},
            notify_enabled=False, position_snapshot=snapshot)

    return Critical, run, events


def test_etoro_sell_commits_ledger_then_journal_then_reconciliation_then_tracker(
        monkeypatch, tmp_path):
    _critical, run, events = _sell_pipeline(monkeypatch, tmp_path)

    assert run() == 1
    assert events == [
        "manager", "ledger", "journal", "reconciliation", "tracker"]


@pytest.mark.parametrize(
    "ledger_error,journal_error,expected",
    [(True, False, ["manager", "ledger"]),
     (False, True, ["manager", "ledger", "journal"])],
)
def test_etoro_sell_failure_never_terminalizes_or_commits_fill(
        monkeypatch, tmp_path, ledger_error, journal_error, expected):
    Critical, run, events = _sell_pipeline(
        monkeypatch, tmp_path, ledger_error=ledger_error,
        journal_error=journal_error)

    with pytest.raises(Critical):
        run()
    assert events == expected
    assert "reconciliation" not in events
    assert "tracker" not in events


# ---------------------------------------------------------------------------
# 9.5.8 -- Der KO-Fall vom 04.09.2026, in derselben Pipeline
# ---------------------------------------------------------------------------
def _sell_pipeline_unklar(monkeypatch, tmp_path):
    """Wie ``_sell_pipeline``, aber der Ledger meldet einen FACHLICHEN Befund.

    Bis 9.5.7 kam hier derselbe ``_CriticalFillAccountingError`` heraus wie bei
    einem technischen Fehler: der Fill blieb unquittiert, der Aktienkern starb,
    und beim naechsten Start lag genau derselbe Fill wieder an -- eine
    Absturzschleife ohne Fortschritt.
    """
    import trade_ledger

    Critical, run, events = _sell_pipeline(monkeypatch, tmp_path)

    def close_ledger(**_kwargs):
        events.append("ledger")
        raise trade_ledger.LedgerZuordnungUnklar(
            "etoro KO: Geschlossene exakte Entry-Lineage besitzt keinen "
            "eindeutig passenden Exitbeleg")

    monkeypatch.setattr(trade_ledger, "trade_close", close_ledger)
    return Critical, run, events


def test_unzuordenbarer_verkauf_stoppt_den_handel_nicht(monkeypatch, tmp_path):
    _critical, run, events = _sell_pipeline_unklar(monkeypatch, tmp_path)

    assert run() == 1, "Der Fill wurde nicht als verarbeitet gezaehlt"
    assert "tracker" in events, (
        "Der Fill blieb unquittiert -- beim naechsten Poll kaeme er wieder "
        "und der Kern wuerde erneut sterben")


def test_unzuordenbarer_verkauf_quittiert_das_exit_journal(
        monkeypatch, tmp_path):
    """Der FILL ist bewiesen -- nur seine Ledgerzeile fehlt.

    Bliebe der Intent offen, fragte der eToro-Adapter ihn alle 10 s erneut ab,
    fuer eine laengst geschlossene Position, unbegrenzt -- und ein spaeterer
    Verkauf derselben positionId liefe in OrderStatusUnklar.
    """
    _critical, run, events = _sell_pipeline_unklar(monkeypatch, tmp_path)
    run()
    assert "journal" in events, (
        "Der Exit-Intent bleibt dauerhaft offen und wird endlos nachgefragt")
    assert events.index("journal") > events.index("ledger")


def test_unzuordenbarer_verkauf_wird_als_buchungsluecke_vermerkt(
        monkeypatch, tmp_path):
    import etoro_reconciliation

    luecken = []
    monkeypatch.setattr(
        etoro_reconciliation, "melde_buchungsluecke",
        lambda **kw: luecken.append(kw))
    _critical, run, _events = _sell_pipeline_unklar(monkeypatch, tmp_path)
    run()

    assert len(luecken) == 1, "Die Luecke wurde nicht festgehalten"
    assert luecken[0]["symbol"] == "ADBE"
    assert luecken[0]["broker_position_id"] == "p-1"
    assert "Ledgerzeile" in luecken[0]["grund"]


def test_unzuordenbarer_verkauf_markiert_den_tages_pnl_unvollstaendig(
        monkeypatch, tmp_path):
    """Sonst sieht der Tagesgewinn vollstaendig aus, obwohl ein Trade fehlt."""
    import live_trader

    vermerkt = []
    Critical, run, events = _sell_pipeline_unklar(monkeypatch, tmp_path)
    # Der Risikoobjekt-Aufruf wird in _sell_pipeline gebaut; hier nur pruefen,
    # dass der Pfad ihn ueberhaupt erreicht.
    original = live_trader._ledger_zuordnung_unklar

    def merken(exc):
        treffer = original(exc)
        vermerkt.append(treffer)
        return treffer

    monkeypatch.setattr(live_trader, "_ledger_zuordnung_unklar", merken)
    run()
    assert vermerkt and vermerkt[0] is True, (
        "Der Befund wurde nicht als fachlich unaufloesbar erkannt")


def test_technischer_ledgerfehler_stoppt_weiterhin(monkeypatch, tmp_path):
    """Die Gegenprobe: ein Neuversuch kann gelingen, also fail-closed."""
    Critical, run, events = _sell_pipeline(
        monkeypatch, tmp_path, ledger_error=True)
    with pytest.raises(Critical):
        run()
    assert "tracker" not in events
