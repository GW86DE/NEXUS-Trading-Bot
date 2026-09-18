"""MASSIVE-Marktdaten-API (Referenzdaten, Kurse, News) -- NEXUS 10.1.

WOFUER
======
MASSIVE liefert drei Dinge, die dem Bot bisher gefehlt haben:

    1. Einen vollstaendigen, taeglich gepflegten Tickerkatalog inklusive
       'active'-Kennzeichen. Damit erkennt der Bot ein Delisting, statt
       weiter gegen ein totes Symbol zu handeln.
    2. Aggregierte Tagesumsaetze fuer die Liquiditaetsbewertung des
       dynamischen Aktienuniversums.
    3. Nachrichten mit sauberem Zeitstempel und Tickerbezug.

TARIFGRENZEN
============
NEXUS nutzt standardmaessig die Grenzen des kostenlosen Plans.
Vier Abrufe pro rollenden 60 Sekunden und mindestens 15 Sekunden Abstand
werden prozessuebergreifend VOR jedem HTTP-Abruf reserviert. Cachetreffer
zaehlen nicht als HTTP-Aufruf.

HTTP 429 pausiert alle Clients nach Retry-After (mindestens eine Minute).
HTTP 402/403 pausiert nur den betroffenen Endpunkt dieser Zugangsdaten.
Beides bleibt ueber Neustarts erhalten. Gueltige Caches bleiben nutzbar.
"""

from __future__ import annotations

import logging
import json
import threading
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable, Optional

import requests
from massive_service import Store, MassivePaused, MAX_CACHE_BYTES, cache_ttl, retry_seconds

logger = logging.getLogger(__name__)

MASSIVE_BASE = "https://api.massive.com"


@dataclass
class TagesBudget:
    """Legacy compatibility helper; MassiveClient uses the shared Store only."""
    limit: int = 250
    tag: str = field(default_factory=lambda: date.today().isoformat())
    verbraucht: int = 0
    gesperrt_bis: float = 0.0
    sperrgrund: str = ""

    def _tag_pruefen(self) -> None:
        heute = date.today().isoformat()
        if heute != self.tag:
            self.tag = heute
            self.verbraucht = 0
            self.gesperrt_bis = 0.0
            self.sperrgrund = ""

    def frei(self) -> tuple[bool, str]:
        self._tag_pruefen()
        if self.gesperrt_bis and time.time() < self.gesperrt_bis:
            rest = int((self.gesperrt_bis - time.time()) / 60)
            return False, f"{self.sperrgrund} (noch ca. {max(1, rest)} min)"
        if self.limit > 0 and self.verbraucht >= self.limit:
            return False, f"Tagesbudget von {self.limit} Anfragen aufgebraucht"
        return True, ""

    def buchen(self) -> None:
        self._tag_pruefen()
        self.verbraucht += 1

    def sperren(self, sekunden: float, grund: str) -> None:
        self.gesperrt_bis = time.time() + max(60.0, float(sekunden))
        self.sperrgrund = str(grund)[:200]

    def als_dict(self) -> dict:
        self._tag_pruefen()
        return {"limit": self.limit, "verbraucht": self.verbraucht,
                "rest": max(0, self.limit - self.verbraucht) if self.limit > 0 else None,
                "gesperrt": bool(self.gesperrt_bis and time.time() < self.gesperrt_bis),
                "sperrgrund": self.sperrgrund, "tag": self.tag}


class MassiveClient:
    """REST-Zugriff auf MASSIVE mit Budgetwaechter und Statusmeldung."""

    def __init__(self, api_key: str = "", *, base_url: str = MASSIVE_BASE,
                 tageslimit: int = 0, timeout: float = 15.0, store=None):
        import config
        self._key = str(api_key or __import__("live_settings").massive_key() or "").strip()
        self.base_url = str(base_url or MASSIVE_BASE).rstrip("/")
        self.timeout = max(1.0, min(30.0, float(timeout)))
        limit = int(tageslimit or __import__("live_settings").massive_tageslimit()
                    or getattr(config, "MASSIVE_DAILY_REQUEST_LIMIT", 0) or 0)
        self.store = store or Store(self._key, self.base_url, daily_limit=limit)
        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/json",
                                     "User-Agent": "NEXUS/10.1"})
        self._status = {"ok": None, "detail": "noch nicht abgefragt", "zeit": "", "count": 0}
        self._lock = threading.RLock()

    @property
    def konfiguriert(self) -> bool:
        return bool(self._key)

    # -- Kernaufruf ---------------------------------------------------------
    def _get(self, pfad: str, params: Optional[dict] = None, *, cache_only=False) -> dict:
        if not self.konfiguriert:
            raise RuntimeError("MASSIVE API-Key fehlt")
        arguments = dict(params or {})
        token, cached, saved_at = self.store.acquire(pfad, arguments, lease_seconds=self.timeout * 4 + 10, cache_only=cache_only)
        if cached is not None:
            self._last_transport = {"source": "cache", "saved_at": saved_at, "http_request_started": False}
            return cached
        self._last_transport = {"source": "http", "http_request_started": True,
                                "request_id": token, "endpoint": pfad}
        anfrage = {**arguments, "apiKey": self._key}
        antwort = None
        read_deadline = time.monotonic() + self.timeout * 2 + 5
        try:
            try:
                antwort = self.session.get(f"{self.base_url}{pfad}", params=anfrage,
                                          timeout=self.timeout, allow_redirects=False, stream=True)
            except requests.RequestException as exc:
                self.store.finish(token, result=type(exc).__name__)
                self._merke(False, f"nicht erreichbar ({type(exc).__name__})")
                raise RuntimeError(f"MASSIVE nicht erreichbar ({type(exc).__name__})") from None
            code = antwort.status_code
            if code >= 300:
                retry = getattr(antwort, "headers", {}).get("Retry-After")
                self.store.finish(token, http_status=code, retry_after=retry, result=f"HTTP_{code}")
                self._merke(False, f"HTTP {code} am Endpunkt {pfad}")
                if code == 429:
                    raise MassivePaused("HTTP 429: Ratenbegrenzung erreicht",
                                        retry_seconds(retry, self.store.clock()), "MASSIVE_HTTP_429")
                raise RuntimeError(f"MASSIVE HTTP {code}: Endpunkt {pfad}")
            try:
                iterator = getattr(antwort, "iter_content", None)
                if callable(iterator):
                    chunks = []; size = 0
                    for chunk in iterator(chunk_size=65536):
                        size += len(chunk)
                        if size > MAX_CACHE_BYTES:
                            raise ValueError("Antwortgroesse ueberschritten")
                        if time.monotonic() > read_deadline:
                            raise ValueError("Antwortzeit ueberschritten")
                        chunks.append(chunk)
                    daten = json.loads(b"".join(chunks))
                else:
                    # Minimal offline fake responses only; requests.Response
                    # always provides the bounded streaming iterator above.
                    daten = antwort.json()
            except (ValueError, requests.RequestException) as exc:
                reason = ("RESPONSE_TOO_LARGE" if str(exc) == "Antwortgroesse ueberschritten" else
                          "RESPONSE_TIMEOUT" if str(exc) == "Antwortzeit ueberschritten" else
                          "READ_ERROR" if isinstance(exc, requests.RequestException) else "INVALID_JSON")
                self.store.finish(token, http_status=code, result=reason)
                self._merke(False, reason)
                raise RuntimeError(f"MASSIVE-Antwort nicht vollständig lesbar (Format, Umfang oder Zeitgrenze) [{reason}]") from None
            if not isinstance(daten, dict):
                self.store.finish(token, http_status=code, result="INVALID_PAYLOAD")
                raise RuntimeError("MASSIVE-Antwort hat kein gueltiges Objektformat")
            status = str(daten.get("status", "")).upper()
            if status and status not in ("OK", "DELAYED", "SUCCESS"):
                self.store.finish(token, http_status=code, result="INVALID_API_STATUS")
                self._merke(False, "API meldet keinen erfolgreichen Datenabruf")
                raise RuntimeError("MASSIVE API meldet keinen erfolgreichen Datenabruf")
            self.store.finish(token, data=daten, ttl=cache_ttl(pfad), http_status=code, result="SUCCESS")
            return daten
        finally:
            close = getattr(antwort, "close", None)
            if callable(close):
                close()

    def _merke(self, ok: bool, detail: str, count: int = 0) -> None:
        with self._lock:
            self._status = {"ok": bool(ok), "detail": str(detail)[:240],
                            "zeit": datetime.now(timezone.utc).isoformat(), "count": int(count)}

    def status(self) -> dict:
        with self._lock:
            eintrag = dict(self._status)
        try:
            eintrag["budget"] = self.store.status()
        except MassivePaused as exc:
            eintrag["budget"] = {"gesperrt": True, "state": exc.code, "sperrgrund": str(exc),
                                 "verbraucht": None, "minute_used": None}
        eintrag["transport"] = getattr(self, "_last_transport", {})
        eintrag["konfiguriert"] = self.konfiguriert
        return eintrag

    # -- Verbindungstest ----------------------------------------------------
    def verbindungstest(self) -> dict:
        """Prueft BEIDE Faehigkeiten getrennt: Referenzdaten und Nachrichten.

        Bis v8.1.1 hat dieser Test nur /v3/reference/tickers abgefragt und
        anschliessend "OK" gemeldet -- auch dann, wenn der tatsaechlich
        genutzte Nachrichtenendpunkt /v2/reference/news im gebuchten Tarif
        gar nicht enthalten war. Der Test hat also die falsche Sache
        geprueft und ein funktionierendes MASSIVE vorgetaeuscht, waehrend
        im Betrieb keine einzige Meldung ankam.
        """
        if not self.konfiguriert:
            return {"ok": False, "detail": "Kein API-Key hinterlegt"}

        faehigkeiten: dict[str, dict] = {}
        # News are the requested capability. The reference probe can be pending
        # under Free pacing and must never turn a successful news test into an
        # outage or claim that an untested capability worked.
        checks = [("Nachrichten", lambda: self.news(limit=1)),
                  ("Referenzdaten", lambda: self._get("/v3/reference/tickers",
                                                    {"limit": 1, "active": "true"}).get("results") or [])]
        for name, fetch in checks:
            try:
                rows = fetch()
                faehigkeiten[name] = {"ok": True, "count": len(rows),
                    "detail": f"erreichbar ({len(rows)} Datensaetze)",
                    "transport": dict(getattr(self, "_last_transport", {}))}
            except MassivePaused as exc:
                denied = exc.code == "MASSIVE_CAPABILITY_PAUSED"
                known_failure = denied or exc.code == "MASSIVE_HTTP_429"
                faehigkeiten[name] = {"ok": False if known_failure else None, "pending": not known_failure, "code": exc.code,
                    "tarif": denied and ("402" in str(exc) or "403" in str(exc)),
                    "retry_after": exc.retry_after, "detail": str(exc)}
            except RuntimeError as exc:
                faehigkeiten[name] = {"ok": False, "tarif": "402" in str(exc) or "403" in str(exc),
                                      "detail": str(exc)}
        news = faehigkeiten["Nachrichten"]
        reference = faehigkeiten["Referenzdaten"]
        if news.get("ok") is True:
            detail = "Nachrichten erreichbar; " + ("Referenzdaten erreichbar" if reference.get("ok") is True
                     else "Referenzdaten noch nicht geprueft (Free-Abrufpause)" if reference.get("pending")
                     else "Referenzdaten: " + reference.get("detail", "unbekannt"))
        else:
            detail = "Nachrichten: " + news.get("detail", "noch nicht geprueft")
        self._merke(news.get("ok") is True, detail, int(news.get("count", 0)))
        return {"ok": news.get("ok"), "pending": bool(news.get("pending")),
                "detail": detail, "faehigkeiten": faehigkeiten,
                "count": int(news.get("count", 0)), "budget": self.status()["budget"]}

    # -- Referenzdaten ------------------------------------------------------
    def tickers(self, *, market: str = "stocks", limit: int = 1000,
                aktiv: bool = True, max_seiten: int = 5) -> list[dict]:
        """Tickerkatalog, seitenweise. Jede Seite kostet eine Anfrage."""
        gesammelt: list[dict] = []
        params = {"market": market, "limit": min(1000, max(1, int(limit))),
                  "active": "true" if aktiv else "false", "sort": "ticker", "order": "asc"}
        cursor = ""
        for _ in range(max(1, int(max_seiten))):
            if cursor:
                params["cursor"] = cursor
            daten = self._get("/v3/reference/tickers", params)
            treffer = daten.get("results") or []
            gesammelt.extend(treffer)
            weiter = str(daten.get("next_url") or "")
            if not weiter or not treffer:
                break
            cursor = weiter.split("cursor=")[-1] if "cursor=" in weiter else ""
            if not cursor:
                break
        self._merke(True, f"{len(gesammelt)} Ticker geladen", len(gesammelt))
        return gesammelt

    def ticker_details(self, ticker: str) -> dict:
        daten = self._get("/v3/reference/tickers", {"ticker": str(ticker).upper(), "limit": 1})
        treffer = daten.get("results") or []
        return treffer[0] if treffer else {}

    def ist_aktiv(self, ticker: str) -> Optional[bool]:
        """True/False, oder None wenn nicht feststellbar (z.B. Budget leer)."""
        try:
            eintrag = self.ticker_details(ticker)
        except RuntimeError:
            return None
        if not eintrag:
            return None
        return bool(eintrag.get("active", True))

    # -- Aggregierte Kurse --------------------------------------------------
    def tagesaggregate(self, ticker: str, tage: int = 30) -> list[dict]:
        """Tageskerzen fuer die Liquiditaetsbewertung."""
        ende = date.today()
        start = ende - timedelta(days=int(max(2, tage)) + 10)
        pfad = (f"/v2/aggs/ticker/{str(ticker).upper()}/range/1/day/"
                f"{start.isoformat()}/{ende.isoformat()}")
        daten = self._get(pfad, {"adjusted": "true", "sort": "desc", "limit": int(tage)})
        return list(daten.get("results") or [])

    def dollar_volumen(self, ticker: str, tage: int = 20) -> float:
        """Mittlerer Tagesumsatz in USD -- das Liquiditaetsmass fuer Aktien."""
        try:
            zeilen = self.tagesaggregate(ticker, tage=tage)
        except RuntimeError:
            return 0.0
        werte = []
        for zeile in zeilen[:tage]:
            volumen = float(zeile.get("v") or 0.0)
            preis = float(zeile.get("vw") or zeile.get("c") or 0.0)
            if volumen > 0 and preis > 0:
                werte.append(volumen * preis)
        return sum(werte) / len(werte) if werte else 0.0

    # -- Nachrichten --------------------------------------------------------
    def news(self, ticker: str = "", limit: int = 20, *, cache_only=False) -> list[dict]:
        requested_limit = min(50, max(1, int(limit)))
        # The common 1/5/10/20-row callers share one provider response.
        params: dict[str, Any] = {"limit": 20 if requested_limit <= 20 else 50, "order": "desc",
                                  "sort": "published_utc"}
        if ticker:
            params["ticker"] = str(ticker).upper()
        daten = self._get("/v2/reference/news", params, cache_only=cache_only)
        treffer = (daten.get("results") or [])[:requested_limit]
        self._merke(True, f"{len(treffer)} Meldungen", len(treffer))
        return treffer


__all__ = ["MassiveClient", "TagesBudget", "MASSIVE_BASE"]
