"""D19: bounded dated type selection before market/GPT work, all HTTP faked."""
from copy import deepcopy
import time

import pytest

from fmp_reference import FMPReferenz
from fmp_service import encoded
from pulsar import candidate_selection as selection, control, research, worker
from test_v989_fmp import Response, plan
from test_v985_pulsar_flow_and_pages import Router, fixtures


def rows(symbols):
    result = research.normalise("apewisdom", {"results": [
        {"ticker": s, "mentions": 1000-i*10, "mentions_24h_ago": 20, "rank": i+1}
        for i, s in enumerate(symbols)]})
    for r in result:
        r.update(observed_at=time.time(), evidence_id=r["symbol"]+"-social")
    return result


def stock(symbol, **overrides):
    return {"symbol": symbol, "isEtf": False, "isFund": False, "cik": "00123", **overrides}


class ProfileSession:
    def __init__(self, data):
        self.data, self.calls = data, []
    def get(self, url, **kwargs):
        assert url.endswith("/profile"), "selection must not fetch quotes/news/bars/financials"
        symbol = kwargs["params"]["symbol"]
        self.calls.append(symbol)
        data = self.data[symbol]
        if isinstance(data, Exception):
            raise data
        return Response([deepcopy(data)])


def client(profiles, *, cached=()):
    c = FMPReferenz("FAKE-TYPED-PROFILES")
    c.session = ProfileSession(profiles)
    for symbol in cached:
        c.store.save(encoded(["/profile", {"symbol": symbol}]), [profiles[symbol]], 86400)
    return c


@pytest.mark.parametrize("profile,expected", [
    (stock("ABC"), "SINGLE_STOCK"),
    (stock("ABC", isEtf=True), "ETF_OR_FUND"),
    (stock("ABC", isFund=True), "ETF_OR_FUND"),
    ({"symbol": "ABC", "isEtf": False}, "UNKNOWN"),
    (stock("ABC", isEtf="false"), "UNKNOWN"),
    (stock("ABC", isFund=0), "UNKNOWN"),
    (stock("OTHER"), "UNKNOWN"),
])
def test_type_requires_explicit_boolean_flags_and_exact_symbol(profile, expected):
    assert selection.profile_type("ABC", profile)[0] == expected


def test_cached_etfs_do_not_take_slots_and_cache_timestamp_is_preserved(plan):
    symbols = ["SPY", "QQQ", "AAA", "BBB", "CCC", "DDD", "EEE", "FFF"]
    profiles = {s: stock(s, isEtf=s in {"SPY", "QQQ"}) for s in symbols}
    c = client(profiles, cached=symbols)
    stamp = c.cached_source("/profile", {"symbol": "SPY"})["saved"]
    chosen = selection.select_candidates(rows(symbols), client=c)
    assert [r["attention"]["symbol"] for r in chosen] == symbols[2:7]
    summary = research.cached("candidate_selection")["data"]
    assert summary["inspected_count"] == 7 and summary["profile_fetch_attempts"] == 0
    assert summary["excluded"][0]["observed_at"] == stamp
    assert {r["status"] for r in summary["excluded"]} == {"ETF_OR_FUND"}
    assert c.session.calls == []
    assert summary["remaining_slots"] == 0


def test_first_cycle_replaces_newly_identified_etfs_without_extra_market_queries(plan):
    symbols = ["SPY", "QQQ", "AAA", "BBB", "CCC", "DDD", "EEE", "FFF"]
    profiles = {s: stock(s, isEtf=s in {"SPY", "QQQ"}) for s in symbols}
    c = client(profiles)
    chosen = selection.select_candidates(rows(symbols), client=c)
    assert [r["attention"]["symbol"] for r in chosen] == symbols[2:7]
    assert c.session.calls == symbols[:7]  # No request for FFF after five stocks.
    selection.select_candidates(rows(symbols), client=c)
    assert c.session.calls == symbols[:7]  # The next run uses shared cache receipts.
    assert c.store.status()["verbraucht"] == 7
    assert research.usage_summary()["day"].get("ai", 0) == 0


def test_ten_profile_attempts_still_allow_known_stocks_to_fill_slots(plan):
    missing = ["U"+str(i) for i in range(12)]
    known = ["AAA", "BBB", "CCC", "DDD", "EEE"]
    symbols = missing+known+["SPY"]
    profiles = {s: {"symbol": s} for s in missing}
    profiles.update({s: stock(s) for s in known})
    profiles["SPY"] = stock("SPY", isEtf=True)
    c = client(profiles, cached=known+["SPY"])
    chosen = selection.select_candidates(rows(symbols), client=c)
    assert [r["attention"]["symbol"] for r in chosen] == known
    assert len(c.session.calls) == 10
    summary = research.cached("candidate_selection")["data"]
    assert summary["profile_fetch_attempts"] == 10 and summary["inspected_count"] == 17
    assert all(r["status"] == "UNKNOWN" for r in summary["excluded"])


def test_inspection_limit_and_transport_failures_stay_unknown_with_pause(plan):
    symbols = ["U"+str(i) for i in range(25)]
    c = client({s: RuntimeError("offline provider failure") for s in symbols})
    assert selection.select_candidates(rows(symbols), client=c) == []
    summary = research.cached("candidate_selection")["data"]
    assert summary["inspected_count"] == 20 and summary["profile_fetch_attempts"] == 10
    assert summary["remaining_slots"] == 5 and summary["uninspected_count"] == 5
    assert all(r["status"] == "UNKNOWN" for r in summary["excluded"])
    # Same failed tickers remain paused; the next ten may be probed only within
    # the same existing daily new-candidate and global FMP quotas.
    first = list(c.session.calls)
    selection.select_candidates(rows(symbols[:10]), client=c)
    assert c.session.calls == first


def test_expired_stock_profile_is_reclassified_and_wrong_symbol_is_unknown(plan):
    c = client({"OLD": stock("OLD", isEtf=True), "BAD": stock("OTHER")})
    c.store.save(encoded(["/profile", {"symbol": "OLD"}]), [stock("OLD")],
                 86400, saved=time.time()-86401)
    c.store.save(encoded(["/profile", {"symbol": "BAD"}]), [stock("OTHER")], 86400)
    assert selection.select_candidates(rows(["OLD", "BAD"]), client=c) == []
    summary = research.cached("candidate_selection")["data"]
    assert [r["status"] for r in summary["excluded"]] == ["ETF_OR_FUND", "UNKNOWN"]
    assert c.session.calls == ["OLD"]


def test_future_or_undated_cache_never_confirms_instrument_type(plan, monkeypatch):
    c = client({"ABC": stock("ABC")})
    monkeypatch.setattr(c, "cached_source", lambda *a: {
        "data": [stock("ABC")], "saved": time.time()+1000, "expires": time.time()+2000})
    assert selection.select_candidates(rows(["ABC"]), client=c) == []
    assert research.cached("candidate_selection")["data"]["excluded"][0]["status"] == "UNKNOWN"


def test_mode_change_stops_before_profile_fetch_and_cold_start_slot_survives(plan):
    symbols = ["A"+str(i) for i in range(8)]+["NEW"]
    c = client({s: stock(s) for s in symbols}, cached=symbols)
    attention = rows(symbols)
    attention[-1]["mentions_24h_ago"] = None
    assert selection.select_candidates(attention, client=c, active=lambda: False) == []
    chosen = selection.select_candidates(attention, client=c)
    assert chosen[4]["attention"]["symbol"] == "NEW"
    assert c.session.calls == []


def test_worker_never_enriches_or_sends_etfs_to_gpt_and_preserves_fmp_receipts(plan, monkeypatch):
    import ai_router
    _, market = fixtures()
    symbols = ["SPY", "QQQ", "AAA", "BBB", "CCC", "DDD", "EEE"]
    profiles = {s: {**market(s)["profile"], "isEtf": s in {"SPY", "QQQ"}} for s in symbols}
    c = client(profiles)
    router, enriched = Router(), []
    control.set_mode("BEOBACHTEN")
    monkeypatch.setattr(worker, "refresh_held_data", lambda: None)
    monkeypatch.setattr(research, "discover", lambda: rows(symbols))
    monkeypatch.setattr(worker, "select_candidates", lambda rs, **kw:
        selection.select_candidates(rs, client=c, **kw))
    def gather(symbol, *, profile_evidence):
        enriched.append(symbol)
        assert profile_evidence["profile"] == profiles[symbol]
        return market(symbol)
    monkeypatch.setattr(worker, "gather_market", gather)
    monkeypatch.setattr(ai_router, "AIRouter", lambda: router)
    w = worker.Worker(); w.session = control.start_session()
    w.cycle(control.settings())
    assert enriched == symbols[2:]
    assert c.session.calls == symbols
    assert [p["symbol"] for p in router.calls[0][1]["candidates"]] == symbols[2:]
    assert sum(task == "pulsar_precheck" for task, _ in router.calls) == 1
    cards = research.cached("top5")["data"]
    assert all(card["instrument_type_evidence"]["status"] == "SINGLE_STOCK" for card in cards)
    for card in cards:
        receipt = card["precheck"]["input_sources"][card["symbol"]]
        assert receipt["status"] == "INPUT_OF_VALIDATED_RESPONSE"
        assert any(s["provider"] == "FMP" and s["included"] for s in receipt["sources"])
        assert card["precheck"]["input_hash"]
    assert not control.proposals()


def test_market_gather_reuses_exact_preflight_profile_without_second_request(plan, monkeypatch):
    import config
    import fmp_reference
    from sec_fundamentals import SecFundamentals
    from test_v989_fmp import Session, bars
    plan["fmp_plan"] = "FREE"
    control.set_mode("BEOBACHTEN")
    c = FMPReferenz("FAKE-ONE-PROFILE")
    c.session = Session(Response([stock("ABC", currency="USD", price=10)]),
                        Response(bars()), Response([{"symbol": "ABC", "price": 10}]))
    monkeypatch.setattr(fmp_reference, "client", lambda: c)
    monkeypatch.setattr(SecFundamentals, "enabled", lambda s: False)
    monkeypatch.setattr(config, "MASSIVE_ENABLED", False)
    candidate = selection.select_candidates(rows(["ABC"]), client=c)[0]
    market = worker.gather_market("ABC", profile_evidence=candidate["profile_evidence"])
    assert market["errors"] == []
    assert sum(url.endswith("/profile") for url, _ in c.session.calls) == 1
    assert market["sources"][0] == candidate["profile_evidence"]["source"]
    assert market["profile"] == candidate["profile_evidence"]["profile"]


def test_pre_update_retry_batch_cannot_dispatch_etfs(monkeypatch):
    control.set_mode("BEOBACHTEN")
    from pulsar.analysis import REVISION
    research.cache_put("ai:precheck:work", {"review_revision": REVISION,
        "revision": control.settings()["revision"], "cards": [{"symbol": "SPY"}],
        "attempts": 0, "expires_at": time.time()+7200, "next_at": 0}, 7200)
    w = worker.Worker()
    monkeypatch.setattr(w, "_review_cards", lambda *a, **kw: pytest.fail("old untyped batch dispatched"))
    assert not w._retry_review(control.settings())
    assert not (research.cached("ai:precheck:work") or {}).get("data")


def test_selection_and_optional_source_status_are_visible_without_requests(plan):
    from pulsar.presentation import snapshot
    c = client({"SPY": stock("SPY", isEtf=True)}, cached=["SPY"])
    selection.select_candidates(rows(["SPY"]), client=c)
    value = snapshot()
    assert value["candidate_selection"]["excluded"][0]["symbol"] == "SPY"
    assert not value["candidate_selection"]["stale"]
    assert "optional_sources" in value
    assert c.session.calls == []
