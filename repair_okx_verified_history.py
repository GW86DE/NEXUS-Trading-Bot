"""Offline repair of Georg's seven explicitly evidenced historical OKX cases.

Default preview operates on a new SQLite/risk copy. Apply requires stopped
writers and creates an independent backup first. No network or broker mutation.
The two exact supplied archives are required; never deploy a diagnosis database.
"""
from __future__ import annotations
from contextlib import closing
from datetime import datetime, timezone
from decimal import Decimal
import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import zipfile

from okx_receipt_math import EvidenceError, number, prove_order, same_quantity
from okx_reference_valuation import Rates, calculate, encode, init_on, load_on, store_on

TRADE_ARCHIVE_HASH = 'cd0f52340306d655684d0812576bb5acc9ea30b7bb2ba6da634147d844fef145'
FX_ARCHIVE_HASH = 'f0f1af51bc1b790c4ae858fbe5e3e012b568e1d22c4b4c0a9c05a879b1ec916f'
ACCOUNT = '86b720de95d16d5f25a0217a'
CASE_IDS = {25,26,27,28,38,45,55}
MANUAL_SUI_SALE = '3904817860665094145'
REPORT_NAME = 'okx_verified_history_repair_report.json'


def read_archive(path, expected, filename):
    path=Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size>2*1024*1024:
        raise EvidenceError('Erwarteter kleiner Originalbeleg fehlt')
    raw=path.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=expected:
        raise EvidenceError('Beleg stimmt nicht mit dem bereits geprueften Originalexport ueberein: '+path.name)
    with zipfile.ZipFile(path) as z:
        names=z.namelist()
        if len(names)!=3 or set(names)!={filename,'MANIFEST.json','LIES_MICH.txt'}:
            raise EvidenceError('Unerwartete Belegstruktur')
        if any(i.file_size>2*1024*1024 for i in z.infolist()):raise EvidenceError('Beleginhalt zu gross')
        body=z.read(filename);manifest=json.loads(z.read('MANIFEST.json'))
        if manifest.get('sha256')!=hashlib.sha256(body).hexdigest() or manifest.get('bytes')!=len(body):
            raise EvidenceError('Interne Belegpruefsumme falsch')
        return json.loads(body)


def inputs(trades_path,fx_path):
    data=read_archive(trades_path,TRADE_ARCHIVE_HASH,'OKX_BELEGE.json')
    fx=read_archive(fx_path,FX_ARCHIVE_HASH,'FX_BELEGE.json')
    check=data.get('account_verification') or {}
    if (check.get('account_fingerprint')!=ACCOUNT or check.get('environment')!='DEMO'
            or data.get('service_identity_changed') or fx.get('source_zip_sha256')!=TRADE_ARCHIVE_HASH):
        raise EvidenceError('Unpassende Kontodomaene oder Quellenkette')
    rates=Rates(fx)
    proofs={}
    for item in data['orders']:
        if len(item['order_details'])!=1:raise EvidenceError('Keine eindeutige Primaerorder')
        order=item['order_details'][0];quotes={f.get('tradeQuoteCcy') for f in item['fills']}
        if len(quotes)!=1 or not next(iter(quotes)):raise EvidenceError('Fillwaehrung nicht eindeutig')
        proof=prove_order(order,item['fills'],inst=order['instId'],oid=item['order_id'],side=item['side'],
            base=order['instId'].split('-')[0],currency=next(iter(quotes)),account=ACCOUNT)
        if proof['order_id'] in proofs:raise EvidenceError('Doppelte Primaerorder')
        proofs[proof['order_id']]=proof
    if len(proofs)!=14 or {x['trade_id'] for x in data['target_mapping']}!=CASE_IDS:
        raise EvidenceError('Andere historische Fallmenge')
    # Fully prove each reference calculation before any copy/ledger migration.
    for case in data['target_mapping']:
        calculate(proofs[case['entry_order_id']],proofs[case['exit_order_id']],rates,account=ACCOUNT,environment='DEMO')
    return data,rates,proofs


def _row(con,case):
    row=con.execute('SELECT * FROM trades WHERE trade_id=?',(case['trade_id'],)).fetchone()
    if not row:raise EvidenceError('Historische Zielzeile fehlt')
    row=dict(row)
    if (row['broker']!='okx' or row['paper']!=1 or row['broker_account_fingerprint']!=ACCOUNT
            or row['entry_order_id']!=case['entry_order_id'] or row['broker_position_id']!=case['entry_instrument']
            or row['symbol']!=case['symbol'] or row.get('superseded_by') is not None
            or not row.get('ausgestiegen_am') or row.get('accounting_kind')!='TRADE'
            or row.get('ownership_status') not in {'VERIFIED','BOT_VERIFIED','VERIFIED_BROKER_FILL_CHAIN'}):
        raise EvidenceError('Aktuelle Zielzeile widerspricht der belegten Einstiegskette')
    if row.get('exit_order_id') not in (None,'',case['exit_order_id']):
        raise EvidenceError('Aktuelle Verkaufsorder widerspricht dem expliziten Suchanker')
    return row


def _cross_currency(con,row,entry,sale,rates):
    """One known manual SUI chain, atomic native claims + residual + valuation."""
    import trade_ledger as tl
    from okx_external_settlement import native_receipt, record_native_exit
    from okx_residual_inventory import entry_from_ledger, record
    # This path needs the manual confirmation already supplied by Georg.
    if row['trade_id']!=45 or sale['order_id']!=MANUAL_SUI_SALE or sale['inst_id']!='SUI-EUR':
        raise EvidenceError('Kein explizit bestaetigter manueller Fremdwaehrungsabschluss')
    local,family=entry_from_ledger(con,row,proven_order_currency=entry['currency'])
    if len(family)!=1 or set(local['fill_ids'])!=set(entry['fill_ids']) or not same_quantity(local['cash'],entry['cash']):
        raise EvidenceError('SUI-Kaufherkunft ist nicht eindeutig')
    residual=number(entry['quantity'])-number(sale['quantity'])
    if residual<0 or residual>=Decimal('0.01'):
        raise EvidenceError('SUI-Abgang passt nicht zum belegten Mengenraster')
    # Native recording is its own transaction and can be resumed after a crash.
    evidence=native_receipt(sale['fills'],account=ACCOUNT,environment='DEMO',
        entry_instrument=entry['inst_id'],expected_quantity=row['menge'],lot_size='0.01',
        expected_order_id=sale['order_id'],manual_confirmed=True)
    record_native_exit(entry_order_id=entry['order_id'],entry_instrument=entry['inst_id'],
        account=ACCOUNT,paper=True,evidence=evidence,residual=Decimal(evidence['residual']))
    valuation=calculate(entry,sale,rates,account=ACCOUNT,environment='DEMO')
    with tl._LOCK,closing(tl._connect()) as target,target:
        target.execute('BEGIN IMMEDIATE')
        current=dict(target.execute('SELECT * FROM trades WHERE trade_id=?',(row['trade_id'],)).fetchone())
        if current.get('netto_pnl') is not None or current.get('ausstieg_preis') is not None:
            raise EvidenceError('Vorhandenes natives SUI-Ergebnis wird nicht ueberschrieben')
        if not same_quantity(current['menge'],entry['quantity']):
            raise EvidenceError('SUI-Bestandszeile wurde parallel veraendert')
        qty=number(sale['quantity']);cost=number(entry['cash'])*qty/number(entry['quantity'])
        entry_fee=cost-number(entry['price'])*qty
        target.execute('''UPDATE trades SET menge=?,entry_cost_basis=?,einstieg_gebuehr=?,eingestiegen_am=?,
            entry_fee_quality='BROKER_CONFIRMED',exit_fee_quality='BROKER_CONFIRMED',fee_quality='BROKER_CONFIRMED',
            haltedauer_minuten=?,notiz=? WHERE trade_id=?''',
            (float(qty),float(cost),float(entry_fee),valuation['entered_at'],
             (datetime.fromisoformat(valuation['closed_at'])-datetime.fromisoformat(valuation['entered_at'])).total_seconds()/60,
             'Manueller SUI-EUR-Verkauf mit Originalbelegen zugeordnet; separate EUR-Referenzbewertung vorhanden.',row['trade_id']))
        if residual:
            record(target,current,dict(trade_id=row['trade_id'],account=ACCOUNT,environment='DEMO',inst=entry['inst_id'],
                oid=entry['order_id'],currency=entry['currency'],quantity=str(residual),
                cost_basis=str(number(entry['cash'])*residual/number(entry['quantity'])),separate_row=False,
                source_receipt_hash=hashlib.sha256(encode(valuation).encode()).hexdigest(),entry=entry,exits=[sale]),separate_row=False)
        updated=dict(target.execute('SELECT * FROM trades WHERE trade_id=?',(row['trade_id'],)).fetchone())
        store_on(target,updated,valuation,rates)
    return valuation


def run(root,data,rates,proofs):
    from repair_okx_accounting import selected_database, offline_risk_sync
    from ledger_result import confirmed_net
    with selected_database(root):
        import trade_ledger as tl
        import okx_closed_reconciliation as cr
        from okx_accounting import status
        from okx_residual_inventory import entry_from_ledger, public_inventory
        cr.init()
        # The independent runtime/risk domain must corroborate the export.
        runtime=json.loads((root/'runtime_status_okx.json').read_text())
        risk=json.loads((root/'risk_state_okx.json').read_text())
        if (runtime.get('account_fingerprint')!=ACCOUNT or runtime.get('modus')!='DEMO'
                or runtime.get('kapital_waehrung')!='EUR' or ':okx:EUR:' not in str(risk.get('equity_basis_key'))):
            raise EvidenceError('Aktueller Laufzeit-/Risikokontext passt nicht zur Reparatur')
        with closing(tl._connect()) as con,con:
            init_on(con)
            # Preflight all current entries before the first historical posting.
            originals={case['trade_id']:_row(con,case) for case in data['target_mapping']}
            for case in data['target_mapping']:
                row=originals[case['trade_id']]
                prior=load_on(con,row)
                if prior:continue
                entry=proofs[case['entry_order_id']]
                local,_=entry_from_ledger(con,row,proven_order_currency=entry['currency'])
                if set(local['fill_ids'])!=set(entry['fill_ids']) or not same_quantity(local['cash'],entry['cash']):
                    raise EvidenceError('Vorhandene Kaufbelege widersprechen dem Import')
                # These cases consume entire sell orders; no sibling allocation is inferred.
                if con.execute('''SELECT 1 FROM trades WHERE broker='okx' AND paper=1 AND
                    broker_account_fingerprint=? AND exit_order_id=? AND trade_id<>?''',
                    (ACCOUNT,case['exit_order_id'],row['trade_id'])).fetchone():
                    raise EvidenceError('Verkaufsorder ist bereits einer anderen Handelszeile zugeordnet')
        before=status(ACCOUNT,'DEMO');applied=[];unchanged=[]
        by_id={b['order_id']:b for b in data['orders']}
        class Adapter:
            demo=True
            name='okx'
            def account_fingerprint(self):return ACCOUNT
            def read_primary_order_status(self,inst,*,order_id,side):
                proof=proofs[order_id]
                if proof['inst_id']!=inst or proof['side']!=side:raise EvidenceError('Falscher Offline-Orderabruf')
                return proof['status']
            def order_fills(self,inst,oid,**kwargs):
                if proofs[oid]['inst_id']!=inst:raise EvidenceError('Falsches Offline-Fillinstrument')
                return proofs[oid]['fills']
            def instrument(self,inst):
                rows=data['instrument_rules_at_collection'][inst]
                if len(rows)!=1 or rows[0]['instId']!=inst:raise EvidenceError('Instrumentregeln nicht eindeutig')
                return SimpleNamespace(inst_id=inst,base_ccy=rows[0]['baseCcy'],lot_size=rows[0]['lotSz'])
        broker=Adapter();broker.client=broker
        for case in data['target_mapping']:
            with closing(tl._connect()) as con,con:
                row=_row(con,case);old=load_on(con,row)
            if old:
                unchanged.append(row['trade_id']);continue
            entry=proofs[case['entry_order_id']];sale=proofs[case['exit_order_id']]
            if entry['currency']!=sale['currency']:
                with closing(tl._connect()) as con:
                    valuation=_cross_currency(con,row,entry,sale,rates)
            else:
                if not confirmed_net(row):
                    receipt=cr.prepare(dict(row,exit_order_id=case['exit_order_id']),broker)
                    if row['trade_id']==25:
                        algo=by_id[case['exit_order_id']]['related_algos'][0]
                        if cr._algo_children(algo,algo['algoId'],row)!=[sale['order_id']] or sale['status'].get('algoId')!=algo['algoId']:
                            raise EvidenceError('BNB-Schutz-/Verkaufsanker widersprechen sich')
                        family=tl.entry_lineage('okx',entry['inst_id'],entry['order_id'],ACCOUNT,paper=True)
                        receipt=cr._build(row,family,entry,[sale],algo=algo,lot_size=receipt['lot_size'])
                    cr.apply(row,receipt)
                valuation=calculate(entry,sale,rates,account=ACCOUNT,environment='DEMO')
                with tl._LOCK,closing(tl._connect()) as con,con:
                    con.execute('BEGIN IMMEDIATE')
                    current=_row(con,case)
                    store_on(con,current,valuation,rates)
            applied.append(dict(trade_id=case['trade_id'],net_eur=valuation['net_eur'],
                quality=valuation['quality'],method=valuation['method']))
        risk_report=offline_risk_sync(root)
        from risk_pots import RiskPot
        buy_check=RiskPot('okx',state_datei=str(root/'risk_state_okx.json')).darf_kaufen()
        with closing(tl._connect()) as con:
            integrity=con.execute('PRAGMA quick_check').fetchone()[0]
            values=[dict(trade_id=case['trade_id'],net_eur=load_on(con,_row(con,case))['net_eur']) for case in data['target_mapping']]
        return dict(schema='nexus-verified-history-repair-v1',at=datetime.now(timezone.utc).isoformat(),
            account=ACCOUNT,environment='DEMO',orders_sent=0,network_requests=0,before=before,
            after=status(ACCOUNT,'DEMO'),applied_cases=applied,already_applied=unchanged,
            eur_values=values,risk=risk_report,risk_buy_check=buy_check,sqlite_integrity=integrity,
            inventory=public_inventory(broker='okx'),method='EUR_REFERENCE_CASHFLOWS_V1')


def repair(root,trades_path,fx_path,*,apply=False,workers_stopped=False):
    from repair_okx_accounting import backup_state
    data,rates,proofs=inputs(trades_path,fx_path)
    root=Path(root).expanduser()
    if root.is_symlink() or not root.is_dir():raise EvidenceError('Kein eindeutiger Zustandsordner')
    root=root.resolve()
    for name in ('decision_history.sqlite','risk_state_okx.json','runtime_status_okx.json'):
        path=root/name
        if path.is_symlink() or not path.is_file():raise EvidenceError('Erforderlicher Zustand fehlt: '+name)
    if apply and not workers_stopped:raise EvidenceError('Anwenden nur mit gestoppten Writern')
    if not apply:
        with tempfile.TemporaryDirectory(prefix='nexus_verified_preview_') as tmp:
            copy=Path(tmp)/'state';backup_state(root,copy)
            report=run(copy,data,rates,proofs)
            return {**report,'preview':True,'applied':False,'source':str(root)}
    backup=root.parent/('NEXUS_Belegbackup_'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S_%f'))
    hashes=backup_state(root,backup)
    report=run(root,data,rates,proofs)
    report.update(preview=False,applied=True,source=str(root),backup=str(backup),backup_sha256=hashes)
    from safe_persistence import atomic_write_json
    atomic_write_json(root/REPORT_NAME,report)
    return report


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--source',required=True,type=Path)
    ap.add_argument('--trades',required=True,type=Path)
    ap.add_argument('--fx',required=True,type=Path)
    ap.add_argument('--apply',action='store_true')
    ap.add_argument('--workers-stopped',action='store_true')
    ap.add_argument('--output',type=Path)
    args=ap.parse_args();os.umask(0o077)
    if args.apply:
        if not args.workers_stopped:raise SystemExit('--workers-stopped ist Pflicht')
        from installer_host import Host
        Host().other_writers(args.source.resolve())
    report=repair(args.source,args.trades,args.fx,apply=args.apply,workers_stopped=args.workers_stopped)
    if args.output:
        from safe_persistence import atomic_write_json
        atomic_write_json(args.output,report)
    print(json.dumps(report,indent=2,ensure_ascii=False))
    return 0


if __name__=='__main__':raise SystemExit(main())
