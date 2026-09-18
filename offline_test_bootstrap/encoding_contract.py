"""Verify the isolated Python text contract before importing NEXUS test code.

The OS locale is deliberately NOT changed. Both legacy and UTF-8 locales must
work. This checks the interpreter started with the controlled UTF-8 environment;
setting PYTHONUTF8 after startup would not fix the interpreter's default codec.
Only encoding/locale metadata is printed, never inherited settings or secrets.
"""
from __future__ import annotations

import codecs
import io
import json
import locale
import sys


def _codec(name: str | None) -> str:
    if not name:
        return "missing"
    try:
        return codecs.lookup(name).name
    except LookupError:
        return "unknown"


def runtime_report() -> dict:
    with io.TextIOWrapper(io.BytesIO()) as stream:
        default_text = stream.encoding
    return {
        "utf8_mode": sys.flags.utf8_mode,
        "locale": locale.setlocale(locale.LC_CTYPE),
        "native_locale_encoding": locale.getencoding(),
        "preferred_encoding": _codec(locale.getpreferredencoding(False)),
        "default_file_encoding": _codec(default_text),
        "filesystem_encoding": _codec(sys.getfilesystemencoding()),
        "stdio": {name: _codec(getattr(getattr(sys, name), "encoding", None))
                  for name in ("stdin", "stdout", "stderr")},
    }


def require_utf8() -> dict:
    report = runtime_report()
    if (report["utf8_mode"] != 1
            or any(report[key] != "utf-8" for key in (
                "preferred_encoding", "default_file_encoding", "filesystem_encoding"))
            or any(codec != "utf-8" for codec in report["stdio"].values())):
        raise RuntimeError("Isolierter Python-Prozess nicht durchgaengig UTF-8: "
                           + json.dumps(report, ensure_ascii=True, sort_keys=True))
    print("TEST-ZEICHENCODIERUNG OK: "
          + json.dumps(report, ensure_ascii=True, sort_keys=True), flush=True)
    return report
