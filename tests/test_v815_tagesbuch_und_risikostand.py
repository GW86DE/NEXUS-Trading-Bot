"""v8.1.5 -- die Verlustbetraege und die zwei getrennten Risikostaende.

DER GEMELDETE FALL (25.08.2026)
==============================
Telegram: "Heute realisiert: -226,60 USD". Tatsaechlich:

    DVLT    geschlossen   -44,40 USD   (so auch im Handelsbuch)
    FLR.US  noch offen   -121,26 USD   Buchverlust
    Gebuehren              -3,00 USD

-226,60 ist keine Kombination dieser Zahlen. Der Wert kam aus dem
Summenzaehler ``risk_state.realized_pnl_today`` -- einem Wert ohne Belege.

DER ZWEITE, SCHWERERE BEFUND
============================
``live_trader`` schrieb risk_state.json, ``RiskPot('etoro')`` las
risk_state_etoro.json. Die Topfdatei blieb dauerhaft leer. Damit konnte die
Tagesverlustbremse des eToro-Topfes NIE ausloesen, und die
brokeruebergreifende Klammer sah die Aktienseite mit 0,00.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("TRADINGBOT_TEST_STATE_DIR", "/tmp/tradingbot_tests")
Path(os.environ["TRADINGBOT_TEST_STATE_DIR"]).mkdir(parents=True, exist_ok=True)

WURZEL = Path(__file__).resolve().parent.parent
TEST_JETZT = datetime(2026, 9, 10, 22, 30, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def feste_testuhr(monkeypatch):
    """Gleicher Zeitpunkt fuer Testbuchung und Auswertung, auch bei Tageswechsel.

    Der reproduzierte Installationsabbruch war um 00:30 Uhr Europe/Berlin.
    Nur dieses Testmodul verwendet die feste Uhr; kein System-/Brokerzeitwechsel.
    """
    import config
    import tagesbuch

    class TestDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return (TEST_JETZT.astimezone(tz) if tz is not None
                    else TEST_JETZT.replace(tzinfo=None))

    monkeypatch.setattr(tagesbuch, "datetime", TestDatetime)
    monkeypatch.setattr(config, "LOCAL_TIMEZONE", "Europe/Berlin")


# ===========================================================================
# Das Tagesbuch
# ===========================================================================
@pytest.fixture
def ledger(tmp_path, monkeypatch):
    """Ein leeres Handelsbuch in einem Wegwerfverzeichnis."""
    monkeypatch.setenv("TRADINGBOT_TEST_STATE_DIR", str(tmp_path))
    import decision_analytics
    monkeypatch.setattr(decision_analytics, "DB_PATH",
                        tmp_path / "decision_history.sqlite", raising=False)
    import trade_ledger
    trade_ledger.init_ledger()
    return trade_ledger


def buche(ledger, symbol, netto, *, brutto=None, gebuehren=0.0, stunden_her=1.0,
          broker="etoro", grund="STOP", jetzt=None):
    """Einen geschlossenen Trade von heute ins Handelsbuch schreiben."""
    from decision_analytics import _LOCK, _connect
    import config
    jetzt = jetzt or TEST_JETZT
    zone = ZoneInfo(config.LOCAL_TIMEZONE)
    tagesbeginn = jetzt.astimezone(zone).replace(
        hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
    aus = jetzt - timedelta(hours=stunden_her)
    # "Heute" meint den konfigurierten lokalen Handelstag. Die alte
    # Begrenzung an UTC-Mitternacht legte um 00:30 Uhr Berlin "vor einer
    # Stunde" auf gestern. Die Produktlogik schloss diese Zeile korrekt aus.
    # Echte Gestern-Faelle (>= 24 h) bleiben unangetastet.
    if 0 <= float(stunden_her) < 24 and aus < tagesbeginn:
        aus = tagesbeginn
    ein = (aus - timedelta(hours=2)).isoformat()
    aus = aus.isoformat()
    with _LOCK, _connect() as con:
        con.execute(
            "INSERT INTO trades (broker, symbol, eingestiegen_am, ausgestiegen_am,"
            " brutto_pnl, gebuehren, netto_pnl, exit_grund, paper) "
            "VALUES (?,?,?,?,?,?,?,?,0)",
            (broker, symbol, ein, aus,
             brutto if brutto is not None else (None if netto is None else netto + gebuehren),
             gebuehren, netto, grund))


class OffenePosition:
    def __init__(self, symbol, unrealized_pnl=None):
        self.symbol = symbol
        self.unrealized_pnl = unrealized_pnl


def test_der_fall_vom_25_08_wird_richtig_aufgeschluesselt(ledger):
    """Ein geschlossener Trade, eine offene Position, Gebuehren getrennt."""
    import tagesbuch

    buche(ledger, "DVLT", netto=-44.40, gebuehren=3.00, grund="MANUAL_SELL")
    ergebnis = tagesbuch.tagesergebnis(
        broker="etoro", waehrung="USD",
        offene_positionen=[OffenePosition("FLR.US", -121.26)],
        zaehlerwert=-226.60)

    assert ergebnis.realisiert_netto == pytest.approx(-44.40)
    assert ergebnis.gebuehren == pytest.approx(3.00)
    assert ergebnis.offen_unrealisiert == pytest.approx(-121.26)
    assert ergebnis.gesamt == pytest.approx(-165.66)
    assert ergebnis.geschlossene_trades == 1
    assert ergebnis.offene_positionen == 1

    # Der falsche Zaehlerwert wird gemeldet, nicht uebernommen.
    assert ergebnis.stimmig is False
    assert ergebnis.abweichung == pytest.approx(-182.20)
    assert any("-226,60" in h.replace(".", ",") or "-226.60" in h
               for h in ergebnis.hinweise)

    text = ergebnis.text()
    assert "Realisiert heute: -44,40 USD" in text.replace(".", ",").replace(",00 USD", ",00 USD")


def test_gebuehren_werden_nicht_doppelt_abgezogen(ledger):
    """Sie stecken im Nettowert -- ein zweiter Abzug waere der naechste
    falsche Betrag."""
    import tagesbuch

    buche(ledger, "AAPL", netto=100.0, brutto=103.0, gebuehren=3.0)
    e = tagesbuch.tagesergebnis(broker="etoro", offene_positionen=[])

    assert e.realisiert_netto == pytest.approx(100.0)
    assert e.realisiert_brutto == pytest.approx(103.0)
    assert e.gebuehren == pytest.approx(3.0)
    assert e.gesamt == pytest.approx(100.0)     # nicht 97.0
    assert "bereits abgezogen" in e.text()


def test_trade_ohne_einstand_wird_nicht_als_null_verbucht(ledger):
    """Eine Null sieht aus wie ein Nullergebnis und verfaelscht die Summe."""
    import tagesbuch

    buche(ledger, "AAPL", netto=50.0)
    buche(ledger, "ALTBESTAND", netto=None, gebuehren=1.0)
    e = tagesbuch.tagesergebnis(broker="etoro", offene_positionen=[])

    assert e.realisiert_netto == pytest.approx(50.0)
    assert e.geschlossene_trades == 2
    assert e.unvollstaendige_trades == 1
    assert e.vollstaendig is False
    assert any("unvollstaendig" in h for h in e.hinweise)


def test_trades_von_gestern_zaehlen_nicht_zu_heute(ledger):
    import tagesbuch

    buche(ledger, "HEUTE", netto=10.0, stunden_her=1)
    buche(ledger, "GESTERN", netto=-999.0, stunden_her=30)
    e = tagesbuch.tagesergebnis(broker="etoro", offene_positionen=[])

    assert e.geschlossene_trades == 1
    assert e.realisiert_netto == pytest.approx(10.0)


def test_testbuchung_bleibt_kurz_nach_utc_mitternacht_heute(ledger):
    """Regression fuer den 9.0.9-Installationslauf um 00:05 UTC."""
    import tagesbuch

    kurz_nach_mitternacht = datetime(2026, 8, 29, 0, 5, tzinfo=timezone.utc)
    buche(ledger, "MITTERNACHT", netto=12.34, stunden_her=1,
          jetzt=kurz_nach_mitternacht)
    e = tagesbuch.tagesergebnis(
        broker="etoro", offene_positionen=[], jetzt=kurz_nach_mitternacht)

    assert e.geschlossene_trades == 1
    assert e.realisiert_netto == pytest.approx(12.34)


@pytest.mark.parametrize("zone,zeit,start", [
    ("Europe/Berlin", "2026-09-10T22:30:00+00:00", "2026-09-10T22:00:00+00:00"),
    ("Europe/Berlin", "2026-09-10T21:59:59+00:00", "2026-09-09T22:00:00+00:00"),
    ("Europe/Berlin", "2026-09-11T00:05:00+00:00", "2026-09-10T22:00:00+00:00"),
    ("Europe/Berlin", "2026-01-10T23:30:00+00:00", "2026-01-10T23:00:00+00:00"),
    ("Europe/Berlin", "2026-03-29T01:30:00+00:00", "2026-03-28T23:00:00+00:00"),
    ("Europe/Berlin", "2026-10-25T01:30:00+00:00", "2026-10-24T22:00:00+00:00"),
    ("UTC", "2026-09-11T00:30:00+00:00", "2026-09-11T00:00:00+00:00"),
    ("America/New_York", "2026-09-11T04:30:00+00:00", "2026-09-11T04:00:00+00:00"),
])
def test_lokaler_tag_zaehlt_heute_aber_keine_buchung_vor_mitternacht(
        ledger, monkeypatch, zone, zeit, start):
    """Feste UTC-Grenzen pruefen die echte Auswertung, inkl. Sommer-/Winterzeit."""
    import config
    import tagesbuch
    from decision_analytics import _LOCK, _connect

    monkeypatch.setattr(config, "LOCAL_TIMEZONE", zone)
    jetzt = datetime.fromisoformat(zeit)
    beginn = datetime.fromisoformat(start)
    assert tagesbuch._tagesbeginn_utc(jetzt) == beginn
    buche(ledger, "HEUTE", netto=12.34, gebuehren=0.25, jetzt=jetzt)
    # Die negative Kontrollbuchung wird direkt mit einem festen Zeitpunkt
    # eingetragen: der Testhelfer darf einen echten gestrigen Verlust nicht
    # in den heutigen Tag verschieben.
    with _LOCK, _connect() as con:
        con.execute("INSERT INTO trades (broker,symbol,eingestiegen_am,ausgestiegen_am,brutto_pnl,"
                    "gebuehren,netto_pnl,paper) VALUES (?,?,?,?,?,?,?,0)",
                    ("etoro", "GESTERN", (beginn-timedelta(hours=2)).isoformat(),
                     (beginn-timedelta(microseconds=1)).isoformat(),
                     -999.0, 0.0, -999.0))
        heutiger_zeitpunkt = datetime.fromisoformat(con.execute(
            "SELECT ausgestiegen_am FROM trades WHERE symbol='HEUTE'").fetchone()[0])
    assert beginn <= heutiger_zeitpunkt <= jetzt
    ergebnis = tagesbuch.tagesergebnis(broker="etoro", offene_positionen=[], jetzt=jetzt)
    assert ergebnis.geschlossene_trades == 1
    assert [b["symbol"] for b in ergebnis.buchungen] == ["HEUTE"]
    assert ergebnis.realisiert_netto == pytest.approx(12.34)
    assert ergebnis.realisiert_brutto == pytest.approx(12.59)
    assert ergebnis.gebuehren == pytest.approx(0.25)


def test_der_andere_broker_bleibt_draussen(ledger):
    import tagesbuch

    buche(ledger, "AAPL", netto=10.0, broker="etoro")
    buche(ledger, "BTC-EUR", netto=-500.0, broker="okx")

    assert tagesbuch.tagesergebnis(broker="etoro", offene_positionen=[]) \
        .realisiert_netto == pytest.approx(10.0)
    assert tagesbuch.tagesergebnis(broker="okx", offene_positionen=[]) \
        .realisiert_netto == pytest.approx(-500.0)
    # Ohne Broker: beide zusammen.
    assert tagesbuch.tagesergebnis(offene_positionen=[]) \
        .realisiert_netto == pytest.approx(-490.0)


def test_stimmiger_zaehler_erzeugt_keinen_hinweis(ledger):
    import tagesbuch

    buche(ledger, "AAPL", netto=-44.40)
    e = tagesbuch.tagesergebnis(broker="etoro", offene_positionen=[],
                                zaehlerwert=-44.40)
    assert e.stimmig is True
    assert e.hinweise == []


def test_unbekannte_offene_positionen_werden_benannt_nicht_geraten(ledger):
    import tagesbuch

    buche(ledger, "AAPL", netto=10.0)
    e = tagesbuch.tagesergebnis(broker="etoro", offene_positionen=None)
    assert e.offen_unrealisiert == 0.0
    assert any("nicht abrufbar" in h for h in e.hinweise)

    # Position ohne Kurs: gezaehlt, aber nicht mit 0 verrechnet.
    e2 = tagesbuch.tagesergebnis(
        broker="etoro", offene_positionen=[OffenePosition("FLR.US", None)])
    assert e2.offene_positionen == 1
    assert e2.offen_unrealisiert == 0.0
    assert any("ohne aktuellen Kurs" in h for h in e2.hinweise)


def test_kaputtes_handelsbuch_erfindet_keine_zahl(monkeypatch):
    import tagesbuch

    def kaputt(*a, **k):
        raise RuntimeError("Datenbank gesperrt")

    monkeypatch.setattr(tagesbuch, "_geschlossene_trades_heute", kaputt)
    e = tagesbuch.tagesergebnis(broker="etoro", offene_positionen=[])
    assert e.realisiert_netto == 0.0
    assert e.geschlossene_trades == 0
    assert any("nicht lesbar" in h for h in e.hinweise)


def test_die_drei_zahlen_stehen_getrennt_in_der_meldung(ledger):
    """Georgs Vorgabe: Realisiert, Offen, Gebuehren, Gesamt -- einzeln."""
    import tagesbuch

    buche(ledger, "DVLT", netto=-44.40, gebuehren=3.0)
    text = tagesbuch.tagesergebnis(
        broker="etoro", offene_positionen=[OffenePosition("FLR.US", -121.26)],
        zaehlerwert=-44.40).text()

    assert "Realisiert heute:" in text
    assert "davon Gebuehren:" in text
    assert "Offen unrealisiert:" in text
    assert "Tagesergebnis gesamt:" in text
    assert "Buchungen:" in text and "DVLT" in text


# ===========================================================================
# Die zwei getrennten Risikostaende
# ===========================================================================
def test_aktienkern_und_risikotopf_lesen_dieselbe_datei(tmp_path, monkeypatch):
    """Der eigentliche Befund vom 25.08.

    Vorher: live_trader -> risk_state.json, RiskPot -> risk_state_etoro.json.
    Die Topfdatei blieb leer, ihre Tagesverlustbremse konnte nie ausloesen.
    """
    monkeypatch.setenv("TRADINGBOT_TEST_STATE_DIR", str(tmp_path))
    import config
    import risk_state_pfad
    from risk_manager import RiskState
    from risk_pots import RiskPot

    kern = RiskState.default_path(getattr(config, "RISK_STATE_FILE"))
    topf = RiskPot("etoro").state_datei
    assert Path(kern).name == Path(topf).name == "risk_state_etoro.json"
    assert Path(kern).resolve() == Path(topf).resolve()
    assert Path(topf).resolve() == risk_state_pfad.zustandsdatei("etoro").resolve()


def test_okx_behaelt_seinen_eigenen_topf(tmp_path, monkeypatch):
    """Zusammenfuehren heisst nicht vermischen -- die Broker bleiben getrennt."""
    monkeypatch.setenv("TRADINGBOT_TEST_STATE_DIR", str(tmp_path))
    from risk_pots import RiskPot

    assert RiskPot("okx").state_datei.name == "risk_state_okx.json"
    assert RiskPot("okx").state_datei != RiskPot("etoro").state_datei


def zustand_datei(pfad, **felder):
    from risk_manager import _handelstag_heute
    grund = {
        # Der RiskPot setzt alte Tageswerte mit Recht zurueck. Ein fester
        # Kalendertag machte diesen Regressionstest nach Mitternacht falsch.
        #
        # KORREKTUR 9.5.5: Hier stand das UTC-Datum. Der Handelstag ist aber
        # bewusst Europe/Berlin (risk_manager._handelstag_heute) -- sonst
        # wechselten Tageslimits in Deutschland um 02:00 Uhr. Zwischen 22:00
        # UTC und Mitternacht UTC ist in Berlin bereits der Folgetag; der
        # geschriebene Zustand galt dem Topf dann als veraltet und wurde
        # korrekt auf null zurueckgesetzt -- der Test fiel jede Nacht zwei
        # Stunden lang um, ohne dass sich am Bot etwas geaendert hatte.
        "current_date": _handelstag_heute().isoformat(), "realized_pnl_today": 0.0,
        "open_positions": 0, "trading_halted": False, "trades_today": 0,
        "estimated_costs_today": 0.0, "lifetime_realized_pnl": 0.0,
    }
    grund.update(felder)
    Path(pfad).write_text(json.dumps(grund), encoding="utf-8")


def test_umzug_uebernimmt_die_gefuehrten_werte(tmp_path, monkeypatch):
    """Georgs Installation: alte Datei gefuellt, neue leer."""
    monkeypatch.setenv("TRADINGBOT_TEST_STATE_DIR", str(tmp_path))
    import risk_state_pfad

    zustand_datei(tmp_path / "risk_state.json",
                  realized_pnl_today=-226.60, trades_today=2, open_positions=1)
    zustand_datei(tmp_path / "risk_state_etoro.json")     # alles Nullen

    gemeldet = []
    text = risk_state_pfad.umzug_etoro(melder=lambda b, t: gemeldet.append(t))

    assert text and "zusammengefuehrt" in text
    assert gemeldet, "Ein Zustandsumzug muss gemeldet werden"
    daten = json.loads((tmp_path / "risk_state_etoro.json").read_text())
    assert daten["realized_pnl_today"] == pytest.approx(-226.60)
    assert daten["trades_today"] == 2
    assert not (tmp_path / "risk_state.json").exists()
    # Die verdraengte Datei ist aufbewahrt, nicht geloescht.
    assert list(tmp_path.glob("risk_state_etoro.json.abgeloest-*"))


def test_umzug_ohne_zieldatei_ist_ein_einfaches_umbenennen(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADINGBOT_TEST_STATE_DIR", str(tmp_path))
    import risk_state_pfad

    zustand_datei(tmp_path / "risk_state.json", realized_pnl_today=-50.0,
                  trades_today=1)
    text = risk_state_pfad.umzug_etoro()

    assert "umgezogen" in text
    assert json.loads((tmp_path / "risk_state_etoro.json").read_text())[
        "realized_pnl_today"] == pytest.approx(-50.0)
    assert not (tmp_path / "risk_state.json").exists()


def test_umzug_verwirft_niemals_einen_gefuehrten_tag(tmp_path, monkeypatch):
    """Beide gefuellt: nichts wird geloescht, und es wird gewarnt."""
    monkeypatch.setenv("TRADINGBOT_TEST_STATE_DIR", str(tmp_path))
    import risk_state_pfad

    from risk_manager import _handelstag_heute
    heute = _handelstag_heute()   # 9.5.5: Handelstag, nicht UTC-Datum
    zustand_datei(tmp_path / "risk_state.json", current_date=heute.isoformat(),
                  realized_pnl_today=-100.0, trades_today=3)
    zustand_datei(tmp_path / "risk_state_etoro.json", current_date=(heute - timedelta(days=1)).isoformat(),
                  realized_pnl_today=-7.0, trades_today=1)

    text = risk_state_pfad.umzug_etoro()

    assert "gegenpruefen" in text          # Georg muss das nachsehen
    daten = json.loads((tmp_path / "risk_state_etoro.json").read_text())
    assert daten["realized_pnl_today"] == pytest.approx(-100.0)   # der juengere Tag
    assert list(tmp_path.glob("risk_state_etoro.json.abgeloest-*"))


def test_umzug_ist_mehrfach_aufrufbar(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADINGBOT_TEST_STATE_DIR", str(tmp_path))
    import risk_state_pfad

    zustand_datei(tmp_path / "risk_state.json", realized_pnl_today=-50.0,
                  trades_today=1)
    risk_state_pfad.umzug_etoro()
    vorher = (tmp_path / "risk_state_etoro.json").read_text()

    assert risk_state_pfad.umzug_etoro() == ""
    assert risk_state_pfad.umzug_etoro() == ""
    assert (tmp_path / "risk_state_etoro.json").read_text() == vorher


def test_tagesverlustbremse_des_etoro_topfes_loest_jetzt_aus(tmp_path, monkeypatch):
    """Der Sicherheitsgewinn, um den es geht.

    Vorher las der Topf eine Datei mit Nullen -- die Bremse verglich immer
    0,00 gegen die Grenze und konnte nie greifen.
    """
    monkeypatch.setenv("TRADINGBOT_TEST_STATE_DIR", str(tmp_path))
    from risk_pots import RiskPot

    zustand_datei(tmp_path / "risk_state_etoro.json", realized_pnl_today=-226.60,
                  trades_today=2)
    topf = RiskPot("etoro")
    topf.setze_kontowert(5000.0)          # Grenze bei 2 % = 100,00

    assert topf.state.realized_pnl_today == pytest.approx(-226.60)
    assert topf.tagesverlust_erreicht() is True
    darf, grund = topf.darf_kaufen()
    assert darf is False
    assert "Tagesverlustgrenze" in grund


def test_globale_klammer_sieht_die_aktienseite(tmp_path, monkeypatch):
    """Vorher steuerte eToro dauerhaft 0,00 zur Gesamtsumme bei."""
    monkeypatch.setenv("TRADINGBOT_TEST_STATE_DIR", str(tmp_path))
    import config
    from risk_pots import RiskPotManager

    zustand_datei(tmp_path / "risk_state_etoro.json", realized_pnl_today=-400.0,
                  trades_today=2)
    zustand_datei(tmp_path / "risk_state_okx.json", realized_pnl_today=-50.0,
                  trades_today=1)

    manager = RiskPotManager(["etoro", "okx"])
    manager.toepfe["etoro"].setze_kontowert(8000.0)
    manager.toepfe["okx"].setze_kontowert(2000.0)

    assert manager.uebersicht()["gesamt_tages_pnl"] == pytest.approx(-450.0)

    alt = getattr(config, "GLOBAL_RISK_GUARD_ENABLED", False)
    try:
        config.GLOBAL_RISK_GUARD_ENABLED = True
        manager.cfg = config
        ok, grund = manager.globale_pruefung()   # 4,5 % von 10.000
        assert ok is False
        assert "Globale Tagesverlustgrenze" in grund
    finally:
        config.GLOBAL_RISK_GUARD_ENABLED = alt


def test_kein_modul_bildet_den_dateinamen_noch_selbst():
    """Genau das Selberbauen hat die beiden Dateien auseinandergefuehrt."""
    import ast

    verdaechtig = []
    for datei in ("live_trader.py", "risk_pots.py", "gui_app.py", "config.py"):
        baum = ast.parse((WURZEL / datei).read_text(encoding="utf-8"))
        for knoten in ast.walk(baum):
            if isinstance(knoten, ast.Constant) and isinstance(knoten.value, str):
                if knoten.value == "risk_state.json":
                    verdaechtig.append(f"{datei}: {knoten.value!r}")
            # f"risk_state_{...}.json" zusammenbauen ist ebenso verboten.
            if isinstance(knoten, ast.JoinedStr):
                teile = [t.value for t in knoten.values
                         if isinstance(t, ast.Constant) and isinstance(t.value, str)]
                if any("risk_state" in t for t in teile):
                    verdaechtig.append(f"{datei}: zusammengebauter Name")
    assert not verdaechtig, (
        "Der Pfad gehoert ausschliesslich in risk_state_pfad.py: "
        + ", ".join(verdaechtig))
