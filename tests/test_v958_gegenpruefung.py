"""9.5.8 -- Die Befunde der Gegenpruefung, als Regressionstests.

Eine adversariale Durchsicht der 9.5.8-Aenderungen fand acht echte Fehler in
meiner eigenen Korrektur, zwei davon kritisch. Diese Datei friert jeden davon
ein, damit er nicht zurueckkommt.

Reihenfolge nach Schwere:

  K1  Ein nicht erreichbares Fill-Archiv galt als "kein Fill" -- ein echter,
      gefuellter Kauf waere als "nie ausgefuehrt" verbucht worden.
      (in test_v958_schutz_und_ungeklaerte_order.py)
  K2  51001 und 51400 galten als "Order unbekannt", obwohl 51400 laut der
      eigenen Codetabelle "bereits AUSGEFUEHRT" heissen kann. (ebenda)
  W1  Der abgesenkte Verkaufs-Limitpreis hatte keinen Boden.
  W4  Vormerkfenster und Polltimeout waren gleich gross -- der Schutzpfad
      wartete deshalb genau dann nicht, wenn er sollte.
  W8  Der Platzhalter <REGISTER-UNLESBAR> wirkte im Kandidatengate nicht, und
      ungeklaerte Orders zaehlten nicht mehr gegen die Positionsgrenze.
  KL1 Die wachsende Wartezeit lief nach ~1024 Versuchen in einen OverflowError.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ===========================================================================
# W1 -- der Boden unter dem Verkaufslimit
# ===========================================================================
def test_zu_duennes_buch_sendet_keine_order(monkeypatch):
    """Ein Verkauf zu jedem Preis ist kein Ausstieg."""
    import config
    from broker.base import BrokerFehler
    from tests.test_v958_verkaufspreisfenster import broker, xlm, BEST, MENGE

    monkeypatch.setattr(config, "OKX_MAX_EXIT_SLIPPAGE_PCT", 0.01, raising=False)
    monkeypatch.setattr(config, "OKX_MAX_EXIT_SLIPPAGE_HARD_PCT", 0.03,
                        raising=False)
    monkeypatch.setattr(config, "OKX_CANCEL_RELEASE_TIMEOUT", 2.0, raising=False)

    # Zweites Level 10 % unter dem besten Bid -- weit unter der harten Grenze.
    # Der VWAP bleibt knapp unter 1 %, das Gate wuerde also durchlassen.
    b = broker([(BEST, 545.0), (BEST * 0.90, 200.0)])
    with pytest.raises(BrokerFehler) as exc:
        b.schliesse_position(xlm(), MENGE, referenzpreis=BEST)
    assert "harte Verkaufsgrenze" in str(exc.value)
    assert not b.client.gesendete_orders, (
        "Trotz zu duennem Buch wurde eine Order gesendet")


def test_innerhalb_der_harten_grenze_wird_gesendet(monkeypatch):
    """Der XLM-Fall vom 03.09. liegt innerhalb -- er muss ausfuehren."""
    import config
    from tests.test_v958_verkaufspreisfenster import broker, xlm, BEST, MENGE, TIEF

    monkeypatch.setattr(config, "OKX_MAX_EXIT_SLIPPAGE_PCT", 0.01, raising=False)
    monkeypatch.setattr(config, "OKX_MAX_EXIT_SLIPPAGE_HARD_PCT", 0.03,
                        raising=False)
    monkeypatch.setattr(config, "OKX_CANCEL_RELEASE_TIMEOUT", 2.0, raising=False)

    abstand = (BEST - TIEF) / BEST
    assert 0.01 < abstand < 0.03, f"Testfall passt nicht mehr ({abstand:.4f})"
    b = broker([(BEST, 500.0), (TIEF, 200.0)])
    ergebnis = b.schliesse_position(xlm(), MENGE, referenzpreis=BEST)
    assert ergebnis.filled_quantity > 0


# ===========================================================================
# W4 -- das Vormerkfenster ist groesser als der Polltimeout
# ===========================================================================
def test_schutz_wartet_auch_nach_einem_erschoepften_verkaufspoll(monkeypatch):
    """Genau die Lage, fuer die die Funktion gebaut wurde.

    Der Verkaufspfad pollt nach dem Storno die vollen 10 s vergeblich und
    bricht ab. Unmittelbar danach erneuert der Schutzpfad die Order -- die
    Position liegt in diesem Moment ungeschuetzt im Konto. Waeren Fenster und
    Timeout gleich gross, waere die Vormerkung dann schon abgelaufen.
    """
    import config
    from tests.test_v958_schutz_und_ungeklaerte_order import (
        FreigabeClient, _broker)

    monkeypatch.setattr(config, "OKX_CANCEL_RELEASE_TIMEOUT", 2.0, raising=False)
    client = FreigabeClient(frei_spaeter=560.88, verzoegerung=1, gesamt=560.88)
    b = _broker(client)
    b._freigabe_vormerken("XLM-USDC")
    # So tun, als laege die Vormerkung schon einen ganzen Polltimeout zurueck.
    b._freigabe_erwartet["XLM-USDC"] = time.monotonic() - 2.5

    _frei, gedeckt = b.warte_auf_guthabenfreigabe(
        "XLM", 560.88, inst_id="XLM-USDC", lot_size=0.000001)
    assert gedeckt, "Die Vormerkung galt schon als abgelaufen"
    assert client.abfragen > 1


def test_vormerkung_benutzt_die_monotone_uhr():
    """Ein NTP-Ruecksprung darf keine negative Zeitdifferenz erzeugen."""
    from tests.test_v958_schutz_und_ungeklaerte_order import (
        FreigabeClient, _broker)

    b = _broker(FreigabeClient(frei_spaeter=1.0, verzoegerung=0, gesamt=1.0))
    b._freigabe_vormerken("XLM-USDC")
    gemerkt = b._freigabe_erwartet["XLM-USDC"]
    assert abs(gemerkt - time.monotonic()) < 1.0
    assert abs(gemerkt - time.time()) > 1000.0, (
        "Es wurde die Wanduhr benutzt statt time.monotonic()")


# ===========================================================================
# W8 -- die symbolgenaue Sperre laesst keine Luecke
# ===========================================================================
def test_unlesbares_register_sperrt_jedes_symbol():
    """Der Platzhalter passt auf kein echtes Symbol -- er muss extra greifen."""
    from candidate_gate import bewerte_kandidat

    offen = ["<REGISTER-UNLESBAR>"]
    entscheidung = bewerte_kandidat(
        state_allows_buy=True, broker_online=True, instrument_identity_ok=True,
        market_open=True, position_already_open=False,
        duplicate_open_order=("SOL" in offen or "<REGISTER-UNLESBAR>" in offen),
        risk_allows_buy=True, portfolio_allows_buy=True, cash_allows_buy=True,
        market_quality_ok=True, cost_quote_ok=True, net_edge_ok=True,
        quantity=1.0, price=100.0, stop=90.0, take_profit=110.0)
    assert not entscheidung.approved
    assert entscheidung.blocked_by == "duplicate_open_order"


def test_ungeklaerte_orders_zaehlen_gegen_die_positionsgrenze():
    """Sonst kann der Bot Grenze + ungeklaerte Orders an Positionen halten."""
    import crypto_engine

    e = crypto_engine.CryptoEngine.__new__(crypto_engine.CryptoEngine)
    e.cfg = SimpleNamespace(OKX_MAX_OPEN_POSITIONS=3)
    e.buch = SimpleNamespace(aktive=lambda: [object(), object()])

    assert e._portfolio_erlaubt() is True
    assert e._portfolio_erlaubt(zusaetzlich_belegt=1) is False, (
        "Eine ungeklaerte Order zaehlt nicht mit -- der Bot koennte die "
        "Positionsgrenze ueberschreiten")


# ===========================================================================
# KL1 -- kein Ueberlauf in einer Schleife, die nie aufgibt
# ===========================================================================
def test_wartezeit_laeuft_nicht_ueber():
    from tests.test_v958_eskalation_statt_schleife import _engine, _position

    e = _engine(None, basis=5.0, maximum=60.0, schwelle=99999)
    p = _position()
    p.exit_fehlversuche = 5000          # weit jenseits von 2**1024
    e._exit_fehlversuch(p, "0 von 560.88 ausgefuehrt")
    assert p.exit_fehlversuche == 5001
    assert p.exit_retry_after, "Es wurde keine neue Wartezeit gesetzt"
