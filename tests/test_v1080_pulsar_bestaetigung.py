"""10.8.0: Fuenf Funktionspunkte fuer PULSAR-Bestaetigungen und Luna.

WARUM DIESE TESTS EXISTIEREN
============================
Am 18.09.2026 (SanDisk) hatte die Hype-Karte den Ausloeser und eine
Bestaetigung, wurde aber erst um 15:50 NY stabil -- nach dem Einstiegsfenster.
Die Kursbestaetigung stand auf OFFEN, weil der FMP-Quote aus dem Cache zu alt
war, die Volumenzaehlung nur stundenweise lief und StockTwits die Stunde nur
als Untergrenze kannte. Fuenf Punkte, jeder mit eigenem Beleg:

1. FMP-Quote nachladen bei OFFEN -- nur in offener Sitzung, hoechstens fuenf
   Karten je Zyklus, dann neu bewerten.
2. StockTwits: aeltere Seiten bei Untergrenze, 15-Minuten-Takt fuer aktive
   Karten, Budget 400/Tag -- weiter nur Zaehlwerte.
3. X-Bestaetigungssuche auf Abruf fuer Ausloeser mit genau einer
   Bestaetigung; zaehlt als zweite Social-Familie, nie als eigener Platz.
4. eToro-15-Minuten-Kerzen fuer aktive Karten: Volumen und Kurs daraus, der
   Quote ist der Rueckfall; der Kern ruft ab (<= 20/h), PULSAR liest nur.
5. Luna-Tagesbudget 200 mit Feld in den Einstellungen; alte 40/50 werden
   beim Update einmalig angehoben.

Invarianten bleiben: UNKNOWN bleibt UNKNOWN, keine Orderbefugnis fuer PULSAR,
X, GPT oder StockTwits; jede Nominierung braucht Georgs Telegram-Bestaetigung.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import json
from pathlib import Path
import time

import pandas as pd
import pytest

from pulsar import control, evidence, research, stocktwits, volume_watch, worker
from test_v1030_pulsar_hype import _card as _karte_v1030, _reddit_attention as _reddit_v1030

ROOT = Path(__file__).resolve().parents[1]
NY = ZoneInfo("America/New_York")


def _ny(y, m, d, hh, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=NY).timestamp()


# Donnerstag, 17.09.2026, 14:00 NY: Sitzung offen, Einstiegsfenster offen.
NOW = _ny(2026, 9, 17, 14, 0)
SAMSTAG = _ny(2026, 9, 19, 14, 0)


def _bars(gain=0.06, volume_multiple=3.5, days=21, base_volume=1_000_000.0):
    """21 abgeschlossene Tageskerzen VOR dem festen NOW dieser Suite; letzter Tag traegt den Spike."""
    start = datetime.fromtimestamp(NOW, timezone.utc).date() - timedelta(days=days+1)
    rows = []
    for i in range(days):
        close = 10.0 if i < days-1 else 10.0*(1+gain)
        volume = base_volume if i < days-1 else base_volume*volume_multiple
        rows.append({"date": (start+timedelta(days=i)).isoformat(), "close": close, "high": close*1.01,
                     "low": close*0.99, "open": close, "volume": volume})
    return rows


def _reddit_attention(mentions=150, previous=40, observed=None):
    return _reddit_v1030(mentions, previous, NOW-600 if observed is None else observed)


def _card(**overrides):
    """Karte wie in test_v1030, aber mit dem festen NOW dieser Suite (Reddit-Beleg 10 min alt)."""
    card = _karte_v1030(attention=_reddit_attention(), bars=_bars())
    card.update(overrides)
    return card


def _quote_quelle(gain=0.06, multiple=1.5, symbol="HYPE"):
    return {"provider": "FMP", "kind": "quote", "symbol": symbol,
            "data": {"symbol": symbol, "price": 10*(1+gain), "previousClose": 10.0, "volume": 1_000_000*multiple,
                     "avgVolume": 1_000_000, "timestamp": NOW-60}}


def _eine_bestaetigung(symbol="HYPE", extra_sources=()):
    """Reddit-Ausloeser + Kurs per Quote (+6 %, 1,5x): genau eine der drei Bestaetigungen."""
    return _card(symbol=symbol, bars=_bars(gain=0.0, volume_multiple=1.0),
                 sources=[_quote_quelle(symbol=symbol), *extra_sources])


class _Instrument:
    def __init__(self, name, asset_type="stock"):
        self.name, self.asset_type, self.use_rth = name, asset_type, True


class _Broker:
    name = "etoro"

    def __init__(self, frames=None, qualified=()):
        self.frames, self.calls, self.qualified, self.qualify_calls = frames or {}, [], list(qualified), 0

    def account_fingerprint(self):
        return "acct-demo-1"

    def ist_paper(self):
        return True

    def historie(self, instrument, dauer, kerzengroesse, nur_handelszeiten=True):
        self.calls.append((instrument.name, dauer, kerzengroesse))
        return self.frames[kerzengroesse]

    def qualifiziere(self, instruments):
        self.qualify_calls += 1
        wanted = {i.name for i in instruments}
        return [q for q in self.qualified if q.name in wanted], []

    def instrument_metadata(self, inst):
        return {"asset_type": inst.asset_type}


def _frame15(now, *, sessions=24, base_volume=1000.0, today_volume=4000.0, gain=0.07, price=10.0):
    """15-Minuten-Kerzen: ``sessions`` volle Sitzungen mit Grundvolumen, heute bis ``now`` mit Spike."""
    rows = []
    day = datetime.fromtimestamp(now, NY).date()
    days, cursor = [], day - timedelta(days=1)
    while len(days) < sessions:
        if cursor.weekday() < 5:
            days.append(cursor)
        cursor -= timedelta(days=1)
    for d in reversed(days):
        for i in range(26):  # 09:30 .. 15:45
            stamp = datetime(d.year, d.month, d.day, 9, 30, tzinfo=NY) + timedelta(minutes=15*i)
            rows.append((stamp, price, base_volume))
    stamp = datetime(day.year, day.month, day.day, 9, 30, tzinfo=NY)
    i = 0
    while stamp.timestamp() + 900 <= now:
        rows.append((stamp, price*(1+gain*min(1.0, (i+1)/6)), today_volume))
        stamp += timedelta(minutes=15)
        i += 1
    index = pd.DatetimeIndex([r[0].astimezone(timezone.utc) for r in rows])
    closes = [r[1] for r in rows]
    return pd.DataFrame({"open": closes, "high": [c*1.001 for c in closes], "low": [c*0.999 for c in closes],
                         "close": closes, "volume": [r[2] for r in rows]}, index=index)


def _store_15m(symbol, now, **kw):
    import etoro_chart_store as store
    store.speichere("acct-demo-1", "DEMO", symbol, "15m", _frame15(now, **kw))
    return store


def _market(symbol="HYPE", stamp=NOW, gain=0.06, volume=3_000_000, avg=2_000_000, bars=None):
    quote = {"symbol": symbol, "price": 10*(1+gain), "previousClose": 10.0, "volume": volume,
             "avgVolume": avg, "timestamp": stamp}
    profile = {"symbol": symbol, "companyName": "Hype Corp", "price": quote["price"], "marketCap": 2e9,
               "currency": "USD", "isEtf": False, "isFund": False}
    return {"sources": [{"provider": "FMP", "kind": "quote", "symbol": symbol, "observed_at": stamp, "data": quote, "id": "q"*64},
                        {"provider": "FMP", "kind": "profile", "symbol": symbol, "observed_at": stamp, "id": "p"*64, "data": profile}],
            "profile": profile, "bars": bars if bars is not None else _bars(gain=0.0, volume_multiple=1.0), "errors": []}


def _attention(symbol="HYPE", **kw):
    return _reddit_attention() | {"symbol": symbol, "url": "https://apewisdom.io/api/v1.0/filter/all-stocks",
                                  "evidence_id": "e"*64, "observation_source": "apewisdom", "rank": 3,
                                  "observed_at": NOW-600} | kw


# ======================================================================
# Punkt 4: eToro-15-Minuten-Kerzen
# ======================================================================

def test_from_hourly_rechnet_auch_mit_15_minuten_kerzen():
    frame = _frame15(NOW)
    rows = [(int(ix.timestamp()), row["volume"], row["close"]) for ix, row in frame.iterrows()]
    out = volume_watch.from_hourly(rows, now=NOW, method=volume_watch.INTRADAY_METHOD)
    assert out["status"] == "OK" and out["method"] == "ETORO_15M_CUMULATIVE_VS_20_SESSIONS"
    assert out["rvol"] == pytest.approx(4.0) and out["gain"] > 0.05
    assert out["sessions"] == 20 and out["last_bar_minute"] == 13*60+45  # letzte abgeschlossene Kerze 13:45-14:00


def test_intraday_from_store_liest_nur_abgeschlossene_kerzen_und_meldet_frische(tmp_path):
    assert volume_watch.intraday_from_store("HYPE", now=NOW) == {
        "rows": [], "intraday": [], "saved_at": None, "fresh": False, "source": "ETORO_15M", "avg_day_volume": None}
    _store_15m("HYPE", NOW)
    out = volume_watch.intraday_from_store("HYPE", now=NOW)
    assert out["fresh"] is True and out["saved_at"] is not None
    assert out["avg_day_volume"] == pytest.approx(26*1000.0)  # Sitzungsvolumen derselben Quelle, ohne heute
    assert volume_watch.session_average_volume(out["rows"][:26*5], now=NOW) is None  # unter 10 Sitzungen
    assert all(ts + 900 <= NOW for ts, _, _ in out["rows"])
    last = datetime.fromisoformat(out["intraday"][-1]["date"])
    assert last.astimezone(NY).strftime("%H:%M") == "13:45"
    assert out["intraday"][-1]["volume"] == 4000.0
    # Zwei Stunden spaeter ohne neuen Abruf: nicht mehr frisch -> Quote ist der Rueckfall.
    assert volume_watch.intraday_from_store("HYPE", now=NOW+2*3600)["fresh"] is False


def test_frische_etoro_kerzen_bestaetigen_skalenfrei_und_verdraengen_den_quote_nie():
    bars = _bars(gain=0.0, volume_multiple=1.0)  # FMP-Tagesdurchschnitt 1 Mio -- eToro zaehlt anders
    frame = _frame15(NOW, today_volume=4000.0)     # eToro: 26 x 1000 je Sitzung, heute 18 x 4000 = 72k (2,8x)
    intraday = [{"date": ix.astimezone(NY).isoformat(), "close": row["close"], "volume": row["volume"]}
                for ix, row in frame.iterrows() if ix.astimezone(NY).date() == datetime.fromtimestamp(NOW, NY).date()]
    quote = {"symbol": "HYPE", "price": 10.1, "previousClose": 10.0, "volume": 5_000_000, "avgVolume": 1_000_000, "timestamp": NOW-60}
    quelle = [{"provider": "FMP", "kind": "quote", "symbol": "HYPE", "data": quote}]
    # Frische Kerzen (+7 %, 2,8x des EIGENEN Tagesdurchschnitts) bestaetigen -- der Quote (+1 %) haette nicht.
    card = _card(bars=bars, intraday=intraday, intraday_source="ETORO_15M", intraday_avg_day_volume=26_000.0, sources=quelle)
    out = evidence.evaluate(card, now=NOW)
    assert out["hype"]["price"]["kind"] == "INTRADAY" and out["hype"]["price"]["source"] == "ETORO_15M"
    assert out["hype"]["price"]["volume_multiple"] == pytest.approx(72_000/26_000)
    assert "eToro-15-Minuten-Kerzen" in out["hype"]["price"]["detail"]
    # Ohne eigenen Tagesdurchschnitt gibt es aus eToro-Kerzen KEINE Volumenaussage (nie gegen FMP gerechnet).
    out = evidence.evaluate(_card(bars=bars, intraday=intraday, intraday_source="ETORO_15M", sources=quelle), now=NOW)
    assert out["hype"]["price"] is None and any("ohne Vergleichsbasis" in m for m in out["missing"])
    # Kerzen sagen Nein (+1 %), der Quote sagt Ja (+8 %, 5x): der Quote bestaetigt -- Kerzen nehmen nichts weg.
    flach = [{**r, "close": 10.1} for r in intraday]
    ja = [{"provider": "FMP", "kind": "quote", "symbol": "HYPE", "data": {**quote, "price": 10.8}}]
    out = evidence.evaluate(_card(bars=bars, intraday=flach, intraday_source="ETORO_15M", intraday_avg_day_volume=26_000.0, sources=ja), now=NOW)
    assert out["hype"]["price"]["kind"] == "QUOTE"
    # Beide Nein: Meldung nennt Quote UND Kerzen.
    out = evidence.evaluate(_card(bars=bars, intraday=flach, intraday_source="ETORO_15M", intraday_avg_day_volume=26_000.0, sources=quelle), now=NOW)
    assert out["hype"]["price"] is None
    assert any("Quote nicht bestaetigt" in m and "Intraday nicht bestaetigt (eToro-15-Minuten-Kerzen)" in m for m in out["missing"])
    # Kerzen aelter als 45 Minuten: der Quote entscheidet allein.
    alt = [r for r in intraday if datetime.fromisoformat(r["date"]).timestamp() <= NOW-3600]
    out = evidence.evaluate(_card(bars=bars, intraday=alt, intraday_source="ETORO_15M", intraday_avg_day_volume=26_000.0, sources=ja), now=NOW)
    assert out["hype"]["price"]["kind"] == "QUOTE"
    # Ohne Quote gilt weiter die 2-Stunden-Regel fuer aeltere Kerzen.
    out = evidence.evaluate(_card(bars=bars, intraday=alt, intraday_source="ETORO_15M", intraday_avg_day_volume=26_000.0), now=NOW)
    assert out["hype"]["price"]["kind"] == "INTRADAY"


def test_build_card_nimmt_etoro_kerzen_fuer_volumen_und_kurs(tmp_path):
    _store_15m("HYPE", NOW)
    card = worker.build_card(_attention(), _market(), None, now=NOW)
    assert card["intraday_source"] == "ETORO_15M" and card["intraday_saved_at"] is not None
    assert card["intraday_avg_day_volume"] == pytest.approx(26_000.0)
    assert card["volume"]["method"] == volume_watch.INTRADAY_METHOD and card["volume"]["rvol"] == pytest.approx(4.0)
    heute = datetime.fromtimestamp(NOW, NY).date()
    assert sum(datetime.fromisoformat(r["date"]).astimezone(NY).date() == heute for r in card["intraday"]) == 18
    assert card["intraday"][-1]["close"] > 10.6
    out = evidence.evaluate(card, now=NOW)
    assert out["hype"]["price"]["kind"] == "INTRADAY" and out["hype"]["price"]["source"] == "ETORO_15M"
    assert out["hype"]["confirmations"]["volumen"]["ok"] and out["hype"]["confirmations"]["kurs"]["ok"]
    # Ohne Reihe: Quote wie bisher (kein erfundener Kerzenstand).
    card = worker.build_card(_attention("NOPE"), _market("NOPE"), None, now=NOW)
    assert card["intraday_source"] == "FMP" and card["intraday"] == [] and card["intraday_avg_day_volume"] is None
    assert card["volume"]["method"] == "FMP_QUOTE_VS_AVGVOLUME"


def test_ergaenze_fuer_karten_hoechstens_fuenf_reihen_mit_ruhezeit(tmp_path):
    import etoro_chart_store as store
    store._LAST_FETCH.clear()
    broker = _Broker({"15 mins": _frame15(NOW)})
    instruments = [_Instrument(s) for s in ("AAA", "BBB", "CCC", "DDD", "EEE", "FFF", "GGG")]
    assert store.ergaenze_fuer_karten(broker, instruments, now=NOW) == 5
    assert [c[0] for c in broker.calls] == ["AAA", "BBB", "CCC", "DDD", "EEE"]
    assert {c[2] for c in broker.calls} == {"15 mins"} and {c[1] for c in broker.calls} == {"17 D"}
    assert store.lade_neueste("AAA", "15m")["rows_total"] > 0
    # Ruhezeit: 15 Minuten je Reihe, danach wieder -- hoechstens 20 Abrufe je Stunde.
    assert store.ergaenze_fuer_karten(broker, instruments, now=NOW+60) == 0
    assert store.ergaenze_fuer_karten(broker, instruments, now=NOW+store.REFRESH_SECONDS+1) == 5
    assert store.ergaenze_fuer_karten(_Broker(), [], now=NOW) == 0
    okx = _Broker({"15 mins": _frame15(NOW)}); okx.name = "okx"
    assert store.ergaenze_fuer_karten(okx, instruments, now=NOW+9999) == 0
    assert store.ergaenze_fuer_karten(broker, [_Instrument("BTC", "crypto")], now=NOW+9999) == 0


def test_lade_neueste_nimmt_die_juengste_reihe_ueber_konten_hinweg(tmp_path):
    import etoro_chart_store as store
    store.speichere("acct-live-9", "LIVE", "HYPE", "15m", _frame15(NOW-86400*7))
    time.sleep(0.01)
    store.speichere("acct-demo-1", "DEMO", "HYPE", "15m", _frame15(NOW))
    out = store.lade_neueste("HYPE", "15m")
    assert out["rows_total"] > 0 and out["candles"][-1]["volume"] == 4000.0
    assert store.lade_neueste("NIX", "15m")["candles"] == []
    with pytest.raises(ValueError):
        store.lade_neueste("HYPE", "5m")


def test_aktive_karten_instrumente_nur_ausloeser_und_hype_in_offener_sitzung(tmp_path):
    from pulsar import core
    from instrument_identity import canonical_key
    core._KARTEN_INSTRUMENTE.clear()
    cards = [{"symbol": "AAA", "state": "AUSLOESER", "attention_rank": 2},
             {"symbol": "BBB", "state": "BEOBACHTUNG", "attention_rank": 1},
             {"symbol": "CCC", "state": "HYPE_KANDIDAT", "attention_rank": 3},
             {"symbol": "DDD", "state": "AUSLOESER", "attention_rank": 4}]
    research.cache_put("top5", cards, 3600, now=NOW)
    known = {canonical_key("AAA", "stock"): _Instrument("AAA")}
    broker = _Broker(qualified=[_Instrument("CCC")])
    # PULSAR AUS: nichts.
    assert core.aktive_karten_instrumente(broker, known, now=NOW) == []
    control.set_mode("BEOBACHTEN")
    out = core.aktive_karten_instrumente(broker, known, now=NOW)
    assert [i.name for i in out] == ["AAA", "CCC"]
    assert broker.qualify_calls == 2  # CCC gefunden, DDD nicht -- beides gemerkt
    out = core.aktive_karten_instrumente(broker, known, now=NOW+600)
    assert [i.name for i in out] == ["AAA", "CCC"] and broker.qualify_calls == 2
    assert core.aktive_karten_instrumente(broker, known, now=SAMSTAG) == []
    assert core.aktive_karten_instrumente(broker, known, now=NOW, limit=1) == [known[canonical_key("AAA", "stock")]]


def test_kern_ruft_kerzen_fuer_karten_nach_den_trades_ab():
    src = (ROOT / "live_trader.py").read_text(encoding="utf-8")
    assert "ergaenze_fuer_karten(broker, pulsar_core.aktive_karten_instrumente(broker, instrument_by_symbol))" in src
    assert src.index("ergaenze_fuer_trades(broker, instrument_by_symbol)") < src.index("ergaenze_fuer_karten(broker")
    # PULSAR selbst bekommt keinen Broker: der Kerzenspeicher wird nur gelesen.
    for rel in ("pulsar/volume_watch.py", "pulsar/worker.py", "pulsar/evidence.py"):
        text = (ROOT / rel).read_text(encoding="utf-8")
        assert "broker.historie(" not in text and "ergaenze_fuer_karten(" not in text


# ======================================================================
# Punkt 1: FMP-Quote nachladen bei OFFEN
# ======================================================================

class _FakeFMP:
    konfiguriert = True

    def __init__(self, now, gain=0.08, volume=6_000_000):
        self.now, self.gain, self.volume, self.calls = now, gain, volume, []

    def quote(self, symbol, *, purpose="automatic", max_age=None):
        self.calls.append((symbol, max_age))
        return {"symbol": symbol, "price": 10*(1+self.gain), "previousClose": 10.0, "volume": self.volume,
                "avgVolume": 2_000_000, "timestamp": self.now-30}

    def cached_source(self, path, params):
        return {"saved": self.now-5}


def _offene_karten(n, now=NOW):
    cards, markets = [], {}
    for i in range(n):
        symbol = f"OF{i}"
        market = _market(symbol, stamp=now-3000, gain=0.01, volume=500_000)  # alter, unbestaetigter Quote
        card = worker.build_card(_attention(symbol), market, None, now=now)
        card.update(evidence.evaluate(card, now=now))
        cards.append(card); markets[symbol] = market
    return cards, markets


def test_refresh_quotes_laedt_nur_offene_karten_in_offener_sitzung_hoechstens_fuenf(monkeypatch, tmp_path):
    import fmp_reference
    control.set_mode("BEOBACHTEN")
    fake = _FakeFMP(NOW)
    monkeypatch.setattr(fmp_reference, "client", lambda: fake)
    cards, markets = _offene_karten(7)
    assert all(c["state"] == "AUSLOESER" and worker._quote_offen(c) for c in cards)
    done = worker.refresh_quotes(cards, markets, now=NOW)
    assert len(done) == 5 and len(fake.calls) == 5
    assert all(age == worker.QUOTE_REFRESH_MAX_AGE for _, age in fake.calls)
    for card in done:
        card.update(evidence.evaluate(card, now=NOW))
        assert card["quote_refreshed_at"] == NOW
        assert card["hype"]["confirmations"]["kurs"]["ok"] and card["hype"]["price"]["kind"] == "QUOTE"
        assert card["state"] == "HYPE_KANDIDAT"
    # Bereits belegte Karten und Karten ohne Ausloeser werden nicht nachgeladen.
    fake.calls.clear()
    assert worker.refresh_quotes(done, markets, now=NOW) == [] and fake.calls == []
    # Ausserhalb der Sitzung: kein Abruf (es gaebe keinen neuen Stand).
    cards, markets = _offene_karten(2)
    assert worker.refresh_quotes(cards, markets, now=SAMSTAG) == [] and fake.calls == []
    assert worker.refresh_quotes(cards, markets, now=_ny(2026, 9, 17, 8, 0)) == [] and fake.calls == []
    # PULSAR AUS: kein Abruf.
    control.set_mode("AUS")
    assert worker.refresh_quotes(cards, markets, now=NOW) == [] and fake.calls == []


def test_refresh_quotes_fehler_lassen_die_karte_unveraendert(monkeypatch, tmp_path):
    import fmp_reference
    control.set_mode("BEOBACHTEN")

    class Kaputt(_FakeFMP):
        def quote(self, symbol, **kw):
            raise RuntimeError("FMP nicht erreichbar")

    monkeypatch.setattr(fmp_reference, "client", lambda: Kaputt(NOW))
    cards, markets = _offene_karten(1)
    vorher = dict(cards[0]["hype"])
    assert worker.refresh_quotes(cards, markets, now=NOW) == []
    assert cards[0]["hype"] == vorher and any("Quote-Nachladen" in e for e in cards[0]["errors"])


def test_fmp_quote_max_age_umgeht_einen_aelteren_cache(monkeypatch):
    import fmp_service
    saved = time.time()-600
    calls = []

    class Store:
        scope = "t"
        path = Path("x") / "fmp.sqlite"

        def permits(self, path, probe=False):
            return True

        def cached(self, key):
            return {"saved": saved, "expires": saved+900, "data": [{"symbol": "HYPE", "price": 1}]}

        def metric(self, name):
            calls.append(name)

        def reserve(self, *a, **k):
            raise RuntimeError("Netz im Test gesperrt")

    class Lock:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(fmp_service, "critical_state_lock", Lock)
    # Ohne max_age: Cache-Treffer. Mit max_age 120 s: Cache zu alt -> Abrufpfad (hier: gesperrt).
    assert fmp_service.request(Store(), None, "k", "/quote", {"symbol": "HYPE"}) == [{"symbol": "HYPE", "price": 1}]
    assert calls == ["cache_hits"]
    with pytest.raises(RuntimeError, match="Netz im Test gesperrt"):
        fmp_service.request(Store(), None, "k", "/quote", {"symbol": "HYPE"}, max_age=120)
    assert fmp_service.request(Store(), None, "k", "/quote", {"symbol": "HYPE"}, max_age=3600)[0]["price"] == 1


# ======================================================================
# Punkt 2: StockTwits-Ausbau
# ======================================================================

class _Resp:
    def __init__(self, payload, status=200):
        self.status_code, self.headers = status, {}
        self._body = json.dumps(payload).encode("utf-8")

    def iter_content(self, chunk_size=32768):
        yield self._body

    def raise_for_status(self):
        pass

    def close(self):
        pass


class _Session:
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def get(self, url, **kwargs):
        self.calls.append(url)
        if url not in self.routes:
            raise RuntimeError("nicht gefunden: " + url)
        return _Resp(self.routes[url])


def _page(first_id, now, *, ages, users=100):
    return {"symbol": {"symbol": "GME", "watchlist_count": 1234},
            "messages": [{"id": first_id-i, "created_at": datetime.fromtimestamp(now-age, timezone.utc).isoformat().replace("+00:00", "Z"),
                          "user": {"id": users+i}, "body": "nicht gespeichert",
                          "entities": {"sentiment": {"basic": "Bullish"}}} for i, age in enumerate(ages)]}


def _stocktwits_routes(now, url):
    seite1 = _page(2000, now, ages=[60*i for i in range(30)])            # alle 30 juenger als eine Stunde
    seite2 = _page(1970, now, ages=[3000+120*i for i in range(30)], users=200)  # 6 in der Stunde, Rest aelter
    return {url: seite1, url+"?max=1970": seite2}


def test_stocktwits_laedt_aeltere_seiten_bis_die_stunde_vollstaendig_ist(tmp_path):
    control.set_mode("BEOBACHTEN")
    url = stocktwits.STREAM_URL.format(symbol="GME")
    session = _Session(_stocktwits_routes(NOW, url))
    row = stocktwits.stream("GME", now=NOW, session=session, active=True)
    assert session.calls == [url, url+"?max=1970"]
    assert row["pages"] == 2 and row["messages_sampled"] == 60
    assert row["messages_1h"] == 36 and row["authors_1h"] == 36 and row["truncated_1h"] is False
    assert "body" not in json.dumps(row) and "user" not in json.dumps(row)  # weiter nur Zaehlwerte
    with research.db(readonly=True) as con:
        assert con.execute("SELECT count(*) FROM usage WHERE kind='stocktwits'").fetchone()[0] == 2
    # Aktive Karte: 15-Minuten-Takt -- nach 10 Minuten Cache, nach 16 Minuten neu.
    assert stocktwits.stream("GME", now=NOW+600, session=session, active=True) == row and len(session.calls) == 2
    again = stocktwits.stream("GME", now=NOW+960, session=session, active=True)
    assert again["observed_at"] == NOW+960 and len(session.calls) >= 3


def test_stocktwits_hoechstens_drei_folgeseiten_und_stundentakt_ohne_aktive_karte(tmp_path):
    control.set_mode("BEOBACHTEN")
    url = stocktwits.STREAM_URL.format(symbol="AMC")
    routes, first = {}, 5000
    for k in range(6):  # sechs Seiten, alle komplett in der Stunde
        page = _page(first, NOW, ages=[10*i+k for i in range(30)])
        routes[url if k == 0 else f"{url}?max={first}"] = page
        first -= 30
    session = _Session(routes)
    row = stocktwits.stream("AMC", now=NOW, session=session, active=False)
    assert row["pages"] == 1 + stocktwits.MAX_EXTRA_PAGES and len(session.calls) == 4
    assert row["truncated_1h"] is True and row["messages_1h"] == 120
    # Nicht aktiv: eine Stunde Cache.
    assert stocktwits.stream("AMC", now=NOW+1000, session=session, active=False) == row and len(session.calls) == 4


def test_stocktwits_403_ist_eine_anbietersperre_mit_vier_stunden_pause(tmp_path):
    control.set_mode("BEOBACHTEN")
    url = stocktwits.STREAM_URL.format(symbol="RARE")

    class Verboten(_Session):
        def get(self, url, **kwargs):
            self.calls.append(url)
            return _Resp({"error": "forbidden"}, status=403)

    session = Verboten({})
    from pulsar.control import Blocked
    with pytest.raises(Blocked, match="403"):
        stocktwits.stream("RARE", now=NOW, session=session)
    pause = research.cached("backoff:stocktwits", now=NOW)
    assert pause["data"]["status"] == 403 and pause["expires"] - NOW == pytest.approx(stocktwits.FORBIDDEN_PAUSE)
    with pytest.raises(Blocked, match="Abrufpause"):
        stocktwits.stream("RARE", now=NOW+3*3600, session=session)
    assert len(session.calls) == 1
    assert "UNKNOWN" in research.social_source_status(now=NOW+10)["stocktwits"]["impact"]


def test_stocktwits_budget_und_takte_10_8_0():
    assert research.LIMITS["stocktwits"] == (400, 2000)
    assert stocktwits.TRENDING_TTL == 900 and stocktwits.STREAM_TTL == 3600 and stocktwits.STREAM_TTL_ACTIVE == 900
    assert stocktwits.oldest_id({"messages": [{"id": 7}, {"id": 3}, {"id": "x"}]}) == 3
    assert stocktwits.oldest_id({"messages": []}) is None
    src = (ROOT / "pulsar" / "worker.py").read_text(encoding="utf-8")
    assert "stocktwits_stream(symbol, active=symbol in research.aktive_symbole())" in src


def test_aktive_symbole_aus_der_juengsten_bewertung(tmp_path):
    assert research.aktive_symbole() == set()
    research.cache_put("top5", [{"symbol": "aaa", "state": "AUSLOESER"}, {"symbol": "BBB", "state": "BEOBACHTUNG"},
                                {"symbol": "CCC", "state": "HYPE_KANDIDAT"}], 3600)
    assert research.aktive_symbole() == {"AAA", "CCC"}


# ======================================================================
# Punkt 3: X-Bestaetigungssuche auf Abruf
# ======================================================================

@pytest.fixture
def x_aktiv(monkeypatch, tmp_path):
    from market_intelligence import service as x, store
    monkeypatch.setattr(store, "ROOT", tmp_path)
    monkeypatch.setattr(x.time, "time", lambda: NOW)
    x.save_settings({"enabled": True, "pricing_acknowledged": True}, bearer_token="fake-offline-token")
    return x


def _profile(symbol, now=NOW, **changes):
    return {"id": "f"*64, "provider": "FMP", "kind": "profile", "observed_at": now,
            "data": {"symbol": symbol, "isEtf": False, "isFund": False, "companyName": "Hype Corp Inc.", **changes}}


def _x_post(identity, symbol, author, now=NOW):
    return {"id": identity, "author_id": author, "created_at": datetime.fromtimestamp(now-300, timezone.utc).isoformat().replace("+00:00", "Z"),
            "text": "$"+symbol+" squeeze incoming, post "+str(identity), "entities": {"urls": []}}


def test_x_bestaetigung_geht_vor_die_slotsuchen_und_zaehlt_hoechstens_acht_je_tag(x_aktiv):
    from market_intelligence import candidate_research as cr, store, request_confirmation
    x = x_aktiv
    out = request_confirmation("HYPE", _profile("HYPE"), now=NOW)
    assert out["accepted"] and out["daily_used"] == 1 and out["daily_limit"] == 8
    assert request_confirmation("HYPE", _profile("HYPE"), now=NOW)["reason"] == "heute bereits angefordert"
    assert not request_confirmation("SPY", _profile("SPY", isEtf=True), now=NOW)["accepted"]
    with store.db(readonly=True) as con:
        plan = cr.plan(con, NOW)
    assert plan["context"] == cr.CONFIRM_CONTEXT and plan["symbols"] == ["HYPE"]
    assert "$HYPE" in plan["query"] and '"Hype Corp Inc."' in plan["query"] and plan["slot"].startswith("confirm:HYPE:")
    # Eine Slot-Suche mit dem falschen Kontext wird nicht reserviert, solange die Bestaetigung ansteht.
    assert x._reserve("search", plan["query"], cr.CONTEXT, plan["slot"], NOW) is None
    req = x._reserve("search", plan["query"], cr.CONFIRM_CONTEXT, plan["slot"], NOW)
    assert req and req["context"] == cr.CONFIRM_CONTEXT
    posts = [_x_post(str(300+i), "HYPE", str(500+i % 6)) for i in range(9)]
    received, processed, duplicates, coverage = x._posts(req, {"data": posts}, NOW)
    x._finish(req, NOW, status="OK", received=received, processed=processed, duplicates=duplicates, coverage=coverage)
    with store.db(readonly=True) as con:
        res = cr.for_symbol(con, "HYPE", NOW)
        assert cr.plan(con, NOW) is None  # erledigt; keine zweite Suche
    assert res["state"] == "PROCESSED" and res["usable_posts"] == 9 and res["distinct_accounts_in_sample"] == 6
    assert res["confirmation"]["confirm_attempted_at"] == NOW and res["confirmation"]["confirm_request_id"] == req["id"]
    assert not res["trade_effect"] and not res["primary_source_confirmed"]
    # Tageslimit: acht Bestaetigungssuchen, die neunte wird abgelehnt.
    for i in range(7):
        assert request_confirmation(f"SYM{i}", _profile(f"SYM{i}"), now=NOW)["accepted"]
    assert "ausgeschoepft" in request_confirmation("SYM9", _profile("SYM9"), now=NOW)["reason"]
    with store.db(readonly=True) as con:
        snap = cr.snapshot(con, NOW)
    assert snap["confirmations_per_day_max"] == 8 and snap["searches_per_day_max"] == 5


def test_x_bestaetigung_zaehlt_als_zweite_social_familie_nie_als_ausloeser_oder_eigener_platz():
    # Reddit-Ausloeser, Kurs belegt (Quote 1,5x), Volumen offen, StockTwits leise -> genau eine Bestaetigung.
    out = evidence.evaluate(_eine_bestaetigung(), now=NOW)
    assert out["state"] == "AUSLOESER" and out["hype"]["confirmed_count"] == 1
    assert out["hype"]["confirmations"]["kurs"]["ok"] and not out["hype"]["confirmations"]["volumen"]["ok"]
    ctx = {"candidate_research": {"confirmation": {"confirm_requested_at": NOW-900, "confirm_attempted_at": NOW-600},
                                  "state": "PROCESSED", "processed_at": NOW-600, "usable_posts": 9,
                                  "distinct_accounts_in_sample": 6}}
    out = evidence.evaluate(_eine_bestaetigung() | {"x_context": ctx}, now=NOW)
    assert out["hype"]["x_confirmation"]["kind"] == "X_BESTAETIGUNG"
    assert out["hype"]["confirmations"]["zweite_social_familie"]["ok"] and "X" in out["hype"]["confirmations"]["zweite_social_familie"]["families"]
    assert out["hype"]["confirmed_count"] == 2 and out["state"] == "HYPE_KANDIDAT"
    assert "X" not in out["hype"]["trigger"]["kind"] and out["hype"]["trigger"]["family"] == "REDDIT"
    assert set(out["hype"]["confirmations"]) == {"volumen", "zweite_social_familie", "kurs"}  # kein vierter Platz
    # Zu duenn, veraltet oder noch offen: keine Familie, Luecke benannt.
    duenn = {**ctx, "candidate_research": {**ctx["candidate_research"], "usable_posts": 4}}
    assert evidence.evaluate(_eine_bestaetigung() | {"x_context": duenn}, now=NOW)["hype"]["confirmed_count"] == 1
    alt = {**ctx, "candidate_research": {**ctx["candidate_research"], "processed_at": NOW-2*86400}}
    out = evidence.evaluate(_eine_bestaetigung() | {"x_context": alt}, now=NOW)
    assert out["hype"]["confirmed_count"] == 1 and any("aelter als 24 Stunden" in m for m in out["missing"])
    offen = {**ctx, "candidate_research": {**ctx["candidate_research"], "state": "COLLECTING"}}
    out = evidence.evaluate(_eine_bestaetigung() | {"x_context": offen}, now=NOW)
    assert out["hype"]["confirmed_count"] == 1 and any("X-Bestaetigungssuche: COLLECTING" in m for m in out["missing"])
    # Eine X-Entdeckung (Ausloeser X) bekommt keine zweite X-Familie aus der Suche.
    attention = {"symbol": "HYPE", "source": "X", "observed_at": NOW-3600,
                 "x_discovery": {"sampled_post_count": 9, "distinct_accounts_in_sample": 6}}
    out = evidence.evaluate(_eine_bestaetigung() | {"attention": attention, "x_context": ctx}, now=NOW)
    assert out["hype"]["confirmations"]["zweite_social_familie"]["ok"] is False


def test_worker_fordert_x_bestaetigung_nur_fuer_ausloeser_mit_genau_einer_bestaetigung(monkeypatch, tmp_path):
    import market_intelligence
    from pulsar import core
    monkeypatch.setattr(core, "entry_window", lambda now=None: True)
    calls = []
    monkeypatch.setattr(market_intelligence, "request_confirmation",
                        lambda symbol, profile_source, *, now=None: calls.append((symbol, (profile_source or {}).get("kind")))
                        or {"accepted": True, "reason": "ok"})
    profile = [{"provider": "FMP", "kind": "profile", "symbol": "HYPE", "data": {"symbol": "HYPE"}}]
    eins = _eine_bestaetigung(extra_sources=profile); eins.update(evidence.evaluate(eins, now=NOW))
    zwei = _card(symbol="ZWEI", sources=profile); zwei.update(evidence.evaluate(zwei, now=NOW))          # HYPE_KANDIDAT
    null = _card(symbol="NULL", bars=_bars(gain=0.0, volume_multiple=1.0), sources=profile); null.update(evidence.evaluate(null, now=NOW))
    assert (eins["state"], zwei["state"], null["state"]) == ("AUSLOESER", "HYPE_KANDIDAT", "AUSLOESER")
    assert (eins["hype"]["confirmed_count"], null["hype"]["confirmed_count"]) == (1, 0)
    assert worker.request_x_confirmations([eins, zwei, null], now=NOW) == ["HYPE"]
    assert calls == [("HYPE", "profile")] and eins["x_confirmation"]["accepted"] is True
    # Einmal je Symbol und Tag; Fenster zu -> nichts.
    assert worker.request_x_confirmations([eins], now=NOW+60) == [] and len(calls) == 1
    monkeypatch.setattr(core, "entry_window", lambda now=None: False)
    drei = _eine_bestaetigung("DREI", extra_sources=profile); drei.update(evidence.evaluate(drei, now=NOW))
    assert worker.request_x_confirmations([drei], now=NOW) == [] and len(calls) == 1


def test_x_bestaetigung_ohne_eingerichtetes_x_wird_nicht_angenommen(monkeypatch, tmp_path):
    from market_intelligence import store, request_confirmation
    monkeypatch.setattr(store, "ROOT", tmp_path)
    assert request_confirmation("HYPE", _profile("HYPE"), now=NOW) == {"accepted": False, "reason": "X-Recherche nicht eingerichtet"}


# ======================================================================
# Punkt 5: Luna-Tagesbudget 200
# ======================================================================

def test_luna_tagesbudget_standard_200_und_einmalige_anhebung(tmp_path):
    import settings_migration as sm
    from types import SimpleNamespace as NS
    from ai_budget import AIBudget
    assert "AI_LUNA_MAX_CALLS_PER_DAY = 200" in (ROOT / "config.py").read_text(encoding="utf-8")
    assert AIBudget(tmp_path / "b.json", NS()).grenze("luna") == 200
    assert AIBudget(tmp_path / "b.json", NS(AI_LUNA_MAX_CALLS_PER_DAY=50)).grenze("luna") == 50
    path = tmp_path / "ai_router_settings.json"
    copied, skipped = [], []
    sm._migrate_v1080_luna_tagesbudget(tmp_path, copied, skipped)   # ohne Datei: nichts
    assert copied == [] and skipped == []
    path.write_text(json.dumps({"enabled": True, "luna_max_calls_per_day": 50, "max_cost_per_day_usd": 5.0}), encoding="utf-8")
    sm._migrate_v1080_luna_tagesbudget(tmp_path, copied, skipped)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["luna_max_calls_per_day"] == 200 and data["max_cost_per_day_usd"] == 5.0 and data["enabled"] is True
    assert data["luna_max_calls_per_day_angehoben"] == {"vorher": 50, "version": "10.8.0"}
    assert copied == ["ai_router_settings.json: Luna-Tagesbudget 50 -> 200"]
    # Zweiter Lauf und spaeter bewusst gesetzte 30: bleiben stehen.
    data["luna_max_calls_per_day"] = 30
    path.write_text(json.dumps(data), encoding="utf-8")
    sm._migrate_v1080_luna_tagesbudget(tmp_path, copied, skipped)
    assert json.loads(path.read_text(encoding="utf-8"))["luna_max_calls_per_day"] == 30 and len(copied) == 1
    # Ein Wert ueber 50 war schon eine bewusste Wahl.
    path.write_text(json.dumps({"luna_max_calls_per_day": 120}), encoding="utf-8")
    sm._migrate_v1080_luna_tagesbudget(tmp_path, copied, skipped)
    assert json.loads(path.read_text(encoding="utf-8"))["luna_max_calls_per_day"] == 120
    # Alter Standard ohne Eintrag (40) wird angehoben.
    path.write_text(json.dumps({"enabled": True}), encoding="utf-8")
    sm._migrate_v1080_luna_tagesbudget(tmp_path, copied, skipped)
    assert json.loads(path.read_text(encoding="utf-8"))["luna_max_calls_per_day"] == 200 and skipped == []
    assert "_migrate_v1080_luna_tagesbudget(target, copied, skipped)" in (ROOT / "settings_migration.py").read_text(encoding="utf-8")


def test_luna_feld_in_den_webui_einstellungen(tmp_path, monkeypatch):
    from webui import settings_store
    monkeypatch.setattr(settings_store, "ROOT", tmp_path)
    (tmp_path / "ai_router_settings.json").write_text(json.dumps({"luna_max_calls_per_day": 50, "max_cost_per_day_usd": 5.0}), encoding="utf-8")
    assert settings_store.snapshot()["openai"]["luna_max_calls_per_day"] == 50
    settings_store.save({"openai": {"luna_max_calls_per_day": 200}})
    data = json.loads((tmp_path / "ai_router_settings.json").read_text(encoding="utf-8"))
    assert data["luna_max_calls_per_day"] == 200 and data["max_cost_per_day_usd"] == 5.0
    settings_store.save({"openai": {"luna_max_calls_per_day": -7}})
    assert json.loads((tmp_path / "ai_router_settings.json").read_text(encoding="utf-8"))["luna_max_calls_per_day"] == 0
    settings_store.save({"openai": {"luna_max_calls_per_day": 99999}})
    assert json.loads((tmp_path / "ai_router_settings.json").read_text(encoding="utf-8"))["luna_max_calls_per_day"] == 5000
    (tmp_path / "ai_router_settings.json").unlink()
    assert settings_store.snapshot()["openai"]["luna_max_calls_per_day"] == 200
    html = (ROOT / "webui" / "templates" / "settings.html").read_text(encoding="utf-8")
    assert 'data-field="openai.luna_max_calls_per_day"' in html and "Luna-Anfragen pro Tag" in html
