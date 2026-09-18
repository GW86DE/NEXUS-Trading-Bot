from __future__ import annotations
import json
from credential_store import load_credentials, save_credentials
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox
import requests

ROOT = Path(__file__).resolve().parent
FILE = ROOT / "telegram_credentials.json"
RUNTIME = ROOT / "runtime_status.json"
TIMEOUT = 20


def load():
    return load_credentials(FILE, {})


def api(token, method, *, params=None, post=False):
    url = f"https://api.telegram.org/bot{token}/{method}"
    if post:
        r = requests.post(url, json=params or {}, timeout=TIMEOUT)
    else:
        r = requests.get(url, params=params or {}, timeout=TIMEOUT)
    try:
        data = r.json()
    except Exception:
        raise RuntimeError(f"Telegram antwortet nicht mit JSON (HTTP {r.status_code}).")
    if not r.ok or not data.get("ok"):
        raise RuntimeError(data.get("description") or f"HTTP {r.status_code}")
    return data.get("result")


def bot_online():
    try:
        from runtime_status import read_runtime
        return bool(read_runtime(RUNTIME).get("online"))
    except Exception:
        return False


def main():
    saved = load()
    root = tk.Tk()
    root.title(f"TradingBot {getattr(config, 'VERSION_NEXUS', 'NEXUS')} – Telegram einrichten")
    root.geometry("720x560")
    root.minsize(670, 500)
    frm = ttk.Frame(root, padding=22)
    frm.pack(fill="both", expand=True)

    ttk.Label(frm, text="Telegram", font=("Segoe UI", 17, "bold")).grid(
        row=0, column=0, columnspan=2, sticky="w"
    )
    ttk.Label(
        frm,
        text=(
            "Token, Chat-ID und freigegebene Telegram-User-ID werden nur lokal gespeichert. "
            "Freigaben werden nur akzeptiert, wenn Chat UND Benutzer-ID passen.\n"
            "Wenn der Trading-Bot bereits läuft, bitte NICHT parallel getUpdates "
            "im Browser öffnen – das kann 409 Conflict verursachen."
        ),
        wraplength=620,
    ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(5, 18))

    enabled = tk.BooleanVar(value=bool(saved.get("enabled", True)))
    token = tk.StringVar(value=str(saved.get("bot_token", "")))
    chat_id = tk.StringVar(value=str(saved.get("chat_id", "")))
    allowed_user_id = tk.StringVar(value=str(saved.get("user_id", saved.get("chat_id", ""))))
    username = tk.StringVar(value=str(saved.get("bot_username", "")))
    status = tk.StringVar(value="Noch nicht getestet.")

    ttk.Checkbutton(frm, text="Telegram-Benachrichtigungen und Fernsteuerung aktiv", variable=enabled).grid(
        row=2, column=0, columnspan=2, sticky="w", pady=6
    )
    ttk.Label(frm, text="Bot-Token").grid(row=3, column=0, sticky="w", pady=6)
    ttk.Entry(frm, textvariable=token, show="•", width=55).grid(row=3, column=1, sticky="ew", pady=6)
    ttk.Label(frm, text="Chat-ID").grid(row=4, column=0, sticky="w", pady=6)
    ttk.Entry(frm, textvariable=chat_id, width=55).grid(row=4, column=1, sticky="ew", pady=6)
    ttk.Label(frm, text="Freigegebene User-ID").grid(row=5, column=0, sticky="w", pady=6)
    ttk.Entry(frm, textvariable=allowed_user_id, width=55).grid(row=5, column=1, sticky="ew", pady=6)
    ttk.Label(frm, text="Bot").grid(row=6, column=0, sticky="w", pady=6)
    ttk.Label(frm, textvariable=username).grid(row=6, column=1, sticky="w", pady=6)

    def collect():
        t = token.get().strip()
        c = chat_id.get().strip()
        if not t:
            raise ValueError("Bot-Token fehlt.")
        if not c:
            raise ValueError("Chat-ID fehlt.")
        u = allowed_user_id.get().strip() or c
        if not c.lstrip("-").isdigit():
            raise ValueError("Die Chat-ID muss numerisch sein.")
        if not u.lstrip("-").isdigit():
            raise ValueError("Die freigegebene User-ID muss numerisch sein.")
        return t, c, u

    def check_token():
        try:
            t = token.get().strip()
            if not t:
                raise ValueError("Bot-Token fehlt.")
            me = api(t, "getMe") or {}
            username.set("@" + str(me.get("username", "?")))
            status.set("✅ Bot-Token ist gültig.")
        except Exception as exc:
            status.set(f"❌ {exc}")
            messagebox.showerror("Telegram", str(exc))

    def auto_chat():
        if bot_online():
            return messagebox.showwarning(
                "Bot läuft",
                "Der Trading-Bot ist online und nutzt bereits getUpdates.\n\n"
                "Bitte die Chat-ID manuell eintragen oder den Bot zuerst pausieren/"
                "beenden. Paralleles Polling kann Telegram 409 Conflict auslösen.",
            )
        try:
            t = token.get().strip()
            if not t:
                raise ValueError("Bot-Token fehlt.")
            info = api(t, "getWebhookInfo") or {}
            if info.get("url"):
                raise RuntimeError("Für diesen Bot ist ein Webhook gesetzt. getUpdates ist dann nicht verfügbar.")
            updates = api(t, "getUpdates", params={"timeout": 0, "allowed_updates": '["message"]'}) or []
            chats = {}
            for upd in updates:
                msg = upd.get("message") or {}
                ch = msg.get("chat") or {}
                sender = msg.get("from") or {}
                if ch.get("id") is not None:
                    cid = str(ch["id"])
                    label = ch.get("username") or ch.get("first_name") or ch.get("title") or cid
                    uid = str(sender.get("id") or (cid if ch.get("type") == "private" else ""))
                    chats[cid] = (label, uid)
            if not chats:
                raise RuntimeError("Keine Nachricht gefunden. Bitte dem Bot zuerst /start senden und erneut versuchen.")
            # Für private Nutzung nehmen wir die zuletzt gefundene Nachricht.
            cid, (label, uid) = list(chats.items())[-1]
            chat_id.set(cid)
            if uid:
                allowed_user_id.set(uid)
            status.set(f"✅ Chat gefunden: {label} ({cid}); User-ID {uid or 'bitte manuell eintragen'}")
        except Exception as exc:
            status.set(f"❌ {exc}")
            messagebox.showerror("Chat-ID", str(exc))

    def test():
        try:
            t, c, _u = collect()
            me = api(t, "getMe") or {}
            username.set("@" + str(me.get("username", "?")))
            api(t, "sendMessage", params={
                "chat_id": c,
                "text": f"✅ TradingBot {getattr(config, 'VERSION_NEXUS', 'NEXUS')}: Telegram-Test erfolgreich.",
            }, post=True)
            status.set("✅ Telegram senden funktioniert.")
            messagebox.showinfo("Telegram", "Testnachricht erfolgreich gesendet.")
        except Exception as exc:
            status.set(f"❌ {exc}")
            messagebox.showerror("Telegram-Test", str(exc))

    def save():
        try:
            t, c, u = collect()
            me = api(t, "getMe") or {}
            data = {
                "enabled": bool(enabled.get()),
                "bot_token": t,
                "chat_id": c,
                "user_id": u,
                "bot_username": me.get("username", ""),
            }
            save_credentials(FILE, data)
            username.set("@" + str(me.get("username", "?")))
            status.set("✅ Gespeichert. Gilt beim nächsten Bot-Start.")
            messagebox.showinfo("Telegram", "Telegram-Einstellungen gespeichert.")
        except Exception as exc:
            messagebox.showerror("Speichern", str(exc))

    buttons = ttk.Frame(frm)
    buttons.grid(row=7, column=0, columnspan=2, sticky="ew", pady=(18, 8))
    ttk.Button(buttons, text="Token prüfen", command=check_token).pack(side="left")
    ttk.Button(buttons, text="Chat-ID automatisch", command=auto_chat).pack(side="left", padx=6)
    ttk.Button(buttons, text="Testnachricht", command=test).pack(side="left", padx=6)
    ttk.Button(buttons, text="Speichern", command=save).pack(side="left", padx=6)
    ttk.Button(buttons, text="Schließen", command=root.destroy).pack(side="right")

    ttk.Label(frm, textvariable=status, wraplength=620).grid(
        row=8, column=0, columnspan=2, sticky="w", pady=(8, 0)
    )
    frm.columnconfigure(1, weight=1)
    root.mainloop()


if __name__ == "__main__":
    main()
