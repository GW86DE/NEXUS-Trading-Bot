"""v8.1.5 -- zwei Meldungen, die die Wahrheit nicht gesagt haben.

Beide Fehler haben nichts falsch gehandelt. Beide haben Georg tagelang
angelogen, und das ist schlimmer: eine falsche Begruendung schickt die
Fehlersuche in die falsche Richtung.

1. "Positionslimit erreicht (offen=0)"
   Am 25.08.2026 der haeufigste Ablehnungsgrund des Tages -- 243 Meldungen.
   Bei null offenen Positionen kann kein Positionslimit greifen. In
   Wahrheit lief die Equity-Tagesbremse. Sie hatte in der Begruendungskette
   von ``live_trader`` keinen eigenen Zweig und fiel in den Sammelfall.

2. "NONE" und "UPDATED_AT" in der Universumstabelle
   Eine oder-Kette hat bei leerem Positionsbereich auf die ganze Datei
   zurueckgegriffen; deren Feldnamen wurden dann zu Symbolen.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("TRADINGBOT_TEST_STATE_DIR", "/tmp/tradingbot_tests")
Path(os.environ["TRADINGBOT_TEST_STATE_DIR"]).mkdir(parents=True, exist_ok=True)

WURZEL = Path(__file__).resolve().parent.parent


# ===========================================================================
# 1. Der falsche Ablehnungsgrund
# ===========================================================================
def zustand(**felder):
    """Ein RiskState fuer heute, mit gezielt gesetzten Feldern."""
    from risk_manager import RiskState, _handelstag_heute
    # Der Bot verwendet bewusst Europe/Berlin statt der Host-Zeitzone. Der
    # Test muss denselben Handelstag nehmen, sonst setzt reset_if_new_day()
    # rund um Mitternacht die absichtlich gesetzten Sperren zurueck.
    s = RiskState(current_date=_handelstag_heute())
    for name, wert in felder.items():
        setattr(s, name, wert)
    return s


def test_equity_bremse_nennt_sich_beim_namen():
    """Der konkrete Fall vom 25.08.: offen=0, gesperrt, falsche Meldung."""
    import risk_manager as rm

    s = zustand(open_positions=0, equity_guard_halted=True,
                equity_drawdown_pct=-0.034)
    grund = rm.kaufsperre_grund(s)

    assert not rm.can_open_new_position(s)
    assert "Positionslimit" not in grund, (
        "Genau diese Verwechslung war der Fehler: die Equity-Tagesbremse "
        f"erschien als Positionslimit. Gemeldet wurde: {grund!r}")
    assert "Equity" in grund
    # Die Zahl muss mit: ohne sie ist die Meldung nicht nachpruefbar.
    assert "-3.40" in grund or "-3,40" in grund
    # Und der beruhigende Teil: Schutz und Verkaeufe laufen weiter.
    assert "Verkaeufe" in grund or "Verkäufe" in grund


def test_positionslimit_meldet_auch_das_limit():
    """"(offen=8)" allein sagt nicht, ob 8 viel ist."""
    import config
    import risk_manager as rm

    s = zustand(open_positions=int(config.MAX_OPEN_POSITIONS))
    grund = rm.kaufsperre_grund(s)

    assert "Positionslimit" in grund
    assert f"offen={config.MAX_OPEN_POSITIONS}" in grund
    assert f"Limit={config.MAX_OPEN_POSITIONS}" in grund


def test_offen_null_ist_niemals_ein_positionslimit():
    """Die Aussage, die den Fehler ueberhaupt sichtbar gemacht hat.

    Solange das Limit groesser als 0 ist, darf bei null offenen Positionen
    nie ein Positionslimit gemeldet werden -- egal, welcher andere Grund
    gerade greift.
    """
    import risk_manager as rm

    faelle = [
        dict(equity_guard_halted=True, equity_drawdown_pct=-0.05),
        dict(trading_halted=True, realized_pnl_today=-226.60),
        dict(cooldown_until=datetime.now() + timedelta(minutes=30)),
        dict(trades_today=999),
        dict(),   # gar nichts gesperrt
    ]
    for felder in faelle:
        s = zustand(open_positions=0, **felder)
        grund = rm.kaufsperre_grund(s)
        assert "Positionslimit" not in grund, (
            f"Bei offen=0 gemeldet: {grund!r} (Zustand {felder})")


@pytest.mark.parametrize("felder,erwartet", [
    (dict(trading_halted=True, realized_pnl_today=-226.60), "Tagesverlustlimit"),
    (dict(equity_guard_halted=True, equity_drawdown_pct=-0.031), "Equity"),
    (dict(cooldown_until=datetime.now() + timedelta(minutes=30)), "Cooldown"),
    (dict(trades_today=10_000), "Trades pro Tag"),
])
def test_jeder_sperrgrund_hat_seine_eigene_meldung(felder, erwartet):
    import risk_manager as rm

    s = zustand(open_positions=0, **felder)
    assert not rm.can_open_new_position(s)
    assert erwartet in rm.kaufsperre_grund(s)


def test_kostenquote_wird_als_kostenquote_gemeldet():
    import config
    import risk_manager as rm

    s = zustand(open_positions=0,
                trades_today=int(getattr(config, "COST_RATIO_MIN_TRADES", 5)) + 1,
                gross_profit_today=100.0, estimated_costs_today=90.0)
    assert s.cost_pressure_active()
    assert "Kostenquote" in rm.kaufsperre_grund(s)


def test_entscheidung_und_begruendung_bleiben_deckungsgleich():
    """Der eigentliche Waechter.

    Der Fehler war moeglich, weil Entscheidung (``can_open_new_position``)
    und Begruendung (Kette in ``live_trader``) getrennt gepflegt wurden.
    Jetzt ist die Begruendung die Entscheidung. Dieser Test haelt das fest:
    gesperrt <=> es gibt einen Text.
    """
    import risk_manager as rm

    from itertools import product
    schalter = [
        ("trading_halted", [False, True]),
        ("equity_guard_halted", [False, True]),
        ("trades_today", [0, 10_000]),
        ("open_positions", [0, 10_000]),
    ]
    namen = [n for n, _ in schalter]
    for werte in product(*[w for _, w in schalter]):
        s = zustand(**dict(zip(namen, werte)))
        erlaubt = rm.can_open_new_position(s)
        grund = rm.kaufsperre_grund(s)
        assert erlaubt == (grund == ""), (
            f"Zustand {dict(zip(namen, werte))}: erlaubt={erlaubt}, "
            f"Grund={grund!r} -- das passt nicht zusammen")


def test_positionslimit_null_ist_ein_konfigurationsfehler():
    """MAX_OPEN_POSITIONS=0 sperrt -- aber es muss auch so heissen."""
    import config
    import risk_manager as rm

    alt = config.MAX_OPEN_POSITIONS
    try:
        config.MAX_OPEN_POSITIONS = 0
        s = zustand(open_positions=0)
        grund = rm.kaufsperre_grund(s)
        assert not rm.can_open_new_position(s)
        assert "Konfigurationsfehler" in grund
        assert "MAX_OPEN_POSITIONS" in grund
    finally:
        config.MAX_OPEN_POSITIONS = alt


def test_live_trader_baut_keine_eigene_begruendungskette_mehr():
    """Zwei Quellen fuer denselben Sachverhalt waren die Ursache.

    Die Meldungstexte gehoeren jetzt ausschliesslich in risk_manager.
    """
    quelle = (WURZEL / "live_trader.py").read_text(encoding="utf-8")
    import ast
    baum = ast.parse(quelle)
    texte = [k.value for k in ast.walk(baum)
             if isinstance(k, ast.Constant) and isinstance(k.value, str)]
    # Kommentare und Docstrings duerfen den Fall erklaeren -- gebaut werden
    # darf die Meldung hier nicht mehr.
    texte = [t for t in texte if len(t.splitlines()) == 1]
    treffer = [t for t in texte
               if "Positionslimit erreicht" in t or "Tagesverlustlimit erreicht" in t]
    assert not treffer, f"live_trader formuliert Sperrgruende selbst: {treffer}"


# ===========================================================================
# 2. "NONE" und "UPDATED_AT" im Universum
# ===========================================================================
@pytest.fixture
def positionsdatei(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADINGBOT_TEST_STATE_DIR", str(tmp_path))

    def schreibe(inhalt):
        (tmp_path / "position_state.json").write_text(
            json.dumps(inhalt), encoding="utf-8")
        import stock_universe_runner as sur
        return sur.StockUniverseRunner(universum=None)._offene_positionen()

    return schreibe


def test_leerer_positionsbereich_liefert_keine_feldnamen(positionsdatei):
    """Der Fehlerfall aus Screenshot 20260825_200730.

    ``{"updated_at": ..., "positions": {}}``: das leere Dict ist falsy, die
    oder-Kette fiel auf die ganze Datei zurueck, und "updated_at" wurde zum
    Symbol.
    """
    symbole = positionsdatei({"updated_at": "2026-08-25T20:07:30Z",
                              "positions": {}})
    assert symbole == []


def test_symbol_none_wird_nie_zum_text_none(positionsdatei):
    """``str(None)`` ergibt "None" -- nicht leer und damit faelschlich wahr."""
    symbole = positionsdatei({"positions": {"AAPL": {"symbol": None,
                                                     "quantity": 3}}})
    assert "NONE" not in symbole
    # Der Schluessel ist ein brauchbarer Ticker und rettet den Eintrag.
    assert symbole == ["AAPL"]


def test_echte_positionen_kommen_unveraendert_durch(positionsdatei):
    symbole = positionsdatei({"updated_at": "x", "positions": {
        "AAPL": {"symbol": "AAPL", "quantity": 3},
        "FLR.US": {"symbol": "FLR.US", "quantity": 12.0},
        "BRK.B": {"symbol": "BRK.B", "quantity": 1},
    }})
    assert symbole == ["AAPL", "BRK.B", "FLR.US"]


def test_geschlossene_position_bleibt_draussen(positionsdatei):
    symbole = positionsdatei({"positions": {
        "AAPL": {"symbol": "AAPL", "quantity": 0},
        "MSFT": {"symbol": "MSFT", "quantity": 2},
    }})
    assert symbole == ["MSFT"]


def test_datei_ohne_positionsbereich_filtert_die_feldnamen(positionsdatei):
    """Alte Dateien ohne Bereich duerfen weiter funktionieren."""
    symbole = positionsdatei({"updated_at": "x", "version": 2,
                              "NVDA": {"symbol": "NVDA", "quantity": 5}})
    assert symbole == ["NVDA"]


def test_liste_statt_zuordnung(positionsdatei):
    symbole = positionsdatei({"positions": [
        {"symbol": "GOOGL", "quantity": 1},
        {"symbol": None},
        "kaputt",
        {"symbol": "COST", "quantity": 4},
    ]})
    assert symbole == ["COST", "GOOGL"]


def test_fehlendes_mengenfeld_verliert_keine_position(positionsdatei):
    """Alte Dateien ohne Mengenfeld: im Zweifel gehalten, nicht verloren."""
    assert positionsdatei({"positions": {"TSLA": {"symbol": "TSLA"}}}) == ["TSLA"]


@pytest.mark.parametrize("text,gueltig", [
    ("AAPL", True), ("FLR.US", True), ("BRK.B", True), ("RDS-A", True),
    ("A", True),
    ("None", False), ("NONE", False), ("updated_at", False),
    ("UPDATED_AT", False), ("", False), ("   ", False),
    ("2026-08-25T20:07:30Z", False), ("positions", False),
    ("1234", False), ("sehr langer text", False), (None, False), (7, False),
])
def test_tickerform(text, gueltig):
    from stock_universe_runner import ist_tickerartig
    assert ist_tickerartig(text) is gueltig


# ===========================================================================
# 3. "Markt geschlossen", obwohl die Boerse offen war
# ===========================================================================
class FakeInstrument:
    def __init__(self, name="GOOGL", exchange="NASDAQ", currency="USD",
                 asset_type="stock"):
        self.name = name
        self.symbol = name
        self.exchange = exchange
        self.currency = currency
        self.asset_type = asset_type


class FakeBroker:
    """Broker, der genau das meldet, was eToro am 25.08. gemeldet hat."""

    def __init__(self, tradable=None, quote_age=None):
        self._tradable = tradable
        self._age = quote_age

    def market_session_status(self, instrument):
        return {"broker_tradable": self._tradable,
                "quote_age_seconds": self._age,
                "source": "eToro Public API rates",
                "detail": f"Quote-Alter {self._age}s"}


def rth(tag=25):
    """Ein Zeitpunkt mitten in der regulaeren US-Handelszeit."""
    # 17:00 UTC = 13:00 New York, ein Dienstag.
    return datetime(2026, 8, tag, 17, 0, tzinfo=timezone.utc)


def test_der_fall_googl_heisst_nicht_mehr_markt_geschlossen():
    """Screenshot vom 25.08.: "Markt geschlossen" bei offener US-Boerse.

    Der Kurs war 18741 s alt. Die Sperre war richtig, die Begruendung nicht.
    """
    from market_session import market_session_status, entry_allowed

    st = market_session_status(FakeBroker(tradable=None, quote_age=18741),
                               FakeInstrument("GOOGL"), now=rth(),
                               quote_max_age_seconds=180.0)
    erlaubt, grund = entry_allowed(st)

    assert not erlaubt                       # gesperrt bleibt gesperrt
    assert st.downgrade_grund == "stale_quote"
    assert st.kalender_offen is True
    assert "geschlossen" not in grund.lower(), (
        f"Der Kalender sagt offen -- 'geschlossen' waere falsch: {grund!r}")
    assert "GOOGL" in grund
    assert "5 h 12 min" in grund              # das Alter, im Klartext
    assert "3 min" in grund                   # und die Grenze, gegen die es lief
    assert "Datenproblem" in grund


def test_kursalter_kommt_im_status_an():
    """Der Wert lag frueher nur im Rohdatensatz und ging unterwegs verloren."""
    from market_session import market_session_status

    st = market_session_status(FakeBroker(quote_age=474),
                               FakeInstrument("COST"), now=rth(),
                               quote_max_age_seconds=180.0)
    assert st.quote_age_seconds == 474
    assert st.quote_max_age_seconds == 180.0
    assert st.symbol == "COST"
    assert st.scheduled_session == "REGULAR"   # der Kalender bleibt sichtbar
    assert st.session == "CLOSED"              # herabgestuft, aber nachvollziehbar
    assert st.to_dict()["quote_age_seconds"] == 474


def test_broker_sperre_wird_nicht_als_boersenzeit_verkauft():
    from market_session import market_session_status, entry_allowed

    st = market_session_status(FakeBroker(tradable=False),
                               FakeInstrument("FLR.US"), now=rth(),
                               quote_max_age_seconds=180.0)
    erlaubt, grund = entry_allowed(st)
    assert not erlaubt
    assert st.downgrade_grund == "broker_closed"
    assert "nicht handelbar" in grund
    assert "geschlossen" not in grund.lower()


def test_frischer_kurs_waehrend_rth_bleibt_erlaubt():
    from market_session import market_session_status, entry_allowed

    st = market_session_status(FakeBroker(quote_age=30),
                               FakeInstrument("AAPL"), now=rth(),
                               quote_max_age_seconds=180.0)
    erlaubt, grund = entry_allowed(st)
    assert erlaubt
    assert st.downgrade_grund == ""
    assert st.session == "REGULAR"


def test_echte_boersenschliessung_nennt_die_naechste_oeffnung():
    """Wenn wirklich zu ist, soll die Meldung sagen, wann wieder auf ist."""
    from market_session import market_session_status, entry_allowed

    nachts = datetime(2026, 8, 25, 2, 0, tzinfo=timezone.utc)   # 22:00 NY Vortag
    st = market_session_status(FakeBroker(quote_age=30),
                               FakeInstrument("AAPL"), now=nachts,
                               quote_max_age_seconds=180.0)
    erlaubt, grund = entry_allowed(st)
    assert not erlaubt
    assert st.downgrade_grund == ""            # kein Datenproblem, echt zu
    assert "geschlossen" in grund.lower() or "Vor-/Nachboerse" in grund
    assert "wieder ab" in grund
    assert st.naechste_oeffnung_lokal


def test_wochenende_verweist_auf_den_naechsten_handelstag():
    from market_session import market_session_status

    samstag = datetime(2026, 8, 29, 17, 0, tzinfo=timezone.utc)
    st = market_session_status(FakeBroker(), FakeInstrument("AAPL"),
                               now=samstag)
    assert st.session == "CLOSED"
    assert st.naechste_oeffnung_lokal            # nicht leer
    assert "Uhr" in st.naechste_oeffnung_lokal


def test_unbekanntes_kursalter_sperrt_nicht_zusaetzlich():
    """Kein Alter zu kennen ist kein Beleg fuer einen alten Kurs.

    Die uebrigen Marktqualitaetspruefungen greifen ohnehin.
    """
    from market_session import market_session_status, entry_allowed

    st = market_session_status(FakeBroker(quote_age=None),
                               FakeInstrument("AAPL"), now=rth())
    assert st.quote_fresh is None
    assert entry_allowed(st)[0] is True


@pytest.mark.parametrize("sekunden,text", [
    (0, "0 s"), (45, "45 s"), (474, "8 min"), (790, "13 min"),
    (18741, "5 h 12 min"), (7200, "2 h"), (None, "unbekannt"),
    (float("nan"), "unbekannt"), (-5, "unbekannt"),
])
def test_altersklartext(sekunden, text):
    from market_session import altersklartext
    assert altersklartext(sekunden) == text


def test_herabstufung_wird_einmal_je_symbol_und_tag_gemeldet():
    """Eine Nachricht haette am 25.08. die ganze Suche erspart -- aber
    nicht 243 davon."""
    import market_session as ms

    ms._gemeldete_herabstufungen.clear()
    gemeldet = []
    st = ms.market_session_status(FakeBroker(quote_age=18741),
                                  FakeInstrument("GOOGL"), now=rth(),
                                  quote_max_age_seconds=180.0)

    assert ms.melde_herabstufung(st, lambda b, t: gemeldet.append((b, t))) is True
    for _ in range(20):
        ms.melde_herabstufung(st, lambda b, t: gemeldet.append((b, t)))
    assert len(gemeldet) == 1
    betreff, text = gemeldet[0]
    assert "MARKT OFFEN" in betreff
    assert "GOOGL" in text and "5 h 12 min" in text
    assert "Stops" in text and "weiter" in text     # beruhigt richtig

    # Anderes Symbol -> eigene Meldung.
    st2 = ms.market_session_status(FakeBroker(quote_age=999),
                                   FakeInstrument("COST"), now=rth(),
                                   quote_max_age_seconds=180.0)
    ms.melde_herabstufung(st2, lambda b, t: gemeldet.append((b, t)))
    assert len(gemeldet) == 2

    # Naechster Tag -> wieder melden.
    st3 = ms.market_session_status(FakeBroker(quote_age=18741),
                                   FakeInstrument("GOOGL"), now=rth(tag=26),
                                   quote_max_age_seconds=180.0)
    ms.melde_herabstufung(st3, lambda b, t: gemeldet.append((b, t)))
    assert len(gemeldet) == 3


def test_echte_boersenschliessung_loest_keine_meldung_aus():
    """Feierabend ist keine Stoerung."""
    import market_session as ms

    ms._gemeldete_herabstufungen.clear()
    gemeldet = []
    nachts = datetime(2026, 8, 25, 2, 0, tzinfo=timezone.utc)
    st = ms.market_session_status(FakeBroker(), FakeInstrument("AAPL"), now=nachts)
    assert ms.melde_herabstufung(st, lambda b, t: gemeldet.append(t)) is False
    assert gemeldet == []


def test_unzustellbare_meldung_wiederholt_sich_nicht():
    """Ein kaputter Telegram-Kanal darf keinen Dauerlauf ausloesen."""
    import market_session as ms

    ms._gemeldete_herabstufungen.clear()
    versuche = []

    def kaputt(betreff, text):
        versuche.append(betreff)
        raise RuntimeError("Telegram nicht erreichbar")

    st = ms.market_session_status(FakeBroker(quote_age=9999),
                                  FakeInstrument("NVDA"), now=rth(),
                                  quote_max_age_seconds=180.0)
    ms.melde_herabstufung(st, kaputt)
    ms.melde_herabstufung(st, kaputt)
    assert len(versuche) == 1


# --- Die Schwelle ist einstellbar und wirkt ---------------------------------
def test_kursaltergrenze_ist_einstellbar_und_wirkt_ohne_neustart(tmp_path, monkeypatch):
    import live_settings

    monkeypatch.setattr(live_settings, "_WURZEL", tmp_path, raising=False)
    monkeypatch.setattr(live_settings, "ROOT", tmp_path, raising=False)
    (tmp_path / "handel_settings.json").write_text(
        json.dumps({"market_session_quote_max_age_seconds": 900}), encoding="utf-8")
    live_settings.verwerfe_cache()
    try:
        assert live_settings.kursalter_grenze() == 900.0
    finally:
        live_settings.verwerfe_cache()


@pytest.mark.parametrize("wert,ok", [
    (30, True), (180, True), (86400, True),
    (5, False), (0, False), (100000, False), ("viel", False),
])
def test_kursaltergrenze_wird_geklammert(wert, ok, tmp_path, monkeypatch):
    """Zu klein sperrt alles, zu gross handelt auf alten Kursen.

    Der Test schreibt bewusst in ein Wegwerfverzeichnis: ``settings_store``
    legt sonst Einstellungs- und Zugangsdateien im Release-Baum an, und die
    Release-Hygiene wuerde sie zu Recht als dort nicht hingehoerend melden.
    """
    from webui import settings_store

    monkeypatch.setattr(settings_store, "ROOT", tmp_path)
    monkeypatch.setattr(settings_store, "_WURZEL", tmp_path, raising=False)
    import credential_store
    monkeypatch.setattr(credential_store, "ROOT", tmp_path, raising=False)

    nutzlast = {"handel": {"market_session_quote_max_age_seconds": wert}}
    if ok:
        ergebnis = settings_store.save(nutzlast)
        assert ergebnis["handel"]["market_session_quote_max_age_seconds"] == float(wert)
        assert (tmp_path / "handel_settings.json").exists()
    else:
        with pytest.raises(ValueError):
            settings_store.save(nutzlast)


# ===========================================================================
# 4. Universumsaufnahme ohne Bestaetigung -- aber mit vier Stunden Bewaehrung
# ===========================================================================
# Georg am 25.08.2026: "bitte aender auch das die freigabe fuer die aufnahme
# ins Universum keine Bestaetigung mehr von mir braucht. Weiterhin die
# abfrage wie bisher nur ohne Bestaetigung von mir. es ist doch eh dynamisch
# und fliegt wieder raus wenn es eine schlechte Wahl war eine maximal
# Begrenzung gibt es ja auch hoffentlich" -- und danach: "mach nicht 24h
# sondern 4h weil es ja im Universum wegen guten werten gelandet ist".
from datetime import timedelta as _td


def stock_manager(tmp_path, **abweichungen):
    import config
    from universe.manager import UniverseManager
    from universe.modelle import UniverseZustand

    class Cfg:
        pass

    for name in dir(config):
        if name.isupper():
            setattr(Cfg, name, getattr(config, name))
    Cfg.STOCK_CORE_SYMBOLS = ()          # kein fester Kern: nur die Dynamik testen
    Cfg.STOCK_CORE_LIMIT = 0
    for name, wert in abweichungen.items():
        setattr(Cfg, name, wert)
    return UniverseManager(UniverseZustand(tmp_path / "u.json"), cfg=Cfg())


def aktien_eintrag(symbol, rang=1, score=0.9):
    from universe.modelle import TIER_KANDIDAT, UniverseKandidat, UniverseScore
    return {"kandidat": UniverseKandidat(symbol, "etoro", asset_type="stock",
                                         inst_id=symbol),
            "score": UniverseScore(gesamt=score), "rang": rang,
            "tier": TIER_KANDIDAT, "tier_begruendung": "hoher Umsatz",
            "sicherheitsabgang": ""}


def test_vier_stunden_bewaehrung_gilt_wirklich(tmp_path):
    """Vor Ablauf der vier Stunden wird nicht freigegeben."""
    from universe.modelle import AKTIV, BEOBACHTUNG

    manager = stock_manager(tmp_path)
    manager.lauf({"broker": "etoro", "rangliste": [aktien_eintrag("ZZTOP")]})
    mitglied = manager.zustand.hole("etoro", "ZZTOP")
    assert mitglied.zustand == BEOBACHTUNG

    # Drei weitere Laeufe -- genug Messungen, aber noch keine vier Stunden.
    for _ in range(3):
        manager.lauf({"broker": "etoro", "rangliste": [aktien_eintrag("ZZTOP")]})
    assert manager.zustand.hole("etoro", "ZZTOP").zustand == BEOBACHTUNG, \
        "Ohne abgelaufene Bewaehrung darf nichts handelbar werden"

    # Jetzt die Uhr um gut vier Stunden zurueckdrehen.
    mitglied = manager.zustand.hole("etoro", "ZZTOP")
    mitglied.zustand_seit = (datetime.now(timezone.utc) - _td(hours=4, minutes=5)).isoformat()
    manager.zustand.setze(mitglied)

    diff = manager.lauf({"broker": "etoro", "rangliste": [aktien_eintrag("ZZTOP")]})
    assert "ZZTOP" in diff.freigegeben
    assert manager.zustand.hole("etoro", "ZZTOP").zustand == AKTIV


def test_drei_stunden_fuenfzig_reichen_nicht(tmp_path):
    """Die Grenze ist eine Grenze, kein Richtwert."""
    from universe.modelle import BEOBACHTUNG

    manager = stock_manager(tmp_path)
    for _ in range(4):
        manager.lauf({"broker": "etoro", "rangliste": [aktien_eintrag("ZZTOP")]})
    mitglied = manager.zustand.hole("etoro", "ZZTOP")
    mitglied.zustand_seit = (datetime.now(timezone.utc) - _td(hours=3, minutes=50)).isoformat()
    manager.zustand.setze(mitglied)

    manager.lauf({"broker": "etoro", "rangliste": [aktien_eintrag("ZZTOP")]})
    assert manager.zustand.hole("etoro", "ZZTOP").zustand == BEOBACHTUNG


def test_eingebrochene_bewertung_besteht_die_bewaehrung_nicht(tmp_path):
    """Vier Stunden allein genuegen nicht -- die Bewertung muss halten."""
    from universe.modelle import BEOBACHTUNG

    manager = stock_manager(tmp_path)
    for _ in range(4):
        manager.lauf({"broker": "etoro", "rangliste": [aktien_eintrag("ZZTOP", score=0.9)]})
    mitglied = manager.zustand.hole("etoro", "ZZTOP")
    mitglied.zustand_seit = (datetime.now(timezone.utc) - _td(hours=5)).isoformat()
    manager.zustand.setze(mitglied)

    # Score bricht auf ein Drittel ein -> Bewaehrung wird verlaengert.
    manager.lauf({"broker": "etoro", "rangliste": [aktien_eintrag("ZZTOP", score=0.3)]})
    assert manager.zustand.hole("etoro", "ZZTOP").zustand == BEOBACHTUNG


def test_hoechstens_fuenf_wechsel_je_lauf(tmp_path):
    """Ohne Rueckfrage heisst nicht: beliebig viele auf einmal."""
    manager = stock_manager(tmp_path)
    rangliste = [aktien_eintrag(f"SYM{i}", rang=i + 1) for i in range(20)]

    diff = manager.lauf({"broker": "etoro", "rangliste": rangliste})

    neu = len(diff.aufgenommen) + len(diff.beobachtung_gestartet)
    assert neu <= 5, f"{neu} Aufnahmen in einem Lauf -- erlaubt sind 5"


def test_der_dynamische_deckel_haelt(tmp_path):
    """Hoechstens 25 dynamische Plaetze, egal wie viele Kandidaten kommen."""
    import config
    from universe.modelle import ABGANG

    manager = stock_manager(tmp_path, STOCK_UNIVERSE_MAX_CHANGES_PER_RUN=0)
    rangliste = [aktien_eintrag(f"SYM{i}", rang=i + 1) for i in range(60)]
    manager.lauf({"broker": "etoro", "rangliste": rangliste})

    mitglieder = [m for m in manager.zustand.fuer_broker("etoro")
                  if m.zustand != ABGANG]
    assert len(mitglieder) <= config.STOCK_UNIVERSE_DYNAMIC_LIMIT, (
        f"{len(mitglieder)} dynamische Werte -- erlaubt sind "
        f"{config.STOCK_UNIVERSE_DYNAMIC_LIMIT}")


def test_ki_kann_die_aufnahme_weder_freigeben_noch_blockieren(tmp_path):
    """Georgs Grenze: GPT darf das Instrumentenuniversum nie freigeben.

    Die autonome Aufnahme aendert daran nichts -- entschieden wird nach Rang,
    Bewaehrungszeit und Bewertungsstabilitaet. Die KI setzt hoechstens einen
    Aufmerksamkeitsvermerk.
    """
    from universe.modelle import AKTIV

    class VetoKI:
        """Eine KI, die alles ablehnt -- und trotzdem nichts verhindert."""
        def __init__(self):
            self.gefragt = 0

        def bewerte_universe_kandidat(self, daten):
            self.gefragt += 1
            return {"attention": "NIEMALS", "modell": "test", "freigabe": False,
                    "blockieren": True}

    ki = VetoKI()
    manager = stock_manager(tmp_path)
    manager.ai = ki
    for _ in range(4):
        manager.lauf({"broker": "etoro", "rangliste": [aktien_eintrag("ZZTOP")]})
    mitglied = manager.zustand.hole("etoro", "ZZTOP")
    mitglied.zustand_seit = (datetime.now(timezone.utc) - _td(hours=5)).isoformat()
    manager.zustand.setze(mitglied)

    diff = manager.lauf({"broker": "etoro", "rangliste": [aktien_eintrag("ZZTOP")]})

    assert "ZZTOP" in diff.freigegeben, "Das KI-Veto darf nichts blockieren"
    assert manager.zustand.hole("etoro", "ZZTOP").zustand == AKTIV
    assert manager.zustand.hole("etoro", "ZZTOP").ai_bewertung == "NIEMALS"


def test_ki_ausfall_haelt_die_aufnahme_nicht_auf(tmp_path):
    from universe.modelle import AKTIV

    class KaputteKI:
        def bewerte_universe_kandidat(self, daten):
            raise RuntimeError("OpenAI nicht erreichbar")

    manager = stock_manager(tmp_path)
    manager.ai = KaputteKI()
    for _ in range(4):
        manager.lauf({"broker": "etoro", "rangliste": [aktien_eintrag("ZZTOP")]})
    mitglied = manager.zustand.hole("etoro", "ZZTOP")
    mitglied.zustand_seit = (datetime.now(timezone.utc) - _td(hours=5)).isoformat()
    manager.zustand.setze(mitglied)

    manager.lauf({"broker": "etoro", "rangliste": [aktien_eintrag("ZZTOP")]})
    assert manager.zustand.hole("etoro", "ZZTOP").zustand == AKTIV
