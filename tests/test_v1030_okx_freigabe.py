"""10.3.0: OKX-Freigabe-Fixes nach der Diagnose vom 17.09.2026.

Drei Befunde aus dem Pi-Lauf unter 10.2.2:
1. exposure_klassifizierung ordnete den kompletten OKX-Demo-Startbestand
   (1 BTC / 10 ETH) zwei offenen Staub-Restzeilen zu und sperrte alle Kaeufe.
2. risk_manager.resolve_unknown_result_at kannte die Composite-Methodenkennung
   der 10.2.1-EUR-Referenzbewertung nicht ("Ungueltiger Referenzbewertungsnachweis").
3. risk_result_recovery loggte dieselbe offene Zeile alle ~7 Sekunden als WARNING.
"""
import logging

import pytest

import exposure_klassifizierung as ek


# Exakte Zahlen vom Pi (Diagnose 2026-09-17 12:57 UTC).
BTC_SALDO = 1.00000000279
BTC_LEDGER_REST = 2.79e-09
ETH_SALDO = 10.000000102
ETH_LEDGER_REST = 1.02e-07
PREISE = {"BTC": 66309.0, "ETH": 2122.5}


def _klassifiziere_pi_fall():
    return ek.klassifiziere(
        {"BTC": {"gesamt": BTC_SALDO}, "ETH": {"gesamt": ETH_SALDO}},
        ledger_trades=[
            {"symbol": "BTC", "menge": BTC_LEDGER_REST, "reconciliation_status": "RESIDUAL_EXPOSURE"},
            {"symbol": "ETH", "menge": ETH_LEDGER_REST, "reconciliation_status": "RESIDUAL_EXPOSURE"},
        ],
        preise=PREISE)


def test_demo_startbestand_ueber_staubrest_sperrt_keine_kaeufe():
    ergebnis = _klassifiziere_pi_fall()
    assert ergebnis["einstiege_gesperrt"] is False
    assert not ergebnis["sperrgruende"]


def test_demo_startbestand_wird_als_konto_asset_getrennt_ausgewiesen():
    ergebnis = _klassifiziere_pi_fall()
    frei = {(b["waehrung"], round(b["menge"], 12)): b
            for b in ergebnis["nach_klasse"].get(ek.ACCOUNT_ASSET, [])}
    ueberhang_btc = frei[("BTC", round(BTC_SALDO - BTC_LEDGER_REST, 12))]
    assert "ausserhalb der offenen Ledger-Restmenge" in ueberhang_btc["begruendung"]
    staub_btc = frei[("BTC", round(BTC_LEDGER_REST, 12))]
    assert "Staubrest" in staub_btc["begruendung"]
    assert staub_btc["zusatz"]["ledger_menge"] == round(BTC_LEDGER_REST, 12)
    ueberhang_eth = frei[("ETH", round(ETH_SALDO - ETH_LEDGER_REST, 12))]
    assert "ausserhalb der offenen Ledger-Restmenge" in ueberhang_eth["begruendung"]
    staub_eth = frei[("ETH", round(ETH_LEDGER_REST, 12))]
    assert "Staubrest" in staub_eth["begruendung"]


def test_signifikanter_ledger_rest_sperrt_weiterhin_nur_die_gebundene_menge():
    ergebnis = ek.klassifiziere(
        {"BTC": {"gesamt": 1.5}},
        ledger_trades=[{"symbol": "BTC", "menge": 0.5}],
        preise={"BTC": 60000.0})
    assert ergebnis["einstiege_gesperrt"] is True
    (sperre,) = [b for b in ergebnis["bestaende"] if b["sperrt_einstiege"]]
    assert sperre["waehrung"] == "BTC" and sperre["menge"] == pytest.approx(0.5)
    assert sperre["klasse"] == ek.RESIDUAL
    frei = ergebnis["nach_klasse"].get(ek.ACCOUNT_ASSET, [])
    assert any(b["menge"] == pytest.approx(1.0) for b in frei)


def test_weniger_saldo_als_ledger_rest_bleibt_gesperrt():
    ergebnis = ek.klassifiziere(
        {"BTC": {"gesamt": 0.2}},
        ledger_trades=[{"symbol": "BTC", "menge": 0.5}],
        preise={"BTC": 60000.0})
    assert ergebnis["einstiege_gesperrt"] is True
    (sperre,) = [b for b in ergebnis["bestaende"] if b["sperrt_einstiege"]]
    assert sperre["menge"] == pytest.approx(0.2)


def test_ohne_kursbeleg_gibt_es_keine_staub_entwarnung():
    ergebnis = ek.klassifiziere(
        {"BTC": {"gesamt": BTC_SALDO}},
        ledger_trades=[{"symbol": "BTC", "menge": BTC_LEDGER_REST}],
        preise={})
    assert ergebnis["einstiege_gesperrt"] is True
    (sperre,) = [b for b in ergebnis["bestaende"] if b["sperrt_einstiege"]]
    assert sperre["menge"] == pytest.approx(BTC_LEDGER_REST)


def test_geschlossene_und_externe_ledgerzeilen_binden_weiterhin_nichts():
    ergebnis = ek.klassifiziere(
        {"BTC": {"gesamt": 1.0}},
        ledger_trades=[{"symbol": "BTC", "menge": 1.0, "reconciliation_status": "CLOSED"}],
        preise={"BTC": 60000.0})
    assert ergebnis["einstiege_gesperrt"] is False


def _composite_evidence(receipt_hash="a" * 64, method="EUR_REFERENCE_CASHFLOWS_COMPOSITE_V1"):
    return {"source": "okx_reference_valuation", "receipt_hash": receipt_hash,
            "method": method, "currency": "EUR", "quality": "REFERENCE_VALUATION"}


def test_risk_manager_akzeptiert_composite_referenzbewertung():
    from risk_manager import RiskState
    state = RiskState()
    state.register_unknown_pnl_trade("ledger:73")
    assert state.resolve_unknown_result_at(
        "ledger:73", 298.7258766336985, 302.0, 100000.0,
        "2026-09-16T21:22:00+00:00", evidence=_composite_evidence())
    receipt = state.realized_receipts["ledger:73"]
    assert receipt["status"] == "CONFIRMED"
    assert receipt["valuation_evidence"]["method"] == "EUR_REFERENCE_CASHFLOWS_COMPOSITE_V1"


def test_risk_manager_akzeptiert_weiterhin_die_einfache_methode():
    from risk_manager import RiskState
    state = RiskState()
    state.register_unknown_pnl_trade("ledger:1")
    assert state.resolve_unknown_result_at(
        "ledger:1", -10.0, -9.0, 1000.0, "2026-09-10T08:00:00Z",
        evidence=_composite_evidence(method="EUR_REFERENCE_CASHFLOWS_V1"))


def test_risk_manager_verwirft_fremde_methodenkennungen():
    from risk_manager import RiskState
    state = RiskState()
    state.register_unknown_pnl_trade("ledger:2")
    with pytest.raises(ValueError, match="Ungueltiger Referenzbewertungsnachweis"):
        state.resolve_unknown_result_at(
            "ledger:2", 1.0, 1.0, 1000.0, "2026-09-10T08:00:00Z",
            evidence=_composite_evidence(method="EUR_REFERENCE_CASHFLOWS_V2"))


def test_offene_abgleiche_werden_nur_alle_zehn_minuten_als_warnung_geloggt(monkeypatch, caplog):
    import risk_result_recovery as rr
    import trade_ledger as tl

    class Broker:
        name = "okx"
        demo = True

        def account_fingerprint(self):
            return "244895ca0a404f80142b79f5"

        def kontowaehrung(self):
            return "USDC"

    class State:
        realized_receipts = {"ledger:73": {"status": "UNKNOWN"}}

        def refresh(self):
            pass

        def register_unknown_pnl_at(self, *a, **k):
            pass

    monkeypatch.setattr("okx_accounting.sync_unknown_results", lambda *a, **k: None)

    def kaputt(_tid):
        raise RuntimeError("Belegkette unterbrochen")

    monkeypatch.setattr(tl, "trade_detail", kaputt)
    rr._LAST.clear()
    with caplog.at_level(logging.DEBUG, logger="risk_result_recovery"):
        assert rr.reconcile(State(), Broker(), account_equity=1000.0) == []
        assert rr.reconcile(State(), Broker(), account_equity=1000.0) == []
    offen = [r for r in caplog.records if "bleibt offen" in r.getMessage()]
    assert len(offen) == 2
    assert [r.levelno for r in offen] == [logging.WARNING, logging.DEBUG]
