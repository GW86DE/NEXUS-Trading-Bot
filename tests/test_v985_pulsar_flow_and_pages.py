"""Exercise provider -> reviews -> visible cards, plus sold-trade review semantics."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS
import importlib
import time

import pytest
from pulsar import control, research, worker, analysis, evidence


def answer(packet, verdict="OBSERVE"):
    symbol = packet["symbol"]
    claim = lambda text: {"text": text, "source_ids": [packet["sources"][-1]["id"]]}
    return {"symbol": symbol, "thesis": claim(symbol+": individuelle Hypothese"),
        "risks": [claim(symbol+": Risiko "+str(i)) for i in range(3)],
        "missing": [], "verdict": verdict}


class Router:
    aktiv = True
    def __init__(self):
        self.calls = []
    def preise(self, tier):
        return .1, .1
    def waehle_stufe(self, task):
        return "terra", "fixture"
    def frage(self, task, payload, schema, **kwargs):
        self.calls.append((task, deepcopy(payload)))
        if task == "pulsar_precheck":
            data = {"notes": [answer(p, {"BBB":"REVIEW", "AAA":"REJECT"}.get(p["symbol"], "OBSERVE"))
                              for p in payload["candidates"]]}
            tier = "luna"
        else:
            data, tier = answer(payload, "REVIEW"), "terra"
        record = dict(ok=True, stufe=tier, daten=data, kosten=.001, grund="")
        return NS(**record, als_dict=lambda: record)


def seed_profiles(monkeypatch, profiles):
    """Worker fixtures also provide the dated type evidence required at selection."""
    from fmp_reference import FMPReferenz
    from fmp_service import encoded
    from pulsar import candidate_selection
    client = FMPReferenz("FAKE-PROFILE-KEY")
    for symbol, profile in profiles.items():
        client.store.save(encoded(["/profile", {"symbol": symbol}]), [profile], 86400)
    monkeypatch.setattr(worker, "select_candidates", lambda rows, **kwargs:
        candidate_selection.select_candidates(rows, client=client, **kwargs))


def fixtures():
    now = time.time()
    symbols = ["AAA", "BBB", "CCC", "DDD", "EEE"]
    social = research.normalise("apewisdom", {"results": [dict(ticker=s,
        mentions=100-i*5, mentions_24h_ago=10, rank=i+1) for i,s in enumerate(symbols)]})
    for row in social:
        row.update(observed_at=now, evidence_id=row["symbol"]+"-social")
    def market(symbol, **kwargs):
        profile = dict(symbol=symbol, currency="USD", companyName=symbol, price=100., marketCap=1e10,
                       isEtf=False, isFund=False)
        bars = [dict(date=(datetime.now(timezone.utc)-timedelta(days=30-i)).date().isoformat(),
            close=100., volume=1e6) for i in range(25)]
        sources = [dict(id=symbol+name, provider="FMP", kind=name, observed_at=now, data=value)
                   for name,value in (("profile",profile),("bars",bars),("quote",{}),("news",[]))]
        return dict(profile=profile, bars=bars, sources=sources, errors=[])
    return social, market


def test_actual_worker_reviews_all_five_with_luna_only_no_terra(monkeypatch):
    # 10.3.0: Terra-Vertiefung und Gegenpruefung sind entfallen; der Worker
    # laesst nur noch die schnelle Luna-Vorpruefung als Warnfilter laufen.
    rows, market = fixtures(); router = Router()
    monkeypatch.setattr(research, "discover", lambda: rows)
    monkeypatch.setattr(worker, "gather_market", market)
    seed_profiles(monkeypatch, {r["symbol"]: market(r["symbol"])["profile"] for r in rows})
    monkeypatch.setattr(importlib.import_module("ai_router"), "AIRouter", lambda: router)
    w = worker.Worker(); w.session = control.start_session(); control.set_mode("BEOBACHTEN")
    w.cycle(control.settings())
    cards = research.cached("top5")["data"]
    assert len(cards) == 5
    assert [c["symbol"] for c in cards] == [r["symbol"] for r in research.attention_order(rows)]
    assert all(c["symbol"] in c["thesis"] and len(c["risks"]) == 3 for c in cards)
    assert all(c["precheck"]["ok"] for c in cards)
    batch = router.calls[0][1]["candidates"]
    assert all(any(s["provider"] == "apewisdom" for s in p["sources"]) for p in batch)
    assert not any(t in {"pulsar_analysis", "pulsar_countercheck"} for t, _ in router.calls)
    # Ohne Kurs-/Volumenbestaetigung (flache Kerzen) wird niemand eligible,
    # obwohl AAA einen belegten Social-Spike traegt.
    assert not any(c["eligible"] for c in cards)
    assert all(c["score"] is None for c in cards)
    assert all(c["rules_version"] == evidence.REVISION for c in cards)
    w.cycle(control.settings())
    assert sum(t == "pulsar_precheck" for t, _ in router.calls) == 1  # Cache greift.
    assert control.proposals() == []  # Beobachtung erzeugt keine Order-Warteschlange.


@pytest.mark.parametrize("fault", ["wrong_source", "missing_candidate", "duplicate", "foreign", "empty_risk"])
def test_luna_bad_references_or_incomplete_batch_never_reach_cards(fault):
    packets = [{"symbol": s,"sources":[{"id":s+"-proof"}]} for s in ("AAA","BBB")]
    notes = [answer(p) for p in packets]
    if fault == "wrong_source": notes[0]["thesis"]["source_ids"] = ["BBB-proof"]
    if fault == "missing_candidate": notes.pop()
    if fault == "duplicate": notes[1] = deepcopy(notes[0])
    if fault == "foreign": notes[0]["symbol"] = "OTHER"
    if fault == "empty_risk": notes[0]["risks"][0]["text"] = " "
    with pytest.raises(control.Blocked):
        analysis._check_precheck(dict(ok=True,stufe="luna",daten={"notes": notes}), packets)


def test_missing_ai_does_not_show_canned_individual_risks(monkeypatch):
    rows, market = fixtures()
    monkeypatch.setattr(research, "discover", lambda: rows)
    monkeypatch.setattr(worker, "gather_market", market)
    seed_profiles(monkeypatch, {r["symbol"]: market(r["symbol"])["profile"] for r in rows})
    monkeypatch.setattr(importlib.import_module("ai_router"), "AIRouter", lambda: NS(aktiv=False))
    w=worker.Worker();w.session=control.start_session();control.set_mode("BEOBACHTEN")
    w.cycle(control.settings())
    cards=research.cached("top5")["data"]
    assert all(c["text_source"] == "DATENUEBERSICHT" and c["risks"] == [] for c in cards)


def test_new_candidate_limit_survives_worker_restart_and_allows_known_symbols():
    now=time.time()
    for i in range(20): research.reserve_enrichment("S"+str(i),now=now)
    control.start_session()
    research.reserve_enrichment("S0",now=now)
    with pytest.raises(control.Blocked,match="20 neue"):
        research.reserve_enrichment("NEW",now=now)
    research.reserve_enrichment("NEW",now=now+86400)


def test_precheck_batch_keeps_social_and_bounds_long_news():
    rows,market=fixtures()
    packets=[]
    for row in rows:
        m=market(row["symbol"])
        m["sources"][-1]["data"]=[dict(title="News",text="Long article "*10000,url="https://example.test/news")]*5
        packets.append(worker.build_card(row,m)["packet"])
    batch=[analysis._preliminary_packet(p) for p in packets]
    assert len(research.encode({"candidates":batch})) < 24000
    assert all(any(s["provider"] == "apewisdom" for s in p["sources"]) for p in batch)


def test_upgrade_does_not_present_legacy_cached_canned_text_as_new_analysis():
    from pulsar.presentation import snapshot
    research.cache_put("top5", [dict(symbol="PEP",thesis="Old canned text",
        risks=["same risk"]*3,score=None,eligible=False)],3600)
    card=snapshot()["cards"][0]
    assert card["risks"] == [] and card["precheck"] == {}
    assert "vor dem Update" in card["thesis"]
    assert not card["eligible"] and not card["analysis"]


def earnings_card(day, now, **change):
    return {"symbol": "PEP", "sources": [{"id":"calendar", "provider":"FMP", "kind":"earnings",
        "observed_at": now, "data":[{"symbol":"PEP", "date":day}], **change}]}


def test_earnings_calendar_respects_sessions_holidays_staleness_and_symbol():
    from pulsar.requirements import earnings_clear, next_earnings
    # Friday before US Labor Day: next two sessions are Tuesday / Wednesday.
    now=datetime(2026,9,4,16,tzinfo=timezone.utc).timestamp()
    assert not earnings_clear(earnings_card("2026-09-09",now),now=now)
    far=earnings_card("2026-09-10",now)
    assert earnings_clear(far,now=now)
    assert not earnings_clear(far,fallback_days=1,now=now)
    assert next_earnings(earnings_card("2026-09-10",now,observed_at=now-21601),now=now) is None
    assert next_earnings(earnings_card("2026-09-10",now,data=[dict(symbol="OTHER",date="2026-09-10")]),now=now) is None
    assert not earnings_clear({"symbol":"PEP"},now=now)


def test_evidence_checklist_accepts_calendar_but_does_not_invent_hype_proof():
    # 10.3.0: Ein Earnings-Kalenderdatum wird angezeigt, macht aber ohne
    # Social-Spike und Kursbestaetigung niemanden eligible.
    from pulsar.evidence import evaluate
    now=time.time(); day=(datetime.now(timezone.utc)+timedelta(days=20)).date().isoformat()
    card=earnings_card(day,now)
    result=evaluate(card,now=now)
    assert next(c for c in result["checks"] if c["name"] == "earnings")["status"] == "KALENDERDATUM"
    assert not result["eligible"] and result["score"] is None
    assert any("Social-Spike" in gap for gap in result["missing"])
    assert any("Kursbestaetigung" in gap for gap in result["missing"])


def test_closed_unpriced_results_visible_in_review_never_reopened(monkeypatch):
    from webui import state
    row=dict(trade_id=1,symbol="AMD",broker="etoro",paper=True,waehrung="USD",
        broker_account_fingerprint="acct",einstieg_preis=519.9,menge=28,
        ausgestiegen_am="2026-09-10T01:22:05Z",brutto_pnl=-64.4,
        netto_pnl=None,fee_quality="UNKNOWN",reconciliation_status="CONFIRMED_CLOSED")
    rows=[row,{**row,"trade_id":2,"fee_quality":"CONFIRMED","netto_pnl":-65,"gebuehren":.6},
        {**row,"trade_id":3,"broker_account_fingerprint":""}]
    monkeypatch.setattr(importlib.import_module("trade_ledger"),"trade_liste",lambda **k:deepcopy(rows))
    monkeypatch.setattr(state,"_json",lambda name,default:default)
    result=state.trade_analysis()
    assert result["offene_trades"] == []
    assert len(result["geschlossene_trades"]) == 3
    assert [r["trade_id"] for r in result["klaerungs_trades"]] == [1]
    assert result["klaerungs_trades"][0]["clarification_kind"] == "RESULT"
    assert result["kennzahlen"]["ergebnis_klaerung"] == 1
    assert rows[0]["netto_pnl"] is None


def test_universe_underdogs_and_pulsar_routes_require_login(monkeypatch):
    from fastapi.testclient import TestClient
    module=importlib.import_module("webui.app")
    with TestClient(module.app) as client:
        for path in ("/universe","/underdogs","/pulsar"):
            assert client.get(path,follow_redirects=False).headers["location"] == "/login"
        monkeypatch.setattr(module,"_session",lambda *a,**k:{"u":"test","csrf":"bound"})
        for path in ("/universe","/underdogs","/pulsar"):
            assert client.get(path).status_code == 200


def test_unadmitted_underdog_catalog_visible_without_membership_mutation(monkeypatch,tmp_path):
    import config
    from universe.modelle import UniverseZustand
    from webui import state
    path=tmp_path/"empty_universe.json"
    monkeypatch.setattr(config,"UNIVERSE_STATE_FILE",str(path))
    monkeypatch.setattr(config,"STOCK_CATALOG_SYMBOLS",[{"symbol":"UNDER","underdog":True}])
    for func in ("_universums_diagnose","_core_volume_status","_dynamic_30_status","_etoro_reconciliation_status"):
        monkeypatch.setattr(state,func,lambda:{})
    result=state.universe()["broker"]["etoro"]
    assert result["katalog_kandidaten"][0]["symbol"] == "UNDER"
    assert result["katalog_kandidaten"][0]["zustand"] == "KATALOG"
    assert not UniverseZustand(str(path)).hole("etoro","UNDER")
