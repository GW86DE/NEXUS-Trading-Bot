"""Regressions for fresh release state handling and the read-only export."""
import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

from test_v975_installer import setup, FakeHost, make_root
from nexus_update import Installer, UpdateError
from Nexus_SUI_Diagnose import collect, read_database


def test_runtime_source_collision_stops_before_any_service_action(setup):
    target, source, host = setup
    (target/'bot_zustand.txt').write_text('aktiv')
    manifest = json.loads((target/'MANIFEST_SHA256.json').read_text())
    manifest['bot_zustand.txt'] = hashlib.sha256(b'aktiv').hexdigest()
    (target/'MANIFEST_SHA256.json').write_text(json.dumps(manifest))
    with pytest.raises(UpdateError, match='Laufzeitzustand'):
        Installer(target, host).run()
    assert not host.calls
    assert (target/'bot_zustand.txt').read_text() == 'aktiv'


@pytest.mark.parametrize('state', ['aktiv', 'pausiert', 'gestoppt'])
def test_real_state_migration_keeps_operator_choice_without_seed(setup, state, monkeypatch):
    import decision_analytics as analytics
    import trade_ledger
    from settings_migration import migrate_from
    from repair_okx_state import repair
    target, source, host = setup
    monkeypatch.setattr(analytics, 'DB_PATH', source/'decision_history.sqlite')
    analytics.init_db()
    trade_ledger.init_ledger()
    for name in ('etoro_reconciliation.json', 'bot_order_registry.json', 'fill_progress.json'):
        (source/name).write_text('{}')
    (source/'bot_order_registry.json').write_text(json.dumps({'orders': {}, 'pending': {}}))
    (source/'bot_zustand.txt').write_text(state)
    original = (source/'bot_zustand.txt').read_bytes()
    def command(args, **kw):
        if args[1] == '-c' and len(args) == 5:
            migrate_from(Path(args[3]), Path(args[4]), strict=True)
        elif args[1] == 'repair_okx_state.py':
            repair(Path(args[3]), apply=True)
        elif args[1] == 'repair_okx_accounting.py':
            from repair_okx_accounting import repair as accounting_repair
            accounting_repair(Path(args[3]), apply=True, workers_stopped=True)
        elif args[1] == '-c' and len(args) == 3:
            return json.dumps({'etoro': True, 'okx': True, 'paper': True, 'okx_live': False})
        elif args[1] in {'check_runtime_dependencies.py', 'volltest.py',
                          'webui_network_setup.py', 'webui_setup.py', 'webui_start.py'}:
            # External validation/UI steps are independently tested by the full
            # release run. This test executes real migration, repair and promotion.
            return ''
        else:
            raise AssertionError('Unexpected command')
    host.command = command
    installer = Installer(target, host)
    installer.migrate(source)
    assert (target/'bot_zustand.txt').read_bytes() == original
    assert (source/'bot_zustand.txt').read_bytes() == original


def test_actual_release_has_no_mutable_source_files():
    from settings_migration import PERSISTENT_FILES
    root = Path(__file__).resolve().parents[1]
    manifest = json.loads((root/'MANIFEST_SHA256.json').read_text())
    assert not set(manifest).intersection(PERSISTENT_FILES)
    assert not (root/'bot_zustand.txt').exists()
    assert (root/'VERSION.txt').read_text().strip() == '10.7.1-NEXUS'


def test_service_version_comes_from_current_release(setup, tmp_path, monkeypatch):
    import shutil
    target, source, host = setup
    (target/'VERSION.txt').write_text('9.8.0-NEXUS\n')
    production = Path(__file__).resolve().parents[1]
    for pattern in ('*.service.template', '*.desktop.template'):
        for path in production.glob(pattern):
            shutil.copy2(path, target/path.name)
    fakehome = tmp_path/'home'
    fakehome.mkdir()
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: fakehome))
    installer = Installer(target, host)
    installer.backup = tmp_path/'backup'
    installer.backup.mkdir()
    from nexus_update import UNITS
    host.stop()
    host.command(['sudo', 'systemctl', 'disable', *UNITS])
    installer.install_units()
    assert installer.info['version'] == '9.8.0-NEXUS'
    assert all('9.8.0' in p.read_text() for p in installer.backup.glob('neu_*.service'))


def test_export_reads_real_sui_rows_without_touching_state_or_credentials(tmp_path):
    root = make_root(tmp_path/'source')
    (root/'crypto_positions.json').write_text(json.dumps({'positionen': {
        'SUI': {'symbol': 'SUI', 'menge': 124.771765},
        'BTC': {'symbol': 'BTC', 'menge': 99}}}))
    (root/'okx_credentials.json').write_text('not valid JSON: must never be read')
    database = root/'decision_history.sqlite'
    with sqlite3.connect(database) as conn:
        conn.execute('CREATE TABLE trades(trade_id INTEGER PRIMARY KEY, symbol TEXT, menge REAL)')
        conn.execute('CREATE TABLE decision_orders(symbol TEXT, raw_json TEXT)')
        conn.execute('CREATE TABLE trade_entry_fills(trade_id INTEGER, fill_id TEXT)')
        conn.execute('CREATE TABLE trade_exit_events(trade_id INTEGER, exit_id TEXT)')
        conn.executemany('INSERT INTO trades VALUES (?,?,?)', [(45,'SUI',124.771765), (46,'BTC',99)])
        conn.execute('INSERT INTO trade_entry_fills VALUES (45,?)', ('98',))
        conn.execute('INSERT INTO decision_orders VALUES (?,?)', ('SUI', json.dumps({'api_key': 'SENSITIVE', 'state':'canceled'})))
    before = {p.name: p.read_bytes() for p in root.iterdir()}
    data = collect(root, {})
    assert [r['symbol'] for r in data['decision_history.sqlite']['trades']['rows']] == ['SUI']
    assert data['decision_history.sqlite']['trade_entry_fills']['rows'][0]['fill_id'] == '98'
    assert 'BTC' not in json.dumps(data)
    assert 'SENSITIVE' not in json.dumps(data)
    assert {p.name: p.read_bytes() for p in root.iterdir()} == before


def test_wrong_database_path_never_creates_an_empty_file(tmp_path):
    path = tmp_path/'missing.sqlite'
    assert read_database(path) == {'available': False}
    assert not path.exists()
