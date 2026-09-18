"""Telegram-only notification service for TradingBot 8.1.1 NEXUS.

This release deliberately removes e-mail and TextMeBot from the active code
path. Telegram is the single notification/control channel and therefore gets
all reliability work: persistent queue, local pacing, HTTP 429 retry_after,
message chunking and priorities. Trade-Ereignisse werden vor dem ersten Sendeversuch persistent eingereiht.
"""
from __future__ import annotations

import json
import hashlib
import logging
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

import requests
import config
from safe_persistence import atomic_write_json, atomic_write_text
from state_lock import critical_state_lock

logger = logging.getLogger(__name__)

_TELEGRAM_WORKER_STARTED = False
_TELEGRAM_QUEUE_LOCK = threading.RLock()
_TELEGRAM_SEND_LOCK = threading.Lock()
_TRADE_BUFFER: list[str] = []
_TRADE_BUFFER_LOCK = threading.Lock()
_LAST_STALL_LOG_AT = 0.0


def _priority(subject: str) -> str:
    s = (subject or "").upper()
    if any(x in s for x in (
        "KAUF", "VERKAUF", "STOP", "TAKE-PROFIT", "ABGESTUERZT", "ABGESTÜRZT",
        "FEHLER", "NOTFALL", "RISIKO LIMIT", "AUTH", "VERBINDUNG ABGEBROCHEN",
    )):
        return "critical"
    if any(x in s for x in ("WIEDERHERGESTELLT", "WIEDER DA", "START", "GESTOPPT", "TAGESBERICHT")):
        return "normal"
    return "low"


def _credentials() -> tuple[str, str]:
    from live_settings import telegram_runtime
    status = telegram_runtime()
    return str(status["token"]), str(status["chat_id"])


def _runtime() -> dict:
    from live_settings import telegram_runtime
    return telegram_runtime()


def _state_root() -> Path:
    return Path(os.getenv("TRADINGBOT_TEST_STATE_DIR", "").strip() or Path(__file__).resolve().parent)

def _queue_path() -> Path:
    return _state_root() / getattr(config, "TELEGRAM_QUEUE_FILE", "telegram_queue.json")


def _load_state(*, strict: bool = False) -> dict:
    p = _queue_path()
    try:
        if p.exists():
            d = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(d, dict):
                d.setdefault("queue", [])
                d.setdefault("sent_at", [])
                d.setdefault("blocked_until", 0.0)
                d.setdefault("last_success_at", 0.0)
                d.setdefault("last_failure_at", 0.0)
                d.setdefault("consecutive_failures", 0)
                d.setdefault("last_failure_detail", "")
                d.setdefault("delivered_event_ids", [])
                d.setdefault("delivered_dedupe", [])
                return d
    except Exception as exc:
        logger.warning("Telegram-Queue konnte nicht gelesen werden: %s", exc)
        if strict:
            raise RuntimeError(
                "Telegram-Queue ist unlesbar; kritische Meldung wird nicht "
                "durch einen leeren Zustand ueberschrieben") from exc
    return {"queue": [], "sent_at": [], "blocked_until": 0.0,
            "last_success_at": 0.0, "last_failure_at": 0.0,
            "consecutive_failures": 0, "last_failure_detail": "",
            "delivered_event_ids": [], "delivered_dedupe": []}


def _save_state(state: dict) -> None:
    atomic_write_json(_queue_path(), state)


def _stall_flag_path() -> Path:
    return _state_root() / getattr(config, "TELEGRAM_STALL_FLAG_FILE", "telegram_delivery_stalled.flag")


def _delivery_health(state: Optional[dict] = None, now: Optional[float] = None) -> dict:
    """Lokale Zustellgesundheit ohne Telegram-Netzabruf.

    Ein Stau liegt vor, wenn eine kritische Nachricht zu lange wartet, irgendeine
    Nachricht sehr lange wartet oder bei vorhandener Queue wiederholt echte
    Sendeversuche fehlschlagen. Die Queue bleibt dabei unangetastet.
    """
    state = state if isinstance(state, dict) else _load_state()
    now = float(now or time.time())
    q = list(state.get("queue", []) or [])

    def age(item):
        try:
            return max(0.0, now - float(item.get("created_at", now) or now))
        except Exception:
            return 0.0

    ages = [age(x) for x in q]
    critical_ages = [age(x) for x in q if str(x.get("priority", "normal")) == "critical"]
    oldest = max(ages) if ages else 0.0
    oldest_critical = max(critical_ages) if critical_ages else 0.0
    failures = max(0, int(state.get("consecutive_failures", 0) or 0))
    critical_limit = max(10.0, float(getattr(config, "TELEGRAM_STALL_CRITICAL_SECONDS", 120)))
    any_limit = max(critical_limit, float(getattr(config, "TELEGRAM_STALL_ANY_SECONDS", 600)))
    failure_limit = max(1, int(getattr(config, "TELEGRAM_STALL_FAILURE_COUNT", 5)))
    stalled = bool(
        oldest_critical >= critical_limit
        or oldest >= any_limit
        or (q and failures >= failure_limit)
    )
    reasons = []
    if oldest_critical >= critical_limit:
        reasons.append(f"kritische Nachricht wartet {oldest_critical:.0f}s")
    if oldest >= any_limit:
        reasons.append(f"aelteste Nachricht wartet {oldest:.0f}s")
    if q and failures >= failure_limit:
        reasons.append(f"{failures} Sendeversuche in Folge fehlgeschlagen")
    return {
        "delivery_stalled": stalled,
        "queued": len(q),
        "oldest_queue_age_seconds": oldest,
        "oldest_critical_age_seconds": oldest_critical,
        "consecutive_failures": failures,
        "last_success_at": float(state.get("last_success_at", 0.0) or 0.0),
        "last_failure_at": float(state.get("last_failure_at", 0.0) or 0.0),
        "last_failure_detail": str(state.get("last_failure_detail", "") or ""),
        "stall_reason": "; ".join(reasons),
    }


def _update_stall_marker(state: Optional[dict] = None) -> dict:
    """Schreibt/entfernt ein lokales Alarm-Flag und protokolliert den Stau.

    Das Flag ist absichtlich unabhaengig von Telegram: ein externer Watchdog
    oder ein Mensch kann es lokal sehen, selbst wenn Telegram selbst ausfaellt.
    """
    global _LAST_STALL_LOG_AT
    health = _delivery_health(state)
    flag = _stall_flag_path()
    if health["delivery_stalled"]:
        try:
            atomic_write_text(flag, health.get("stall_reason") or "Telegram-Zustellstau")
        except Exception as exc:
            logger.warning("Telegram-Stauflag konnte nicht geschrieben werden: %s", exc)
        cooldown = max(30.0, float(getattr(config, "TELEGRAM_STALL_ALERT_COOLDOWN_SECONDS", 300)))
        now_mono = time.monotonic()
        if now_mono - _LAST_STALL_LOG_AT >= cooldown:
            _LAST_STALL_LOG_AT = now_mono
            logger.error("TELEGRAM ZUSTELLSTAU: %s", health.get("stall_reason") or "unbekannt")
    else:
        try:
            flag.unlink(missing_ok=True)
        except Exception:
            __import__("logging").getLogger(__name__).debug("Best-effort-Ausnahme unterdrueckt", exc_info=True)
    return health


def _mark_failure(detail: str) -> None:
    with _TELEGRAM_QUEUE_LOCK, critical_state_lock(_queue_path()):
        state = _load_state(strict=True)
        state["last_failure_at"] = time.time()
        state["consecutive_failures"] = int(state.get("consecutive_failures", 0) or 0) + 1
        state["last_failure_detail"] = str(detail or "")[:1000]
        _save_state(state)
        _update_stall_marker(state)


def _prune_sent(state: dict, now: Optional[float] = None) -> None:
    now = now or time.time()
    state["sent_at"] = [float(x) for x in state.get("sent_at", []) if now - float(x) < 60.0]


def _dedupe_key(message: str, reply_markup=None, silent: bool = False) -> str:
    """Stabiler Fingerabdruck einer Telegram-Nachricht ohne Geheimdaten."""
    roh = json.dumps(
        {"message": str(message), "reply_markup": reply_markup,
         "silent": bool(silent)},
        ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str,
    )
    return hashlib.sha256(roh.encode("utf-8")).hexdigest()


def _prune_dedupe(state: dict, now: Optional[float] = None) -> None:
    now = float(now or time.time())
    window = max(1.0, float(getattr(config, "TELEGRAM_DEDUP_WINDOW_SECONDS", 25.0)))
    sauber = []
    for row in state.get("delivered_dedupe", []) or []:
        try:
            if now - float(row.get("at", 0.0) or 0.0) <= window:
                sauber.append({"key": str(row.get("key") or ""),
                               "at": float(row.get("at") or 0.0)})
        except (TypeError, ValueError, AttributeError):
            continue
    state["delivered_dedupe"] = sauber[-2000:]


def _enqueue(message: str, priority: str = "normal", reply_markup=None, silent: bool = False,
             event_id: str = "", coalesce: bool = True) -> str:
    with _TELEGRAM_QUEUE_LOCK, critical_state_lock(_queue_path()):
        state = _load_state(strict=True); q = state.setdefault("queue", []); now = time.time()
        _prune_dedupe(state, now)
        event_id = str(event_id or "")
        delivered = set(str(x) for x in state.get("delivered_event_ids", []) or [])
        if event_id and (event_id in delivered or any(str(x.get("event_id")) == event_id for x in q)):
            return event_id
        dedupe_key = _dedupe_key(message, reply_markup, silent)
        if any(str(x.get("dedupe_key") or _dedupe_key(
                x.get("message", ""), x.get("reply_markup"), bool(x.get("silent", False))))
               == dedupe_key for x in q):
            return f"dedupe:{dedupe_key}"
        if any(str(x.get("key") or "") == dedupe_key
               for x in state.get("delivered_dedupe", []) or []):
            return f"dedupe:{dedupe_key}"
        item_id = event_id or f"tg-{uuid.uuid4().hex}"
        # Das Sammelfenster gilt bewusst auch fuer kritische Meldungen: es
        # verhindert, dass derselbe Text in Sekundenabstand mehrfach zugestellt
        # wird. Die Nachricht ist dabei nie verloren, sie liegt persistent in
        # der Queue -- siehe den Rueckgabewert von send_telegram().
        delay = (max(0.0, float(getattr(config, "TELEGRAM_COALESCE_SECONDS", 30.0)))
                 if coalesce else 0.0)
        q.append({"id": item_id, "message": str(message), "priority": priority,
                  "created_at": now, "reply_markup": reply_markup,
                  "silent": bool(silent), "dedupe_key": dedupe_key,
                  "not_before": now + delay})
        if event_id:
            q[-1]["event_id"] = event_id
        max_q = max(20, int(getattr(config, "TELEGRAM_MAX_QUEUE_ITEMS", 250)))
        if len(q) > max_q:
            # discard oldest low-priority items first; critical messages are kept.
            q.sort(key=lambda x: ({"low":0,"normal":1,"critical":2}.get(x.get("priority","normal"),1), x.get("created_at",0)))
            while len(q) > max_q and q and q[0].get("priority") == "low":
                q.pop(0)
            if len(q) > max_q:
                logger.error("Telegram-Queue ueber Limit (%d). Kritische Meldungen werden nicht still verworfen.", len(q))
        _save_state(state)
        return item_id


def _chunks(text: str) -> list[str]:
    max_len = int(getattr(config, "TELEGRAM_MAX_MESSAGE_CHARS", 3900))
    if len(text) <= max_len:
        return [text]
    chunks, current = [], ""
    for line in str(text).splitlines():
        candidate = line if not current else current + "\n" + line
        if len(candidate) <= max_len:
            current = candidate; continue
        if current: chunks.append(current)
        while len(line) > max_len:
            chunks.append(line[:max_len]); line = line[max_len:]
        current = line
    if current: chunks.append(current)
    return chunks


def _wait_seconds() -> float:
    now = time.time()
    with _TELEGRAM_QUEUE_LOCK:
        state = _load_state(); _prune_sent(state, now)
        blocked = float(state.get("blocked_until", 0.0) or 0.0)
        if blocked > now: return blocked - now
        sent = state.get("sent_at", [])
        if sent:
            return max(0.0, float(sent[-1]) + max(0.25, float(getattr(config,"TELEGRAM_MIN_INTERVAL_SECONDS",1.1))) - now)
    return 0.0


def _mark_sent() -> None:
    with _TELEGRAM_QUEUE_LOCK, critical_state_lock(_queue_path()):
        now = time.time()
        state = _load_state(strict=True); state.setdefault("sent_at", []).append(now); state["blocked_until"] = 0.0
        state["last_success_at"] = now
        state["consecutive_failures"] = 0
        state["last_failure_detail"] = ""
        _prune_sent(state, now); _save_state(state); _update_stall_marker(state)


def _set_block(seconds: float) -> None:
    with _TELEGRAM_QUEUE_LOCK, critical_state_lock(_queue_path()):
        state = _load_state(strict=True); state["blocked_until"] = max(float(state.get("blocked_until",0) or 0), time.time()+max(1.0,seconds)); _save_state(state)


def _send_one(part: str, reply_markup=None, silent: bool = False) -> tuple[bool, str, float]:
    token, chat_id = _credentials()
    if not token or not chat_id: return False, "Bot-Token/Chat-ID fehlen", 0.0
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": part, "disable_web_page_preview": True,
                  "disable_notification": bool(silent),
                  **({"reply_markup": reply_markup} if reply_markup else {})},
            timeout=float(getattr(config,"TELEGRAM_TIMEOUT_SECONDS",20)),
        )
        try: data = resp.json()
        except ValueError: data = {}
        if resp.ok and data.get("ok"): return True, "ok", 0.0
        retry_after = 0.0
        if resp.status_code == 429:
            try: retry_after = float((data.get("parameters") or {}).get("retry_after") or 0.0)
            except Exception: retry_after = 0.0
            retry_after = max(retry_after, float(getattr(config,"TELEGRAM_RETRY_DELAY_SECONDS",5)))
        return False, f"HTTP {resp.status_code}: {data.get('description') or (resp.text or '')[:500]}", retry_after
    except requests.RequestException as exc:
        return False, f"NETWORK: {exc}", float(getattr(config,"TELEGRAM_RETRY_DELAY_SECONDS",5))
    except Exception as exc:
        return False, f"ERROR: {exc}", float(getattr(config,"TELEGRAM_RETRY_DELAY_SECONDS",5))


def _send_direct(payload: str, reply_markup=None, silent: bool = False) -> tuple[bool, str]:
    with _TELEGRAM_SEND_LOCK:
        parts = _chunks(payload)
        for i, part in enumerate(parts):
            if _wait_seconds() > 0: return False, "\n".join(parts[i:])
            ok, detail, retry_after = _send_one(part, reply_markup if i == len(parts)-1 else None, silent)
            if not ok:
                if retry_after: _set_block(retry_after)
                _mark_failure(detail)
                logger.warning("Telegram-Versand fehlgeschlagen: %s", detail)
                return False, "\n".join(parts[i:])
            _mark_sent()
        return True, ""


def flush_telegram_queue(max_messages: int = 1) -> int:
    if not _runtime()["enabled"]: return 0
    sent = 0
    for _ in range(max(1,int(max_messages))):
        with _TELEGRAM_QUEUE_LOCK, critical_state_lock(_queue_path()):
            state = _load_state(strict=True); q = state.get("queue", [])
            if not q or _wait_seconds() > 0: break
            rank={"critical":0,"normal":1,"low":2}
            now = time.time()
            verfuegbar = [x for x in q
                          if float(x.get("claim_until", 0.0) or 0.0) <= now
                          and float(x.get("not_before", 0.0) or 0.0) <= now]
            if not verfuegbar:
                break
            item=min(verfuegbar,key=lambda x:(rank.get(x.get("priority","normal"),1),x.get("created_at",0)))
            item_id=item.get("id"); payload=str(item.get("message", "")); markup=item.get("reply_markup"); silent=bool(item.get("silent",False))
            claim_id = uuid.uuid4().hex
            item["claim_id"] = claim_id
            item["claim_until"] = now + max(
                10.0, float(getattr(config, "TELEGRAM_CLAIM_TIMEOUT_SECONDS", 90.0)))
            _save_state(state)
        ok, remaining = _send_direct(payload, markup, silent)
        with _TELEGRAM_QUEUE_LOCK, critical_state_lock(_queue_path()):
            state = _load_state(strict=True); q=state.get("queue",[])
            aktuell = next((x for x in q if x.get("id") == item_id), None)
            if aktuell is None or str(aktuell.get("claim_id") or "") != claim_id:
                logger.warning("Telegram-Queue-Claim %s ist nicht mehr aktuell.", item_id)
                if not ok:
                    break
                continue
            if ok:
                event_id = str(item.get("event_id") or "")
                if event_id:
                    delivered = list(state.get("delivered_event_ids", []) or [])
                    if event_id not in delivered:
                        delivered.append(event_id)
                    state["delivered_event_ids"] = delivered[-2000:]
                _prune_dedupe(state)
                state.setdefault("delivered_dedupe", []).append({
                    "key": str(item.get("dedupe_key") or _dedupe_key(payload, markup, silent)),
                    "at": time.time(),
                })
                state["delivered_dedupe"] = state["delivered_dedupe"][-2000:]
                state["queue"]=[x for x in q if x.get("id")!=item_id]; sent += 1
            else:
                if remaining and remaining != payload:
                    aktuell["message"] = remaining
                aktuell.pop("claim_id", None)
                aktuell.pop("claim_until", None)
            _save_state(state)
        if not ok: break
    return sent


def send_telegram(message: str, priority: str = "normal", queue_on_fail: bool = True,
                  silent: bool | None = None, event_id: str = "") -> bool:
    runtime = _runtime()
    if not runtime["enabled"]:
        return False
    notify_mode = str(runtime["notification_mode"] or "ON").upper()
    # OFF unterdrueckt Routine, aber niemals kritische Trade-/Ausfallmeldungen.
    if notify_mode == "OFF" and priority != "critical":
        logger.info("Telegram-Routinemeldung wegen Modus OFF unterdrueckt.")
        return False
    silent = bool(notify_mode == "SILENT") if silent is None else bool(silent)
    if priority == "critical":
        silent = False
    # Sicherheitsrelevante Meldungen werden ZUERST dauerhaft gespeichert.
    # Dadurch geht ein bestaetigter Trade auch dann nicht verloren, wenn Token,
    # Chat-ID oder Netzwerk genau in diesem Moment nicht verfuegbar sind.
    if queue_on_fail:
        eingereiht = _enqueue(str(message), priority, silent=silent, event_id=event_id)
        token, chat_id = _credentials()
        if not token or not chat_id:
            logger.warning("Telegram ist aktiviert, aber Bot-Token/Chat-ID fehlen; Nachricht bleibt persistent in der Queue.")
            return False
        if flush_telegram_queue(max_messages=1) > 0:
            return True
        # v9.1: Ehrlicher Rueckgabewert. Eine Nachricht, die im Sammelfenster
        # liegt oder als Duplikat erkannt wurde, ist NICHT fehlgeschlagen --
        # der Worker stellt sie zu. Vorher lieferte send_telegram dafuer False
        # und hat Aufrufer, die den Wert auswerten, in die Irre gefuehrt.
        return bool(eingereiht)
    token, chat_id = _credentials()
    if not token or not chat_id:
        logger.warning("Telegram ist aktiviert, aber Bot-Token/Chat-ID fehlen.")
        return False
    return _send_direct(str(message), silent=silent)[0]



def send_telegram_buttons(message: str, inline_keyboard, priority: str = "normal") -> bool:
    """Persistente Telegram-Nachricht mit Inline-Buttons.

    Die Callback-Daten duerfen nur kurze interne IDs enthalten; die eigentliche
    Berechtigung und Zustandsmaschine liegen in telegram_steuerung/
    universe_proposals.
    """
    if not _runtime()["enabled"]:
        return False
    markup={"inline_keyboard": list(inline_keyboard or [])}
    _enqueue(str(message), priority, reply_markup=markup, coalesce=False)
    token, chat_id = _credentials()
    if not token or not chat_id:
        logger.warning("Telegram-Buttons eingereiht, aber Credentials fehlen.")
        return False
    return flush_telegram_queue(max_messages=1) > 0


def send_telegram_bound(message: str, inline_keyboard):
    """Send once and return the exact message identity for a bound approval.

    A timeout is not retried or queued: a delivered but unacknowledged message
    cannot acquire execution authority without its recorded message ID.
    """
    if not _runtime()["enabled"]:
        return None
    token, chat_id = _credentials()
    if not token or not chat_id:
        return None
    try:
        response = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": str(chat_id), "text": str(message)[:4096],
                  "disable_web_page_preview": True,
                  "reply_markup": {"inline_keyboard": list(inline_keyboard or [])}},
            timeout=float(getattr(config, "TELEGRAM_TIMEOUT_SECONDS", 20)))
        data = response.json()
        result = data.get("result") or {}
        if (response.ok and data.get("ok") and result.get("message_id")
                and str((result.get("chat") or {}).get("id")) == str(chat_id)):
            return str(result["message_id"])
    except Exception:
        logger.warning("PULSAR-Freigabenachricht nicht bestaetigt; kein erneutes Senden")
    return None


def answer_callback_query(callback_query_id: str, text: str = "") -> bool:
    token,_chat_id=_credentials()
    if not token or not callback_query_id:
        return False
    try:
        r=requests.post(
            f"https://api.telegram.org/bot{token}/answerCallbackQuery",
            json={"callback_query_id":str(callback_query_id),"text":str(text or "")[:180]},
            timeout=float(getattr(config,"TELEGRAM_TIMEOUT_SECONDS",20)),
        )
        try: data=r.json()
        except ValueError: data={}
        return bool(r.ok and data.get("ok"))
    except Exception as exc:
        logger.debug("Telegram Callback-Acknowledge fehlgeschlagen: %s",exc)
        return False


def edit_telegram_message(chat_id: str, message_id: int, text: str, inline_keyboard=None) -> bool:
    """Aktualisiert eine Menü-/Statusnachricht; bei Fehler entscheidet der Aufrufer über Fallback."""
    token, _ = _credentials()
    if not token or not chat_id or not message_id:
        return False
    payload = {
        "chat_id": str(chat_id), "message_id": int(message_id),
        "text": str(text or "")[:4096], "disable_web_page_preview": True,
    }
    if inline_keyboard is not None:
        payload["reply_markup"] = {"inline_keyboard": list(inline_keyboard or [])}
    try:
        response = requests.post(
            f"https://api.telegram.org/bot{token}/editMessageText",
            json=payload, timeout=float(getattr(config, "TELEGRAM_TIMEOUT_SECONDS", 20)),
        )
        data = response.json()
        # "message is not modified" ist funktional bereits der Zielzustand.
        return bool(response.ok and data.get("ok")) or "not modified" in str(data.get("description", "")).lower()
    except Exception as exc:
        logger.debug("Telegram-Message-Edit fehlgeschlagen: %s", exc)
        return False
def send_document(path, caption: str = "") -> bool:
    """Sendet eine Datei direkt ueber Telegram sendDocument.

    Dokumente werden absichtlich nicht in die Text-Queue serialisiert. Bei
    Fehler bleibt die lokale Exportdatei erhalten und der Fehler wird markiert.
    """
    from provider_safety import redact
    if not _runtime()["enabled"]:
        return False
    token, chat_id = _credentials()
    if not token or not chat_id:
        return False
    p = Path(path)
    if not p.exists() or not p.is_file():
        logger.warning("Telegram-Dokument fehlt: %s", p)
        return False
    try:
        with _TELEGRAM_SEND_LOCK, p.open("rb") as fh:
            resp = requests.post(
                f"https://api.telegram.org/bot{token}/sendDocument",
                data={"chat_id": chat_id, "caption": str(caption or "")[:1024]},
                files={"document": (p.name, fh)},
                timeout=max(30.0, float(getattr(config, "TELEGRAM_TIMEOUT_SECONDS", 20))),
            )
        try:
            data = resp.json()
        except ValueError:
            data = {}
        if resp.ok and data.get("ok"):
            _mark_sent()
            return True
        detail = redact(f"HTTP {resp.status_code}: {data.get('description') or resp.text or ''}", secrets=(token, chat_id))
        _mark_failure(detail)
        logger.warning("Telegram-Dokumentversand fehlgeschlagen: %s", detail)
        return False
    except Exception as exc:
        detail = redact(exc, secrets=(token, chat_id))
        _mark_failure(detail)
        logger.warning("Telegram-Dokumentversand fehlgeschlagen: %s", detail)
        return False

def telegram_status(active: bool = True) -> dict:
    runtime = _runtime(); token, chat_id = _credentials(); state=_load_state()
    out={"provider":"Telegram","enabled":bool(runtime["enabled"]),"configured":bool(token and chat_id),
         "chat_id":chat_id,"bot_username":"","api_ok":False,"detail":"","queued":len(state.get("queue",[])),
         "blocked_until":float(state.get("blocked_until",0) or 0)}
    out.update(_update_stall_marker(state))
    if not token: out["detail"]="Bot-Token fehlt."; return out
    if not active:
        out["detail"] = "Passiver Queue-Status; kein automatischer Telegram-Netzabruf."
        return out
    try:
        r=requests.get(f"https://api.telegram.org/bot{token}/getMe",timeout=float(getattr(config,"TELEGRAM_TIMEOUT_SECONDS",20)))
        d=r.json(); out["api_ok"]=bool(r.ok and d.get("ok"))
        if out["api_ok"]:
            out["bot_username"]=str((d.get("result") or {}).get("username", "")); out["detail"]="Bot API erreichbar."
        else: out["detail"]=str(d.get("description") or r.text[:400])
    except Exception as exc: out["detail"]=f"{type(exc).__name__}: {exc}"
    return out


def _worker() -> None:
    while True:
        try:
            flush_telegram_queue(max_messages=1)
            _update_stall_marker()
        except Exception as exc:
            logger.warning("Telegram-Queue Worker: %s", exc)
        time.sleep(max(0.5,float(getattr(config,"TELEGRAM_QUEUE_POLL_SECONDS",1.0))))


def start_telegram_worker() -> None:
    global _TELEGRAM_WORKER_STARTED
    if _TELEGRAM_WORKER_STARTED: return
    _TELEGRAM_WORKER_STARTED=True
    threading.Thread(target=_worker,name="TelegramQueue",daemon=True).start()


def buffer_trade(message: str) -> None:
    with _TRADE_BUFFER_LOCK: _TRADE_BUFFER.append(str(message))


def pending_trade_count() -> int:
    with _TRADE_BUFFER_LOCK: return len(_TRADE_BUFFER)


def flush_trades(header: str = "TRADE-ZUSAMMENFASSUNG") -> bool:
    with _TRADE_BUFFER_LOCK:
        if not _TRADE_BUFFER: return False
        rows=list(_TRADE_BUFFER); _TRADE_BUFFER.clear()
    if len(rows)==1: return send_telegram(rows[0],priority="critical")
    payload=f"{header} ({len(rows)} Trades)\n\n" + ("\n\n" + "-"*28 + "\n\n").join(rows)
    logger.info("Sende gebuendelte Telegram-Trade-Nachricht mit %d Eintraegen.",len(rows))
    return send_telegram(payload,priority="critical")


def notify_trade(subject: str, message: str, bundle: bool = True,
                 event_id: str = "") -> None:
    """Persistiert jede Trade-Meldung sofort in der Telegram-Queue.

    `bundle` bleibt nur fuer API-Kompatibilitaet erhalten. Echtgeld-Ereignisse
    duerfen nie zuerst ausschliesslich in einem RAM-Puffer liegen: ein Prozess-
    oder Stromausfall zwischen Fill und Zyklusende darf keine Kaufmeldung mehr
    vernichten. `send_telegram(..., queue_on_fail=True)` schreibt vor dem ersten
    HTTP-Versuch persistent in die Queue.
    """
    logger.info("TRADE [%s]: %s", subject, message)
    payload = str(message).strip()
    send_telegram(payload, priority="critical", queue_on_fail=True,
                  event_id=str(event_id or ""))


def notify(subject: str, message: str, priority: Optional[str] = None) -> bool:
    logger.info("NOTIFY [%s]: %s",subject,message)
    payload=f"{subject}\n{message}".strip()
    return send_telegram(payload,priority=priority or _priority(subject))


start_telegram_worker()
