"""Ermittelt eine sichere WebUI-Bind-Adresse, ohne WireGuard zu veraendern."""
from __future__ import annotations

import argparse
import ipaddress
import json
import os
import subprocess
from pathlib import Path

from safe_persistence import atomic_write_json
from webui.settings_store import validate_bind_host

ROOT = Path(__file__).resolve().parent
SETTINGS = ROOT / "web_ui_settings.json"


def _command(*args: str) -> str:
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=3, check=False)
        return result.stdout or ""
    except Exception:
        return ""


def _ipv4_interface(name: str) -> str:
    text = _command("ip", "-4", "-o", "address", "show", "dev", name, "scope", "global")
    for token in text.split():
        if "/" not in token:
            continue
        try:
            return str(ipaddress.ip_interface(token).ip)
        except ValueError:
            continue
    return ""


def _lan_ipv4() -> str:
    text = _command("ip", "-4", "route", "get", "1.1.1.1")
    parts = text.split()
    if "src" in parts:
        try:
            candidate = parts[parts.index("src") + 1]
            address = ipaddress.ip_address(candidate)
            if address.is_private and not address.is_loopback:
                return str(address)
        except Exception:
            __import__("logging").getLogger(__name__).debug(
                "LAN-Quelladresse nicht auswertbar", exc_info=True)
    return ""


def configure(mode: str = "auto") -> dict:
    selected = str(mode or "auto").strip().lower()
    if selected not in {"auto", "lan", "vpn", "local"}:
        raise ValueError("Modus muss auto, lan, vpn oder local sein.")
    try:
        current = json.loads(SETTINGS.read_text(encoding="utf-8")) if SETTINGS.exists() else {}
    except Exception:
        current = {}
    existing = str(current.get("bind_host") or "")
    if selected == "auto" and existing:
        try:
            host = validate_bind_host(existing)
            reason = "vorhandene sichere Einstellung beibehalten"
        except ValueError:
            host = ""
    else:
        host = ""
    lan = _lan_ipv4()
    wg = _ipv4_interface("wg0")
    if not host:
        if selected in {"auto", "lan"} and lan:
            host, reason = lan, "private LAN-IP (Heimnetz und via WireGuard-Route)"
        elif selected in {"auto", "vpn"} and wg:
            host, reason = wg, "WireGuard-IP (nur VPN)"
        elif selected == "lan":
            raise ValueError("Keine private LAN-IP erkannt.")
        elif selected == "vpn":
            raise ValueError("Keine aktive wg0-IPv4 erkannt.")
        else:
            host, reason = "127.0.0.1", "nur lokal"
    host = validate_bind_host(host)
    payload = {
        "bind_host": host,
        "port": max(1024, min(65535, int(current.get("port", 8780) or 8780))),
        "cookie_secure": bool(current.get("cookie_secure", False)),
        "access_reason": reason,
        "wireguard_detected": bool(wg),
        "wireguard_ip": wg,
        "lan_ip": lan,
    }
    atomic_write_json(SETTINGS, payload)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Sichere NEXUS-WebUI-Netzkonfiguration")
    parser.add_argument("--mode", choices=("auto", "lan", "vpn", "local"),
                        default=os.getenv("NEXUS_WEBUI_ACCESS_MODE", "auto"))
    args = parser.parse_args()
    try:
        data = configure(args.mode)
    except ValueError as exc:
        print(f"FEHLER: {exc}")
        return 2
    print(f"WebUI: http://{data['bind_host']}:{data['port']} ({data['access_reason']})")
    if data["wireguard_detected"]:
        print(f"WireGuard erkannt: {data['wireguard_ip']}; vorhandene Keys/Peers wurden NICHT veraendert.")
    else:
        print("WireGuard nicht aktiv erkannt; es wurde keine VPN-Konfiguration angelegt oder veraendert.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
