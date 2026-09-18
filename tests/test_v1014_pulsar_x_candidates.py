"""X discoveries remain research seeds; only complete receipts graduate."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import time

import pytest

from pulsar import candidate_selection, control, evidence, research, worker
from pulsar.source_coordination import (attention_fresh, canonical_url, coordination,
    protect_review, research_order, x_attention)
from test_repair_pulsar_selection import client, rows, stock
from test_v989_fmp import plan


def seed(symbol="NEW", *, now=None, posts=4, accounts=2):
    return {"symbol": symbol, "source_family": "X", "observed_at": time.time() if now is None else now,
        "evidence_ids": ["post-"+symbol], "sampled_post_count": posts,
        "distinct_accounts_in_sample": accounts, "topics": ["EARNINGS"], "source_roles": ["ACCOUNT_WATCH"]}


def test_fair_new_x_candidates_do_not_replace_reddit_or_sum_shared_mentions(plan):
    attention = rows(["AAA", "BBB", "CCC", "DDD", "EEE"])
    seeds = [seed("AAA"), seed("NEW"), seed("NEXT")]
    c = client({s: stock(s) for s in ["AAA", "BBB", "CCC", "DDD", "EEE", "NEW", "NEXT"]},
               cached=["AAA", "BBB", "CCC", "DDD", "EEE", "NEW", "NEXT"])
    selected = candidate_selection.select_candidates(attention, x_candidates=seeds, client=c)
    assert [s["attention"]["symbol"] for s in selected] == ["AAA", "NEW", "BBB", "NEXT", "CCC"]
    assert selected[0]["attention"]["mentions"] == 1000
    assert selected[0]["attention"]["discovery_origin"] == "REDDIT_AND_X"
    assert selected[1]["attention"]["mentions"] is None
    pipeline = research.cached("candidate_selection")["data"]["source_pipeline"]
    assert pipeline["x_duplicates_merged"] == 1 and pipeline["x_profile_validated"] == 3
    assert pipeline["reddit_selected"] == 3 and pipeline["x_new_selected"] == 2
    assert c.session.calls == []


def test_unknown_etf_x_tickers_cannot_take_valid_slots_or_trade(plan):
    rs = rows(["AAA", "BBB", "CCC", "DDD", "EEE"])
    profiles = {s: stock(s) for s in ["AAA", "BBB", "CCC", "DDD", "EEE"]}
    profiles.update(SPY=stock("SPY", isEtf=True), FAKE=stock("OTHER"))
    c = client(profiles, cached=profiles)
    selected = candidate_selection.select_candidates(rs, x_candidates=[seed("SPY"), seed("FAKE")], client=c)
    assert [s["attention"]["symbol"] for s in selected] == ["AAA", "BBB", "CCC", "DDD", "EEE"]
    result = research.cached("candidate_selection")["data"]
    assert result["source_pipeline"]["x_profile_rejected"] == 2
    assert {r["status"] for r in result["excluded"]} == {"UNKNOWN", "ETF_OR_FUND"}
    assert not control.proposals()


def test_only_exact_inspected_profile_receipt_is_forwarded_to_x_monitor(plan, monkeypatch):
    import market_intelligence
    observed = []
    def record(symbol, source, **kwargs):
        observed.append((symbol, deepcopy(source)))
        return {"status": "RECORDED_TEST"}
    monkeypatch.setattr(market_intelligence, "record_candidate_validation", record, raising=False)
    c = client({"NEW": stock("NEW"), "SPY": stock("SPY", isEtf=True)}, cached=["NEW", "SPY"])
    selected = candidate_selection.select_candidates([], x_candidates=[seed("NEW"), seed("SPY")], client=c)
    assert [s for s, _ in observed] == ["NEW", "SPY"]
    assert observed[0][1] == selected[0]["profile_evidence"]["source"]
    assert observed[1][1]["data"]["isEtf"] is True
    assert selected[0]["selection"]["x_count_monitoring"] == "RECORDED_TEST"


@pytest.mark.parametrize("patch", [{"observed_at": 0}, {"symbol": "ABC;DROP"},
    {"sampled_post_count": 0}, {"source_family": "FMP"}, {"evidence_ids": []}])
def test_invalid_social_seed_never_reaches_profile_fetch(patch):
    now = time.time()
    ranked, status = research_order([], [{**seed(now=now), **patch}], now=now)
    assert ranked == [] and status["x_invalid_count"] == 1


def test_sample_counts_never_create_mentions_or_score():
    # 10.3.0: Eine X-Stichprobe bleibt eine Stichprobe -- sie erzeugt weder
    # Erwaehnungszahlen noch einen Punktestand. Unter 8 Beitraegen / 5
    # Accounts erfuellt sie auch das Hype-Social-Kriterium nicht.
    now = time.time()
    attention = x_attention(seed(now=now), now=now)
    result = evidence.evaluate({"symbol": "NEW", "attention": attention}, now=now)
    assert attention["mentions"] is None
    assert result["score"] is None and result["eligible"] is False
    assert any("X-Stichprobe zu duenn" in m for m in result["missing"])
    # Frisch ist eine Stichprobe 24 Stunden, danach nicht mehr.
    assert attention_fresh({"attention": attention}, now=now)
    assert not attention_fresh({"attention": attention}, now=now+86401)


def test_rich_x_sample_meets_social_criterion_but_needs_price_proof():
    now = time.time()
    attention = x_attention(seed(now=now, posts=9, accounts=6), now=now)
    result = evidence.evaluate({"symbol": "NEW", "attention": attention}, now=now)
    assert not result["eligible"]  # Kursbestaetigung fehlt weiterhin.
    assert result["hype"]["social"]["kind"] == "X_STICHPROBE"
    assert not any("Social-Spike" in m for m in result["missing"])


def test_article_syndication_and_x_links_are_not_independent_votes():
    now = time.time()
    url = "https://www.example.com/story?a=tracking"
    canonical = canonical_url(url)
    timestamp = datetime.fromtimestamp(now-60, timezone.utc).isoformat()
    attention = {"source_family": "reddit_aggregate", "x_discovery": {
        "linked_url_hashes": [hashlib.sha256(canonical.encode()).hexdigest()]}}
    sources = [{"id": "fmp", "provider": "FMP", "kind": "news", "data": [
        {"url": url, "publishedDate": timestamp, "title": "earnings"}]},
        {"id": "massive", "provider": "MASSIVE", "kind": "news", "data": [
        {"article_url": "https://example.com/story", "published_utc": timestamp, "title": "earnings"}]}]
    report = coordination("NEW", attention, None, sources, {}, now=now)
    assert report["news_rows"] == 2 and report["distinct_article_urls"] == 1
    assert report["duplicate_news_rows"] == 1 and report["shared_x_article_count"] == 1
    assert not report["x_claim_confirmed"] and not report["crisis_authorized"]
    assert report["independent_event_sources"] is None


def test_x_only_ai_verdict_cannot_reject_or_promote_a_candidate():
    review = {"ok": True, "daten": {"verdict": "REJECT", "missing": [],
        "thesis": {"text": "Unconfirmed topic", "source_ids": ["x"]},
        "risks": [{"text": "Risk", "source_ids": ["x"]}]}}
    card = {"sources": [{"id": "x", "provider": "X"}]}
    result = protect_review(review, card)
    assert result["daten"]["verdict"] == "OBSERVE"
    assert result["source_authority"]["original_verdict"] == "REJECT"
    assert review["daten"]["verdict"] == "REJECT"  # Original AI receipt intact.
    review["daten"]["verdict"] = "REVIEW"
    assert protect_review(review, card)["daten"]["verdict"] == "OBSERVE"
    # A company-profile citation does not independently prove X's risk claim.
    review["daten"]["verdict"] = "REJECT"
    review["daten"]["thesis"]["source_ids"].append("profile")
    card["sources"].append({"id": "profile", "provider": "FMP", "kind": "profile"})
    assert protect_review(review, card)["daten"]["verdict"] == "OBSERVE"


def test_missing_observation_time_is_reported_not_imputed():
    # 10.3.0: Ohne Zeitbeleg der Beobachtung gibt es keinen Social-Beleg;
    # es wird weder eine Null noch eine Historie erfunden.
    card = {"symbol": "NEW", "attention": {}}
    result = evidence.evaluate(card)
    assert any("ohne Zeitbeleg" in m for m in result["missing"])
    assert not result["eligible"] and result["score"] is None


def test_worker_new_x_stock_reaches_bounded_luna_research_with_sample_labels(plan, monkeypatch):
    import ai_router
    import market_intelligence
    from test_v985_pulsar_flow_and_pages import Router, fixtures
    _, market = fixtures()
    router = Router()
    control.set_mode("BEOBACHTEN")
    monkeypatch.setattr(worker, "refresh_held_data", lambda: None)
    monkeypatch.setattr(research, "discover", lambda: rows(["AAA", "BBB", "CCC", "DDD", "EEE"]))
    monkeypatch.setattr(market_intelligence, "discovery_candidates", lambda **kw: [seed("NEW")])
    monkeypatch.setattr(market_intelligence, "for_symbol", lambda symbol, **kw: {})
    c = client({s: market(s)["profile"] for s in ["AAA", "BBB", "CCC", "DDD", "EEE", "NEW"]},
               cached=["AAA", "BBB", "CCC", "DDD", "EEE", "NEW"])
    monkeypatch.setattr(worker, "select_candidates", lambda rs, **kw:
        candidate_selection.select_candidates(rs, client=c, **kw))
    monkeypatch.setattr(worker, "gather_market", market)
    monkeypatch.setattr(ai_router, "AIRouter", lambda: router)
    w = worker.Worker(); w.session = control.start_session()
    w.cycle(control.settings())
    cards = research.cached("top5")["data"]
    new = next(card for card in cards if card["symbol"] == "NEW")
    assert new["precheck"]["ok"] and new["attention"]["mentions"] is None
    assert not new["eligible"] and new["score"] is None
    precheck = next(payload for task, payload in router.calls if task == "pulsar_precheck")
    packet = next(row for row in precheck["candidates"] if row["symbol"] == "NEW")
    x = next(source for source in packet["sources"] if source["provider"] == "X")
    assert x["data"]["discovery"]["sampled_post_count"] == 4
    assert x["data"]["discovery"]["calibrated_spike"] is False
    assert len(cards) == 5 and not control.proposals()
    pipeline = research.cached("candidate_selection")["data"]["source_pipeline"]
    assert pipeline["x_researched"] == 1 and pipeline["researched"] == 5
