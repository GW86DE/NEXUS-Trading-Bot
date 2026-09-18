"""Regression: the mandatory auth test must run, not fail at httpx import."""
from pathlib import Path
import importlib.metadata as metadata
import pytest
import check_test_dependencies as deps

ROOT = Path(__file__).resolve().parents[1]


def test_actual_pins_and_real_testclient():
    assert deps.check() == []


def test_missing_httpx_fails_before_probe(monkeypatch):
    real = deps.metadata.version
    def version(name):
        if name == 'httpx':
            raise metadata.PackageNotFoundError(name)
        return real(name)
    monkeypatch.setattr(deps.metadata, 'version', version)
    monkeypatch.setattr(deps, '_probe_client', lambda: pytest.fail('probe must not run'))
    assert any('httpx fehlt' in message for message in deps.check())


def test_wrong_httpx_version_is_not_silently_accepted(monkeypatch):
    real = deps.metadata.version
    monkeypatch.setattr(deps.metadata, 'version', lambda name: '0.27.0' if name == 'httpx' else real(name))
    assert any('installiert 0.27.0' in message for message in deps.check())


def test_broken_import_is_failure_even_with_package_metadata(monkeypatch):
    def broken():
        raise ModuleNotFoundError("No module named 'httpx'")
    monkeypatch.setattr(deps, '_probe_client', broken)
    assert any('httpx' in message for message in deps.check())


def test_incompatible_testclient_is_failure(monkeypatch):
    def broken():
        raise TypeError('unexpected transport argument')
    monkeypatch.setattr(deps, '_probe_client', broken)
    assert any('TypeError' in message for message in deps.check())


@pytest.mark.parametrize('content', [
    'pytest==9.0.2\n',
    'httpx==0.28.1\n',
    'pytest==9.0.2\nhttpx>=0.28.1\n',
    'pytest==9.0.2\nhttpx==0.28.1\nHTTPX==0.28.1\n',
    'pytest==9.0.2\nhttpx==1.0.dev6\n',
])
def test_missing_unpinned_duplicate_or_prerelease_manifest_rejected(tmp_path, content):
    path = tmp_path / 'requirements-test.txt'
    path.write_text(content)
    assert deps.check(path)


def test_missing_manifest_does_not_pass(tmp_path):
    assert deps.check(tmp_path / 'missing.txt')


def test_comments_in_manifest_are_allowed(tmp_path):
    path = tmp_path / 'requirements-test.txt'
    path.write_text('# mandatory\npytest==9.0.2\n\nhttpx==0.28.1 # test client\n')
    assert deps.pinned_requirements(path) == {'pytest': '9.0.2', 'httpx': '0.28.1'}


def test_main_fails_without_installing_or_skipping(monkeypatch, capsys):
    monkeypatch.setattr(deps, 'check', lambda: ['httpx fehlt'])
    assert deps.main() == 78
    output = capsys.readouterr().out
    assert 'httpx fehlt' in output and 'Keine Testfreigabe' in output


def test_installer_checks_package_consistency_before_volltest_and_units():
    installer = (ROOT / 'Pi_Installieren.sh').read_text()
    install = installer.index('-c requirements-lock.txt -r requirements-test.txt')
    pip_check = installer.index('"$PY" -m pip check')
    client_check = installer.index('"$PY" check_test_dependencies.py')
    full_test = installer.index('"$PY" volltest.py')
    units = installer.index('sudo install -m 0644 "$SERVICE_TMP"')
    assert install < pip_check < client_check < full_test < units


def test_volltest_declares_new_release_files_and_runs_client_check():
    import volltest
    assert 'check_test_dependencies.py' in volltest.REQUIRED_RELEASE_FILES
    assert 'tests/test_v974_test_dependencies.py' in volltest.REQUIRED_RELEASE_FILES
    source = (ROOT / 'volltest.py').read_text()
    assert 'TEST-ABHAENGIGKEITEN / WEBUI-CLIENT' in source
    assert 'dependency_result[1] != 0' in source
    assert '"-q", "-ra", "tests"' in source
