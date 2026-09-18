"""Verified broker heartbeat state for v5.6.0.

The dashboard must not infer 'ONLINE' from a Python object alone. A healthy
state means the selected broker recently answered a real broker/API health
request (eToro authenticated REST).
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from broker import AuthentifizierungsFehler, VerbindungVerloren, NichtVerbunden
from broker.connectivity import is_connectivity_error
from decision_analytics import heartbeat_record

logger=logging.getLogger(__name__)

@dataclass
class HealthSnapshot:
    state: str
    ok: bool
    checked_at: str
    latency_ms: float | None = None
    detail: str = ""
    last_contact: str | None = None
    components: dict = field(default_factory=dict)

class BrokerHealthTracker:
    def __init__(self, broker, *, record_min_interval: float = 60.0):
        self.broker=broker
        self.last_state="STARTING"
        self.last_record_at=0.0
        self.record_min_interval=float(record_min_interval)
        self.last_snapshot=HealthSnapshot("STARTING",False,datetime.now(timezone.utc).isoformat())

    def _emit(self, snap: HealthSnapshot):
        # A heartbeat outcome cannot stand in for REST/WS/position agreement.
        # All component getters are local observations and perform no requests.
        components = getattr(self.broker, "connection_components", None)
        if callable(components):
            try:
                snap.components = components()
            except Exception as exc:
                snap.components = {"state": "UNKNOWN", "error_code": type(exc).__name__}
        now=time.monotonic()
        if snap.state != self.last_state or now-self.last_record_at >= self.record_min_interval:
            heartbeat_record(getattr(self.broker,"name","?"),snap.state,snap.detail,snap.latency_ms,snap.last_contact)
            self.last_state=snap.state;self.last_record_at=now
        self.last_snapshot=snap
        return snap

    def probe(self, force: bool = True) -> HealthSnapshot:
        start=time.perf_counter()
        try:
            ok=bool(self.broker.health_check(force=force))
            latency=(time.perf_counter()-start)*1000.0
            if not ok:
                return self._emit(HealthSnapshot("DEGRADED",False,datetime.now(timezone.utc).isoformat(),latency,"Healthcheck lieferte False",self._last_contact()))
            return self._emit(HealthSnapshot("ONLINE",True,datetime.now(timezone.utc).isoformat(),latency,"Authentifizierter Broker-Healthcheck erfolgreich",self._last_contact()))
        except AuthentifizierungsFehler as exc:
            return self._emit(HealthSnapshot("AUTH_ERROR",False,datetime.now(timezone.utc).isoformat(),(time.perf_counter()-start)*1000.0,str(exc),self._last_contact()))
        except Exception as exc:
            state="OFFLINE" if isinstance(exc,(VerbindungVerloren,NichtVerbunden)) or is_connectivity_error(exc) else "DEGRADED"
            return self._emit(HealthSnapshot(state,False,datetime.now(timezone.utc).isoformat(),(time.perf_counter()-start)*1000.0,str(exc),self._last_contact()))

    def note_probe(self, ok: bool, latency_ms: float | None = None, detail: str = "") -> HealthSnapshot:
        state="ONLINE" if ok else "DEGRADED"
        snap=HealthSnapshot(state,bool(ok),datetime.now(timezone.utc).isoformat(),latency_ms,str(detail),self._last_contact())
        return self._emit(snap)

    def note_state(self,state: str, detail: str = "") -> HealthSnapshot:
        snap=HealthSnapshot(str(state).upper(),str(state).upper()=="ONLINE",datetime.now(timezone.utc).isoformat(),None,str(detail),self._last_contact())
        return self._emit(snap)

    def _last_contact(self):
        try:return self.broker.last_contact() if hasattr(self.broker,"last_contact") else None
        except Exception:return None
