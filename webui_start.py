"""Unabhaengiger WebUI-Prozess; ein Webfehler beendet den Trading-Core nicht."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import config

ROOT = Path(__file__).resolve().parent


def _settings() -> dict:
    path = ROOT / "web_ui_settings.json"
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except Exception:
        return {}


def main() -> int:
    parser = argparse.ArgumentParser(description=f"TradingBot {config.VERSION_NEXUS} WebUI")
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    parser.add_argument("--check", action="store_true", help="Konfiguration pruefen und beenden")
    args = parser.parse_args()
    from webui.auth import configured
    if not configured():
        print("WebUI-Zugang fehlt. Zuerst lokal ausfuehren: python3 webui_setup.py")
        return 2
    settings = _settings()
    host = str(args.host or os.getenv("WEBUI_BIND_HOST") or settings.get("bind_host") or "127.0.0.1")
    port = int(args.port or os.getenv("WEBUI_PORT") or settings.get("port") or 8780)
    from webui.settings_store import validate_bind_host
    try:
        host = validate_bind_host(host)
    except ValueError as exc:
        print(f"Unsichere Bind-Adresse blockiert: {exc}")
        return 2
    if bool(settings.get("cookie_secure", False)):
        os.environ["WEBUI_COOKIE_SECURE"] = "1"
    if args.check:
        from webui.app import app
        print(f"OK: {app.title} | http://{host}:{port}")
        return 0
    import uvicorn
    uvicorn.run("webui.app:app", host=host, port=port, log_level="info",
                proxy_headers=False, server_header=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
