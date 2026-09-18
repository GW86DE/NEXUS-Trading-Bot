"""Explicit, read-only-source import of an operator-provided OKX receipt bundle.

The bundle is not executable. No ID, account or amount is guessed; current local
entry fills must corroborate it. Import is optional, offline, audited and cannot
place orders. A later/current production database must never be replaced by the
old diagnosis database that accompanied a report.
"""
from __future__ import annotations
from contextlib import closing
from pathlib import Path, PurePosixPath
from decimal import Decimal
import hashlib
import json
import zipfile
from okx_receipt_math import EvidenceError, prove_order, same_quantity

MAX_BYTES = 12 * 1024 * 1024


def read_bundle(path):
    path=Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size>MAX_BYTES:
        raise EvidenceError('Belegdatei fehlt, ist ein Symlink oder zu gross')
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as z:
            entries=z.infolist()
            if len(entries)>30 or sum(i.file_size for i in entries)>MAX_BYTES:
                raise EvidenceError('Belegarchiv ueberschreitet Lesebudget')
            names=[i.filename for i in entries]
            if len(names)!=len(set(names)):
                raise EvidenceError('Doppelte Archivpfade')
            for name in names:
                if '\\' in name or PurePosixPath(name).is_absolute() or '..' in PurePosixPath(name).parts:
                    raise EvidenceError('Unsicherer Archivpfad')
            matches=[i for i in entries if PurePosixPath(i.filename).name=='DOGE_Belege.json']
            if len(matches)!=1:raise EvidenceError('Genau eine DOGE_Belege.json erforderlich')
            raw=z.read(matches[0])
    else:
        raw=path.read_bytes()
    if len(raw)>MAX_BYTES:raise EvidenceError('Beleginhalt zu gross')
    def unique(pairs):
        d={}
        for k,v in pairs:
            if k in d:raise EvidenceError('Doppelter JSON-Schluessel')
            d[k]=v
        return d
    data=json.loads(raw,object_pairs_hook=unique)
    return data,hashlib.sha256(raw).hexdigest()


def _rows(wrapper, endpoint, params, env):
    if not isinstance(wrapper,dict) or wrapper.get('method')!='GET' or wrapper.get('endpoint')!=endpoint:
        raise EvidenceError('Falscher Rohbeleg-Endpunkt')
    if (wrapper.get('environment')!=env or wrapper.get('http_status')!=200 or wrapper.get('ok') is not True
            or wrapper.get('params')!=params):
        raise EvidenceError('Rohbeleg ohne erfolgreichen exakten GET-Kontext')
    response=wrapper.get('response') or {}
    if response.get('code')!='0' or not isinstance(response.get('data'),list):
        raise EvidenceError('Erfolgreiche API-Antwort fehlt')
    return response['data']


def extract(data):
    if (data.get('schema')!='nexus-doge-sale-readonly-v1' or data.get('read_only') is not True
            or data.get('automatic_booking') is not False):
        raise EvidenceError('Nicht unterstuetztes lesendes Belegformat')
    account=data.get('expected_account_fingerprint');env=data.get('environment');inst=data.get('instrument')
    check=data.get('account_check') or {}
    if (not account or env not in {'DEMO','LIVE'} or not inst or check.get('matched') is not True
            or check.get('ok') is not True or check.get('fingerprint')!=account or check.get('api_code')!='0'):
        raise EvidenceError('Kontopruefung des Belegexports fehlt/widerspricht')
    sale=data.get('user_supplied_sell_order')
    if not isinstance(sale,str) or not sale.isdigit():raise EvidenceError('Verkaufsorder ohne unverkuerzte ID')
    orders=data.get('orders') or {}
    purchase=(orders.get('purchase') or {}).get('params',{}).get('ordId')
    if not isinstance(purchase,str) or not purchase.isdigit():raise EvidenceError('Kauforder-ID fehlt')
    def order(key,oid):
        rows=_rows(orders.get(key),'/api/v5/trade/order',dict(instId=inst,ordId=oid),env)
        if len(rows)!=1:raise EvidenceError('Primaere Order nicht eindeutig')
        return rows[0]
    buy=order('purchase',purchase);sell=order('sale',sale)
    fills=[]
    for endpoint,w in (data.get('sale_fills') or {}).items():
        if endpoint not in {'/api/v5/trade/fills','/api/v5/trade/fills-history'}:
            raise EvidenceError('Nicht unterstuetzter Fill-Endpunkt')
        pages=w.get('pages') or []
        if not isinstance(pages,list) or not pages or len(pages)>20:
            raise EvidenceError('Ausfuehrungsseiten fehlen oder Lesebudget ueberschritten')
        for page in pages:
            params=page.get('params') or {}
            if params.get('ordId')!=sale or params.get('instId')!=inst or params.get('instType')!='SPOT' or set(params)-{'instType','instId','ordId','limit','after'}:
                raise EvidenceError('Ausfuehrungsabruf nicht orderbezogen')
            fills.extend(_rows(page,endpoint,params,env))
    if not fills:raise EvidenceError('Verkaufsfills fehlen')
    algo_wrapper=data.get('historical_algo') or {}
    aid=(algo_wrapper.get('params') or {}).get('algoId')
    algos=_rows(algo_wrapper,'/api/v5/trade/order-algo',dict(algoId=aid),env)
    if len(algos)!=1:raise EvidenceError('Schutzorderbeleg nicht eindeutig')
    return dict(account=account,environment=env,inst=inst,purchase=purchase,sale=sale,
        buy=buy,sell=sell,fills=fills,algo=algos[0])


def prepare(data, payload_hash):
    import trade_ledger as tl
    import okx_closed_reconciliation as cr
    from okx_residual_inventory import entry_from_ledger
    cr.init();parts=extract(data)
    with closing(tl._connect()) as con, con:
        prior=con.execute('SELECT trade_id FROM okx_receipt_imports WHERE source_hash=?',(payload_hash,)).fetchone()
        if prior:
            proof=con.execute('SELECT receipt_json FROM okx_closed_result_receipts WHERE trade_id=?',(prior['trade_id'],)).fetchone()
            row=con.execute('SELECT * FROM trades WHERE trade_id=?',(prior['trade_id'],)).fetchone()
            if not proof or not row:raise EvidenceError('Importnachweis ohne bestehende Abrechnung')
            receipt=json.loads(proof[0]);row=dict(row)
            if (row['broker_account_fingerprint']!=parts['account'] or bool(row['paper'])!=(parts['environment']=='DEMO')
                    or row['exit_order_id']!=parts['sale'] or row['netto_pnl']!=receipt['net']
                    or row['menge']!=receipt['quantity'] or row['ausstieg_preis']!=receipt['price']):
                raise EvidenceError('Bereits importierter Ergebnisstand wurde veraendert')
            return dict(status='ALREADY_APPLIED',trade_id=row['trade_id'],receipt=receipt)
        rows=[dict(r) for r in con.execute('''SELECT * FROM trades WHERE broker='okx' AND broker_account_fingerprint=?
            AND paper=? AND broker_position_id=? AND entry_order_id=? AND superseded_by IS NULL''',
            (parts['account'],int(parts['environment']=='DEMO'),parts['inst'],parts['purchase']))]
        candidates=[r for r in rows if r['ausgestiegen_am'] and r.get('accounting_kind')!='RESIDUAL'
            and r.get('exit_order_id') in ('',parts['sale'])]
        if len(candidates)!=1:raise EvidenceError('Aktuelle Zielkaufkette fehlt oder ist mehrdeutig')
        row=candidates[0]
        if row['ownership_status'] not in {'VERIFIED','BOT_VERIFIED','VERIFIED_BROKER_FILL_CHAIN'}:
            raise EvidenceError('Bot-Eigentum der Zielkette fehlt')
        if json.loads(row.get('exit_native_json') or '{}'):
            raise EvidenceError('Nativer Fremdwaehrungsabschluss verlangt eigenen Import')
        aid=str(parts['algo'].get('algoId') or '')
        # Exact historical client anchor found in the current DB is mandatory.
        if aid not in cr.historical_algo_ids(row):
            raise EvidenceError('Schutzorder hat keinen gespeicherten Anker in der aktuellen Einstiegskette')
        children=cr._algo_children(parts['algo'],aid,row)
        if (children!=[parts['sale']] or parts['sell'].get('algoId')!=aid
                or parts['sell'].get('algoClOrdId')!=row.get('protection_client_order_id')):
            raise EvidenceError('Beidseitige Schutz-/Verkaufsorder-Verknuepfung fehlt')
        currency=row['waehrung'];base=parts['inst'].split('-')[0]
        # The local immutable entry fills are independently matched to this exact order.
        stored=[dict(r) for r in con.execute('''SELECT f.* FROM trade_entry_fills f JOIN trades t ON t.trade_id=f.trade_id
            WHERE f.broker='okx' AND f.broker_account_fingerprint=? AND f.instrument=? AND f.order_id=? AND t.paper=?''',
            (parts['account'],parts['inst'],parts['purchase'],int(row['paper'])))]
        entry_fills=[json.loads(f['raw_json']) for f in stored]
        entry=prove_order(parts['buy'],entry_fills,inst=parts['inst'],oid=parts['purchase'],side='buy',
            base=base,currency=currency,account=parts['account'])
        local,family=entry_from_ledger(con,row,proven_order_currency=entry['currency'])
        if set(local['fill_ids'])!=set(entry['fill_ids']) or not same_quantity(local['cash'],entry['cash']):
            raise EvidenceError('Lokale Kauf-Fills widersprechen dem Nachbeleg')
        if parts['buy'].get('clOrdId') and parts['buy']['clOrdId']!=row['client_order_id']:
            raise EvidenceError('Kauf-Client-ID widerspricht der Zielkette')
        sale=prove_order(parts['sell'],parts['fills'],inst=parts['inst'],oid=parts['sale'],side='sell',
            base=base,currency=currency,account=parts['account'])
        existing=con.execute('SELECT * FROM okx_closed_result_receipts WHERE trade_id=?',(row['trade_id'],)).fetchone()
        if existing:
            if hashlib.sha256(existing['receipt_json'].encode()).hexdigest()!=existing['receipt_hash']:
                raise EvidenceError('Bestehender Nachbeleghash falsch')
            saved=json.loads(existing['receipt_json']);original=json.loads(existing['original_json'])
            proof=cr._build(original,saved['lineage_snapshot'],entry,[sale],algo=parts['algo'],
                lot_size=saved['lot_size'],tiny_legacy=saved['tiny_legacy'])
            keys=('account','environment','inst_id','currency','entry_order_id','exit_order_ids','fill_ids',
                'quantity','residual','cost_basis','price','gross','fees','net','closed_at','entered_at')
            if any(saved[k]!=proof[k] for k in keys) or any(row[k]!=proof[v] for k,v in
                    [('netto_pnl','net'),('ausstieg_preis','price'),('brutto_pnl','gross'),('gebuehren','fees'),('menge','quantity'),('ausgestiegen_am','closed_at')]):
                raise EvidenceError('Nachbeleg widerspricht dem bereits bestaetigten Ergebnis')
            return dict(status='ALREADY_VERIFIED',trade_id=row['trade_id'],receipt=saved)
        receipt=cr._build(row,family,entry,[sale],algo=parts['algo'],tiny_legacy=True)
    return dict(status='READY',trade_id=row['trade_id'],original=row,receipt=receipt,
        import_record=dict(source_hash=payload_hash,source_schema=data['schema'],data=parts))


def import_bundle(path, *, apply=False):
    import okx_closed_reconciliation as cr
    data,sha=read_bundle(path);plan=prepare(data,sha)
    if apply and plan['status']=='READY':
        changed=cr.apply(plan['original'],plan['receipt'],import_record=plan['import_record'])
        plan['status']='APPLIED' if changed else 'ALREADY_APPLIED'
    return {k:v for k,v in plan.items() if k not in {'original','import_record'}}
