"""10.7.0 Teil E -- Der Gebuehrenbeleg entsteht, statt zu fehlen.

BEFUND VOM 18.09.2026 (CSCO, Trade 88)
======================================
Kauf 14:15:11, Verkauf durch eToro-Stop 14:15:12, AMD gleichzeitig offen. Die
Historie liefert fees=0. Der Cash-Delta-Automat aus 10.3.1 kann nur "genau ein
Verkauf zwischen zwei Belegen, sonst nichts" -- hier lagen Kauf UND Verkauf im
selben Intervall, es gab keinen Beleg "mit Position davor". Ergebnis: UNKNOWN,
PNL_INCOMPLETE, eToro gesperrt. Von 32 eToro-Trades hatten 27 keinen Beleg.

Stufe 2: Das Cash-Delta ueber ALLE bekannten Ereignisse im Intervall.
Stufe 3 (Freigabe Georg: "lockere die Regel und trage einen erwartungswert
ein"): Erwartungswert aus bestaetigten Abrechnungen desselben Kontos,
gekennzeichnet EXPECTED_UNVERIFIED, vom naechsten Barbestandsbeleg ersetzt.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json

import pytest

import etoro_settlement_review as review
import etoro_history_accounting as history
import trade_ledger as ledger
from ledger_result import confirmed_net, modelled_net, usable_net, result_status
from test_v1014_etoro_history_accounting import scenario

CLOSED = "2026-09-14T13:32:26.867Z"


def _basis(monkeypatch, tmp_path):
    tid, record, raw, args, kw = scenario(monkeypatch, tmp_path)
    with ledger._connect() as con:
        con.execute("UPDATE trades SET ownership_status='BOT_VERIFIED' WHERE trade_id=?", (tid,))
    ledger.reconcile_entry_fees_exact(**args)
    history.record_history(**kw)
    return tid, args


def _cash(args, at, cash, ids, *, clean=True, units=None):
    review.save_cash(account=args['account'], paper=True, source='TEST_AUTHENTICATED_PNL',
                     observed_at=at, cash=cash, positions=ids, clean_orders=clean, position_units=units)


def _kauf_ins_intervall(tid, wann="2026-09-14T13:31:30+00:00"):
    with ledger._connect() as con:
        con.execute("UPDATE trades SET eingestiegen_am=? WHERE trade_id=?", (wann, tid))


# ---------------------------------------------------------------------------
# Stufe 2: Intervallrechnung
# ---------------------------------------------------------------------------
def test_kauf_und_verkauf_im_selben_intervall_werden_abgerechnet(monkeypatch, tmp_path):
    """Das CSCO-Muster: 4 Stueck zu 100 gekauft (+1 USD), zu 105 verkauft, alles
    zwischen zwei Belegen. Cash 1000 -> 1000 - 401 + 420 - 1 = 1018."""
    tid, args = _basis(monkeypatch, tmp_path)
    _kauf_ins_intervall(tid)
    _cash(args, '2026-09-14T13:31:00Z', '1000', ['999'])
    _cash(args, '2026-09-14T13:33:00Z', '1018', ['999'])

    p = review.preview(tid)
    assert p['method'] == 'INTERVAL'
    assert p['proposed_exit_cost'] == '1.00' and p['proposed_net'] == '18.00'
    assert p['source'] == 'BROKER_CASH_DELTA_INTERVAL_AUTOMATIC' and p['automatic_release']
    assert [e['art'] for e in p['events']] == ['KAUF']
    ergebnis = review.auto_settle(tid)
    assert ergebnis['updated'] == 1 and ergebnis['method'] == 'INTERVAL'
    zeile = ledger.trade_detail(tid)
    assert confirmed_net(zeile) and zeile['gebuehren'] == 2 and zeile['netto_pnl'] == 18
    assert zeile['fee_quality'] == 'CASH_DELTA_CONFIRMED'
    assert review.preview(tid)['completed'] is True
    with ledger._connect() as con:
        assert [a[0] for a in con.execute('SELECT actor FROM etoro_settlement_reviews')] == [review.INTERVAL_ACTOR]


def test_bekannter_zweiter_verkauf_im_intervall_wird_eingerechnet(monkeypatch, tmp_path):
    """AMD (bestaetigt) und CSCO (offen) im selben Intervall: AMD ist bekannt."""
    tid, args = _basis(monkeypatch, tmp_path)
    _kauf_ins_intervall(tid)
    anderer = ledger.trade_open(broker='etoro', symbol='AMD', menge=2, einstieg_preis=50, asset_type='stock',
                                waehrung='USD', paper=True, zeit='2026-09-14T10:00:00+00:00',
                                broker_position_id='555', entry_order_id='444',
                                broker_account_fingerprint=args['account'], decision_id=4242,
                                ownership_status='BOT_VERIFIED', gebuehr=1.0, critical=True)
    with ledger._connect() as con:
        con.execute("""UPDATE trades SET ausgestiegen_am='2026-09-14T13:32:00+00:00', ausstieg_preis=60,
                       gebuehren=2, netto_pnl=18, fee_quality='CASH_DELTA_CONFIRMED', einstieg_gebuehr=1,
                       entry_fee_quality='CONFIRMED' WHERE trade_id=?""", (anderer,))
    # AMD-Verkauf: +120 - 1 = +119. CSCO: -401 + 420 - 1 = +18. Summe +137.
    _cash(args, '2026-09-14T13:31:00Z', '1000', ['555', '999'])
    _cash(args, '2026-09-14T13:33:00Z', '1137', ['999'])

    p = review.preview(tid)
    assert p['method'] == 'INTERVAL' and p['proposed_exit_cost'] == '1.00'
    assert sorted(e['art'] for e in p['events']) == ['KAUF', 'VERKAUF']
    assert review.auto_settle(tid)['updated'] == 1


def test_zweiter_verkauf_ohne_bestaetigte_gebuehr_bricht_ab(monkeypatch, tmp_path):
    tid, args = _basis(monkeypatch, tmp_path)
    _kauf_ins_intervall(tid)
    anderer = ledger.trade_open(broker='etoro', symbol='AMD', menge=2, einstieg_preis=50, asset_type='stock',
                                waehrung='USD', paper=True, zeit='2026-09-14T10:00:00+00:00',
                                broker_position_id='555', entry_order_id='444',
                                broker_account_fingerprint=args['account'], decision_id=4243,
                                ownership_status='BOT_VERIFIED', gebuehr=1.0, critical=True)
    with ledger._connect() as con:
        con.execute("""UPDATE trades SET ausgestiegen_am='2026-09-14T13:32:00+00:00', ausstieg_preis=60,
                       fee_quality='UNKNOWN', entry_fee_quality='CONFIRMED', einstieg_gebuehr=1 WHERE trade_id=?""", (anderer,))
    _cash(args, '2026-09-14T13:31:00Z', '1000', ['555', '999'])
    _cash(args, '2026-09-14T13:33:00Z', '1137', ['999'])
    with pytest.raises(ValueError, match='ohne bestaetigte Gebuehr'):
        review.preview(tid)
    assert not usable_net(ledger.trade_detail(tid))


def test_verschwundene_position_ohne_ledgerbeleg_bricht_ab(monkeypatch, tmp_path):
    """Position 777 verschwindet laut Beleg, das Ledger weiss nichts davon."""
    tid, args = _basis(monkeypatch, tmp_path)
    _kauf_ins_intervall(tid)
    _cash(args, '2026-09-14T13:31:00Z', '1000', ['777', '999'])
    _cash(args, '2026-09-14T13:33:00Z', '1018', ['999'])
    with pytest.raises(ValueError, match='ohne Ledgerbeleg'):
        review.preview(tid)


def test_unplausible_differenz_bricht_ab(monkeypatch, tmp_path):
    tid, args = _basis(monkeypatch, tmp_path)
    _kauf_ins_intervall(tid)
    _cash(args, '2026-09-14T13:31:00Z', '1000', ['999'])
    _cash(args, '2026-09-14T13:33:00Z', '1500', ['999'])      # 482 USD zu viel: unbeobachtete Einzahlung
    with pytest.raises(ValueError, match='unbeobachtete Geldbewegung'):
        review.preview(tid)


def test_schwebende_orders_verhindern_die_rechnung(monkeypatch, tmp_path):
    tid, args = _basis(monkeypatch, tmp_path)
    _kauf_ins_intervall(tid)
    _cash(args, '2026-09-14T13:31:00Z', '1000', ['999'], clean=False)
    _cash(args, '2026-09-14T13:33:00Z', '1018', ['999'])
    with pytest.raises(ValueError, match='sauberen Barbestandsbelege'):
        review.preview(tid)


def test_einfacher_weg_bleibt_vorrangig(monkeypatch, tmp_path):
    """Der 10.3.1-Fall (ein Verkauf, sonst nichts) laeuft weiter ueber den einfachen Weg."""
    tid, args = _basis(monkeypatch, tmp_path)
    _cash(args, '2026-09-14T13:31:00Z', '1000', ['888', '999'])
    _cash(args, '2026-09-14T13:33:00Z', '1419', ['999'])
    p = review.preview(tid)
    assert 'method' not in p and p['source'] == 'BROKER_CASH_DELTA_AUTOMATIC'
    assert review.auto_settle(tid)['method'] == 'SINGLE'


# ---------------------------------------------------------------------------
# Stufe 3: Erwartungswert
# ---------------------------------------------------------------------------
def _bestaetigte_abrechnungen(args, anzahl=3, kosten='1.00', start=0):
    """Nachgestellte Historie: bereits bestaetigte Abschlussgebuehren desselben Kontos."""
    tids = []
    for n in range(anzahl):
        i = start + n
        t = ledger.trade_open(broker='etoro', symbol=f'ALT{i}', menge=1, einstieg_preis=10, asset_type='stock',
                              waehrung='USD', paper=True, zeit=f'2026-09-0{i + 1}T10:00:00+00:00',
                              broker_position_id=f'7{i}', entry_order_id=f'6{i}',
                              broker_account_fingerprint=args['account'], decision_id=9000 + i,
                              ownership_status='BOT_VERIFIED', gebuehr=1.0, critical=True)
        with ledger._connect() as con:
            con.execute("""UPDATE trades SET ausgestiegen_am=?, ausstieg_preis=12, gebuehren=2, netto_pnl=0,
                           fee_quality='CASH_DELTA_CONFIRMED' WHERE trade_id=?""",
                        (f'2026-09-0{i + 1}T12:00:00+00:00', t))
            review.tables(con)
            con.execute('INSERT INTO etoro_settlement_reviews VALUES (?,?,?,?,?,?)',
                        (t, 'a' * 64, review.AUTO_ACTOR, f'2026-09-0{i + 1}T12:01:00+00:00', '{}',
                         json.dumps({'proposed_exit_cost': kosten})))
        tids.append(t)
    return tids


SPAETER = datetime.fromisoformat(CLOSED.replace('Z', '+00:00')) + timedelta(seconds=1000)


def test_erwartungswert_wird_gekennzeichnet_eingetragen(monkeypatch, tmp_path):
    tid, args = _basis(monkeypatch, tmp_path)
    _bestaetigte_abrechnungen(args, 3)
    assert not usable_net(ledger.trade_detail(tid))

    ergebnis = review.expected_fee_settle(tid, now=SPAETER)

    assert ergebnis['updated'] == 1 and ergebnis['quality'] == 'EXPECTED_UNVERIFIED'
    assert ergebnis['exit_cost'] == '1.00' and ergebnis['samples'] == 3
    zeile = ledger.trade_detail(tid)
    assert zeile['fee_quality'] == 'EXPECTED_UNVERIFIED' and zeile['gebuehren'] == 2 and zeile['netto_pnl'] == 18
    assert modelled_net(zeile) and usable_net(zeile) and not confirmed_net(zeile), (
        "Ein Erwartungswert ist beziffert, aber kein Beleg")
    assert result_status(zeile) == 'EXPECTED'
    assert 'Erwartungswert' in zeile['notiz'] and 'nicht belegt' in zeile['notiz']
    assert review.expected_fee_settle(tid, now=SPAETER)['updated'] == 0


def test_erwartungswert_braucht_drei_bestaetigte_abrechnungen(monkeypatch, tmp_path):
    tid, args = _basis(monkeypatch, tmp_path)
    _bestaetigte_abrechnungen(args, 2)
    with pytest.raises(ValueError, match='mindestens 3'):
        review.expected_fee_settle(tid, now=SPAETER)
    assert not usable_net(ledger.trade_detail(tid))


def test_erwartungswert_laesst_dem_barbestand_vorrang(monkeypatch, tmp_path):
    tid, args = _basis(monkeypatch, tmp_path)
    _bestaetigte_abrechnungen(args, 3)
    zu_frueh = datetime.fromisoformat(CLOSED.replace('Z', '+00:00')) + timedelta(seconds=120)
    with pytest.raises(ValueError, match='Vorrang'):
        review.expected_fee_settle(tid, now=zu_frueh)


def test_erwartungswert_ist_der_median(monkeypatch, tmp_path):
    tid, args = _basis(monkeypatch, tmp_path)
    _bestaetigte_abrechnungen(args, 2, kosten='1.00')
    _bestaetigte_abrechnungen(args, 1, kosten='3.00', start=2)      # Ausreisser
    ergebnis = review.expected_fee_settle(tid, now=SPAETER)
    assert ergebnis['exit_cost'] == '1.00'
    with ledger._connect() as con:
        proof = json.loads(con.execute('SELECT proof_json FROM etoro_settlement_reviews WHERE trade_id=?', (tid,)).fetchone()[0])
    assert proof['sample_min'] == '1.00' and proof['sample_max'] == '3.00' and proof['sample_count'] == 3


def test_barbestandsbeleg_ersetzt_den_erwartungswert(monkeypatch, tmp_path):
    """Spaeter eintreffende Belege gewinnen; die Abweichung wird festgehalten."""
    tid, args = _basis(monkeypatch, tmp_path)
    _bestaetigte_abrechnungen(args, 3, kosten='1.50')
    review.expected_fee_settle(tid, now=SPAETER)
    assert ledger.trade_detail(tid)['gebuehren'] == 2.5
    _cash(args, '2026-09-14T13:31:00Z', '1000', ['888', '999'])
    _cash(args, '2026-09-14T13:33:00Z', '1419', ['999'])

    ergebnis = review.auto_settle(tid)

    assert ergebnis['updated'] == 1 and ergebnis['expected_deviation'] == '-0.50'
    zeile = ledger.trade_detail(tid)
    assert zeile['fee_quality'] == 'CASH_DELTA_CONFIRMED' and zeile['gebuehren'] == 2 and zeile['netto_pnl'] == 18
    assert 'Abweichung -0.50' in zeile['notiz']
    assert review.preview(tid)['completed'] is True
    with ledger._connect() as con:
        assert con.execute('SELECT COUNT(*) FROM etoro_settlement_reviews WHERE trade_id=?', (tid,)).fetchone()[0] == 1


def test_bestaetigter_beleg_wird_nie_durch_erwartungswert_ersetzt(monkeypatch, tmp_path):
    tid, args = _basis(monkeypatch, tmp_path)
    _bestaetigte_abrechnungen(args, 3)
    _cash(args, '2026-09-14T13:31:00Z', '1000', ['888', '999'])
    _cash(args, '2026-09-14T13:33:00Z', '1419', ['999'])
    review.auto_settle(tid)
    assert review.expected_fee_settle(tid, now=SPAETER)['updated'] == 0
    assert ledger.trade_detail(tid)['fee_quality'] == 'CASH_DELTA_CONFIRMED'


# ---------------------------------------------------------------------------
# Risikozustand und Buchungsabgleich nehmen den Erwartungswert an
# ---------------------------------------------------------------------------
def test_risikozustand_nimmt_den_gekennzeichneten_beleg_an(tmp_path, monkeypatch):
    monkeypatch.setenv('TRADINGBOT_TEST_STATE_DIR', str(tmp_path))
    from risk_manager import RiskState
    state = RiskState.load(tmp_path / 'risk_state_etoro.json')
    state.register_unknown_pnl_at('ledger:88', '2026-09-18T14:15:12.633000+00:00')
    state.resolve_unknown_result_at('ledger:88', -10.16, -8.16, 100000.0, '2026-09-18T14:15:12.633000+00:00',
        settlement_evidence=dict(source='ETORO_EXPECTED_FEE_MODEL', receipt_hash='b' * 64,
                                 currency='USD', quality='EXPECTED_UNVERIFIED'))
    assert state.realized_receipts['ledger:88']['status'] == 'CONFIRMED'
    with pytest.raises(ValueError):
        state.resolve_unknown_result_at('ledger:89', 1.0, 2.0, 100000.0, '2026-09-18T14:15:12.633000+00:00',
            settlement_evidence=dict(source='ETORO_EXPECTED_FEE_MODEL', receipt_hash='b' * 64,
                                     currency='USD', quality='CASH_DELTA_CONFIRMED'))


def test_buchungsabgleich_sperrt_mit_erwartungswert_nicht_mehr():
    from etoro_accounting_resolution import _proof  # noqa: F401  (Modul ladbar)
    import etoro_accounting_resolution as res
    src = open(res.__file__, encoding='utf-8').read()
    assert 'usable_net' in src and '"EXPECTED" if known_net' in src
