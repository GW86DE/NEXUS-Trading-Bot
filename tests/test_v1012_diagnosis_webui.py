"""Authenticated diagnosis jobs, detached locking and explicit document delivery."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import zipfile

import pytest
from webui import diagnosis_jobs as jobs


@pytest.fixture
def configured(tmp_path, monkeypatch):
    import notifier
    destination = tmp_path/'reports'; destination.mkdir()
    monkeypatch.setattr(jobs, 'output_dir', lambda: destination)
    monkeypatch.setattr(notifier, 'telegram_status', lambda **kw: {'enabled': True, 'configured': True})
    monkeypatch.setattr(notifier, 'send_document', lambda *a, **kw: pytest.fail('unrequested delivery'))
    return destination


def completed(configured, *, telegram_status='NOT_REQUESTED'):
    jobs.job_dir().mkdir(exist_ok=True)
    p = configured/'NEXUS_10_Diagnose_fixture.zip'
    with zipfile.ZipFile(p, 'w') as z: z.writestr('META.json', '{}')
    row = {'id': 'a'*32, 'status': 'COMPLETED', 'mode': 'instant', 'created_at': 10,
        'telegram_status': telegram_status, 'archive': str(p), 'archive_sha256': jobs._digest(p),
        'completion': 'COMPLETED'}
    jobs._write(row)
    return row, p


@pytest.mark.parametrize('mode,args', [('instant', ['--sofort']), ('30min', ['--minuten', '30'])])
def test_worker_uses_fixed_collector_arguments_preserves_zip_and_never_sends_by_default(configured, monkeypatch, mode, args):
    import NEXUS_10_Diagnose as d
    monkeypatch.setattr(jobs, '_spawn', lambda *a: None)
    row = jobs.start(mode, False)
    captured = []
    def collect(argv, *, progress):
        captured.extend(argv)
        progress('OBSERVATION', observation_started_at=123, requested_seconds=row['requested_seconds'])
        p = configured/'NEXUS_10_Diagnose_fixture.zip'
        with zipfile.ZipFile(p, 'w') as z: z.writestr('META.json', '{}')
        progress('ARCHIVE_READY', archive=str(p), completion='COMPLETED')
        return 0
    monkeypatch.setattr(d, 'main', collect)
    jobs.worker('--worker', row['id'], jobs._lock())
    out = jobs._read(row['id'])
    assert out['status'] == 'COMPLETED' and out['telegram_status'] == 'NOT_REQUESTED'
    assert captured == ['--quelle', str(jobs.ROOT), '--ausgabe', str(configured), *args]
    assert out['archive_sha256'] == jobs._digest(jobs.archive_path(row['id']))
    assert out['observation_started_at'] == 123


def test_process_launch_is_detached_and_uses_no_shell_or_request_path(configured, monkeypatch):
    captured = []
    class Child: pid = 123
    def popen(args, **kw): captured.append((args, kw)); return Child()
    monkeypatch.setattr(jobs.subprocess, 'Popen', popen)
    row = jobs.start('30min', False)
    argv, kw = captured[0]
    assert argv[:4] == [sys.executable, '-m', 'webui.diagnosis_jobs', '--worker']
    assert argv[4] == row['id'] and kw['pass_fds'] == (int(argv[5]),)
    assert kw['start_new_session'] and not kw.get('shell')
    assert kw['cwd'] == jobs.ROOT


def test_actual_inherited_kernel_lock_blocks_second_process_until_child_exits(configured):
    fd = jobs._lock()
    child = subprocess.Popen([sys.executable, '-c', 'import sys; sys.stdin.buffer.read(1)', str(fd)],
        stdin=subprocess.PIPE, pass_fds=(fd,), start_new_session=True)
    os.close(fd)
    try:
        with pytest.raises(ValueError, match='bereits'): jobs._lock()
        with pytest.raises(ValueError, match='bereits'): jobs.start('instant')
        assert jobs.status()['busy']
    finally:
        child.communicate(b'x', timeout=10)
    fd = jobs._lock(); os.close(fd)
    assert not jobs.status()['busy']


def test_interrupted_job_is_not_success_and_sending_is_never_automatically_retried(configured):
    row, _ = completed(configured, telegram_status='SENDING')
    row['status'] = 'SENDING'; jobs._write(row)
    out = jobs.status()['jobs'][0]
    assert out['status'] == 'COMPLETED' and out['telegram_status'] == 'UNKNOWN'
    with pytest.raises(ValueError, match='Doppelversand'): jobs.send(row['id'])
    row.update(id='b'*32, archive=None, status='RUNNING', telegram_status='NOT_REQUESTED')
    jobs._write(row)
    assert next(r for r in jobs.status()['jobs'] if r['id'] == row['id'])['status'] == 'INTERRUPTED'


def test_document_is_sent_once_only_when_requested_and_failure_keeps_zip(configured, monkeypatch):
    import notifier
    row, p = completed(configured)
    calls = []
    monkeypatch.setattr(notifier, 'send_document', lambda path, caption: calls.append((path, caption)) or True)
    jobs._deliver(row); jobs._write(row)
    assert row['telegram_status'] == 'SENT' and calls[0][0] == p and p.exists()
    with pytest.raises(ValueError, match='Doppelversand'): jobs.send(row['id'])
    row['telegram_status'] = 'NOT_REQUESTED'; row['status'] = 'COMPLETED'; jobs._write(row)
    monkeypatch.setattr(notifier, 'send_document', lambda *a: False)
    jobs._deliver(row); jobs._write(row)
    assert row['telegram_status'] == 'UNKNOWN' and p.exists()
    with pytest.raises(ValueError): jobs.send(row['id'])


def test_oversized_zip_and_disabled_telegram_keep_local_download(configured, monkeypatch):
    import notifier
    row, p = completed(configured)
    monkeypatch.setattr(jobs, 'MAX_TELEGRAM_BYTES', 1)
    jobs._deliver(row)
    assert row['telegram_status'] == 'TOO_LARGE' and p.exists()
    monkeypatch.setattr(notifier, 'telegram_status', lambda **kw: {'enabled': False, 'configured': True})
    with pytest.raises(ValueError, match='einrichten'): jobs.send(row['id'])
    with pytest.raises(ValueError, match='einrichten'): jobs.start('instant', True)
    assert jobs.archive_path(row['id']) == p


def test_download_rejects_path_escape_symlink_and_changed_bytes(configured, tmp_path):
    row, p = completed(configured)
    with pytest.raises(ValueError): jobs.archive_path('../outside')
    old = p.read_bytes(); p.write_bytes(old+b'altered')
    with pytest.raises(ValueError, match='veraendert'): jobs.archive_path(row['id'])
    p.unlink(); outside = tmp_path/'outside.zip'; outside.write_bytes(old); p.symlink_to(outside)
    with pytest.raises(ValueError, match='erwarteten'): jobs.archive_path(row['id'])


def test_authentication_csrf_modes_and_zip_download(configured, monkeypatch):
    from fastapi.testclient import TestClient
    from webui.app import app
    from test_v100_webui_backend import configured_auth
    auth, _ = configured_auth(monkeypatch)
    token, csrf = auth.issue_session('testuser')
    monkeypatch.setattr(jobs, '_spawn', lambda *a: None)
    client = TestClient(app)
    assert client.get('/api/diagnosis').status_code == 401
    assert client.post('/api/diagnosis', json={'mode': 'instant'}).status_code == 401
    assert client.get('/diagnosis', follow_redirects=False).status_code == 303
    client.cookies.set(auth.COOKIE, token)
    assert client.get('/diagnosis').status_code == 200
    assert client.post('/api/diagnosis', json={'mode': 'instant'}).status_code == 403
    headers = {'X-CSRF-Token': csrf}
    assert client.post('/api/diagnosis', headers=headers, content='bad json').status_code == 400
    assert client.post('/api/diagnosis', headers=headers, json={'mode': 'instant', 'path': '/tmp'}).status_code == 400
    assert client.post('/api/diagnosis', headers=headers, json={'mode': 'instant', 'telegram': 'false'}).status_code == 409
    assert client.post('/api/diagnosis', headers=headers, json={'mode': []}).status_code == 409
    assert client.post('/api/diagnosis', headers=headers, json={'mode': None}).status_code == 409
    assert client.post('/api/diagnosis', headers=headers, json={'mode': 'instant'}).status_code == 200
    row, p = completed(configured)
    response = client.get('/api/diagnosis/'+row['id']+'/download')
    assert response.status_code == 200 and response.content == p.read_bytes()
    assert response.headers['cache-control'] == 'no-store'
    assert client.post('/api/diagnosis/'+row['id']+'/telegram').status_code == 403


def test_telegram_document_errors_do_not_expose_token(tmp_path, monkeypatch, caplog):
    import notifier
    p = tmp_path/'fixture.zip'; p.write_bytes(b'local archive fixture')
    token, chat = 'fixture-token-very-secret', 'fixture-chat-secret'
    failures = []
    monkeypatch.setattr(notifier, '_runtime', lambda: {'enabled': True})
    monkeypatch.setattr(notifier, '_credentials', lambda: (token, chat))
    monkeypatch.setattr(notifier, '_mark_failure', failures.append)
    def failed(*a, **kw): raise TimeoutError('https://api.telegram.org/bot'+token+'/sendDocument '+chat)
    monkeypatch.setattr(notifier.requests, 'post', failed)
    assert not notifier.send_document(p)
    assert token not in str(failures)+caplog.text and chat not in str(failures)+caplog.text
