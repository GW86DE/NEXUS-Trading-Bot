"""Offline receipt lifecycle: expired analysis never becomes a trade or cache."""
import json
import threading
from types import SimpleNamespace as NS

import pytest

import ai_router
from pulsar import analysis, control, research, sources, worker

SCHEMA = {"type": "object", "properties": {"value": {"type": "string"}},
          "required": ["value"], "additionalProperties": False}


def router(monkeypatch):
    import ai_control
    monkeypatch.setattr(ai_control, "read_mode", lambda: "AUTO")
    control.set_mode("BEOBACHTEN")
    cfg = NS(AI_ROUTER_ENABLED=True, OPENAI_API_KEY="offline-fixture",
             AI_MAX_COST_PER_DAY_USD=2, AI_LUNA_MAX_CALLS_PER_DAY=100)
    return ai_router.AIRouter(cfg)


def response(text='{"value":"valid"}', *, usage=True, web=False):
    body = {"id": "offline-response", "status": "completed", "output_text": text}
    if usage:
        body["usage"] = {"input_tokens": 100, "output_tokens": 50}
    if web:
        body["output"] = [{"type": "web_search_call", "status": "completed",
                           "action": {"sources": []}}]
    return NS(content=b"fixture", ok=True, status_code=200, json=lambda: body)


def test_late_usage_settles_both_budgets_once_without_adopting_text(monkeypatch):
    client = router(monkeypatch)
    token = research.reserve("ai", .1)
    release, settled = threading.Event(), threading.Event()
    finalizations = []
    original = client.budget.abschliessen
    def finalize(*args, **kw):
        finalizations.append(kw)
        return original(*args, **kw)
    monkeypatch.setattr(client.budget, "abschliessen", finalize)
    monkeypatch.setattr(ai_router.requests, "post",
                        lambda *a, **kw: release.wait(2) and response())
    def callback(receipt):
        assert receipt["late"] and "daten" not in receipt
        research.settle(token, receipt["kosten"])
        settled.set()
    try:
        answer = client.frage("pulsar_precheck", {}, SCHEMA, timeout_seconds=.02,
                              cache_erlaubt=False, usage_callback=callback)
        assert not answer.ok and "Zeitrahmen" in answer.grund
        assert research.usage_summary()["day"]["ai"] == pytest.approx(.1)
        release.set()
        assert settled.wait(2)
        assert research.usage_summary()["day"]["ai"] == pytest.approx(.00008)
        assert len(finalizations) == 1
        assert not answer.ok and answer.daten == {}
        assert control.proposals() == []
        assert not client.cache.datei.exists()
    finally:
        release.set()


def test_invalid_json_with_usage_is_accounted_once(monkeypatch):
    client = router(monkeypatch)
    token = research.reserve("ai", .1)
    monkeypatch.setattr(ai_router, "_post_bounded", lambda *a, **kw: response("invalid"))
    result = client.frage("pulsar_precheck", {}, SCHEMA, cache_erlaubt=False,
                          usage_callback=analysis._usage_callback(token))
    assert not result.ok and result.usage_confirmed
    assert research.usage_summary()["day"]["ai"] == pytest.approx(.00008)
    research.settle(token, result.kosten)
    with pytest.raises(control.Blocked, match="Widerspruechliche"):
        research.settle(token, 0)


def test_unknown_response_keeps_reservation(monkeypatch):
    client = router(monkeypatch)
    token = research.reserve("ai", .1)
    def failed(*a, **kw):
        raise TimeoutError("offline unknown")
    monkeypatch.setattr(ai_router, "_post_bounded", failed)
    result = client.frage("pulsar_precheck", {}, SCHEMA,
                          usage_callback=analysis._usage_callback(token))
    assert not result.ok and not result.usage_confirmed
    assert research.usage_summary()["day"]["ai"] == pytest.approx(.1)


@pytest.mark.parametrize("blocked", ["disabled", "budget", "transport"])
def test_proven_not_sent_releases_only_its_reservation(monkeypatch, blocked):
    client = router(monkeypatch)
    token = research.reserve("ai", .1)
    calls = []
    def never(*a, **kw):
        calls.append(1)
        raise ai_router.AIRequestNotSent("busy")
    monkeypatch.setattr(ai_router, "_post_bounded", never)
    if blocked == "disabled": client.cfg.AI_ROUTER_ENABLED = False
    if blocked == "budget": client.cfg.AI_LUNA_MAX_CALLS_PER_DAY = 0
    result = client.frage("pulsar_precheck", {}, SCHEMA,
                          usage_callback=analysis._usage_callback(token))
    assert not result.ok
    assert research.usage_summary()["day"]["ai"] == 0
    assert len(calls) == (1 if blocked == "transport" else 0)


def test_one_batch_has_90_seconds_and_persistent_error_cooldown(monkeypatch):
    from test_v985_pulsar_flow_and_pages import answer
    client = router(monkeypatch)
    calls = []
    packets = [{"symbol": s, "sources": [{"id": s+"-receipt"}]} for s in ["A", "B", "C", "D", "E"]]
    def failed(task, payload, schema, **kwargs):
        calls.append((task, payload, kwargs))
        return ai_router.AIAntwort(grund="Timeout")
    monkeypatch.setattr(client, "frage", failed)
    assert not analysis.precheck(packets, client)["ok"]
    assert not analysis.precheck(packets, client)["ok"]
    assert len(calls) == 1 and len(calls[0][1]["candidates"]) == 5
    assert calls[0][2]["timeout_seconds"] == 90
    assert research.cached("ai:precheck:retry_after")
    with research.db() as con:
        con.execute("DELETE FROM cache WHERE key='ai:precheck:retry_after'")
    monkeypatch.setattr(client, "frage", lambda task, payload, schema, **kw: ai_router.AIAntwort(
        ok=True, stufe="luna", daten={"notes": [answer(p) for p in payload["candidates"]]}))
    assert analysis.precheck(packets, client)["ok"]


def test_mode_change_discards_valid_precheck(monkeypatch):
    from test_v985_pulsar_flow_and_pages import answer
    client = router(monkeypatch)
    packet = {"symbol": "EXAM", "sources": [{"id": "receipt"}]}
    def changed(*args, **kwargs):
        control.set_mode("AUS")
        return ai_router.AIAntwort(ok=True, stufe="luna", daten={"notes": [answer(packet)]})
    monkeypatch.setattr(client, "frage", changed)
    result = analysis.precheck([packet], client)
    assert not result["ok"] and "Modus" in result["grund"]
    assert control.proposals() == []


def test_late_web_usage_reconciles_cost_and_tool_quota(monkeypatch):
    client = router(monkeypatch)
    callbacks = []
    def late(*a, **kw):
        callbacks.append(kw["on_late_response"])
        raise TimeoutError("offline late")
    monkeypatch.setattr(ai_router, "_post_bounded", late)
    result = sources.web_research("EXAM", {"profile": {"companyName": "Example", "website": "https://example.com"}}, client)
    assert not result["ok"]
    assert research.usage_summary()["day"]["web_search"] == 2
    callbacks[0](response(web=True))
    assert research.usage_summary()["day"]["web_search"] == 1
    assert research.usage_summary()["day"]["ai"] == pytest.approx(.01008)
    with research.db() as con:
        assert con.execute("SELECT count(*) FROM cache WHERE key LIKE 'web:%'").fetchone()[0] == 0


def test_heartbeat_runs_during_blocked_research_and_stops(monkeypatch):
    # Accelerate only the heartbeat wait, not the shared time module.
    beat, finish = threading.Event(), threading.Event()
    class QuickEvent:
        def __init__(self): self.event = threading.Event()
        def clear(self): self.event.clear()
        def set(self): self.event.set()
        def wait(self, seconds): return self.event.wait(min(seconds, .01))
    w = worker.Worker()
    w.stop_event = QuickEvent()
    monkeypatch.setattr(w, "_run", lambda: finish.wait(2))
    actual = control.heartbeat
    monkeypatch.setattr(control, "heartbeat", lambda session: (actual(session), beat.set()))
    try:
        w.start()
        assert beat.wait(1) and w.thread.is_alive()
        finish.set()
        w.stop()
        assert not w.thread.is_alive() and not w.heartbeat_thread.is_alive()
    finally:
        finish.set(); w.stop()
