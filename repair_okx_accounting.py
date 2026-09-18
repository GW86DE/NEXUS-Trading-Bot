"""Offline, evidence-based 9.8.8 state migration. Default: preview on a copy.

Only an explicit --apply --workers-stopped changes the selected state directory.
The updater calls this on freshly migrated STAGING, never on an old diagnosis DB.
No broker object is instantiated, no API request is sent, no credentials copied.
Each financial correction is transactional and replay-safe. The JSON risk state
is recoverable from the committed ledger if the process stops between files.
"""
from __future__ import annotations
from contextlib import contextmanager, closing
from datetime import datetime, timezone
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile

REPORT_NAME = 'okx_accounting_repair_report.json'
INPUTS = ('decision_history.sqlite','risk_state_okx.json','runtime_status_okx.json')


class AccountingRepairError(RuntimeError):
    pass


def backup_state(source: Path, target: Path):
    target.mkdir(mode=0o700,parents=True,exist_ok=False)
    hashes={}
    for name in INPUTS:
        p=source/name
        if not p.exists():continue
        if p.is_symlink() or not p.is_file():
            raise AccountingRepairError('Keine regulaere Zustandsdatei: '+name)
        out=target/name
        if name.endswith('.sqlite'):
            with closing(sqlite3.connect(p.as_uri()+'?mode=ro',uri=True,timeout=10)) as src, closing(sqlite3.connect(out)) as dst:
                src.backup(dst,pages=512,sleep=.05)
                if dst.execute('PRAGMA quick_check').fetchone()[0]!='ok':
                    raise AccountingRepairError('Sicherung der Handelsdatenbank ist nicht intakt')
        else:
            shutil.copyfile(p,out)
        out.chmod(0o600);hashes[name]=hashlib.sha256(out.read_bytes()).hexdigest()
    return hashes


@contextmanager
def selected_database(root):
    # The scope MUST precede importing decision_analytics: that module's
    # established import-time init_db() otherwise writes into the release root.
    from ledger_database_scope import existing_database
    with existing_database(Path(root) / 'decision_history.sqlite'):
        import decision_analytics as da
        with da._LOCK:
            yield


def offline_risk_sync(root):
    """Use explicit exported/current runtime context, never infer an FX rate.

    Existing legacy risk numbers are preserved. If their account/mode cannot be
    corroborated through each linked ledger receipt, leave that JSON untouched
    and report the problem. The independent ledger gate still blocks new buys.
    """
    import trade_ledger as tl
    from risk_manager import RiskState
    from risk_result_recovery import reconcile
    from types import SimpleNamespace
    rp=root/'risk_state_okx.json';runtime=root/'runtime_status_okx.json'
    if not rp.is_file() or not runtime.is_file():
        return dict(status='DEFERRED',detail='Risikostand/Laufzeitkontext fehlt; kontogebundenes Ledgergate bleibt aktiv')
    raw=json.loads(rp.read_text(encoding='utf-8'));rt=json.loads(runtime.read_text(encoding='utf-8'))
    account=str(rt.get('account_fingerprint') or '');env=str(rt.get('modus') or '').upper()
    currency=str(rt.get('kapital_waehrung') or '').upper()
    basis=str(raw.get('equity_basis_key') or '')
    if not account or env not in {'DEMO','LIVE'} or not currency or ':okx:'+currency+':' not in basis:
        return dict(status='DEFERRED',detail='Keine eindeutige bisherige OKX-Risikodomaene; keine neue Zuordnung erfunden')
    for key in (raw.get('realized_receipts') or {}):
        if not key.startswith('ledger:'):
            return dict(status='DEFERRED',detail='Altrisiko ohne kontogebundenen Ledgeranker')
        try:row=tl.trade_detail(int(key.split(':',1)[1]))
        except (ValueError,TypeError):row=None
        if (not row or row['broker']!='okx' or row['broker_account_fingerprint']!=account
                or bool(row['paper'])!=(env=='DEMO')):
            return dict(status='DEFERRED',detail='Risikobeleg passt nicht eindeutig zum bisherigen Konto/Modus')
    state=RiskState.load(rp)
    broker=SimpleNamespace(name='okx',account_fingerprint=lambda:account,demo=env=='DEMO',kontowaehrung=lambda:currency)
    done=reconcile(state,broker,account_equity=float(raw.get('last_equity') or 0))
    state.refresh()
    unknown=[k for k,v in state.realized_receipts.items() if v.get('status')=='UNKNOWN']
    return dict(status='SYNCED',account=account,environment=env,currency=currency,
        resolved=done,unknown=unknown,
        detail=('Ergebnisbelege vollstaendig in der Risikowaehrung uebernommen' if not unknown else
                'Native Fremdwaehrung ohne historischen FX-Beleg bleibt im Risikostand unbeziffert'))


def _run(root, receipts):
    with selected_database(root):
        import trade_ledger as tl
        import okx_closed_reconciliation as cr
        from okx_receipt_import import import_bundle
        from okx_residual_inventory import classify_existing,public_inventory
        from okx_accounting import status
        cr.init()
        # Validate a supplied import before any inventory/money migration.
        preview=import_bundle(receipts) if receipts else None
        before=[]
        with tl._connect() as con:
            domains=con.execute("SELECT DISTINCT broker_account_fingerprint,paper FROM trades WHERE broker='okx' AND broker_account_fingerprint<>''").fetchall()
        for account,paper in domains:
            before.append(dict(account=account,environment='DEMO' if paper else 'LIVE',**status(account,'DEMO' if paper else 'LIVE')))
        residual=classify_existing(limit=500)
        imported=import_bundle(receipts,apply=True) if preview else {'status':'NOT_PROVIDED'}
        # Risk JSON commits follow SQLite and can be rebuilt after interruption.
        risk=offline_risk_sync(root)
        after=[dict(account=account,environment='DEMO' if paper else 'LIVE',**status(account,'DEMO' if paper else 'LIVE')) for account,paper in domains]
        summary=dict(schema='nexus-okx-accounting-repair-v1',version='9.8.8',
            at=datetime.now(timezone.utc).isoformat(),network_requests=0,orders_sent=0,
            residuals=residual,receipt_import={k:v for k,v in imported.items() if k!='receipt'},
            risk=risk,before=before,after=after,inventory=public_inventory(broker='okx'))
        if imported.get('receipt'):
            summary['receipt_result']={k:imported['receipt'][k] for k in ('trade_id','account','environment','inst_id','currency','quantity','net','fees','closed_at','residual','cost_basis')}
        with closing(tl._connect()) as con:
            checkpoint=con.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()
            if checkpoint[0]:raise AccountingRepairError('Handelsdatenbank hat unerwartete parallele Writer')
        return summary


def repair(root, *, apply=False, workers_stopped=False, receipts=None, backup_dir=None):
    source=Path(root).expanduser()
    if source.is_symlink() or not source.is_dir():raise AccountingRepairError('Quellzustand ist kein lokales Verzeichnis')
    source=source.resolve()
    if not (source/'decision_history.sqlite').is_file() or (source/'decision_history.sqlite').is_symlink():
        raise AccountingRepairError('Lokale Handelsdatenbank fehlt')
    if apply and not workers_stopped:raise AccountingRepairError('Anwenden nur mit gestoppten Writern')
    if receipts:
        from okx_receipt_import import read_bundle,extract
        extract(read_bundle(receipts)[0])  # No state writes if external input is malformed.
    if not apply:
        with tempfile.TemporaryDirectory(prefix='nexus_accounting_preview_') as tmp:
            copy=Path(tmp)/'state';backup_state(source,copy)
            result=_run(copy,receipts)
            result.update(applied=False,preview=True,source=str(source))
            return result
    backup=Path(backup_dir) if backup_dir else source.parent/('NEXUS_Buchungsbackup_'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S_%f'))
    if backup.resolve().is_relative_to(source):raise AccountingRepairError('Sicherung muss ausserhalb des Quellzustands liegen')
    hashes=backup_state(source,backup)
    result=_run(source,receipts)
    result.update(applied=True,preview=False,source=str(source),backup=str(backup),backup_sha256=hashes)
    from safe_persistence import atomic_write_json
    atomic_write_json(source/REPORT_NAME,result)
    return result


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--source',type=Path,required=True)
    ap.add_argument('--receipts',type=Path)
    ap.add_argument('--apply',action='store_true')
    ap.add_argument('--workers-stopped',action='store_true')
    ap.add_argument('--output',type=Path)
    args=ap.parse_args()
    os.umask(0o077)
    if args.apply:
        if not args.workers_stopped:raise SystemExit('--workers-stopped ist Pflicht fuer --apply')
        from nexus_update import Host
        Host().other_writers(args.source.resolve())
    result=repair(args.source,apply=args.apply,workers_stopped=args.workers_stopped,receipts=args.receipts)
    if args.output:
        from safe_persistence import atomic_write_json
        atomic_write_json(args.output,result)
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
