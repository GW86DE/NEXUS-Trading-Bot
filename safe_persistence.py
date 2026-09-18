"""Robuste lokale Persistenz fuer Windows/OneDrive und parallele Threads.

Ziele:
- keine gemeinsam genutzte feste ``*.tmp``-Datei (Race Conditions vermeiden),
- ``os.replace`` bei kurzzeitigen Windows/OneDrive-Sperren wiederholen,
- nicht-kritische Telemetrie darf den Trading-Prozess NIEMALS beenden,
- kritische Zustandsdateien melden einen Fehler klar an den Aufrufer.
"""
from __future__ import annotations

import json
import errno
import logging
import os
import random
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _tmp_for(path: Path) -> Path:
    token = f"{os.getpid()}-{threading.get_ident()}-{random.randrange(1_000_000):06d}"
    return path.with_name(f".{path.name}.{token}.tmp")


def atomic_write_text(path: str | Path, text: str, *, encoding: str = "utf-8",
                      retries: int = 8, base_delay: float = 0.04,
                      durable: bool = True) -> None:
    """Schreibt Text atomar und toleriert kurzzeitige Windows-Dateisperren.

    ``PermissionError``/WinError 5 tritt bei OneDrive, Virenscannern und einem
    gleichzeitig lesenden GUI-Prozess gelegentlich nur fuer Millisekunden auf.
    Der Schreibvorgang wird deshalb mit exponentiellem, gedeckeltem Backoff
    wiederholt. Jede Wiederholung bekommt eine neue Temp-Datei.

    ``durable=True`` bestaetigt auf POSIX erst Datei-fsync, replace und
    Verzeichnis-fsync. Jeder Synchronisationsfehler wird weitergegeben.
    Scheitert der letzte Schritt, kann die neue Datei bereits sichtbar sein:
    eine Exception bedeutet dann UNBESTAETIGTE Dauerhaftigkeit, nicht Rollback.
    Unter Windows gilt Datei-fsync + replace; ein POSIX-Verzeichnis-fsync
    steht dort nicht zur Verfuegung. ``durable=False`` verspricht bewusst
    nur atomare Sichtbarkeit und ist fuer rekonstruierbare Telemetrie gedacht.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    last_exc: Exception | None = None

    for attempt in range(max(1, int(retries))):
        tmp = _tmp_for(target)
        replaced = False
        try:
            with open(tmp, "w", encoding=encoding, newline="") as fh:
                fh.write(text)
                fh.flush()
                if durable:
                    os.fsync(fh.fileno())
            os.replace(tmp, target)
            replaced = True
            # Auf Linux/Raspberry Pi ist fuer wirklich kritische Zustandsdateien
            # auch der Verzeichniseintrag zu synchronisieren. Nichtkritische
            # Telemetrie kann durable=False verwenden und spart damit SD-Schreiblast.
            if durable and os.name == "posix":
                fd = os.open(str(target.parent), os.O_RDONLY)
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
            return
        except (PermissionError, OSError) as exc:
            last_exc = exc
            try:
                tmp.unlink(missing_ok=True)
            except Exception:
                __import__("logging").getLogger(__name__).debug("Best-effort-Ausnahme unterdrueckt", exc_info=True)
            # EIO, ENOSPC, EROFS und nicht unterstuetztes fsync sind keine
            # Dateisperre. Nach replace keinesfalls den unbestaetigten Write
            # still erneut ueberdecken; der Aufrufer muss ihn abgleichen.
            transient = (exc.errno in {errno.EINTR, errno.EAGAIN, errno.EBUSY}
                         or (isinstance(exc, PermissionError)
                             and (os.name == "nt" or getattr(exc, "winerror", None)
                                  in {5, 32, 33})))
            if not replaced and transient and attempt + 1 < max(1, int(retries)):
                time.sleep(min(0.50, base_delay * (2 ** attempt)))
                continue
            break
        except Exception:
            try:
                tmp.unlink(missing_ok=True)
            except Exception:
                __import__("logging").getLogger(__name__).debug("Best-effort-Ausnahme unterdrueckt", exc_info=True)
            raise

    if last_exc is not None:
        raise last_exc
    raise OSError(f"Datei konnte nicht atomar geschrieben werden: {target}")


def atomic_write_json(path: str | Path, data: Any, *, indent: int = 2,
                      ensure_ascii: bool = False, retries: int = 8,
                      durable: bool = True) -> None:
    atomic_write_text(
        path,
        json.dumps(data, indent=indent, ensure_ascii=ensure_ascii),
        retries=retries,
        durable=durable,
    )


def best_effort_json(path: str | Path, data: Any, *, label: str = "Statusdatei",
                     warning_interval_seconds: float = 300.0,
                     durable: bool = True) -> bool:
    """Nicht-kritische Statusdatei schreiben, ohne den Bot abstuerzen zu lassen."""
    try:
        atomic_write_json(path, data, durable=durable)
        return True
    except Exception as exc:
        now = time.monotonic()
        key = str(Path(path).resolve())
        last = _WARNED_AT.get(key, 0.0)
        if now - last >= max(1.0, warning_interval_seconds):
            _WARNED_AT[key] = now
            logger.warning("%s konnte nicht gespeichert werden (%s): %s", label, path, exc)
        else:
            logger.debug("%s kurzzeitig nicht speicherbar (%s): %s", label, path, exc)
        return False


_WARNED_AT: dict[str, float] = {}
