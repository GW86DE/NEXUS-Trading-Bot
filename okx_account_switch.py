"""Explicit fresh DEMO account activation. All remote operations are GETs.

An interrupted local commit leaves a durable startup barrier. Historical mixed
broker databases are never cleared or rewritten by the account switch.
"""
from pathlib import Path
from contextlib import closing
from datetime import datetime, timezone
import argparse
import getpass
import hashlib
import json
import os
import sqlite3
import tempfile
import uuid
import zipfile

from okx_account_context import AccountContextError, existing_accounts, read_context
from safe_persistence import atomic_write_json
from instance_lock import SingleInstanceLock


class SwitchError(RuntimeError):
    pass


def inspect_new(client):
    if client.demo is not True:
        raise SwitchError('Fresh-Start ist auf DEMO begrenzt')
    client.server_time_ms()
    cfg = client.account_config()
    if not str(cfg.get('uid') or '').strip():
        raise SwitchError('Neue Kontokennung fehlt')
    marker = '|'.join(('demo', str(client.base_url), str(cfg['uid']),
                       str(cfg.get('subAcct') or cfg.get('label') or '')))
    account = hashlib.sha256(marker.encode()).hexdigest()[:24]
    checks = [('/trade/orders-pending', {}), ('/account/positions', {})]
    checks += [('/trade/orders-algo-pending', {'ordType': t}) for t in ('conditional','oco','trigger','move_order_stop')]
    checks += [(path, {'instType': t, 'limit': '1'}) for path in
               ('/trade/fills','/trade/fills-history','/trade/orders-history-archive')
               for t in ('SPOT','MARGIN','SWAP','FUTURES','OPTION')]
    for path, params in checks:
        rows = client.request('GET', path, params=params, private=True)
        if not isinstance(rows, list):
            raise SwitchError('Vorpruefung unvollstaendig: '+path)
        if rows:
            raise SwitchError('Kein frisches Konto: Orders/Positionen/Handelsbelege vorhanden: '+path)
    balances = client.balances()
    if not isinstance(balances, dict):
        raise SwitchError('Guthaben konnte nicht gelesen werden')
    import config
    allowed = tuple(dict.fromkeys(str(x).upper() for x in getattr(
        config, 'OKX_ALLOWED_QUOTE_CCY', ('EUR','USDC')) if str(x).strip()))
    funded = []
    for currency, row in balances.items():
        try:
            available = float((row or {}).get('cash', 0.0) or 0.0)
        except (TypeError, ValueError, AttributeError):
            continue
        if available > 0:
            funded.append(str(currency).upper())
    funded = sorted(set(funded))
    supported = sorted(set(funded).intersection(allowed))
    unsupported = sorted(set(funded)-set(allowed))
    funding_state = ('SUPPORTED_FUNDS_AVAILABLE' if supported else
                     'UNSUPPORTED_FUNDS_ONLY' if unsupported else 'NO_FREE_BALANCE')
    return dict(account=account, environment='DEMO', checks=len(checks)+2,
                checked_at=datetime.now(timezone.utc).isoformat(),
                account_mode={'acctLv': str(cfg.get('acctLv') or ''),
                              'posMode': str(cfg.get('posMode') or '')},
                funding={'state': funding_state, 'allowed_currencies': list(allowed),
                         'funded_supported_currencies': supported,
                         'funded_unsupported_currencies': unsupported},
                history_limit='Nur die von OKX abrufbaren Historienfenster; kein Beweis lebenslanger Handelsfreiheit')


def make_archive(directory, context):
    """Keep full forensic state; SQLite backup includes committed WAL frames."""
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'_'+uuid.uuid4().hex[:8]
    archive = directory/('okx_account_archive_'+stamp+'.zip')
    manifest = {}
    reactivation_candidates = []
    # Preserve all persistent evidence, including shared DBs, without mutating it.
    from settings_migration import PERSISTENT_FILES
    names = set(PERSISTENT_FILES) | {'runtime_status_okx.json','okx_account_context.json'}
    names = {n for n in names if not any(w in n for w in ('credentials','settings','usage','cache'))}
    with tempfile.TemporaryDirectory(prefix='nexus_account_backup_') as td:
        with zipfile.ZipFile(archive, 'x', compression=zipfile.ZIP_DEFLATED) as z:
            for name in sorted(names):
                p = directory/name
                if not p.exists():
                    continue
                if p.is_symlink() or not p.is_file():
                    raise SwitchError('Ungueltiger Zustandspfad: '+name)
                if name.endswith('.sqlite'):
                    backup = Path(td)/name
                    with closing(sqlite3.connect(p.as_uri()+'?mode=ro', uri=True)) as source, closing(sqlite3.connect(backup)) as dest:
                        source.backup(dest)
                        if dest.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                            raise SwitchError('Datenbankarchiv unvollstaendig')
                        if name == 'decision_history.sqlite':
                            dest.row_factory = sqlite3.Row
                            tables = {r[0] for r in dest.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                            if 'okx_balance_gaps' in tables:
                                for gap in dest.execute("SELECT * FROM okx_balance_gaps WHERE status='PENDING'").fetchall():
                                    trade = dest.execute('SELECT * FROM trades WHERE trade_id=?', (gap['trade_id'],)).fetchone()
                                    reactivation_candidates.append(dict(balance_gap=dict(gap),
                                        trade_snapshot=dict(trade) if trade else None,
                                        position_restore_authorized=False))
                    content = backup.read_bytes()
                else:
                    content = p.read_bytes()
                z.writestr(name, content)
                manifest[name] = hashlib.sha256(content).hexdigest()
            z.writestr('ACCOUNT_ARCHIVE.json', json.dumps(dict(context=context, sha256=manifest,
                reactivation_candidates=reactivation_candidates,
                reactivation='Keine automatische Reaktivierung. Bestandsluecken, Fills und Schutzbelege muessen zuerst abgeglichen werden.'), indent=2))
        with zipfile.ZipFile(archive) as z:
            if z.testzip() is not None or any(hashlib.sha256(z.read(n)).hexdigest()!=h for n,h in manifest.items()):
                raise SwitchError('Archivpruefung fehlgeschlagen')
    archive.chmod(0o600)
    return archive


def activate(directory, client, *, credentials=None):
    directory = Path(directory).resolve(strict=True)
    with SingleInstanceLock(directory/'tradingbot.instance.lock'), SingleInstanceLock(directory/'okx_account_switch.lock'):
        context = read_context(directory)
        receipt = inspect_new(client)
        account = receipt['account']
        known = existing_accounts(directory)
        if context:
            known.add(context['account'])
            known.update(context.get('retired_accounts', []))
        if account in known:
            raise SwitchError('Konto ist bereits bekannt. Keine blinde Reaktivierung oder Ruecksetzung einer Risikobasis')
        archive = make_archive(directory, context or dict(legacy_accounts=sorted(known)))
        replacements = {
            'crypto_positions.json': {'positionen': []},
            'risk_state_okx.json': {},
            'okx_account_context.json': dict(version=1, account=account, environment='DEMO', strict=True,
                retired_accounts=sorted(known), archive=archive.name, precheck=receipt),
        }
        marker = directory/'okx_account_switch_pending.json'
        atomic_write_json(marker, dict(account=account, archive=archive.name, state='COMMITTING'))
        # The marker is intentionally not removed on an exception or power loss.
        # A new start must never use a half-written account/risk/credential tuple.
        if credentials is not None:
            from credential_store import save_credentials
            save_credentials(directory/'okx_credentials.json', credentials)
        for name, data in replacements.items():
            atomic_write_json(directory/name, data)
        marker.unlink()
        return dict(**receipt, archive=archive.name, nexus_positions=0)


def main():
    ap = argparse.ArgumentParser(description='Neues OKX-Demokonto archiviert und getrennt aktivieren')
    ap.add_argument('--okx-neues-konto', action='store_true', required=True)
    ap.add_argument('--nur-pruefen', action='store_true', help='Nur GET-Vorpruefung; kein Kontowechsel und keine Zustandsaenderung')
    ap.add_argument('--state-dir', type=Path, default=Path(__file__).resolve().parent)
    args = ap.parse_args()
    root = args.state_dir.resolve(strict=True)
    from credential_store import load_credentials
    old = load_credentials(root/'okx_credentials.json', {})
    if old.get('live_trading'):
        raise SwitchError('Live-Konfiguration: Wechsel abgebrochen')
    print('API-Zugang des NEUEN OKX-Demounterkontos eingeben. Eingaben bleiben verborgen.', flush=True)
    key = getpass.getpass('Neuer Demo API-Key: ').strip()
    secret = getpass.getpass('Neues Demo Secret: ').strip()
    phrase = getpass.getpass('Neue Demo Passphrase: ').strip()
    if not all((key, secret, phrase)):
        raise SwitchError('Alle drei Zugangsdaten erforderlich')
    from broker.okx import OKXClient
    import config
    client = OKXClient(key, secret, phrase, demo=True, base_url=config.OKX_BASE_URL)
    from okx_precheck_diagnostics import instrument_client
    instrument_client(client, SwitchError)
    if args.nur_pruefen:
        result = inspect_new(client)
        print('GET-Vorpruefung erfolgreich. Kein Kontowechsel ausgefuehrt.')
        print('Gepruefte Umgebung: '+result['environment'])
        funding = result.get('funding') or {}
        print('Finanzierung: '+str(funding.get('state') or 'UNKNOWN')
              + '; freigegeben finanziert: ' + (', '.join(funding.get('funded_supported_currencies') or []) or 'keine')
              + '; nicht freigegeben finanziert: ' + (', '.join(funding.get('funded_unsupported_currencies') or []) or 'keine'))
        mode = result.get('account_mode') or {}
        print('OKX-Kontomodus: acctLv='+str(mode.get('acctLv') or 'unbekannt')
              + ', posMode='+str(mode.get('posMode') or 'unbekannt'))
        return
    creds = dict(old, demo_api_key=key, demo_api_secret=secret, demo_passphrase=phrase,
                 live_trading=False, enabled=True)
    result = activate(root, client, credentials=creds)
    print('Kontowechsel abgeschlossen. NEXUS-Positionen: 0. Broker-Guthaben unveraendert.')
    print('Belegarchiv: '+result['archive'])
    print('Kontofingerprint: '+result['account'])


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        # Transport exceptions may contain broker content: never echo credentials.
        print('Kontowechsel abgebrochen: '+(str(exc) if isinstance(exc, (SwitchError, AccountContextError)) else type(exc).__name__))
        raise SystemExit(1)
