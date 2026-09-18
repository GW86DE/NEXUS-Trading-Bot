"""Tests der uebrigen v7-Bausteine: Taktgeber, Risikotoepfe, AI-Router,
Quellenprotokoll, Broker-Hub und Einstellungsuebernahme.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("TRADINGBOT_TEST_STATE_DIR", "/tmp/tradingbot_tests")
Path(os.environ["TRADINGBOT_TEST_STATE_DIR"]).mkdir(parents=True, exist_ok=True)

import ai_router  # noqa: E402
import decision_source as ds  # noqa: E402
import scheduler_v7 as sched  # noqa: E402
from broker.multi import ABGEMELDET, GESTOERT, VERBUNDEN, ZUGANG_FEHLT, BrokerHub  # noqa: E402
from broker.base import AuthentifizierungsFehler, VerbindungVerloren  # noqa: E402
from risk_pots import RiskPot, RiskPotManager, TopfGrenzen  # noqa: E402


# ===========================================================================
# Taktgeber
# ===========================================================================
class FesteFenster(sched.Marktfenster):
    def __init__(self, aktien_offen=True):
        self._aktien_offen = aktien_offen

    def krypto_offen(self, jetzt=None):
        return True, "Krypto handelt rund um die Uhr"

    def aktien_offen(self, jetzt=None):
        return self._aktien_offen, "Test" if self._aktien_offen else "Boerse geschlossen"


def test_krypto_laeuft_auch_bei_geschlossener_boerse():
    """Der Kern-Bugfix aus v6: das Wochenende darf Krypto nicht abschalten."""
    takt = sched.Taktgeber(marktfenster=FesteFenster(aktien_offen=False))
    krypto_faellig, _ = takt.faellig("crypto", sched.SCAN, jetzt=0.0)
    aktien_faellig, grund = takt.faellig("stock", sched.SCAN, jetzt=0.0)
    assert krypto_faellig is True
    assert aktien_faellig is False
    assert "geschlossen" in grund


def test_krypto_scan_alle_fuenf_minuten():
    takt = sched.Taktgeber(marktfenster=FesteFenster())
    takt.markiere("crypto", sched.SCAN, jetzt=1000.0)
    assert takt.faellig("crypto", sched.SCAN, jetzt=1200.0)[0] is False   # nach 200 s
    assert takt.faellig("crypto", sched.SCAN, jetzt=1300.0)[0] is True    # nach 300 s


def test_universum_laeuft_seltener_als_der_scan():
    takt = sched.Taktgeber(marktfenster=FesteFenster())
    assert takt.intervall("crypto", sched.UNIVERSUM) > takt.intervall("crypto", sched.SCAN)
    assert takt.intervall("stock", sched.UNIVERSUM) > takt.intervall("stock", sched.SCAN)


def test_positionsueberwachung_ist_schneller_als_der_scan():
    """Ein Stop darf nie auf das Ende eines Scanzyklus warten."""
    takt = sched.Taktgeber(marktfenster=FesteFenster())
    for asset in ("crypto", "stock"):
        assert takt.intervall(asset, sched.POSITIONEN) < takt.intervall(asset, sched.SCAN)


def test_positionsueberwachung_laeuft_auch_bei_geschlossener_boerse():
    """Eine offene Aktienposition muss ueber Nacht beobachtbar bleiben."""
    takt = sched.Taktgeber(marktfenster=FesteFenster(aktien_offen=False))
    faellig, _ = takt.faellig("stock", sched.POSITIONEN, jetzt=0.0)
    assert faellig is True


def test_wartezeit_richtet_sich_nach_dem_schnellsten_takt():
    takt = sched.Taktgeber(marktfenster=FesteFenster(aktien_offen=False))
    for arbeit in (sched.SCAN, sched.UNIVERSUM, sched.POSITIONEN, sched.NEWS):
        takt.markiere("crypto", arbeit, jetzt=1000.0)
        takt.markiere("stock", arbeit, jetzt=1000.0)
    wartezeit = takt.wartezeit(jetzt=1000.0, maximum=600.0)
    # Der schnellste offene Takt ist die Kryptopositionspruefung (60 s).
    assert wartezeit == pytest.approx(60.0, abs=1.0)


def test_manueller_scan_erzwingt_faelligkeit():
    takt = sched.Taktgeber(marktfenster=FesteFenster())
    takt.markiere("crypto", sched.SCAN, jetzt=1000.0)
    assert takt.faellig("crypto", sched.SCAN, jetzt=1001.0)[0] is False
    takt.erzwinge("crypto", sched.SCAN)
    assert takt.faellig("crypto", sched.SCAN, jetzt=1001.0)[0] is True


def test_kalenderfehler_oeffnet_den_aktienhandel_nicht(monkeypatch):
    """Im Zweifel geschlossen -- ein Kalenderfehler darf nicht handeln lassen."""
    fenster = sched.Marktfenster()

    def kaputt(*args, **kwargs):
        raise RuntimeError("Kalender kaputt")

    monkeypatch.setitem(sys.modules, "market_calendar",
                        type("M", (), {"darf_arbeiten": staticmethod(kaputt)}))
    offen, grund = fenster.aktien_offen()
    assert offen is False and "fehlgeschlagen" in grund
    # Krypto bleibt davon voellig unberuehrt.
    assert fenster.krypto_offen()[0] is True


def test_plan_zeigt_beide_anlageklassen():
    takt = sched.Taktgeber(marktfenster=FesteFenster())
    plan = takt.plan()
    assert set(plan["takte"]) == {"crypto", "stock"}
    assert plan["takte"]["crypto"]["rund_um_die_uhr"] is True


# ===========================================================================
# Risikotoepfe
# ===========================================================================
@pytest.fixture
def topf(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADINGBOT_TEST_STATE_DIR", str(tmp_path))
    p = RiskPot("okx", grenzen=TopfGrenzen(risiko_pro_trade_pct=0.01, max_position_pct=0.50,
                                           max_offene_positionen=3, max_tagesverlust_pct=0.02,
                                           max_trades_pro_tag=5))
    p.setze_kontowert(10_000.0)
    return p


def test_positionsgroesse_folgt_dem_risikoabstand(topf):
    menge, grund = topf.positionsgroesse(100.0, 95.0)
    # 1 % von 10.000 = 100 USD Risiko, Abstand 5 USD -> 20 Stueck
    assert menge == pytest.approx(20.0)
    assert "Risiko/Trade" in grund


def test_positionsgroesse_wird_durch_maximalgroesse_gedeckelt(topf):
    # Enger Stop: das Risiko erlaubt sehr viel, die Positionsgrenze nicht.
    menge, grund = topf.positionsgroesse(100.0, 99.9)
    assert menge == pytest.approx(50.0)     # 50 % von 10.000 / 100 USD
    assert "Positionsgroesse" in grund


def test_stop_ueber_einstieg_ergibt_keine_position(topf):
    assert topf.positionsgroesse(100.0, 105.0)[0] == 0.0
    assert topf.positionsgroesse(100.0, 0.0)[0] == 0.0


def test_tagesverlustgrenze_stoppt_neue_kaeufe(topf):
    topf.state.realized_pnl_today = -150.0     # 1,5 % -> noch erlaubt
    topf.state.save()
    assert topf.darf_kaufen()[0] is True
    topf.state.realized_pnl_today = -250.0     # 2,5 % -> ueber der Grenze
    topf.state.save()
    darf, grund = topf.darf_kaufen()
    assert darf is False and "Tagesverlust" in grund


def test_positionsanzahl_wird_begrenzt(topf):
    topf.setze_offene_positionen(3)
    darf, grund = topf.darf_kaufen()
    assert darf is False and "offene Positionen" in grund


def test_toepfe_sind_wirklich_getrennt(tmp_path, monkeypatch):
    """Der Kern der Anforderung: ein schlechter Kryptotag darf Aktien nicht bremsen."""
    monkeypatch.setenv("TRADINGBOT_TEST_STATE_DIR", str(tmp_path))
    verwaltung = RiskPotManager(["etoro", "okx"])
    verwaltung.topf("etoro").setze_kontowert(20_000.0)
    verwaltung.topf("okx").setze_kontowert(2_000.0)
    verwaltung.topf("okx").state.realized_pnl_today = -500.0     # 25 % Verlust
    verwaltung.topf("okx").state.save()

    assert verwaltung.darf_kaufen("crypto")[0] is False
    assert verwaltung.darf_kaufen("stock")[0] is True


def test_globale_klammer_greift_nur_wenn_eingeschaltet(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADINGBOT_TEST_STATE_DIR", str(tmp_path))
    import config
    verwaltung = RiskPotManager(["etoro", "okx"])
    verwaltung.topf("etoro").setze_kontowert(10_000.0)
    verwaltung.topf("okx").setze_kontowert(10_000.0)
    verwaltung.topf("etoro").state.realized_pnl_today = -900.0

    monkeypatch.setattr(config, "GLOBAL_RISK_GUARD_ENABLED", False, raising=False)
    assert verwaltung.globale_pruefung()[0] is True

    monkeypatch.setattr(config, "GLOBAL_RISK_GUARD_ENABLED", True, raising=False)
    monkeypatch.setattr(config, "GLOBAL_MAX_DAILY_LOSS_PCT", 0.03, raising=False)
    ok, grund = verwaltung.globale_pruefung()
    assert ok is False and "Globale" in grund


def test_globaler_stopp_blockiert_beide_seiten(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADINGBOT_TEST_STATE_DIR", str(tmp_path))
    verwaltung = RiskPotManager(["etoro", "okx"])
    verwaltung.globaler_stopp("Notaus per Telegram")
    assert verwaltung.darf_kaufen("crypto")[0] is False
    assert verwaltung.darf_kaufen("stock")[0] is False
    verwaltung.globalen_stopp_aufheben()
    verwaltung.topf("okx").setze_kontowert(1000.0)
    assert verwaltung.darf_kaufen("crypto")[0] is True


def test_unerreichbarer_broker_setzt_kontowert_nicht_auf_null(tmp_path, monkeypatch):
    """Sonst wuerde die naechste Positionsgroesse still null -- ohne Grund."""
    monkeypatch.setenv("TRADINGBOT_TEST_STATE_DIR", str(tmp_path))
    verwaltung = RiskPotManager(["okx"])
    verwaltung.topf("okx").setze_kontowert(5_000.0)

    class HubOhneBroker:
        def broker(self, name):
            return None

    werte = verwaltung.aktualisiere_kontowerte(HubOhneBroker())
    assert werte["okx"] == 5_000.0
    assert verwaltung.topf("okx").kontowert == 5_000.0


def test_grenzen_fallen_auf_globale_standards_zurueck(monkeypatch):
    import config
    monkeypatch.setattr(config, "MAX_OPEN_POSITIONS", 8, raising=False)
    monkeypatch.delattr(config, "TESTBROKER_MAX_OPEN_POSITIONS", raising=False)
    grenzen = TopfGrenzen.fuer_broker("testbroker", config)
    assert grenzen.max_offene_positionen == 8


# ===========================================================================
# AI-Router
# ===========================================================================
@pytest.fixture
def router(tmp_path, monkeypatch):
    monkeypatch.setattr(ai_router, "STATE_ROOT", tmp_path)

    class Cfg:
        OPENAI_API_KEY = "sk-test"
        AI_ROUTER_ENABLED = True
        AI_LUNA_MODEL = "gpt-5.6-luna"
        AI_TERRA_MODEL = "gpt-5.6-terra"
        AI_LUNA_MAX_CALLS_PER_DAY = 3
        AI_TERRA_MAX_CALLS_PER_DAY = 1
        AI_MAX_COST_PER_DAY_USD = 1.0
        AI_TERRA_FALLBACK_TO_LUNA = True
        AI_ROUTER_USAGE_FILE = "ai_router_usage.json"

    budget = ai_router.AIBudget(tmp_path / "usage.json", cfg=Cfg)
    cache = ai_router.AICache(tmp_path / "cache.json")
    return ai_router.AIRouter(Cfg, budget=budget, cache=cache)


def test_einfache_aufgaben_gehen_an_luna(router):
    stufe, _ = router.waehle_stufe("universe_kandidat")
    assert stufe == ai_router.LUNA


def test_komplexe_aufgaben_gehen_an_terra(router):
    stufe, _ = router.waehle_stufe("anomalie_research")
    assert stufe == ai_router.TERRA


def test_eskalation_hebt_eine_luna_aufgabe_auf_terra(router):
    stufe, grund = router.waehle_stufe("universe_kandidat",
                                       {"anlass": "moeglicher Exploit im Protokoll"})
    assert stufe == ai_router.TERRA
    assert "Eskalation" in grund


def test_ohne_schluessel_wird_gar_nicht_gefragt(router):
    router.cfg.OPENAI_API_KEY = ""
    stufe, grund = router.waehle_stufe("universe_kandidat")
    assert stufe == ai_router.KEINE
    assert "deaktiviert" in grund or "Schluessel" in grund


def test_aufgebrauchtes_terra_budget_faellt_auf_luna_zurueck(router):
    router.budget.buche(ai_router.TERRA, input_tokens=10, output_tokens=10, kosten=0.0)
    stufe, grund = router.waehle_stufe("anomalie_research")
    assert stufe == ai_router.LUNA
    assert "Terra nicht verfuegbar" in grund


def test_aufgebrauchtes_luna_budget_schaltet_die_ki_ab(router):
    for _ in range(3):
        router.budget.buche(ai_router.LUNA, input_tokens=1, output_tokens=1, kosten=0.0)
    stufe, grund = router.waehle_stufe("universe_kandidat")
    assert stufe == ai_router.KEINE
    assert "Tagesbudget" in grund


def test_kostenbudget_stoppt_auch_bei_freien_anfragen(router):
    router.budget.buche(ai_router.LUNA, input_tokens=1, output_tokens=1, kosten=1.5)
    stufe, grund = router.waehle_stufe("universe_kandidat")
    assert stufe == ai_router.KEINE
    assert "kostenbudget" in grund.lower()


def test_ki_ausfall_liefert_auswertbares_ergebnis(router, monkeypatch):
    def kaputt(*args, **kwargs):
        raise RuntimeError("Netzwerk weg")

    monkeypatch.setattr(ai_router.requests, "post", kaputt)
    ergebnis = router.bewerte_universe_kandidat({"symbol": "BTC"})
    assert ergebnis["attention"] == ""
    assert router.degraded is True
    assert "Netzwerk weg" in router.status()["degraded_grund"]


def test_antwort_landet_im_cache_und_kostet_nur_einmal(router, monkeypatch):
    aufrufe = []

    class Antwort:
        status_code = 200
        content = b"x"
        ok = True

        @staticmethod
        def json():
            return {"output_text": json.dumps({
                "attention": "HIGH", "research_needed": False,
                "risiken": [], "begruendung": "gut"}),
                "usage": {"input_tokens": 1000, "output_tokens": 200}}

    def fake_post(*args, **kwargs):
        aufrufe.append(1)
        return Antwort()

    monkeypatch.setattr(ai_router.requests, "post", fake_post)
    erste = router.bewerte_universe_kandidat({"symbol": "BTC"})
    zweite = router.bewerte_universe_kandidat({"symbol": "BTC"})

    assert erste["attention"] == "HIGH"
    assert zweite["attention"] == "HIGH"
    assert len(aufrufe) == 1, "Die zweite Anfrage muss aus dem Cache kommen"
    assert router.budget.status()["cache_treffer"] == 1


def test_kostenschaetzung_verwendet_die_richtigen_preise(router, monkeypatch):
    class Antwort:
        status_code = 200
        content = b"x"
        ok = True

        @staticmethod
        def json():
            return {"output_text": json.dumps({
                "attention": "NORMAL", "research_needed": False,
                "risiken": [], "begruendung": "ok"}),
                "usage": {"input_tokens": 1_000_000, "output_tokens": 0}}

    monkeypatch.setattr(ai_router.requests, "post", lambda *a, **k: Antwort())
    router.bewerte_universe_kandidat({"symbol": "XRP"})
    # 1 Mio. Input-Token bei Luna: 0,20 USD
    assert router.budget.status()["luna"]["kosten"] == pytest.approx(0.20, rel=1e-6)


def test_batch_verwirft_erfundene_symbole(router, monkeypatch):
    class Antwort:
        status_code = 200
        content = b"x"
        ok = True

        @staticmethod
        def json():
            return {"output_text": json.dumps({"bewertungen": [
                {"symbol": "BTC", "attention": "HIGH", "research_needed": False, "begruendung": "a"},
                {"symbol": "ERFUNDEN", "attention": "HIGH", "research_needed": True, "begruendung": "b"},
            ], "hinweis": ""}), "usage": {"input_tokens": 10, "output_tokens": 10}}

    monkeypatch.setattr(ai_router.requests, "post", lambda *a, **k: Antwort())
    ergebnis = router.bewerte_universe_batch([{"symbol": "BTC"}, {"symbol": "ETH"}])
    assert "BTC" in ergebnis
    assert "ERFUNDEN" not in ergebnis, "Die KI darf keine Instrumente erfinden"


def test_cache_invalidierung_bei_neuem_ereignis(router):
    router.cache.setze("k1", {"attention": "HIGH"}, aufgabe="universe_kandidat")
    assert router.cache.hole("k1", 6.0) is not None
    router.cache.verwerfe(aufgabe="universe_kandidat")
    assert router.cache.hole("k1", 6.0) is None


# ===========================================================================
# Quellenprotokoll
# ===========================================================================
def test_ki_kann_niemals_blockieren():
    """Sicherheitsregel: KI ist Kontext, kein Torwaechter."""
    b = ds.Beitrag(quelle=ds.LUNA, richtung=ds.BLOCKIEREND, gewicht=1.0)
    assert b.richtung == ds.DAGEGEN


def test_deterministische_quellen_duerfen_blockieren():
    p = ds.Entscheidungsprotokoll("BTC", broker="okx")
    p.blockiert(ds.RISK_GATE, "Tagesverlustgrenze erreicht")
    assert len(p.blockierer) == 1
    assert p.hauptquelle() == ds.RISK_GATE


def test_protokoll_haelt_quelle_und_gewicht_fest():
    p = ds.Entscheidungsprotokoll("ETH", asset_type="crypto", broker="okx")
    p.technik(ds.DAFUER, 0.6, "Trend intakt, RSI 58")
    p.news(ds.DAGEGEN, 0.3, "Regulierungsmeldung", quelle_name="GDELT")
    p.ki("luna", ds.DAFUER, "Aufmerksamkeit HIGH", modell="gpt-5.6-luna", kosten_usd=0.0004)
    daten = p.abschliessen("AUSGEFUEHRT", "alle Filter bestanden")

    quellen = {q["quelle"] for q in daten["quellen"]}
    assert quellen == {ds.TECHNIK, ds.NEWS, ds.LUNA}
    assert daten["hauptquelle"] == ds.TECHNIK
    assert daten["ki_beteiligt"] is True
    assert daten["ki_kosten_usd"] == pytest.approx(0.0004)
    assert daten["stimmungsbild"]["tendenz"] > 0


def test_protokoll_wird_geschrieben_und_gefiltert_gelesen(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADINGBOT_TEST_STATE_DIR", str(tmp_path))
    for symbol, ergebnis in (("BTC", "AUSGEFUEHRT"), ("ETH", "ABGELEHNT")):
        p = ds.Entscheidungsprotokoll(symbol, broker="okx")
        p.technik(ds.DAFUER, 0.5, "Test")
        p.abschliessen(ergebnis)
        ds.speichere(p)

    assert len(ds.lies(limit=10)) == 2
    assert len(ds.lies(limit=10, symbol="BTC")) == 1
    assert len(ds.lies(limit=10, ergebnis="ABGELEHNT")) == 1
    assert ds.lies(limit=10, nur_ki=True) == []
    assert len(ds.lies(limit=10, quelle=ds.TECHNIK)) == 2


def test_klartext_ist_lesbar():
    p = ds.Entscheidungsprotokoll("SOL", broker="okx")
    p.technik(ds.DAFUER, 0.7, "Ausbruch")
    p.abschliessen("AUSGEFUEHRT")
    text = p.klartext()
    assert "SOL" in text and "TECHNIK" in text and "Ausbruch" in text


# ===========================================================================
# Broker-Hub
# ===========================================================================
class FakeBroker:
    def __init__(self, name="fake", gesund=True):
        self.name = name
        self._gesund = gesund

    def connect(self):
        return True

    def disconnect(self):
        pass

    def is_connected(self):
        return True

    def health_check(self, force=False):
        return self._gesund

    def last_contact(self):
        return "2026-08-23T10:00:00+00:00"

    def ist_paper(self):
        return True

    def beschreibung(self):
        return f"{self.name} (PAPER)"

    def kontowert(self):
        return 1000.0


def test_hub_verbindet_beide_broker():
    hub = BrokerHub(factory=lambda name: FakeBroker(name))
    hub.registriere("etoro")
    hub.registriere("okx")
    assert hub.verbinde_alle() == {"etoro": True, "okx": True}
    assert set(hub.aktive_broker()) == {"etoro", "okx"}


def test_ausfall_eines_brokers_laesst_den_anderen_laufen():
    def factory(name):
        if name == "okx":
            raise VerbindungVerloren("OKX weg")
        return FakeBroker(name)

    hub = BrokerHub(factory=factory)
    hub.registriere("etoro")
    hub.registriere("okx")
    hub.verbinde_alle()

    assert hub.broker("etoro") is not None
    assert hub.broker("okx") is None
    assert hub.zustaende()["okx"]["status"] == GESTOERT
    assert hub.zustaende()["etoro"]["status"] == VERBUNDEN


def test_falsche_zugangsdaten_werden_nicht_endlos_wiederholt():
    versuche = []

    def factory(name):
        versuche.append(name)
        raise AuthentifizierungsFehler("Schluessel ungueltig")

    hub = BrokerHub(factory=factory)
    hub.registriere("okx")
    hub.verbinde("okx")
    hub.verbinde("okx")       # zweiter Versuch muss durch Backoff blockiert sein
    assert len(versuche) == 1
    assert hub.zustaende()["okx"]["status"] == ZUGANG_FEHLT


def test_deaktivierter_broker_wird_nicht_verbunden():
    hub = BrokerHub(factory=lambda name: FakeBroker(name))
    hub.registriere("okx", aktiv=False)
    assert hub.verbinde("okx") is False
    assert hub.zustaende()["okx"]["status"] == ABGEMELDET


def test_notaus_stoppt_beide_broker():
    hub = BrokerHub(factory=lambda name: FakeBroker(name))
    hub.registriere("etoro")
    hub.registriere("okx")
    hub.verbinde_alle()
    hub.notaus("Test")
    assert hub.aktive_broker() == {}
    assert hub.broker("etoro") is None
    hub.notaus_aufheben()
    assert hub.broker("etoro") is not None


def test_routing_nach_anlageklasse():
    hub = BrokerHub(factory=lambda name: FakeBroker(name))
    hub.registriere("etoro")
    hub.registriere("okx")
    hub.verbinde_alle()

    class Aktie:
        asset_type = "stock"

    class Coin:
        asset_type = "crypto"

    assert hub.name_fuer(Aktie()) == "etoro"
    assert hub.name_fuer(Coin()) == "okx"
    assert hub.broker_fuer(Coin()).name == "okx"


def test_gesundheitspruefung_markiert_ausfall():
    hub = BrokerHub(factory=lambda name: FakeBroker(name, gesund=False))
    hub.registriere("okx")
    hub.verbinde("okx")
    assert hub.pruefe_gesundheit()["okx"] is False
    assert hub.zustaende()["okx"]["status"] == GESTOERT


# ===========================================================================
# Einstellungsuebernahme
# ===========================================================================
def test_v6_risikozustand_wird_zum_etoro_topf(tmp_path):
    import settings_migration as sm
    quelle = tmp_path / "TradingBot_v6"
    ziel = tmp_path / "TradingBot_v7"
    quelle.mkdir()
    ziel.mkdir()
    (quelle / "risk_state.json").write_text(json.dumps({"realized_pnl_today": -12.5}),
                                            encoding="utf-8")
    kopiert, _ = sm.migrate_from(quelle, ziel)
    assert (ziel / "risk_state_etoro.json").exists()
    assert any("risk_state_etoro.json" in k for k in kopiert)


def test_neue_v7_dateien_stehen_in_der_uebernahmeliste():
    import settings_migration as sm
    for name in ("okx_credentials.json", "massive_credentials.json",
                 "universe_state.json", "ai_router_usage.json",
                 "decision_sources.jsonl"):
        assert name in sm.PERSISTENT_FILES


def test_bericht_nennt_fehlende_eingaben(tmp_path):
    import settings_migration as sm
    offen = sm.fehlende_eingaben(tmp_path)
    assert any("okx_credentials.json" in z for z in offen)
    assert any("massive" in z.lower() for z in offen)


def test_bestehende_dateien_werden_nicht_ueberschrieben(tmp_path):
    import settings_migration as sm
    quelle = tmp_path / "alt"
    ziel = tmp_path / "neu"
    quelle.mkdir()
    ziel.mkdir()
    (quelle / "favorites.json").write_text('["ALT"]', encoding="utf-8")
    (ziel / "favorites.json").write_text('["NEU"]', encoding="utf-8")
    kopiert, uebersprungen = sm.migrate_from(quelle, ziel, overwrite=False)
    assert "favorites.json" in uebersprungen
    assert (ziel / "favorites.json").read_text(encoding="utf-8") == '["NEU"]'
