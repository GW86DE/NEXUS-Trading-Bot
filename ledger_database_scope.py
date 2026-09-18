"""Explicit existing-ledger scope for offline repair, including cold imports.

Pure standard library; importing this module never opens a database. A repair
must enter the scope BEFORE importing modules that initialize analytics tables.
ContextVar isolates concurrent threads and nested calls. This is not an
environment-variable override and does not change normal broker/runtime paths.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path


class DatabaseScopeError(RuntimeError):
    """The explicitly selected, pre-existing ledger is no longer usable."""


_SELECTED: ContextVar[Path | None] = ContextVar("nexus_existing_ledger", default=None)


def _validate(path: Path) -> Path:
    if (not path.is_absolute() or path.is_symlink() or not path.is_file()
            or path.resolve() != path):
        raise DatabaseScopeError("Explizite Handelsdatenbank fehlt oder ist ein Symlink: " + str(path))
    return path


def current_path() -> Path | None:
    """Revalidate on every connection; never recreate a removed repair input."""
    path = _SELECTED.get()
    return _validate(path) if path is not None else None


@contextmanager
def existing_database(path: Path):
    """Select an existing regular ledger, without importing analytics/config."""
    selected = _validate(Path(path))
    token = _SELECTED.set(selected)
    try:
        yield selected
    finally:
        _SELECTED.reset(token)
