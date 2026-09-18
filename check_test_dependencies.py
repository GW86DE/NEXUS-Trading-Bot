"""Check mandatory test dependencies without importing NEXUS or contacting brokers.

The TestClient probe is in-process ASGI, not an HTTP request to a listening
server. Missing packages, wrong test-tool versions or broken imports fail the
check; nothing is installed, skipped or substituted by this program.
"""
from __future__ import annotations

import importlib.metadata as metadata
import platform
import re
import shlex
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def pinned_requirements(path: Path) -> dict[str, str]:
    """Read this release's deliberately small, exact test dependency manifest."""
    pins: dict[str, str] = {}
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        match = re.fullmatch(r"([A-Za-z0-9][A-Za-z0-9_.-]*)==([0-9]+(?:\.[0-9]+)*)", line)
        if match is None:
            raise ValueError(f"{path.name}:{number}: exakter stabiler Versionspin erforderlich")
        name = re.sub(r"[-_.]+", "-", match.group(1)).lower()
        if name in pins:
            raise ValueError(f"{path.name}:{number}: doppelter Paketname {name}")
        pins[name] = match.group(2)
    missing = {"pytest", "httpx"} - set(pins)
    if missing:
        raise ValueError("Pflicht-Testpakete nicht deklariert: " + ", ".join(sorted(missing)))
    return pins


def _probe_client() -> None:
    # Import the REAL libraries. No NEXUS app, credentials, broker or worker.
    import pytest  # noqa: F401 -- verifies that metadata alone is not enough
    import httpx  # noqa: F401
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()

    @app.get("/__nexus_test_probe")
    def probe() -> dict[str, bool]:
        return {"ok": True}

    with TestClient(app) as client:
        response = client.get("/__nexus_test_probe")
        if response.status_code != 200 or response.json() != {"ok": True}:
            raise RuntimeError("In-Process-TestClient liefert keine gueltige Testantwort")


def check(requirements_path: Path | None = None) -> list[str]:
    """Return diagnostic errors; an empty list means the test stack is usable."""
    path = requirements_path if requirements_path is not None else ROOT / "requirements-test.txt"
    try:
        pins = pinned_requirements(path)
    except (OSError, ValueError) as exc:
        return [f"Testmanifest nicht gueltig: {exc}"]
    errors: list[str] = []
    for name, expected in pins.items():
        try:
            actual = metadata.version(name)
        except metadata.PackageNotFoundError:
            errors.append(f"{name} fehlt; erforderlich: {name}=={expected}")
            continue
        except Exception as exc:
            errors.append(f"{name}: Paketmetadaten nicht lesbar: {exc}")
            continue
        if actual != expected:
            errors.append(f"{name}: installiert {actual}, erforderlich {expected}")
    if errors:
        return errors
    try:
        _probe_client()
    except Exception as exc:
        errors.append(f"Echter FastAPI/Starlette-TestClient nicht einsatzbereit: {type(exc).__name__}: {exc}")
    return errors


def main() -> int:
    print(f"Test-Python: {sys.executable}")
    print(f"Python: {platform.python_version()} / {platform.machine()}")
    errors = check()
    if errors:
        for error in errors:
            print("FEHLER:", error)
        print("Keine Testfreigabe. In der eigenen Projekt-.venv installieren, nicht mit sudo pip:")
        command = [sys.executable, "-m", "pip", "install", "--only-binary=:all:",
                   "-r", str(ROOT / "requirements-lock.txt"),
                   "-r", str(ROOT / "requirements-test.txt")]
        print(shlex.join(command))
        return 78
    for name in ("pytest", "httpx", "fastapi", "starlette", "httpcore", "anyio"):
        try:
            print(f"{name}: {metadata.version(name)}")
        except metadata.PackageNotFoundError:
            print(f"{name}: Versionsmetadaten nicht vorhanden")
    print("TESTABHAENGIGKEITEN OK (echter In-Process-TestClient; keine Brokeraufrufe)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
