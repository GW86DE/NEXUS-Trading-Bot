from __future__ import annotations

from datetime import datetime, timezone

import config
import news_sources
from news_sources import MultiSourceNews


class FakeResponse:
    def __init__(self, payload, *, status=200, reason="OK"):
        self._payload = payload
        self.status_code = status
        self.reason = reason
        self.ok = 200 <= status < 300
        self.content = b"x"

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.headers = {}

    def get(self, url, params=None, timeout=None, headers=None, **kwargs):
        self.calls.append({"url": url, "params": dict(params or {}), "timeout": timeout, "headers": headers})
        if not self.responses:
            raise AssertionError(f"unerwarteter HTTP-Aufruf: {url}")
        return self.responses.pop(0)


def _only_fmp(monkeypatch, tmp_path):
    monkeypatch.setattr(news_sources, "STATUS_FILE", tmp_path / "news_source_status.json")
    news_sources._GLOBAL_CACHE.clear()
    import fmp_service
    monkeypatch.setattr(fmp_service, "settings", lambda: ("AUTO", "STARTER"))
    monkeypatch.setattr(config, "NEWS_LEGACY_OPTIONAL_SOURCES_ENABLED", False, raising=False)
    monkeypatch.setattr(config, "FMP_API_KEY", "SECRET_FMP_KEY", raising=False)
    monkeypatch.setattr(config, "NEWS_SOURCE_FMP_ENABLED", True, raising=False)
    monkeypatch.setattr(config, "FMP_NEWS_ENABLED", True, raising=False)
    monkeypatch.setattr(config, "NEWS_FOCUSED_FMP_ENABLED", True, raising=False)
    for name in (
        "NEWS_SOURCE_SEC_ENABLED", "NEWS_SOURCE_GDELT_ENABLED", "NEWS_SOURCE_YAHOO_ENABLED",
        "NEWS_SOURCE_GOOGLE_NEWS_ENABLED", "NEWS_SOURCE_NASDAQ_HALTS_ENABLED",
        "NEWS_SOURCE_FINANZEN_NET_ENABLED", "NEWS_SOURCE_ALPHA_VANTAGE_ENABLED",
        "NEWS_SOURCE_FINNHUB_ENABLED",
    ):
        monkeypatch.setattr(config, name, False, raising=False)
    monkeypatch.setattr(config, "NEWS_SOURCE_MAX_PER_PROVIDER", 10, raising=False)


def test_fmp_stable_symbol_search_behavior(monkeypatch, tmp_path):
    _only_fmp(monkeypatch, tmp_path)
    n = MultiSourceNews()
    # Replace the HTTP session so no network is possible.
    fake = FakeSession([FakeResponse([{
        "symbol": "AAPL", "name": "Apple Inc.", "exchange": "NASDAQ", "currency": "USD"
    }])])
    n.session = fake

    rows = n.fmp_search_symbol("aapl")

    assert rows[0]["symbol"] == "AAPL"
    call = fake.calls[0]
    assert call["url"].endswith("/stable/search-symbol")
    assert call["params"]["query"] == "AAPL"
    assert call["headers"]["apikey"] == "SECRET_FMP_KEY"
    assert "apikey" not in call["params"]


def test_fmp_focused_news_uses_current_search_endpoint_and_parses(monkeypatch, tmp_path):
    _only_fmp(monkeypatch, tmp_path)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    n = MultiSourceNews()
    fake = FakeSession([FakeResponse([{
        "symbol": "AAPL",
        "publishedDate": now,
        "title": "Apple publishes an update",
        "text": "Company-specific test news",
        "url": "https://example.test/aapl",
    }])])
    n.session = fake

    rows = n._fmp("AAPL", hours=48)

    assert len(rows) == 1
    assert rows[0].source == "FMP"
    assert "AAPL" in rows[0].symbols
    call = fake.calls[0]
    assert call["url"].endswith("/stable/news/stock")
    assert call["params"]["symbols"] == "AAPL"
    assert call["params"]["limit"] == 20  # Same shared request as PULSAR; age filtered locally.


def test_fmp_market_news_uses_stock_latest(monkeypatch, tmp_path):
    _only_fmp(monkeypatch, tmp_path)
    n = MultiSourceNews()
    fake = FakeSession([FakeResponse([])])
    n.session = fake

    assert n._fmp(None, hours=24, market=True) == []
    call = fake.calls[0]
    assert call["url"].endswith("/stable/news/stock-latest")
    assert call["params"]["page"] == 0


def test_fmp_429_enters_backoff_and_prevents_repeat(monkeypatch, tmp_path):
    _only_fmp(monkeypatch, tmp_path)
    n = MultiSourceNews()
    fake = FakeSession([FakeResponse({"message": "Too Many Requests"}, status=429, reason="Too Many Requests")])
    n.session = fake

    first = n.fetch_market(hours=24, universe=["AAPL"])
    assert "FMP" in first.sources_failed
    assert n._backoff_remaining("FMP") > 0
    assert len(fake.calls) == 1

    second = n.fetch_market(hours=24, universe=["AAPL"])
    assert "FMP" in second.sources_failed
    assert len(fake.calls) == 1, "Backoff muss den zweiten HTTP-Aufruf verhindern"


def test_fmp_error_text_never_contains_api_key(monkeypatch, tmp_path):
    _only_fmp(monkeypatch, tmp_path)
    n = MultiSourceNews()
    n.session = FakeSession([FakeResponse({"message": "invalid request"}, status=401, reason="Unauthorized")])
    try:
        n.fmp_search_symbol("AAPL")
    except RuntimeError as exc:
        text = str(exc)
    else:
        raise AssertionError("HTTP 401 muss als RuntimeError sichtbar werden")
    assert "SECRET_FMP_KEY" not in text
