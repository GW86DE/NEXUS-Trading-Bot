from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from datetime import datetime, timezone

from safe_persistence import best_effort_json

try:
    import config
except Exception:  # pragma: no cover - Offline-/Teilimporte
    config = None

try:
    from pi_system import metrics as _pi_metrics, health_flags as _pi_health_flags
except Exception:  # pragma: no cover
    _pi_metrics = None
    _pi_health_flags = None

try:
    from systemd_notify import ready as _sd_ready, watchdog as _sd_watchdog, stopping as _sd_stopping
except Exception:  # pragma: no cover
    _sd_ready = _sd_watchdog = _sd_stopping = lambda *a, **k: False


def _cfg(name: str, default):
    try:
        return getattr(config, name, default)
    except Exception:
        return default



def _zustandswurzel() -> Path:
    """Das Botverzeichnis -- niemals das Arbeitsverzeichnis.

    v9.1: Ein relativer Dateiname loeste bisher gegen das ARBEITSVERZEICHNIS
    auf. Startet der Bot einmal aus einem anderen Ordner, schriebe er seinen
    Zustand woanders hin -- der Not-Aus griffe ins Leere und der
    Ordereigentumsnachweis waere weg. Genau diese Regel wurde in v8.1.5 fuer
    den Risikozustand eingefuehrt (risk_manager._zustandswurzel) und hier
    nachgezogen.
    """
    test_dir = os.getenv("TRADINGBOT_TEST_STATE_DIR", "").strip()
    return Path(test_dir) if test_dir else Path(__file__).resolve().parent

class RuntimeStatus:
    """Leichtgewichtige Telemetrie mit Pi-schonender Schreibdrosselung.

    Der Trading-Core darf den Status beliebig oft aktualisieren. Auf den
    Datentraeger wird nur bei wichtigen Zustandswechseln oder in einem
    konfigurierbaren Intervall geschrieben. Kritische Trading-State-Dateien
    sind davon nicht betroffen.
    """

    IMPORTANT_KEYS = {
        "running", "broker_connected", "connection_state", "state", "cycle",
        "mode", "broker", "offline_since", "last_connection_error",
    }

    def __init__(self, path='runtime_status.json'):
        kandidat = Path(path)
        self.path = kandidat if kandidat.is_absolute() else _zustandswurzel() / kandidat.name
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.thread = None
        self.last_write_mono = 0.0
        self.last_main_touch_mono = time.monotonic()
        self.write_interval = max(2.0, float(_cfg("RUNTIME_STATUS_WRITE_SECONDS", 10.0)))
        self.heartbeat_interval = max(2.0, float(_cfg("RUNTIME_HEARTBEAT_THREAD_SECONDS", 10.0)))
        self.watchdog_main_stale = max(30.0, float(_cfg("SYSTEMD_WATCHDOG_MAIN_STALE_SECONDS", 150.0)))
        self.watchdog_startup_grace = max(60.0, float(_cfg("SYSTEMD_WATCHDOG_STARTUP_GRACE_SECONDS", 900.0)))
        self.started_mono = time.monotonic()
        self.data = {
            'running': False, 'pid': os.getpid(), 'last_heartbeat': None,
            'broker': '', 'mode': '', 'cycle': 0, 'state': '',
            'broker_connected': False, 'connection_state': 'STARTING',
            'reconnect_attempts': 0, 'reconnect_attempts_current': 0,
            'reconnects_total': 0, 'offline_since': None,
            'last_connection_error': None, 'last_broker_contact': None,
            'initialization_complete': False,
        }

    def _now(self):
        return datetime.now(timezone.utc).isoformat()

    def _system_fields(self) -> dict:
        if not bool(_cfg("PI_MODE", False)) or _pi_metrics is None:
            return {}
        try:
            m = _pi_metrics(self.path.parent)
            flags = _pi_health_flags(self.path.parent) if _pi_health_flags else []
            return {
                "pi_mode": True,
                "pi_model": m.get("device_model"),
                "pi_architecture": m.get("architecture"),
                "pi_cpu_count": m.get("cpu_count"),
                "pi_cpu_temperature_c": m.get("cpu_temperature_c"),
                "pi_cpu_usage_pct": m.get("cpu_usage_pct"),
                "pi_memory_total_mb": m.get("memory_total_mb"),
                "pi_memory_used_mb": m.get("memory_used_mb"),
                "pi_memory_available_mb": m.get("memory_available_mb"),
                "pi_disk_total_mb": m.get("disk_total_mb"),
                "pi_disk_used_mb": m.get("disk_used_mb"),
                "pi_disk_free_mb": m.get("disk_free_mb"),
                "pi_system_uptime_seconds": m.get("system_uptime_seconds"),
                "pi_load_1m": m.get("load_1m"),
                "pi_throttled_hex": m.get("pi_throttled_hex"),
                "pi_undervoltage_now": m.get("undervoltage_now"),
                "pi_throttled_now": m.get("throttled_now"),
                "pi_health_warnings": list(flags or []),
            }
        except Exception:
            return {"pi_mode": True}

    def _write_locked(self) -> bool:
        self.data.update(self._system_fields())
        self.data['pid'] = os.getpid()
        self.data['last_heartbeat'] = self._now()
        ok = best_effort_json(
            self.path,
            self.data,
            label='Runtime-Status',
            warning_interval_seconds=300,
            # Reiner Heartbeat: atomare Ersetzung ja, aber kein fsync je 30 s.
            # Risiko-/Positions-/Order-State bleibt weiterhin durable=True.
            durable=False,
        )
        self.last_write_mono = time.monotonic()
        return ok

    def update(self, _heartbeat_only: bool = False, _force_write: bool = False, **kwargs):
        with self.lock:
            if not _heartbeat_only:
                self.last_main_touch_mono = time.monotonic()

            important_change = False
            for key, value in kwargs.items():
                if key in self.IMPORTANT_KEYS and self.data.get(key) != value:
                    important_change = True
                self.data[key] = value

            self.data['pid'] = os.getpid()
            now_mono = time.monotonic()
            due = (now_mono - self.last_write_mono) >= self.write_interval
            if _force_write or important_change or due or self.last_write_mono <= 0:
                return self._write_locked()
            return True

    def start(self, **kwargs):
        self.update(running=True, _force_write=True, **kwargs)
        if os.getenv("TRADINGBOT_SUPERVISED", "0") != "1":
            _sd_ready("TradingBot Runtime aktiv")
        if self.thread and self.thread.is_alive():
            return
        self.thread = threading.Thread(target=self._loop, daemon=True, name='RuntimeHeartbeat')
        self.thread.start()

    def _loop(self):
        while not self.stop_event.wait(self.heartbeat_interval):
            self.update(running=True, _heartbeat_only=True)
            now_mono = time.monotonic()
            main_age = now_mono - self.last_main_touch_mono
            startup_age = now_mono - self.started_mono
            initializing = not bool(self.data.get("initialization_complete", False))
            # Die initiale Broker-/Universumsqualifizierung kann legitimerweise
            # mehrere Minuten blockierend laufen. Bis max. startup_grace wird
            # der Watchdog deshalb weiter gefuettert. Danach gilt im normalen
            # Betrieb die deutlich engere Main-Thread-Grenze. Ein echter Hänger
            # wird so erkannt, ohne eine langsame Broker-Initialisierung abzubrechen.
            healthy = (
                (initializing and startup_age <= self.watchdog_startup_grace)
                or main_age <= self.watchdog_main_stale
            )
            if healthy and os.getenv("TRADINGBOT_SUPERVISED", "0") != "1":
                phase = "Initialisierung" if initializing else "Betrieb"
                _sd_watchdog(f"TradingBot {phase}; Main-Thread {main_age:.0f}s alt")

    def stop(self, reason='beendet'):
        self.stop_event.set()
        # Im NEXUS-Supervisor darf ein einzelner Worker systemd niemals
        # STOPPING=1 melden. Das versetzte den gesamten Dienst in stop-sigterm
        # und fuehrte trotz geplanter Worker-Neustarts zum SIGKILL.
        if os.getenv("TRADINGBOT_SUPERVISED", "0") != "1":
            _sd_stopping(str(reason))
        self.update(running=False, reason=reason, _force_write=True)


def read_runtime(path='runtime_status.json', max_age_seconds=None):
    p = Path(path)
    if not p.exists():
        return {'online': False, 'running': False}
    try:
        d = json.loads(p.read_text(encoding='utf-8'))
        ts = datetime.fromisoformat(d.get('last_heartbeat'))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if max_age_seconds is None:
            try:
                max_age_seconds = float(_cfg("RUNTIME_STATUS_MAX_AGE_SECONDS", 45.0))
            except Exception:
                max_age_seconds = float(os.getenv("TRADINGBOT_RUNTIME_MAX_AGE_SECONDS", "45"))
        age = (datetime.now(timezone.utc) - ts).total_seconds()
        d['heartbeat_age_seconds'] = age
        d['online'] = bool(d.get('running') and age <= float(max_age_seconds))
        return d
    except Exception:
        return {'online': False, 'running': False}
