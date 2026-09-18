"""No HTTP: provider transport diversity cannot manufacture crisis corroboration."""
from datetime import datetime, timedelta, timezone

import pytest

from news_sources import MultiSourceNews, NewsItem, canonical_news_url
from news_filter import Nachrichtenlage, NachrichtenFilter
from event_intelligence import news_risk_evidence, global_crisis_score

NOW = datetime.now(timezone.utc)


def row(source, text, host, path="/story", **extra):
    return {"source": source, "text": text, "url": "https://"+host+path,
            "published_at": NOW.isoformat(), "metadata": {}, **extra}


def lage(*rows):
    return Nachrichtenlage(symbol="MARKT", geprueft=True,
        schlagzeilen=[r["text"] for r in rows], quellen=[r["source"] for r in rows],
        quellen_meldungen=list(rows))


def test_same_article_different_headlines_and_tracking_is_one_origin():
    items = [NewsItem("FMP", "Example Corporation wins major federal procurement deal", url="https://example.org/release/1?utm_source=fmp", published_at=NOW),
             NewsItem("MASSIVE", "A breakthrough award lifts outlook for a growing business", url="http://www.example.org/release/1#top", published_at=NOW)]
    result = MultiSourceNews._dedupe(items)
    assert len(result) == 1
    assert result[0].providers == ["FMP", "MASSIVE"]
    assert result[0].metadata["deduplicated_urls"] == ["https://example.org/release/1"]


def test_content_query_parameters_are_part_of_identity():
    assert canonical_news_url("https://a.example.org/?story=1&utm_medium=x") != canonical_news_url("https://a.example.org/?story=2")


def test_duplicate_headline_social_lineage_survives_provider_merge():
    title = "Nuclear war concerns emerge from a political social media post"
    items = [NewsItem("FMP", title, url="https://example.org/news", published_at=NOW),
             NewsItem("X", title, url="https://x.com/user/status/123", published_at=NOW)]
    merged = MultiSourceNews._dedupe(items)
    assert len(merged) == 1
    assert merged[0].metadata["social_lineage"] is True
    reported = row("FMP", title, "example.org", metadata=merged[0].metadata)
    proof = news_risk_evidence(lage(reported), now=NOW)
    assert proof["excluded_reasons"] == {"SOCIAL_LINEAGE": 1}
    assert proof["actionable_crisis_score"] == 0


def test_single_source_panic_is_retained_but_not_confirmed():
    item = lage(row("FMP", "War invasion sanctions nuclear missile attack", "example.org"))
    proof = news_risk_evidence(item, now=NOW)
    assert proof["raw_crisis_score"] == 30
    assert proof["actionable_crisis_score"] == 0
    assert proof["status"] == "UNCONFIRMED_RISK_HINT"
    assert not proof["verified_event"]
    assert not proof["buy_pause_supported"]
    assert global_crisis_score(item) == 0


@pytest.mark.parametrize("second_host,second_metadata", [
    ("other.example.org", {}),
    ("example.net", {"original_url": "https://example.org/original"}),
    ("example.net", {"origin_platform": "X"}),
    ("example.net", {"repost_of": "123"}),
    ("x.com", {}),
])
def test_providers_subdomains_or_social_reposts_do_not_add_independent_origin(second_host, second_metadata):
    evidence = news_risk_evidence(lage(
        row("FMP", "War invasion as military forces cross the disputed eastern frontier", "example.org"),
        row("MASSIVE", "Military escalation: diplomats respond to invasion overnight", second_host, metadata=second_metadata)), now=NOW)
    assert not evidence["buy_pause_supported"]
    assert evidence["actionable_crisis_score"] == 0


def test_two_origins_same_specific_category_are_risk_hint_not_event_proof():
    evidence = news_risk_evidence(lage(
        row("FMP", "War invasion as military forces cross the disputed eastern frontier", "example.org"),
        row("MASSIVE", "Military escalation: diplomats respond to invasion overnight", "example.net")), now=NOW)
    assert evidence["buy_pause_supported"]
    assert evidence["actionable_crisis_score"] == 30
    assert evidence["origin_count"] == 2
    assert evidence["status"] == "MULTISOURCE_RISK_HINT"
    assert evidence["verified_event"] is False


def test_unrelated_categories_or_neutral_second_article_cannot_confirm_risk():
    evidence = news_risk_evidence(lage(
        row("FMP", "War invasion across the border", "example.org"),
        row("MASSIVE", "Bank failure and a sovereign default concern investors", "example.net"),
        row("Finnhub", "Quarterly report released by a leading retailer", "example.com")), now=NOW)
    assert not evidence["buy_pause_supported"]


def test_single_nuclear_word_does_not_gain_support_from_two_war_reports():
    evidence = news_risk_evidence(lage(
        row("FMP", "War and nuclear rhetoric escalate along the eastern border", "example.org"),
        row("MASSIVE", "Diplomats discuss the consequences of war overnight", "example.net")), now=NOW)
    assert evidence["actionable_crisis_score"] == 8


@pytest.mark.parametrize("published", [None, (NOW-timedelta(days=2)).isoformat(), (NOW+timedelta(minutes=1)).isoformat()])
def test_stale_undated_or_future_article_cannot_be_second_origin(published):
    evidence = news_risk_evidence(lage(
        row("FMP", "War invasion across the border", "example.org"),
        row("MASSIVE", "Military escalation concerns regional officials", "example.net", published_at=published)), now=NOW)
    assert not evidence["buy_pause_supported"]
    assert evidence["excluded_reasons"] == {"STALE_OR_UNDATED": 1}


def test_global_keyword_pause_also_requires_multiple_origins(monkeypatch):
    filt = NachrichtenFilter.__new__(NachrichtenFilter)
    one = lage(row("FMP", "Bankruptcy chapter 11 insolvency proceedings begin in the region", "example.org"))
    monkeypatch.setattr(filt, "marktlage", lambda: one)
    assert not filt.markt_kritisch()
    two = lage(*one.quellen_meldungen, row("MASSIVE", "Company seeks bankruptcy protection during a restructuring", "example.net"))
    monkeypatch.setattr(filt, "marktlage", lambda: two)
    assert filt.markt_kritisch()


def test_active_exchange_halt_remains_separate_hard_signal():
    item = Nachrichtenlage(symbol="PEP", geprueft=True, quellen_meldungen=[{
        "source": "Nasdaq Halts", "official": True, "symbols": ["PEP"],
        "metadata": {"active_halt": True, "hard_signal": True, "observed_at": datetime.now(timezone.utc).isoformat()}}])
    assert item.aktiver_halt
    assert item.kauf_blockiert


def test_radar_syndication_has_no_diversity_bonus_but_real_origins_do(monkeypatch):
    from news_radar import priority_from_articles
    import config
    monkeypatch.setattr(config, "NEWS_RADAR_MIN_SCORE", 1)
    monkeypatch.setattr(config, "NEWS_DIVERSITY_BONUS_PER_SOURCE", 2)
    first = {"source": "FMP", "headline": "Manufacturer raises guidance with record revenue", "symbols": ["PEP"],
             "url": "https://example.org/release?utm_source=fmp", "published_at": NOW.isoformat()}
    syndication = {**first, "source": "MASSIVE", "headline": "Business outlook stronger after guidance raised", "url": "https://example.org/release"}
    single = priority_from_articles([first], ["PEP"])[0]
    repeated = priority_from_articles([first, syndication], ["PEP"])[0]
    assert repeated.score == single.score
    assert repeated.sources == ("FMP", "MASSIVE")
    assert repeated.publication_origins == ("example.org",)
    independent = {**first, "source": "MASSIVE", "headline": "New contract award boosts confidence in the manufacturer", "url": "https://example.net/industry"}
    multiple = priority_from_articles([first, independent], ["PEP"])[0]
    assert multiple.score == single.score+2
    repost = {**independent, "metadata": {"original_url": "https://x.com/user/status/123"}}
    assert priority_from_articles([first, repost], ["PEP"])[0].score == single.score
    linked_repost = {**independent, "summary": "Original post: https://x.com/user/status/123"}
    assert priority_from_articles([first, linked_repost], ["PEP"])[0].score == single.score
