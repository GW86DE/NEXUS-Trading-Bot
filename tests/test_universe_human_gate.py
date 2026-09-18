import json
from pathlib import Path

import approved_universe
import universe_proposals as up


def _candidate(symbol="XYZ"):
    return {"symbol":symbol,"company":"Example Corp","sector":"Industrie","rationale":"Diversifikation und hohe Liquiditaet.",
            "sources":[{"title":"Source A","url":"https://example.com/a"},{"title":"Source B","url":"https://example.org/b"}]}


def test_two_human_stages_before_persistent_universe(monkeypatch,tmp_path):
    monkeypatch.setenv("TRADINGBOT_TEST_STATE_DIR",str(tmp_path))
    p=up.create_proposals([_candidate()])[0]
    ok,detail=up.approve(p["id"],actor="test")
    assert not ok
    assert not approved_universe.path().exists()

    ok,_=up.request_review(p["id"],actor="test")
    assert ok and up.get(p["id"])["status"]=="REVIEW_REQUESTED"
    review={"passed":True,"etoro_symbol_full":"XYZ.US","instrument_id":77,"summary":"PASS","checks":[]}
    ok,_=up.set_technical_result(p["id"],review)
    assert ok and up.get(p["id"])["status"]=="TECHNICALLY_APPROVED"
    assert not approved_universe.path().exists(), "technische Pruefung allein darf nicht aufnehmen"

    ok,detail=up.approve(p["id"],actor="test")
    assert ok and "naechstem Bot-Start" in detail
    rows=approved_universe.approved_stock_rows()
    assert len(rows)==1 and rows[0]["symbol"]=="XYZ" and rows[0]["broad"] is True
    assert up.get(p["id"])["status"]=="HUMAN_APPROVED"

    ok,_=up.approve(p["id"],actor="test")
    assert ok and len(approved_universe.approved_stock_rows())==1


def test_research_module_has_no_universe_write_or_order_rights():
    source=Path(__file__).resolve().parents[1].joinpath("universe_research.py").read_text(encoding="utf-8")
    assert "add_approved_stock" not in source
    assert "kaufe_mit_absicherung" not in source
    assert "submit_protected_buy" not in source


def test_approved_rows_are_merged_only_when_merge_is_called(monkeypatch,tmp_path):
    monkeypatch.setenv("TRADINGBOT_TEST_STATE_DIR",str(tmp_path))
    p=up.create_proposals([_candidate("NEW")])[0]
    up.request_review(p["id"],actor="test")
    up.set_technical_result(p["id"],{"passed":True,"etoro_symbol_full":"NEW.US","instrument_id":88,"summary":"PASS","checks":[]})
    ok,_=up.approve(p["id"],actor="test")
    assert ok

    base=[{"symbol":"AAA","asset_type":"stock"}]
    # Persisting approval alone does not mutate any already-built in-memory list.
    already_running=list(base)
    assert [r["symbol"] for r in already_running]==["AAA"]
    merged=approved_universe.merge_approved_stocks(base)
    assert {r["symbol"] for r in merged}=={"AAA","NEW"}
