"""Resolve pending risk receipts only from exact, confirmed ledger evidence."""
from __future__ import annotations
from contextlib import closing
import json
import math
import time
import logging

log=logging.getLogger(__name__)
from ledger_result import confirmed_net, finite_number, usable_net

_LAST = {}


def _enrich_okx(row, broker):
    # 9.8.8: the bounded, independent closed-result worker obtains COMPLETE
    # entry+exit evidence and allocates split fees transactionally. Never turn
    # a locally stored entry-fee estimate into a confirmed result here.
    return None


def reconcile(state, broker, *, account_equity):
    import trade_ledger as tl
    name = str(getattr(broker, 'name', '')).lower()
    if name not in {'etoro', 'okx'} or not callable(getattr(broker, 'account_fingerprint', None)):
        return []
    account = str(broker.account_fingerprint() or '')
    paper = bool(broker.demo) if name == 'okx' else bool(broker.ist_paper())
    currency = str(broker.kontowaehrung() or '').upper()
    if not account or not currency:
        return []
    state.refresh()
    if name == 'okx':
        from okx_accounting import sync_unknown_results
        from okx_receipt_math import same_quantity
        import hashlib
        sync_unknown_results(state,broker)
        tl.init_ledger()
        with closing(tl._connect()) as con, con:
            names={r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if 'okx_closed_result_receipts' in names:
                # Also discover a receipt committed before a crash but absent from risk JSON.
                for r in con.execute('''SELECT t.* FROM trades t JOIN okx_closed_result_receipts c ON c.trade_id=t.trade_id
                    WHERE t.broker='okx' AND t.broker_account_fingerprint=? AND t.paper=? AND t.superseded_by IS NULL''',(account,int(paper))):
                    state.register_unknown_pnl_at('ledger:'+str(r['trade_id']),r['ausgestiegen_am'])
            if 'okx_reference_results' in names:
                for r in con.execute('''SELECT t.* FROM trades t JOIN okx_reference_results v ON v.trade_id=t.trade_id
                    WHERE t.broker='okx' AND t.broker_account_fingerprint=? AND t.paper=? AND t.superseded_by IS NULL''',(account,int(paper))):
                    state.register_unknown_pnl_at('ledger:'+str(r['trade_id']),r['ausgestiegen_am'])
            if 'okx_residual_inventory' in names:
                for r in con.execute('''SELECT i.*,t.menge,t.accounting_kind FROM okx_residual_inventory i
                    JOIN trades t ON t.trade_id=i.trade_id WHERE i.account=? AND i.environment=? AND i.separate_row=1
                    AND t.broker='okx' AND t.broker_account_fingerprint=i.account AND t.paper=?''',(account,'DEMO' if paper else 'LIVE',int(paper))):
                    if (r['accounting_kind']=='RESIDUAL' and same_quantity(r['quantity'],r['menge'])
                            and hashlib.sha256(r['proof_json'].encode()).hexdigest()==r['proof_hash']):
                        state.resolve_proven_residual('ledger:'+str(r['trade_id']),r['proof_hash'])
    state.refresh()
    done = []
    for key, receipt in list(state.realized_receipts.items()):
        if receipt.get('status') != 'UNKNOWN' or not key.startswith('ledger:'):
            continue
        try:
            row = tl.trade_detail(int(key.split(':', 1)[1]))
            if (not row or row['broker'] != name or row['broker_account_fingerprint'] != account
                    or bool(row['paper']) != paper or not row['ausgestiegen_am']
                    or row.get('superseded_by')):
                continue
            if name == 'okx' and currency == 'EUR':
                from okx_reference_valuation import load_on
                with closing(tl._connect()) as con:
                    valued = load_on(con,row,currency=currency)
                if valued:
                    state.resolve_unknown_result_at(key,float(valued['net_eur']),float(valued['gross_eur']),
                        account_equity,valued['closed_at'],evidence={
                            'source':'okx_reference_valuation','receipt_hash':valued['receipt_hash'],
                            'method':valued['method'],'currency':'EUR','quality':valued['quality']})
                    done.append(key)
                    continue
            if row['waehrung'] != currency:
                continue
            if not confirmed_net(row) and name == 'okx':
                identity = (name, account, paper, key)
                if time.monotonic() - _LAST.get(identity, -1e9) >= 300:
                    _LAST[identity] = time.monotonic()
                    _enrich_okx(row, broker)
                    row = tl.trade_detail(row['trade_id'])
            if not confirmed_net(row) and name == 'etoro':
                # 10.3.1: Automatische Cash-Delta-Abrechnung aus den bereits
                # gespeicherten Barbestandsbelegen; hoechstens alle 5 Minuten
                # je Zeile, Ablehnung laesst den Trade unveraendert UNKNOWN.
                # 10.7.0: auto_settle kennt jetzt auch die Intervallrechnung
                # und ersetzt einen frueher eingetragenen Erwartungswert.
                identity = ('etoro-auto', account, paper, key)
                if time.monotonic() - _LAST.get(identity, -1e9) >= 300:
                    _LAST[identity] = time.monotonic()
                    from etoro_settlement_review import auto_settle, expected_fee_settle
                    try:
                        outcome = auto_settle(row['trade_id'])
                        if outcome.get('updated'):
                            log.info('eToro-Abrechnung automatisch protokolliert: Trade %s netto %s USD '
                                     '(Abschlusskosten %s USD aus Barbestandsbelegen, Weg %s)',
                                     row['trade_id'], outcome.get('net'), outcome.get('exit_cost'),
                                     outcome.get('method') or 'SINGLE')
                    except (ValueError, KeyError) as exc:
                        log.debug('eToro-Abrechnung Trade %s bleibt offen: %s', row['trade_id'], exc)
                    row = tl.trade_detail(row['trade_id'])
                    if not usable_net(row):
                        # Stufe 3 (Freigabe Georg 18.09.2026): Erwartungswert
                        # aus bestaetigten Abrechnungen desselben Kontos,
                        # gekennzeichnet, nach 15 Minuten ohne Beleg.
                        try:
                            outcome = expected_fee_settle(row['trade_id'])
                            if outcome.get('updated'):
                                log.info('eToro-Abrechnung Trade %s mit ERWARTUNGSWERT eingetragen: '
                                         'netto %s USD (Abschlusskosten %s USD aus %s bestaetigten '
                                         'Abrechnungen; nicht belegt)', row['trade_id'],
                                         outcome.get('net'), outcome.get('exit_cost'), outcome.get('samples'))
                        except (ValueError, KeyError) as exc:
                            log.debug('eToro-Erwartungswert Trade %s nicht moeglich: %s', row['trade_id'], exc)
                        row = tl.trade_detail(row['trade_id'])
            if usable_net(row) and finite_number(row.get('brutto_pnl')) is not None:
                verified_late_receipt = None
                if name == 'okx':
                    with closing(tl._connect()) as con, con:
                        if con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='okx_closed_result_receipts'").fetchone():
                            verified_late_receipt = con.execute('SELECT receipt_json FROM okx_closed_result_receipts WHERE trade_id=?', (row['trade_id'],)).fetchone()
                if verified_late_receipt:
                    proof = json.loads(verified_late_receipt['receipt_json'])
                    if (proof['closed_at'] != row['ausgestiegen_am'] or proof['net'] != row['netto_pnl']
                            or proof['account'] != account or proof['currency'] != currency):
                        continue
                    state.resolve_unknown_result_at(key,row['netto_pnl'],row['brutto_pnl'],
                        account_equity,row['ausgestiegen_am'])
                elif name == 'etoro':
                    # Completion belongs to the proven sale day. Reading a
                    # delayed fee receipt tomorrow must not create today's
                    # profit or reset today's loss sequence.
                    evidence = None
                    qualitaet = str(row.get('fee_quality') or '')
                    if qualitaet in ('USER_CONFIRMED', 'CASH_DELTA_CONFIRMED', 'EXPECTED_UNVERIFIED'):
                        from etoro_settlement_review import preview, _review_token_for
                        if qualitaet == 'EXPECTED_UNVERIFIED':
                            # 10.7.0: Der Erwartungswert traegt seinen eigenen
                            # Token; preview() wuerde ihn als unvollstaendig
                            # werten, weil er ersetzbar bleibt.
                            token = _review_token_for(row['trade_id'], expected=True)
                            if not token:
                                continue
                            evidence = dict(source='ETORO_EXPECTED_FEE_MODEL', receipt_hash=token,
                                            currency='USD', quality='EXPECTED_UNVERIFIED')
                        else:
                            review = preview(row['trade_id'])
                            if not review['completed']:
                                continue
                            if qualitaet == 'USER_CONFIRMED':
                                evidence = dict(source='ETORO_USER_VERIFIED_CASH_DELTA',
                                                receipt_hash=review['token'], currency='USD',
                                                quality='USER_CONFIRMED')
                            elif review.get('method') == 'INTERVAL':
                                evidence = dict(source='ETORO_INTERVAL_CASH_DELTA',
                                                receipt_hash=review['token'], currency='USD',
                                                quality='CASH_DELTA_CONFIRMED')
                            else:
                                evidence = dict(source='ETORO_AUTOMATIC_CASH_DELTA',
                                                receipt_hash=review['token'], currency='USD',
                                                quality='CASH_DELTA_CONFIRMED')
                    state.resolve_unknown_result_at(key, row['netto_pnl'], row['brutto_pnl'],
                        account_equity, row['ausgestiegen_am'], settlement_evidence=evidence)
                else:
                    state.register_realized_pnl(row['netto_pnl'], account_equity,
                        gross_pnl=row['brutto_pnl'], trade_id=key)
                done.append(key)
        except Exception as exc:
            # Receipt remains UNKNOWN and still blocks buys. No destructive repair.
            # 10.3.0: Ratenbremse -- dieselbe offene Zeile stand vorher alle
            # paar Sekunden als WARNING im Log (17.09.2026: ledger:73 im
            # 7-Sekunden-Takt). Der Fehlertext bleibt vollstaendig, nur die
            # Wiederholung wandert nach DEBUG.
            marker = ('warn', name, account, key, type(exc).__name__, str(exc)[:200])
            if time.monotonic() - _LAST.get(marker, -1e9) >= 600:
                _LAST[marker] = time.monotonic()
                log.warning('Risiko-Ergebnisabgleich %s bleibt offen: %s', key, exc)
            else:
                log.debug('Risiko-Ergebnisabgleich %s bleibt offen: %s', key, exc)
            continue
    return done
