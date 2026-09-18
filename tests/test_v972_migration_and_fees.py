from datetime import datetime,timezone
import sqlite3
import pytest

@pytest.mark.parametrize('name',['decision_history.sqlite','broker_exit_journal.sqlite'])
def test_sqlite_copy_includes_uncheckpointed_wal(tmp_path,name):
    from settings_migration import _kopiere
    src=tmp_path/name;dst=tmp_path/('copy_'+name)
    con=sqlite3.connect(src);con.execute('PRAGMA journal_mode=WAL');con.execute('PRAGMA wal_autocheckpoint=0')
    try:
        con.execute('CREATE TABLE evidence(id INTEGER PRIMARY KEY,payload TEXT)');con.commit()
        con.execute('INSERT INTO evidence VALUES(1,?)',('persisted in WAL',));con.commit()
        assert src.with_name(src.name+'-wal').stat().st_size>0
        _kopiere(name,src,dst)
        other=sqlite3.connect(dst)
        try:
            assert other.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
            assert other.execute('SELECT payload FROM evidence').fetchone()[0]=='persisted in WAL'
        finally:other.close()
    finally:con.close()


def test_other_account_fee_is_not_used_for_chart(monkeypatch):
    import config,okx_status,trade_chart_data
    from broker_display_context import context
    trade={'broker':'okx','paper':True,'broker_account_fingerprint':'A'}
    monkeypatch.setattr(okx_status,'lies',lambda:{'modus':'DEMO','account_fingerprint':'B','last_heartbeat':datetime.now(timezone.utc).isoformat(),'gebuehrensatz_pct':.01})
    monkeypatch.setattr(config,'OKX_TAKER_FEE_PCT',.003)
    assert trade_chart_data._gemessener_taker_satz(trade=trade)==.003
    monkeypatch.setattr(okx_status,'lies',lambda:{'modus':'DEMO','account_fingerprint':'A','last_heartbeat':datetime.now(timezone.utc).isoformat(),'gebuehrensatz_pct':.01})
    assert trade_chart_data._gemessener_taker_satz(trade=trade)==.0001


def source_state(tmp_path):
    import json
    src=tmp_path/'old';src.mkdir()
    con=sqlite3.connect(src/'decision_history.sqlite');con.execute('CREATE TABLE evidence(id)');con.commit();con.close()
    for name in ('etoro_reconciliation.json','bot_order_registry.json','fill_progress.json'):(src/name).write_text('{}')
    return src


def test_strict_migration_rejects_existing_target_before_writing(tmp_path):
    from settings_migration import migrate_from
    src=source_state(tmp_path);dst=tmp_path/'new';dst.mkdir();(dst/'fill_progress.json').write_text('{"sentinel":true}')
    with pytest.raises(ValueError,match='nicht mischen'):migrate_from(src,dst,strict=True)
    assert (dst/'fill_progress.json').read_text()=='{"sentinel":true}'
    assert not (dst/'decision_history.sqlite').exists()


def test_strict_migration_requires_whole_source(tmp_path):
    from settings_migration import migrate_from
    src=source_state(tmp_path);(src/'fill_progress.json').unlink();dst=tmp_path/'new';dst.mkdir()
    with pytest.raises(ValueError,match='unvollstaendig'):migrate_from(src,dst,strict=True)
    assert not list(dst.iterdir())


def test_strict_migration_reports_copy_failure(tmp_path,monkeypatch):
    import settings_migration as m
    src=source_state(tmp_path);dst=tmp_path/'new';dst.mkdir()
    orig=m._kopiere
    def broken(name,*args):
        if name=='fill_progress.json':raise OSError('test disk write failure')
        return orig(name,*args)
    monkeypatch.setattr(m,'_kopiere',broken)
    with pytest.raises(RuntimeError,match='unvollstaendig'):m.migrate_from(src,dst,strict=True)
    assert (dst/'handelsmodus.txt').read_text().strip()=='paper'


def test_strict_migration_success_preserves_source_and_resets_mode(tmp_path):
    from settings_migration import migrate_from
    src=source_state(tmp_path);(src/'handelsmodus.txt').write_text('live');dst=tmp_path/'new';dst.mkdir()
    copied,skipped=migrate_from(src,dst,strict=True)
    assert not skipped and 'decision_history.sqlite' in copied
    assert (src/'handelsmodus.txt').read_text()=='live'
    assert (dst/'handelsmodus.txt').read_text().strip()=='paper'


def test_shell_check_checks_second_file_not_only_first(tmp_path):
    import subprocess,shutil
    if not shutil.which('bash'):pytest.skip('bash not installed on platform')
    from volltest import _shell_syntax_command
    good=tmp_path/'good.sh';bad=tmp_path/'bad.sh'
    good.write_text('echo ok\n');bad.write_text('if ; then\n')
    result=subprocess.run(_shell_syntax_command([good,bad]),capture_output=True)
    assert result.returncode!=0
    bad.write_text('echo also_ok\n')
    assert subprocess.run(_shell_syntax_command([good,bad]),capture_output=True).returncode==0
