"""One-command, evidence-preserving DEMO upgrade for an existing Pi installation.

No live arming, no remote broker request before the explicit service-start phase.
No old-state rollback after any new service has started and could accept commands.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import platform
import pwd
import grp
import re
import shutil
import subprocess
import sqlite3
from contextlib import closing
import sys
import tarfile
import tempfile
import time
import urllib.request
from datetime import datetime, timezone

UNITS = ('tradingbot-pi5.service', 'tradingbot-webui.service')
STATE = 'nexus_update_state.json'


class UpdateError(RuntimeError):
    pass


def write_json(path, data):
    path = Path(path)
    temp = path.with_name(path.name+'.tmp')
    with temp.open('w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.flush(); os.fsync(f.fileno())
    temp.chmod(0o600)
    os.replace(temp, path)


def safe_root(path):
    p = Path(path)
    if p.is_symlink() or not re.fullmatch(r'/[A-Za-z0-9_./-]+', str(p)):
        raise UpdateError('Pfad muss absolut, ohne Symlink, Leer-/Sonderzeichen sein')
    if p.resolve() != p or not p.is_dir():
        raise UpdateError('Gueltiger existierender Installationsordner erforderlich')
    return p


def determine_source(states, target, explicit=None):
    roots = {s.get('WorkingDirectory', '') for s in states.values()}
    if '' in roots or len(roots) != 1:
        raise UpdateError('Core/WebUI haben keine eindeutige gemeinsame Datenquelle. Keine Umstellung.')
    source = safe_root(roots.pop())
    if explicit is not None and Path(explicit).resolve() != source:
        raise UpdateError('--source passt nicht zu den bisherigen Diensten')
    if source == target:
        raise UpdateError('Dienste zeigen bereits auf das Ziel; kein erneuter Datenimport')
    for s in states.values():
        if s.get('ActiveState') not in {'active', 'inactive', 'failed'}:
            raise UpdateError('Dienst ist in einem Uebergangszustand; spaeter erneut versuchen')
        if str(source) not in s.get('ExecStart', ''):
            raise UpdateError('ExecStart und WorkingDirectory widersprechen sich')
        if s.get('UnitFileState') not in {'enabled', 'enabled-runtime', 'disabled'}:
            raise UpdateError('Unbekannter/gesperrter Autostartzustand; keine automatische Dienstumstellung')
    return source


def fresh_runtime(data, after, now, broker):
    try:
        stamp = datetime.fromisoformat(str(data.get('last_heartbeat') or data.get('zeit') or '').replace('Z', '+00:00'))
        ts = stamp.timestamp()
    except (ValueError, TypeError):
        return False
    connected = data.get('online') if broker == 'okx' and 'online' in data else data.get('broker_connected')
    mode = data.get('modus') if broker == 'okx' and 'modus' in data else data.get('mode')
    return (ts >= after and -10 <= now-ts <= 180
            and bool(connected) and bool(data.get('running'))
            and str(mode or '').upper() in {'PAPER','DEMO'}
            and str(data.get('broker') or broker).lower() == broker
            and str(data.get('state') or '').upper() not in {
                'STOPPED','STOPPING','OFFLINE','ERROR','ACCOUNTING_RECOVERY_REQUIRED'})


class Host:
    def command(self, args, *, cwd=None, capture=False, timeout=1800):
        # Commands contain only paths/options, NEVER credentials.
        print('+ '+ ' '.join(str(x) for x in args), flush=True)
        try:
            r = subprocess.run([str(x) for x in args], cwd=cwd, check=True,
                               stdout=subprocess.PIPE if capture else None,
                               stderr=subprocess.PIPE if capture else None,
                               text=True, encoding='utf-8', errors='replace', timeout=timeout)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            raise UpdateError('Schritt fehlgeschlagen: '+str(args[0])+' (Details im Installationsprotokoll)') from exc
        return r.stdout if capture else ''

    def states(self):
        result = {}
        for unit in UNITS:
            text = self.command(['systemctl','show',unit,'--no-pager',
                                 '-p','WorkingDirectory','-p','ExecStart','-p','ActiveState',
                                 '-p','UnitFileState','-p','FragmentPath'], capture=True, timeout=15)
            result[unit] = dict(line.split('=',1) for line in text.splitlines() if '=' in line)
        return result

    def stop(self):
        self.command(['sudo','systemctl','stop',*UNITS], timeout=180)
        if any(x.get('ActiveState') not in {'inactive','failed'} for x in self.states().values()):
            raise UpdateError('Nicht alle Dienste sind gestoppt')

    def other_writers(self, source):
        for entry in Path('/proc').iterdir():
            if not entry.name.isdigit() or int(entry.name) == os.getpid():
                continue
            try:
                cwd = (entry/'cwd').resolve()
                cmd = (entry/'cmdline').read_bytes().split(b'\0')
            except (OSError, RuntimeError):
                continue
            entrypoints = {b'nexus_start.py',b'pi_service.py',b'live_trader.py',b'webui_start.py',b'gui_app.py'}
            related_script = any(Path(os.fsdecode(x)).name.encode() in entrypoints for x in cmd[1:])
            related_cwd = source.parent == cwd.parent or cwd == source
            absolute_script = any(os.fsdecode(x).startswith(str(source.parent)+'/') for x in cmd[1:])
            if any(b'python' in x for x in cmd[:1]) and (cwd == source or (related_script and (related_cwd or absolute_script))):
                raise UpdateError('Zusaetzlicher Python-Writer im Quellordner, PID '+entry.name+
                                  '. Zugehoeriges Botfenster schliessen, nichts pauschal killen.')


def check_demo_config(source):
    # Read only the public mode flag, never print access credentials.
    mode = source/'handelsmodus.txt'
    if not mode.is_file() or mode.read_text(encoding='utf-8').strip().lower() != 'paper':
        raise UpdateError('Automatischer Upgrade-Start nur fuer bestaetigten Paper/Demo-Ausgangsstand. LIVE bleibt unveraendert.')
    from credential_store import load_credentials
    raw = load_credentials(source/'okx_credentials.json', {})
    if not isinstance(raw, dict) or raw.get('live_trading') is not False:
        raise UpdateError('OKX-Demo ist in der Quelle nicht eindeutig bestaetigt. Keine automatische Kontoumschaltung.')


def sealed_state_files(stage):
    """Seal stopped staging DBs before promotion; never move live WAL handles.

    SQLite shared-memory files are transient metadata, not persistent state.
    Promote a main database only after a successful checkpoint and integrity
    check; any outstanding WAL frames or concurrent reader/writer aborts.
    """
    databases={p.name for p in stage.iterdir() if p.name.endswith('.sqlite')}
    for name in sorted(databases):
        path=stage/name
        if not path.is_file() or path.is_symlink():
            raise UpdateError('Ungueltige Staging-Datenbank')
        with closing(sqlite3.connect(path,timeout=5)) as con:
            checkpoint=con.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()
            if checkpoint[0] or con.execute('PRAGMA quick_check').fetchone()[0]!='ok':
                raise UpdateError('Staging-Datenbank nicht vollstaendig gesichert/noch belegt: '+name)
        wal=stage/(name+'-wal')
        if wal.exists() and wal.stat().st_size:
            raise UpdateError('Staging-WAL wurde nach dem Checkpoint erneut beschrieben: '+name)
    files=[]
    for path in stage.iterdir():
        if path.is_symlink() or not path.is_file():
            raise UpdateError('Unerwarteter Eintrag im migrierten Zustand')
        if any(path.name==n+suffix for n in databases for suffix in ('-shm','-wal')):
            # Empty checkpointed WAL and SHM can disappear on last handle close.
            # They remain in staging, never deleted or treated as trade data.
            continue
        files.append(path)
    return files


class Installer:
    def __init__(self, root, host=None, *, receipts=None, verified_trades=None, verified_fx=None, fmp_starter=False, okx_neues_konto=False):
        if bool(verified_trades) != bool(verified_fx):
            raise UpdateError('Historische Handels- und FX-Belege werden gemeinsam benoetigt')
        for item in (verified_trades, verified_fx):
            if item and Path(item).expanduser().is_symlink():
                raise UpdateError('Historische Belegdatei darf kein Symlink sein')
        self.okx_neues_konto = bool(okx_neues_konto)
        self.fmp_starter = bool(fmp_starter)
        self.verified_trades = Path(verified_trades).expanduser().resolve() if verified_trades else None
        self.verified_fx = Path(verified_fx).expanduser().resolve() if verified_fx else None
        if receipts and Path(receipts).expanduser().is_symlink():
            raise UpdateError('Belegdatei darf kein Symlink sein')
        self.receipts = Path(receipts).expanduser().resolve() if receipts else None
        self.root = safe_root(root)
        self.version = (self.root/'VERSION.txt').read_text(encoding='utf-8').strip()
        self.host = host or Host()
        self.journal = self.root/STATE
        self.info = {'version': self.version, 'phase': 'NEW', 'started': time.time()}
        self.promoted = []
        self.start_boundary = False
        self.stopped = False
        self.old_states = {}
        self.backup = None
        self.stage = None
        self.units_changed = False
        self.desktop_changes = []
        self.autostart_change_attempted = False

    def phase(self, name):
        self.info['phase'] = name
        write_json(self.journal, self.info)
        print('\n=== '+name+' ===', flush=True)

    def assert_target_source_only(self, boundary):
        """No runtime state may be created before promotion, even by preflight.

        Do not delete or "recognize as empty" an unexpected database. Its
        provenance cannot be proven just from its size or schema. Abort with
        the process boundary that produced the first unexpected file.
        """
        from offline_validation import read_manifest, extra_files
        allowed = {STATE, 'nexus_update.log', '.nexus_update.lock'}
        occupied = sorted(p.relative_to(self.root).as_posix()
                          for p in extra_files(self.root, read_manifest(self.root))
                          if p.relative_to(self.root).as_posix() not in allowed)
        if occupied:
            raise UpdateError('Zielzustand ist bereits belegt (' + boundary + '): '
                              + ', '.join(occupied[:10])
                              + '. Keine Datei geloescht oder ueberschrieben.')

    def prepare(self):
        self.phase('1_VORBEREITUNG_UND_OFFLINE_TEST')
        # Obtain sudo authority while the old service is still running.
        self.host.command(['sudo','-v'],timeout=120)
        py = self.root/'.venv/bin/python'
        if (self.root/'.venv').is_symlink():
            raise UpdateError('Keine verlinkte/shared .venv beim Upgrade')
        if not py.exists():
            # Existing Pi installs already have venv. If missing, install only
            # required OS packages, never upgrade the operating system.
            try:
                self.host.command(['python3','-c','import venv,ensurepip'],capture=True)
            except UpdateError:
                self.host.command(['sudo','apt-get','update'],timeout=900)
                self.host.command(['sudo','apt-get','install','-y','python3-venv'],timeout=900)
            self.host.command(['python3','-m','venv',self.root/'.venv'], cwd=self.root)
        if not shutil.which('node'):
            self.host.command(['sudo','apt-get','update'],timeout=900)
            self.host.command(['sudo','apt-get','install','-y','nodejs'],timeout=900)
        self.host.command([py,'-m','pip','install','--only-binary=:all:',
                           '-r','requirements-lock.txt','-r','requirements-test.txt'], cwd=self.root)
        self.host.command([py,'-m','pip','check'], cwd=self.root)
        # Not merely pytest imports: real runtime libraries must be installed.
        self.host.command([py,'check_runtime_dependencies.py'], cwd=self.root)
        self.host.command([py,'check_test_dependencies.py'], cwd=self.root)
        self.host.command([py,'volltest.py'], cwd=self.root, timeout=1800)
        self.host.command([py,'pi_preflight.py','--service'], cwd=self.root, timeout=60)

    def save_source(self, source):
        self.phase('2_DIENSTE_STOPPEN_UND_SICHERN')
        # Record before stop: a failure half way through must restore previous
        # activity before the service-start boundary, not leave an unnoticed halt.
        self.stopped = True
        self.host.stop()
        # stop alone does not remove WantedBy symlinks. A power loss after
        # replacing only one unit could otherwise boot mixed source/target
        # services before the persisted start boundary. Disable reboot starts
        # before any migration/unit replacement; preserve prior enable states.
        self.autostart_change_attempted = True
        self.info['autostart_change_attempted'] = True
        self.info['old_states'] = self.old_states
        write_json(self.journal, self.info)
        self.host.command(['sudo', 'systemctl', 'disable', *UNITS])
        runtime_enabled = [u for u, s in self.old_states.items() if s.get('UnitFileState') == 'enabled-runtime']
        if runtime_enabled:
            self.host.command(['sudo', 'systemctl', 'disable', '--runtime', *runtime_enabled])
        self.assert_services_disarmed()
        self.host.other_writers(source)
        self.backup = self.root.parent/('NEXUS_Sicherung_'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S_%f'))
        self.backup.mkdir(mode=0o700)
        self.info['backup'] = str(self.backup)
        self.info['old_states'] = self.old_states
        write_json(self.journal, self.info)
        archive = self.backup/'alter_zustand.tar.gz'
        def filt(member):
            parts = Path(member.name).parts
            if any(x in {'.venv','__pycache__','.pytest_cache','.git'} for x in parts):
                return None
            if member.issym() or member.islnk():
                raise UpdateError('Symlink im zu sichernden Zustand; manuelle Pruefung erforderlich')
            return member
        with tarfile.open(archive,'w:gz') as tar:
            tar.add(source,arcname='source',filter=filt)
        archive.chmod(0o600)
        with tarfile.open(archive,'r:gz') as tar:
            if not tar.getmembers():
                raise UpdateError('Leere Sicherung')
            for member in tar.getmembers():
                if member.isfile():
                    stream = tar.extractfile(member)
                    while stream.read(1024*1024):
                        continue
        for unit, old in self.old_states.items():
            fp = Path(old.get('FragmentPath') or '')
            if fp != Path('/etc/systemd/system')/unit or not fp.is_file() or fp.is_symlink():
                raise UpdateError('Unerwarteter Unit-Pfad: keine automatische Ueberschreibung')
            shutil.copy2(fp,self.backup/unit)
        write_json(self.backup/'dienstzustand.json', self.old_states)

    def assert_services_disarmed(self):
        states = self.host.states()
        if any(states[u].get('UnitFileState') != 'disabled'
               or states[u].get('ActiveState') not in {'inactive', 'failed'} for u in UNITS):
            raise UpdateError('Core/WebUI muessen vor Umstellung gestoppt und rebootfest deaktiviert sein')

    def restore_previous_autostart(self):
        if not self.autostart_change_attempted:
            return
        for unit, old in self.old_states.items():
            enabled = old.get('UnitFileState')
            if enabled == 'enabled':
                command = ['sudo', 'systemctl', 'enable', unit]
            elif enabled == 'enabled-runtime':
                command = ['sudo', 'systemctl', 'enable', '--runtime', unit]
            elif enabled == 'disabled':
                command = ['sudo', 'systemctl', 'disable', unit]
            else:
                raise UpdateError('Urspruenglicher Autostartzustand kann nicht sicher wiederhergestellt werden')
            self.host.command(command)
        states = self.host.states()
        if any(states[u].get('UnitFileState') != self.old_states[u].get('UnitFileState') for u in UNITS):
            raise UpdateError('Wiederherstellung des urspruenglichen Autostartzustands unbestaetigt')

    def migrate(self, source):
        self.phase('3_ZUSTAND_UEBERNEHMEN_UND_BELEGT_REPARIEREN')
        self.assert_target_source_only('vor Zustandsuebernahme')
        self.stage = Path(tempfile.mkdtemp(prefix='.nexus_state_', dir=self.root.parent))
        self.stage.chmod(0o700)
        self.info['staging'] = str(self.stage)
        write_json(self.journal, self.info)
        py = self.root/'.venv/bin/python'
        code = ('from pathlib import Path; from settings_migration import migrate_from; '
                'import sys; migrate_from(Path(sys.argv[1]),Path(sys.argv[2]),strict=True)')
        self.host.command([py,'-c',code,source,self.stage], cwd=self.root)
        self.assert_target_source_only('nach Einstellungsuebernahme')
        if self.fmp_starter:
            code = ('from fmp_setup import starter; import sys; starter(sys.argv[1])')
            self.host.command([py,'-c',code,self.stage], cwd=self.root)
            self.info['fmp_setup'] = 'AUTO, gebucht STARTER; bestehende Kontingente bleiben erhalten'
            self.assert_target_source_only('nach FMP-Tarifvorgabe')
        if self.verified_trades:
            # Accounting needs the stopped worker's recorded account/currency.
            # This historical context must never be promoted as fresh liveness.
            context = Path(source)/'runtime_status_okx.json'
            if context.is_symlink() or not context.is_file():
                raise UpdateError('Bisheriger OKX-Laufzeitkontext fehlt fuer die historische Bewertung')
            shutil.copy2(context,self.stage/context.name)
        self.host.command([py,'repair_okx_state.py','--source',self.stage,'--apply','--workers-stopped'], cwd=self.root)
        self.assert_target_source_only('nach repair_okx_state.py')
        accounting=[py,'repair_okx_accounting.py','--source',self.stage,'--apply','--workers-stopped']
        if self.receipts:accounting += ['--receipts',self.receipts]
        self.host.command(accounting,cwd=self.root)
        self.assert_target_source_only('nach repair_okx_accounting.py')
        if self.verified_trades:
            self.host.command([py,'repair_okx_verified_history.py','--source',self.stage,
                '--trades',self.verified_trades,'--fx',self.verified_fx,'--apply','--workers-stopped'],cwd=self.root)
            self.assert_target_source_only('nach repair_okx_verified_history.py')
            (self.stage/'runtime_status_okx.json').unlink()
        if self.okx_neues_konto:
            self.host.command([py, 'okx_account_switch.py', '--okx-neues-konto',
                               '--state-dir', self.stage], cwd=self.root, timeout=600)
            self.assert_target_source_only('nach OKX-Kontowechsel')
        # Never overwrite an occupied target. A failed promotion moves only this
        # transaction's files back to staging; it never deletes user data.
        files = sealed_state_files(self.stage)
        for path in files:
            if path.is_symlink() or not path.is_file():
                raise UpdateError('Unerwarteter Eintrag im migrierten Zustand')
            if (self.root/path.name).exists() or (self.root/path.name).is_symlink():
                raise UpdateError('Zielzustand ist bereits belegt: '+path.name)
        for path in files:
            target = self.root/path.name
            self.promoted.append(target)
            self.info['promoted'] = [p.name for p in self.promoted]
            write_json(self.journal,self.info)
            os.replace(path,target)
        self.host.command([py,'check_runtime_dependencies.py'], cwd=self.root)
        self.host.command([py,'volltest.py'], cwd=self.root, timeout=1800)
        for name in ('web_ui_settings.json', 'web_ui_credentials.json',
                     'web_ui_credentials.json.dpapi'):
            path = self.root/name
            if not path.exists() and path not in self.promoted:
                self.promoted.append(path)
        self.info['promoted'] = [p.name for p in self.promoted]
        write_json(self.journal,self.info)
        self.host.command([py,'webui_network_setup.py','--mode','auto'], cwd=self.root)
        try:
            self.host.command([py,'webui_setup.py','--status'], cwd=self.root)
        except UpdateError:
            if not sys.stdin.isatty():
                raise UpdateError('WebUI-Zugang fehlt. Installer in einem interaktiven Terminal starten') from None
            self.host.command([py,'webui_setup.py'], cwd=self.root, timeout=900)
        self.host.command([py,'webui_start.py','--check'], cwd=self.root)
        code = ('import json,config; print(json.dumps({"etoro":bool(config.ETORO_ENABLED),'
                '"okx":bool(config.OKX_ENABLED),"paper":bool(config.PAPER_TRADING),'
                '"okx_live":bool(config.OKX_LIVE_TRADING)}))')
        text = self.host.command([py,'-c',code], cwd=self.root, capture=True)
        self.info['config'] = json.loads(text.strip().splitlines()[-1])
        cfg = self.info['config']
        if not cfg['paper'] or cfg['okx_live'] or not (cfg['etoro'] or cfg['okx']):
            raise UpdateError('Keine bestaetigte aktive Demo/Paper-Konfiguration')

    def install_units(self):
        self.phase('4_DIENSTE_UMSTELLEN')
        self.assert_services_disarmed()
        user = pwd.getpwuid(os.getuid()).pw_name
        group = grp.getgrgid(os.getgid()).gr_name
        for unit in UNITS:
            template = (self.root/(unit+'.template')).read_text(encoding='utf-8')
            for a,b in {'__USER__':user,'__GROUP__':group,'__BOTDIR__':str(self.root),'__VERSION__':self.version.removesuffix('-NEXUS')}.items():
                template = template.replace(a,b)
            path = self.backup/('neu_'+unit)
            path.write_text(template,encoding='utf-8')
            self.units_changed = True
            self.host.command(['sudo','install','-m','0644',path,'/etc/systemd/system/'+unit])
        self.host.command(['sudo','systemctl','daemon-reload'])
        states = self.host.states()
        if any(s.get('WorkingDirectory') != str(self.root) or str(self.root) not in s.get('ExecStart','') for s in states.values()):
            raise UpdateError('Effektiver Dienstpfad widerspricht dem Ziel (z.B. Drop-in). Kein Start.')
        # Existing desktop links must not point at the old core after upgrade.
        desktop = Path.home()/('Schreibtisch' if (Path.home()/'Schreibtisch').is_dir() else 'Desktop')
        desktop.mkdir(exist_ok=True)
        for name in ('TradingBot_GUI','TradingBot_WebUI','TradingBot_Starten','TradingBot_Stoppen','TradingBot_Status'):
            path = desktop/(name+'.desktop')
            if path.is_symlink():
                raise UpdateError('Desktop-Starter ist ein Symlink; keine Ueberschreibung')
            present = path.exists()
            if present:
                shutil.copy2(path,self.backup/path.name)
            self.desktop_changes.append((path,present))
            text = (self.root/(name+'.desktop.template')).read_text(encoding='utf-8').replace('__BOTDIR__',str(self.root))
            path.write_text(text,encoding='utf-8'); path.chmod(0o700)

    def start_and_check(self):
        self.phase('5_WEBUI_UND_DEMO_CORE_STARTEN')
        self.start_boundary = True
        self.info['start_boundary'] = True
        self.info['new_start_time'] = time.time()
        write_json(self.journal,self.info)
        self.host.command(['sudo','systemctl','enable','--now','tradingbot-webui.service'])
        settings = json.loads((self.root/'web_ui_settings.json').read_text(encoding='utf-8'))
        host = str(settings.get('bind_host') or '127.0.0.1')
        if not ipaddress.ip_address(host).is_private:
            raise UpdateError('WebUI-Startkontrolle nur an privater Adresse')
        url = f"http://{'['+host+']' if ':' in host else host}:{int(settings.get('port') or 8780)}/login"
        deadline = time.monotonic()+40
        while True:
            try:
                # No proxies: this is an on-device health request.
                with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(url,timeout=3) as r:
                    text = r.read().decode('utf-8')
                if self.version.removesuffix('-NEXUS') in text:
                    break
            except (OSError, ValueError):
                text = ''
            if time.monotonic()>deadline:
                raise UpdateError('Neue WebUI-Version nicht erreichbar')
            time.sleep(2)
        self.host.command(['sudo','systemctl','enable','--now','tradingbot-pi5.service'],timeout=360)
        self.phase('6_FRISCHE_BROKER_HEARTBEATS_PRUEFEN')
        deadline = time.monotonic()+360
        required = [('etoro','runtime_status.json'),('okx','runtime_status_okx.json')]
        while True:
            healthy = True
            for broker,name in required:
                if not self.info['config'][broker]:
                    continue
                try:
                    data = json.loads((self.root/name).read_text(encoding='utf-8'))
                except (OSError,ValueError):
                    data = {}
                healthy = healthy and fresh_runtime(data,self.info['new_start_time'],time.time(),broker)
            if healthy and all(x.get('ActiveState')=='active' for x in self.host.states().values()):
                break
            if time.monotonic()>deadline:
                raise UpdateError('Startkontrolle: nicht alle aktivierten Broker bestaetigen frische Verbindung')
            time.sleep(5)
        self.info['webui'] = url.removesuffix('/login')
        self.phase('ERFOLGREICH')
        print('UPDATE UND STARTKONTROLLE ERFOLGREICH: '+self.info['webui'])
        print('Demo/Paper aktiv. Eine fachliche Kaufsperre kann bei offenen Belegen weiterhin korrekt sein.')

    def rollback_prestart(self):
        if self.start_boundary:
            # New writes/commands could exist. Never restore old trading state.
            self.host.stop()
            self.host.command(['sudo','systemctl','disable',*UNITS])
            print('Neue Dienste angehalten. KEIN automatischer Rueckfall nach Start; neueste Daten bleiben erhalten. '
                  'Client-Stops laufen jetzt nicht; Brokerschutz direkt kontrollieren.')
            return
        if self.units_changed and self.backup:
            for unit in UNITS:
                self.host.command(['sudo','install','-m','0644',self.backup/unit,'/etc/systemd/system/'+unit])
            self.host.command(['sudo','systemctl','daemon-reload'])
        for path,present in reversed(self.desktop_changes):
            if present:
                shutil.copy2(self.backup/path.name,path)
            elif path.exists():
                os.replace(path,self.backup/('nicht_aktiv_'+path.name))
        if self.stage:
            for path in self.promoted:
                if path.exists():
                    os.replace(path,self.stage/path.name)
        self.restore_previous_autostart()
        if self.stopped:
            for unit, state in self.old_states.items():
                if state.get('ActiveState')=='active':
                    self.host.command(['sudo','systemctl','start',unit])

    def run(self, explicit=None, *, plan=False):
        from offline_validation import read_manifest, _safe_file, digest, extra_files
        manifest = read_manifest(self.root)
        # Port the documented 9.7.5 Installer FIX1 invariant to a fresh release.
        # Runtime state may never collide with the immutable source manifest.
        from settings_migration import PERSISTENT_FILES
        collisions = sorted(set(manifest).intersection(PERSISTENT_FILES))
        if collisions:
            raise UpdateError('Release enthaelt Laufzeitzustand im Quellmanifest: '
                              + ', '.join(collisions))
        for name, expected in manifest.items():
            if digest(_safe_file(self.root,name)) != expected:
                raise UpdateError('Quellhash ungueltig: '+name)
        if not re.fullmatch(r'\d+\.\d+\.\d+-NEXUS', self.version):
            raise UpdateError('Ungueltige Release-Version')
        current = self.host.states()
        if self.journal.exists():
            prior = json.loads(self.journal.read_text(encoding='utf-8'))
            if prior.get('phase') not in {'NEW','1_VORBEREITUNG_UND_OFFLINE_TEST','FEHLGESCHLAGEN','ERFOLGREICH'}:
                raise UpdateError('Unterbrochenes Update erkannt. Sicherung/Journal erhalten; kein automatischer zweiter Import.')
            if prior.get('phase')=='ERFOLGREICH' and all(s.get('WorkingDirectory')==str(self.root) for s in current.values()):
                print('Dieser Stand ist bereits installiert. Keine zweite Migration/kein Neustart.'); return
            if prior.get('start_boundary'):
                raise UpdateError('Frueherer Lauf hat bereits Dienste gestartet. Kein blinder zweiter Import.')
            if prior.get('promoted') and any((self.root/n).exists() for n in prior['promoted']):
                raise UpdateError('Unterbrochene Zustandsuebernahme: Daten erhalten, nicht automatisch ueberschreiben')
        source = determine_source(current,self.root,explicit)
        self.old_states = current
        check_demo_config(source)
        occupied = [p.name for p in extra_files(self.root,manifest)
                    if p.name not in {STATE,'nexus_update.log','.nexus_update.lock'}]
        if occupied:
            raise UpdateError('Ziel hat bereits lokale Dateien: '+', '.join(occupied[:10]))
        if self.receipts:
            from okx_receipt_import import read_bundle,extract
            extract(read_bundle(self.receipts)[0])
            self.info['receipt_source_hash']=hashlib.sha256(self.receipts.read_bytes()).hexdigest()
        if self.verified_trades:
            from repair_okx_verified_history import inputs, TRADE_ARCHIVE_HASH, FX_ARCHIVE_HASH
            inputs(self.verified_trades,self.verified_fx)
            self.info['verified_history_sources'] = {'trades':TRADE_ARCHIVE_HASH,'fx':FX_ARCHIVE_HASH}
        self.info['source'] = str(source)
        self.info['old_states'] = current
        if plan:
            print(json.dumps({'source':str(source),'target':str(self.root),
                              'mode':'DEMO/Paper','will_start_services':True},indent=2)); return
        size = sum(p.stat().st_size for p in source.rglob('*') if p.is_file()
                   and not any(x in {'.venv','__pycache__','.git'} for x in p.relative_to(source).parts))
        if shutil.disk_usage(self.root.parent).free < size*3+512*1024*1024:
            raise UpdateError('Nicht genug Platz fuer Sicherung und Staging')
        try:
            self.prepare()
            self.assert_target_source_only('nach Vorpruefung, vor Dienststopp')
            self.save_source(source)
            self.migrate(source)
            self.install_units()
            self.start_and_check()
        except BaseException:
            try:
                self.rollback_prestart()
            except Exception as exc:
                print('ZUSAETZLICHER RUECKNAHMEFEHLER: '+type(exc).__name__+'; Dienstpfade pruefen.')
            self.info['failed_phase'] = self.info['phase']
            self.phase('FEHLGESCHLAGEN')
            # 10.2.0: Ein Fehlschlag im Offline-Test raeumt das eigene Staging
            # selbst weg (Ursache des zweiten Rev-2-Abbruchs am 16.09.2026:
            # der verwaiste Rev-1-Zielstand blockierte die Neuinstallation).
            self._archive_failed_staging()
            raise

    def _archive_failed_staging(self):
        """Eigenes, nachweislich inaktives Staging nach Phase-1-Fehlschlag archivieren.

        Nur wenn KEIN Dienst je aus diesem Verzeichnis lief (WorkingDirectory
        der installierten Units zeigt woandershin), nichts promotet wurde und
        die Dienste nie gestoppt wurden. Das ALT_-Praefix stellt sicher, dass
        die TradingBot_v*-Quellsuche den Ordner nie wieder als Quelle waehlt.
        Journal und Logs bleiben im archivierten Ordner erhalten.
        """
        try:
            if self.stopped or self.start_boundary or self.promoted:
                return
            if str(self.info.get('failed_phase') or '') != '1_VORBEREITUNG_UND_OFFLINE_TEST':
                return
            states = self.old_states or {}
            if not states or any(str(s.get('WorkingDirectory') or '') == str(self.root)
                                 for s in states.values()):
                return
            # Unerwartete Dateien im Staging (z. B. eine fremde Datenbank) sind
            # Beweismittel ungeklaerter Herkunft: sie bleiben AM ORT liegen und
            # der Nutzer prueft von Hand -- der v9.8.8-Vertrag "Keine Datei
            # geloescht oder ueberschrieben" gilt auch fuers Umbenennen. Nur
            # ein sauberes Staging, dessen Offline-Test scheiterte, raeumt
            # sich selbst weg. Jeder Pruefzweifel laesst alles unangetastet.
            from offline_validation import extra_files, read_manifest
            erlaubt = {STATE, 'nexus_update.log', '.nexus_update.lock'}
            unerwartet = [p for p in extra_files(self.root, read_manifest(self.root))
                          if p.relative_to(self.root).as_posix() not in erlaubt]
            if unerwartet:
                print('Staging bleibt zur Pruefung liegen (unerwartete Dateien, '
                      'nichts wird verschoben): '
                      + ', '.join(p.relative_to(self.root).as_posix()
                                  for p in unerwartet[:5]))
                return
            stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')
            ziel = self.root.parent / f'ALT_FEHLGESCHLAGEN_{stamp}_{self.root.name}'
            os.rename(self.root, ziel)
            print('Fehlgeschlagenes Staging archiviert: ' + str(ziel)
                  + '. Der naechste Installationsversuch startet sauber; '
                  'Journal und Logs liegen im archivierten Ordner.')
        except Exception as exc:
            # Die Archivierung ist Komfort und darf den urspruenglichen
            # Fehlschlag niemals ueberdecken (auch kein kaputtes Manifest).
            print('Staging konnte nicht archiviert werden: ' + str(exc)
                  + '. Vor dem naechsten Versuch von Hand umbenennen (ALT_-Praefix).')


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--source',type=Path)
    ap.add_argument('--receipts',type=Path,help='Optionaler eigener Original-Belegexport; nur lesender Import im gestoppten Staging')
    ap.add_argument('--verified-trades',type=Path,help='Gepruefter historischer Originalexport vom 12. September 18:03')
    ap.add_argument('--verified-fx',type=Path,help='Dazugehoerige historische Kursbelege vom 12. September 18:34')
    ap.add_argument('--fmp-starter',action='store_true',help='Vom Nutzer gebuchtes Starter-Abo mit automatischem Free-Rueckfall einrichten')
    ap.add_argument('--okx-neues-konto', action='store_true', help='Neues Demokonto interaktiv pruefen, alten OKX-Zustand archivieren')
    ap.add_argument('--plan',action='store_true',help='Nur Quelle/Ziel anzeigen, keine Aenderung')
    args=ap.parse_args()
    if os.geteuid()==0:
        raise SystemExit('Nicht mit sudo starten. Normaler Benutzer; sudo nur fuer systemd/apt.')
    if platform.system()!='Linux' or platform.machine() not in {'aarch64','arm64'}:
        raise SystemExit('Automatisches Update erfordert Raspberry Pi OS 64-Bit/ARM64')
    if sys.version_info<(3,11):
        raise SystemExit('Python >=3.11 erforderlich')
    root=Path(__file__).resolve().parent
    os.umask(0o077)
    with (root/'.nexus_update.lock').open('a') as lock:
        try: fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError: raise SystemExit('Ein Update laeuft bereits') from None
        try:
            Installer(root,receipts=args.receipts,verified_trades=args.verified_trades,
                      verified_fx=args.verified_fx,fmp_starter=args.fmp_starter,okx_neues_konto=args.okx_neues_konto).run(args.source,plan=args.plan)
        except Exception as exc:
            print('UPDATE ABGEBROCHEN: '+str(exc),file=sys.stderr)
            return 1
    return 0


if __name__=='__main__':
    raise SystemExit(main())
