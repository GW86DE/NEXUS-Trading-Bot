"""Tests des dynamischen Universums (v7.0 NEXUS).

Schwerpunkt sind die drei Stabilitaetsregeln, die verhindern, dass das
Universum bei jedem Lauf durchgewuerfelt wird, sowie die Sicherheitsregel,
dass eine offene Position niemals aus dem Monitoring faellt.
"""
from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("TRADINGBOT_TEST_STATE_DIR", "/tmp/tradingbot_tests")
Path(os.environ["TRADINGBOT_TEST_STATE_DIR"]).mkdir(parents=True, exist_ok=True)

from universe.crypto_selector import CryptoUniverseSelector, HEBEL_MUSTER, STABLECOINS  # noqa: E402
from universe.manager import UniverseManager, UniverseRegeln  # noqa: E402
from universe.modelle import (  # noqa: E402
    ABGANG, AKTIV, BEOBACHTUNG, TIER_ETABLIERT, TIER_KANDIDAT, TIER_KERN,
    UniverseKandidat, UniverseMitglied, UniverseScore, UniverseZustand,
)
from universe.scoring import (  # noqa: E402
    aktivitaets_guete, cheap_score, quality_score, spread_guete, volatilitaets_guete,
)


# ---------------------------------------------------------------------------
# Hilfsmittel
# ---------------------------------------------------------------------------
def kandidat(symbol="AAA", **kwargs) -> UniverseKandidat:
    daten = dict(symbol=symbol, broker="okx", asset_type="crypto",
                 inst_id=f"{symbol}-EUR", preis=10.0, bid=9.99, ask=10.01,
                 spread_pct=0.002, volumen_quote_24h=20_000_000.0,
                 change_24h_pct=0.02, alter_tage=400.0)
    daten.update(kwargs)
    return UniverseKandidat(**daten)


def eintrag(symbol, rang, score=0.7, tier=TIER_ETABLIERT, **kwargs):
    return {"kandidat": kandidat(symbol, **kwargs),
            "score": UniverseScore(gesamt=score, teile={}, stufe="quality"),
            "rang": rang, "tier": tier, "tier_begruendung": "Test"}



def aktivieren(manager, broker, *symbole):
    """Simuliert eine bestandene Bewaehrung.

    Ab v8.1.3 kommt JEDER nicht zum festen Kern gehoerende Wert zuerst in
    BEOBACHTUNG -- auch ein als ETABLIERT eingestufter. Tests, die einen
    handelbaren Wert brauchen, muessen ihn deshalb erst freigeben.
    """
    from universe.modelle import AKTIV as _AKTIV
    for symbol in symbole:
        m = manager.zustand.hole(broker, symbol)
        assert m is not None, f"{symbol} ist nicht im Universum"
        m.wechsle(_AKTIV, "Testfreigabe")
        manager.zustand.setze(m)

@pytest.fixture
def zustand(tmp_path):
    return UniverseZustand(tmp_path / "universe_state.json")


@pytest.fixture
def manager(zustand):
    return UniverseManager(zustand)


# ---------------------------------------------------------------------------
# Bewertung
# ---------------------------------------------------------------------------
def test_enger_spread_ist_besser_als_weiter():
    assert spread_guete(0.0003, 0.0005, 0.006) == 1.0
    assert spread_guete(0.006, 0.0005, 0.006) == 0.0
    assert 0.0 < spread_guete(0.003, 0.0005, 0.006) < 1.0


def test_fehlender_spread_gibt_keinen_bonus():
    """Ohne Bid/Ask gibt es keine Aussage -- und damit keine Punkte."""
    assert spread_guete(0.0, 0.0005, 0.006) == 0.0


def test_volatilitaet_wird_nicht_monoton_belohnt():
    """Der Kern von DU-006: extreme Bewegung senkt den Score wieder."""
    ruhig = volatilitaets_guete(0.005, 0.03, 0.15)
    optimal = volatilitaets_guete(0.03, 0.03, 0.15)
    wild = volatilitaets_guete(0.14, 0.03, 0.15)
    extrem = volatilitaets_guete(0.30, 0.03, 0.15)
    assert optimal == 1.0
    assert ruhig < optimal
    assert wild < optimal
    assert extrem == 0.0


def test_aktivitaet_belohnt_keine_richtung():
    """Sonst waere der Winner-Bias durch die Hintertuer zurueck."""
    hoch = aktivitaets_guete(kandidat(change_24h_pct=0.05))
    runter = aktivitaets_guete(kandidat(change_24h_pct=-0.05))
    assert hoch == pytest.approx(runter)


def test_hoher_umsatz_schlaegt_niedrigen_umsatz():
    gross = cheap_score(kandidat(volumen_quote_24h=500_000_000.0))
    klein = cheap_score(kandidat(volumen_quote_24h=3_000_000.0))
    assert gross.gesamt > klein.gesamt


def test_qualitaetsstufe_beruecksichtigt_orderbuchtiefe():
    ohne = quality_score(kandidat(orderbuch_tiefe_quote=0.0, atr_pct=0.03))
    mit = quality_score(kandidat(orderbuch_tiefe_quote=400_000.0, atr_pct=0.03))
    assert mit.gesamt > ohne.gesamt
    assert mit.stufe == "quality"


# ---------------------------------------------------------------------------
# Harte Filter
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("symbol", ["BTC3L", "ETH3S", "SOL5L", "XRPUP", "DOGEDOWN"])
def test_hebel_token_werden_erkannt(symbol):
    assert HEBEL_MUSTER.match(symbol)


@pytest.mark.parametrize("symbol", ["BTC", "ETH", "SOLANA", "SUI", "DOT", "ARB"])
def test_normale_coins_werden_nicht_als_hebel_erkannt(symbol):
    """Das Muster darf keine echten Coins mitreissen."""
    assert not HEBEL_MUSTER.match(symbol)


def test_stablecoins_sind_gesperrt():
    assert "USDC" in STABLECOINS and "DAI" in STABLECOINS


class FakeOKX:
    """Minimaler OKX-Client fuer den Selektor."""

    def __init__(self, instrumente, tickers):
        self._instrumente = instrumente
        self._tickers = tickers

    def instruments(self, **_):
        return self._instrumente

    def tickers(self):
        return self._tickers

    def orderbook(self, inst_id, depth=20):
        return {"bids": [(10.0, 100.0)] * 10, "asks": [(10.1, 100.0)] * 10}

    def candles(self, inst_id, bar="15m", limit=96, nur_abgeschlossen=True):
        import pandas as pd
        index = pd.date_range("2026-08-01", periods=40, freq="15min", tz="UTC")
        return pd.DataFrame({"open": 10.0, "high": 10.3, "low": 9.8, "close": 10.1,
                             "volume": 100.0, "quote_volume": 1000.0}, index=index)


def _meta(sym, **kwargs):
    from broker.okx import OKXInstrument
    daten = dict(inst_id=f"{sym}-EUR", base_ccy=sym, quote_ccy="EUR", state="live",
                 tick_size="0.001", lot_size="0.0001", min_size="0.001",
                 list_time_ms=int((time.time() - 800 * 86400) * 1000))
    daten.update(kwargs)
    return OKXInstrument(**daten)


def _ticker(sym, **kwargs):
    from broker.okx import OKXTicker
    daten = dict(inst_id=f"{sym}-EUR", last=10.0, bid=9.99, ask=10.01,
                 vol_24h_base=1e6, vol_24h_quote=1e8, open_24h=9.8,
                 timestamp_ms=int(time.time() * 1000))
    daten.update(kwargs)
    return OKXTicker(**daten)


def test_selektor_filtert_hebel_stablecoins_und_junge_coins():
    instrumente = {
        "BTC-EUR": _meta("BTC"),
        "BTC3L-EUR": _meta("BTC3L"),
        "USDC-EUR": _meta("USDC"),
        "NEU-EUR": _meta("NEU", list_time_ms=int((time.time() - 5 * 86400) * 1000)),
        "EUR-EUR": _meta("EUR", quote_ccy="EUR"),
        "TOT-EUR": _meta("TOT", state="suspend"),
        "DUENN-EUR": _meta("DUENN"),
    }
    tickers = {k: _ticker(k.split("-")[0]) for k in instrumente}
    tickers["DUENN-EUR"] = _ticker("DUENN", vol_24h_quote=1000.0)

    selektor = CryptoUniverseSelector(FakeOKX(instrumente, tickers))
    pool, abgelehnt = selektor.eligible_pool()
    namen = {k.symbol for k in pool}
    gruende = dict(abgelehnt)

    # Ab 9.0.12 ist positives Volumen ein Rankingmerkmal, keine starre
    # Zugangssperre. Die eigentlichen Ausfuehrungsfilter bleiben bestehen.
    assert namen == {"BTC", "DUENN"}
    assert "gehebelt" in gruende["BTC3L-EUR"]
    assert "Stablecoin" in gruende["USDC-EUR"]
    assert "Tage gelistet" in gruende["NEU-EUR"]
    assert "live" in gruende["TOT-EUR"]
    assert "DUENN-EUR" not in gruende


def test_einstufung_trennt_etablierte_von_kandidaten():
    selektor = CryptoUniverseSelector(FakeOKX({}, {}))
    etabliert = kandidat("BTC", alter_tage=2000, volumen_quote_24h=2e9, spread_pct=0.0002)
    frisch = kandidat("XYZ", alter_tage=60, volumen_quote_24h=5e6, spread_pct=0.004)
    assert selektor.einstufung(etabliert)[0] == TIER_ETABLIERT
    assert selektor.einstufung(frisch)[0] == TIER_KANDIDAT


def test_kernliste_ueberspringt_die_bewaehrung_nicht_die_filter():
    selektor = CryptoUniverseSelector(FakeOKX({}, {}))
    # SOL steht auf der Kernliste, hat hier aber schwache Kennzahlen.
    tier, grund = selektor.einstufung(kandidat("SOL", alter_tage=100, volumen_quote_24h=1e6))
    assert tier == TIER_ETABLIERT and "Kernliste" in grund


# ---------------------------------------------------------------------------
# Manager: Aufnahme
# ---------------------------------------------------------------------------
def test_etablierte_coins_werden_direkt_aktiv(manager):
    diff = manager.lauf({"broker": "okx", "rangliste": [eintrag("BTC", 1)]})
    assert "BTC" in diff.aufgenommen
    assert manager.zustand.hole("okx", "BTC").zustand == AKTIV
    assert "BTC" in manager.handelbare_symbole("okx")


def test_neue_coins_landen_erst_in_beobachtung(manager):
    diff = manager.lauf({"broker": "okx",
                         "rangliste": [eintrag("NEU", 1, tier=TIER_KANDIDAT)]})
    assert "NEU" in diff.beobachtung_gestartet
    mitglied = manager.zustand.hole("okx", "NEU")
    assert mitglied.zustand == BEOBACHTUNG
    # Der wichtigste Punkt: beobachtet heisst NICHT handelbar.
    assert "NEU" not in manager.handelbare_symbole("okx")


def test_aktives_limit_wird_eingehalten(manager, monkeypatch):
    import config
    monkeypatch.setattr(config, "CRYPTO_UNIVERSE_ACTIVE_LIMIT", 6, raising=False)
    monkeypatch.setattr(config, "CRYPTO_CORE_LIMIT", 3, raising=False)
    monkeypatch.setattr(config, "CRYPTO_UNIVERSE_MAX_CHANGES_PER_RUN", 0, raising=False)
    rangliste = [eintrag(f"C{i}", i) for i in range(1, 11)]
    manager.lauf({"broker": "okx", "rangliste": rangliste})
    # Ab v8.1.3 wird der feste Kern IMMER angelegt, auch wenn er gerade nicht
    # in der Rangliste steht: 3 Kernwerte + 3 dynamische Plaetze = 6 aktiv.
    mitglieder = manager.zustand.fuer_broker("okx")
    assert len(mitglieder) == 6
    kern = [m for m in mitglieder if manager.ist_kern("okx", m.symbol)]
    assert len(kern) == 3, "BTC, ETH und SOL sind gesetzt"
    assert len(mitglieder) - len(kern) == 3, "genau drei dynamische Plaetze"


def test_aenderungslimit_pro_lauf_greift(manager, monkeypatch):
    import config
    monkeypatch.setattr(config, "CRYPTO_UNIVERSE_MAX_CHANGES_PER_RUN", 2, raising=False)
    rangliste = [eintrag(f"C{i}", i) for i in range(1, 9)]
    diff = manager.lauf({"broker": "okx", "rangliste": rangliste})
    # Nicht-Kernwerte starten in Beobachtung, nicht direkt als aufgenommen.
    assert len(diff.beobachtung_gestartet) == 2
    # Das Aenderungslimit begrenzt die DYNAMIK. Der feste Kern ist keine
    # Aenderung, sondern die Definition des Universums -- er wird immer
    # angelegt und zaehlt nicht gegen das Limit.
    assert set(diff.aufgenommen) == set(config.CRYPTO_CORE_SYMBOLS)
    assert "aenderungslimit" in diff.gruende


def test_dynamische_aktie_kommt_ohne_rueckfrage_aber_nur_auf_bewaehrung(manager):
    """GEAENDERT IN v8.1.5 -- bewusst, auf Georgs Vorgabe.

    Bis v8.1.4 wurde eine dynamische Aktie nur VORGESCHLAGEN und brauchte
    seine Telegram-Freigabe. Georg am 25.08.2026: "bitte aender auch das die
    freigabe fuer die aufnahme ins Universum keine Bestaetigung mehr von mir
    braucht ... es ist doch eh dynamisch und fliegt wieder raus wenn es eine
    schlechte Wahl war".

    Was NICHT wegfaellt, ist die Bewaehrung. Der Wert landet in BEOBACHTUNG,
    nicht sofort in AKTIV -- beobachtet werden ist nicht handelbar sein.
    """
    e = eintrag("ZZTOP", 1, tier=TIER_KANDIDAT)   # bewusst kein Kernwert
    e["kandidat"].broker = "etoro"
    e["kandidat"].asset_type = "stock"
    diff = manager.lauf({"broker": "etoro", "rangliste": [e]})

    assert "ZZTOP" not in diff.aufgenommen, "Sofort aktiv waere zu schnell"
    assert "ZZTOP" in diff.beobachtung_gestartet
    mitglied = manager.zustand.hole("etoro", "ZZTOP")
    assert mitglied is not None and mitglied.zustand == BEOBACHTUNG


def test_aktienkern_kommt_ohne_freigabe_ins_universum(manager):
    """Sonst waere die Aktienseite dauerhaft leer -- Georgs "0 oder 80?"."""
    diff = manager.lauf({"broker": "etoro", "rangliste": []})
    import config
    erwartet = min(len(config.STOCK_CORE_SYMBOLS), config.STOCK_CORE_LIMIT)
    assert len(diff.aufgenommen) == erwartet
    aapl = manager.zustand.hole("etoro", "AAPL")
    assert aapl is not None and aapl.zustand == AKTIV
    assert aapl.tier == TIER_KERN


def test_kernwert_ohne_bewertung_geht_nicht_auf_abgang(manager):
    """Geschlossene Boerse ist kein Sicherheitsproblem."""
    manager.lauf({"broker": "etoro", "rangliste": []})
    manager.lauf({"broker": "etoro", "rangliste": []})
    aapl = manager.zustand.hole("etoro", "AAPL")
    assert aapl.zustand == AKTIV, "Ein Kernwert faellt nicht wegen fehlender Daten heraus"


# ---------------------------------------------------------------------------
# Manager: Hysterese, Aufenthalt, Bestaetigung
# ---------------------------------------------------------------------------
def test_hysterese_entfernt_nicht_bei_kleiner_rangaenderung(manager, monkeypatch):
    import config
    monkeypatch.setattr(config, "CRYPTO_UNIVERSE_ACTIVE_LIMIT", 50, raising=False)
    monkeypatch.setattr(config, "CRYPTO_UNIVERSE_REMOVAL_RANK", 75, raising=False)
    manager.lauf({"broker": "okx", "rangliste": [eintrag("BTC", 10)]})
    # Rang faellt auf 60: schlechter als die Aufnahmegrenze, aber besser als 75.
    diff = manager.lauf({"broker": "okx", "rangliste": [eintrag("BTC", 60)]})
    assert diff.entfernt == []
    assert manager.zustand.hole("okx", "BTC").zustand == AKTIV


def test_ein_schlechter_rang_entfernt_noch_nicht(manager):
    # C99 ist absichtlich kein fester Kernwert und bleibt per Rang entfernbar.
    manager.lauf({"broker": "okx", "rangliste": [eintrag("C99", 5)]})
    aktivieren(manager, "okx", "C99")
    diff = manager.lauf({"broker": "okx", "rangliste": [eintrag("C99", 99)]})
    assert diff.entfernt == []
    assert "C99" in diff.abgang_angekuendigt
    assert manager.zustand.hole("okx", "C99").zustand == ABGANG


def test_drei_schlechte_raenge_entfernen_beim_naechsten_tageslauf(manager):
    start = datetime.now(timezone.utc)
    manager.lauf({"broker": "okx", "rangliste": [eintrag("C99", 5)]}, jetzt=start)
    aktivieren(manager, "okx", "C99")
    for _ in range(3):
        diff = manager.lauf({"broker": "okx", "rangliste": [eintrag("C99", 99)]}, jetzt=start)
        assert diff.entfernt == []
    diff = manager.lauf({"broker": "okx", "rangliste": [eintrag("C99", 99)]},
                        jetzt=start + timedelta(days=1))
    assert "C99" in diff.entfernt
    assert manager.zustand.hole("okx", "C99") is None


def test_erholung_bricht_den_abgang_ab(manager):
    manager.lauf({"broker": "okx", "rangliste": [eintrag("BTC", 5)]})
    manager.lauf({"broker": "okx", "rangliste": [eintrag("BTC", 99)]})
    manager.lauf({"broker": "okx", "rangliste": [eintrag("BTC", 12)]})
    mitglied = manager.zustand.hole("okx", "BTC")
    assert mitglied.zustand == AKTIV
    assert mitglied.schlechte_raenge_in_folge == 0


def test_sicherheitsgrund_sperrt_sofort_ohne_untertaegige_entfernung(manager):
    manager.lauf({"broker": "okx", "rangliste": [eintrag("C99", 5)]})
    e = eintrag("C99", 5)
    e["sicherheitsabgang"] = "Delisting laut Broker"
    diff = manager.lauf({"broker": "okx", "rangliste": [e]})
    assert diff.entfernt == []
    assert diff.gruende["C99"] == "Delisting laut Broker"
    assert not manager.zustand.hole("okx", "C99").handelbar


# ---------------------------------------------------------------------------
# Manager: Bewaehrung
# ---------------------------------------------------------------------------
def _setze_zustand_alter(mitglied: UniverseMitglied, stunden: float) -> None:
    frueher = datetime.now(timezone.utc) - timedelta(hours=stunden)
    mitglied.zustand_seit = frueher.isoformat()


def test_bewaehrung_endet_nicht_vor_ablauf_der_zeit(manager):
    manager.lauf({"broker": "okx", "rangliste": [eintrag("NEU", 5, tier=TIER_KANDIDAT)]})
    manager.lauf({"broker": "okx", "rangliste": [eintrag("NEU", 5, tier=TIER_KANDIDAT)]})
    assert manager.zustand.hole("okx", "NEU").zustand == BEOBACHTUNG


def test_bewaehrung_bestanden_macht_handelbar(manager):
    manager.lauf({"broker": "okx", "rangliste": [eintrag("NEU", 5, tier=TIER_KANDIDAT)]})
    mitglied = manager.zustand.hole("okx", "NEU")
    # Drei stabile Beobachtungen und abgelaufene Bewaehrungszeit.
    for _ in range(3):
        mitglied.merke_score(0.70, 5)
    _setze_zustand_alter(mitglied, 30.0)
    manager.zustand.setze(mitglied)

    diff = manager.lauf({"broker": "okx", "rangliste": [eintrag("NEU", 5, tier=TIER_KANDIDAT)]})
    assert "NEU" in diff.freigegeben
    assert manager.zustand.hole("okx", "NEU").zustand == AKTIV
    assert "NEU" in manager.handelbare_symbole("okx")


def test_einbrechende_bewertung_verlaengert_die_bewaehrung(manager):
    manager.lauf({"broker": "okx", "rangliste": [eintrag("NEU", 5, tier=TIER_KANDIDAT)]})
    mitglied = manager.zustand.hole("okx", "NEU")
    for _ in range(3):
        mitglied.merke_score(0.90, 5)
    _setze_zustand_alter(mitglied, 30.0)
    manager.zustand.setze(mitglied)

    # Score bricht um mehr als 25 % ein.
    diff = manager.lauf({"broker": "okx",
                         "rangliste": [eintrag("NEU", 5, score=0.40, tier=TIER_KANDIDAT)]})
    assert "NEU" not in diff.freigegeben
    assert manager.zustand.hole("okx", "NEU").zustand == BEOBACHTUNG


def test_ki_priorisiert_aber_blockiert_keine_technische_freigabe(manager):
    class FakeRouter:
        def bewerte_universe_kandidat(self, daten):
            return {"attention": "LOW", "modell": "gpt-5.6-luna"}

    manager.ai = FakeRouter()
    manager.lauf({"broker": "okx", "rangliste": [eintrag("NEU", 5, tier=TIER_KANDIDAT)]})
    mitglied = manager.zustand.hole("okx", "NEU")
    for _ in range(3):
        mitglied.merke_score(0.70, 5)
    _setze_zustand_alter(mitglied, 30.0)
    manager.zustand.setze(mitglied)

    diff = manager.lauf({"broker": "okx", "rangliste": [eintrag("NEU", 5, tier=TIER_KANDIDAT)]})
    assert "NEU" in diff.freigegeben
    assert "NEU" not in diff.entfernt
    assert manager.zustand.hole("okx", "NEU").ai_bewertung == "LOW"


def test_ki_ausfall_blockiert_die_freigabe_nicht(manager):
    class KaputterRouter:
        def bewerte_universe_kandidat(self, daten):
            raise RuntimeError("OpenAI nicht erreichbar")

    manager.ai = KaputterRouter()
    manager.lauf({"broker": "okx", "rangliste": [eintrag("NEU", 5, tier=TIER_KANDIDAT)]})
    mitglied = manager.zustand.hole("okx", "NEU")
    for _ in range(3):
        mitglied.merke_score(0.70, 5)
    _setze_zustand_alter(mitglied, 30.0)
    manager.zustand.setze(mitglied)

    diff = manager.lauf({"broker": "okx", "rangliste": [eintrag("NEU", 5, tier=TIER_KANDIDAT)]})
    assert "NEU" in diff.freigegeben


# ---------------------------------------------------------------------------
# Manager: gepinnte Positionen
# ---------------------------------------------------------------------------
def test_offene_position_bleibt_im_monitoring(manager):
    manager.lauf({"broker": "okx", "rangliste": [eintrag("BTC", 5)]})
    for _ in range(3):
        manager.lauf({"broker": "okx", "rangliste": [eintrag("BTC", 99)]},
                     offene_positionen=["BTC"])
    mitglied = manager.zustand.hole("okx", "BTC")
    assert mitglied is not None, "Ein Wert mit offener Position darf nie verschwinden"
    assert mitglied.gepinnt
    assert "BTC" in manager.monitoring_symbole("okx")


def test_offene_position_ausserhalb_des_universums_wird_aufgenommen(manager):
    diff = manager.lauf({"broker": "okx", "rangliste": []}, offene_positionen=["DOGE"])
    assert "DOGE" in diff.gepinnt
    assert manager.zustand.hole("okx", "DOGE") is not None


def test_gepinnte_werte_zaehlen_nicht_gegen_das_limit(manager, monkeypatch):
    import config
    # 5 aktiv minus 3 Kernplaetze = genau 2 dynamische Plaetze fuer A und B.
    monkeypatch.setattr(config, "CRYPTO_UNIVERSE_ACTIVE_LIMIT", 5, raising=False)
    monkeypatch.setattr(config, "CRYPTO_CORE_LIMIT", 3, raising=False)
    monkeypatch.setattr(config, "CRYPTO_UNIVERSE_MAX_CHANGES_PER_RUN", 0, raising=False)
    manager.lauf({"broker": "okx", "rangliste": [eintrag("A", 1), eintrag("B", 2)]},
                 offene_positionen=["ZZZ"])
    aktivieren(manager, "okx", "A", "B")
    handelbar = manager.handelbare_symbole("okx")
    assert "A" in handelbar and "B" in handelbar and "ZZZ" in handelbar


# ---------------------------------------------------------------------------
# Focus Set und Persistenz
# ---------------------------------------------------------------------------
def test_focus_set_ist_begrenzt_und_stellt_positionen_voran(manager, monkeypatch):
    import config
    monkeypatch.setattr(config, "CRYPTO_UNIVERSE_FOCUS_LIMIT", 3, raising=False)
    monkeypatch.setattr(config, "CRYPTO_UNIVERSE_MAX_CHANGES_PER_RUN", 0, raising=False)
    rangliste = [eintrag(f"C{i}", i, score=1.0 - i * 0.05) for i in range(1, 8)]
    manager.lauf({"broker": "okx", "rangliste": rangliste}, offene_positionen=["C7"])
    aktivieren(manager, "okx", *[f"C{i}" for i in range(1, 7)])
    focus = manager.focus_set("okx")
    assert len(focus) == 3
    assert focus[0] == "C7", "Eine offene Position gehoert immer in das Focus Set"


def test_zustand_ueberlebt_neustart(tmp_path):
    datei = tmp_path / "universe_state.json"
    m1 = UniverseManager(UniverseZustand(datei))
    m1.lauf({"broker": "okx", "rangliste": [eintrag("BTC", 1)]})

    m2 = UniverseManager(UniverseZustand(datei))
    mitglied = m2.zustand.hole("okx", "BTC")
    assert mitglied is not None and mitglied.zustand == AKTIV


def test_diff_kurzfassung_ist_lesbar(manager):
    diff = manager.lauf({"broker": "okx", "rangliste": [eintrag("BTC", 1)]})
    assert "neu" in diff.kurzfassung()
    assert diff.hat_aenderungen


def test_uebersicht_liefert_kennzahlen(manager):
    import config
    manager.lauf({"broker": "okx", "rangliste": [eintrag("BTC", 1),
                                                 eintrag("NEU", 2, tier=TIER_KANDIDAT)]})
    u = manager.uebersicht("okx")
    # 20 feste Kernwerte + NEU in Beobachtung
    assert u["gesamt"] == len(config.CRYPTO_CORE_SYMBOLS) + 1
    assert u["kern"] == len(config.CRYPTO_CORE_SYMBOLS) and u["dynamisch"] == 1
    assert u["handelbar"] == len(config.CRYPTO_CORE_SYMBOLS)
    assert u["beobachtung"] == 1
    assert u["autonome_aufnahme"] is True


def test_aktienregeln_nehmen_autonom_auf_mit_vier_stunden_bewaehrung():
    """GEAENDERT IN v8.1.5: autonome Aufnahme -- aber mit Deckeln."""
    import config
    regeln = UniverseRegeln.fuer("etoro")
    assert regeln.autonome_aufnahme is True
    assert regeln.beobachtung_stunden == 4.0, \
        "Vier Stunden, nicht 24 -- der Wert ist wegen guter Kennzahlen da"
    # Ohne Rueckfrage heisst nicht ohne Grenzen.
    assert regeln.kern_limit == config.STOCK_CORE_LIMIT
    assert regeln.dynamisch_limit == config.STOCK_UNIVERSE_DYNAMIC_LIMIT
    assert regeln.max_aenderungen_pro_lauf == 5

    krypto = UniverseRegeln.fuer("okx")
    assert krypto.autonome_aufnahme is True
    assert krypto.beobachtung_stunden == 24.0, \
        "Die Kryptoseite behaelt ihre laengere Bewaehrung"


# ---------------------------------------------------------------------------
# Kernwerte: keine dauerhafte Sperre durch fehlende Daten (v8.1.3)
# ---------------------------------------------------------------------------
def test_kernwert_ohne_bewertung_wird_nicht_gesperrt(manager):
    """Fehlende Bewertung ist kein Sicherheitsproblem.

    Vor dem Fix htte drei Lufe ohne Rangliste gereicht, um BTC dauerhaft
    unkaufbar zu machen -- bei 900 s Takt also nach 45 Minuten.
    """
    for _ in range(5):
        manager.lauf({"broker": "okx", "rangliste": []})
    btc = manager.zustand.hole("okx", "BTC")
    assert btc is not None
    assert btc.handelbar is True, "Ein Kernwert darf nicht wegen fehlender Daten sperren"
    assert btc.kern_blockiert is False
    assert "BTC" in manager.handelbare_symbole("okx")


def test_kernsperre_loest_sich_wieder(manager):
    """Eine Sperre muss reversibel sein -- sonst hilft nur Datei lschen."""
    manager.lauf({"broker": "okx", "rangliste": [eintrag("BTC", 1)]})
    # Echter Sicherheitsgrund aus der Bewertung
    e = eintrag("BTC", 1)
    e["sicherheitsabgang"] = "bei OKX nicht mehr handelbar"
    manager.lauf({"broker": "okx", "rangliste": [e]})
    btc = manager.zustand.hole("okx", "BTC")
    assert btc.kern_blockiert is True, "Ein echter Sicherheitsfilter muss sperren"
    assert btc.handelbar is False

    # Wieder sauber bewertet -> Sperre lst sich
    manager.lauf({"broker": "okx", "rangliste": [eintrag("BTC", 1)]})
    btc = manager.zustand.hole("okx", "BTC")
    assert btc.kern_blockiert is False
    assert btc.handelbar is True


def test_kernwert_bekommt_vollstaendige_inst_id(manager):
    """"BTC" ist bei OKX keine gltige Kennung -- "BTC-EUR" schon."""
    manager.lauf({"broker": "okx", "rangliste": []})
    btc = manager.zustand.hole("okx", "BTC")
    assert "-" in btc.inst_id, f"unvollstaendige Kennung: {btc.inst_id}"
    import config
    assert btc.inst_id.endswith(str(config.OKX_QUOTE_CCY).upper())


def test_aktienkern_und_katalog_stimmen_ueberein():
    """Zwei sich widersprechende "Kerne" waeren spaeter nicht auffindbar."""
    import config
    katalog = {str(s.get("symbol") if isinstance(s, dict) else s).upper()
               for s in config.STOCK_SYMBOLS}
    kern = [str(s).upper() for s in config.STOCK_CORE_SYMBOLS][:config.STOCK_CORE_LIMIT]
    fehlend = [s for s in kern if s not in katalog]
    assert fehlend == [], f"Kernwerte ausserhalb des Katalogs: {fehlend}"
