"""10.5.0: PULSAR-Umbau -- Quellen ohne Reddit-Schluessel, Ausloeser + Bestaetigungen,
Vorwaertsmessung, aufgeraeumte PULSAR-Seite und Diagnosebericht in der WebUI.

Kernaussagen:
- StockTwits (zweite Social-Familie), FINRA Short Interest (Squeeze-Merkmal,
  nur Information) und relatives Volumen (Quote/Stundenkerzen) liefern
  Zaehlwerte; fehlende Daten bleiben UNKNOWN, Rohtexte/Autoren werden nie
  gespeichert.
- Ein Hype-Kandidat braucht Ausloeser, zwei von drei Bestaetigungen und
  mindestens eine Social-Familie; ein Volumen-Ausbruch ohne Social wird
  gemessen, aber nie nominiert.
- Die Vorwaertsmessung speichert jeden Ausloeser mit Kurs, traegt Schluesse
  nach 1/3/5/10 Handelstagen nach und urteilt erst ab 30 vollstaendigen
  Messungen; die Diagnose wertet dieselben Zeilen mit derselben Regel aus.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path
import time
import zipfile

import pytest

from pulsar import evidence, measurement, short_interest, stocktwits, volume_watch
from test_v1030_pulsar_hype import NOW, _bars, _card, _reddit_attention

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"


def _fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class FakeResponse:
    def __init__(self, payload, status=200, headers=None):
        self.status_code, self.headers = status, headers or {}
        self._body = json.dumps(payload).encode("utf-8") if not isinstance(payload, (bytes, str)) else (
            payload.encode("utf-8") if isinstance(payload, str) else payload)
        self.text = self._body.decode("utf-8", errors="replace")
        self.content = self._body

    def iter_content(self, chunk_size=32768):
        for i in range(0, len(self._body), chunk_size):
            yield self._body[i:i+chunk_size]

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return json.loads(self._body)

    def close(self):
        pass


class FakeSession:
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def get(self, url, **kwargs):
        self.calls.append(("GET", url))
        hit = self.routes.get(url)
        if isinstance(hit, Exception):
            raise hit
        return hit if hit is not None else FakeResponse({"error": "nicht gefunden"}, 404)

    def post(self, url, data=None, **kwargs):
        body = json.loads(data or "{}")
        stamp = next((f["fieldValue"] for f in body.get("compareFilters", []) if f["fieldName"] == "settlementDate"), None)
        symbol = next((f["fieldValue"] for f in body.get("compareFilters", []) if f["fieldName"] == "symbolCode"), None)
        self.calls.append(("POST", stamp, symbol))
        rows = [r for r in self.routes.get("finra", []) if r["settlementDate"] == stamp and r["symbolCode"] == symbol]
        return FakeResponse(rows) if rows else FakeResponse(b"", 204)

    def close(self):
        pass


# ---------------------------------------------------------------------------
# StockTwits
# ---------------------------------------------------------------------------
def test_stocktwits_trending_nimmt_nur_aktien_und_keine_krypto():
    rows = stocktwits.normalise_trending(_fixture("echt_stocktwits_trending.json"), now=NOW)
    symbols = [r["symbol"] for r in rows]
    assert "BB" in symbols and "SOFI" in symbols
    assert not any(s.endswith(".X") for s in symbols)
    assert all(r["source"] == "stocktwits_trending" and r["observed_at"] == NOW for r in rows)
    assert rows[0]["rank"] == 1 and rows[0]["watchlist_count"] == 150612


def test_stocktwits_strom_zaehlt_letzte_stunde_ohne_texte_und_autoren():
    raw = _fixture("echt_stocktwits_stream_gme.json")
    newest = datetime.fromisoformat(raw["messages"][0]["created_at"].replace("Z", "+00:00")).timestamp()
    row = stocktwits.normalise_stream("GME", raw, now=newest + 60)
    assert row["messages_sampled"] == 30 and row["source"] == "stocktwits"
    # Die Stichprobe umfasst zwei Stunden: nicht alle 30 liegen in der letzten Stunde.
    assert 0 < row["messages_1h"] < 30 and row["truncated_1h"] is False
    assert row["authors_1h"] <= row["messages_1h"] and row["bullish_1h"] >= 1
    assert row["mentions"] == row["messages_1h"]
    dumped = json.dumps(row)
    assert "Beitrag" not in dumped and "nutzer_" not in dumped and "body" not in dumped
    # Alle 30 in der letzten Stunde -> Untergrenze.
    tight = {**raw, "messages": [{**m, "created_at": raw["messages"][0]["created_at"]} for m in raw["messages"]]}
    assert stocktwits.normalise_stream("GME", tight, now=newest + 60)["truncated_1h"] is True


def test_stocktwits_activity_absolut_und_gegen_eigene_basis():
    assert stocktwits.activity(None)[0] is None
    duenn = {"messages_1h": 5, "authors_1h": 3}
    assert stocktwits.activity(duenn)[0] is None
    laut = {"messages_1h": 25, "authors_1h": 12}
    hit, gap = stocktwits.activity(laut)
    assert hit["kind"] == "STOCKTWITS_ABSOLUT" and gap is None
    basis = {"ready_14d": True, "median_mentions": 4}
    hit, _ = stocktwits.activity({"messages_1h": 14, "authors_1h": 7}, basis)
    assert hit["kind"] == "STOCKTWITS_WACHSTUM" and hit["ratio"] == pytest.approx(3.5)
    assert stocktwits.activity({"messages_1h": 8, "authors_1h": 7}, basis)[0] is None


def test_stocktwits_abruf_speichert_zeitreihe_und_pausiert_bei_fehler(monkeypatch):
    raw = _fixture("echt_stocktwits_stream_gme.json")
    newest = datetime.fromisoformat(raw["messages"][0]["created_at"].replace("Z", "+00:00")).timestamp()
    now = newest + 120
    session = FakeSession({stocktwits.STREAM_URL.format(symbol="GME"): FakeResponse(raw),
                           stocktwits.TRENDING_URL: FakeResponse(_fixture("echt_stocktwits_trending.json"))})
    row = stocktwits.stream("GME", now=now, session=session)
    assert row["messages_1h"] >= 1
    again = stocktwits.stream("GME", now=now + 60, session=session)
    assert again == row and len([c for c in session.calls if "GME" in c[1]]) == 1  # Cache eine Stunde
    from pulsar import research
    assert research.baseline("GME", "stocktwits", now=now + 86400)["hours"] >= 1
    assert research.social_source_status(now=now + 60)["stocktwits"]["state"] == "ok"
    trending = stocktwits.trending(now=now, session=session)
    assert trending and trending[0]["symbol"] == "BB"
    # Netzfehler -> Abrufpause, Status error, kein Nullwert
    broken = FakeSession({stocktwits.STREAM_URL.format(symbol="AMC"): ConnectionError("DNS")})
    with pytest.raises(Exception):
        stocktwits.stream("AMC", now=now, session=broken)
    from pulsar.control import Blocked
    with pytest.raises(Blocked):
        stocktwits.stream("AMC", now=now + 10, session=broken)
    status = research.social_source_status(now=now + 10)["stocktwits"]
    assert status["state"] == "backoff" and "UNKNOWN" in status["impact"]
    # Trending laeuft ueber dieselbe Naht wie die Reddit-Aggregate (Testdoubles greifen einheitlich).
    assert research.fetch_social("stocktwits", now=now, session=session)[0]["symbol"] == "BB"


def test_stocktwits_und_finra_sind_opt_in_und_ohne_schalter_ohne_abruf(monkeypatch):
    from pulsar import control, worker, research
    control.set_mode("BEOBACHTEN")
    assert control.settings().get("stocktwits") == 0 and control.settings().get("finra") == 0
    def boom(*a, **k):
        raise AssertionError("externer Abruf trotz ausgeschalteter Quelle")
    monkeypatch.setattr(stocktwits, "stream", boom)
    monkeypatch.setattr(stocktwits, "trending", boom)
    monkeypatch.setattr(short_interest, "fetch", boom)
    monkeypatch.setattr(research, "fetch_social", lambda source, **kw: boom() if source == "stocktwits" else [])
    class Client:
        konfiguriert = False
    monkeypatch.setattr("fmp_reference.client", lambda: Client())
    packet = worker.gather_market("HYPE")
    assert "stocktwits" not in packet and "short_interest" not in packet
    rows = research.discover()
    assert rows == [] or all(r.get("source") != "stocktwits_trending" for r in rows)
    state = control.set_mode("BEOBACHTEN", stocktwits=True, finra=True)
    assert state["stocktwits"] == 1 and state["finra"] == 1
    from pulsar.presentation import snapshot
    s = snapshot()
    assert s["stocktwits"] is True and s["finra"] is True and "finra_status" in s
    with pytest.raises(ValueError):
        control.set_mode("BEOBACHTEN", stocktwits="ja")


# ---------------------------------------------------------------------------
# FINRA Short Interest
# ---------------------------------------------------------------------------
def test_finra_stichtage_und_normalisierung():
    from datetime import date
    dates = short_interest.settlement_dates(date(2026, 9, 18))
    # 15.08.2026 ist ein Samstag -> FINRA-Stichtag Freitag 14.08. (so auch in der echten Antwort)
    assert dates[0] == date(2026, 9, 15) and dates[1] == date(2026, 8, 31) and dates[2] == date(2026, 8, 14)
    rows = _fixture("echt_finra_short_interest.json")
    gme = short_interest.normalise(rows, "GME")
    assert gme["settlement_date"] == "2026-08-31" and gme["short_shares"] == 56990026 and gme["days_to_cover"] == 9.72
    assert short_interest.normalise(rows, "ZZZZ") is None


def test_finra_abruf_findet_juengsten_stichtag_und_squeeze_merkmal(monkeypatch):
    session = FakeSession({"finra": _fixture("echt_finra_short_interest.json")})
    now = datetime(2026, 9, 18, 12, tzinfo=timezone.utc).timestamp()
    data = short_interest.fetch("GME", now=now, session=session)
    assert data["status"] == "OK" and data["settlement_date"] == "2026-08-31"
    assert [c[1] for c in session.calls] == ["2026-09-15", "2026-08-31"]  # 15.09. noch nicht veroeffentlicht (204)
    cached = short_interest.fetch("GME", now=now + 60, session=session)
    assert cached == data and len(session.calls) == 2
    profile = short_interest.squeeze_profile(data, 447_000_000, now=now)
    assert profile["status"] == "OK" and profile["flag"] is True and profile["short_ratio"] == pytest.approx(0.1275, abs=1e-3)
    assert "Days-to-Cover 9.7" in profile["detail"] and "nur Information" in profile["detail"]
    low = short_interest.squeeze_profile({**data, "days_to_cover": 1.2, "short_shares": 1_000_000}, 447_000_000, now=now)
    assert low["flag"] is False
    stale = short_interest.squeeze_profile(data, 447_000_000, now=now + 60*86400)
    assert stale["status"] == "STALE" and stale["flag"] is None
    assert short_interest.squeeze_profile(None)["flag"] is None
    missing = short_interest.fetch("ZZZZ", now=now, session=session)
    assert missing["status"] == "NOT_FOUND" and short_interest.squeeze_profile(missing)["status"] == "UNKNOWN"
    status = short_interest.status(now=now + 60)
    assert status["state"] == "ok" and status["symbols_cached"] == 2


# ---------------------------------------------------------------------------
# Relatives Volumen
# ---------------------------------------------------------------------------
def _ny(y, m, d, hh, mm):
    return datetime(y, m, d, hh, mm, tzinfo=volume_watch.NY).timestamp()


def test_relatives_volumen_aus_quote_zeitanteilig():
    stamp = _ny(2026, 9, 17, 11, 30)  # Mittwoch, 2 h nach Eroeffnung = 30.8 % der Sitzung
    quote = {"volume": 3_000_000, "avgVolume": 2_000_000, "timestamp": stamp, "price": 10.6, "previousClose": 10.0}
    out = volume_watch.from_quote(quote, now=stamp + 60)
    assert out["status"] == "OK" and out["rvol"] == pytest.approx(3_000_000 / (2_000_000 * 120/390), rel=1e-3)
    assert out["gain"] == pytest.approx(0.06)
    verdict = volume_watch.classify(out)
    assert verdict["trigger"] is True and verdict["confirm"] is True
    fallend = volume_watch.classify(volume_watch.from_quote({**quote, "price": 9.5}, now=stamp + 60))
    assert fallend["trigger"] is False and "nicht positiv" in fallend["reason"]
    assert volume_watch.from_quote(quote, now=stamp + 3600)["status"] == "STALE"
    pre = _ny(2026, 9, 17, 8, 0)
    assert volume_watch.from_quote({**quote, "timestamp": pre}, now=pre + 60)["status"] == "NOT_IN_SESSION"
    assert volume_watch.from_quote({**quote, "avgVolume": None}, now=stamp + 60)["status"] == "UNKNOWN"
    assert volume_watch.classify(None)["trigger"] is False


def test_relatives_volumen_aus_stundenkerzen_gegen_20_sitzungen():
    rows = []
    day = datetime(2026, 8, 17, tzinfo=volume_watch.NY)
    sessions = 0
    while sessions < 22:
        if day.weekday() < 5:
            for hour in (10, 11, 12, 13, 14, 15):
                stamp = day.replace(hour=hour).timestamp()
                vol = 1000 if sessions < 21 else 4000  # heutiger Tag: 4x
                rows.append((stamp, vol, 100.0 + (2.0 if sessions == 21 else 0.0)))
            sessions += 1
        day += timedelta(days=1)
    today = day - timedelta(days=1)
    while today.weekday() >= 5:
        today -= timedelta(days=1)
    now = today.replace(hour=15, minute=30).timestamp()
    out = volume_watch.from_hourly(rows, now=now)
    assert out["status"] == "OK" and out["rvol"] == pytest.approx(4.0) and out["sessions"] == 20
    assert out["gain"] == pytest.approx(0.02)
    seeds = volume_watch.scan_universe({"GOOGL": rows, "LEER": []}, now=now)
    assert [s["symbol"] for s in seeds] == ["GOOGL"] and seeds[0]["source"] == "volume_watch"
    assert volume_watch.from_hourly(rows[:30], now=now)["status"] in {"UNKNOWN", "NOT_IN_SESSION"}


# ---------------------------------------------------------------------------
# Bewertung: Ausloeser + zwei von drei Bestaetigungen
# ---------------------------------------------------------------------------
def test_volumen_ausbruch_ohne_social_wird_gemessen_aber_nicht_nominiert():
    card = _card(attention=_reddit_attention(mentions=150, previous=80), volume={"status": "OK", "rvol": 4.2, "gain": 0.06, "detail": "4,2x"})
    out = evidence.evaluate(card, now=NOW)
    assert out["hype"]["trigger"]["kind"] == "VOLUMEN" and out["state"] == "AUSLOESER"
    assert out["hype"]["confirmations"]["volumen"]["ok"] and out["hype"]["confirmations"]["kurs"]["ok"]
    assert out["eligible"] is False
    assert any("ohne Social-Familie" in m for m in out["missing"])
    assert out["rules_version"] == "PULSAR-2.2-TRIGGER-CONFIRM-MEASURED"


def test_reddit_spike_plus_stocktwits_als_zweite_familie_qualifiziert():
    st = {"messages_1h": 25, "authors_1h": 12, "bullish_1h": 20, "bearish_1h": 1}
    out = evidence.evaluate(_card(bars=_bars(gain=0.0, volume_multiple=1.0), stocktwits=st), now=NOW)
    assert out["hype"]["trigger"]["kind"] == "REDDIT+STOCKTWITS"
    conf = out["hype"]["confirmations"]
    assert conf["zweite_social_familie"]["ok"] and conf["zweite_social_familie"]["families"] == ["REDDIT", "STOCKTWITS"]
    assert conf["kurs"]["ok"] is False and conf["volumen"]["ok"] is False
    assert out["eligible"] is False and out["hype"]["confirmed_count"] == 1
    # Mit Kursbestaetigung (Tageskerze 5 % / 3,5x) sind es drei von drei.
    voll = evidence.evaluate(_card(stocktwits=st), now=NOW)
    assert voll["eligible"] is True and voll["hype"]["confirmed_count"] == 3
    names = [c["name"] for c in voll["checks"]]
    assert names[:7] == ["identitaet", "existenzrisiko", "ausloeser", "volumen", "zweite_social_familie", "kursbestaetigung", "squeeze_merkmal"]


def test_eigene_reddit_basis_zaehlt_als_spike_und_squeeze_bleibt_information():
    card = _card(attention=_reddit_attention(mentions=120, previous=100),  # Anbieter: nur 1,2x -> kein Spike
                 baseline={"ready_14d": True, "median_mentions": 30, "days": 15})
    out = evidence.evaluate(card, now=NOW)
    assert out["hype"]["social"]["kind"] == "REDDIT_EIGENE_BASIS" and out["hype"]["social"]["ratio"] == pytest.approx(4.0)
    finra = {"status": "OK", "settlement_date": datetime.fromtimestamp(NOW, timezone.utc).date().isoformat(),
             "short_shares": 90_000_000, "days_to_cover": 8.0}
    quote = {"provider": "FMP", "kind": "quote", "data": {"symbol": "HYPE", "price": 10.7, "previousClose": 10.0,
             "volume": 2_500_000.0, "timestamp": NOW - 100, "sharesOutstanding": 300_000_000}}
    out = evidence.evaluate(_card(short_interest=finra, sources=[quote]), now=NOW)
    assert out["hype"]["squeeze"]["flag"] is True and out["hype"]["squeeze"]["short_ratio"] == pytest.approx(0.3)
    assert next(c for c in out["checks"] if c["name"] == "squeeze_merkmal")["status"] == "JA"
    assert out["blocks"] == []  # Squeeze-Merkmal blockt nie
    ohne = evidence.evaluate(_card(), now=NOW)
    assert ohne["hype"]["squeeze"]["flag"] is None


def test_telegram_alarm_nennt_ausloeser_bestaetigungen_und_squeeze():
    from pulsar import telegram
    card = _card(stocktwits={"messages_1h": 25, "authors_1h": 12})
    card.update(evidence.evaluate(card, now=NOW))
    text = telegram.hype_text(card, mode="BEOBACHTUNG")
    assert "Auslöser VOLUMEN+REDDIT+STOCKTWITS" in text and "Bestätigt:" in text and "3 von 3" in text
    assert "Squeeze-Merkmal:" in text and "StockTwits" in text


# ---------------------------------------------------------------------------
# Kandidatenreihenfolge und Karte
# ---------------------------------------------------------------------------
def test_research_order_nimmt_frische_zusatzkandidaten_und_merkt_reddit_treffer():
    from pulsar.source_coordination import research_order
    rows = [_reddit_attention(mentions=200, previous=50) | {"symbol": "AAA"},
            _reddit_attention(mentions=150, previous=40) | {"symbol": "BBB"},
            _reddit_attention(mentions=90, previous=30) | {"symbol": "CCC"},
            _reddit_attention(mentions=80, previous=30) | {"symbol": "DDD"}]
    extra = [{"symbol": "VOL", "source": "volume_watch", "observed_at": NOW - 60, "rvol": 3.5, "gain": 0.04, "detail": "3,5x"},
             {"symbol": "DDD", "source": "stocktwits_trending", "observed_at": NOW - 60, "rank": 2},
             {"symbol": "ALT", "source": "volume_watch", "observed_at": NOW - 7200, "rvol": 5.0},
             {"symbol": "bad ticker", "source": "volume_watch", "observed_at": NOW}]
    ranked, pipeline = research_order(rows, [], now=NOW, extra_candidates=extra)
    symbols = [r["symbol"] for r in ranked]
    assert symbols[0] == "AAA" and "VOL" in symbols[:3] and set(symbols) == {"AAA", "BBB", "CCC", "DDD", "VOL"}
    ddd = next(r for r in ranked if r["symbol"] == "DDD")
    assert ddd["discovery_origin"] == "REDDIT_AND_STOCKTWITS" and ddd["stocktwits_discovery"]["rank"] == 2
    vol = next(r for r in ranked if r["symbol"] == "VOL")
    assert vol["discovery_origin"] == "VOLUMEN"
    assert pipeline["extra_received"] == 4 and pipeline["extra_valid"] == 2 and pipeline["x_invalid_count"] == 2


def test_build_card_traegt_stocktwits_finra_und_volumen_als_belege(monkeypatch):
    from pulsar import worker
    stamp = _ny(2026, 9, 17, 11, 30)
    attention = _reddit_attention() | {"symbol": "HYPE", "url": "https://apewisdom.io/api/v1.0/filter/all-stocks",
                                       "evidence_id": "e" * 64, "observation_source": "apewisdom", "rank": 3}
    quote = {"symbol": "HYPE", "price": 10.6, "previousClose": 10.0, "volume": 3_000_000, "avgVolume": 2_000_000, "timestamp": stamp}
    market = {"sources": [{"provider": "FMP", "kind": "quote", "symbol": "HYPE", "observed_at": stamp, "data": quote, "id": "q" * 64},
                          {"provider": "FMP", "kind": "profile", "symbol": "HYPE", "observed_at": stamp, "id": "p" * 64,
                           "data": {"symbol": "HYPE", "companyName": "Hype Corp", "price": 10.6, "marketCap": 2e9, "currency": "USD"}}],
              "profile": {"symbol": "HYPE", "companyName": "Hype Corp", "price": 10.6, "marketCap": 2e9, "currency": "USD"},
              "bars": [], "errors": [],
              "stocktwits": {"symbol": "HYPE", "messages_1h": 22, "authors_1h": 11, "bullish_1h": 18, "bearish_1h": 2,
                             "messages_sampled": 30, "truncated_1h": False, "observed_at": stamp, "url": "https://api.stocktwits.com/x"},
              "short_interest": {"symbol": "HYPE", "status": "OK", "settlement_date": "2026-08-31", "short_shares": 5e7,
                                 "previous_short_shares": 4e7, "average_daily_volume": 3e6, "days_to_cover": 6.1, "market": "NYSE",
                                 "observed_at": stamp, "url": "https://api.finra.org/x"}}
    card = worker.build_card(attention, market, None, now=stamp + 60)
    assert card["volume"]["status"] == "OK" and card["volume"]["rvol"] > 3
    assert card["stocktwits"]["messages_1h"] == 22 and card["short_interest"]["days_to_cover"] == 6.1
    providers = {s["provider"] for s in card["sources"]}
    assert {"StockTwits", "FINRA", "apewisdom"} <= providers
    st_source = next(s for s in card["sources"] if s["provider"] == "StockTwits")
    assert "body" not in json.dumps(st_source) and st_source["data"]["authors_1h"] == 11
    # Volumen-Saat ohne Quote: Stundenkerzen-Wert wird uebernommen.
    seed = {"symbol": "HYPE", "source": "volume_watch", "observed_at": stamp, "rvol": 3.3, "gain": 0.03, "mentions": None,
            "mentions_24h_ago": None, "url": "", "detail": "3,3x aus Stundenkerzen", "discovery": {"state": "VOLUMEN_AUSLOESER"}}
    card2 = worker.build_card(seed, {**market, "sources": [market["sources"][1]]}, None, now=stamp + 60)
    assert card2["volume"]["method"] == "HOURLY_CUMULATIVE_VS_20_SESSIONS" and card2["volume"]["rvol"] == 3.3
    assert "Volumen-Ausloeser" in card2["thesis"] and any(s["kind"] == "discovery_seed" for s in card2["sources"])


# ---------------------------------------------------------------------------
# Vorwaertsmessung
# ---------------------------------------------------------------------------
def _measured_card(symbol="HYPE", price=10.7, now=NOW, **overrides):
    quote = {"provider": "FMP", "kind": "quote", "data": {"symbol": symbol, "price": price, "previousClose": 10.0,
             "volume": 2_500_000.0, "timestamp": now - 100}}
    card = _card(symbol=symbol, sources=[quote], **overrides)
    card["attention"] = {**card["attention"], "symbol": symbol, "observed_at": now - 600}
    card.update(evidence.evaluate(card, now=now))
    return card


def test_messung_speichert_einen_ausloeser_je_symbol_und_tag(monkeypatch):
    # Fester Zeitpunkt mitten im NY-Handelstag: der Tageswechsel darf den Test nicht treffen.
    at = datetime(2026, 9, 17, 15, 0, tzinfo=measurement.NY).timestamp()
    card = _measured_card(now=at)
    assert measurement.trigger_of(card)
    row = measurement.record(card, now=at, mode="BEOBACHTEN")
    assert row and row["price_at"] == 10.7 and row["eligible"] == 1 and row["trigger_day"] == "2026-09-17"
    assert measurement.record(card, now=at + 600) is None  # gleicher Tag: keine zweite Zeile
    ohne = _card(attention=_reddit_attention(mentions=10, previous=10), bars=_bars(gain=0.0, volume_multiple=1.0))
    ohne.update(evidence.evaluate(ohne, now=NOW))
    assert measurement.trigger_of(ohne) is None and measurement.record(ohne, now=NOW) is None
    rows = measurement.rows()
    assert len(rows) == 1 and rows[0]["status"] == "OPEN" and rows[0]["trigger_kind"] == "REDDIT"
    public = measurement.public_row(rows[0])
    assert "evidence" not in public and public["symbol"] == "HYPE"


def test_messung_traegt_schluesse_nach_und_urteilt_erst_ab_30(monkeypatch):
    start = datetime(2026, 8, 3, 15, 0, tzinfo=measurement.NY).timestamp()  # Montag
    for i in range(3):
        card = _measured_card(symbol=f"SY{i}", now=start)
        measurement.record(card, now=start)
    def bars(symbol):
        base = datetime(2026, 8, 3, tzinfo=timezone.utc).date()
        out = []
        for d in range(0, 20):
            day = base + timedelta(days=d)
            if day.weekday() >= 5:
                continue
            factor = {"SY0": 1.05, "SY1": 0.95, "SY2": 1.10}[symbol]
            out.append({"date": day.isoformat(), "close": 10.7 * (factor if d > 0 else 1.0)})
        return out
    later = start + 3*86400  # Donnerstag: nur +1 faellig
    result = measurement.update_outcomes(bars, now=later)
    assert result["updated"] == 3
    rows = {r["symbol"]: r for r in measurement.rows()}
    assert rows["SY0"]["status"] == "PARTIAL" and rows["SY0"]["r1"] == pytest.approx(0.05) and rows["SY0"]["r5"] is None
    much_later = start + 20*86400
    measurement.update_outcomes(bars, now=much_later)
    rows = {r["symbol"]: r for r in measurement.rows()}
    assert all(r["status"] == "COMPLETE" for r in rows.values())
    assert rows["SY1"]["r10"] == pytest.approx(-0.05) and rows["SY2"]["r5"] == pytest.approx(0.10)
    summary = measurement.summary()
    assert summary["total"] == 3 and summary["horizons"]["5"]["n"] == 3
    assert summary["horizons"]["5"]["hit_rate"] == pytest.approx(2/3)
    assert summary["verdict"]["status"] == "ZU_WENIG_DATEN" and "3 von 30" in summary["verdict"]["reason"]
    # Ohne Kurse: nach 20 Handelstagen UNRESOLVABLE, nie null.
    measurement.record(_measured_card(symbol="LEER", now=start), now=start)
    measurement.update_outcomes(lambda s: [] if s == "LEER" else bars(s), now=start + 40*86400)
    assert {r["symbol"]: r["status"] for r in measurement.rows()}["LEER"] == "UNRESOLVABLE"


def test_urteil_regeln_und_diagnose_rechnen_identisch():
    import importlib.util
    spec = importlib.util.spec_from_file_location("nexus_diag", ROOT / "NEXUS_10_Diagnose.py")
    diag = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(diag)
    def rows(returns, **extra):
        return [{"symbol": f"S{i}", "trigger_day": "2026-08-01", "trigger_kind": "REDDIT" if i % 2 else "VOLUMEN",
                 "price_at": 10.0, "eligible": i % 3 == 0, "squeeze": (1 if i % 4 == 0 else 0), "r1": r, "r3": r, "r5": r, "r10": r,
                 "status": "COMPLETE", **extra} for i, r in enumerate(returns)]
    good = rows([0.03] * 20 + [-0.01] * 10)
    s = measurement.summarize(good)
    assert s["verdict"]["status"] == "ERFOLGREICH" and s["horizons"]["5"]["n"] == 30
    assert s["horizons"]["5"]["median_after_costs"] == pytest.approx(0.03 - 0.007)
    bad = rows([-0.02] * 18 + [0.02] * 12)
    assert measurement.summarize(bad)["verdict"]["status"] == "NICHT_ERFOLGREICH"
    few = rows([0.05] * 10)
    assert measurement.summarize(few)["verdict"]["status"] == "ZU_WENIG_DATEN"
    grenz = rows([0.02] * 16 + [-0.01] * 14)  # Trefferquote 53 % (< 55 %), Median nach Kosten +1,3 %
    assert measurement.summarize(grenz)["verdict"]["status"] == "UNKLAR"
    for sample in (good, bad, few, grenz):
        mine, theirs = measurement.summarize(sample), diag.pulsar_measurement_report(sample, {"status": "OK"})
        assert mine["verdict"]["status"] == theirs["verdict"]["status"]
        for h in ("1", "5", "10"):
            assert mine["horizons"][h]["hit_rate"] == pytest.approx(theirs["horizons"][h]["hit_rate"])
            assert mine["horizons"][h]["median_after_costs"] == pytest.approx(theirs["horizons"][h]["median_after_costs"])
        assert set(mine["by_trigger"]) == set(theirs["by_trigger"]) and set(mine["by_squeeze"]) == set(theirs["by_squeeze"])
    assert diag.pulsar_measurement_report([], {"status": "MISSING"})["verdict"]["status"] == "UNBEKANNT"


def test_worker_aktualisiert_messungen_hoechstens_alle_sechs_stunden(monkeypatch):
    from pulsar import worker, research
    calls = []
    monkeypatch.setattr(measurement, "update_outcomes", lambda provider, now=None: calls.append(now) or {"updated": 0})
    class Client:
        konfiguriert = False
    monkeypatch.setattr("fmp_reference.client", lambda: Client())
    assert worker.update_measurements(now=NOW)["updated"] == 0
    assert worker.update_measurements(now=NOW + 60) is None
    assert len(calls) == 1


# ---------------------------------------------------------------------------
# WebUI: PULSAR-Seite, Messungs-API, Diagnosebericht
# ---------------------------------------------------------------------------
def test_pulsar_seite_ist_aufgeraeumt_und_zeigt_messung():
    html = (ROOT / "webui/templates/pulsar.html").read_text(encoding="utf-8")
    js = (ROOT / "webui/static/pulsar.js").read_text(encoding="utf-8")
    for needle in ('id="p-messung"', 'id="p-verdict"', 'id="p-quellen"', 'id="p-mode"', "Beobachten und messen",
                   'id="p-stocktwits"', 'id="p-finra"',
                   "Messen und handeln", 'id="p-cards"', 'id="p-observation-weeks"', "/api/pulsar/export"):
        assert needle in html, needle
    for legacy in ("Community", "Fünf Beobachtungskandidaten", "Belegscore"):
        assert legacy not in html, legacy
    for needle in ("renderMeasurement", "measurementRows", "/api/pulsar/measurements", "pulsarHypeView", "Zweite Social-Familie",
                   "Squeeze-Merkmal", "Existenzrisiko", "pulsarXContextView", "pulsarAttentionView", "tradeRows"):
        assert needle in js, needle
    assert "community_checks" not in js and "communityChecks" not in js
    css = (ROOT / "webui/static/nexus.css").read_text(encoding="utf-8")
    assert ".verdict-ok" in css and ".chip-block" in css and ".report-page" in css


def test_snapshot_und_messungs_api(monkeypatch):
    from pulsar.presentation import snapshot
    s = snapshot()
    assert s["measurement"]["total"] == 0 and s["measurement"]["verdict"]["status"] == "ZU_WENIG_DATEN"
    assert s["demo_trades"]["closed"] == 0 and s["finra_status"]["state"] in {"unknown", "ok", "backoff"}
    assert s["stocktwits"] is False and s["finra"] is False
    assert s["rules_version"] == evidence.REVISION and "StockTwits" in s["source_note"]
    from fastapi.testclient import TestClient
    from test_v100_webui_backend import configured_auth
    from webui.app import app
    auth, _ = configured_auth(monkeypatch)
    session, _csrf = auth.issue_session("testuser")
    client = TestClient(app)
    assert client.get("/api/pulsar/measurements").status_code == 401
    client.cookies.set(auth.COOKIE, session)
    measurement.record(_measured_card(), now=NOW)
    data = client.get("/api/pulsar/measurements?limit=5").json()
    assert data["rows"][0]["symbol"] == "HYPE" and data["summary"]["total"] == 1
    assert "evidence" not in data["rows"][0]


def test_diagnosebericht_aus_verifizierter_zip(monkeypatch, tmp_path):
    from webui import diagnosis_jobs as jobs
    out = tmp_path / "NEXUS_Diagnosen"
    out.mkdir()
    monkeypatch.setattr(jobs, "output_dir", lambda: out)
    summary = {"schema": 1, "completion": "COMPLETED", "tool_version": "1.9.0", "runtime": {"okx": {"freshness": "FRESH", "buys_allowed": True}},
               "findings": {"counts": {"WARN": 1}, "items": [{"component": "okx", "level": "WARN", "code": "X"}]},
               "pulsar": {"measurement": {"total": 2, "verdict": {"status": "ZU_WENIG_DATEN", "reason": "2 von 30"}}}}
    archive = out / "NEXUS_10_Diagnose_test_run.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("ZUSAMMENFASSUNG.json", json.dumps(summary))
        z.writestr("META.json", json.dumps({"completion": "COMPLETED"}))
        z.writestr("BEFUNDE.json", json.dumps(summary["findings"]["items"]))
        z.writestr("BERICHT.md", "# NEXUS 10 – Diagnosebelege\n")
    identity = "b" * 32
    jobs.job_dir().mkdir(parents=True, exist_ok=True)
    row = {"id": identity, "status": "COMPLETED", "mode": "30min", "created_at": time.time(), "completion": "COMPLETED",
           "archive": str(archive), "archive_sha256": jobs._digest(archive), "telegram_status": "NOT_REQUESTED"}
    jobs._write(row)
    result = jobs.summary(identity)
    assert result["summary"]["completion"] == "COMPLETED" and result["summary"]["pulsar"]["measurement"]["total"] == 2
    assert result["report_markdown"].startswith("# NEXUS 10") and result["archive"]["members"] == 4
    # Aeltere ZIP ohne Zusammenfassung: Minimalsicht, keine Erfindung.
    old = out / "NEXUS_10_Diagnose_alt.zip"
    with zipfile.ZipFile(old, "w") as z:
        z.writestr("META.json", json.dumps({"completion": "INTERRUPTED_PARTIAL", "errors": [{"error": "OSError"}]}))
        z.writestr("BEFUNDE.json", json.dumps([{"level": "UNKNOWN", "code": "Y", "component": "etoro"}]))
    alt = "c" * 32
    jobs._write({"id": alt, "status": "COMPLETED", "mode": "instant", "created_at": time.time(), "archive": str(old),
                 "archive_sha256": jobs._digest(old), "telegram_status": "NOT_REQUESTED"})
    fallback = jobs.summary(alt)["summary"]
    assert fallback["fallback"] is True and fallback["findings"]["counts"] == {"UNKNOWN": 1} and fallback["completion"] == "INTERRUPTED_PARTIAL"
    # Manipulierte ZIP wird abgelehnt.
    archive.write_bytes(archive.read_bytes() + b"x")
    with pytest.raises(ValueError):
        jobs.summary(identity)
    from fastapi.testclient import TestClient
    from test_v100_webui_backend import configured_auth
    from webui.app import app
    auth, _ = configured_auth(monkeypatch)
    session, _csrf = auth.issue_session("testuser")
    client = TestClient(app)
    assert client.get(f"/api/diagnosis/{alt}/summary").status_code == 401
    assert client.get(f"/diagnosis/{alt}/bericht", follow_redirects=False).status_code == 303
    client.cookies.set(auth.COOKIE, session)
    assert client.get(f"/api/diagnosis/{alt}/summary").json()["summary"]["fallback"] is True
    assert client.get(f"/api/diagnosis/{identity}/summary").status_code == 404
    page = client.get(f"/diagnosis/{alt}/bericht")
    assert page.status_code == 200 and f'content="{alt}"' in page.text and "diagnosis_report.js" in page.text
    assert client.get("/diagnosis/zz/bericht").status_code == 404
    listed = client.get("/api/diagnosis").json()["jobs"]
    assert any(j["id"] == alt and j["download_url"] for j in listed)


def test_diagnose_zusammenfassung_dokument_und_bericht_abschnitt():
    import importlib.util
    spec = importlib.util.spec_from_file_location("nexus_diag2", ROOT / "NEXUS_10_Diagnose.py")
    diag = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(diag)
    assert diag.TOOL_VERSION == "1.9.0"
    analysis = {"window": {"start_utc": "a", "end_utc": "b"}, "sample_count": 3,
                "runtime": {"okx": {"freshness": "FRESH", "state": "RUNNING", "buy_readiness": {"kaeufe_erlaubt": False, "grund": "Reserve"},
                                    "tradable_capital": 1.0, "capital_currency": "EUR"}},
                "decisions": {"rows_observed_in_window": 4, "block_reasons": {"RISK_GATE": 4}, "export_status": "OK"},
                "gpt": {"local_dispatches_in_window": 1, "successful_results_in_window": 1, "cache_uses_in_window": 0, "failed_timed_out_discarded_in_window": 0},
                "pulsar": {"card_count": 1, "cards_status": "OK", "cards": [{"symbol": "GME", "state": "AUSLOESER", "eligible": False, "blocks": [], "missing": ["x"], "sources": [], "stages": []}],
                           "measurement": diag.pulsar_measurement_report([], {"status": "OK"})},
                "providers": {"pipeline": {"sources": [{"provider": "FMP", "state": "ok", "last_success": "t"}]}},
                "database_exports": {"decision_history.sqlite": {"status": "OK"}}, "limits": ["L"],
                "findings": [{"component": "okx", "level": "WARN", "code": "RISK_MANAGER_BUY_BLOCK", "meaning": "m"}]}
    meta = {"tool_version": "1.9.0", "run_id": "r", "completion": "COMPLETED", "observation_mode": "INSTANT_EXPORT",
            "started_utc": "a", "ended_utc": "b", "observed_seconds": 0, "source": "/x", "errors": [{"error": "OSError", "detail": "d"}]}
    doc = diag.summary_document(analysis, meta)
    assert doc["runtime"]["okx"]["buys_allowed"] is False and doc["findings"]["counts"] == {"WARN": 1}
    assert doc["pulsar"]["measurement"]["verdict"]["status"] == "ZU_WENIG_DATEN" and doc["decisions"]["block_reasons"] == {"RISK_GATE": 4}
    assert doc["errors"][0]["type"] == "OSError" and json.dumps(doc)  # serialisierbar
    findings = diag.diagnostic_findings({**analysis, "providers": {"pipeline": {}, "source_history": {"current": []}}, "new_evidence": {},
                                        "broker_history_results": {"rows": []}, "etoro_private_stream": {"scopes": []},
                                        "historical_cost_gaps": {"brokers": {}}})
    assert any(f["code"] == "PULSAR_MEASUREMENT_ZU_WENIG_DATEN" and f["level"] == "INFO" for f in findings)
    diag_js = (ROOT / "webui/static/diagnosis.js").read_text(encoding="utf-8")
    assert "/bericht" in diag_js and 'target="_blank"' in diag_js
    source = (ROOT / "NEXUS_10_Diagnose.py").read_text(encoding="utf-8")
    assert "ZUSAMMENFASSUNG.json" in source and "PULSAR-Vorwaertsmessung" in source


def test_release_pflichtdateien_und_alte_wrapper():
    import volltest
    for name in ("pulsar/stocktwits.py", "pulsar/short_interest.py", "pulsar/volume_watch.py", "pulsar/measurement.py",
                 "webui/templates/diagnosis_report.html", "webui/static/diagnosis_report.js",
                 "tests/fixtures/echt_stocktwits_stream_gme.json", "tests/fixtures/echt_stocktwits_trending.json",
                 "tests/fixtures/echt_finra_short_interest.json", "tests/test_v1050_pulsar_messung_quellen.py"):
        assert name in volltest.REQUIRED_RELEASE_FILES, name
        assert (ROOT / name).is_file(), name
    assert not (ROOT / "NEXUS_10_4_0_Diagnose.py").exists()
