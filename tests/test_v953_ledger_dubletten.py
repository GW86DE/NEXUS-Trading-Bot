"""Doppelt gebuchte Brokerpositionen (v9.5.3).

Grundlage sind die ECHTEN vier Zeilen aus ``decision_history.sqlite`` vom
02.09.2026 (``tests/fixtures/echt_dubletten_trades.json``):

    ADBE positionId 3592625451, entry_order 378375675
        trade 35  ba32...  decision_id NULL  LEGACY_UNLINKED  -344,76
        trade 36  664e...  decision_id 4153...  LINKED        -344,76
    CRM  positionId 3592539858, entry_order 378375526
        trade 34  ba32...  decision_id NULL  LEGACY_UNLINKED  +171,68
        trade 37  664e...  decision_id 1518...  LINKED        None

Ursache: Der Ledger-Suchschluessel enthaelt ``broker_account_fingerprint``.
9.5.1 wechselte das Fingerprint-Verfahren, ohne das Ledger mitzunehmen --
dieselbe Brokerposition lag danach unter zwei Fingerprints, und beide Zeilen
konnten unabhaengig geschlossen werden. Der ADBE-Verlust stand deshalb
zweimal im Tagesergebnis.
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

FIXTURES = Path(__file__).resolve().parent / "fixtures"
ALT = "ba32f97fe3482fcbc326e51a"
NEU = "664e7dfbc13e0cd60e493592"


@pytest.fixture()
def ledger(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADINGBOT_TEST_STATE_DIR", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    import importlib
    import trade_ledger
    importlib.reload(trade_ledger)
    trade_ledger.init_ledger()
    roh = json.loads((FIXTURES / "echt_dubletten_trades.json").read_text(encoding="utf-8"))
    with trade_ledger._LOCK, trade_ledger._connect() as con:
        for r in roh["trades"]:
            con.execute(
                """INSERT INTO trades
                   (trade_id, decision_id, link_status, broker, symbol, menge,
                    paper, broker_account_fingerprint, broker_position_id,
                    entry_order_id, exit_order_id, eingestiegen_am,
                    einstieg_preis, ausgestiegen_am, exit_grund, netto_pnl)
                   VALUES (?,?,?,'etoro',?,?,1,?,?,?,?,?,?,?,?,?)""",
                (r["trade_id"], r["decision_id"], r["link_status"], r["symbol"],
                 r["menge"], r["broker_account_fingerprint"],
                 r["broker_position_id"], r["entry_order_id"], r["exit_order_id"],
                 r["eingestiegen_am"],
                 293.92 if r["symbol"] == "ADBE" else 257.23,
                 r["ausgestiegen_am"], r["exit_grund"], r["netto_pnl"]))
        con.commit()
    return trade_ledger


def summe(tl) -> float:
    with tl._LOCK, tl._connect() as con:
        return float(con.execute(
            "SELECT COALESCE(SUM(netto_pnl),0) FROM trades "
            "WHERE ausgestiegen_am IS NOT NULL AND superseded_by IS NULL"
        ).fetchone()[0])


# ---------------------------------------------------------------------------
# Der Befund
# ---------------------------------------------------------------------------
def test_der_ausgangszustand_zaehlt_adbe_doppelt(ledger):
    assert round(summe(ledger), 2) == -517.84
    gruppen = ledger.dubletten_gruppen("etoro")
    assert len(gruppen) == 2
    nach_symbol = {g["symbol"]: g for g in gruppen}
    assert sorted(nach_symbol["ADBE"]["konten"]) == [NEU, ALT]
    assert nach_symbol["ADBE"]["mit_ergebnis"] == [35, 36], (
        "Beide ADBE-Zeilen tragen ein Ergebnis -- das ist die Doppelbuchung")
    assert nach_symbol["CRM"]["mit_ergebnis"] == [34]


# ---------------------------------------------------------------------------
# Die Zusammenfuehrung
# ---------------------------------------------------------------------------
def test_probelauf_aendert_nichts(ledger):
    plan = ledger.fuehre_dubletten_zusammen("etoro", probelauf=True)
    assert plan["gefunden"] == 2
    assert round(summe(ledger), 2) == -517.84, "Ein Probelauf schreibt nicht"


def test_zusammenfuehrung_entfernt_die_doppelbuchung(ledger):
    ledger.fuehre_dubletten_zusammen("etoro", probelauf=False, actor="test")
    assert round(summe(ledger), 2) == -173.08, (
        "-344,76 (ADBE, einmal) + 171,68 (CRM) -- der doppelte Verlust ist weg")


def test_die_primaerzeile_behaelt_ihre_identitaet(ledger):
    ledger.fuehre_dubletten_zusammen("etoro", probelauf=False, actor="test")
    adbe = ledger.trade_detail(36)
    assert adbe["decision_id"] == 4153277691980212224
    assert adbe["superseded_by"] is None
    assert adbe["netto_pnl"] == pytest.approx(-344.76, abs=1e-6)


def test_crm_bekommt_den_echten_brokerabschluss(ledger):
    """Die LINKED-Zeile hatte kein Ergebnis und den lokalen Buchungszeitpunkt
    vom 02.09. Beides muss aus der Altzeile kommen."""
    vorher = ledger.trade_detail(37)
    assert vorher["netto_pnl"] is None
    assert vorher["ausgestiegen_am"].startswith("2026-09-02T08:19")

    ledger.fuehre_dubletten_zusammen("etoro", probelauf=False, actor="test")
    nachher = ledger.trade_detail(37)
    assert nachher["netto_pnl"] == pytest.approx(171.68, abs=1e-6)
    assert nachher["ausgestiegen_am"].startswith("2026-09-01T14:26:32"), (
        "Der Brokerabschluss gilt, nicht der lokale Buchungszeitpunkt")
    assert nachher["exit_grund"] != "BROKER_CLOSED_RESULT_MISSING"


def test_nichts_wird_geloescht(ledger):
    ledger.fuehre_dubletten_zusammen("etoro", probelauf=False, actor="test")
    for trade_id, primaer in ((35, 36), (34, 37)):
        alt = ledger.trade_detail(trade_id)
        assert alt is not None, "Die Auditspur bleibt erhalten"
        assert alt["superseded_by"] == primaer
        assert "zusammengefuehrt in Trade" in str(alt.get("notiz") or "")


def test_ersetzte_zeilen_verschwinden_aus_allen_auswertungen(ledger):
    ledger.fuehre_dubletten_zusammen("etoro", probelauf=False, actor="test")
    ids = {int(x["trade_id"]) for x in ledger.trade_liste(broker="etoro", tage=3650)}
    assert 34 not in ids and 35 not in ids
    assert 36 in ids and 37 in ids


def test_zusammenfuehrung_ist_idempotent(ledger):
    ledger.fuehre_dubletten_zusammen("etoro", probelauf=False, actor="test")
    zweiter = ledger.fuehre_dubletten_zusammen("etoro", probelauf=False, actor="test")
    assert zweiter["gefunden"] == 0
    assert round(summe(ledger), 2) == -173.08


def test_init_ledger_ueberschreibt_die_ersetzung_nicht(ledger):
    """link_status wird von init_ledger() bei jedem Aufruf neu geschrieben --
    deshalb steht die Ersetzung in einer eigenen Spalte."""
    ledger.fuehre_dubletten_zusammen("etoro", probelauf=False, actor="test")
    for _ in range(3):
        ledger.init_ledger()
    assert ledger.trade_detail(35)["superseded_by"] == 36
    assert round(summe(ledger), 2) == -173.08


# ---------------------------------------------------------------------------
# Sicherheitsgrenzen
# ---------------------------------------------------------------------------
def test_unterschiedliche_mengen_sind_keine_dublette(ledger):
    with ledger._LOCK, ledger._connect() as con:
        con.execute("UPDATE trades SET menge=25.0 WHERE trade_id=35")
        con.commit()
    bericht = ledger.fuehre_dubletten_zusammen("etoro", probelauf=False, actor="test")
    uebersprungen = [x for x in bericht["uebersprungen"] if x["symbol"] == "ADBE"]
    assert uebersprungen, "Ein Teilverkauf darf nicht zusammengefuehrt werden"
    assert ledger.trade_detail(35)["superseded_by"] is None


def test_verschiedene_positionen_werden_nie_zusammengefuehrt(ledger):
    with ledger._LOCK, ledger._connect() as con:
        con.execute("UPDATE trades SET broker_position_id='9999999' WHERE trade_id=35")
        con.commit()
    assert ledger.fuehre_dubletten_zusammen("etoro", probelauf=True)["gefunden"] == 1
    ledger.fuehre_dubletten_zusammen("etoro", probelauf=False, actor="test")
    assert ledger.trade_detail(35)["superseded_by"] is None


# ---------------------------------------------------------------------------
# Kontoaliase -- die Ursache
# ---------------------------------------------------------------------------
def test_ohne_alias_findet_die_suche_die_altzeile_nicht(ledger):
    assert ledger.konto_identitaeten("etoro", NEU) == [NEU]


def test_mit_beleg_findet_die_suche_beide_fingerprints(ledger):
    assert ledger.register_account_alias(
        broker="etoro", alias_fingerprint=ALT, account_fingerprint=NEU,
        beleg="orderId 378375675 im aktuellen Konto bestaetigt") is True
    assert set(ledger.konto_identitaeten("etoro", NEU)) == {NEU, ALT}
    assert ledger.konto_identitaeten("etoro", NEU)[0] == NEU, (
        "Geschrieben wird immer unter dem aktuellen Fingerprint")


def test_alias_auf_sich_selbst_wird_abgelehnt(ledger):
    assert ledger.register_account_alias(
        broker="etoro", alias_fingerprint=NEU, account_fingerprint=NEU,
        beleg="unsinnig") is False


def test_alias_eines_fremden_kontos_wirkt_nicht(ledger):
    ledger.register_account_alias(
        broker="etoro", alias_fingerprint=ALT, account_fingerprint=NEU, beleg="Test")
    assert ledger.konto_identitaeten("etoro", "ein-anderes-konto") == ["ein-anderes-konto"]


def test_reconcile_findet_die_altzeile_ueber_den_alias(ledger):
    """Der eigentliche Schutz: die vorhandene Reparaturfunktion findet die
    unter dem alten Fingerprint angelegte Zeile jetzt."""
    ohne = ledger.reconcile_closed_trade_exact(
        broker="etoro", symbol="ADBE", paper=True, decision_id=4153277691980212224,
        broker_position_id="3592625451", broker_account_fingerprint=NEU,
        entry_order_id="378375675")
    assert ohne["status"] != "NOT_FOUND", "Die 664e-Zeile existiert"

    ledger.register_account_alias(
        broker="etoro", alias_fingerprint=ALT, account_fingerprint=NEU, beleg="Test")
    identitaeten = ledger.konto_identitaeten("etoro", NEU)
    assert ALT in identitaeten


# ===========================================================================
# OKX-Ausfall vom 02.09.2026, 12:30 Uhr
# ===========================================================================
def test_serverfehler_bekommt_einen_kuerzeren_backoff():
    """Der Backoff stand nach fuenf Minuten bei 300 s. Eine wiederhergestellte
    Verbindung waere damit bis zu fuenf Minuten unbemerkt geblieben."""
    from broker.multi import BrokerHub

    assert BrokerHub.BACKOFF_SERVERFEHLER[-1] < BrokerHub.BACKOFF[-1]
    assert BrokerHub._ist_serverfehler(
        "OKX-Serverfehler HTTP 503 (Demo-Handelsdienst GET /api/v5/account/balance).")
    assert BrokerHub._ist_serverfehler("OKX-Ratenbegrenzung erreicht (HTTP 429).")
    assert not BrokerHub._ist_serverfehler("OKX lehnt die Zugangsdaten ab (HTTP 401).")


def test_fehlermeldung_nennt_den_gescheiterten_aufruf(monkeypatch):
    """25 Minuten lang stand im Log nur "OKX-Serverfehler HTTP 503" -- welcher
    Aufruf scheiterte, war nirgends zu sehen. Tatsaechlich lief der
    oeffentliche Pfad einwandfrei und nur der Demo-Handelsdienst fiel aus.

    Geprueft wird der echte Aufrufpfad mit einer ersetzten HTTP-Sitzung.
    """
    import broker.okx as okx

    class Antwort:
        status_code = 503
        text = "Service Unavailable"
        headers: dict = {}

        def json(self):
            raise ValueError("kein JSON")

    class Sitzung:
        def request(self, *_a, **_k):
            return Antwort()

    client = okx.OKXClient(api_key="k", api_secret="s", passphrase="p", demo=True)
    monkeypatch.setattr(client, "session", Sitzung())

    with pytest.raises(Exception) as privat:
        client.request("GET", "/account/balance", private=True)
    text = str(privat.value)
    assert "503" in text
    assert "account/balance" in text
    assert "Demo-Handelsdienst" in text, (
        "Der Nutzer muss sehen, dass der Demo-Handelsdienst ausfaellt und "
        "nicht die ganze Verbindung")

    with pytest.raises(Exception) as oeffentlich:
        client.request("GET", "/public/time")
    assert "Marktdaten" in str(oeffentlich.value)


def test_universumslauf_friert_bei_leerem_ergebnis_ein(monkeypatch):
    """Am 02.09. um 12:33 rechnete der Lauf waehrend des Ausfalls auf
    0 geeignete Basen und Kern 0/20. Ein Brokerausfall darf das Universum
    einfrieren, niemals leeren.
    """
    import crypto_engine

    motor = object.__new__(crypto_engine.CryptoEngine)
    motor.letzter_fehler = ""
    meldungen = []
    motor._melde_einmal = lambda schluessel, text, **k: meldungen.append(text)
    motor._entwarnung = lambda schluessel, text: None
    motor._favoriten = lambda: []
    motor._okx_bar = lambda: "15m"

    class Selektor:
        def auswahl(self, **_k):
            return {"pool": 0, "abgelehnt": []}

    class Zustand:
        def fuer_broker(self, _b):
            return [object()] * 26          # 26 Werte waren vorher aktiv

    class Universum:
        zustand = Zustand()

        def lauf(self, *_a, **_k):          # darf gar nicht erreicht werden
            raise AssertionError("Ein leeres Ergebnis darf nicht angewendet werden")

    motor._selektor = lambda: Selektor()
    motor.universum = Universum()
    motor.hub = type("Hub", (), {"broker": staticmethod(lambda _n: type(
        "B", (), {"health_state": staticmethod(lambda: "ONLINE")})())})()

    ergebnis = motor.universumslauf()
    assert ergebnis["ok"] is False
    assert ergebnis["eingefroren"] is True
    assert ergebnis["vorher"] == 26
    assert meldungen and "0 Kandidaten" in meldungen[0]


def test_universumslauf_laeuft_bei_gesundem_broker_normal():
    """Die Gegenprobe: ein normales Ergebnis wird angewendet."""
    import crypto_engine

    motor = object.__new__(crypto_engine.CryptoEngine)
    motor.letzter_fehler = ""
    motor._melde_einmal = lambda *a, **k: None
    motor._entwarnung = lambda *a, **k: None
    motor._favoriten = lambda: []
    motor._okx_bar = lambda: "15m"
    motor.buch = type("Buch", (), {"symbole": staticmethod(lambda: [])})()
    angewendet = {"ja": False}

    class Universum:
        zustand = type("Z", (), {"fuer_broker": staticmethod(lambda _b: [1] * 26)})()

    motor._selektor = lambda: type("S", (), {
        "auswahl": staticmethod(lambda **k: {"pool": 26, "abgelehnt": []})})()
    motor.universum = Universum()
    motor.hub = type("Hub", (), {"broker": staticmethod(lambda _n: type(
        "B", (), {"health_state": staticmethod(lambda: "ONLINE")})())})()
    motor.letzter_universumslauf = {}

    class Angewendet(Exception):
        """Sentinel: der Lauf hat das Ergebnis wirklich uebernommen."""

    def _lauf(*_a, **_k):
        angewendet["ja"] = True
        raise Angewendet

    motor.universum.lauf = _lauf
    with pytest.raises(Angewendet):
        motor.universumslauf()
    assert angewendet["ja"], "Ein gueltiges Ergebnis muss angewendet werden"


def test_universumslauf_rechnet_bei_offline_broker_gar_nicht():
    import crypto_engine

    motor = object.__new__(crypto_engine.CryptoEngine)
    motor._selektor = lambda: type("S", (), {
        "auswahl": staticmethod(lambda **k: (_ for _ in ()).throw(
            AssertionError("Bei OFFLINE darf nicht gerechnet werden")))})()
    motor.hub = type("Hub", (), {"broker": staticmethod(lambda _n: type(
        "B", (), {"health_state": staticmethod(lambda: "OFFLINE")})())})()

    ergebnis = motor.universumslauf()
    assert ergebnis["ok"] is False
    assert ergebnis["eingefroren"] is True
    assert "nicht authentifiziert" in ergebnis["grund"]
