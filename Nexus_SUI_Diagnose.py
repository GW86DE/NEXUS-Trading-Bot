"""Read-only symbol evidence export (--symbol BTC). No broker calls or writes to bot state."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import subprocess
import zipfile

UNITS = ('tradingbot-pi5.service', 'tradingbot-webui.service')
SENSITIVE = re.compile(r'key|secret|password|passphrase|token|authorization|cookie', re.I)


def sanitize(value):
    if isinstance(value, dict):
        return {k: '[REDACTED]' if SENSITIVE.search(str(k)) else sanitize(v)
                for k, v in value.items()}
    if isinstance(value, list):
        return [sanitize(v) for v in value]
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except (ValueError, TypeError):
            decoded = None
        if isinstance(decoded, (dict, list)):
            return sanitize(decoded)
        return re.sub(r'(?i)((?:api[_-]?key|token|secret|password)=)[^&\s]+',
                      r'\1[REDACTED]', value)
    if isinstance(value, bytes):
        return '[BINARY OMITTED]'
    return value


def service_state():
    result = {}
    for unit in UNITS:
        proc = subprocess.run(['systemctl', 'show', unit, '--no-pager',
            '-p', 'WorkingDirectory', '-p', 'ActiveState', '-p', 'SubState'],
            capture_output=True, text=True, timeout=10)
        result[unit] = dict(line.split('=', 1) for line in proc.stdout.splitlines() if '=' in line)
        result[unit]['returncode'] = proc.returncode
    return result


def select_sui(value, symbol='SUI'):
    """Keep complete symbol records; default retained for older integrations."""
    if isinstance(value, dict):
        if any(str(value.get(k, '')).upper().split('-')[0] == symbol
               for k in ('symbol', 'inst_id', 'instId', 'instrument')):
            return value
        result = {}
        for key, item in value.items():
            child = select_sui(item, symbol)
            if child not in (None, {}, []):
                result[key] = child
            elif str(key).upper() == symbol:
                result[key] = item
        return result or None
    if isinstance(value, list):
        return [r for item in value if (r := select_sui(item, symbol)) is not None] or None
    return None


def read_database(path, symbol='SUI'):
    if not path.is_file():
        return {'available': False}
    # mode=ro will never create an empty database for an incorrect filename.
    with sqlite3.connect(path.resolve().as_uri()+'?mode=ro', uri=True, timeout=5) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA query_only=ON')
        conn.execute('BEGIN')
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        result = {'available': True, 'consistent_database_transaction': True}
        for table in ('trades', 'decision_orders', 'trade_entry_fills', 'trade_exit_events',
                      'execution_orders', 'execution_fills', 'execution_events', 'trade_native_exit_fills'):
            if table not in tables:
                result[table] = {'missing': True}
                continue
            columns = {r[1] for r in conn.execute(f'PRAGMA table_info({table})')}
            if table.startswith('execution_') and 'execution_orders' in tables:
                query = (f'SELECT order_key AS execution_ref, * FROM {table} WHERE order_key IN '
                         "(SELECT order_key FROM execution_orders WHERE broker='okx' AND "
                         "instrument LIKE ?) ORDER BY rowid DESC LIMIT 201")
                args = (symbol+'-%',)
            elif 'symbol' in columns:
                query = f'SELECT * FROM {table} WHERE upper(symbol)=? ORDER BY rowid DESC LIMIT 201'
                args = (symbol,)
            elif 'trade_id' in columns and 'trades' in tables:
                query = (f'SELECT * FROM {table} WHERE trade_id IN '
                         "(SELECT trade_id FROM trades WHERE upper(symbol)=?) ORDER BY rowid DESC LIMIT 201")
                args = (symbol,)
            else:
                result[table] = {'unsupported_schema': True}
                continue
            rows = [dict(r) for r in conn.execute(query, args)]
            result[table] = {'rows': rows[:200], 'truncated': len(rows) > 200}
        return sanitize(result)


def collect(source, services, symbol='SUI'):
    source = source.resolve(strict=True)
    data = dict(schema='nexus-symbol-readonly-v3', symbol=symbol, created_at=datetime.now(timezone.utc).isoformat(),
        source=str(source), services=services,
        limits='JSON files and database are read sequentially, not one atomic runtime snapshot. '
               'No current exchange state is queried. No service is stopped or started.')
    for name in ('VERSION.txt', 'broker/okx.py', 'crypto_engine.py', 'MANIFEST_SHA256.json'):
        path = source/name
        data[name] = {'sha256': hashlib.sha256(path.read_bytes()).hexdigest()} if path.is_file() else {'missing': True}
    version = source/'VERSION.txt'
    if version.is_file():
        data['version'] = version.read_text(encoding='utf-8').strip()
    for name in ('crypto_positions.json', 'bot_order_registry.json', 'runtime_status_okx.json'):
        path = source/name
        if not path.is_file():
            data[name] = {'missing': True}
            continue
        try:
            raw = json.loads(path.read_text(encoding='utf-8'))
            data[name] = sanitize(raw if name.startswith('runtime_') else select_sui(raw, symbol))
        except (OSError, ValueError) as exc:
            data[name] = {'read_error': type(exc).__name__}
    data['decision_history.sqlite'] = read_database(source/'decision_history.sqlite', symbol)
    return data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, help='Explicit active bot folder; otherwise read systemd paths')
    parser.add_argument('--output', type=Path, help='New ZIP file; an existing file is never overwritten')
    parser.add_argument('--symbol', default='SUI', type=str.upper, help='Base symbol, e.g. BTC or SUI')
    args = parser.parse_args()
    if not re.fullmatch(r'[A-Z0-9]{1,20}', args.symbol):
        parser.error('Symbol muss aus 1 bis 20 Buchstaben/Ziffern bestehen')
    try:
        services = service_state()
    except (OSError, subprocess.SubprocessError) as exc:
        services = {'error': type(exc).__name__}
    source = args.source
    if source is None:
        roots = {services.get(unit, {}).get('WorkingDirectory') for unit in UNITS}
        if len(roots) != 1 or not next(iter(roots)):
            parser.error('Dienstpfade fehlen oder widersprechen sich. --source mit dem aktiven Ordner angeben.')
        source = Path(next(iter(roots)))
    data = collect(source, services, args.symbol)
    output = args.output or (Path.home()/('NEXUS_'+args.symbol+'_Diagnose_'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'.zip'))
    with zipfile.ZipFile(output, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(args.symbol+'_Diagnose.json', json.dumps(data, indent=2, ensure_ascii=False))
    output.chmod(0o600)
    print('Diagnose erstellt: '+str(output.resolve()))


if __name__ == '__main__':
    main()
