"""Gemeinsame Erkennung von Verbindungs- und Authentifizierungsfehlern.

Der Trading-Core muss unterscheiden koennen zwischen
- einem globalen/transportbedingten Ausfall (Internet, DNS, Timeout, Socket),
  bei dem der Scanner pausiert und spaeter automatisch fortsetzt, und
- einem fachlichen Datenfehler eines einzelnen Instruments, der weiterhin
  einen instrumentbezogenen Cooldown ausloesen darf.

Die Erkennung ist absichtlich defensiv und funktioniert ohne feste Bindung an
HTTP-Clients. Exception-Chains werden rekursiv betrachtet.
"""
from __future__ import annotations

import socket
from typing import Iterable


def _chain(exc: BaseException) -> Iterable[BaseException]:
    seen = set()
    cur = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        yield cur
        cur = getattr(cur, "__cause__", None) or getattr(cur, "__context__", None)


def _status_code(exc: BaseException) -> int | None:
    for item in _chain(exc):
        value = getattr(item, "status_code", None)
        if value is None:
            response = getattr(item, "response", None)
            value = getattr(response, "status_code", None)
        try:
            if value is not None:
                return int(value)
        except (TypeError, ValueError):
            pass
    return None


def is_authentication_error(exc: BaseException) -> bool:
    status = _status_code(exc)
    if status in (401, 403):
        return True
    text = " ".join(str(x).lower() for x in _chain(exc))
    markers = (
        "unauthorized", "forbidden", "invalid api key", "invalid key",
        "authentication failed", "not authenticated", "401", "403",
    )
    return any(m in text for m in markers)


def is_connectivity_error(exc: BaseException) -> bool:
    """True nur fuer voraussichtlich temporaere Transport-/Dienstfehler."""
    if is_authentication_error(exc):
        return False

    status = _status_code(exc)
    # 408/425/429/5xx sind temporaer. 429 ist kein Internetfehler, aber fuer
    # die Broker-Erreichbarkeit ebenfalls kein instrumentbezogener Fehler.
    if status in (408, 425, 429) or (status is not None and 500 <= status <= 599):
        return True

    for item in _chain(exc):
        if isinstance(item, (TimeoutError, ConnectionError, socket.timeout, socket.gaierror, OSError)):
            # OSError ist breit; typische lokale Datei-/Permission-Fehler sollen
            # nicht als Netzfehler gelten. Deshalb zusaetzlich Text pruefen.
            if isinstance(item, OSError) and not isinstance(item, (socket.timeout, socket.gaierror, ConnectionError)):
                text = str(item).lower()
                if not any(m in text for m in (
                    "network", "socket", "connection", "timed out", "timeout",
                    "name or service", "temporary failure", "host", "dns",
                    "unreachable", "refused", "reset", "broken pipe",
                )):
                    continue
            return True

        mod = type(item).__module__.lower()
        name = type(item).__name__.lower()
        if any(x in mod for x in ("requests", "urllib3", "httpx", "httpcore", "aiohttp")):
            if any(x in name for x in (
                "connect", "timeout", "network", "readerror", "writeerror",
                "protocolerror", "pooltimeout", "proxyerror",
            )):
                return True

    text = " ".join(str(x).lower() for x in _chain(exc))
    markers = (
        "connection refused", "connection reset", "connection aborted",
        "connection error", "connecterror", "network is unreachable",
        "no route to host", "temporary failure in name resolution",
        "name or service not known", "nodename nor servname provided",
        "failed to establish a new connection", "max retries exceeded",
        "read timed out", "connect timeout", "timed out", "broken pipe",
        "server disconnected", "remote protocol error", "502 bad gateway",
        "503 service unavailable", "504 gateway timeout",
    )
    return any(m in text for m in markers)
