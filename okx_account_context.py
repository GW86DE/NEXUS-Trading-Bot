"""Durable OKX account boundary. No network and no broker mutations."""
from pathlib import Path
import os
import json
import sqlite3
from contextlib import closing
from safe_persistence import atomic_write_json
from state_lock import critical_state_lock


class AccountContextError(RuntimeError):
    pass


def root():
    return Path(os.getenv('TRADINGBOT_TEST_STATE_DIR') or Path(__file__).resolve().parent)


def read_context(directory=None):
    directory = Path(directory) if directory is not None else root()
    if (directory/'okx_account_switch_pending.json').exists():
        raise AccountContextError('OKX_ACCOUNT_SWITCH_INCOMPLETE: Kontowechsel unterbrochen; Archiv pruefen')
    p = directory/'okx_account_context.json'
    if not p.exists():
        return {}
    data = json.loads(p.read_text(encoding='utf-8'))
    if not isinstance(data, dict) or not data.get('account') or data.get('environment') not in {'DEMO','LIVE'}:
        raise AccountContextError('OKX_ACCOUNT_CONTEXT_UNREADABLE')
    return data


def strict_account(account, environment):
    data = read_context()
    return bool(data.get('strict') and data.get('account') == account and data.get('environment') == environment)


def active_rows(rows):
    """Only implicit operational views; explicit historical queries retain all evidence."""
    data = read_context()
    if not data:
        return rows
    return [r for r in rows if r.get('broker') != 'okx' or
        ((r.get('broker_account_fingerprint') == data['account'] or
          (not data.get('strict') and not r.get('broker_account_fingerprint')))
         and bool(r.get('paper')) == (data['environment'] == 'DEMO'))]


def existing_accounts(directory):
    """Infer legacy identity only from durable receipts, never from a new API key."""
    directory = Path(directory)
    accounts = set()
    for name in ('crypto_positions.json','runtime_status_okx.json','risk_state_okx.json'):
        p = directory/name
        if not p.exists():
            continue
        data = json.loads(p.read_text(encoding='utf-8'))
        if not isinstance(data, dict):
            raise AccountContextError('Ungueltiger Altkontext: '+name)
        values = data.get('positionen', []) if name == 'crypto_positions.json' else [data]
        for r in values:
            account = r.get('account_fingerprint')
            if account:
                accounts.add(str(account))
        # The risk receipt stores a serialized [broker, environment, account].
        for key, value in data.items():
            if 'scope' in key and isinstance(value, str) and value.startswith('['):
                scope = json.loads(value)
                if len(scope) == 3 and scope[0] == 'okx' and scope[2]:
                    accounts.add(str(scope[2]))
    db = directory/'decision_history.sqlite'
    if db.exists():
        with closing(sqlite3.connect(db.as_uri()+'?mode=ro', uri=True)) as con:
            tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if 'trades' in tables:
                accounts.update(str(r[0]) for r in con.execute("SELECT DISTINCT broker_account_fingerprint FROM trades WHERE broker='okx' AND COALESCE(broker_account_fingerprint,'')<>''"))
    return accounts


def bind_verified(account, environment):
    if not account or environment not in {'DEMO','LIVE'}:
        raise AccountContextError('OKX_ACCOUNT_IDENTITY_UNKNOWN')
    directory = root()
    with critical_state_lock(directory/'okx_account_context.json'):
        old = read_context(directory)
        if old:
            if old['account'] != account or old['environment'] != environment:
                raise AccountContextError('OKX_ACCOUNT_CONTEXT_MISMATCH: --okx-neues-konto fuer ausdruecklichen Kontowechsel verwenden')
            return old
        known = existing_accounts(directory)
        if known and known != {account}:
            raise AccountContextError('OKX_ACCOUNT_CONTEXT_MISMATCH: vorhandene Belege gehoeren zu anderem/mehreren Konten')
        # Existing money without any provable account identity cannot be silently rebound.
        if not known:
            p = directory/'crypto_positions.json'
            risk = directory/'risk_state_okx.json'
            if (p.exists() and json.loads(p.read_text()).get('positionen')) or (risk.exists() and any(
                    json.loads(risk.read_text()).get(k) for k in ('day_start_equity','last_equity','trades_today','lifetime_realized_pnl'))):
                raise AccountContextError('OKX_ACCOUNT_CONTEXT_UNKNOWN: Altzustand ohne Kontonachweis')
        data = dict(version=1, account=account, environment=environment, strict=not bool(known))
        atomic_write_json(directory/'okx_account_context.json', data)
        return data


def require_active(account, environment):
    data = read_context()
    if data and (data['account'] != account or data['environment'] != environment):
        raise AccountContextError('OKX_ACCOUNT_CONTEXT_MISMATCH')
