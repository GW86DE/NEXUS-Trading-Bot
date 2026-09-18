"""10.3.0: PULSAR-2.0 Hype-Spur -- Kriterien, Stabilitaet, Limits, Zubringer.

Kernaussagen:
- Ein Hype-Kandidat braucht GLEICHZEITIG Social-Spike, Kurs-/Volumen-
  bestaetigung und saubere Identitaet; Luna bleibt Warnfilter (REJECT blockt).
- Fehlende Anbieterfelder werden nie erfunden (UNKNOWN bleibt UNKNOWN).
- Hoechstens 1 offener Trade, 1 Nominierung pro Tag; eine Ablehnung
  verbraucht den Tag, nicht mehr die Woche.
- Zeitstop 10 Handelstage aus dem eingefrorenen Plan; Altplaene behalten 20.
- Entdeckungen speisen als Gaeste das normale Universum (max 10, 14 Tage).
"""
from datetime import datetime, timedelta, timezone
import time

import pytest

from pulsar import evidence


NOW = time.time()


def _bars(gain=0.06, volume_multiple=3.5, days=21, base_volume=1_000_000.0):
    """21 abgeschlossene Tageskerzen; letzter Tag traegt Spike."""
    start = datetime.fromtimestamp(NOW, timezone.utc).date() - timedelta(days=days+1)
    rows = []
    for i in range(days):
        close = 10.0 if i < days-1 else 10.0*(1+gain)
        volume = base_volume if i < days-1 else base_volume*volume_multiple
        rows.append({"date": (start+timedelta(days=i)).isoformat(),
                     "close": close, "high": close*1.01, "low": close*0.99,
                     "open": close, "volume": volume})
    return rows


def _reddit_attention(mentions=150, previous=40, observed=None):
    return {"symbol": "HYPE", "source": "apewisdom", "source_family": "reddit_aggregate",
            "mentions": mentions, "mentions_24h_ago": previous,
            "observed_at": NOW-600 if observed is None else observed}


def _card(**overrides):
    card = {"symbol": "HYPE", "name": "Hype Corp",
            "attention": _reddit_attention(), "bars": _bars(), "intraday": [],
            "blocks": [], "missing": [], "errors": [], "precheck": {},
            "sources": [], "evidence_hash": "e"*64}
    card.update(overrides)
    return card


def test_reddit_wachstum_plus_tageskerze_qualifiziert():
    out = evidence.evaluate(_card(), now=NOW)
    assert out["eligible"] is True and out["state"] == "HYPE_KANDIDAT"
    assert out["hype"]["social"]["kind"] == "REDDIT_WACHSTUM"
    assert out["hype"]["price"]["kind"] == "TAGESKERZE"
    assert out["score"] is None


def test_cold_start_ohne_vortageswert_qualifiziert_ab_40_erwaehnungen():
    card = _card(attention=_reddit_attention(mentions=55, previous=None))
    out = evidence.evaluate(card, now=NOW)
    assert out["eligible"] is True
    assert out["hype"]["social"]["kind"] == "REDDIT_NEUZUGANG"
    card = _card(attention=_reddit_attention(mentions=39, previous=None))
    assert evidence.evaluate(card, now=NOW)["eligible"] is False


def test_wachstum_unter_schwelle_bleibt_beobachtung():
    card = _card(attention=_reddit_attention(mentions=150, previous=80))
    out = evidence.evaluate(card, now=NOW)
    assert out["eligible"] is False
    assert any("Kein belegter Spike" in m for m in out["missing"])


def test_alte_reddit_beobachtung_zaehlt_nicht():
    card = _card(attention=_reddit_attention(observed=NOW-7200))
    out = evidence.evaluate(card, now=NOW)
    assert out["eligible"] is False
    assert any("aelter als eine Stunde" in m for m in out["missing"])


def test_x_stichprobe_qualifiziert_mit_posts_und_accounts():
    attention = {"symbol": "HYPE", "source": "X", "observed_at": NOW-3600,
                 "x_discovery": {"sampled_post_count": 9, "distinct_accounts_in_sample": 6}}
    out = evidence.evaluate(_card(attention=attention), now=NOW)
    assert out["eligible"] is True
    assert out["hype"]["social"]["kind"] == "X_STICHPROBE"
    duenne = {"symbol": "HYPE", "source": "X", "observed_at": NOW-3600,
              "x_discovery": {"sampled_post_count": 3, "distinct_accounts_in_sample": 2}}
    assert evidence.evaluate(_card(attention=duenne), now=NOW)["eligible"] is False


def test_kursplus_ohne_volumen_bestaetigt_nicht():
    out = evidence.evaluate(_card(bars=_bars(volume_multiple=1.2)), now=NOW)
    assert out["eligible"] is False
    assert any("Kursbestaetigung" in m for m in out["missing"])


def test_volumen_ohne_kursplus_bestaetigt_nicht():
    out = evidence.evaluate(_card(bars=_bars(gain=0.01)), now=NOW)
    assert out["eligible"] is False


def test_intraday_bestaetigung_gegen_letzten_tagesschluss():
    bars = _bars(gain=0.0, volume_multiple=1.0)
    stamp = datetime.fromtimestamp(NOW-900, timezone.utc)
    intraday = [{"date": stamp.isoformat(), "close": 10.7, "volume": 21_000_000.0}]
    out = evidence.evaluate(_card(bars=bars, intraday=intraday), now=NOW)
    assert out["eligible"] is True
    assert out["hype"]["price"]["kind"] == "INTRADAY"


def test_veralteter_intraday_stand_faellt_auf_tageskerze_zurueck():
    stamp = datetime.fromtimestamp(NOW-3*3600, timezone.utc)
    intraday = [{"date": stamp.isoformat(), "close": 10.7, "volume": 21_000_000.0}]
    out = evidence.evaluate(_card(intraday=intraday), now=NOW)
    assert out["hype"]["price"]["kind"] == "TAGESKERZE"


def test_luna_reject_blockiert_hype_kandidaten():
    precheck = {"ok": True, "stufe": "luna", "daten": {"verdict": "REJECT", "missing": []}}
    out = evidence.evaluate(_card(precheck=precheck), now=NOW)
    assert out["eligible"] is False
    assert any("Luna" in b for b in out["blocks"])


def test_kartenbloecke_bleiben_hart():
    out = evidence.evaluate(_card(blocks=["ETF/Fonds statt Einzelaktie"]), now=NOW)
    assert out["eligible"] is False


def test_stable_candidate_verlangt_zwei_getrennte_hype_messungen(tmp_path, monkeypatch):
    from pulsar import research
    card = _card()
    card.update(evidence.evaluate(card, now=NOW))
    assert card["eligible"]
    research.save_assessment(dict(card), now=NOW-900)
    assert research.stable_candidate("HYPE", now=NOW) is None
    research.save_assessment(dict(card), now=NOW-120)
    stable = research.stable_candidate("HYPE", now=NOW)
    assert stable and stable["symbol"] == "HYPE"


def test_stable_candidate_ignoriert_messungen_alter_regelversionen(tmp_path):
    from pulsar import research
    card = _card()
    card.update(evidence.evaluate(card, now=NOW))
    old = dict(card, rules_version="PULSAR-1.4-SOURCE-COORDINATION")
    research.save_assessment(old, now=NOW-900)
    research.save_assessment(dict(card), now=NOW-120)
    assert research.stable_candidate("HYPE", now=NOW) is None


def test_universumsgaeste_werden_gemerkt_und_verfallen(tmp_path):
    from pulsar import research
    research.merke_universumsgaeste(["ABCD", "EFGH", "nicht gültig"], now=NOW-15*86400)
    research.merke_universumsgaeste(["EFGH"], now=NOW-3600)
    guests = research.universe_guests(now=NOW)
    assert [g["symbol"] for g in guests] == ["EFGH"]


def test_universumsgaeste_limit_zehn(tmp_path):
    from pulsar import research
    research.merke_universumsgaeste([f"SYM{i}" for i in range(15)], now=NOW)
    assert len(research.universe_guests(now=NOW)) == 10


def test_regeln_hoechstens_ein_offener_trade_und_tageslimit():
    from pulsar import control
    assert control.RULES["max_open"] == 1
    assert control.RULES["daily_nominations"] == 1
    assert control.RULES["max_hold_sessions"] == 10
    assert control.RULES["version"] == "PULSAR-2.0"
    assert "weekly_nominations" not in control.RULES


def test_zeitstop_kommt_aus_dem_plan(monkeypatch):
    from pulsar import positions, control

    class Record:
        avg_cost = 10.0
        quantity = 5.0
        planned_stop = 0.0
        entry_time = (datetime.now(timezone.utc)-timedelta(days=30)).isoformat()
        protection_plan_history = []

    calls = {}

    class Con:
        def execute(self, sql, params=()):
            calls.setdefault("sql", []).append(sql)
            class R:
                def fetchone(self_inner):
                    return {"proposal_id": "p1", "high15m": 0.0, "stop": 9.0,
                            "partial_state": "", "partial_quantity": 0.0, "review_at": 1.0,
                            "partial_orders": "[]"}
            return R()

    from contextlib import contextmanager

    @contextmanager
    def fake_transaction():
        yield Con()

    monkeypatch.setattr(control, "transaction", fake_transaction)
    monkeypatch.setattr(positions, "sessions_held", lambda *a, **k: 10)
    item = {"id": "p1", "plan": {"price": 10.0, "R": 1.0, "stop": 9.0,
                                 "quantity": 5.0, "max_hold_sessions": 10, "review_sessions": 5}}
    action = positions.decision(item, Record(), 9.5)
    assert action["action"] == "CLOSE" and "10 Handelstage" in action["reason"]
    legacy = {"id": "p1", "plan": {"price": 10.0, "R": 1.0, "stop": 9.0, "quantity": 5.0}}
    action = positions.decision(legacy, Record(), 9.5)
    assert action["action"] == "HOLD"


def test_x_kandidatensuche_hat_fuenf_slots_pro_tag():
    from market_intelligence import candidate_research, service
    assert candidate_research.SEARCHES_PER_DAY == 5
    assert candidate_research.INTERVAL == 86400//5
    assert service.MAX_COUNTS_PER_DAY == 0
    assert service.CANDIDATE_SEARCHES_PER_DAY == 5
    assert service.GENERAL_SEARCHES_PER_DAY == 2
    public = service._settings_public(service._validate_settings({}))
    assert public["estimated_month_eur"] <= 15.0
    assert public["counts_per_day_max"] == 0
    assert public["candidate_searches_per_day"] == 5


def test_alte_gespeicherte_drei_suchen_werden_angehoben():
    from market_intelligence import service
    data = service._validate_settings({"searches_per_day": 3})
    assert data["searches_per_day"] == 5
    with pytest.raises(ValueError):
        service._validate_settings({"searches_per_day": 7})


def test_hype_alarm_text_traegt_belege_und_keine_orderfreigabe():
    from pulsar.telegram import hype_text
    card = _card()
    card.update(evidence.evaluate(card, now=NOW))
    card["market"] = {"price": 10.6, "turnover_usd": 25_000_000}
    text = hype_text(card, mode="BEOBACHTEN")
    assert "PULSAR-HYPE · HYPE" in text
    assert "keine Order" in text
    assert "Beobachtungsmodus" in text
    text = hype_text(card, mode="FREIGABE")
    assert "persönlichen Bestätigung" in text
