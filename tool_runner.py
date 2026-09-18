"""Hilfen fuer GUI-Testwerkzeuge: UTF-8 und sofort sichtbare Eingabe-Prompts."""
from __future__ import annotations
import os
import subprocess
import sys
from pathlib import Path


def utf8_child_env(base=None):
    env=dict(base or os.environ)
    env["PYTHONIOENCODING"]="utf-8"
    env["PYTHONUTF8"]="1"
    env["PYTHONUNBUFFERED"]="1"
    return env


def iter_stream_chunks(stream, chunk_size: int = 1):
    """Liest auch Prompts ohne Zeilenumbruch sofort aus einem Textstream."""
    size=max(1,int(chunk_size or 1))
    while True:
        chunk=stream.read(size)
        if chunk == "":
            break
        yield chunk


def run_capture(path: Path, args=None, timeout=None):
    cmd=[sys.executable,"-u",str(path)]+list(args or [])
    p=subprocess.run(cmd,cwd=str(path.parent),text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
                     timeout=timeout,encoding="utf-8",errors="replace",env=utf8_child_env())
    return p.returncode,p.stdout or ""
