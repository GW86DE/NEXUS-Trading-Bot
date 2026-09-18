"""Daily membership, intraday measurements and restart-safe safety blocks."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace

import pytest

from universe.manager import UniverseManager
from universe.modelle import (AKTIV, UniverseKandidat, UniverseMitglied,
                               UniverseScore, UniverseZustand)

DAY = datetime(2026, 9, 12, 5, tzinfo=timezone.utc)


def make_manager(tmp_path):
    cfg = SimpleNamespace(LOCAL_TIMEZONE="Europe/Berlin", CRYPTO_CORE_SYMBOLS=(),
                          CRYPTO_CORE_LIMIT=0, STOCK_CORE_SYMBOLS=(),
                          STOCK_CORE_LIMIT=0, CRYPTO_UNIVERSE_MIN_RESIDENCE_HOURS=0)
    return UniverseManager(UniverseZustand(tmp_path / "universe_state.json"), cfg=cfg)


def selection(*symbols, broker="okx", safety="", core=None):
    result = {"broker": broker, "rangliste": [
        {"kandidat": UniverseKandidat(symbol=s, broker=broker, inst_id=s + "-EUR"),
         "score": UniverseScore(gesamt=0.8), "rang": i + 1,
         "sicherheitsabgang": safety} for i, s in enumerate(symbols)]}
    if core is not None:
        result.update(core_symbols=core, eligible_symbols=list(symbols))
    return result


@pytest.mark.parametrize("broker", ["okx", "etoro"])
def test_one_membership_batch_per_local_day(tmp_path, broker):
    manager = make_manager(tmp_path)
    first = manager.lauf(selection("WLD", broker=broker), jetzt=DAY)
    assert "WLD" in first.beobachtung_gestartet
    for minute in (15, 30, 45, 60):
        diff = manager.lauf(selection("BTC", broker=broker),
                            jetzt=DAY + timedelta(minutes=minute))
        assert not diff.entfernt and not diff.aufgenommen and not diff.beobachtung_gestartet
        assert manager.zustand.hole(broker, "WLD") is not None
        assert manager.zustand.hole(broker, "BTC") is None
    diff = manager.lauf(selection("BTC", broker=broker), jetzt=DAY + timedelta(days=1))
    assert "WLD" in diff.entfernt
    assert "BTC" in diff.beobachtung_gestartet


def test_marker_and_core_survive_a_fresh_manager(tmp_path):
    manager = make_manager(tmp_path)
    manager.lauf(selection("WLD", core=["WLD"]), jetzt=DAY)
    manager = UniverseManager(UniverseZustand(manager.zustand.datei), cfg=manager.cfg)
    assert manager.kernwerte("okx") == {"WLD"}  # auch ohne Lauf in der WebUI
    diff = manager.lauf(selection("BTC", core=["BTC"]), jetzt=DAY + timedelta(hours=2))
    assert not diff.entfernt and not diff.aufgenommen
    assert manager.kernwerte("okx") == {"WLD"}
    assert manager.zustand.hole("okx", "WLD").handelbar is False
    assert manager.zustand.hole("okx", "BTC") is None
    diff = manager.lauf(selection("BTC", core=["BTC"]), jetzt=DAY + timedelta(days=1))
    assert diff.aufgenommen == ["BTC"] and diff.entfernt == ["WLD"]


def test_brokers_have_independent_days(tmp_path):
    manager = make_manager(tmp_path)
    manager.lauf(selection("WLD"), jetzt=DAY)
    diff = manager.lauf(selection("AAPL", broker="etoro"), jetzt=DAY)
    assert diff.beobachtung_gestartet == ["AAPL"]
    assert set(manager.zustand.tagesauswahl) == {"okx", "etoro"}


def test_intraday_safety_block_persists_without_removal(tmp_path):
    manager = make_manager(tmp_path)
    manager.lauf(selection("WLD"), jetzt=DAY)
    manager.zustand.hole("okx", "WLD").zustand = AKTIV
    diff = manager.lauf(selection("WLD", safety="Delisting laut Broker"),
                        jetzt=DAY + timedelta(hours=1))
    assert diff.entfernt == []
    manager = UniverseManager(UniverseZustand(manager.zustand.datei), cfg=manager.cfg)
    assert not manager.zustand.hole("okx", "WLD").handelbar
    manager.lauf(selection("WLD"), jetzt=DAY + timedelta(hours=2))
    assert not manager.zustand.hole("okx", "WLD").handelbar
    manager.lauf(selection("WLD"), jetzt=DAY + timedelta(days=1))
    assert manager.zustand.hole("okx", "WLD").handelbar


def test_daily_removal_does_not_readd_same_symbol(tmp_path):
    manager = make_manager(tmp_path)
    manager.lauf(selection("WLD"), jetzt=DAY)
    diff = manager.lauf(selection("WLD", safety="Delisting laut Broker"),
                        jetzt=DAY + timedelta(days=1))
    assert diff.entfernt == ["WLD"]
    assert manager.zustand.hole("okx", "WLD") is None


def test_scores_and_probation_continue_intraday(tmp_path):
    manager = make_manager(tmp_path)
    manager.lauf(selection("WLD"), jetzt=DAY)
    member = manager.zustand.hole("okx", "WLD")
    manager.lauf(selection("WLD"), jetzt=DAY + timedelta(minutes=15))
    member.zustand_seit = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    diff = manager.lauf(selection("WLD"), jetzt=DAY + timedelta(minutes=30))
    assert len(member.score_verlauf) == 3
    assert diff.freigegeben == ["WLD"]
    assert member.handelbar


def test_new_position_is_monitored_intraday(tmp_path):
    manager = make_manager(tmp_path)
    manager.lauf(selection("WLD"), jetzt=DAY)
    diff = manager.lauf(selection("WLD"), offene_positionen=["SOL"],
                        jetzt=DAY + timedelta(minutes=15))
    assert "SOL" in diff.gepinnt
    assert "SOL" in manager.monitoring_symbole("okx")


def test_held_position_stays_monitored_but_cannot_buy_after_safety_event(tmp_path):
    manager = make_manager(tmp_path)
    manager.lauf(selection("WLD"), offene_positionen=["WLD"], jetzt=DAY)
    diff = manager.lauf(selection("WLD", safety="Delisting laut Broker"),
                        offene_positionen=["WLD"], jetzt=DAY + timedelta(hours=1))
    assert not diff.entfernt
    assert "WLD" in manager.monitoring_symbole("okx")
    assert "WLD" not in manager.handelbare_symbole("okx")
    assert manager.mitglieder_tabelle("okx")[0]["kaufblock_grund"] == "Delisting laut Broker"


def test_empty_snapshot_does_not_consume_new_day(tmp_path):
    manager = make_manager(tmp_path)
    manager.lauf(selection("WLD"), jetzt=DAY)
    tomorrow = DAY + timedelta(days=1)
    for minute in (0, 15, 30):
        manager.lauf(selection(), jetzt=tomorrow + timedelta(minutes=minute))
    assert manager.zustand.tagesauswahl["okx"] == "2026-09-12"
    assert manager.zustand.hole("okx", "WLD") is not None
    diff = manager.lauf(selection("BTC"), jetzt=tomorrow + timedelta(hours=1))
    assert diff.entfernt == ["WLD"] and diff.beobachtung_gestartet == ["BTC"]


def test_calendar_uses_berlin_and_does_not_follow_clock_backwards(tmp_path):
    manager = make_manager(tmp_path)
    manager.lauf(selection("WLD"), jetzt=datetime(2026, 9, 12, 21, 55, tzinfo=timezone.utc))
    diff = manager.lauf(selection("WLD", "BTC"),
                        jetzt=datetime(2026, 9, 12, 22, 5, tzinfo=timezone.utc))
    assert diff.beobachtung_gestartet == ["BTC"]
    assert manager.zustand.tagesauswahl["okx"] == "2026-09-13"
    diff = manager.lauf(selection("SOL"), jetzt=DAY)
    assert not diff.beobachtung_gestartet and not diff.entfernt


def test_old_state_migrates_without_losing_members(tmp_path):
    member = UniverseMitglied(symbol="WLD", broker="okx")
    path = tmp_path / "universe_state.json"
    path.write_text(json.dumps({"version": 1, "mitglieder": [member.als_dict()]}))
    manager = make_manager(tmp_path)
    manager.lauf(selection("WLD", "BTC"), jetzt=DAY)
    saved = json.loads(path.read_text())
    assert saved["tagesauswahl"] == {"okx": "2026-09-12"}
    assert {m["symbol"] for m in saved["mitglieder"]} == {"WLD", "BTC"}


def test_failed_write_does_not_consume_batch_or_publish_new_state(tmp_path, monkeypatch):
    import safe_persistence
    manager = make_manager(tmp_path)
    real_write = safe_persistence.atomic_write_json
    def denied(*args, **kwargs):
        raise OSError("simulated disk failure")
    monkeypatch.setattr(safe_persistence, "atomic_write_json", denied)
    with pytest.raises(OSError):
        manager.lauf(selection("WLD"), jetzt=DAY)
    assert manager.zustand.tagesauswahl == {} and manager.zustand.mitglieder == {}
    monkeypatch.setattr(safe_persistence, "atomic_write_json", real_write)
    assert manager.lauf(selection("WLD"), jetzt=DAY).beobachtung_gestartet == ["WLD"]


def test_parallel_runs_share_one_membership_batch(tmp_path):
    manager = make_manager(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as pool:
        diffs = list(pool.map(lambda symbol: manager.lauf(selection(symbol), jetzt=DAY),
                              ["WLD", "BTC"]))
    assert sum(len(d.beobachtung_gestartet) for d in diffs) == 1
    saved = UniverseZustand(manager.zustand.datei)
    assert len(saved.mitglieder) == 1 and saved.tagesauswahl["okx"] == "2026-09-12"


def test_optional_ai_does_not_hold_the_position_read_lock(tmp_path):
    manager = make_manager(tmp_path)
    manager.lauf(selection("WLD"), jetzt=DAY)
    manager.lauf(selection("WLD"), jetzt=DAY + timedelta(minutes=15))
    member = manager.zustand.hole("okx", "WLD")
    member.zustand_seit = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    observed = []
    class Router:
        def bewerte_universe_kandidat(self, payload):
            observed.append(pool.submit(manager.zustand.hole, "okx", "WLD").result(timeout=1))
            return {"attention": "NORMAL", "modell": "fixture"}
    manager.ai = Router()
    with ThreadPoolExecutor(max_workers=1) as pool:
        manager.lauf(selection("WLD"), jetzt=DAY + timedelta(minutes=30))
    assert observed == [member]
    assert member.ai_modell == "fixture"
