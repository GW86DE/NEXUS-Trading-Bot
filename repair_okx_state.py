"""Evidence-bound repair of 9.7.4 order projections, never of financial trades.

Default: read-only plan. --apply requires stopped writers and takes private backups.
No symbol/quantity guess, no broker request, no deletion of the historical evidence.
"""
from __future__ import annotations

from contextlib import nullcontext
import argparse
import hashlib
import json
import math
from pathlib import Path
import shutil
import sqlite3
from datetime import datetime, timezone

from safe_persistence import atomic_write_json
from state_lock import critical_state_lock


class RepairError(RuntimeError):
    pass


def _n(x):
    try:
        value = float(x)
    except (ValueError, TypeError):
        raise RepairError("Non-numeric execution evidence") from None
    if not math.isfinite(value):
        raise RepairError("Non-finite execution evidence")
    return value


def _same(a, b):
    return math.isclose(_n(a), _n(b), rel_tol=1e-9, abs_tol=1e-10)


def _proofs(con):
    trades = [dict(r) for r in con.execute(
        "SELECT * FROM trades WHERE broker='okx' AND superseded_by IS NULL ORDER BY trade_id")]
    groups = {}
    for row in trades:
        key = (row['broker_account_fingerprint'], int(row['paper']), row['broker_position_id'], row['entry_order_id'])
        groups.setdefault(key, []).append(row)
    for (account, paper, instrument, oid), rows in groups.items():
        if not account or not oid or '-' not in instrument:
            continue  # No retrospective assignment of unbound legacy inventory.
        first = rows[0]
        client = str(first.get('client_order_id') or '')
        did = first.get('decision_id')
        if not client or not did or any(str(x.get('client_order_id') or '') != client or x.get('decision_id') != did for x in rows):
            continue
        fills = [dict(r) for r in con.execute(
            "SELECT * FROM trade_entry_fills WHERE broker='okx' AND broker_account_fingerprint=? AND instrument=? AND order_id=?",
            (account, instrument, oid))]
        if not fills:
            continue
        unique = {}; fees = {}; gross = value = base_charge = quote_fee = 0.0
        base, quote = instrument.split('-', 1)
        try:
            for f in fills:
                raw = json.loads(f['raw_json'])
                if (str(raw.get('ordId') or '') != oid or str(raw.get('clOrdId') or '') != client
                        or raw.get('instId') != instrument or raw.get('side') != 'buy'
                        or f['trade_id'] not in {x['trade_id'] for x in rows}):
                    raise RepairError('Fill identity conflict')
                fid = str(raw.get('tradeId') or '')
                qty, px, charge = _n(raw.get('fillSz')), _n(raw.get('fillPx')), -_n(raw.get('fee'))
                ccy = str(raw.get('feeCcy') or '').upper()
                signature = (qty, px, charge, ccy)
                if not fid or qty <= 0 or px <= 0 or ccy not in {base, quote}:
                    raise RepairError('Incomplete fill/fee evidence')
                if fid in unique:
                    if unique[fid] != signature:
                        raise RepairError('Conflicting duplicated fill')
                    continue
                unique[fid] = signature
                gross += qty; value += qty * px
                fees[ccy] = fees.get(ccy, 0.0) + charge
                if ccy == base:
                    base_charge += charge; quote_fee += charge * px
                else:
                    quote_fee += charge
            net = gross - base_charge
            if (not _same(sum(_n(r['menge']) for r in rows), net)
                    or any(not _same(r['einstieg_preis'], value / gross) for r in rows)
                    or not _same(sum(_n(r['einstieg_gebuehr']) for r in rows), quote_fee)):
                raise RepairError('Ledger quantity/fee does not match complete entry fills')
        except (RepairError, ValueError, KeyError, TypeError, ZeroDivisionError):
            continue  # Unproved cases remain untouched, not silently "repaired".
        yield {"account": account, "paper": paper, "instrument": instrument,
               "order_id": oid, "client_id": client, "decision_id": did,
               "symbol": first['symbol'], "trade_id": first['trade_id'],
               "gross": gross, "net": net, "price": value/gross, "fee_quote": quote_fee,
               "fees": fees, "fills": [f'okx:{account}:{instrument}:{oid}:{fid}' for fid in unique]}


def _plan(con, registry):
    updated = json.loads(json.dumps(registry))
    if not isinstance(updated.get('orders'), dict) or not isinstance(updated.get('pending'), dict):
        raise RepairError('Registry shape invalid')
    changes, order_updates, verified = [], [], []
    for proof in _proofs(con):
        verified.append(proof['trade_id'])
        oid, client = proof['order_id'], proof['client_id']
        env = 'DEMO' if proof['paper'] else 'LIVE'
        aliases = [oid] + proof['fills']
        if client in updated['orders']:
            aliases.append(client)
        for key in aliases:
            old = updated['orders'].get(key)
            if old is None or str(old.get('account_fingerprint') or '') != proof['account']:
                continue
            if str(old.get('environment') or '').upper() != env or str(old.get('inst_id') or '') != proof['instrument']:
                continue
            if str(old.get('decision_id') or '') != str(proof['decision_id']):
                continue
            new = dict(old)
            for name in ('reason', 'exit_attempt_id', 'entry_order_id', 'identifier_type', '_order_aliases'):
                new.pop(name, None)
            new.update({'role': 'ENTRY', 'zustand': 'FILLED', 'ord_id': oid,
                        'client_order_id': client, 'cl_ord_id': client,
                        'trade_id': proof['trade_id'], 'fill_ids': proof['fills'],
                        'identifier_type': 'FILL' if key in proof['fills'] else 'ORDER',
                        '_order_aliases': sorted(aliases)})
            if new != old:
                changes.append({'kind': 'entry_identity', 'key': key, 'before': old, 'after': new})
                updated['orders'][key] = new
        # Mark only exact, durably accounted ENTRY intents complete. Preserve
        # the original record in the repair audit, including SUBMITTING evidence.
        for key, old in list(updated['pending'].items()):
            if (old.get('broker') == 'okx' and old.get('role', 'ENTRY') == 'ENTRY'
                    and old.get('inst_id') == proof['instrument']
                    and old.get('account_fingerprint') == proof['account']
                    and str(old.get('environment') or '').upper() == env
                    and str(old.get('decision_id')) == str(proof['decision_id'])
                    and str(old.get('cl_ord_id') or old.get('client_order_id') or '') == client):
                changes.append({'kind': 'pending_accounted', 'key': key, 'before': old})
                updated['pending'].pop(key)
        for row in con.execute("SELECT * FROM decision_orders WHERE broker='okx' AND broker_order_id=? AND role='ENTRY'", (oid,)):
            old = dict(row)
            if (str(old['decision_id']) != str(proof['decision_id']) or old['client_order_id'] != client
                    or str(old['currency']) != proof['instrument'].split('-', 1)[1]):
                continue
            raw = json.loads(old['raw_json'] or '{}')
            if not raw.get('fill_evidence_complete') or not _same(raw.get('gross_filled_quantity', -1), proof['gross']):
                continue
            remaining = max(0.0, _n(old['requested_qty'])-proof['gross'])
            if not _same(old['filled_qty'], proof['gross']) or not _same(old['remaining_qty'], remaining):
                raw['net_filled_quantity'] = proof['net']
                raw['quantity_semantics'] = 'GROSS_EXECUTION_V1'
                new = {'filled_qty': proof['gross'], 'remaining_qty': remaining, 'raw_json': json.dumps(raw, ensure_ascii=False)}
                order_updates.append({'id': old['id'], 'before': old, 'after': new})
    return updated, {'schema': 1, 'verified_trade_ids': verified, 'registry_changes': changes,
                     'decision_order_changes': order_updates,
                     'financial_trades_changed': 0,
                     'unproven_records': 'Preserved; no fabricated broker execution status'}


def repair(root: Path, *, apply=False, backup_dir: Path | None = None):
    root = Path(root).resolve()
    db, path = root/'decision_history.sqlite', root/'bot_order_registry.json'
    if not db.is_file() or not path.is_file() or db.is_symlink() or path.is_symlink():
        raise RepairError('Readable local ledger and registry required')
    # The caller must have stopped ALL writers. Registry lock + DB transaction
    # additionally prevent in-process races, not rogue uncooperative writers.
    with (critical_state_lock(path) if apply else nullcontext()):
        original = path.read_bytes()
        registry = json.loads(original)
        con = sqlite3.connect(db.as_uri()+('?mode=rw' if apply else '?mode=ro'), uri=True, timeout=10)
        con.row_factory = sqlite3.Row
        try:
            if con.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                raise RepairError('SQLite integrity check failed')
            con.execute('BEGIN IMMEDIATE' if apply else 'BEGIN')
            updated, report = _plan(con, registry)
            count = len(report['registry_changes'])+len(report['decision_order_changes'])
            report['changed'] = count
            if not apply or not count:
                report['applied'] = False
                return report
            backup = Path(backup_dir or root.parent / ('nexus_repair_backup_'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S_%f')))
            backup.mkdir(mode=0o700, parents=True, exist_ok=False)
            (backup/path.name).write_bytes(original)
            (backup/path.name).chmod(0o600)
            # Use a second read-only connection: backup from the IMMEDIATE
            # transaction's connection could stall on its own write lock.
            with sqlite3.connect(db.as_uri()+'?mode=ro', uri=True) as src, sqlite3.connect(backup/db.name) as dst:
                src.backup(dst)
            (backup/db.name).chmod(0o600)
            atomic_write_json(backup/'repair_plan.json', report)
            for change in report['decision_order_changes']:
                n = change['after']
                con.execute('UPDATE decision_orders SET filled_qty=?,remaining_qty=?,raw_json=? WHERE id=?',
                            (n['filled_qty'], n['remaining_qty'], n['raw_json'], change['id']))
            if path.read_bytes() != original:
                raise RepairError('Registry changed while planning')
            atomic_write_json(path, updated)
            try:
                con.commit()
            except Exception:
                atomic_write_json(path, registry)
                raise
            report['applied'] = True
            report['backup'] = str(backup)
            atomic_write_json(root/'order_repair_report.json', report)
            return report
        finally:
            con.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--source', type=Path, default=Path(__file__).resolve().parent)
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--workers-stopped', action='store_true')
    ap.add_argument('--output', type=Path)
    args = ap.parse_args()
    if args.apply and not args.workers_stopped:
        ap.error('--apply requires --workers-stopped after stopping Core and WebUI')
    result = repair(args.source, apply=args.apply)
    if args.output:
        atomic_write_json(args.output, result)
    print(json.dumps({k:v for k,v in result.items() if k not in {'registry_changes','decision_order_changes'}}, indent=2))


if __name__ == '__main__':
    main()
