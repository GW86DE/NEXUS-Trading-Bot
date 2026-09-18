"""eToro Public-API Adapter (Demo/PAPER und Real/LIVE).

Sicherheitsprinzipien:
- getrennte API-Key-Paare fuer Demo und Real;
- keine automatische Wiederholung von Order-POSTs nach unklarem Transportfehler;
- X-Request-Id wird fuer Idempotenz/Lookup gespeichert;
- standardmaessig nur REAL-Settlement mit Hebel 1; CFD muss explizit erlaubt sein;
- vor neuen Trades offizielle What-if-Kosten und Eligibility verwenden;
- Stop-Loss/Take-Profit werden beim Oeffnen an eToro uebergeben.
"""
from __future__ import annotations

import logging
import hashlib
import math
import threading
import time
import uuid
from collections import deque
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any

import pandas as pd
import requests
from broker_observation import BrokerObservation, observe_rest, transport_started, transport_received

import config
from safe_persistence import best_effort_json
from instrument_identity import canonical_key, etoro_market_symbol_matches, market_asset_type, normalize_user_symbol
from .base import (
    BrokerBase, BrokerFehler, AuftragAbgelehnt, VerbindungVerloren, OrderStatusUnklar, AuthentifizierungsFehler,
    NichtVerbunden, NichtUnterstuetzt, Position, Fill, OrderErgebnis,
)
from .history_safety import completed_bars_only, retry_call

logger = logging.getLogger(__name__)


def _api_value(row: dict, *names, default=None):
    """Ersten vorhandenen eToro-Wert lesen, ohne Gross-/Kleinschreibung zu raten.

    Die offiziellen Demo-Beispiele verwenden ``positionId``/``instrumentId``,
    reale Portfolioantworten teilweise ``positionID``/``instrumentID``. Die
    Normalisierung geschieht einmal am Adapterrand; der Kern sieht danach nur
    noch die kanonischen camelCase-Felder.
    """
    if not isinstance(row, dict):
        return default
    for name in names:
        if name in row and row[name] not in (None, ""):
            return row[name]
    return default


def _known_fees(row, *fields):
    """A missing charge is unknown; only explicit finite broker fields count."""
    values = [row.get(name) for name in fields]
    if any(value in (None, '') for value in values):
        return None
    from execution_lifecycle import number
    return float(sum((number(value) for value in values)))


def _normalisiere_positionszeile(row: dict) -> dict:
    out = dict(row or {})
    mappings = {
        "positionId": ("positionId", "positionID", "PositionId", "PositionID"),
        "instrumentId": ("instrumentId", "instrumentID", "InstrumentId", "InstrumentID"),
        "orderId": ("orderId", "orderID", "OrderId", "OrderID"),
        "cid": ("cid", "CID", "Cid"),
        "mirrorId": ("mirrorId", "mirrorID", "MirrorId", "MirrorID"),
        "parentPositionId": (
            "parentPositionId", "parentPositionID", "ParentPositionId", "ParentPositionID"),
    }
    for target, names in mappings.items():
        value = _api_value(out, *names)
        if value not in (None, ""):
            out[target] = value
    return out


def _close_execution_id(row: dict) -> str:
    """Staerkste brokerseitige Identitaet einer Close-Ausfuehrung.

    Derselbe eToro-Fill kann zuerst im Close-Info-Endpunkt und spaeter in der
    Trade-History auftauchen. Optionale Orderfelder unterscheiden sich zwischen
    diesen Quellen; eine vorhandene Execution-/Deal-ID darf deshalb niemals mit
    der jeweils sichtbaren Order-ID kombiniert werden.
    """
    return str(_api_value(
        row,
        "executionId", "executionID", "ExecutionId", "ExecutionID",
        "positionExecutionId", "positionExecutionID",
        "PositionExecutionId", "PositionExecutionID",
        "dealId", "dealID", "DealId", "DealID",
        default="") or "").strip()


def _identity_number(value) -> str:
    """Numerische Brokerwerte quellenuebergreifend stabil darstellen."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value or "").strip()
    if not math.isfinite(number):
        return str(value or "").strip()
    return format(number, ".15g")


def _identity_timestamp(value) -> str:
    """Zeitanker aus Close-Info und History in dieselbe UTC-Form bringen."""
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        numeric = float(raw)
        if math.isfinite(numeric):
            # Einige Brokerantworten liefern Unix-Millisekunden, andere ISO-8601.
            if abs(numeric) >= 10_000_000_000:
                numeric /= 1000.0
            return datetime.fromtimestamp(
                numeric, tz=timezone.utc).isoformat(timespec="microseconds")
    except (TypeError, ValueError, OverflowError, OSError):
        pass
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat(timespec="microseconds")
    except ValueError:
        return raw


def _close_fill_identity(account_fingerprint: str, position_id: str,
                         order_id: str, row: dict, occurred: str) -> str:
    """Kontoweit stabile Identitaet einer eToro-Schliessausfuehrung."""
    raw = _close_execution_id(row)
    if raw:
        # Die echte Execution-ID ist staerker als jede optionale Close-Order-ID.
        # Dadurch bleibt die Identitaet in Close-Info und History identisch.
        anchor = f"exec:{raw}"
    else:
        evidence = "|".join((
            _identity_timestamp(occurred),
            _identity_number(_api_value(
                row, "units", "Units", "closedUnits", "ClosedUnits",
                default="")),
            _identity_number(_api_value(
                row, "rate", "closeRate", "Rate", "CloseRate",
                default="")),
        ))
        anchor = "evidence:" + hashlib.sha256(
            evidence.encode("utf-8")).hexdigest()[:24]
    return ":".join((
        "etoro", str(account_fingerprint or "unresolved"), "close",
        str(position_id), anchor))


class _RollingLimiter:
    """Gleitender eToro-Limiter mit optional gleichmäßiger Taktung.

    Ein reines Rolling Window erlaubt zunächst einen großen Burst und zwingt
    den Scanner danach fast eine Minute zum Warten. Die Mindestdistanz verteilt
    die Aufrufe stattdessen über das Fenster und lässt Reserve für den
    Reconciliation-Worker.
    """

    def __init__(self, maximum: int, seconds: float = 60.0,
                 min_interval: float = 0.0):
        self.maximum = max(1, int(maximum))
        self.seconds = max(1.0, float(seconds))
        self.min_interval = max(0.0, float(min_interval))
        self._events = deque()
        self._lock = threading.Lock()
        self._last_acquired = 0.0

    def acquire(self, timeout: float = 30.0) -> bool:
        deadline = time.monotonic() + max(0.0, float(timeout))
        while True:
            with self._lock:
                now = time.monotonic()
                while self._events and now - self._events[0] >= self.seconds:
                    self._events.popleft()
                window_wait = (
                    self.seconds - (now - self._events[0])
                    if len(self._events) >= self.maximum else 0.0
                )
                spacing_wait = max(
                    0.0, self.min_interval - (now - self._last_acquired)
                ) if self._last_acquired else 0.0
                wait = max(window_wait, spacing_wait)
                if wait <= 0.0:
                    self._events.append(now)
                    self._last_acquired = now
                    return True
            if time.monotonic() + wait > deadline:
                return False
            time.sleep(min(0.25, max(0.01, wait)))


class EtoroBroker(BrokerBase):
    name = "etoro"

    def __init__(self, *, paper: bool | None = None, api_key: str | None = None,
                 user_key: str | None = None, session: requests.Session | None = None):
        self.paper = bool(config.PAPER_TRADING if paper is None else paper)
        if self.paper:
            self.api_key = (api_key or getattr(config, "ETORO_DEMO_API_KEY", "")).strip()
            self.user_key = (user_key or getattr(config, "ETORO_DEMO_USER_KEY", "")).strip()
        else:
            self.api_key = (api_key or getattr(config, "ETORO_LIVE_API_KEY", "")).strip()
            self.user_key = (user_key or getattr(config, "ETORO_LIVE_USER_KEY", "")).strip()
        # Die Kontoidentitaet darf nicht am austauschbaren User-Key haengen.
        # Sie wird nach /api/v1/me aus der stabilen Demo-/Real-CID gebildet.
        # Vor diesem Brokerbeweis existiert nur eine umgebungsweite,
        # fail-closed Platzhalterdomain. Sie enthaelt weder API- noch User-Key
        # und wird bei /api/v1/me zwingend durch den CID-Hash ersetzt.
        unresolved_source = (
            f"etoro|{'demo' if self.paper else 'live'}|identity-unresolved")
        self._account_fingerprint = hashlib.sha256(
            unresolved_source.encode("utf-8")).hexdigest()[:24]
        self._account_cid = ""
        self._identity_loaded = False
        self._identity_lock = threading.RLock()
        self.base_url = str(getattr(config, "ETORO_API_BASE_URL", "https://public-api.etoro.com")).rstrip("/")
        self.session = session or requests.Session()
        self._observations = BrokerObservation("etoro", "DEMO" if self.paper else "LIVE")
        self._connected = False
        self._last_contact: str | None = None
        self._last_health = 0.0
        self._instrument_cache: dict[str, dict] = {}
        self._instrument_candidates: dict[str, list[dict]] = {}
        self._instrument_by_id: dict[int, dict] = {}
        self._eligibility: dict[str, dict] = {}
        self._cost_cache: dict[tuple, tuple[float, dict]] = {}
        self._fill_queue: list[Fill] = []
        self._seen_history: set[str] = set()
        self._last_history_poll = 0.0
        self._history_recovery_complete = False
        # ``fills()`` kann nur Brokerbelege liefern. Ob dieselben Belege auch
        # dauerhaft in FillTracker und Ledger verbucht wurden, weiss allein der
        # zentrale Consumer. Darum darf der grosse Recovery-Cursor hier nur zur
        # Quittierung vorgemerkt, niemals beim Queueing selbst geschlossen
        # werden.
        self._history_recovery_pending_ack = False
        self._history_recovery_expected_fill_ids: set[str] = set()
        self._last_close_order_poll = 0.0
        self._portfolio_cache: tuple[float, dict] | None = None
        self._current_position_ids: set[str] = set()
        self._current_position_map: dict[str, set[str]] = {}
        self._current_position_ids_known = False
        self._position_snapshot_quality = {"state": "UNKNOWN", "complete": None,
                                           "last_attempt_at": None, "last_success_at": None}
        self._position_schema_logged = False
        self._history_cache: dict[tuple[str, int], tuple[float, dict]] = {}
        self._all_instruments_loaded = False
        self._http_lock = threading.RLock()
        self._portfolio_state_lock = threading.RLock()
        self._history_state_lock = threading.RLock()
        # Unter den offiziellen 60/Minute fuer Standardabfragen bzw.
        # 20/Minute fuer Ausfuehrungsaufrufe bleiben. Eligibility besitzt
        # ebenfalls ein eigenes 20/Minute-Fenster.
        from etoro_transport_budget import SharedLimiter
        # Key rotation intentionally creates a new credential scope. No secret
        # or reversible key material is written to the shared budget database.
        credential_scope = hashlib.sha256("\x00".join((
            self.base_url, str(self.paper), self.api_key, self.user_key
        )).encode("utf-8")).hexdigest()
        self._limit_read = SharedLimiter(credential_scope, "read", 54, min_interval=1.15)
        self._limit_execution = SharedLimiter(credential_scope, "execution", 18, min_interval=3.4)
        self._limit_eligibility = SharedLimiter(credential_scope, "eligibility", 18, min_interval=3.4)
        self._limit_costs = SharedLimiter(credential_scope, "costs", 18, min_interval=3.4)
        self._service_lock = threading.RLock()
        self._service_starting = False
        self._reconcile_stop = threading.Event()
        self._reconcile_thread = None
        self._reconcile_generation = 0
        self._reconcile_health = {
            "running": False,
            "generation": 0,
            "started_at_utc": "",
            "last_tick_started_at_utc": "",
            "last_tick_completed_at_utc": "",
            "last_success_at_utc": "",
            "last_error_at_utc": "",
            "last_error": "",
            "active_records": None,
            "changed_records": None,
        }
        self._private_stream = None
        # Kurzer Cache fuer Rates: Marktstatus und Marktqualitaet duerfen nicht
        # denselben eToro-Endpunkt direkt hintereinander doppelt belasten.
        self._rate_cache: dict[int, tuple[float, dict]] = {}

    def account_fingerprint(self) -> str:
        """Nicht umkehrbare Kontokennung; Zugangsdaten werden nie persistiert."""
        with self._identity_lock:
            return str(self._account_fingerprint or "")

    def _require_bound_identity(self, operation: str) -> str:
        """Money-Path nur nach brokerseitigem CID-Beweis betreten.

        Der vorlaeufige Fingerprint trennt zwar DEMO und LIVE, ist aber fuer alle
        Konten derselben Umgebung gleich. Er darf daher niemals als Kontoanker
        fuer Positionen, Fills oder Close-Intents verwendet werden.
        """
        with self._identity_lock:
            loaded = bool(self._identity_loaded and self._account_cid)
            fingerprint = str(self._account_fingerprint or "")
        if not loaded:
            raise AuthentifizierungsFehler(
                f"eToro {operation} blockiert: Kontoidentitaet wurde noch nicht "
                "ueber /api/v1/me gebunden; zuerst connect() ausfuehren")
        return fingerprint

    def _bind_account_identity(self, profile: dict) -> str:
        """Bindet den Adapter an die brokerseitige CID statt an Zugangsdaten."""
        cid_key = "demoCid" if self.paper else "realCid"
        cid = _api_value(
            profile or {}, cid_key,
            "demoCID" if self.paper else "realCID",
            "DemoCid" if self.paper else "RealCid",
            "DemoCID" if self.paper else "RealCID")
        if cid in (None, "", 0, "0"):
            env = "DEMO" if self.paper else "LIVE"
            raise AuthentifizierungsFehler(
                f"eToro {env}: /api/v1/me liefert keine {cid_key}")
        cid_text = str(cid).strip()
        source = f"etoro|{'demo' if self.paper else 'live'}|cid:{cid_text}"
        fingerprint = hashlib.sha256(source.encode("utf-8")).hexdigest()[:24]
        with self._identity_lock:
            previous = str(self._account_fingerprint or "")
            if self._identity_loaded and previous and previous != fingerprint:
                raise AuthentifizierungsFehler(
                    "eToro-Kontoidentitaet hat sich innerhalb derselben "
                    "Adapterinstanz geaendert")
            self._account_cid = cid_text
            self._account_fingerprint = fingerprint
            self._identity_loaded = True
        return fingerprint

    # --------------------------- HTTP ---------------------------------
    def _headers(self, request_id: str | None = None, json_body=False) -> dict:
        h = {
            "x-api-key": self.api_key,
            "x-user-key": self.user_key,
            "x-request-id": request_id or str(uuid.uuid4()),
            "Accept": "application/json",
            "User-Agent": "TradingBot/6.0",
        }
        if json_body:
            h["Content-Type"] = "application/json"
        return h

    def _mark_contact(self):
        self._last_contact = datetime.now(timezone.utc).isoformat()
        self._connected = True

    @observe_rest(private_default=True)
    def _request(self, method: str, path: str, *, params=None, payload=None,
                 request_id: str | None = None, safe_retry: bool = True,
                 bound_cid_header: bool = False) -> Any:
        if not self.api_key or not self.user_key:
            env = "DEMO" if self.paper else "LIVE"
            raise AuthentifizierungsFehler(f"eToro {env}: Public API Key/User Key fehlen")
        url = self.base_url + path
        if "/trading/execution/" in path:
            limiter = self._limit_execution
        elif path.endswith("/eligibility"):
            limiter = self._limit_eligibility
        elif path.endswith("/costs"):
            limiter = self._limit_costs
        else:
            limiter = self._limit_read
        # A caller cannot accidentally enable blind retries for an execution
        # POST by relying on the historical safe_retry=True default.
        attempts = 3 if safe_retry and method.upper() in {"GET", "HEAD"} else 1
        last_exc = None
        for attempt in range(attempts):
            # Retries are real requests too. This reservation is shared with
            # other adapters/processes using the same credential pair.
            if not limiter.acquire(timeout=float(getattr(config, "ETORO_RATE_LIMIT_WAIT_SECONDS", 30.0))):
                raise BrokerFehler(f"eToro Ratenfenster fuer {path} nicht rechtzeitig frei")
            headers = self._headers(request_id, payload is not None)
            if bound_cid_header:
                self._require_bound_identity("CID-gebundener Schutz-Readback")
                headers["CID"] = self._account_cid
            try:
                with self._http_lock:
                    transport_started()
                    resp = self.session.request(
                        method.upper(), url, params=params, json=payload,
                        headers=headers,
                        timeout=(5, float(getattr(config, "ETORO_REQUEST_TIMEOUT_SECONDS", 25))),
                    )
                    transport_received(resp.status_code)
            except requests.RequestException as exc:
                last_exc = exc
                if attempt + 1 < attempts:
                    time.sleep(min(2.0, 0.25 * (2 ** attempt)))
                    continue
                self._connected = False
                raise VerbindungVerloren(f"eToro Netzwerkfehler: {exc}") from exc

            if resp.status_code in (401, 403):
                self._connected = False
                detail=resp.text[:350].replace("\n"," ")
                raise AuthentifizierungsFehler(
                    f"eToro Authentifizierung/Berechtigung fehlgeschlagen (HTTP {resp.status_code}). "
                    f"Pruefe x-api-key, x-user-key, Demo/Real-Umgebung und Read/Write-Scope. {detail}"
                )
            if resp.status_code == 429:
                retry_after = 60.0
                try:
                    raw_retry = str(resp.headers.get("Retry-After", "60") or "60")
                    try:
                        retry_after = float(raw_retry)
                    except ValueError:
                        retry_date = parsedate_to_datetime(raw_retry)
                        if retry_date.tzinfo is None:
                            retry_date = retry_date.replace(tzinfo=timezone.utc)
                        retry_after = retry_date.timestamp() - time.time()
                    if not math.isfinite(retry_after):
                        retry_after = 60.0
                    retry_after = max(0.5, retry_after)
                except (ValueError, TypeError, OverflowError):
                    retry_after = 60.0
                # Store the full deadline even when our synchronous wait
                # budget is shorter. A subsequent worker must respect it.
                limiter.defer(retry_after)
                if attempt + 1 < attempts:
                    continue
                raise AuftragAbgelehnt(f"eToro Rate-Limit (HTTP 429; retry_after={retry_after:g}s)")
            if resp.status_code >= 500:
                if attempt + 1 < attempts:
                    time.sleep(min(2.0, 0.5 * (2 ** attempt)))
                    continue
                self._connected = False
                raise VerbindungVerloren(f"eToro Serverfehler HTTP {resp.status_code}: {resp.text[:200]}")
            if not resp.ok:
                detail = resp.text[:500]
                try:
                    j = resp.json()
                    detail = j.get("detail") or j.get("title") or detail
                except Exception:
                    __import__("logging").getLogger(__name__).debug("Best-effort-Ausnahme unterdrueckt", exc_info=True)
                error_type = AuftragAbgelehnt if resp.status_code in (400, 422) else BrokerFehler
                raise error_type(f"eToro HTTP {resp.status_code}: {detail}")
            self._mark_contact()
            if not resp.content:
                return {}
            try:
                return resp.json()
            except ValueError as exc:
                raise BrokerFehler(f"eToro lieferte ungueltiges JSON fuer {path}") from exc
        if last_exc:
            raise VerbindungVerloren(str(last_exc))
        raise BrokerFehler("eToro Anfrage fehlgeschlagen")

    def _path(self, demo: str, real: str) -> str:
        return demo if self.paper else real

    # --------------------------- Verbindung ----------------------------
    def _get_pnl_health(self) -> Any:
        """Environment-specific authenticated liveness probe.

        This endpoint is intentionally used for the heartbeat because a
        successful answer proves not just DNS/HTTPS, but also the selected
        DEMO/REAL account access. No order is created.
        """
        path = self._path(
            "/api/v1/trading/info/demo/pnl",
            "/api/v1/trading/info/real/pnl",
        )
        return self._request("GET", path)

    def diagnose_credentials(self) -> dict:
        """Multi-stage, read-only eToro connection diagnosis.

        Stages:
        1) authenticated identity (/api/v1/me),
        2) selected DEMO/REAL CID presence and scopes (diagnostic),
        3) selected environment P&L endpoint (definitive liveness),
        4) aggregate portfolio (account values used by the bot).

        No order is ever sent by this method.
        """
        env = "DEMO" if self.paper else "LIVE"
        steps=[]
        profile=self._request("GET","/api/v1/me")
        steps.append({"step":"identity","ok":True,"detail":"/api/v1/me beantwortet"})
        scopes=[str(x) for x in (profile.get("scopes") or profile.get("scopeNames") or [])]
        cid_key="demoCid" if self.paper else "realCid"
        cid = _api_value(
            profile, cid_key,
            "demoCID" if self.paper else "realCID",
            "DemoCid" if self.paper else "RealCid",
            "DemoCID" if self.paper else "RealCID")
        if cid in (None, "", 0, "0"):
            raise AuthentifizierungsFehler(
                f"eToro {env}: /api/v1/me liefert keine {cid_key}. "
                "Der User-Key passt vermutlich nicht zur gewaehlten Demo/Live-Umgebung."
            )
        self._bind_account_identity(profile)
        steps.append({"step":"environment","ok":True,"detail":f"{cid_key} vorhanden"})

        env_token = "demo" if self.paper else "real"
        scope_read_ok = (not scopes) or any(
            (env_token in sc.lower()) and ("read" in sc.lower()) for sc in scopes
        )
        steps.append({
            "step":"scope", "ok":bool(scope_read_ok),
            "detail":("Read-Scope plausibel" if scope_read_ok else
                      f"Kein eindeutiger {env}-Read-Scope in Scope-Liste; Endpunkt-Test entscheidet."),
        })

        pnl=self._get_pnl_health()
        steps.append({"step":"pnl","ok":True,"detail":"Environment-P&L Endpunkt beantwortet"})
        agg=self._get_aggregate(force=True)
        steps.append({"step":"portfolio","ok":True,"detail":"Aggregate-Portfolio beantwortet"})
        totals=agg.get("accountTotals") or {} if isinstance(agg,dict) else {}
        return {
            "username":profile.get("username"), "demoCid":profile.get("demoCid"),
            "realCid":profile.get("realCid"), "selectedCid":cid,
            "scopes":scopes, "scope_read_ok":scope_read_ok,
            "scope_warning":("" if scope_read_ok else
                f"Scope-Liste enthaelt keinen eindeutigen {env}-Read-Scope; "
                "P&L und Portfolio waren dennoch erfolgreich."),
            "currency":(agg.get("accountCurrency") if isinstance(agg,dict) else None) or "USD",
            "equity":_float(totals.get("accountTotalValue")),
            "cash":_float(totals.get("accountAvailableCash")),
            "environment":env, "steps":steps, "pnl_available":pnl is not None,
            "last_contact":self._last_contact,
        }

    def connect(self) -> bool:
        if not self.api_key or not self.user_key:
            env = "DEMO" if self.paper else "LIVE"
            raise AuthentifizierungsFehler(
                f"eToro {env}-Keys fehlen. eToro-Setup ausfuehren; Demo und Live benoetigen getrennte Keys."
            )
        # Erst Identitaet/Scope, dann environment-spezifisches Portfolio pruefen.
        self.diagnose_credentials()
        self._connected = True
        self.ensure_runtime_services()
        logger.info("eToro verbunden: %s", "DEMO/PAPER" if self.paper else "LIVE")
        return True

    def _start_reconciliation_worker(self) -> None:
        """Startet genau eine aktive Worker-Generation idempotent.

        Ein gestoppter, aber noch kurz lebender Thread darf einen Reconnect
        nicht mehr verhindern. Jede Generation besitzt deshalb ihr eigenes
        Stop-Event; die alte Generation wird vor dem Start der neuen beendet.
        """
        with self._service_lock:
            current = self._reconcile_thread
            current_stop = self._reconcile_stop
            if (current is not None and current.is_alive()
                    and not current_stop.is_set()):
                return
            current_stop.set()
            self._reconcile_generation += 1
            generation = int(self._reconcile_generation)
            stop_event = threading.Event()
            self._reconcile_stop = stop_event
            started = datetime.now(timezone.utc).isoformat()
            self._reconcile_health.update({
                "running": True,
                "generation": generation,
                "started_at_utc": started,
                "last_error": "",
            })

            def _run(_stop=stop_event, _generation=generation):
                interval = max(0.1, float(getattr(
                    config, "ETORO_RECONCILIATION_INTERVAL_SECONDS", 5.0)))
                try:
                    # Der erste Tick laeuft sofort. Gerade nach einem
                    # angenommenen POST zaehlt jede Sekunde Broker-Propagation.
                    while not _stop.is_set():
                        if self._connected:
                            tick_started = datetime.now(timezone.utc).isoformat()
                            with self._service_lock:
                                if _generation == self._reconcile_generation:
                                    self._reconcile_health[
                                        "last_tick_started_at_utc"] = tick_started
                            try:
                                import etoro_reconciliation
                                # Re-match durable hints after late REST import
                                # or restart. Replay never performs an order or
                                # treats WS data as final execution evidence.
                                inbox_state = self._replay_private_stream_hints()
                                changed = etoro_reconciliation.background_tick(
                                    self, paper=bool(self.paper),
                                    profile=str(getattr(
                                        config, "ACTIVE_PROFILE", "") or ""))
                                domain = etoro_reconciliation.domain_key(
                                    paper=bool(self.paper),
                                    profile=str(getattr(
                                        config, "ACTIVE_PROFILE", "") or ""),
                                    account_fingerprint=self.account_fingerprint())
                                active = etoro_reconciliation.active_for_domain(domain)
                                completed = datetime.now(timezone.utc).isoformat()
                                with self._service_lock:
                                    if _generation == self._reconcile_generation:
                                        self._reconcile_health.update({
                                            "last_tick_completed_at_utc": completed,
                                            "last_success_at_utc": completed,
                                            "last_error": "",
                                            "active_records": len(active),
                                            "changed_records": len(changed or []),
                                            "stream_inbox": inbox_state,
                                        })
                            except Exception as exc:
                                failed = datetime.now(timezone.utc).isoformat()
                                with self._service_lock:
                                    if _generation == self._reconcile_generation:
                                        self._reconcile_health.update({
                                            "last_tick_completed_at_utc": failed,
                                            "last_error_at_utc": failed,
                                            "last_error": (
                                                f"{type(exc).__name__}: {exc}")[:500],
                                        })
                                # Die persistente Domaenensperre bleibt bestehen.
                                # Stacktrace und Heartbeat machen den Fehler in
                                # Log/WebUI sichtbar, der Thread lebt weiter.
                                logger.warning(
                                    "eToro-Reconciliation wartet weiter: %s",
                                    exc, exc_info=True)
                        if _stop.wait(interval):
                            break
                finally:
                    with self._service_lock:
                        if _generation == self._reconcile_generation:
                            self._reconcile_health["running"] = False

            thread = threading.Thread(
                target=_run,
                name=f"etoro-reconciliation-{generation}", daemon=True)
            self._reconcile_thread = thread
            thread.start()
        logger.info("eToro-Reconciliation-Worker gestartet (Generation %d)", generation)

    def reconciliation_worker_status(self) -> dict:
        """Persistenzfreie Laufzeitdiagnose fuer Logbuch und WebUI."""
        with self._service_lock:
            thread = self._reconcile_thread
            result = dict(self._reconcile_health)
            result["thread_alive"] = bool(thread and thread.is_alive())
            result["stop_requested"] = bool(self._reconcile_stop.is_set())
            result["running"] = bool(
                result.get("running") and result["thread_alive"]
                and not result["stop_requested"])
            return result

    def ensure_runtime_services(self) -> bool:
        """Stellt Worker und privaten Stream nach Start/Reconnect sicher.

        ``health_check`` kann eine Verbindung bereits durch einen erfolgreichen
        GET bestaetigen. Daher darf der Runtime-Lifecycle nicht ausschliesslich
        an einen einmaligen Aufruf von ``connect`` gekoppelt sein.
        """
        with self._service_lock:
            if not self._connected:
                return False
            # Den Start vollständig unter dem reentranten Lifecycle-Lock
            # serialisieren. Ein paralleler health_check() wartet damit auf
            # das Ergebnis, statt kurzzeitig fälschlich "kein Worker" zu sehen.
            self._service_starting = True
            try:
                self._start_reconciliation_worker()
                self._start_private_stream_best_effort()
            finally:
                self._service_starting = False
        return bool(self.reconciliation_worker_status().get("running"))

    def _replay_private_stream_hints(self) -> dict:
        """Optional inbox damage must never prevent the REST reconciliation.

        One malformed event also must not starve later messages in the batch.
        Error details are metadata only; private payloads are not logged here.
        """
        errors = []
        state = {}
        try:
            import etoro_stream_inbox as inbox
            import etoro_reconciliation
            if self._identity_loaded:
                events = inbox.replay_pending(account_fingerprint=self.account_fingerprint(),
                                              paper=bool(self.paper), limit=25)
                for pending_event in events:
                    try:
                        etoro_reconciliation.apply_stream_event(
                            pending_event, account_fingerprint=self.account_fingerprint(),
                            paper=bool(self.paper), cid=self._account_cid, _replay=True)
                    except Exception as exc:
                        errors.append("event:" + type(exc).__name__)
            try:
                state = inbox.metrics(account_fingerprint=self.account_fingerprint(), paper=bool(self.paper))
            except Exception as exc:
                errors.append("metrics:" + type(exc).__name__)
        except Exception as exc:
            errors.append("replay:" + type(exc).__name__)
        return {**state, "replay_error": "; ".join(errors[:3]),
                "replay_error_count": len(errors), "rest_fallback": bool(errors)}

    def _start_private_stream_best_effort(self) -> None:
        """Private Pushs beschleunigen; REST bleibt die autoritative Wahrheit."""
        if not bool(getattr(config, "ETORO_PRIVATE_WS_ENABLED", True)):
            return
        with self._service_lock:
            existing = self._private_stream
        if existing is not None:
            try:
                status = existing.status()
                running = (status.get("running") if isinstance(status, dict)
                           else getattr(status, "running", False))
                if bool(running):
                    return
            except Exception:
                logger.debug("eToro-Privatstream-Status nicht lesbar", exc_info=True)
            try:
                existing.stop()
            except Exception:
                logger.debug("Alten eToro-Privatstream stoppen fehlgeschlagen",
                             exc_info=True)
        try:
            from .etoro_stream import EtoroPrivateStream
            import etoro_reconciliation
            stream = EtoroPrivateStream(
                self.api_key, self.user_key,
                on_private_event=lambda event: etoro_reconciliation.apply_stream_event(
                    event, account_fingerprint=self._require_bound_identity("Privatstream"),
                    paper=self.paper, cid=self._account_cid),
                url=str(getattr(config, "ETORO_PRIVATE_WS_URL",
                                "wss://ws.etoro.com/ws")))
            if not stream.start():
                logger.warning("eToro-Privatstream nicht gestartet; REST-Abgleich aktiv.")
            with self._service_lock:
                self._private_stream = stream
        except Exception as exc:
            with self._service_lock:
                self._private_stream = None
            logger.warning("eToro-Privatstream nicht verfuegbar (%s); REST-Abgleich aktiv.",
                           type(exc).__name__)

    def disconnect(self) -> None:
        self._connected = False
        with self._service_lock:
            stop_event = self._reconcile_stop
            thread = self._reconcile_thread
            stream = self._private_stream
            self._private_stream = None
            stop_event.set()
            self._reconcile_health["running"] = False
        if stream is not None:
            try:
                stream.stop()
            except Exception:
                logger.debug("eToro-Privatstream-Stopp fehlgeschlagen", exc_info=True)
        if (thread is not None and thread.is_alive()
                and thread is not threading.current_thread()):
            thread.join(timeout=max(2.0, float(getattr(
                config, "ETORO_RECONCILIATION_INTERVAL_SECONDS", 5.0)) + 1.0))
        try:
            self.session.close()
        except Exception:
            __import__("logging").getLogger(__name__).debug("Best-effort-Ausnahme unterdrueckt", exc_info=True)

    def is_connected(self) -> bool:
        return bool(self._connected)

    def health_check(self, force: bool = False) -> bool:
        """Authenticated broker liveness, not merely a local flag."""
        now = time.monotonic()
        ttl = float(getattr(config, "BROKER_HEALTHCHECK_SECONDS", 30))
        # Der reale Startpfad rief historisch zuerst health_check() auf. Ein
        # erfolgreicher GET setzte _connected=True, ohne connect() und damit
        # ohne Reconciliation-Worker. Eine noch nicht gebundene CID erzwingt
        # deshalb den vollstaendigen, idempotenten Connect-Lifecycle.
        with self._identity_lock:
            identity_loaded = bool(self._identity_loaded)
        if not self._connected or not identity_loaded:
            self.connect()
            self._last_health = now
            return True
        if not force and now - self._last_health < ttl:
            self.ensure_runtime_services()
            return True
        self._get_pnl_health()
        self._last_health = now
        self.ensure_runtime_services()
        return True

    def last_contact(self) -> str | None:
        return self._last_contact

    def connection_components(self) -> dict:
        """No network: REST responses, WS events and reconciliation stay separate."""
        stream = self.stream_status()
        observations = self._observations.snapshot()
        rest = observations["rest"].get("private", {})
        return {
            "observations": observations,
            "account_fingerprint": self.account_fingerprint(),
            "environment": "DEMO" if self.paper else "LIVE",
            "rest_state": rest.get("state", "UNKNOWN"),
            "last_rest_attempt": rest.get("last_attempt_at"),
            "last_rest_success": rest.get("last_success_at"),
            "ws_connected": bool(stream.get("connected")),
            "ws_authenticated": bool(stream.get("authenticated")),
            "ws_connected_since": stream.get("connected_since"),
            "ws_last_message": stream.get("last_message"),
            "ws_last_private_event": stream.get("last_private_event"),
            "reconciliation": self.reconciliation_worker_status(),
            "position_snapshot": {**dict(self._position_snapshot_quality),
                "snapshot_id": self.portfolio_snapshot_id() or None,
                "meaning": "Letzter validierter REST-Snapshot; kein atomarer WS-Fill-Checkpoint"},
        }

    def stream_status(self) -> dict:
        if self._private_stream is None:
            return {"running": False, "connected": False,
                    "authenticated": False, "subscribed": False,
                    "last_error": "nicht gestartet"}
        return self._private_stream.status().as_dict()

    def runtime_services_status(self) -> dict:
        """Gemeinsamer Diagnosepunkt fuer WebUI, Watchdog und Support-Logs."""
        try:
            stream = self.stream_status()
        except Exception as exc:
            stream = {
                "running": False, "connected": False,
                "authenticated": False, "subscribed": False,
                "last_error": f"{type(exc).__name__}: {exc}",
            }
        return {
            "connected": bool(self._connected),
            "environment": "DEMO" if self.paper else "LIVE",
            "account_fingerprint": self.account_fingerprint(),
            "reconciliation": self.reconciliation_worker_status(),
            "private_stream": stream,
            "connection_components": self.connection_components(),
        }

    def ist_paper(self) -> bool:
        return self.paper

    # --------------------------- Konto ---------------------------------
    def _get_aggregate(self, force=False) -> dict:
        now = time.monotonic()
        if not force and self._portfolio_cache:
            ts, data = self._portfolio_cache
            if now - ts <= float(getattr(config, "BROKER_ACCOUNT_CACHE_SECONDS", 10)):
                return data
        path = self._path(
            "/api/v1/trading/info/demo/aggregate-portfolio",
            "/api/v1/trading/info/aggregate-portfolio",
        )
        data = self._request("GET", path)
        self._portfolio_cache = (now, data)
        return data

    def kontowert(self) -> float:
        d = self._get_aggregate()
        return _float((d.get("accountTotals") or {}).get("accountTotalValue"))

    def verfuegbares_cash(self) -> float | None:
        """Frei verwendbares Cash ABZUEGLICH schwebender eigener Kaeufe (v9.3).

        Bis 9.2 wurde fuer jeden Kandidaten der rohe Brokerstand gelesen. Ist
        Kandidat A angenommen, aber die Belastung bei eToro noch nicht
        verbucht, sah Kandidat B dasselbe Geld ein zweites Mal. Die
        Domaenensperre (assert_domain_available) faengt das heute ab -- aber
        erst beim Absenden, nicht schon bei der Planung. Hier wird das Geld
        bereits vor der Entscheidung zurueckgestellt.
        """
        d = self._get_aggregate()
        roh = _float((d.get("accountTotals") or {}).get("accountAvailableCash"))
        return max(0.0, roh - self.reservierte_mittel())

    def reservierte_mittel(self) -> float:
        """Kapital, das durch noch ungeklaerte eigene Kaeufe gebunden ist."""
        try:
            import etoro_reconciliation
            offen = etoro_reconciliation.offene_kaufabsichten()
        except Exception:
            logger.debug("Reservierungen nicht lesbar", exc_info=True)
            return 0.0
        puffer = 1.0 + max(0.0, float(getattr(
            config, "RESERVIERUNG_PUFFER_PCT", 0.01)))
        summe = 0.0
        for satz in offen:
            if bool(satz.get("paper")) != bool(self.paper):
                continue
            summe += abs(float(satz.get("reserved_cash") or 0.0)) * puffer
        return summe

    def kontowaehrung(self) -> str:
        try:
            return str(self._get_aggregate().get("accountCurrency") or "USD").upper()
        except Exception:
            return "USD"

    # --------------------------- Instrumente ---------------------------
    @staticmethod
    def _symbol(inst) -> str:
        kind = str(getattr(inst, "asset_type", "stock") or "stock").lower()
        return normalize_user_symbol(getattr(inst, "name", inst), kind)

    @staticmethod
    def _cache_key(inst) -> str:
        return canonical_key(getattr(inst, "name", inst), getattr(inst, "asset_type", "stock"))

    @staticmethod
    def _normalize_market_symbol(value: str) -> str:
        raw = str(value or "").upper().strip()
        for suffix in ("/USD", "-USD", ".USD", ".US"):
            if raw.endswith(suffix):
                raw = raw[:-len(suffix)]
                break
        return raw

    def _load_all_instruments(self, force: bool = False) -> None:
        """Lädt eToros Instrument-Stammdaten in EINEM Market-Data-Call.

        Das vermeidet beim Start 200+ Search-Aufrufe und hält die gemeinsame
        120/min Market-Data-Quote frei für Kurse/Historie.
        """
        if self._all_instruments_loaded and not force:
            return
        data = self._request("GET", "/api/v1/market-data/instruments")
        rows = data.get("instrumentDisplayDatas") or []
        for item in rows:
            try:
                iid = int(item.get("instrumentID") or item.get("instrumentId"))
            except Exception:
                continue
            symbol_full = str(item.get("symbolFull") or item.get("internalSymbolFull") or "").upper().strip()
            if not symbol_full:
                continue
            norm = self._normalize_market_symbol(symbol_full)
            normalized = dict(item)
            normalized["instrumentId"] = iid
            normalized["symbol"] = symbol_full
            normalized["symbolFull"] = symbol_full
            normalized["_detected_asset_type"] = market_asset_type(symbol_full, metadata=normalized)
            self._instrument_candidates.setdefault(norm, []).append(normalized)
            self._instrument_by_id[iid] = normalized
        self._all_instruments_loaded = True

    def _resolve(self, inst) -> dict:
        sym = self._symbol(inst)
        kind = str(getattr(inst, "asset_type", "") or "").lower()
        if kind not in ("stock", "crypto"):
            raise NichtUnterstuetzt(f"eToro Asset-Typ nicht unterstuetzt: {kind or '?'}")
        key = self._cache_key(inst)
        if key in self._instrument_cache:
            return self._instrument_cache[key]
        try:
            self._load_all_instruments()
        except BrokerFehler:
            pass

        candidates = []
        for row in self._instrument_candidates.get(sym, []):
            full = str(row.get("symbolFull") or row.get("symbol") or "")
            detected = row.get("_detected_asset_type")
            if etoro_market_symbol_matches(sym, kind, full) and (detected in (None, kind)):
                candidates.append(row)
        if len(candidates) == 1:
            exact = candidates[0]
        elif len(candidates) > 1:
            typed = [x for x in candidates if x.get("_detected_asset_type") == kind]
            if len(typed) == 1:
                exact = typed[0]
            else:
                raise NichtUnterstuetzt(f"eToro Instrument mehrdeutig: {kind}:{sym}")
        else:
            # Search ist nur ein Fallback; auch dessen Antwort wird asset-sicher gefiltert.
            data = self._request("GET", "/api/v1/market-data/search", params={"internalSymbolFull": sym})
            items = data.get("items", data if isinstance(data, list) else []) or []
            filtered=[]
            for item in items:
                full=str(item.get("internalSymbolFull") or item.get("symbolFull") or item.get("symbol") or "").upper().strip()
                detected=market_asset_type(full, metadata=item)
                if etoro_market_symbol_matches(sym, kind, full) and detected in (None, kind):
                    filtered.append((item,full,detected))
            if len(filtered) != 1:
                raise NichtUnterstuetzt(f"eToro Instrument nicht eindeutig gefunden: {kind}:{sym}")
            item,full,detected=filtered[0]
            exact=dict(item); exact["symbolFull"]=full; exact["symbol"]=full; exact["_detected_asset_type"]=detected
            exact["instrumentId"] = int(exact.get("instrumentId") or exact.get("instrumentID"))
            self._instrument_by_id[exact["instrumentId"]]=exact

        exact = dict(exact)
        exact["_bot_asset_type"] = kind
        exact["_bot_symbol"] = sym
        exact["symbol"] = str(exact.get("symbolFull") or exact.get("symbol") or sym).upper()
        self._instrument_cache[key] = exact
        self._instrument_by_id[int(exact["instrumentId"])] = exact
        return exact

    def _eligibility_for(self, inst) -> dict:
        sym = self._symbol(inst)
        key = self._cache_key(inst)
        if key in self._eligibility:
            return self._eligibility[key]
        resolved = self._resolve(inst)
        path = self._path(
            "/api/v2/trading/info/demo/eligibility",
            "/api/v2/trading/info/eligibility",
        )
        data = self._request("POST", path, payload={
            "instrumentIds": [resolved["instrumentId"]], "currency": "USD"
        })
        rows = data.get("eligibilities") or []
        if not rows:
            raise NichtUnterstuetzt(f"eToro: keine Trading-Eligibility fuer {sym}")
        row = rows[0]
        self._eligibility[key] = row
        return row

    def _settlement(self, inst) -> str:
        row = self._eligibility_for(inst)
        if not bool(row.get("allowOpenPosition", False)):
            raise NichtUnterstuetzt(f"eToro: {self._symbol(inst)} darf fuer dieses Konto nicht eroeffnet werden")
        configs = row.get("leverageConfigs") or []
        real = None; cfd = None
        for c in configs:
            st = str(c.get("settlementType") or "").lower()
            direction = str(c.get("direction") or "").lower()
            vals = [int(v) for v in (c.get("leverageValues") or []) if str(v).isdigit()]
            if direction not in ("long", "buy", "") or 1 not in vals:
                continue
            if st in ("real", "underlying", "asset"):
                real = str(c.get("settlementType"))
            elif st == "cfd":
                cfd = str(c.get("settlementType"))
        if real:
            return real
        if cfd and bool(getattr(config, "ETORO_ALLOW_CFD", False)):
            return cfd
        raise NichtUnterstuetzt(
            f"eToro: {self._symbol(inst)} ist fuer dieses Konto nicht als REAL/Hebel 1 freigegeben"
            + ("; CFD waere verfuegbar, ist aber aus Sicherheitsgruenden deaktiviert" if cfd else "")
        )

    def _validate_protection(self, inst, settlement: str, reference: float, stop: float, take_profit: float) -> None:
        """Prueft SL/TP gegen die konto-/instrumentbezogenen eToro-Grenzen.

        Wir veraendern die Strategiepreise NICHT stillschweigend. Passt ein
        geplanter Schutz nicht zu eToros aktuell gemeldeter Eligibility, wird
        der Kauf fail-closed ausgelassen.
        """
        ref = float(reference or 0); sl = float(stop or 0); tp = float(take_profit or 0)
        if ref <= 0 or sl <= 0 or tp <= 0 or not (sl < ref < tp):
            raise BrokerFehler("eToro: Stop/Take-Profit oder Referenzpreis ungueltig")
        row = self._eligibility_for(inst)
        chosen = None
        for c in row.get("leverageConfigs") or []:
            st = str(c.get("settlementType") or "").lower()
            direction = str(c.get("direction") or "").lower()
            vals = []
            for v in c.get("leverageValues") or []:
                try: vals.append(int(v))
                except Exception: __import__("logging").getLogger(__name__).debug("Best-effort-Ausnahme unterdrueckt", exc_info=True)
            if st == str(settlement).lower() and direction in ("long", "buy", "") and 1 in vals:
                chosen = c; break
        if not chosen:
            raise NichtUnterstuetzt(f"eToro: keine passende Eligibility fuer {self._symbol(inst)} / {settlement} / Hebel 1")
        if not bool(chosen.get("allowStopLossTakeProfit", True)):
            raise NichtUnterstuetzt(f"eToro: Stop-Loss/Take-Profit fuer {self._symbol(inst)} nicht erlaubt")
        sl_pct = (ref - sl) / ref * 100.0
        tp_pct = (tp - ref) / ref * 100.0
        min_sl = _float(chosen.get("minStopLossPercentage"), -1)
        max_sl = _float(chosen.get("maxStopLossPercentage"), -1)
        min_tp = _float(chosen.get("minTakeProfitPercentage"), -1)
        max_tp = _float(chosen.get("maxTakeProfitPercentage"), -1)
        if min_sl >= 0 and sl_pct + 1e-9 < min_sl:
            raise NichtUnterstuetzt(f"eToro: geplanter Stop {sl_pct:.2f}% liegt unter Minimum {min_sl:.2f}%")
        if max_sl >= 0 and sl_pct - 1e-9 > max_sl:
            raise NichtUnterstuetzt(f"eToro: geplanter Stop {sl_pct:.2f}% liegt ueber Maximum {max_sl:.2f}%")
        if min_tp >= 0 and tp_pct + 1e-9 < min_tp:
            raise NichtUnterstuetzt(f"eToro: geplantes Take-Profit {tp_pct:.2f}% liegt unter Minimum {min_tp:.2f}%")
        if max_tp >= 0 and tp_pct - 1e-9 > max_tp:
            raise NichtUnterstuetzt(f"eToro: geplantes Take-Profit {tp_pct:.2f}% liegt ueber Maximum {max_tp:.2f}%")

    def instrument_metadata(self, instrument) -> dict:
        """Liefert nur Stammdaten fuer deterministische Universums-Pruefungen.

        Keine Order, kein Settlement-Wechsel und keine KI-Seitenwirkung. Die
        asset-sichere _resolve()-Logik bleibt die einzige Symbolaufloesung.
        """
        row = self._resolve(instrument)
        display = str(
            row.get("instrumentDisplayName") or row.get("displayName") or
            row.get("instrumentName") or row.get("name") or ""
        ).strip()
        return {
            "instrument_id": int(row.get("instrumentId")),
            "symbol_full": str(row.get("symbolFull") or row.get("symbol") or "").upper(),
            "asset_type": str(row.get("_detected_asset_type") or row.get("_bot_asset_type") or ""),
            "instrument_type": str(row.get("instrumentType") or row.get("assetType") or ""),
            "display_name": display,
        }

    def instrument_handelbar(self, instrument) -> tuple[bool, str]:
        if getattr(instrument, "asset_type", "") not in ("stock", "crypto"):
            return False, "eToro-Adapter handelt in diesem Bot nur Aktien und Krypto"
        # EU-Symbole koennen an mehreren Boersen mehrdeutig sein. Bis wir eine
        # Exchange-ID im Watchlistmodell fuehren, vermeiden wir falsche Treffer.
        if getattr(instrument, "asset_type", "") == "stock" and str(getattr(instrument, "currency", "USD")).upper() != "USD":
            return False, "EU-Aktie bei eToro noch nicht eindeutig per Exchange gemappt"
        try:
            self._resolve(instrument)
            self._settlement(instrument)
            return True, ""
        except BrokerFehler as exc:
            return False, str(exc)

    def qualifiziere(self, instrumente: list) -> tuple[list, list]:
        self._load_all_instruments()
        candidates, failed = [], []
        for inst in instrumente:
            if getattr(inst, "asset_type", "") not in ("stock", "crypto"):
                failed.append((inst.name, "eToro-Adapter handelt nur Aktien und Krypto")); continue
            if getattr(inst, "asset_type", "") == "stock" and str(getattr(inst, "currency", "USD")).upper() != "USD":
                failed.append((inst.name, "EU-Aktie noch nicht eindeutig per Exchange gemappt")); continue
            try:
                candidates.append((inst, self._resolve(inst)))
            except BrokerFehler as exc:
                failed.append((inst.name, str(exc)))

        # eToro erlaubt bis zu 100 Instrument-IDs je Eligibility-Abfrage.
        path = self._path("/api/v2/trading/info/demo/eligibility", "/api/v2/trading/info/eligibility")
        for i in range(0, len(candidates), 100):
            batch = candidates[i:i+100]
            ids = [r["instrumentId"] for _, r in batch]
            if not ids:
                continue
            data = self._request("POST", path, payload={"instrumentIds": ids, "currency": "USD"})
            rows = data.get("eligibilities") or []
            by_id = {}
            for row in rows:
                try:
                    by_id[int(row.get("instrumentId") or row.get("instrumentID"))] = row
                except Exception:
                    __import__("logging").getLogger(__name__).debug("Best-effort-Ausnahme unterdrueckt", exc_info=True)
            for inst, r in batch:
                row = by_id.get(r["instrumentId"])
                if row:
                    self._eligibility[self._cache_key(inst)] = row

        ok = []
        for inst, _ in candidates:
            try:
                self._settlement(inst)
                ok.append(inst)
            except BrokerFehler as exc:
                failed.append((inst.name, str(exc)))
        return ok, failed

    # --------------------------- Marktdaten ----------------------------
    def _rate_row(self, instrument, *, force: bool = False) -> dict | None:
        r = self._resolve(instrument)
        return self._rate_row_by_id(int(r["instrumentId"]), force=force)

    def _rate_row_by_id(self, iid: int, *, force: bool = False) -> dict | None:
        """Kurszeile direkt ueber die instrumentId (v9.3).

        Der Positionssnapshot kennt die instrumentId bereits; ein zweiter
        Umweg ueber _resolve() waere unnoetig und koennte fehlschlagen, wenn
        das Instrument nicht im aktuellen Universum steht.
        """
        iid = int(iid)
        ttl = max(0.0, float(getattr(config, "ETORO_MARKET_DATA_CACHE_SECONDS", 3)))
        cached = self._rate_cache.get(iid)
        if not force and cached and time.time() - cached[0] <= ttl:
            return dict(cached[1])
        data = self._request("GET", "/api/v1/market-data/instruments/rates",
                             params={"instrumentIds": str(iid)})
        rows = data.get("rates") or []
        for row in rows:
            try:
                row_iid = int(row.get("instrumentID") or row.get("instrumentId") or -1)
            except Exception:
                row_iid = -1
            if row_iid == iid:
                self._rate_cache[iid] = (time.time(), dict(row))
                return dict(row)
        return None

    @classmethod
    def _rate_age_seconds(cls, row: dict | None):
        """Alter der Kurszeile in Sekunden, oder ``None`` wenn unbekannt."""
        stamp = cls._rate_timestamp(row)
        if not stamp:
            return None
        try:
            gemessen = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
        if gemessen.tzinfo is None:
            gemessen = gemessen.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - gemessen).total_seconds())

    @staticmethod
    def _rate_timestamp(row: dict | None):
        if not isinstance(row, dict):
            return None
        return (row.get("date") or row.get("timestamp") or row.get("updateTime")
                or row.get("lastUpdated") or row.get("lastUpdate"))

    @staticmethod
    def _quote_age_seconds(value) -> float | None:
        if value in (None, ""):
            return None
        try:
            if isinstance(value, (int, float)):
                n=float(value)
                # Millisekunden-Epoch erkennen.
                if n > 10_000_000_000:
                    n /= 1000.0
                dt=datetime.fromtimestamp(n, tz=timezone.utc)
            else:
                txt=str(value).strip().replace("Z", "+00:00")
                dt=datetime.fromisoformat(txt)
                if dt.tzinfo is None:
                    dt=dt.replace(tzinfo=timezone.utc)
                dt=dt.astimezone(timezone.utc)
            return max(0.0, (datetime.now(timezone.utc)-dt).total_seconds())
        except Exception:
            return None

    @staticmethod
    def _explicit_market_flag(row: dict | None) -> tuple[bool | None, str]:
        """Use an explicit flag/status only if eToro actually returned one.

        The public documentation does not guarantee a universal isOpen field,
        therefore unknown/numeric values are never guessed.
        """
        if not isinstance(row, dict):
            return None, ""
        for key in ("isMarketOpen", "marketOpen", "isOpen", "isTradable", "tradable"):
            if key in row and isinstance(row.get(key), bool):
                return bool(row.get(key)), f"{key}={row.get(key)}"
        for key in ("marketStatus", "tradingStatus", "status", "state"):
            if key not in row:
                continue
            value=str(row.get(key) or "").strip().upper().replace(" ", "_")
            if value in {"OPEN", "OPENED", "TRADING", "TRADEABLE", "TRADABLE", "ACTIVE"}:
                return True, f"{key}={value}"
            if value in {"CLOSED", "CLOSE", "HALTED", "HALT", "SUSPENDED", "INACTIVE", "DISABLED"}:
                return False, f"{key}={value}"
        return None, ""

    def market_session_status(self, instrument) -> dict:
        """Read-only broker probe used by market_session.py.

        It deliberately does not pretend that a fresh quote equals REGULAR
        market hours. It only reports broker-side evidence; the exchange clock
        is evaluated separately by market_session.py.
        """
        row=self._rate_row(instrument)
        if not row:
            return {"broker_tradable": None, "quote_age_seconds": None,
                    "source": "eToro Public API rates", "detail": "kein Rate-Datensatz"}
        flag, raw=self._explicit_market_flag(row)
        ts=self._rate_timestamp(row)
        age=self._quote_age_seconds(ts)
        detail=[]
        if raw:
            detail.append(raw)
        if age is not None:
            detail.append(f"Quote-Alter {age:.0f}s")
        else:
            detail.append("Quote-Alter unbekannt")
        return {"broker_tradable": flag, "quote_age_seconds": age,
                "timestamp": ts, "source": "eToro Public API rates",
                "detail": "; ".join(detail)}

    def latest_bid_ask(self, instrument):
        row = self._rate_row(instrument)
        if row:
            return {"bid": _float(row.get("bid")), "ask": _float(row.get("ask")),
                    "last": _float(row.get("lastExecution")), "timestamp": self._rate_timestamp(row),
                    "broker_tradable": self._explicit_market_flag(row)[0],
                    "source": "eToro Public API rates"}
        return None

    def historie(self, instrument, dauer: str, kerzengroesse: str,
                 nur_handelszeiten: bool = True) -> pd.DataFrame:
        r = self._resolve(instrument)
        interval_map = {
            "1 min": "OneMinute", "5 mins": "FiveMinutes", "5 min": "FiveMinutes",
            "10 mins": "TenMinutes", "15 mins": "FifteenMinutes",
            "30 mins": "ThirtyMinutes", "1 hour": "OneHour", "4 hours": "FourHours",
            "1 day": "OneDay", "1 week": "OneWeek",
        }
        interval = interval_map.get(str(kerzengroesse).strip().lower(), "OneHour")
        # Core braucht fuer Indikatoren typischerweise deutlich <1000 Bars.
        digits = "".join(ch for ch in str(dauer) if ch.isdigit())
        n = int(digits or 30)
        count = min(1000, max(80, n * (24 if "d" in str(dauer).lower() else 1)))

        def fetch():
            data = self._request(
                "GET",
                f"/api/v1/market-data/instruments/{r['instrumentId']}/history/candles/desc/{interval}/{count}",
            )
            outer = data.get("candles") or []
            rows = []
            for group in outer:
                rows.extend(group.get("candles") or [])
            if not rows:
                return pd.DataFrame(columns=["open","high","low","close","volume"])
            df = pd.DataFrame(rows)
            rename = {"fromDate": "date"}
            df = df.rename(columns=rename)
            df["date"] = pd.to_datetime(df["date"], utc=True, errors="coerce")
            df = df.dropna(subset=["date"]).sort_values("date").set_index("date")
            for col in ("open","high","low","close","volume"):
                df[col] = pd.to_numeric(df.get(col), errors="coerce")
            return completed_bars_only(df[["open","high","low","close","volume"]].dropna(subset=["close"]), kerzengroesse)
        return retry_call(fetch, label=f"eToro Historie {self._symbol(instrument)}")

    # --------------------------- Portfolio -----------------------------
    @staticmethod
    def _validate_pnl_schema(data: dict) -> dict:
        """Beweist die fuer negative Positionsaussagen erforderliche Struktur.

        Ein HTTP-200 und ein JSON-Objekt reichen nicht: ein leerer Fehlerwrapper
        oder ein unvollstaendiger API-Rollout darf niemals als "keine Position"
        interpretiert werden. Eine wirklich leere Positionensicht ist nur das
        explizite ``positions: []`` innerhalb eines clientPortfolio-Objekts.
        """
        if not isinstance(data, dict):
            raise BrokerFehler(
                "eToro-P&L-Endpunkt lieferte keinen vollstaendigen Objektsnapshot")
        portfolio = data.get("clientPortfolio")
        if not isinstance(portfolio, dict):
            raise BrokerFehler(
                "eToro-P&L-Snapshot ohne clientPortfolio; negative "
                "Positionsaussage bleibt gesperrt")
        if "positions" not in portfolio or not isinstance(
                portfolio.get("positions"), list):
            raise BrokerFehler(
                "eToro-P&L-Snapshot ohne explizites positions-Array; negative "
                "Positionsaussage bleibt gesperrt")
        mirrors = portfolio.get("mirrors", [])
        if mirrors is None:
            mirrors = []
        if not isinstance(mirrors, list):
            raise BrokerFehler(
                "eToro-P&L-Snapshot besitzt kein gueltiges mirrors-Array")
        for index, mirror in enumerate(mirrors):
            if not isinstance(mirror, dict):
                raise BrokerFehler(
                    f"eToro-P&L-Snapshot: mirrors[{index}] ist kein Objekt")
            if ("positions" in mirror
                    and not isinstance(mirror.get("positions"), list)):
                raise BrokerFehler(
                    f"eToro-P&L-Snapshot: mirrors[{index}].positions ist keine Liste")
        return portfolio

    def _pnl(self, *, force: bool = False) -> dict:
        """Der autoritative Portfolio-Snapshot (v9.3).

        Bis 9.2 holten Positionen, Cash, Orders und der positionId-Abgleich
        diesen Stand JEDER FUER SICH. Innerhalb eines Zyklus konnten sie
        damit unterschiedliche Brokerstaende sehen -- eine Position war im
        einen Aufruf schon da und im naechsten noch nicht. Fuer eine
        Zuordnung ueber exakte positionIds ist das nicht tragbar.

        Der Snapshot bekommt eine Generation (``snapshot_id``) und eine
        kurze Lebensdauer. ``force=True`` erneuert ihn bewusst -- etwa zur
        Orderbestaetigung; alle darauf aufbauenden Zuordnungen benutzen dann
        dieselbe Generation.
        """
        ttl = max(0.0, float(getattr(
            config, "ETORO_PORTFOLIO_SNAPSHOT_SECONDS", 2.0)))
        # ``force=True`` darf bei gleichzeitigem Scanner-/Worker-Zugriff nicht
        # zwei ineinander verschachtelte Generationen erzeugen. Der Lock deckt
        # deshalb Cacheentscheidung, genau einen GET und das Veröffentlichen
        # der Generation gemeinsam ab.
        with self._portfolio_state_lock:
            jetzt = time.monotonic()
            cached = getattr(self, "_pnl_snapshot", None)
            if not force and cached and (jetzt - cached[0]) <= ttl:
                return cached[1]
            path = self._path(
                "/api/v1/trading/info/demo/pnl",
                "/api/v1/trading/info/real/pnl")
            data = self._request("GET", path)
            self._validate_pnl_schema(data)
            self._pnl_generation = int(
                getattr(self, "_pnl_generation", 0)) + 1
            data = dict(data)
            data["_snapshot_id"] = (
                f"etoro:{'demo' if self.paper else 'live'}:"
                f"{self._pnl_generation}")
            data["_snapshot_at"] = datetime.now(timezone.utc).isoformat()
            try:
                from etoro_settlement_review import capture_pnl
                capture_pnl(self, data)
            except Exception as exc:
                logger.debug("Barbestandsbeleg derzeit nicht speicherbar: %s", type(exc).__name__)
            try:
                # 10.2.0: Gebuehrenfelder offener Positionen dauerhaft sichern,
                # solange die API sie liefert (Vorarbeit Ergebnisabgleich).
                from etoro_fee_snapshot import capture_pnl as capture_fees
                capture_fees(self, data)
            except Exception as exc:
                logger.debug("Gebuehren-Snapshot derzeit nicht speicherbar: %s", type(exc).__name__)
            self._pnl_snapshot = (time.monotonic(), data)
            return data

    def portfolio_snapshot_id(self) -> str:
        """Generation des zuletzt gelesenen Portfolio-Snapshots."""
        with self._portfolio_state_lock:
            cached = getattr(self, "_pnl_snapshot", None)
            return str((cached[1] if cached else {}).get("_snapshot_id") or "")

    @staticmethod
    def _all_positions(pnl: dict) -> list[dict]:
        cp = pnl.get("clientPortfolio") or {}
        out = list(cp.get("positions") or [])
        for mirror in cp.get("mirrors") or []:
            out.extend(mirror.get("positions") or [])
        return [_normalisiere_positionszeile(x) for x in out if isinstance(x, dict)]

    @staticmethod
    def _all_orders(pnl: dict) -> list[dict]:
        cp = pnl.get("clientPortfolio") or {}
        out = []
        for key in ("orders", "ordersForOpen", "ordersForClose", "ordersForCloseMultiple"):
            for row in cp.get(key) or []:
                if isinstance(row, dict):
                    tagged = dict(row); tagged["_order_role"] = key
                    out.append(tagged)
        for mirror in cp.get("mirrors") or []:
            for key in ("orders", "ordersForOpen", "ordersForClose", "ordersForCloseMultiple"):
                for row in mirror.get(key) or []:
                    if isinstance(row, dict):
                        tagged = dict(row); tagged["_order_role"] = key
                        out.append(tagged)
        return [_normalisiere_positionszeile(x) for x in out if isinstance(x, dict)]

    @staticmethod
    def _symbolkern(wert) -> str:
        """Vergleichbare Form eines Symbols (SPGI, SPGI.US, spgi)."""
        roh = str(wert or "").strip().upper()
        for suffix in (".US", ".DE", ".L", ".PA", ".MI", ".AS", ".SW"):
            if roh.endswith(suffix):
                return roh[: -len(suffix)]
        return roh

    def _symbol_for_id(self, iid: int) -> str:
        if iid not in self._instrument_by_id:
            try:
                self._load_all_instruments()
            except Exception:
                __import__("logging").getLogger(__name__).debug("Best-effort-Ausnahme unterdrueckt", exc_info=True)
        if iid in self._instrument_by_id:
            row=self._instrument_by_id[iid]
            return str(row.get("symbolFull") or row.get("symbol") or iid).upper()
        return f"ETORO_{iid}"

    def position_snapshot(self, *, force: bool = False) -> dict:
        self._require_bound_identity("Positionssnapshot")
        with self._portfolio_state_lock:
            self._position_snapshot_quality["last_attempt_at"] = datetime.now(timezone.utc).isoformat()
            try:
                result = self._position_snapshot_locked(force=force)
            except Exception as exc:
                self._position_snapshot_quality.update(state="ERROR", complete=False,
                                                       error_code=type(exc).__name__)
                raise
            self._position_snapshot_quality.update(
                state="OK", complete=True, error_code="",
                last_success_at=result.get("snapshot_at") or None,
                snapshot_id=result.get("snapshot_id") or None)
            return result

    def _position_snapshot_locked(self, *, force: bool = False) -> dict:
        """Atomarer, leichter Positionsbeweis aus genau einer P&L-Antwort.

        Der Reconciliation-Worker benötigt IDs und rohe Schutz-/Orderfelder,
        nicht einen Kurs je Position. Daher löst dieser Pfad weder Rates- noch
        Instrumentkatalog-Abfragen aus. ``positionen()`` reichert denselben
        Snapshot erst anschließend für die Anzeige an.
        """
        pnl = self._pnl(force=force)
        self._validate_pnl_schema(pnl)
        if not self._position_schema_logged:
            cp = pnl.get("clientPortfolio") or {}
            raw_rows = list(cp.get("positions") or [])
            for mirror in cp.get("mirrors") or []:
                raw_rows.extend(mirror.get("positions") or [])
            id_fields = sorted({
                key for row in raw_rows if isinstance(row, dict)
                for key in row
                if str(key).lower() in {"positionid", "instrumentid"}
            })
            logger.info(
                "eToro-Portfolio-ID-Diagnose %s: %d Depotzeile(n), "
                "erkannte ID-Felder=%s, Snapshot=%s",
                "DEMO" if self.paper else "LIVE", len(raw_rows),
                id_fields or ["KEINE"], str(pnl.get("_snapshot_id") or "?"))
            self._position_schema_logged = True
        all_rows = self._all_positions(pnl)
        snapshot_id = str(pnl.get("_snapshot_id") or "")
        environment = "DEMO" if self.paper else "LIVE"
        # Eine offene Zeile ohne positionId darf niemals zu einem per Symbol
        # geratenen Bestand werden. Das ist ein Brokerprotokollfehler und
        # sperrt den eToro-Zyklus fail-closed.
        missing_ids = [
            sorted(str(k) for k in row.keys())
            for row in all_rows
            if bool(row.get("isBuy", True))
            and max(0.0, _float(row.get("units"))) > 0
            and row.get("positionId") in (None, "")
        ]
        if missing_ids:
            logger.error(
                "eToro-Depotzeile ohne positionId (%s, Snapshot %s, Felder=%s)",
                environment, snapshot_id or "unbekannt", missing_ids[:3])
            raise BrokerFehler(
                "eToro-Depot liefert eine offene Position ohne normalisierbare "
                "positionId; Aktienhandel bleibt sicher gesperrt")
        missing_instrument_ids = [
            str(row.get("positionId") or "?")
            for row in all_rows
            if bool(row.get("isBuy", True))
            and max(0.0, _float(row.get("units"))) > 0
            and int(_float(row.get("instrumentId"), 0.0)) <= 0
        ]
        if missing_instrument_ids:
            logger.error(
                "eToro-Depotzeile ohne instrumentId (%s, Snapshot %s, "
                "positionIds=%s)", environment, snapshot_id or "unbekannt",
                missing_instrument_ids[:5])
            raise BrokerFehler(
                "eToro-Depot liefert eine offene Position ohne normalisierbare "
                "instrumentId; Aktienhandel bleibt sicher gesperrt")

        open_rows = [
            dict(row) for row in all_rows
            if bool(row.get("isBuy", True))
            and max(0.0, _float(row.get("units"))) > 0
        ]
        rows_by_position_id = {
            str(row["positionId"]): dict(row) for row in open_rows
        }
        open_ids = set(rows_by_position_id)
        instrument_ids_by_position_id = {
            pid: str(row.get("instrumentId") or "")
            for pid, row in rows_by_position_id.items()
        }
        # Diese Menge ist die aktuelle Brokerwahrheit fuer die
        # Neustart-Reconciliation. Eine gespeicherte FILLED-Order allein darf
        # niemals wieder eine offene Position erzeugen.
        lightweight_map: dict[str, set[str]] = {}
        for pid, row in rows_by_position_id.items():
            iid = int(row.get("instrumentId") or 0)
            metadata = self._instrument_by_id.get(iid) or {}
            symbol = str(metadata.get("symbolFull") or
                         metadata.get("symbol") or f"ETORO_{iid}").upper()
            lightweight_map.setdefault(symbol, set()).add(pid)
        with self._portfolio_state_lock:
            self._current_position_ids = set(open_ids)
            self._current_position_map = lightweight_map
            self._current_position_ids_known = True
        return {
            "snapshot_id": snapshot_id,
            "snapshot_at": str(pnl.get("_snapshot_at") or ""),
            "environment": environment,
            "account_fingerprint": self.account_fingerprint(),
            "rows": [dict(row) for row in open_rows],
            "rows_by_position_id": {
                pid: dict(row) for pid, row in rows_by_position_id.items()},
            "open_ids": set(open_ids),
            "position_ids": set(open_ids),
            "instrument_ids_by_position_id": instrument_ids_by_position_id,
            "complete": True,
        }

    def positionen(self) -> list:
        snapshot = self.position_snapshot(force=False)
        all_rows = list(snapshot["rows"])
        snapshot_id = str(snapshot.get("snapshot_id") or "")
        environment = str(snapshot.get("environment") or
                          ("DEMO" if self.paper else "LIVE"))
        out = []
        display_position_map: dict[str, set[str]] = {}
        for row in all_rows:
            if not bool(row.get("isBuy", True)):
                continue  # Bot eroeffnet keine Shorts.
            iid = int(row.get("instrumentId") or 0)
            pid = str(row.get("positionId") or "")
            qty = max(0.0, _float(row.get("units")))
            if qty <= 0:
                continue
            if iid <= 0:
                raise BrokerFehler(
                    f"eToro-Position {pid} ohne instrumentId; Aktienhandel gesperrt")
            avg = _float(row.get("openRate"))
            # v9.3: Ein FEHLENDES pnL ist unbekannt, nicht null. _float()
            # machte daraus 0.0 -- im Dashboard stand deshalb "Buchwert
            # +0,00" fuer eine frisch gekaufte Position (MSFT, 31.08.2026).
            # Das sieht aus wie ausgeglichen und ist eine Falschaussage.
            pnl_sum = (_float(row.get("pnL")) if row.get("pnL") is not None else None)
            symbol = self._symbol_for_id(iid)
            display_position_map.setdefault(symbol, set()).add(pid)
            rate, quelle, alter = self._positionskurs(iid, [row])
            md=self._instrument_by_id.get(iid,{})
            asset_type=str(md.get("_bot_asset_type") or md.get("_detected_asset_type") or "unknown")
            stop_value = _float(row.get("stopLossRate") or row.get("stopLoss")) or None
            take_value = _float(row.get("takeProfitRate") or row.get("takeProfit")) or None
            if row.get("isNoStopLoss") is True:
                stop_value = None
            if row.get("isNoTakeProfit") is True:
                take_value = None
            out.append(Position(symbol=symbol, quantity=qty, avg_cost=avg, currency="USD",
                                asset_type=asset_type, broker_id=pid, position_ids=(pid,),
                                instrument_id=str(iid), snapshot_id=snapshot_id,
                                account_fingerprint=self.account_fingerprint(),
                                broker_environment=environment,
                                market_price=(rate or 0.0),
                                market_value=qty*rate if rate else 0.0,
                                unrealized_pnl=pnl_sum,
                                price_source=quelle, price_age_seconds=alter,
                                broker_stop=stop_value, broker_take_profit=take_value))
        with self._portfolio_state_lock:
            # Der leichte Snapshot hat ggf. nur ETORO_<instrumentId>. Sobald
            # die Anzeige den Katalog ohnehin benötigt, wird die Evidenzkarte
            # ohne weitere P&L-Antwort auf das echte Symbol verfeinert.
            if self.portfolio_snapshot_id() == snapshot_id:
                self._current_position_map = display_position_map
        return out

    def _positionskurs(self, iid: int, rows: list) -> tuple:
        """Belastbarer Kurs einer offenen Position (v9.3).

        Bis 9.2 galt ausschliesslich ``closeRate``. Bei einer frisch
        eroeffneten Position ist der oft noch nicht gesetzt -- der Kurs wurde
        0, der Marktwert 0, und in der Oberflaeche war die Position damit
        nicht uebernehmbar ("Kein aktueller Kurs"). Der Rates-Endpunkt liegt
        im Adapter bereits vor und liefert Bid/Ask/Last.

        Rueckgabe: (kurs oder None, quelle, alter_in_sekunden oder None).
        """
        rate = max((_float(r.get("closeRate")) for r in rows), default=0.0)
        if rate > 0:
            return rate, "ETORO_PNL_CLOSE_RATE", 0.0
        row = None
        try:
            row = self._rate_row_by_id(iid)
        except Exception:
            logger.debug("Rates-Fallback fuer %s nicht abrufbar", iid, exc_info=True)
        if isinstance(row, dict):
            alter = self._rate_age_seconds(row)
            for feld, quelle in (("bid", "ETORO_RATES_BID"),
                                 ("ask", "ETORO_RATES_ASK"),
                                 ("lastExecution", "ETORO_RATES_LAST"),
                                 ("last", "ETORO_RATES_LAST")):
                wert = _float(row.get(feld))
                if wert > 0:
                    return wert, quelle, alter
        # Kein erfundener Kurs. Die Oberflaeche zeigt "unbekannt".
        return None, "UNBEKANNT", None

    def current_position_ids(self, *, force: bool = False) -> set[str]:
        """Exakte aktuell offene eToro-positionIds, niemals Symbolraten."""
        snapshot = self.position_snapshot(force=force)
        return set(snapshot.get("open_ids") or set())

    def current_position_evidence(self) -> dict[str, set[str]]:
        """Letzter erfolgreich gelesener Depot-Snapshot nach Symbol."""
        with self._portfolio_state_lock:
            if not self._current_position_ids_known:
                return {}
            return {symbol: set(ids)
                    for symbol, ids in self._current_position_map.items()}

    def trade_history_snapshot(self, min_date: str, *, max_pages: int = 12,
                               force: bool = False) -> dict:
        with self._history_state_lock:
            return self._trade_history_snapshot_locked(
                min_date, max_pages=max_pages, force=force)

    def _trade_history_snapshot_locked(
            self, min_date: str, *, max_pages: int = 12,
            force: bool = False) -> dict:
        """Paginiert geschlossene Positionen samt Vollständigkeitsbeweis.

        eToro akzeptiert höchstens ein knappes Jahr Rückblick. Der Adapter
        klemmt deshalb auf 364 Tage. Ein volles letztes Blatt am ``max_pages``-
        Limit ist ausdrücklich *unvollständig* und darf keine negative
        Eigentums-/Close-Schlussfolgerung begründen.
        """
        today = date.today()
        lower_bound = today - timedelta(days=364)
        try:
            requested = date.fromisoformat(str(min_date or today.isoformat())[:10])
        except (TypeError, ValueError):
            requested = today
        requested = min(today, max(lower_bound, requested))
        page_limit = max(1, int(max_pages))
        key = (requested.isoformat(), page_limit)
        cached = self._history_cache.get(key)
        if (not force and cached
                and time.monotonic() - cached[0] < float(getattr(
                    config, "ETORO_TRADE_HISTORY_POLL_SECONDS", 60.0))):
            result = dict(cached[1])
            result["rows"] = [dict(x) for x in result.get("rows") or []]
            return result
        path = self._path(
            "/api/v1/trading/info/trade/demo/history",
            "/api/v1/trading/info/trade/history")
        rows: list[dict] = []
        seen_pages: set[tuple] = set()
        page_size = 200
        complete = False
        stop_reason = "page_limit"
        pages_read = 0
        for page in range(1, page_limit + 1):
            data = self._request("GET", path, params={
                "minDate": key[0], "page": page, "pageSize": page_size
            })
            pages_read = page
            if isinstance(data, list):
                batch = data
            elif isinstance(data, dict):
                if "items" in data:
                    batch = data.get("items")
                elif "trades" in data:
                    batch = data.get("trades")
                else:
                    raise BrokerFehler(
                        "eToro Trade-History besitzt weder items noch trades")
            else:
                raise BrokerFehler(
                    "eToro Trade-History lieferte kein Listen-/Objektschema")
            if not isinstance(batch, list):
                raise BrokerFehler(
                    "eToro Trade-History-Seite ist keine Liste")
            if not batch:
                complete = True
                stop_reason = "empty_page"
                break
            batch = [_normalisiere_positionszeile(x)
                     for x in batch if isinstance(x, dict)]
            signature = tuple((
                str(x.get("positionId") or ""),
                _close_execution_id(x),
                _identity_timestamp(
                    x.get("closeTimestamp") or x.get("closeTime") or ""),
                _identity_number(_api_value(
                    x, "units", "Units", "closedUnits", "ClosedUnits",
                    default="")),
                _identity_number(_api_value(
                    x, "closeRate", "rate", "CloseRate", "Rate",
                    default="")),
            ) for x in batch[:5])
            if signature in seen_pages:
                stop_reason = "repeated_page"
                break
            seen_pages.add(signature)
            rows.extend(dict(x) for x in batch)
            if len(batch) < page_size:
                complete = True
                stop_reason = "short_page"
                break
        unique: dict[tuple[str, ...], dict] = {}
        for row in rows:
            # Teilverkäufe derselben Position dürfen nicht wegen gleicher
            # positionId/closeTimestamp zusammenfallen. Dieselben ID-Varianten
            # wie in _close_fill_identity verwenden; sonst koennten zwei Deals
            # hier bereits verschwinden, obwohl der spaetere Fillpfad sie sauber
            # unterscheiden koennte.
            execution_id = _close_execution_id(row)
            if execution_id:
                identity = (
                    "execution", str(row.get("positionId") or ""), execution_id)
            else:
                identity = (
                    "evidence",
                    str(row.get("positionId") or ""),
                    _identity_timestamp(
                        row.get("closeTimestamp") or row.get("closeTime") or ""),
                    _identity_number(_api_value(
                        row, "units", "Units", "closedUnits", "ClosedUnits",
                        default="")),
                    _identity_number(_api_value(
                        row, "closeRate", "rate", "CloseRate", "Rate",
                        default="")),
                )
            unique[identity] = row
        result = {
            "rows": [dict(x) for x in unique.values()],
            "account_fingerprint": self.account_fingerprint(),
            "environment": "DEMO" if self.paper else "LIVE",
            "snapshot_at": datetime.now(timezone.utc).isoformat(),
            "complete": bool(complete),
            "truncated": not bool(complete),
            "stop_reason": stop_reason,
            "pages_read": pages_read,
            "max_pages": page_limit,
            "min_date": key[0],
        }
        self._history_cache[key] = (time.monotonic(), dict(result))
        return {**result, "rows": [dict(x) for x in result["rows"]]}

    def trade_history(self, min_date: str, *, max_pages: int = 12) -> list[dict]:
        """Kompatibilitäts-API; Metadaten liefert ``trade_history_snapshot``."""
        return list(self.trade_history_snapshot(
            min_date, max_pages=max_pages, force=False)["rows"])

    def _history_window(self) -> tuple[str, int]:
        """Kleines Normalfenster, beim ersten Lauf aber restart-sicher.

        Ein Close kann waehrend einer mehrtaegigen Offlinephase entstanden
        sein. Darum beginnt der erste erfolgreiche Lauf beim aeltesten noch
        offenen, exakt kontogebundenen eToro-Trade (mit konfigurierter harter
        Obergrenze). Alle weiteren Polls lesen nur das kurze Ueberlappungs-
        fenster. Ein API-Fehler setzt den Einmallauf nicht auf erledigt.
        """
        normal_days = min(364, max(2, int(getattr(
            config, "ETORO_HISTORY_LOOKBACK_DAYS", 7))))
        normal_date = date.today() - timedelta(days=normal_days)
        if self._history_recovery_complete:
            return normal_date.isoformat(), int(getattr(
                config, "ETORO_HISTORY_MAX_PAGES", 12))

        max_days = min(364, max(normal_days, int(getattr(
            config, "ETORO_HISTORY_RECOVERY_MAX_DAYS", 364))))
        lower_bound = date.today() - timedelta(days=max_days)
        earliest = normal_date
        try:
            import trade_ledger
            account = self.account_fingerprint()
            for row in trade_ledger.offene_trades("etoro"):
                stored_account = str(
                    row.get("broker_account_fingerprint") or "")
                # Alte, kontolose Zeilen sind kein Beweis fuer dieses Konto
                # und duerfen keine unbeschraenkte Historienabfrage ausloesen.
                if not account or stored_account != account:
                    continue
                raw = str(row.get("eingestiegen_am") or "")
                try:
                    opened = datetime.fromisoformat(
                        raw.replace("Z", "+00:00")).date()
                except (TypeError, ValueError):
                    continue
                earliest = min(earliest, max(lower_bound, opened))
        except Exception as exc:
            logger.debug("eToro-Startcursor nicht aus Ledger ableitbar: %s", exc)
        return earliest.isoformat(), int(getattr(
            config, "ETORO_HISTORY_RECOVERY_MAX_PAGES", 60))

    def ack_history_recovery_committed(
            self, committed_fill_ids=()) -> bool:
        """Grossen History-Lauf erst nach dauerhaftem Consumer-Commit quittieren.

        Der Orchestrator darf diese Methode ausschliesslich aufrufen, nachdem
        alle vom zugehoerigen ``fills()``-Aufruf gelieferten Belege erfolgreich
        durch FillTracker und Ledger verarbeitet oder dort bereits dedupliziert
        wurden. Eine leere ID-Liste quittiert deshalb nur eine nachweislich leere
        vollstaendige History. Ohne vollstaendigen Brokerlauf oder ohne alle
        erwarteten Fill-IDs bleibt die Methode fail-closed wirkungslos.
        """
        committed = {
            str(value or "").strip() for value in (committed_fill_ids or ())
            if str(value or "").strip()
        }
        with self._history_state_lock:
            if not self._history_recovery_pending_ack:
                return False
            if not self._history_recovery_expected_fill_ids.issubset(committed):
                return False
            self._history_recovery_complete = True
            self._history_recovery_pending_ack = False
            self._history_recovery_expected_fill_ids.clear()
            return True

    # --------------------------- Fills ---------------------------------
    def fills(self) -> list:
        account_fingerprint = self._require_bound_identity("Fill-Abgleich")
        queued = self._fill_queue[:]
        self._fill_queue.clear()
        emitted_close_ids = {
            str(getattr(fill, "fill_id", "") or "")
            for fill in queued
            if str(getattr(fill, "side", "") or "").upper().startswith("SELL")
            and str(getattr(fill, "fill_id", "") or "")
        }

        def _queue_close(fill: Fill, detail: dict) -> bool:
            """Close-Info und History innerhalb eines Polls exakt deduplizieren."""
            fid = str(fill.fill_id or "")
            if not fid or fid in emitted_close_ids:
                return False
            # Fill ist bewusst brokerneutral, besitzt aber kein statisches
            # Detailfeld. Dataclasses ohne slots erlauben diese unveraenderliche
            # Diagnosekopie; der zentrale Consumer kann sie fuer Audit/Outbox
            # verwenden, ohne erneut Brokerdaten laden zu muessen.
            fill.broker_detail = MappingProxyType(deepcopy(dict(detail or {})))
            from execution_lifecycle import observe_etoro_exit
            observe_etoro_exit(fill, environment="DEMO" if self.paper else "LIVE",
                               instrument=_api_value(detail, "instrumentId", "instrumentID", default=""))
            queued.append(fill)
            emitted_close_ids.add(fid)
            return True

        # Persistierte Close-Intents nach einem Neustart ueber den offiziellen
        # Close-Order-Endpunkt fortschreiben. Kein zweiter POST wird erzeugt.
        now = time.monotonic()
        if now - self._last_close_order_poll >= float(getattr(
                config, "ETORO_CLOSE_ORDER_POLL_SECONDS", 10.0)):
            self._last_close_order_poll = now
            try:
                import broker_exit_journal
                broker_exit_journal.replay_cancelled_projections(
                    account_fingerprint=account_fingerprint,
                    environment="DEMO" if self.paper else "LIVE")
                for intent in broker_exit_journal.active(
                        broker="etoro",
                        account_fingerprint=account_fingerprint):
                    try:
                        intent_id = str(intent.get("intent_id") or "")
                        expected_environment = "DEMO" if self.paper else "LIVE"
                        if (str(intent.get("environment") or "").upper()
                                not in {"", expected_environment}):
                            logger.error(
                                "eToro-Close-Intent %s gehoert zu %s statt %s",
                                intent_id, intent.get("environment"),
                                expected_environment)
                            continue
                        expected_pid = str(intent.get("position_id") or "")
                        expected_iid = str(intent.get("instrument_id") or "")
                        oid = str(intent.get("broker_order_id") or "")
                        if not oid:
                            detail = {
                                "recovery": "WAITING_FOR_BROKER_HISTORY",
                                "reason": ("Close-POST besitzt noch keine "
                                           "broker_order_id; kein zweiter POST"),
                                "position_id": expected_pid,
                                "client_order_id": str(
                                    intent.get("client_order_id") or ""),
                            }
                            broker_exit_journal.update(
                                intent_id, "UNCLEAR", detail=detail)
                            logger.warning(
                                "eToro-Close-Intent %s fuer positionId %s bleibt "
                                "UNCLEAR; Recovery wartet auf Broker-History",
                                intent_id, expected_pid or "?")
                            continue

                        info = self.close_order_info(oid)
                        returned_cid = str(_api_value(info, "CID", "cid", "accountId", default="") or "")
                        if returned_cid and returned_cid != str(self._account_cid or ""):
                            broker_exit_journal.update(intent_id, "UNCLEAR", detail={"validation_error": "Close-Info gehoert zu fremdem Konto"})
                            continue
                        returned_oid = str(_api_value(
                            info, "closeOrderId", "closeOrderID",
                            "closingOrderId", "closingOrderID",
                            "orderId", "orderID", default="") or "")
                        if returned_oid and returned_oid != oid:
                            detail = dict(info)
                            detail["validation_error"] = (
                                f"Close-Info orderId {returned_oid} != {oid}")
                            broker_exit_journal.update(
                                intent_id, "UNCLEAR", broker_order_id=oid,
                                detail=detail)
                            logger.error(
                                "eToro-Close-Info fuer %s meldet fremde orderId %s",
                                oid, returned_oid)
                            continue

                        # Eine nichtleere Positionsliste mit exakter Position,
                        # Instrument, Kurs und Menge ist der Fillbeweis. Ein
                        # HTTP-200 oder eine fremde Zeile ist ausdrücklich keiner.
                        closed_rows = [_normalisiere_positionszeile(x)
                                       for x in (info.get("positions") or [])
                                       if isinstance(x, dict)]
                        # Die gesamte Antwort wird vor dem ersten Fill auf den
                        # Intent-Scope geprueft. Sonst koennte eine gemischte
                        # Brokerantwort erst eine passende Zeile bestaetigen und
                        # die fremde Zeile danach wirkungslos verwerfen.
                        scope_errors = []
                        if not expected_pid:
                            scope_errors.append("Intent ohne positionId")
                        if not expected_iid:
                            scope_errors.append("Intent ohne instrumentId")
                        for row in closed_rows:
                            pid = str(row.get("positionId") or "")
                            row_iid = str(row.get("instrumentId") or
                                          info.get("instrumentId") or "")
                            if not expected_pid or pid != expected_pid:
                                scope_errors.append(
                                    f"positionId {pid or '?'} != "
                                    f"{expected_pid or '?'}")
                            if (not row_iid or not expected_iid or
                                    _identity_number(row_iid) !=
                                    _identity_number(expected_iid)):
                                scope_errors.append(
                                    f"instrumentId {row_iid or '?'} != "
                                    f"{expected_iid or '?'}")
                        if scope_errors:
                            detail = dict(info)
                            detail["validation_error"] = "; ".join(scope_errors)
                            broker_exit_journal.update(
                                intent_id, "UNCLEAR", broker_order_id=oid,
                                detail=detail)
                            logger.error(
                                "eToro-Close-Info %s komplett verworfen: %s",
                                oid, detail["validation_error"])
                            continue

                        completed = False
                        for row in closed_rows:
                            pid = str(row.get("positionId") or "")
                            row_iid = str(row.get("instrumentId") or
                                          info.get("instrumentId") or "")
                            price = _float(_api_value(
                                row, "rate", "closeRate", "Rate", "CloseRate"))
                            qty = abs(_float(_api_value(
                                row, "units", "Units", "closedUnits",
                                "ClosedUnits")))
                            occurred = str(_api_value(
                                row, "occurred", "closeTimestamp", "closeTime",
                                default="") or "")
                            # Request time is never a later execution time.
                            if price <= 0 or qty <= 0 or not occurred:
                                logger.warning(
                                    "eToro-Close-Info %s fuer positionId %s noch "
                                    "ohne belastbaren Kurs/Menge/Ausführungszeit", oid, pid)
                                continue
                            iid = int(_float(row_iid or expected_iid, 0.0))
                            if iid <= 0:
                                logger.error(
                                    "eToro-Close-Info %s fuer %s ohne instrumentId",
                                    oid, pid)
                                continue
                            fid = _close_fill_identity(
                                account_fingerprint, pid, oid, row, occurred)
                            close_detail = {
                                key: value for key, value in dict(info).items()
                                if key != "positions"
                            }
                            close_detail.update(dict(row))
                            close_detail.update({
                                "closeOrderId": oid,
                                "closeRate": price,
                                "closedUnits": qty,
                                "closeTimestamp": occurred,
                                "fillId": fid,
                            })
                            fill = Fill(
                                fill_id=fid, order_id=oid,
                                symbol=self._symbol_for_id(iid), side="SELL",
                                quantity=qty, price=price, currency="USD",
                                asset_type=str((self._instrument_by_id.get(iid, {}) or {}).get(
                                    "_bot_asset_type") or (self._instrument_by_id.get(iid, {}) or {}).get(
                                    "_detected_asset_type") or "unknown"),
                                broker_id=pid, timestamp=occurred,
                                # Legacy close/history fees omit external
                                # commission. This is not a complete fee total.
                                explicit_fees=None,
                                execution_reason=str(
                                    row.get("closeReason") or
                                    row.get("closingReason") or
                                    row.get("closeType") or
                                    row.get("exitReason") or ""),
                                raw_fill_id=_close_execution_id(row) or fid,
                                account_fingerprint=account_fingerprint,
                            )
                            _queue_close(fill, close_detail)
                            completed = True
                        error_code = int(_float(info.get("errorCode"), 0.0))
                        error_message = str(info.get("errorMessage") or "")
                        if completed:
                            continue
                        # Only an explicit textual broker outcome is a cancel
                        # proof. Numeric statusID=7 is deliberately unclassified.
                        raw_status = info.get("status")
                        status_name = str((raw_status.get("name") if isinstance(raw_status, dict)
                                           else raw_status if isinstance(raw_status, str) else "")
                                          or info.get("statusName") or "").upper()
                        if (returned_oid == oid and returned_cid == str(self._account_cid or "")
                                and status_name in {"CANCELED", "CANCELLED"}
                                and info.get("positions") == [] and not error_code and not error_message):
                            broker_exit_journal.confirm_cancelled(
                                account_fingerprint=account_fingerprint,
                                environment=expected_environment, intent_id=intent_id,
                                broker_order_id=oid, detail=info)
                            continue
                        if error_code or error_message:
                            # A lookup error does not prove rejection of the
                            # already accepted close. Keep read-only recovery
                            # active; only exact executions settle this intent.
                            broker_exit_journal.update(
                                intent_id, "UNCLEAR",
                                broker_order_id=oid, detail=info)
                        else:
                            broker_exit_journal.update(
                                intent_id, "SUBMITTED",
                                broker_order_id=oid, detail=info)
                    except VerbindungVerloren as exc:
                        # Auch ein Transportfehler fuer einen einzelnen alten
                        # Intent darf spaetere Intents desselben Kontos nicht
                        # verhungern lassen. Der Intent bleibt aktiv und wird im
                        # naechsten Poll erneut ueber GET/History abgeglichen;
                        # ein Close-POST wird dabei nie wiederholt.
                        logger.warning(
                            "eToro-Close-Intent %s voruebergehend nicht "
                            "erreichbar; weitere Intents laufen weiter: %s",
                            intent.get("intent_id") or "?", exc)
                        continue
                    except Exception as exc:
                        # Ein alter/defekter Intent darf die folgenden Intents
                        # desselben Kontos nicht vom Abgleich ausschliessen.
                        logger.warning(
                            "eToro-Close-Intent %s konnte nicht abgeglichen "
                            "werden; weitere Intents laufen weiter: %s",
                            intent.get("intent_id") or "?", exc,
                            exc_info=True)
            except VerbindungVerloren:
                raise
            except Exception as exc:
                logger.warning("eToro-Close-Order-Abgleich nicht verfuegbar: %s", exc)
        # Exact reconciled BUY executions survive process restarts. Repeated
        # delivery is safe because the persistent FillTracker deduplicates the
        # stable fill_id before accounting.
        try:
            from etoro_reconciliation import recovered_buy_fills
            current_ids = (self.current_position_ids(force=False)
                           if self._current_position_ids_known else set())
            queued.extend(recovered_buy_fills(
                paper=bool(self.paper), current_position_ids=current_ids,
                account_fingerprint=account_fingerprint))
        except Exception as exc:
            logger.warning("Persistierte eToro-Kauffills konnten nicht gelesen werden: %s", exc)
        # Ein ueberlappender Historiencursor macht SELL-Accounting auch ueber
        # Mitternacht und laengere Ausfaelle hinweg restart-sicher. Die
        # persistente Fill-Deduplizierung quittiert bereits verbuchte Zeilen.
        now = time.monotonic()
        if now - self._last_history_poll >= float(getattr(
                config, "ETORO_TRADE_HISTORY_POLL_SECONDS", 60.0)):
            self._last_history_poll = now
            recovery_scan = not bool(self._history_recovery_complete)
            if recovery_scan:
                with self._history_state_lock:
                    self._history_recovery_pending_ack = False
                    self._history_recovery_expected_fill_ids.clear()
            try:
                min_date, max_pages = self._history_window()
                history = self.trade_history_snapshot(
                    min_date, max_pages=max_pages, force=False)
                rows = list(history.get("rows") or [])
                processed_ok = True
                recovery_fill_ids: set[str] = set()
                for row in rows:
                    if not bool(row.get("isBuy", True)):
                        continue  # nur Long-Positionen des Bots
                    pid = str(row.get("positionId") or "")
                    ts = str(row.get("closeTimestamp") or "")
                    price = _float(row.get("closeRate"))
                    qty = abs(_float(row.get("units")))
                    # Ein Close ohne Preis ist noch kein abrechenbarer Fill.
                    # Nicht quittieren: beim naechsten Historienlauf kann eToro
                    # die endgueltigen Werte nachliefern.
                    if not pid or price <= 0 or qty <= 0:
                        processed_ok = False
                        logger.warning(
                            "eToro-History-Close noch unvollstaendig "
                            "(positionId=%s, Preis=%s, Menge=%s); Recovery-Cursor "
                            "bleibt offen", pid or "?", price, qty)
                        continue
                    # Beim Trade-History-Endpunkt ist ``orderId`` die
                    # ursprüngliche ENTRY-Order. 9.5 schrieb sie irrtümlich
                    # als SELL/exit_order_id. Nur explizite Close-Felder sind
                    # ein echter Close-Order-Anker.
                    entry_order_id = str(row.get("orderId") or "")
                    close_order_id = str(_api_value(
                        row,
                        "closeOrderId", "closeOrderID", "CloseOrderId",
                        "closingOrderId", "closingOrderID", "ClosingOrderId",
                        default="") or "")
                    # ``orderId`` der Trade-History ist die ENTRY-Order. Wenn
                    # eToro keine ausdrueckliche Close-Order-ID liefert, darf
                    # deshalb auch kein synthetischer Wert als Brokerorder in
                    # Ledger/Reconciliation gelangen. Die stabile lokale
                    # Idempotenz lebt ausschliesslich in ``fill_id``.
                    close_detail = dict(row)
                    close_detail["entry_order_id"] = entry_order_id
                    close_detail["close_order_id"] = close_order_id
                    fid = _close_fill_identity(
                        account_fingerprint, pid,
                        close_order_id, row, ts)
                    recovery_fill_ids.add(fid)
                    close_detail["fillId"] = fid
                    iid = int(row.get("instrumentId") or 0)
                    symbol = self._symbol_for_id(iid)
                    fill = Fill(
                        fill_id=fid,
                        order_id=close_order_id, symbol=symbol,
                        side="SELL", quantity=qty,
                        price=price, currency="USD",
                        asset_type=str((self._instrument_by_id.get(iid,{}) or {}).get("_bot_asset_type") or (self._instrument_by_id.get(iid,{}) or {}).get("_detected_asset_type") or "unknown"),
                        broker_id=pid, timestamp=ts,
                        explicit_fees=None,  # external closing costs not supplied by history
                        execution_reason=str(
                            row.get("closeReason") or row.get("closingReason")
                            or row.get("closeType") or row.get("exitReason") or ""),
                        raw_fill_id=_close_execution_id(row) or fid,
                        account_fingerprint=account_fingerprint,
                    )
                    _queue_close(fill, close_detail)
                if (bool(history.get("complete")) and processed_ok
                        and getattr(self, "_pnl_snapshot", None)):
                    # Run even for already consumed history fills. This repairs
                    # pre-10.1.3 position-only FILLED projections after restart,
                    # without fabricating a close-order receipt or a new POST.
                    try:
                        import broker_exit_journal
                        broker_exit_journal.reconcile_position_closures(
                            account_fingerprint=account_fingerprint,
                            environment="DEMO" if self.paper else "LIVE",
                            snapshot=self.position_snapshot(force=False), history=history)
                    except Exception as exc:
                        logger.warning("eToro-Auftrags-/Positionsabschluss getrennt noch nicht belegbar: %s", exc)
                # Ein vollstaendiger Brokerlauf darf den Cursor lediglich zur
                # spaeteren Quittierung vormerken. Das eigentliche Umschalten
                # erfolgt erst, nachdem FillTracker und Ledger alle gelieferten
                # Belege dauerhaft committed haben.
                if (recovery_scan and bool(history.get("complete"))
                        and processed_ok):
                    with self._history_state_lock:
                        # Ein paralleles Consumer-Ack kann waehrend dieses
                        # redundanten Scans bereits den vorherigen Recovery-
                        # Lauf abgeschlossen haben. In dem Fall keinen zweiten
                        # Pending-Ack hinter dem fertigen Cursor erzeugen.
                        if not self._history_recovery_complete:
                            self._history_recovery_pending_ack = True
                            self._history_recovery_expected_fill_ids = set(
                                recovery_fill_ids)
            except VerbindungVerloren:
                raise
            except Exception as exc:
                logger.warning("eToro Trade-History konnte nicht gelesen werden: %s", exc)
        return queued

    # --------------------------- Kosten --------------------------------
    def dynamic_close_cost_quote(self, instrument, position_id: str, quantity: float) -> dict:
        """Current position-bound what-if estimate, never an opening-cost proxy.

        The published documentation is contradictory: its summary describes
        close, while the parameter text still says close is unsupported. A
        rejected/unavailable result therefore stays UNKNOWN with a cooldown.
        This POST only asks for costs; it does not create/modify an order.
        """
        from etoro_exit_costs import number, parse_close_costs
        qty = number(quantity, positive=True)
        pid = str(position_id or "")
        if not pid.isdigit() or int(pid) <= 0:
            raise BrokerFehler("Exakte Position fuer Verkaufskosten fehlt")
        r = self._resolve(instrument)
        key = (self.account_fingerprint(), "DEMO" if self.paper else "LIVE", pid, qty)
        failed = getattr(self, "_close_cost_failure", None)
        if failed and time.monotonic() - failed[0] < 900:
            raise NichtUnterstuetzt("Aktuelle eToro-Verkaufskosten nicht verfuegbar; erneute Pruefung nach 15 Minuten")
        cache = getattr(self, "_close_cost_quotes", {})
        cached = cache.get(key)
        if cached and time.monotonic() - cached[0] <= 15:
            return dict(cached[1])
        try:
            data = self._request("POST", self._path(
                "/api/v2/trading/info/demo/costs", "/api/v2/trading/info/costs"), payload={
                "action": "close", "transaction": "sell", "orderType": "mkt",
                "positionIds": [int(pid)], "units": qty, "orderCurrency": "usd"})
            fetched = datetime.now(timezone.utc)
            result = parse_close_costs(data, instrument_id=r["instrumentId"],
                fetched_at=fetched.isoformat(), now=fetched)
        except (AuthentifizierungsFehler, VerbindungVerloren):
            raise
        except Exception as exc:
            self._close_cost_failure = (time.monotonic(), type(exc).__name__)
            raise NichtUnterstuetzt("Aktuelle eToro-Verkaufskosten nicht vollstaendig belegt: " + type(exc).__name__) from exc
        self._close_cost_quotes = {key: (time.monotonic(), result)}
        return dict(result)

    def dynamic_cost_quote(self, instrument, quantity: float, price: float,
                           *, action: str = "open") -> dict | None:
        if action != "open":
            raise NichtUnterstuetzt("eToro Cost-Quote fuer Pre-Trade wird derzeit fuer action=open verwendet")
        r = self._resolve(instrument)
        settlement = self._settlement(instrument)
        qty = abs(float(quantity))
        key = (r["instrumentId"], round(qty, 8), settlement.lower(), "open")
        cached = self._cost_cache.get(key)
        ttl = max(5, int(getattr(config, "ETORO_COST_CACHE_SECONDS", 300)))
        if cached and time.monotonic() - cached[0] < ttl:
            return cached[1]
        path = self._path("/api/v2/trading/info/demo/costs", "/api/v2/trading/info/costs")
        data = self._request("POST", path, payload={
            "action": "open", "transaction": "buy", "instrumentId": r["instrumentId"],
            "settlementType": settlement, "orderType": "mkt", "leverage": 1,
            "units": qty, "orderCurrency": "usd",
        })
        comp = {}
        for item in data.get("costs") or []:
            # eToro dokumentiert camelCase-Namen; intern normalisieren wir auf
            # lowercase, damit API-Schreibweisen die Kostenpruefung nicht
            # unbemerkt auf 0 fallen lassen koennen.
            key_name = str(item.get("costType") or "").strip().lower()
            if key_name:
                comp[key_name] = comp.get(key_name, 0.0) + abs(_float(item.get("amount")))
        result = {
            "symbol": r["symbol"], "instrument_id": r["instrumentId"],
            "settlement_type": settlement, "components": comp,
            "currency": (data.get("costs") or [{}])[0].get("currency", "USD") if data.get("costs") else "USD",
            "last_updated": data.get("lastUpdated") or datetime.now(timezone.utc).isoformat(),
            "source": "eToro Public API what-if costs",
        }
        self._cost_cache[key] = (time.monotonic(), result)
        best_effort_json(Path(__file__).resolve().parent.parent / "etoro_cost_status.json", result,
                         label="eToro-Kostenstatus")
        return result

    # --------------------------- Orders --------------------------------
    def _lookup_order(self, *, order_id=None, reference_id=None) -> dict:
        """Order exakt nach einem Anker lesen, mit dokumentiertem v1-ID-Fallback.

        ``referenceId`` existiert nur am v2-Lookup. Sobald eine ``orderId``
        bekannt ist, kann jedoch der ältere order-by-id-Endpunkt belastbarer
        sein. Die Anker werden nie in einer Anfrage kombiniert.
        """
        if order_id in (None, "") and reference_id in (None, ""):
            raise BrokerFehler("eToro-Order-Lookup ohne exakten Anker")
        v2_path = self._path(
            "/api/v2/trading/info/demo/orders:lookup",
            "/api/v2/trading/info/orders:lookup")
        params = ({"orderId": order_id} if order_id not in (None, "")
                  else {"referenceId": reference_id})
        v2_error = None
        try:
            data = self._request("GET", v2_path, params=params)
            if isinstance(data, dict) and data:
                return data
        except AuthentifizierungsFehler:
            raise
        except BrokerFehler as exc:
            v2_error = exc

        # Ein referenceId-Lookup besitzt keinen sicheren v1-Ersatz.
        if order_id in (None, ""):
            if v2_error is not None:
                raise v2_error
            return {}

        oid = str(order_id).strip()
        v1_path = self._path(
            f"/api/v1/trading/info/demo/orders/{oid}",
            f"/api/v1/trading/info/orders/{oid}")
        try:
            data = self._request("GET", v1_path)
        except AuthentifizierungsFehler:
            raise
        except BrokerFehler as fallback_error:
            if v2_error is not None:
                raise BrokerFehler(
                    "eToro-Order weder ueber v2-Lookup noch v1-order-by-id "
                    f"lesbar: v2={v2_error}; v1={fallback_error}") from fallback_error
            raise
        if not isinstance(data, dict):
            return {}
        # Je nach Rollout liefert der Endpunkt die Order direkt oder in einem
        # kleinen Wrapper. Nie eine andere Order aus einer Liste uebernehmen.
        candidates = []
        for key in ("order", "item"):
            if isinstance(data.get(key), dict):
                candidates.append(data[key])
        for key in ("orders", "items"):
            if isinstance(data.get(key), list):
                candidates.extend(x for x in data[key] if isinstance(x, dict))
        if not candidates:
            candidates = [data]
        for candidate in candidates:
            candidate = _normalisiere_positionszeile(candidate)
            returned_id = str(candidate.get("orderId") or "")
            if not returned_id or returned_id == oid:
                return candidate
        return {}

    def close_order_info(self, order_id: str) -> dict:
        """Offiziellen Status eines eToro-Schliessauftrags lesen."""
        oid = str(order_id or "").strip()
        if not oid:
            return {}
        path = self._path(
            f"/api/v1/trading/info/demo/close-orders/{oid}",
            f"/api/v1/trading/info/close-orders/{oid}")
        data = self._request("GET", path)
        if isinstance(data, dict):
            try:
                from etoro_settlement_review import observe_proceeds
                observe_proceeds(account=self.account_fingerprint(), paper=self.paper,
                    order_id=oid, raw=data)
            except Exception as exc:
                logger.debug("Erlösbeleg derzeit nicht speicherbar: %s", type(exc).__name__)
        return _normalisiere_positionszeile(data) if isinstance(data, dict) else {}

    def _wait_open_order(self, *, order_id=None, reference_id=None, timeout=15.0) -> dict:
        deadline = time.monotonic() + max(2.0, timeout)
        last = {}
        while time.monotonic() < deadline:
            for key, value in (("order_id", order_id), ("reference_id", reference_id)):
                if value in (None, ""):
                    continue
                try:
                    # Official API anchors are deliberately separate requests.
                    candidate = self._lookup_order(**{key: value})
                    if candidate:
                        last = candidate
                        break
                except BrokerFehler:
                    # A fresh accepted order can transiently return 404. Retry
                    # with pacing and then try the independent referenceId.
                    continue
            status = last.get("status") or {}
            sid = int(status.get("id") or 0)
            if sid in (3, 4, 7, 9, 10):
                return last
            time.sleep(0.6)
        return last

    def _submit_primary_request(self, path, *, payload, request_id, quantity,
                                instrument_id, side, position_id=""):
        from execution_lifecycle import reserve, accepted, rejected
        key = reserve(broker="etoro",account=self.account_fingerprint(),
            environment="DEMO" if self.paper else "LIVE",instrument=str(instrument_id),
            side=side,client_id=request_id,quantity=quantity,request=payload,
            position_id=str(position_id))
        if side == "BUY":
            try:
                from pulsar.control import submit_plan
                context = dict(getattr(self, "_nexus_submit_context", {}) or {})
                plan = submit_plan(context.get("decision_id"), self.account_fingerprint(),
                                   "DEMO" if self.paper else "LIVE") if context.get("decision_id") else None
                if plan and (float(payload.get("limitRate") or 0) > plan["max_entry_price"]
                             or quantity != plan["quantity"]):
                    raise ValueError("PULSAR-Preis-/Mengengrenze vor Versand verletzt")
            except Exception as exc:
                rejected(key, proven=True)
                raise AuftragAbgelehnt(str(exc)) from exc
        try:
            response = self._request("POST",path,payload=payload,request_id=request_id,safe_retry=False)
        except Exception as exc:
            proven = isinstance(exc, (AuthentifizierungsFehler, AuftragAbgelehnt))
            try:
                rejected(key, proven=proven)
            except Exception as persist_error:
                raise OrderStatusUnklar("eToro-Antwort nicht dauerhaft gespeichert; Abgleich erforderlich.",
                    reference_id=request_id, accepted=True) from persist_error
            if proven:
                raise
            raise OrderStatusUnklar("eToro-Uebermittlung unklar; kein zweiter POST.",
                reference_id=request_id,accepted=True) from exc
        try:
            row = response if side=="BUY" else (response.get("orderForClose") or {})
            oid = _api_value(row,"orderId","orderID","OrderId","OrderID",default="")
            if not oid and side == "SELL" and (response.get("errorCode") or response.get("errorMessage")):
                rejected(key, proven=True)
            else:
                accepted(key,str(oid or ""))
        except Exception as exc:
            raise OrderStatusUnklar("eToro-Antwort nach Uebermittlung nicht dauerhaft zugeordnet.",
                reference_id=request_id,accepted=True) from exc
        return response

    def kaufe_mit_absicherung(self, instrument, menge: float, referenzpreis: float,
                              stop: float, take_profit: float) -> OrderErgebnis:
        if not self._connected:
            raise NichtVerbunden("eToro nicht verbunden")
        r = self._resolve(instrument)
        settlement = self._settlement(instrument)
        qty = abs(float(menge))
        if qty <= 0 or stop <= 0 or take_profit <= 0:
            raise BrokerFehler("eToro Kauf: Menge/Stop/Take-Profit ungueltig")
        reference = float(referenzpreis)
        if reference <= 0 or float(stop) >= reference:
            raise BrokerFehler(
                "eToro Kauf: Referenzpreis/Stop erlauben keinen Long-Risikodeckel")
        max_slippage = float(getattr(
            config, "ETORO_MAX_ENTRY_SLIPPAGE_PCT", 0.003))
        risk_multiplier = float(getattr(
            config, "ETORO_MAX_ENTRY_RISK_MULTIPLIER", 1.10))
        if (not math.isfinite(max_slippage) or max_slippage < 0
                or not math.isfinite(risk_multiplier)
                or risk_multiplier <= 0):
            raise BrokerFehler("eToro Entry-Cap-Konfiguration ist ungueltig")
        # Zwei unabhaengige Grenzen: maximal tolerierte Preisabweichung und
        # maximal tolerierte Verbreiterung des absoluten Stop-Risikos.
        slippage_cap = reference * (1.0 + max_slippage)
        risk_cap = float(stop) + (reference - float(stop)) * risk_multiplier
        entry_cap = min(slippage_cap, risk_cap)
        context = dict(getattr(self, "_nexus_submit_context", {}) or {})
        from pulsar.control import submit_plan, Blocked as PulsarBlocked
        pulsar_plan = submit_plan(context.get("decision_id"), self.account_fingerprint(),
                                 "DEMO" if self.paper else "LIVE") if context.get("decision_id") else None
        if pulsar_plan:
            if (str(pulsar_plan["instrument_id"]) != str(r["instrumentId"])
                    or qty != pulsar_plan["quantity"] or float(stop) != pulsar_plan["stop"]
                    or float(take_profit) != pulsar_plan["take"]):
                raise AuftragAbgelehnt("PULSAR-Menge/Instrument/Schutz passt nicht zur Freigabe")
            entry_cap = min(entry_cap, pulsar_plan["max_entry_price"])
        if not math.isfinite(entry_cap) or entry_cap <= 0:
            raise BrokerFehler("eToro Entry-Cap konnte nicht sicher berechnet werden")
        self._validate_protection(instrument, settlement, referenzpreis, stop, take_profit)
        if bool(getattr(config, "ETORO_REQUIRE_LIVE_PROTECTION_QUOTE", True)):
            # Der Signalpreis kann mehrere Sekunden alt sein. eToro validiert
            # SL/TP aber gegen den Preis bei Orderannahme; genau daraus kamen
            # die protokollierten Min-pip-Ablehnungen. Unmittelbar vor dem POST
            # wird daher gegen einen frischen Brief-/Letzten Kurs erneut
            # fail-closed geprueft, ohne den Strategie-Stop still zu veraendern.
            live_row = self._rate_row(instrument, force=True)
            live_ask = _float((live_row or {}).get("ask"))
            live_reference = live_ask or _float(
                (live_row or {}).get("lastExecution"))
            if live_reference <= 0:
                raise BrokerFehler(
                    "eToro Kauf blockiert: frischer Schutz-Referenzkurs nicht abrufbar")
            if live_ask > entry_cap + max(1e-9, entry_cap * 1e-9):
                # 9.5.4: Die Meldung nennt jetzt alle Zahlen und vor allem,
                # WELCHE der beiden Grenzen gebunden hat. Ohne das liess sich
                # nicht unterscheiden, ob ein Einstieg an normaler
                # Kursbewegung scheiterte (Slippage-Grenze, anhebbar) oder am
                # Stop-Risiko (Risiko-Grenze, bewusst nicht anhebbar).
                bindend = ("Slippage-Grenze" if slippage_cap <= risk_cap
                           else "Risiko-Grenze")
                abweichung = (live_ask / reference - 1.0) * 100.0
                raise BrokerFehler(
                    "eToro Kauf blockiert: aktueller Ask "
                    f"{live_ask:.8f} liegt ueber dem risikobegrenzten "
                    f"limitIOC-Preisdeckel {entry_cap:.8f} "
                    f"(Referenz {reference:.8f}, Abweichung {abweichung:+.3f}%, "
                    f"bindend: {bindend}; Slippage-Deckel {slippage_cap:.8f}, "
                    f"Risiko-Deckel {risk_cap:.8f})")
            self._validate_protection(
                instrument, settlement, live_reference, stop, take_profit)
        # Kostenquelle vor Order zwingend ansprechen. Dadurch bleibt der Kauf
        # fail-closed, wenn eToro seine aktuell gueltigen Kosten nicht liefert.
        if bool(getattr(config, "ETORO_REQUIRE_COST_QUOTE", True)):
            self.dynamic_cost_quote(instrument, qty, referenzpreis, action="open")
        if pulsar_plan:
            from cost_engine import estimate_roundtrip_with_broker
            costs = estimate_roundtrip_with_broker(self, qty, entry_cap, instrument=instrument,
                                                  currency=instrument.currency)
            if costs.total_cost > pulsar_plan["cost_budget"]:
                raise AuftragAbgelehnt("PULSAR-Kosten vor Uebermittlung ueber dem freigegebenen Budget")
        reqid = str(uuid.uuid4())
        context = dict(getattr(self, "_nexus_submit_context", {}) or {})
        if context.get("decision_id"):
            from etoro_reconciliation import register_reference
            register_reference(int(context["decision_id"]), reqid)
        payload = {
            "action": "open", "transaction": "buy", "instrumentId": r["instrumentId"],
            "settlementType": settlement, "orderType": "limitIOC",
            "limitRate": float(entry_cap), "leverage": 1,
            "units": qty, "orderCurrency": "usd", "stopLossRate": float(stop),
            "takeProfitRate": float(take_profit), "stopLossType": "fixed",
        }
        # v3 antwortet bewusst asynchron mit HTTP 202. Die Ausfuehrung wird
        # danach ausschliesslich ueber den offiziellen v2-Lookup bewiesen.
        path = self._path("/api/v3/trading/execution/demo/orders", "/api/v3/trading/execution/orders")
        decision_id = int(context.get("decision_id") or 0)
        if decision_id:
            # Diese persistente Kante liegt unmittelbar VOR dem ersten Byte des
            # POST. Ab hier ist jede lokale Exception potentiell eine bereits
            # beim Broker angekommene Order und darf nie als sicher FAILED oder
            # erneut sendbar behandelt werden.
            from etoro_reconciliation import mark_post_attempted
            mark_post_attempted(decision_id, reference_id=reqid)
        try:
            accepted = self._submit_primary_request(path,payload=payload,request_id=reqid,
                quantity=qty,instrument_id=r["instrumentId"],side="BUY")
        except (AuthentifizierungsFehler, AuftragAbgelehnt):
            raise
        except Exception as exc:
            # NIEMALS blind erneut POSTen. eToro dokumentiert referenceId als
            # Lookup-Anker, falls die Antwort, ihr JSON oder der Transport nach
            # Sendebeginn verloren ging. Auch HTTP 202 + kaputtes JSON landet
            # deshalb im nichtterminalen Ambiguitaetszustand.
            try:
                time.sleep(1.0)
                accepted = self._lookup_order(reference_id=reqid)
                if not isinstance(accepted, dict) or not accepted:
                    raise BrokerFehler("referenceId noch nicht sichtbar")
            except Exception as lookup_exc:
                raise OrderStatusUnklar(
                    "eToro Kaufstatus nach begonnenem POST unklar; Order NICHT "
                    f"wiederholt. referenceId={reqid}: submit={exc}; "
                    f"lookup={lookup_exc}",
                    reference_id=reqid,
                    accepted=True,
                    profile=str(context.get("profile") or ""),
                    intent=payload,
                ) from exc
        if not isinstance(accepted, dict):
            raise OrderStatusUnklar(
                "eToro Kauf-POST lieferte keine auswertbare Antwort; Order "
                "NICHT wiederholen.", reference_id=reqid, accepted=True,
                profile=str(context.get("profile") or ""), intent=payload)
        oid = _api_value(
            accepted, "orderId", "orderID", "OrderId", "OrderID")
        returned_reference = str(_api_value(
            accepted, "referenceId", "referenceID", "ReferenceId",
            "ReferenceID", default="") or "")
        if returned_reference and returned_reference != reqid:
            mismatch = (
                "eToro antwortete auf den Kauf-POST mit einer anderen "
                f"referenceId (gesendet={reqid}, empfangen={returned_reference}); "
                "keine automatische Zuordnung.")
            if decision_id:
                from etoro_reconciliation import mark_execution_anomaly
                mark_execution_anomaly(
                    decision_id, "REFERENCE_ID_MISMATCH", mismatch)
            raise OrderStatusUnklar(
                mismatch, reference_id=reqid,
                order_ids=[str(oid)] if oid else [], accepted=True,
                profile=str(context.get("profile") or ""), intent=payload)
        broker_reference = returned_reference or reqid
        if context.get("decision_id"):
            from etoro_reconciliation import accepted as persist_accepted
            persist_accepted(int(context["decision_id"]), order_id=str(oid or ""), reference_id=broker_reference)
        try:
            data = self._wait_open_order(order_id=oid, reference_id=broker_reference)
            if not data:
                raise OrderStatusUnklar(
                    "eToro Order nach Annahme noch nicht auffindbar; NICHT wiederholen",
                    reference_id=broker_reference, order_ids=[oid] if oid else [], accepted=True,
                    profile=str(context.get("profile") or ""), intent=payload)
        except (VerbindungVerloren, BrokerFehler) as exc:
            if isinstance(exc, OrderStatusUnklar):
                raise
            raise OrderStatusUnklar(
                f"eToro Order nach Annahme nicht abschliessend lesbar; NICHT wiederholen. referenceId={reqid}: {exc}",
                reference_id=broker_reference, order_ids=[oid] if oid else [], accepted=True,
                profile=str(context.get("profile") or ""), intent=payload,
            ) from exc
        status = data.get("status") or {}
        sid = int(status.get("id") or 0)
        executions = [_normalisiere_positionszeile(ex)
                      for ex in (data.get("positionExecutions") or [])
                      if isinstance(ex, dict)]
        filled = 0.0; value = 0.0; fees = 0.0
        pending_fills: list[Fill] = []
        for ex in executions:
            op = ex.get("openingData") or {}
            # remainingUnits is the unfilled rest, never an executed quantity.
            q = abs(_float(op.get("units") or ex.get("filledUnits")))
            px = _float(op.get("avgPrice"))
            if q > 0 and px > 0:
                filled += q; value += q * px
                fees += abs(_float(op.get("fees"))) + abs(_float(op.get("taxes")))
                pending_fills.append(Fill(
                    fill_id=(f"etoro:{self.account_fingerprint() or 'unresolved'}:open:"
                             f"{oid or reqid}:{ex.get('positionId')}:"
                             f"{op.get('executionTime')}"),
                    order_id=str(oid or reqid), symbol=str(r.get("symbolFull") or r["symbol"]).upper(), side="BUY", quantity=q,
                    price=px, currency="USD", asset_type=getattr(instrument, "asset_type", "stock"),
                    broker_id=str(ex.get("positionId") or ""), timestamp=op.get("executionTime"),
                    explicit_fees=_known_fees(op, "fees", "taxes"),
                    raw_fill_id=str(op.get("executionId") or ""),
                    account_fingerprint=self.account_fingerprint(),
                ))
        avg = value / filled if filled > 0 else 0.0
        max_fill_price = max(
            (float(item.price) for item in pending_fills), default=avg)
        if (filled > 0 and
                max_fill_price > entry_cap + max(1e-9, entry_cap * 1e-9)):
            anomaly = (
                "eToro meldet einen Fill oberhalb des zuvor gesendeten "
                f"limitIOC-Preisdeckels (Fill {max_fill_price:.8f}, "
                f"Durchschnitt {avg:.8f}, Cap {entry_cap:.8f}). "
                "Position bleibt zur manuellen Pruefung gesperrt.")
            if decision_id:
                from etoro_reconciliation import (
                    apply_broker_evidence, mark_execution_anomaly,
                )
                mark_execution_anomaly(
                    decision_id, "ENTRY_FILL_ABOVE_PRICE_CAP", anomaly)
                # Die exakten Order-/positionIds bleiben trotz Quarantaene
                # erhalten, damit der Brokerbestand sichtbar und schliessbar
                # bleibt; nur die automatische Freigabe wird gesperrt.
                apply_broker_evidence(decision_id, data)
            raise OrderStatusUnklar(
                anomaly, reference_id=broker_reference,
                order_ids=[str(oid)] if oid else [], accepted=True,
                profile=str(context.get("profile") or ""), intent=payload)
        from etoro_order_status import lookup_status
        phase = lookup_status(status)
        if not phase["known"]:
            raise OrderStatusUnklar("eToro-Auftragsstatus unbekannt oder widersprüchlich",
                reference_id=broker_reference, order_ids=[str(oid)] if oid else [], accepted=True,
                profile=str(context.get("profile") or ""), intent=payload)
        sid = phase['code']
        if sid == 7 and filled <= 0:
            if data.get('positionExecutions') != []:
                raise OrderStatusUnklar('Stornierter Kaufauftrag ohne vollständigen Ausführungsbeleg',
                    reference_id=broker_reference, order_ids=[str(oid)] if oid else [], accepted=True,
                    profile=str(context.get('profile') or ''), intent=payload)
            if decision_id:
                from etoro_reconciliation import apply_broker_evidence
                apply_broker_evidence(decision_id, data)
            raise AuftragAbgelehnt("eToro-Kauf ohne Ausführung storniert")
        if sid in (5, 9, 10) and filled <= 0:
            # 5/10 bedeuten laut eToro (Rejected)PartiallyFilled. Fehlen die
            # positionExecutions in genau diesem Lookup, ist das KEIN Beweis
            # fuer null Ausfuehrung, sondern eine zwingend blockierende
            # Rekonstruktion ueber orderId, P&L und History.
            if decision_id:
                from etoro_reconciliation import apply_broker_evidence
                apply_broker_evidence(decision_id, data)
            raise OrderStatusUnklar(
                "eToro meldet eine Teilausführung, liefert aber "
                "noch keine positionExecutions; Kauf bleibt bis zur exakten "
                "Broker-Rekonstruktion gesperrt.",
                reference_id=broker_reference,
                order_ids=[str(oid)] if oid else [], accepted=True,
                profile=str(context.get("profile") or ""), intent=payload)
        if sid == 4 and filled <= 0:
            from execution_lifecycle import reject_reference
            reject_reference(broker="etoro",account=self.account_fingerprint(),
                environment="DEMO" if self.paper else "LIVE",client_id=reqid)
            if context.get("decision_id"):
                from etoro_reconciliation import apply_broker_evidence
                apply_broker_evidence(int(context["decision_id"]), data)
            raise BrokerFehler(f"eToro Kauf abgelehnt: {status.get('errorMessage') or status.get('name')}")
        position_ids=[str(ex.get("positionId")) for ex in executions
                      if ex.get("positionId") is not None]
        result = OrderErgebnis(
            order_ids=[str(oid or reqid)], status=str(status.get("name") or "accepted"),
            filled_quantity=filled, avg_fill_price=avg,
            # Die Orderantwort beweist nur die Ausfuehrung, nicht den aktuell
            # am Broker hinterlegten Schutz. Ruecklesen folgt weiter unten.
            stop_order_platziert=False,
            take_order_platziert=False,
            hinweis=f"eToro {('DEMO' if self.paper else 'LIVE')} · settlement={settlement} · fees={fees:.4f}",
            reference_id=broker_reference, position_ids=position_ids, paper=bool(self.paper),
            requested_quantity=qty, remaining_quantity=max(0.0, qty-filled),
            terminal=bool(sid in (3, 4, 7, 9, 10)), raw_status=str(status.get("name") or ""),
            account_fingerprint=self.account_fingerprint(),
            broker_environment="DEMO" if self.paper else "LIVE",
        )
        if context.get("decision_id"):
            from etoro_reconciliation import (
                apply_broker_evidence, record_for, verify_broker_truth,
            )
            decision_id = int(context["decision_id"])
            apply_broker_evidence(decision_id, data)
            # Fuer die Orderbestaetigung genau EINE frische Snapshot-
            # Generation an alle Beweisschritte weiterreichen. Ein zweiter
            # GET koennte bereits einen anderen Brokerzeitpunkt zeigen.
            broker_snapshot = self.position_snapshot(force=True)
            current_ids = set(broker_snapshot.get("open_ids") or set())
            verify_broker_truth(
                self, paper=bool(self.paper),
                profile=str(context.get("profile") or ""),
                account_fingerprint=self.account_fingerprint(),
                current_position_ids=current_ids,
                position_snapshot=broker_snapshot,
            )
            proof = record_for(decision_id)
            if (not bool(proof.get("position_verified"))
                    or str(proof.get("broker_position_status") or "") != "OPEN_CONFIRMED"):
                raise OrderStatusUnklar(
                    "eToro meldet eine Ausfuehrung, die positionId ist im aktuellen "
                    "Depot aber noch nicht bestaetigt; kein lokaler Trade und kein "
                    "erneuter Kauf.",
                    reference_id=broker_reference, order_ids=[str(oid)] if oid else [],
                    accepted=True, profile=str(context.get("profile") or ""),
                    intent=payload,
                )
        if filled > 0 and position_ids:
            protection = self.reconcile_position_protection(
                instrument, filled, stop, take_profit,
                position_ids=position_ids,
                instrument_id=str(r["instrumentId"]), force_snapshot=False)
            confirmed = bool(protection.get("protection_confirmed"))
            result.stop_order_platziert = confirmed
            result.take_order_platziert = confirmed
            if not confirmed:
                result.hinweis += " · Schutz noch nicht exakt rueckgelesen; Automatik bleibt pausiert"
        # Erst nach dem exakten Depotbeweis in die Geld-/Ledger-Pipeline
        # geben. Ohne Submit-Kontext bleibt das alte Adapterverhalten nur fuer
        # isolierte Diagnoseaufrufe erhalten; produktive Orders tragen immer
        # eine decision_id.
        result.fills = [{"fill_id":f.fill_id,"ordId":f.order_id,
            "instId":str(r["instrumentId"]),"side":"buy","fillSz":f.quantity,
            "fillPx":f.price,"fee":-f.explicit_fees if f.explicit_fees is not None else None,
            "feeCcy":"USD","position_id":f.broker_id,"filled_at":f.timestamp}
            for f in pending_fills]
        result.fill_ids = [f.fill_id for f in pending_fills]
        result.fill_evidence_complete = bool(result.terminal and
            ((filled>0 and pending_fills) or (sid==4 and filled==0)))
        result.fees_quote = sum(f.explicit_fees for f in pending_fills) if all(f.explicit_fees is not None for f in pending_fills) else None
        from execution_lifecycle import observe
        observe(result,broker="etoro",instrument=str(r["instrumentId"]))
        self._fill_queue.extend(pending_fills)
        return result

    def schliesse_position(self, instrument, menge: float, referenzpreis: float = 0.0,
                           *, position_ids=None, snapshot_id: str = "",
                           instrument_id: str = "") -> OrderErgebnis:
        """Nur exakt bewiesene eToro-Position-Lines schliessen."""
        account_fingerprint = self._require_bound_identity("Positionsschliessung")
        r = self._resolve(instrument)
        expected_instrument_id = int(instrument_id or r["instrumentId"])
        target = abs(float(menge))
        if target <= 0:
            return OrderErgebnis(status="keine_menge")
        expected = {str(x) for x in (position_ids or []) if str(x).strip()}
        if not expected:
            raise BrokerFehler("eToro-Verkauf ohne bewiesene positionId blockiert")
        if len(expected) != 1:
            raise BrokerFehler(
                "eToro-Verkauf blockiert: ein lokaler Datensatz muss genau eine "
                "bewiesene positionId adressieren")
        pnl = self._pnl(force=True)
        self._validate_pnl_schema(pnl)
        current_snapshot = str(pnl.get("_snapshot_id") or "")
        rows = [x for x in self._all_positions(pnl)
                if str(x.get("positionId") or "") in expected
                and bool(x.get("isBuy", True))]
        found = {str(x.get("positionId") or "") for x in rows}
        if found != expected:
            raise BrokerFehler(
                "eToro-Verkauf blockiert: nicht alle bewiesenen positionIds "
                f"sind im frischen Depot-Snapshot {current_snapshot or '?'} offen")
        wrong_instrument = [x for x in rows
                            if int(x.get("instrumentId") or 0) != expected_instrument_id]
        if wrong_instrument:
            raise BrokerFehler(
                "eToro-Verkauf blockiert: positionId und Instrument widersprechen sich")
        if len(rows) != 1:
            raise BrokerFehler(
                "eToro-Verkauf blockiert: der frische Depot-Snapshot enthaelt "
                "die bewiesene positionId nicht genau einmal")
        available = sum(abs(_float(x.get("units"))) for x in rows)
        if target > available + max(1e-8, available * 1e-7):
            raise BrokerFehler(
                f"eToro-Verkauf blockiert: Zielmenge {target:g} ist groesser "
                f"als die bewiesene Menge {available:g}")
        remaining = target; order_ids = []
        reference_ids = []
        for row in rows:
            if remaining <= 1e-10:
                break
            pos_units = abs(_float(row.get("units")))
            q = min(pos_units, remaining)
            if q <= 0:
                continue
            pid = int(row.get("positionId"))
            full_tolerance = max(1e-10, pos_units * 1e-8)
            full_close = abs(q - pos_units) <= full_tolerance
            reqid = str(uuid.uuid4())
            import broker_exit_journal
            intent, created = broker_exit_journal.begin(
                broker="etoro", account_fingerprint=account_fingerprint,
                environment="DEMO" if self.paper else "LIVE",
                instrument_id=str(expected_instrument_id), position_id=str(pid),
                quantity=q,
                reason=("NEXUS positionsbezogener Full Close" if full_close
                        else "NEXUS positionsbezogener Partial Close"),
                client_order_id=reqid)
            if not created:
                raise OrderStatusUnklar(
                    "eToro-Close fuer diese positionId ist bereits aktiv; kein "
                    "zweiter POST.", reference_id=str(intent.get("reference_id") or
                    intent.get("client_order_id") or ""),
                    order_ids=[str(intent.get("broker_order_id"))]
                    if intent.get("broker_order_id") else [], accepted=True)
            intent_id = str(intent["intent_id"])
            path = self._path(
                f"/api/v1/trading/execution/demo/market-close-orders/positions/{pid}",
                f"/api/v1/trading/execution/market-close-orders/positions/{pid}",
            )
            payload = {
                ("InstrumentID" if self.paper else "InstrumentId"):
                    int(row.get("instrumentId") or r["instrumentId"]),
            }
            # Laut eToro bedeutet ein fehlendes/null UnitsToDeduct einen echten
            # Full Close. Eine numerische Gesamtmenge kann wegen Broker-Rundung
            # als Partial Close mit Restbestand enden.
            if not full_close:
                payload["UnitsToDeduct"] = q
            try:
                resp = self._submit_primary_request(path,payload=payload,request_id=reqid,
                    quantity=q,instrument_id=expected_instrument_id,side="SELL",position_id=pid)
            except OrderStatusUnklar as exc:
                broker_exit_journal.update(
                    intent_id, "UNCLEAR", reference_id=reqid,
                    detail={"error": str(exc), "position_id": str(pid),
                            "post_outcome": "AMBIGUOUS_RESPONSE"})
                raise
            except VerbindungVerloren as exc:
                # Close wird ebenfalls nicht blind wiederholt. Der Bot resynct;
                # Sicherheitspfad bleibt aktiv und kann den Restbestand erneut sehen.
                broker_exit_journal.update(
                    intent_id, "UNCLEAR", reference_id=reqid,
                    detail={"error": str(exc), "position_id": str(pid)})
                raise OrderStatusUnklar(
                    f"eToro Close-Status unklar; Close NICHT blind wiederholt (Position {pid}, request {reqid}): {exc}",
                    reference_id=reqid,
                    accepted=True,
                ) from exc
            except AuthentifizierungsFehler as exc:
                # 401/403 ist eine belastbare Ablehnung, keine angenommene Order.
                broker_exit_journal.update(
                    intent_id, "FAILED", reference_id=reqid,
                    detail={"error": str(exc), "position_id": str(pid),
                            "post_outcome": "REJECTED_AUTH"})
                raise
            except BrokerFehler as exc:
                message = str(exc)
                # Nach einem erfolgreichen HTTP-Status kann nur das JSON kaputt
                # sein. Dann ist die Brokerannahme unklar und ein Retry verboten.
                ambiguous = "ungueltiges json" in message.lower()
                broker_exit_journal.update(
                    intent_id, "UNCLEAR" if ambiguous else "FAILED",
                    reference_id=reqid,
                    detail={"error": message, "position_id": str(pid),
                            "post_outcome": ("AMBIGUOUS_RESPONSE" if ambiguous
                                             else "REJECTED_HTTP")})
                if ambiguous:
                    raise OrderStatusUnklar(
                        "eToro-Close moeglicherweise angenommen, Antwort aber "
                        "nicht auswertbar; kein zweiter POST bis zur History-"
                        f"Rekonstruktion (Position {pid}, request {reqid}).",
                        reference_id=reqid, accepted=True) from exc
                raise
            except Exception as exc:
                # Nach Beginn des POST-Aufrufs ist ein unbekannter lokaler Fehler
                # ebenfalls nicht sicher erneut sendbar.
                broker_exit_journal.update(
                    intent_id, "UNCLEAR", reference_id=reqid,
                    detail={"error": str(exc), "position_id": str(pid),
                            "post_outcome": "AMBIGUOUS_LOCAL"})
                raise OrderStatusUnklar(
                    "eToro-Close-Status nach begonnenem POST unklar; kein "
                    f"zweiter POST (Position {pid}, request {reqid}).",
                    reference_id=reqid, accepted=True) from exc
            if not isinstance(resp, dict):
                broker_exit_journal.update(
                    intent_id, "UNCLEAR", reference_id=reqid,
                    detail={"error": "Close-POST lieferte kein Objekt",
                            "position_id": str(pid),
                            "post_outcome": "AMBIGUOUS_SCHEMA"})
                raise OrderStatusUnklar(
                    "eToro-Close moeglicherweise angenommen, Antwort besitzt "
                    "aber kein Objektschema; kein zweiter POST.",
                    reference_id=reqid, accepted=True)
            ofc = resp.get("orderForClose") or {}
            if not isinstance(ofc, dict):
                ofc = {}
            oid = _api_value(
                ofc, "orderId", "orderID", "OrderId", "OrderID", default="")
            reference_ids.append(reqid)
            error_code = int(_float(resp.get("errorCode"), 0.0))
            error_message = str(resp.get("errorMessage") or "")
            if oid and (error_code or error_message):
                broker_exit_journal.update(
                    intent_id, "UNCLEAR", reference_id=reqid,
                    broker_order_id=str(oid),
                    detail={**resp,
                            "post_outcome": "CONTRADICTORY_RESPONSE",
                            "position_id": str(pid)})
                raise OrderStatusUnklar(
                    "eToro-Close-Antwort enthaelt gleichzeitig orderId und "
                    "Fehler; kein zweiter POST bis zum Brokerabgleich.",
                    reference_id=reqid, order_ids=[str(oid)], accepted=True)
            if oid:
                broker_exit_journal.update(
                    intent_id, "SUBMITTED", reference_id=reqid,
                    broker_order_id=str(oid), detail=resp)
                order_ids.append(str(oid))
            else:
                if error_code or error_message:
                    broker_exit_journal.update(
                        intent_id, "FAILED", reference_id=reqid, detail=resp)
                    raise BrokerFehler(
                        "eToro-Close abgelehnt: "
                        f"{error_message or 'errorCode=' + str(error_code)}")
                broker_exit_journal.update(
                    intent_id, "UNCLEAR", reference_id=reqid,
                    detail={**resp,
                            "post_outcome": "ACCEPTED_WITHOUT_ORDER_ID",
                            "position_id": str(pid)})
                # Der POST kann angenommen worden sein. Der persistierte Intent
                # wird ueber positionId/Request-ID weiter abgeglichen; ein
                # zweiter Close-POST waere jetzt gefaehrlich.
                raise OrderStatusUnklar(
                    "eToro-Close moeglicherweise angenommen, aber ohne orderId; "
                    "kein zweiter POST bis zum Brokerabgleich.",
                    reference_id=reqid, accepted=True)
            remaining -= q
        if not order_ids:
            raise BrokerFehler(f"eToro: keine offene Position fuer {r['symbol']} zum Schliessen gefunden")
        return OrderErgebnis(
            order_ids=order_ids, status="submitted", filled_quantity=0.0,
            position_ids=sorted(expected),
            reference_id=(reference_ids[0] if reference_ids else ""),
            hinweis=("Close-Orders fuer exakt bewiesene positionIds uebermittelt; "
                     "Fill wird ueber Trade-History verbucht"), paper=bool(self.paper),
            account_fingerprint=self.account_fingerprint(),
            broker_environment="DEMO" if self.paper else "LIVE")

    def offene_orders(self) -> list:
        pnl = self._pnl(); out = []
        for o in self._all_orders(pnl):
            iid = int(o.get("instrumentId") or o.get("instrumentID") or 0)
            role = str(o.get("_order_role") or "")
            is_close = role in {"ordersForClose", "ordersForCloseMultiple"}
            out.append({
                "symbol": self._symbol_for_id(iid),
                "side": "SELL" if is_close else ("BUY" if bool(o.get("isBuy", True)) else "SELL"),
                "qty": o.get("amountInUnits") or o.get("unitsToDeduct") or o.get("units") or "?",
                "type": o.get("orderType", "?"), "status": o.get("statusId", "open"),
                "order_id": o.get("orderId"), "asset_type": str((self._instrument_by_id.get(iid,{}) or {}).get("_bot_asset_type") or (self._instrument_by_id.get(iid,{}) or {}).get("_detected_asset_type") or "unknown"),
            })
        return out

    def hat_offene_order(self, instrument, seite: str = "BUY") -> bool:
        wanted = seite.upper()
        target = self._cache_key(instrument)
        for o in self.offene_orders():
            try:
                if canonical_key(o.get("symbol", ""), o.get("asset_type", "")) == target and wanted in str(o.get("side", "")).upper():
                    return True
            except Exception:
                continue
        return False

    def storniere_offene_orders(self, instrument) -> int:
        # Nur Pending OPEN-Orders stornieren; SL/TP an offenen Positionen werden
        # nicht als getrennte Pending-Orders behandelt und bleiben unangetastet.
        sym = self._symbol(instrument); count = 0
        for o in self.offene_orders():
            if o.get("symbol") != sym or "BUY" not in str(o.get("side", "")).upper():
                continue
            oid = o.get("order_id")
            if not oid:
                continue
            from etoro_cancellations import enqueue
            enqueue(account=self.account_fingerprint(), paper=self.paper,
                order_id=str(oid), actor="core:pending-open-cancellation")
            count += 1
        return count

    def verwaiste_orders_aufraeumen(self) -> int:
        # eToro SL/TP gehoeren zur Position, daher keine getrennten Schutzorders.
        return 0

    def _protection_breakdown_rows(self, pnl, rows, instrument_id):
        """One CID-bound stop-type readback per existing P&L generation/asset.

        Only explicitly revised plans use this additional endpoint. Both
        responses must independently match IDs, direction, remaining units and
        protection rates before a missing stop type can be enriched.
        """
        self._require_bound_identity("Schutztyp-Readback")
        from etoro_protection_repair import merge_breakdown, fresh
        fresh(pnl.get("_snapshot_at"))
        key = (str(pnl.get("_snapshot_id") or ""), str(instrument_id))
        cache = getattr(self, "_protection_breakdown_cache", {})
        data = cache.get(key)
        if data is None:
            path = self._path("/api/v2/trading/info/demo/instrument-breakdown",
                              "/api/v2/trading/info/instrument-breakdown")
            data = self._request("GET", path, bound_cid_header=True,
                params={"instrumentIds": int(instrument_id), "positionLevel": "Normal",
                        "orderLevel": "Normal", "mirrorLevel": "None"})
            # Keep only this P&L generation, avoiding an unbounded account cache.
            cache = {k: v for k, v in cache.items() if k[0] == key[0]}
            cache[key] = data
            self._protection_breakdown_cache = cache
        return merge_breakdown(rows, data, instrument_id=instrument_id)

    def protection_repair_snapshot(self, instrument_id, position_ids):
        """Explicit maintenance read; no workers, order submission or PATCH."""
        expected = {str(x) for x in position_ids}
        if len(expected) != 1:
            raise BrokerFehler("ETORO_REPAIR_SINGLE_OWNED_POSITION_REQUIRED")
        snapshot = self.position_snapshot(force=True)
        pnl = self._pnl(force=False)
        if str(pnl.get("_snapshot_id")) != str(snapshot.get("snapshot_id")):
            raise BrokerFehler("ETORO_REPAIR_SNAPSHOT_CHANGED")
        # The ordinary compatibility reader drops non-dict rows. Maintenance
        # must not turn a malformed close-order collection into an empty view.
        self._validate_protection_order_view(pnl)
        rows = [r for r in snapshot["rows"] if str(r.get("positionId")) in expected]
        if len(rows) != 1 or str(rows[0].get("instrumentId")) != str(instrument_id):
            raise BrokerFehler("ETORO_REPAIR_POSITION_INSTRUMENT_MISMATCH")
        enriched, pending = self._protection_breakdown_rows(pnl, rows, instrument_id)
        # No pending operation may race the local adoption. Unknown order scope
        # is also unresolved, not an empty/irrelevant pending-order view.
        for row in self._all_orders(pnl):
            iid, pid = str(row.get("instrumentId") or ""), str(row.get("positionId") or "")
            if iid == str(instrument_id) or pid in expected or (not iid and not pid):
                pending.append(dict(row))
        from etoro_protection_journal import pending as pending_protection
        unresolved = pending_protection(snapshot["account_fingerprint"], snapshot["environment"], expected)
        return {"source": "ETORO_PNL_AND_INSTRUMENT_BREAKDOWN", "complete": True,
            "snapshot_id": snapshot["snapshot_id"], "snapshot_at": snapshot["snapshot_at"],
            "account_fingerprint": snapshot["account_fingerprint"], "environment": snapshot["environment"],
            "rows": enriched, "pending_orders_complete": True, "pending_orders": pending,
            "pending_protection_updates": unresolved}

    @staticmethod
    def _validate_protection_order_view(pnl):
        portfolio = pnl.get("clientPortfolio")
        if not isinstance(portfolio, dict):
            raise BrokerFehler("ETORO_REPAIR_PENDING_ORDER_VIEW_UNPROVEN")
        mirrors = portfolio.get("mirrors", [])
        if not isinstance(mirrors, list) or any(not isinstance(x, dict) for x in mirrors):
            raise BrokerFehler("ETORO_REPAIR_PENDING_ORDER_VIEW_UNPROVEN")
        for container in [portfolio, *mirrors]:
            for key in ("orders", "ordersForOpen", "ordersForClose", "ordersForCloseMultiple"):
                if key in container:
                    value = container[key]
                    if not isinstance(value, list) or any(not isinstance(x, dict) for x in value):
                        raise BrokerFehler("ETORO_REPAIR_PENDING_ORDER_VIEW_UNPROVEN")

    def reconcile_position_protection(self, instrument, quantity: float,
                                      stop: float, take_profit: float,
                                      *, position_ids=None,
                                      update_requested: bool = False,
                                      instrument_id: str = "",
                                      force_snapshot: bool = True,
                                      price_rule: dict | None = None,
                                      strict_contract: bool = False) -> dict:
        """Schutz fuer exakt ausgewaehlte positionIds setzen und bestaetigen."""
        r = self._resolve(instrument)
        expected_instrument_id = int(instrument_id or r["instrumentId"])
        if expected_instrument_id != int(r["instrumentId"]):
            raise BrokerFehler("eToro: Fill-instrumentId widerspricht dem typisierten Instrument")
        expected = {str(x) for x in (position_ids or []) if str(x).strip()}
        if not expected:
            return {"checked": True, "changed": False,
                    "protection_confirmed": False,
                    "detail": "keine bewiesene positionId; Schutz nicht veraendert"}

        def selected_rows(*, force=False):
            pnl = self._pnl(force=force)
            rows = [x for x in self._all_positions(pnl)
                    if str(x.get("positionId") or "") in expected
                    and bool(x.get("isBuy", True))]
            return pnl, rows

        pnl, rows = selected_rows(force=bool(force_snapshot))
        found = {str(x.get("positionId") or "") for x in rows}
        if found != expected:
            return {"checked": True, "changed": False,
                    "protection_confirmed": False,
                    "snapshot_id": str(pnl.get("_snapshot_id") or ""),
                    "detail": "nicht alle ausgewaehlten positionIds sind offen"}
        if any(int(x.get("instrumentId") or 0) != expected_instrument_id for x in rows):
            return {"checked": True, "changed": False,
                    "protection_confirmed": False,
                    "detail": "positionId und Instrument widersprechen sich"}

        from etoro_protection_evidence import assess, normalize, number
        try:
            # A position ID alone cannot prove the expected remaining size.
            from decimal import Decimal
            actual_qty = sum((number(x.get("units")) for x in rows), Decimal(0))
            expected_qty = number(quantity)
            if abs(actual_qty - expected_qty) > max(Decimal("1e-8"), expected_qty * Decimal("1e-9")):
                raise ValueError("ETORO_PROTECTION_QUANTITY_MISMATCH")
            normalized = (normalize(stop, take_profit, price_rule,
                instrument_id=expected_instrument_id, account=self.account_fingerprint(),
                environment="DEMO" if self.paper else "LIVE") if price_rule is not None else None)
        except (ValueError, TypeError) as exc:
            return {"checked": True, "changed": False, "protection_confirmed": False,
                "reason_code": str(exc), "detail": str(exc),
                "protection_evidence": {"schema_version": 1, "confirmed": False,
                    "reason_code": str(exc), "snapshot_id": str(pnl.get("_snapshot_id") or "")}}

        requested_stop, requested_take = stop, take_profit
        if normalized:
            stop, take_profit = normalized["stop"], normalized["take_profit"]

        def proof():
            nonlocal rows
            if strict_contract:
                try:
                    rows, _ = self._protection_breakdown_rows(pnl, rows, expected_instrument_id)
                except (BrokerFehler, ValueError, TypeError, KeyError) as exc:
                    # This supplementary read is scoped to one instrument. A
                    # complete P&L/fill/order reconciliation remains independent.
                    # Never enrich rows with an older generation after failure.
                    code = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
                    return {"schema_version": 1, "confirmed": False,
                        "reason_code": "ETORO_PROTECTION_SUPPLEMENT_UNPROVEN",
                        "supplement_error": code[:160],
                        "snapshot_id": str(pnl.get("_snapshot_id") or ""),
                        "observed": [], "position_ids": sorted(expected)}
            return assess(rows, expected, requested_stop, requested_take,
                quantity=quantity, instrument_id=expected_instrument_id,
                normalized=normalized, snapshot_id=pnl.get("_snapshot_id") or "",
                strict_contract=strict_contract)

        changed = False
        if update_requested:
            self._require_bound_identity("Schutzauftrag")
            initial_proof = proof()
            if initial_proof.get("reason_code") == "ETORO_PROTECTION_SUPPLEMENT_UNPROVEN":
                return {"checked": True, "changed": False, "protection_confirmed": False,
                    "reason_code": initial_proof["reason_code"],
                    "detail": "Zusatzbeleg dieser Position fehlt; Schutzauftrag nicht gesendet",
                    "protection_evidence": initial_proof}
            for row in rows:
                pid = str(row.get("positionId"))
                from etoro_protection_journal import prepare, confirm
                account = self.account_fingerprint()
                environment = "DEMO" if self.paper else "LIVE"
                # A prior timed-out edit may already be present. Read evidence
                # first; never generate another PATCH just because it timed out.
                confirm(account, environment, pid,
                    row.get("stopLossRate", row.get("stopLoss")),
                    row.get("takeProfitRate", row.get("takeProfit")),
                    str(pnl.get("_snapshot_id") or ""),
                    flags={k: row[k] for k in ("isNoStopLoss", "isNoTakeProfit") if k in row},
                    position_row=row, snapshot_at=pnl.get("_snapshot_at"))
                if initial_proof["confirmed"] or assess([row], {pid}, stop, take_profit,
                        quantity=row.get("units"), instrument_id=expected_instrument_id,
                        strict_contract=strict_contract)["confirmed"]:
                    continue
                reqid = str(uuid.uuid4())
                payload = {"stopLossRate": float(stop),
                           "takeProfitRate": float(take_profit), "stopLossType": "fixed"}
                prepare(account, environment, pid, reqid, payload, normalized,
                    contract={"instrument_id": str(expected_instrument_id),
                              "quantity": row["units"], "strict_contract": strict_contract})
                path = self._path(
                    f"/api/v2/trading/demo/positions/{pid}",
                    f"/api/v2/trading/positions/{pid}")
                try:
                    response = self._request("PATCH", path,
                        payload=payload, request_id=reqid, safe_retry=False)
                except (AuthentifizierungsFehler, AuftragAbgelehnt) as exc:
                    from etoro_protection_journal import rejected
                    rejected(account, environment, pid, reqid, type(exc).__name__)
                    raise
                from etoro_protection_journal import accepted
                accepted(account, environment, pid, reqid, response)
                changed = True
            # 202 bedeutet nur angenommen. Kein zweiter PATCH: lediglich
            # frische Depot-Snapshots bis zur exakten Bestaetigung lesen.
            deadline = time.monotonic() + float(getattr(
                config, "ETORO_PROTECTION_CONFIRM_SECONDS", 12.0))
            while time.monotonic() < deadline:
                time.sleep(0.8)
                pnl, rows = selected_rows(force=True)
                if proof()["confirmed"]:
                    break

        evidence = proof()
        if changed:
            evidence["sent"] = {"stop": stop, "take_profit": take_profit,
                                "meaning": "PATCH in diesem Aufruf; Annahme allein ist keine Bestaetigung"}
        confirmed = evidence["confirmed"]
        if getattr(self, "_identity_loaded", False):
            from etoro_protection_journal import confirm, pending
            for row in rows:
                receipt = confirm(self.account_fingerprint(), "DEMO" if self.paper else "LIVE",
                    str(row["positionId"]), row.get("stopLossRate", row.get("stopLoss")),
                    row.get("takeProfitRate", row.get("takeProfit")),
                    str(pnl.get("_snapshot_id") or ""),
                    flags={k: row[k] for k in ("isNoStopLoss", "isNoTakeProfit") if k in row},
                    position_row=row, snapshot_at=pnl.get("_snapshot_at"))
                if (confirmed and receipt and number(receipt["stop"]) == number(stop)
                        and number(receipt["take_profit"]) == number(take_profit)):
                    evidence["sent"] = receipt
            unresolved = pending(self.account_fingerprint(), "DEMO" if self.paper else "LIVE", expected)
            if unresolved:
                # An older, still visible stop is not a settled current plan
                # while an accepted/timed-out change can still execute later.
                confirmed = False
                evidence["current_protection_observed"] = evidence.get("confirmed") is True
                evidence.update(confirmed=False,
                    reason_code="ETORO_PROTECTION_PATCH_RECONCILIATION_REQUIRED",
                    pending_updates=unresolved)
        return {
            "checked": True, "changed": changed,
            "protection_confirmed": confirmed,
            "snapshot_id": str(pnl.get("_snapshot_id") or ""),
            "position_ids": sorted(expected),
            "instrument_id": str(expected_instrument_id),
            "reason_code": evidence["reason_code"],
            "protection_evidence": evidence,
            "detail": ("gewuenschter eToro-Stop und Take-Profit exakt bestaetigt"
                       if confirmed else
                       evidence["reason_code"] + ": Schutzwerte nicht bestaetigt; "
                       "keine Rundungsregel aus angezeigten Nachkommastellen ableiten"),
        }

    @staticmethod
    def _protection_values_match(rows, expected, stop, take_profit) -> bool:
        from etoro_protection_evidence import assess
        return assess(rows, expected, stop, take_profit)["confirmed"]

    def unterstuetzt_krypto_stop(self) -> bool:
        return True

    def unterstuetzt_bruchstuecke(self, asset_type: str = "stock") -> bool:
        return True


def _float(value, default=0.0) -> float:
    try:
        v = float(value)
        return v if math.isfinite(v) else default
    except Exception:
        return default
