"""State-free offline validation for an installed NEXUS source tree.

No trading module is imported in the installed tree. Only manifest-listed,
hash-verified release files enter a private working copy. Credentials, state,
HOME settings, inherited API variables and unlisted executable files never
become test inputs. This is regression isolation, not a hostile-code sandbox.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tempfile

MANIFEST = "MANIFEST_SHA256.json"
CACHE_DIRS = {".venv", "venv", "env", "site-packages", "__pycache__",
              ".pytest_cache", ".mypy_cache", ".ruff_cache", ".git", "build", "dist"}
EXECUTABLE_SUFFIXES = {".py", ".pyw", ".sh", ".bat", ".js", ".html", ".css", ".template"}
ALLOW_ENV = {"PATH", "LANG", "LC_ALL", "LC_CTYPE", "TZ", "SYSTEMROOT", "SystemRoot",
             "WINDIR", "COMSPEC", "PATHEXT"}


class IsolationError(RuntimeError):
    """Validation could not be isolated; do not run tests or install services."""


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_manifest(root: Path) -> dict[str, str]:
    path = root / MANIFEST
    if path.is_symlink() or not path.is_file():
        raise IsolationError("Release-Dateimanifest fehlt oder ist ein Symlink")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise IsolationError("Release-Dateimanifest ist nicht lesbar") from exc
    if not isinstance(data, dict) or not data:
        raise IsolationError("Release-Dateimanifest ist leer/ungueltig")
    for name, expected in data.items():
        if (not isinstance(name, str) or not name or "\\" in name or ":" in name
                or PurePosixPath(name).is_absolute()
                or any(part in {"..", "."} for part in name.split("/"))
                or any(not part for part in name.split("/"))
                or any(part in CACHE_DIRS for part in PurePosixPath(name).parts)
                or name == MANIFEST
                or not isinstance(expected, str)
                or not re.fullmatch(r"[0-9a-f]{64}", expected)):
            raise IsolationError("Ungueltiger Eintrag im Release-Dateimanifest")
    return data


def _safe_file(root: Path, name: str) -> Path:
    path = root / name
    # Do not follow a symlink at any level, even one pointing inside the tree.
    cursor = root
    for part in PurePosixPath(name).parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise IsolationError(f"Symlink statt Release-Datei: {name}")
    if not path.is_file():
        raise IsolationError(f"Release-Datei fehlt: {name}")
    return path


def extra_files(root: Path, manifest: dict[str, str]) -> list[Path]:
    extras = []
    for directory, subdirs, names in os.walk(root, followlinks=False):
        parent = Path(directory)
        kept = []
        for item in subdirs:
            path = parent / item
            if item in CACHE_DIRS:
                continue
            if path.is_symlink():
                raise IsolationError("Nicht freigegebener Verzeichnis-Symlink im Quellbaum")
            kept.append(item)
        subdirs[:] = kept
        for name in names:
            path = parent / name
            relative = path.relative_to(root).as_posix()
            if relative != MANIFEST and relative not in manifest:
                extras.append(path)
    return extras


def prepare_copy(root: Path, target: Path, *, allow_local_state: bool) -> dict[str, int]:
    """Verify every release byte, copy source, never open unlisted user data."""
    root = root.resolve(strict=True)
    if target.exists():
        raise IsolationError("Test-Zielordner muss neu sein")
    manifest = read_manifest(root)
    extra = extra_files(root, manifest)
    # New source files must not be silently omitted from tests or hygiene checks.
    code_extra = [p for p in extra if p.suffix.lower() in EXECUTABLE_SUFFIXES]
    if code_extra:
        raise IsolationError("Nicht manifestierte Quell-/Testdatei: "
                             + code_extra[0].relative_to(root).as_posix())
    if extra and not allow_local_state:
        raise IsolationError("Lokale Daten im Quellordner. Fuer installierte Baeume: "
                             "TRADINGBOT_ALLOW_LOCAL_STATE=1 python volltest.py. "
                             "Nichts loeschen; lokale Daten werden NICHT mitgetestet.")
    target.mkdir(parents=True, mode=0o700)
    for name, expected in manifest.items():
        source = _safe_file(root, name)
        content = source.read_bytes()
        if hashlib.sha256(content).hexdigest() != expected:
            raise IsolationError(f"Release-Datei wurde veraendert: {name}")
        destination = target / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
        destination.chmod(source.stat().st_mode & 0o777)
    (target / MANIFEST).write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return {"release_files": len(manifest), "local_files_excluded": len(extra)}


def test_environment(source: Path, temporary: Path, inherited: dict[str, str]) -> dict[str, str]:
    """Allowlist OS essentials; no inherited credentials, profiles or plugins."""
    env = {k: v for k, v in inherited.items() if k in ALLOW_ENV}
    env.setdefault("PATH", os.defpath)
    env.setdefault("LANG", "C.UTF-8")
    for dirname in ("home", "tmp", "state", "config", "cache", "data"):
        (temporary / dirname).mkdir(parents=True, exist_ok=True)
    env.update({
        "HOME": str(temporary / "home"), "USERPROFILE": str(temporary / "home"),
        "TMPDIR": str(temporary / "tmp"), "TEMP": str(temporary / "tmp"),
        "TMP": str(temporary / "tmp"), "XDG_CONFIG_HOME": str(temporary / "config"),
        "XDG_CACHE_HOME": str(temporary / "cache"), "XDG_DATA_HOME": str(temporary / "data"),
        "TRADINGBOT_TEST_STATE_DIR": str(temporary / "state"),
        "TRADING_MODE": "paper", "PYTHONUNBUFFERED": "1",
        # Re-establish this contract AFTER the environment allowlist. The
        # installer exports these too, but inherited PYTHON* settings must not
        # be trusted or blindly retained. Keep the user's locale and timezone;
        # only this isolated Python process tree uses the UTF-8 text contract.
        "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8:strict",
        "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        "NEXUS_OFFLINE_TEST_ROOT": str(source.resolve()),
        "NEXUS_OFFLINE_NETWORK_EVENTS": str(temporary / "network-events.txt"),
        "PYTHONPATH": os.pathsep.join([str(source / "offline_test_bootstrap"), str(source)]),
    })
    return env


def refuse_unsafe_direct_pytest(root: Path) -> None:
    """Early conftest check: no bot import from a configured install via pytest."""
    if os.getenv("NEXUS_OFFLINE_TEST_ROOT") == str(root.resolve()):
        return
    inherited = {k for k in os.environ if k.startswith((
        "TELEGRAM_", "ETORO_", "OKX_", "OPENAI_", "FMP_", "FINNHUB_", "MASSIVE_",
        "ALPHAVANTAGE_", "AI_", "NOTIFY_", "SEC_"))}
    if inherited or os.getenv("TRADING_MODE", "paper").lower() != "paper":
        raise IsolationError("Direkter pytest-Aufruf mit Handels-/API-Umgebung abgelehnt. "
                             "Bitte python volltest.py verwenden.")
    manifest = read_manifest(root)
    if extra_files(root, manifest):
        raise IsolationError("Direkter pytest-Aufruf in konfiguriertem Ordner abgelehnt. "
                             "TRADINGBOT_ALLOW_LOCAL_STATE=1 python volltest.py verwenden.")


def run_isolated(root: Path) -> int:
    """Run all original check groups in a fresh source projection, then discard."""
    root = root.resolve()
    try:
        original_manifest_hash = digest(root / MANIFEST)
        with tempfile.TemporaryDirectory(prefix="nexus_offline_") as name:
            temporary = Path(name)
            source = temporary / "source"
            stats = prepare_copy(root, source, allow_local_state=(
                os.getenv("TRADINGBOT_ALLOW_LOCAL_STATE", "").strip() == "1"))
            if digest(root / MANIFEST) != original_manifest_hash:
                raise IsolationError("Release-Manifest waehrend der Kopie veraendert")
            env = test_environment(source, temporary, dict(os.environ))
            print("TESTISOLATION FIX1: "
                  f"{stats['release_files']} Release-Dateien hashgeprueft; "
                  f"{stats['local_files_excluded']} lokale Dateien nicht eingelesen.", flush=True)
            print("Privates Testverzeichnis; keine Live-Konfiguration, "
                  "keine geerbten API-Schluessel. HTTP-/Socket-Schutz aktiv.", flush=True)
            command = [sys.executable, str(source / "offline_test_bootstrap" / "run_checks.py")]
            process = subprocess.run(command, cwd=source, env=env)
            events = temporary / "network-events.txt"
            if events.exists() and events.stat().st_size:
                print("FEHLER: Ein Test versuchte echten Netzwerkverkehr. "
                      "Die Verbindung wurde blockiert; keine Testfreigabe.", flush=True)
                print(events.read_text(encoding="utf-8")[:5000], flush=True)
                return 1
            # A race or a test must not silently change the actual install's code.
            if digest(root / MANIFEST) != original_manifest_hash:
                raise IsolationError("Release-Manifest waehrend der Pruefung veraendert")
            manifest = read_manifest(root)
            for relative, expected in manifest.items():
                if digest(_safe_file(root, relative)) != expected:
                    raise IsolationError(f"Quellstand waehrend der Pruefung veraendert: {relative}")
            return process.returncode
    except (IsolationError, OSError, ValueError) as exc:
        print(f"TESTISOLATION FEHLER: {exc}\nKeine Dienste umstellen.", file=sys.stderr)
        return 78
