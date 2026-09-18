"""Kleine prozess- und threaduebergreifende Dateisperre fuer kritischen Zustand.

Alle JSON-Zustaende im Geldpfad muessen ihre komplette Lese-Aendern-Schreib-
Folge unter derselben Sperre ausfuehren. Ein atomisches ``os.replace`` allein
verhindert zwar halbe Dateien, aber keinen Lost-Update zwischen zwei Schreibern.
"""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import os
import threading
import time


_THREAD_LOCKS: dict[str, threading.RLock] = {}
_THREAD_LOCKS_GUARD = threading.Lock()
_THREAD_STATE = threading.local()


def _thread_lock(path: Path) -> threading.RLock:
    key = str(path.resolve())
    with _THREAD_LOCKS_GUARD:
        return _THREAD_LOCKS.setdefault(key, threading.RLock())


@contextmanager
def critical_state_lock(target: str | Path, *, timeout_seconds: float = 15.0):
    """Sperrt den kritischen Zustand lokal und ueber Prozessgrenzen.

    ``target`` ist die Zustandsdatei, nicht die Lockdatei. So verwenden alle
    Aufrufer automatisch dieselbe ``.<name>.lock`` direkt daneben.
    """
    state = Path(target)
    lock_path = state.with_name(f".{state.name}.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    local = _thread_lock(lock_path)
    acquired = local.acquire(timeout=max(0.1, float(timeout_seconds)))
    if not acquired:
        raise TimeoutError(f"Kritische Zustandssperre nicht erhalten: {state}")
    # Verschachtelte Aufrufe im selben Thread werden bereits durch die
    # aeussere Prozesssperre geschuetzt. Ein zweites ``flock`` auf einem neuen
    # Dateideskriptor kann sich sonst selbst blockieren.
    key = str(lock_path.resolve())
    held = getattr(_THREAD_STATE, "held", None)
    if held is None:
        held = {}
        _THREAD_STATE.held = held
    if int(held.get(key, 0)) > 0:
        held[key] = int(held[key]) + 1
        try:
            yield
        finally:
            held[key] -= 1
            if held[key] <= 0:
                held.pop(key, None)
            local.release()
        return
    held[key] = 1
    handle = None
    process_locked = False
    try:
        handle = open(lock_path, "a+b")
        deadline = time.monotonic() + max(0.1, float(timeout_seconds))
        if os.name == "posix":
            import fcntl
            while True:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    process_locked = True
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError(
                            f"Kritische Prozesssperre nicht erhalten: {state}")
                    time.sleep(0.05)
        else:
            import msvcrt
            while True:
                try:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    process_locked = True
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError(
                            f"Kritische Prozesssperre nicht erhalten: {state}")
                    time.sleep(0.05)
        yield
    finally:
        if handle is not None:
            try:
                if process_locked and os.name == "posix":
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                elif process_locked:
                    import msvcrt
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            finally:
                handle.close()
        held[key] -= 1
        if held[key] <= 0:
            held.pop(key, None)
        local.release()
