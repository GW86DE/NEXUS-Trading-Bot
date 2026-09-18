from datetime import datetime, timezone
from pathlib import Path

import weekly_intelligence as wi


def test_weekly_target_catches_missed_run():
    # Wed 2026-08-19 noon -> previous Sunday 11:00 must be the due target.
    now = datetime(2026, 8, 19, 12, 0, tzinfo=timezone.utc)
    target = wi._target(now, 6, 11)
    assert target == datetime(2026, 8, 16, 11, 0, tzinfo=timezone.utc)


def test_ai_off_skips_research_but_keeps_deterministic_strategy(monkeypatch, tmp_path):
    monkeypatch.setenv("TRADINGBOT_TEST_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(wi, "read_mode", lambda: "OFF")
    monkeypatch.setattr(wi, "_due", lambda *a, **k: True)
    monkeypatch.setattr(wi.config, "AI_RESEARCH_ENABLED", True)
    monkeypatch.setattr(wi.config, "STRATEGY_ANALYST_ENABLED", True)

    class ResearchMustNotRun:
        def __init__(self):
            raise AssertionError("Research darf bei /ai off nicht instanziiert werden")

    report = tmp_path / "strategy.txt"
    report.write_text("deterministische Statistik", encoding="utf-8")
    seen = {}

    class FakeAnalyst:
        def __init__(self):
            self.ai_enabled = True
        def run(self):
            seen["ai_enabled"] = self.ai_enabled
            return report, {"stats": {"decision_count": 42, "lookback_days": 90}, "interpretation": None}

    monkeypatch.setattr(wi, "UniverseResearchAssistant", ResearchMustNotRun)
    monkeypatch.setattr(wi, "StrategyAnalyst", FakeAnalyst)
    monkeypatch.setattr(wi, "send_document", lambda *a, **k: True)
    monkeypatch.setattr(wi, "send_telegram", lambda *a, **k: True)

    result = wi.WeeklyIntelligence().check_once()
    assert result["research"] is None
    assert result["strategy"]["ai"] is False
    assert seen["ai_enabled"] is False


def test_research_sends_only_proposals_and_no_admission(monkeypatch, tmp_path):
    monkeypatch.setenv("TRADINGBOT_TEST_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(wi, "read_mode", lambda: "AUTO")
    monkeypatch.setattr(wi, "_due", lambda state, key, *a, **k: key == "research")
    monkeypatch.setattr(wi.config, "AI_RESEARCH_ENABLED", True)
    monkeypatch.setattr(wi.config, "STRATEGY_ANALYST_ENABLED", False)
    monkeypatch.setattr(wi.config, "STOCK_SYMBOLS", [{"symbol": "AAA"}])
    # Research laeuft nur bei geoeffnetem Aktienmarkt. Ohne diese Steuerung
    # haengt der Test an der echten Uhrzeit: ausserhalb der US-Sitzung nahm er
    # den "deferred"-Zweig und schlug mit KeyError fehl. (Fehler bestand
    # bereits in 8.1.1.)
    import market_calendar
    monkeypatch.setattr(market_calendar, "darf_arbeiten",
                        lambda *a, **k: (True, "Test: Markt offen"), raising=False)

    proposal = {
        "id": "U2026-001-001", "symbol": "XYZ", "company": "Example Corp",
        "sector": "Industrie", "rationale": "Research",
        "sources": [{"title": "A", "url": "https://a.example/x"}, {"title": "B", "url": "https://b.example/y"}],
        "status": "PROPOSED",
    }
    class FakeResearch:
        last_error = ""
        def generate(self, rows):
            assert rows == [{"symbol": "AAA"}]
            return [proposal]

    sent_buttons = []
    monkeypatch.setattr(wi, "UniverseResearchAssistant", FakeResearch)
    monkeypatch.setattr(wi, "send_telegram_buttons", lambda msg, kb, priority="normal": sent_buttons.append((msg, kb)) or True)
    monkeypatch.setattr(wi, "send_telegram", lambda *a, **k: True)

    result = wi.WeeklyIntelligence().check_once()
    assert result["research"]["proposals"] == 1
    assert len(sent_buttons) == 1
    # Weekly orchestration must have no direct approved-universe write path.
    source = Path(wi.__file__).read_text(encoding="utf-8")
    assert "add_approved_stock" not in source


def test_research_wird_bei_geschlossenem_markt_verschoben(monkeypatch, tmp_path):
    """Bei geschlossener Boerse wird Research verschoben, nicht verworfen."""
    monkeypatch.setenv("TRADINGBOT_TEST_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(wi, "read_mode", lambda: "AUTO")
    monkeypatch.setattr(wi, "_due", lambda state, key, *a, **k: key == "research")
    monkeypatch.setattr(wi.config, "AI_RESEARCH_ENABLED", True)
    monkeypatch.setattr(wi.config, "STRATEGY_ANALYST_ENABLED", False)

    import market_calendar
    monkeypatch.setattr(market_calendar, "darf_arbeiten",
                        lambda *a, **k: (False, "Wochenende"), raising=False)

    gerufen = []

    class KeinResearch:
        last_error = ""

        def __init__(self):
            gerufen.append("gestartet")

        def generate(self, rows):
            return []

    monkeypatch.setattr(wi, "UniverseResearchAssistant", KeinResearch)

    ergebnis = wi.WeeklyIntelligence().check_once()
    assert ergebnis["research"]["deferred"] is True
    assert "Wochenende" in ergebnis["research"]["reason"]
    assert gerufen == [], "Bei geschlossenem Markt darf kein Research anlaufen"
