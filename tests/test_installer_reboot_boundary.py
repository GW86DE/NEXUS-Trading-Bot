"""Reboot-safe source/target handover with simulated systemd only."""
from copy import deepcopy
import json

import pytest

from nexus_update import Installer, UpdateError, UNITS
from test_v975_installer import make_root, FakeHost


def fixture(tmp_path, old_enable=('enabled', 'enabled')):
    source = make_root(tmp_path / 'source')
    target = make_root(tmp_path / 'target')
    host = FakeHost(source)
    for unit, state in zip(UNITS, old_enable):
        host.current[unit]['UnitFileState'] = state
    installer = Installer(target, host)
    installer.old_states = host.states()
    return source, target, host, installer


@pytest.mark.parametrize('states', [('enabled','enabled'), ('disabled','enabled'),
                                   ('enabled-runtime','disabled')])
def test_real_save_source_disarms_reboot_before_backup_then_restores_prior_enable_states(tmp_path, states):
    source, target, host, installer = fixture(tmp_path, states)
    original = host.states()
    def stopped_boundary(path):
        assert path == source
        assert all(s['ActiveState'] == 'inactive' and s['UnitFileState'] == 'disabled'
                   for s in host.states().values())
        # Interrupt here: backup/migration/unit replacement have not begun.
        raise UpdateError('simulated interruption after durable disarm')
    host.other_writers = stopped_boundary
    with pytest.raises(UpdateError, match='durable disarm'):
        installer.save_source(source)
    assert installer.backup is None and not installer.start_boundary
    journal = json.loads(installer.journal.read_text())
    assert journal['autostart_change_attempted'] is True
    assert journal['old_states'] == original
    assert host.calls[:2] == [['STOP'], ['sudo','systemctl','disable',*UNITS]]
    if 'enabled-runtime' in states:
        assert ['sudo','systemctl','disable','--runtime',
                *[u for u, s in zip(UNITS, states) if s == 'enabled-runtime']] in host.calls
    # Before a controlled rollback, reboot cannot start old/new/mixed units.
    assert all(s['UnitFileState'] == 'disabled' for s in host.states().values())
    installer.rollback_prestart()
    assert host.states() == original
    last_enable = max(i for i, c in enumerate(host.calls) if c[:3] in (
        ['sudo','systemctl','enable'], ['sudo','systemctl','disable']))
    first_start = next(i for i, c in enumerate(host.calls) if c[:3] == ['sudo','systemctl','start'])
    assert last_enable < first_start


def test_partial_disable_failure_restores_both_original_enable_states(tmp_path):
    source, target, host, installer = fixture(tmp_path)
    original = host.states()
    normal_command = host.command
    failed = False
    def partial_failure(args, **kwargs):
        nonlocal failed
        if list(args) == ['sudo','systemctl','disable',*UNITS] and not failed:
            failed = True
            host.current[UNITS[0]]['UnitFileState'] = 'disabled'
            raise UpdateError('systemd failed after first unit')
        return normal_command(args, **kwargs)
    host.command = partial_failure
    with pytest.raises(UpdateError, match='first unit'):
        installer.save_source(source)
    installer.rollback_prestart()
    assert host.states() == original
    assert installer.backup is None


@pytest.mark.parametrize('field,value', [('UnitFileState','enabled'), ('ActiveState','active')])
def test_unit_replacement_cannot_begin_with_live_or_enabled_service(tmp_path, field, value):
    source, target, host, installer = fixture(tmp_path)
    host.stop(); host.command(['sudo','systemctl','disable',*UNITS])
    host.current[UNITS[0]][field] = value
    before = deepcopy(host.calls)
    with pytest.raises(UpdateError, match='rebootfest deaktiviert'):
        installer.install_units()
    assert host.calls == before and installer.units_changed is False


def test_start_boundary_failure_keeps_latest_data_and_disables_without_old_restart(tmp_path):
    source, target, host, installer = fixture(tmp_path)
    installer.autostart_change_attempted = True
    installer.start_boundary = True
    latest = target / 'position_state.json'; latest.write_text('{"latest_fill":true}')
    for state in host.current.values():
        state.update(WorkingDirectory=str(target), ExecStart=str(target/'python'),
                     ActiveState='active', UnitFileState='enabled')
    installer.rollback_prestart()
    assert latest.read_text() == '{"latest_fill":true}'
    assert all(s['UnitFileState']=='disabled' and s['ActiveState']=='inactive'
               and s['WorkingDirectory']==str(target) for s in host.states().values())
    assert not any(c[:3] in (['sudo','systemctl','enable'], ['sudo','systemctl','start']) for c in host.calls)


def test_unknown_enable_state_rejected_before_any_service_action(tmp_path):
    from nexus_update import determine_source
    source, target, host, installer = fixture(tmp_path, ('masked','enabled'))
    with pytest.raises(UpdateError, match='Autostartzustand'):
        determine_source(host.states(), target)
    assert not host.calls
