"""Tests for configured-install validation, without touching real user state."""
from pathlib import Path
import hashlib
import json
import os
import subprocess
import sys

import pytest
import offline_validation as isolation

ROOT = Path(__file__).resolve().parents[1]


def _release(tmp_path, files=None):
    root = tmp_path / "install"
    root.mkdir()
    files = files or {"config.py": "VALUE = 7\n", "tests/test_all.py": "assert True\n"}
    manifest = {}
    for name, content in files.items():
        p = root / name
        p.parent.mkdir(exist_ok=True, parents=True)
        p.write_text(content)
        manifest[name] = isolation.digest(p)
    (root / isolation.MANIFEST).write_text(json.dumps(manifest))
    return root


def test_configured_copy_retains_source_not_private_state(tmp_path):
    root = _release(tmp_path)
    (root / "telegram_credentials.json").write_text('{"token":"PRIVATE_SENTINEL"}')
    (root / "decision_history.sqlite").write_bytes(b"state-sentinel")
    (root / "universe_proposals.json").write_text(json.dumps({"broker":"ib" + "kr"}))
    before = {p.name: p.read_bytes() for p in root.iterdir() if p.is_file()}
    dest = tmp_path / "isolated"
    stats = isolation.prepare_copy(root, dest, allow_local_state=True)
    assert stats == {"release_files": 2, "local_files_excluded": 3}
    assert not (dest / "telegram_credentials.json").exists()
    assert not (dest / "decision_history.sqlite").exists()
    assert (dest / "config.py").read_bytes() == (root / "config.py").read_bytes()
    assert before == {p.name: p.read_bytes() for p in root.iterdir() if p.is_file()}


def test_release_mode_still_rejects_private_state(tmp_path):
    root = _release(tmp_path)
    (root / "telegram_credentials.json").write_text('{}')
    with pytest.raises(isolation.IsolationError, match="Lokale Daten"):
        isolation.prepare_copy(root, tmp_path / "shadow", allow_local_state=False)


@pytest.mark.parametrize("name", ["extra.py", "tests/missed.py", "broker/new.py", "ui.js"])
def test_local_mode_must_not_hide_new_code_or_tests(tmp_path, name):
    root = _release(tmp_path)
    p = root / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("# still requires release review")
    with pytest.raises(isolation.IsolationError, match="Quell-/Testdatei"):
        isolation.prepare_copy(root, tmp_path / "shadow", allow_local_state=True)


def test_modified_known_source_fails_closed(tmp_path):
    root = _release(tmp_path)
    (root / "config.py").write_text("VALUE = 'different'\n")
    with pytest.raises(isolation.IsolationError, match="veraendert: config.py"):
        isolation.prepare_copy(root, tmp_path / "shadow", allow_local_state=True)


def test_missing_manifest_no_fallback_scan(tmp_path):
    with pytest.raises(isolation.IsolationError, match="manifest fehlt"):
        isolation.prepare_copy(tmp_path, tmp_path / "shadow", allow_local_state=True)


@pytest.mark.parametrize("name", ["../private.py", "/tmp/private.py", "a/../b", "a\\b", "a//b", "./a", "C:/private"])
def test_manifest_path_traversal_rejected(tmp_path, name):
    (tmp_path / isolation.MANIFEST).write_text(json.dumps({name: "a" * 64}))
    with pytest.raises(isolation.IsolationError):
        isolation.read_manifest(tmp_path)


@pytest.mark.parametrize("payload", ["null", "[]", "{}", "{", '{"x.py":"wrong"}'])
def test_corrupt_manifest_never_executes(tmp_path, payload):
    (tmp_path / isolation.MANIFEST).write_text(payload)
    with pytest.raises(isolation.IsolationError):
        isolation.read_manifest(tmp_path)


def test_source_symlink_rejected(tmp_path):
    root = _release(tmp_path)
    real = tmp_path / "elsewhere.py"
    real.write_text("VALUE=7")
    (root / "config.py").unlink()
    (root / "config.py").symlink_to(real)
    with pytest.raises(isolation.IsolationError, match="Symlink"):
        isolation.prepare_copy(root, tmp_path / "shadow", allow_local_state=True)


def test_environment_has_no_inherited_keys_paths_profiles_or_proxy(tmp_path):
    inherited = {"PATH": os.defpath, "FMP_API_KEY":"SENTINEL", "OPENAI_API_KEY":"SENTINEL",
                 "TELEGRAM_CHAT_ID":"99", "ACTIVE_PROFILE":"offensiv", "TRADING_MODE":"live",
                 "PYTHONPATH":"/private/site", "PYTEST_ADDOPTS":"--ignore=tests",
                 "HTTP_PROXY":"http://private", "HOME":"/private/home",
                 "TRADINGBOT_ALLOW_LOCAL_STATE":"1", "NEXUS_WEBUI_ACCESS_MODE":"public"}
    env = isolation.test_environment(tmp_path / "source", tmp_path, inherited)
    assert not any(v == "SENTINEL" for v in env.values())
    assert all(k not in env for k in ["FMP_API_KEY", "OPENAI_API_KEY", "TELEGRAM_CHAT_ID",
                                     "HTTP_PROXY", "ACTIVE_PROFILE", "PYTEST_ADDOPTS",
                                     "NEXUS_WEBUI_ACCESS_MODE", "TRADINGBOT_ALLOW_LOCAL_STATE"])
    assert env["TRADING_MODE"] == "paper"
    assert env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] == "1"
    assert env["HOME"] == str(tmp_path / "home")
    assert inherited["TRADING_MODE"] == "live"


def test_caches_and_virtualenv_not_copied(tmp_path):
    root = _release(tmp_path)
    for name in [".venv/lib/private.py", "__pycache__/config.pyc", ".pytest_cache/foo"]:
        p = root / name
        p.parent.mkdir(exist_ok=True, parents=True)
        p.write_text("IGNORE")
    assert isolation.prepare_copy(root, tmp_path / "shadow", allow_local_state=False)["local_files_excluded"] == 0


def test_repeat_copy_is_deterministic(tmp_path):
    root = _release(tmp_path)
    isolation.prepare_copy(root, tmp_path / "first", allow_local_state=False)
    isolation.prepare_copy(root, tmp_path / "second", allow_local_state=False)
    assert (tmp_path / "first/config.py").read_bytes() == (tmp_path / "second/config.py").read_bytes()


def test_direct_pytest_in_configured_tree_refused_before_import(tmp_path, monkeypatch):
    root = _release(tmp_path)
    (root / "telegram_credentials.json").write_text('{"token":"PRIVATE_SENTINEL"}')
    monkeypatch.delenv("NEXUS_OFFLINE_TEST_ROOT", raising=False)
    with pytest.raises(isolation.IsolationError, match="konfiguriertem Ordner"):
        isolation.refuse_unsafe_direct_pytest(root)


def test_direct_pytest_with_external_credentials_refused(tmp_path, monkeypatch):
    root = _release(tmp_path)
    monkeypatch.delenv("NEXUS_OFFLINE_TEST_ROOT", raising=False)
    monkeypatch.setenv("FMP_API_KEY", "DUMMY")
    with pytest.raises(isolation.IsolationError, match="API-Umgebung"):
        isolation.refuse_unsafe_direct_pytest(root)


@pytest.mark.parametrize("code", [
    "import socket; socket.socket().connect(('127.0.0.1', 9))",
    "import socket; socket.getaddrinfo('example.invalid', 80)",
    "import requests; requests.get('https://example.invalid/?token=DUMMY')",
])
def test_network_attempt_fails_without_recording_secret(tmp_path, code):
    env = isolation.test_environment(ROOT, tmp_path, dict(os.environ))
    result = subprocess.run([sys.executable, str(ROOT / "offline_test_bootstrap/run_python.py"), "-c", code], cwd=tmp_path, env=env,
                            capture_output=True, text=True, timeout=10)
    assert result.returncode != 0
    events = (tmp_path / "network-events.txt").read_text()
    assert any(word in events for word in ["http", "socket", "dns"])
    assert "DUMMY" not in events


def test_real_inprocess_testclient_still_works_with_network_guard(tmp_path):
    env = isolation.test_environment(ROOT, tmp_path, dict(os.environ))
    code = "from check_test_dependencies import _probe_client; _probe_client(); print('OK')"
    result = subprocess.run([sys.executable, str(ROOT / "offline_test_bootstrap/run_python.py"), "-c", code], cwd=tmp_path, env=env,
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout
    assert not (tmp_path / "network-events.txt").exists()


def test_installed_root_never_imports_trading_config():
    # Wrapper must execute on the installed tree with stdlib only.
    source = (ROOT / "offline_validation.py").read_text()
    assert "import config" not in source
    full = (ROOT / "volltest.py").read_text()
    assert "raise SystemExit(run_isolated(ROOT))" in full


def test_source_hygiene_still_detects_forbidden_broker_reference(tmp_path, monkeypatch):
    import volltest
    root = _release(tmp_path, {"code.py": "# broker: " + "ib" + "kr" + "\n"})
    shadow = tmp_path / "shadow"
    isolation.prepare_copy(root, shadow, allow_local_state=True)
    monkeypatch.setattr(volltest, "ROOT", shadow)
    code, issues = volltest._static_hygiene()
    assert code == 1
    assert any("Alt-Brokerreferenz" in issue for issue in issues)


def test_manifest_changed_during_run_cannot_validate_new_unexecuted_code(tmp_path, monkeypatch):
    from types import SimpleNamespace
    root = _release(tmp_path)
    def fake_run(*args, **kwargs):
        (root / "config.py").write_text("VALUE=99\n")
        manifest = isolation.read_manifest(root)
        manifest["config.py"] = isolation.digest(root / "config.py")
        (root / isolation.MANIFEST).write_text(json.dumps(manifest))
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(isolation.subprocess, "run", fake_run)
    assert isolation.run_isolated(root) == 78


def test_network_tripwire_makes_run_fail_even_if_tests_return_success(tmp_path, monkeypatch):
    from types import SimpleNamespace
    root = _release(tmp_path)
    def fake_run(*args, **kwargs):
        Path(kwargs["env"]["NEXUS_OFFLINE_NETWORK_EVENTS"]).write_text("http\n")
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(isolation.subprocess, "run", fake_run)
    assert isolation.run_isolated(root) == 1
