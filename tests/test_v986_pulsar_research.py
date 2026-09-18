"""Exercise discovery, real tool receipts, primary-text gates and UI migration offline."""
from copy import deepcopy
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace as NS
import json
import time

import pytest
from pulsar import control, research, sources, evidence, worker


def fixture_document(now=None):
    now = time.time() if now is None else now
    date = datetime.fromtimestamp(now-3600, timezone.utc).isoformat()
    text = "Example Corporation announces financial results: revenue increased by 20 percent this quarter with positive operating cash flow."
    doc, _ = sources.primary_source("EXAM", {"url": "https://investors.example.com/results",
        "text": '<html><meta property="article:published_time" content="'+date+'"><body>'+text+'</body></html>',
        "observed_at": now}, date, {"type": "COMPANY_DOMAIN", "domain": "example.com",
        "company": "Example Corporation", "symbol": "EXAM"}, now=now)
    receipt = {"symbol": "EXAM", "event_type": "RESULTS", "source_ids": [doc["id"]],
        "document_hash": doc["document_hash"], "event_time": date, "excerpt": text, "summary": "Quartalsergebnisse"}
    return doc, receipt


def ready_card(now=None):
    now = time.time() if now is None else now
    doc, receipt = fixture_document(now)
    review = {"ok": True, "stufe": "terra", "source_hash": "current-proof", "daten": {"missing": [], "verdict": "REVIEW", "risks": [{}, {}, {}]}}
    return {"symbol": "EXAM", "sources": [doc, {"id": "sec-facts", "provider": "SEC", "kind": "companyfacts",
        "data": {"available": True, "symbol": "EXAM", "cik": 123, "symbol_identity_verified": True,
            "net_income": {"value": 100, "unit": "USD", "start": "2026-01-01", "end": "2026-06-30", "filed": "2026-08-01"},
            "operating_cashflow": {"value": 100, "unit": "USD", "start": "2026-01-01", "end": "2026-06-30", "filed": "2026-08-01"}}}],
        "verified_evidence": {"catalyst": receipt}, "baseline": {"ready_14d": True, "days": 14, "median_mentions": 20},
        "attention": {"mentions": 80, "observed_at": now}, "market": {"turnover_usd": 2e7},
        "bars": [{"close": 10}]*20, "analysis": deepcopy(review), "countercheck": deepcopy(review),
        "blocks": [], "missing": [], "evidence_hash": "current-proof"}


def test_discovery_pagination_and_overlapping_forums_never_sum_counts(monkeypatch):
    control.set_mode("BEOBACHTEN")
    calls = []
    def fetch(source, **kw):
        calls.append((kw["feed"], kw["page"]))
        if kw["feed"] == "investing":
            raise RuntimeError("unavailable")
        return [{"symbol": "EXAM", "source": source, "feed": kw["feed"],
                 "mentions": 100 if kw["feed"] == "all-stocks" else 30, "observed_at": kw["now"]}]
    monkeypatch.setattr(research, "fetch_social", fetch)
    found = research.discover()
    assert calls == [("all-stocks", 1), ("all-stocks", 2), ("all-stocks", 3), ("stocks", 1), ("investing", 1)]
    assert len(found) == 1 and found[0]["mentions"] == 100
    assert set(found[0]["feeds_seen"]) == {"all-stocks", "stocks"}
    assert not research.cached("coverage")["data"]["complete"]
    control.set_mode("AUS")
    research.discover()
    assert len(calls) == 5


def test_same_social_facts_keep_evidence_but_record_both_days():
    now = time.time()-86400
    raw = json.dumps({"pages": 1, "results": [{"ticker": "EXAM", "mentions": 60, "mentions_24h_ago": 20}]}).encode()
    resp = NS(status_code=200, headers={}, raise_for_status=lambda: None, close=lambda: None,
              iter_content=lambda **kw: [raw])
    session = NS(get=lambda *a, **kw: resp)
    a = research.fetch_social("apewisdom", now=now, session=session)
    b = research.fetch_social("apewisdom", now=now+86400, session=session)
    assert a[0]["evidence_id"] == b[0]["evidence_id"]
    assert a[0]["observed_at"] != b[0]["observed_at"]
    with research.db() as con:
        assert con.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 2


def test_per_forum_baseline_is_not_mixed_with_aggregate():
    now = time.time()
    with research.db() as con:
        for i in range(1, 15):
            for src, mentions in (("apewisdom", 100), ("apewisdom:stocks", 10)):
                con.execute("INSERT INTO observations VALUES(?,?,?,?,?)", (src+str(i), src, now-i*86400,
                    "EXAM", json.dumps({"mentions": mentions})))
    assert research.baseline("EXAM", now=now)["median_mentions"] == 100
    assert research.baseline("EXAM", "apewisdom:stocks", now=now)["median_mentions"] == 10


@pytest.mark.parametrize("fault", ["quote", "symbol", "hash", "domain", "old", "future", "model_boolean"])
def test_forged_or_stale_catalyst_does_not_unlock_score(fault):
    now = time.time(); card = ready_card(now)
    r = card["verified_evidence"]["catalyst"]
    if fault == "quote": r["excerpt"] = "Invented earnings increased "*5
    if fault == "symbol": r["symbol"] = "OTHER"
    if fault == "hash": card["sources"][0]["data"]["text"] += "tampered"
    if fault == "domain": card["sources"][0]["url"] = "https://unrelated.example/press"
    if fault == "old": now += 73*3600
    if fault == "future": now -= 7200
    if fault == "model_boolean": card["verified_evidence"]["catalyst"] = {"primary_source": True, "material_event": True}
    out = evidence.evaluate(card, now=now)
    assert out["score"] is None and not out["eligible"]


def hype_card(now=None):
    """10.3.0: kleinste eligible Hype-Karte (Spike + Kurs-/Volumenbestaetigung)."""
    now = time.time() if now is None else now
    start = datetime.fromtimestamp(now, timezone.utc).date() - timedelta(days=23)
    bars = []
    for i in range(21):
        close = 10.0 if i < 20 else 10.6
        volume = 1e6 if i < 20 else 3.5e6
        bars.append({"date": (start+timedelta(days=i)).isoformat(), "close": close, "volume": volume})
    return {"symbol": "EXAM", "attention": {"symbol": "EXAM", "source": "apewisdom",
        "mentions": 150, "mentions_24h_ago": 40, "observed_at": now-600},
        "bars": bars, "intraday": [], "blocks": [], "missing": [], "precheck": {},
        "sources": [], "evidence_hash": "current-proof"}


def test_hype_criteria_replace_score_and_terra_gates():
    now = time.time()
    out = evidence.evaluate(hype_card(now), now=now)
    assert out["eligible"] and out["state"] == "HYPE_KANDIDAT" and out["score"] is None
    # Die alte Score-100-Karte (Katalysator+Finanzen+Terra) ist ohne
    # Kursbestaetigung NICHT eligible -- ein Beleg ersetzt keinen Kursbeweis.
    old = ready_card(now)
    assert not evidence.evaluate(old, now=now)["eligible"]


def test_rule_migration_requires_new_temporal_confirmation():
    now = time.time(); card = hype_card(now); card.update(evidence.evaluate(card, now=now))
    assert card["eligible"]
    research.save_assessment({**card, "rules_version": "PULSAR-1.0"}, now=now-3600)
    research.save_assessment(dict(card), now=now)
    assert research.stable_candidate("EXAM", now=now) is None
    research.save_assessment(dict(card), now=now-900)
    assert research.stable_candidate("EXAM", now=now)


def test_sec_submissions_bind_cik_symbol_document_and_exhibit(monkeypatch):
    import config
    now = time.time(); doc, _ = fixture_document(now)
    monkeypatch.setattr(config, "SEC_USER_AGENT_EMAIL", "research@example.com")
    recent = {"cik": "123", "tickers": ["EXAM"], "name": "Example Corporation", "filings": {"recent": {
        "form": ["8-K"], "acceptanceDateTime": [doc["data"]["published_at"]],
        "accessionNumber": ["0000000123-26-000001"], "primaryDocument": ["form8k.htm"]}}}
    calls = []
    def fetch(url, **kw):
        calls.append(url)
        body = json.dumps(recent) if "submissions" in url else '<body>'+doc["data"]["text"]+'<a href="ex991.htm">99.1</a></body>'
        return {"url": url, "text": body, "observed_at": now}
    monkeypatch.setattr(sources, "fetch", fetch)
    market = {"sources": [{"kind": "companyfacts", "data": {"symbol": "EXAM", "cik": 123}}]}
    found, errors = sources.sec_documents("EXAM", market, now=now)
    assert len(found) == 2 and not errors and len(calls) == 3
    assert all(s["data"]["identity"]["cik"] == 123 for s in found)
    recent["tickers"] = ["OTHER"]
    found, errors = sources.sec_documents("EXAM", market, now=now)
    assert not found and errors


def test_http_redirect_cannot_leave_allowed_domains(monkeypatch):
    import requests
    control.set_mode("BEOBACHTEN")
    calls = []
    response = NS(status_code=302, headers={"Location": "https://127.0.0.1/secrets"}, close=lambda: None)
    client = NS(get=lambda *a, **kw: calls.append((a, kw)) or response, close=lambda: None)
    monkeypatch.setattr(requests, "Session", lambda: client)
    monkeypatch.setattr(sources, "_public_host", lambda host: None)
    with pytest.raises(control.Blocked):
        sources.fetch("https://example.com/release", domains=["example.com"])
    assert len(calls) == 1 and calls[0][1]["allow_redirects"] is False
    assert "Authorization" not in calls[0][1]["headers"]


@pytest.mark.parametrize("url", ["http://example.com", "https://example.com.evil.org/x", "https://user@example.com", "https://example.com:81/a", "https://127.0.0.1/a"])
def test_document_url_rejects_unsafe_or_unrelated_addresses(url):
    assert not sources.allowed_url(url, ["example.com"])


def test_web_tool_receipts_ignore_model_invented_urls():
    from ai_router import web_receipts
    calls, urls = web_receipts({"output_text": '{"urls":["https://invented.example"]}', "output": [
        {"type": "web_search_call", "status": "completed", "action": {"sources": [{"url": "https://example.com/release"}]}}]})
    assert calls == 1 and [u["url"] for u in urls] == ["https://example.com/release"]


class SearchRouter:
    aktiv = True
    def __init__(self, now=None):
        self.calls = []; self.now = time.time() if now is None else now
    def preise(self, tier): return .20, 1.20
    def waehle_stufe(self, task): return ("terra" if task in {"pulsar_analysis", "pulsar_countercheck"} else "luna"), "fixture"
    def frage(self, task, payload, schema, **kw):
        from ai_router import AIAntwort
        self.calls.append((task, deepcopy(payload)))
        if task == "pulsar_web_research":
            return AIAntwort(ok=True, stufe="luna", kosten=.0105, usage_confirmed=True, web_calls=1,
                web_sources=[{"url": "https://investors.example.com/results"}], daten={"symbol": payload["symbol"],
                "urls": ["https://investors.example.com/results", "https://example.com/invented"],
                "summary": "Ergebnisse gefunden", "counterevidence": "Keine weiteren Gegenbelege gefunden; keine Entwarnung."})
        if task == "pulsar_event_check":
            doc = payload["documents"][0]
            return AIAntwort(ok=True, stufe="luna", kosten=.001, usage_confirmed=True, daten={
                "symbol": payload["symbol"], "event_type": "RESULTS", "source_id": doc["id"],
                "excerpt": doc["text"], "summary": "Quartalszahlen"})
        def answer(p):
            claim = {"text": "Unternehmensergebnisse prüfen", "source_ids": [p["sources"][-1]["id"]]}
            return {"symbol": p["symbol"], "thesis": claim, "risks": [claim]*3, "missing": [], "verdict": "REVIEW"}
        return AIAntwort(ok=True, stufe=self.waehle_stufe(task)[0], kosten=.001, usage_confirmed=True,
            daten={"notes": [answer(p) for p in payload["candidates"]]} if task == "pulsar_precheck" else answer(payload))


def test_selective_gpt_search_cache_quota_and_tool_cost(monkeypatch):
    control.set_mode("BEOBACHTEN")
    market = {"profile": {"companyName": "Example Corporation", "website": "https://example.com"}}
    r = SearchRouter(); first = sources.web_research("EXAM", market, r)
    assert first["urls"] == ["https://investors.example.com/results"]
    assert sources.web_research("EXAM", market, r)["cached"] and len(r.calls) == 1
    sources.web_research("OTHER", market, r)
    with pytest.raises(control.Blocked): sources.web_research("THIRD", market, r)
    assert research.usage_summary()["day"]["ai"] == pytest.approx(.021)
    assert len(r.calls) == 2
    control.set_mode("AUS")
    assert not sources.web_research("NEVER", market, r)["ok"] and len(r.calls) == 2


def test_actual_router_requires_web_tool_and_accounts_tool_fees(monkeypatch):
    import ai_router, ai_control
    control.set_mode("BEOBACHTEN")
    monkeypatch.setattr(ai_control, "read_mode", lambda: "ON")
    cfg = NS(AI_ROUTER_ENABLED=True, OPENAI_API_KEY="test-only", AI_WEB_SEARCH_ENABLED=False)
    router = ai_router.AIRouter(cfg)
    monkeypatch.setattr(router, "waehle_stufe", lambda *a: ("luna", "fixture"))
    captured = []; bills = []
    monkeypatch.setattr(router.budget, "reserviere", lambda *a: ("reservation", ""))
    monkeypatch.setattr(router.budget, "abschliessen", lambda *a, **kw: bills.append(kw))
    body = {"status": "completed", "output_text": json.dumps({"symbol": "EXAM", "summary": "Found",
        "urls": [], "counterevidence": "Unknown"}), "usage": {"input_tokens": 100, "output_tokens": 50},
        "output": [{"type": "web_search_call", "status": "completed", "action": {"sources": []}}]}
    monkeypatch.setattr(ai_router, "_post_bounded", lambda *a, **kw: captured.append(kw["json"]) or
        NS(ok=True, content=b"body", json=lambda: deepcopy(body)))
    result = router.frage("pulsar_web_research", {"symbol": "EXAM", "website": "https://example.com"},
        sources.SEARCH_SCHEMA, cache_erlaubt=False)
    assert result.ok and result.usage_confirmed and result.kosten == pytest.approx(.01008)
    assert bills[-1]["kosten"] == pytest.approx(.01008)
    payload = captured[0]
    assert payload["max_tool_calls"] == 2 and payload["tool_choice"] == "required"
    assert payload["include"] == ["web_search_call.action.sources"]
    assert payload["tools"][0]["filters"]["allowed_domains"] == ["sec.gov", "fda.gov", "example.com"]
    body["output"] = []
    assert not router.frage("pulsar_web_research", {"symbol": "EXAM"}, sources.SEARCH_SCHEMA, cache_erlaubt=False).ok


def test_worker_gpt_search_original_document_event_and_two_reviews(monkeypatch):
    import ai_router
    from test_v985_pulsar_flow_and_pages import seed_profiles
    control.set_mode("BEOBACHTEN")
    now = time.time(); doc, receipt = fixture_document(now)
    router = SearchRouter(now)
    attention = {"symbol": "EXAM", "source": "apewisdom", "mentions": 80, "mentions_24h_ago": 20,
                 "observed_at": now, "url": research.URLS["apewisdom"], "evidence_id": "social"}
    market = {"profile": {"symbol": "EXAM", "currency": "USD", "companyName": "Example Corporation", "website": "https://example.com", "price": 10,
                           "marketCap": 1e9, "isEtf": False, "isFund": False}, "bars": [{"date": (datetime.now(timezone.utc)-timedelta(days=i)).date().isoformat(),
                           "close": 10, "volume": 2e6} for i in range(1, 25)], "sources": [], "errors": []}
    monkeypatch.setattr(research, "discover", lambda: [attention])
    monkeypatch.setattr(worker, "gather_market", lambda symbol, **kwargs: deepcopy(market))
    seed_profiles(monkeypatch, {"EXAM": market["profile"]})
    monkeypatch.setattr(sources, "sec_documents", lambda *a, **kw: ([], ["SEC nicht konfiguriert"]))
    monkeypatch.setattr(sources, "fetch", lambda url, **kw: {"url": url, "observed_at": now,
        "text": '<meta property="article:published_time" content="'+receipt["event_time"]+'"><body>'+receipt["excerpt"]+'</body>'})
    monkeypatch.setattr(ai_router, "AIRouter", lambda: router)
    w = worker.Worker(); w.session = control.start_session(); w.cycle(control.settings())
    card = research.latest_cards()[0]
    # 10.3.0: Keine Terra-Analyse/Gegenpruefung mehr; die Ereignisrecherche
    # (Websuche + Originaldokument) bleibt als Belegquelle fuer die Anzeige.
    assert [task for task, _ in router.calls] == ["pulsar_precheck", "pulsar_web_research", "pulsar_event_check"]
    assert card["verified_evidence"]["catalyst"]["excerpt"] == receipt["excerpt"]
    assert not card["eligible"] and not control.proposals()  # Kein Kursspike, Observe mode.
    w.cycle(control.settings())
    assert sum(t == "pulsar_web_research" for t, _ in router.calls) == 1
    assert sum(t == "pulsar_event_check" for t, _ in router.calls) == 1


def test_web_settings_persist_and_old_green_cards_are_not_new_approvals():
    from pulsar.presentation import snapshot
    control.set_mode("BEOBACHTEN", web_search=False)
    control.start_session()
    assert control.settings()["web_search"] == 0
    research.cache_put("top5", [{"symbol": "EXAM", "text_source": "TERRA", "score": 100,
                               "eligible": True, "rules_version": "PULSAR-1.0"}], 3600)
    card = snapshot()["cards"][0]
    assert not card["eligible"] and card["score"] is None and card["analysis"] == {}


def test_uncertain_gpt_bill_keeps_reservation_and_failure_cooldown():
    from ai_router import AIAntwort, pulsar_web_reserve
    control.set_mode("BEOBACHTEN")
    router = SearchRouter()
    calls = []
    router.frage = lambda *a, **kw: calls.append(a) or AIAntwort(grund="Timeout, Ausgang unbekannt")
    market = {"profile": {"companyName": "Example Corporation", "website": "https://example.com"}}
    assert not sources.web_research("EXAM", market, router)["ok"]
    assert not sources.web_research("EXAM", market, router)["ok"]
    assert len(calls) == 1
    used = research.usage_summary()["day"]
    assert used["ai"] == pytest.approx(pulsar_web_reserve(.20, 1.20))
    assert used["web_search"] == 2


def test_luna_reject_blocks_even_a_complete_hype_card():
    now = time.time(); card = hype_card(now)
    card["precheck"] = {"ok": True, "stufe": "luna",
                        "daten": {"verdict": "REJECT", "missing": []}}
    out = evidence.evaluate(card, now=now)
    assert not out["eligible"]
    assert any("Luna" in b for b in out["blocks"])


def test_original_document_requires_publish_metadata_not_gpt_guessed_date(monkeypatch):
    now = time.time(); doc, _ = fixture_document(now)
    market = {"profile": {"companyName": "Example Corporation", "website": "https://example.com"}}
    monkeypatch.setattr(sources, "fetch", lambda url, **kw: {"url": url, "text": doc["data"]["text"], "observed_at": now})
    found, errors = sources.corporate_documents("EXAM", market, {"urls": [doc["url"]],
        "summary": "Allegedly published today"}, now=now)
    assert not found and errors
