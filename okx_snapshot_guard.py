"""Reject inventory-inconsistent equity before risk consumes it (no broker writes)."""
import math


def _eigene_exit_fills(broker, positions, ledger_rows, symbol):
    """Belegte eigene Verkaufs-Fills (Schutz-/Exitorders) eines Basiswerts.

    10.2.0 (DOGE-Fall 17.09.2026): Ein teilerfuellter bot-eigener Take-Profit
    reduziert den Bestand, ist aber fachlich KEIN unbekannter Zustand -- die
    Order ist ueber protection_algo_id dem Bot zuordenbar und die fehlende
    Menge ist die bereits verkaufte Menge. Nur nachweislich eigene, als SELL
    bestaetigte Orderfuellungen zaehlen; jeder Zweifel liefert 0.0 und laesst
    damit die alte konservative Sperre bestehen.
    """
    algo_ids = set()
    inst_ids = {}
    for p in positions:
        if str(getattr(p, 'symbol', '')).upper() != symbol:
            continue
        if not getattr(p, 'ist_bewiesene_botposition', False):
            continue
        algo = str(getattr(p, 'protection_algo_id', '') or '').strip()
        inst = str(getattr(p, 'inst_id', '') or '').strip()
        if algo and inst:
            algo_ids.add(algo)
            inst_ids[algo] = inst
    for r in ledger_rows:
        if str(r.get('symbol') or '').upper() != symbol:
            continue
        algo = str(r.get('protection_algo_id') or '').strip()
        inst = str(r.get('broker_position_id') or '').strip()
        if algo and inst and '-' in inst:
            algo_ids.add(algo)
            inst_ids.setdefault(algo, inst)
    gefuellt = 0.0
    gebunden = 0.0
    for algo in sorted(algo_ids):
        inst = inst_ids.get(algo, '')
        if not inst:
            continue
        for ord_id in sorted(broker.protection_exit_order_ids(algo)):
            row = broker.client.order_status(inst, ord_id=ord_id)
            if str(row.get('side') or '').lower() != 'sell':
                continue
            acc = float(row.get('accFillSz') or 0.0)
            if math.isfinite(acc) and acc > 0:
                gefuellt += acc
            state = str(row.get('state') or '').lower()
            if state in {'live', 'partially_filled'}:
                sz = float(row.get('sz') or 0.0)
                if math.isfinite(sz) and sz > acc:
                    gebunden += sz - acc
    return gefuellt, gebunden


def validate(broker, positions):
    """Guthaben gegen Buch und Ledger pruefen; Sperren nach Reichweite (10.7.0).

    Bis 10.6.0 sperrte ein fehlender Bestand EINES Coins die ganze Domaene
    (``valid=False``), und der Aufrufer buchte ihn sofort als verschwunden --
    ohne die Bestaetigungsfrist des Positionsabgleichs. Jetzt liefert der
    Waechter ``blocked_symbols``: nur diese Coins sind fuer Neueinstiege
    gesperrt. ``valid=False`` bleibt den Faellen vorbehalten, in denen die
    Messung selbst nichts taugt (Schnappschuss unlesbar, Kontokontext falsch)
    oder ein domaenenweiter Buchungsbeleg offen ist. Gebucht wird hier nichts;
    das tut der Positionsabgleich nach zwei bestaetigten Messungen.
    """
    import trade_ledger
    from okx_account_context import strict_account
    result = dict(valid=False, missing=[], observed={}, exit_in_progress=[],
                  blocked_symbols=[], detail='BALANCE_SNAPSHOT_INVALID')
    try:
        account = broker.account_fingerprint()
        environment = 'DEMO' if broker.demo else 'LIVE'
        if not account:
            raise ValueError('Kontobindung fehlt')
        snapshot = broker.guthaben_schnappschuss()
        if not isinstance(snapshot, dict):
            raise ValueError('Guthabenformat ungueltig')
        for row in snapshot.values():
            if not isinstance(row, dict):
                raise ValueError('Unbrauchbare Guthabenzeile')
            for key in ('cash', 'gesamt', 'frozen', 'eq_usd'):
                if key in row and (not math.isfinite(float(row[key])) or float(row[key]) < 0):
                    raise ValueError('Unbrauchbare Guthabenzahl')
        expected = {}
        for p in positions:
            if not p.ist_bewiesene_botposition:
                continue
            if p.account_fingerprint != account or bool(p.paper) != bool(broker.demo):
                raise ValueError('OKX_ACCOUNT_CONTEXT_MISMATCH')
            symbol = p.symbol.upper()
            expected[symbol] = expected.get(symbol, 0.0) + float(p.menge)
        # Direct read: the legacy convenience reader converts DB errors to [].
        trade_ledger.init_ledger()
        with trade_ledger._connect() as con:
            rows = con.execute("SELECT * FROM trades WHERE broker='okx' AND paper=? AND ausgestiegen_am IS NULL AND superseded_by IS NULL", (int(broker.demo),)).fetchall()
        ledger_expected = {}
        for raw in rows:
            r = dict(raw)
            if r.get('broker_account_fingerprint') != account:
                continue
            if r.get('reconciliation_status') in {'DISMISSED','ACCOUNT_ASSET_CONFIRMED'} or r.get('accounting_kind') == 'RESIDUAL':
                continue
            symbol = str(r['symbol']).upper()
            ledger_expected[symbol] = ledger_expected.get(symbol, 0.0) + float(r['menge'])
        for symbol, qty in ledger_expected.items():
            expected[symbol] = max(expected.get(symbol, 0.0), qty)
        for symbol, qty in expected.items():
            row = snapshot.get(symbol, {})
            observed = float(row.get('gesamt', float(row.get('cash', 0)) + float(row.get('frozen', 0))))
            if not math.isfinite(observed) or observed < 0 or not math.isfinite(qty) or qty <= 0:
                raise ValueError('Unbrauchbare Menge')
            result['observed'][symbol] = observed
            if observed < qty * 0.98:
                result['missing'].append(symbol)
        if result['missing']:
            # 10.2.0: eigene laufende Schutz-/Exitorders erklaeren den Bestand.
            # Erklaerte Symbole werden EXIT_IN_PROGRESS statt UNKNOWN; die
            # Sperre wird dadurch positionsbezogen (Aufrufer), nicht mehr
            # domaenenweit. Unerklaerte Symbole sperren unveraendert alles.
            for symbol in list(result['missing']):
                try:
                    gefuellt, gebunden = _eigene_exit_fills(
                        broker, positions, [dict(raw) for raw in rows], symbol)
                except Exception:
                    continue  # Zweifel erklaert nichts; Sperre bleibt.
                qty = expected[symbol]
                observed = result['observed'][symbol]
                if gefuellt > 0 and observed + gefuellt >= qty * 0.98:
                    result['missing'].remove(symbol)
                    result['exit_in_progress'].append({
                        'symbol': symbol, 'erwartet': qty, 'beobachtet': observed,
                        'eigene_exit_fills': gefuellt, 'in_eigenen_orders_gebunden': gebunden,
                    })
        from okx_accounting import status
        accounting = status(account, environment)
        gesperrt = set(result['missing'])
        gesperrt |= {str(e['symbol']).upper() for e in result['exit_in_progress']}
        gesperrt |= {str(s).upper() for s in (accounting.get('blocked_symbols') or [])}
        result['blocked_symbols'] = sorted(gesperrt)
        if not accounting['complete']:
            # Domaenenweit offener Beleg (Geld ohne Kontozuordnung o. ae.).
            result['detail'] = 'BROKER_STATE_UNKNOWN: ' + accounting['detail']
            return result
        teile = []
        if result['missing']:
            teile.append('BESTAND_FEHLT: Bestand fehlt oder reduziert (noch unbestaetigt): ' + ', '.join(result['missing']))
        if result['exit_in_progress']:
            teile.append('EXIT_IN_PROGRESS: eigener Schutz-Exit teilerfuellt: '
                         + ', '.join(e['symbol'] for e in result['exit_in_progress']))
        offene = [s for s in (accounting.get('blocked_symbols') or [])]
        if offene:
            teile.append('BUCHUNGSBELEG: offene Bestandsbelege: ' + ', '.join(offene))
        if gesperrt:
            result.update(valid=True, detail=(
                'SYMBOL_GESPERRT: ' + '; '.join(teile)
                + ' -- nur diese Coins fuer Neueinstiege gesperrt, alle anderen Kaeufe frei'))
        else:
            result.update(valid=True, detail='Bestand und kontogebundene Belege konsistent')
    except Exception as exc:
        result['detail'] = 'BALANCE_SNAPSHOT_INVALID: ' + type(exc).__name__
    return result
