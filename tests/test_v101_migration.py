"""The actual migration keeps provider quota and ambiguous protection writes."""
import json
import sqlite3
import pytest

import settings_migration as sm


def source(tmp_path):
    old = tmp_path / 'old'; old.mkdir()
    target = tmp_path / 'new'; target.mkdir()
    with sqlite3.connect(old / 'decision_history.sqlite') as con:
        con.execute('CREATE TABLE decisions(id INTEGER)')
    for name in ('etoro_reconciliation.json', 'bot_order_registry.json', 'fill_progress.json'):
        (old / name).write_text('{}')
    return old, target


def test_real_strict_migration_preserves_quota_wal_and_pending_protection(tmp_path):
    old, target = source(tmp_path)
    # Keep the source connection open: last reservation exists in the WAL.
    con = sqlite3.connect(old / 'massive_service.sqlite')
    con.execute('PRAGMA journal_mode=WAL')
    con.execute('CREATE TABLE calls(id INTEGER PRIMARY KEY, at REAL, result TEXT)')
    con.execute("INSERT INTO calls VALUES(1,1789309500,'RESERVED')"); con.commit()
    journal = {'orders': {'synthetic-id': {'state': 'UNKNOWN', 'request_id': 'once-only'}}}
    (old / 'etoro_protection_journal.json').write_text(json.dumps(journal))
    try:
        sm.migrate_from(old, target, strict=True)
        with sqlite3.connect(target / 'massive_service.sqlite') as copied:
            assert copied.execute('SELECT * FROM calls').fetchall() == [(1,1789309500,'RESERVED')]
        assert json.loads((target / 'etoro_protection_journal.json').read_text()) == journal
        assert con.execute('SELECT count(*) FROM calls').fetchone()[0] == 1
    finally:
        con.close()


@pytest.mark.parametrize('filename,payload', [('massive_service.sqlite',b'broken sqlite'), ('etoro_protection_journal.json', b'{broken')])
def test_corrupt_quota_or_protection_aborts_before_import(tmp_path, filename, payload):
    old, target = source(tmp_path)
    (old / filename).write_bytes(payload)
    with pytest.raises((ValueError, sqlite3.DatabaseError)):
        sm.migrate_from(old, target, strict=True)
    assert not list(target.iterdir())


@pytest.mark.parametrize('filename', ['massive_service.sqlite','etoro_protection_journal.json'])
def test_occupied_quota_or_protection_target_is_never_merged(tmp_path, filename):
    old, target = source(tmp_path)
    (target / filename).write_bytes(b'keep this state')
    with pytest.raises(ValueError, match='nicht mischen'):
        sm.migrate_from(old, target, strict=True)
    assert (target / filename).read_bytes() == b'keep this state'


@pytest.mark.parametrize('filename', [
    'okx_credentials.json', 'etoro_credentials.json', 'okx_credentials.json.dpapi',
    'etoro_credentials.json.dpapi', 'handelsmodus.txt', 'aktives_profil.txt',
    'web_ui_settings.json', 'universe_settings.json', 'ai_router_settings.json',
    'decision_history.sqlite-wal', 'decision_history.sqlite-shm'])
def test_strict_import_rejects_foreign_identity_settings_and_sidecars_before_writing(tmp_path, filename):
    old,target=source(tmp_path)
    (old/'okx_credentials.json').write_text(json.dumps({'demo_api_key':'SOURCE_ACCOUNT_A','live_trading':False}))
    sentinel=b'TARGET_ACCOUNT_B_KEEP_UNCHANGED'
    (target/filename).write_bytes(sentinel)
    before={p.name:p.read_bytes() for p in target.iterdir()}
    with pytest.raises(ValueError,match='nicht mischen'):
        sm.migrate_from(old,target,strict=True)
    assert {p.name:p.read_bytes() for p in target.iterdir()}==before
    assert not (target/'decision_history.sqlite').exists()


@pytest.mark.parametrize('table', ['trades','execution_fills','execution_orders','broker_exit_intents','metadata','odd"table','sqliteXtrades'])
def test_nonstrict_import_preserves_nonempty_ledger_even_with_empty_decisions(tmp_path,table):
    old,target=source(tmp_path)
    db=target/'decision_history.sqlite'
    quoted='"'+table.replace('"','""')+'"'
    with sqlite3.connect(old/'decision_history.sqlite') as con:
        con.execute('INSERT INTO decisions VALUES(1)')
    with sqlite3.connect(db) as con:
        con.execute('CREATE TABLE decisions(id INTEGER)')
        con.execute('CREATE TABLE '+quoted+'(id INTEGER, account TEXT, amount REAL)')
        con.execute('INSERT INTO '+quoted+' VALUES(999,?,42.5)',('TARGET_ACCOUNT_B',))
    copied,skipped=sm.migrate_from(old,target,overwrite=False)
    assert 'decision_history.sqlite' not in copied and 'decision_history.sqlite' in skipped
    with sqlite3.connect(db) as con:
        assert con.execute('SELECT * FROM '+quoted).fetchall()==[(999,'TARGET_ACCOUNT_B',42.5)]
        assert con.execute('SELECT * FROM decisions').fetchall()==[]


def test_nonstrict_import_preserves_corrupt_unknown_database(tmp_path):
    old,target=source(tmp_path)
    path=target/'decision_history.sqlite';sentinel=b'UNREADABLE_BUT_MUST_NOT_BE_OVERWRITTEN'
    path.write_bytes(sentinel)
    copied,skipped=sm.migrate_from(old,target,overwrite=False)
    assert path.read_bytes()==sentinel
    assert 'decision_history.sqlite' in skipped and 'decision_history.sqlite' not in copied


def test_nonstrict_import_can_replace_proven_empty_ui_initialization(tmp_path):
    old,target=source(tmp_path)
    with sqlite3.connect(old/'decision_history.sqlite') as con:
        con.execute('INSERT INTO decisions VALUES(73)')
    with sqlite3.connect(target/'decision_history.sqlite') as con:
        con.execute('CREATE TABLE decisions(id INTEGER)')
        con.execute('CREATE TABLE trades(id INTEGER PRIMARY KEY AUTOINCREMENT)')
        con.execute('CREATE TABLE execution_fills(id INTEGER)')
    copied,skipped=sm.migrate_from(old,target,overwrite=False)
    assert 'decision_history.sqlite' in copied
    with sqlite3.connect(target/'decision_history.sqlite') as con:
        assert con.execute('SELECT id FROM decisions').fetchall()==[(73,)]


def test_nonstrict_empty_check_includes_committed_target_wal(tmp_path):
    old,target=source(tmp_path)
    con=sqlite3.connect(target/'decision_history.sqlite')
    try:
        con.execute('PRAGMA journal_mode=WAL')
        con.execute('PRAGMA wal_autocheckpoint=0')
        con.execute('CREATE TABLE decisions(id INTEGER)')
        con.execute('CREATE TABLE trades(id INTEGER, amount REAL)')
        con.commit()
        con.execute('INSERT INTO trades VALUES(999,100.75)');con.commit()
        copied,skipped=sm.migrate_from(old,target,overwrite=False)
        assert 'decision_history.sqlite' in skipped
        assert con.execute('SELECT * FROM trades').fetchall()==[(999,100.75)]
    finally:
        con.close()
