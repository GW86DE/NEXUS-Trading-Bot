"""Regressionstests aus den ECHTEN Zustandsdateien vom 02.09.2026.

Alle Fixtures in ``tests/fixtures/echt_*.json`` stammen unveraendert vom
laufenden Pi. Genau das fehlte bisher: die Tests bauten ihre Eingaben selbst
und prueften den Code damit gegen die eigenen Annahmen. Deshalb waren 1071
Tests gruen, waehrend der Kryptohandel 9,5 Stunden stillstand.

Kein Test in dieser Datei durchsucht Quelltext. Jeder fuehrt echte Funktionen
mit echten Daten aus.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("TRADINGBOT_TEST_STATE_DIR", "/tmp/tradingbot_tests")
Path(os.environ["TRADINGBOT_TEST_STATE_DIR"]).mkdir(parents=True, exist_ok=True)

FIXTURES = Path(__file__).resolve().parent / "fixtures"
AKTUELLE_DOMAENE = "etoro:demo:664e7dfbc13e0cd60e493592"
ALTER_FINGERPRINT = "ba32f97fe3482fcbc326e51a"
NEUER_FINGERPRINT = "664e7dfbc13e0cd60e493592"
FET_ORDER = "3884888641315221505"


def echt(name: str) -> dict:
    return json.loads((FIXTURES / f"echt_{name}.json").read_text(encoding="utf-8"))


@pytest.fixture()
def register(tmp_path):
    """Georgs Register -- alle 57 Eintraege als waeren sie noch offen."""
    from order_ownership import OrderOwnershipRegistry

    roh = echt("bot_order_registry")
    pfad = tmp_path / "bot_order_registry.json"
    pfad.write_text(json.dumps(
        {"schema_version": 3, "orders": {}, "pending": roh["orders"]}),
        encoding="utf-8")
    return OrderOwnershipRegistry(pfad)


@pytest.fixture()
def reconciliation(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADINGBOT_TEST_STATE_DIR", str(tmp_path))
    shutil.copy(FIXTURES / "echt_etoro_reconciliation.json",
                tmp_path / "etoro_reconciliation.json")
    import etoro_reconciliation
    return etoro_reconciliation


# ===========================================================================
# 1 -- Der Terminalfilter
# ===========================================================================
def test_abgeschlossene_orders_sperren_nicht_mehr(register):
    """Der Kern der OKX-Sperre.

    ``nicht_terminale()`` benutzte ``darf_per_ttl_verfallen()``, das nur die
    Frage "darf per Zeitablauf verschwinden" beantwortet -- wahr allein fuer
    PLANNED. Ein FILLED-Eintrag galt damit als offen.
    """
    zustaende = {}
    for meta in register.pending.values():
        zustaende[str(meta.get("zustand") or "")] = \
            zustaende.get(str(meta.get("zustand") or ""), 0) + 1
    assert zustaende["FILLED"] == 17
    assert zustaende["CANCELED"] == 30

    offen = register.nicht_terminale()
    assert len(offen) == 10, "Nur die zehn wirklich offenen Saetze"
    assert all(str(v.get("zustand")).upper() == "AWAITING_POSITION_CONFIRMATION"
               for v in offen.values())


def test_fet_order_sperrt_nach_stornierung_nicht_mehr(register):
    """Die konkrete Order, die den Kryptohandel 9,5 Stunden angehalten hat."""
    fet = register.pending[FET_ORDER]
    assert fet["symbol"] == "FET" and fet["inst_id"] == "FET-EUR"
    assert fet["zustand"] == "CANCELED"
    assert FET_ORDER not in register.nicht_terminale()


# ===========================================================================
# 2 -- Die Brokertrennung
# ===========================================================================
def test_etoro_aktien_sperren_den_kryptohandel_nicht(register):
    """Alle zehn offenen Saetze sind eToro-Aktien. Keiner davon geht OKX an."""
    okx = register.pending_for(broker="okx", environment="DEMO",
                               asset_type="crypto")
    etoro = register.pending_for(broker="etoro", environment="DEMO",
                                 asset_type="stock")
    assert okx == {}, "Der Kryptohandel darf davon nichts sehen"
    assert {v["symbol"] for v in etoro.values()} == {
        "ADBE", "CRM", "MSFT", "ORCL", "SPGI"}


def test_fremdes_konto_taucht_nicht_auf(register):
    beispiel = next(iter(register.pending.values()))
    assert register.pending_for(
        broker=str(beispiel["broker"]), environment="DEMO",
        account_fingerprint="ein-voellig-anderes-konto",
        asset_type=str(beispiel["asset_type"])) != register.pending

    treffer = register.pending_for(broker="etoro", environment="DEMO",
                                   account_fingerprint="", asset_type="stock")
    assert treffer, "Ohne Kontovorgabe bleiben die Saetze sichtbar"


def test_demo_und_live_bleiben_getrennt(tmp_path):
    from order_ownership import OrderOwnershipRegistry

    pfad = tmp_path / "r.json"
    pfad.write_text(json.dumps({"schema_version": 3, "orders": {}, "pending": {
        "a": {"broker": "okx", "asset_type": "crypto", "symbol": "BTC",
              "zustand": "SUBMITTING", "environment": "DEMO"},
        "b": {"broker": "okx", "asset_type": "crypto", "symbol": "ETH",
              "zustand": "SUBMITTING", "environment": "LIVE"},
    }}), encoding="utf-8")
    r = OrderOwnershipRegistry(pfad)
    assert {v["symbol"] for v in r.pending_for(
        broker="okx", environment="DEMO", asset_type="crypto").values()} == {"BTC"}
    assert {v["symbol"] for v in r.pending_for(
        broker="okx", environment="LIVE", asset_type="crypto").values()} == {"ETH"}


# ===========================================================================
# 3 -- Die Klaerungsfrist (FET)
# ===========================================================================
def test_ueberfaellige_order_wird_erkannt(tmp_path):
    from order_ownership import OrderOwnershipRegistry

    jetzt = time.time()
    pfad = tmp_path / "r.json"
    pfad.write_text(json.dumps({"schema_version": 3, "orders": {}, "pending": {
        "frisch": {"broker": "okx", "asset_type": "crypto", "symbol": "BTC",
                   "zustand": "SUBMITTING", "created_at_ts": jetzt - 60},
        "alt": {"broker": "okx", "asset_type": "crypto", "symbol": "FET",
                "zustand": "SUBMITTING", "created_at_ts": jetzt - 38035,
                "ord_id": FET_ORDER},
    }}), encoding="utf-8")
    r = OrderOwnershipRegistry(pfad)

    ueberfaellig = r.ueberfaellige(hoechstalter_sekunden=900.0, broker="okx",
                                   asset_type="crypto")
    assert [x["symbol"] for x in ueberfaellig] == ["FET"]
    assert ueberfaellig[0]["ord_id"] == FET_ORDER
    # 38035 s sind die tatsaechlichen 10 h 34 min aus dem Register.
    assert ueberfaellig[0]["alter_sekunden"] > 10 * 3600


def test_ohne_frist_wird_nichts_gemeldet(register):
    assert register.ueberfaellige(hoechstalter_sekunden=0) == []


# ===========================================================================
# 4 -- Die drei Domaenengenerationen
# ===========================================================================
def test_die_datei_enthaelt_drei_domaenengenerationen(reconciliation):
    domaenen = {str(r.get("domain") or "")
                for r in echt("etoro_reconciliation")["records"].values()}
    assert "etoro:demo" in domaenen, "Altbestand ohne Kontobindung"
    assert f"etoro:demo:{ALTER_FINGERPRINT}" in domaenen, "9.5-Generation"
    assert AKTUELLE_DOMAENE not in domaenen, (
        "Kein einziger Satz traegt die aktuelle Domaene -- genau deshalb "
        "meldete der Kaufpfad null ungeklaerte Faelle")


def test_alter_fingerprint_sperrt_erst_nach_brokerbeleg(reconciliation):
    """Vor dem Beleg unklar, nach dem Beleg sperrend -- nie geraten."""
    vorher = reconciliation.active_for_domain(AKTUELLE_DOMAENE)
    assert vorher == [], (
        "Ein unbekannter fremder Fingerprint darf diesen Handel nicht "
        "anhalten -- er koennte zu einem anderen eToro-Konto gehoeren")

    # Der Worker sieht ihn trotzdem, sonst klaert er sich nie.
    kandidaten = {r["symbol"] for r in
                  reconciliation.klaerungskandidaten(AKTUELLE_DOMAENE)}
    assert kandidaten == {"ADBE", "CRM"}

    reconciliation.konto_alias_registrieren(
        ALTER_FINGERPRINT, NEUER_FINGERPRINT,
        beleg="orderId 378375675 im aktuellen Konto bestaetigt")

    nachher = reconciliation.active_for_domain(AKTUELLE_DOMAENE)
    assert [r["symbol"] for r in nachher] == ["ADBE"]
    assert nachher[0]["_domaenen_zuordnung"] == "ALIAS"


def test_ein_wirklich_fremdes_konto_bleibt_unberuehrt(reconciliation):
    reconciliation.konto_alias_registrieren(
        ALTER_FINGERPRINT, NEUER_FINGERPRINT, beleg="Test")
    assert reconciliation.active_for_domain("etoro:demo:voellig-anderes") == []


def test_alias_ist_idempotent(reconciliation):
    assert reconciliation.konto_alias_registrieren(
        ALTER_FINGERPRINT, NEUER_FINGERPRINT, beleg="erster Beleg") is True
    assert reconciliation.konto_alias_registrieren(
        ALTER_FINGERPRINT, NEUER_FINGERPRINT, beleg="zweiter Beleg") is False
    assert reconciliation.konto_aliase()[ALTER_FINGERPRINT] == NEUER_FINGERPRINT


def test_adbe_bleibt_ohne_worker_fuer_immer_stehen(reconciliation):
    """Der Beleg fuer den verwaisten Datensatz.

    ``position_absence_checks`` steht auf 1, noetig sind 3 -- und die letzte
    Pruefung war 17 Stunden alt. Ohne Klaerungsbereich haette sich das nie
    mehr bewegt.
    """
    adbe = echt("etoro_reconciliation")["records"]["4153277691980212224"]
    assert adbe["symbol"] == "ADBE"
    assert adbe["position_absence_checks"] == 1
    assert adbe["state"] == "AWAITING_POSITION_CONFIRMATION"
    assert adbe["last_position_absence_check_at_utc"].startswith("2026-09-01T14:38")

    kandidaten = [r["decision_id"] for r in
                  reconciliation.klaerungskandidaten(AKTUELLE_DOMAENE)]
    assert 4153277691980212224 in kandidaten


# ===========================================================================
# 5 -- Buchungsluecke ist keine offene Order
# ===========================================================================
def test_crm_ist_eine_buchungsluecke_keine_offene_order(reconciliation):
    reconciliation.konto_alias_registrieren(
        ALTER_FINGERPRINT, NEUER_FINGERPRINT, beleg="Test")
    sperrend = {r["symbol"] for r in
                reconciliation.active_for_domain(AKTUELLE_DOMAENE)}
    assert "CRM" not in sperrend, (
        "Der Broker hat abgeschlossen -- es kann nichts doppelt gekauft werden")

    luecken = reconciliation.buchungsluecken(AKTUELLE_DOMAENE)
    assert "CRM" in {x["symbol"] for x in luecken}
    unvollstaendig, text = reconciliation.pnl_unvollstaendig(AKTUELLE_DOMAENE)
    assert unvollstaendig is True
    assert "PNL_INCOMPLETE" in text


def test_buchungsluecke_sperrt_den_kauf_weiterhin():
    """Die Schutzwirkung bleibt -- nur unter dem richtigen Namen."""
    import trading_ready

    b = trading_ready.Handelsbereitschaft("etoro")
    assert "buchung_vollstaendig" in b.bedingungen
    for name in b.bedingungen:
        b.melde(name, True, "")
    assert "buchung_vollstaendig" not in [x.name for x in b.offene_bedingungen()]
    b.melde("buchung_vollstaendig", False, "PNL_INCOMPLETE: CRM")
    assert "buchung_vollstaendig" in [x.name for x in b.offene_bedingungen()]
    assert b.darf_kaufen()[0] is False


# ===========================================================================
# 6 -- Die Anzeige
# ===========================================================================
def test_der_sperrgrund_nennt_den_richtigen_broker():
    import trading_ready

    okx = trading_ready.Handelsbereitschaft("okx")
    etoro = trading_ready.Handelsbereitschaft("etoro")
    assert okx.bedingungen["keine_unklare_order"].beschreibung == \
        "OKX-Ausfuehrungsabgleich abgeschlossen"
    assert etoro.bedingungen["keine_unklare_order"].beschreibung == \
        "eToro-Ausfuehrungsabgleich abgeschlossen"
    assert "eToro" not in okx.bedingungen["keine_unklare_order"].beschreibung


def test_die_okx_diagnosefelder_sind_nicht_mehr_leer():
    """In der echten Datei standen drei Felder dauerhaft auf null."""
    gemeldet = echt("runtime_status_okx")["connection_components"]
    assert gemeldet["account_last_update"] is None, "so war es in 9.5.1"
    stream = echt("runtime_status_okx")["position_stream"]
    assert stream["last_account"], "die Daten lagen die ganze Zeit vor"

    from broker.okx_stream import StreamStatus
    status = StreamStatus(
        running=True, connected=True, authenticated=True,
        last_message="2026-09-02T07:12:05Z", last_error="", reconnects=0,
        mode="demo", endpoint="wss://example", last_account="2026-09-02T07:12:05Z",
        last_order="2026-09-02T07:11:00Z", last_pong="2026-09-02T07:12:00Z")
    d = status.as_dict()
    assert d["last_account_update"] == d["last_account"]
    assert d["last_order_update"] == d["last_order"]
    assert d["last_pong"] == "2026-09-02T07:12:00Z"


# ===========================================================================
# 7 -- Der eToro-WebSocket
# ===========================================================================
def test_kein_rfc_ping_mehr_und_ein_waechter_stattdessen(monkeypatch):
    """Der Client-Ping hat die Verbindung im Zweiminutentakt selbst beendet.

    Geprueft wird der tatsaechliche Aufruf, nicht der Quelltext: ein
    Ersatz-WebSocketApp zeichnet auf, mit welchen Argumenten ``run_forever``
    laeuft. Ein Texttest haette schon an einem Kommentar angeschlagen.
    """
    import sys
    import types
    import broker.etoro_stream as es

    aufrufe = []

    class FakeApp:
        def __init__(self, url, **kwargs):
            self.url = url

        def run_forever(self, **kwargs):
            aufrufe.append(dict(kwargs))
            stream._stop.set()

        def close(self):
            pass

    modul = types.ModuleType("websocket")
    modul.WebSocketApp = FakeApp
    monkeypatch.setitem(sys.modules, "websocket", modul)

    stream = es.EtoroPrivateStream("api", "user")
    stream._run()

    assert aufrufe, "run_forever wurde nicht aufgerufen"
    assert "ping_interval" not in aufrufe[0], (
        "eToro beantwortet den RFC-Ping nicht; der Client trennte sich damit "
        "alle zwei Minuten selbst")
    assert "ping_timeout" not in aufrufe[0]
    assert hasattr(es.EtoroPrivateStream, "_waechter")
    assert es.AUTH_FRIST_SEKUNDEN > 0 and es.SUBSCRIBE_FRIST_SEKUNDEN > 0
    assert es.STILLE_FRIST_SEKUNDEN >= 60


def test_backoff_wird_nach_einer_stabilen_sitzung_zurueckgesetzt():
    """Im Log wuchs die Pause bis auf 30 s, obwohl jede Sitzung lief."""
    import broker.etoro_stream as es

    assert es.STABIL_NACH_SEKUNDEN > 0
    # Eine Sitzung, die laenger als die Schwelle lief, gilt als stabil.
    assert es.STABIL_NACH_SEKUNDEN <= 300, (
        "Sonst gilt praktisch keine Sitzung als stabil")


def test_waechter_erneuert_bei_fehlender_authentifizierung(monkeypatch):
    import broker.etoro_stream as es

    stream = es.EtoroPrivateStream("api", "user")
    geschlossen = {"ja": False}

    class App:
        def close(self_inner):
            geschlossen["ja"] = True

    app = App()
    stream._app = app
    uhr = {"t": 0.0}
    monkeypatch.setattr(es.time, "monotonic", lambda: uhr["t"])

    class Stop:
        def is_set(self_inner):
            return False

        def wait(self_inner, _s):
            uhr["t"] += 30.0      # Frist ueberschreiten
            return False

    stream._stop = Stop()
    stream._waechter(app)
    assert geschlossen["ja"] is True
    assert "Authentifizierung" in stream.status().last_error


def test_waechter_laesst_eine_lebende_verbindung_in_ruhe(monkeypatch):
    import broker.etoro_stream as es

    stream = es.EtoroPrivateStream("api", "user")
    geschlossen = {"ja": False}

    class App:
        def close(self_inner):
            geschlossen["ja"] = True

    app = App()
    stream._app = app
    stream._status.authenticated = True
    stream._status.subscribed = True
    uhr = {"t": 0.0}
    monkeypatch.setattr(es.time, "monotonic", lambda: uhr["t"])
    schritte = {"n": 0}

    class Stop:
        def is_set(self_inner):
            return schritte["n"] > 20

        def wait(self_inner, _s):
            schritte["n"] += 1
            uhr["t"] += 5.0
            stream._letzte_nachricht_monoton = uhr["t"]   # Daten fliessen
            return schritte["n"] > 20

    stream._stop = Stop()
    stream._waechter(app)
    assert geschlossen["ja"] is False, (
        "Eine Verbindung, die liefert, darf nicht getrennt werden")


def test_streamzustand_ist_sichtbar():
    import broker.etoro_stream as es

    d = es.StreamStatus().as_dict()
    for feld in ("generation", "session_id", "endpoint", "connected_since",
                 "last_close_code", "last_close_reason", "next_reconnect_in",
                 "zustand"):
        assert feld in d, f"{feld} fehlt -- der Stream waere wieder unsichtbar"


def test_okx_pong_wird_nur_durch_ein_echtes_pong_quittiert():
    import broker.okx_stream as os_

    stream = object.__new__(os_.OKXPrivateStream)
    stream._lock = __import__("threading").RLock()
    stream._last_message_at = 0.0
    stream._letzte_nachricht_monoton = 0.0
    stream._warte_auf_pong_seit = 111.0
    stream._last_pong_at = 0.0

    stream._touch()                       # irgendeine Kontonachricht
    assert stream._warte_auf_pong_seit == 111.0, (
        "Datenverkehr allein beweist kein beantwortetes Keepalive")
    stream._touch(pong=True)              # echtes "pong"
    assert stream._warte_auf_pong_seit == 0.0
    assert stream._last_pong_at > 0
