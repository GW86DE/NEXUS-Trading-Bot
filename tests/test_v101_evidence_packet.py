"""Offline regressions for the exact five packets in the 13 Sep diagnostic."""
from copy import deepcopy
from pathlib import Path
import runpy

import pytest

from pulsar.ai_packet import BAR_FIELDS, REVISION, project, size
from pulsar.control import Blocked, digest
from pulsar.news_normalization import normalize_news


EVIDENCE = runpy.run_path(str(Path(__file__).parent / "fixtures" / "v101_evidence_packet.py"))["DIAGNOSTIC_EVIDENCE"]


def prepared_packet(card):
    packet = deepcopy(card["packet"])
    for source in packet["sources"]:
        if source.get("provider") == "MASSIVE" and source.get("kind") == "news":
            source["data"] = normalize_news(card["massive_original"], "MASSIVE")
    return packet


@pytest.mark.parametrize("card", EVIDENCE["cards"], ids=lambda c: c["symbol"])
def test_original_massive_receipts_retain_all_fifteen_descriptions_urls_and_times(card):
    original = deepcopy(card["massive_original"])
    normalized = normalize_news(original, "MASSIVE")
    assert len(original) == len(normalized) == 3
    for before, after in zip(original, normalized):
        assert all(after[k] == v for k, v in before.items())
        assert after["text"] == before["description"]
        assert after["url"] == before["article_url"]
        assert after["publishedDate"] == before["published_utc"]
        assert after["text"] and after["url"] and after["publishedDate"]
    assert original == card["massive_original"]


@pytest.mark.parametrize("card", EVIDENCE["cards"], ids=lambda c: c["symbol"])
def test_all_five_real_prechecks_include_latest_ohlcv_and_citable_massive_news(card):
    packet = prepared_packet(card)
    original = deepcopy(packet)
    view = project(packet, budget=4300)
    assert size(view) <= 4300
    assert REVISION == view["view_revision"] == "evidence-view-3"
    assert view["full_packet_sha256"] == digest(packet)
    raw = next(s["data"] for s in packet["sources"] if s.get("kind") == "bars")
    bars = next(s for s in view["sources"] if s["kind"] == "bars")
    latest = max(raw, key=lambda r: r["date"])
    assert bars["as_of"] == bars["latest_available_date"] == "2026-09-11"
    assert bars["data"][0] == {k: latest[k] for k in BAR_FIELDS}
    assert bars["data"][0]["volume"] == latest["volume"] > 0
    assert all(set(r) == set(BAR_FIELDS) for r in bars["data"])
    assert bars["data_order"] == "newest_first" and bars["view_truncated"]
    assert bars["full_data_sha256"] == digest(raw)
    assert bars["included_rows"] == len(bars["data"])
    massive = next(s for s in view["sources"] if s["provider"] == "MASSIVE")
    assert isinstance(massive["data"], list) and massive["data"]
    news = massive["data"][0]
    assert news["url"] == card["massive_original"][0]["article_url"]
    assert news["publishedDate"] == card["massive_original"][0]["published_utc"]
    assert news["title"] and news["text"]
    assert packet == original


def candle(date="2026-09-11", **kwargs):
    return dict(date=date, open=123.45, high=125.67, low=122.34, close=124.56, volume=12345678, **kwargs)


def bar_packet(rows):
    return {"symbol": "TEST", "sources": [{"id": "bars-id", "provider": "FMP", "kind": "bars", "data": rows}]}


def test_bar_order_does_not_depend_on_provider_order_and_missing_volume_is_not_zero():
    newest = candle()
    del newest["volume"]
    rows = [candle("2026-09-10"), newest, candle("2026-09-09")]
    first = project(bar_packet(rows))
    second = project(bar_packet(list(reversed(rows))))
    assert first["sources"][0]["data"] == second["sources"][0]["data"]
    assert first["sources"][0]["data"][0] == newest
    assert "volume" not in first["sources"][0]["data"][0]
    assert first["sources"][0]["as_of"] == "2026-09-11"


def test_zero_observed_volume_is_preserved_and_undated_rows_are_disclosed():
    row = candle(); row["volume"] = 0
    view = project(bar_packet([row, {"close": 1}, "malformed", {"date": "not-a-date"}]))
    bars = view["sources"][0]
    assert bars["data"][0]["volume"] == 0
    assert bars["undated_rows"] == 3 and bars["available_rows"] == 1


def test_tight_budget_never_degrades_a_candle_into_a_date_only_object():
    packet = bar_packet([candle("2026-09-11"), candle("2026-09-10")])
    successful = blocked = 0
    for budget in range(500, 1600, 25):
        try:
            view = project(packet, budget=budget)
        except Blocked:
            blocked += 1
            continue
        assert size(view) <= budget
        bars = view["sources"][0]
        assert all(set(row) == set(BAR_FIELDS) for row in bars["data"])
        successful += 1
        assert bars["data"] and bars["as_of"] == "2026-09-11"
    assert successful and blocked


def test_full_hash_changes_when_an_omitted_old_candle_changes():
    packet = bar_packet([candle(f"2026-09-{day:02d}") for day in range(1, 12)])
    first = project(packet, budget=800)
    packet["sources"][0]["data"][0]["close"] = 88
    second = project(packet, budget=800)
    assert first["sources"][0]["data"] == second["sources"][0]["data"]
    assert first["full_packet_sha256"] != second["full_packet_sha256"]
    assert first["sources"][0]["full_data_sha256"] != second["sources"][0]["full_data_sha256"]


def test_normalization_preserves_provider_metadata_and_canonical_precedence():
    row = {"title": "Test", "text": "Canonical", "description": "Provider excerpt",
           "url": "https://canonical.test", "article_url": "https://provider.test",
           "publishedDate": "2026-09-11", "published_utc": "2026-09-10T12:00:00Z",
           "publisher": {"name": "Publisher"}, "tickers": ["TEST"], "id": "news-id"}
    result = normalize_news([row], "Massive")[0]
    assert result == row
    result["publisher"]["name"] = "Changed"
    assert row["publisher"]["name"] == "Publisher"
    assert normalize_news([row], "FMP") == [row]


@pytest.mark.parametrize("provider", ["MASSIVE", "FMP", "UNKNOWN"])
def test_missing_news_values_remain_missing(provider):
    assert normalize_news(None, provider) == []
    result = normalize_news([None, "invalid", {"title": "Only title"}], provider)
    assert result == [{"title": "Only title"}]


def test_news_url_and_timestamp_are_never_cut_to_make_a_receipt_fit():
    row = {"title": "ä漢😀" * 300, "text": "ä漢😀" * 300,
           "url": "https://news.test/" + "x" * 700, "publishedDate": "2026-09-11T12:00:00.123456Z"}
    packet = {"symbol": "TEST", "sources": [{"id": "news", "provider": "FMP", "kind": "news", "data": [row]}]}
    roomy = project(packet, budget=1600)["sources"][0]["data"][0]
    assert roomy["url"] == row["url"] and roomy["publishedDate"] == row["publishedDate"]
    assert roomy["text"].endswith("…")
    tight = project(packet, budget=700)
    assert size(tight) <= 700
    assert tight["sources"][0]["data"]["details_omitted"]
    assert tight["sources"][0]["view_truncated"]


def test_duplicate_source_id_is_rejected_before_projection():
    packet = bar_packet([candle()])
    packet["sources"].append(deepcopy(packet["sources"][0]))
    with pytest.raises(Blocked):
        project(packet)


def test_empty_news_stay_empty_and_are_not_mislabeled_as_truncated():
    packet = {"symbol": "TEST", "sources": [{"id": "news", "provider": "FMP", "kind": "news", "data": []}]}
    source = project(packet)["sources"][0]
    assert source["data"] == [] and source["view_truncated"] is False


@pytest.mark.parametrize("card", EVIDENCE["cards"], ids=lambda c: c["symbol"])
def test_worker_uses_news_normalization_for_actual_five_markets(monkeypatch, card):
    from pulsar import worker
    packet = deepcopy(card["packet"])
    attention = deepcopy(card["attention"])
    sources = [s for s in packet["sources"] if s.get("provider") != "apewisdom"]
    for source in sources:
        if source.get("provider") == "MASSIVE":
            source["data"] = deepcopy(card["massive_original"])
    market = {"sources": sources,
              "profile": next(s["data"] for s in sources if s.get("kind") == "profile"),
              "bars": next(s["data"] for s in sources if s.get("kind") == "bars")}
    monkeypatch.setattr(worker.research, "baseline", lambda *a, **kw: {})
    rebuilt = worker.build_card(attention, market, now=1789304460.)
    normalized = next(s["data"] for s in rebuilt["packet"]["sources"] if s.get("provider") == "MASSIVE")
    assert len(normalized) == 3
    for before, after in zip(card["massive_original"], normalized):
        assert after["text"] == before["description"][:600]
        assert after["url"] == before["article_url"]
        assert after["publishedDate"] == before["published_utc"]
    receipt = next(s["data"] for s in rebuilt["sources"] if s.get("provider") == "MASSIVE")
    assert receipt == card["massive_original"]
    view = project(rebuilt["packet"], budget=4300)
    assert size(view) <= 4300
    assert next(s for s in view["sources"] if s["kind"] == "bars")["as_of"] == "2026-09-11"


def test_actual_nbis_financial_period_and_errors_survive_bounded_projection():
    import fmp_data
    raw = deepcopy(runpy.run_path(str(Path(__file__).with_name("v101_financial_fixture.py")))["DATA"]["NBIS"])
    profile = raw.pop("profile")
    annual = fmp_data.normalize_financials("NBIS", profile, raw, now=1789304460.)
    packet = prepared_packet(next(c for c in EVIDENCE["cards"] if c["symbol"] == "NBIS"))
    for source in packet["sources"]:
        if source.get("kind") == "annual_financials":
            source["data"] = annual
    view = project(packet, budget=4300)
    assert size(view) <= 4300
    financial = next(s["data"] for s in view["sources"] if s["kind"] == "annual_financials")
    assert financial["errors"] == annual["errors"]
    assert financial["trend_period"] == annual["trend_period"]
    assert financial["trend_period"]["comparable"] is False
    assert financial["trend_period"]["from"] == "2021-12-31"
    assert financial["trend_period"]["to"] == "2025-12-31"
    assert financial["available"] == annual["available"]
    assert financial["symbol_identity_verified"] == annual["symbol_identity_verified"]
    assert next(s for s in view["sources"] if s["kind"] == "bars")["as_of"] == "2026-09-11"
