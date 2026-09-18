"""Native-currency external sell evidence, never inferred FX or bot ownership."""
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json


def number(value, *, positive=False):
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError("Unvollstaendiger Zahlenbeleg") from None
    if not result.is_finite() or (positive and result <= 0):
        raise ValueError("Ungueltiger Zahlenbeleg")
    return result


def native_receipt(rows, *, account, environment, entry_instrument, expected_quantity,
                   lot_size, expected_order_id="", manual_confirmed=False):
    """One exact complete order, no proportional invention of fill identities.

    Allocation is permitted only when its whole base consumption matches the
    vanished tracked inventory, except for less than one exchange lot of dust.
    Mixed orders/currencies, conflicting duplicate fills and overfills fail.
    """
    from broker.okx import okx_fill_identity
    if not account or environment not in {"DEMO", "LIVE"} or not rows:
        raise ValueError("Kontodomaene oder Beleg fehlt")
    base = entry_instrument.split('-')[0]
    unique = {}
    for raw in rows:
        row = dict(raw)
        if (row.get('side') != 'sell' or not row.get('tradeId') or not row.get('ordId')
                or str(row.get('instId','')).split('-')[0] != base):
            raise ValueError("Kein passender echter Verkaufsfill")
        if expected_order_id and str(row['ordId']) != expected_order_id:
            raise ValueError("Falsche Verkaufsorder")
        fid = okx_fill_identity(row, account)
        # Extra response fields may differ; immutable execution values may not.
        core = {k: str(row.get(k,'')) for k in
                ('instId','ordId','tradeId','side','fillSz','fillPx','fee','feeCcy','fillTime','ts','tradeQuoteCcy')}
        if fid in unique and unique[fid][0] != core:
            raise ValueError("Widerspruechlicher Fill mit gleicher Identitaet")
        unique[fid] = (core,row)
    fills = [v[1] for _,v in sorted(unique.items())]
    instruments = {r['instId'] for r in fills}
    orders = {str(r['ordId']) for r in fills}
    if len(instruments) != 1 or len(orders) != 1:
        raise ValueError("Mehrere Verkaufsinstrumente oder Orders sind nicht eindeutig zugeordnet")
    inst = next(iter(instruments))
    currencies = {str(r.get('tradeQuoteCcy') or inst.split('-')[-1]).upper() for r in fills}
    if len(currencies) != 1:
        raise ValueError("Mehrdeutige Abrechnungswaehrung")
    ccy = next(iter(currencies))
    quantity = consumed = gross = fee_quote = Decimal(0)
    fees = {}; quote_known = True; times = []
    for row in fills:
        q, px = number(row['fillSz'],positive=True), number(row['fillPx'],positive=True)
        fee = -number(row.get('fee'))
        fc = str(row.get('feeCcy') or '').upper()
        if fee and not fc:
            raise ValueError("Gebuehrenwaehrung fehlt")
        quantity += q; consumed += q; gross += q*px
        fees[fc] = fees.get(fc,Decimal(0)) + fee
        if fc == base:
            consumed += fee; fee_quote += fee*px
        elif fc == ccy:
            fee_quote += fee
        elif fee:
            quote_known = False
        times.append(number(row.get('fillTime') or row.get('ts'),positive=True))
    expected = number(expected_quantity,positive=True)
    residual = expected - consumed
    if residual < 0 or residual >= number(lot_size,positive=True):
        raise ValueError("Verkaufsmenge passt nicht exakt zum Bestand (ausser Lot-Staub)")
    return dict(confirmed=True, native_currency=ccy, inst_id=inst,
        entry_instrument=entry_instrument, account=account, environment=environment,
        quantity=float(consumed), gross_quantity=str(quantity), residual=str(residual),
        avg_price=float(gross/quantity), gross_proceeds=str(gross),
        fees={k:str(v) for k,v in fees.items() if k},
        fees_quote=float(fee_quote) if quote_known else None,
        net_proceeds=str(gross-fees.get(ccy,Decimal(0))),
        fill_ids=sorted(unique), raw_fill_ids=[str(r['tradeId']) for r in fills],
        order_ids=sorted(orders), fills=fills,
        closed_at=datetime.fromtimestamp(float(max(times))/1000,timezone.utc).isoformat(),
        beweisart='FREMDVERKAUF', manual_confirmed=bool(manual_confirmed))


def composite_receipt(order_groups, *, account, environment, entry_instrument,
                      expected_quantity, lot_size, own_order_ids=()):
    """10.2.1: Mehrere terminale Verkaufsorders belegen ZUSAMMEN einen Abgang.

    Der DOGE-Fall vom 17.09.2026: ein teilerfuellter eigener Take-Profit
    (DOGE-USD, Abrechnung USDC) plus zwei manuelle Market-Orders (DOGE-EUR,
    eine davon teilgefuellt-storniert) ergeben exakt die Buchmenge -- aber
    ``native_receipt`` verlangt EINE Order/Waehrung und konnte das nie belegen.

    Regeln (keine Aufweichung der Beweisdisziplin):
    - Jede Gruppe ist EINE Order desselben Basiswerts, terminal
      (filled/canceled), deren accFillSz exakt der Summe ihrer tradeId-
      belegten Fills entspricht (kommt vom Aufrufer via prove_order-Muster).
    - Die Gesamtmenge (inkl. Basiswaehrungs-Gebuehren) deckt die erwartete
      Menge bis auf weniger als ein Boersen-Lot Staub; Ueberdeckung bricht ab.
    - Erloese und Gebuehren werden JE ABRECHNUNGSWAEHRUNG nativ gefuehrt
      (``legs``); es wird kein Wechselkurs erfunden und kein Mischkurs gebildet.
    """
    from broker.okx import okx_fill_identity
    if not account or environment not in {"DEMO", "LIVE"} or not order_groups:
        raise ValueError("Kontodomaene oder Beleg fehlt")
    base = entry_instrument.split('-')[0]
    own = {str(x) for x in (own_order_ids or ()) if str(x)}
    unique = {}
    orders = {}
    for group in order_groups:
        status = dict(group.get("status") or {})
        fills = [dict(r) for r in (group.get("fills") or [])]
        oid = str(status.get("ordId") or "")
        inst = str(status.get("instId") or "")
        if not oid or not fills or inst.split('-')[0] != base:
            raise ValueError("Ordergruppe ohne belegte Identitaet")
        if str(status.get("side") or "") != "sell":
            raise ValueError("Kein Verkaufsauftrag")
        if str(status.get("state") or "") not in {"filled", "canceled", "mmp_canceled"}:
            raise ValueError("Kein terminaler Orderstatus")
        acc = number(status.get("accFillSz"), positive=True)
        summe = Decimal(0)
        for row in fills:
            if (row.get('side') != 'sell' or not row.get('tradeId')
                    or str(row.get('ordId') or '') != oid
                    or str(row.get('instId') or '') != inst):
                raise ValueError("Fill gehoert nicht zur Ordergruppe")
            fid = okx_fill_identity(row, account)
            core = {k: str(row.get(k, '')) for k in
                    ('instId', 'ordId', 'tradeId', 'side', 'fillSz', 'fillPx',
                     'fee', 'feeCcy', 'fillTime', 'ts', 'tradeQuoteCcy')}
            if fid in unique and unique[fid][0] != core:
                raise ValueError("Widerspruechlicher Fill mit gleicher Identitaet")
            unique[fid] = (core, row)
            summe += number(row.get('fillSz'), positive=True)
        if summe != acc:
            raise ValueError("Ordermenge nicht vollstaendig durch Einzel-Fills belegt")
        if oid in orders:
            raise ValueError("Doppelte Verkaufsorder im Beleg")
        quote = str(status.get('tradeQuoteCcy') or inst.split('-')[-1]).upper()
        orders[oid] = dict(inst_id=inst, quote_ccy=quote, status=dict(status),
                            eigen=(oid in own))
    fills = [v[1] for _, v in sorted(unique.items())]
    legs = {}
    consumed = Decimal(0)
    times = []
    for row in fills:
        oid = str(row.get('ordId'))
        ccy = orders[oid]['quote_ccy']
        q = number(row['fillSz'], positive=True)
        px = number(row['fillPx'], positive=True)
        fee = -number(row.get('fee'))
        fc = str(row.get('feeCcy') or '').upper()
        if fee and not fc:
            raise ValueError("Gebuehrenwaehrung fehlt")
        leg = legs.setdefault(ccy, dict(quantity=Decimal(0), gross=Decimal(0),
                                         fees={}, fee_quote=Decimal(0)))
        leg['quantity'] += q
        leg['gross'] += q * px
        consumed += q
        if fee:
            leg['fees'][fc] = leg['fees'].get(fc, Decimal(0)) + fee
            if fc == base:
                consumed += fee
                leg['fee_quote'] += fee * px
            elif fc == ccy:
                leg['fee_quote'] += fee
        times.append(number(row.get('fillTime') or row.get('ts'), positive=True))
    expected = number(expected_quantity, positive=True)
    residual = expected - consumed
    if residual < 0 or residual >= number(lot_size, positive=True):
        raise ValueError("Verkaufsmenge passt nicht exakt zum Bestand (ausser Lot-Staub)")
    leg_out = {}
    for ccy, leg in legs.items():
        leg_out[ccy] = dict(
            quantity=str(leg['quantity']), gross_proceeds=str(leg['gross']),
            fees={k: str(v) for k, v in leg['fees'].items() if k},
            net_proceeds=str(leg['gross'] - leg['fees'].get(ccy, Decimal(0))),
            avg_price=float(leg['gross'] / leg['quantity']))
    fremd = [oid for oid, o in orders.items() if not o['eigen']]
    return dict(confirmed=True, beweisart='ZUSAMMENGESETZTER_VERKAUF',
        inst_id=entry_instrument, entry_instrument=entry_instrument,
        account=account, environment=environment,
        quantity=float(consumed), residual=str(residual),
        legs=leg_out, native_currencies=sorted(leg_out),
        native_currency=(sorted(leg_out)[0] if len(leg_out) == 1 else None),
        avg_price=(leg_out[sorted(leg_out)[0]]['avg_price'] if len(leg_out) == 1 else None),
        fees_quote=None, net_proceeds=None,
        fill_ids=sorted(unique), raw_fill_ids=[str(r['tradeId']) for r in fills],
        order_ids=sorted(orders), fills=fills,
        orders={oid: dict(inst_id=o['inst_id'], quote_ccy=o['quote_ccy'],
                           eigen=o['eigen'], status=o['status'])
                for oid, o in orders.items()},
        own_order_ids=sorted(own & set(orders)),
        closed_at=datetime.fromtimestamp(float(max(times)) / 1000, timezone.utc).isoformat(),
        manual_confirmed=bool(fremd))


def _resolve_balance_gap(con, *, trade_id, account, environment, verkauft, exit_ids, residual=0):
    """Loest den PENDING-Bestandsbeleg auf, wenn die Fills den Abgang decken.

    10.2.1: ``record_native_exit`` schloss den Trade, liess den balance_gap
    aber PENDING stehen -- die Domaene blieb dadurch trotz vollstaendigem
    Beleg gesperrt (der reguläre trade_close-Pfad hat dieselbe Aufloesung
    schon immer; hier wurde sie schlicht vergessen).

    10.7.0: Die Rechnung selbst liegt jetzt in ``okx_accounting.
    resolve_balance_gap_on`` -- eine Stelle fuer alle Verkaufswege, und sie
    kennt den Lot-Rest.
    """
    from okx_accounting import resolve_balance_gap_on
    resolve_balance_gap_on(con, trade_id=trade_id, account=account, environment=environment,
                           sold=verkauft, residual=residual, exit_ids=exit_ids,
                           reason='EXACT_EXIT_FILLS')


def record_composite_exit(*, entry_order_id, entry_instrument, account, paper,
                          evidence, residual=0):
    """Zusammengesetzten Abschluss atomar ankern; PnL bleibt ohne FX unbekannt."""
    import trade_ledger as ledger
    if (evidence.get('account') != account
            or evidence.get('environment') != ('DEMO' if paper else 'LIVE')
            or evidence.get('entry_instrument') != entry_instrument
            or evidence.get('beweisart') != 'ZUSAMMENGESETZTER_VERKAUF'):
        raise ValueError("Beleg und Handelsdomaene widersprechen sich")
    ledger.init_ledger()
    with ledger._LOCK, ledger._connect() as con:
        con.execute('BEGIN IMMEDIATE')
        rows = con.execute("""SELECT * FROM trades WHERE broker='okx'
            AND broker_account_fingerprint=? AND paper=? AND broker_position_id=?
            AND entry_order_id=? AND superseded_by IS NULL""",
            (account, int(paper), entry_instrument, entry_order_id)).fetchall()
        if len(rows) != 1:
            raise ValueError("Keine einzelne exakte Entry-Zuordnung")
        trade = dict(rows[0]); tid = trade['trade_id']
        # Nachrechnen gegen die LEDGER-Menge, nicht gegen einen Aufruferwert.
        groups = {}
        for row in evidence['fills']:
            groups.setdefault(str(row['ordId']), []).append(dict(row))
        order_groups = [dict(status=evidence['orders'][oid]['status'], fills=f)
                        for oid, f in groups.items()]
        receipt = composite_receipt(order_groups, account=account,
            environment=evidence['environment'], entry_instrument=entry_instrument,
            expected_quantity=trade['menge'],
            lot_size=number(evidence['residual']) + Decimal('0.000000000001'),
            own_order_ids=evidence.get('own_order_ids') or ())
        if abs(number(receipt['residual']) - number(residual)) > Decimal('0.000000001'):
            raise ValueError("Bestandsrest widerspricht dem Verkaufsbeleg")
        if trade.get('netto_pnl') is not None or trade.get('ausstieg_preis') is not None:
            raise ValueError("Vorhandenes beziffertes Ergebnis wird nicht ueberschrieben")
        previous = json.loads(trade.get('exit_native_json') or '{}')
        if previous:
            if previous.get('receipt') != receipt:
                raise ValueError("Abweichender Beleg fuer bereits zugeordneten Verkauf")
            return tid
        if trade.get('exit_order_id') not in (None, '', receipt['order_ids'][0]):
            raise ValueError("Bestehende Exit-ID widerspricht dem Beleg")
        for fid in receipt['fill_ids']:
            con.execute('INSERT INTO trade_native_exit_fills(broker,account,environment,fill_id,trade_id) VALUES(?,?,?,?,?)',
                ('okx', account, receipt['environment'], fid, tid))
        document = dict(receipt=receipt, original=dict(trade),
                        fx_status='MISSING_HISTORICAL_FX', entry_currency=trade['waehrung'])
        eigen = len(receipt.get('own_order_ids') or [])
        fremd = len(receipt['order_ids']) - eigen
        reason = (f"Zusammengesetzter OKX-Verkauf belegt ({eigen} eigene Schutz-, "
                  f"{fremd} Nutzer-Order(s) ueber {len(receipt['legs'])} Abrechnungswaehrung(en))")
        con.execute("""UPDATE trades SET ausgestiegen_am=?,exit_order_id=?,exit_fill_ids_json=?,
            exit_grund=?,exit_native_json=?,reconciliation_status='CLOSED',
            reconciliation_updated_at=?,notiz=?,protection_status='CLOSED'
            WHERE trade_id=?""", (receipt['closed_at'], receipt['order_ids'][0],
            json.dumps(receipt['fill_ids']), reason, json.dumps(document, sort_keys=True),
            datetime.now(timezone.utc).isoformat(),
            'Zusammengesetzter nativer Verkaufsbeleg gespeichert; Netto-PnL folgt aus der EUR-Referenzbewertung.', tid))
        _resolve_balance_gap(con, trade_id=tid, account=account,
                             environment=receipt['environment'],
                             verkauft=receipt['quantity'],
                             exit_ids=receipt['fill_ids'],
                             residual=receipt.get('residual') or 0)
        return tid


def record_native_exit(*, entry_order_id, entry_instrument, account, paper, evidence,
                       residual=0):
    """Atomic exact-anchor close/backfill without cross-currency subtraction.

    Existing numeric results and other close identities are never overwritten.
    The original row is saved in the receipt for an auditable correction.
    """
    import trade_ledger as ledger
    if (evidence.get('account') != account or evidence.get('environment') != ('DEMO' if paper else 'LIVE')
            or evidence.get('entry_instrument') != entry_instrument):
        raise ValueError("Beleg und Handelsdomaene widersprechen sich")
    ledger.init_ledger()
    with ledger._LOCK, ledger._connect() as con:
        con.execute('BEGIN IMMEDIATE')
        rows = con.execute("""SELECT * FROM trades WHERE broker='okx'
            AND broker_account_fingerprint=? AND paper=? AND broker_position_id=?
            AND entry_order_id=? AND superseded_by IS NULL""",
            (account,int(paper),entry_instrument,entry_order_id)).fetchall()
        if len(rows) != 1:
            raise ValueError("Keine einzelne exakte Entry-Zuordnung")
        trade = dict(rows[0]); tid = trade['trade_id']
        receipt = native_receipt(evidence['fills'],account=account,
            environment=evidence['environment'],entry_instrument=entry_instrument,
            expected_quantity=trade['menge'],
            # Recheck exact caller-proven remaining dust, not a relative 0.1% tolerance.
            lot_size=number(evidence['residual'])+Decimal('0.000000000001'),
            expected_order_id=evidence['order_ids'][0],manual_confirmed=evidence.get('manual_confirmed',False))
        if abs(number(receipt['residual'])-number(residual)) > Decimal('0.000000001'):
            raise ValueError("Bestandsrest widerspricht dem Verkaufsbeleg")
        if trade.get('netto_pnl') is not None or trade.get('ausstieg_preis') is not None:
            raise ValueError("Vorhandenes beziffertes Ergebnis wird nicht ueberschrieben")
        previous = json.loads(trade.get('exit_native_json') or '{}')
        if previous:
            if previous.get('receipt') != receipt:
                raise ValueError("Abweichender Beleg fuer bereits zugeordneten Verkauf")
            return tid
        if trade.get('exit_order_id') not in (None,'',receipt['order_ids'][0]):
            raise ValueError("Bestehende Exit-ID widerspricht dem Beleg")
        # Fill consumption is exclusive across trade rows, even on replay.
        for fid in receipt['fill_ids']:
            con.execute('INSERT INTO trade_native_exit_fills(broker,account,environment,fill_id,trade_id) VALUES(?,?,?,?,?)',
                ('okx',account,receipt['environment'],fid,tid))
        document = dict(receipt=receipt, original=dict(trade),
                        fx_status='MISSING_HISTORICAL_FX', entry_currency=trade['waehrung'])
        reason = ('Vom Nutzer selbst bei OKX verkauft' if receipt['manual_confirmed']
                  else 'Extern bei OKX verkauft (keine NEXUS-Verkaufsorder)')
        con.execute("""UPDATE trades SET ausgestiegen_am=?,exit_order_id=?,exit_fill_ids_json=?,
            exit_grund=?,exit_native_json=?,reconciliation_status='CLOSED',
            reconciliation_updated_at=?,notiz=?,protection_status='CLOSED'
            WHERE trade_id=?""",(receipt['closed_at'],receipt['order_ids'][0],
            json.dumps(receipt['fill_ids']),reason,json.dumps(document,sort_keys=True),
            datetime.now(timezone.utc).isoformat(),
            'Nativer Verkaufsbeleg gespeichert; historischer Wechselkurs fehlt. Netto-PnL bleibt unbekannt.',tid))
        # 10.2.1: Der vollstaendig belegte Abgang loest auch den PENDING-
        # Bestandsbeleg auf (bislang vergessen; die Domaene blieb gesperrt).
        _resolve_balance_gap(con, trade_id=tid, account=account,
                             environment=receipt['environment'],
                             verkauft=receipt['quantity'],
                             exit_ids=receipt['fill_ids'],
                             residual=receipt.get('residual') or 0)
        return tid
