"""One durable read-only backtest worker; no broker or order client is created.

Der Worker startet das eigenstaendige, read-only Werkzeug
``NEXUS_Universum_Backtest_V6.sh`` aus dem Quellordner. Es erstellt keine
Orders und veraendert keine NEXUS-Daten; das Skript besitzt zusaetzlich einen
eigenen Ausgabeordner-Lock. Das Schliessen des Browsers bricht einen Lauf
nicht ab. Ein toter Worker wird als unterbrochen gemeldet, niemals als
abgeschlossener Backtest. Ergebnisse (HTML/ZIP) bleiben ausserhalb der Quelle.
"""
from __future__ import annotations

import fcntl
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
SCRIPT = ROOT / "NEXUS_Universum_Backtest_V6.sh"
ACTIVE = {"STARTING", "RUNNING"}
SCOPES = {"fokus", "aktiv"}
LOG_TAIL_LINES = 200


def job_dir():
    test_root = os.environ.get("TRADINGBOT_TEST_STATE_DIR")
    return Path(test_root) / "backtest_jobs" if test_root else output_dir() / ".nexus_jobs"


def output_dir():
    return Path.home() / "NEXUS_Backtests"


def _path(identity):
    if not re.fullmatch(r"[0-9a-f]{32}", str(identity)):
        raise ValueError("Ungueltige Backtestkennung")
    return job_dir() / (identity + ".json")


def _read(identity):
    p = _path(identity)
    if p.is_symlink():
        raise ValueError("Ungueltige Backtestdatei")
    row = json.loads(p.read_text(encoding="utf-8"))
    if row.get("id") != identity:
        raise ValueError("Backtestkennung widerspricht gespeicherten Daten")
    return row


def _write(row):
    row["updated_at"] = time.time()
    atomic_write_json(_path(row["id"]), row)


def _lock():
    directory = job_dir()
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(directory / "worker.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        raise ValueError("Ein Backtest laeuft bereits") from None
    return fd


def _orphaned():
    # Caller owns the exclusive lock: no previous worker can still be running.
    for p in sorted(job_dir().glob("*.json")):
        row = _read(p.stem)
        if row.get("status") in ACTIVE:
            row.update(status="COMPLETED" if row.get("archive") else "INTERRUPTED",
                       detail="Vorheriger Prozess beendet; vorhandene Ergebnisse bleiben erhalten.")
            _write(row)


def _digest(p):
    with p.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def _result_file(identity, key, *, suffix, media_word):
    row = _read(identity)
    raw = row.get(key)
    if not raw:
        raise ValueError(f"Noch kein fertiger Backtest-{media_word} vorhanden")
    p = Path(raw)
    base = output_dir().resolve()
    resolved = p.resolve()
    # Das Werkzeug legt die ZIP als NEXUS_Backtest_<stamp>_V5.zip in den
    # Ausgabeordner und den Bericht als BACKTEST_REPORT.html in den
    # Laufordner NEXUS_Backtest_<stamp>/ (Rev-1-Fehler: nur der erste Name
    # war zugelassen, der Bericht liess sich deshalb nicht oeffnen).
    named_ok = (p.name.startswith("NEXUS_Backtest")
                or (p.name == "BACKTEST_REPORT.html"
                    and resolved.parent.name.startswith("NEXUS_Backtest")))
    if (not named_ok or p.suffix != suffix or p.is_symlink()
            or base not in (resolved.parent, *resolved.parents) or not p.is_file()):
        raise ValueError(f"Backtest-{media_word} ist nicht im erwarteten Ergebnisordner vorhanden")
    digest_key = key + "_sha256"
    if row.get(digest_key) and _digest(p) != row[digest_key]:
        raise ValueError(f"Backtest-{media_word} wurde seit der Erstellung veraendert")
    return p


def archive_path(identity):
    return _result_file(identity, "archive", suffix=".zip", media_word="ZIP")


def report_path(identity):
    return _result_file(identity, "report", suffix=".html", media_word="Bericht")


def log_tail(identity, limit=LOG_TAIL_LINES):
    _read(identity)  # validiert die Kennung
    p = job_dir() / (str(identity) + ".log")
    if not p.is_file() or p.is_symlink():
        return []
    raw = p.read_bytes()[-400_000:]
    lines = raw.decode("utf-8", errors="replace").splitlines()
    return lines[-max(1, min(int(limit), 1000)):]


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
        row["download_url"] = "/api/backtest/" + row["id"] + "/download" if row.get("archive") else None
        row["report_url"] = "/api/backtest/" + row["id"] + "/report" if row.get("report") else None
        rows.append(row)
    return {"jobs": rows, "busy": fd is None, "output_directory": str(output_dir()),
            "script_available": SCRIPT.is_file()}


def _validated_options(options):
    if not isinstance(options, dict) or set(options) - {"umfang", "aktien_limit", "krypto_limit", "nur_plan"}:
        raise ValueError("Ungueltige Backtestparameter")
    scope = options.get("umfang", "fokus")
    if scope not in SCOPES:
        raise ValueError("Umfang muss Fokus oder Aktiv sein")
    try:
        stocks = int(options.get("aktien_limit", 15))
        crypto = int(options.get("krypto_limit", 5))
    except (TypeError, ValueError):
        raise ValueError("Limits muessen ganze Zahlen sein") from None
    if not 1 <= stocks <= 40 or not 1 <= crypto <= 10:
        raise ValueError("Aktienlimit 1-40 und Kryptolimit 1-10 einhalten")
    if type(options.get("nur_plan", False)) is not bool:
        raise ValueError("Ungueltige Backtestparameter")
    return {"umfang": scope, "aktien_limit": stocks, "krypto_limit": crypto,
            "nur_plan": options.get("nur_plan", False)}


def _spawn(row, fd):
    with (job_dir() / (row["id"] + ".log")).open("ab") as log:
        child = subprocess.Popen([sys.executable, "-m", "webui.backtest_jobs", "--worker",
            row["id"], str(fd)], cwd=ROOT, stdin=subprocess.DEVNULL, stdout=log,
            stderr=subprocess.STDOUT, start_new_session=True, pass_fds=(fd,))
    # Worker owns the descriptor now; do not LOCK_UN the shared open description.
    return child.pid


def start(options):
    checked = _validated_options(options or {})
    if not SCRIPT.is_file():
        raise ValueError("Backtest-Werkzeug fehlt im Quellordner")
    fd = _lock()
    row = None
    try:
        _orphaned()
        row = {"id": uuid.uuid4().hex, "status": "STARTING", "created_at": time.time(),
               "options": checked, "detail": "Backtest wird vorbereitet.",
               "mode": "Nur Plan" if checked["nur_plan"] else
                       ("Fokus" if checked["umfang"] == "fokus" else "Aktives Universum")}
        _write(row)
        _spawn(row, fd)
        return row
    except Exception:
        if row:
            row.update(status="FAILED", detail="Backtestprozess konnte nicht gestartet werden.")
            _write(row)
        raise
    finally:
        os.close(fd)


def _script_arguments(options):
    args = ["--nexus-root", str(ROOT), "--ausgabe", str(output_dir()),
            "--umfang", options["umfang"],
            "--aktien-limit", str(options["aktien_limit"]),
            "--krypto-limit", str(options["krypto_limit"])]
    if options.get("nur_plan"):
        args.append("--nur-plan")
    return args


def worker(identity, lock_fd):
    row = _read(identity)
    log_file = job_dir() / (identity + ".log")
    try:
        row.update(status="RUNNING", started_at=time.time())
        _write(row)
        command = ["/bin/bash", str(SCRIPT)] + _script_arguments(row["options"])
        report = archive = None
        last_update = 0.0
        with log_file.open("ab") as log:
            child = subprocess.Popen(command, cwd=ROOT, stdin=subprocess.DEVNULL,
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            for raw in child.stdout:
                log.write(raw)
                log.flush()
                line = raw.decode("utf-8", errors="replace").rstrip()
                found = re.match(r"^(HTML|ZIP):\s+(.+)$", line)
                if found:
                    if found.group(1) == "HTML":
                        report = found.group(2).strip()
                    else:
                        archive = found.group(2).strip()
                now = time.monotonic()
                if line and now - last_update >= 2.0:
                    row["detail"] = line[:300]
                    _write(row)
                    last_update = now
            code = child.wait()
        row["exit_code"] = code
        if archive:
            p = Path(archive)
            if p.is_file():
                row["archive"] = str(p)
                row["archive_sha256"] = _digest(p)
        if report:
            p = Path(report)
            if p.is_file():
                row["report"] = str(p)
                row["report_sha256"] = _digest(p)
        plan_only = bool(row["options"].get("nur_plan"))
        ok = code == 0 and (plan_only or row.get("archive"))
        row["status"] = "COMPLETED" if ok else "FAILED"
        row["detail"] = ("Plan erstellt; kein Netzabruf, keine Ergebnis-ZIP." if ok and plan_only else
                         "Backtest abgeschlossen. HTML-Bericht und ZIP liegen bereit." if ok else
                         f"Backtest beendet mit Fehlercode {code}; Protokoll pruefen.")
        row["finished_at"] = time.time()
        _write(row)
    except BaseException as exc:
        row.update(status="COMPLETED" if row.get("archive") else "FAILED",
                   detail="Verarbeitung unterbrochen: " + type(exc).__name__,
                   finished_at=time.time())
        _write(row)
        raise
    finally:
        os.close(lock_fd)


if __name__ == "__main__":
    if len(sys.argv) != 4 or sys.argv[1] != "--worker":
        raise SystemExit("Nur als interner Backtestworker aufrufen")
    worker(sys.argv[2], int(sys.argv[3]))
