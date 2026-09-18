from copy import deepcopy
from datetime import datetime, timezone
import json

import pytest
from pulsar import analysis, control
from pulsar.ai_packet import project, size
from test_v985_pulsar_flow_and_pages import fixtures, Router, answer


def test_large_fmp_and_sec_evidence_reaches_gpt_as_bounded_views():
    control.set_mode("BEOBACHTEN")
    rows, market = fixtures()
    packets = []
    for row in rows:
        symbol = row["symbol"]
        sources = market(symbol)["sources"] + [
            {"id": symbol+"-financials", "provider": "FMP", "kind": "annual_financials",
             "data": {"symbol": symbol, "currency": "USD", "latest": {"revenue": 100},
                      "years": [{"long_filing": "ä漢😀"*10000}]*5}},
            {"id": symbol+"-social", "provider": "apewisdom", "data": {"mentions": 77}}]
        packets.append({"symbol": symbol, "sources": sources})
    original = deepcopy(packets)
    router = Router()
    result = analysis.precheck(packets, router)
    assert result["ok"] and len(router.calls) == 1
    payload = router.calls[0][1]
    assert size(payload) <= 24000
    assert len(payload["candidates"]) == 5
    for old, new in zip(packets, payload["candidates"]):
        assert [s["id"] for s in new["sources"]] == [f"r{i+1}" for i in range(len(old["sources"]))]
        note = next(n for n in result["daten"]["notes"] if n["symbol"] == old["symbol"])
        assert note["thesis"]["source_ids"] == [old["sources"][-1]["id"]]
        assert any(s["view_truncated"] for s in new["sources"])
    assert packets == original
    assert analysis.analyse(packets[0], router)["ok"]
    assert size(router.calls[-1][1]) <= 22000
    assert analysis.analyse(packets[0], router)["cached"]
    assert len(router.calls) == 2


def test_model_and_omitted_evidence_changes_invalidate_cache_key():
    p = {"symbol": "X", "sources": [{"id": "a", "data": "ä"*90000}]}
    r = Router()
    r.modellname = lambda tier: "model-a"
    first = analysis._key("precheck", project(p), r)
    r.modellname = lambda tier: "model-b"
    assert analysis._key("precheck", project(p), r) != first
    r.modellname = lambda tier: "model-a"
    p["sources"][0]["data"] += "changed beyond the excerpt"
    assert analysis._key("precheck", project(p), r) != first


def pep():
    row = dict(broker="etoro", broker_position_id="3597440106", entry_order_id="380127623",
               broker_account_fingerprint="account-a", paper=True, waehrung="USD", menge=109,
               einstieg_preis=136.35)
    pos = dict(owned_position_ids=["3597440106"], order_ids=["380127623"],
               account_fingerprint="account-a", broker_environment="DEMO", waehrung="USD",
               kurs=137, kursalter=5, kursquelle="ETORO_RATES_BID", broker_stop=None,
               broker_take_profit=None, planned_stop=135.8946, planned_take_profit=138.5207)
    return row, dict(updated_at=datetime.fromtimestamp(1000, timezone.utc).isoformat(), positionen=[pos])


def test_pep_shows_reference_and_planned_protection_without_inventing_confirmation():
    from etoro_trade_display import enrich
    row, snapshot = pep()
    enrich(row, snapshot, now=1010)
    assert row["current_price"] == 137 and not row["current_executable"]
    assert row["planned_take_profit"] == 138.5207
    assert row["stop_price"] is None and row["broker_take_profit"] is None
    assert row["open_display_pnl"] == pytest.approx(70.85)
    assert "open_net_pnl" not in row


@pytest.mark.parametrize("key,value", [("account_fingerprint", "wrong"), ("broker_environment", "LIVE"),
    ("owned_position_ids", ["wrong"]), ("order_ids", ["wrong"]), ("waehrung", "EUR")])
def test_no_etoro_overlay_for_wrong_binding(key, value):
    from etoro_trade_display import enrich
    row, snapshot = pep()
    original = deepcopy(row)
    snapshot["positionen"][0][key] = value
    enrich(row, snapshot, now=1010)
    assert row == original


def test_stale_quote_does_not_become_live_after_ui_refresh():
    from etoro_trade_display import enrich
    row, snapshot = pep()
    snapshot["positionen"][0].update(broker_stop=135, broker_take_profit=140)
    enrich(row, snapshot, now=1500)
    assert "current_price" not in row
    assert row["reference_price"] == 137
    assert row["stop_price"] is None and row["broker_take_profit"] is None
