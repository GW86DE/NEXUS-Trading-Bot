"""A personal receipt import is paired, staged and precedes service startup."""
from pathlib import Path
import pytest
from nexus_update import Installer, UpdateError
from test_v975_installer import make_root, FakeHost


def test_partial_historical_receipt_pair_is_rejected(tmp_path):
    root = make_root(tmp_path/'new')
    for args in ({'verified_trades':tmp_path/'trades.zip'}, {'verified_fx':tmp_path/'fx.zip'}):
        with pytest.raises(UpdateError, match='gemeinsam'):
            Installer(root, **args)


def test_historical_repair_uses_staged_context_without_promoting_liveness(tmp_path):
    source = tmp_path/'old';source.mkdir()
    (source/'runtime_status_okx.json').write_text('{"account_fingerprint":"historical"}')
    target = make_root(tmp_path/'new')
    class Host(FakeHost):
        def command(self, args, **kw):
            args = [str(a) for a in args];self.calls.append(args)
            if len(args)==3 and args[1]=='-c':
                return '{"etoro":true,"okx":true,"paper":true,"okx_live":false}'
            if len(args)>1 and args[1]=='repair_okx_verified_history.py':
                assert all(s['ActiveState']=='inactive' for s in self.current.values())
                stage=Path(args[args.index('--source')+1])
                assert stage!=source and stage!=target
                assert (stage/'runtime_status_okx.json').read_bytes()==(source/'runtime_status_okx.json').read_bytes()
                (stage/'okx_verified_history_repair_report.json').write_text('{"applied":true}')
            return ''
    host=Host(source);host.stop()
    updater=Installer(target,host,verified_trades=tmp_path/'trades.zip',verified_fx=tmp_path/'fx.zip')
    updater.migrate(source)
    assert (target/'okx_verified_history_repair_report.json').is_file()
    assert not (target/'runtime_status_okx.json').exists()
    names=[a[1] for a in host.calls if len(a)>1]
    assert names.index('repair_okx_accounting.py')<names.index('repair_okx_verified_history.py')<names.index('volltest.py')
    assert (source/'runtime_status_okx.json').read_text()=='{"account_fingerprint":"historical"}'
