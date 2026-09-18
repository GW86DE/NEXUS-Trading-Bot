"""Lokale Zugangsdaten sicherer speichern.

Windows: DPAPI (Current User) verschlüsselt den gesamten JSON-Blob. Die Datei
``<name>.dpapi`` kann nur im selben Windows-Benutzerkontext entschlüsselt
werden. Legacy-JSON wird weiter gelesen und kann automatisch migriert werden.
Andere Systeme: JSON mit Dateimodus 0600, weil DPAPI dort nicht existiert.

Umgebungsvariablen bleiben in config.py weiterhin höchste Priorität.
"""
from __future__ import annotations
import base64, ctypes, json, os, sys
from ctypes import wintypes
from pathlib import Path
from typing import Any
from safe_persistence import atomic_write_text

ROOT = Path(__file__).resolve().parent

class DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]

def _dpapi_encrypt(raw: bytes) -> bytes:
    if os.name != "nt": return raw
    buf = ctypes.create_string_buffer(raw)
    in_blob = DATA_BLOB(len(raw), ctypes.cast(buf, ctypes.POINTER(ctypes.c_byte)))
    out_blob = DATA_BLOB()
    if not ctypes.windll.crypt32.CryptProtectData(ctypes.byref(in_blob), "TradingBot", None, None, None, 0, ctypes.byref(out_blob)):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out_blob.pbData)

def _dpapi_decrypt(raw: bytes) -> bytes:
    if os.name != "nt": return raw
    buf = ctypes.create_string_buffer(raw)
    in_blob = DATA_BLOB(len(raw), ctypes.cast(buf, ctypes.POINTER(ctypes.c_byte)))
    out_blob = DATA_BLOB()
    if not ctypes.windll.crypt32.CryptUnprotectData(ctypes.byref(in_blob), None, None, None, None, 0, ctypes.byref(out_blob)):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out_blob.pbData)

def secure_path(filename: str | Path) -> Path:
    p = Path(filename)
    if not p.is_absolute(): p = ROOT / p
    return p.with_suffix(p.suffix + ".dpapi")

def load_credentials(filename: str | Path, default: Any = None) -> Any:
    default = {} if default is None else default
    p = Path(filename); p = p if p.is_absolute() else ROOT / p
    sp = secure_path(p)
    if os.name == "nt" and sp.exists():
        try:
            enc = base64.b64decode(sp.read_text(encoding="ascii"))
            return json.loads(_dpapi_decrypt(enc).decode("utf-8"))
        except Exception:
            # Nicht auf eine eventuell alte Klartextdatei verzichten, wenn
            # z.B. ein Windows-Profilwechsel die DPAPI-Datei unlesbar machte.
            __import__("logging").getLogger(__name__).debug("Best-effort-Ausnahme unterdrueckt", exc_info=True)
    if p.exists():
        try: return json.loads(p.read_text(encoding="utf-8"))
        except Exception: return default
    return default

def save_credentials(filename: str | Path, data: Any) -> Path:
    p = Path(filename); p = p if p.is_absolute() else ROOT / p
    text = json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8")
    if os.name == "nt":
        sp = secure_path(p)
        atomic_write_text(sp, base64.b64encode(_dpapi_encrypt(text)).decode("ascii"), encoding="ascii")
        try: p.unlink(missing_ok=True)
        except Exception: __import__("logging").getLogger(__name__).debug("Best-effort-Ausnahme unterdrueckt", exc_info=True)
        return sp
    atomic_write_text(p, text.decode("utf-8"))
    try: os.chmod(p, 0o600)
    except Exception: __import__("logging").getLogger(__name__).debug("Best-effort-Ausnahme unterdrueckt", exc_info=True)
    return p

def migrate_known_credentials() -> list[str]:
    names = [
        "etoro_credentials.json", "telegram_credentials.json",
        "alpha_vantage_credentials.json", "news_sources_credentials.json",
        "openai_ai_settings.json",
    ]
    moved=[]
    if os.name != "nt": return moved
    for name in names:
        p=ROOT/name
        if p.exists():
            data=load_credentials(p, None)
            if isinstance(data, dict):
                save_credentials(p, data); moved.append(name)
    return moved
