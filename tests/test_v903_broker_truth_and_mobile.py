"""Regressionen fuer Broker-Wahrheit, Reconciliation und mobile WebUI in 9.0.3."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

ACCOUNT = "test-etoro-demo-account-v903"


def _etoro_fill(monkeypatch, tmp_path, decision_id: int, position_id: str):
    monkeypatch.setenv("TRADINGBOT_TEST_STATE_DIR", str(tmp_path))
    import decision_analytics as analytics
    import etoro_reconciliation as reconciliation

    monkeypatch.setattr(analytics, "DB_PATH", tmp_path / "decisions.sqlite")
    monkeypatch.setattr(reconciliation, "_notify", lambda *_args, **_kwargs: None)
    analytics.record({
        "decision_id": decision_id, "symbol": "MSFT", "asset_type": "stock",
        "broker": "etoro", "paper": True, "status": "APPROVED",
        "profile": "balanced", "price": 499.2, "qty": 29,
        "stop": 490.0, "take": 520.0,
    })
    reconciliation.start_intent(
        decision_id=decision_id, symbol="MSFT", paper=True, profile="balanced",
        quantity=29, price=499.2, stop=490.0, take_profit=520.0,
        account_fingerprint=ACCOUNT)
    reconciliation.accepted(decision_id, order_id="order-msft", reference_id="ref-msft")
    reconciliation.apply_broker_evidence(decision_id, {
        "orderId": "order-msft", "referenceId": "ref-msft",
        "status": {"id": 3, "name": "Filled"},
        "positionExecutions": [{
            "positionId": position_id,
            "openingData": {"units": 29, "avgPrice": 499.2,
                            "executionTime": "2026-08-27T19:33:00Z"},
        }],
    })
    return reconciliation


class _EtoroTruth:
    def __init__(self, current=(), history=()):
        self.current = set(current)
        self.history = list(history)

    def current_position_ids(self, *, force=True):
        return set(self.current)

    def account_fingerprint(self):
        return ACCOUNT

    def position_snapshot(self, *, force=True):
        return {
            "position_ids": set(self.current), "open_ids": set(self.current),
            "rows": [{"positionId": value} for value in self.current],
            "complete": True, "account_fingerprint": ACCOUNT,
            "environment": "DEMO",
        }

    def trade_history(self, _min_date, **_kwargs):
        return list(self.history)


def test_etoro_lookup_fill_is_not_open_until_current_position_id_proves_it(monkeypatch, tmp_path):
    rec = _etoro_fill(monkeypatch, tmp_path, 90301, "position-msft")
    # Ein FILLED-Orderlookup allein darf nach einem Neustart keinen Phantomtrade erzeugen.
    assert rec.recovered_buy_fills(paper=True, current_position_ids={"position-msft"}) == []
    changed = rec.verify_broker_truth(
        _EtoroTruth(current={"position-msft"}), paper=True, profile="balanced",
        account_fingerprint=ACCOUNT)
    assert changed and changed[0]["broker_position_status"] == "OPEN_CONFIRMED"
    fills = rec.recovered_buy_fills(
        paper=True, current_position_ids={"position-msft"})
    assert len(fills) == 1 and fills[0].broker_id == "position-msft"


def test_etoro_closed_history_never_replays_a_buy(monkeypatch, tmp_path):
    rec = _etoro_fill(monkeypatch, tmp_path, 90302, "position-closed")
    changed = rec.verify_broker_truth(
        _EtoroTruth(history=[{
            "positionId": "position-closed",
            "closeTimestamp": "2026-08-27T20:00:00Z",
        }]), paper=True, profile="balanced", account_fingerprint=ACCOUNT)
    assert changed and changed[0]["state"] == "CLOSED_BEFORE_IMPORT"
    assert rec.recovered_buy_fills(
        paper=True, current_position_ids=set()) == []


def test_okx_fill_history_paginates_and_deduplicates(monkeypatch):
    from broker.okx import OKXClient

    client = OKXClient()
    calls = []
    # GEAENDERT IN v9.1: OKX paginiert /trade/fills[-history] ueber billId,
    # nicht ueber tradeId -- beide Felder stehen getrennt in der Antwort. Mit
    # dem falschen Cursor war ab Seite 2 Schluss, und zwar genau im Beweispfad
    # fuer extern ausgefuehrte Verkaeufe. Die Zeilen tragen deshalb jetzt
    # beide Felder, und der Test prueft den billId-Cursor.
    first = [{"tradeId": str(i), "billId": f"b{i}", "ordId": f"o{i}",
              "ts": str(i), "fillSz": "1"} for i in range(200, 100, -1)]
    second = [first[-1], {"tradeId": "100", "billId": "b100", "ordId": "o100",
                          "ts": "100", "fillSz": "1"}]

    def page(_inst_id="", *, limit=100, after="", before="", ord_id=""):
        calls.append((limit, after, before))
        return first if not after else second

    monkeypatch.setattr(client, "fills_history", page)
    rows = client.fills_history_paginated(max_pages=5)
    assert len(rows) == 101
    assert calls == [(100, "", ""), (100, "b101", "")], (
        "der Folgecursor muss die billId der letzten Zeile sein")
    assert len({row["tradeId"] for row in rows}) == len(rows)


def test_trade_page_separates_confirmed_open_from_reconciliation(monkeypatch, tmp_path):
    import decision_analytics
    import trade_ledger
    from webui.state import trade_analysis

    monkeypatch.setattr(decision_analytics, "DB_PATH", tmp_path / "trades.sqlite")
    now = datetime.now(timezone.utc)
    confirmed = trade_ledger.trade_open(
        broker="okx", symbol="ETH", menge=1, einstieg_preis=2000,
        decision_id=90310, asset_type="crypto", zeit=now,
        entry_fill_id="fill-confirmed", reconciliation_status="CONFIRMED_OPEN")
    pending = trade_ledger.trade_open(
        broker="okx", symbol="BTC", menge=.1, einstieg_preis=65000,
        decision_id=90311, asset_type="crypto", zeit=now,
        entry_fill_id="fill-pending", reconciliation_status="PENDING_CONFIRMATION")
    assert confirmed and pending
    data = trade_analysis(broker="okx", tage=30)
    assert data["kennzahlen"]["offen"] == 1
    assert data["kennzahlen"]["klaerung"] == 1
    assert data["offene_trades"][0]["symbol"] == "ETH"
    assert data["klaerungs_trades"][0]["symbol"] == "BTC"


def test_responsive_navigation_is_available_on_phone_tablet_and_desktop():
    root = Path(__file__).resolve().parents[1]
    js = (root / "webui/static/common.js").read_text(encoding="utf-8")
    css = (root / "webui/static/app.css").read_text(encoding="utf-8")
    for label in ("Hauptmenü", "Seite auswählen", "mobile-navigation"):
        assert label in js
    assert "@media(max-width:1200px)" in css
    assert ".desktop-navigation{display:none}" in css
    assert "min-height:44px" in css
    assert "@media(max-width:600px)" in css
