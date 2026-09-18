#!/usr/bin/env python3
"""Read-only local result inventory; no configuration, credentials or broker API.

A live SQLite/WAL snapshot is read in one transaction. Never use immutable=1
against a running WAL database. No guessed fees, migration or state reset.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sqlite3


def inspect(database: Path) -> dict:
    database=Path(database).expanduser().resolve(strict=True)
    report={"version":"9.8.8","read_only":True,"database":str(database),"at":datetime.now(timezone.utc).isoformat(),
            "scope":"Lokaler Zustand, keine aktuelle Brokerbestaetigung","items":[],"domains":{}}
    con=sqlite3.connect(database.as_uri()+'?mode=ro',uri=True,timeout=5)
    con.row_factory=sqlite3.Row
    try:
        con.execute('PRAGMA query_only=ON');con.execute('BEGIN')
        tables={r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'trades' not in tables:
            raise ValueError('Keine Trades-Tabelle in dieser Datenbank')
        columns={r[1] for r in con.execute('PRAGMA table_info(trades)')}
        names=['trade_id','broker','paper','broker_account_fingerprint','symbol','waehrung',
            'ausgestiegen_am','einstieg_preis','ausstieg_preis','netto_pnl','fee_quality',
            'entry_order_id','exit_order_id','exit_native_json','protection_status','reconciliation_status','accounting_kind','menge']
        names=[n for n in names if n in columns]
        where=' WHERE superseded_by IS NULL' if 'superseded_by' in columns else ''
        report['total_trades']=con.execute('SELECT COUNT(*) FROM trades'+where).fetchone()[0]
        # Reference receipts validate the entire financial row snapshot.
        rows=con.execute('SELECT * FROM trades'+where+' ORDER BY trade_id DESC LIMIT 2000').fetchall()
        checks={r['trade_id']:dict(r) for r in con.execute('SELECT trade_id,checked_at,status,detail FROM okx_closed_result_checks')} if 'okx_closed_result_checks' in tables else {}
        report['truncated']=report['total_trades']>len(rows)
        def finite(value,positive=False):
            try:return value is not None and math.isfinite(float(value)) and (not positive or float(value)>0)
            except (TypeError,ValueError):return False
        report['eur_reference_results']=[]
        for raw in rows:
            row=dict(raw);closed=bool(row.get('ausgestiegen_am'))
            mode='DEMO' if row.get('paper')==1 else 'LIVE' if row.get('paper')==0 else 'UNKNOWN'
            domain=' / '.join(str(row.get(k) or 'UNKNOWN') for k in ('broker','broker_account_fingerprint','waehrung'))+' / '+mode
            stats=report['domains'].setdefault(domain,{'open':0,'closed':0,'result_pending':0,'protection_unconfirmed':0,'residual_inventory':0})
            if row.get('accounting_kind')=='RESIDUAL':
                stats['residual_inventory']+=1
                continue
            stats['closed' if closed else 'open']+=1
            gaps=[]
            if closed:
                valued=None
                if row.get('broker')=='okx':
                    from okx_reference_valuation import load_on
                    from okx_receipt_math import EvidenceError
                    try:valued=load_on(con,row)
                    except (EvidenceError,ValueError,TypeError,KeyError):gaps.append('FX_EVIDENCE_INVALID')
                if valued:
                    report['eur_reference_results'].append({'trade_id':row['trade_id'],
                        **{k:valued[k] for k in ('result_currency','net_eur','method','quality','receipt_hash')}})
                if not finite(row.get('ausstieg_preis'),True) and not valued:gaps.append('EXIT_PRICE_MISSING')
                if not finite(row.get('einstieg_preis'),True):gaps.append('ENTRY_PRICE_MISSING')
                if row.get('fee_quality') not in ('CONFIRMED','BROKER_CONFIRMED','KNOWN'):gaps.append('FEES_UNCONFIRMED')
                if not finite(row.get('netto_pnl')) and not valued:gaps.append('NET_RESULT_MISSING')
                try:
                    native=json.loads(row.get('exit_native_json') or '{}').get('receipt',{})
                    if native.get('native_currency') and native['native_currency']!=row.get('waehrung') and not valued:
                        gaps.append('NATIVE_FX_REVIEW')
                except (ValueError,TypeError,AttributeError):gaps.append('NATIVE_RECEIPT_UNREADABLE')
                if gaps:stats['result_pending']+=1
            elif row.get('protection_status')!='ACTIVE':
                gaps.append('BROKER_PROTECTION_UNCONFIRMED');stats['protection_unconfirmed']+=1
            if gaps:
                report['items'].append({k:row.get(k) for k in ('trade_id','broker','symbol','waehrung','entry_order_id','exit_order_id')} |
                    {'environment':mode,'account':row.get('broker_account_fingerprint'),'closed':closed,
                     'missing':gaps,'last_result_check':checks.get(row.get('trade_id'),{})})
        report['bookkeeping']=[]
        if {'broker_account_fingerprint','paper','broker'} <= columns:
            from okx_accounting import status_on_connection
            for account,paper in con.execute("SELECT DISTINCT broker_account_fingerprint,paper FROM trades WHERE broker='okx' AND broker_account_fingerprint<>''"):
                env='DEMO' if paper else 'LIVE'
                report['bookkeeping'].append(dict(account=account,environment=env,**status_on_connection(con,account,env)))
        report['residual_inventory']=[]
        if 'okx_residual_inventory' in tables:
            report['residual_inventory']=[dict(x) for x in con.execute('SELECT trade_id,account,environment,instrument,entry_order_id,quantity,cost_basis,currency,separate_row,recorded_at FROM okx_residual_inventory ORDER BY trade_id')]
        con.rollback()
    finally:con.close()
    return report


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--datenbank',type=Path,default=Path(__file__).resolve().parent/'decision_history.sqlite')
    args=p.parse_args()
    try:result=inspect(args.datenbank)
    except (OSError,ValueError,sqlite3.Error) as exc:
        print(json.dumps({'read_only':True,'error':str(exc)},ensure_ascii=False,indent=2));return 2
    print(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False));return 0


if __name__=='__main__':raise SystemExit(main())
