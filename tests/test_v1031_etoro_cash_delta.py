"""10.3.1: eToro-Abschlusskosten aus Barbestandsbelegen bei mehreren offenen Positionen.

Befund 17.09.2026 (AAPL Trade 79): Waehrend AAPL geschlossen wurde, blieb META
offen. Die alte Zuordnung verlangte "genau eine offene Position davor, keine
danach" und konnte deshalb nie greifen -- weder automatisch noch im manuellen
Dialog. Jede eToro-Verkaufsrunde sperrte damit die Kaeufe des Kontos.

Neu: Positionsmenge davor minus geschlossene Position == Menge danach, keine
offenen Orders, keine andere Handelsbewegung; unter engen Grenzen wird die
Abrechnung automatisch als CASH_DELTA_CONFIRMED protokolliert. Ausserhalb der
Grenzen bleibt UNKNOWN und der manuelle Dialog.
"""
from types import SimpleNamespace

import pytest

import etoro_settlement_review as review
import etoro_history_accounting as history
import trade_ledger as ledger
from ledger_result import confirmed_net
from test_v1014_etoro_history_accounting import scenario


def ready(monkeypatch, tmp_path, *, before_ids, after_ids, cash_before='1000', cash_after='1419',
          units_before=None, units_after=None, clean_before=True, after_at='2026-09-14T13:33:00Z'):
    tid, record, raw, args, kw = scenario(monkeypatch, tmp_path)
    with ledger._connect() as con:
        con.execute("UPDATE trades SET ownership_status='BOT_VERIFIED' WHERE trade_id=?", (tid,))
    ledger.reconcile_entry_fees_exact(**args)
    history.record_history(**kw)
    common = dict(account=args['account'], paper=True, source='TEST_AUTHENTICATED_PNL')
    review.save_cash(**common, observed_at='2026-09-14T13:31:00Z', cash=cash_before,
                     positions=before_ids, clean_orders=clean_before, position_units=units_before)
    review.save_cash(**common, observed_at=after_at, cash=cash_after,
                     positions=after_ids, clean_orders=True, position_units=units_after)
    return tid, args, kw


def test_concurrent_open_position_settles_automatically(monkeypatch, tmp_path):
    tid, args, kw = ready(monkeypatch, tmp_path, before_ids=['888', '999'], after_ids=['999'])
    p = review.preview(tid)
    assert p['automatic_release'] is True and p['source'] == 'BROKER_CASH_DELTA_AUTOMATIC'
    assert p['proposed_exit_cost'] == '1.00' and p['proposed_net'] == '18.00'
    before = ledger.trade_detail(tid)
    assert not confirmed_net(before)
    result = review.auto_settle(tid)
    assert result['updated'] == 1 and result['quality'] == 'CASH_DELTA_CONFIRMED'
    after = ledger.trade_detail(tid)
    assert confirmed_net(after) and after['netto_pnl'] == 18 and after['gebuehren'] == 2
    assert after['fee_quality'] == 'CASH_DELTA_CONFIRMED' and after['exit_fee_quality'] == 'CASH_DELTA_CONFIRMED'
    for k in ('menge', 'entry_order_id', 'exit_order_id', 'entry_fill_ids_json', 'exit_fill_ids_json', 'ausgestiegen_am'):
        assert before[k] == after[k]
    assert review.preview(tid)['completed'] is True
    assert review.auto_settle(tid)['updated'] == 0
    with ledger._connect() as con:
        audit = con.execute('SELECT actor FROM etoro_settlement_reviews').fetchall()
        assert [a[0] for a in audit] == [review.AUTO_ACTOR]


def test_pi_numbers_aapl_with_meta_open(monkeypatch, tmp_path):
    """Exakte Zahlen der Diagnose vom 17.09.2026: 44 AAPL 332,85 -> 335,67, META offen."""
    tid, record, raw, args, kw = scenario(monkeypatch, tmp_path)
    with ledger._connect() as con:
        con.execute("UPDATE trades SET ownership_status='BOT_VERIFIED' WHERE trade_id=?", (tid,))
    ledger.reconcile_entry_fees_exact(**args)
    with ledger._connect() as con:
        con.execute("UPDATE trades SET menge=44, einstieg_preis=332.85, ausstieg_preis=335.67, "
                    "ausgestiegen_am='2026-09-17T16:01:31.227Z' WHERE trade_id=?", (tid,))
    row = kw['snapshot']['rows'][0]
    row.update(units=44, openRate=332.85, closeRate=335.67, netProfit=124.08,
               closeTimestamp='2026-09-17T16:01:31.227Z')
    kw['snapshot']['snapshot_at'] = '2026-09-17T16:01:46+00:00'
    assert history.record_history(**kw)['quality'] == 'BROKER_HISTORY_COST_SCOPE_CONFLICT'
    common = dict(account=args['account'], paper=True, source='ETORO_AUTHENTICATED_PNL_CREDIT')
    review.save_cash(**common, observed_at='2026-09-17T15:11:07.994Z', cash='70213.54',
                     positions=['888', '3600814421'], clean_orders=True)
    review.save_cash(**common, observed_at='2026-09-17T16:00:37.835Z', cash='70213.54',
                     positions=['888', '3600814421'], clean_orders=True)
    review.save_cash(**common, observed_at='2026-09-17T16:01:36.320Z', cash='84982.02',
                     positions=['3600814421'], clean_orders=True)
    p = review.preview(tid)
    assert review.amount(p['gross_proceeds']) == review.amount('14769.48')
    assert review.amount(p['cash_delta']) == review.amount('14768.48')
    assert p['proposed_exit_cost'] == '1.00' and p['proposed_net'] == '122.08'
    assert p['automatic_release'] is True and p['interval_seconds'] == 58
    assert review.auto_settle(tid)['net'] == '122.08'
    assert ledger.trade_detail(tid)['gebuehren'] == 2


def test_other_position_vanishing_in_interval_blocks_attribution(monkeypatch, tmp_path):
    tid, args, kw = ready(monkeypatch, tmp_path, before_ids=['888', '999'], after_ids=[])
    with pytest.raises(ValueError, match='Positionsbestand'):
        review.preview(tid)
    with pytest.raises(ValueError):
        review.auto_settle(tid)
    assert not confirmed_net(ledger.trade_detail(tid))


def test_partial_close_of_other_position_blocks_attribution(monkeypatch, tmp_path):
    tid, args, kw = ready(monkeypatch, tmp_path, before_ids=['888', '999'], after_ids=['999'],
                          units_before={'888': '4', '999': '10'}, units_after={'999': '8'})
    with pytest.raises(ValueError, match='Stückzahlen'):
        review.preview(tid)


def test_identical_units_of_other_positions_are_accepted(monkeypatch, tmp_path):
    tid, args, kw = ready(monkeypatch, tmp_path, before_ids=['888', '999'], after_ids=['999'],
                          units_before={'888': '4', '999': '10'}, units_after={'999': '10'})
    assert review.preview(tid)['automatic_release'] is True


def test_implausible_exit_cost_stays_manual(monkeypatch, tmp_path):
    # 1400 statt 1419: Abschlusskosten waeren 20 USD > max(5, 0,5 % von 420).
    tid, args, kw = ready(monkeypatch, tmp_path, before_ids=['888', '999'], after_ids=['999'], cash_after='1400')
    p = review.preview(tid)
    assert p['automatic_release'] is False and 'Plausibilitaetsgrenze' in p['automatic_block_reason']
    assert p['source'] == 'USER_VERIFIED_CASH_DELTA'
    with pytest.raises(ValueError, match='Keine automatische Freigabe'):
        review.auto_settle(tid)
    assert not confirmed_net(ledger.trade_detail(tid))
    result = review.confirm(tid, token=p['token'], actor='tester', no_other_cashflows=True)
    assert result['quality'] == 'USER_CONFIRMED'
    assert ledger.trade_detail(tid)['fee_quality'] == 'USER_CONFIRMED'
    assert review.preview(tid)['completed'] is True


def test_pending_orders_or_long_interval_stay_manual(monkeypatch, tmp_path):
    tid, args, kw = ready(monkeypatch, tmp_path, before_ids=['888', '999'], after_ids=['999'], clean_before=False)
    p = review.preview(tid)
    assert p['automatic_release'] is False and 'Offene Orders' in p['automatic_block_reason']
    with pytest.raises(ValueError):
        review.auto_settle(tid)


def test_long_interval_stays_manual(monkeypatch, tmp_path):
    tid, args, kw = ready(monkeypatch, tmp_path, before_ids=['888', '999'], after_ids=['999'],
                          after_at='2026-09-14T14:10:00Z')
    p = review.preview(tid)
    assert p['automatic_release'] is False and 'Messintervall' in p['automatic_block_reason']


def test_recovery_resolves_risk_automatically_on_sale_day(monkeypatch, tmp_path):
    import risk_result_recovery as recovery
    from risk_manager import RiskState
    tid, args, kw = ready(monkeypatch, tmp_path, before_ids=['888', '999'], after_ids=['999'])
    risk = RiskState.load(tmp_path/'risk.json')
    row = ledger.trade_detail(tid)
    risk.register_unknown_pnl_at('ledger:'+str(tid), row['ausgestiegen_am'])
    assert risk.realized_receipts['ledger:'+str(tid)]['status'] == 'UNKNOWN'
    b = SimpleNamespace(name='etoro', ist_paper=lambda: True, kontowaehrung=lambda: 'USD',
                        account_fingerprint=lambda: args['account'])
    recovery._LAST.clear()
    assert recovery.reconcile(risk, b, account_equity=10000) == ['ledger:'+str(tid)]
    receipt = risk.realized_receipts['ledger:'+str(tid)]
    assert receipt['status'] == 'CONFIRMED' and receipt['booked_day'] == '2026-09-14'
    assert receipt['settlement_evidence']['quality'] == 'CASH_DELTA_CONFIRMED'
    assert receipt['settlement_evidence']['source'] == 'ETORO_AUTOMATIC_CASH_DELTA'
    assert risk.lifetime_realized_pnl == 18
    assert recovery.reconcile(risk, b, account_equity=10000) == []


def test_risk_manager_rejects_mismatched_settlement_pairing():
    from risk_manager import RiskState
    state = RiskState()
    state.register_unknown_pnl_trade('ledger:5')
    with pytest.raises(ValueError, match='Abrechnungsnachweis'):
        state.resolve_unknown_result_at('ledger:5', 1.0, 2.0, 1000.0, '2026-09-10T08:00:00Z',
            settlement_evidence=dict(source='ETORO_USER_VERIFIED_CASH_DELTA', receipt_hash='a'*64,
                                     currency='USD', quality='CASH_DELTA_CONFIRMED'))
    assert state.resolve_unknown_result_at('ledger:5', 1.0, 2.0, 1000.0, '2026-09-10T08:00:00Z',
        settlement_evidence=dict(source='ETORO_AUTOMATIC_CASH_DELTA', receipt_hash='a'*64,
                                 currency='USD', quality='CASH_DELTA_CONFIRMED'))


def test_cash_sampler_records_units_per_position(monkeypatch, tmp_path):
    tid, record, raw, args, kw = scenario(monkeypatch, tmp_path)
    b = SimpleNamespace(paper=True, account_fingerprint=lambda: args['account'])
    data = dict(_snapshot_at='2026-09-14T13:30:00Z', clientPortfolio=dict(
        accountCurrencyId=1, credit=1000, positions=[dict(positionId=888, units=4), dict(positionId=999, units=10)],
        mirrors=[], orders=[], ordersForOpen=[], ordersForClose=[]))
    h = review.capture_pnl(b, data)
    assert h
    with ledger._connect() as con:
        import json
        proof = json.loads(con.execute('SELECT receipt_json FROM etoro_cash_receipts WHERE receipt_hash=?', (h,)).fetchone()[0])
    assert proof['position_units'] == {'888': '4', '999': '10'}
    assert proof['position_ids'] == ['888', '999']


def test_confirmed_quality_is_recognised_everywhere():
    from ledger_result import fees_confirmed, result_status
    row = {'fee_quality': 'CASH_DELTA_CONFIRMED', 'netto_pnl': 1.5}
    assert fees_confirmed(row) and result_status(row) == 'CONFIRMED'
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    for name in ('trade_ledger.py', 'NEXUS_10_Diagnose.py'):
        assert 'CASH_DELTA_CONFIRMED' in (root/name).read_text(encoding='utf-8')
