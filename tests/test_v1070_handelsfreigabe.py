"""10.7.0 Teile A und B -- Sperren mit Reichweite und Ablauf, eine Sperrliste.

ENTSCHEIDUNG GEORG (18.09.2026)
===============================
"Ja, baue es so." -- Ein unbekanntes Verkaufsergebnis sperrt nur noch am
Handelstag des Verkaufs. Ein ungeklaerter Bestand sperrt nur den betroffenen
Coin. Domaenenweit bleiben Kontowechsel, nicht persistierbarer Risikozustand
und unlesbares Ledger. Jede Sperre traegt Grund, Reichweite, Ablauf und
Aufloesung, und die Oberflaeche zeigt die Liste.

Bis 10.6.0 sperrte jede Luecke die ganze Domaene, unbegrenzt: neun
Sperrfaelle in zehn Tagen, darunter ein Gebuehrenloch vom 08.09., das am
18.09. den Handel anhielt, und drei Lot-Reste, die nach einem sauberen
Verkauf jeden OKX-Kauf blockierten.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

import okx_accounting as buchung
from handelsfreigabe import (BELEG, DOMAIN, REPARATUR, SYMBOL, TAGESRESET, ZEIT,
                             etoro_sperren, okx_sperren, zusammenfassung)

ROOT = Path(__file__).resolve().parents[1]
HEUTE = date(2026, 9, 18)


# ---------------------------------------------------------------------------
# classify_gaps: Reichweite und Ablauf je Beleg
# ---------------------------------------------------------------------------
def test_bestandsbelege_sperren_nur_ihren_coin():
    gaps = [dict(trade_id=81, symbol='XRP-EUR', kind='BALANCE_REDUCTION'),
            dict(trade_id=82, symbol='BTC-EUR', kind='BROKER_STATE_UNKNOWN'),
            dict(trade_id=85, symbol='XRP', kind='RESIDUAL_PROOF_MISSING'),
            dict(trade_id=None, symbol='ETH-EUR', kind='LEDGER_ANCHOR_MISSING')]
    z = buchung.classify_gaps(gaps, today=HEUTE)
    assert z['complete'] is True, "Kein einziger Beleg sperrt die Domaene"
    assert z['blocked_symbols'] == ['BTC', 'ETH', 'XRP']
    assert all(g['scope'] == SYMBOL and g['ablauf'] == BELEG and g['sperrt'] for g in z['gaps'])
    assert all(g['aufloesung'] for g in z['gaps'])
    assert 'alle anderen Kaeufe frei' in z['detail']


def test_ergebnis_von_heute_sperrt_die_domaene_bis_zum_tagesreset():
    gaps = [dict(trade_id=88, symbol='CSCO', kind='RESULT', closed_at='2026-09-18T14:15:12.633000+00:00')]
    z = buchung.classify_gaps(gaps, today=HEUTE)
    assert z['complete'] is False and z['blocked_symbols'] == []
    assert z['gaps'][0]['scope'] == DOMAIN and z['gaps'][0]['ablauf'] == TAGESRESET


def test_ergebnis_von_gestern_sperrt_nicht_mehr_bleibt_aber_offen():
    """Das Gebuehrenloch von TXN (08.09.) und AMD (10.09.) hielt am 18.09. den Handel an."""
    gaps = [dict(trade_id=50, symbol='TXN', kind='RESULT', closed_at='2026-09-08T16:40:51.123000+00:00'),
            dict(trade_id=53, symbol='AMD', kind='RESULT', closed_at='2026-09-10T01:22:05.003000+00:00')]
    z = buchung.classify_gaps(gaps, today=HEUTE)
    assert z['complete'] is True and z['blocked_symbols'] == []
    assert [g['trade_id'] for g in z['expired']] == [50, 53]
    assert all(g['sperrt'] is False for g in z['gaps']), "Offen bleibt offen -- nur die Sperre endet"
    assert 'nicht mehr sperrend' in z['detail']


def test_ergebnis_ohne_zeitpunkt_sperrt_vorsichtshalber():
    z = buchung.classify_gaps([dict(trade_id=1, symbol='X', kind='RESULT', closed_at='')], today=HEUTE)
    assert z['complete'] is False


def test_geld_ohne_kontozuordnung_bleibt_domaenenweit():
    z = buchung.classify_gaps([dict(trade_id=2, symbol='SOL-USDC', kind='ACCOUNT_UNKNOWN')], today=HEUTE)
    assert z['complete'] is False and z['gaps'][0]['scope'] == DOMAIN and z['gaps'][0]['ablauf'] == BELEG


def test_unbekannte_belegart_sperrt_vorsichtshalber_die_domaene():
    z = buchung.classify_gaps([dict(trade_id=3, symbol='SOL', kind='NEUE_ART')], today=HEUTE)
    assert z['complete'] is False


def test_tageswechsel_berlin_entscheidet():
    """Verkauf 23:30 UTC = 01:30 Berlin am Folgetag -> zaehlt zum Folgetag."""
    gaps = [dict(trade_id=4, symbol='X', kind='RESULT', closed_at='2026-09-17T23:30:00+00:00')]
    assert buchung.classify_gaps(gaps, today=date(2026, 9, 18))['complete'] is False
    assert buchung.classify_gaps(gaps, today=date(2026, 9, 19))['complete'] is True


# ---------------------------------------------------------------------------
# require_tradable: Kaufpfad je Coin
# ---------------------------------------------------------------------------
def test_require_tradable_sperrt_den_coin_und_laesst_andere_frei(monkeypatch):
    from broker.base import BrokerFehler
    zustand = buchung.classify_gaps([dict(trade_id=81, symbol='XRP-EUR', kind='BALANCE_REDUCTION')], today=HEUTE)
    monkeypatch.setattr(buchung, 'status_on_connection', lambda con, a, e: zustand)
    buchung.require_tradable(None, 'A', 'DEMO', 'BTC-EUR')
    buchung.require_tradable(None, 'A', 'DEMO', 'ETH-USDC')
    with pytest.raises(BrokerFehler, match='XRP'):
        buchung.require_tradable(None, 'A', 'DEMO', 'XRP-EUR')
    with pytest.raises(BrokerFehler, match='XRP'):
        buchung.require_tradable(None, 'A', 'DEMO', 'XRP-USDC')


def test_require_tradable_bei_domaenensperre_sperrt_alles(monkeypatch):
    from broker.base import BrokerFehler
    zustand = buchung.classify_gaps([dict(trade_id=2, symbol='SOL', kind='ACCOUNT_UNKNOWN')], today=HEUTE)
    monkeypatch.setattr(buchung, 'status_on_connection', lambda con, a, e: zustand)
    with pytest.raises(BrokerFehler):
        buchung.require_tradable(None, 'A', 'DEMO', 'BTC-EUR')


# ---------------------------------------------------------------------------
# Sperrliste: vier Pflichtangaben je Sperre
# ---------------------------------------------------------------------------
def test_okx_sperrliste_traegt_vier_angaben():
    topf = SimpleNamespace(darf_kaufen=lambda: (False, 'okx: Tagesverlustgrenze erreicht'))
    accounting = buchung.classify_gaps([dict(trade_id=81, symbol='XRP-EUR', kind='BALANCE_REDUCTION')], today=HEUTE)
    guard = {'valid': True, 'detail': '', 'missing': ['ETH'], 'exit_in_progress': [{'symbol': 'DOGE'}]}
    sperren = okx_sperren(topf, None, guard=guard, accounting=accounting)
    for s in sperren:
        assert s.grund and s.reichweite in (SYMBOL, DOMAIN) and s.ablauf in (TAGESRESET, BELEG, ZEIT, REPARATUR)
        assert s.aufloesung
    nach_grund = {s.grund: s for s in sperren}
    assert nach_grund['TAGESVERLUSTGRENZE'].reichweite == DOMAIN and nach_grund['TAGESVERLUSTGRENZE'].ablauf == TAGESRESET
    assert nach_grund['BALANCE_REDUCTION'].symbol == 'XRP' and nach_grund['BALANCE_REDUCTION'].reichweite == SYMBOL
    assert nach_grund['BESTAND_FEHLT_UNBESTAETIGT'].symbol == 'ETH'
    assert nach_grund['EXIT_IN_PROGRESS'].symbol == 'DOGE'
    z = zusammenfassung(sperren)
    assert z['domaene_gesperrt'] is True and z['gesperrte_symbole'] == ['DOGE', 'ETH', 'XRP']
    assert len(z['sperren']) == 4 and all({'grund', 'reichweite', 'ablauf', 'aufloesung'} <= set(d) for d in z['sperren'])


def test_okx_sperrliste_ohne_sperren_ist_leer():
    topf = SimpleNamespace(darf_kaufen=lambda: (True, ''))
    accounting = buchung.classify_gaps([], today=HEUTE)
    z = zusammenfassung(okx_sperren(topf, None, guard={'valid': True, 'missing': [], 'exit_in_progress': []},
                                    accounting=accounting))
    assert z == {'domaene_gesperrt': False, 'gesperrte_symbole': [], 'anzahl': 0, 'sperren': []}


def test_abgelaufene_ergebnisbelege_stehen_nicht_in_der_sperrliste():
    topf = SimpleNamespace(darf_kaufen=lambda: (True, ''))
    accounting = buchung.classify_gaps(
        [dict(trade_id=50, symbol='TXN', kind='RESULT', closed_at='2026-09-08T16:40:51+00:00')], today=HEUTE)
    assert okx_sperren(topf, None, guard=None, accounting=accounting) == []


@pytest.mark.parametrize('text,grund,reichweite,ablauf', [
    ('Ergebnisabgleich ausstehend: 1 Verkaufsergebnis(se) ...', 'ERGEBNIS_OFFEN_HEUTE', DOMAIN, TAGESRESET),
    ('Equity-Tagesbremse aktiv: -2.60 % seit Tagesbeginn', 'EQUITY_BREMSE', DOMAIN, TAGESRESET),
    ('Verlustserien-Cooldown aktiv bis 2026-09-18T20:00', 'VERLUSTSERIE_PAUSE', DOMAIN, ZEIT),
    ('RISK_PERSISTENCE_UNCONFIRMED: Risikozustand nicht bestaetigt; ...', 'RISIKOZUSTAND_UNBESTAETIGT', DOMAIN, REPARATUR),
    ('Positionslimit erreicht (offen=8, Limit=8)', 'POSITIONSLIMIT', DOMAIN, BELEG),
    ('Maximale Trades pro Tag erreicht (12/12)', 'TRADES_TAGESLIMIT', DOMAIN, TAGESRESET),
    ('irgendein neuer Grund', 'RISIKO_SONSTIGES', DOMAIN, BELEG),
])
def test_risikogruende_werden_klassifiziert(text, grund, reichweite, ablauf, tmp_path, monkeypatch):
    monkeypatch.setenv('TRADINGBOT_TEST_STATE_DIR', str(tmp_path))
    import risk_manager
    monkeypatch.setattr(risk_manager, 'kaufsperre_grund', lambda risk: text)
    import etoro_reconciliation
    monkeypatch.setattr(etoro_reconciliation, 'buchungsluecken', lambda domain='': [])
    sperren = etoro_sperren(SimpleNamespace())
    assert [(s.grund, s.reichweite, s.ablauf) for s in sperren] == [(grund, reichweite, ablauf)]
    assert sperren[0].detail == text


def test_etoro_buchungsabgleich_erscheint_als_tagesreset_sperre(tmp_path, monkeypatch):
    """Verknuepfter Verkauf mit noch unbeziffertem Ergebnis: Tagesregel."""
    monkeypatch.setenv('TRADINGBOT_TEST_STATE_DIR', str(tmp_path))
    import etoro_reconciliation
    monkeypatch.setattr(etoro_reconciliation, 'buchungsluecken', lambda domain='': [
        {'symbol': 'CSCO', 'status': 'RESOLVED', 'blocks_entries': True, 'trade_ids': [88],
         'detail': 'Konto, Umgebung, Position ... bestaetigt; aktuelles Ergebnis/Gebuehren noch unvollstaendig'}])
    sperren = etoro_sperren(None)
    assert len(sperren) == 1 and sperren[0].grund == 'PNL_INCOMPLETE' and sperren[0].symbol == 'CSCO'
    assert sperren[0].reichweite == DOMAIN and sperren[0].ablauf == TAGESRESET
    assert 'Erwartungswert' in sperren[0].aufloesung


def test_etoro_offener_ledgerabgleich_endet_nicht_mit_dem_handelstag(tmp_path, monkeypatch):
    """10.7.1: CSCO am 18.09.2026 -- der Abgleich schlug 324x fehl ('Einstiegs-
    Fillregister enthaelt widersprechenden Beleg'). So ein Fall ist ACTIVE, nicht
    RESOLVED, und faellt NICHT unter die Tagesregel; er braucht die Reparatur.
    10.7.0 zeigte trotzdem 'endet durch TAGESRESET'."""
    monkeypatch.setenv('TRADINGBOT_TEST_STATE_DIR', str(tmp_path))
    import etoro_reconciliation
    monkeypatch.setattr(etoro_reconciliation, 'buchungsluecken', lambda domain='': [
        {'symbol': 'CSCO', 'status': 'ACTIVE', 'blocks_entries': True, 'trade_ids': [],
         'detail': 'Ledgerabgleich noch nicht erfolgreich'},
        {'symbol': 'AMD', 'status': 'RESOLVED', 'blocks_entries': True, 'trade_ids': [87],
         'detail': 'bestaetigt; aktuelles Ergebnis/Gebuehren noch unvollstaendig'}])
    sperren = etoro_sperren(None)
    assert [(s.grund, s.symbol, s.ablauf) for s in sperren] == [
        ('LEDGERABGLEICH_OFFEN', 'CSCO', REPARATUR), ('PNL_INCOMPLETE', 'AMD', TAGESRESET)]
    assert all(s.reichweite == DOMAIN for s in sperren)
    assert 'Ledgerabgleich noch nicht erfolgreich' in sperren[0].detail


def test_etoro_buchungsabgleich_unlesbar_sperrt_sichtbar(tmp_path, monkeypatch):
    monkeypatch.setenv('TRADINGBOT_TEST_STATE_DIR', str(tmp_path))
    import etoro_reconciliation
    def kaputt(domain=''):
        raise OSError('Abgleichdatei')
    monkeypatch.setattr(etoro_reconciliation, 'buchungsluecken', kaputt)
    sperren = etoro_sperren(None)
    assert [(s.grund, s.reichweite, s.ablauf) for s in sperren] == [('BUCHUNG_NICHT_PRUEFBAR', DOMAIN, REPARATUR)]


# ---------------------------------------------------------------------------
# Vertraege: Kaufpfade und Status nutzen dieselben Quellen
# ---------------------------------------------------------------------------
def test_kaufpfade_lesen_die_symbolsperre():
    engine = (ROOT / 'crypto_engine.py').read_text(encoding='utf-8')
    assert '_gesperrte_symbole' in engine and "accounting.get('blocked_symbols')" in engine
    assert '"sperren": self._sperrliste()' in engine
    assert 'self._mark_broker_unknown(position, observed if observed < position.menge else None)' not in engine, (
        "Der Waechter bucht nicht mehr sofort; das tut der Positionsabgleich nach zwei Messungen")
    life = (ROOT / 'execution_lifecycle.py').read_text(encoding='utf-8')
    assert life.count('require_tradable(') == 2 and 'require_complete(' not in life
    trader = (ROOT / 'live_trader.py').read_text(encoding='utf-8')
    assert 'runtime.update(risk_manager_buy_gate=gate, sperren=sperren)' in trader
    pots = (ROOT / 'risk_pots.py').read_text(encoding='utf-8')
    assert 'offene_ergebnisse_heute(self.state)' in pots and 'lifetime_unknown_pnl_trades\n' not in pots


def test_topf_und_risk_manager_zaehlen_gleich(tmp_path, monkeypatch):
    """Beide Broker sperren nach derselben Regel: nur der laufende Handelstag."""
    monkeypatch.setenv('TRADINGBOT_TEST_STATE_DIR', str(tmp_path))
    from risk_manager import RiskState, offene_ergebnisse_heute
    from etoro_risk_period import result_scope_summary
    state = RiskState.load(tmp_path / 'risk_state_okx.json')
    heute = str(state.current_date)
    gestern = str(date.fromisoformat(heute) - timedelta(days=1))
    state.realized_receipts = {
        'ledger:1': {'status': 'UNKNOWN', 'pnl': None, 'booked_day': gestern},
        'ledger:2': {'status': 'UNKNOWN', 'pnl': None, 'booked_day': heute},
        'ledger:3': {'status': 'CONFIRMED', 'pnl': 1.0, 'booked_day': heute}}
    state.lifetime_unknown_pnl_trades = 2
    state.unknown_pnl_trades_today = 0
    assert offene_ergebnisse_heute(state) == 1
    s = result_scope_summary(state)
    assert s['active_unknown'] == 1 and s['open_unknown'] == 2 and s['lifetime_unknown'] == 2
    state.realized_receipts['ledger:2']['status'] = 'CONFIRMED'
    assert offene_ergebnisse_heute(state) == 0
    assert result_scope_summary(state)['active_unknown'] == 0
