import json
from unittest.mock import patch

import config
from universe_research import UniverseResearchAssistant
from universe_proposals import list_proposals
import approved_universe


class Response:
    ok=True; status_code=200; content=b"x"; text=""
    def __init__(self,data): self._data=data
    def json(self): return self._data


def test_research_filters_existing_and_bad_sources_and_never_approves(monkeypatch,tmp_path):
    monkeypatch.setenv("TRADINGBOT_TEST_STATE_DIR",str(tmp_path))
    monkeypatch.setattr(config,"OPENAI_API_KEY","key")
    monkeypatch.setattr(config,"AI_ROUTER_ENABLED",True)
    monkeypatch.setattr(config,"AI_RESEARCH_ENABLED",True)
    monkeypatch.setattr(config,"AI_RESEARCH_MIN_PROPOSALS",1)
    candidates=[
        {"symbol":"AAPL","company":"Apple","sector":"Tech","rationale":"already there","sources":[{"title":"1","url":"https://one.example/a"},{"title":"2","url":"https://two.example/b"}]},
        {"symbol":"XYZ","company":"Example","sector":"Industrie","rationale":"new","sources":[{"title":"1","url":"https://one.example/x"},{"title":"2","url":"https://two.example/y"}]},
        {"symbol":"BAD","company":"Bad","sector":"Tech","rationale":"same-domain","sources":[{"title":"1","url":"https://same.example/a"},{"title":"2","url":"https://same.example/b"}]},
    ]
    payload={"candidates":candidates,"portfolio_observation":"test"}
    data={"output_text":json.dumps(payload)}
    seen={}
    def fake_post(url,headers,json,timeout):
        seen["payload"]=json
        return Response(data)
    with patch("ai_router.requests.post",side_effect=fake_post):
        a=UniverseResearchAssistant(); rows=a.generate([{"symbol":"AAPL","sector":"Tech"}])
    assert [r["symbol"] for r in rows]==["XYZ"]
    assert list_proposals()[0]["status"]=="PROPOSED"
    assert not approved_universe.path().exists()
    assert seen["payload"]["tools"][0]["type"]=="web_search"


def test_source_validation_rejects_local_and_ip_hosts():
    from universe_proposals import valid_sources
    assert valid_sources([
        {"title":"local","url":"https://localhost/a"},
        {"title":"ip","url":"https://127.0.0.1/b"},
        {"title":"ok","url":"https://example.com/c"},
    ]) == []
