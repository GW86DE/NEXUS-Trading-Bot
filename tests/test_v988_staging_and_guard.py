"""Real staged SQLite migration with simulated OS boundaries; no service/API calls."""
from pathlib import Path
from contextlib import closing
from copy import deepcopy
import hashlib,json,sqlite3
import pytest
from test_v988_import_migration_ui import bundle
from test_v987_closed_results import closed
from test_v975_execution_and_repair import engine
from test_v975_installer import FakeHost,make_root
import nexus_update as nu
from okx_receipt_math import EvidenceError


def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()


class StagingHost(FakeHost):
    def command(self,args,**kwargs):
        from settings_migration import migrate_from
        from repair_okx_accounting import repair
        a=[str(x) for x in args];self.calls.append(a)
        if len(a)>1 and a[1]=='-c' and len(a)==5:
            migrate_from(Path(a[3]),Path(a[4]),strict=True)
        elif len(a)>1 and a[1]=='repair_okx_state.py':
            from repair_okx_state import repair as old_repair
            old_repair(Path(a[3]),apply=True)
        elif len(a)>1 and a[1]=='repair_okx_accounting.py':
            assert all(s['ActiveState']=='inactive' for s in self.current.values())
            receipt=Path(a[a.index('--receipts')+1]) if '--receipts' in a else None
            result=repair(Path(a[3]),apply=True,workers_stopped=True,receipts=receipt)
            assert result['orders_sent']==0
        elif len(a)>1 and a[1]=='-c' and len(a)==3:
            return json.dumps(dict(etoro=True,okx=True,paper=True,okx_live=False))
        elif len(a)>1 and a[1] in {'check_runtime_dependencies.py','volltest.py','webui_network_setup.py','webui_setup.py','webui_start.py'}:
            return ''  # Real independent dependency/full-test suites cover these.
        else:
            raise AssertionError('Unexpected external command: '+str(a))
        return ''


def test_real_staging_imports_receipt_not_original_database_and_repeat_is_guarded(bundle,tmp_path):
    import decision_analytics as da
    import trade_ledger as tl
    src=da.db_pfad().parent;dst=make_root(tmp_path/'new')
    for n in ('etoro_reconciliation.json','fill_progress.json'):(src/n).write_text('{}')
    for n,value in {'handelsmodus.txt':'paper','bot_zustand.txt':'pausiert',
        'okx_credentials.json':json.dumps({'live_trading':False})}.items():(src/n).write_text(value)
    with tl._connect() as c:c.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    before=digest(src/'decision_history.sqlite')
    host=StagingHost(src);host.stop()
    installer=nu.Installer(dst,host,receipts=bundle['path']);installer.migrate(src)
    assert digest(src/'decision_history.sqlite')==before
    with closing(sqlite3.connect(dst/'decision_history.sqlite')) as c:
        row=c.execute('select netto_pnl,fee_quality from trades where trade_id=?',(bundle['tid'],)).fetchone()
        assert row[0] is not None and row[1]=='BROKER_CONFIRMED'
        assert c.execute('select count(*) from okx_receipt_imports').fetchone()[0]==1
    assert (dst/'bot_zustand.txt').read_text()=='pausiert'
    assert (dst/'okx_accounting_repair_report.json').is_file()
    assert all(not p.name.endswith(('-wal','-shm')) for p in installer.promoted)
    # A subsequent normal reader may recreate WAL/SHM; no frames were needed
    # for the promoted main file. Check a standalone copy of only that file.
    import shutil
    standalone=tmp_path/'standalone.sqlite';shutil.copyfile(dst/'decision_history.sqlite',standalone)
    with closing(sqlite3.connect(standalone)) as verify:
        assert verify.execute('select netto_pnl from trades where trade_id=?',(bundle['tid'],)).fetchone()[0]==row[0]
    commands=[a[1] for a in host.calls if len(a)>1]
    assert commands.index('repair_okx_state.py')<commands.index('repair_okx_accounting.py')<commands.index('volltest.py')
    with pytest.raises(nu.UpdateError,match='bereits belegt'):installer.migrate(src)


def test_foreign_current_state_aborts_staging_before_any_promotion(bundle,tmp_path):
    import decision_analytics as da
    import trade_ledger as tl
    source=da.db_pfad().parent;target=make_root(tmp_path/'target')
    for n in ('etoro_reconciliation.json','fill_progress.json'):(source/n).write_text('{}')
    data=deepcopy(bundle['data']);data['expected_account_fingerprint']='B';data['account_check']['fingerprint']='B'
    p=tmp_path/'foreign-receipt.json';p.write_text(json.dumps(data))
    with tl._connect() as c:c.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    before=digest(source/'decision_history.sqlite');host=StagingHost(source);host.stop()
    installer=nu.Installer(target,host,receipts=p)
    with pytest.raises(EvidenceError):installer.migrate(source)
    assert not installer.promoted and not installer.start_boundary
    assert not (target/'decision_history.sqlite').exists()
    assert digest(source/'decision_history.sqlite')==before


def test_sealing_does_not_promote_transient_shared_memory(tmp_path):
    tmp_path=tmp_path/'staging';tmp_path.mkdir()
    db=tmp_path/'a.sqlite';con=sqlite3.connect(db)
    try:
        con.execute('pragma journal_mode=WAL');con.execute('create table t(x)');con.execute('insert into t values(1)');con.commit()
        selected=nu.sealed_state_files(tmp_path)
        assert selected==[db]
        other=sqlite3.connect(db)
        try:assert other.execute('select x from t').fetchone()[0]==1
        finally:other.close()
    finally:con.close()


def test_sealing_refuses_uncheckpointable_reader_not_missing_frames(tmp_path):
    tmp_path=tmp_path/'staging';tmp_path.mkdir()
    db=tmp_path/'a.sqlite';writer=sqlite3.connect(db);reader=sqlite3.connect(db)
    try:
        writer.execute('pragma journal_mode=WAL');writer.execute('create table t(x)');writer.execute('insert into t values(1)');writer.commit()
        reader.execute('begin');reader.execute('select * from t').fetchall()
        writer.execute('insert into t values(2)');writer.commit()
        with pytest.raises(nu.UpdateError,match='noch belegt'):nu.sealed_state_files(tmp_path)
        reader.rollback()
        assert nu.sealed_state_files(tmp_path)==[db]
        assert writer.execute('select count(*) from t').fetchone()[0]==2
    finally:reader.close();writer.close()


def test_unreadable_gate_is_a_controlled_broker_rejection(closed):
    import okx_accounting as gate
    from broker.base import BrokerFehler
    class Broken:
        def execute(self,*args):raise sqlite3.OperationalError('busy/corrupt')
    with pytest.raises(BrokerFehler,match='nicht pruefbar'):gate.require_complete(Broken(),'A','DEMO')


def test_new_read_gate_closes_all_its_connection_handles(closed,monkeypatch):
    import okx_accounting as gate
    import trade_ledger as tl
    tracked=[];real=tl._connect
    def connection():
        c=real();tracked.append(c);return c
    monkeypatch.setattr(tl,'_connect',connection)
    for _ in range(5):
        result=gate.status('A','DEMO')
        # 10.7.0: Das Ergebnis vom 10.09. bleibt ein offener Beleg, sperrt aber nur
        # noch am Verkaufstag. Der Leser muss ihn weiterhin liefern (kein Abbruch,
        # keine leere Antwort) -- geprueft wird hier die Handle-Hygiene je Aufruf.
        assert [g['kind'] for g in result['gaps']]==['RESULT'] and result['complete'] and not result['blocked_symbols']
    for c in tracked:
        with pytest.raises(sqlite3.ProgrammingError,match='closed'):c.execute('select 1')


def test_alias_resolution_inside_writer_uses_same_connection_no_nested_schema(closed,monkeypatch):
    import trade_ledger as tl
    from contextlib import closing
    tl._alias_cache_leeren()
    with closing(tl._connect()) as con, con:
        con.execute("INSERT OR REPLACE INTO account_aliases(broker,account_fingerprint,alias_fingerprint,erfasst_am_utc) VALUES('okx','A','OLD','2026-09-11')")
        con.commit();con.execute('BEGIN IMMEDIATE')
        def forbidden():
            raise AssertionError('nested schema writer inside financial transaction')
        monkeypatch.setattr(tl,'init_ledger',forbidden)
        assert tl.konto_identitaeten('okx','A',con=con)==['A','OLD']
        sql,values=tl._konto_platzhalter('okx','A',con=con)
        assert values==['A','OLD'] and sql.count('?')==2


def test_alias_cache_is_scoped_to_database_not_only_account(closed,tmp_path,monkeypatch):
    import trade_ledger as tl
    import decision_analytics as da
    from contextlib import closing
    tl._alias_cache_leeren()
    with closing(tl._connect()) as con,con:
        con.execute("INSERT OR REPLACE INTO account_aliases(broker,account_fingerprint,alias_fingerprint,erfasst_am_utc) VALUES('okx','A','OLD','2026-09-11')")
    assert 'OLD' in tl.konto_identitaeten('okx','A')
    monkeypatch.setattr(da,'DB_PATH',tmp_path/'second.sqlite')
    assert tl.konto_identitaeten('okx','A')==['A']
