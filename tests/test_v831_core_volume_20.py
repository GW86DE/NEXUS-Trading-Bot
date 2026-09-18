from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json


@dataclass
class Meta:
    base_ccy: str
    quote_ccy: str = "EUR"
    state: str = "live"
    inst_type: str = "SPOT"


@dataclass
class Ticker:
    vol_24h_quote: float


def _markets(n=25):
    instruments = {f"C{i:02d}-EUR": Meta(f"C{i:02d}") for i in range(n)}
    tickers = {k: Ticker(1000-i) for i, k in enumerate(instruments)}
    return instruments, tickers


def test_volume_normalization_dedup_filters_limit_and_tiebreak():
    import core_volume_20 as core
    inst, tick = _markets(25)
    inst.update({"C00-USDC": Meta("C00", "USDC"), "USDC-EUR": Meta("USDC"),
                 "BTC3L-EUR": Meta("BTC3L"), "SWAP-EUR": Meta("SWAP", inst_type="SWAP"),
                 "OFF-EUR": Meta("OFF", state="suspend")})
    tick.update({"C00-USDC": Ticker(2000), "USDC-EUR": Ticker(999999),
                 "BTC3L-EUR": Ticker(999999), "SWAP-EUR": Ticker(999999), "OFF-EUR": Ticker(999999)})
    rows = core.rank_snapshot(inst, tick, quote_rates={"USDC": .9})
    assert len(rows) == 20 and len({x["base"] for x in rows}) == 20
    assert rows[0]["pair"] == "C00-USDC" and rows[0]["volume_24h_normalized_eur"] == 1800
    assert not ({"USDC", "BTC3L", "SWAP", "OFF"} & {x["base"] for x in rows})
    tied = core.rank_snapshot({"B-EUR": Meta("B"), "A-EUR": Meta("A")},
                              {"B-EUR": Ticker(5), "A-EUR": Ticker(5)})
    assert [x["base"] for x in tied] == ["A", "B"]


def test_persistence_restart_and_stale_fail_closed(monkeypatch, tmp_path):
    monkeypatch.setenv("TRADINGBOT_TEST_STATE_DIR", str(tmp_path))
    import core_volume_20 as core
    old = {"schema": 1, "status": "CURRENT", "items": [{"base": "BTC"}]}
    from safe_persistence import atomic_write_json
    atomic_write_json(core.state_path(), old)
    assert core.current_bases() == {"BTC"} and core.can_open_new("BTC")
    state = core.monthly_update(now=datetime(2026, 8, 26, tzinfo=timezone.utc), min_days=20)
    assert state["status"] == "STALE" and core.current_bases() == {"BTC"}
    assert not core.can_open_new("BTC")


def test_first_run_builds_history_but_never_activates_one_day_bootstrap(monkeypatch, tmp_path):
    monkeypatch.setenv("TRADINGBOT_TEST_STATE_DIR", str(tmp_path))
    import core_volume_20 as core
    inst, tick = _markets(25)
    core.collect(inst, tick, now=datetime(2026, 8, 26, tzinfo=timezone.utc))
    state = core.load()
    assert state["status"] == "BUILDING" and state["items"] == []
    assert state["selection_method"] == "30d median of one daily OKX snapshot"
    assert not core.can_open_new("C00") and state["next_due_utc"]


def test_collection_writes_only_one_bounded_snapshot_per_day(monkeypatch, tmp_path):
    monkeypatch.setenv("TRADINGBOT_TEST_STATE_DIR", str(tmp_path))
    import core_volume_20 as core
    inst, tick = _markets(140)
    day = datetime(2026, 8, 26, 1, tzinfo=timezone.utc)
    core.collect(inst, tick, now=day)
    before = core.history_path().stat().st_mtime_ns
    core.collect(inst, tick, now=day + timedelta(minutes=15))
    history = json.loads(core.history_path().read_text(encoding="utf-8"))
    assert len(history["samples"]) == 1
    assert len(history["samples"][0]["items"]) == 100
    assert core.history_path().stat().st_mtime_ns == before


def test_each_ranked_coin_needs_its_own_day_coverage(monkeypatch, tmp_path):
    monkeypatch.setenv("TRADINGBOT_TEST_STATE_DIR", str(tmp_path))
    import core_volume_20 as core
    now = datetime(2026, 9, 1, tzinfo=timezone.utc)
    samples = []
    for d in range(20):
        items = []
        for i in range(20):
            items.append({"base": f"C{i:02d}", "pair": f"C{i:02d}-EUR",
                          "quote": "EUR", "measured_at_utc": (now-timedelta(days=d)).isoformat(),
                          "volume_24h_normalized_eur": 1000-i})
        if d == 0:
            items.append({"base": "PUMP", "pair": "PUMP-EUR", "quote": "EUR",
                          "measured_at_utc": now.isoformat(),
                          "volume_24h_normalized_eur": 999999})
        samples.append({"measured_at_utc": (now-timedelta(days=d)).isoformat(), "items": items})
    from safe_persistence import atomic_write_json
    atomic_write_json(core.history_path(), {"schema": 1, "samples": samples})
    rows, coverage = core.rank_30d(now=now, min_days=20)
    assert coverage["complete"] and "PUMP" not in {x["base"] for x in rows}


def test_jup_is_not_misclassified_as_leveraged_and_missing_state_fails_closed():
    import core_volume_20 as core
    assert not core.LEVERAGED.fullmatch("JUP")
    class NoState:
        base_ccy="BTC"; quote_ccy="EUR"; inst_type="SPOT"
    assert core.rank_snapshot({"BTC-EUR": NoState()}, {"BTC-EUR": Ticker(10)}) == []


def _seed_30_days(core, now):
    samples=[]
    inst, tick = _markets(22)
    for d in range(30):
        measured = now - timedelta(days=d)
        rows = core.rank_snapshot(inst, tick, measured_at=measured, limit=100)
        samples.append({"measured_at_utc": measured.isoformat(), "items": rows})
    from safe_persistence import atomic_write_json
    atomic_write_json(core.history_path(), {"schema": 1, "samples": samples})


def test_month_boundary_timezone_restart_one_gpt_and_immutable_ranking(monkeypatch, tmp_path):
    monkeypatch.setenv("TRADINGBOT_TEST_STATE_DIR", str(tmp_path))
    import core_volume_20 as core
    now = datetime(2026, 10, 1, 3, 10, tzinfo=core.ZONE)
    _seed_30_days(core, now)
    calls=[]
    def gpt(payload):
        calls.append(payload)
        return {"warnings": ["C01 möglicher Namenskonflikt"], "rank": ["HACK"]}
    out = core.monthly_update(now=now, gpt_review=gpt, min_days=20)
    assert out["status"] == "CURRENT" and len(out["items"]) == 20
    assert len(calls) == 1 and [x["base"] for x in out["items"]][:2] == ["C00", "C01"]
    again = core.monthly_update(now=now + timedelta(hours=1), gpt_review=gpt, min_days=20)
    assert not again["ran"] and len(calls) == 1
    assert core.next_due(datetime(2026, 3, 31, 23, 30, tzinfo=core.ZONE)).month == 4


def test_gpt_failure_invalid_response_does_not_change_deterministic_list(monkeypatch, tmp_path):
    monkeypatch.setenv("TRADINGBOT_TEST_STATE_DIR", str(tmp_path))
    import core_volume_20 as core
    now = datetime(2026, 11, 1, 4, tzinfo=timezone.utc); _seed_30_days(core, now)
    out = core.monthly_update(now=now, gpt_review=lambda _: "invalid", min_days=20)
    assert out["status"] == "CURRENT" and out["gpt_review"]["status"] == "FAILED"
    assert len(out["items"]) == 20


def test_atomic_activation_failure_preserves_old_snapshot(monkeypatch, tmp_path):
    monkeypatch.setenv("TRADINGBOT_TEST_STATE_DIR", str(tmp_path))
    import core_volume_20 as core
    from safe_persistence import atomic_write_json as real_write
    old = {"status": "CURRENT", "items": [{"base": "OLD"}]}
    real_write(core.state_path(), old)
    now = datetime(2026, 12, 1, 4, tzinfo=timezone.utc); _seed_30_days(core, now)
    def fail_state(path, data):
        if path == core.state_path(): raise OSError("disk full")
        return real_write(path, data)
    monkeypatch.setattr(core, "atomic_write_json", fail_state)
    import pytest
    with pytest.raises(OSError): core.monthly_update(now=now, min_days=20)
    assert json.loads(core.state_path().read_text(encoding="utf-8"))["items"] == old["items"]


def test_dynamic_union_and_core_never_bypasses_trade_filters(monkeypatch, tmp_path):
    monkeypatch.setenv("TRADINGBOT_TEST_STATE_DIR", str(tmp_path))
    import core_volume_20 as core
    from safe_persistence import atomic_write_json
    atomic_write_json(core.state_path(), {"status": "CURRENT", "items": [{"base": "C00"}]})
    dynamic = {"DYN1", "DYN2"}; combined = dynamic | core.current_bases()
    assert combined == {"C00", "DYN1", "DYN2"}
    from candidate_gate import bewerte_kandidat
    result = bewerte_kandidat(state_allows_buy=True, broker_online=True,
        instrument_identity_ok=True, market_open=True, position_already_open=False,
        duplicate_open_order=False, risk_allows_buy=False, portfolio_allows_buy=True,
        cash_allows_buy=True, market_quality_ok=True, cost_quote_ok=True, net_edge_ok=True,
        quantity=1, price=10, stop=9, take_profit=12)
    assert not result.approved and result.blocked_by == "risk_manager"


def test_fixed_core_that_fails_current_hard_filter_is_not_tradeable(monkeypatch, tmp_path):
    monkeypatch.setenv("TRADINGBOT_TEST_STATE_DIR", str(tmp_path))
    import core_volume_20 as core
    from safe_persistence import atomic_write_json
    atomic_write_json(core.state_path(), {"status": "CURRENT", "items": [{"base": "C00"}]})
    from universe.manager import UniverseManager
    from universe.modelle import AKTIV, TIER_KERN, UniverseMitglied, UniverseZustand
    state = UniverseZustand(tmp_path / "universe.json")
    state.setze(UniverseMitglied(symbol="BTC", broker="okx", inst_id="BTC-EUR",
                                zustand=AKTIV, tier=TIER_KERN))
    manager = UniverseManager(state)
    manager.lauf({"broker": "okx", "rangliste": [], "eligible_symbols": [],
                  "ineligible_reasons": {"BTC": "kein aktuelles live Spot-Paar"}})
    member = manager.zustand.hole("okx", "BTC")
    assert member is not None and member.kern_blockiert and not member.handelbar


def test_removed_core_position_is_not_exit_signal(monkeypatch, tmp_path):
    monkeypatch.setenv("TRADINGBOT_TEST_STATE_DIR", str(tmp_path))
    import core_volume_20 as core
    from safe_persistence import atomic_write_json
    atomic_write_json(core.state_path(), {"status": "CURRENT", "items": [{"base": "OLD"}]})
    # The core module contains no broker sell/close operation; removal changes membership only.
    source = core.__file__ and open(core.__file__, encoding="utf-8").read()
    assert "schliesse_position" not in source and "register_sell" not in source
