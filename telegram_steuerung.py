"""Telegram-Fernsteuerung mit Long-Polling, Heartbeat und Konflikterkennung."""
from __future__ import annotations

import json
import logging
import os
import secrets
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
import config
from notifier import (
    answer_callback_query, edit_telegram_message, send_telegram,
    send_telegram_buttons,
)
from safe_persistence import best_effort_json
from risk_profile_control import activate_risk_profile

logger = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parent
STATE_ROOT = Path(os.getenv("TRADINGBOT_TEST_STATE_DIR", "").strip() or ROOT)
STATE = STATE_ROOT / "telegram_control_state.json"
STATUS = STATE_ROOT / getattr(config, "TELEGRAM_CONTROL_STATUS_FILE", "telegram_control_status.json")
AUDIT = STATE_ROOT / "telegram_command_audit.jsonl"

ALIASES = {
    "/STATUS": "STATUS", "STATUS": "STATUS",
    "/PNL": "PNL", "PNL": "PNL",
    "/DECISIONS": "DECISIONS", "DECISIONS": "DECISIONS",
    "/HEALTH": "HEALTH", "HEALTH": "HEALTH",
    "/SCAN": "SCAN", "SCAN": "SCAN",
    "/DATABASE": "DATABASE", "DATABASE": "DATABASE",
    "/AI": "AI", "AI": "AI",
    "/VORSCHLAEGE": "VORSCHLAEGE", "/VORSCHLÄGE": "VORSCHLAEGE", "VORSCHLAEGE": "VORSCHLAEGE",
    "/UNIVERSUM": "UNIVERSUM", "UNIVERSUM": "UNIVERSUM",
    "/SHUTDOWNBOT": "SHUTDOWNBOT", "SHUTDOWNBOT": "SHUTDOWNBOT",
    "/RESUME": "RESUME", "RESUME": "RESUME",
    "/POSITIONS": "POSITIONEN", "/POSITIONEN": "POSITIONEN",
    "POSITIONS": "POSITIONEN", "POSITIONEN": "POSITIONEN",
    "/ORDERS": "ORDERS", "ORDERS": "ORDERS",
    "/REPORT": "BERICHT", "/BERICHT": "BERICHT",
    "REPORT": "BERICHT", "BERICHT": "BERICHT",
    "/PAUSE": "PAUSE", "PAUSE": "PAUSE",
    "/STOP": "STOPP", "/STOPP": "STOPP", "STOP": "STOPP", "STOPP": "STOPP",
    "/START": "START", "START": "START",
    "/HELP": "HILFE", "HELP": "HILFE", "HILFE": "HILFE",
    "/RISK1": "RISK1", "RISK1": "RISK1",
    "/RISK2": "RISK2", "RISK2": "RISK2",
    "/RISK3": "RISK3", "RISK3": "RISK3",
    "/MENU": "MENU", "MENU": "MENU",
    "/VERSION": "VERSION", "VERSION": "VERSION",
    "/DAILY": "BERICHT", "DAILY": "BERICHT",
    "/WEEKLY": "WEEKLY", "WEEKLY": "WEEKLY",
    "/PERFORMANCE": "PERFORMANCE", "PERFORMANCE": "PERFORMANCE",
    "/STATS": "STATS", "STATS": "STATS",
    "/LOCKS": "LOCKS", "LOCKS": "LOCKS",
    "/TELEGRAM": "TELEGRAM", "TELEGRAM": "TELEGRAM",
    "/CRYPTO": "CRYPTO", "CRYPTO": "CRYPTO",
    "/CRYPTOPAUSE": "CRYPTO_PAUSE", "CRYPTOPAUSE": "CRYPTO_PAUSE",
    "/CRYPTOSTOP": "CRYPTO_PAUSE", "CRYPTOSTOP": "CRYPTO_PAUSE",
}


def parse_command(text):
    parts = (text or "").strip().split()
    if not parts:
        return None, []
    key = parts[0].upper().split("@")[0]
    return ALIASES.get(key), [x.upper() for x in parts[1:]]


class TelegramSteuerung:
    def __init__(self, befehl_ausfuehren, melden=None, callback_handler=None):
        self.exec = befehl_ausfuehren
        self.melden = melden
        self.callback_handler = callback_handler
        self.stop_event = threading.Event()
        self.thread = None
        self.offset = self._load_offset()
        self.last_alert = 0.0
        self.consecutive_errors = 0
        self.session_id = secrets.token_hex(4)
        self._processed_updates: list[int] = self._load_processed_updates()
        self._confirmations: dict[str, tuple[float, str, list[str]]] = {}
        # RISK3-Bestaetigungen leben absichtlich nur im RAM: ein Neustart
        # macht jeden alten Telegram-Button automatisch ungueltig.
        self._risk3_confirmations: dict[str, float] = {}

    @property
    def aktiv(self):
        from live_settings import telegram_runtime
        runtime = telegram_runtime()
        return bool(getattr(config, "TELEGRAM_CONTROL_ENABLED", True)
                    and runtime["enabled"] and runtime["configured"])

    def _load_offset(self):
        try:
            if STATE.exists():
                return int(json.loads(STATE.read_text(encoding="utf-8")).get("offset", 0))
        except Exception as exc:
            logger.debug("Telegram-Offset konnte nicht gelesen werden: %s", exc)
        return 0

    def _load_processed_updates(self) -> list[int]:
        try:
            if STATE.exists():
                data = json.loads(STATE.read_text(encoding="utf-8"))
                return [int(x) for x in data.get("processed_updates", [])][-500:]
        except Exception:
            logger.debug("Telegram-Deduplizierungszustand nicht lesbar", exc_info=True)
        return []

    def _save_offset(self):
        best_effort_json(STATE, {
            "offset": self.offset,
            "processed_updates": self._processed_updates[-500:],
            "last_session_id": self.session_id,
        }, label="Telegram-Offset")

    def _remember_update(self, upd) -> bool:
        try:
            update_id = int(upd.get("update_id"))
        except Exception:
            return True
        if update_id in self._processed_updates:
            return False
        self._processed_updates.append(update_id)
        self._save_offset()
        return True

    def _audit(self, action: str, *, command: str = "", result: str = "", user: str = "") -> None:
        row = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "session_id": self.session_id, "action": action,
            "command": command, "result": str(result)[:500], "user": str(user)[:40],
        }
        try:
            with AUDIT.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            AUDIT.chmod(0o600)
        except Exception:
            logger.debug("Telegram-Audit nicht schreibbar", exc_info=True)

    def _status(self, status, detail="", **extra):
        payload = {
            "status": status,
            "detail": detail,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "offset": self.offset,
            "consecutive_errors": self.consecutive_errors,
            "session_id": self.session_id,
            **extra,
        }
        best_effort_json(STATUS, payload, label="Telegram-Control-Status")

    def _new_confirmation(self, command: str, args: list[str]) -> tuple[str, int]:
        ttl = 60
        now = time.monotonic()
        self._confirmations = {k: v for k, v in self._confirmations.items() if v[0] >= now}
        token = secrets.token_hex(6)
        self._confirmations[token] = (now + ttl, command, list(args))
        return token, ttl

    def _execute(self, command: str, args: list[str]):
        try:
            return self.exec.ausfuehren(command, quelle="telegram", args=args)
        except AttributeError:
            return self.exec(command)

    def _handle_internal_callback(self, data: str) -> dict | None:
        parts = str(data or "").split(":")
        if len(parts) >= 3 and parts[0] == "menu":
            if parts[1] != self.session_id:
                return {"text": "⌛ Dieses Menü stammt aus einer alten Bot-Sitzung. Bitte /menu neu senden."}
            command = parts[2]
            reply = self._execute(command, [])
            self._audit("menu", command=command, result="ok")
            return {"text": str(reply), "keyboard": self._menu_keyboard()}
        if len(parts) == 4 and parts[0] == "cmd":
            if parts[1] != self.session_id:
                return {"text": "⌛ Diese Bestätigung gehört zu einer alten Bot-Sitzung."}
            action, token = parts[2], parts[3]
            row = self._confirmations.pop(token, None)
            if row is None or time.monotonic() > row[0]:
                return {"text": "⌛ Bestätigung abgelaufen, ungültig oder bereits benutzt."}
            if action == "cancel":
                self._audit("cancel", command=row[1], result="abgebrochen")
                return {"text": f"❌ {row[1]} wurde nicht ausgeführt."}
            if action != "confirm":
                return {"text": "Unbekannte Aktion."}
            if row[1] == "CRYPTO" and row[2][:1] == ["FREQTRADE"]:
                from crypto_strategy_mode import FREQTRADE_SAMPLE, set_mode
                set_mode(
                    FREQTRADE_SAMPLE, source="telegram:confirmed",
                    reason="zweistufig per Telegram aktiviert", notify=False)
                self._audit("confirmed", command="CRYPTO FREQTRADE", result="ok")
                return {"text": (
                    "✅ FREQTRADE_SAMPLE ist jetzt fuer neue OKX-Einstiege aktiv. "
                    "Offene Positionen behalten ihre bisherige Einstiegsstrategie. "
                    "Notaus: /cryptopause"
                )}
            reply = self._execute(row[1], row[2])
            self._audit("confirmed", command=row[1], result="ok")
            return {"text": str(reply)}
        return None

    def _menu_keyboard(self):
        sid = self.session_id
        return [
            [{"text": "📊 Status", "callback_data": f"menu:{sid}:STATUS"},
             {"text": "💰 P&L", "callback_data": f"menu:{sid}:PNL"}],
            [{"text": "🧭 Entscheidungen", "callback_data": f"menu:{sid}:DECISIONS"},
             {"text": "🔌 Health", "callback_data": f"menu:{sid}:HEALTH"}],
            [{"text": "📈 Performance", "callback_data": f"menu:{sid}:PERFORMANCE"},
             {"text": "🔒 Sperren", "callback_data": f"menu:{sid}:LOCKS"}],
        ]

    def _alert(self, text):
        now = time.time()
        cooldown = float(getattr(config, "TELEGRAM_CONTROL_ALERT_COOLDOWN_SECONDS", 600))
        if now - self.last_alert < cooldown:
            return
        self.last_alert = now
        logger.error(text)
        if self.melden:
            try:
                self.melden(text)
            except Exception as exc:
                logger.debug("Telegram-Control-Alarm konnte nicht ueber Alternativkanal gemeldet werden: %s", exc)

    def _authorized(self, chat: str, user: str) -> bool:
        from live_settings import telegram_runtime
        runtime = telegram_runtime()
        authorized_chat = str(runtime["chat_id"]).strip()
        authorized_user = str(runtime["user_id"] or authorized_chat).strip()
        return bool(chat == authorized_chat and (not authorized_user or user == authorized_user))

    def _new_risk3_confirmation(self) -> tuple[str, int]:
        ttl = int(getattr(config, "TELEGRAM_RISK3_CONFIRM_TTL_SECONDS", 60) or 60)
        ttl = max(15, min(300, ttl))
        now = time.monotonic()
        # Abgelaufene Tokens regelmaessig entfernen.
        self._risk3_confirmations = {
            token: expires for token, expires in self._risk3_confirmations.items()
            if expires >= now
        }
        token = secrets.token_hex(8)
        self._risk3_confirmations[token] = now + ttl
        return token, ttl

    def _handle_risk3_callback(self, data: str) -> dict:
        parts = str(data or "").split(":", 2)
        if len(parts) != 3 or parts[0] != "risk3":
            return {"text": "Unbekannte Risiko-Aktion."}
        action, token = parts[1], parts[2]
        expires = self._risk3_confirmations.pop(token, None)
        if expires is None:
            return {"text": "⚠️ Diese RISK3-Bestätigung ist ungültig, bereits benutzt oder nach einem Neustart verfallen."}
        if time.monotonic() > expires:
            return {"text": "⌛ Die RISK3-Bestätigung ist abgelaufen. Bitte sende /risk3 erneut."}
        if action == "cancel":
            return {"text": "❌ RISK3 wurde nicht aktiviert. Das bisherige Risikoprofil bleibt unverändert."}
        if action != "confirm":
            return {"text": "Unbekannte Risiko-Aktion."}
        activate_risk_profile("offensiv")
        return {
            "text": (
                "⚠️ RISK3 / OFFENSIV wurde nach deiner zweiten Bestätigung aktiviert.\n"
                "Gilt ab jetzt für neue Entscheidungen. Bestehende Positionen bleiben unverändert."
            )
        }

    def _process_callback(self, cb, send_func=send_telegram, send_buttons_func=send_telegram_buttons):
        message=cb.get("message") or {}
        chat=str((message.get("chat") or {}).get("id", ""))
        user=str((cb.get("from") or {}).get("id", ""))
        cbid=str(cb.get("id") or "")
        if not self._authorized(chat,user):
            logger.warning("Telegram-Callback von nicht berechtigtem Nutzer ignoriert: chat=%s user=%s",chat,user)
            answer_callback_query(cbid,"Nicht berechtigt")
            return "unauthorized"
        data = str(cb.get("data") or "")
        internal = data.startswith(("risk3:", "menu:", "cmd:", "pulsar:"))
        if not self.callback_handler and not internal:
            answer_callback_query(cbid,"Aktion nicht verfügbar")
            return "ignored"
        try:
            if data.startswith("pulsar:"):
                from pulsar.telegram import handle
                result = handle(data, chat=chat, user=user,
                                message_id=str(message.get("message_id") or ""))
            elif data.startswith("risk3:"):
                result = self._handle_risk3_callback(data) or {}
            elif data.startswith(("menu:", "cmd:")):
                result = self._handle_internal_callback(data) or {}
            else:
                result=self.callback_handler(data) or {}
            answer_callback_query(cbid,"Verarbeitet")
            text=str(result.get("text") or "Aktion verarbeitet.")
            keyboard=result.get("keyboard") or []
            if keyboard:
                message_id = message.get("message_id")
                edited = bool(message_id and edit_telegram_message(chat, int(message_id), text, keyboard))
                if not edited and data.startswith("pulsar:"):
                    send_func("PULSAR: Der zweite Dialog konnte nicht aktualisiert werden. "
                              "Die Freigabe bleibt aus und laeuft ab.")
                elif not edited:
                    send_buttons_func(text,keyboard,priority="normal")
            else:
                send_func(text)
            return "callback"
        except Exception as exc:
            logger.exception("Telegram-Callback fehlgeschlagen: %s",exc)
            answer_callback_query(cbid,"Fehler")
            try: send_func(f"🔴 Fehler bei Telegram-Aktion: {exc}")
            except Exception: logger.debug("Callback-Fehlerantwort fehlgeschlagen",exc_info=True)
            return "handler_error"

    def process_update(self, upd, send_func=send_telegram, send_buttons_func=send_telegram_buttons):
        if not self._remember_update(upd):
            self._audit("duplicate", result="ignoriert")
            return "duplicate"
        if upd.get("callback_query"):
            return self._process_callback(upd.get("callback_query") or {},send_func,send_buttons_func)
        message = upd.get("message") or {}
        chat = str((message.get("chat") or {}).get("id", ""))
        user = str((message.get("from") or {}).get("id", ""))

        if not self._authorized(chat, user):
            logger.warning(
                "Telegram-Befehl von nicht berechtigtem Nutzer ignoriert: chat=%s user=%s", chat, user
            )
            return "unauthorized"

        cmd, args = parse_command(str(message.get("text", "")))
        if not cmd:
            return "ignored"
        self._audit("received", command=cmd, user=user)

        if cmd == "MENU":
            send_buttons_func(
                f"🤖 NEXUS {getattr(config, 'VERSION_NEXUS', 'unbekannt')} – Schnellmenü",
                self._menu_keyboard(), priority="normal")
            return "MENU"

        if cmd == "RISK3":
            token, ttl = self._new_risk3_confirmation()
            keyboard = [[
                {"text": "✅ RISK3 wirklich aktivieren", "callback_data": f"risk3:confirm:{token}"},
                {"text": "❌ Abbrechen", "callback_data": f"risk3:cancel:{token}"},
            ]]
            warning = (
                "⚠️ OFFENSIVES RISIKOPROFIL AKTIVIEREN?\n\n"
                "RISK3 erlaubt bis zu 2 % Kontorisiko pro Trade, bis zu 15 % Kapital "
                "je Position, bis zu 8 offene Positionen und ein Tagesverlustlimit von 6 %.\n\n"
                "Der erste /risk3-Befehl ändert noch NICHTS. "
                f"Bestätige innerhalb von {ttl} Sekunden mit dem Button. "
                "Der Button ist nur einmal verwendbar."
            )
            try:
                send_buttons_func(warning, keyboard, priority="high")
            except Exception as exc:
                self._risk3_confirmations.pop(token, None)
                logger.warning("RISK3-Bestätigungsdialog konnte nicht gesendet werden: %s", exc)
                return "handler_error"
            return "confirm_required"

        if (cmd in {"START", "RESUME", "SHUTDOWNBOT", "STOPP"}
                or (cmd == "CRYPTO" and args[:1] == ["FREQTRADE"])):
            token, ttl = self._new_confirmation(cmd, args)
            keyboard = [[
                {"text": "✅ Bestätigen", "callback_data": f"cmd:{self.session_id}:confirm:{token}"},
                {"text": "❌ Abbrechen", "callback_data": f"cmd:{self.session_id}:cancel:{token}"},
            ]]
            send_buttons_func(
                f"⚠️ {cmd} wirklich ausführen? Noch wurde nichts geändert. "
                f"Die Bestätigung gilt {ttl} Sekunden und nur in dieser Bot-Sitzung.",
                keyboard, priority="normal",
            )
            return "confirm_required"

        try:
            try:
                reply = self._execute(cmd, args)
            except AttributeError:
                reply = self.exec(cmd)
        except Exception as exc:
            logger.exception("Telegram-Befehl %s fehlgeschlagen", cmd)
            try:
                send_func(f"🔴 Fehler bei {cmd}: {exc}")
            except Exception as send_exc:
                logger.debug("Telegram-Fehlerantwort konnte nicht gesendet werden: %s", send_exc)
            return "handler_error"
        self._audit("executed", command=cmd, result="ok", user=user)

        try:
            send_func(str(reply))
        except Exception as exc:
            # Der Befehl ist bereits ausgeführt. Ein Antwortfehler darf die
            # Polling-Schleife nicht töten oder den Befehl erneut auslösen.
            logger.warning("Telegram-Antwort zu %s fehlgeschlagen: %s", cmd, exc)
        return cmd

    def start(self):
        if self.thread and self.thread.is_alive():
            return self.thread
        self.stop_event.clear()
        self.thread = threading.Thread(
            target=self._loop, daemon=True, name="TelegramControl"
        )
        self.thread.start()
        return self.thread

    def stop(self):
        self.stop_event.set()

    def _loop(self):
        while not self.stop_event.is_set():
            try:
                from live_settings import telegram_runtime
                runtime = telegram_runtime()
                if (not getattr(config, "TELEGRAM_CONTROL_ENABLED", True)
                        or not runtime["enabled"] or not runtime["configured"]):
                    self._status("disabled", "Telegram wurde in der WebUI deaktiviert.")
                    self.stop_event.wait(2)
                    continue
                token = str(runtime["token"]).strip()
                url = f"https://api.telegram.org/bot{token}/getUpdates"
                poll_seconds = max(1, min(50, int(getattr(config, "TELEGRAM_CONTROL_POLL_SECONDS", 20))))
                response = requests.get(
                    url,
                    params={
                        "timeout": poll_seconds,
                        "offset": self.offset,
                        "allowed_updates": '["message","callback_query"]',
                    },
                    timeout=(5, poll_seconds + 15),
                )

                try:
                    data = response.json()
                except ValueError:
                    data = {}

                if response.status_code == 409 or (
                    not data.get("ok")
                    and "conflict" in str(data.get("description", "")).lower()
                ):
                    self.consecutive_errors += 1
                    detail = (
                        "Telegram 409 Conflict: Es pollt wahrscheinlich eine zweite "
                        "Bot-Instanz/getUpdates-Sitzung. Fernsteuerung wartet."
                    )
                    self._status("conflict", detail, http_status=409)
                    self._alert(detail)
                    self.stop_event.wait(15)
                    continue

                if not response.ok or not data.get("ok"):
                    raise RuntimeError(
                        data.get("description") or response.text[:300]
                    )

                self.consecutive_errors = 0
                self._status("ok", "Long-Polling erreichbar.",
                             last_poll_success_at=datetime.now(timezone.utc).isoformat(),
                             received_updates=len(data.get("result", [])))

                for upd in data.get("result", []):
                    new_offset = int(upd.get("update_id", 0)) + 1
                    try:
                        self.process_update(upd)
                    except Exception as exc:
                        # Ein einzelnes kaputtes Update darf den Listener nicht
                        # dauerhaft blockieren.
                        logger.exception(
                            "Telegram-Update %s konnte nicht verarbeitet werden: %s",
                            upd.get("update_id"),
                            exc,
                        )
                    finally:
                        self.offset = max(self.offset, new_offset)
                        self._save_offset()

            except requests.ReadTimeout:
                # Normales Long-Polling kann leer auslaufen. Das ist kein
                # Fernsteuerungsfehler und soll das Dashboard nicht zuspammen.
                self.consecutive_errors = 0
                self._status("ok", "Long-Polling erreichbar (keine neuen Updates).")
                logger.debug("Telegram Long-Polling ohne Update beendet; neuer Poll startet.")
                continue
            except requests.RequestException as exc:
                self.consecutive_errors += 1
                self._status("network_error", str(exc))
                logger.warning("Telegram Long-Polling Netzwerkfehler: %s", exc)
                if self.consecutive_errors >= 3:
                    self._alert(
                        f"Telegram-Fernsteuerung hat {self.consecutive_errors} "
                        f"Netzwerkfehler in Folge: {exc}"
                    )
                self.stop_event.wait(5)
            except Exception as exc:
                self.consecutive_errors += 1
                self._status("error", str(exc))
                logger.exception("Telegram-Steuerung Fehler: %s", exc)
                if self.consecutive_errors >= 3:
                    self._alert(
                        f"Telegram-Fernsteuerung ist gestört: {exc}"
                    )
                self.stop_event.wait(5)
