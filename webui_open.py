"""Oeffnet die tatsaechlich konfigurierte WebUI-Adresse."""
from __future__ import annotations
import json
import webbrowser
from pathlib import Path

def main() -> int:
    root = Path(__file__).resolve().parent
    try:
        settings = json.loads((root / "web_ui_settings.json").read_text(encoding="utf-8"))
    except Exception:
        settings = {}
    host = str(settings.get("bind_host") or "127.0.0.1")
    port = int(settings.get("port") or 8780)
    return 0 if webbrowser.open(f"http://{host}:{port}") else 1


if __name__ == "__main__":
    raise SystemExit(main())
