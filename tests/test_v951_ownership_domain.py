from __future__ import annotations

import json
import time


def test_etoro_fill_ownership_is_bound_to_account_environment_and_broker(tmp_path):
    from order_ownership import OrderOwnershipRegistry, classify_fill_owner

    registry = OrderOwnershipRegistry(tmp_path / "registry.json")
    meta = {
        "decision_id": 7,
        "broker": "etoro",
        "paper": True,
        "environment": "DEMO",
        "account_fingerprint": "account-a",
    }
    registry.register_orders(["order-7"], "ADBE", meta, owner="BOT")

    assert classify_fill_owner(
        registry.metadata("order-7"), registry, "order-7",
        account_fingerprint="account-a", paper=True,
        broker_name="etoro") == "BOT"
    assert classify_fill_owner(
        registry.metadata("order-7"), registry, "order-7",
        account_fingerprint="account-b", paper=True,
        broker_name="etoro") == "AMBIGUOUS"
    assert classify_fill_owner(
        registry.metadata("order-7"), registry, "order-7",
        account_fingerprint="account-a", paper=False,
        broker_name="etoro") == "AMBIGUOUS"


def test_legacy_bot_meta_without_domain_fails_closed_when_context_is_known(tmp_path):
    from order_ownership import OrderOwnershipRegistry, classify_fill_owner

    registry = OrderOwnershipRegistry(tmp_path / "registry.json")
    registry.register_orders(
        ["old-order"], "ADBE", {"decision_id": 8}, owner="BOT")
    assert classify_fill_owner(
        registry.metadata("old-order"), registry, "old-order",
        account_fingerprint="account-a", paper=True,
        broker_name="etoro") == "AMBIGUOUS"


def test_order_and_reference_aliases_change_state_atomically(tmp_path):
    from order_ownership import OrderOwnershipRegistry

    registry = OrderOwnershipRegistry(tmp_path / "registry.json")
    registry.register_orders(
        ["378375675", "reference-adbe"], "ADBE",
        {"decision_id": 4153277691980212224,
         "reference_id": "reference-adbe", "zustand": "AWAITING_POSITION_CONFIRMATION"},
        owner="BOT")
    assert registry.update_order("378375675", zustand="CLOSED_CONFIRMED")
    assert registry.metadata("378375675")["zustand"] == "CLOSED_CONFIRMED"
    assert registry.metadata("reference-adbe")["zustand"] == "CLOSED_CONFIRMED"


def test_filled_order_anchor_is_not_deleted_only_because_it_is_old(tmp_path):
    from order_ownership import OrderOwnershipRegistry

    path = tmp_path / "registry.json"
    registry = OrderOwnershipRegistry(path)
    registry.register_orders(
        ["long-held-order"], "MSFT",
        {"decision_id": 9, "zustand": "FILLED"}, owner="BOT")
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["orders"]["long-held-order"]["registered_at"] = time.time() - 500 * 86400
    path.write_text(json.dumps(raw), encoding="utf-8")

    registry.cleanup()
    assert registry.metadata("long-held-order")["zustand"] == "FILLED"

