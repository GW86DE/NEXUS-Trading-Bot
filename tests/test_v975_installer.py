"""Exercise installer transactions using a local filesystem and simulated systemd.

The fake host never executes sudo, pip, a broker call, or a live service.
"""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace as NS
import time

import pytest

from nexus_update import Installer, UpdateError, UNITS, STATE, determine_source, fresh_runtime, check_demo_config


def make_root(path):
    path.mkdir()
    (path/'VERSION.txt').write_text('9.7.5-NEXUS\n')
    (path/'MANIFEST_SHA256.json').write_text(json.dumps({'VERSION.txt':hashlib.sha256((path/'VERSION.txt').read_bytes()).hexdigest()}))
    return path


class FakeHost:
    def __init__(self, source):
        self.calls=[];self.source=source
        self.current={u:dict(WorkingDirectory=str(source),ExecStart=str(source/'python'),
                            ActiveState='active',UnitFileState='enabled',FragmentPath='/etc/systemd/system/'+u) for u in UNITS}
    def command(self,args,**kwargs):
        args=[str(x) for x in args];self.calls.append(args)
        if args[:3]==['sudo','systemctl','start']:
            self.current[args[3]]['ActiveState']='active'
        if args[:3] in (['sudo','systemctl','disable'], ['sudo','systemctl','enable']):
            for unit in (x for x in args[3:] if x in self.current):
                self.current[unit]['UnitFileState'] = ('disabled' if args[2]=='disable' else
                    ('enabled-runtime' if '--runtime' in args else 'enabled'))
        if args[:2]==['sudo','install']:
            text=Path(args[-2]).read_text()
            unit=Path(args[-1]).name
            wd=next(x.split('=',1)[1] for x in text.splitlines() if x.startswith('WorkingDirectory=')) if 'WorkingDirectory=' in text else str(self.source)
            self.current[unit].update(WorkingDirectory=wd,ExecStart=wd+'/python')
        return ''
    def states(self):return deepcopy(self.current)
    def stop(self):
        self.calls.append(['STOP'])
        for s in self.current.values():s['ActiveState']='inactive'
    def other_writers(self,source):return None


@pytest.fixture
def setup(tmp_path):
    target=make_root(tmp_path/'new');source=make_root(tmp_path/'old')
    (source/'handelsmodus.txt').write_text('paper\n')
    (source/'okx_credentials.json').write_text(json.dumps({'live_trading':False}))
    (source/'ledger.txt').write_text('unchanged financial proof')
    return target,source,FakeHost(source)


class Transaction(Installer):
    """Real run/rollback code; replace ONLY OS/external preparation and services."""
    fail=None
    def step(self,name):
        self.phase(name)
        if self.fail==name:raise UpdateError('injected '+name)
    def prepare(self):self.step('PREPARE')
    def save_source(self,source):
        self.step('BACKUP');self.stopped=True;self.host.stop()
        self.backup=self.root.parent/'backup';self.backup.mkdir()
        for u in UNITS:(self.backup/u).write_text('old unit')
        (self.backup/'financial.txt').write_bytes((source/'ledger.txt').read_bytes())
    def migrate(self,source):
        self.stage=self.root.parent/'stage';self.stage.mkdir()
        destination=self.root/'ledger.txt'
        destination.write_bytes((source/'ledger.txt').read_bytes())
        self.promoted.append(destination);self.info['promoted']=['ledger.txt']
        self.step('MIGRATE')
    def install_units(self):
        self.units_changed=True
        for u in UNITS:self.host.current[u].update(WorkingDirectory=str(self.root),ExecStart=str(self.root/'python'))
        self.step('UNITS')
    def start_and_check(self):
        self.start_boundary=True;self.info['start_boundary']=True
        (self.root/'ledger.txt').write_text('new fill committed')
        self.step('START')
        self.phase('ERFOLGREICH')


@pytest.mark.parametrize('phase',['PREPARE','BACKUP','MIGRATE','UNITS'])
def test_failure_before_start_preserves_source_and_restores_activity(setup,phase):
    target,source,host=setup;t=Transaction(target,host);t.fail=phase
    before=(source/'ledger.txt').read_bytes()
    with pytest.raises(UpdateError):t.run()
    assert (source/'ledger.txt').read_bytes()==before
    assert all(s['WorkingDirectory']==str(source) for s in host.states().values())
    assert all(s['ActiveState']=='active' for s in host.states().values())
    assert not (target/'ledger.txt').exists()
    if phase=='PREPARE':assert not any(c==['STOP'] for c in host.calls)


def test_failure_after_start_never_restores_old_trading_state(setup):
    target,source,host=setup;t=Transaction(target,host);t.fail='START'
    with pytest.raises(UpdateError):t.run()
    assert (target/'ledger.txt').read_text()=='new fill committed'
    assert (source/'ledger.txt').read_text()=='unchanged financial proof'
    assert all(s['WorkingDirectory']==str(target) for s in host.states().values())
    assert all(s['ActiveState']=='inactive' for s in host.states().values())
    assert ['sudo','systemctl','disable',*UNITS] in host.calls
    assert not any(x[:3]==['sudo','systemctl','start'] for x in host.calls)


def test_successful_repeat_does_not_import_or_restart(setup):
    target,source,host=setup;t=Transaction(target,host);t.run()
    before=list(host.calls);data=(target/'ledger.txt').read_bytes()
    Transaction(target,host).run()
    assert host.calls==before and (target/'ledger.txt').read_bytes()==data


def test_interrupted_start_refuses_second_import(setup):
    target,source,host=setup
    (target/STATE).write_text(json.dumps({'phase':'FEHLGESCHLAGEN','start_boundary':True}))
    with pytest.raises(UpdateError,match='bereits Dienste gestartet'):Transaction(target,host).run()
    assert not host.calls


@pytest.mark.parametrize('phase',['2_DIENSTE_STOPPEN_UND_SICHERN','3_ZUSTAND_UEBERNEHMEN_UND_BELEGT_REPARIEREN','4_DIENSTE_UMSTELLEN'])
def test_crash_between_phases_does_not_guess_data_source(setup,phase):
    target,source,host=setup;(target/STATE).write_text(json.dumps({'phase':phase}))
    with pytest.raises(UpdateError,match='Unterbrochenes'):Transaction(target,host).run()
    assert not host.calls


@pytest.mark.parametrize('kind',['roots','exec','transition','explicit'])
def test_conflicting_service_identity_blocks_before_any_stop(setup,kind):
    target,source,host=setup
    if kind=='roots':host.current[UNITS[1]]['WorkingDirectory']=str(target)
    elif kind=='exec':host.current[UNITS[1]]['ExecStart']='/wrong/python'
    elif kind=='transition':host.current[UNITS[1]]['ActiveState']='activating'
    with pytest.raises(UpdateError):Transaction(target,host).run(target if kind=='explicit' else None)
    assert not host.calls


@pytest.mark.parametrize('mode,okx',[('live',False),('paper',True),('paper',None)])
def test_live_or_unknown_environment_never_switched_to_demo_silently(setup,mode,okx):
    target,source,host=setup;(source/'handelsmodus.txt').write_text(mode)
    (source/'okx_credentials.json').write_text(json.dumps({'live_trading':okx}))
    with pytest.raises(UpdateError):Transaction(target,host).run()
    assert not host.calls


def test_existing_target_state_is_not_overwritten(setup):
    target,source,host=setup;(target/'crypto_positions.json').write_text('preserve')
    with pytest.raises(UpdateError,match='lokale Dateien'):Transaction(target,host).run()
    assert (target/'crypto_positions.json').read_text()=='preserve' and not host.calls


def test_code_hash_failure_blocks_before_service_actions(setup):
    target,source,host=setup;(target/'VERSION.txt').write_text('edited')
    with pytest.raises(UpdateError,match='hash'):Transaction(target,host).run()
    assert not host.calls


def test_plan_is_readonly_and_names_both_paths(setup,capsys):
    target,source,host=setup;Transaction(target,host).run(plan=True)
    assert str(source) in capsys.readouterr().out and not host.calls and not (target/STATE).exists()


@pytest.mark.parametrize('change',[
    {'running':False},{'broker_connected':False},{'broker':'etoro'},{'mode':'LIVE'},
    {'state':'ACCOUNTING_RECOVERY_REQUIRED'},{'last_heartbeat':'nonsense'},
    {'last_heartbeat':'2000-01-01T00:00:00+00:00'}])
def test_stale_or_other_domain_runtime_never_satisfies_health(change):
    now=time.time();d=dict(running=True,broker_connected=True,broker='okx',mode='DEMO',state='RUNNING',last_heartbeat=datetime.now(timezone.utc).isoformat())
    assert fresh_runtime(d,now-1,now,'okx')
    d.update(change);assert not fresh_runtime(d,now-1,now,'okx')


def test_installer_calls_real_dependency_checks_before_stop(setup,monkeypatch):
    target,source,host=setup
    (target/'.venv/bin').mkdir(parents=True);(target/'.venv/bin/python').write_text('synthetic')
    t=Installer(target,host);t.prepare()
    scripts=[c[1] for c in host.calls if c and c[0].endswith('/python') and len(c)>1]
    assert 'check_runtime_dependencies.py' in scripts and 'check_test_dependencies.py' in scripts and 'volltest.py' in scripts
    assert not any(c==['STOP'] for c in host.calls)


def test_real_offline_test_failure_leaves_previous_installation_running(setup):
    """Pi-Abbruch: echter prepare/run-Pfad, nur OS-Befehle sind Testdoubles."""
    target, source, host = setup
    (target/'.venv/bin').mkdir(parents=True)
    (target/'.venv/bin/python').write_text('synthetic')
    before = {p.name:p.read_bytes() for p in source.iterdir() if p.is_file()}
    command = host.command

    def failing_command(args, **kwargs):
        result = command(args, **kwargs)
        if len(args)>1 and str(args[1])=='volltest.py':
            raise UpdateError('sieben fehlgeschlagene Regressionstests')
        return result

    host.command = failing_command
    installer = Installer(target, host)
    with pytest.raises(UpdateError, match='sieben fehlgeschlagene'):
        installer.run()
    assert {p.name:p.read_bytes() for p in source.iterdir() if p.is_file()} == before
    assert all(s['ActiveState']=='active' and s['WorkingDirectory']==str(source)
               for s in host.states().values())
    assert not installer.stopped and not installer.start_boundary
    assert not installer.promoted and installer.backup is None
    assert not any(c==['STOP'] or 'systemctl' in c for c in host.calls)
    assert installer.info['failed_phase']=='1_VORBEREITUNG_UND_OFFLINE_TEST'


def test_desktop_rollback_restores_old_and_preserves_new_file(setup,tmp_path):
    target,source,host=setup;t=Installer(target,host);t.backup=tmp_path/'backup';t.backup.mkdir()
    old=tmp_path/'existing.desktop';old.write_text('new');(t.backup/old.name).write_text('original')
    new=tmp_path/'new.desktop';new.write_text('new')
    t.desktop_changes=[(old,True),(new,False)];t.rollback_prestart()
    assert old.read_text()=='original' and not new.exists()
    assert (t.backup/'nicht_aktiv_new.desktop').read_text()=='new'


def test_okx_native_runtime_schema_is_accepted_but_verbunden_alone_is_not():
    now=time.time()
    d=dict(running=True,online=True,verbunden=True,modus='DEMO',last_heartbeat=datetime.now(timezone.utc).isoformat())
    assert fresh_runtime(d,now-1,now,'okx')
    d['online']=False
    assert not fresh_runtime(d,now-1,now,'okx')


def test_stale_old_configured_status_cannot_pass_new_start_boundary():
    now=time.time()
    d=dict(running=True,online=True,modus='DEMO',last_heartbeat=datetime.fromtimestamp(now-10,timezone.utc).isoformat())
    assert not fresh_runtime(d,now,now,'okx')


def test_real_template_rendering_does_not_start_core_and_updates_all_desktop_links(setup,tmp_path,monkeypatch):
    target,source,host=setup
    production=Path(__file__).resolve().parents[1]
    import shutil
    for p in production.glob('*.service.template'):shutil.copy2(p,target/p.name)
    for p in production.glob('*.desktop.template'):shutil.copy2(p,target/p.name)
    fakehome=tmp_path/'home';fakehome.mkdir();monkeypatch.setattr(Path,'home',classmethod(lambda cls:fakehome))
    host.stop()
    host.command(['sudo','systemctl','disable',*UNITS])
    t=Installer(target,host);t.backup=tmp_path/'backup';t.backup.mkdir();t.install_units()
    assert all(x['WorkingDirectory']==str(target) for x in host.states().values())
    files=list((fakehome/'Desktop').glob('*.desktop'));assert len(files)==5
    assert all(str(target) in p.read_text() for p in files)
    assert not any('start' in c or '--now' in c for c in host.calls)
    for p in t.backup.glob('neu_*.service'):
        assert '__BOTDIR__' not in p.read_text() and '__USER__' not in p.read_text()
        assert '9.7.5' in p.read_text()


def test_health_phase_demands_each_enabled_broker_not_any_one(setup,tmp_path,monkeypatch):
    import nexus_update as nu
    target,source,host=setup;t=Installer(target,host)
    (target/'web_ui_settings.json').write_text(json.dumps({'bind_host':'127.0.0.1','port':8780}))
    class Page:
        def __enter__(self):return self
        def __exit__(self,*a):return None
        def read(self):return b'NEXUS 9.7.5'
    monkeypatch.setattr(nu.urllib.request,'build_opener',lambda *_:NS(open=lambda *a,**k:Page()))
    t.info['config']={'etoro':True,'okx':True,'paper':True,'okx_live':False}
    times=iter([0,0,1000]);monkeypatch.setattr(nu.time,'monotonic',lambda:next(times))
    monkeypatch.setattr(nu.time,'sleep',lambda _:None)
    stamp=datetime.now(timezone.utc).isoformat()
    (target/'runtime_status_okx.json').write_text(json.dumps(dict(running=True,online=True,modus='DEMO',last_heartbeat=stamp)))
    with pytest.raises(UpdateError,match='nicht alle'):t.start_and_check()
    assert t.start_boundary
