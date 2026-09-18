"""Telegram-Benachrichtigungen lokal ein-/ausschalten, Zugangsdaten bleiben erhalten."""
from __future__ import annotations
from pathlib import Path
from credential_store import load_credentials, save_credentials, secure_path

PATH = Path(__file__).resolve().parent / "telegram_credentials.json"

if __name__ == "__main__":
    if not PATH.exists() and not secure_path(PATH).exists():
        print("Telegram ist noch nicht eingerichtet. Bitte zuerst telegram_setup.py starten.")
        input("Enter ... ")
        raise SystemExit(1)
    data = load_credentials(PATH, {})
    if not isinstance(data, dict) or not data:
        print("Telegram-Konfiguration kann nicht gelesen werden.")
        input("Enter ... ")
        raise SystemExit(1)
    current = bool(data.get("enabled", True))
    print(f"Telegram ist aktuell: {'AKTIV' if current else 'DEAKTIVIERT'}")
    print("1 = Aktivieren")
    print("2 = Deaktivieren")
    print("0 = Abbrechen")
    choice = input("Auswahl: ").strip()
    if choice == "1": data["enabled"] = True
    elif choice == "2": data["enabled"] = False
    else: raise SystemExit(0)
    save_credentials(PATH, data)
    print("Sicher gespeichert. Aenderung gilt beim naechsten gestarteten Bot-/Testprozess.")
    input("Enter ... ")
