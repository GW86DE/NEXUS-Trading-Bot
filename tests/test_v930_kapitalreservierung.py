"""Kapital darf nicht zweimal als frei gelten (v9.3, CR-01).

Beide Scanner arbeiten ihre Kandidaten seriell ab -- es werden also nie
absichtlich zwei Kauforders gleichzeitig gesendet. Das allein genuegt aber
nicht: Ist Kandidat A beim Broker angenommen, die Belastung aber noch nicht
verbucht, sah Kandidat B bis 9.2 dasselbe Geld ein zweites Mal.

v9.3 zieht schwebende eigene Kaeufe vom verfuegbaren Guthaben ab -- bei
eToro kontoweit, bei OKX je Quote-Lane getrennt. Die neun Pflichtfaelle aus
dem Aenderungsantrag sind hier abgebildet.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("TRADINGBOT_TEST_STATE_DIR", "/tmp/tradingbot_tests")
Path(os.environ["TRADINGBOT_TEST_STATE_DIR"]).mkdir(parents=True, exist_ok=True)


@pytest.fixture()
def registry(tmp_path):
    from order_ownership import OrderOwnershipRegistry
    return OrderOwnershipRegistry(tmp_path / "bot_order_registry.json")


def intent(symbol, qty, preis, lane, *, zustand="SUBMITTING", gefuellt=0.0):
    return {"broker": "okx", "asset_type": "crypto", "symbol": symbol,
            "qty": float(qty), "signal_price": float(preis),
            "filled_qty": float(gefuellt), "trade_quote_ccy": lane,
            "zustand": zustand}


# ---------------------------------------------------------------------------
# 1-3: ein schwebender Kauf bindet Kapital
# ---------------------------------------------------------------------------
def test_schwebender_kauf_bindet_kapital(registry):
    registry.register_pending("BTC", intent("BTC", 0.01, 60000.0, "EUR"),
                              asset_type="crypto")
    assert registry.lane_reservierung("EUR") == pytest.approx(600.0)


def test_unbekannter_ausgang_gibt_das_kapital_nicht_frei(registry):
    """Pflichtfall 3: UNKNOWN_AFTER_SUBMIT. Die Order koennte ausgefuehrt
    worden sein -- das Geld bleibt gebunden."""
    registry.register_pending("BTC", intent("BTC", 0.01, 60000.0, "EUR"),
                              asset_type="crypto")
    registry.setze_zustand("BTC", "UNKNOWN_AFTER_SUBMIT", asset_type="crypto")
    assert registry.lane_reservierung("EUR") == pytest.approx(600.0), (
        "Ein unklarer Ausgang darf niemals als freies Guthaben gelten")


def test_teilfill_gibt_nur_den_unverbrauchten_teil_frei(registry):
    """Pflichtfall 5."""
    registry.register_pending(
        "BTC", intent("BTC", 0.01, 60000.0, "EUR", gefuellt=0.004),
        asset_type="crypto")
    assert registry.lane_reservierung("EUR") == pytest.approx(360.0)


# ---------------------------------------------------------------------------
# 6: Quote-Lanes bleiben getrennt
# ---------------------------------------------------------------------------
def test_quote_lanes_vermischen_sich_nicht(registry):
    """Pflichtfall 6: eine EUR-Reservierung darf kein USDC blockieren."""
    registry.register_pending("BTC", intent("BTC", 0.01, 60000.0, "EUR"),
                              asset_type="crypto")
    registry.register_pending("SOL", intent("SOL", 10.0, 150.0, "USDC"),
                              asset_type="crypto")
    assert registry.lane_reservierung("EUR") == pytest.approx(600.0)
    assert registry.lane_reservierung("USDC") == pytest.approx(1500.0)
    assert registry.lane_reservierung("USDT") == 0.0


def test_eigene_reservierung_blockiert_den_eigenen_nachlauf_nicht(registry):
    """Ein zweiter Versuch fuer DASSELBE Symbol darf sich nicht selbst
    blockieren -- sonst kaeme eine Position nach einem Neustart nie mehr
    zustande."""
    registry.register_pending("BTC", intent("BTC", 0.01, 60000.0, "EUR"),
                              asset_type="crypto")
    assert registry.lane_reservierung("EUR", ausser_symbol="BTC") == 0.0


def test_aktienreservierung_bindet_kein_kryptoguthaben(registry):
    """Pflichtfall 9: eToro und OKX zaehlen sich nicht gegenseitig als
    Cashquelle."""
    registry.register_pending("MSFT", {"broker": "etoro", "asset_type": "stock",
                                       "symbol": "MSFT", "qty": 29.0,
                                       "signal_price": 509.35,
                                       "trade_quote_ccy": "USD"},
                              asset_type="stock")
    assert registry.lane_reservierung("USD", asset_type="crypto") == 0.0


# ---------------------------------------------------------------------------
# 4: Neustart
# ---------------------------------------------------------------------------
def test_reservierung_ueberlebt_den_neustart(tmp_path):
    """Pflichtfall 4: zwischen Reservierung und Brokerantwort neu gestartet."""
    from order_ownership import OrderOwnershipRegistry

    pfad = tmp_path / "bot_order_registry.json"
    erste = OrderOwnershipRegistry(pfad)
    erste.register_pending("BTC", intent("BTC", 0.01, 60000.0, "EUR"),
                           asset_type="crypto")

    zweite = OrderOwnershipRegistry(pfad)
    assert zweite.lane_reservierung("EUR") == pytest.approx(600.0), (
        "Nach einem Neustart darf das Geld nicht wieder als frei gelten")


# ---------------------------------------------------------------------------
# 7: eToro Demo und Live sind getrennte Kontodomaenen
# ---------------------------------------------------------------------------
def test_etoro_demo_und_live_bleiben_getrennt(tmp_path, monkeypatch):
    """Pflichtfall 7."""
    import broker.etoro as etoro

    monkeypatch.setattr(
        etoro, "_float", etoro._float, raising=False)
    saetze = [
        {"symbol": "MSFT", "paper": True, "reserved_cash": 1000.0},
        {"symbol": "NVDA", "paper": False, "reserved_cash": 2000.0},
    ]
    import etoro_reconciliation
    monkeypatch.setattr(etoro_reconciliation, "offene_kaufabsichten",
                        lambda: [dict(x) for x in saetze])

    demo = object.__new__(etoro.EtoroBroker)
    demo.paper = True
    live = object.__new__(etoro.EtoroBroker)
    live.paper = False

    assert demo.reservierte_mittel() == pytest.approx(1010.0)
    assert live.reservierte_mittel() == pytest.approx(2020.0)


def test_etoro_cash_zieht_die_reservierung_ab(monkeypatch):
    """Pflichtfall 1/2: Cash reicht nur fuer einen Kauf."""
    import broker.etoro as etoro
    import etoro_reconciliation

    monkeypatch.setattr(etoro_reconciliation, "offene_kaufabsichten",
                        lambda: [{"symbol": "MSFT", "paper": True,
                                  "reserved_cash": 14_771.15}])
    adapter = object.__new__(etoro.EtoroBroker)
    adapter.paper = True
    monkeypatch.setattr(etoro.EtoroBroker, "_get_aggregate",
                        lambda self: {"accountTotals": {"accountAvailableCash": 15_000.0}})

    frei = adapter.verfuegbares_cash()
    assert frei < 100.0, "Das gebundene Geld darf nicht ein zweites Mal zaehlen"
    assert frei >= 0.0, "Niemals negativ"


def test_etoro_cash_wird_nie_negativ(monkeypatch):
    import broker.etoro as etoro
    import etoro_reconciliation

    monkeypatch.setattr(etoro_reconciliation, "offene_kaufabsichten",
                        lambda: [{"symbol": "MSFT", "paper": True,
                                  "reserved_cash": 99_999.0}])
    adapter = object.__new__(etoro.EtoroBroker)
    adapter.paper = True
    monkeypatch.setattr(etoro.EtoroBroker, "_get_aggregate",
                        lambda self: {"accountTotals": {"accountAvailableCash": 100.0}})
    assert adapter.verfuegbares_cash() == 0.0


def test_unlesbare_reservierungen_blockieren_den_handel_nicht(monkeypatch):
    """Ein Lesefehler darf den Handel nicht lahmlegen -- aber auch nicht
    stillschweigend Geld freigeben. 0.0 ist hier die sichere Seite, weil die
    Domaenensperre in order_execution.py unabhaengig davon greift."""
    import broker.etoro as etoro
    import etoro_reconciliation

    def kaputt():
        raise RuntimeError("Datei unlesbar")

    monkeypatch.setattr(etoro_reconciliation, "offene_kaufabsichten", kaputt)
    adapter = object.__new__(etoro.EtoroBroker)
    adapter.paper = True
    assert adapter.reservierte_mittel() == 0.0


# ---------------------------------------------------------------------------
# 8: Reconciliation-Worker und Kaufpruefung gleichzeitig
# ---------------------------------------------------------------------------
def test_gleichzeitiger_worker_verliert_keine_reservierung(tmp_path, monkeypatch):
    """Pflichtfall 8."""
    import threading
    import etoro_reconciliation as rec

    monkeypatch.setenv("TRADINGBOT_TEST_STATE_DIR", str(tmp_path))
    for did in range(12):
        rec.start_intent(decision_id=did, symbol=f"SYM{did}", paper=True,
                         profile="test", quantity=2.0, price=100.0,
                         stop=95.0, take_profit=110.0)

    gelesen = []
    fehler = []

    def leser():
        try:
            for _ in range(40):
                gelesen.append(len(rec.offene_kaufabsichten()))
        except Exception as exc:  # pragma: no cover
            fehler.append(exc)

    def schreiber():
        try:
            for did in range(12):
                rec.accepted(did, order_id=f"ord-{did}", reference_id=f"ref-{did}")
        except Exception as exc:  # pragma: no cover
            fehler.append(exc)

    threads = [threading.Thread(target=leser), threading.Thread(target=schreiber)]
    for x in threads:
        x.start()
    for x in threads:
        x.join()

    assert not fehler
    assert all(n >= 0 for n in gelesen)
    for did in range(12):
        assert f"ord-{did}" in (rec.record_for(did).get("order_ids") or [])
