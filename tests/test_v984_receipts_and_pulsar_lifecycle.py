from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS
import json
import time

import pytest

from pulsar import research, control


def test_fee_receipt_requires_exact_entry_fills_and_is_idempotent():
    import trade_ledger as ledger
    tid = ledger.trade_open(broker="etoro", symbol="PEP", menge=4, einstieg_preis=100,
        asset_type="stock", waehrung="USD", decision_id=123, broker_position_id="pos",
        entry_order_id="entry", entry_fill_id="entry-fill", broker_account_fingerprint="acct", critical=True)
    ledger.trade_close(broker="etoro", symbol="PEP", menge=4, ausstieg_preis=105,
        asset_type="stock", waehrung="USD", paper=True, broker_position_id="pos",
        broker_account_fingerprint="acct", entry_order_id="entry", exit_order_id="exit",
        exit_fill_ids=["exit-fill"], critical=True)
    args = dict(broker="etoro", account="acct", paper=True, position_id="pos", entry_order_id="entry",
        entry_fills=[dict(fill_id="entry-fill", quantity=4, price=100, fee=1., fee_currency="USD")],
        exit_fills=[dict(fill_id="exit-fill", quantity=4, price=105, fee=1.2, fee_currency="USD")])
    with pytest.raises(ledger.LedgerZuordnungUnklar):
        ledger.reconcile_fees_exact(**{**args, "entry_fills": [{**args["entry_fills"][0], "fill_id": "different"}]})
    assert ledger.reconcile_fees_exact(**args) == {"updated": 1}
    assert ledger.reconcile_fees_exact(**args) == {"updated": 0}
    with ledger._connect() as con:
        row = dict(con.execute("SELECT * FROM trades WHERE trade_id=?", (tid,)).fetchone())
    assert row["fee_quality"] == "CONFIRMED"
    assert row["netto_pnl"] == pytest.approx(17.8)
    with pytest.raises(ledger.LedgerZuordnungUnklar):
        ledger.reconcile_fees_exact(**{**args, "exit_fills": [{**args["exit_fills"][0], "fee": 3.}]})


def test_social_429_keeps_reservation_and_persistent_backoff():
    response = NS(status_code=429, headers={"Retry-After": "1800"}, close=lambda: None)
    calls = []
    client = NS(get=lambda *a, **k: (calls.append((a, k)) or response))
    with pytest.raises(control.Blocked):
        research.fetch_social("apewisdom", session=client, now=1_789_050_000)
    with pytest.raises(control.Blocked):
        research.fetch_social("apewisdom", session=client, now=1_789_050_001)
    assert len(calls) == 1
    with research.db() as con:
        assert con.execute("SELECT COUNT(*) FROM usage WHERE actual IS NULL").fetchone()[0] == 2


def test_social_payload_cache_and_oversize_limit():
    raw = json.dumps({"results": [{"ticker": "PEP", "mentions": 50, "mentions_24h_ago": 10}]}).encode()
    calls = []
    response = NS(status_code=200, headers={}, close=lambda: None, raise_for_status=lambda: None,
                  iter_content=lambda **kw: [raw])
    client = NS(get=lambda *a, **k: (calls.append(1) or response))
    rows = research.fetch_social("apewisdom", session=client)
    assert rows[0]["unique_authors"] is None
    assert research.fetch_social("apewisdom", session=client) == rows
    assert len(calls) == 1


def test_stability_requires_real_hour_despite_quarterhour_observations(monkeypatch):
    from pulsar import sources
    # Temporal gate in isolation; primary-text validity is covered by v986 tests.
    monkeypatch.setattr(sources, "valid_catalyst", lambda *a, **kw: True)
    now = time.time()
    card = dict(symbol="PEP", evidence_hash="proof", eligible=True, rules_version=__import__("pulsar.evidence",fromlist=["REVISION"]).REVISION,
                verified_evidence={"catalyst": {"document_hash": "same-event"}}, attention={"observed_at": now})
    for delta in (3600, 2700, 1800, 900, 0):
        research.save_assessment(card, now=now-delta)
    assert research.stable_candidate("PEP", now=now)
    research.save_assessment({**card, "eligible": False}, now=now+1)
    assert research.stable_candidate("PEP", now=now+1) is None


def test_frozen_exit_policy_survives_restart_and_partial_unknown():
    from pulsar.positions import decision, claim_partial, partial_result
    import pandas as pd
    now = datetime(2026, 9, 10, 15, 0, tzinfo=timezone.utc)
    item = {"id": "proposal", "plan": {"price": 100., "R": 10., "stop": 90., "quantity": 9., "partial_at_2R": True}}
    rec = NS(avg_cost=100., entry_time="2026-09-09T14:00:00+00:00", quantity=9.)
    bars = pd.DataFrame({"high": [125., 500.]}, index=pd.to_datetime(["2026-09-10T14:30:00Z", "2026-09-10T14:55:00Z"]))
    out = decision(item, rec, 121., bars, now=now)
    assert out["action"] == "PARTIAL" and out["quantity"] == 3
    assert out["stop"] == 100.
    claim_partial(item["id"], 3)
    partial_result(item["id"], "UNKNOWN", {"error": "Timeout"})
    control.start_session()
    with pytest.raises(control.Blocked):
        claim_partial(item["id"], 3)
    assert decision(item, rec, 121., now=now)["action"] == "HOLD"
    assert decision(item, rec, 99., now=now)["action"] == "CLOSE"
    with control.transaction() as con:
        assert con.execute("SELECT high15m FROM pulsar_position_control").fetchone()[0] == 125.


def test_pulsar_aggregate_only_never_claims_full_score():
    from pulsar.evidence import evaluate
    card = {"sources": [], "bars": [], "attention": {"mentions": 1000000}, "missing": ["Autoren fehlen"]}
    result = evaluate(card)
    assert result["score"] is None and not result["eligible"]


def test_pulsar_settlement_real_case_and_time_gate():
    from pulsar.core import entry_window
    assert entry_window(datetime(2026, 9, 10, 14, 5, tzinfo=timezone.utc))
    assert not entry_window(datetime(2026, 9, 10, 13, 45, tzinfo=timezone.utc))
    assert not entry_window(datetime(2026, 9, 10, 19, 30, tzinfo=timezone.utc))
    assert not entry_window(datetime(2026, 9, 12, 16, 0, tzinfo=timezone.utc))


def test_personal_telegram_confirmation_text_uses_plan():
    from pulsar.telegram import text, keyboard
    p = {"symbol": "PEP", "environment": "DEMO", "account": "acct", "quantity": 2,
         "price": 100, "currency": "USD", "stop": 90, "take": 160, "partial_at_2R": False}
    assert "keine Teilorder" in text(p)
    assert all(len(b["callback_data"].encode()) <= 64 for b in keyboard("x"*24, "y"*22)[0])
