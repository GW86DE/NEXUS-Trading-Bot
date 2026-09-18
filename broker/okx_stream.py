"""Privater OKX-EEA-WebSocket fuer Account- und Order-Ereignisse.

Der Stream ist eine Beschleunigung, keine einzelne Fehlerquelle: bleibt er
aus oder wird er zu alt, verwendet der Broker automatisch wieder REST. Orders
werden weiterhin ausschliesslich ueber den geprueften REST-Geldpfad gesendet.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

OKX_EEA_PRIVATE_WS = "wss://wseea.okx.com:8443/ws/v5/private"
OKX_EEA_DEMO_PRIVATE_WS = "wss://wseeapap.okx.com:8443/ws/v5/private"


def _zahl(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


# Zustaende des Privatstreams. Vorher gab es nur "connected" und
# "authenticated" -- damit galt der Stream schon als brauchbar, bevor OKX
# die Kanaele ueberhaupt bestaetigt hatte.
OFFLINE = "OFFLINE"
CONNECTED = "CONNECTED"
AUTHENTICATED = "AUTHENTICATED"
SUBSCRIBED = "SUBSCRIBED"
DEGRADED = "DEGRADED"
KONFIGURATIONSFEHLER = "KONFIGURATIONSFEHLER"

# OKX erlaubt im WebSocket-Feld "id" NUR alphanummerische Zeichen, maximal 32.
# Der Bindestrich in der frueheren ID "nexus8-private" war damit unzulaessig
# und hat bei jedem Subscribe Code 60033 ("Parameter id error") ausgeloest --
# danach schloss OKX die Verbindung mit Code 4004 und der Bot verband sich
# im Minutentakt neu, ohne je Account- oder Orderdaten zu bekommen.
SUBSCRIPTION_ID = "nexus8private"

# Fehlercodes, die sich durch Wiederverbinden NICHT beheben lassen.
KONFIGURATIONSCODES = {"60033", "60012", "60013", "60018"}

# Ohne Nachricht laenger als das hier trennt OKX die Verbindung nach etwa
# 30 Sekunden selbst. Ein String-"ping" auf Anwendungsebene haelt sie offen.
KEEPALIVE_SEKUNDEN = 20.0
PONG_TIMEOUT_SEKUNDEN = 10.0


@dataclass(frozen=True)
class StreamStatus:
    running: bool
    connected: bool
    authenticated: bool
    last_message: Optional[str]
    last_error: str
    reconnects: int
    mode: str
    endpoint: str
    zustand: str = OFFLINE
    kanaele: tuple = ()
    healthy: bool = False
    konfigurationsfehler: str = ""
    last_account: Optional[str] = None
    last_order: Optional[str] = None
    last_pong: Optional[str] = None

    def as_dict(self) -> dict:
        return {
            "running": self.running,
            "connected": self.connected,
            "authenticated": self.authenticated,
            "last_message": self.last_message,
            "last_error": self.last_error,
            "reconnects": self.reconnects,
            "mode": self.mode,
            "endpoint": self.endpoint,
            "zustand": self.zustand,
            "kanaele": list(self.kanaele),
            "healthy": self.healthy,
            "konfigurationsfehler": self.konfigurationsfehler,
            "last_account": self.last_account,
            "last_order": self.last_order,
            # 9.5.2: Bis 9.5.1 las broker/okx.py::connection_components() die
            # Namen last_account_update / last_order_update / last_pong. Die
            # gab es hier nie -- im Dashboard standen deshalb dauerhaft drei
            # null-Felder, obwohl die Daten vorlagen. Beide Schreibweisen
            # werden jetzt geliefert, damit kein Aufrufer ins Leere greift.
            "last_account_update": self.last_account,
            "last_order_update": self.last_order,
            "last_pong": self.last_pong,
        }


class OKXPrivateStream:
    """Wiederverbindender, optionaler OKX-V5-Privatstream.

    ``websocket-client`` wird absichtlich erst beim Start importiert. Fehlt
    das Paket in einer alten Installation, bleibt der REST-Betrieb erhalten
    und die WebUI zeigt den Grund als Streamfehler.
    """

    def __init__(self, api_key: str, api_secret: str, passphrase: str, *,
                 demo: bool = True, stale_after: float = 45.0,
                 time_offset_seconds: float = 0.0):
        self._key = str(api_key or "")
        self._secret = str(api_secret or "")
        self._passphrase = str(passphrase or "")
        self.demo = bool(demo)
        self.endpoint = OKX_EEA_DEMO_PRIVATE_WS if self.demo else OKX_EEA_PRIVATE_WS
        self.stale_after = max(10.0, float(stale_after))
        self._time_offset_seconds = float(time_offset_seconds)

        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._app = None
        self._running = False
        self._connected = False
        self._authenticated = False
        self._last_message_at = 0.0
        # Transportkontakt (einschliesslich pong) und fachliche Kontodaten
        # sind getrennte Wahrheiten. Ein pong beweist keine aktuellen Salden.
        self._last_account_at = 0.0
        self._last_order_at = 0.0
        self._last_error = ""
        self._reconnects = 0
        self._balances: dict[str, dict] = {}
        self._orders: dict[str, dict] = {}
        # Erst wenn OKX BEIDE Kanaele bestaetigt hat, gilt der Stream als
        # gesund. Vorher wurde bereits nach dem Login "healthy" gemeldet.
        self._kanaele: set[str] = set()
        self._zustand = OFFLINE
        self._konfigurationsfehler = ""
        self._letzter_ping = 0.0
        self._letzte_nachricht_monoton = 0.0
        self._warte_auf_pong_seit = 0.0
        self._last_pong_at = 0.0

    @property
    def configured(self) -> bool:
        return bool(self._key and self._secret and self._passphrase)

    def start(self) -> bool:
        if not self.configured:
            self._set_error("Zugangsdaten unvollstaendig")
            return False
        try:
            import websocket  # noqa: F401
        except ImportError:
            self._set_error("Paket websocket-client fehlt; REST-Fallback aktiv")
            return False
        with self._lock:
            if self._thread and self._thread.is_alive():
                return True
            self._stop.clear()
            self._running = True
            self._thread = threading.Thread(
                target=self._run, name="OKX-EEA-Private-Stream", daemon=True
            )
            self._thread.start()
        return True

    def stop(self, timeout: float = 3.0) -> None:
        self._stop.set()
        with self._lock:
            app = self._app
            thread = self._thread
        if app is not None:
            try:
                app.close()
            except Exception:
                logger.debug("OKX-Stream konnte nicht sauber geschlossen werden", exc_info=True)
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=max(0.0, float(timeout)))
        with self._lock:
            self._running = False
            self._connected = False
            self._authenticated = False

    def _run(self) -> None:
        try:
            import websocket
        except ImportError:
            self._set_error("Paket websocket-client fehlt; REST-Fallback aktiv")
            return

        pause = 1.0
        while not self._stop.is_set():
            with self._lock:
                self._connected = False
                self._authenticated = False
            try:
                app = websocket.WebSocketApp(
                    self.endpoint,
                    on_open=self._on_open,
                    on_message=self._on_message,
                    on_error=self._on_error,
                    on_close=self._on_close,
                )
                with self._lock:
                    self._app = app
                keepalive = threading.Thread(
                    target=self._keepalive, args=(app,),
                    name="OKX-Stream-Keepalive", daemon=True)
                keepalive.start()
                # OKX dokumentiert fuer V5 einen TEXT-Ping ("ping"/"pong").
                # WebSocket-Control-Frames allein belegen daher keine
                # anwendungsseitig lebende OKX-Verbindung.
                app.run_forever(ping_interval=0)
            except Exception as exc:
                self._set_error(f"{type(exc).__name__}: Streamverbindung beendet")
            finally:
                with self._lock:
                    was_authenticated = self._authenticated
                    self._app = None
                    self._connected = False
                    self._authenticated = False

            with self._lock:
                konfigfehler = bool(self._konfigurationsfehler)
                self._zustand = KONFIGURATIONSFEHLER if konfigfehler else OFFLINE
                self._kanaele = set()

            # Backoff mit Zufallsanteil: viele gleichzeitige Reconnects im
            # exakt gleichen Takt erzeugen sonst Lastspitzen beim Anbieter.
            import random
            if konfigfehler:
                # Ein Parameterfehler heilt nicht durch Warten -- aber wir
                # versuchen es weit auseinandergezogen erneut, falls der
                # Nutzer die Konfiguration zwischenzeitlich korrigiert.
                wartezeit = 300.0
            elif was_authenticated:
                wartezeit = 1.0
            else:
                wartezeit = min(60.0, pause * 2.0)
            wartezeit += random.uniform(0.0, min(5.0, wartezeit * 0.25))

            if self._stop.wait(wartezeit):
                break
            with self._lock:
                self._reconnects += 1
            pause = 1.0 if was_authenticated else min(60.0, pause * 2.0)

        with self._lock:
            self._running = False
            self._zustand = OFFLINE

    def _keepalive(self, app) -> None:
        """TEXT-Ping nach Inaktivitaet; ohne pong innerhalb der Frist reconnecten."""
        while not self._stop.is_set():
            if self._stop.wait(1.0):
                return
            with self._lock:
                laeuft = self._app is app
                jetzt = time.monotonic()
                # 9.5.2: "noch nie eine Nachricht" ist Stille, nicht Frische.
                # ``or jetzt`` haette das als gerade eben empfangen gewertet.
                roh_letzte = self._letzte_nachricht_monoton
                letzte = roh_letzte if roh_letzte else None
                wartet_seit = self._warte_auf_pong_seit
            if not laeuft:
                return
            if wartet_seit and jetzt - wartet_seit >= PONG_TIMEOUT_SEKUNDEN:
                # 9.5.2: Ein ausbleibendes pong ist ein Grund zum Erneuern --
                # aber nur, wenn auch sonst nichts mehr ankommt. Fliessen
                # weiterhin Kontodaten, ist die Verbindung nachweislich am
                # Leben; ein Reconnect wuerde dann nur eine Abbruchschleife
                # erzeugen, wie sie der eToro-Stream mit seinem RFC-Ping
                # gezeigt hat. Der Zustand wird trotzdem sichtbar gemacht.
                stumm = (letzte is None
                         or (jetzt - letzte) >= PONG_TIMEOUT_SEKUNDEN)
                if not stumm:
                    # Kein pong, aber es fliessen weiter Kontodaten: die
                    # Verbindung ist nachweislich am Leben. Ein Reconnect
                    # wuerde hier nur eine Abbruchschleife erzeugen -- genau
                    # das Muster, das der eToro-Stream mit seinem RFC-Ping
                    # zeigt. Der Zustand wird gemeldet, einmal, nicht in
                    # jeder Sekunde erneut.
                    with self._lock:
                        neu_gemeldet = self._zustand != DEGRADED
                        self._zustand = DEGRADED
                    if neu_gemeldet:
                        self._set_error(
                            "OKX antwortet nicht auf den Keepalive, liefert "
                            "aber weiter Daten; Verbindung bleibt bestehen")
                    continue
                self._set_error("Kein pong und keine Daten auf dem OKX-Stream; "
                                "Verbindung wird erneuert")
                with self._lock:
                    self._zustand = DEGRADED
                try:
                    app.close()
                except Exception:
                    logger.debug("OKX-Keepalive konnte Stream nicht schliessen", exc_info=True)
                return
            if wartet_seit or (letzte is not None
                               and jetzt - letzte < KEEPALIVE_SEKUNDEN):
                continue
            try:
                app.send("ping")
                with self._lock:
                    self._letzter_ping = jetzt
                    self._warte_auf_pong_seit = jetzt
            except Exception:
                logger.debug("OKX-Keepalive konnte nicht senden", exc_info=True)
                return

    def _signature(self, timestamp: str) -> str:
        message = timestamp + "GET" + "/users/self/verify"
        digest = hmac.new(
            self._secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256
        ).digest()
        return base64.b64encode(digest).decode("ascii")

    def _on_open(self, app) -> None:
        with self._lock:
            self._connected = True
            self._last_error = ""
            self._zustand = CONNECTED
            self._kanaele = set()
            jetzt = time.monotonic()
            self._letzter_ping = jetzt
            self._letzte_nachricht_monoton = jetzt
            self._warte_auf_pong_seit = 0.0
        # Der Login verlangt Unix-Sekunden und laeuft nach 30 Sekunden ab.
        # Derselbe zuvor per REST gemessene Offset wird deshalb auch hier
        # verwendet.
        timestamp = str(int(time.time() + self._time_offset_seconds))
        app.send(json.dumps({
            "op": "login",
            "args": [{
                "apiKey": self._key,
                "passphrase": self._passphrase,
                "timestamp": timestamp,
                "sign": self._signature(timestamp),
            }],
        }, separators=(",", ":")))

    def _on_message(self, app, raw: str) -> None:
        if raw == "pong":
            self._touch(pong=True)
            with self._lock:
                self._letzter_ping = time.monotonic()
            return
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError):
            return
        self._touch()

        event = str(payload.get("event", ""))
        if event == "notice":
            # OKX kuendigt Wartung/Verbindungswechsel rund 60 Sekunden vor
            # dem Abbruch an. Sofort reconnecten, damit REST derweil als
            # sichere Quelle dient und der Stream nicht still veraltet.
            code = str(payload.get("code", "?"))
            meldung = str(payload.get("msg", "Servicehinweis"))
            self._set_error(f"OKX-Wartungshinweis {code}: {meldung}")
            with self._lock:
                self._zustand = DEGRADED
            app.close()
            return
        if event == "channel-conn-count":
            logger.info("OKX-Privatstream: Verbindungsauslastung %s",
                        payload.get("connCount", "?"))
            return
        if event == "channel-conn-count-error":
            self._set_error("OKX-WebSocket-Verbindungslimit erreicht")
            with self._lock:
                self._zustand = DEGRADED
            app.close()
            return
        if event == "login":
            if str(payload.get("code", "")) != "0":
                self._set_error(f"Login abgelehnt: Code {payload.get('code', '?')}")
                app.close()
                return
            with self._lock:
                self._authenticated = True
            with self._lock:
                self._zustand = AUTHENTICATED
                self._kanaele = set()
            app.send(json.dumps({
                "id": SUBSCRIPTION_ID,
                "op": "subscribe",
                "args": [
                    {"channel": "account"},
                    {"channel": "orders", "instType": "SPOT"},
                ],
            }, separators=(",", ":")))
            return
        if event == "subscribe":
            # Erst diese Bestaetigung macht den Stream wirklich nutzbar.
            kanal = str((payload.get("arg") or {}).get("channel", ""))
            if kanal:
                with self._lock:
                    self._kanaele.add(kanal)
                    if {"account", "orders"} <= self._kanaele:
                        self._zustand = SUBSCRIBED
                        self._konfigurationsfehler = ""
                logger.info("OKX-Privatstream: Kanal %s bestaetigt", kanal)
            return
        if event == "unsubscribe":
            kanal = str((payload.get("arg") or {}).get("channel", ""))
            with self._lock:
                self._kanaele.discard(kanal)
                self._zustand = DEGRADED
            return
        if event == "error":
            code = str(payload.get("code", "?"))
            meldung = str(payload.get("msg", "Fehler"))
            self._set_error(f"WebSocket Code {code}: {meldung}")
            if code in KONFIGURATIONSCODES:
                # Endlos neu verbinden hilft hier nicht: der Fehler steckt in
                # der Anfrage, nicht in der Leitung. Sichtbar machen und lange
                # pausieren statt im Minutentakt dieselbe Ablehnung erzeugen.
                with self._lock:
                    self._zustand = KONFIGURATIONSFEHLER
                    self._konfigurationsfehler = f"Code {code}: {meldung}"
                logger.error("OKX-Privatstream: Konfigurationsfehler %s (%s). "
                             "Reconnect wird stark verlangsamt.", code, meldung)
            app.close()
            return

        channel = str((payload.get("arg") or {}).get("channel", ""))
        data = payload.get("data") or []
        if channel == "account":
            self._update_balances(
                data, replace=str(payload.get("eventType") or "").lower() == "snapshot")
        elif channel == "orders":
            self._update_orders(data)

    def _update_balances(self, rows: list, *, replace: bool = False) -> None:
        aktualisiert: dict[str, dict] = {}
        for konto in rows:
            for detail in konto.get("details") or []:
                ccy = str(detail.get("ccy", "")).upper()
                if not ccy:
                    continue
                aktualisiert[ccy] = {
                    "cash": _zahl(detail.get("availBal")),
                    "gesamt": _zahl(detail.get("cashBal")) or _zahl(detail.get("eq")),
                    "frozen": _zahl(detail.get("frozenBal")),
                    "eq_usd": _zahl(detail.get("eqUsd")),
                }
        with self._lock:
            self._last_account_at = time.time()
            if replace:
                # Der Snapshot ist autoritativ. Omitted currencies duerfen
                # nicht als Geistersaldo aus einem alten Cache weiterleben.
                self._balances = {}
            if aktualisiert:
                self._balances.update(aktualisiert)
                # Nullsalden aus Delta-Updates explizit entfernen.
                for ccy, value in list(self._balances.items()):
                    if not any(abs(float(value.get(k, 0.0) or 0.0)) > 1e-15
                               for k in ("cash", "gesamt", "frozen")):
                        self._balances.pop(ccy, None)

    def _update_orders(self, rows: list) -> None:
        with self._lock:
            self._last_order_at = time.time()
            for row in rows:
                for key in (str(row.get("ordId", "")), str(row.get("clOrdId", ""))):
                    if key:
                        self._orders[key] = dict(row)
            if len(self._orders) > 1000:
                self._orders = dict(list(self._orders.items())[-600:])

    def _on_error(self, app, error) -> None:
        self._set_error(f"{type(error).__name__}: Verbindung gestoert")

    def _on_close(self, app, code, message) -> None:
        with self._lock:
            self._connected = False
            self._authenticated = False
        if code and not self._stop.is_set():
            self._set_error(f"Verbindung geschlossen (Code {code})")

    def _touch(self, *, pong: bool = False) -> None:
        """Datenverkehr vermerken.

        KORREKTUR 9.5.2: Bis 9.5.1 setzte JEDE eingehende Nachricht
        ``_warte_auf_pong_seit`` zurueck. Der Keepalive galt damit als
        beantwortet, sobald irgendein Kanal etwas schickte -- ein echtes
        ``"pong"`` wurde nie nachgewiesen. Eine halbtote Verbindung, die noch
        Daten schiebt aber nicht mehr antwortet, waere unentdeckt geblieben.
        Nur noch ein tatsaechliches pong quittiert den Keepalive.
        """
        with self._lock:
            self._last_message_at = time.time()
            self._letzte_nachricht_monoton = time.monotonic()
            if pong:
                self._warte_auf_pong_seit = 0.0
                self._last_pong_at = time.time()

    def _set_error(self, text: str) -> None:
        with self._lock:
            self._last_error = str(text)[:240]
        logger.warning("OKX-Privatstream: %s", text)

    def fresh(self, max_age: Optional[float] = None) -> bool:
        """Sind die Streamdaten aktuell UND vollstaendig abonniert?

        Frueher reichte hier ein erfolgreicher Login. Damit galten Daten als
        frisch, obwohl OKX das Abo mit Code 60033 abgelehnt hatte und nie
        ein einziges Account- oder Order-Update kam.
        """
        age_limit = self.stale_after if max_age is None else max(1.0, float(max_age))
        with self._lock:
            return bool(
                self._zustand == SUBSCRIBED and self._last_message_at
                and (time.time() - self._last_message_at) <= age_limit
            )

    def balances(self, max_age: Optional[float] = None) -> Optional[dict[str, dict]]:
        age_limit = self.stale_after if max_age is None else max(1.0, float(max_age))
        with self._lock:
            # Ausschliesslich ein echter account-Event darf Salden frisch
            # halten. Keepalive-pongs werden hier bewusst ignoriert.
            if (self._zustand != SUBSCRIBED or not self._last_account_at
                    or time.time() - self._last_account_at > age_limit
                    or not self._balances):
                return None
            return {ccy: dict(value) for ccy, value in self._balances.items()}

    def replace_balances(self, balances: dict[str, dict]) -> None:
        """Autoritativen REST-Snapshot in den Streamcache spiegeln."""
        with self._lock:
            self._balances = {str(k).upper(): dict(v)
                              for k, v in (balances or {}).items()}
            self._last_account_at = time.time()

    def order(self, *, ord_id: str = "", cl_ord_id: str = "") -> Optional[dict]:
        with self._lock:
            row = self._orders.get(str(ord_id or "")) or self._orders.get(str(cl_ord_id or ""))
            return dict(row) if row else None

    def status(self) -> StreamStatus:
        with self._lock:
            last = (datetime.fromtimestamp(self._last_message_at, timezone.utc).isoformat()
                    if self._last_message_at else None)
            last_account = (datetime.fromtimestamp(self._last_account_at, timezone.utc).isoformat()
                            if self._last_account_at else None)
            last_order = (datetime.fromtimestamp(self._last_order_at, timezone.utc).isoformat()
                          if self._last_order_at else None)
            last_pong = (datetime.fromtimestamp(self._last_pong_at, timezone.utc).isoformat()
                         if getattr(self, "_last_pong_at", 0.0) else None)
            return StreamStatus(
                running=self._running,
                connected=self._connected,
                authenticated=self._authenticated,
                last_message=last,
                last_error=self._last_error,
                reconnects=self._reconnects,
                mode="demo" if self.demo else "live",
                endpoint=self.endpoint,
                zustand=self._zustand,
                kanaele=tuple(sorted(self._kanaele)),
                healthy=bool(self._zustand == SUBSCRIBED and self._last_message_at
                             and (time.time() - self._last_message_at) <= self.stale_after),
                konfigurationsfehler=self._konfigurationsfehler,
                last_account=last_account,
                last_order=last_order,
                last_pong=last_pong,
            )


__all__ = [
    "OKXPrivateStream", "StreamStatus", "OKX_EEA_PRIVATE_WS",
    "OKX_EEA_DEMO_PRIVATE_WS",
]
