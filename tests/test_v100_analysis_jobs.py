"""Bounded analysis processes: admission, identity, time and output budgets."""
from __future__ import annotations

import json
import os
import sys
import time
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def jobs(tmp_path, monkeypatch):
    from webui import analysis_jobs
    monkeypatch.setattr(analysis_jobs, "JOB_DIR", tmp_path / "jobs")
    monkeypatch.setattr(analysis_jobs, "ROOT", tmp_path)
    analysis_jobs.JOB_DIR.mkdir()
    return analysis_jobs


def state(jobs, **changes):
    value = {"job_id": "fixture-job", "task": "backtest", "label": "Fixture",
             "status": "STARTING", "started_at": jobs._utc(), "started_epoch": time.time(),
             "pid": None, "process_identity": None}
    value.update(changes)
    jobs._write(jobs._state_path(value["job_id"]), value)
    return value


@pytest.mark.parametrize("pid", [None, 0, -1, "bad", True, 1.5])
def test_invalid_pid_never_signals_process_group(jobs, monkeypatch, pid):
    def forbidden(*args):
        raise AssertionError("invalid PID must not reach os.kill")
    monkeypatch.setattr(jobs.os, "kill", forbidden)
    assert not jobs._running({"status": "RUNNING", "pid": pid})
    assert not jobs._pid_alive(pid)


def test_reused_pid_is_not_this_job(jobs, monkeypatch):
    value = state(jobs, status="RUNNING", pid=42, process_identity="old-boot:old-start")
    monkeypatch.setattr(jobs, "_process_identity", lambda pid: "new-boot:new-start")
    assert not jobs._running(value)
    assert jobs._recent()[0]["status"] == "INTERRUPTED"


def test_starting_lease_counts_for_admission_and_expires(jobs):
    value = state(jobs)
    assert jobs.overview()["running"]
    with pytest.raises(ValueError, match="bereits"):
        jobs.start("backtest")
    value["started_epoch"] = time.time() - jobs.START_GRACE_SECONDS - 1
    jobs._write(jobs._state_path(value["job_id"]), value)
    assert jobs._recent()[0]["status"] == "INTERRUPTED"


def test_legacy_live_pid_without_creation_identity_is_unknown(jobs):
    state(jobs, status="RUNNING", pid=os.getpid())
    overview = jobs.overview()
    assert overview["blocked"] and not overview["running"]
    assert overview["recent"][0]["status"] == "UNKNOWN"
    with pytest.raises(ValueError):
        jobs.start("backtest")


def test_corrupt_job_is_preserved_and_prevents_duplicate_launch(jobs):
    path = jobs.JOB_DIR / "damaged.json"
    path.write_text("broken", encoding="utf-8")
    assert jobs.overview()["blocked"]
    with pytest.raises(ValueError):
        jobs.start("backtest")
    assert path.read_text(encoding="utf-8") == "broken"


def test_two_simultaneous_requests_launch_one_worker(jobs, monkeypatch):
    barrier = threading.Barrier(2)
    calls = []
    def fake_popen(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(pid=os.getpid())
    monkeypatch.setattr(jobs.subprocess, "Popen", fake_popen)
    def request():
        barrier.wait(timeout=3)
        try:
            return jobs.start("backtest")["status"]
        except ValueError:
            return "BLOCKED"
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(request) for _ in range(2)]
        results = [future.result(timeout=5) for future in futures]
    assert sorted(results) == ["BLOCKED", "RUNNING"]
    assert len(calls) == 1 and len(list(jobs.JOB_DIR.glob("*.json"))) == 1


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux fork and /proc identity")
def test_independent_webui_processes_share_admission_lock(jobs, monkeypatch):
    import multiprocessing
    context = multiprocessing.get_context("fork")
    ready = context.Barrier(2)
    release = context.Event()
    results = context.Queue()
    monkeypatch.setattr(jobs.subprocess, "Popen", lambda *a, **k: SimpleNamespace(pid=os.getpid()))
    def request():
        ready.wait(timeout=5)
        try:
            jobs.start("backtest")
            results.put("STARTED")
            release.wait(timeout=5)
        except ValueError:
            results.put("BLOCKED")
    processes = [context.Process(target=request) for _ in range(2)]
    try:
        for process in processes:
            process.start()
        observed = [results.get(timeout=5) for _ in processes]
        assert sorted(observed) == ["BLOCKED", "STARTED"]
    finally:
        release.set()
        for process in processes:
            process.join(timeout=5)
            if process.is_alive():
                process.terminate()
                process.join(timeout=2)
        results.close()


def test_failed_launch_leaves_terminal_evidence_not_stale_starting(jobs, monkeypatch):
    def fail(*args, **kwargs):
        raise OSError("synthetic launch failure")
    monkeypatch.setattr(jobs.subprocess, "Popen", fail)
    with pytest.raises(RuntimeError, match="nicht gestartet"):
        jobs.start("backtest")
    data = jobs.overview()
    assert not data["running"] and not data["blocked"]
    assert data["recent"][0]["status"] == "FAILED"


def test_non_allowlisted_command_never_spawns(jobs, monkeypatch):
    calls = []
    monkeypatch.setattr(jobs.subprocess, "Popen", lambda *a, **k: calls.append(a))
    with pytest.raises(ValueError):
        jobs.start("../../arbitrary.py")
    assert not calls


def test_worker_refuses_wrong_task_or_reused_identity(jobs, monkeypatch):
    state(jobs, status="RUNNING", pid=os.getpid(), process_identity="old-process")
    with pytest.raises(RuntimeError, match="anderen Worker"):
        jobs._worker("backtest", "fixture-job")
    with pytest.raises(ValueError):
        jobs._worker("not_allowlisted", "fixture-job")


def test_actual_short_child_succeeds_and_receives_single_thread_limits(jobs):
    script = jobs.ROOT / "fixture.py"
    script.write_text("import os,json\nprint(json.dumps({k:os.environ.get(k) for k in "
                      "['OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS']}))\n", encoding="utf-8")
    value = state(jobs)
    rc, status, detail = jobs._run_script(script, jobs._log_path(value["job_id"]), value)
    assert rc == 0 and status == "SUCCEEDED" and detail == ""
    lines = jobs._log_path(value["job_id"]).read_text().splitlines()
    data = json.loads(next(line for line in lines if line.startswith("{")))
    assert set(data.values()) == {"1"}


def test_flushed_progress_is_visible_before_analysis_finishes(jobs):
    script = jobs.ROOT / "fixture.py"
    release = jobs.ROOT / "release.flag"
    script.write_text("import pathlib,time\nprint('PROGRESS_VISIBLE',flush=True)\n"
                      f"p=pathlib.Path({str(release)!r})\n"
                      "deadline=time.monotonic()+5\n"
                      "while not p.exists() and time.monotonic()<deadline: time.sleep(.02)\n", encoding="utf-8")
    value = state(jobs)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(jobs._run_script, script, jobs._log_path(value["job_id"]), value)
        try:
            deadline = time.monotonic() + 2
            visible = False
            while time.monotonic() < deadline:
                if "PROGRESS_VISIBLE" in jobs.output(value["job_id"])["output"]:
                    visible = True
                    break
                time.sleep(.02)
            assert visible and not future.done()
        finally:
            release.write_text("done", encoding="utf-8")
            result = future.result(timeout=5)
    assert result[1] == "SUCCEEDED"


@pytest.mark.skipif(os.name != "posix", reason="POSIX process-group termination")
def test_timeout_stops_child_and_descendant_in_own_group(jobs, monkeypatch):
    monkeypatch.setattr(jobs, "JOB_TIMEOUT_SECONDS", 1.0)
    script = jobs.ROOT / "fixture.py"
    script.write_text("import subprocess,sys,time\n"
                      "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'])\n"
                      "print(p.pid,flush=True)\ntime.sleep(60)\n", encoding="utf-8")
    value = state(jobs)
    started = time.monotonic()
    rc, status, detail = jobs._run_script(script, jobs._log_path(value["job_id"]), value)
    assert status == "TIMED_OUT" and rc != 0 and time.monotonic() - started < 5
    child_pid = int(next(line for line in jobs._log_path(value["job_id"]).read_text().splitlines() if line.isdigit()))
    assert jobs._process_identity(value["child_pid"]) is None
    assert jobs._process_identity(child_pid) is None


def test_excess_output_stops_child_and_bounds_disk_use(jobs, monkeypatch):
    monkeypatch.setattr(jobs, "MAX_LOG_BYTES", 2048)
    script = jobs.ROOT / "fixture.py"
    script.write_text("import sys,time\nwhile True:\n sys.stdout.write('x'*65536)\n sys.stdout.flush()\n", encoding="utf-8")
    value = state(jobs)
    rc, status, detail = jobs._run_script(script, jobs._log_path(value["job_id"]), value)
    assert status == "OUTPUT_LIMIT"
    assert jobs._log_path(value["job_id"]).stat().st_size <= 2048
    assert jobs._process_identity(value["child_pid"]) is None


def test_output_reads_only_bounded_tail(jobs, monkeypatch):
    value = state(jobs, status="SUCCEEDED")
    path = jobs._log_path(value["job_id"])
    with path.open("wb") as stream:
        stream.write(b"OLD OUTPUT")
        stream.seek(jobs.MAX_OUTPUT_BYTES * 100)
        stream.write(b"TAIL MARKER")
    def forbidden(*args):
        raise AssertionError("Logviewer must not read whole files")
    monkeypatch.setattr(Path, "read_bytes", forbidden)
    result = jobs.output(value["job_id"])
    assert result["output"].endswith("TAIL MARKER") and "OLD OUTPUT" not in result["output"]
    assert len(result["output"].encode("utf-8")) <= jobs.MAX_OUTPUT_BYTES + 100


def test_kernel_identity_changes_when_process_exits(jobs):
    if not sys.platform.startswith("linux"):
        pytest.skip("Linux /proc creation identity")
    identity = jobs._process_identity(os.getpid())
    if identity is None:
        # Keep the actual kernel evidence in an assertion failure rather than
        # masking an integration-only process/namespace regression.
        stat = Path("/proc/self/stat").read_text(encoding="utf-8")
        boot = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii")
        pytest.fail(f"Missing identity: pid={os.getpid()}, platform={sys.platform}, "
                    f"stat={stat[:180]!r}, boot={boot!r}")
    assert identity.startswith("linux:")
    assert identity == jobs._process_identity(os.getpid())
    self_stat = Path("/proc/self/stat").read_text(encoding="utf-8")
    start_tick = self_stat.rsplit(") ", 1)[1].split()[19]
    mounted_pid = self_stat.split(" ", 1)[0]
    assert identity.endswith(f":{mounted_pid}:{start_tick}")


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux /proc namespace contract")
def test_missing_pidfd_in_different_namespace_does_not_guess_host_pid(jobs, monkeypatch):
    monkeypatch.setattr(jobs.os, "pidfd_open", None)
    monkeypatch.setattr(jobs.os, "readlink", lambda path: str(os.getpid() + 100000))
    assert jobs._process_identity(os.getpid()) is None


def test_log_redaction_masks_before_truncation_and_preserves_default():
    from provider_safety import redact
    secret = "fixture-secret-key-1234567890"
    text = "a" * 795 + secret + "END"
    default = redact(text, [secret])
    assert len(default) == 800 and secret not in default
    extended = redact(text, [secret], max_chars=5000)
    assert extended.endswith("[MASKIERT]END") and secret not in extended
    assert len(redact("x" * 300000, max_chars=99999999)) == 250000
    assert redact("text", max_chars=-1) == ""


def test_active_child_survives_missing_supervisor_as_blocked_evidence(jobs):
    state(jobs, status="RUNNING", pid=999999999, process_identity="dead-supervisor",
          child_pid=os.getpid(), child_identity=jobs._process_identity(os.getpid()))
    overview = jobs.overview()
    assert overview["blocked"]
    assert overview["recent"][0]["status"] == "UNKNOWN"
