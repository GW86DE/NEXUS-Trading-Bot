"""Lokale Passwort- und Sitzungsverwaltung ohne externen Identity-Provider."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import time
from pathlib import Path

from credential_store import load_credentials, save_credentials

ROOT = Path(__file__).resolve().parents[1]
FILE = ROOT / "web_ui_credentials.json"
COOKIE = "tb8_session"
ITERATIONS = 310_000
USERNAME_RE = re.compile(r"^[A-Za-z0-9._-]{3,64}$")


def _credentials() -> dict:
    value = load_credentials(FILE, {})
    return value if isinstance(value, dict) else {}


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def hash_password(password: str, *, salt: bytes | None = None) -> str:
    if len(str(password)) < 12:
        raise ValueError("Das WebUI-Passwort muss mindestens 12 Zeichen lang sein.")
    salt = salt or secrets.token_bytes(18)
    digest = hashlib.pbkdf2_hmac("sha256", str(password).encode(), salt, ITERATIONS)
    return f"pbkdf2_sha256${ITERATIONS}${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        scheme, rounds, salt, expected = str(encoded).split("$", 3)
        if scheme != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac("sha256", str(password).encode(), _unb64(salt), int(rounds))
        return hmac.compare_digest(_b64(digest), expected)
    except Exception:
        return False


def configure(username: str, password: str) -> None:
    username = str(username or "").strip()
    if not USERNAME_RE.fullmatch(username):
        raise ValueError(
            "Benutzername: 3 bis 64 Zeichen; erlaubt sind Buchstaben, Zahlen, Punkt, _ und -."
        )
    save_credentials(FILE, {
        "username": username,
        "password_hash": hash_password(password),
        # Jede explizite Neueinrichtung widerruft vorher ausgestellte Cookies.
        "session_secret": secrets.token_urlsafe(48),
    })


def configured() -> bool:
    data = _credentials()
    return bool(data.get("username") and data.get("password_hash") and data.get("session_secret"))


def authenticate(username: str, password: str) -> bool:
    data = _credentials()
    return (hmac.compare_digest(str(username), str(data.get("username") or ""))
            and verify_password(password, str(data.get("password_hash") or "")))


def issue_session(username: str, ttl_seconds: int = 8 * 3600) -> tuple[str, str]:
    data = _credentials()
    if (not all(data.get(key) for key in ("username", "password_hash", "session_secret"))
            or not hmac.compare_digest(str(username), str(data["username"]))):
        raise ValueError("WebUI-Zugang ist nicht eingerichtet oder Benutzer unbekannt.")
    csrf = secrets.token_urlsafe(24)
    payload = _b64(json.dumps({
        "u": str(username), "exp": int(time.time()) + int(ttl_seconds), "csrf": csrf,
        "nonce": secrets.token_urlsafe(10),
    }, separators=(",", ":")).encode())
    signature = _b64(hmac.new(str(data.get("session_secret") or "").encode(),
                              payload.encode(), hashlib.sha256).digest())
    return f"{payload}.{signature}", csrf


def decode_session(token: str) -> dict | None:
    try:
        payload, signature = str(token or "").split(".", 1)
        data = _credentials()
        # Fehlende/beschaedigte Credentials duerfen niemals einen bekannten
        # leeren HMAC-Schluessel zu einer gueltigen Sitzung machen.
        if not all(data.get(key) for key in ("username", "password_hash", "session_secret")):
            return None
        expected = _b64(hmac.new(str(data.get("session_secret") or "").encode(),
                                 payload.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(signature, expected):
            return None
        session = json.loads(_unb64(payload).decode())
        if not isinstance(session, dict) or not session.get("csrf") or not session.get("nonce"):
            return None
        if int(session.get("exp", 0)) <= int(time.time()):
            return None
        if not hmac.compare_digest(str(session.get("u") or ""), str(data.get("username") or "")):
            return None
        return session
    except Exception:
        return None


def secure_cookie() -> bool:
    return os.getenv("WEBUI_COOKIE_SECURE", "0").strip().lower() in {"1", "true", "yes", "on"}
