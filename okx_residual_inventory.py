"""Prove and retain unsold entry remainders, separately from realized trades.

No status text alone grants relief. Entry fills, every sold sibling's complete
execution, and the whole entry quantity must balance in the same account/mode.
"""
from __future__ import annotations
from contextlib import closing
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN
import hashlib
import json
from okx_receipt_math import EvidenceError, number, same_quantity, summarize, prove_order
from broker.okx import okx_fill_identity


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def tables(con):
    return {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def lineage(con, row):
    return [dict(r) for r in con.execute('''SELECT * FROM trades WHERE broker='okx'
        AND broker_account_fingerprint=? AND paper=? AND broker_position_id=?
        AND entry_order_id=? AND superseded_by IS NULL ORDER BY trade_id''',
        (row['broker_account_fingerprint'], row['paper'], row['broker_position_id'], row['entry_order_id']))]


def scope(row):
    if (row.get('broker') != 'okx' or not row.get('entry_order_id') or not row.get('broker_position_id')
            or not row.get('broker_account_fingerprint') or row.get('paper') not in (0, 1)
            or row.get('superseded_by') is not None):
        raise EvidenceError('Rest ohne exakte Kontokette')
    return dict(account=row['broker_account_fingerprint'],
        environment='DEMO' if row['paper'] else 'LIVE', inst=row['broker_position_id'],
        oid=row['entry_order_id'], base=row['broker_position_id'].split('-')[0], currency=row['waehrung'])


def entry_from_ledger(con, row, *, proven_order_currency=""):
    ctx = scope(row)
    family = lineage(con, row)
    tids = {r['trade_id'] for r in family}
    entries = [dict(r) for r in con.execute('''SELECT f.* FROM trade_entry_fills f JOIN trades t ON t.trade_id=f.trade_id
        WHERE f.broker='okx' AND f.broker_account_fingerprint=? AND f.instrument=? AND f.order_id=? AND t.paper=?''',
        (ctx['account'], ctx['inst'], ctx['oid'], row['paper']))]
    if not entries or any(f['trade_id'] not in tids for f in entries):
        raise EvidenceError('Vollstaendige lokale Kauf-Fillkette fehlt')
    raws = []
    for f in entries:
        raw = json.loads(f['raw_json'])
        if f.get('paper') is not None and int(f['paper']) != row['paper']:
            raise EvidenceError('Kauf-Fill aus anderer Umgebung')
        if not same_quantity(f['quantity'], raw.get('fillSz')) or not same_quantity(f['price'], raw.get('fillPx')):
            raise EvidenceError('Kauf-Fillprojektion widerspricht Original')
        raws.append(raw)
    if not proven_order_currency and 'okx_closed_result_receipts' in tables(con):
        for receipt in con.execute('SELECT * FROM okx_closed_result_receipts WHERE trade_id IN ('+','.join('?' for _ in tids)+')',tuple(sorted(tids))):
            if hashlib.sha256(receipt['receipt_json'].encode()).hexdigest()!=receipt['receipt_hash']:
                raise EvidenceError('Kauf-Nachbeleghash widerspricht Original')
            data=json.loads(receipt['receipt_json'])
            if data.get('account')!=ctx['account'] or data.get('environment')!=ctx['environment'] or data.get('inst_id')!=ctx['inst']:
                raise EvidenceError('Kauf-Nachbeleg ohne exakte Domaene')
            saved=data.get('entry_proof') or {}
            verified=prove_order(saved.get('status'),saved.get('fills') or [],inst=ctx['inst'],oid=ctx['oid'],side='buy',
                base=ctx['base'],currency=ctx['currency'],account=ctx['account'])
            if set(verified['fill_ids'])!={okx_fill_identity(f,ctx['account']) for f in raws}:
                raise EvidenceError('Kauf-Nachbeleg passt nicht zu lokalen Fills')
            proven_order_currency=verified['currency']
            break
    proof = summarize(raws, side='buy', order_currency=proven_order_currency, **{k: v for k, v in ctx.items() if k != 'environment'})
    total = sum((number(r['menge'], positive=True) for r in family), Decimal(0))
    if 'okx_residual_inventory' in tables(con):
        for extra in con.execute('''SELECT * FROM okx_residual_inventory WHERE account=? AND environment=?
                AND instrument=? AND entry_order_id=? AND separate_row=0''',
                (ctx['account'],ctx['environment'],ctx['inst'],ctx['oid'])):
            if extra['trade_id'] not in tids or hashlib.sha256(extra['proof_json'].encode()).hexdigest()!=extra['proof_hash']:
                raise EvidenceError('Anhaengender Rest ohne passende Kaufkette')
            total += number(extra['quantity'],positive=True)
    if not same_quantity(total, proof['quantity']):
        raise EvidenceError('Summe der Trade-Teile passt nicht zum Nettokauf')
    for r in family:
        if abs(number(r['einstieg_preis'], positive=True) - number(proof['price'])) > max(Decimal('1e-9'), number(proof['price']) * Decimal('1e-9')):
            raise EvidenceError('Einstand der Teile widerspricht Kaufbelegen')
    return proof, family


def local_exit(con, row):
    """Use existing broker execution rows, never a mere CLOSED/CONFIRMED flag."""
    ctx = scope(row)
    if not row.get('exit_order_id'):
        raise EvidenceError('Verkaufsorder fuer Restnachweis fehlt')
    oid = row['exit_order_id']
    if {'execution_orders', 'execution_fills'} <= tables(con):
        orders = con.execute('''SELECT * FROM execution_orders WHERE broker='okx' AND account=?
            AND environment=? AND instrument=? AND order_id=? AND side='SELL' ''',
            (ctx['account'], ctx['environment'], ctx['inst'], oid)).fetchall()
        if len(orders) == 1:
            order = dict(orders[0])
            if not order['terminal'] or not order['evidence_complete']:
                raise EvidenceError('Verkaufsorder noch nicht vollstaendig belegt')
            evidence = json.loads(order['evidence_json'])
            if (evidence.get('ord_id') != oid or evidence.get('inst_id') != ctx['inst']
                    or evidence.get('broker_environment') != ctx['environment']):
                raise EvidenceError('Gespeicherter Orderbeleg ohne exakte Domaene')
            fills = [json.loads(f[0]) for f in con.execute('SELECT raw_json FROM execution_fills WHERE order_key=?', (order['order_key'],))]
            status = dict(instId=ctx['inst'], ordId=oid, side='sell', state=evidence.get('state'),
                accFillSz=order['filled'], sz=order['requested'], avgPx=evidence.get('avg_price'))
            proof = prove_order(status, fills, inst=ctx['inst'], oid=oid, side='sell', base=ctx['base'],
                currency=ctx['currency'], account=ctx['account'])
            if not same_quantity(proof['quantity'], row['menge']):
                raise EvidenceError('Verkaufsteilmenge widerspricht Einzel-Fills')
            if row.get('ausstieg_preis') is not None and abs(number(row['ausstieg_preis'])-number(proof['price'])) > max(Decimal('1e-9'), number(proof['price'])*Decimal('1e-9')):
                raise EvidenceError('Verkaufspreis widerspricht Restnachweis')
            return proof
    if 'okx_closed_result_receipts' in tables(con):
        r = con.execute('SELECT receipt_json,receipt_hash FROM okx_closed_result_receipts WHERE trade_id=?', (row['trade_id'],)).fetchone()
        if r:
            if hashlib.sha256(r['receipt_json'].encode()).hexdigest()!=r['receipt_hash']:
                raise EvidenceError('Nachbeleg-Hash widerspricht Original')
            p = json.loads(r['receipt_json'])
            if p.get('account') != ctx['account'] or p.get('environment') != ctx['environment'] or p.get('inst_id') != ctx['inst']:
                raise EvidenceError('Nachbeleg aus fremder Domaene')
            pieces = p.get('exit_proofs') or []
            verified=[]
            for piece in pieces:
                full=prove_order(piece['status'],piece['fills'],inst=ctx['inst'],oid=piece['order_id'],
                    side='sell',base=ctx['base'],currency=ctx['currency'],account=ctx['account'])
                chosen=[f for f in full['fills'] if okx_fill_identity(f,ctx['account']) in p['fill_ids']]
                if chosen:
                    verified.append(summarize(chosen,inst=ctx['inst'],oid=piece['order_id'],side='sell',
                        base=ctx['base'],currency=ctx['currency'],account=ctx['account'],order_currency=full['currency']))
            actual=sum((number(x['quantity']) for x in verified),Decimal(0))
            if not verified or not same_quantity(actual,row['menge']):
                raise EvidenceError('Nachbelegmenge passt nicht')
            return dict(quantity=str(actual),cash=str(sum((number(x['cash']) for x in verified),Decimal(0))),
                fill_ids=[i for x in verified for i in x['fill_ids']],fills=[f for x in verified for f in x['fills']],source='LATE_RECEIPT')
    raise EvidenceError('Unabhaengige Verkaufs-Fills fuer Restnachweis fehlen')


def prepare(con, row, *, entry=None, exits=None):
    ctx = scope(row)
    if (row.get('ausstieg_preis') is not None or row.get('netto_pnl') is not None
            or row.get('brutto_pnl') is not None or row.get('exit_order_id')
            or json.loads(row.get('exit_fill_ids_json') or '[]') or json.loads(row.get('exit_native_json') or '{}')):
        raise EvidenceError('Restzeile besitzt Verkaufs-/Ergebnisbelege')
    local_entry, family = entry_from_ledger(con, row)
    entry = entry or local_entry
    if not same_quantity(entry['quantity'], local_entry['quantity']):
        raise EvidenceError('Kaufnachbelege widersprechen einander')
    sold, remainders = [], []
    for member in family:
        if member.get('ausstieg_preis') is not None or member.get('exit_order_id'):
            proof = (exits or {}).get(member['trade_id']) or local_exit(con, member)
            if not same_quantity(proof['quantity'], member['menge']):
                raise EvidenceError('Verkaufsteilmenge nicht belegt')
            sold.append((member, proof))
        elif member.get('netto_pnl') is None and not json.loads(member.get('exit_native_json') or '{}'):
            remainders.append(member)
        else:
            raise EvidenceError('Ungeklaerter Teil derselben Kaufkette')
    if not sold or not any(m['trade_id'] == row['trade_id'] for m in remainders):
        raise EvidenceError('Kein belegter Verkauf vor diesem Rest')
    seen = [fid for _, p in sold for fid in p['fill_ids']]
    if len(seen) != len(set(seen)):
        raise EvidenceError('Verkaufsfill wird mehrfach auf Kaufteile verteilt')
    consumed = sum((number(p['quantity']) for _, p in sold), Decimal(0))
    remainder = sum((number(m['menge']) for m in remainders), Decimal(0))
    if not same_quantity(consumed + remainder, entry['quantity']):
        raise EvidenceError('Restrechnung passt nicht zur gesamten Kaufkette')
    q = number(row['menge'], positive=True)
    return dict(trade_id=row['trade_id'], **ctx, quantity=str(q),
        cost_basis=str(number(entry['cash']) * q / number(entry['quantity'])),
        entry=entry, sold=[dict(trade_id=m['trade_id'], proof=p) for m, p in sold],
        family_quantities={str(m['trade_id']): str(m['menge']) for m in family},
        separate_row=True)



def allocate_entry_costs(con, row, proof):
    from ledger_result import confirmed_net
    family=lineage(con,row);entry=proof['entry'];ep=number(entry['price']);total=number(entry['quantity'])
    for member in family:
        qty=number(member['menge'],positive=True);cost=number(entry['cash'])*qty/total;fee=cost-ep*qty
        sold=next((s['proof'] for s in proof['sold'] if s['trade_id']==member['trade_id']),None)
        if sold and confirmed_net(member):
            if abs(number(sold['cash'])-cost-number(member['netto_pnl']))>Decimal('0.00000001'):
                raise EvidenceError('Bestehendes Netto widerspricht der belegten Teilkostenrechnung')
        data=dict(trade_id=member['trade_id'],account=proof['account'],environment=proof['environment'],
            inst=proof['inst'],oid=proof['oid'],entry=entry,quantity=str(qty),cost_basis=str(cost),entry_fee=str(fee))
        encoded=encode(data);digest=hashlib.sha256(encoded.encode()).hexdigest()
        old=con.execute('SELECT * FROM okx_entry_allocations WHERE trade_id=?',(member['trade_id'],)).fetchone()
        if old:
            if old['proof_hash']!=digest or not same_quantity(member.get('entry_cost_basis'),cost) or not same_quantity(member.get('einstieg_gebuehr'),fee):
                raise EvidenceError('Widerspruechliche bestehende Kostenallokation')
        else:
            con.execute('INSERT INTO okx_entry_allocations VALUES(?,?,?,?,?,?,?)',
                (member['trade_id'],str(cost),str(fee),encode(member),digest,encoded,datetime.now(timezone.utc).isoformat()))
            con.execute('UPDATE trades SET einstieg_gebuehr=?,entry_cost_basis=? WHERE trade_id=?',
                (float(fee),float(cost),member['trade_id']))

def record(con, original, proof, *, separate_row=True):
    """Caller holds BEGIN IMMEDIATE. All original data and the proof are retained."""
    tid = original['trade_id']
    payload = encode(proof); digest = hashlib.sha256(payload.encode()).hexdigest()
    old = con.execute('SELECT * FROM okx_residual_inventory WHERE trade_id=?', (tid,)).fetchone()
    if old:
        if (old['account'] != proof['account'] or old['environment'] != proof['environment']
                or old['entry_order_id'] != proof['oid'] or not same_quantity(old['quantity'], proof['quantity'])
                or not same_quantity(old['cost_basis'], proof['cost_basis'])):
            raise EvidenceError('Widerspruechlicher Restbeleg; keine Ueberschreibung')
        return False
    con.execute('INSERT INTO okx_residual_inventory VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
        (tid, proof['account'], proof['environment'], proof['inst'], proof['oid'], proof['quantity'],
         proof['cost_basis'], proof['currency'], int(separate_row), digest, encode(original), payload,
         datetime.now(timezone.utc).isoformat()))
    if separate_row:
        allocate_entry_costs(con,original,proof)
        con.execute("""UPDATE trades SET accounting_kind='RESIDUAL',
            reconciliation_status='RESIDUAL_CONFIRMED',protection_status='NOT_APPLICABLE',
            entry_cost_basis=?,reconciliation_updated_at=? WHERE trade_id=?""",
            (float(number(proof['cost_basis'])), datetime.now(timezone.utc).isoformat(), tid))
    return True


def classify(trade_id, *, allow_open=False, market_rules=None):
    import trade_ledger as tl
    tl.init_ledger()
    with tl._LOCK, closing(tl._connect()) as con, con:
        con.execute('BEGIN IMMEDIATE')
        r = con.execute('SELECT * FROM trades WHERE trade_id=?', (int(trade_id),)).fetchone()
        if not r:
            raise EvidenceError('Resttrade fehlt')
        row = dict(r)
        if row.get('accounting_kind') == 'RESIDUAL':
            from okx_accounting import status_on_connection
            faults=status_on_connection(con,row['broker_account_fingerprint'],'DEMO' if row['paper'] else 'LIVE')['gaps']
            if any(g.get('trade_id')==row['trade_id'] and g['kind']=='RESIDUAL_PROOF_MISSING' for g in faults):
                raise EvidenceError('Vorhandener Restmarker besitzt keinen gueltigen Nachweis')
            return False
        if not row.get('ausgestiegen_am') and not allow_open:
            raise EvidenceError('Aktive Position wird nicht automatisch archiviert')
        if row.get('ausgestiegen_am') and 'rundungsrest' not in str(row.get('exit_grund') or '').lower():
            raise EvidenceError('Ungeklaerter Abschluss ist kein dokumentierter Rundungsrest')
        proof = prepare(con, row)
        if not row.get('ausgestiegen_am'):
            rules=market_rules or {}
            if rules.get('inst_id')!=row['broker_position_id']:
                raise EvidenceError('Aktuelle instrumentbezogene Handelsschritte fehlen')
            checked=datetime.fromisoformat(str(rules.get('checked_at') or '').replace('Z','+00:00'))
            if checked.tzinfo is None or not -5 <= (datetime.now(timezone.utc)-checked).total_seconds() <= 60:
                raise EvidenceError('Handelsschritte nicht frisch')
            lot=number(rules.get('lot_size'),positive=True);minimum=number(rules.get('min_size'),positive=True)
            q=number(row['menge'],positive=True);balance=number(rules.get('observed_balance'))
            floored=(q/lot).to_integral_value(rounding=ROUND_DOWN)*lot
            if balance<q and not same_quantity(balance,q):
                raise EvidenceError('Offene Botmenge fehlt teilweise; kein Restabschluss')
            if floored>0 and floored>=minimum:
                raise EvidenceError('Rest ist noch verkaeuflich und bleibt aktiv')
            proof['market_rules']=dict(rules)
        if not row.get('ausgestiegen_am'):
            # This records the inventory classification time, not an execution.
            con.execute('UPDATE trades SET ausgestiegen_am=? WHERE trade_id=?',
                (datetime.now(timezone.utc).isoformat(), row['trade_id']))
        return record(con, row, proof)


def classify_existing(*, account=None, paper=None, limit=50):
    import trade_ledger as tl
    tl.init_ledger()
    sql = "SELECT trade_id FROM trades WHERE broker='okx' AND accounting_kind='TRADE' AND ausgestiegen_am IS NOT NULL AND ausstieg_preis IS NULL AND netto_pnl IS NULL AND superseded_by IS NULL"
    params=[]
    if account is not None: sql += ' AND broker_account_fingerprint=?'; params.append(account)
    if paper is not None: sql += ' AND paper=?'; params.append(int(paper))
    with closing(tl._connect()) as con, con:
        tids = [r[0] for r in con.execute(sql+' ORDER BY trade_id LIMIT ?', (*params, limit))]
    result = {'changed': [], 'pending': []}
    for tid in tids:
        try:
            if classify(tid): result['changed'].append(tid)
        except (EvidenceError, ValueError, TypeError, KeyError) as exc:
            result['pending'].append({'trade_id': tid, 'detail': str(exc)})
    return result


def public_inventory(*, broker=''):
    """Small read-only projection; no original JSON/keys/providers in the WebUI."""
    if broker not in ('','okx'):return []
    import trade_ledger as tl
    from broker_display_context import context
    with closing(tl._connect()) as con, con:
        if 'okx_residual_inventory' not in tables(con):return []
        rows=con.execute('''SELECT i.*,t.symbol,t.paper,t.menge,t.accounting_kind FROM okx_residual_inventory i
            JOIN trades t ON t.trade_id=i.trade_id WHERE t.broker='okx' AND t.superseded_by IS NULL
            AND t.broker_account_fingerprint=i.account AND t.broker_position_id=i.instrument
            AND t.entry_order_id=i.entry_order_id ORDER BY i.recorded_at DESC,i.trade_id DESC''').fetchall()
    result=[]
    for r in rows:
        if r['environment']!=('DEMO' if r['paper'] else 'LIVE') or hashlib.sha256(r['proof_json'].encode()).hexdigest()!=r['proof_hash']:
            raise EvidenceError('Restbestand ohne unveraenderten Domaenennachweis')
        if r['separate_row'] and (r['accounting_kind']!='RESIDUAL' or not same_quantity(r['quantity'],r['menge'])):
            raise EvidenceError('Restbestand widerspricht Inventarprojektion')
        q=number(r['quantity'],positive=True);cost=number(r['cost_basis'],positive=True)
        item=dict(trade_id=r['trade_id'],broker='okx',paper=r['paper'],symbol=r['symbol'],
            broker_account_fingerprint=r['account'],waehrung=r['currency'],instrument=r['instrument'],
            quantity_text=format(q,'f').replace('.',','),cost_basis_text=format(cost,'.12f').rstrip('0').rstrip('.').replace('.',','),
            quantity=str(q),cost_basis=str(cost),recorded_at=r['recorded_at'])
        item['display_context']=context(item);result.append(item)
    return result
