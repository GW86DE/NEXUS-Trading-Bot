"""Offline, explicit historical manual-sale backfill; never sends broker orders.

Default is read-only preview. --anwenden requires --manuell-bestaetigt and
creates a consistent SQLite backup before applying the exact-anchor change.
"""
from contextlib import closing
from datetime import datetime, timezone
from decimal import Decimal
import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile

from okx_external_settlement import native_receipt,number


def prepare(report, trade, exit_order, max_dust, manual_confirmed):
    account=str(report.get('account_fingerprint') or '')
    if (report.get('mode')!='DEMO' or report.get('account_match') is not True
            or report.get('collection_finished') is not True
            or not account or account!=report.get('expected_account_fingerprint')
            or account!=trade.get('broker_account_fingerprint') or trade.get('paper')!=1
            or trade.get('broker')!='okx' or not trade.get('ausgestiegen_am')
            or report.get('entry_order_id')!=trade.get('entry_order_id')):
        raise ValueError('Bericht und bereits geschlossener DEMO-Trade passen nicht exakt zusammen')
    if any(r.get('method')!='GET' for r in report.get('requests',[])):
        raise ValueError('Kein ausschliesslich lesender Diagnosebericht')
    requests=[r for r in report.get('requests',[]) if r.get('ok') is True]
    entries=[]; exits=[]; details=[]
    for request in requests:
        path=request.get('path')
        for row in request.get('response',{}).get('data',[]):
            if path=='/trade/fills-history':
                if str(row.get('ordId'))==trade['entry_order_id']:entries.append(row)
                if str(row.get('ordId'))==exit_order:exits.append(row)
            if path=='/trade/order' and str(row.get('ordId'))==exit_order:details.append(row)
    if not entries or not exits or len(details)!=1:
        raise ValueError('Entry-Fills, Exit-Fills oder exakte Orderdetails fehlen')
    detail=details[0]; inst=str(detail.get('instId') or '')
    if detail.get('state')!='filled' or detail.get('side')!='sell':
        raise ValueError('Verkaufsorder ist nicht vollstaendig ausgefuehrt')
    if any(detail.get(k) for k in ('tag','clOrdId','algoId','algoClOrdId')):
        raise ValueError('Verkauf hat Bot-/Algo-Verknuepfung; kein manueller Abschluss geraten')
    for iid in (trade['broker_position_id'],inst):
        streams=[s for s in report.get('streams',[]) if s.get('path')=='/trade/fills-history'
                 and s.get('params',{}).get('instId')==iid]
        if len(streams)!=1 or streams[0].get('complete_within_endpoint') is not True:
            raise ValueError('Historische Fill-Abfrage ist unvollstaendig')
    base=trade['broker_position_id'].split('-')[0]
    unique={}; entry_qty=Decimal(0)
    for row in entries:
        key=(str(row.get('instId')),str(row.get('ordId')),str(row.get('tradeId')))
        if key in unique:
            if unique[key]!=row:raise ValueError('Widerspruechliche Entry-Fills')
            continue
        unique[key]=row
        if row.get('instId')!=trade['broker_position_id'] or row.get('side')!='buy':
            raise ValueError('Falscher Entry-Fill')
        q=number(row.get('fillSz'),positive=True);px=number(row.get('fillPx'),positive=True)
        if abs(px-number(trade['einstieg_preis']))>Decimal('0.00000001'):
            # Multi-price entries need an explicit VWAP reconciliation, not this narrow repair.
            raise ValueError('Entry-Preis widerspricht dem vorhandenen Trade')
        entry_qty+=q
        fee=number(row.get('fee'))
        if row.get('feeCcy')==base:entry_qty+=fee
    if abs(entry_qty-number(trade['menge']))>Decimal('0.000000001'):
        raise ValueError('Netto-Entry-Menge widerspricht dem vorhandenen Trade')
    if str(detail.get('tradeQuoteCcy') or inst.split('-')[-1])==trade['waehrung']:
        raise ValueError('Dieser Reparaturpfad ist nur fuer den belegten Fremdwaehrungsfall')
    receipt=native_receipt([dict(r,tradeQuoteCcy=detail.get('tradeQuoteCcy') or inst.split('-')[-1]) for r in exits],
        account=account,environment='DEMO',entry_instrument=trade['broker_position_id'],
        expected_quantity=trade['menge'],lot_size=number(max_dust,positive=True),
        expected_order_id=exit_order,manual_confirmed=manual_confirmed)
    if number(receipt['gross_quantity'])!=number(detail.get('accFillSz'),positive=True):
        raise ValueError('Fill-Summe widerspricht der Orderausfuehrung')
    closed=datetime.fromisoformat(receipt['closed_at'].replace('Z','+00:00'))
    opened=datetime.fromisoformat(str(trade['eingestiegen_am']).replace('Z','+00:00'))
    if opened.tzinfo is None or closed<opened:
        raise ValueError('Verkauf liegt vor dem Einstieg')
    return receipt


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--beleg',type=Path,required=True)
    parser.add_argument('--datenbank',type=Path,required=True)
    parser.add_argument('--trade-id',type=int,required=True)
    parser.add_argument('--verkaufsorder',required=True)
    parser.add_argument('--max-staub',required=True)
    parser.add_argument('--manuell-bestaetigt',action='store_true')
    parser.add_argument('--anwenden',action='store_true')
    args=parser.parse_args()
    if args.anwenden and not args.manuell_bestaetigt:
        parser.error('--anwenden erfordert --manuell-bestaetigt')
    db=args.datenbank.resolve(strict=True)
    with closing(sqlite3.connect(db.as_uri()+'?mode=ro',uri=True)) as con:
        con.row_factory=sqlite3.Row;con.execute('PRAGMA query_only=ON')
        row=con.execute('SELECT * FROM trades WHERE trade_id=?',(args.trade_id,)).fetchone()
    if row is None:raise ValueError('Trade-ID nicht gefunden')
    raw=args.beleg.read_bytes();report=json.loads(raw)
    receipt=prepare(report,dict(row),args.verkaufsorder,args.max_staub,args.manuell_bestaetigt)
    summary={k:receipt[k] for k in ('inst_id','quantity','residual','native_currency','net_proceeds','closed_at','order_ids','fill_ids')}
    summary.update(trade_id=args.trade_id,netto_pnl=None,hinweis='Verkaufserloes ist NICHT Gewinn',angewendet=False)
    if args.anwenden:
        fd,name=tempfile.mkstemp(prefix='vor_externem_abgleich_',suffix='.sqlite',dir=db.parent)
        os.close(fd)
        with closing(sqlite3.connect(db.as_uri()+'?mode=ro',uri=True)) as source, closing(sqlite3.connect(name)) as backup:
            source.backup(backup)
        import decision_analytics
        decision_analytics.DB_PATH=db
        from okx_external_settlement import record_native_exit
        record_native_exit(entry_order_id=row['entry_order_id'],entry_instrument=row['broker_position_id'],
            account=row['broker_account_fingerprint'],paper=True,evidence=receipt,residual=receipt['residual'])
        summary.update(angewendet=True,sicherung=name,beleg_sha256=hashlib.sha256(raw).hexdigest())
    print(json.dumps(summary,ensure_ascii=False,indent=2))


if __name__=='__main__':
    try:main()
    except (ValueError,OSError,sqlite3.Error) as exc:raise SystemExit('ABGEBROCHEN: '+str(exc))
