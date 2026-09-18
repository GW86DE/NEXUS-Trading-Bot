"""Freigaberegressionen fuer NEXUS 9.0.4.

Die Faelle bilden die konkret gemeldeten Vorfaelle ab: SOL-Legacy-Sperre,
Unified-USD-Handel, eToro-Phantomfill und Telegram-Laufzeitschalter.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest


def _engine_without_positions():
    import crypto_engine
    engine = crypto_engine.CryptoEngine.__new__(crypto_engine.CryptoEngine)
    engine.buch = SimpleNamespace(alle=lambda: [])
    engine._fehlender_ledger_bestand = {}
    engine._melde = lambda *_args, **_kwargs: None
    return engine


def test_sol_legacy_entry_stays_audit_only_without_sale(monkeypatch):
    import exposure_klassifizierung as exposure
    import trade_ledger

    engine = _engine_without_positions()
    row = {
        "trade_id": 11, "symbol": "SOL", "menge": 14.1906,
        "link_status": "LEGACY_UNLINKED", "decision_id": None,
        "entry_order_id": "", "entry_fill_id": "", "broker_position_id": "",
        "broker_account_fingerprint": "", "eingestiegen_am": "2026-08-25T11:53:00Z",
    }
    closed = []
    monkeypatch.setattr(trade_ledger, "offene_trades", lambda _broker="": [dict(row)])
    monkeypatch.setattr(trade_ledger, "set_reconciliation_status", lambda *_a, **_k: None)
    monkeypatch.setattr(trade_ledger, "mark_reconciled_closed",
                        lambda trade_id, **_kwargs: closed.append(trade_id) or True)
    balance = {"SOL": SimpleNamespace(quantity=14.19086594)}

    first = engine._offene_ledger_abgleichen(balance)
    assert first["geschlossen"] == []
    assert first["residual"][0]["status"] == "LEGACY_AUDIT"
    second = engine._offene_ledger_abgleichen(balance)
    assert second["geschlossen"] == [] and closed == []

    # Nach der Ledgerbereinigung bleibt exakt dasselbe Guthaben sichtbar,
    # aber ohne Bot-Eigentumsbeweis und damit ohne Auto-Verkauf/Sperre.
    classified = exposure.klassifiziere(
        {"SOL": {"gesamt": 14.19086594, "cash": 14.19086594}},
        positionsbuch=[], offene_orders=[], ledger_trades=[], preise={"SOL": 94.0})
    sol = next(x for x in classified["bestaende"] if x["waehrung"] == "SOL")
    assert sol["klasse"] == exposure.ACCOUNT_ASSET
    assert not sol["sperrt_einstiege"] and not classified["einstiege_gesperrt"]
    assert "SOL" not in classified["cash_waehrungen"]


def test_linked_sol_trade_is_not_discarded_merely_because_position_book_is_missing(monkeypatch):
    import trade_ledger
    engine = _engine_without_positions()
    row = {
        "trade_id": 12, "symbol": "SOL", "menge": 2.0,
        "link_status": "LINKED", "decision_id": 90412,
        "entry_order_id": "ord-sol", "entry_fill_id": "fill-sol",
        "broker_position_id": "SOL-USD", "broker_account_fingerprint": "account-1",
        "eingestiegen_am": "2026-08-28T08:00:00Z",
    }
    closed = []
    monkeypatch.setattr(trade_ledger, "offene_trades", lambda _broker="": [dict(row)])
    monkeypatch.setattr(trade_ledger, "set_reconciliation_status", lambda *_a, **_k: None)
    monkeypatch.setattr(trade_ledger, "mark_reconciled_closed",
                        lambda trade_id, **_kwargs: closed.append(trade_id) or True)
    balance = {"SOL": SimpleNamespace(quantity=2.0)}
    assert engine._offene_ledger_abgleichen(balance)["residual"][0]["status"] == "RESIDUAL_EXPOSURE"
    assert engine._offene_ledger_abgleichen(balance)["residual"][0]["status"] == "RESIDUAL_EXPOSURE"
    assert closed == []


def test_okx_refuses_new_trade_with_zero_or_inverted_protection():
    from broker.base import BrokerFehler
    from broker.okx import OKXBroker, OKXInstrument
    meta = OKXInstrument("SOL-USD", "SOL", "USD", "live", ".01", ".001", ".01",
                         trade_quote_ccy_list=("USD", "USDC"))
    client = SimpleNamespace(hat_zugangsdaten=True, instrument=lambda _iid: meta)
    broker = OKXBroker(client=client)
    broker._account_fingerprint = "fixture-test_v904_critical_regressions"  # Exakte Testkontobindung
    instrument = SimpleNamespace(name="SOL", asset_type="crypto",
                                 contract=SimpleNamespace(localSymbol="SOL-USD"))
    with pytest.raises(BrokerFehler, match="Stop-Loss"):
        broker.kaufe_mit_absicherung(instrument, 1, 100, 0, 110)
    with pytest.raises(BrokerFehler, match="Stop < Einstieg < Ziel"):
        broker.kaufe_mit_absicherung(instrument, 1, 100, 105, 110)


def test_unified_usd_uses_trade_quote_list_and_available_usdc():
    from broker.okx import OKXBroker, OKXInstrument
    from okx_test_band import DemoWithoutPriceBand
    meta = OKXInstrument("UNI-USD", "UNI", "USD", "live", ".0001", ".01", ".1",
                         trade_quote_ccy_list=("USD", "USDC", "USDG"))
    class Client(DemoWithoutPriceBand):
        hat_zugangsdaten = True
        def __init__(self): self.orders = []; self.algos = []
        def instrument(self, _iid): return meta
        def balances(self): return {"USDC": {"cash": 1000, "gesamt": 1000}}
        def place_order(self, body): self.orders.append(dict(body)); return {"ordId": "o1"}
        def order_status(self, *_a, **_k):
            return {"ordId": "o1", "state": "filled",
                    "accFillSz": "10", "avgPx": "6"}
        def fills(self, *_args, **_kwargs):
            return [{"tradeId": "f1", "ordId": "o1", "instId": "UNI-USD",
                     "fillSz": "10", "fillPx": "6", "fee": "0",
                     "feeCcy": "USDC", "ts": "1"}]
        def fills_history_paginated(self, *_args, **_kwargs): return []
        def place_algo_order(self, body): self.algos.append(dict(body)); return {"algoId": "a1"}
        def pending_orders(self, *_args, **_kwargs): return []
        def pending_algo_orders(self, *_args, **_kwargs): return []
        def orderbook(self, *_args, **_kwargs):
            import time
            return {"timestamp_ms": int(time.time() * 1000),
                    "asks": [(6, 100)], "bids": [(5.99, 100)]}
    client = Client()
    broker = OKXBroker(client=client, quote_ccy="EUR", allowed_quotes=("EUR", "USD", "USDC"))
    broker._account_fingerprint = "fixture-test_v904_critical_regressions"  # Exakte Testkontobindung
    instrument = SimpleNamespace(name="UNI", asset_type="crypto",
                                 contract=SimpleNamespace(localSymbol="UNI-USD"))
    result = broker.kaufe_mit_absicherung(instrument, 10, 6, 5.5, 7)
    assert result.trade_quote_ccy == "USDC"
    assert client.orders[0]["tradeQuoteCcy"] == "USDC"
    broker.reconcile_position_protection(instrument, 10, 5.5, 7,
                                         protection_client_id="PUNI",
                                         trade_quote_ccy="USDC")
    # 9.8.3: OKX now documents tradeQuoteCcy for SPOT algo orders too.
    # Source: https://my.okx.com/docs-v5/en/#order-book-trading-algo-trading-post-place-algo-order
    assert client.algos[0]["tradeQuoteCcy"] == "USDC"


def test_etoro_lookup_fill_never_unlocks_only_because_propagation_timed_out(monkeypatch, tmp_path):
    import decision_analytics as analytics
    import etoro_reconciliation as rec
    monkeypatch.setenv("TRADINGBOT_TEST_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(analytics, "DB_PATH", tmp_path / "decision.sqlite")
    monkeypatch.setattr(rec, "_notify", lambda *_a, **_k: None)
    analytics.record({"decision_id": 90401, "symbol": "MSFT", "broker": "etoro",
                      "asset_type": "stock", "status": "APPROVED", "paper": True})
    account = "test-etoro-demo-account-v904"
    rec.start_intent(decision_id=90401, symbol="MSFT", paper=True, profile="balanced",
                     quantity=29, price=499.2, stop=490, take_profit=520,
                     account_fingerprint=account)
    rec.accepted(90401, order_id="msft-order", reference_id="msft-ref")
    state = rec.apply_broker_evidence(90401, {
        "orderId": "msft-order", "status": {"id": 3, "name": "Filled"},
        "positionExecutions": [{"positionId": "phantom-position",
                                "openingData": {"units": 29, "avgPrice": 499.2}}],
    })
    assert state["state"] == "AWAITING_POSITION_CONFIRMATION"
    broker = SimpleNamespace(
        account_fingerprint=lambda: account,
        position_snapshot=lambda **_k: {
            "position_ids": set(), "open_ids": set(), "rows": [],
            "complete": True, "account_fingerprint": account,
            "environment": "DEMO",
        },
        trade_history_snapshot=lambda *_a, **_k: {
            "rows": [], "complete": True},
        current_position_ids=lambda **_k: set(),
        trade_history=lambda *_a, **_k: [])
    changed = rec.verify_broker_truth(
        broker, paper=True, profile="balanced",
        account_fingerprint=account)
    assert changed[0]["state"] == "AWAITING_POSITION_CONFIRMATION"
    assert rec.active_for_domain(rec.domain_key(
        paper=True, profile="balanced", account_fingerprint=account))
    assert rec.recovered_buy_fills(paper=True, current_position_ids=set()) == []

    # Ein bewiesener Fill darf auch nach abgelaufener Karenz und mehreren
    # Negativ-Snapshots nicht zeitgesteuert freigegeben werden. Der Fall
    # wechselt in die manuelle Klaerung und bleibt fail-closed.
    data = rec._load()
    data["records"]["90401"]["position_confirmation_started_at_utc"] = "2020-01-01T00:00:00+00:00"
    rec._save(data)
    rec.verify_broker_truth(
        broker, paper=True, profile="balanced", account_fingerprint=account)
    final = rec.verify_broker_truth(
        broker, paper=True, profile="balanced", account_fingerprint=account)
    assert final[0]["state"] == "AWAITING_POSITION_CONFIRMATION"
    assert final[0]["position_state"] == "MISSING_REVIEW"
    assert rec.active_for_domain(rec.domain_key(
        paper=True, profile="balanced", account_fingerprint=account))


def test_telegram_callback_is_really_acknowledged(monkeypatch):
    import notifier
    monkeypatch.setattr(notifier, "_credentials", lambda: ("token", "chat"))
    response = SimpleNamespace(ok=True, json=lambda: {"ok": True})
    calls = []
    monkeypatch.setattr(notifier.requests, "post",
                        lambda url, **kwargs: calls.append((url, kwargs)) or response)
    assert notifier.answer_callback_query("callback-1", "Verarbeitet")
    assert calls and calls[0][1]["json"]["callback_query_id"] == "callback-1"


def test_telegram_webui_switch_is_live_without_restart(monkeypatch, tmp_path):
    import live_settings
    import webui.settings_store as store
    monkeypatch.setattr(store, "ROOT", tmp_path)
    monkeypatch.setattr(live_settings, "ROOT", tmp_path)
    store._merge_secrets("telegram_credentials.json", {
        "bot_token": "token", "chat_id": "42", "user_id": "42", "enabled": False,
    }, {"bot_token", "chat_id", "user_id", "enabled"}, {"bot_token"})
    assert not live_settings.telegram_runtime()["enabled"]
    store.set_telegram_runtime(True)
    assert live_settings.telegram_runtime()["enabled"]
    store.set_telegram_runtime(False)
    assert not live_settings.telegram_runtime()["enabled"]


def test_version_is_9012():
    import config
    # v9.1: Die Versionspruefung haengt nicht mehr an einer
    # fest eingetippten Zahl -- sie prueft die Uebereinstimmung
    # zwischen config und VERSION.txt. Genau das soll sie leisten.
    from pathlib import Path as _P
    datei = (_P(__file__).resolve().parent.parent / "VERSION.txt")
    assert config.VERSION_NEXUS == datei.read_text(encoding="utf-8").strip()
