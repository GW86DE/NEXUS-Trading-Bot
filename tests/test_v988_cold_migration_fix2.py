"""Cold installer processes must not create a ledger in the release directory.

Unlike the earlier in-process StagingHost test these launch the real CLI with
no TRADINGBOT_TEST_STATE_DIR. Only package installation, service/network/UI
configuration boundaries are simulated; SQLite migration and repairs execute
in fresh Python children under the real offline network guard.
"""
from __future__ import annotations

from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest
import offline_validation
from ledger_database_scope import DatabaseScopeError, current_path, existing_database
from test_v988_import_migration_ui import bundle
from test_v987_closed_results import closed
from test_v975_execution_and_repair import engine
from test_v975_installer import FakeHost, make_root, setup
from nexus_update import Installer, UpdateError

ROOT = Path(__file__).resolve().parents[1]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def cold_env(root, scratch):
    scratch.mkdir(exist_ok=True)
    env = {k: v for k, v in os.environ.items()
           if k in offline_validation.ALLOW_ENV}
    env.update(HOME=str(scratch), PYTHONUTF8='1', PYTHONIOENCODING='utf-8:strict',
               PYTHONDONTWRITEBYTECODE='1', PYTHONPATH=str(root/'offline_test_bootstrap'),
               NEXUS_OFFLINE_TEST_ROOT=str(root))
    # This absence, and the absence of pre-imported Python modules, are the regression.
    assert 'TRADINGBOT_TEST_STATE_DIR' not in env
    return env


def copy_release(path):
    offline_validation.prepare_copy(ROOT, path, allow_local_state=True)
    return path


def run_cold(root, args, scratch, *, cwd=None):
    result = subprocess.run([sys.executable, *map(str, args)], cwd=cwd or root,
        env=cold_env(root, scratch), capture_output=True, text=True,
        encoding='utf-8', errors='strict', timeout=60)
    return result


def cold_state(bundle, parent):
    import decision_analytics as da
    src = parent/'old-state'
    src.mkdir()
    with closing(sqlite3.connect(da.db_pfad())) as c, closing(sqlite3.connect(src/'decision_history.sqlite')) as d:
        c.backup(d)
    for name, value in {'handelsmodus.txt':'paper', 'bot_zustand.txt':'pausiert',
            'okx_credentials.json':json.dumps({'live_trading':False}),
            'etoro_reconciliation.json':'{}', 'fill_progress.json':'{}',
            'bot_order_registry.json':json.dumps({'orders':{}, 'pending':{}})}.items():
        (src/name).write_text(value, encoding='utf-8')
    return src


def test_plain_repair_import_neither_imports_analytics_nor_opens_default_database(tmp_path):
    code = copy_release(tmp_path/'code')
    result = run_cold(code, ['-c', 'import repair_okx_accounting,sys; '
        'assert "decision_analytics" not in sys.modules; '
        'assert "trade_ledger" not in sys.modules'], tmp_path/'home')
    assert result.returncode == 0, result.stderr
    assert not offline_validation.extra_files(code, offline_validation.read_manifest(code))


@pytest.mark.parametrize('apply', [False, True])
@pytest.mark.parametrize('other_cwd', [False, True])
def test_real_cli_cold_import_uses_only_selected_database(bundle, tmp_path, apply, other_cwd):
    code = copy_release(tmp_path/'code')
    source = cold_state(bundle, tmp_path)
    before = sha(source/'decision_history.sqlite')
    args = [code/'repair_okx_accounting.py', '--source', source, '--receipts', bundle['path']]
    if apply:
        args += ['--apply', '--workers-stopped']
    caller = tmp_path/'caller'; caller.mkdir()
    result = run_cold(code, args, tmp_path/'home', cwd=caller if other_cwd else code)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data['applied'] is apply and data['preview'] is not apply
    assert data['receipt_import']['status'] == 'APPLIED'
    assert not offline_validation.extra_files(code, offline_validation.read_manifest(code))
    assert not list(caller.iterdir())
    if not apply:
        assert sha(source/'decision_history.sqlite') == before
    else:
        with closing(sqlite3.connect(source/'decision_history.sqlite')) as c:
            first = c.execute('SELECT * FROM trades ORDER BY trade_id').fetchall()
        again = run_cold(code, args, tmp_path/'home')
        assert again.returncode == 0, again.stderr
        assert json.loads(again.stdout)['receipt_import']['status'] == 'ALREADY_APPLIED'
        with closing(sqlite3.connect(source/'decision_history.sqlite')) as c:
            assert c.execute('SELECT * FROM trades ORDER BY trade_id').fetchall() == first
        assert not offline_validation.extra_files(code, offline_validation.read_manifest(code))


@pytest.mark.parametrize('apply', [False, True])
def test_cold_repair_never_touches_an_existing_default_ledger(bundle, tmp_path, apply):
    code = copy_release(tmp_path/'code'); source = cold_state(bundle, tmp_path)
    default = code/'decision_history.sqlite'
    # A pre-existing file must not even receive an analytics schema migration.
    with closing(sqlite3.connect(default)) as c:
        c.execute('CREATE TABLE operator_sentinel (v TEXT)')
        c.execute("INSERT INTO operator_sentinel VALUES ('never overwrite')"); c.commit()
    before = sha(default)
    args = [code/'repair_okx_accounting.py', '--source', source, '--receipts', bundle['path']]
    if apply: args += ['--apply', '--workers-stopped']
    result = run_cold(code,args,tmp_path/'home')
    assert result.returncode == 0, result.stderr
    assert sha(default) == before
    assert not default.with_name(default.name+'-wal').exists()
    assert not default.with_name(default.name+'-shm').exists()


@pytest.mark.parametrize('kind', ['missing', 'directory', 'symlink', 'relative'])
def test_scope_refuses_bad_inputs_without_creation(tmp_path, kind):
    path = tmp_path/'decision_history.sqlite'
    if kind == 'directory': path.mkdir()
    elif kind == 'symlink': path.symlink_to(tmp_path/'missing-target')
    elif kind == 'relative': path = Path('untrusted-relative-ledger.sqlite')
    with pytest.raises(DatabaseScopeError):
        with existing_database(path): pytest.fail('invalid scope entered')
    assert current_path() is None
    assert not (tmp_path/'missing-target').exists()


def test_nested_scope_and_exception_restore_previous_selection(tmp_path):
    a=tmp_path/'a.sqlite'; b=tmp_path/'b.sqlite'; a.touch(); b.touch()
    assert current_path() is None
    with existing_database(a):
        assert current_path() == a
        with pytest.raises(RuntimeError, match='probe'):
            with existing_database(b):
                assert current_path() == b
                raise RuntimeError('probe')
        assert current_path() == a
    assert current_path() is None


def test_scope_is_thread_local_and_does_not_retarget_other_work(tmp_path):
    a=tmp_path/'a.sqlite'; b=tmp_path/'b.sqlite'; a.touch(); b.touch()
    def other():
        assert current_path() is None
        with existing_database(b): return current_path()
    with existing_database(a):
        with ThreadPoolExecutor(max_workers=1) as pool:
            assert pool.submit(other).result() == b
        assert current_path() == a
    assert current_path() is None


def test_deleted_selected_ledger_is_not_silently_recreated(tmp_path):
    import decision_analytics as da
    selected=tmp_path/'selected.sqlite'; selected.touch()
    before=da.db_pfad()
    with existing_database(selected):
        selected.unlink()
        with pytest.raises(DatabaseScopeError): da._connect()
    assert not selected.exists() and da.db_pfad() == before


def test_selected_connection_rejects_a_delete_race_via_sqlite_rw_mode(tmp_path,monkeypatch):
    import decision_analytics as da
    selected=tmp_path/'selected.sqlite'; selected.touch()
    real_connect=sqlite3.connect
    calls=[]
    def racing_connect(database, *args, **kwargs):
        calls.append((database, kwargs))
        selected.unlink()
        return real_connect(database,*args,**kwargs)
    with existing_database(selected):
        monkeypatch.setattr(da.sqlite3,'connect',racing_connect)
        with pytest.raises(sqlite3.OperationalError): da._connect()
    assert not selected.exists()
    assert len(calls)==1 and calls[0][0].endswith('?mode=rw') and calls[0][1]['uri'] is True


def test_warm_repair_scope_does_not_change_normal_db_path(tmp_path):
    import decision_analytics as da
    from repair_okx_accounting import selected_database
    selected=tmp_path/'selected';selected.mkdir();(selected/'decision_history.sqlite').touch()
    before=da.db_pfad();before_var=da.DB_PATH
    with selected_database(selected):
        assert da.db_pfad()==selected/'decision_history.sqlite'
        assert da.DB_PATH==before_var
    assert da.db_pfad()==before and da.DB_PATH==before_var


class ColdMigrationHost(FakeHost):
    def __init__(self, source, root, home):
        super().__init__(source); self.root=root; self.home=home
    def command(self,args,**kwargs):
        a=list(map(str,args)); self.calls.append(a)
        # Real fresh processes for every migration/repair, not direct Python calls.
        if ((a[1]=='-c' and 'migrate_from' in a[2])
                or a[1] in {'repair_okx_state.py','repair_okx_accounting.py'}):
            assert all(s['ActiveState']=='inactive' for s in self.current.values())
            result=run_cold(self.root,a[1:],self.home)
            if result.returncode:
                raise UpdateError(result.stderr or result.stdout)
            return result.stdout
        if a[1]=='-c' and 'import json,config' in a[2]:
            return json.dumps(dict(etoro=True,okx=True,paper=True,okx_live=False))
        if a[1] in {'check_runtime_dependencies.py','volltest.py','webui_network_setup.py',
                    'webui_setup.py','webui_start.py'}:
            # No recursive full suite, package install, or UI/network configuration.
            return ''
        raise AssertionError('Unexpected external action '+str(a))


def test_real_installer_migration_crosses_cold_process_boundary_and_promotes(bundle,tmp_path):
    source=cold_state(bundle,tmp_path); target=copy_release(tmp_path/'target')
    before=sha(source/'decision_history.sqlite')
    host=ColdMigrationHost(source,target,tmp_path/'home');host.stop()
    installer=Installer(target,host,receipts=bundle['path'])
    installer.migrate(source)
    assert sha(source/'decision_history.sqlite')==before
    assert installer.info['staging']==str(installer.stage)
    assert (target/'bot_zustand.txt').read_text(encoding='utf-8')=='pausiert'
    assert 'decision_history.sqlite' in [p.name for p in installer.promoted]
    with closing(sqlite3.connect(target/'decision_history.sqlite')) as c:
        assert c.execute('SELECT count(*) FROM okx_receipt_imports').fetchone()[0]==1
        assert c.execute('SELECT netto_pnl FROM trades WHERE trade_id=?',(bundle['tid'],)).fetchone()[0] is not None
    before_second=sha(target/'decision_history.sqlite')
    with pytest.raises(UpdateError,match='bereits belegt'):
        installer.migrate(source)
    assert sha(target/'decision_history.sqlite')==before_second


def test_preflight_pollution_stops_before_services_are_stopped(setup):
    target,source,host=setup
    class DirtyPreparation(Installer):
        def prepare(self):
            self.phase('1_VORBEREITUNG_UND_OFFLINE_TEST')
            (self.root/'decision_history.sqlite').write_bytes(b'preserve unexpected state')
    installer=DirtyPreparation(target,host)
    with pytest.raises(UpdateError,match='vor Dienststopp'):
        installer.run()
    assert not installer.stopped and not installer.promoted and not installer.start_boundary
    assert ['STOP'] not in host.calls
    assert (target/'decision_history.sqlite').read_bytes()==b'preserve unexpected state'


def test_occupied_target_is_rejected_before_creating_staging_or_running_repairs(setup):
    target,source,host=setup; p=target/'decision_history.sqlite';p.write_bytes(b'not empty')
    installer=Installer(target,host)
    with pytest.raises(UpdateError,match='vor Zustandsuebernahme'): installer.migrate(source)
    assert installer.stage is None and not installer.promoted and not host.calls
    assert p.read_bytes()==b'not empty'


def test_new_boundary_guard_identifies_unexpected_file_without_deleting_it(setup):
    target,source,host=setup; p=target/'decision_history.sqlite-wal';p.write_bytes(b'preserve wal')
    with pytest.raises(UpdateError,match='nach test-child'):
        Installer(target,host).assert_target_source_only('nach test-child')
    assert p.read_bytes()==b'preserve wal'


def test_fix2_unpack_preserves_both_aborted_predecessors(tmp_path):
    from release_unpack import RELEASE_ROOT,unpack_release
    from test_v975_release_unpack import payload
    before={}
    for name in ('TradingBot_v9.8.8_NEXUS','TradingBot_v9.8.8_NEXUS_FIX1','TradingBot_v9.8.8_NEXUS_FIX2','TradingBot_v9.9.0_NEXUS','TradingBot_v10.0.0_NEXUS','TradingBot_v10.1.0_NEXUS'):
        d=tmp_path/name;d.mkdir();(d/'decision_history.sqlite').write_bytes(b'preserve '+name.encode())
        before[name]=sha(d/'decision_history.sqlite')
    data, digest=payload();target=unpack_release(data,tmp_path,digest)
    assert RELEASE_ROOT=='TradingBot_v10.1.10_NEXUS' and target.name==RELEASE_ROOT
    assert all(sha(tmp_path/n/'decision_history.sqlite')==h for n,h in before.items())
