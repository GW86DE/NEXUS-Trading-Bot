"""Accidental-network tripwire, only in the offline test child processes.

Not an operating-system security sandbox. Real HTTP transports and CPython
socket/DNS events are rejected; in-process TestClient and test doubles work.
"""
import os
import socket
import sys


def _record(category):
    path = os.getenv("NEXUS_OFFLINE_NETWORK_EVENTS", "")
    if path:
        # No hostname, URL, token, or request body is written here.
        with open(path, "a", encoding="utf-8") as stream:
            # Test identity only, never URL/host/body/credentials.
            test = os.getenv("PYTEST_CURRENT_TEST", "outside pytest").split(" ", 1)[0]
            stream.write(category + " " + test[:220] + "\n")
    raise OSError("Echter Netzwerkaufruf im Offline-Test gesperrt")


def _audit(event, args):
    if event in {"socket.getaddrinfo", "socket.gethostbyname", "socket.gethostbyaddr"}:
        _record("dns")
    if event in {"socket.connect", "socket.sendto", "socket.sendmsg"}:
        sock = args[0]
        if getattr(sock, "family", None) in {socket.AF_INET, socket.AF_INET6}:
            _record("socket")


def install():
    if not os.getenv("NEXUS_OFFLINE_TEST_ROOT"):
        raise RuntimeError("Offline-Netzschutz nur im isolierten Testprozess")
    if getattr(sys, "_nexus_test_network_guard", False):
        return
    sys.addaudithook(_audit)
    import requests
    def _no_http(self, request, *args, **kwargs):
        _record("http")
    requests.adapters.HTTPAdapter.send = _no_http
    sys._nexus_test_network_guard = True
