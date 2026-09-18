"""10.6.0 -- Einsatzstufe je Broker, zur Laufzeit umstellbar.

BEFUND VOM 18.09.2026
=====================
Der Bot kaufte XRP, BTC und ETH fuer je rund 20 EUR, obwohl das OKX-Konto
172.214,65 EUR wert war. Zwei Ursachen, beide belegt im Entscheidungsjournal
des Diagnosepakets vom 18.09.2026, 06:11 UTC:

1. Krypto dimensioniert bewusst auf dem freien Guthaben DER WAEHRUNG, in der
   gekauft wird. Beim ETH-Kauf um 00:55 UTC waren das 649,86 EUR, nicht die
   172.214,65 EUR Kontowert. 0,3 % davon sind 1,95 EUR Risiko; bei 10 %
   Stopabstand ergibt das 19,50 EUR Positionswert. Genau so steht es im Beleg.

2. Das Risikoprofil war wirkungslos. ``TopfGrenzen.fuer_broker`` bevorzugt
   Werte mit Brokerpraefix, und in der Konfiguration stehen feste
   ``OKX_RISK_PER_TRADE_PCT``/``OKX_MAX_POSITION_PCT``. Das Profil schreibt
   nur die praefixlosen und die CRYPTO_-Namen. OFFENSIV meldete 2,00 % je
   Trade -- gerechnet wurde weiter mit 0,3 %.

Seit 10.6.0 entscheidet eine eigene Einsatzstufe je Broker, waehlbar in der
WebUI, wirksam ohne Neustart. Diese Tests halten beides fest: dass die Wahl
wirkt UND dass ohne Wahl exakt nichts anders ist als vorher.
"""
from __future__ import annotations

import json

import pytest


@pytest.fixture
def stufen(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADINGBOT_TEST_STATE_DIR", str(tmp_path))
    import risk_levels
    monkeypatch.setattr(risk_levels, "_root", lambda: tmp_path)
    return risk_levels


@pytest.fixture
def okx_topf(tmp_path, stufen):
    from risk_pots import RiskPot
    topf = RiskPot("okx", state_datei=str(tmp_path / "risk_okx.json"))
    topf.kontowert = 649.864179867
    return topf


# Die echten Zahlen des ETH-Kaufs vom 18.09.2026, 00:55:07 UTC.
ETH_PREIS = 2131.30
ETH_STOP = 1918.17
ETH_LANE_CASH = 649.864179867
ETH_WERT = 19.495853620000002


def wert(topf) -> float:
    menge, _ = topf.positionsgroesse(ETH_PREIS, ETH_STOP, asset_type="crypto",
                                     kontowert_override=ETH_LANE_CASH)
    return menge * ETH_PREIS


# ---------------------------------------------------------------------------
# Ohne Wahl aendert sich nichts
# ---------------------------------------------------------------------------
def test_ohne_gewaehlte_stufe_bleibt_der_echte_kauf_unveraendert(okx_topf):
    """Gegenprobe gegen den echten Beleg: 19,50 EUR, keinen Cent anders."""
    assert wert(okx_topf) == pytest.approx(ETH_WERT, abs=0.01)


def test_ohne_wahl_meldet_das_modul_ausdruecklich_keine_stufe(stufen):
    for broker in ("okx", "etoro"):
        row = stufen.status(broker)
        assert row["chosen"] is False
        assert row["level"] == ""
        assert row["risiko_pro_trade_pct"] is None, (
            "Ohne Wahl darf keine Zahl vorgetaeuscht werden")


def test_ohne_wahl_liefert_effective_die_uebergebene_vorgabe(stufen):
    werte = stufen.effective("etoro", 0.0123, 0.0456)
    assert werte["risiko_pro_trade_pct"] == 0.0123
    assert werte["max_position_pct"] == 0.0456
    assert werte["quelle"] == "vorgabe" and werte["chosen"] is False


def test_basiswerte_kommen_aus_derselben_quelle_wie_der_topf(stufen):
    import config
    from risk_pots import TopfGrenzen
    grenzen = TopfGrenzen.fuer_broker("okx")
    assert stufen.basiswerte("okx") == (grenzen.risiko_pro_trade_pct,
                                        grenzen.max_position_pct)
    assert stufen.basiswerte("okx") == (config.OKX_RISK_PER_TRADE_PCT,
                                        config.OKX_MAX_POSITION_PCT)


# ---------------------------------------------------------------------------
# Die Wahl wirkt -- ohne Neustart
# ---------------------------------------------------------------------------
def test_stufenwechsel_wirkt_ohne_neustart_am_selben_topf(okx_topf, stufen):
    """Derselbe Topf, dasselbe Objekt, keine Neuerzeugung: Wert muss steigen."""
    vorher = wert(okx_topf)
    stufen.set_level("okx", "mittel", source="test", notify=False)
    mittel = wert(okx_topf)
    stufen.set_level("okx", "erhoeht", source="test", notify=False)
    hoch = wert(okx_topf)
    assert vorher == pytest.approx(ETH_WERT, abs=0.01)
    assert mittel == pytest.approx(vorher * 2, rel=0.001)
    assert hoch == pytest.approx(vorher * 4, rel=0.001)


def test_anzeige_nennt_dieselben_zahlen_wie_der_kaufpfad(okx_topf, stufen):
    stufen.set_level("okx", "mittel", source="test", notify=False)
    sicht = okx_topf.uebersicht()
    rechnung = okx_topf.einsatz()
    assert sicht["risiko_pro_trade_pct"] == rechnung["risiko_pro_trade_pct"] == 0.006
    assert sicht["max_position_pct"] == rechnung["max_position_pct"] == 0.10
    assert sicht["einsatz_stufe"] == "mittel" and sicht["einsatz_quelle"] == "stufe"


def test_die_begruendung_nennt_die_wirksame_stufe(okx_topf, stufen):
    stufen.set_level("okx", "erhoeht", source="test", notify=False)
    _, grund = okx_topf.positionsgroesse(ETH_PREIS, ETH_STOP, asset_type="crypto",
                                         kontowert_override=ETH_LANE_CASH)
    assert "Erhoeht" in grund and "1.20 %" in grund, grund


def test_positionsdeckel_begrenzt_bei_engem_stop(okx_topf, stufen):
    """Bei engem Stop gewinnt der Deckel -- die kleinere Grenze gilt immer."""
    stufen.set_level("okx", "mittel", source="test", notify=False)
    menge, grund = okx_topf.positionsgroesse(100.0, 99.0, asset_type="crypto",
                                             kontowert_override=1000.0)
    assert menge * 100.0 == pytest.approx(100.0, rel=0.001)  # 10 % von 1000
    assert "Positionsgroesse" in grund


def test_jede_stufe_ist_streng_groesser_als_die_vorherige(stufen):
    for broker in ("okx", "etoro"):
        werte = [stufen.LEVELS[broker][name] for name in stufen.VALID_LEVELS]
        risiko = [w["risiko_pro_trade_pct"] for w in werte]
        deckel = [w["max_position_pct"] for w in werte]
        assert risiko == sorted(risiko) and len(set(risiko)) == 3
        assert deckel == sorted(deckel) and len(set(deckel)) == 3
        assert stufen.VALID_LEVELS[0] == "vorsichtig", (
            "Die erste Stufe ist der Rueckfall und muss die kleinste sein")


def test_okx_vorsichtig_trifft_den_bisherigen_wert_exakt(stufen):
    """Die kleinste OKX-Stufe ist genau der alte Zustand, nicht ungefaehr."""
    import config
    row = stufen.LEVELS["okx"]["vorsichtig"]
    assert row["risiko_pro_trade_pct"] == config.OKX_RISK_PER_TRADE_PCT
    assert row["max_position_pct"] == config.OKX_MAX_POSITION_PCT


def test_etoro_stufen_bilden_die_profile_exakt_ab(stufen):
    import profiles
    paare = {"vorsichtig": "konservativ", "mittel": "ausgewogen", "erhoeht": "offensiv"}
    for stufe, profil in paare.items():
        werte = profiles.PROFILES[profil]["werte"]
        assert stufen.LEVELS["etoro"][stufe]["risiko_pro_trade_pct"] == werte["RISK_PER_TRADE_PCT"]
        assert stufen.LEVELS["etoro"][stufe]["max_position_pct"] == werte["MAX_POSITION_PCT"]


# ---------------------------------------------------------------------------
# Fail-safe: ein Fehler macht die Position nie groesser
# ---------------------------------------------------------------------------
def test_beschaedigte_datei_faellt_auf_die_kleinste_stufe(okx_topf, stufen):
    stufen.set_level("okx", "erhoeht", source="test", notify=False)
    assert wert(okx_topf) == pytest.approx(ETH_WERT * 4, rel=0.001)
    stufen.path().write_text("{kaputt", encoding="utf-8")
    row = stufen.status("okx")
    assert row["level"] == "vorsichtig" and row["error"]
    assert wert(okx_topf) == pytest.approx(ETH_WERT, abs=0.01)


def test_entfernte_datei_nach_einer_wahl_faellt_ebenfalls_zurueck(okx_topf, stufen):
    stufen.set_level("okx", "erhoeht", source="test", notify=False)
    stufen.path().unlink()
    row = stufen.status("okx")
    assert row["level"] == "vorsichtig" and "fehlt" in row["error"]
    assert wert(okx_topf) == pytest.approx(ETH_WERT, abs=0.01)


def test_fremder_inhalt_ohne_brokerteil_gilt_als_keine_wahl(okx_topf, stufen):
    stufen.path().write_text(json.dumps({"schema": 1, "broker": {}}), encoding="utf-8")
    assert stufen.status("okx")["chosen"] is False
    assert wert(okx_topf) == pytest.approx(ETH_WERT, abs=0.01)


def test_unbekannte_stufe_und_unbekannter_broker_werden_abgelehnt(stufen):
    with pytest.raises(ValueError):
        stufen.set_level("okx", "riesig", source="test", notify=False)
    with pytest.raises(ValueError):
        stufen.set_level("binance", "mittel", source="test", notify=False)
    with pytest.raises(ValueError):
        stufen.status("binance")


def test_ausdruecklich_gesetzte_grenzen_folgen_der_stufe_nicht(tmp_path, stufen):
    """Wer Grenzen uebergibt, meint genau diese -- sonst braechen alte Tests."""
    from risk_pots import RiskPot, TopfGrenzen
    stufen.set_level("okx", "erhoeht", source="test", notify=False)
    topf = RiskPot("okx", grenzen=TopfGrenzen(risiko_pro_trade_pct=0.001,
                                              max_position_pct=0.02),
                   state_datei=str(tmp_path / "fest.json"))
    topf.kontowert = ETH_LANE_CASH
    assert topf.einsatz()["risiko_pro_trade_pct"] == 0.001
    assert topf.einsatz()["quelle"] == "explizit"


# ---------------------------------------------------------------------------
# Getrennte Broker
# ---------------------------------------------------------------------------
def test_okx_und_etoro_sind_vollstaendig_getrennt(okx_topf, stufen, tmp_path):
    from risk_pots import RiskPot
    stufen.set_level("okx", "erhoeht", source="test", notify=False)
    etoro = RiskPot("etoro", state_datei=str(tmp_path / "risk_etoro.json"))
    assert okx_topf.einsatz()["level"] == "erhoeht"
    assert etoro.einsatz()["level"] == "", "eToro darf sich nicht mitverstellen"
    assert stufen.status("etoro")["chosen"] is False


def test_aktienpfad_folgt_der_etoro_stufe(stufen):
    import config
    import live_trader
    vorgabe = live_trader._einsatz_etoro("stock")
    assert vorgabe["risiko_pro_trade_pct"] == config.RISK_PER_TRADE_PCT
    assert vorgabe["max_position_pct"] == config.MAX_POSITION_PCT
    stufen.set_level("etoro", "vorsichtig", source="test", notify=False)
    gewaehlt = live_trader._einsatz_etoro("stock")
    assert gewaehlt["risiko_pro_trade_pct"] == 0.005
    assert gewaehlt["max_position_pct"] == 0.03
    assert gewaehlt["level"] == "vorsichtig"


def test_size_new_position_nimmt_den_uebergebenen_einsatz(stufen):
    from risk_manager import size_new_position
    klein = size_new_position(100000.0, 100.0, 90.0, max_capital_pct=1.0,
                              asset_type="stock", risk_pct=0.005)
    gross = size_new_position(100000.0, 100.0, 90.0, max_capital_pct=1.0,
                              asset_type="stock", risk_pct=0.02)
    assert gross == pytest.approx(klein * 4, rel=0.001)
    assert size_new_position(100000.0, 100.0, 90.0, asset_type="stock",
                             risk_pct=0.0) == 0.0


# ---------------------------------------------------------------------------
# Bedienung und Belege
# ---------------------------------------------------------------------------
def test_hoechste_stufe_nur_mit_ausdruecklicher_bestaetigung(stufen, monkeypatch):
    import webui.settings_store as store
    monkeypatch.setattr(store, "snapshot", lambda: {"ok": True})
    with pytest.raises(ValueError, match="EINSATZ ERHOEHEN"):
        store.set_risk_level("okx", "erhoeht")
    assert stufen.status("okx")["chosen"] is False, "Nichts darf gespeichert sein"
    store.set_risk_level("okx", "erhoeht", "EINSATZ ERHOEHEN")
    assert stufen.status("okx")["level"] == "erhoeht"
    store.set_risk_level("okx", "mittel")   # kleiner werden geht ohne Phrase
    assert stufen.status("okx")["level"] == "mittel"


def test_uebersicht_nennt_alle_drei_stufen_mit_beschreibung(stufen):
    overview = stufen.overview()
    for broker in ("okx", "etoro"):
        row = overview[broker]
        assert [o["level"] for o in row["options"]] == list(stufen.VALID_LEVELS)
        assert all(o["beschreibung"].strip() for o in row["options"])
        assert row["bezugsgroesse"].strip()
        assert row["wirksam"]["risiko_pro_trade_pct"] > 0
    assert "freies Guthaben" in overview["okx"]["bezugsgroesse"], (
        "Die OKX-Bezugsgroesse ist der haeufigste Irrtum und muss dastehen")


def test_wechsel_wird_mit_revision_und_verlauf_festgehalten(stufen):
    stufen.set_level("okx", "mittel", source="test", reason="Probe", notify=False)
    stufen.set_level("okx", "erhoeht", source="test", notify=False)
    zustand = json.loads(stufen.path().read_text(encoding="utf-8"))
    assert zustand["broker"]["okx"]["revision"] == 2
    assert [e["level"] for e in zustand["history"]] == ["mittel", "erhoeht"]
    assert zustand["history"][0]["reason"] == "Probe"


def test_klartext_nennt_wirksame_zahlen_und_bezugsgroesse(stufen):
    text = stufen.klartext("okx")
    assert "0.30 %" in text and "freies Guthaben" in text
    stufen.set_level("okx", "erhoeht", source="test", notify=False)
    assert "1.20 %" in stufen.klartext("okx")
