"""Privater eToro-WebSocket als schneller Reconciliation-Hinweisgeber.

Die Push-Nachricht ist kein alleiniger Eigentumsbeweis. Sie weckt und
bereichert nur die persistente Beweiskette; der REST-Lookup und der exakte
positionId-Abgleich bleiben autoritativ.
"""
from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Callable

logger = logging.getLogger(__name__)


# 9.5.2 -- Fristen und Takt des Supervisors.
#
# Bis 9.5.1 lief der Stream mit ``run_forever(ping_interval=25,
# ping_timeout=10)``. Das sind RFC-6455-Ping-Frames: der Client verlangt vom
# Server ein Pong-Frame und schliesst die Verbindung selbst, wenn keines
# kommt. Das offizielle eToro-Beispiel kennt diesen Client-Ping nicht -- der
# Server beantwortet ihn offenbar nicht, und der Client hat sich deshalb im
# Zweiminutentakt selbst getrennt:
#
#     20:00:48 connected -> 20:02:48 ping/pong timeout -> 20:03:18 connected
#
# Der Ping ist ersatzlos entfallen. An seine Stelle treten Fristen fuer
# Authentifizierung und Subscription sowie ein Stillewaechter: eine
# Verbindung, die nichts mehr liefert, wird erneuert -- eine, die liefert,
# bleibt bestehen.
AUTH_FRIST_SEKUNDEN = 20.0
SUBSCRIBE_FRIST_SEKUNDEN = 30.0
STILLE_FRIST_SEKUNDEN = 180.0
STABIL_NACH_SEKUNDEN = 120.0
WAECHTER_TAKT_SEKUNDEN = 1.0


@dataclass
class StreamStatus:
    running: bool = False
    connected: bool = False
    authenticated: bool = False
    subscribed: bool = False
    last_message: str | None = None
    last_private_event: str | None = None
    last_error: str = ""
    reconnects: int = 0
    # 9.5.2: Ohne diese Felder war der Stream in der Oberflaeche unsichtbar.
    # runtime_status.json enthielt keinen Streamabschnitt fuer eToro; das
    # Dashboard zeigte durchgehend "ONLINE", weil es nur REST kannte.
    generation: int = 0
    session_id: str = ""
    endpoint: str = ""
    connected_since: str | None = None
    last_close_code: int | None = None
    last_close_reason: str = ""
    next_reconnect_in: float = 0.0
    zustand: str = "OFFLINE"
    private_events_received: int = 0
    private_events_matched: int = 0
    private_events_unmatched: int = 0
    private_events_errors: int = 0
    malformed_messages: int = 0

    def as_dict(self) -> dict:
        return asdict(self)


class EtoroPrivateStream:
    URL = "wss://ws.etoro.com/ws"

    def __init__(self, api_key: str, user_key: str,
                 on_private_event: Callable[[dict], None] | None = None,
                 *, url: str = ""):
        self._api_key = str(api_key or "")
        self._user_key = str(user_key or "")
        self._callback = on_private_event
        self.url = str(url or self.URL)
        self._status = StreamStatus()
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._app = None
        self._letzte_nachricht_monoton = 0.0

    def start(self) -> bool:
        if not self._api_key or not self._user_key:
            return False
        try:
            import websocket  # noqa: F401
        except Exception:
            self._set_error("Paket websocket-client fehlt; REST-Reconciliation bleibt aktiv")
            return False
        if self._thread and self._thread.is_alive():
            return True
        self._stop.clear()
        with self._lock:
            self._status.running = True
        self._thread = threading.Thread(
            target=self._run, name="etoro-private-stream", daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()
        app = self._app
        if app is not None:
            try:
                app.close()
            except Exception:
                logger.debug("eToro-WebSocket konnte nicht geschlossen werden", exc_info=True)
        with self._lock:
            self._status.running = False
            self._status.connected = False
            self._status.authenticated = False
            self._status.subscribed = False

    def status(self) -> StreamStatus:
        with self._lock:
            return StreamStatus(**asdict(self._status))

    def _run(self) -> None:
        import websocket
        backoff = 1.0
        while not self._stop.is_set():
            begonnen = time.monotonic()
            with self._lock:
                self._status.generation += 1
                self._status.session_id = uuid.uuid4().hex[:12]
                self._status.endpoint = self.url
                self._status.zustand = "CONNECTING"
                self._status.next_reconnect_in = 0.0
                generation = self._status.generation
                session = self._status.session_id
            logger.info("eToro-Privatstream verbindet (Generation %s, Sitzung %s, %s)",
                        generation, session, self.url)
            waechter = None
            try:
                self._app = websocket.WebSocketApp(
                    self.url, on_open=self._on_open, on_message=self._on_message,
                    on_error=self._on_error, on_close=self._on_close)
                waechter = threading.Thread(
                    target=self._waechter, args=(self._app,),
                    name=f"etoro-stream-waechter-{generation}", daemon=True)
                waechter.start()
                # KEIN ping_interval: eToro beantwortet den RFC-Ping nicht,
                # und der Client trennte sich deshalb selbst (siehe oben).
                self._app.run_forever()
            except Exception as exc:
                self._set_error(f"{type(exc).__name__}: WebSocket-Lauf abgebrochen")
            finally:
                self._app = None
                if waechter is not None:
                    waechter.join(timeout=2.0)
            gelaufen = time.monotonic() - begonnen
            with self._lock:
                self._status.connected = False
                self._status.authenticated = False
                self._status.subscribed = False
                self._status.connected_since = None
                self._status.zustand = "OFFLINE"
            if self._stop.is_set():
                break
            # Eine Sitzung, die lange stabil lief, setzt den Backoff zurueck.
            # Sonst waechst er nach jedem spaeten Abbruch weiter, bis 30 s
            # Pause entstehen -- genau die Luecken aus dem Log.
            if gelaufen >= STABIL_NACH_SEKUNDEN:
                backoff = 1.0
            with self._lock:
                self._status.next_reconnect_in = backoff
            if self._stop.wait(backoff):
                break
            with self._lock:
                self._status.reconnects += 1
            backoff = min(30.0, backoff * 2.0)
        with self._lock:
            self._status.running = False
            self._status.zustand = "OFFLINE"

    def _waechter(self, app) -> None:
        """Fristen fuer Auth und Subscription, danach Stillewache (9.5.2)."""
        begonnen = time.monotonic()
        while not self._stop.is_set():
            if self._stop.wait(WAECHTER_TAKT_SEKUNDEN):
                return
            if self._app is not app:
                return
            jetzt = time.monotonic()
            with self._lock:
                auth = self._status.authenticated
                sub = self._status.subscribed
                letzte = self._letzte_nachricht_monoton
            grund = ""
            if not auth and jetzt - begonnen >= AUTH_FRIST_SEKUNDEN:
                grund = (f"Keine Authentifizierung innerhalb von "
                         f"{AUTH_FRIST_SEKUNDEN:.0f} s")
            elif auth and not sub and jetzt - begonnen >= SUBSCRIBE_FRIST_SEKUNDEN:
                grund = (f"Keine Subscription-Bestaetigung innerhalb von "
                         f"{SUBSCRIBE_FRIST_SEKUNDEN:.0f} s")
            elif letzte and jetzt - letzte >= STILLE_FRIST_SEKUNDEN:
                grund = (f"Seit {jetzt - letzte:.0f} s keine Nachricht mehr "
                         f"(Grenze {STILLE_FRIST_SEKUNDEN:.0f} s)")
            if not grund:
                continue
            self._set_error(f"{grund}; Verbindung wird erneuert")
            with self._lock:
                self._status.zustand = "DEGRADED"
            try:
                app.close()
            except Exception:
                logger.debug("eToro-Waechter konnte Stream nicht schliessen",
                             exc_info=True)
            return

    def _send(self, operation: str, data: dict) -> None:
        app = self._app
        if app is None:
            return
        app.send(json.dumps({"id": str(uuid.uuid4()), "operation": operation,
                             "data": data}, separators=(",", ":")))

    def _on_open(self, _app) -> None:
        with self._lock:
            self._status.connected = True
            self._status.connected_since = datetime.now(timezone.utc).isoformat()
            self._status.zustand = "CONNECTED"
            self._status.last_error = ""
            self._letzte_nachricht_monoton = time.monotonic()
        self._send("Authenticate", {"userKey": self._user_key,
                                    "apiKey": self._api_key})

    def _on_message(self, _app, message) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self._status.last_message = now
            self._letzte_nachricht_monoton = time.monotonic()
        try:
            payload = json.loads(message)
        except (TypeError, ValueError):
            with self._lock:
                self._status.malformed_messages += 1
            return
        if not isinstance(payload, dict):
            with self._lock:
                self._status.malformed_messages += 1
            return
        operation = str(payload.get("operation") or "")
        if operation == "Authenticate":
            if payload.get("success") is True:
                with self._lock:
                    self._status.authenticated = True
                self._send("Subscribe", {"topics": ["private"], "snapshot": False})
            else:
                self._set_error("eToro-WebSocket-Authentifizierung abgelehnt")
            return
        if operation == "Subscribe":
            with self._lock:
                self._status.subscribed = payload.get("success") is True
                self._status.zustand = ("SUBSCRIBED"
                                        if self._status.subscribed else "CONNECTED")
            return
        envelopes = payload.get("messages") or []
        if not isinstance(envelopes, list):
            with self._lock:
                self._status.malformed_messages += 1
            return
        for envelope in envelopes:
            if not isinstance(envelope, dict):
                with self._lock:
                    self._status.malformed_messages += 1
                continue
            if str(envelope.get("topic") or "") != "private":
                continue
            try:
                raw_content = envelope.get("content") or "{}"
                content = raw_content if isinstance(raw_content, dict) else json.loads(raw_content)
                if not isinstance(content, dict):
                    raise ValueError("private content is not an object")
            except (TypeError, ValueError):
                with self._lock:
                    self._status.malformed_messages += 1
                continue
            event = {"message_id": str(envelope.get("id") or ""),
                     "message_type": str(envelope.get("type") or ""),
                     "received_at_utc": now, "content": content}
            with self._lock:
                self._status.last_private_event = now
                self._status.private_events_received += 1
            if self._callback:
                try:
                    result = self._callback(event)
                    with self._lock:
                        if result:
                            self._status.private_events_matched += 1
                        else:
                            self._status.private_events_unmatched += 1
                except Exception:
                    with self._lock:
                        self._status.private_events_errors += 1
                    logger.warning("eToro-Privatevent konnte nicht verarbeitet werden",
                                   exc_info=True)

    def _on_error(self, _app, error) -> None:
        self._set_error(f"{type(error).__name__}: WebSocket-Fehler")

    def _on_close(self, _app, code, reason) -> None:
        with self._lock:
            self._status.connected = False
            self._status.authenticated = False
            self._status.subscribed = False
            self._status.connected_since = None
            self._status.zustand = "OFFLINE"
            # 9.5.2: Close-Code und -Grund festhalten. Ohne sie liess sich im
            # Nachhinein nicht sagen, ob der Server oder der Client die
            # Verbindung beendet hat -- genau die Frage beim Ping-Problem.
            self._status.last_close_code = (int(code) if code is not None else None)
            self._status.last_close_reason = str(reason or "")[:200]
        if code and not self._stop.is_set():
            self._set_error(f"WebSocket geschlossen ({code})"
                            + (f": {reason}" if reason else ""))

    def _set_error(self, text: str) -> None:
        with self._lock:
            self._status.last_error = str(text)[:300]


__all__ = ["EtoroPrivateStream", "StreamStatus"]
