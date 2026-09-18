"""Bounded, process-local observations; never a trading permission or ledger.

Request generations order local observations only. They are NOT broker sequence
numbers and do not establish that REST includes every earlier WebSocket fill.
No payload, URL parameters, account identifiers or exception text are retained.
"""
from __future__ import annotations

from contextvars import ContextVar
from datetime import datetime, timezone
from functools import wraps
import threading
import time

_active_request = ContextVar("nexus_broker_observation", default=None)


def _utc():
    return datetime.now(timezone.utc).isoformat()


class BrokerObservation:
    def __init__(self, broker: str, environment: str):
        self.broker = broker
        self.environment = environment
        self._lock = threading.RLock()
        self._generation = 0
        self._channels = {}

    def begin(self, channel: str, method: str):
        with self._lock:
            self._generation += 1
            generation = self._generation
            row = self._channels.setdefault(channel, {
                "state": "UNKNOWN", "last_attempt_at": None,
                "last_started_at": None, "last_success_at": None,
                "last_response_at": None, "http_status": None,
                "transport_state": "UNKNOWN",
                "last_error_at": None, "error_code": "",
                "latency_ms": None, "request_generation": 0,
                "completed_generation": 0, "in_flight": 0,
                "wire_attempts": 0, "last_method": "",
            })
            row.update(last_attempt_at=_utc(), request_generation=generation,
                       last_method=method.upper(), in_flight=row["in_flight"] + 1,
                       _attempt_monotonic=time.monotonic())
            return {"owner": self, "channel": channel, "generation": generation,
                    "started": time.monotonic(), "wire_attempts": 0}

    def response_received(self, token, status):
        with self._lock:
            token["http_status"] = int(status)
            token["response_at"] = _utc()
            self._channels[token["channel"]]["last_response_at"] = token["response_at"]

    def wire_started(self, token):
        with self._lock:
            token["wire_attempts"] += 1
            token.pop("http_status", None)
            token.pop("response_at", None)
            row = self._channels[token["channel"]]
            row["last_started_at"] = _utc()
            row["wire_attempts"] += 1

    def finish(self, token, error=None):
        with self._lock:
            row = self._channels[token["channel"]]
            row["in_flight"] = max(0, row["in_flight"] - 1)
            now = _utc()
            # Late successes remain observable as actual responses, but cannot
            # erase the failure of a later-started request.
            if error is None and token["wire_attempts"]:
                row["last_success_at"] = now
                row["_success_monotonic"] = time.monotonic()
            if error is not None:
                row["last_error_at"] = now
            if token["generation"] < row["completed_generation"]:
                return
            row.update(completed_generation=token["generation"],
                       latency_ms=round((time.monotonic() - token["started"]) * 1000, 2),
                       error_code=type(error).__name__ if error else "",
                       http_status=token.get("http_status"),
                       transport_state=("OK" if token.get("response_at") else
                                        "ERROR" if token["wire_attempts"] else "NOT_SENT"),
                       state=("ERROR" if error else "OK" if token["wire_attempts"] else "UNKNOWN"))
            if token.get("response_at"):
                row["last_response_at"] = token["response_at"]

    def snapshot(self):
        with self._lock:
            now = time.monotonic()
            channels = {}
            for name, row in self._channels.items():
                channels[name] = {k: v for k, v in row.items() if not k.startswith("_")}
                channels[name]["success_age_seconds"] = (
                    round(max(0, now - row["_success_monotonic"]), 2)
                    if "_success_monotonic" in row else None)
                channels[name]["attempt_age_seconds"] = round(max(0, now - row["_attempt_monotonic"]), 2)
            return {"schema_version": 1, "broker": self.broker,
                    "environment": self.environment, "observed_at": _utc(),
                    "rest": channels,
                    "request_sequence_is_broker_sequence": False}


def transport_started():
    """Called immediately before the actual HTTP client invocation."""
    token = _active_request.get()
    if token is not None:
        token["owner"].wire_started(token)


def transport_received(status):
    token = _active_request.get()
    if token is not None:
        token["owner"].response_received(token, status)


def observe_rest(*, private_default=False):
    def decorate(fn):
        @wraps(fn)
        def wrapped(self, method, *args, **kwargs):
            observer = getattr(self, "_observations", None)
            if observer is None:  # Compatibility with small legacy test adapters.
                return fn(self, method, *args, **kwargs)
            channel = "private" if kwargs.get("private", private_default) else "public"
            token = observer.begin(channel, method)
            context = _active_request.set(token)
            try:
                result = fn(self, method, *args, **kwargs)
            except BaseException as exc:
                observer.finish(token, exc)
                raise
            else:
                observer.finish(token)
                return result
            finally:
                _active_request.reset(context)
        return wrapped
    return decorate


class ProtectionObservation:
    """Elapsed time for the real position-check call, not process heartbeat."""
    def __init__(self):
        self._lock = threading.RLock()
        self._completed_monotonic = None
        self._started_monotonic = None
        self._row = {"state": "UNKNOWN", "running": False,
                     "last_started_at": None, "last_completed_at": None,
                     "last_success_at": None, "duration_ms": None,
                     "checked_positions": None, "errors": [], "cycles": 0}

    def begin(self):
        with self._lock:
            self._started_monotonic = time.monotonic()
            self._row.update(running=True, last_started_at=_utc())

    def finish(self, report=None, error=None):
        with self._lock:
            now = time.monotonic()
            report = report if isinstance(report, dict) else {}
            errors = list(report.get("diagnostic_errors") or [])[:32]
            if error is not None:
                errors.append(type(error).__name__)
            if report.get("ok") is False:
                errors.append("POSITION_CHECK_INCOMPLETE")
            completed = error is None and report.get("ok") is True
            state = "OK" if completed and not errors else "WARN" if completed else "ERROR"
            self._row.update(state=state, running=False, last_completed_at=_utc(),
                             duration_ms=round(max(0, now - self._started_monotonic) * 1000, 2),
                             checked_positions=report.get("geprueft"), errors=errors,
                             cycles=self._row["cycles"] + 1)
            self._completed_monotonic = now
            if state == "OK":
                self._row["last_success_at"] = self._row["last_completed_at"]

    def snapshot(self):
        with self._lock:
            now = time.monotonic()
            return {**self._row, "errors": list(self._row["errors"]),
                    "age_seconds": (round(max(0, now - self._completed_monotonic), 2)
                                    if self._completed_monotonic is not None else None),
                    "running_seconds": (round(max(0, now - self._started_monotonic), 2)
                                        if self._row["running"] else None),
                    "meaning": "Durchlauf der Positionspruefung; kein Beweis fuer jede native Schutzorder"}
