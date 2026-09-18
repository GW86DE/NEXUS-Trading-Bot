"""Verified, no-overwrite extraction used by the self-contained update starter."""
from __future__ import annotations
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import tempfile
import zipfile

RELEASE_ROOT = 'TradingBot_v10.1.10_NEXUS'


def unpack_release(data: bytes, parent: Path, expected_sha: str) -> Path:
    if hashlib.sha256(data).hexdigest() != expected_sha:
        raise ValueError('Archivpruefsumme stimmt nicht; keine Extraktion')
    parent = Path(parent).expanduser().resolve()
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = []
        total = 0
        for info in z.infolist():
            name = info.filename
            parts = name.rstrip('/').split('/')
            mode = info.external_attr >> 16
            if (not name or name.startswith('/') or '\\' in name or ':' in name
                    or any(p in {'','.','..'} for p in parts)
                    or parts[0] != RELEASE_ROOT or stat.S_ISLNK(mode)
                    or (stat.S_IFMT(mode) not in {0,stat.S_IFREG,stat.S_IFDIR})):
                raise ValueError('Unsicherer Archivpfad/-typ')
            if name in names:
                raise ValueError('Doppelter Archiveintrag')
            names.append(name);total += info.file_size
        if total > 100*1024*1024:
            raise ValueError('Quellarchiv unerwartet gross')
        manifest = json.loads(z.read(RELEASE_ROOT+'/MANIFEST_SHA256.json'))
        files = {n[len(RELEASE_ROOT)+1:] for n in names if not n.endswith('/')}
        if not isinstance(manifest,dict) or set(manifest) != files-{'MANIFEST_SHA256.json'}:
            raise ValueError('Archiv und Dateimanifest widersprechen sich')
        for relative, expected in manifest.items():
            if hashlib.sha256(z.read(RELEASE_ROOT+'/'+relative)).hexdigest() != expected:
                raise ValueError('Quellhash stimmt nicht: '+relative)
        parent.mkdir(mode=0o700,parents=True,exist_ok=True)
        target=parent/RELEASE_ROOT
        if target.is_symlink():
            raise ValueError('Ziel ist ein Symlink')
        if target.exists():
            if not target.is_dir():raise ValueError('Ziel ist keine Verzeichnisinstallation')
            for relative,expected in manifest.items():
                p=target/relative
                if any(x.is_symlink() for x in [p,*p.parents] if x!=parent.parent):
                    raise ValueError('Symlink im Ziel')
                if not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest()!=expected:
                    raise ValueError('Zielquellstand ist veraendert; nicht ueberschrieben: '+relative)
            if json.loads((target/'MANIFEST_SHA256.json').read_text(encoding='utf-8')) != manifest:
                raise ValueError('Zielmanifest ist veraendert')
            return target  # Leave all user states and venv byte-for-byte alone.
        staging=Path(tempfile.mkdtemp(prefix='.nexus_entpacken_',dir=parent))
        try:
            for info in z.infolist():
                dest=staging/info.filename
                if info.is_dir():dest.mkdir(parents=True,exist_ok=True);continue
                dest.parent.mkdir(parents=True,exist_ok=True)
                with dest.open('xb') as out:out.write(z.read(info))
                dest.chmod(0o700 if dest.suffix=='.sh' else 0o600)
            os.rename(staging/RELEASE_ROOT,target)
        finally:
            shutil.rmtree(staging)
        return target
