"""Lokale, authentifizierte Analyse-Auftraege fuer die WebUI.

Die WebUI startet ausschliesslich fest hinterlegte Analyseprogramme.  Sie
veraendert weder Handelsparameter noch Brokerzustand und uebergibt keine frei
formulierbaren Shell-Befehle.  Ergebnisse werden lokal protokolliert, damit
Browser-Neuladen und ein Neustart der WebUI sie nicht verlieren.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import uuid
import signal
import threading
import errno

from safe_persistence import atomic_write_json
from state_lock import critical_state_lock
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JOB_DIR = ROOT / "runtime" / "webui_analysis"
MAX_OUTPUT_BYTES = 250_000
MAX_LOG_BYTES = 5_000_000
JOB_TIMEOUT_SECONDS = 1800
START_GRACE_SECONDS = 30

TASKS = {
    "backtest": {"label": "Backtest", "group": "Strategie", "script": "run_backtest.py",
                 "description": "Historischer Out-of-Sample-Test nach Kosten."},
    "walkforward": {"label": "Walk-Forward", "group": "Strategie", "script": "run_walkforward.py",
                    "description": "Zeitliche Stabilitaet in mehreren Testfenstern."},
    "crypto_backtest": {
        "label": "Krypto · Freqtrade Backtest", "group": "Krypto · Freqtrade",
        "script": "run_crypto_backtest.py",
        "description": ("SampleStrategy auf abgeschlossenen oeffentlichen "
                        "OKX-5m-Kerzen inklusive Kosten und Slippage.")},
    "crypto_walkforward": {
        "label": "Krypto · Walk-Forward", "group": "Krypto · Freqtrade",
        "script": "run_crypto_walkforward.py",
        "description": ("Chronologische Stabilitaet der Freqtrade-Regeln "
                        "ueber mehrere voneinander getrennte Testfenster.")},
    "profiles": {"label": "Profile vergleichen", "group": "Strategie", "script": "profile_vergleich.py",
                 "description": "Rendite und Drawdown der drei Risikoprofile."},
    "sweep": {"label": "Parameter / Symbol Sweep", "group": "Strategie", "script": "sweep.py",
              "description": "Robustheit ueber Symbole und Parameterkombinationen."},
    "ml": {"label": "ML-Modell trainieren", "group": "ML", "script": "train_model.py",
           "description": "Gemeinsames Modell mit chronologischem Split trainieren."},
    "news_check": {"label": "News-/Krisencheck", "group": "News & Research",
                   "script": "webui_news_check.py",
                   "description": "Aktuelle Marktlage mit dem bestehenden Nachrichtenfilter pruefen."},
    "underdogs": {"label": "Underdog Screening", "group": "News & Research",
                  "script": "underdog_screening.py",
                  "description": "Kandidaten mit den bestehenden NEXUS-Kriterien pruefen."},
    "news_sources": {"label": "Newsquellen pruefen", "group": "News & Research",
                     "script": "news_sources_status.py",
                     "description": "Verfuegbarkeit der konfigurierten Research-Quellen."},
}


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _job_id(value: str) -> str:
    value = str(value or "")
    if not value or len(value) > 80 or any(c not in
            "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for c in value):
        raise ValueError("Ungueltige Auftrags-ID")
    return value


def _state_path(job_id: str) -> Path:
    return JOB_DIR / f"{_job_id(job_id)}.json"


def _log_path(job_id: str) -> Path:
    return JOB_DIR / f"{_job_id(job_id)}.log"


def _admission_lock():
    return critical_state_lock(JOB_DIR / "admission")


def _write(path: Path, payload: dict) -> None:
    atomic_write_json(path, payload)


def _read(path: Path) -> dict:
    try:
        if path.stat().st_size > 65536:
            return {}
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _process_identity(pid) -> str | None:
    """Positive PID plus kernel creation identity; never signal PID zero."""
    try:
        if isinstance(pid, bool) or not isinstance(pid, (str, int)):
            return None
        pid = int(pid)
        if pid <= 0:
            return None
        if sys.platform.startswith("linux"):
            # A container can expose a host /proc mount while os.getpid and
            # Popen use an inner PID namespace. pidfd_open resolves the local
            # PID in the kernel; fdinfo reports its PID in this /proc mount.
            # Never accidentally attribute host PID 5 to local process 5.
            fd = None
            try:
                opener = getattr(os, "pidfd_open", None)
                if callable(opener):
                    try:
                        fd = opener(pid, 0)
                    except OSError as exc:
                        if exc.errno not in {errno.ENOSYS, errno.EINVAL}:
                            raise
                if fd is not None:
                    info = Path(f"/proc/self/fdinfo/{fd}").read_text(encoding="ascii")
                    entry = next(line.partition(":")[2] for line in info.splitlines() if line.startswith("Pid:"))
                    proc_pid = int(entry.strip())
                    if proc_pid <= 0:
                        return None
                else:
                    if int(os.readlink("/proc/self")) != os.getpid():
                        return None  # No reliable namespace mapping available.
                    proc_pid = pid
                stat = Path(f"/proc/{proc_pid}/stat").read_text(encoding="utf-8")
                fields = stat.rsplit(") ", 1)[1].split()
                if fields[0] in {"Z", "X"} or int(stat.split(" ", 1)[0]) != proc_pid:
                    return None
                boot = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip()
                return f"linux:{boot}:{proc_pid}:{fields[19]}"
            finally:
                if fd is not None:
                    os.close(fd)
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.OpenProcess.restype = wintypes.HANDLE
            kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            handle = kernel.OpenProcess(0x1000, False, pid)
            if not handle:
                return None
            try:
                exit_code = wintypes.DWORD()
                kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
                if not kernel.GetExitCodeProcess(handle, ctypes.byref(exit_code)) or exit_code.value != 259:
                    return None
                created, exited, system, user = [wintypes.FILETIME() for _ in range(4)]
                kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
                if not kernel.GetProcessTimes(handle, *[ctypes.byref(x) for x in (created, exited, system, user)]):
                    return None
                return f"windows:{created.dwHighDateTime}:{created.dwLowDateTime}"
            finally:
                kernel.CloseHandle.argtypes = [wintypes.HANDLE]
                kernel.CloseHandle(handle)
    except (OSError, ValueError, TypeError, IndexError, StopIteration):
        return None
    return None


def _pid_alive(pid) -> bool:
    try:
        if isinstance(pid, bool) or not isinstance(pid, (str, int)):
            return False
        pid = int(pid)
        if pid <= 0:
            return False
        os.kill(pid, 0)
        return True
    except (OSError, TypeError, ValueError):
        return False


def _running(state: dict) -> bool:
    if state.get("status") not in {"RUNNING", "STARTING"}:
        return False
    expected = str(state.get("process_identity") or "")
    return bool(expected and _process_identity(state.get("pid")) == expected)


def _normalise(state: dict) -> dict:
    # Caller holds the shared admission lock; a completed worker cannot be
    # overwritten by an old status poll while this transition is written.
    status = state.get("status")
    if status not in {"RUNNING", "STARTING"} or _running(state):
        return state
    if status == "STARTING" and not state.get("pid"):
        try:
            age = time.time() - float(state.get("started_epoch", 0))
        except (ValueError, TypeError):
            age = START_GRACE_SECONDS + 1
        if 0 <= age <= START_GRACE_SECONDS:
            return state
    child_identity = str(state.get("child_identity") or "")
    child_alive = bool(child_identity and _process_identity(state.get("child_pid")) == child_identity)
    # Existing releases did not record creation identity. A live unproven PID
    # is UNKNOWN, not evidence of this job and not permission for another job.
    unproven_live = not state.get("process_identity") and _pid_alive(state.get("pid"))
    state = {**state, "status": "UNKNOWN" if child_alive or unproven_live else "INTERRUPTED",
             "finished_at": None if child_alive or unproven_live else _utc(),
             "detail": ("Analyseprozess noch aktiv oder Identitaet nicht belegbar; weitere Starts gesperrt."
                        if child_alive or unproven_live else
                        "Analyseprozess fehlt oder Prozesskennung wurde wiederverwendet.")}
    if state.get("job_id"):
        _write(_state_path(state["job_id"]), state)
    return state


def _recent(limit: int | None = 20) -> list[dict]:
    with _admission_lock():
        states = []
        for path in JOB_DIR.glob("*.json"):
            state = _read(path)
            if not state or state.get("job_id") != path.stem or state.get("task") not in TASKS:
                # Preserve damaged job evidence and refuse a new job rather
                # than silently forgetting a possibly still-running worker.
                state = {"job_id": path.stem, "task": "unknown", "label": "Unlesbarer Analyseauftrag",
                         "status": "UNKNOWN", "detail": "Auftragsdatei muss geprueft werden."}
            else:
                state = _normalise(state)
            states.append(state)
        states.sort(key=lambda s: str(s.get("started_at") or ""), reverse=True)
        return states if limit is None else states[:max(0, int(limit))]


def overview() -> dict:
    all_states = _recent(None)
    recent = all_states[:20]
    latest = {}
    for state in recent:
        latest.setdefault(state["task"], state)
    tasks = []
    for task_id, spec in TASKS.items():
        state = latest.get(task_id, {})
        tasks.append({"id": task_id, **spec,
                      "status": state.get("status", "NEVER"),
                      "started_at": state.get("started_at"),
                      "finished_at": state.get("finished_at"),
                      "job_id": state.get("job_id")})
    return {"tasks": tasks, "recent": recent,
            "running": any(item.get("status") in {"RUNNING", "STARTING"} for item in all_states),
            "blocked": any(item.get("status") == "UNKNOWN" for item in all_states),
            "timeout_seconds": JOB_TIMEOUT_SECONDS, "max_log_bytes": MAX_LOG_BYTES}


def _child_environment() -> dict:
    env = dict(os.environ)
    for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
                 "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "BLIS_NUM_THREADS"):
        env[name] = "1"
    return env


def start(task: str, actor: str = "webui") -> dict:
    task = str(task or "").strip().lower()
    if task not in TASKS:
        raise ValueError("Unbekannte Analyse")
    with _admission_lock():
        if any(item.get("status") in {"STARTING", "RUNNING", "UNKNOWN"} for item in _recent(None)):
            raise ValueError("Es laeuft bereits eine Analyse oder ihr Prozessstatus muss geprueft werden.")
        job_id = f"{int(time.time())}-{uuid.uuid4().hex[:8]}"
        state = {"job_id": job_id, "task": task, "label": TASKS[task]["label"],
                 "status": "STARTING", "started_at": _utc(), "started_epoch": time.time(),
                 "finished_at": None, "actor": str(actor)[:80], "pid": None, "returncode": None,
                 "timeout_seconds": JOB_TIMEOUT_SECONDS}
        _write(_state_path(job_id), state)
        command = [sys.executable, "-m", "webui.analysis_jobs", "--worker", task, job_id]
        try:
            process = subprocess.Popen(command, cwd=str(ROOT), stdin=subprocess.DEVNULL,
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                       env=_child_environment(), start_new_session=True, close_fds=True)
        except Exception as exc:
            state.update(status="FAILED", finished_at=_utc(), detail=f"Start fehlgeschlagen: {type(exc).__name__}")
            _write(_state_path(job_id), state)
            raise RuntimeError("Analyseprozess konnte nicht gestartet werden") from exc
        state.update(status="RUNNING", pid=process.pid, process_identity=_process_identity(process.pid))
        _write(_state_path(job_id), state)
        return state


def output(job_id: str) -> dict:
    job_id = _job_id(job_id)
    with _admission_lock():
        state = _normalise(_read(_state_path(job_id)))
    if not state:
        raise FileNotFoundError(job_id)
    try:
        with _log_path(job_id).open("rb") as stream:
            stream.seek(0, os.SEEK_END)
            size = stream.tell()
            stream.seek(max(0, size - MAX_OUTPUT_BYTES), os.SEEK_SET)
            raw = stream.read(MAX_OUTPUT_BYTES)
        prefix = "[... aeltere Ausgabe gekuerzt ...]\n" if size > MAX_OUTPUT_BYTES else ""
        text = raw.decode("utf-8", errors="replace")
    except FileNotFoundError:
        prefix = ""
        text = "Analyse wird gestartet ..."
    from provider_safety import redact
    # One bounded redaction pass avoids repeated credential-cache lookups
    # for thousands of short log lines on the Raspberry Pi.
    return {"state": state, "output": prefix + redact(text, max_chars=MAX_OUTPUT_BYTES)}


def _terminate_child(process) -> None:
    """Terminate only this newly created child/session, never a persisted PID."""
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGTERM)
            # Keep the leader unreaped during the grace period so its PID
            # cannot be recycled before the final process-group signal.
            threading.Event().wait(.2)
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        else:
            process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            pass
        # The leader can exit before its children. The known fresh session
        # still needs a kill after timeout/output overflow.
        if os.name != "posix" and process.poll() is None:
            process.kill()
        process.wait(timeout=3)
    except ProcessLookupError:
        pass


def _run_script(script: Path, log: Path, state: dict) -> tuple[int, str, str]:
    limit_hit = threading.Event()
    reader_error = []
    with log.open("wb") as stream:
        stream.write(f"NEXUS Analyse: {state['label']}\nStart: {_utc()}\n\n".encode("utf-8"))
        stream.flush()
        process = subprocess.Popen([sys.executable, "-u", str(script)], cwd=str(ROOT),
                                   stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, env=_child_environment(),
                                   start_new_session=True, close_fds=True)
        state.update(child_pid=process.pid, child_identity=_process_identity(process.pid))
        try:
            with _admission_lock():
                _write(_state_path(state["job_id"]), state)
        except Exception:
            _terminate_child(process)
            raise
        def drain():
            total = stream.tell()
            try:
                while True:
                    # BufferedReader.read(n) may wait until n bytes arrive;
                    # read1 exposes already-flushed progress promptly.
                    chunk = process.stdout.read1(65536)
                    if not chunk:
                        break
                    remaining = max(0, MAX_LOG_BYTES - total)
                    stream.write(chunk[:remaining])
                    stream.flush()
                    total += min(remaining, len(chunk))
                    if len(chunk) > remaining:
                        limit_hit.set()
                        break
            except Exception as exc:
                reader_error.append(type(exc).__name__)
                limit_hit.set()
        reader = threading.Thread(target=drain, name="nexus-analysis-output", daemon=True)
        reader.start()
        deadline = time.monotonic() + JOB_TIMEOUT_SECONDS
        status, detail = "", ""
        try:
            while process.poll() is None:
                if limit_hit.wait(.1):
                    status, detail = "OUTPUT_LIMIT", "Ausgabelimit erreicht; Analyseprozessgruppe beendet."
                    break
                if time.monotonic() >= deadline:
                    status, detail = "TIMED_OUT", "Zeitlimit erreicht; Analyseprozessgruppe beendet."
                    break
            if status:
                _terminate_child(process)
            else:
                process.wait(timeout=3)
            reader.join(timeout=3)
            if reader.is_alive():
                _terminate_child(process)
                reader.join(timeout=3)
                status, detail = "FAILED", "Analyseausgabe wurde nicht geschlossen; Prozessgruppe beendet."
            if reader_error:
                status, detail = "FAILED", f"Analyseausgabe nicht speicherbar: {reader_error[0]}"
            elif limit_hit.is_set() and not status:
                status, detail = "OUTPUT_LIMIT", "Ausgabelimit erreicht."
            rc = int(process.returncode if process.returncode is not None else 1)
            return rc, status or ("SUCCEEDED" if rc == 0 else "FAILED"), detail
        finally:
            if process.poll() is None:
                _terminate_child(process)
            if not reader.is_alive() and process.stdout is not None:
                process.stdout.close()


def _worker(task: str, job_id: str) -> int:
    if task not in TASKS:
        raise ValueError("Unbekannte Analyse")
    _job_id(job_id)
    with _admission_lock():
        state = _read(_state_path(job_id))
        identity = _process_identity(os.getpid())
        if (state.get("job_id") != job_id or state.get("task") != task
                or state.get("status") not in {"STARTING", "RUNNING"}
                or (state.get("pid") and int(state["pid"]) != os.getpid())
                or (state.get("process_identity") and state["process_identity"] != identity)):
            raise RuntimeError("Analyseauftrag fehlt oder gehoert einem anderen Worker")
        state.update(status="RUNNING", pid=os.getpid(), process_identity=identity)
        _write(_state_path(job_id), state)
    log = _log_path(job_id)
    try:
        rc, status, detail = _run_script(ROOT / TASKS[task]["script"], log, state)
    except Exception as exc:
        rc, status, detail = 1, "FAILED", f"Analyse konnte nicht ausgefuehrt werden: {type(exc).__name__}"
    state.update(status=status, detail=detail, returncode=rc, finished_at=_utc())
    with _admission_lock():
        _write(_state_path(job_id), state)
    return 0 if status == "SUCCEEDED" else (rc or 1)


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "--worker" and sys.argv[2] in TASKS:
        raise SystemExit(_worker(sys.argv[2], sys.argv[3]))
    raise SystemExit("Nur als NEXUS-Analyseworker aufrufen.")
