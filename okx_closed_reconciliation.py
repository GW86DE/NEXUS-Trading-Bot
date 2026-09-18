"""Account-scoped, read-only recovery of complete OKX execution chains.

Split trades keep explicit fill allocations. Unsold remainders are inventory,
not sales. Every write is CAS-checked and transactionally stores original rows,
raw receipts, exclusive fill claims, accounting and residual costs. No broker POST.
"""
from __future__ import annotations
from contextlib import closing
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
import hashlib
import json
import logging
import threading
import time
from broker.base import BrokerFehler
from broker.okx import OKXBroker, OKXClient, okx_fill_identity
from ledger_result import confirmed_net
from okx_receipt_math import EvidenceError, number, tolerance, same_quantity, stamp, prove_order, summarize

log = logging.getLogger(__name__)
_GUARD = threading.Lock()
_JOBS = {}


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def init():
    import trade_ledger as tl
    tl.init_ledger()
    with tl._LOCK, closing(tl._connect()) as con, con:
        con.executescript('''CREATE TABLE IF NOT EXISTS okx_closed_result_receipts (
            trade_id INTEGER PRIMARY KEY, receipt_hash TEXT NOT NULL,
            original_json TEXT NOT NULL, receipt_json TEXT NOT NULL, applied_at TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS okx_closed_result_checks (
            trade_id INTEGER PRIMARY KEY, checked_at REAL NOT NULL,
            status TEXT NOT NULL, detail TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS okx_receipt_imports (source_hash TEXT PRIMARY KEY, trade_id INTEGER NOT NULL,
            source_schema TEXT NOT NULL, raw_json TEXT NOT NULL, imported_at TEXT NOT NULL);''')


def _stamp(row):
    return stamp(row)


def _time(text):
    dt = datetime.fromisoformat(str(text).replace('Z', '+00:00'))
    if dt.tzinfo is None:
        raise EvidenceError('Zeitbeleg ohne Zeitzone')
    return dt


def _order(broker, inst, oid, side, currency):
    """Missing order quote may be supplemented by explicit, consistent fills."""
    status = broker.read_primary_order_status(inst, order_id=oid, side=side)
    if not status or any(str(status.get(k) or '') != v for k, v in
            [('instId', inst), ('ordId', oid), ('side', side)]):
        raise EvidenceError('Exakter primaerer Orderstatus fehlt oder widerspricht der Identitaet')
    meta = broker.client.instrument(inst)
    if not meta or meta.inst_id != inst:
        raise EvidenceError('Exakte Instrumentmetadaten fehlen')
    rows = broker.order_fills(inst, oid, expected_qty=float(number(status.get('accFillSz'), positive=True)), attempts=1)
    proof = prove_order(status, rows, inst=inst, oid=oid, side=side, base=meta.base_ccy,
        currency=currency, account=broker.account_fingerprint())
    return SimpleNamespace(proof=proof, fills=proof['fills'], fees_quote=float(number(proof['fees_quote'])),
        avg_fill_price=float(number(proof['price'])), terminal=True, fill_evidence_complete=True,
        trade_quote_ccy=currency, account_fingerprint=broker.account_fingerprint(),
        broker_environment='DEMO' if broker.demo else 'LIVE')


def _family_snapshot(family):
    fields=('trade_id','broker','broker_account_fingerprint','paper','broker_position_id',
        'entry_order_id','menge','einstieg_preis','ausgestiegen_am','ausstieg_preis',
        'exit_order_id','exit_fill_ids_json','superseded_by','accounting_kind')
    return [{k:r.get(k) for k in fields} for r in family]


def historical_algo_ids(row):
    """An explicitly persisted client anchor, never a symbol/time guess."""
    import trade_ledger as tl
    ids={str(row['protection_algo_id'])} if row.get('protection_algo_id') else set()
    client=str(row.get('protection_client_order_id') or '')
    if not client:
        return sorted(ids)
    account=row['broker_account_fingerprint'];env='DEMO' if row['paper'] else 'LIVE';inst=row['broker_position_id']
    with closing(tl._connect()) as con, con:
        names={r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table in ('okx_protection_submissions','okx_protection_history'):
            if table not in names: continue
            for r in con.execute(f"SELECT algo_id FROM {table} WHERE account=? AND environment=? AND instrument=? AND client_id=?",(account,env,inst,client)):
                if r['algo_id']:ids.add(str(r['algo_id']))
        if 'execution_orders' in names:
            for r in con.execute("SELECT request_json FROM execution_orders WHERE broker='okx' AND account=? AND environment=? AND instrument=? AND side='SELL'",(account,env,inst)):
                request=json.loads(r['request_json'])
                if (request.get('instId')==inst and request.get('side')=='sell'
                        and request.get('protection_client_id')==client and request.get('protection_algo_id')):
                    ids.add(str(request['protection_algo_id']))
    if len(ids)>8:raise EvidenceError('Historische Schutzanker ueberschreiten Lesebudget')
    return sorted(ids)


def _algo_children(algo, aid, row):
    if (str(algo.get('algoId') or '') != aid or algo.get('instId') != row['broker_position_id']
            or algo.get('side') != 'sell'):
        raise EvidenceError('Exakte Algo-Instrument-Verknuepfung fehlt')
    if algo.get('tradeQuoteCcy') and algo['tradeQuoteCcy'] != row['waehrung']:
        raise EvidenceError('Algo-Abrechnungswaehrung widerspricht dem Trade')
    if algo.get('algoClOrdId') and row.get('protection_client_order_id') and algo['algoClOrdId'] != row['protection_client_order_id']:
        raise EvidenceError('Algo-Clientanker widerspricht dem gespeicherten Trade')
    raw=algo.get('ordIdList') or []
    if not isinstance(raw,list):raise EvidenceError('Algo-Kindorders nicht als belegte Liste geliefert')
    ids=[str(x.get('ordId') if isinstance(x,dict) else x) for x in raw if x]
    if algo.get('ordId'):ids.append(str(algo['ordId']))
    return sorted(set(x for x in ids if x and x != 'None'))


def _build(row, family, entry, exits, *, algo=None, lot_size=None, tiny_legacy=False):
    """Revalidate all evidence and compute cash/inventory allocations in Decimal."""
    account=row['broker_account_fingerprint'];inst=row['broker_position_id'];currency=row['waehrung']
    entry=prove_order(entry['status'],entry['fills'],inst=inst,oid=row['entry_order_id'],side='buy',
        base=entry['base'],currency=currency,account=account)
    family_total=sum((number(r['menge'],positive=True) for r in family),Decimal(0))
    if not same_quantity(family_total,entry['quantity']):
        raise EvidenceError('Summe der Kaufteile entspricht nicht der vollstaendigen Einstiegskette')
    if len({r['trade_id'] for r in family})!=len(family) or not any(r['trade_id']==row['trade_id'] for r in family):
        raise EvidenceError('Einstiegskette nicht eindeutig')
    for member in family:
        if (member['broker_account_fingerprint']!=account or member['paper']!=row['paper']
                or member['broker_position_id']!=inst or member['entry_order_id']!=row['entry_order_id']):
            raise EvidenceError('Kaufteile aus verschiedenen Domaenen')
        if abs(number(member['einstieg_preis'],positive=True)-number(entry['price']))>max(Decimal('1e-10'),number(entry['price'])*Decimal('1e-9')):
            raise EvidenceError('Belegter Einstand widerspricht der Ledgerzeile')
    proofs=[]
    for piece in exits:
        proof=prove_order(piece['status'],piece['fills'],inst=inst,oid=piece['order_id'],side='sell',
            base=entry['base'],currency=currency,account=account)
        if algo and proof['status'].get('algoId') and proof['status']['algoId'] != algo['algoId']:
            raise EvidenceError('Verkaufsorder widerspricht dem Schutzorder-Anker')
        proofs.append(proof)
    if not proofs or len({p['order_id'] for p in proofs})!=len(proofs):
        raise EvidenceError('Primaere Verkaufsorders fehlen oder sind doppelt')
    wanted=set(json.loads(row.get('exit_fill_ids_json') or '[]'))
    all_ids={f for p in proofs for f in p['fill_ids']}
    if wanted and not wanted<=all_ids:
        raise EvidenceError('Vorhandene Verkaufs-Fillanker widersprechen dem Nachweis')
    # Two partial rows may refer to one order only with explicit disjoint fills.
    selected=[]
    for p in proofs:
        rows=[f for f in p['fills'] if not wanted or okx_fill_identity(f,account) in wanted]
        if rows:
            selected.append(summarize(rows,inst=inst,oid=p['order_id'],side='sell',base=entry['base'],currency=currency,
                account=account,order_currency=p['status'].get('tradeQuoteCcy') or ''))
    fills=[f for p in selected for f in p['fills']]
    ids=sorted(okx_fill_identity(f,account) for f in fills)
    if not ids or len(ids)!=len(set(ids)):
        raise EvidenceError('Fill fehlt oder wird mehrfach verschiedenen Orders zugeordnet')
    qty=sum((number(p['quantity']) for p in selected),Decimal(0))
    gross_qty=sum((number(p['gross_quantity']) for p in selected),Decimal(0))
    cash=sum((number(p['cash']) for p in selected),Decimal(0))
    value=sum((number(p['value']) for p in selected),Decimal(0))
    original_qty=number(row['menge'],positive=True)
    if len(family)==1: original_qty=number(entry['quantity'])
    residual=original_qty-qty
    if abs(residual)<=tolerance(original_qty):residual=Decimal(0)
    if residual<0:
        raise EvidenceError('Verkaufsmenge groesser als zugeordnetes Inventar')
    if len(family)>1 and residual:
        raise EvidenceError('Geteilter Trade verlangt eine exakte mengenbezogene Fill-Allokation')
    if residual:
        if lot_size is not None:
            if residual>=number(lot_size,positive=True):
                raise EvidenceError('Handelbarer Teilrest: keine automatische Vollschliessung')
        elif not tiny_legacy or residual/number(entry['quantity'])>Decimal('0.00000001'):
            raise EvidenceError('Rest ohne belegte Schrittweite oder eng begrenzten Altbestandsnachweis')
    # No allocation may steal a sibling's fills or an unallocated shared order.
    orders=set(p['order_id'] for p in selected)
    for member in family:
        if member['trade_id']==row['trade_id']:continue
        peer=set(json.loads(member.get('exit_fill_ids_json') or '[]'))
        if peer & set(ids):raise EvidenceError('Fill wird von einem anderen Kaufteil beansprucht')
        if member.get('exit_order_id') in orders and not peer:
            raise EvidenceError('Gemeinsame Verkaufsorder ohne eindeutige Teil-Fillanker')
    price=value/gross_qty; entry_price=number(entry['price'])
    cost=number(entry['cash'])*qty/number(entry['quantity'])
    entry_fee=cost-entry_price*qty
    exit_fee=qty*price-cash
    gross=(price-entry_price)*qty; net=cash-cost; fees=entry_fee+exit_fee
    closed=max(stamp(f) for f in fills); entered=min(stamp(f) for f in entry['fills'])
    if max(stamp(f) for f in entry['fills'])>min(stamp(f) for f in fills):
        raise EvidenceError('Kauf- und Verkaufszeiten der Vollausfuehrung widersprechen sich')
    for field,val in [('ausstieg_preis',price),('brutto_pnl',gross)]:
        if row.get(field) is not None and abs(number(row[field])-val)>max(Decimal('1e-8'),abs(val)*Decimal('1e-9')):
            raise EvidenceError('Vorhandener Finanzwert widerspricht dem Beleg: '+field)
    if confirmed_net(row) and abs(number(row['netto_pnl'])-net)>Decimal('1e-8'):
        raise EvidenceError('Bestaetigtes Netto wird nicht ueberschrieben')
    result=dict(trade_id=row['trade_id'],account=account,environment='DEMO' if row['paper'] else 'LIVE',
        inst_id=inst,currency=currency,entry_order_id=row['entry_order_id'],exit_order_ids=sorted(orders),
        fill_ids=ids,fills=fills,entry_fills=entry['fills'],entry_proof=entry,exit_proofs=proofs,
        algo=algo or {},quantity=float(qty),residual=str(residual),residual_cost=str(number(entry['cash'])*residual/number(entry['quantity'])),
        cost_basis=str(cost),price=float(price),gross=float(gross),fees=float(fees),net=float(net),
        entry_fee=float(entry_fee),exit_fee=float(exit_fee),entered_at=entered.isoformat(),closed_at=closed.isoformat(),
        holding_minutes=(closed-entered).total_seconds()/60,lot_size=str(lot_size) if lot_size is not None else None,
        tiny_legacy=bool(tiny_legacy),lineage_snapshot=_family_snapshot(family))
    return result


def prepare(row, broker):
    import trade_ledger as tl
    if (row.get('broker')!='okx' or not row.get('ausgestiegen_am') or row.get('superseded_by')
            or not row.get('entry_order_id') or not row.get('broker_position_id')
            or not row.get('broker_account_fingerprint') or row['broker_account_fingerprint']!=broker.account_fingerprint()
            or bool(row['paper'])!=bool(broker.demo) or row.get('accounting_kind')=='RESIDUAL'):
        raise EvidenceError('Geschlossene kontogebundene OKX-Einstiegskette erforderlich')
    if row.get('ownership_status') not in {'VERIFIED','BOT_VERIFIED','VERIFIED_BROKER_FILL_CHAIN'}:
        raise EvidenceError('Bot-Eigentum nicht belegt')
    if json.loads(row.get('exit_native_json') or '{}'):
        raise EvidenceError('Nativer Fremdwaehrungsabschluss bleibt im eigenen FX-Abgleich')
    family=tl.entry_lineage('okx',row['broker_position_id'],row['entry_order_id'],row['broker_account_fingerprint'],paper=bool(row['paper']))
    # Validate the caller's exact row against persisted lineage before any reads.
    member=next((x for x in family if x['trade_id']==row['trade_id']),None)
    if member is None or any(member.get(k)!=row.get(k) for k in ('menge','einstieg_preis','entry_order_id')):
        raise EvidenceError('Bestandsmenge/Einstand widerspricht gespeicherter Einstiegskette')
    inst=row['broker_position_id'];currency=row['waehrung'];algo={}
    exit_ids=[str(row['exit_order_id'])] if row.get('exit_order_id') else []
    if not exit_ids:
        for aid in historical_algo_ids(row):
            candidate=broker.client.algo_order_details(aid)
            children=_algo_children(candidate,aid,row)
            if not children:continue
            if algo and candidate!=algo:
                raise EvidenceError('Mehrere ausgeloeste Schutzauftraege verlangen getrennten Teilabgleich')
            algo=candidate;exit_ids=children
    if not exit_ids or len(exit_ids)>8:
        raise EvidenceError('Primaere Verkaufsorder fehlt oder Kindorder-Budget ueberschritten')
    exits=[_order(broker,inst,oid,'sell',currency).proof for oid in sorted(set(exit_ids))]
    entry=_order(broker,inst,row['entry_order_id'],'buy',currency).proof
    # Existing raw entry fills must agree. Never overwrite a known purchase.
    from okx_residual_inventory import entry_from_ledger
    with closing(tl._connect()) as con, con:
        local,_=entry_from_ledger(con,row,proven_order_currency=entry["currency"])
    if (set(local['fill_ids'])!=set(entry['fill_ids']) or not same_quantity(local['quantity'],entry['quantity'])
            or not same_quantity(local['cash'],entry['cash'])):
        raise EvidenceError('Kauf-Nachbeleg widerspricht der vorhandenen Rohfillkette')
    meta=broker.client.instrument(inst)
    return _build(row,family,entry,exits,algo=algo,lot_size=meta.lot_size)


def apply(original, receipt, *, import_record=None):
    """One SQLite transaction: complete receipt, exclusive claims and all money."""
    import trade_ledger as tl
    from okx_residual_inventory import lineage, record
    init();tid=original['trade_id'];payload=encode(receipt);digest=hashlib.sha256(payload.encode()).hexdigest()
    with tl._LOCK,closing(tl._connect()) as con, con:
        con.execute('BEGIN IMMEDIATE')
        previous=con.execute('SELECT receipt_hash FROM okx_closed_result_receipts WHERE trade_id=?',(tid,)).fetchone()
        if previous:
            if previous['receipt_hash']!=digest:raise EvidenceError('Abweichender Nachbeleg zu bereits abgeschlossenem Abgleich')
            return False
        current=con.execute('SELECT * FROM trades WHERE trade_id=?',(tid,)).fetchone()
        if not current or dict(current)!=original:raise EvidenceError('Ledger wurde zwischen Lesen und Anwenden veraendert')
        family=lineage(con,original)
        if _family_snapshot(family)!=receipt['lineage_snapshot']:
            raise EvidenceError('Kaufteil-Allokation wurde zwischen Lesen und Anwenden veraendert')
        checked=_build(original,family,receipt['entry_proof'],receipt['exit_proofs'],algo=receipt['algo'],
            lot_size=receipt['lot_size'],tiny_legacy=receipt['tiny_legacy'])
        if encode(checked)!=payload:raise EvidenceError('Reparaturplan weicht von seiner eigenen Rohbelegrechnung ab')
        if receipt['trade_id']!=tid or receipt['account']!=original['broker_account_fingerprint'] or receipt['environment']!=('DEMO' if original['paper'] else 'LIVE'):
            raise EvidenceError('Reparaturplan passt nicht zum Zielkonto')
        for oid in receipt['exit_order_ids']:
            others=con.execute('''SELECT * FROM trades WHERE broker='okx' AND broker_account_fingerprint=? AND paper=?
                AND trade_id<>? AND superseded_by IS NULL AND exit_order_id=?''',
                (receipt['account'],int(original['paper']),tid,oid)).fetchall()
            for other in others:
                peer=set(json.loads(other['exit_fill_ids_json'] or '[]'))
                if not peer or peer & set(receipt['fill_ids']):
                    raise EvidenceError('Verkaufsorder wird bereits von einem anderen Trade beansprucht')
        for fid in receipt['fill_ids']:
            conflict=con.execute('''SELECT e.trade_id FROM trade_exit_events e JOIN trades t ON t.trade_id=e.trade_id
                WHERE e.broker='okx' AND e.broker_account_fingerprint=? AND e.fill_id=? AND e.trade_id<>? AND t.paper=?''',
                (receipt['account'],fid,tid,int(original['paper']))).fetchone()
            if conflict:raise EvidenceError('Verkaufsfill ist bereits einem anderen Trade zugeordnet')
            claim=con.execute('SELECT trade_id FROM trade_native_exit_fills WHERE broker=? AND account=? AND environment=? AND fill_id=?',
                ('okx',receipt['account'],receipt['environment'],fid)).fetchone()
            if claim and claim['trade_id']!=tid:raise EvidenceError('Verkaufsfill ist bereits beansprucht')
            con.execute('INSERT OR IGNORE INTO trade_native_exit_fills VALUES(?,?,?,?,?)',('okx',receipt['account'],receipt['environment'],fid,tid))
        events=[dict(r) for r in con.execute('SELECT * FROM trade_exit_events WHERE trade_id=?',(tid,))]
        if events and any(r['fill_id'] not in receipt['fill_ids'] for r in events):
            raise EvidenceError('Vorhandene Exit-Ereignisse verlangen einen gesonderten Abgleich')
        now=datetime.now(timezone.utc).isoformat()
        con.execute('INSERT INTO okx_closed_result_receipts VALUES(?,?,?,?,?)',(tid,digest,encode(original),payload,now))
        if not events:
            for fill in receipt['fills']:
                # Existing event contract: aggregate execution-group amounts.
                # Exact per-fill cash/fees are retained in the immutable receipt.
                con.execute('''INSERT INTO trade_exit_events
                    (trade_id,broker,broker_account_fingerprint,instrument,order_id,fill_id,event_id,quantity,price,fee,booked_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?)''',(tid,'okx',receipt['account'],receipt['inst_id'],str(fill['ordId']),
                    okx_fill_identity(fill,receipt['account']),'okx-closed-receipt:'+digest,receipt['quantity'],receipt['price'],receipt['exit_fee'],now))
        else:
            for event in events:
                if event['fee'] is not None and abs(number(event['fee'])-number(receipt['exit_fee']))>Decimal('1e-8'):
                    raise EvidenceError('Exit-Gebuehrenereignis widerspricht dem Nachbeleg')
            con.execute('UPDATE trade_exit_events SET fee=? WHERE trade_id=? AND fee IS NULL',(receipt['exit_fee'],tid))
        reason={'sl':'Broker-Stop-Loss (Ausfuehrung belegt)','tp':'Broker-Take-Profit (Ausfuehrung belegt)'}.get(receipt['algo'].get('actualSide'), original.get('exit_grund'))
        con.execute('''UPDATE trades SET menge=?,einstieg_gebuehr=?,entry_cost_basis=?,eingestiegen_am=?,ausgestiegen_am=?,ausstieg_preis=?,
            brutto_pnl=?,gebuehren=?,netto_pnl=?,fee_quality='BROKER_CONFIRMED',entry_fee_quality='BROKER_CONFIRMED',exit_fee_quality='BROKER_CONFIRMED',
            exit_order_id=?,exit_fill_ids_json=?,exit_grund=?,protection_status='TRADE_CLOSED',
            protection_detail='Abschluss durch exakte Order-/Fillbelege bestaetigt; historische Schutzanker bleiben im Audit',
            reconciliation_status='CLOSED',reconciliation_updated_at=?,haltedauer_minuten=? WHERE trade_id=?''',
            (receipt['quantity'],receipt['entry_fee'],float(number(receipt['cost_basis'])),receipt['entered_at'],receipt['closed_at'],receipt['price'],
            receipt['gross'],receipt['fees'],receipt['net'],receipt['exit_order_ids'][0] if len(receipt['exit_order_ids'])==1 else '',
            encode(receipt['fill_ids']),reason,now,receipt['holding_minutes'],tid))
        con.execute("UPDATE trades SET notiz=? WHERE trade_id=?",(
            '9.8.8: Abschluss mit Original-Ausfuehrungsbelegen nachgetragen. Fruehere Notiz (historisch): '+str(original.get('notiz') or ''),tid))
        if number(receipt['residual'])>0:
            record(con,original,dict(trade_id=tid,account=receipt['account'],environment=receipt['environment'],inst=receipt['inst_id'],
                oid=receipt['entry_order_id'],currency=receipt['currency'],quantity=receipt['residual'],cost_basis=receipt['residual_cost'],
                separate_row=False,source_receipt_hash=digest,entry=receipt['entry_proof'],exits=receipt['exit_proofs']),separate_row=False)
        con.execute("UPDATE okx_balance_gaps SET status='RESOLVED',resolution=?,last_seen=? WHERE trade_id=? AND account=? AND environment=?",
            (digest,now,tid,receipt['account'],receipt['environment']))
        if import_record is not None:
            con.execute("INSERT INTO okx_receipt_imports VALUES(?,?,?,?,?)",
                (import_record["source_hash"],tid,import_record["source_schema"],encode(import_record["data"]),now))
        con.commit()
    return True


def run_one(broker, *, now=None):
    """One bounded lineage per cycle; residual evidence may repair sibling sales."""
    import trade_ledger as tl
    from okx_residual_inventory import classify, classify_existing
    now=time.time() if now is None else float(now);init()
    classify_existing(account=broker.account_fingerprint(),paper=broker.demo,limit=50)
    with closing(tl._connect()) as con, con:
        rows=con.execute('''SELECT t.* FROM trades t LEFT JOIN okx_closed_result_checks c ON c.trade_id=t.trade_id
            WHERE t.broker='okx' AND t.broker_account_fingerprint=? AND t.paper=?
              AND t.ausgestiegen_am IS NOT NULL AND COALESCE(t.superseded_by,0)=0 AND t.accounting_kind='TRADE'
              AND (t.ausstieg_preis IS NULL OR t.netto_pnl IS NULL OR COALESCE(t.fee_quality,'UNKNOWN') NOT IN ('CONFIRMED','BROKER_CONFIRMED','KNOWN'))
              AND COALESCE(c.checked_at,0)<? ORDER BY COALESCE(c.checked_at,0),t.trade_id LIMIT 1''',
            (broker.account_fingerprint(),int(broker.demo),now-300)).fetchall()
    if not rows:return dict(status='IDLE',detail='Kein faelliger geschlossener OKX-Ergebnisfall')
    original=dict(rows[0]);tid=original['trade_id']
    try:
        from okx_reference_valuation import load_on
        with closing(tl._connect()) as con:
            valued=load_on(con,original)
        if valued and valued['cross_currency']:
            with closing(tl._connect()) as con,con:
                con.execute('INSERT OR REPLACE INTO okx_closed_result_checks VALUES(?,?,?,?)',
                    (tid,now,'REFERENCE_CONFIRMED','Nativer Abschluss und separate EUR-Referenzbewertung vollstaendig belegt'))
            return dict(trade_id=tid,status='REFERENCE_CONFIRMED',checked_at=now)
        # A historical dust label is only a search hint. Raw sold siblings prove it.
        if 'Rundungsrest' in str(original.get('exit_grund') or ''):
            family=tl.entry_lineage('okx',original['broker_position_id'],original['entry_order_id'],original['broker_account_fingerprint'],paper=bool(original['paper']))
            siblings=[r for r in family if r['trade_id']!=tid and r.get('ausgestiegen_am') and r.get('exit_order_id')]
            if not siblings or len(siblings)>4:raise EvidenceError('Rest ohne begrenzte belegbare Verkaufskette')
            for member in siblings:
                if not confirmed_net(member):apply(member,prepare(member,broker))
            changed=classify(tid)
            status,detail='RESIDUAL','Nicht verkaufter Rest durch vollstaendige Kauf-/Verkaufsbelege bestaetigt'
        else:
            receipt=prepare(original,broker);changed=apply(original,receipt)
            status,detail='CONFIRMED','Order-/Fill-/Gebuehrenbelege atomar nachgetragen' if changed else 'Identischer Nachbeleg bereits verbucht'
        classify_existing(account=broker.account_fingerprint(),paper=broker.demo,limit=50)
    except (ValueError,BrokerFehler,ArithmeticError,KeyError,TypeError) as exc:
        status,detail='PENDING',str(exc)[:450]
    with closing(tl._connect()) as con, con:
        con.execute('INSERT OR REPLACE INTO okx_closed_result_checks VALUES(?,?,?,?)',(tid,now,status,detail))
    return dict(trade_id=tid,status=status,detail=detail,checked_at=now)


class _ReadOnlyClient(OKXClient):
    def request(self, method, path, **kwargs):
        if method.upper() != 'GET' or kwargs.get('is_order'):
            raise BrokerFehler('Ergebnisabgleich darf keine Broker-Schreibaktion ausfuehren')
        if time.monotonic() > self.deadline or self.calls >= 24:
            raise BrokerFehler('Lesebudget erreicht; Abgleich wird spaeter fortgesetzt')
        self.calls += 1
        return super().request(method, path, **kwargs)


def schedule(broker):
    """Nonblocking; separate HTTP session, same account/environment and limiters."""
    if not isinstance(broker, OKXBroker) or not isinstance(broker.client, OKXClient):
        return dict(status='UNAVAILABLE', detail='Kein eingerichteter OKX-Adapter')
    key = (broker.account_fingerprint(), bool(broker.demo))
    if not key[0] or not broker.client.hat_zugangsdaten:
        return dict(status='UNAVAILABLE', detail='Kontobindung oder Zugang fehlt')
    with _GUARD:
        item = _JOBS.get(key, {})
        if item.get('thread') and item['thread'].is_alive():
            return dict(status='RUNNING', detail='Begrenzter lesender Ergebnisabgleich laeuft')
        if time.monotonic() - item.get('started', -1e9) < 60:
            return dict(item.get('result') or {})
        item = dict(started=time.monotonic(),result=dict(status='RUNNING'))
        _JOBS[key] = item
        def work():
            source = broker.client
            client = _ReadOnlyClient(source._key,source._secret,source._passphrase,
                demo=broker.demo,base_url=source.base_url,timeout=min(8,source.timeout))
            client.deadline = time.monotonic()+60; client.calls=0
            client._private_limit=source._private_limit;client._public_limit=source._public_limit
            client._clock_offset_seconds=source._clock_offset_seconds
            reader = OKXBroker(client=client,demo=broker.demo,quote_ccy=broker.quote_ccy,allowed_quotes=broker.allowed_quotes)
            reader._account_fingerprint=key[0]
            try:
                result=run_one(reader)
            except Exception as exc:
                log.warning('Geschlossener OKX-Ergebnisabgleich bleibt offen: %s',type(exc).__name__)
                result=dict(status='ERROR',detail='Lesender Abgleich fehlgeschlagen: '+type(exc).__name__)
            finally:
                client.session.close()
            with _GUARD:
                item['result']=result
        thread=threading.Thread(target=work,name='okx-closed-results',daemon=True)
        item['thread']=thread
        thread.start()
        return dict(status='RUNNING',detail='Historische Ergebnisse werden separat lesend abgeglichen')
