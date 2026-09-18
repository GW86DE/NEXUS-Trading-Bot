"""Telegram-Bot fuer parallele Trading-Benachrichtigungen einrichten."""
from __future__ import annotations

import getpass
import time
from pathlib import Path
from credential_store import load_credentials, save_credentials

try:
    import requests
except ModuleNotFoundError:
    print("\nFEHLER: Das Python-Paket 'requests' fehlt in der aktuell verwendeten Python-Umgebung.")
    print("Starte die GUI ueber Start_Gui.bat oder fuehre Umgebung_Reparieren.bat aus.")
    input("Enter ... ")
    raise SystemExit(2)

BASE = Path(__file__).resolve().parent
CRED_FILE = BASE / "telegram_credentials.json"
TIMEOUT = 20


def api(token: str, method: str, *, params=None, timeout=TIMEOUT):
    url = f"https://api.telegram.org/bot{token}/{method}"
    response = requests.get(url, params=params or {}, timeout=timeout)
    try:
        data = response.json()
    except ValueError:
        raise RuntimeError(f"Telegram antwortet nicht mit JSON (HTTP {response.status_code}).")
    if not response.ok or not data.get("ok"):
        raise RuntimeError(data.get("description") or f"HTTP {response.status_code}")
    return data.get("result")


def load_existing():
    data = load_credentials(CRED_FILE, {})
    return data if isinstance(data, dict) else {}


def find_chats(token: str):
    info = api(token, "getWebhookInfo") or {}
    if info.get("url"):
        print("\nHINWEIS: Fuer diesen Bot ist bereits ein Webhook gesetzt.")
        print("Automatisches Ermitteln der Chat-ID ueber getUpdates ist dann nicht moeglich.")
        return []

    print("\nOeffne jetzt deinen Telegram-Bot auf dem Handy und sende ihm /start")
    print("oder irgendeine kurze Nachricht. Danach hier Enter druecken.")
    input("Weiter mit Enter ... ")

    deadline = time.time() + 20
    found = {}
    while time.time() < deadline:
        updates = api(token, "getUpdates", params={"timeout": 2, "allowed_updates": '["message"]'}, timeout=5) or []
        for update in updates:
            msg = update.get("message") or {}
            chat = msg.get("chat") or {}
            if chat.get("id") is None:
                continue
            cid = str(chat["id"])
            label = chat.get("username") or chat.get("first_name") or chat.get("title") or cid
            sender = msg.get("from") or {}
            uid = str(sender.get("id") or (cid if chat.get("type") == "private" else ""))
            found[cid] = (f"{label} ({chat.get('type','?')})", uid)
        if found:
            break
    return list(found.items())


def main():
    print("=" * 62)
    print(" TELEGRAM EINRICHTUNG – EINZIGER KOMMUNIKATIONSKANAL")
    print("=" * 62)
    old = load_existing()
    old_token = str(old.get("bot_token", "") or "")
    if old_token:
        entered = getpass.getpass("Bot-Token [Enter = vorhandenen behalten]: ").strip()
        token = entered or old_token
    else:
        token = getpass.getpass("Bot-Token von @BotFather: ").strip()
    if not token:
        print("FEHLER: Bot-Token fehlt.")
        input("Enter ... ")
        return

    try:
        me = api(token, "getMe") or {}
    except Exception as exc:
        print(f"FEHLER: Token konnte nicht bestaetigt werden: {exc}")
        input("Enter ... ")
        return

    print(f"\nBot erkannt: @{me.get('username','?')} ({me.get('first_name','Telegram Bot')})")
    old_chat = str(old.get("chat_id", "") or "")
    chats = []
    try:
        chats = find_chats(token)
    except Exception as exc:
        print(f"Chat-ID konnte nicht automatisch ermittelt werden: {exc}")

    chat_id = ""
    if chats:
        print("\nGefundene Chats:")
        for i, (cid, info) in enumerate(chats, start=1):
            label, uid = info
            print(f" {i}. {label} | Chat-ID {cid} | User-ID {uid or '?'}")
        if len(chats) == 1:
            chat_id = chats[0][0]
            print(f"Automatisch gewaehlt: {chat_id}")
        else:
            while True:
                choice = input("Nummer des Ziel-Chats: ").strip()
                if choice.isdigit() and 1 <= int(choice) <= len(chats):
                    chat_id = chats[int(choice)-1][0]
                    break
    if not chat_id:
        default = old_chat
        prompt = f"Chat-ID manuell [{default}]: " if default else "Chat-ID manuell: "
        chat_id = input(prompt).strip() or default
    if not chat_id:
        print("FEHLER: Keine Chat-ID vorhanden.")
        input("Enter ... ")
        return

    detected_user = ""
    for cid, info in chats:
        if str(cid) == str(chat_id):
            detected_user = str(info[1] or "")
            break
    old_user = str(old.get("user_id", "") or "")
    default_user = detected_user or old_user or (str(chat_id) if not str(chat_id).startswith("-") else "")
    prompt = f"Freigegebene Telegram-User-ID [{default_user}]: " if default_user else "Freigegebene Telegram-User-ID: "
    user_id = input(prompt).strip() or default_user
    if not user_id or not user_id.lstrip("-").isdigit():
        print("FEHLER: Gueltige numerische User-ID erforderlich. Fuer private Chats ist sie normalerweise identisch mit der Chat-ID.")
        input("Enter ... ")
        return

    data = {
        "enabled": True,
        "bot_token": token,
        "chat_id": str(chat_id),
        "user_id": str(user_id),
        "bot_username": me.get("username", ""),
    }
    ziel = save_credentials(CRED_FILE, data)
    print(f"\nZugangsdaten sicher gespeichert: {ziel.name}")

    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        response = requests.post(url, json={
            "chat_id": chat_id,
            "text": "✅ TradingBot: Telegram wurde erfolgreich eingerichtet.",
        }, timeout=TIMEOUT)
        payload = response.json()
        if response.ok and payload.get("ok"):
            print("TEST: Telegram-Nachricht erfolgreich gesendet.")
        else:
            print("TEST FEHLER:", payload.get("description") or response.text[:400])
    except Exception as exc:
        print("TEST FEHLER:", exc)

    print("\nAb dem naechsten Botstart laufen Benachrichtigungen und Fernsteuerung ueber Telegram.")
    print(f"Sicherheitsfreigabe: Chat-ID {chat_id} UND User-ID {user_id} muessen uebereinstimmen.")
    input("Enter druecken ... ")


if __name__ == "__main__":
    main()
