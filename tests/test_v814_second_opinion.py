"""GPT Second Opinion vor Kauforders (v8.1.4).

Georgs Vorgabe: "Als zusaetzliche Einschaetzung ja -- als automatische
Kauf-Freigabe wuerde ich es nicht einsetzen. Die Order darf ausschliesslich
durch die festen Regeln, Live-Freigabe und Broker-Schutz ausgeloest werden."
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("TRADINGBOT_TEST_STATE_DIR", "/tmp/tradingbot_tests")
Path(os.environ["TRADINGBOT_TEST_STATE_DIR"]).mkdir(parents=True, exist_ok=True)

WURZEL = Path(__file__).resolve().parent.parent


class FakeAntwort:
    def __init__(self, daten=None, ok=True, grund=""):
        self.ok = ok
        self.daten = daten or {}
        self.grund = grund
        self.modell = "terra-test"
        self.kosten_usd = 0.004


class FakeRouter:
    def __init__(self, antwort=None, fehler=None):
        self._antwort = antwort
        self._fehler = fehler
        self.anfragen = []

    def frage(self, aufgabe, nutzlast, schema, **kw):
        self.anfragen.append((aufgabe, nutzlast))
        if self._fehler:
            raise self._fehler
        return self._antwort


@pytest.fixture
def sauber(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADINGBOT_TEST_STATE_DIR", str(tmp_path))
    import second_opinion as so
    monkeypatch.setattr(so, "_state_root", lambda: tmp_path)
    return so


def fakten(preis=100.0, wert=1000.0, stop=95.0):
    return {"symbol": "BTC", "broker": "okx", "preis": preis, "wert": wert,
            "menge": 10.0, "stop": stop, "ziel": 110.0, "waehrung": "EUR"}


# ---------------------------------------------------------------------------
# Der Grundsatz: keine Freigabe, nur Warnung
# ---------------------------------------------------------------------------
def test_unauffaellig_aendert_nichts(sauber):
    urteil = sauber.hole(FakeRouter(FakeAntwort(
        {"einschaetzung": "unauffaellig", "begruendung": "nichts Auffaelliges"})),
        fakten())
    assert urteil.einschaetzung == sauber.UNAUFFAELLIG
    assert urteil.blockiert is False


def test_vorsichtig_haelt_den_kauf_nicht_auf(sauber):
    urteil = sauber.hole(FakeRouter(FakeAntwort(
        {"einschaetzung": "vorsichtig", "begruendung": "RSI dreht nur schwach"})),
        fakten())
    assert urteil.blockiert is False


def test_kritisch_bleibt_reine_warnung(sauber):
    urteil = sauber.hole(FakeRouter(FakeAntwort(
        {"einschaetzung": "kritisch", "begruendung": "4h-Trend faellt"})), fakten())
    assert urteil.kritisch is True
    assert urteil.blockiert is False


def test_ki_ausfall_haelt_den_kauf_nicht_auf(sauber):
    """Wuerde ein Ausfall den Kauf verhindern, waere die KI ein Freigabetor."""
    urteil = sauber.hole(FakeRouter(fehler=RuntimeError("Timeout")), fakten())
    assert urteil.einschaetzung == sauber.NICHT_ERREICHBAR
    assert urteil.blockiert is False
    assert "Timeout" in urteil.begruendung


def test_antwort_ausserhalb_des_schemas_haelt_nicht_auf(sauber):
    urteil = sauber.hole(FakeRouter(FakeAntwort(
        {"einschaetzung": "kaufen!", "begruendung": "los"})), fakten())
    assert urteil.einschaetzung == sauber.NICHT_ERREICHBAR
    assert urteil.blockiert is False


def test_ohne_router_wird_nicht_gefragt(sauber):
    urteil = sauber.hole(None, fakten())
    assert urteil.gefragt is False


# ---------------------------------------------------------------------------
# Der Schalter
# ---------------------------------------------------------------------------
def test_schalter_aus_erzeugt_keine_anfrage(monkeypatch):
    import config
    import second_opinion as so
    monkeypatch.setattr(config, "AI_SECOND_OPINION_MODE", "aus", raising=False)
    assert so.aktiv(live=True) is False
    assert so.aktiv(live=False) is False


def test_nur_live(monkeypatch):
    import config
    import second_opinion as so
    monkeypatch.setattr(config, "AI_SECOND_OPINION_MODE", "nur_live", raising=False)
    assert so.aktiv(live=True) is True
    assert so.aktiv(live=False) is False


def test_immer_gilt_auch_im_demo(monkeypatch):
    import config
    import second_opinion as so
    monkeypatch.setattr(config, "AI_SECOND_OPINION_MODE", "immer", raising=False)
    assert so.aktiv(live=False) is True


# ---------------------------------------------------------------------------
# Wartende Orders
# ---------------------------------------------------------------------------
def test_wartende_order_wird_gespeichert_und_gefunden(sauber):
    urteil = sauber.Urteil(einschaetzung=sauber.KRITISCH, begruendung="Test",
                           gefragt=True)
    sauber.stelle_zurueck(broker="okx", symbol="BTC", urteil=urteil, fakten=fakten())
    offen = sauber.offene("okx")
    assert len(offen) == 1 and offen[0]["symbol"] == "BTC"
    assert offen[0]["freigegeben"] is False


def test_freigabe_kauft_nicht_sofort(sauber):
    """Die Freigabe hebt eine Warnung auf -- sie kauft nichts."""
    urteil = sauber.Urteil(einschaetzung=sauber.KRITISCH, gefragt=True)
    sauber.stelle_zurueck(broker="okx", symbol="BTC", urteil=urteil, fakten=fakten())
    eintrag = sauber.gib_frei("okx", "BTC")
    assert eintrag is not None and eintrag["freigegeben"] is True
    assert sauber.freigegebene("okx", "BTC") is not None


def test_wartende_order_verfaellt(sauber, monkeypatch):
    import config
    monkeypatch.setattr(config, "AI_PENDING_EXPIRY_MINUTES", 15.0, raising=False)
    urteil = sauber.Urteil(einschaetzung=sauber.KRITISCH, gefragt=True)
    eintrag = sauber.stelle_zurueck(broker="okx", symbol="BTC", urteil=urteil,
                                    fakten=fakten())
    # Zeit vorspulen, indem die Verfallszeit direkt in die Vergangenheit gelegt wird
    import json
    datei = sauber._state_root() / sauber.WARTEDATEI
    daten = json.loads(datei.read_text(encoding="utf-8"))
    daten["okx:BTC"]["verfaellt_um"] = time.time() - 1
    datei.write_text(json.dumps(daten), encoding="utf-8")

    assert sauber.offene("okx") == []
    assert sauber.freigegebene("okx", "BTC") is None


def test_nur_eine_wartende_order_je_symbol(sauber):
    urteil = sauber.Urteil(einschaetzung=sauber.KRITISCH, gefragt=True)
    sauber.stelle_zurueck(broker="okx", symbol="BTC", urteil=urteil, fakten=fakten())
    zweiter = sauber.stelle_zurueck(broker="okx", symbol="BTC", urteil=urteil,
                                    fakten=fakten())
    assert zweiter["ersetzt_vorherige"] is True
    assert len(sauber.offene("okx")) == 1


# ---------------------------------------------------------------------------
# Die Grundlage muss noch stimmen
# ---------------------------------------------------------------------------
def test_kursdrift_laesst_die_freigabe_verfallen(sauber, monkeypatch):
    import config
    monkeypatch.setattr(config, "AI_PENDING_MAX_DRIFT_PCT", 0.003, raising=False)
    eintrag = {"fakten": fakten(preis=100.0)}
    gueltig, grund = sauber.grundlage_noch_gueltig(
        eintrag, preis=101.0, cash=99999, bestand_vorhanden=False, risiko_offen=True)
    assert gueltig is False
    assert "gewandert" in grund


def test_fehlendes_guthaben_laesst_verfallen(sauber):
    eintrag = {"fakten": fakten(wert=1000.0)}
    gueltig, grund = sauber.grundlage_noch_gueltig(
        eintrag, preis=100.0, cash=500.0, bestand_vorhanden=False, risiko_offen=True)
    assert gueltig is False and "Guthaben reicht nicht" in grund


def test_bereits_gehalten_laesst_verfallen(sauber):
    gueltig, grund = sauber.grundlage_noch_gueltig(
        {"fakten": fakten()}, preis=100.0, cash=99999,
        bestand_vorhanden=True, risiko_offen=True)
    assert gueltig is False and "bereits gehalten" in grund


def test_kurs_unter_stop_laesst_verfallen(sauber):
    """Enger Stop: der Kurs liegt darunter, ist aber noch in der Drifttoleranz."""
    gueltig, grund = sauber.grundlage_noch_gueltig(
        {"fakten": fakten(preis=100.0, stop=99.9)}, preis=99.85, cash=99999,
        bestand_vorhanden=False, risiko_offen=True)
    assert gueltig is False and "unter dem geplanten Stop" in grund


def test_unveraenderte_grundlage_bleibt_gueltig(sauber):
    gueltig, grund = sauber.grundlage_noch_gueltig(
        {"fakten": fakten(preis=100.0)}, preis=100.1, cash=99999,
        bestand_vorhanden=False, risiko_offen=True)
    assert gueltig is True and grund == ""


# ---------------------------------------------------------------------------
# Der Kaufpfad
# ---------------------------------------------------------------------------
def test_ki_beitrag_hat_immer_gewicht_null():
    """Sonst wuerde die Einschaetzung das Stimmungsbild verschieben."""
    quelle = (WURZEL / "crypto_engine.py").read_text(encoding="utf-8")
    stelle = quelle.index("def _second_opinion")
    block = quelle[stelle:quelle.index("def _melde_bereitschaft")]
    assert "gewicht=0.0" in block
    assert "NEUTRAL" in block
    # Die KI bleibt NEUTRAL/0.0. DAFUER darf nur beim menschlichen NUTZER-
    # Beitrag stehen, nicht beim Terra-Beitrag.
    for verboten in ("gewicht=0.1", "gewicht=0.2"):
        assert verboten not in block, f"{verboten} wuerde die Entscheidung beeinflussen"
    assert 'protokoll.ki("terra", NEUTRAL' in block


def test_second_opinion_steht_nach_dem_candidate_gate():
    quelle = (WURZEL / "crypto_engine.py").read_text(encoding="utf-8")
    gate = quelle.index("if not entscheidung.approved")
    so_stelle = quelle.index("self._second_opinion(", gate)
    order = quelle.index("ergebnis = broker.kaufe_mit_absicherung")
    assert gate < so_stelle < order, \
        "Die KI wird erst nach allen festen Pruefungen gefragt und vor der Order"
    zwischenraum = quelle[so_stelle:order]
    assert '"WARTET"' not in zwischenraum


def test_kritisches_urteil_erzeugt_sicheres_human_gate():
    quelle = (WURZEL / "crypto_engine.py").read_text(encoding="utf-8")
    start = quelle.index("def _second_opinion")
    ende = quelle.index("def _melde_bereitschaft", start)
    block = quelle[start:ende]
    assert "human_gate_aktiv" in block
    assert "stelle_zurueck" in block
    assert "rueckfragetext" in block
    assert "grundlage_noch_gueltig" in block
    assert "return False" in block


def test_verkaufspfad_fragt_die_ki_nie():
    """Kein Ausstieg darf je auf ein Modell warten."""
    quelle = (WURZEL / "crypto_engine.py").read_text(encoding="utf-8")
    start = quelle.index("def _schliesse")
    ende = quelle.index("def ", start + 20)
    block = quelle[start:ende]
    for verboten in ("second_opinion", "_second_opinion", "self.ai"):
        assert verboten not in block, \
            f"{verboten} im Verkaufspfad -- ein Ausstieg darf nie auf die KI warten"


def test_positionspruefung_fragt_die_ki_nie():
    quelle = (WURZEL / "crypto_engine.py").read_text(encoding="utf-8")
    start = quelle.index("def pruefe_positionen")
    ende = quelle.index("def _position_verschwunden")
    assert "second_opinion" not in quelle[start:ende]


def test_aufgabe_ist_im_router_registriert():
    from ai_router import AUFGABEN
    aufgabe = AUFGABEN.get("second_opinion")
    assert aufgabe is not None
    assert aufgabe.web_suche is False, "Der Kaufpfad darf nicht auf eine Websuche warten"
    assert aufgabe.cache_stunden == 0.0, \
        "Eine Einschaetzung von vor Stunden waere fuer diesen Einstieg wertlos"


def test_rueckfragetext_nennt_zahlen_und_verfall(sauber):
    urteil = sauber.Urteil(einschaetzung=sauber.KRITISCH,
                           begruendung="4h-Trend faellt", gefragt=True,
                           risiken=["wenig Puffer zur Kostenhuerde"])
    eintrag = sauber.stelle_zurueck(broker="okx", symbol="BTC", urteil=urteil,
                                    fakten=fakten())
    text = sauber.rueckfragetext(eintrag)
    assert "WARTET AUF FREIGABE" in text
    assert "KRITISCH" in text
    assert "4h-Trend faellt" in text
    assert "Verfaellt um" in text
    assert "Die feste Kaskade hat diesen Kauf freigegeben" in text
