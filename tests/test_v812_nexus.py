"""Regressionstests fuer TradingBot v8.1.2 NEXUS.

Jeder Test haelt einen Fehler fest, der in 8.1.1 real aufgetreten ist:

    News          Schalter und Schluessel wirkten erst nach einem Neustart
    FMP           Nachrichten im Gratistarif nicht enthalten (HTTP 402)
    MASSIVE       Verbindungstest prueft die falsche Faehigkeit
    WebUI         Speichern loeschte Quellen ohne Checkbox aus der Datei
    WebSocket     Subscription-ID mit Bindestrich -> OKX-Code 60033
    Kern          BTC/ETH/SOL waren per Rang entfernbar
    Ownership     nicht-terminale Orders verfielen nach sechs Stunden
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("TRADINGBOT_TEST_STATE_DIR", "/tmp/tradingbot_v812")
Path(os.environ["TRADINGBOT_TEST_STATE_DIR"]).mkdir(parents=True, exist_ok=True)


# ===========================================================================
# News-Einstellungen wirken sofort
# ===========================================================================
@pytest.fixture
def quellen_datei(tmp_path, monkeypatch):
    import live_settings
    monkeypatch.setattr(live_settings, "ROOT", tmp_path)
    live_settings.verwerfe_cache()
    yield tmp_path
    live_settings.verwerfe_cache()


def _schreibe(pfad: Path, daten: dict) -> None:
    (pfad / "news_sources_credentials.json").write_text(
        json.dumps(daten), encoding="utf-8")


def test_schalter_wirkt_ohne_neustart(quellen_datei):
    """Der Kernfehler aus 8.1.1: der Haken blieb wirkungslos."""
    import live_settings
    _schreibe(quellen_datei, {"finnhub_api_key": "K", "enabled": {"finnhub": False}})
    assert live_settings.quelle_aktiv("finnhub") is False

    _schreibe(quellen_datei, {"finnhub_api_key": "K", "enabled": {"finnhub": True}})
    live_settings.verwerfe_cache()
    assert live_settings.quelle_aktiv("finnhub") is True, \
        "Ein umgelegter Schalter muss ohne Neustart wirken"


def test_neuer_schluessel_wirkt_ohne_neustart(quellen_datei):
    import live_settings
    _schreibe(quellen_datei, {"finnhub_api_key": ""})
    assert live_settings.finnhub_key() == ""
    _schreibe(quellen_datei, {"finnhub_api_key": "FRISCH123"})
    live_settings.verwerfe_cache()
    assert live_settings.finnhub_key() == "FRISCH123"


def test_datei_schlaegt_config(quellen_datei, monkeypatch):
    """Die Datei hat Vorrang -- sonst gewinnt wieder der Startwert."""
    import config
    import live_settings
    monkeypatch.setattr(config, "NEWS_SOURCE_FINNHUB_ENABLED", False, raising=False)
    _schreibe(quellen_datei, {"enabled": {"finnhub": True}})
    live_settings.verwerfe_cache()
    assert live_settings.quelle_aktiv("finnhub") is True


def test_config_gilt_wenn_die_datei_schweigt(quellen_datei, monkeypatch):
    """Ohne Aussage in der Datei bleibt config gueltig (Dienste, Tests)."""
    import config
    import live_settings
    _schreibe(quellen_datei, {"enabled": {}})
    live_settings.verwerfe_cache()
    monkeypatch.setattr(config, "NEWS_SOURCE_FINNHUB_ENABLED", True, raising=False)
    assert live_settings.quelle_aktiv("finnhub") is True
    monkeypatch.setattr(config, "NEWS_SOURCE_FINNHUB_ENABLED", False, raising=False)
    assert live_settings.quelle_aktiv("finnhub") is False


def test_umgebungsvariable_schlaegt_alles(quellen_datei, monkeypatch):
    import live_settings
    _schreibe(quellen_datei, {"finnhub_api_key": "AUS_DATEI"})
    live_settings.verwerfe_cache()
    monkeypatch.setenv("FINNHUB_API_KEY", "AUS_UMGEBUNG")
    assert live_settings.finnhub_key() == "AUS_UMGEBUNG"


def test_alle_quellen_haben_einen_schalter():
    import live_settings
    schalter = live_settings.alle_schalter()
    for name in ("finnhub", "fmp", "massive", "alpha_vantage", "gdelt",
                 "sec_edgar", "yahoo_finance", "google_news", "nasdaq_halts"):
        assert name in schalter, f"{name} fehlt in den Schaltern"


# ===========================================================================
# FMP: Referenzquelle statt Nachrichtenquelle
# ===========================================================================
def test_fmp_nachrichten_sind_standardmaessig_aus():
    """Nachgemessen: /news/stock antwortet im Gratistarif mit HTTP 402."""
    import config
    assert getattr(config, "FMP_NEWS_ENABLED", True) is False


def test_fmp_symbolsuche_bleibt_trotz_gesperrter_nachrichten_nutzbar(monkeypatch):
    """Ein 402 der News darf die funktionierende Symbolsuche nicht mitreissen."""
    import config
    import live_settings
    from news_sources import MultiSourceNews

    monkeypatch.setattr(live_settings, "quelle_aktiv", lambda name: name in ("fmp",))
    monkeypatch.setattr(live_settings, "fmp_key", lambda: "TESTKEY")
    monkeypatch.setattr(config, "FMP_NEWS_ENABLED", False, raising=False)

    conf = MultiSourceNews().provider_configuration()
    assert conf["FMP Symbol Search"] is True
    assert conf["FMP"] is False


def test_fmp_402_ist_ein_tarifzustand_kein_fehler():
    from fmp_reference import FMPReferenz, FMPTarifFehlt

    class Antwort:
        status_code = 402
        content = b"x"

        @staticmethod
        def json():
            return {}

    class Session:
        headers = {}

        def get(self, *a, **k):
            return Antwort()

    referenz = FMPReferenz("KEY")
    referenz.session = Session()
    with pytest.raises(FMPTarifFehlt) as info:
        referenz.quote("AAPL")
    assert "Tarif" in str(info.value)
    # Wichtig: KEIN 24h-Backoff, die uebrigen Faehigkeiten bleiben nutzbar.
    frei, _ = referenz.budget.frei()
    assert frei is True


def test_fmp_tagesbudget_wird_hart_eingehalten():
    from fmp_reference import FMPReferenz

    class Antwort:
        status_code = 200
        content = b"x"

        @staticmethod
        def json():
            return [{"symbol": "AAPL"}]

    class Session:
        headers = {}

        def __init__(self):
            self.aufrufe = 0

        def get(self, *a, **k):
            self.aufrufe += 1
            return Antwort()

    referenz = FMPReferenz("KEY", tageslimit=3)
    referenz.session = Session()
    for n in range(3):
        referenz.search_symbol("AAPL", n+1)  # Distinct requests; identical queries are cached.
    with pytest.raises(RuntimeError) as info:
        referenz.search_symbol("AAPL", 4)
    assert "Budget" in str(info.value) or "pausiert" in str(info.value)
    assert referenz.session.aufrufe == 3, "Nach dem Limit darf nichts mehr rausgehen"


def test_fmp_schluessel_taucht_in_keiner_fehlermeldung_auf():
    import requests
    from fmp_reference import FMPReferenz

    class Session:
        headers = {}

        def get(self, *a, **k):
            raise requests.exceptions.ConnectTimeout("kaputt")

    referenz = FMPReferenz("GEHEIMERSCHLUESSEL123")
    referenz.session = Session()
    with pytest.raises(RuntimeError) as info:
        referenz.quote("AAPL")
    assert "GEHEIMERSCHLUESSEL123" not in str(info.value)


def test_fmp_referenzcache_verhindert_taegliche_ueberlast(tmp_path, monkeypatch):
    """250 Anfragen am Tag reichen nur mit Zwischenspeicher."""
    monkeypatch.setenv("TRADINGBOT_TEST_STATE_DIR", str(tmp_path))
    from fmp_reference import FMPReferenz

    class Antwort:
        status_code = 200
        content = b"x"

        @staticmethod
        def json():
            return [{"symbol": "AAPL", "sector": "Technology",
                     "isActivelyTrading": True, "price": 100.0,
                     "volume": 1000, "close": 100.0, "vwap": 100.0,
                     "currency": "USD", "open": 100., "high": 101., "low": 99.,
                     "date": (__import__("datetime").datetime.now(__import__("zoneinfo").ZoneInfo("America/New_York")).date()-__import__("datetime").timedelta(days=1)).isoformat()}]

    class Session:
        headers = {}

        def __init__(self):
            self.aufrufe = 0

        def get(self, *a, **k):
            self.aufrufe += 1
            return Antwort()

    referenz = FMPReferenz("KEY", tageslimit=250)
    referenz.session = Session()
    erste = referenz.referenzdaten("AAPL")
    nach_erstem = referenz.session.aufrufe
    zweite = referenz.referenzdaten("AAPL")

    assert erste.get("frisch") is True
    assert zweite.get("frisch") is False, "Zweiter Zugriff muss aus dem Speicher kommen"
    assert referenz.session.aufrufe == nach_erstem, "Kein zusaetzlicher Netzaufruf"
    assert zweite.get("sektor") == "Technology"
    assert zweite.get("aktiv") is True


def test_fmp_erkennt_delisting_ueber_das_profil(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADINGBOT_TEST_STATE_DIR", str(tmp_path))
    from fmp_reference import FMPReferenz

    class Antwort:
        status_code = 200
        content = b"x"

        @staticmethod
        def json():
            return [{"symbol": "TOT", "isActivelyTrading": False}]

    class Session:
        headers = {}

        def get(self, *a, **k):
            return Antwort()

    referenz = FMPReferenz("KEY")
    referenz.session = Session()
    assert referenz.ist_aktiv("TOT") is False


# ===========================================================================
# MASSIVE: Test prueft die tatsaechlich genutzte Faehigkeit
# ===========================================================================
def test_massive_test_meldet_nicht_ok_wenn_nachrichten_fehlen():
    """8.1.1 meldete OK, obwohl der News-Endpunkt gar nicht im Tarif war."""
    from massive_api import MassiveClient

    class Antwort:
        def __init__(self, status, payload):
            self.status_code = status
            self.content = b"x"
            self._payload = payload

        def json(self):
            return self._payload

    class Session:
        headers = {}

        def get(self, url, **k):
            if "/v3/reference/tickers" in url:
                return Antwort(200, {"status": "OK", "results": [{"ticker": "A"}]})
            return Antwort(402, {})

    client = MassiveClient("KEY")
    client.session = Session()
    ergebnis = client.verbindungstest()

    assert ergebnis["ok"] is False, "Ohne Nachrichten ist MASSIVE keine Nachrichtenquelle"
    # v10.1: The second capability waits for the shared Free pacing gate.
    # Advance only the local fake clock; no sleep/network bypass in production.
    assert ergebnis["faehigkeiten"]["Referenzdaten"]["ok"] is None
    assert ergebnis["faehigkeiten"]["Referenzdaten"]["pending"] is True
    later = client.store.clock() + 16
    later_mono = client.store.monotonic() + 16
    client.store.clock = lambda: later
    client.store.monotonic = lambda: later_mono
    ergebnis = client.verbindungstest()
    assert ergebnis["ok"] is False
    assert ergebnis["faehigkeiten"]["Referenzdaten"]["ok"] is True
    assert ergebnis["faehigkeiten"]["Nachrichten"]["ok"] is False
    assert ergebnis["faehigkeiten"]["Nachrichten"].get("tarif") is True
    assert "Nachrichten" in ergebnis["detail"]


def test_massive_test_meldet_ok_wenn_beides_geht():
    from massive_api import MassiveClient

    class Antwort:
        status_code = 200
        content = b"x"

        @staticmethod
        def json():
            return {"status": "OK", "results": [{"ticker": "A", "title": "X"}]}

    class Session:
        headers = {}

        def get(self, *a, **k):
            return Antwort()

    client = MassiveClient("KEY")
    client.session = Session()
    assert client.verbindungstest()["ok"] is True


# ===========================================================================
# WebUI: Quellenschalter
# ===========================================================================
def test_speichern_loescht_keine_quellen_ohne_checkbox(tmp_path, monkeypatch):
    """8.1.1 warf Yahoo, Google News und Nasdaq Halts bei jedem Speichern weg."""
    from webui import settings_store
    monkeypatch.setattr(settings_store, "ROOT", tmp_path)
    (tmp_path / "news_sources_credentials.json").write_text(json.dumps({
        "enabled": {"yahoo_finance": True, "google_news": True,
                    "nasdaq_halts": True, "finnhub": False}}), encoding="utf-8")

    settings_store.save({"news": {"enabled": {"finnhub": True}}})
    danach = json.loads((tmp_path / "news_sources_credentials.json").read_text())["enabled"]

    assert danach["finnhub"] is True, "Die Aenderung muss ankommen"
    for name in ("yahoo_finance", "google_news", "nasdaq_halts"):
        assert danach.get(name) is True, f"{name} darf nicht verschwinden"


def test_webui_kennt_alle_quellenschalter():
    seite = (Path(__file__).resolve().parent.parent
             / "webui" / "templates" / "settings.html").read_text(encoding="utf-8")
    for name in ("finnhub", "fmp", "alpha_vantage", "gdelt", "sec_edgar",
                 "yahoo_finance", "google_news", "nasdaq_halts"):
        assert f"news.enabled.{name}" in seite, f"Schalter fuer {name} fehlt in der WebUI"
    assert "news.enabled.finanzen_net" not in seite
    assert "news.massive_enabled" in seite


# ===========================================================================
# WebUI: Universums-Seite
# ===========================================================================
def test_universums_seite_trennt_beide_anbieter():
    from webui.state import universe
    daten = universe()
    assert "okx" in daten["broker"] and "etoro" in daten["broker"]
    # GEAENDERT IN v8.1.5: Auch Aktien werden autonom aufgenommen -- gegen
    # feste Deckel und mit 4 h Bewaehrung. Die Seite muss beides zeigen.
    assert daten["broker"]["okx"]["autonome_aufnahme"] is True
    assert daten["broker"]["etoro"]["autonome_aufnahme"] is True
    assert daten["broker"]["etoro"]["beobachtung_stunden"] == 4.0


def test_universums_seite_kennt_den_festen_kern():
    from webui.state import universe
    daten = universe()
    assert set(daten["kernwerte"]) >= {"BTC", "ETH", "SOL"}


def test_universums_route_ist_angemeldeten_vorbehalten():
    quelle = (Path(__file__).resolve().parent.parent / "webui" / "app.py").read_text(encoding="utf-8")
    assert '@app.get("/universe"' in quelle
    assert '@app.get("/api/universe")' in quelle
    block = quelle.split('@app.get("/api/universe")')[1][:200]
    assert "_session(request)" in block, "Die API muss eine Anmeldung verlangen"


# ===========================================================================
# P0-01: OKX Private WebSocket
# ===========================================================================
def test_subscription_id_ist_okx_konform():
    """Der Bindestrich in 'nexus8-private' loeste OKX-Code 60033 aus."""
    from broker.okx_stream import SUBSCRIPTION_ID
    assert re.fullmatch(r"[A-Za-z0-9]{1,32}", SUBSCRIPTION_ID), \
        "OKX erlaubt im Feld 'id' nur alphanumerische Zeichen, maximal 32"


def test_stream_wird_erst_nach_subscribe_ack_gesund():
    from broker.okx_stream import OKXPrivateStream

    class App:
        def __init__(self):
            self.sent = []

        def send(self, value):
            self.sent.append(value)

        def close(self):
            pass

    app = App()
    stream = OKXPrivateStream("K", "S", "P", demo=True)
    stream._on_open(app)
    assert stream.status().zustand == "CONNECTED"

    stream._on_message(app, json.dumps({"event": "login", "code": "0"}))
    assert stream.status().zustand == "AUTHENTICATED"
    assert stream.status().healthy is False, "Login allein macht den Stream nicht gesund"

    stream._on_message(app, json.dumps({"event": "subscribe", "arg": {"channel": "account"}}))
    assert stream.status().zustand == "AUTHENTICATED", "Ein Kanal reicht nicht"

    stream._on_message(app, json.dumps({"event": "subscribe", "arg": {"channel": "orders"}}))
    assert stream.status().zustand == "SUBSCRIBED"
    assert set(stream.status().kanaele) == {"account", "orders"}


def test_60033_wird_als_konfigurationsfehler_erkannt():
    """Sonst verbindet sich der Bot im Minutentakt gegen dieselbe Ablehnung."""
    from broker.okx_stream import OKXPrivateStream

    class App:
        def send(self, value):
            pass

        def close(self):
            pass

    stream = OKXPrivateStream("K", "S", "P", demo=True)
    stream._on_open(App())
    stream._on_message(App(), json.dumps({
        "event": "error", "code": "60033", "msg": "Parameter id error"}))
    status = stream.status()
    assert status.zustand == "KONFIGURATIONSFEHLER"
    assert "60033" in status.konfigurationsfehler
    assert status.healthy is False


def test_daten_ohne_abo_gelten_nicht_als_frisch():
    from broker.okx_stream import OKXPrivateStream

    class App:
        def send(self, value):
            pass

        def close(self):
            pass

    app = App()
    stream = OKXPrivateStream("K", "S", "P", demo=True)
    stream._on_open(app)
    stream._on_message(app, json.dumps({"event": "login", "code": "0"}))
    stream._on_message(app, json.dumps({
        "arg": {"channel": "account"},
        "data": [{"details": [{"ccy": "EUR", "availBal": "10", "cashBal": "10"}]}]}))
    assert stream.balances() is None, "Ohne subscribe-ACK duerfen Daten nicht gelten"


def test_pong_haelt_die_verbindung_am_leben():
    from broker.okx_stream import OKXPrivateStream

    class App:
        def send(self, value):
            pass

        def close(self):
            pass

    stream = OKXPrivateStream("K", "S", "P", demo=True)
    stream._on_open(App())
    stream._letzter_ping = 0.0
    stream._on_message(App(), "pong")
    assert stream._letzter_ping > 0.0


# ===========================================================================
# P0-06: fester Krypto-Kern
# ===========================================================================
@pytest.fixture
def manager(tmp_path):
    from universe.manager import UniverseManager
    from universe.modelle import UniverseZustand
    return UniverseManager(UniverseZustand(tmp_path / "universe_state.json"))


def _kandidat(symbol, rang, sicherheitsabgang=""):
    from universe.modelle import TIER_ETABLIERT, UniverseKandidat, UniverseScore
    k = UniverseKandidat(symbol=symbol, broker="okx", asset_type="crypto",
                         inst_id=f"{symbol}-USDT", preis=10.0, bid=9.99, ask=10.01,
                         spread_pct=0.001, volumen_quote_24h=5e8, alter_tage=2000)
    eintrag = {"kandidat": k, "score": UniverseScore(gesamt=0.8, teile={}, stufe="quality"),
               "rang": rang, "tier": TIER_ETABLIERT, "tier_begruendung": "Test"}
    if sicherheitsabgang:
        eintrag["sicherheitsabgang"] = sicherheitsabgang
    return eintrag


def test_kernwerte_sind_als_kern_erkannt(manager):
    import config
    for symbol in config.CRYPTO_CORE_SYMBOLS:
        assert manager.ist_kern("okx", symbol) is True
    assert manager.ist_kern("okx", "C99") is False
    assert manager.ist_kern("etoro", "BTC") is False, "Aktien haben keinen Kern"


def test_kernwert_ueberlebt_beliebig_viele_schlechte_raenge(manager):
    manager.lauf({"broker": "okx", "rangliste": [_kandidat("BTC", 1)]})
    for _ in range(6):
        manager.lauf({"broker": "okx", "rangliste": [_kandidat("BTC", 200)]})
    mitglied = manager.zustand.hole("okx", "BTC")
    assert mitglied is not None, "Ein fester Kern darf nie per Rang verschwinden"
    assert mitglied.schlechte_raenge_in_folge == 0
    assert "BTC" in manager.handelbare_symbole("okx")


def test_kernwert_wird_bei_sicherheitsfall_gesperrt_statt_entfernt(manager):
    from universe.modelle import KERN_SICHERHEIT_BLOCKIERT
    manager.lauf({"broker": "okx", "rangliste": [_kandidat("BTC", 1)]})
    manager.lauf({"broker": "okx",
                  "rangliste": [_kandidat("BTC", 1, sicherheitsabgang="Spread extrem")]})

    mitglied = manager.zustand.hole("okx", "BTC")
    assert mitglied is not None, "Auch gesperrt bleibt der Kern im Monitoring"
    assert mitglied.kern_blockiert is True
    assert KERN_SICHERHEIT_BLOCKIERT in mitglied.abganggrund
    assert "BTC" not in manager.handelbare_symbole("okx"), \
        "Ein gesperrter Kernwert darf keine neuen Einstiege erzeugen"


def test_nicht_kernwerte_bleiben_normal_entfernbar(manager):
    from datetime import datetime, timedelta, timezone
    now = datetime(2026, 9, 12, 6, tzinfo=timezone.utc)
    manager.lauf({"broker": "okx", "rangliste": [_kandidat("C99", 1)]}, jetzt=now)
    diff = manager.lauf({"broker": "okx",
                         "rangliste": [_kandidat("C99", 1, sicherheitsabgang="Delisting")]},
                        jetzt=now+timedelta(minutes=15))
    assert "C99" not in diff.entfernt  # FIX3: one membership batch per local day.
    assert "C99" not in manager.handelbare_symbole("okx")  # Safety is immediate.
    diff = manager.lauf({"broker": "okx",
                         "rangliste": [_kandidat("C99", 1, sicherheitsabgang="Delisting")]},
                        jetzt=now+timedelta(days=1))
    assert "C99" in diff.entfernt


# ===========================================================================
# P0-05: Ownership ohne TTL-Verlust
# ===========================================================================
def test_nicht_terminale_order_verfaellt_nicht_nach_stunden(tmp_path):
    """8.1.1 loeschte UNKNOWN_AFTER_SUBMIT nach sechs Stunden."""
    from order_ownership import OrderOwnershipRegistry

    registry = OrderOwnershipRegistry(tmp_path / "reg.json", pending_ttl=60)
    registry.register_pending("BTC", {"cl_ord_id": "abc"}, "crypto")
    registry.setze_zustand("BTC", "UNKNOWN_AFTER_SUBMIT", "crypto", ord_id="123")

    for meta in registry.pending.values():
        meta["created_at_ts"] = time.time() - 24 * 3600
    registry.cleanup()

    assert registry.nicht_terminale(), "Eine nicht beantwortete Order muss erhalten bleiben"
    assert registry.pending_metadata("BTC", "crypto").get("ord_id") == "123"


def test_nur_geplante_order_verfaellt_weiterhin(tmp_path):
    from order_ownership import OrderOwnershipRegistry

    registry = OrderOwnershipRegistry(tmp_path / "reg.json", pending_ttl=60)
    registry.register_pending("ETH", {}, "crypto")
    def altere_persistierten_eintrag():
        for meta in registry.pending.values():
            meta["created_at_ts"] = time.time() - 24 * 3600
    registry._mutate(altere_persistierten_eintrag)
    registry.cleanup()
    assert registry.pending_metadata("ETH", "crypto") == {}


@pytest.mark.parametrize("zustand,darf_verfallen", [
    ("PLANNED", True),
    ("SUBMITTING", False),
    ("SUBMITTED", False),
    ("PARTIALLY_FILLED", False),
    ("UNKNOWN_AFTER_SUBMIT", False),
    ("LATE_FILL_WATCH", False),
])
def test_nur_vorbereitete_zustaende_duerfen_verfallen(zustand, darf_verfallen):
    from order_ownership import darf_per_ttl_verfallen
    assert darf_per_ttl_verfallen({"zustand": zustand}) is darf_verfallen


def test_altbestand_mit_brokerreferenz_verfaellt_nicht():
    """Eintraege aus 8.1.1 haben kein Zustandsfeld -- konservativ behandeln."""
    from order_ownership import darf_per_ttl_verfallen
    assert darf_per_ttl_verfallen({"ord_id": "123"}) is False
    assert darf_per_ttl_verfallen({}) is True


# ===========================================================================
# P0-07: Guthaben und ungeklaerte Exposure klassifizieren
# ===========================================================================
def test_cash_wird_nicht_als_kryptobestand_gewertet():
    """USD tauchte im Log als vermeintlicher Bestand auf."""
    import exposure_klassifizierung as ek
    ergebnis = ek.klassifiziere({"USD": {"gesamt": 500}, "EUR": {"gesamt": 100}})
    klassen = {b["waehrung"]: b["klasse"] for b in ergebnis["bestaende"]}
    assert klassen["USD"] == ek.CASH
    assert klassen["EUR"] == ek.CASH
    assert ergebnis["einstiege_gesperrt"] is False


def test_gebuchte_position_ist_bot_managed():
    import exposure_klassifizierung as ek
    ergebnis = ek.klassifiziere(
        {"BTC": {"gesamt": 0.5}},
        positionsbuch=[{"symbol": "BTC", "menge": 0.5}],
        preise={"BTC": 60000})
    assert ergebnis["bestaende"][0]["klasse"] == ek.BOT_MANAGED
    assert ergebnis["einstiege_gesperrt"] is False


def test_manueller_altbestand_wird_nicht_angefasst():
    """Der Bot verkauft nie, was er nicht selbst eroeffnet hat."""
    import exposure_klassifizierung as ek
    ergebnis = ek.klassifiziere({"ETH": {"gesamt": 2.0}}, preise={"ETH": 3000})
    bestand = ergebnis["bestaende"][0]
    assert bestand["klasse"] == ek.ACCOUNT_ASSET
    assert bestand["sperrt_einstiege"] is False


def test_offene_bot_order_sperrt_neue_einstiege():
    import exposure_klassifizierung as ek
    ergebnis = ek.klassifiziere(
        {"XRP": {"gesamt": 1000}},
        offene_orders=[{"symbol": "XRP"}], preise={"XRP": 0.5})
    assert ergebnis["bestaende"][0]["klasse"] == ek.RESIDUAL
    assert ergebnis["einstiege_gesperrt"] is True


def test_ueberhang_gegenueber_dem_buch_bleibt_konto_asset():
    """Nur die bewiesene Buchmenge ist Bottrade; der Ueberhang ist extern."""
    import exposure_klassifizierung as ek
    ergebnis = ek.klassifiziere(
        {"BTC": {"gesamt": 1.5}},
        positionsbuch=[{"symbol": "BTC", "menge": 0.5}],
        preise={"BTC": 60000})
    klassen = [b["klasse"] for b in ergebnis["bestaende"]]
    assert ek.BOT_MANAGED in klassen
    assert ek.ACCOUNT_ASSET in klassen
    assert ek.UNKNOWN not in klassen
    assert ergebnis["einstiege_gesperrt"] is False


def test_staubreste_sperren_nichts():
    import exposure_klassifizierung as ek
    ergebnis = ek.klassifiziere({"SHIB": {"gesamt": 100}}, preise={"SHIB": 0.000001})
    assert ergebnis["bestaende"][0]["klasse"] == ek.ACCOUNT_ASSET
    assert ergebnis["einstiege_gesperrt"] is False


def test_kurzfassung_nennt_den_sperrgrund():
    import exposure_klassifizierung as ek
    ergebnis = ek.klassifiziere({"XRP": {"gesamt": 1000}},
                                offene_orders=[{"symbol": "XRP"}], preise={"XRP": 0.5})
    text = ek.kurzfassung(ergebnis)
    assert "GESPERRT" in text and "XRP" in text
