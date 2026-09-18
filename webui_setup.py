"""Lokale Ersteinrichtung/Passwortaenderung fuer die NEXUS-WebUI."""
from __future__ import annotations

import argparse
import getpass

from webui.auth import configure, configured


def main() -> int:
    parser = argparse.ArgumentParser(description="TradingBot 8.1.1 WebUI-Benutzer einrichten")
    parser.add_argument("--status", action="store_true")
    args = parser.parse_args()
    if args.status:
        print("WebUI-Zugang ist eingerichtet." if configured() else "WebUI-Zugang fehlt.")
        return 0 if configured() else 1
    username = input("WebUI-Benutzername: ").strip()
    password = getpass.getpass("Neues Passwort (mindestens 12 Zeichen): ")
    repeat = getpass.getpass("Passwort wiederholen: ")
    if password != repeat:
        print("Passwoerter stimmen nicht ueberein.")
        return 2
    try:
        configure(username, password)
    except ValueError as exc:
        print(f"Fehler: {exc}")
        return 2
    print("WebUI-Zugang gespeichert. Das Passwort wird nicht im Klartext abgelegt.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
