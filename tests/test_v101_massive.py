"""Behavioral regression: six-call burst, concurrent workers, restart and 429.

All HTTP responses are fakes; rate-limit time is injected. Multiprocess tests
use real SQLite transactions, and no test sleeps for provider limits.
"""
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from datetime import datetime, timezone
from email.utils import format_datetime
import json
import os
import sqlite3
import time

import pytest
import requests

from massive_api import MASSIVE_BASE, MassiveClient
from massive_service import MassivePaused, Store, readonly_status


class Clock:
    def __init__(self, value=1800000000.0): self.value = value
    def __call__(self): return self.value


class Response:
    def __init__(self, data=None, status=200, headers=None):
        self.data = {"status": "OK", "results": [{"title": "News", "description": "Details"}]} if data is None else data
        self.status_code = status
        self.headers = headers or {}
        self.closed = False
    def json(self): return self.data
    def close(self): self.closed = True


class Session:
    def __init__(self, *responses): self.responses = list(responses); self.calls = []; self.headers = {}
    def get(self, url, **kw):
        self.calls.append((url, kw))
        assert self.responses, "unexpected HTTP request"
        result = self.responses.pop(0)
        if isinstance(result, Exception): raise result
        return result


def client(path, clock, responses=(), key="FAKE-MASSIVE-KEY", limit=0):
    store = Store(key, MASSIVE_BASE, limit, path=path, clock=clock)
    instance = MassiveClient(key, store=store)
    instance.session = Session(*responses)
    return instance


def test_original_six_call_burst_is_locally_limited_not_six_http_calls(tmp_path):
    clock = Clock(); path = tmp_path / "massive.sqlite"; sessions = []; errors = []
    for symbol in ("MU", "SPY", "ORCL", "NVDA", "NBIS", "MARKET"):
        c = client(path, clock, [Response()]); sessions.append(c.session)
        try: c.news(symbol, 5)
        except MassivePaused as exc: errors.append(exc)
    assert sum(len(s.calls) for s in sessions) == 1
    assert len(errors) == 5 and all(e.retry_after == 15 for e in errors)
    assert c.status()["budget"]["minute_used"] == 1


def test_first_upgrade_waits_one_unknown_minute_without_resetting_on_retry(tmp_path):
    clock = Clock(); path = tmp_path / "massive_service.sqlite"
    (tmp_path / "news_source_status.json").write_text('{"MASSIVE":{"ok":true}}')
    first = client(path, clock)
    with pytest.raises(MassivePaused, match="Upgrade-Pause"): first.news("ABC")
    until = clock.value + 60
    clock.value += 30
    second = client(path, clock)
    with pytest.raises(MassivePaused): second.news("ABC")
    assert second.status()["budget"]["blocked_until"] == until
    clock.value = until
    third = client(path, clock, [Response()])
    assert third.news("ABC") and third.status()["budget"]["verbraucht"] == 1


def test_rolling_minute_boundary_and_gap_preserved(tmp_path):
    clock = Clock(); c = client(tmp_path / "x.sqlite", clock, [Response() for _ in range(5)])
    start = clock.value
    for i in range(4):
        clock.value = start + i * 15
        c.news(str(i))
    clock.value = start + 59.999
    with pytest.raises(MassivePaused): c.news("fifth")
    clock.value = start + 60
    c.news("fifth")
    assert len(c.session.calls) == 5 and c.status()["budget"]["minute_used"] == 4


def test_new_instances_and_keys_do_not_reset_shared_rate_window(tmp_path):
    clock = Clock(); path = tmp_path / "x.sqlite"
    client(path, clock, [Response()]).news("ABC")
    with pytest.raises(MassivePaused): client(path, clock, key="OTHER-KEY").news("DEF")


def test_persisted_cache_serves_common_consumers_without_new_http(tmp_path):
    clock = Clock(); path = tmp_path / "x.sqlite"
    raw = {"status": "OK", "results": [{"title": str(i), "description": "Body", "article_url": "https://example.test/" + str(i),
             "published_utc": "2026-09-13T10:00:00Z"} for i in range(20)]}
    first = client(path, clock, [Response(raw)])
    assert len(first.news("ABC", 10)) == 10
    second = client(path, clock)
    assert second.news("ABC", 5) == raw["results"][:5]
    assert second.news("ABC", 1, cache_only=True) == raw["results"][:1]
    assert second.status()["budget"]["verbraucht"] == 1
    assert second.status()["budget"]["cache_hits"] == 2
    assert second.status()["transport"]["http_request_started"] is False
    assert second.status()["transport"]["saved_at"] == clock.value


def test_cache_only_miss_never_reserves_or_sends(tmp_path):
    clock = Clock(); c = client(tmp_path / "x.sqlite", clock)
    with pytest.raises(MassivePaused, match="MASSIVE_CACHE_MISS"):
        c.news("ABC", cache_only=True)
    assert c.status()["budget"]["verbraucht"] == 0 and not c.session.calls


def test_expired_or_other_key_cache_is_never_silently_reused(tmp_path):
    clock = Clock(); path = tmp_path / "x.sqlite"
    client(path, clock, [Response()]).news("ABC")
    clock.value += 1801
    other = client(path, clock, key="OTHER")
    with pytest.raises(MassivePaused, match="CACHE_MISS"): other.news("ABC", cache_only=True)
    with pytest.raises(MassivePaused, match="CACHE_MISS"): client(path, clock).news("ABC", cache_only=True)


def _reserve_child(args):
    path, index, clock = args
    store = Store("K", MASSIVE_BASE, path=path, clock=lambda: clock)
    try:
        token, _, _ = store.acquire("/v2/reference/news", {"ticker": str(index)})
        return ("allowed", token)
    except MassivePaused as exc:
        return ("paused", exc.code)


def test_real_processes_cannot_race_the_atomic_reservation(tmp_path):
    path = tmp_path / "x.sqlite"
    now = 1800000000.0
    Store("K", MASSIVE_BASE, path=path, clock=lambda: now).status()
    with ProcessPoolExecutor(max_workers=4) as pool:
        outcomes = list(pool.map(_reserve_child, [(str(path), i, now) for i in range(12)]))
    assert sum(x[0] == "allowed" for x in outcomes) == 1
    assert Store("K", MASSIVE_BASE, path=path, clock=lambda: now).status()["verbraucht"] == 1


def test_simultaneous_identical_call_is_deduped_then_cache_reused(tmp_path):
    import threading
    clock = Clock(); path = tmp_path / "x.sqlite"; start = threading.Event(); finish = threading.Event()
    first = client(path, clock)
    class SlowSession(Session):
        def get(self, url, **kw):
            self.calls.append((url, kw)); start.set()
            assert finish.wait(5)
            return Response()
    first.session = SlowSession()
    with ThreadPoolExecutor(max_workers=2) as pool:
        one = pool.submit(first.news, "ABC", 5)
        try:
            assert start.wait(5)
            with pytest.raises(MassivePaused, match="INFLIGHT"):
                client(path, clock).news("ABC", 5)
        finally: finish.set()
        assert one.result()
    assert client(path, clock).news("ABC", 5) and len(first.session.calls) == 1


def test_restart_after_reservation_retains_charge_and_lease(tmp_path):
    path = tmp_path / "x.sqlite"; clock = Clock()
    store = Store("FAKE-MASSIVE-KEY", MASSIVE_BASE, path=path, clock=clock)
    store.acquire("/v2/reference/news", {"ticker": "ABC"}, lease_seconds=40)
    restarted = Store("FAKE-MASSIVE-KEY", MASSIVE_BASE, path=path, clock=clock)
    clock.value += 20
    with pytest.raises(MassivePaused, match="INFLIGHT"):
        restarted.acquire("/v2/reference/news", {"ticker": "ABC"})
    clock.value += 21
    assert restarted.acquire("/v2/reference/news", {"ticker": "ABC"})[0]
    assert restarted.status()["verbraucht"] == 2


@pytest.mark.parametrize("retry", ["120", "Sun, 15 Jan 2027 08:02:00 GMT", "invalid"])
def test_429_is_shared_and_honors_retry_after_even_after_restart(tmp_path, retry):
    clock = Clock(); path = tmp_path / "x.sqlite"
    if retry.startswith("Sun"):
        retry = format_datetime(datetime.fromtimestamp(clock.value + 180, timezone.utc), usegmt=True)
    response = Response({}, 429, {"Retry-After": retry})
    first = client(path, clock, [response])
    with pytest.raises(MassivePaused, match="HTTP_429"): first.news("ABC")
    status = client(path, clock).status()["budget"]
    expected = 120 if retry == "120" else 60 if retry == "invalid" else 180
    assert status["blocked_until"] == clock.value + expected
    clock.value += expected - 1
    with pytest.raises(MassivePaused): client(path, clock, key="OTHER").news("DEF")
    clock.value += 1
    second = client(path, clock, [Response()]); assert second.news("ABC")
    assert response.closed and second.status()["budget"]["verbraucht"] == 2


def test_valid_cache_remains_available_during_429_pause(tmp_path):
    clock = Clock(); path = tmp_path / "x.sqlite"
    client(path, clock, [Response()]).news("ABC")
    clock.value += 15
    with pytest.raises(MassivePaused): client(path, clock, [Response({}, 429)]).news("DEF")
    assert client(path, clock).news("ABC")


def test_timeout_is_charged_without_blind_retry(tmp_path):
    clock = Clock(); c = client(tmp_path / "x.sqlite", clock, [requests.Timeout("secret URL")])
    with pytest.raises(RuntimeError, match="Timeout") as exc: c.news("ABC")
    assert "secret URL" not in str(exc.value)
    with pytest.raises(MassivePaused): c.news("ABC")
    assert len(c.session.calls) == 1 and c.status()["budget"]["verbraucht"] == 1


@pytest.mark.parametrize("status", [402, 403])
def test_denied_endpoint_does_not_disable_different_capability(tmp_path, status):
    clock = Clock(); c = client(tmp_path / "x.sqlite", clock, [Response({}, status), Response()])
    with pytest.raises(RuntimeError): c.news("ABC")
    clock.value += 15
    assert c.ticker_details("ABC")
    clock.value += 15
    with pytest.raises(MassivePaused, match="CAPABILITY"): c.news("DEF")
    assert len(c.session.calls) == 2


def test_ui_probe_uses_same_budget_and_does_not_claim_untested_reference(tmp_path):
    clock = Clock(); c = client(tmp_path / "x.sqlite", clock, [Response(), Response()])
    one = c.verbindungstest()
    assert one["ok"] is True and one["faehigkeiten"]["Referenzdaten"]["ok"] is None
    assert len(c.session.calls) == 1
    clock.value += 15
    two = c.verbindungstest()
    assert all(v["ok"] is True for v in two["faehigkeiten"].values())
    assert len(c.session.calls) == 2


def test_daily_cap_persists_and_zero_is_not_unlimited_minute_budget(tmp_path):
    clock = Clock(); path = tmp_path / "x.sqlite"
    client(path, clock, [Response()], limit=1).news("ABC")
    clock.value += 60
    with pytest.raises(MassivePaused, match="Tagesbudget"): client(path, clock, limit=1).news("DEF")
    assert client(path, clock, limit=0).status()["budget"]["minute_limit"] == 4


def test_clock_rollback_fails_closed_instead_of_zeroing_attempts(tmp_path):
    clock = Clock(); c = client(tmp_path / "x.sqlite", clock, [Response()])
    c.news("ABC"); clock.value -= 300
    with pytest.raises(MassivePaused, match="Systemzeit"): c.news("DEF")


def test_forward_wall_clock_jump_does_not_refill_actual_minute_budget(tmp_path):
    wall = Clock(); mono = Clock(100)
    store = Store("KEY", MASSIVE_BASE, path=tmp_path / "x.sqlite", clock=wall, monotonic=mono)
    c = MassiveClient("KEY", store=store); c.session = Session(Response(), Response())
    c.news("ABC")
    wall.value += 3600; mono.value += 1
    with pytest.raises(MassivePaused, match="Systemzeit"): c.news("DEF")
    assert store.status()["retry_after"] == 60 and len(c.session.calls) == 1
    wall.value += 60; mono.value += 60
    assert c.news("DEF") and len(c.session.calls) == 2


def test_clock_jump_does_not_shorten_server_retry_after(tmp_path):
    wall = Clock(); mono = Clock(100)
    store = Store("KEY", MASSIVE_BASE, path=tmp_path / "x.sqlite", clock=wall, monotonic=mono)
    c = MassiveClient("KEY", store=store); c.session = Session(Response({}, 429, {"Retry-After": "120"}), Response())
    with pytest.raises(MassivePaused): c.news("ABC")
    wall.value += 3601; mono.value += 1
    with pytest.raises(MassivePaused): c.news("DEF")
    assert store.status()["retry_after"] == 119
    wall.value += 61; mono.value += 61
    with pytest.raises(MassivePaused): c.news("DEF")
    assert store.status()["retry_after"] == 58
    wall.value += 58; mono.value += 58
    assert c.news("DEF")


def test_small_clock_slew_cannot_shorten_monotonic_spacing(tmp_path):
    wall = Clock(); mono = Clock(100)
    store = Store("KEY", MASSIVE_BASE, path=tmp_path / "x.sqlite", clock=wall, monotonic=mono)
    c = MassiveClient("KEY", store=store); c.session = Session(Response(), Response())
    c.news("ABC")
    wall.value += 14; mono.value += 13
    store.status()  # Small wall-clock adjustment, below the jump threshold.
    wall.value += 1; mono.value += 0.9
    with pytest.raises(MassivePaused): c.news("DEF")
    assert len(c.session.calls) == 1
    wall.value += 1.2; mono.value += 1.2
    assert c.news("DEF")


def test_oversized_stream_is_closed_and_never_cached(tmp_path):
    class Large(Response):
        def iter_content(self, **kw):
            yield b" " * (4 * 1024 * 1024)
            yield b"extra"
    response = Large(); c = client(tmp_path / "x.sqlite", Clock(), [response])
    with pytest.raises(RuntimeError, match="Zeitgrenze"): c.news("ABC")
    assert response.closed and c.status()["budget"]["last_http_success_at"] is None
    assert c.session.calls[0][1]["stream"] is True
    with sqlite3.connect(c.store.path) as con:
        assert con.execute("SELECT result FROM calls").fetchone()[0] == "RESPONSE_TOO_LARGE"


def test_stream_deadline_stops_slow_body_without_retry(tmp_path, monkeypatch):
    import massive_api
    from types import SimpleNamespace
    elapsed = [0]
    monkeypatch.setattr(massive_api, "time", SimpleNamespace(monotonic=lambda: elapsed[0]))
    class Slow(Response):
        def iter_content(self, **kw):
            yield b'{"status":"OK",'
            elapsed[0] = 100
            yield b'"results":[]}'
    response = Slow(); c = client(tmp_path / "x.sqlite", Clock(), [response])
    with pytest.raises(RuntimeError, match="RESPONSE_TIMEOUT"): c.news("ABC")
    assert response.closed and len(c.session.calls) == 1
    with sqlite3.connect(c.store.path) as con:
        assert con.execute("SELECT result FROM calls").fetchone()[0] == "RESPONSE_TIMEOUT"


def test_corrupt_store_never_falls_back_to_unrestricted_http(tmp_path):
    path = tmp_path / "x.sqlite"; path.write_bytes(b"not sqlite")
    c = client(path, Clock())
    with pytest.raises(MassivePaused, match="STATE_UNAVAILABLE"): c.news("ABC")
    assert path.read_bytes() == b"not sqlite" and not c.session.calls


def test_corrupt_clock_metadata_fails_closed_and_ui_stays_readable(tmp_path):
    clock = Clock(); c = client(tmp_path / "x.sqlite", clock); c.status()
    with sqlite3.connect(c.store.path) as con:
        con.execute("UPDATE meta SET value=? WHERE key='clock_guard'", ('{"unexpected":1}',))
    with pytest.raises(MassivePaused, match="STATE_UNAVAILABLE"): c.news("ABC")
    assert not c.session.calls
    status = readonly_status("FAKE-MASSIVE-KEY", path=c.store.path, clock=clock)
    assert status["state"] == "unknown" and status["verbraucht"] is None


def test_locked_store_is_bounded_and_never_sends(tmp_path):
    path = tmp_path / "x.sqlite"; c = client(path, Clock()); c.status()
    with sqlite3.connect(path) as locked:
        locked.execute("BEGIN EXCLUSIVE")
        start = time.monotonic()
        with pytest.raises(MassivePaused, match="STATE_UNAVAILABLE"): c.news("ABC")
        assert time.monotonic() - start < 2 and not c.session.calls


def test_readonly_status_does_not_create_modify_or_infer_unknown_counts(tmp_path):
    path = tmp_path / "x.sqlite"; clock = Clock()
    absent = readonly_status("KEY", path=path, clock=clock)
    assert absent["verbraucht"] is None and not path.exists()
    c = client(path, clock, [Response()]); c.news("ABC")
    before = path.read_bytes(); stamp = path.stat().st_mtime_ns
    status = readonly_status("FAKE-MASSIVE-KEY", path=path, clock=clock)
    assert status["verbraucht"] == 1 and status["last_http_success_at"] == clock.value
    assert path.read_bytes() == before and path.stat().st_mtime_ns == stamp


def test_no_plaintext_credentials_persisted_and_redirects_disabled(tmp_path):
    path = tmp_path / "x.sqlite"; c = client(path, Clock(), [Response()]); c.news("ABC")
    assert b"FAKE-MASSIVE-KEY" not in path.read_bytes()
    url, kw = c.session.calls[0]
    assert "FAKE-MASSIVE-KEY" not in url and kw["allow_redirects"] is False


def test_old_fmp_news_error_is_history_not_a_current_capability_failure(tmp_path, monkeypatch):
    from datetime import timedelta
    import news_sources as n
    now = datetime.now(timezone.utc)
    raw = {"FMP": {"ok": True, "time": now.isoformat(), "detail": "Stable News OK"},
           "FMP News": {"ok": False, "time": (now - timedelta(days=20)).isoformat(), "detail": "HTTP 402"},
           "FMP Symbol Search": {"ok": False, "time": now.isoformat(), "detail": "HTTP 403"}}
    status_path = tmp_path / "news.json"; status_path.write_text(json.dumps(raw))
    monkeypatch.setattr(n, "STATUS_FILE", status_path)
    source = n.MultiSourceNews()
    monkeypatch.setattr(source, "provider_configuration", lambda: {"FMP": True, "FMP Symbol Search": True})
    health = source.health_snapshot()
    assert health["FMP"]["state"] == "ok"
    assert health["FMP Symbol Search"]["state"] == "error"
    assert health["FMP News (historisch)"]["state"] == "stale"
    assert health["FMP News (historisch)"]["historical_alias"] is True
    assert health["FMP News (historisch)"]["endpoint"] == "UNKNOWN_LEGACY_NEWS"
    assert json.loads(status_path.read_text()) == raw


def test_local_pacing_pause_keeps_last_success_and_http_failure_count(tmp_path, monkeypatch):
    import news_sources as n
    status_path = tmp_path / "news.json"; monkeypatch.setattr(n, "STATUS_FILE", status_path)
    source = n.MultiSourceNews()
    monkeypatch.setattr(source, "provider_configuration", lambda: {"MASSIVE": True})
    source._mark("MASSIVE", True, "erreichbar", 5)
    before = json.loads(status_path.read_text())["MASSIVE"]
    def pause(): raise MassivePaused("Minutenbudget", 15, "MASSIVE_RATE_PAUSED")
    bundle = n.NewsBundle(); assert source._provider_call("MASSIVE", pause, bundle) == []
    after = json.loads(status_path.read_text())["MASSIVE"]
    assert after["last_success_at"] == before["last_success_at"] and after["time"] == before["time"]
    assert after["consecutive_failures"] == 0 and after["ok"] is True
    assert "MASSIVE" in bundle.sources_backoff and not bundle.sources_failed


def test_real_429_updates_http_failure_status_unlike_local_pacing(tmp_path, monkeypatch):
    import news_sources as n
    path = tmp_path / "news.json"; monkeypatch.setattr(n, "STATUS_FILE", path)
    source = n.MultiSourceNews()
    monkeypatch.setattr(source, "provider_configuration", lambda: {"MASSIVE": True})
    source._mark("MASSIVE", True, "erreichbar", 5)
    last_success = json.loads(path.read_text())["MASSIVE"]["last_success_at"]
    def rate_error(): raise MassivePaused("HTTP 429", 90, "MASSIVE_HTTP_429")
    bundle = n.NewsBundle(); source._provider_call("MASSIVE", rate_error, bundle)
    row = json.loads(path.read_text())["MASSIVE"]
    assert row["ok"] is False and row["failure_kind"] == "rate_limit"
    assert row["last_success_at"] == last_success and row["consecutive_failures"] == 1
    assert "MASSIVE" in bundle.sources_failed


def test_primary_fmp_news_skip_new_massive_calls_but_fill_real_gaps(tmp_path, monkeypatch):
    import news_sources as n
    source = n.MultiSourceNews(); calls = []
    monkeypatch.setattr(source, "provider_configuration", lambda: {"FMP": True, "MASSIVE": True})
    monkeypatch.setattr(source, "_load_status", lambda: {})
    date = datetime.now(timezone.utc)
    complete = [n.NewsItem("FMP", name, "News body", "https://example.test/" + str(i), date, ["ABC"])
                for i, name in enumerate(("Company files audited accounts", "Factory opened in new location", "Management announces acquisition"))]
    monkeypatch.setattr(source, "_fmp", lambda *a, **kw: complete)
    monkeypatch.setattr(source, "_massive", lambda *a, **kw: calls.append(kw.get("cache_only", False)) or [])
    source.fetch_market()
    assert calls == [True]
    complete[1].url = ""  # Missing source links are not complete primary evidence.
    source.fetch_market()
    assert calls == [True, False]


def test_three_duplicate_fmp_articles_do_not_suppress_massive_gap_request():
    import news_sources as n
    row = n.NewsItem("FMP", "Company announces the quarterly financial results", "News body",
                     "https://example.test/article", datetime.now(timezone.utc))
    assert not n.MultiSourceNews()._fmp_news_sufficient([row, row, row], 24)


def test_invalid_provider_payload_never_becomes_cached_success(tmp_path):
    clock = Clock(); c = client(tmp_path / "x.sqlite", clock, [Response([])])
    with pytest.raises(RuntimeError, match="Objektformat"): c.news("ABC")
    assert c.status()["budget"]["last_http_success_at"] is None
    assert c.status()["budget"]["verbraucht"] == 1


def test_older_failed_provider_status_is_stale_not_a_current_outage(tmp_path, monkeypatch):
    import news_sources as n
    from datetime import timedelta
    path = tmp_path / "news.json"
    path.write_text(json.dumps({"FMP": {"ok": False, "time": (datetime.now(timezone.utc) - timedelta(days=20)).isoformat(), "detail": "HTTP 402"}}))
    monkeypatch.setattr(n, "STATUS_FILE", path)
    source = n.MultiSourceNews()
    monkeypatch.setattr(source, "provider_configuration", lambda: {"FMP": True})
    status = source.health_snapshot()["FMP"]
    assert status["state"] == "stale" and status["healthy"] is False
