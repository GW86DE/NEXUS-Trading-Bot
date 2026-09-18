"""One durable passive diagnosis worker; no broker or order client is created.

The kernel lock is inherited by the detached worker. Closing the browser does
not abort an observation. A service restart may stop its child processes.
A dead worker is reported
as interrupted, never as a completed diagnosis. Archives remain outside source.
"""
from __future__ import annotations

try:
    import fcntl
except ImportError:  # Windows-Pruefstand: nur lesende Funktionen (summary/status) nutzbar
    fcntl = None
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid

from safe_persistence import atomic_write_json

ROOT = Path(__file__).resolve().parents[1]
ACTIVE = {"STARTING", "RUNNING", "SENDING"}
MAX_TELEGRAM_BYTES = 50_000_000


def job_dir():
    test_root = os.environ.get("TRADINGBOT_TEST_STATE_DIR")
    return Path(test_root) / "diagnosis_jobs" if test_root else output_dir() / ".nexus_jobs"


def output_dir():
    return Path.home() / "Downloads" / "NEXUS_Diagnosen"


def _path(identity):
    if not re.fullmatch(r"[0-9a-f]{32}", str(identity)):
        raise ValueError("Ungueltige Diagnosekennung")
    return job_dir() / (identity + ".json")


def _read(identity):
    p = _path(identity)
    if p.is_symlink():
        raise ValueError("Ungueltige Diagnosedatei")
    row = json.loads(p.read_text(encoding="utf-8"))
    if row.get("id") != identity:
        raise ValueError("Diagnosekennung widerspricht gespeicherten Daten")
    return row


def _write(row):
    row["updated_at"] = time.time()
    atomic_write_json(_path(row["id"]), row)


def _lock():
    directory = job_dir()
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    if fcntl is None:
        raise ValueError("Dateisperre auf dieser Plattform nicht verfuegbar")
    fd = os.open(directory / "worker.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        raise ValueError("Eine Diagnose oder ein ZIP-Versand laeuft bereits") from None
    return fd


def _orphaned():
    # Caller owns the exclusive lock: no previous worker can still be running.
    for p in sorted(job_dir().glob("*.json")):
        row = _read(p.stem)
        if row.get("status") in ACTIVE:
            sending = row.get("status") == "SENDING"
            row.update(status="COMPLETED" if row.get("archive") else "INTERRUPTED",
                       detail="Vorheriger Prozess beendet; vorhandene ZIP bleibt erhalten.")
            if sending:
                row["telegram_status"] = "UNKNOWN"
                row["detail"] = "Versandausgang unbekannt; kein automatischer Neuversand."
            _write(row)


def archive_path(identity):
    row = _read(identity)
    raw = row.get("archive")
    if not raw:
        raise ValueError("Noch keine abgeschlossene ZIP vorhanden")
    p = Path(raw)
    if (p.parent.resolve() != output_dir().resolve() or p.is_symlink()
            or not p.name.startswith("NEXUS_10_Diagnose_") or p.suffix != ".zip"
            or not p.is_file()):
        raise ValueError("ZIP ist nicht im erwarteten Diagnoseordner vorhanden")
    if row.get("archive_sha256") and _digest(p) != row["archive_sha256"]:
        raise ValueError("Diagnose-ZIP wurde seit der Erstellung veraendert")
    return p


def _digest(p):
    with p.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


SUMMARY_MAX_BYTES = 6_000_000


def _zip_json(archive, name, *, limit=SUMMARY_MAX_BYTES):
    """Read one JSON member of the verified ZIP; missing or oversized stays None."""
    import zipfile
    try:
        info = archive.getinfo(name)
    except KeyError:
        return None
    if info.file_size > limit:
        return {"_omitted": "Datei groesser als das Lesebudget", "size": info.file_size}
    try:
        return json.loads(archive.read(name).decode("utf-8"))
    except (ValueError, UnicodeDecodeError, zipfile.BadZipFile):
        return None


def summary(identity):
    """10.5.0: aufbereitete Sicht aus der verifizierten ZIP (ZUSAMMENFASSUNG.json).

    Aeltere ZIPs ohne ZUSAMMENFASSUNG.json bekommen eine Minimalsicht aus
    META.json und BEFUNDE.json. Es wird nichts entpackt und nichts veraendert.
    """
    import zipfile
    row = _read(identity)
    p = archive_path(identity)
    with zipfile.ZipFile(p) as archive:
        summary_doc = _zip_json(archive, "ZUSAMMENFASSUNG.json")
        meta = _zip_json(archive, "META.json") or {}
        findings = _zip_json(archive, "BEFUNDE.json")
        report = None
        try:
            info = archive.getinfo("BERICHT.md")
            if info.file_size <= 400_000:
                report = archive.read("BERICHT.md").decode("utf-8", errors="replace")
        except KeyError:
            report = None
        members = len(archive.namelist())
    if not isinstance(summary_doc, dict) or summary_doc.get("_omitted"):
        from collections import Counter
        items = findings if isinstance(findings, list) else []
        summary_doc = {"schema": 0, "fallback": True, "tool_version": meta.get("tool_version"),
                       "run_id": meta.get("run_id"), "completion": meta.get("completion"),
                       "observation_mode": meta.get("observation_mode"), "started_utc": meta.get("started_utc"),
                       "ended_utc": meta.get("ended_utc"), "observed_seconds": meta.get("observed_seconds"),
                       "runtime": {}, "decisions": {}, "gpt": {}, "pulsar": {"measurement": {}}, "sources": [],
                       "findings": {"counts": dict(Counter(str(f.get("level") or "INFO") for f in items if isinstance(f, dict))),
                                    "items": [{k: f.get(k) for k in ("component", "level", "code", "symbol", "meaning", "scope")}
                                              for f in items if isinstance(f, dict)][:200]},
                       "errors": [e if isinstance(e, dict) else {"message": str(e)[:200]} for e in (meta.get("errors") or [])][:50],
                       "limits": [], "meaning": "Aeltere Diagnose ohne ZUSAMMENFASSUNG.json; Minimalsicht aus META und BEFUNDEN."}
    return {"job": {k: row.get(k) for k in ("id", "status", "mode", "created_at", "finished_at", "completion", "detail",
                                            "telegram_status", "archive_sha256")},
            "archive": {"name": p.name, "size": p.stat().st_size, "members": members},
            "summary": summary_doc, "report_markdown": report}


def status():
    try:
        fd = _lock()
    except ValueError:
        fd = None
    if fd is not None:
        try:
            _orphaned()
        finally:
            os.close(fd)
    rows = []
    for p in sorted(job_dir().glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:40]:
        row = _read(p.stem)
        row["download_url"] = "/api/diagnosis/" + row["id"] + "/download" if row.get("archive") else None
        rows.append(row)
    from notifier import telegram_status
    telegram = telegram_status(active=False)
    return {"jobs": rows, "busy": fd is None, "output_directory": str(output_dir()),
            "telegram_available": bool(telegram.get("enabled") and telegram.get("configured")),
            "telegram_limit_bytes": MAX_TELEGRAM_BYTES}


def _spawn(row, fd, action):
    with (job_dir() / (row["id"] + ".log")).open("ab") as log:
        child = subprocess.Popen([sys.executable, "-m", "webui.diagnosis_jobs", action,
            row["id"], str(fd)], cwd=ROOT, stdin=subprocess.DEVNULL, stdout=log,
            stderr=subprocess.STDOUT, start_new_session=True, pass_fds=(fd,))
    # Worker owns the descriptor now; do not LOCK_UN the shared open description.
    return child.pid


def start(mode, telegram=False):
    if not isinstance(mode, str) or mode not in {"instant", "30min"} or type(telegram) is not bool:
        raise ValueError("Modus muss Sofort oder 30 Minuten sein")
    fd = _lock()
    row = None
    try:
        _orphaned()
        if telegram:
            from notifier import telegram_status
            t = telegram_status(active=False)
            if not (t.get("enabled") and t.get("configured")):
                raise ValueError("Telegram zuerst in den Einstellungen einrichten und aktivieren")
        row = {"id": uuid.uuid4().hex, "status": "STARTING", "phase": "START_EXPORT",
               "created_at": time.time(), "mode": mode, "telegram_requested": telegram,
               "telegram_status": "PENDING" if telegram else "NOT_REQUESTED",
               "detail": "Startsammlung wird vorbereitet.", "requested_seconds": 1800 if mode == "30min" else 0}
        _write(row)
        _spawn(row, fd, "--worker")
        return row
    except Exception:
        if row:
            row.update(status="FAILED", detail="Diagnoseprozess konnte nicht gestartet werden.")
            _write(row)
        raise
    finally:
        os.close(fd)


def send(identity):
    fd = _lock()
    try:
        _orphaned()
        row = _read(identity)
        archive_path(identity)
        from notifier import telegram_status
        telegram = telegram_status(active=False)
        if not (telegram.get("enabled") and telegram.get("configured")):
            raise ValueError("Telegram zuerst in den Einstellungen einrichten und aktivieren")
        if row.get("telegram_status") in {"SENT", "SENDING", "UNKNOWN"}:
            raise ValueError("ZIP bereits versendet oder Versandausgang ungeklärt; kein Doppelversand")
        row.update(status="SENDING", telegram_status="SENDING")
        _write(row)
        try:
            _spawn(row, fd, "--send")
        except Exception:
            row.update(status="COMPLETED", telegram_status="FAILED", detail="Versandprozess nicht gestartet.")
            _write(row)
            raise
        return row
    finally:
        os.close(fd)


def _deliver(row):
    p = archive_path(row["id"])
    if p.stat().st_size > MAX_TELEGRAM_BYTES:
        row.update(telegram_status="TOO_LARGE", detail="ZIP groesser als 50 MB; lokal herunterladen.")
        return
    from notifier import send_document
    row.update(status="SENDING", telegram_status="SENDING")
    _write(row)  # An ambiguous crash/timeout never silently sends twice.
    sent = send_document(p, "NEXUS-Diagnose · " + row["mode"] + " · " + str(row.get("completion", "")))
    row["telegram_status"] = "SENT" if sent else "UNKNOWN"
    row["detail"] = "ZIP ueber Telegram versendet." if sent else "Telegram-Versand nicht bestaetigt. ZIP bleibt lokal; kein automatischer Neuversand."


def worker(action, identity, lock_fd):
    row = _read(identity)
    try:
        if action == "--worker":
            import NEXUS_10_Diagnose as diagnostic
            row.update(status="RUNNING", started_at=time.time())
            _write(row)
            def progress(phase, **values):
                row.update(phase=phase, **values)
                if values.get("archive"):
                    row["archive_sha256"] = _digest(Path(values["archive"]))
                _write(row)
            args = ["--quelle", str(ROOT), "--ausgabe", str(output_dir())]
            args += ["--sofort"] if row["mode"] == "instant" else ["--minuten", "30"]
            code = diagnostic.main(args, progress=progress)
            row["exit_code"] = code
            row["status"] = "COMPLETED" if row.get("archive") else "FAILED"
            row["detail"] = "ZIP fertig. " + str(row.get("completion", "Sammlung fehlgeschlagen"))
            if row.get("archive") and row.get("telegram_requested"):
                _deliver(row)
        elif action == "--send":
            _deliver(row)
        row["status"] = "COMPLETED" if row.get("archive") else "FAILED"
        row["finished_at"] = time.time()
        _write(row)
    except BaseException as exc:
        row.update(status="COMPLETED" if row.get("archive") else "FAILED",
                   detail="Verarbeitung unterbrochen: " + type(exc).__name__, finished_at=time.time())
        if row.get("telegram_status") == "SENDING":
            row["telegram_status"] = "UNKNOWN"
        _write(row)
        raise
    finally:
        os.close(lock_fd)


if __name__ == "__main__":
    if len(sys.argv) != 4 or sys.argv[1] not in {"--worker", "--send"}:
        raise SystemExit("Nur als interner Diagnoseworker aufrufen")
    worker(sys.argv[1], sys.argv[2], int(sys.argv[3]))
