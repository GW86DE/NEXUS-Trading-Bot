#!/usr/bin/env python3
"""NEXUS: passive, bounded, standard-library-only 30 minute evidence collection.
Never imports NEXUS, sends a request, executes its code, or changes service/state.
"""
from __future__ import annotations
import argparse
import ast
from collections import Counter
from contextlib import closing, contextmanager
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import selectors
import shutil
import signal
import sqlite3
import stat
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import zipfile
from zoneinfo import ZoneInfo

TOOL_VERSION = '1.9.0'
OVERSIZE = '[OVERSIZE_CELL_OMITTED]'
MIB = 1024 * 1024
SNAPSHOT_SECONDS = 60  # 10.1.10: grosse, stark beschriebene DBs brauchen laenger
SNAPSHOT_MAX_BYTES = 1024*MIB
TABLE_QUERY_SECONDS = 10
UNITS = ('tradingbot-pi5.service', 'tradingbot-webui.service')
PROPERTIES = ('Id', 'LoadState', 'ActiveState', 'SubState', 'WorkingDirectory', 'MainPID',
              'ExecMainStartTimestamp', 'ExecMainExitTimestamp', 'ExecMainCode', 'ExecMainStatus',
              'NRestarts', 'Result', 'MemoryCurrent', 'CPUUsageNSec', 'TasksCurrent', 'FragmentPath')
JSON_FILES = '''runtime_status.json runtime_status_okx.json news_source_status.json
position_state.json crypto_positions.json stock_positions.json bot_order_registry.json fill_progress.json
risk_state.json risk_state_etoro.json risk_state_okx.json etoro_reconciliation.json scan_uebersicht.json
okx_accounting_repair_report.json okx_verified_history_repair_report.json
universe_state.json universe_settings.json approved_universe.json universe_proposals.json
crypto_dynamic_30.json crypto_dynamic_30_history.json core_volume_20.json core_volume_20_history.json
favorites.json underdog_freigabe.json crypto_strategy_mode.json risiko_stufen.json
telegram_control_status.json telegram_control_state.json telegram_queue.json ai_control_state.json
ai_attention_usage.json ai_usage.json ai_router_usage.json ai_router_cache.json api_daily_budgets.json
news_research_settings.json intelligence_settings.json ai_router_settings.json news_rules_settings.json
handel_settings.json second_opinion_settings.json web_ui_settings.json
market_session_state.json earnings_calendar_status.json walkforward_status.json ml_training_status.json
strategy_report.json trading_ready_notifications.json manual_trade_commands.json manual_coin_locks.json
bot_zustand.json fmp_reference_cache.json nexus_update_state.json okx_account_action.json candle_observations.json nasdaq_halt_diagnostics.json massive_status.json etoro_protection_journal.json
market_intelligence_status.json'''.split()
TEXT_FILES = ('VERSION.txt', 'RELEASE_BUILD.txt', 'handelsmodus.txt', 'aktives_profil.txt', 'bot_zustand.txt')
SAMPLE_FILES = ('runtime_status.json', 'runtime_status_okx.json', 'news_source_status.json',
                'telegram_control_status.json', 'ai_router_usage.json', 'api_daily_budgets.json',
                'market_intelligence_status.json')
DATABASES = ('decision_history.sqlite', 'broker_exit_journal.sqlite', 'pulsar_research.sqlite',
             'fmp_service.sqlite', 'market_candles.sqlite', 'freqtrade_scan.sqlite', 'massive_service.sqlite',
             'etoro_stream_inbox.sqlite')
INBOX_METADATA_COLUMNS = frozenset(('event_key', 'broker', 'account_fingerprint', 'environment',
    'received_at_utc', 'message_type', 'kind', 'position_id', 'instrument_id', 'validation',
    'state', 'deliveries', 'processed_at_utc'))
AUDITS = ('ai_usage_audit.jsonl', 'ai_usage_audit.1.jsonl', 'decision_journal.jsonl',
          'decision_journal.1.jsonl', 'decision_sources.jsonl', 'universe_audit.jsonl',
          'telegram_command_audit.jsonl')
LOG_NAMES = ('trading_bot.log', 'trading_bot.log.1', 'trading_bot.log.2', 'nexus_update.log', 'webui.log')
CREDENTIALS = ('okx_credentials.json', 'etoro_credentials.json', 'massive_credentials.json',
               'telegram_credentials.json', 'news_sources_credentials.json', 'openai_ai_settings.json',
               'alpha_vantage_credentials.json', 'web_ui_credentials.json', 'market_intelligence_credentials.json')
SENSITIVE = {'apikey', 'userkey', 'xuserkey', 'apisecret', 'secret', 'secretkey', 'password', 'passwort', 'passphrase',
             'token', 'bearertoken', 'accesstoken', 'refreshtoken', 'bottoken', 'authorization', 'cookie',
             'setcookie', 'sessionsecret', 'sessionkey', 'sessiontoken', 'privatekey', 'nonce',
             'noncehash', 'passwordhash', 'salt', 'hash', 'csrf', 'csrftoken', 'signingkey'}
# 'hash' is only secret within a credential object. Data/source hashes remain usable.
SENSITIVE.remove('hash')
TIME_COLUMNS = ('updated_at_utc', 'updated_at', 'created_at_utc', 'created_at', 'observed',
                'saved', 'at', 'created', 'eingestiegen_am', 'checked', 'ts', 'candle', 'triggered_at', 'updated')
PRIORITY_TABLES = ('pulsar_control', 'decisions', 'execution_orders', 'execution_fills', 'execution_events',
                   'etoro_settlement_reviews', 'etoro_cancellations', 'etoro_proceeds_observations', 'etoro_cash_receipts',
                   'trades', 'decision_orders', 'decision_events', 'pulsar_proposals', 'pulsar_audit',
                   'cache', 'assessments', 'attention_outcomes', 'usage', 'calls', 'evidence_uses', 'capabilities',
                   'broker_exit_intents', 'broker_exit_fill_events', 'processed', 'candles')


def utc(stamp=None):
    return datetime.fromtimestamp(time.time() if stamp is None else stamp, timezone.utc).isoformat()


def number(value):
    if isinstance(value, bool):
        return None
    try:
        n = float(value)
        return n if math.isfinite(n) else None
    except (TypeError, ValueError, OverflowError):
        return None


def timestamp(value):
    n = number(value)
    if n is not None:
        return n / 1000 if n > 100_000_000_000 else n
    try:
        dt = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return dt.timestamp() if dt.tzinfo is not None else None
    except (ValueError, TypeError, OverflowError):
        return None


def digest(data):
    return hashlib.sha256(data).hexdigest()


def dumps(data):
    return json.dumps(data, ensure_ascii=False, sort_keys=True, allow_nan=False)


def read_regular(path, limit, *, tail=False):
    """No symlinks, devices, FIFOs or unbounded reads; preserves truncated marker."""
    path = Path(path)
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode):
        raise ValueError('Kein regulaerer Dateieintrag; nicht gelesen')
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
    with os.fdopen(fd, 'rb') as stream:
        opened = os.fstat(stream.fileno())
        if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise ValueError('Datei waehrend Oeffnung ersetzt')
        offset = max(0, opened.st_size - limit) if tail else 0
        stream.seek(offset)
        data = stream.read(limit)
        after = os.fstat(stream.fileno())
    return data, {'size': opened.st_size, 'mtime_utc': utc(opened.st_mtime),
        'truncated': opened.st_size > limit, 'offset': offset,
        'changed_during_read': (opened.st_mtime_ns, opened.st_size) != (after.st_mtime_ns, after.st_size)}


class Scrubber:
    """Mask credentials even when reflected in logs; preserve tokens_used/source IDs."""
    def __init__(self):
        self.values = set()
        self.replacements = 0

    def learn(self, value, credential=False):
        if isinstance(value, dict):
            for key, item in value.items():
                norm = re.sub('[^a-z0-9]', '', str(key).lower())
                if (norm in SENSITIVE or norm.endswith(('apikey', 'userkey', 'apisecret', 'bottoken', 'passphrase', 'sessionsecret'))
                        or credential and norm in {'key', 'hash', 'session'}):
                    if isinstance(item, str) and len(item) >= 4:
                        self.values.add(item)
                self.learn(item, credential)
        elif isinstance(value, list):
            for item in value:
                self.learn(item, credential)

    def text(self, value):
        text = str(value)
        for secret in sorted(self.values, key=len, reverse=True):
            from urllib.parse import quote
            for encoded in {secret, quote(secret, safe='')}:
                if encoded in text:
                    self.replacements += text.count(encoded)
                    text = text.replace(encoded, '[MASKIERT]')
        rules = [
            (r'(?im)(\b(?:authorization|cookie|set-cookie)\s*[:=]\s*)[^\r\n]+', r'\1[MASKIERT]'),
            (r'(?i)(https?://)[^\s/@:]+:[^\s/@]+@', r'\1[MASKIERT]@'),
            (r'(?i)([?&](?:api_?key|token|access_token|secret|signature)=)[^\s&#"\']+', r'\1[MASKIERT]'),
            (r'(?i)(\b(?:api[ _-]?key|user[ _-]?key|api[ _-]?secret|access[ _-]?token|password|passphrase|secret|authorization|cookie)\s*["\']?\s*[:=]\s*["\']?)(?:Bearer\s+)?[^\s,;"\'}]+', r'\1[MASKIERT]'),
            (r'(?i)\bBearer\s+[^\s,;"\']+', 'Bearer [MASKIERT]'),
            (r'\bsk-[A-Za-z0-9_-]{10,}', '[MASKIERT]'),
            (r'\bbot\d{5,}:[A-Za-z0-9_-]{15,}', 'bot[MASKIERT]'),
            (r'\b\d{7,}:[A-Za-z0-9_-]{25,}', '[MASKIERT]'),
            (r'(?s)-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----', '[MASKIERT]'),
        ]
        for pattern, replacement in rules:
            text, n = re.subn(pattern, replacement, text)
            self.replacements += n
        return text

    def clean(self, value, depth=0):
        if depth > 45:
            return '[VERSCHACHTELUNG_BEGRenZT]'
        if isinstance(value, dict):
            result = {}
            for key, item in value.items():
                norm = re.sub('[^a-z0-9]', '', str(key).lower())
                if norm in SENSITIVE or norm.endswith(('apikey', 'userkey', 'apisecret', 'bottoken', 'passphrase', 'sessionsecret')):
                    result[self.text(key)] = '[MASKIERT]'
                    self.replacements += 1
                else:
                    result[self.text(key)] = self.clean(item, depth + 1)
            return result
        if isinstance(value, (list, tuple)):
            return [self.clean(item, depth + 1) for item in value]
        if isinstance(value, str):
            stripped = value.lstrip()
            if stripped.startswith(('{', '[')):
                try:
                    return self.clean(json.loads(value), depth + 1)
                except (ValueError, RecursionError):
                    pass
            return self.text(value)
        if isinstance(value, bytes):
            return {'binary_omitted': True, 'bytes': len(value)}
        if isinstance(value, float) and not math.isfinite(value):
            return None
        return value


def run_command(args, *, timeout=6, limit=2*MIB):
    """Bound stdout/stderr and duration; never shell=True, sudo or environment dump."""
    allowed = {'systemctl', 'journalctl', 'vcgencmd'}
    if Path(args[0]).name not in allowed:
        raise ValueError('Nicht erlaubtes Diagnosekommando')
    if Path(args[0]).name == 'systemctl' and (len(args) < 2 or args[1] != 'show'):
        raise ValueError('Nur systemctl show erlaubt')
    try:
        process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   stdin=subprocess.DEVNULL)
    except OSError as exc:
        return {'available': False, 'error': type(exc).__name__, 'output': ''}
    data = bytearray()
    truncated = timed_out = False
    try:
        with selectors.DefaultSelector() as sel:
            sel.register(process.stdout, selectors.EVENT_READ)
            deadline = time.monotonic() + timeout
            while sel.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    timed_out = True
                    break
                for key, _ in sel.select(min(remaining, .25)):
                    part = os.read(key.fileobj.fileno(), 65536)
                    if not part:
                        sel.unregister(key.fileobj)
                        continue
                    space = max(0, limit-len(data))
                    data.extend(part[:space])
                    if len(part) > space:
                        truncated = True
                        break
                if truncated:
                    break
        if process.poll() is None:
            if truncated or timed_out:
                process.terminate()
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
    finally:
        process.stdout.close()
        if process.poll() is None:
            process.kill()
            process.wait(timeout=2)
    return {'available': True, 'returncode': process.returncode,
            'truncated': truncated, 'timeout': timed_out,
            'output': data.decode('utf-8', errors='replace')}


def services():
    result = {}
    for unit in UNITS:
        raw = run_command(['systemctl', 'show', unit, '--no-pager', '--property='+','.join(PROPERTIES)], limit=65536)
        fields = dict(line.split('=', 1) for line in raw['output'].splitlines() if '=' in line)
        result[unit] = {**raw, 'fields': fields}
    return result


def valid_root(path):
    try:
        p = Path(path).expanduser()
        if not p.is_absolute():
            p = p.absolute()
        if not p.is_dir() or any(x.is_symlink() for x in (p, *p.parents)):
            return False
        data, _ = read_regular(p/'VERSION.txt', 512)
        return bool(re.fullmatch(r'\d+\.\d+(?:\.\d+)?(?:[-_][A-Za-z0-9.-]+)?', data.decode().strip())) and (p/'nexus_start.py').is_file()
    except (OSError, ValueError, UnicodeError):
        return False


def detect_root(explicit, status, launcher):
    roots = [s.get('fields', {}).get('WorkingDirectory') for s in status.values()]
    good = sorted({str(Path(p).absolute()) for p in roots if p and valid_root(p)})
    if explicit:
        p = Path(explicit).expanduser().absolute()
        if not valid_root(p):
            return None, {'state': 'ERROR', 'reason': 'Expliziter Quellordner nicht als NEXUS erkannt', 'path': str(p)}
        return p, {'state': 'EXPLICIT', 'service_roots': good, 'mismatch': bool(good and good != [str(p)])}
    if len(good) == 1 and len(set(p for p in roots if p)) == 1:
        return Path(good[0]), {'state': 'SERVICE_PATH', 'service_roots': good}
    if len(good) > 1 or len(set(p for p in roots if p)) > 1:
        return None, {'state': 'AMBIGUOUS', 'reason': 'Core/WebUI zeigen auf verschiedene Ordner; --quelle erforderlich', 'service_roots': roots}
    candidates = [Path.cwd(), Path(launcher).absolute().parent]
    matches = sorted({str(p.absolute()) for p in candidates if valid_root(p)})
    if not matches:
        # Version-independent fallback; never silently pick an old release
        # when several installations exist and the active service is unknown.
        try:
            candidates = list((Path.home()/'Georg').glob('TradingBot*'))[:100]
        except OSError:
            candidates = []
        matches = sorted({str(p.absolute()) for p in candidates if valid_root(p)})
    if len(matches) == 1:
        return Path(matches[0]), {'state': 'FALLBACK_PATH', 'reason': 'Aktiver Dienstpfad nicht belegt', 'service_roots': roots}
    return None, {'state': 'UNKNOWN', 'reason': 'Kein eindeutiger NEXUS-Ordner; --quelle verwenden', 'candidates': matches}


class Bundle:
    def __init__(self, output, *, max_bytes=128*MIB):
        self.directory = Path(output)
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.run_id = 'NEXUS_10_Diagnose_' + datetime.now(timezone.utc).strftime('%Y-%m-%d_%H-%M-%S_%f_UTC') + '_' + uuid.uuid4().hex[:10]
        self.work = Path(tempfile.mkdtemp(prefix=self.run_id+'_', dir=self.directory))
        self.maximum = max_bytes
        self.used = 0
        self.omitted = []
        self.scrub = Scrubber()

    def write(self, name, data, *, essential=False):
        if isinstance(data, (dict, list)):
            body = (dumps(self.scrub.clean(data))+'\n').encode()
        else:
            body = self.scrub.text(data).encode()
        if not essential and self.used+len(body) > self.maximum:
            self.omitted.append({'file': name, 'reason': 'Gesamtausgabe begrenzt', 'bytes': len(body)})
            return False
        path = self.work/name
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with path.open('xb') as stream:
            stream.write(body)
        path.chmod(0o600)
        self.used += len(body)
        return True

    def finish(self):
        self.write('AUSLASSUNGEN.json', self.omitted, essential=True)
        manifest = {}
        for path in sorted(self.work.rglob('*')):
            if path.is_file():
                manifest[path.relative_to(self.work).as_posix()] = digest(path.read_bytes())
        self.write('MANIFEST_SHA256.json', manifest, essential=True)
        output = self.directory/(self.run_id+'.zip')
        # Exclusive mode prevents accidental overwrite, even under concurrent runs.
        with zipfile.ZipFile(output, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=3) as archive:
            for path in sorted(self.work.rglob('*')):
                if path.is_file():
                    archive.write(path, path.relative_to(self.work).as_posix())
        output.chmod(0o600)
        with zipfile.ZipFile(output) as archive:
            bad = archive.testzip()
            if bad:
                raise OSError('ZIP-Pruefung fehlgeschlagen: '+bad)
        shutil.rmtree(self.work)
        return output


def error_record(exc):
    return {'status': 'ERROR', 'error': type(exc).__name__, 'detail': str(exc)[:350]}


def load_json(path, scrub, *, limit=8*MIB):
    try:
        data, meta = read_regular(path, limit)
        if meta['truncated']:
            return {**meta, 'status': 'OMITTED_TOO_LARGE'}
        value = json.loads(data)
        scrub.learn(value)
        return {**meta, 'status': 'OK', 'data': scrub.clean(value)}
    except FileNotFoundError:
        return {'status': 'MISSING'}
    except (OSError, ValueError, RecursionError) as exc:
        return error_record(exc)


def learn_credentials(root, scrub):
    """Read solely for masking and safe configuration facts; never export files."""
    result = {}
    safe_fields = {'enabled', 'aktiv', 'live_trading', 'paper_trading', 'daily_limit',
                   'model', 'modell', 'model_luna', 'model_terra', 'model_astra', 'timeout_seconds'}
    for name in CREDENTIALS:
        try:
            raw, meta = read_regular(root/name, MIB)
            if meta['truncated']:
                result[name] = {'status': 'OVERSIZE_NOT_READ'}
                continue
            value = json.loads(raw)
            scrub.learn(value, credential=True)
            result[name] = {'status': 'PRESENT', 'values_excluded': True,
                'nonsecret_configuration': scrub.clean({k: v for k, v in value.items() if k in safe_fields})
                    if isinstance(value, dict) else {}}
        except FileNotFoundError:
            result[name] = {'status': 'MISSING'}
        except (OSError, ValueError) as exc:
            result[name] = {'status': 'UNREADABLE', 'error': type(exc).__name__}
    return result


def config_literals(root, scrub):
    try:
        raw, _ = read_regular(root/'config.py', MIB)
        tree = ast.parse(raw.decode('utf-8-sig'))
        values = {}
        for node in tree.body:
            if not isinstance(node, ast.Assign):
                continue
            for target in node.targets:
                if not isinstance(target, ast.Name):
                    continue
                name = target.id
                if any(s in name for s in ('KEY', 'TOKEN', 'PASSWORD', 'SECRET', 'PASSPHRASE')):
                    continue
                if name.startswith(('NEWS_', 'MASSIVE_', 'FMP_', 'PULSAR_', 'AI_ROUTER_', 'CRYPTO_',
                                    'STOCK_', 'FREQTRADE_', 'TELEGRAM_', 'RISK_')) or name in {
                    'CYCLE_MINUTES', 'INSTRUMENTS_PER_CYCLE', 'PAUSE_BETWEEN_INSTRUMENTS', 'USE_COMPLETED_BAR_ONLY'}:
                    try:
                        values[name] = ast.literal_eval(node.value)
                    except (ValueError, TypeError, SyntaxError):
                        pass
        return {'status': 'OK', 'kind': 'STATIC_DEFAULTS_ONLY',
                'notice': 'Kein Import. Laufzeitdateien koennen diese Standardwerte uebersteuern.',
                'values': scrub.clean(values)}
    except (OSError, ValueError, SyntaxError) as exc:
        return error_record(exc)


def source_identity(root):
    result = {'root': str(root), 'checked_at': utc()}
    try:
        raw, meta = read_regular(root/'MANIFEST_SHA256.json', MIB)
        manifest = json.loads(raw)
        if meta['truncated'] or not isinstance(manifest, dict) or len(manifest) > 2000:
            raise ValueError('Ungeeignetes Manifest')
        changes = []
        checked = 0
        for relative, expected in manifest.items():
            p = Path(relative)
            if p.is_absolute() or '..' in p.parts or any(x.is_symlink() for x in (root/p, *(root/p).parents)):
                changes.append({'file': relative, 'status': 'UNSAFE_PATH'})
                continue
            try:
                data, detail = read_regular(root/p, 8*MIB)
                actual = digest(data)
                checked += 1
                if detail['truncated'] or actual != expected:
                    changes.append({'file': relative, 'expected': expected, 'actual': actual, 'truncated': detail['truncated']})
            except (OSError, ValueError) as exc:
                changes.append({'file': relative, **error_record(exc)})
        result.update(status='MATCH' if not changes else 'DIFFERENCES', files_checked=checked, differences=changes)
    except (OSError, ValueError) as exc:
        result.update(error_record(exc))
    return result


def qid(name):
    return '"'+str(name).replace('"', '""')+'"'


def db_connection(path, seconds=15):
    if path.is_symlink() or not path.is_file():
        raise FileNotFoundError('Datenbank fehlt oder ist verlinkt')
    con = sqlite3.connect(path.absolute().as_uri()+'?mode=ro', uri=True, timeout=.25)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA query_only=ON')
    con.execute('PRAGMA trusted_schema=OFF')
    deadline = time.monotonic()+seconds
    con.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
    con.execute('BEGIN')
    return con


def newest_clause(columns):
    order = next((key for key in TIME_COLUMNS if key in columns), None)
    if order:
        return ' ORDER BY '+qid(order)+' DESC'
    if 'id' in columns:
        return ' ORDER BY "id" DESC'
    return ' ORDER BY rowid DESC'


@contextmanager
def database_snapshot(path, directory, metadata):
    """SQLite online backup, including committed WAL; no live long read transaction.

    Raw bytes stay in a private temporary directory OUTSIDE the ZIP staging tree.
    Page steps release the live read lock. Redaction only begins after closing
    the live connection. Never fall back to copying .sqlite without its WAL.
    """
    if path.is_symlink() or not path.is_file():
        raise FileNotFoundError('Datenbank fehlt oder ist verlinkt')
    if path.stat().st_size > SNAPSHOT_MAX_BYTES:
        raise ValueError('Datenbank groesser als begrenztes Sicherungsbudget (1 GiB)')
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix='nexus_db_snapshot_', dir=directory) as folder:
        target = Path(folder)/'snapshot.sqlite'
        target.touch(mode=0o600)
        with closing(sqlite3.connect(path.absolute().as_uri()+'?mode=ro', uri=True, timeout=2.0)) as source:
            source.execute('PRAGMA query_only=ON')
            source.execute('PRAGMA trusted_schema=OFF')
            page_size = source.execute('PRAGMA page_size').fetchone()[0]
            journal_mode = str(source.execute('PRAGMA journal_mode').fetchone()[0])
            metadata['source_journal_mode'] = journal_mode
            if shutil.disk_usage(folder).free < path.stat().st_size+32*MIB:
                raise OSError('Zu wenig Platz fuer temporaere SQLite-Sicherung')
            def progress(status, remaining, total):
                if time.monotonic()-started > SNAPSHOT_SECONDS:
                    raise TimeoutError('SQLite-Sicherung zeitlich begrenzt; Quelle bleibt unveraendert')
                if total*page_size > SNAPSHOT_MAX_BYTES:
                    raise ValueError('SQLite-Sicherung ueberschreitet 1 GiB')
                if shutil.disk_usage(folder).free < remaining*page_size+32*MIB:
                    raise OSError('Zu wenig Platz fuer temporaere SQLite-Sicherung')
            with closing(sqlite3.connect(target)) as destination:
                # 10.1.10: Bei WAL blockiert ein Leser keine Schreiber; die
                # Sicherung laeuft in EINEM Schritt und kann nicht mehr durch
                # laufende Bot-Schreibzugriffe neu gestartet werden. Die
                # bisherige 128-Seiten-Schrittsicherung startete bei einer
                # stark beschriebenen decision_history immer wieder neu, lief
                # ins Zeitlimit und stoerte gleichzeitig die Bot-Abfragen
                # (beobachtete OperationalError am 16.09.2026). Nicht-WAL
                # behaelt die schonende Schrittsicherung.
                if journal_mode.lower() == 'wal':
                    source.backup(destination, pages=-1)
                else:
                    source.backup(destination, pages=128, progress=progress, sleep=.05)
        metadata.update(snapshot_seconds=round(time.monotonic()-started,3),
                        snapshot_bytes=target.stat().st_size, snapshot_finished_at=utc())
        yield target


def export_database(path, bundle, prefix, *, limit=5000):
    """Sanitize a consistent private backup, never while holding a live DB lock."""
    summary = {'status': 'MISSING', 'database': path.name, 'observed_at': utc(), 'tables': {},
               'consistency': 'SQLite-Online-Sicherung; WAL beruecksichtigt. Auswertung auf privater Kopie; getrennt von anderen Dateien/DBs.'}
    kept = {}
    if not path.exists():
        bundle.write(prefix+'/index.json', summary)
        return summary, kept
    try:
        with database_snapshot(path, bundle.directory, summary) as snapshot, closing(db_connection(snapshot)) as con:
            schema = con.execute("SELECT name,sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()
            summary.update(status='OK', schema_tables=len(schema))
            names = sorted(schema, key=lambda r: (PRIORITY_TABLES.index(r['name']) if r['name'] in PRIORITY_TABLES else 100, r['name']))
            summary['tables_omitted'] = [r['name'] for r in names[100:]]
            for idx, table in enumerate(names[:100]):
                name, sql = table['name'], table['sql'] or ''
                label = re.sub('[^A-Za-z0-9_-]', '_', name)[:80]+'_'+digest(name.encode())[:8]
                meta = {'export_file': prefix+'/'+label+'.jsonl', 'schema': sql, 'rows_exported': 0,
                        'total_rows': None, 'truncated': False, 'status': 'OK', 'omitted_cells': []}
                summary['tables'][name] = meta
                if path.name == 'etoro_stream_inbox.sqlite' and name != 'etoro_stream_events':
                    meta.update(status='OMITTED_NOT_IN_ALLOWLIST', schema=None)
                    continue
                if 'VIRTUAL TABLE' in sql.upper():
                    meta.update(status='OMITTED_VIRTUAL_TABLE')
                    continue
                try:
                    # Each table gets its own SQL deadline. Time spent masking a
                    # previous table must not poison the following table's query.
                    deadline = time.monotonic()+TABLE_QUERY_SECONDS
                    con.set_progress_handler(lambda: int(time.monotonic()>deadline), 1000)
                    columns = [r['name'] for r in con.execute('PRAGMA table_info('+qid(name)+')')]
                    meta['columns'] = columns
                    if path.name == 'etoro_stream_inbox.sqlite':
                        # Never load event_json, matched_json, message IDs or error
                        # text into an export row. Only explicit delivery metadata
                        # leaves the private SQLite backup, even after a schema change.
                        meta['excluded_columns'] = sorted(set(columns)-INBOX_METADATA_COLUMNS)
                        columns = [c for c in columns if c in INBOX_METADATA_COLUMNS]
                        meta.update(columns=columns, schema=None, export_policy='SCOPED_PRIVATE_STREAM_METADATA_ONLY')
                        if not columns:
                            meta.update(status='OMITTED_NO_SAFE_COLUMNS')
                            continue
                    meta['total_rows'] = con.execute('SELECT COUNT(*) FROM '+qid(name)).fetchone()[0]
                    rows = []
                    if name == 'candles' and {'domain','instrument','ts'}.issubset(columns):
                        groups = con.execute('SELECT domain,instrument,COUNT(*) AS count,MIN(ts) AS first,MAX(ts) AS last '
                            'FROM candles GROUP BY domain,instrument LIMIT 81').fetchall()
                        meta['series'] = [dict(g) for g in groups[:80]]
                        meta['series_truncated'] = len(groups)>80
                        for group in groups[:80]:
                            rows.extend(con.execute('SELECT * FROM candles WHERE domain=? AND instrument=? ORDER BY ts DESC LIMIT 600',
                                                   (group['domain'], group['instrument'])).fetchall())
                    else:
                        order = newest_clause(columns)
                        if name == 'trades' and {'ausgestiegen_am','eingestiegen_am'}.issubset(columns):
                            order = ' ORDER BY (ausgestiegen_am IS NULL OR ausgestiegen_am=\'\') DESC, COALESCE(ausgestiegen_am,eingestiegen_am) DESC'
                        elif name == 'execution_orders' and {'terminal','accounted','updated_at'}.issubset(columns):
                            order = ' ORDER BY (terminal=0 OR accounted=0) DESC, updated_at DESC'
                        elif name == 'decisions' and 'created_at_utc' in columns:
                            order = ' ORDER BY created_at_utc DESC, id DESC' if 'id' in columns else ' ORDER BY created_at_utc DESC'
                        elif name == 'etoro_stream_events' and 'received_at_utc' in columns:
                            order = ' ORDER BY received_at_utc DESC'
                        if 'WITHOUT ROWID' in sql.upper() and order == ' ORDER BY rowid DESC':
                            order = ''
                        projection = ','.join('CASE WHEN typeof('+qid(c)+') IN (\'text\',\'blob\') AND length('+qid(c)+')>2097152 THEN \'[OVERSIZE_CELL_OMITTED]\' ELSE '+qid(c)+' END AS '+qid(c) for c in columns)
                        rows = con.execute('SELECT '+projection+' FROM '+qid(name)+order+' LIMIT ?', (limit+1,))
                    # Read bounded raw rows first. Secret filtering may be slow
                    # on the Pi and is deliberately outside the SQL deadline.
                    raw_rows, raw_size = [], 0
                    try:
                        for row in rows:
                            data = dict(row)
                            row_size = sum(len(v.encode('utf-8')) if isinstance(v,str) else len(v) if isinstance(v,bytes) else 16 for v in data.values())
                            if raw_size+row_size>12*MIB or (name!='candles' and len(raw_rows)>=limit):
                                meta['truncated'] = True
                                break
                            raw_rows.append(data)
                            raw_size += row_size
                    except sqlite3.Error as exc:
                        meta.update(error_record(exc))
                        meta['truncated'] = True
                    finally:
                        if hasattr(rows, 'close'):
                            rows.close()
                        con.set_progress_handler(None, 0)
                    cleaned = []
                    size = 0
                    for data in raw_rows:
                        for column, value in data.items():
                            if value == OVERSIZE:
                                meta['omitted_cells'].append({'column': column,
                                    'row_identity': {k: data[k] for k in ('id','key','saved','expires') if k in data},
                                    'reason': 'OVERSIZE_CELL_OMITTED'})
                        bundle.scrub.learn(data)
                        clean = bundle.scrub.clean(data)
                        encoded = dumps(clean)
                        if size+len(encoded.encode()) > 10*MIB:
                            meta['truncated'] = True
                            break
                        size += len(encoded.encode())+1
                        cleaned.append(clean)
                    meta['rows_exported'] = len(cleaned)
                    meta['omitted_cell_count'] = len(meta['omitted_cells'])
                    meta['cell_completeness'] = 'PARTIAL' if meta['omitted_cells'] else 'COMPLETE'
                    meta['truncated'] = meta['truncated'] or len(cleaned) < meta['total_rows']
                    if bundle.write(meta['export_file'], '\n'.join(dumps(r) for r in cleaned)+'\n'):
                        kept[name] = cleaned
                    else:
                        meta.update(status='OUTPUT_LIMIT', rows_exported=0)
                except (sqlite3.Error, OSError, ValueError, RecursionError) as exc:
                    meta.update(error_record(exc))
                    meta['truncated'] = True
                finally:
                    con.set_progress_handler(None, 0)
            con.rollback()
            if any(t.get('status')!='OK' for t in summary['tables'].values()) or summary['tables_omitted']:
                summary['status'] = 'PARTIAL_ERROR'
            elif any(t.get('truncated') or t.get('omitted_cell_count') for t in summary['tables'].values()):
                summary['status'] = 'PARTIAL_LIMITED'
    except (sqlite3.Error, OSError, ValueError) as exc:
        summary.update(error_record(exc))
    summary['finished_at'] = utc()
    bundle.write(prefix+'/index.json', summary)
    return summary, kept


def sample_pulsar(root, scrub, memo=None):
    result = {}
    try:
        with closing(db_connection(root/'decision_history.sqlite', seconds=2)) as con:
            row = con.execute('SELECT * FROM pulsar_control WHERE id=1').fetchone()
        result['control'] = scrub.clean(dict(row)) if row else None
    except (OSError, sqlite3.Error) as exc:
        result['control_error'] = type(exc).__name__
    try:
        with closing(db_connection(root/'pulsar_research.sqlite', seconds=2)) as con:
            rows = con.execute("SELECT key,saved,expires,CASE WHEN length(payload)>16777216 THEN '[OVERSIZE_CELL_OMITTED]' ELSE payload END AS payload FROM cache WHERE key IN ('status','top5','diagnostic_top5','coverage','candidate_selection','source_status:tradestie','backoff:tradestie','ai:precheck:retry_after','ai:precheck:last_failure')").fetchall()
        # Large unchanged top5 packets are read briefly, fingerprinted, and
        # scrubbed only once. No SQLite read lock is held while masking.
        cached = {} if memo is None else memo
        sanitized = []
        for r in rows:
            raw = dict(r)
            payload = raw.get('payload') or ''
            fingerprint = digest(dumps([raw.get('saved'), raw.get('expires'), payload,
                                        sorted(scrub.values)]).encode())
            key = str(raw.get('key'))
            previous = cached.get(key)
            if previous and previous[0] == fingerprint:
                clean = previous[1]
            else:
                clean = scrub.clean(raw)
                cached[key] = (fingerprint, clean)
            sanitized.append(clean)
            if payload == OVERSIZE:
                result['cache_error'] = 'OVERSIZE_CELL_OMITTED: Direktlesebudget 16 MiB pro Zelle'
        for key in set(cached) - {str(r['key']) for r in rows}:
            cached.pop(key, None)
        result['cache'] = sanitized
    except (OSError, sqlite3.Error) as exc:
        result['cache_error'] = type(exc).__name__
    return result


def system_sample(status, disk_path):
    data = {'at': utc(), 'load': list(os.getloadavg()) if hasattr(os, 'getloadavg') else None,
            'cpu_count': os.cpu_count(), 'python': sys.version.split()[0], 'processes': {}}
    reads = {'memory': '/proc/meminfo', 'cpu_ticks': '/proc/stat', 'uptime': '/proc/uptime',
             'temperature_millidegrees': '/sys/class/thermal/thermal_zone0/temp'}
    for key, name in reads.items():
        try:
            raw = Path(name).read_text()[:30000]
            data[key] = raw.splitlines()[0] if key != 'memory' else raw
        except OSError:
            data[key] = None
    for unit, record in status.items():
        pid = record.get('fields', {}).get('MainPID')
        if pid and str(pid).isdigit() and int(pid)>0:
            p = Path('/proc')/str(pid)
            row = {'pid': int(pid)}
            try:
                # No command line or environment; they can contain secrets.
                row['status'] = '\n'.join(line for line in (p/'status').read_text().splitlines()
                    if line.split(':',1)[0] in {'Name','State','VmRSS','VmSize','Threads'})
                row['stat'] = (p/'stat').read_text()
                row['cwd'] = str((p/'cwd').resolve())
            except OSError as exc:
                row['error'] = type(exc).__name__
            data['processes'][unit] = row
    try:
        usage = shutil.disk_usage(disk_path)
        data['disk_bytes'] = {'total': usage.total, 'free': usage.free, 'used': usage.used}
    except OSError:
        data['disk_bytes'] = None
    return data


def capture_logs(root, bundle, prefix):
    results, audits = {}, []
    names = list(dict.fromkeys(AUDITS+LOG_NAMES))
    # Recent bounded analysis-job status/output files also belong to diagnosis.
    jobdir = root/'runtime/webui_analysis'
    if jobdir.is_dir() and not any(p.is_symlink() for p in (jobdir,*jobdir.parents)):
        names.extend('runtime/webui_analysis/'+p.name for p in sorted(jobdir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)[:20]
                     if p.suffix in {'.json','.log'} and not p.is_symlink())
    for name in names:
        try:
            raw, meta = read_regular(root/name, 4*MIB, tail=True)
            if meta['offset']:
                raw = raw.partition(b'\n')[2]  # Drop partial first line, never emit half a secret.
            text = raw.decode('utf-8', errors='replace')
            cleaned_lines = []
            bad = 0
            for line in text.splitlines():
                try:
                    parsed = json.loads(line)
                    bundle.scrub.learn(parsed)
                    clean = bundle.scrub.clean(parsed)
                    cleaned_lines.append(dumps(clean))
                    if name.startswith('ai_usage_audit') and isinstance(clean, dict):
                        audits.append(clean)
                except (ValueError, RecursionError):
                    cleaned_lines.append(bundle.scrub.text(line))
                    bad += 1
            meta.update(status='OK', invalid_json_lines=bad if name.endswith('.jsonl') else None)
            if not bundle.write(prefix+'/'+name, '\n'.join(cleaned_lines)+'\n'):
                meta.update(status='OUTPUT_LIMIT', truncated=True)
            results[name] = meta
        except FileNotFoundError:
            results[name] = {'status': 'MISSING'}
        except (OSError, ValueError) as exc:
            results[name] = error_record(exc)
    bundle.write(prefix+'/index.json', results)
    return audits


def capture_risk_period_proofs(root, bundle, prefix, risk, *, limit=2*MIB):
    """Export only named period proofs beside the state, never arbitrary backups."""
    receipt = dict_value(dict_value(risk).get('basis_review_receipt'))
    expected = receipt.get('archive_sha256')
    rows = []
    patterns = {'archive': r'risk_state_etoro\.json\.legacy-([0-9a-f]{64})\.bak',
                'evidence': r'etoro_risk_period_([0-9a-f]{64})\.json'}
    for kind, pattern in patterns.items():
        name = receipt.get(kind)
        record = {'kind': kind, 'file': name, 'present': None, 'exported': False,
                  'expected_archive_sha256': expected, 'sha256_raw': None}
        match = re.fullmatch(pattern, name) if isinstance(name, str) else None
        if not receipt:
            record['status'] = 'NOT_REFERENCED'
        elif not match or match.group(1) != expected:
            record['status'] = 'INVALID_REFERENCE'
        else:
            try:
                raw, meta = read_regular(Path(root)/name, limit)
                record.update(meta, present=True)
                if meta['truncated']:
                    record.update(status='OMITTED_TOO_LARGE', hash_status='UNAVAILABLE_READ_LIMIT')
                elif meta['changed_during_read']:
                    record.update(status='CHANGED_DURING_READ', hash_status='UNSTABLE')
                else:
                    record.update(sha256_raw=digest(raw), hash_status='COMPLETE_FILE')
                    record['archive_hash_matches'] = digest(raw) == expected if kind == 'archive' else None
                    parsed = json.loads(raw)
                    bundle.scrub.learn(parsed)
                    member = prefix+'/risk_period_proofs/'+name+'.json'
                    exported = bundle.write(member, {'status': 'OK', 'sha256_raw': digest(raw),
                        'data': bundle.scrub.clean(parsed),
                        'meaning': 'Bereinigter JSON-Beleg; sha256_raw gehoert den Originalbytes vor Bereinigung.'})
                    record.update(status=('HASH_MISMATCH' if kind == 'archive' and digest(raw) != expected
                                          else 'OK') if exported else 'OUTPUT_LIMIT',
                                  exported=exported, export_file=member if exported else None)
            except FileNotFoundError:
                record.update(status='MISSING', present=False)
            except (OSError, ValueError, RecursionError) as exc:
                record.update(error_record(exc))
        rows.append(bundle.scrub.clean(record))
    result = {'status': 'OK' if all(r['status'] == 'OK' for r in rows) else
                        'NOT_REFERENCED' if not receipt else 'INCOMPLETE',
              'data': {'references': rows, 'search_scope': 'CURRENT_STATE_DIRECTORY_ONLY',
                       'meaning': 'Nur die zwei referenzierten Originalbelege; fehlend oder gekuerzt bleibt unbelegt.'}}
    bundle.write(prefix+'/risk_period_proofs.json', result, essential=True)
    return result


def capture_states(root, bundle, prefix):
    values = {}
    for name in JSON_FILES:
        record = load_json(root/name, bundle.scrub)
        if name == 'market_intelligence_status.json' and isinstance(record.get('data'), dict):
            record['data'] = x_receipts_only(record['data'])
        bundle.write(prefix+'/'+name, record)
        values[name] = record
    for name in TEXT_FILES:
        try:
            raw, meta = read_regular(root/name, 65536)
            record = {**meta, 'status': 'OK', 'data': raw.decode('utf-8', errors='replace')}
        except FileNotFoundError:
            record = {'status': 'MISSING'}
        except (OSError, ValueError) as exc:
            record = error_record(exc)
        bundle.write(prefix+'/'+name+'.json', record)
        values[name] = record
    values['risk_period_proofs.json'] = capture_risk_period_proofs(root, bundle, prefix,
        values.get('risk_state_etoro.json', {}).get('data'))
    return values


def object_value(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def table_rows(databases, name, table):
    return databases.get(name, {}).get(table, [])


def dict_value(value):
    value = object_value(value)
    return value if isinstance(value, dict) else {}


X_RECEIPT_FIELDS = frozenset('''source source_family role mode trade_effect direct_trade_effect
settings state status detail observed_at worker_heartbeat last_attempt last_success cooldown_until
counts requests successful_requests failed_requests posts_received unique_posts duplicates processed_posts
events consumer_reads budget month limit_eur reserved_eur charged_upper_eur remaining_eur invoice_reconciled
scope attention consumers content_exported raw_post_retention_days enabled monthly_budget_eur symbols
priority_accounts pricing_acknowledged counts_interval_hours posts_per_request searches_per_day configured
pricing_acknowledged_at pricing_reference pricing_date pricing_url pricing_basis price_micro_eur
estimated_month_max_eur estimate_scope pricing_current bearer_configured token_configured
symbol query_hash coverage_status day count stale comparison_days complete_days baseline_window_days
baseline_ready same_day_type_median long_ratio short_ratio market_query_share market_control_query_hash
normalized_ratio normalized_comparison_days normalization day_type day_type_method absence_inferred
calibrated_spike event_id topic title detected_at event_time expires_at posts distinct_accounts_in_sample
max_author_share duplicate_texts evidence_hashes near_duplicate_posts largest_30m_window_share post_ids
affected_symbols linked_domains primary_source_confirmed consumer context payload_hash consumed id kind
started http body_hash coverage finished received processed discovery automatic candidates candidate_count
source_accounts handle group reference selection selection_checked_at api_identity_verified statement_confirmed
plan posts_per_search slots_utc counts_per_day_max monitoring_symbols cohort_limit evidence_ids sampled_post_count
topics source_roles attention_kind linked_url_hashes counts_coverage checked_at expected_days observed_days
missing_days invalid_rows has_errors has_next_page consecutive_incomplete_days control_query_hash
previous_control_query_hash changed_at reason members normalization_status normalization_epoch
missing_comparison_days missing_control_days inconsistent_control_days coverage_issue active query_epoch
retained_posts cooldown_until_utc next_due_at last_request_at last_request_state discovery_enabled
month_days request_cost_upper_eur automatic_discovery enabled_at generated_at configuration_error
estimated_month_eur price_basis_date price_source post_usd counts_request_usd eur_reserve_multiplier
cost_scope raw_retention_days manual_symbols_required monitored_symbols_max'''.split())
X_RECEIPT_FIELDS = X_RECEIPT_FIELDS | frozenset('''daily_samples short_comparison_method confidence
    event_type hashes source_identity_verified source_priority discovery_candidates latest_post_at
    duplicate_text_count instrument_identity_verified independence_confirmed checked_day
    profile_validation_status monitoring_status zero_control_days candidate_research
    candidate_searches_per_day total_searches_per_day_max'''.split())


def x_receipts_only(value, _depth=0, _path=()):
    """Bounded, explicit metadata projection; unknown future content is omitted.

    Configured public source-account handles may be shown. Post-author identity,
    search expressions and raw social text are never fields in this contract.
    """
    if _depth > 10:
        return None
    if isinstance(value, dict) and 'candidate_research' in _path:
        fields = X_RECEIPT_FIELDS | frozenset("""queue_size max_queue searches_per_day_max symbols_per_search_max
            next_slot_at candidates company_name origin selected_at profile_source_id request_id query query_hash
            last_attempt processed_at request_posts_received matched_posts usable_posts excluded_duplicates_or_spam
            sentiment categories positive negative mixed unclassified evidence post_id created_at evidence_hash
            category topic assessment method recent_queries started source_family primary_source_confirmed""".split())
        return {str(k): x_receipts_only(v, _depth+1, (*_path, k)) for k,v in list(value.items())[:100]
                if k in fields}
    if isinstance(value, dict):
        return {str(k): x_receipts_only(v, _depth+1, (*_path, k)) for k, v in list(value.items())[:250]
                if k in X_RECEIPT_FIELDS and not (k == 'posts' and isinstance(v, (list, dict)))
                and not (k == 'handle' and 'source_accounts' not in _path)}
    if isinstance(value, (list, tuple)):
        return [x_receipts_only(v, _depth+1, _path) for v in value[:1000]]
    if isinstance(value, str):
        return value[:2000]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return None


def bounded_metadata(value, fields, *, depth=0):
    """Project a persisted receipt without assuming unknown keys are harmless."""
    if depth > 8:
        return None
    if isinstance(value, dict):
        return {str(k): bounded_metadata(v, fields, depth=depth+1)
                for k, v in list(value.items())[:150] if k in fields}
    if isinstance(value, (list, tuple)):
        return [bounded_metadata(v, fields, depth=depth+1) for v in value[:100]]
    return value[:1000] if isinstance(value, str) else value if value is None or isinstance(value, (bool, int, float)) else None


PULSAR_PIPELINE_FIELDS = frozenset('''mode trade_effect reddit_received x_received x_valid_samples
    x_duplicates_merged x_invalid_samples symbol reason x_invalid_count research_slots reddit x shared_limit
    observed_at x_profile_validated x_new_selected reddit_selected x_profile_rejected researched x_researched
    research_status research_at research_errors detail x_candidate_research_queued x_candidate_research_error'''.split())
SOURCE_COORDINATION_FIELDS = frozenset('''status roles X Reddit FMP_profile financials news primary_document
    news_rows distinct_article_urls duplicate_news_rows shared_x_article_count cross_platform_attention
    primary_event_source_ids x_claim_confirmed independent_event_sources crisis_authorized trade_effect detail'''.split())
ATTENTION_COVERAGE_FIELDS = frozenset('''source method days censored_days unknown_days source_subset_days
    absent_from_observed_subset_days window_dates detail'''.split())
NEWS_RISK_FIELDS = frozenset('''status raw_crisis_score actionable_crisis_score buy_pause_supported
    verified_event origin_count categories category origins multiple_origins excluded_reasons
    observed_at assessed_at updated_at as_of detail SOCIAL_LINEAGE MISSING_ARTICLE_ORIGIN
    STALE_OR_UNDATED AGGREGATOR_WITHOUT_ORIGIN'''.split())


def source_pipeline_report(news, cache_rows, x_status, *, now, news_risk_evidence=None):
    """Project persisted evidence only. No source contact, content or count inference.

    A received aggregate row is not a Reddit post; a packet reference is not
    model comprehension. These stages intentionally have separate counters.
    """
    cache_rows = [r for r in cache_rows if isinstance(r, dict)]
    cache = {str(r.get('key')): r for r in cache_rows}
    cards = object_value(cache.get('top5', {}).get('payload'))
    cards = cards if isinstance(cards, list) else None
    coverage = dict_value(cache.get('coverage', {}).get('payload'))
    selection_row = cache.get('candidate_selection', {})
    selection = dict_value(selection_row.get('payload'))
    pipeline = bounded_metadata(dict_value(selection.get('source_pipeline')), PULSAR_PIPELINE_FIELDS)
    selection_saved = timestamp(selection_row.get('saved'))
    selection_expires = timestamp(selection_row.get('expires'))
    selection_state = ('NOT_OBSERVED' if not pipeline or selection_saved is None else
                       'CLOCK_MISMATCH' if selection_saved > now+5 else
                       'STALE' if selection_expires is not None and selection_expires < now else 'OBSERVED')
    card_sources = []
    sources_in_cards = Counter()
    provider_cards = {}
    source_ids = {}
    for card_index, card in enumerate(cards or []):
        if not isinstance(card, dict):
            continue
        card_sources.append({'symbol': str(card.get('symbol') or '')[:20],
            'source_coordination': bounded_metadata(dict_value(card.get('source_coordination')), SOURCE_COORDINATION_FIELDS),
            'attention_coverage': bounded_metadata(dict_value(card.get('attention_coverage')), ATTENTION_COVERAGE_FIELDS)})
        packet = dict_value(card.get('packet'))
        rows = packet.get('sources', card.get('sources', []))
        if not isinstance(rows, list):
            continue
        seen = set()
        for row in rows:
            if not isinstance(row, dict):
                continue
            provider = str(row.get('provider') or row.get('source') or 'UNKNOWN')[:80]
            identifier = str(row.get('id') or row.get('evidence_id') or '')
            if identifier:
                source_ids.setdefault(provider, set()).add(identifier)
            seen.add(provider)
        sources_in_cards.update(seen)
        for provider in seen:
            provider_cards.setdefault(provider, set()).add(card_index)
    result = []
    for name, raw in sorted(dict_value(news).items()):
        if not isinstance(raw, dict):
            continue
        checked = timestamp(raw.get('last_checked_at') or raw.get('time'))
        success = timestamp(raw.get('last_success_at') or (raw.get('time') if raw.get('ok') is True else None))
        age = now - checked if checked is not None else None
        state = ('UNKNOWN' if age is None or age < -5 else 'STALE' if age > 1800
                 else 'OK' if raw.get('ok') is True else 'ERROR')
        error = timestamp(raw.get('error_at'))
        canonical = str(raw.get('provider') or name)
        matched = {key for key in sources_in_cards if key.casefold() in {str(name).casefold(), canonical.casefold()}}
        result.append({'provider': name, 'platform': 'NEWS', 'state': state,
            'last_attempt': utc(checked) if checked is not None else None,
            'last_success': utc(success) if success is not None else None,
            'returned_items_last_request': number(raw.get('count')),
            'request_total': None, 'failure_kind': str(raw.get('failure_kind') or '')[:60],
            'error_scope': 'HISTORICAL' if error is not None and success is not None and success >= error else
                           'CURRENT' if state == 'ERROR' else 'UNVERIFIED' if error is not None else 'NONE',
            'error_at': utc(error) if error is not None else None,
            'next_retry_at': raw.get('next_retry_at') or dict_value(raw.get('local_pause')).get('next_retry_at'),
            'canonical_provider': canonical,
            'cards_with_source': len({i for key in matched for i in provider_cards[key]}) if cards is not None else None,
            'source_ids': sorted({identifier for key in matched for identifier in source_ids.get(key, set())})[:100]})
    for provider in ('apewisdom', 'tradestie'):
        raw = dict_value(cache.get('source_status:' + provider, {}).get('payload'))
        checked = timestamp(raw.get('last_attempt'))
        success = timestamp(raw.get('last_success'))
        pause = cache.get('backoff:' + provider, {})
        pause_until = number(pause.get('expires'))
        state = ('PAUSED' if pause_until is not None and pause_until > now else
                 'UNKNOWN' if checked is None else 'STALE' if now-checked > 3600 or now-checked < -5 else
                 'OK' if raw.get('ok') is True else 'ERROR')
        error = timestamp(raw.get('error_at'))
        feed_rows = [r for r in cache_rows if str(r.get('key', '')).startswith('social:' + provider)]
        measured = []
        for row in feed_rows:
            payload = object_value(row.get('payload'))
            if isinstance(payload, dict) and isinstance(payload.get('results'), list):
                measured.append(len(payload['results']))
            elif isinstance(payload, list):
                measured.append(len(payload))
        result.append({'provider': provider, 'platform': 'REDDIT_AGGREGATOR', 'state': state,
            'last_attempt': utc(checked) if checked is not None else None,
            'last_success': utc(success) if success is not None else None,
            'returned_items_last_request': None, 'request_total': None,
            'aggregate_rows_in_cache': sum(measured) if measured else None,
            'posts_received': None, 'authors_observed': None,
            'error_scope': 'HISTORICAL' if error is not None and success is not None and success >= error else
                           'CURRENT' if state in {'ERROR','PAUSED'} else 'UNVERIFIED' if error is not None else 'NONE',
            'error_at': utc(error) if error is not None else None,
            'failure_kind': str(raw.get('failure_kind') or '')[:60],
            'cards_with_source': len({i for k, ids in provider_cards.items() if k.lower() == provider for i in ids}) if cards is not None else None,
            'source_ids': sorted({v for k, ids in source_ids.items() if k.lower() == provider for v in ids})[:100]})
    x = x_receipts_only(dict_value(x_status))
    return {'schema_version': 2, 'as_of': utc(now), 'passive': True,
        'sources': result,
        'news_risk_evidence': bounded_metadata(dict_value(news_risk_evidence), NEWS_RISK_FIELDS),
        'reddit': {'mode': 'AGGREGATORS_ONLY', 'direct_api_observed': False,
                   'platform_count': 1, 'symbols_in_last_coverage': coverage.get('symbols'),
                   'feeds_in_last_coverage': coverage.get('feeds', []),
                   'coverage_errors': coverage.get('errors', []), 'complete_platform_coverage': False},
        'x': x if x else {'source': 'X', 'state': 'NOT_OBSERVED', 'counts': {},
                         'detail': 'Noch kein gespeicherter X-Sammlerbeleg vorhanden.'},
        'pulsar_pipeline': {'selection_saved': selection_row.get('saved'),
            'selection_expires': selection_row.get('expires'), 'selection_state': selection_state,
            'source_pipeline': pipeline, 'card_sources': card_sources[:100],
            'detail': 'Persistierte Auswahl- und Kartenbelege; keine Schlussfolgerung aus API-Erfolg allein. '
                      'Eine Kandidatenaufnahme beweist keine Handelsfreigabe oder unabhaengige Ereignisbestaetigung.'},
        'card_cache_saved': cache.get('top5', {}).get('saved'),
        'cards_observed': len(cards) if cards is not None else None,
        'meaning': 'Nur gespeicherte Belege: Anbieterantwort, Aufbereitung, Karten-/Paketreferenz und GPT-Ausfuehrung sind verschiedene Stufen. '
                   'Fehlende Zaehler bleiben unbekannt. ApeWisdom und Tradestie sind ueberlappende Reddit-Aggregatoren, keine zwei Plattformen. '
                   'Eine Quellenreferenz beweist keine kausale Handelswirkung; X bleibt Recherchehinweis ohne Order- oder Sperrgewalt.'}


def in_window(row, since, until):
    for field in TIME_COLUMNS+('zeit',):
        if row.get(field) is not None:
            value = timestamp(row[field])
            if value is not None:
                return since <= value <= until
    return False


def in_interval(stamp, since, until, include_end=False):
    """Adjacent collection phases never overlap; no five-second grace period."""
    return stamp is not None and since <= stamp and (stamp <= until if include_end else stamp < until)


def count_evidence(observed, metadata, *, id_gaps=0):
    """Counts describe exported evidence, not unobserved activity after a snapshot."""
    available = metadata.get('status') == 'OK'
    limited = bool(metadata.get('truncated') or metadata.get('omitted_cell_count') or
                   metadata.get('invalid_json_lines') or id_gaps)
    return {'count': observed if available else None, 'observed_lower_bound': observed,
            'quality': 'EXACT_EXPORTED_SCOPE' if available and not limited else 'LOWER_BOUND' if observed or available else 'UNKNOWN',
            'export_status': metadata.get('status','UNKNOWN'), 'records_without_id_or_time': id_gaps,
            'meaning': 'Eindeutige exportierte Belege. EXACT_EXPORTED_SCOPE gilt bis zum jeweiligen Quellen-Snapshot; keine Garantie fuer danach entstandene Ereignisse.'}


def phase_intervals(meta, start, end):
    bounds = [timestamp(meta.get(k)) for k in ('started_utc','observation_start_utc','observation_end_utc','ended_utc')]
    if all(v is not None for v in bounds) and bounds == sorted(bounds):
        a,b,c,d = bounds
        return {'startup': (a,b,False), 'observation': (b,c,False), 'shutdown': (c,d,True), 'total': (a,d,True)}
    return {'observation': (start,end,True), 'total': (start,end,True)}


def phase_counts(ai_records, dbs, dbmeta, intervals, *, initial_dbs=None, audit_meta=None):
    """Attribute request starts and completions independently; deduplicate IDs."""
    ai_records = list({digest(dumps(row).encode()):row for row in ai_records}.values())
    result = {}
    for phase, (since,until,inclusive) in intervals.items():
        ai = ai_report(ai_records, since, until, include_end=inclusive)
        invalid = sum(1 for row in ai_records if not dict_value(row.get('execution')).get('local_request_id')
                      and in_interval(timestamp(row.get('zeit')), since, until, inclusive))
        counts = {key: count_evidence(ai[key], audit_meta or {}, id_gaps=invalid) for key in
                  ('local_dispatches_in_window','successful_results_in_window','cache_uses_in_window','failed_timed_out_discarded_in_window')}
        pending = 0
        for request in ai['requests']:
            execution = dict_value(request.get('execution'))
            finished = timestamp(execution.get('finished_at'))
            if request.get('started_in_window') and execution.get('request_dispatched') is True:
                if finished is not None:
                    pending += int(finished > until or finished == until and not inclusive)
                else:
                    pending += int(execution.get('phase') not in {'SUCCEEDED','CACHE_HIT','FAILED','TIMED_OUT','DISCARDED','NOT_STARTED'})
        counts['pending_started_requests'] = count_evidence(pending, audit_meta or {})
        counts['not_started'] = count_evidence(sum(1 for r in ai['requests'] if
            dict_value(r.get('execution')).get('phase') == 'NOT_STARTED' and
            in_interval(timestamp(dict_value(r.get('execution')).get('requested_at')), since, until, inclusive)), audit_meta or {})
        row = {'start_utc': utc(since), 'end_utc': utc(until), 'end_inclusive': inclusive, 'gpt': counts}
        for provider, db in (('fmp','fmp_service.sqlite'),('massive','massive_service.sqlite')):
            records = table_rows(initial_dbs or {}, db, 'calls') + table_rows(dbs, db, 'calls')
            unique, unidentified = {}, {}
            for event in records:
                at = timestamp(event.get('at'))
                if at is None:
                    unidentified[digest(dumps(event).encode())] = event
                    continue
                if not in_interval(at, since, until, inclusive):
                    continue
                if event.get('id') is None:
                    unidentified[digest(dumps(event).encode())] = event
                else:
                    # A reset database can reuse an ID; keep the different start
                    # as separate evidence and expose the ambiguity.
                    unique[(str(event['id']), at)] = event
            selected = list(unique.values())
            reused_ids = len(selected)-len({str(v['id']) for v in selected})
            table_meta = dbmeta.get(db,{}).get('tables',{}).get('calls',{})
            count = count_evidence(len(selected), table_meta, id_gaps=len(unidentified)+reused_ids)
            count.update(ids=[v.get('id') for v in selected],
                         observed_capabilities=dict(Counter(str(v.get('capability','UNKNOWN')) for v in selected)),
                         observed_outcomes=dict(Counter(str(v.get('result','DONE' if v.get('done') else 'PENDING')) for v in selected)),
                         source_snapshot_finished_at=dbmeta.get(db,{}).get('snapshot_finished_at'),
                         count_kind='CALL_JOURNAL_ENTRY' if provider == 'fmp' else 'RESERVATION_NOT_NECESSARILY_SENT')
            row[provider] = count
        result[phase] = row
    return result


def ai_report(records, since, until, *, include_end=True):
    groups, legacy = {}, 0
    unique = {digest(dumps(row).encode()): row for row in records}
    for row in sorted(unique.values(), key=lambda r: timestamp(r.get('zeit')) or 0):
        execution = dict_value(row.get('execution'))
        identity = execution.get('local_request_id')
        if not identity:
            legacy += 1
            continue
        item = groups.setdefault(identity, {'local_request_id': identity, 'phases': [], 'audit_events': []})
        item['audit_events'].append(row)
        if row.get('usage_only'):
            item['late_usage'] = row
            continue
        item.update(task=row.get('aufgabe'), model=row.get('modell'), symbols=row.get('instrumente'),
                    execution=execution, reason=row.get('grund'), ok=row.get('ok'))
        phase = execution.get('phase') or 'UNKNOWN'
        if phase not in item['phases']:
            item['phases'].append(phase)
    started = completed = cached = failed = 0
    for item in groups.values():
        ex = item.get('execution', {})
        start = timestamp(ex.get('started_at'))
        finish = timestamp(ex.get('finished_at'))
        item['started_in_window'] = in_interval(start, since, until, include_end)
        item['finished_in_window'] = in_interval(finish, since, until, include_end)
        started += int(item['started_in_window'] and ex.get('request_dispatched') is True)
        completed += int(item['finished_in_window'] and ex.get('phase') == 'SUCCEEDED')
        cached += int(item['finished_in_window'] and ex.get('phase') == 'CACHE_HIT')
        failed += int(item['finished_in_window'] and ex.get('phase') in {'FAILED','TIMED_OUT','DISCARDED'})
    return {'local_dispatches_in_window': started, 'successful_results_in_window': completed,
            'cache_uses_in_window': cached, 'failed_timed_out_discarded_in_window': failed,
            'legacy_rows_without_execution_receipt': legacy, 'requests': list(groups.values()),
            'evidence_status': 'OBSERVED' if groups else 'UNKNOWN',
            'interpretation': 'Lokaler Versand ist kein Anbieter-Empfangsbeleg. Erfolg beweist noch keine fachliche Nutzung. '
                              'Keine neuen Belege bedeutet nicht automatisch, dass GPT defekt ist.'}


def news_fields(data):
    rows = data if isinstance(data, list) else [data]
    groups = {'body': ('text','description','summary'), 'url': ('url','article_url','link'),
              'published_at': ('publishedDate','published_utc','published_at','date')}
    return {name: any(isinstance(row, dict) and any(row.get(k) for k in keys) for row in rows)
            for name, keys in groups.items()}


def source_refs(value):
    found = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if key == 'source_ids' and isinstance(item, list):
                found.update(str(v) for v in item)
            else:
                found.update(source_refs(item))
    elif isinstance(value, list):
        for item in value:
            found.update(source_refs(item))
    return found


def pulsar_sample_evidence(samples, read_object, run_id):
    """Use only referenced, hash-checked objects from this collection's samples."""
    evidence, seen = [], set()
    for sample in reversed(samples):
        ref = dict_value(sample.get('objects')).get('pulsar_cache_top5', {})
        path = ref.get('file')
        if not isinstance(path,str) or not path.startswith('verlauf/objekte/') or '..' in Path(path).parts or path in seen:
            continue
        seen.add(path)
        try:
            row = read_object(path)
            hashed = digest(dumps(row).encode())
            if hashed != ref.get('sha256_sanitized') or not isinstance(row,dict) or row.get('key') != 'top5':
                continue
            evidence.append({'row': row, 'source': path, 'observed_at': sample.get('at'),
                             'run_id': run_id, 'sha256_sanitized': hashed})
        except (OSError, ValueError, KeyError, RecursionError):
            continue
    return evidence


def pulsar_report(rows, ai, now, fallback=None, *, run_id=None, run_started=None):
    cache = {r.get('key'): r for r in rows}
    top = cache.get('top5', {})
    cards = object_value(top.get('payload'))
    status = 'EXPORTED'
    if 'top5' not in cache:
        status = 'NOT_EXPORTED'
    elif not isinstance(cards, list):
        status = 'OMITTED_OVERSIZE_CELL' if top.get('payload') == OVERSIZE else 'UNREADABLE_PAYLOAD'
    elif not all(isinstance(c,dict) for c in cards):
        status = 'INVALID_CARD_ROWS'
    exported_status = status
    provenance = {'source': 'database_export', 'cache_saved': top.get('saved'), 'cache_expires': top.get('expires')}
    compact = cache.get('diagnostic_top5', {})
    compact_payload = object_value(compact.get('payload'))
    compact_cards = compact_payload.get('cards') if isinstance(compact_payload, dict) else None
    compact_source_saved = timestamp(compact_payload.get('top5_saved')) if isinstance(compact_payload, dict) else None
    compact_saved = timestamp(compact.get('saved'))
    top_saved = timestamp(top.get('saved'))
    if (status != 'EXPORTED' and isinstance(compact_cards, list)
            and all(isinstance(c,dict) for c in compact_cards)
            and compact_saved is not None and compact_source_saved is not None
            and abs(compact_saved-compact_source_saved) <= 0.001
            and (top_saved is None or abs(top_saved-compact_source_saved) <= 0.001)):
        cards = compact_cards
        status = 'EXPORTED_COMPACT_PROJECTION'
        provenance = {'source': 'diagnostic_top5', 'cache_saved': compact.get('saved'),
                      'cache_expires': compact.get('expires'),
                      'matches_exported_cache_revision': top_saved is None or abs(top_saved-compact_source_saved) <= 0.001}
    alternatives = []
    if status not in {'EXPORTED','EXPORTED_COMPACT_PROJECTION'}:
        cards = None
        for item in sorted(fallback or [], key=lambda v: timestamp(v.get('observed_at')) or 0, reverse=True):
            candidate = dict_value(item.get('row'))
            recovered = object_value(candidate.get('payload'))
            observed = timestamp(item.get('observed_at'))
            saved = timestamp(candidate.get('saved'))
            if (run_id is None or item.get('run_id') != run_id or observed is None or observed > now+5 or
                    run_started is not None and observed < run_started or
                    saved is None or saved > observed+5 or not isinstance(recovered,list) or
                    not all(isinstance(c,dict) for c in recovered) or candidate.get('key') != 'top5' or
                    digest(dumps(candidate).encode()) != item.get('sha256_sanitized')):
                continue
            # saved/expires identify a cache revision. A later direct reread or
            # an older sample must never impersonate the omitted end revision.
            same_revision = (timestamp(top.get('saved')) == saved and top.get('expires') == candidate.get('expires'))
            provenance_candidate = {k:v for k,v in item.items() if k != 'row'}
            provenance_candidate.update(cache_saved=candidate.get('saved'),cache_expires=candidate.get('expires'),
                                        card_count=len(recovered),matches_exported_cache_revision=same_revision)
            if same_revision:
                cards, top = recovered, candidate
                status, provenance = 'RECOVERED_FROM_SAME_RUN_SAMPLE', provenance_candidate
                break
            alternatives.append(provenance_candidate)
    unknown_cards = cards is None
    cards = cards if isinstance(cards,list) else []
    audit = {r['local_request_id']: r for r in ai['requests']}
    reports = []
    for card in cards:
        if not isinstance(card, dict):
            continue
        packet = dict_value(card.get('packet'))
        original_sources = card.get('sources') if isinstance(card.get('sources'), list) else []
        packet_sources = packet.get('sources') if isinstance(packet.get('sources'), list) else []
        originals = {r.get('id'): r for r in original_sources if isinstance(r, dict) and isinstance(r.get('id'), str) and r.get('id')}
        packets = {r.get('id'): r for r in packet_sources if isinstance(r, dict) and isinstance(r.get('id'), str) and r.get('id')}
        source_rows = []
        for source_id, original in originals.items():
            projected = packets.get(source_id)
            entry = {'source_id': source_id, 'provider': original.get('provider'), 'kind': original.get('kind'),
                     'observed_at': original.get('observed_at'), 'in_analysis_packet': projected is not None}
            if original.get('kind') == 'news' and projected is not None:
                before = (dict_value(original.get('diagnostic_fields')) or news_fields(original.get('data')))
                after = (dict_value(projected.get('diagnostic_fields')) or news_fields(projected.get('data')))
                entry.update(original_fields=before, packet_fields=after,
                             fields_lost=[k for k in before if before.get(k) and not after.get(k)])
            source_rows.append(entry)
        stages = []
        for stage, tier in (('precheck','LUNA'), ('analysis','TERRA'), ('countercheck',None)):
            review = dict_value(card.get(stage))
            execution = dict_value(review.get('execution'))
            original_ex = dict_value(review.get('source_execution'))
            identity = execution.get('local_request_id') or original_ex.get('local_request_id')
            answer = dict_value(review.get('daten'))
            thesis = dict_value(answer.get('thesis')).get('text')
            refs = source_refs(answer)
            applied = bool(tier and review.get('ok') is True and thesis and card.get('text_source') == tier
                           and card.get('thesis') == thesis)
            stages.append({'stage': stage, 'ok': review.get('ok'), 'phase': execution.get('phase') or 'UNKNOWN',
                'local_request_id': identity or None, 'audit_match': identity in audit if identity else None,
                'request_dispatched': execution.get('request_dispatched'),
                'response_received': execution.get('response_received'), 'model': review.get('modell'),
                'cache_used': execution.get('cache_used'), 'reason': review.get('grund'),
                'input_hash': review.get('input_hash'), 'input_sources': review.get('input_sources'),
                'source_execution': original_ex,
                'referenced_source_ids': sorted(refs), 'source_ids_missing_from_packet': sorted(refs-set(packets)),
                'text_application': 'EXACT_TEXT_MATCH' if applied else 'NOT_PROVEN' if tier else 'SEPARATE_COUNTERCHECK',
                'explanation': 'Identische These plus passender Textursprung belegt Textuebernahme; keine Aussage ueber Rendite oder Kaufursache.'})
        reports.append({'symbol': card.get('symbol'), 'assessment_id': card.get('assessment_id') or card.get('id'),
            'state': card.get('state'), 'score': card.get('score'), 'eligible': card.get('eligible'),
            'source_structure_valid': isinstance(card.get('sources'),list) and isinstance(packet.get('sources'),list),
            'blocks': card.get('blocks'), 'missing': card.get('missing'), 'errors': card.get('errors'),
            'analysis_status': card.get('analysis_status'), 'text_source': card.get('text_source'),
            'sources': source_rows, 'stages': stages, 'claims': card.get('claims'),
            'source_coordination': bounded_metadata(dict_value(card.get('source_coordination')), SOURCE_COORDINATION_FIELDS),
            'attention_coverage': bounded_metadata(dict_value(card.get('attention_coverage')), ATTENTION_COVERAGE_FIELDS)})
    try:
        local = datetime.fromtimestamp(now, ZoneInfo('America/New_York'))
        schedule = {'new_york_time': local.isoformat(), 'weekend': local.weekday() >= 5,
                    'discovery_interval_seconds': 43200 if local.weekday() >= 5 else 900,
                    'in_discovery_window': local.weekday() >= 5 or 7 <= local.hour < 19,
                    'kind': 'NEXUS_10_STATIC_SCHEDULE',
                    'note': ('Wochenende: Kandidatensuche alle 12 Stunden.' if local.weekday() >= 5 else
                             'Werktag: Kandidatensuche alle 15 Minuten zwischen 07 und 19 Uhr New York.') +
                            ' Wiederaufnahme einer offenen GPT-Vorpruefung separat. Kein Lauf wird erzwungen.'}
    except Exception:
        schedule = {'kind': 'UNKNOWN', 'note': 'Zeitzonendaten fehlen'}
    saved = timestamp(top.get('saved'))
    return {'cards': reports, 'card_count': None if unknown_cards else len(reports),
            'cards_status': status, 'exported_cards_status': exported_status,
            'cards_source': provenance.get('source'), 'cards_provenance': provenance,
            'other_observed_revisions': alternatives, 'top5_saved': saved,
            'top5_age_seconds': now-saved if saved is not None else None,
            'candidate_selection': cache.get('candidate_selection'),
            'optional_source_cache': {key:cache[key] for key in ('source_status:tradestie','backoff:tradestie') if key in cache},
            'schedule': schedule, 'status_cache': cache.get('status'),
            'interpretation': 'Quellenabruf, Aufnahme ins Analysepaket, GPT-Beleg und Textuebernahme werden getrennt ausgewiesen.'}


def decisions_report(rows, since, until):
    recent = [dict(row) for row in rows if in_window(row, since, until)]
    checks = []
    for row in recent:
        payload = dict_value(row.get('payload_json'))
        execution = str(row.get('execution_status') or payload.get('execution_status') or '').upper()
        row['decision_status'] = row.get('status')
        if execution in {'FEHLGESCHLAGEN', 'FAILED', 'REJECTED', 'CANCELLED', 'CANCELED', 'ABGELEHNT', 'NICHT_AUSGEFUEHRT', 'UNKNOWN_AFTER_SUBMIT', 'UNCLEAR'}:
            row['status'] = execution
            receipt = dict_value(row.get('execution_result_json'))
            row['reason'] = receipt.get('reason') or ('Broker-Ausführung: ' + execution + '; Detailbeleg nicht exportiert')
            row['blocked_by'] = payload.get('blocked_by') or row.get('blocked_by')

    for row in recent:
        payload = dict_value(row.get('payload_json'))
        snap = dict_value(payload.get('decision_snapshot'))
        technical = dict_value(snap.get('technical'))
        news_ai = dict_value(snap.get('news_ai'))
        ai = dict_value(news_ai.get('ai') or payload.get('ai'))
        checks.append({'id': row.get('id'), 'symbol': row.get('symbol'), 'broker': row.get('broker'),
            'time': row.get('created_at_utc'), 'status': row.get('status'), 'decision_status': row.get('decision_status'), 'blocked_by': row.get('blocked_by'),
            'reason': row.get('reason'), 'strategy_mode': row.get('strategy_mode'),
            'structured_snapshot': bool(snap), 'technical': technical,
            'gates': snap.get('gates'), 'news_sources': news_ai.get('sources'),
            'ai_result_recorded': bool(ai), 'ai_execution': ai.get('execution'),
            'execution_status': row.get('execution_status')})
    return {'rows_observed_in_window': len(recent), 'status_counts': dict(Counter(str(r.get('status')) for r in recent)),
            'broker_counts': dict(Counter(str(r.get('broker')) for r in recent)),
            'block_reasons': dict(Counter(str(r.get('blocked_by') or '') for r in recent)), 'checks': checks,
            'interpretation': 'Aus exportierten Zeilen, bei gekuerztem Export keine Gesamtzahl. Ablehnung an einem fruehen Gate kann spaetere Datenpruefungen korrekt auslassen.'}


def candle_report(rows, now):
    groups = {}
    for row in rows:
        groups.setdefault((row.get('domain'),row.get('instrument')), []).append(row)
    result = []
    for (domain, instrument), values in groups.items():
        stamps = sorted(n for r in values if (n:=timestamp(r.get('ts'))) is not None)
        invalid = 0
        for r in values:
            o,h,l,c,v = [number(r.get(k)) for k in ('open','high','low','close','volume')]
            if any(x is None for x in (o,h,l,c,v)) or min(o,h,l,c)<=0 or v<0 or h<max(o,l,c) or l>min(o,h,c):
                invalid += 1
        result.append({'domain': domain, 'instrument': instrument, 'exported_candles': len(values),
            'first_utc': utc(stamps[0]) if stamps else None, 'last_utc': utc(stamps[-1]) if stamps else None,
            'invalid_ohlcv_rows': invalid, 'duplicate_timestamps': len(stamps)-len(set(stamps)),
            'gaps_over_5m_in_export': sum(b-a>301 for a,b in zip(stamps,stamps[1:])),
            'future_timestamps': sum(x>now+5 for x in stamps),
            'note': 'Gespeicherte NEXUS-5m-Basis. Exportauswahl/Marktdatenluecken sind kein Beweis fuer Lookahead oder falsche Live-Signale.'})
    return result


def runtime_check(records, now):
    result = {}
    for broker, name in (('etoro','runtime_status.json'),('okx','runtime_status_okx.json')):
        entry = records.get(name, {})
        data = dict_value(entry.get('data'))
        stamp = timestamp(data.get('last_heartbeat') or data.get('zeit'))
        age = now-stamp if stamp is not None else None
        result[broker] = {'file_status': entry.get('status', 'MISSING'), 'heartbeat_age_seconds': age,
            'freshness': 'UNKNOWN' if age is None else 'CLOCK_MISMATCH' if age < -10 else 'FRESH' if age<=180 else 'STALE',
            'running': data.get('running'), 'worker_alive': data.get('worker_alive'),
            'state': data.get('state') or data.get('connection_state'),
            'account_fingerprint': data.get('account_fingerprint'), 'mode': data.get('modus') or data.get('mode'),
            'connection_components': data.get('connection_components'), 'functional_health': data.get('functional_health'),
            'buy_readiness': data.get('stock_trading_ready') or data.get('handelsbereitschaft') or data.get('trading_ready'),
            'risk_manager_buy_gate': data.get('risk_manager_buy_gate'),
            # 10.7.0: jede Sperre mit Grund, Reichweite, Ablauf und Aufloesung.
            'sperren': data.get('sperren'),
            'gesperrte_symbole': data.get('gesperrte_symbole') if broker == 'okx' else None,
            'balances': data.get('guthaben') if broker == 'okx' else None,
            'capital_currency': data.get('kapital_waehrung') if broker == 'okx' else None,
            'tradable_capital': data.get('handelbares_kapital') if broker == 'okx' else None,
            'capital_evidence': data.get('kapitalbeleg') if broker == 'okx' else None,
            'balance_snapshot_valid': data.get('balance_snapshot_valid') if broker == 'okx' else None,
            'balance_snapshot_detail': data.get('balance_snapshot_detail') if broker == 'okx' else None,
            'account_action': {k: dict_value(data.get('account_action')).get(k) for k in
                ('state','blocked','required','code','instrument','observed_at','account','environment','detail')},
            'exit_costs': data.get('etoro_exit_costs') if broker == 'etoro' else None,
            'open_operations': data.get('etoro_open_operations') if broker == 'etoro' else None,
            'note': 'Frischer Heartbeat beweist Prozessaktivitaet; Positionsschutz und Handelskorrektheit bleiben separate Belege.'}
    return result


def provider_history(statuses, settings, since, until):
    result = {'current': [], 'historical': [], 'unknown': [], 'active_pauses': []}
    for name, status in statuses.items():
        if not isinstance(status,dict):
            continue
        canonical = status.get('canonical_provider') or status.get('provider') or name
        stamp = timestamp(status.get('last_checked_at') or status.get('time'))
        enabled = status.get('enabled')
        if canonical == 'GDELT' and 'gdelt_enabled' in settings:
            enabled = settings['gdelt_enabled']
        elif name == 'finanzen.net' and settings.get('legacy_optional_sources_enabled') is False:
            enabled = False
        alias = status.get('historical_alias') is True
        retry = timestamp(status.get('next_retry_at'))
        paused = enabled is not False and not alias and retry is not None and retry > until
        if paused:
            result['active_pauses'].append({'source': name, 'canonical_provider': canonical,
                'next_retry_at': utc(retry), 'trigger_observed_at': utc(stamp) if stamp is not None else None,
                'classification': 'CURRENT_PAUSE', 'trigger_before_window': stamp is not None and stamp < since})
        scope = ('historical' if alias or enabled is False or stamp is not None and stamp < since else
                 'current' if in_interval(stamp,since,until,True) else 'unknown')
        result[scope].append({'source': name, 'canonical_provider': canonical, 'enabled': enabled,
            'observed_at': utc(stamp) if stamp is not None else None,
            'age_seconds': until-stamp if stamp is not None else None,
            'historical_alias': alias, 'reported_error': status.get('ok') is False,
            'next_retry_at': status.get('next_retry_at'),
            'currently_paused': paused,
            'classification': 'DISABLED_SOURCE' if enabled is False else 'HISTORICAL_ALIAS' if alias else scope.upper(),
            'evidence': status})
    return result


def fmp_input_usage(pulsar, ai):
    """Validated input receipts + execution audit; no claim of trade causality."""
    audited = {r['local_request_id']:r for r in ai.get('requests',[])}
    receipts, unproven = {}, []
    for card in pulsar.get('cards',[]):
        for stage in card.get('stages',[]):
            identity = stage.get('local_request_id')
            audit = dict_value(audited.get(identity))
            executed = dict_value(audit.get('execution'))
            input_hash = stage.get('input_hash')
            for symbol, input_entry in dict_value(stage.get('input_sources')).items():
                if not isinstance(input_entry,dict):
                    continue
                for source in input_entry.get('sources') or []:
                    if not isinstance(source,dict) or str(source.get('provider')).upper() != 'FMP' or source.get('included') is not True:
                        continue
                    proven = (input_entry.get('status') == 'INPUT_OF_VALIDATED_RESPONSE' and
                              stage.get('ok') is True and stage.get('audit_match') is True and
                              executed.get('phase') == 'SUCCEEDED' and executed.get('response_received') is True and
                              bool(re.fullmatch(r'[0-9a-f]{64}',str(input_hash or ''))))
                    row = {**source, 'symbol': symbol, 'stage': stage.get('stage'), 'local_request_id': identity,
                           'input_hash': input_hash, 'input_receipt_status': input_entry.get('status'),
                           'audit_match': stage.get('audit_match'), 'execution_phase': executed.get('phase'),
                           'started_at': executed.get('started_at'),
                           'evidence_level': 'VALIDATED_INPUT_RECEIPT_AND_AUDIT' if proven else 'INPUT_RECEIPT_WITHOUT_COMPLETE_EXECUTION_PROOF'}
                    if proven:
                        receipts[(identity,symbol,source.get('source_id'),stage.get('stage'))] = row
                    else:
                        unproven.append(row)
    values = list(receipts.values())
    return {'status': 'FMP_INPUT_USE_PROVEN' if values else 'NOT_PROVEN',
            'requests_with_proven_fmp_input': len({r['local_request_id'] for r in values}),
            'symbols': sorted({r['symbol'] for r in values}), 'input_receipts': values,
            'receipts_without_complete_execution_proof': unproven,
            'meaning': 'FMP-Fakten sind laut gespeichertem Eingabebeleg Bestandteil einer validierten GPT-Antwort mit passendem Ausfuehrungsaudit. Der Diagnosecode rekonstruiert den Eingabehash nicht erneut. Uebergabe und Textuebernahme beweisen keine kausale Handelswirkung; leere evidence_uses widerlegen diese Nutzung nicht.'}


def historical_cost_gaps(rows, metadata):
    groups = {}
    confirmed = {'CONFIRMED','BROKER_CONFIRMED','USER_CONFIRMED','CASH_DELTA_CONFIRMED'}
    for row in rows:
        if not row.get('ausgestiegen_am'):
            continue
        broker = str(row.get('broker') or 'UNKNOWN')
        group = groups.setdefault(broker, {'closed_rows_observed': 0, 'net_result_missing': [],
                                          'fee_evidence_incomplete': [], 'known_net_result_rows': 0,
                                          'expected_fee_rows': []})
        group['closed_rows_observed'] += 1
        item = {k:row.get(k) for k in ('trade_id','symbol','ausgestiegen_am','broker_position_id',
            'ownership_status','reconciliation_status','entry_fee_quality','exit_fee_quality','fee_quality','netto_pnl')}
        if number(row.get('netto_pnl')) is None:
            group['net_result_missing'].append(item)
        else:
            group['known_net_result_rows'] += 1
        # 10.7.0: Ein Erwartungswert (EXPECTED_UNVERIFIED) ist beziffert, aber
        # kein Beleg. Er wird getrennt gezaehlt -- weder als vollstaendig noch
        # als Luecke verschwiegen.
        if str(row.get('fee_quality')) == 'EXPECTED_UNVERIFIED':
            group['expected_fee_rows'].append(item)
        if str(row.get('fee_quality')) not in confirmed or str(row.get('entry_fee_quality')) not in confirmed or str(row.get('exit_fee_quality')) not in confirmed:
            group['fee_evidence_incomplete'].append(item)
    for group in groups.values():
        group['net_result_missing_count'] = count_evidence(len(group['net_result_missing']), metadata)
        group['fee_evidence_incomplete_count'] = count_evidence(len(group['fee_evidence_incomplete']), metadata)
        group['expected_fee_rows_count'] = count_evidence(len(group['expected_fee_rows']), metadata)
        group['complete_performance_proven'] = bool(metadata.get('status') == 'OK' and not metadata.get('truncated') and
                                                  not group['net_result_missing'] and not group['fee_evidence_incomplete'])
    return {'brokers': groups, 'export_status': metadata.get('status','UNKNOWN'),
            'scope': 'HISTORICAL_CLOSED_TRADES',
            'meaning': 'Historische Kosten-/Nettoluecken sind keine offenen Positionen. Fehlende Gebuehren oder Nettoergebnisse bleiben unbekannt; es wird weder mit null aufgefuellt noch eine Gesamtperformance aus unvollstaendigen Belegen berechnet.'}


def etoro_settlement_report(dbs, metadata):
    """Report evidence sources separately; a cash sample is not a fee receipt."""
    db = 'decision_history.sqlite'
    names = ('etoro_settlement_reviews','etoro_cash_receipts',
             'etoro_proceeds_observations','etoro_cancellations','execution_orders')
    rows = {name:table_rows(dbs,db,name) for name in names}
    table_meta = metadata.get(db,{}).get('tables',{})
    reviews = []
    for row in rows['etoro_settlement_reviews'][:100]:
        p = dict_value(row.get('proof_json'))
        # Nur ein Automatik-Flag, nie der Klarname des Bestaetigers (Privatsphaere).
        automatic = str(row.get('actor') or '').startswith('nexus:')
        reviews.append(dict(trade_id=row.get('trade_id'),reviewed_at=row.get('reviewed_at'),
            source=('BROKER_CASH_DELTA_AUTOMATIC' if automatic else 'USER_VERIFIED_CASH_DELTA'),
            quality=('CASH_DELTA_CONFIRMED' if automatic else 'USER_CONFIRMED'),automatic=automatic,
            account=p.get('account'),paper=p.get('paper'),position_id=p.get('position_id'),
            entry_cost=p.get('entry_cost'),exit_cost=p.get('proposed_exit_cost'),net=p.get('proposed_net')))
    scopes = {}
    for row in rows['etoro_cash_receipts']:
        key=(row.get('account'),row.get('paper'))
        item=scopes.setdefault(key,dict(account=key[0],paper=key[1],samples_observed=0,latest_at=None))
        item['samples_observed']+=1
        if str(row.get('observed_at') or '') > str(item['latest_at'] or ''):
            item['latest_at']=row.get('observed_at')
    cancels=[{k:r.get(k) for k in ('account','paper','order_id','state','label','created_at','checked_at')}
             for r in rows['etoro_cancellations'][:100]]
    old=[{k:r.get(k) for k in ('account','environment','order_id','position_id','state','filled')}
         for r in rows['execution_orders'] if r.get('broker')=='etoro' and r.get('state')=='POSITION_CLOSED_ORDER_UNPROVEN']
    proceeds=[{k:r.get(k) for k in ('account','paper','order_id','observed_at')}
              for r in rows['etoro_proceeds_observations'][:100]]
    return dict(reviews=reviews,cash_scopes=list(scopes.values()),cancellations=cancels,
        closed_position_old_orders=old[:100],unclassified_proceeds=proceeds,
        counts={k:count_evidence(len(v),table_meta.get(k,{})) for k,v in rows.items()},
        meaning='Bestaetigte Barbestandsableitungen sind Nutzerbelege, keine nativen Broker-Gebuehrenbelege. '
            'Barbestand und unklassifiziertes proceeds loesen allein keine Kaufsperre. '
            'Ein alter Auftrag mit geschlossenem Positionsnachweis bleibt nachvollziehbar, ist aber kein aktiver Mengenauftrag. '
            'Eine angenommene Stornoanfrage ist noch keine bestaetigte Stornierung.')


def broker_history_results(rows, metadata):
    """Broker-reported profit and locally reconciled profit are different receipts."""
    fields = frozenset('''reason computed_gross_pnl confirmed_entry_costs computed_net_pnl
        arithmetic_delta broker_vs_computed_net_delta fee_scope_confirmed risk_release
        status quality source receipt_hash currency position_id instrument_id order_id
        broker_reported_net_pnl broker_reported_fees missing_evidence lookup_route_missing
        native_close_candidates'''.split())
    results = []
    for row in rows:
        if row.get('broker') != 'etoro' or not row.get('broker_result_quality'):
            continue
        result = {k: row.get(k) for k in ('trade_id', 'symbol', 'broker_position_id',
            'account_fingerprint', 'environment', 'ausgestiegen_am', 'entry_fee_quality',
            'exit_fee_quality', 'fee_quality', 'netto_pnl', 'broker_reported_net_pnl', 'broker_reported_fees',
            'broker_result_currency', 'broker_result_quality', 'broker_result_receipt_hash')}
        result['assessment'] = bounded_metadata(dict_value(row.get('broker_result_detail_json')), fields)
        results.append(result)
    return {'status': 'OBSERVED' if results else 'NOT_OBSERVED',
        'export_status': metadata.get('status', 'UNKNOWN'), 'rows': results[:100],
        'rows_observed_count': count_evidence(len(results), metadata), 'row_limit': 100,
        'rows_omitted': max(0, len(results)-100),
        'meaning': 'Broker-NetProfit und lokale Nettobuchung werden getrennt gezeigt. '
            'Ein History-Gebuehrenwert von null beweist keinen vollstaendigen Kostenumfang. '
            'Ein Kostenkonflikt oder fehlender Abschlussbeleg wird durch die Diagnose nicht aufgeloest.'}


def private_stream_report(rows, metadata):
    """Count persisted delivery/processing metadata, separated by account scope."""
    groups = {}
    for row in rows:
        key = (str(row.get('broker') or 'UNKNOWN'), str(row.get('account_fingerprint') or ''),
               str(row.get('environment') or 'UNKNOWN'))
        group = groups.setdefault(key, {'broker': key[0], 'account_fingerprint': key[1],
            'environment': key[2], 'events_observed': 0, 'deliveries_observed': 0,
            'states': Counter(), 'validations': Counter(), 'last_received_at_utc': None,
            'last_processed_at_utc': None})
        group['events_observed'] += 1
        group['deliveries_observed'] += max(0, int(number(row.get('deliveries')) or 0))
        group['states'][str(row.get('state') or 'UNKNOWN')] += 1
        group['validations'][str(row.get('validation') or 'UNKNOWN')] += 1
        for name in ('received_at_utc', 'processed_at_utc'):
            stamp = timestamp(row.get(name))
            existing = timestamp(group['last_'+name])
            if stamp is not None and (existing is None or stamp > existing):
                group['last_'+name] = utc(stamp)
    for group in groups.values():
        group['states'] = dict(group['states'])
        group['validations'] = dict(group['validations'])
        group['count_quality'] = count_evidence(group['events_observed'], metadata)['quality']
    return {'status': metadata.get('status', 'MISSING'), 'passive': True,
        'events_count': count_evidence(len(rows), metadata), 'scopes': list(groups.values())[:64],
        'scopes_omitted': max(0, len(groups)-64), 'raw_payloads_exported': False,
        'meaning': 'Dauerhaft gespeicherte Zustellungen und Zuordnungen pro Konto/Umgebung. '
            'MATCHED belegt lokale Zuordnung, keine bestaetigte Ausfuehrung oder Gebuehrenbuchung. '
            'Leere oder begrenzte Exporte beweisen keinen vollstaendigen privaten Ereignisstrom.'}


def final_analysis(initial, final, dbs, dbmeta, audits, samples, start, end, *,
                   collection_meta=None, initial_dbs=None, pulsar_fallback=None, audit_meta=None):
    # Old ZIPs did not report individual oversized cells in table metadata.
    # Preserve the original status and add the loss detected in actual rows.
    dbmeta = json.loads(dumps(dbmeta))
    for database,tables in dbs.items():
        for table,rows in tables.items():
            losses = [{'column':key,'row_identity':{k:row[k] for k in ('id','key','saved','expires') if k in row},
                       'reason':'OVERSIZE_CELL_OMITTED'} for row in rows for key,value in row.items() if value == OVERSIZE]
            detail = dbmeta.get(database,{}).get('tables',{}).get(table)
            if losses and detail is not None and not detail.get('omitted_cell_count'):
                detail.update(omitted_cell_count=len(losses),omitted_cells=losses,cell_completeness='PARTIAL',
                              cell_loss_detection='PLACEHOLDER_IN_EXPORTED_ROWS')
    ai = ai_report(audits, start, end)
    collection_meta = collection_meta or {}
    intervals = phase_intervals(collection_meta,start,end)
    evaluated_at = intervals['total'][1]
    phases = phase_counts(audits,dbs,dbmeta,intervals,initial_dbs=initial_dbs,audit_meta=audit_meta)
    for metric in ('local_dispatches_in_window','successful_results_in_window','cache_uses_in_window','failed_timed_out_discarded_in_window'):
        ai[metric+'_observed_lower_bound'] = ai[metric]
        ai[metric] = phases['observation']['gpt'][metric]['count']
    research = dbs.get('pulsar_research.sqlite', {})
    pulsar = pulsar_report(research.get('cache', []), ai, intervals['total'][1],
                           fallback=pulsar_fallback,run_id=collection_meta.get('run_id'),run_started=intervals['total'][0])
    pulsar['measurement'] = pulsar_measurement_report(research.get('attention_outcomes', []),
        dbmeta.get('pulsar_research.sqlite',{}).get('tables',{}).get('attention_outcomes'))
    decisions = decisions_report(table_rows(dbs,'decision_history.sqlite','decisions'), start, end)
    decision_meta = dbmeta.get('decision_history.sqlite',{}).get('tables',{}).get('decisions',{})
    decisions['export_status'] = decision_meta.get('status','UNKNOWN')
    decisions['export_truncated'] = decision_meta.get('truncated')
    decisions['rows_exported_in_window'] = decisions['rows_observed_in_window']
    if decision_meta.get('status')!='OK':
        # No usable export does not mean the bot made zero decisions.
        decisions['rows_observed_in_window'] = None
        decisions['count_meaning'] = 'UNBEKANNT: Datenbankexport unvollstaendig; exportierte Belege sind nur eine Untergrenze.'
    else:
        decisions['count_meaning'] = 'Anzahl im begrenzten Export; bei Kuerzung keine vollstaendige Laufzeitstatistik.'
    fmp = dbs.get('fmp_service.sqlite', {})
    uses = [r for r in fmp.get('evidence_uses', []) if in_window(r,start,end)]
    calls = [r for r in fmp.get('calls', []) if in_window(r,start,end)]
    fmp_calls_meta = dbmeta.get('fmp_service.sqlite', {}).get('tables', {}).get('calls', {})
    fmp_uses_meta = dbmeta.get('fmp_service.sqlite', {}).get('tables', {}).get('evidence_uses', {})
    massive_calls_meta = dbmeta.get('massive_service.sqlite', {}).get('tables', {}).get('calls', {})
    massive_calls = [r for r in table_rows(dbs, 'massive_service.sqlite', 'calls') if in_window(r,start,end)]
    massive_meta = {r.get('key'): object_value(r.get('value')) for r in table_rows(dbs, 'massive_service.sqlite', 'meta')}
    statuses = dict_value(final.get('news_source_status.json', {}).get('data'))
    source_history = provider_history(statuses,dict_value(final.get('news_research_settings.json',{}).get('data')),
                                      intervals['total'][0],intervals['total'][1])
    data_changes = []
    for name in SAMPLE_FILES:
        before = initial.get(name, {}).get('data')
        after = final.get(name, {}).get('data')
        data_changes.append({'file': name, 'before_available': before is not None,
                             'after_available': after is not None,
                             'content_changed': before != after if before is not None and after is not None else None})
    return {'window': {'start_utc': utc(start), 'end_utc': utc(end), 'seconds': end-start},
        'collection_phases': phases,
        'phase_meaning': 'Startsammlung [Start, Messbeginn), Messung [Messbeginn, Messende), Endsammlung [Messende, Ende]. GPT-Starts und Abschluesse werden jeweils nach ihrem eigenen Zeitpunkt gezaehlt; Gesamtsummen sind ID-dedupliziert. Abruf fehlend/nicht faellig ist ohne Schedulerbeleg nicht bestimmbar.',
        'runtime': runtime_check(final, evaluated_at), 'data_changes': data_changes,
        'gpt': ai, 'pulsar': pulsar, 'decisions': decisions,
        'candles': candle_report(table_rows(dbs,'market_candles.sqlite','candles'), evaluated_at),
        'historical_cost_gaps': historical_cost_gaps(table_rows(dbs,'decision_history.sqlite','trades'),
            dbmeta.get('decision_history.sqlite',{}).get('tables',{}).get('trades',{})),
        'broker_history_results': broker_history_results(table_rows(dbs,'decision_history.sqlite','trades'),
            dbmeta.get('decision_history.sqlite',{}).get('tables',{}).get('trades',{})),
        'etoro_settlement': etoro_settlement_report(dbs,dbmeta),
        'etoro_private_stream': private_stream_report(table_rows(dbs,'etoro_stream_inbox.sqlite','etoro_stream_events'),
            dbmeta.get('etoro_stream_inbox.sqlite',{}).get('tables',{}).get('etoro_stream_events',{})),
        'providers': {'news_status': statuses, 'source_history': source_history,
                      'pipeline': source_pipeline_report(statuses, research.get('cache', []),
                          dict_value(final.get('market_intelligence_status.json', {}).get('data')), now=evaluated_at,
                          news_risk_evidence=dict_value(final.get('runtime_status.json', {}).get('data')).get('news_risk_evidence')),
                      'fmp_input_usage': fmp_input_usage(pulsar,ai),
                      'fmp_observed_calls_in_window': len(calls) if fmp_calls_meta.get('status') == 'OK' else None,
                      'fmp_calls_export': fmp_calls_meta,
                      'fmp_recorded_evidence_uses_in_window': len(uses) if fmp_uses_meta.get('status') == 'OK' else None,
                      'fmp_uses_export': fmp_uses_meta, 'fmp_evidence_uses': uses,
                      'fmp_evidence_uses_meaning': 'Spezifische registrierte Verwendungen; null beweist nicht, dass FMP-Daten in GPT oder Entscheidungen ungenutzt blieben. Paket- und Auditbelege separat pruefen.',
                      'massive': {'export_status': dbmeta.get('massive_service.sqlite',{}).get('status','MISSING'),
                                  'observed_reservations_in_window': len(massive_calls) if massive_calls_meta.get('status') == 'OK' else None,
                                  'reservations_export': massive_calls_meta,
                                  'exported_rows_in_window_lower_bound': len(massive_calls),
                                  'count_meaning': 'Begrenzter Export; bei fehlender/fehlerhafter Tabelle ist die Gesamtzahl unbekannt, bei gekuerzter Tabelle eine Untergrenze.',
                                  'reservation_outcomes': dict(Counter(str(r.get('result') or 'UNKNOWN') for r in massive_calls)),
                                  'http_status_counts': dict(Counter(str(r.get('http_status')) for r in massive_calls if r.get('http_status') is not None)),
                                  'persistent_budget_meta': massive_meta,
                                  'rate_measurement': 'Ab 10.1: exportierte persistente Reservierungen/Ausgaenge; RESERVED allein beweist keinen gesendeten Request. Bei fehlender DB ist die Rate unbekannt.',
                                  'status': statuses.get('MASSIVE')}},
        'new_evidence': {name: final.get(name, {'status': 'MISSING'}) for name in
            ('okx_account_action.json','candle_observations.json','nasdaq_halt_diagnostics.json','etoro_protection_journal.json',
             'risk_period_proofs.json')},
        'database_exports': dbmeta, 'sample_count': len(samples),
        'decision_evidence_status': dbmeta.get('decision_history.sqlite',{}).get('tables',{}).get('decisions',{}).get('status','UNKNOWN'),
        'pulsar_evidence_status': dbmeta.get('pulsar_research.sqlite',{}).get('tables',{}).get('cache',{}).get('status','UNKNOWN'),
        'scope': 'PASSIVE_DIAGNOSIS_NOT_RELEASE_APPROVAL',
        'limits': ['Null bei Belegzaehlern heisst keine entsprechenden exportierten Belege; keine Garantie, dass kein Ereignis stattfand.',
                   'Keine neue Broker-, Massive-, FMP-, GPT- oder Telegram-Anfrage.',
                   'Keine Orders, Reparaturen, Migrationen, Einstellungen oder Dienste veraendert.',
                   'Zustandsdateien werden nacheinander gelesen; kein global atomarer Systemzustand.',
                   'Kein neuer GPT-Request innerhalb von 30 Minuten kann bei Vorfiltern, Cache, Budgets oder Wochenendintervall korrekt sein.',
                   'Daten im RAM ohne persistierten Beleg lassen sich nicht nachtraeglich rekonstruieren.',
                   'Datenqualitaet/Quellenverarbeitung kann untersucht werden; 30 Minuten beweisen keine profitable Strategie.',
                   'Secrets werden gefiltert; Trade-IDs, Kontofingerprints, Positionen und Betraege bleiben fuer die Diagnose enthalten.']}


MEASUREMENT_HORIZONS = (1, 3, 5, 10)
MEASUREMENT_COSTS = 0.007
MEASUREMENT_MIN_COMPLETE = 30
MEASUREMENT_PRIMARY = 5


def _measurement_stats(values, costs):
    values = [v for v in values if v is not None]
    if not values:
        return {'n': 0, 'hit_rate': None, 'median': None, 'mean': None, 'median_after_costs': None, 'worst': None, 'best': None}
    return {'n': len(values), 'hit_rate': sum(v > 0 for v in values)/len(values),
            'median': statistics.median(values), 'mean': sum(values)/len(values),
            'median_after_costs': statistics.median(values)-costs, 'worst': min(values), 'best': max(values)}


def pulsar_measurement_report(rows, table_meta=None, *, costs=MEASUREMENT_COSTS):
    """10.5.0: Vorwaertsmessung der Hype-Spur aus dem Export von attention_outcomes.

    Standalone-Kopie der Auswertung in pulsar/measurement.py (gleiche Regeln), damit
    die ZIP ohne den Botcode ausgewertet werden kann. Ein fehlender Export ist
    UNBEKANNT, keine leere Messung.
    """
    table_meta = table_meta or {}
    rows = [dict_value(r) for r in (rows or [])]
    if table_meta.get('status') not in (None, 'OK') and not rows:
        return {'status': 'UNKNOWN', 'total': None, 'verdict': {'status': 'UNBEKANNT', 'reason': 'Tabelle attention_outcomes nicht exportiert'},
                'export': table_meta}
    out = {'status': 'OK' if table_meta.get('status', 'OK') == 'OK' else 'PARTIAL', 'total': len(rows), 'round_trip_costs': costs,
           'export': {k: table_meta.get(k) for k in ('status', 'total_rows', 'rows_exported', 'truncated')},
           'status_counts': dict(Counter(str(r.get('status') or 'OPEN') for r in rows)), 'horizons': {}, 'by_trigger': {},
           'by_squeeze': {}, 'by_eligible': {}, 'min_complete_for_verdict': MEASUREMENT_MIN_COMPLETE,
           'primary_horizon_days': MEASUREMENT_PRIMARY}
    for n in MEASUREMENT_HORIZONS:
        out['horizons'][str(n)] = _measurement_stats([number(r.get('r%d' % n)) for r in rows], costs)
    def grouped(key_fn):
        groups = {}
        for row in rows:
            groups.setdefault(key_fn(row), []).append(row)
        result = {}
        for key, group in sorted(groups.items(), key=lambda kv: str(kv[0])):
            item = {str(n): _measurement_stats([number(r.get('r%d' % n)) for r in group], costs) for n in MEASUREMENT_HORIZONS}
            item['total'] = len(group)
            result[str(key)] = item
        return result
    out['by_trigger'] = grouped(lambda r: r.get('trigger_kind') or 'UNBEKANNT')
    out['by_squeeze'] = grouped(lambda r: 'JA' if number(r.get('squeeze')) == 1 else 'nein' if number(r.get('squeeze')) == 0 else 'unbekannt')
    out['by_eligible'] = grouped(lambda r: 'HYPE_KANDIDAT' if number(r.get('eligible')) else 'nur_Ausloeser')
    primary = out['horizons'][str(MEASUREMENT_PRIMARY)]
    n = primary['n']
    if n < MEASUREMENT_MIN_COMPLETE:
        verdict, reason = 'ZU_WENIG_DATEN', '%d von %d vollstaendigen %d-Tage-Messungen' % (n, MEASUREMENT_MIN_COMPLETE, MEASUREMENT_PRIMARY)
    elif primary['hit_rate'] >= 0.55 and primary['median_after_costs'] >= 0.005:
        verdict, reason = 'ERFOLGREICH', 'Trefferquote %.0f %%, Median nach Kosten %+.2f %% ueber %d Messungen' % (primary['hit_rate']*100, primary['median_after_costs']*100, n)
    elif primary['hit_rate'] < 0.45 or primary['median_after_costs'] <= 0:
        verdict, reason = 'NICHT_ERFOLGREICH', 'Trefferquote %.0f %%, Median nach Kosten %+.2f %% ueber %d Messungen' % (primary['hit_rate']*100, primary['median_after_costs']*100, n)
    else:
        verdict, reason = 'UNKLAR', 'Grenzbereich ueber %d Messungen; weiter messen' % n
    out['verdict'] = {'status': verdict, 'reason': reason, 'horizon_days': MEASUREMENT_PRIMARY, 'n': n}
    out['recent'] = [{k: r.get(k) for k in ('symbol', 'trigger_day', 'trigger_kind', 'price_at', 'eligible', 'squeeze', 'rvol', 'r1', 'r3', 'r5', 'r10', 'status')}
                     for r in sorted(rows, key=lambda r: str(r.get('trigger_day') or ''), reverse=True)[:40]]
    out['meaning'] = ('Papiermessung ab Ausloeser bis Schluss nach n Handelstagen, pauschal 0,7 % Rundreise-Kosten; '
                      'keine Kursprognose, keine Handelsfreigabe. Urteil erst ab 30 vollstaendigen 5-Tage-Messungen.')
    return out


def summary_document(analysis, meta):
    """10.5.0: ZUSAMMENFASSUNG.json -- kompakte, fuer die WebUI aufbereitete Sicht auf die ZIP."""
    a = analysis or {}
    findings = a.get('findings') or []
    levels = Counter(str(f.get('level') or 'INFO') for f in findings)
    runtime = {}
    for broker, state in (a.get('runtime') or {}).items():
        ready = dict_value(state.get('buy_readiness'))
        runtime[broker] = {'freshness': state.get('freshness'), 'state': state.get('state'),
                           'buys_allowed': ready.get('kaeufe_erlaubt'), 'buy_reason': ready.get('grund'),
                           'tradable_capital': state.get('tradable_capital'), 'capital_currency': state.get('capital_currency')}
    decisions = dict_value(a.get('decisions'))
    gpt = dict_value(a.get('gpt'))
    pulsar = dict_value(a.get('pulsar'))
    measurement = dict_value(pulsar.get('measurement'))
    pipeline = dict_value(dict_value(a.get('providers')).get('pipeline'))
    sources = [{'provider': s.get('provider'), 'state': s.get('state'), 'last_success': s.get('last_success')}
               for s in (pipeline.get('sources') or []) if isinstance(s, dict)]
    exports = {name: m.get('status') for name, m in (a.get('database_exports') or {}).items() if isinstance(m, dict)}
    return {'schema': 1, 'tool_version': meta.get('tool_version'), 'run_id': meta.get('run_id'),
            'completion': meta.get('completion'), 'observation_mode': meta.get('observation_mode'),
            'started_utc': meta.get('started_utc'), 'ended_utc': meta.get('ended_utc'),
            'observed_seconds': meta.get('observed_seconds'), 'sample_count': a.get('sample_count'),
            'window': a.get('window'), 'source': meta.get('source'),
            'runtime': runtime,
            'decisions': {'rows_in_window': decisions.get('rows_observed_in_window'), 'block_reasons': decisions.get('block_reasons') or {},
                          'export_status': decisions.get('export_status')},
            'gpt': {k: gpt.get(k) for k in ('local_dispatches_in_window', 'successful_results_in_window', 'cache_uses_in_window', 'failed_timed_out_discarded_in_window')},
            'pulsar': {'card_count': pulsar.get('card_count'), 'cards_status': pulsar.get('cards_status'),
                       'cards': [{'symbol': c.get('symbol'), 'state': c.get('state'), 'eligible': c.get('eligible'),
                                  'blocks': c.get('blocks'), 'missing': (c.get('missing') or [])[:6]} for c in (pulsar.get('cards') or [])[:5]],
                       'measurement': {k: measurement.get(k) for k in ('status', 'total', 'status_counts', 'horizons', 'by_trigger', 'by_squeeze', 'by_eligible', 'verdict', 'recent', 'meaning')}},
            'sources': sources, 'database_exports': exports,
            'findings': {'counts': dict(levels), 'items': [{k: f.get(k) for k in ('component', 'level', 'code', 'symbol', 'meaning', 'scope')} for f in findings][:200]},
            'errors': [{'type': (e.get('type') or e.get('error')) if isinstance(e, dict) else str(e)[:80], 'message': str(e.get('message') or e.get('detail') or '')[:300] if isinstance(e, dict) else ''}
                       for e in (meta.get('errors') or [])][:50],
            'limits': a.get('limits') or [],
            'meaning': 'Verdichtete Sicht fuer die WebUI; massgeblich bleiben AUSWERTUNG.json, BEFUNDE.json und die Belegdateien.'}


def diagnostic_findings(analysis):
    findings = []
    for broker, runtime in analysis['runtime'].items():
        if runtime['freshness'] != 'FRESH':
            findings.append({'component': broker, 'level': 'UNKNOWN' if runtime['freshness']=='UNKNOWN' else 'WARN',
                'code': 'RUNTIME_'+runtime['freshness'], 'evidence': runtime,
                'meaning': 'Kein aktueller Heartbeatbeleg. Brokerverbindung und Positionsschutz separat pruefen.'})
        action = dict_value(runtime.get('account_action'))
        if action.get('required'):
            findings.append({'component': broker, 'level': 'WARN', 'code': 'BROKER_ACCOUNT_ACTION_REQUIRED',
                'scope': 'CURRENT' if runtime['freshness']=='FRESH' else 'LAST_SAVED_STATE', 'evidence': action,
                'meaning': 'Broker verlangt eine Bestätigung im eigenen Konto. NEXUS akzeptiert diese Erklärung nicht stellvertretend.'})
        risk = dict_value(runtime.get('risk_manager_buy_gate'))
        if risk.get('blocked') is True:
            findings.append({'component': broker, 'level': 'WARN', 'code': 'RISK_MANAGER_BUY_BLOCK',
                'scope': 'CURRENT' if runtime['freshness'] == 'FRESH' else 'LAST_SAVED_STATE', 'evidence': risk})
            if dict_value(runtime.get('buy_readiness')).get('kaeufe_erlaubt') is True:
                findings.append({'component': broker, 'level': 'WARN', 'code': 'BUY_READINESS_CONTRADICTS_RISK_GATE',
                    'meaning': 'Bereitschaft meldet Kauefe erlaubt, aber der Risikomanager sperrt. Keine wirksame Kaufbereitschaft.'})
    receipts = dict_value(dict_value(analysis.get('new_evidence', {}).get('candle_observations.json')).get('data')).get('receipts', [])
    for receipt in receipts if isinstance(receipts, list) else []:
        count = number(receipt.get('raw_zero_volume_rows'))
        total = number(receipt.get('hashed_rows'))
        if count is not None and total and count / total >= .5:
            findings.append({'component': 'OKX_MARKET_DATA', 'level': 'INFO', 'code': 'BROKER_ZERO_VOLUME_SAMPLE',
                'instrument': receipt.get('instrument'), 'environment': receipt.get('environment'),
                'raw_zero_volume_rows': count, 'observed_rows': total,
                'observed_at': receipt.get('response_received_utc'),
                'meaning': 'Nullvolumen ist bereits in dieser Brokerantwort enthalten. Kein Beweis für einen Parserfehler oder eine Verbindungsstörung; Volumenregel für Käufe bleibt wirksam.'})
    x = dict_value(analysis['providers'].get('pipeline', {}).get('x'))
    if x.get('state') not in {None, 'DISABLED', 'NOT_OBSERVED', 'OK', 'WAITING', 'WAITING_FIRST_REQUEST'}:
        findings.append({'component': 'X', 'level': 'INFO', 'code': 'X_RESEARCH_STATUS',
            'evidence': {key: x.get(key) for key in ('state', 'detail', 'last_attempt', 'last_success', 'cooldown_until')},
            'meaning': 'Quellen-/Zeitplan-/Budgetstatus; X besitzt keine Order- oder Kaufsperrgewalt.'})
    for coverage in (x.get('counts_coverage') if isinstance(x.get('counts_coverage'), list) else [])[:100]:
        if isinstance(coverage, dict) and (coverage.get('has_errors') or coverage.get('has_next_page')
                or number(coverage.get('invalid_rows')) or coverage.get('missing_days')):
            findings.append({'component': 'X', 'level': 'INFO', 'code': 'X_COUNTS_COVERAGE_INCOMPLETE',
                'symbol': coverage.get('symbol'), 'evidence': coverage,
                'meaning': 'Unvollstaendige/ungueltige Tagesabdeckung ist keine gemessene Null. '
                           'Normalisierte Aufmerksamkeit darf daraus nicht als bestaetigt erscheinen.'})
    for attention in (x.get('attention') if isinstance(x.get('attention'), list) else [])[:100]:
        if isinstance(attention, dict) and attention.get('inconsistent_control_days'):
            findings.append({'component': 'X', 'level': 'WARN', 'code': 'X_COUNTS_CONTROL_INCONSISTENT',
                'symbol': attention.get('symbol'), 'evidence': attention,
                'meaning': 'Symbol- und Kontrollmenge passen nicht zusammen; normalisierter Wert bleibt unbelegt.'})
    for row in analysis.get('broker_history_results', {}).get('rows', []):
        if row.get('broker_result_quality') in {'BROKER_HISTORY_COST_SCOPE_CONFLICT', 'BROKER_HISTORY_ARITHMETIC_MISMATCH'}:
            resolved = row.get('fee_quality') in {'CONFIRMED','BROKER_CONFIRMED','USER_CONFIRMED','CASH_DELTA_CONFIRMED'} and number(row.get('netto_pnl')) is not None
            # 10.7.0: Erwartungswert -- beziffert, gekennzeichnet, nicht belegt.
            expected = row.get('fee_quality') == 'EXPECTED_UNVERIFIED' and number(row.get('netto_pnl')) is not None
            findings.append({'component': 'etoro', 'level': 'INFO' if (resolved or expected) else 'WARN',
                'code': ('ETORO_EXPECTED_FEE_MODEL' if expected else row['broker_result_quality']),
                'symbol': row.get('symbol'), 'evidence': row,
                'meaning': ('Lokal ist ein Nettoergebnis mit eigenem Kostenbeleg gebucht; das History-Feld umfasst weiterhin andere Kostenbestandteile.' if resolved else
                    'Abschlussgebuehr als Erwartungswert aus bestaetigten Abrechnungen desselben Kontos eingetragen (nicht belegt); der naechste Barbestandsbeleg ersetzt ihn.' if expected else
                    'Die Position kann bereits geschlossen sein. Broker-History und bestaetigte Kosten '
                    'sind noch nicht zu einer widerspruchsfreien Nettobuchung abgeglichen.')})
    for scope in analysis.get('etoro_private_stream', {}).get('scopes', []):
        states = dict_value(scope.get('states'))
        if any(number(states.get(state)) for state in ('ERROR', 'QUARANTINED', 'UNMATCHED', 'PENDING')):
            findings.append({'component': 'etoro', 'level': 'INFO', 'code': 'PRIVATE_STREAM_MATCHING_PENDING',
                'evidence': scope, 'meaning': 'Persistierte Ereignisse haben noch keine gueltige lokale Zuordnung. '
                'Das ist weder ein Ausfuehrungsbeweis noch ein Grund, Konten miteinander zu vermischen.'})
    for item in analysis['providers'].get('source_history',{}).get('current',[]):
        if item['reported_error']:
            findings.append({'component': item['canonical_provider'], 'level': 'WARN',
                             'code': 'SOURCE_REPORTED_ERROR', 'evidence': item})
    measurement = dict_value(analysis['pulsar'].get('measurement'))
    verdict = dict_value(measurement.get('verdict'))
    if measurement:
        findings.append({'component':'PULSAR','level':'INFO' if verdict.get('status') in {'ERFOLGREICH','ZU_WENIG_DATEN','UNKLAR'} else 'WARN' if verdict.get('status')=='NICHT_ERFOLGREICH' else 'UNKNOWN',
            'code':'PULSAR_MEASUREMENT_'+str(verdict.get('status') or 'UNBEKANNT'),
            'evidence': {'total': measurement.get('total'), 'status_counts': measurement.get('status_counts'), 'verdict': verdict},
            'meaning': 'Vorwaertsmessung der Hype-Spur (Papierergebnis je Ausloeser); ' + str(verdict.get('reason') or '') +
                       '. Kein Beweis fuer oder gegen einen profitablen Handel ohne Ausfuehrungskosten.'})
    if analysis['pulsar'].get('card_count') is None:
        findings.append({'component':'PULSAR','level':'UNKNOWN','code':'PULSAR_CARDS_NOT_PROVEN',
                         'evidence': {k:analysis['pulsar'].get(k) for k in ('cards_status','cards_provenance','other_observed_revisions')}})
    for card in analysis['pulsar']['cards']:
        for source in card['sources']:
            if source.get('fields_lost'):
                findings.append({'component':'PULSAR','symbol':card['symbol'],'level':'WARN',
                    'code':'NEWS_FIELDS_MISSING_IN_PACKET','evidence':source})
        for stage in card['stages']:
            if stage['phase'] in {'FAILED','TIMED_OUT','DISCARDED'}:
                findings.append({'component':'GPT','symbol':card['symbol'],'level':'WARN',
                    'code':'GPT_'+stage['phase'],'evidence':stage})
            if stage['source_ids_missing_from_packet']:
                findings.append({'component':'PULSAR','symbol':card['symbol'],'level':'WARN',
                    'code':'SOURCE_REFERENCE_NOT_IN_PACKET','evidence':stage})
    for name, meta in analysis['database_exports'].items():
        tables = meta.get('tables',{})
        has_hard_table_error = any(t.get('status')!='OK' for t in tables.values())
        has_omitted_cells = any(t.get('omitted_cell_count') for t in tables.values())
        if meta.get('status')!='OK':
            if meta.get('status')=='PARTIAL_LIMITED' and not has_hard_table_error and not has_omitted_cells:
                findings.append({'component':name,'level':'INFO','code':'DATABASE_EXPORT_BOUNDED',
                                 'meaning':'Bewusst begrenzter Diagnoseexport; Tabellenzaehler bleiben Untergrenzen, kein Lese- oder Datenbankfehler.'})
            else:
                findings.append({'component':name,'level':'UNKNOWN','code':'DATABASE_EVIDENCE_'+meta.get('status','UNKNOWN')})
        for name2, table in tables.items():
            evidence = {k:table.get(k) for k in ('status','total_rows','rows_exported','truncated')}
            if table.get('status')!='OK':
                findings.append({'component':name+'/'+name2,'level':'UNKNOWN','code':'TABLE_EXPORT_INCOMPLETE',
                                 'evidence':evidence})
            elif table.get('truncated'):
                findings.append({'component':name+'/'+name2,'level':'INFO','code':'TABLE_EXPORT_BOUNDED',
                                 'evidence':evidence,
                                 'meaning':'Bewusste Zeilen-/Groessenbegrenzung; kein Beweis, dass die Tabelle selbst unvollstaendig oder defekt ist.'})
            if table.get('omitted_cell_count'):
                findings.append({'component':name+'/'+name2,'level':'UNKNOWN','code':'TABLE_CELLS_OMITTED',
                                 'evidence':table.get('omitted_cells')})
    for broker, history in analysis.get('historical_cost_gaps',{}).get('brokers',{}).items():
        if history['net_result_missing'] or history['fee_evidence_incomplete']:
            findings.append({'component':broker,'level':'INFO','code':'HISTORICAL_COST_RESULT_GAPS',
                'scope':'HISTORICAL_CLOSED_TRADES',
                'missing_net_results':len(history['net_result_missing']),
                'incomplete_fee_evidence':len(history['fee_evidence_incomplete']),
                'meaning':'Historische Kosten-/Nettoluecken; keine zusaetzlichen offenen Positionen. Vollstaendige Performance nicht belegt.'})
    return findings


def render_report(analysis, meta):
    a = analysis
    lines = ['# NEXUS 10 – Diagnosebelege', '',
             '**Beobachtungsbericht; keine pauschale Handelsfreigabe.**', '',
             'Die ZIP dient der anschliessenden fachlichen Auswertung. Sie fuehrt keinen Handelstest aus.', '',
             f"Zeitraum: {a['window']['start_utc']} bis {a['window']['end_utc']}.",
             f"Tatsaechliche Beobachtung: {meta.get('observed_seconds',0)/60:.1f} Minuten; {a['sample_count']} Messpunkte.",
             f"Laufabschluss: {meta.get('completion')}; Quellerkennung: {meta.get('source_detection',{}).get('state')}.", '',
             '| Bereich | Beobachteter Befund |', '|---|---|']
    for broker, state in a['runtime'].items():
        lines.append(f"| {broker} | Heartbeat: {state['freshness']}; Laufzustand: {state['state'] or 'unbekannt'} |")
    lines += [f"| Entscheidungen im Export/Zeitraum | {a['decisions']['rows_observed_in_window']} |",
              f"| GPT lokal gestartet im Zeitraum | {a['gpt']['local_dispatches_in_window']} |",
              f"| GPT erfolgreiche Ergebnisse im Zeitraum | {a['gpt']['successful_results_in_window']} |",
              f"| GPT Cache-Nutzungen im Zeitraum | {a['gpt']['cache_uses_in_window']} |",
              f"| GPT Fehler/Timeout/verworfen im Zeitraum | {a['gpt']['failed_timed_out_discarded_in_window']} |",
              f"| PULSAR-Karten im belegten Cache-Stand | {a['pulsar']['card_count'] if a['pulsar']['card_count'] is not None else 'UNBEKANNT'} ({a['pulsar']['cards_status']}) |", '',
              '## Handelsbereitschaft und beobachtete Blockaden', '']
    for broker, state in a['runtime'].items():
        ready = dict_value(state.get('buy_readiness'))
        if ready:
            lines.append(f"- {broker.upper()}: Kaeufe {'ERLAUBT' if ready.get('kaeufe_erlaubt') is True else 'GESPERRT'}; "
                         f"Grund: {ready.get('grund') or 'kein Grund im Export'}")
            if broker == 'okx':
                evidence = dict_value(state.get('capital_evidence'))
                balances = dict_value(state.get('balances'))
                lines.append(f"  - OKX Risikokapital: {state.get('tradable_capital') if state.get('tradable_capital') is not None else 'UNBEKANNT'} "
                             f"{state.get('capital_currency') or evidence.get('basis_currency') or ''}; "
                             f"freigegebene finanzierte Waehrungen: {', '.join(evidence.get('funded_allowed_lanes') or []) or 'keine/noch nicht belegt'}; "
                             f"positive nicht freigegebene Waehrungen: {', '.join(evidence.get('unsupported_positive_balance_currencies') or []) or 'keine/noch nicht belegt'}.")
                if balances:
                    lines.append('  - Im Runtime-Status gespeicherte positive Guthabenwaehrungen: ' + ', '.join(sorted(balances)) + '.')
        # 10.7.0: Sperrliste mit Grund, Reichweite, Ablauf und Aufloesung.
        sperren = dict_value(state.get('sperren'))
        if sperren:
            rows = sperren.get('sperren') or []
            if rows:
                lines.append(f"  - Kaufsperren ({len(rows)}): "
                             + ('Konto gesperrt' if sperren.get('domaene_gesperrt') else
                                'Konto frei, nur einzelne Werte gesperrt: ' + (', '.join(sperren.get('gesperrte_symbole') or []) or 'keine')))
                for s in rows:
                    lines.append(f"    - {s.get('grund')}{(' ' + s.get('symbol')) if s.get('symbol') else ''}: "
                                 f"Reichweite {s.get('reichweite')}, endet durch {s.get('ablauf')}; "
                                 f"Aufloesung: {s.get('aufloesung')}"
                                 + (f" ({s.get('detail')})" if s.get('detail') else ''))
            else:
                lines.append('  - Kaufsperren: keine aktive Sperre gemeldet.')
    blocks = dict_value(a.get('decisions')).get('block_reasons') or {}
    if blocks:
        lines += ['', '| Entscheidungsblocker | Anzahl im Auswertungsfenster |', '|---|---:|']
        for reason, count in sorted(blocks.items(), key=lambda item: (-int(item[1] or 0), str(item[0]))):
            lines.append(f"| {reason} | {count} |")
    lines += ['', 'Ein Blocker ist kein Brokerfehlerbeweis. Er zeigt, an welcher NEXUS-Pruefung die Entscheidung endete; die konkrete Ursache steht im Readiness-/Entscheidungsbeleg.', '',
              '## Startsammlung, Messung und Endsammlung', '',
              '| Phase | GPT gestartet | GPT erfolgreich abgeschlossen | FMP-Journaleintraege | Massive-Reservierungen |',
              '|---|---:|---:|---:|---:|']
    def shown(count):
        return ('UNBEKANNT' if count['count'] is None else
                ('mindestens ' if count['quality']=='LOWER_BOUND' else '')+str(count['count']))
    for phase, item in a.get('collection_phases',{}).items():
        label = {'startup':'Startsammlung','observation':'Messung','shutdown':'Endsammlung','total':'Gesamtlauf'}[phase]
        gpt = item['gpt']
        lines.append(f"| {label} | {shown(gpt['local_dispatches_in_window'])} | {shown(gpt['successful_results_in_window'])} | {shown(item['fmp'])} | {shown(item['massive'])} |")
    usage = a['providers']['fmp_input_usage']
    history = a['providers']['source_history']
    lines += ['', a.get('phase_meaning',''),
              'Die Zaehler beziehen sich auf exportierte Quellen-Snapshots. Reserviert ist nicht gleich gesendet; begrenzte Exporte liefern Untergrenzen. Cache-Nutzungen und ausstehende Requests stehen separat in AUSWERTUNG.json.', '',
              '## Datenabruf und tatsaechliche Verarbeitung', '',
              f"FMP-Eingabeverwendung: **{usage['status']}**; {usage['requests_with_proven_fmp_input']} eindeutige GPT-Requests mit verknuepftem Eingabebeleg und Ausfuehrungsaudit. Keine Aussage ueber kausale Handelswirkung.", '',
              'AUSWERTUNG.json trennt Quellenbelege, Uebernahme ins PULSAR-Paket, GPT-Ausfuehrung, Quellenzitate und Textuebernahme. '
              'EXACT_TEXT_MATCH bedeutet eine identische uebernommene These mit passendem Textursprung. NOT_PROVEN bedeutet, dass der Export diese Nutzung nicht beweist.', '',
              'Die PULSAR-Karten werden zusaetzlich darauf geprueft, ob beim Verdichten von News Text, URL oder Publikationszeit verloren gingen. '
              'Das ist ein Datenqualitaetsbefund; eine fachliche Bewertung folgt anhand der Original- und Paketdaten.', '',
              f"Quellenstatus: {len(history['current'])} im Lauf beobachtet; {len(history['historical'])} historische/deaktivierte Eintraege; {len(history['unknown'])} zeitlich nicht einordenbar. Alte Fehler werden nicht als neue Warnung gezaehlt.", '',
              f"Aktuell wirksame Quellenpausen: {len(history.get('active_pauses', []))}. Auch vor dem Messfenster begonnene Pausen werden separat gezaehlt.",
              *[f"- {r['source']}: pausiert bis {r['next_retry_at']}" for r in history.get('active_pauses', [])], '',
              '## Historische Kosten- und Nettoluecken', '',
              '| Broker | Geschlossene Zeilen ohne Nettoergebnis | Zeilen mit unvollstaendigem Kostenbeleg |',
              '|---|---:|---:|']
    for broker, item in a['historical_cost_gaps']['brokers'].items():
        lines.append(f"| {broker} | {shown(item['net_result_missing_count'])} | {shown(item['fee_evidence_incomplete_count'])} |")
    broker_results = a.get('broker_history_results', {})
    lines += ['', broker_results.get('meaning', ''), '',
              '| Position | Broker-History G/V | Lokal abgeglichenes Netto | Kostenpruefung |',
              '|---|---:|---:|---|']
    for row in broker_results.get('rows', []):
        lines.append(f"| {row.get('symbol') or row.get('broker_position_id')} | "
                     f"{row.get('broker_reported_net_pnl') if row.get('broker_reported_net_pnl') is not None else 'UNBEKANNT'} "
                     f"{row.get('broker_result_currency') or ''} | "
                     f"{row.get('netto_pnl') if row.get('netto_pnl') is not None else 'UNBEKANNT'} | "
                     f"{row.get('broker_result_quality') or 'UNBEKANNT'} |")
    settlement = a.get('etoro_settlement', {})
    lines += ['', '## eToro: Abrechnung und Stornierungen', '', settlement.get('meaning','Nicht beobachtet'), '',
        f"Bestaetigte Nutzerabrechnungen im Export: {len(settlement.get('reviews',[]))}; "
        f"Stornovorgaenge: {len(settlement.get('cancellations',[]))}; "
        f"proceeds-Beobachtungen ohne bestaetigten Gebuehrenumfang: {len(settlement.get('unclassified_proceeds',[]))}. "
        'Bei fehlendem/gekuerztem Export sind diese Zahlen keine vollstaendigen Kontosummen.']
    for r in settlement.get('reviews',[]):
        lines.append(f"- Trade {r.get('trade_id')}: Einstieg {r.get('entry_cost')} USD, Abschluss {r.get('exit_cost')} USD, Netto {r.get('net')} USD; Quelle USER_CONFIRMED.")
    for r in settlement.get('cancellations',[]):
        lines.append(f"- Storno Order {r.get('order_id')}: {r.get('state')} / {r.get('label')}.")
    stream = a.get('etoro_private_stream', {})
    lines += ['', '## Privater eToro-Ereignisstrom', '', stream.get('meaning', ''), '',
              f"Exportstatus: {stream.get('status', 'MISSING')}. Rohereignisse werden nicht exportiert."]
    for scope in stream.get('scopes', []):
        lines.append(f"- Konto {scope.get('account_fingerprint') or 'UNBEKANNT'} / {scope.get('environment')}: "
                     f"{scope.get('events_observed')} eindeutige Ereignisse im Export, "
                     f"{scope.get('deliveries_observed')} Zustellungen; Zuordnung: {dumps(scope.get('states', {}))}. "
                     f"Zaehlerqualitaet: {scope.get('count_quality')}.")
    pipeline = a['providers'].get('pipeline', {})
    news_risk = dict_value(pipeline.get('news_risk_evidence'))
    lines += ['', '## News, Reddit und X: Kommunikations- und Verarbeitungskette', '',
              pipeline.get('meaning', 'Keine getrennten Verarbeitungsbelege vorhanden.'), '',
              f"News-Risikohinweis: {news_risk.get('status', 'NOT_OBSERVED')}; "
              f"Rohscore {news_risk.get('raw_crisis_score', 'UNBEKANNT')}, "
              f"nach Quellenabgleich verwendbarer Score {news_risk.get('actionable_crisis_score', 'UNBEKANNT')}; "
              f"Kaufpause gestuetzt: {news_risk.get('buy_pause_supported', 'UNBEKANNT')}; "
              f"beobachtete Urspruenge: {news_risk.get('origin_count', 'UNBEKANNT')}. "
              'Mehrere Urspruenge sind ein Risikohinweis, kein Beweis eines unabhaengig verifizierten Ereignisses.', '',
              '| Quelle | Zugang zuletzt | Letzter Erfolg UTC | Elemente im letzten Abruf | Karten mit Quellenbeleg |',
              '|---|---|---|---:|---:|']
    for source in pipeline.get('sources', []):
        count = source.get('returned_items_last_request')
        cards = source.get('cards_with_source')
        lines.append(f"| {source['provider']} | {source['state']} | {source.get('last_success') or 'UNBEKANNT'} | {count if count is not None else 'UNBEKANNT'} | {cards if cards is not None else 'UNBEKANNT'} |")
    x = pipeline.get('x', {})
    counts = dict_value(x.get('counts'))
    budget = dict_value(x.get('budget'))
    lines += ['', 'Reddit: aggregierte Toplisten aus ApeWisdom/Tradestie. Keine direkte Reddit-API oder vollstaendige Post-/Autorenmessung belegt.', '',
              f"X: **{x.get('state', 'NOT_OBSERVED')}**; letzter API-Erfolg: {x.get('last_success') or 'UNBEKANNT'}. "
              f"Requests: {counts.get('requests', 'UNBEKANNT')}; empfangene Posts: {counts.get('posts_received', 'UNBEKANNT')}; "
              f"verarbeitet: {counts.get('processed_posts', 'UNBEKANNT')}; Duplikate: {counts.get('duplicates', 'UNBEKANNT')}; "
              f"Consumer-Lesevorgaenge: {counts.get('consumer_reads', 'UNBEKANNT')}.", '',
              f"X-Budgetperiode: {budget.get('month', 'UNBEKANNT')}; lokale Kostenobergrenze verbraucht: {budget.get('charged_upper_eur', 'UNBEKANNT')} EUR; "
              f"reserviert: {budget.get('reserved_eur', 'UNBEKANNT')} EUR; Limit: {budget.get('limit_eur', 'UNBEKANNT')} EUR. Kein Anbieter-Rechnungsbeleg.", '',
              'X-Ereignisse und Hashbelege stehen in AUSWERTUNG.json und market_intelligence_status.json. '
              'Die Diagnose fuehrt keine Anbieter-Testabrufe aus und exportiert keine X-Rohtexte, Autorenkennungen aus Beitraegen oder Zugangsdaten.']
    discovery = dict_value(x.get('discovery'))
    coordination = dict_value(pipeline.get('pulsar_pipeline'))
    lines += ['', f"X-Kandidaten im gespeicherten Sammlerstand: {discovery.get('candidate_count', 'UNBEKANNT')}; "
              f"PULSAR-Auswahlbeleg: {coordination.get('selection_state', 'NOT_OBSERVED')}. "
              'Entdeckt, zur Pruefung ausgewaehlt und in eine Karte aufgenommen sind getrennte Stufen. '
              'Tagesabdeckung, Aenderungen der Vergleichsgruppe und unabhaengige Gegenbelege stehen in AUSWERTUNG.json.']
    measurement = dict_value(a['pulsar'].get('measurement'))
    mverdict = dict_value(measurement.get('verdict'))
    lines += ['', '## PULSAR-Vorwaertsmessung (Hype-Spur)', '',
              f"Urteil: **{mverdict.get('status', 'UNBEKANNT')}** -- {mverdict.get('reason', 'keine Messdaten im Export')}.",
              f"Gemessene Ausloeser: {measurement.get('total') if measurement.get('total') is not None else 'UNBEKANNT'}; Stand: {dumps(measurement.get('status_counts') or {})}.", '',
              '| Horizont | Messungen | Trefferquote | Median | Median nach Kosten |', '|---|---:|---:|---:|---:|']
    for horizon, stats in (measurement.get('horizons') or {}).items():
        stats = dict_value(stats)
        fmt = lambda v: 'UNBEKANNT' if v is None else f"{v*100:+.2f} %"
        hit = stats.get('hit_rate')
        hit_text = 'UNBEKANNT' if hit is None else f"{hit*100:.0f} %"
        lines.append(f"| {horizon} Handelstage | {stats.get('n', 0)} | {hit_text} | {fmt(stats.get('median'))} | {fmt(stats.get('median_after_costs'))} |")
    lines += ['', measurement.get('meaning', 'Keine Messung im Export.'), '',
              a['historical_cost_gaps']['meaning'], '',
              '## PULSAR und Zeitfenster', '', a['pulsar']['schedule'].get('note','Zeitfenster unbekannt'), '',
              'Kein neuer Kauf, kein GPT-Aufruf und kein neuer PULSAR-Suchlauf sind fuer sich allein kein Fehler.', '',
              '## Dateien fuer die Auswertung', '',
              '- AUSWERTUNG.json: maschinenlesbare Befunde und Belegketten.',
              '- BEFUNDE.json: auffaellige Zustaende und konkrete Belegluecken; kein pauschales PASS.',
              '- ZUSAMMENFASSUNG.json: verdichtete Sicht (Laufzeit, Blocker, Befunde, PULSAR-Messung) fuer den WebUI-Bericht.',
              '- start/ und ende/: Zustand, Datenbanktabellen, Quellen-/KI-Audits und Logs.',
              '- verlauf/: regelmaessige Messpunkte, darunter PULSAR-Heartbeat und Phasen.',
              '- system/: Dienstpfade, Prozess-/Pi-Messungen und Dienstjournal.',
              '- software/: Versions-/Quellhashpruefung und statische Konfigurationswerte.',
              '- META.json, FEHLER.json, AUSLASSUNGEN.json: Laufdetails und erkennbare Erfassungsluecken.',
              '- MANIFEST_SHA256.json: Pruefsummen aller enthaltenen Belegdateien.', '',
              '## Grenzen', ''] + ['- '+v for v in a['limits']]
    lines = [line.replace('| None |', '| UNBEKANNT – Export unvollstaendig |') for line in lines]
    incomplete = [(db,table) for db,meta_db in a['database_exports'].items()
                  for table,detail in meta_db.get('tables',{}).items() if detail.get('status')!='OK']
    limited = [(db,table) for db,meta_db in a['database_exports'].items()
               for table,detail in meta_db.get('tables',{}).items() if detail.get('truncated') or detail.get('omitted_cell_count')]
    lines[4:4] = [f'**Erfassungsqualitaet: {len(incomplete)} fehlerhafte Tabellenexporte; {len(limited)} bewusst begrenzte bzw. zellenweise unvollstaendige Tabellen.**',
                  'Fehlende Belege werden nicht als null Ereignisse interpretiert.', '']
    if meta.get('errors'):
        lines += ['', '**Nicht alle Erfassungen waren erfolgreich. FEHLER.json und die einzelnen Indexdateien beachten.**']
    return '\n'.join(lines)+'\n'


def capture_journal(bundle, since, until):
    for unit in UNITS:
        record = run_command(['journalctl', '-u', unit, '--since', '@'+str(int(since)),
                              '--until', '@'+str(int(until)), '-n', '12000', '--no-pager',
                              '-o', 'short-iso-precise'], timeout=10, limit=4*MIB)
        record['scope'] = {'since_utc': utc(since), 'until_utc': utc(until), 'max_lines': 12000}
        record['permission_notice'] = 'Nur fuer den aufrufenden Benutzer lesbare Journalzeilen; kein sudo.'
        bundle.write('system/journal_'+unit+'.json', record)


def package_versions(root):
    results = []
    base = root/'.venv/lib'
    if not base.is_dir() or any(p.is_symlink() for p in (base, base.parent)):
        return {'status': 'NOT_AVAILABLE', 'packages': []}
    for path in sorted(base.glob('python*/site-packages/*.dist-info/METADATA'))[:250]:
        try:
            if any(p.is_symlink() for p in (path, *path.parents)):
                continue
            raw, _ = read_regular(path, 16000)
            fields = {}
            for line in raw.decode('utf-8', errors='replace').splitlines():
                if line.startswith(('Name: ', 'Version: ')):
                    key, value = line.split(': ',1)
                    fields[key] = value
                if 'Name' in fields and 'Version' in fields:
                    break
            results.append(fields)
        except (OSError, ValueError):
            continue
    return {'status': 'METADATA_ONLY', 'packages': results, 'limit': 250,
            'notice': 'Paketmetadaten gelesen; keine Installation und kein Import von Botmodulen.'}


def capture_phase(root, bundle, label):
    print(f'[{datetime.now().strftime("%H:%M:%S")}] {label}: Zustand und Datenbankbelege erfassen ...', flush=True)
    summaries, dbs = {}, {}
    for name in DATABASES:
        summaries[name], dbs[name] = export_database(root/name, bundle, label+'/datenbanken/'+name)
    # Runtime heartbeat must not age during the slower database export.
    audits = capture_logs(root, bundle, label+'/logs')
    states = capture_states(root, bundle, label+'/zustand')
    return states, summaries, dbs, audits


def export_issues(summaries, phase):
    issues = []
    for name, meta in summaries.items():
        if meta.get('status')!='OK':
            issues.append({'phase':phase,'database':name,'status':meta.get('status','UNKNOWN'),
                           'detail':meta.get('detail',''),
                           'tables':[k for k,v in meta.get('tables',{}).items() if v.get('status')!='OK' or v.get('truncated') or v.get('omitted_cell_count')]})
    return issues


def cpu_percent(previous, current):
    try:
        a = [int(v) for v in previous.split()[1:9]]
        b = [int(v) for v in current.split()[1:9]]
        deltas = [y-x for x,y in zip(a,b)]
        total = sum(deltas)
        return round(100*(total-deltas[3]-deltas[4])/total,2) if total>0 and min(deltas)>=0 else None
    except (AttributeError, ValueError, IndexError):
        return None


def observe(root, bundle, stop, deadline, interval):
    samples, seen = [], {}
    pulsar_memo = {}
    previous_cpu = None
    counter = 0
    while True:
        began = time.monotonic()
        state = services()
        system = system_sample(state, bundle.directory)
        system['cpu_percent_since_previous_sample'] = cpu_percent(previous_cpu, system.get('cpu_ticks'))
        previous_cpu = system.get('cpu_ticks')
        parts = {name: load_json(root/name, bundle.scrub) for name in SAMPLE_FILES}
        if isinstance(parts.get('market_intelligence_status.json', {}).get('data'), dict):
            parts['market_intelligence_status.json']['data'] = x_receipts_only(parts['market_intelligence_status.json']['data'])
        pulsar = sample_pulsar(root, bundle.scrub, memo=pulsar_memo)
        parts['pulsar_control'] = pulsar.get('control', {'error': pulsar.get('control_error')})
        for row in pulsar.get('cache', []):
            parts['pulsar_cache_'+str(row.get('key'))] = row
        sample = {'at': utc(), 'services': state, 'system': system, 'objects': {},
                  'pulsar_read_errors': {k:v for k,v in pulsar.items() if k.endswith('_error')}}
        for name, value in parts.items():
            hashed = digest(dumps(value).encode())
            key = name+':'+hashed
            if key not in seen:
                location = 'verlauf/objekte/'+re.sub('[^A-Za-z0-9_.-]', '_',name)+'_'+hashed[:16]+'.json'
                if bundle.used > bundle.maximum//2:
                    sample['objects'][name] = {'sha256_sanitized': hashed, 'omitted': 'Platz fuer Endzustand reserviert'}
                    continue
                if bundle.write(location, value):
                    seen[key] = location
            sample['objects'][name] = {'sha256_sanitized': hashed, 'file': seen.get(key)}
        sample['collection_seconds'] = time.monotonic()-began
        bundle.write(f'verlauf/messpunkt_{counter:04d}.json', sample)
        samples.append(sample)
        counter += 1
        remaining = deadline-time.monotonic()
        if stop.is_set() or remaining<=0:
            break
        if counter == 1 or counter % 2 == 0:
            print(f'Beobachtung laeuft: {counter} Messpunkte, noch etwa {math.ceil(remaining/60)} Minuten. Strg+C erstellt einen Teilbericht.', flush=True)
        stop.wait(min(remaining, max(.1, interval-(time.monotonic()-began))))
    return samples


def parser_for_cli():
    parser = argparse.ArgumentParser(prog='NEXUS_10_Diagnose_Starten.sh', description='NEXUS 10: ohne Optionen volle 30 Minuten beobachten, danach ZIP erstellen. --sofort sammelt nur vorhandene Belege. Keine Broker-/GPT-Abfrage, keine Dienstaktion.')
    parser.add_argument('--quelle', type=Path, help='Aktiver NEXUS-Ordner; sonst automatische Erkennung aus systemd')
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--minuten', type=float, default=30, help='Beobachtungsdauer in Minuten (Standard 30; maximal 180)')
    group.add_argument('--sofort', action='store_true', help='Sofortbericht aus bereits vorhandenen Belegen; kein 30-Minuten-Warten')
    parser.add_argument('--intervall', type=float, default=30, help='Messabstand in Sekunden (Standard 30, mindestens 5)')
    parser.add_argument('--rueckblick', type=int, default=120, help='Journal-Kontext in Minuten; zugleich Auswertungsfenster bei --sofort')
    parser.add_argument('--ausgabe', type=Path, help='ZIP-Ausgabeordner; Standard ~/Downloads/NEXUS_Diagnosen')
    return parser


def replay_archive(path):
    """Re-evaluate an existing diagnosis ZIP offline, without extracting files.

    Only manifest-checked, bounded JSON/JSONL evidence is interpreted. No source
    code from the archive is executed, no database is opened, no broker called.
    """
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)) or len(names) > 5000:
            raise ValueError('Doppelte oder zu viele Archiveintraege')
        manifest_info = archive.getinfo('MANIFEST_SHA256.json')
        if manifest_info.file_size > MIB:
            raise ValueError('Manifest zu gross')
        manifest = json.loads(archive.read(manifest_info))
        def read_bytes(name):
            info = archive.getinfo(name)
            if info.file_size > 16*MIB or name.startswith('/') or '..' in Path(name).parts:
                raise ValueError('Unzulaessiger Archiveintrag')
            body = archive.read(info)
            if digest(body) != manifest.get(name):
                raise ValueError('Archivpruefsumme stimmt nicht: '+name)
            return body
        def read_json(name):
            return json.loads(read_bytes(name))
        def read_rows(name):
            return [json.loads(line) for line in read_bytes(name).splitlines() if line.strip()]
        meta = read_json('META.json')
        initial, final = {}, {}
        for phase, states in (('start',initial),('ende',final)):
            for name in [*JSON_FILES, 'risk_period_proofs.json']:
                member = phase+'/zustand/'+name
                if member in names:
                    states[name] = read_json(member)
        dbmeta, dbs, initial_dbs = {}, {}, {}
        wanted = {'pulsar_research.sqlite':{'cache'}, 'decision_history.sqlite':{'decisions','trades',
                  'etoro_settlement_reviews','etoro_cancellations','etoro_proceeds_observations','etoro_cash_receipts','execution_orders'},
                  'market_candles.sqlite':{'candles'}, 'fmp_service.sqlite':{'calls','evidence_uses'},
                  'massive_service.sqlite':{'calls','meta'},
                  'etoro_stream_inbox.sqlite':{'etoro_stream_events'}}
        for db in DATABASES:
            member = 'ende/datenbanken/'+db+'/index.json'
            if member not in names:
                continue
            dbmeta[db] = read_json(member)
            dbs[db] = {}
            for table, detail in dbmeta[db].get('tables',{}).items():
                if table in wanted.get(db,set()) and detail.get('export_file') in names:
                    dbs[db][table] = read_rows(detail['export_file'])
            member = 'start/datenbanken/'+db+'/index.json'
            if db in {'fmp_service.sqlite','massive_service.sqlite'} and member in names:
                detail = read_json(member).get('tables',{}).get('calls',{})
                if detail.get('export_file') in names:
                    initial_dbs[db] = {'calls':read_rows(detail['export_file'])}
        audits = []
        for phase in ('start','ende'):
            for name in ('ai_usage_audit.jsonl','ai_usage_audit.1.jsonl'):
                member = phase+'/logs/'+name
                if member in names:
                    for line in read_bytes(member).splitlines():
                        try:
                            event = json.loads(line)
                            if isinstance(event,dict):
                                audits.append(event)
                        except ValueError:
                            pass  # The log index preserves malformed-line counts.
        samples = [read_json(n) for n in sorted(names) if re.fullmatch(r'verlauf/messpunkt_\d+\.json',n)]
        fallback = pulsar_sample_evidence(samples,read_json,meta.get('run_id'))
        index = read_json('ende/logs/index.json') if 'ende/logs/index.json' in names else {}
        start = timestamp(meta.get('observation_start_utc') or meta.get('started_utc'))
        end = timestamp(meta.get('observation_end_utc') or meta.get('ended_utc'))
        if start is None or end is None:
            raise ValueError('Zeitfenster fehlt')
        result = final_analysis(initial,final,dbs,dbmeta,audits,samples,start,end,
            collection_meta=meta,initial_dbs=initial_dbs,pulsar_fallback=fallback,audit_meta=audit_export_quality(index))
        result['findings'] = diagnostic_findings(result)
        return result, meta


def audit_export_quality(index):
    current = dict_value(index.get('ai_usage_audit.jsonl'))
    entries = [dict_value(v) for k,v in index.items() if k.startswith('ai_usage_audit') and v.get('status') != 'MISSING']
    return {'status': current.get('status','UNKNOWN'),
            'truncated': any(v.get('truncated') or v.get('changed_during_read') or v.get('status')!='OK' for v in entries),
            'invalid_json_lines': sum(v.get('invalid_json_lines') or 0 for v in entries),
            'source_mtime_utc': current.get('mtime_utc')}


def main(argv=None, *, launcher=None, progress=None):
    def report(phase, **values):
        if progress:
            progress(phase, **values)
    parser = parser_for_cli()
    args = parser.parse_args(argv)
    if not math.isfinite(args.minuten) or not 0 <= args.minuten <= 180:
        parser.error('--minuten muss zwischen 0 und 180 liegen')
    if not math.isfinite(args.intervall) or not 5 <= args.intervall <= 300:
        parser.error('--intervall muss zwischen 5 und 300 Sekunden liegen')
    if not 1 <= args.rueckblick <= 1440:
        parser.error('--rueckblick muss zwischen 1 und 1440 Minuten liegen')
    duration = 0 if args.sofort else args.minuten*60
    if duration:
        print(f'MODUS: {args.minuten:g} Minuten passiv beobachten. Die Beobachtung beginnt nach dem Start-Export; danach folgt der End-Export.', flush=True)
    else:
        print('MODUS: SOFORTEXPORT vorhandener Belege. Dies ist KEIN 30-Minuten-Verlaufstest.', flush=True)
    started, started_clock = time.time(), time.monotonic()
    stop = threading.Event()
    signals = []
    def interrupted(sig, frame):
        signals.append(sig)
        stop.set()
    for sig in (signal.SIGINT, signal.SIGTERM, getattr(signal, 'SIGHUP', signal.SIGTERM)):
        signal.signal(sig, interrupted)
    status = services()
    root, detection = detect_root(args.quelle, status, launcher or sys.argv[0])
    output = (args.ausgabe or Path.home()/'Downloads/NEXUS_Diagnosen').expanduser().absolute()
    if root and output.resolve().is_relative_to(root.resolve()):
        parser.error('Ausgabe ausserhalb des Botordners waehlen, damit dessen Zustand unveraendert bleibt.')
    try:
        bundle = Bundle(output)
    except OSError as exc:
        print('ZIP-Ausgabeordner nicht beschreibbar: '+str(exc), file=sys.stderr)
        return 2
    meta = {'tool_version': TOOL_VERSION, 'run_id': bundle.run_id, 'started_utc': utc(started),
        'source': str(root) if root else None, 'source_detection': detection,
        'requested_observation_seconds': duration, 'interval_seconds': args.intervall,
        'lookback_minutes': args.rueckblick, 'observation_mode': 'TIMED_OBSERVATION' if duration else 'INSTANT_EXPORT', 'completion': 'RUNNING', 'errors': [],
        'collection_type': 'PASSIVE_READ_ONLY', 'network_requests_generated': 0,
        'database_mode': 'mode=ro + query_only; kurze Online-Sicherung inkl. WAL. Export von privater temporaerer Kopie; keine Rohdatenbank in der ZIP.',
        'permission': 'Aufrufbenutzer; keine automatische Rechteerweiterung'}
    initial, final, dbs, dbmeta, audits, samples, initial_calls = {}, {}, {}, {}, [], [], {}
    print('NEXUS-Diagnose: '+(str(root) if root else 'Quellordner nicht eindeutig erkannt'), flush=True)
    print('ZIP-Ziel: '+str(output), flush=True)
    print('NEXUS weiterlaufen lassen. Es werden keine Zusatzabfragen und keine Trades ausgeloest.', flush=True)
    report('START_EXPORT', requested_seconds=duration)
    try:
        if root is None:
            meta['completion'] = 'SOURCE_NOT_FOUND'
            meta['errors'].append(detection)
        else:
            bundle.write('software/konfiguration_ohne_zugangsdaten.json', learn_credentials(root,bundle.scrub))
            bundle.write('software/statische_standardwerte.json', config_literals(root,bundle.scrub))
            bundle.write('software/version_start.json', source_identity(root))
            bundle.write('software/python_pakete.json', package_versions(root))
            initial, initial_dbmeta, initial_dbs, first_audits = capture_phase(root,bundle,'start')
            meta['database_export_issues'] = export_issues(initial_dbmeta, 'start')
            audits.extend(first_audits)
            initial_calls = {name: {'calls': tables.get('calls',[])} for name,tables in initial_dbs.items()
                             if name in {'fmp_service.sqlite','massive_service.sqlite'}}
            del initial_dbs
            bundle.write('system/dienste_start.json', status)
            bundle.write('system/pi_firmware_start.json', run_command(['vcgencmd','get_throttled']))
            # Duration begins after the baseline, so the requested observation is full length.
            observed_start, clock_start = time.time(), time.monotonic()
            report('OBSERVATION', observation_started_at=observed_start, requested_seconds=duration)
            samples = observe(root,bundle,stop,clock_start+duration,args.intervall)
            observed_end = time.time()
            observed_seconds = time.monotonic()-clock_start
            report('END_EXPORT')
            final, dbmeta, dbs, last_audits = capture_phase(root,bundle,'ende')
            meta['database_export_issues'].extend(export_issues(dbmeta, 'ende'))
            audits.extend(last_audits)
            meta['observation_start_utc'] = utc(observed_start)
            meta['observation_end_utc'] = utc(observed_end)
            meta['observed_seconds'] = observed_seconds
            meta['completion'] = 'INTERRUPTED_PARTIAL' if signals else 'COMPLETED'
            if meta['completion']=='COMPLETED' and any(v.get('status') not in {'PARTIAL_LIMITED','MISSING'} for v in meta['database_export_issues']):
                meta['completion'] = 'COMPLETED_WITH_EXPORT_ERRORS'
            if duration:
                started = observed_start
            bundle.write('software/version_ende.json', source_identity(root))
    except Exception as exc:
        meta['completion'] = 'PARTIAL_ERROR'
        meta['errors'].append(error_record(exc))
    ended = time.time()
    meta.update(ended_utc=utc(ended), total_seconds=time.monotonic()-started_clock,
                received_signals=signals, masked_items=bundle.scrub.replacements)
    meta['errors'].extend(v for v in meta.get('database_export_issues',[]) if v.get('status') not in {'PARTIAL_LIMITED','MISSING'})
    try:
        bundle.write('system/dienste_ende.json', services())
        bundle.write('system/pi_firmware_ende.json', run_command(['vcgencmd','get_throttled']))
        if root is not None:
            capture_journal(bundle, started-args.rueckblick*60, ended)
        else:
            bundle.write('system/journal_nicht_erfasst.json', {'reason':'Quellordner unbekannt; kein Logexport ohne zugeordnete Geheimnisfilterung.'})
        window_start = started if duration else ended-args.rueckblick*60
        window_end = timestamp(meta.get('observation_end_utc')) if duration else ended
        window_end = window_end if window_end is not None else ended
        def read_bundle_object(path):
            raw, info = read_regular(bundle.work/path, 16*MIB)
            if info['truncated']:
                raise ValueError('Verlaufsobjekt groesser als Diagnose-Lesebudget')
            return json.loads(raw)
        fallback = pulsar_sample_evidence(samples,read_bundle_object,bundle.run_id)
        try:
            audit_meta = audit_export_quality(read_bundle_object('ende/logs/index.json'))
        except (OSError, ValueError):
            audit_meta = {'status':'MISSING'}
        analysis = final_analysis(initial,final,dbs,dbmeta,audits,samples,window_start,window_end,
                                 collection_meta=meta,initial_dbs=initial_calls,
                                 pulsar_fallback=fallback,audit_meta=audit_meta)
        analysis['findings'] = diagnostic_findings(analysis)
        bundle.write('BEFUNDE.json',analysis['findings'],essential=True)
        bundle.write('AUSWERTUNG.json',analysis,essential=True)
        bundle.write('BERICHT.md',render_report(analysis,meta),essential=True)
        bundle.write('ZUSAMMENFASSUNG.json',summary_document(analysis,meta),essential=True)
    except Exception as exc:
        meta['completion'] = 'PARTIAL_ERROR'
        meta['errors'].append(error_record(exc))
        bundle.write('BERICHT.md','# Teilbericht\nDie automatische Zusammenfassung war unvollstaendig. META.json und vorhandene Belege pruefen.\n',essential=True)
    bundle.write('META.json',meta,essential=True)
    bundle.write('FEHLER.json',meta['errors'],essential=True)
    try:
        report('ARCHIVING')
        archive = bundle.finish()
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        print('ZIP konnte nicht abgeschlossen werden: '+str(exc),file=sys.stderr)
        print('Gesammelte Belege bleiben erhalten: '+str(bundle.work),file=sys.stderr)
        return 2
    print('\nDIAGNOSE-ZIP ERSTELLT:\n'+str(archive)+'\nDiese ZIP zur Auswertung hochladen.',flush=True)
    report('ARCHIVE_READY', archive=str(archive), completion=meta['completion'])
    if meta['completion'] != 'COMPLETED':
        print('Teilbericht: '+meta['completion']+'; Hinweise in META.json.',flush=True)
    return 0 if meta['completion'] in {'COMPLETED','INTERRUPTED_PARTIAL'} else 1


if __name__ == '__main__':
    raise SystemExit(main())
