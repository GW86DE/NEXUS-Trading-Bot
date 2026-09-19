"""Mehrquellen-Nachrichtenadapter fuer TradingBot 8.2 NEXUS.

Ziel: Nachrichten- und Risikologik nutzt mehrere voneinander unabhaengige Quellen.

Quellen umfassen GDELT, SEC EDGAR, Nasdaq Halts, Yahoo/Google News sowie optional
Alpha Vantage, Finnhub, Financial Modeling Prep und MASSIVE.

Wichtig: Quellen ohne Zugangsdaten werden sauber uebersprungen. Positive
Event-Trades koennen in event_intelligence.py eine Mindest-Quellenvielfalt
verlangen; kritische negative/offizielle Meldungen duerfen weiterhin auch aus
einer einzelnen glaubwuerdigen Quelle einen Sicherheitsblock ausloesen.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote
import hashlib
import html
from email.utils import parsedate_to_datetime
import json
from safe_persistence import best_effort_json
import logging
import os
import re
import time
import threading
import xml.etree.ElementTree as ET
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

import requests
from massive_service import MassivePaused
import config
from provider_safety import redact
from state_lock import critical_state_lock
# 10.8.0 (Schritt 2): Das Nachrichtenmodell (NewsItem, _clean_text, _safe_dt)
# liegt in news_model, damit nasdaq_halt_feed es ohne Rueckgriff auf dieses
# Modul nutzen kann (Import-Zyklus). Re-Export fuer bestehende Importeure.
from news_model import NewsItem, _clean_text, _safe_dt  # noqa: F401

logger = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parent

# Der Quellenstatus ist eine LAUFZEITdatei und gehoert damit in das
# Zustandsverzeichnis -- nicht in den Releasebaum. Vorher schrieb jeder
# Testlauf 'news_source_status.json' direkt neben den Quellcode, wodurch
# die Release-Hygienepruefung anschliessend zu Recht Alarm schlug.
_STATE_ROOT = Path(os.getenv("TRADINGBOT_TEST_STATE_DIR", "").strip() or ROOT)
STATUS_FILE = _STATE_ROOT / getattr(config, "NEWS_SOURCE_STATUS_FILE", "news_source_status.json")

# Gemeinsamer Prozess-Cache: NewsRadar und NachrichtenFilter teilen dieselben
# Provider-Antworten. Dadurch wird dieselbe Quelle im selben Zyklus nicht
# mehrfach abgefragt.
_GLOBAL_CACHE = {}
_GLOBAL_CACHE_LOCK = threading.RLock()



def _now_utc():
    return datetime.now(timezone.utc)


def normalize_source_status(records):
    """Annotate provider/capability history without merging distinct endpoints."""
    out = {}
    for name, value in (records or {}).items():
        if not isinstance(value, dict):
            out[name] = value
            continue
        row = dict(value)
        provider = "FMP" if name in {"FMP", "FMP News", "FMP Symbol Search"} else name
        row.setdefault("provider", provider)
        row.setdefault("capability", "reference" if name == "FMP Symbol Search" else "news")
        if name == "FMP News":
            row.update(historical_alias=True, canonical_provider="FMP",
                       history_note="Historischer Quellenname; kein Nachweis eines aktuellen FMP-Ausfalls")
            # The old record does not identify the endpoint. Do not invent it
            # or fold its 402 into another endpoint's successful response.
            row.setdefault("endpoint", "UNKNOWN_LEGACY_NEWS")
        out[name] = row
    return out


def canonical_news_url(value):
    """Article identity; remove tracking, retain query parameters selecting content."""
    try:
        parsed = urlsplit(str(value or "").strip())
        host = (parsed.hostname or "").lower().rstrip(".")
        if parsed.scheme not in {"http", "https"} or not host or parsed.username or parsed.password:
            return ""
        port = parsed.port
        if host.startswith("www."):
            host = host[4:]
        if port not in {None, 80, 443}:
            host += ":" + str(port)
        ignored = {"fbclid", "gclid", "dclid", "msclkid", "mc_cid", "mc_eid", "ref", "referrer"}
        query = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True)
                 if not k.lower().startswith("utm_") and k.lower() not in ignored]
        return urlunsplit(("https", host, parsed.path.rstrip("/") or "/", urlencode(sorted(query)), ""))
    except (ValueError, TypeError, UnicodeError):
        return ""


def social_news_lineage(row):
    """An X repost remains social evidence when delivered by a news provider."""
    metadata = row.get("metadata") or {}
    if not isinstance(metadata, dict):
        metadata = {}
    labels = [row.get("source"), row.get("source_family"), metadata.get("source_family"),
              metadata.get("origin_platform"), metadata.get("original_source")]
    if any(str(v or "").strip().lower() in {"x", "twitter", "reddit", "social", "x/twitter"} for v in labels):
        return True
    if any(metadata.get(k) for k in ("derived_from_x", "is_repost", "repost_of", "social_lineage")):
        return True
    urls = [row.get("url"), metadata.get("original_url"), metadata.get("origin_url"),
            metadata.get("source_url"), *metadata.get("deduplicated_urls", [])]
    # A linked social post is not an independent confirmation of that post.
    text = row.get("text") or (str(row.get("headline") or "") + " " + str(row.get("summary") or ""))
    urls += re.findall(r"https?://[^\s<>]+", str(text))
    for value in urls:
        try:
            host = (urlsplit(str(value or "")).hostname or "").lower().removeprefix("www.")
        except ValueError:
            continue
        if any(host == d or host.endswith("."+d) for d in ("x.com", "twitter.com", "t.co", "reddit.com", "redd.it")):
            return True
    return False



def publication_origin(row):
    """Conservative publisher site identity, not a claim of factual independence."""
    import ipaddress
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    if social_news_lineage(row):
        return ""
    original = canonical_news_url(metadata.get("original_url") or metadata.get("origin_url")
                                  or metadata.get("source_url") or row.get("url"))
    host = (urlsplit(original).hostname or "").lower()
    aggregators = ("google.com", "yahoo.com", "financialmodelingprep.com", "massive.com", "polygon.io")
    if not host or any(host == a or host.endswith("."+a) for a in aggregators):
        return ""
    try:
        ipaddress.ip_address(host)
        return ""  # An IP literal is not a verified publication-site identity.
    except ValueError:
        pass
    if "." not in host or host.endswith((".local", ".localhost")):
        return ""
    labels = host.split(".")
    n = 3 if len(labels) > 2 and labels[-2] in {"co", "com", "org", "net", "gov", "ac"} else 2
    return ".".join(labels[-n:])


def _headline_key(headline, url=""):
    # URL ist bei Syndizierung oft verschieden; Titel normalisieren.
    text = _clean_text(headline).lower()
    text = re.sub(r"[^a-z0-9äöüß]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) >= 24:
        return text[:180]
    return (str(url or "").split("?")[0] or text)[:220]


def _within_hours(dt, hours):
    if not isinstance(dt, datetime) or dt.tzinfo is None:
        return False
    try:
        return _now_utc() - timedelta(hours=max(1, int(hours))) <= dt <= _now_utc() + timedelta(minutes=5)
    except (TypeError, ValueError, OverflowError):
        return False


@dataclass
class NewsBundle:
    items: list[NewsItem] = field(default_factory=list)
    sources_ok: list[str] = field(default_factory=list)
    sources_failed: dict = field(default_factory=dict)
    sources_skipped: list[str] = field(default_factory=list)
    sources_backoff: dict = field(default_factory=dict)
    sources_partial: dict = field(default_factory=dict)
    source_coverage: dict = field(default_factory=dict)

    @property
    def source_count(self):
        return len(set(self.sources_ok))

    @property
    def provider_names(self):
        names = set()
        for item in self.items:
            names.update(item.providers or [item.source])
        return sorted(names)

    def as_dicts(self):
        return [x.as_dict() for x in self.items]


class ProviderRows(list):
    """A row collection with explicit coverage; empty rows are not clearance."""
    def __init__(self, rows, coverage):
        super().__init__(rows)
        self.coverage = coverage


class MultiSourceNews:
    ALPHA_URL = "https://www.alphavantage.co/query"
    FINNHUB_BASE = "https://finnhub.io/api/v1"
    FMP_BASE = "https://financialmodelingprep.com/stable"
    SEC_TICKERS = "https://www.sec.gov/files/company_tickers.json"
    SEC_SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
    GDELT_DOC = "https://api.gdeltproject.org/api/v2/doc/doc"
    YAHOO_RSS = "https://feeds.finance.yahoo.com/rss/2.0/headline"
    GOOGLE_NEWS_RSS = "https://news.google.com/rss/search"
    NASDAQ_HALTS_RSS = "https://www.nasdaqtrader.com/rss.aspx?feed=tradehalts"

    def __init__(self):
        self.session = requests.Session()
        # Klarer, normaler HTTP-Client fuer die aktiv gepflegten Quellen.
        self.session.headers.update({
            "User-Agent": "TradingBot/9.8.8-NEXUS (+local private trading assistant)",
            "Accept": "application/json, text/plain, */*",
        })
        self.timeout = float(getattr(config, "NEWS_SOURCE_TIMEOUT_SECONDS", 10))
        self.max_each = int(getattr(config, "NEWS_SOURCE_MAX_PER_PROVIDER", 10))
        self._cache = _GLOBAL_CACHE
        self._status = self._load_status()
        self._sec_tickers_cache = None
        self._sec_tickers_loaded_at = 0.0
        self._massive_client = None

    # ------------------------------- Status -------------------------------
    def _load_status(self):
        try:
            return normalize_source_status(json.loads(STATUS_FILE.read_text(encoding="utf-8"))) if STATUS_FILE.exists() else {}
        except Exception:
            return {}

    def _save_status(self):
        best_effort_json(STATUS_FILE, self._status, label="News-Quellenstatus")

    def _mark(self, source, ok, detail="", count=0, backoff_seconds=0, endpoint="", coverage=None):
        now = _now_utc()
        with critical_state_lock(STATUS_FILE):
            self._status = self._load_status()
            old = self._status.get(source, {})
            failures = 0 if ok else int(old.get("consecutive_failures", 0) or 0) + 1
            safe = redact(detail)
            self._status[source] = {
                **old, "ok": bool(ok), "detail": safe, "count": int(count),
                "provider": "FMP" if source.startswith("FMP") else source,
                "capability": "reference" if source == "FMP Symbol Search" else "news",
                "endpoint": endpoint or old.get("endpoint", ""),
                "local_pause": {},
                "time": now.isoformat(), "last_checked_at": now.isoformat(),
                "last_success_at": now.isoformat() if ok else old.get("last_success_at",
                    old.get("time", "") if old.get("ok") else ""),
                "error_at": old.get("error_at", old.get("time", "") if old.get("ok") is False else "") if ok else now.isoformat(),
                "last_error_detail": old.get("last_error_detail", old.get("detail", "") if old.get("ok") is False else "") if ok else safe,
                "consecutive_failures": failures,
                "next_retry_at": (now + timedelta(seconds=max(1, int(backoff_seconds)))).isoformat()
                    if not ok and backoff_seconds else "",
                "failure_kind": "" if ok else ("entitlement" if "402" in safe or "Payment Required" in safe
                    else "rate_limit" if any(x in safe.lower() for x in ("429", "rate limit", "requests per day"))
                    else "authentication" if any(x in safe.lower() for x in ("401", "invalid api key", "invalid token"))
                    else "tls" if any(x in safe.lower() for x in ("ssl", "certificate", "tls"))
                    else "partial" if coverage and not coverage.get("complete") else "error"),
            }
            if coverage is not None:
                self._status[source]["coverage"] = coverage
            self._save_status()
        return failures

    def _mark_pause(self, source, exc):
        # A local rate decision is not a failed HTTP call. Keep last success,
        # article count and HTTP timestamps; expose scheduling separately.
        now = _now_utc()
        with critical_state_lock(STATUS_FILE):
            self._status = self._load_status()
            old = self._status.get(source, {})
            self._status[source] = {**old, "local_pause": {
                "code": exc.code, "detail": str(exc), "at": now.isoformat(),
                "next_retry_at": (now + timedelta(seconds=exc.retry_after)).isoformat()}}
            self._save_status()

    def _backoff_remaining(self, source):
        self._status = self._load_status()
        row = self._status.get(source, {}) if isinstance(self._status.get(source, {}), dict) else {}
        value = row.get("next_retry_at")
        if source == "MASSIVE":
            # New shared service owns all Massive pauses. Old per-client 1 h
            # pauses must not defeat cache reuse or delay a now-due request.
            return 0
        if not value:
            return 0
        dt = _safe_dt(value)
        if not dt:
            return 0
        return max(0, int((dt - _now_utc()).total_seconds()))

    # Fehler, die sich durch Warten NICHT beheben: Der Tarif deckt den
    # Endpunkt nicht ab oder der Schluessel ist ungueltig. Ein stuendlicher
    # Wiederholungsversuch erzeugt hier nur Protokollrauschen und Last.
    _DAUERHAFTE_FEHLER = (
        "402", "payment required",
        "401", "unauthorized", "invalid api key", "invalid token",
        "403 forbidden" ,
        "you don't have access", "not available under your", "upgrade your plan",
        "exclusive endpoint", "premium",
    )

    def _ist_dauerhaft(self, text: str) -> bool:
        return any(x in text for x in self._DAUERHAFTE_FEHLER)

    def _failure_backoff(self, source, exc):
        if isinstance(exc, MassivePaused):
            return max(1, int(__import__("math").ceil(exc.retry_after)))
        text = str(exc).lower()
        response = getattr(exc, "response", None)
        retry = getattr(response, "headers", {}).get("Retry-After", "") if response is not None else ""
        if retry:
            try:
                seconds = float(retry)
            except ValueError:
                dt = _safe_dt(retry)
                seconds = (dt - _now_utc()).total_seconds() if dt else 0
            if seconds > 0:
                return min(86400, max(60, int(seconds)))

        # Tarif-/Schluesselprobleme: sehr lange pausieren statt staendig
        # neu zu versuchen. Der Nutzer sieht den Grund im Status und kann
        # die Quelle bewusst abschalten oder einen Tarif waehlen.
        if self._ist_dauerhaft(text):
            return int(getattr(config, "NEWS_SOURCE_PLAN_BACKOFF_SECONDS", 86400))

        if any(x in text for x in ("25 requests per day", "daily api rate", "daily limit")):
            return int(getattr(config, "NEWS_SOURCE_DAILY_LIMIT_BACKOFF_SECONDS", 21600))
        if any(x in text for x in ("429", "492", "too many requests", "rate limit")):
            # GDELT ist in der kostenlosen Nutzung besonders streng und
            # antwortet schnell mit 429. Eine kurze Pause fuehrt direkt in
            # die naechste Sperre -- deshalb hier deutlich laenger.
            if source.upper() == "GDELT":
                return int(getattr(config, "NEWS_SOURCE_GDELT_RATE_BACKOFF_SECONDS", 7200))
            return int(getattr(config, "NEWS_SOURCE_RATE_LIMIT_BACKOFF_SECONDS", 3600))
        base = int(getattr(config, "NEWS_SOURCE_FAILURE_BACKOFF_SECONDS", 600))
        prev = self._status.get(source, {}) if isinstance(self._status.get(source, {}), dict) else {}
        n = max(0, int(prev.get("consecutive_failures", 0) or 0))
        return min(int(getattr(config, "NEWS_SOURCE_MAX_BACKOFF_SECONDS", 3600)), base * (2 ** min(n, 3)))

    def health_snapshot(self):
        """Lokaler Quellenstatus ohne Netzwerkzugriff.

        ``configured`` bedeutet: aktiviert und erforderliche Zugangsdaten sind
        vorhanden. ``healthy`` bedeutet: der letzte echte Abruf war erfolgreich
        und ist fuer die jeweilige Cache-Dauer noch hinreichend aktuell.
        """
        self._status = self._load_status()
        conf = self.provider_configuration()
        out = {}
        for name, configured in conf.items():
            row = self._status.get(name, {}) if isinstance(self._status.get(name, {}), dict) else {}
            dt = _safe_dt(row.get("time"))
            age = ((_now_utc() - dt).total_seconds() if dt else None)
            max_age = max(1800, self._ttl(name) * int(getattr(config, "NEWS_SOURCE_HEALTH_TTL_MULTIPLIER", 3)))
            if name == 'Nasdaq Halts':
                max_age = 90
            backoff = self._backoff_remaining(name)
            coverage = row.get("coverage") if isinstance(row.get("coverage"), dict) else {}
            if not configured:
                state = "disabled"
            elif not row:
                state = "unknown"
            elif coverage and not coverage.get("complete") and age is not None and 0 <= age <= 90 and not backoff:
                state = "partial"
            elif backoff > 0 and not row.get("ok"):
                state = row.get("failure_kind") if row.get("failure_kind") in {"entitlement", "authentication"} else "backoff"
            elif age is not None and age > max_age:
                state = "stale"
            elif bool(row.get("ok")) and age is not None and 0 <= age <= max_age:
                state = "ok"
            elif bool(row.get("ok")):
                state = "stale"
            else:
                state = "error"
            out[name] = {
                "provider": row.get("provider", name),
                "endpoint": row.get("endpoint", ""),
                "capability": row.get("capability", ""),
                "local_pause": row.get("local_pause", {}),
                "configured": bool(configured),
                "state": state,
                "healthy": state == "ok",
                "detail": ("Deaktiviert; letzter Pruefstand: " if not configured and row else "") + redact(row.get("detail", "")),
                "last_checked_at": str(row.get("last_checked_at") or row.get("time") or ""),
                "last_success_at": str(row.get("last_success_at") or (row.get("time") if row.get("ok") else "") or ""),
                "role": "Referenzdaten" if name == "FMP Symbol Search" else "Marktkontext" if name in {"Federal Reserve", "GDELT"} else "Instrumentnachrichten",
                "failure_kind": str(row.get("failure_kind", "")),
                "count": int(row.get("count", 0) or 0),
                "time": str(row.get("time", "")),
                "age_seconds": age,
                "backoff_seconds": backoff,
                "consecutive_failures": int(row.get("consecutive_failures", 0) or 0),
                "coverage": coverage,
            }
            error_at = _safe_dt(row.get("error_at") or (row.get("time") if row.get("ok") is False else ""))
            scope = ("HISTORICAL" if state in {"disabled", "stale", "ok"} else "CURRENT") if error_at else "NONE"
            effect = ("SOURCE_PARTIAL" if state == "partial" else "SOURCE_PAUSED" if backoff and configured
                      else "SOURCE_UNAVAILABLE" if state in {"error", "authentication", "entitlement"}
                      else "UNKNOWN" if state in {"stale", "unknown"} else "NONE")
            out[name].update(error_at=error_at.isoformat() if error_at else "",
                error_age_seconds=max(0, (_now_utc()-error_at).total_seconds()) if error_at else None,
                error_scope=scope, last_error_detail=redact(row.get("last_error_detail") or (row.get("detail") if row.get("ok") is False else "") or ""),
                current_effect=effect, impact=(
                    f"{coverage.get('usable_count', 0)} gueltige Haltbelege nutzbar; fehlendes Symbol ist keine Entwarnung" if state == "partial" else
                    "Optionaler Marktkontext fehlt; Core und andere Quellen arbeiten weiter" if name == "GDELT" and effect in {"SOURCE_PAUSED", "SOURCE_UNAVAILABLE"} else
                    "Letzter Fehler ist historisch; aktuelle Verfuegbarkeit ungeprueft" if scope == "HISTORICAL" and state == "stale" else
                    "Historischer Fehler ohne aktuelle Fehlerwirkung" if scope == "HISTORICAL" else
                    "Quelle voruebergehend nicht nutzbar; andere Quellen bleiben getrennt" if effect in {"SOURCE_PAUSED", "SOURCE_UNAVAILABLE"} else
                    "Aktueller Quellenstand nicht bestaetigt" if effect == "UNKNOWN" else "Keine aktuelle Fehlerwirkung"))
        legacy = self._status.get("FMP News")
        if isinstance(legacy, dict):
            checked = _safe_dt(legacy.get("last_checked_at") or legacy.get("time"))
            out["FMP News (historisch)"] = {
                **legacy, "configured": False, "healthy": False, "state": "stale",
                "role": "Historischer Pruefstand", "count": int(legacy.get("count", 0) or 0),
                "age_seconds": (_now_utc() - checked).total_seconds() if checked else None,
                "detail": "Historischer Pruefstand: " + str(legacy.get("detail", "")),
                "backoff_seconds": 0,
                "error_scope": "HISTORICAL", "current_effect": "NONE",
                "error_at": str(legacy.get("error_at") or legacy.get("time") or ""),
                "error_age_seconds": (_now_utc() - checked).total_seconds() if checked else None,
                "last_error_detail": redact(legacy.get("last_error_detail") or legacy.get("detail", "")),
                "impact": "Historischer Quellenname; keine Aussage ueber aktuelle FMP-Endpunkte",
            }
        if "MASSIVE" in out:
            from massive_service import readonly_status
            out["MASSIVE"]["request_status"] = readonly_status(__import__("live_settings").massive_key())
        return out

    def provider_configuration(self):
        """Welche Quellen sind JETZT aktiv?

        Ab v8.1.2 kommen Schalter und Schluessel aus live_settings und damit
        direkt aus der Zugangsdatei. Vorher stammten sie aus config.py, das
        nur beim Prozessstart gelesen wird -- ein in der WebUI umgelegter
        Schalter blieb dadurch wirkungslos, bis Core und WebUI neu starteten.
        Das war die eigentliche Ursache der "News gehen nicht"-Meldungen.
        """
        import live_settings as ls
        return {
            "SEC EDGAR": bool(ls.quelle_aktiv("sec_edgar") and ls.sec_kontakt()),
            "Nasdaq Halts": bool(ls.quelle_aktiv("nasdaq_halts")),
            "Federal Reserve": bool(ls.quelle_aktiv("federal_reserve")),
            "Yahoo Finance": bool(ls.quelle_aktiv("yahoo_finance")),
            "Google News": bool(ls.quelle_aktiv("google_news")),
            "GDELT": bool(ls.quelle_aktiv("gdelt")),
            "Alpha Vantage": bool(ls.quelle_aktiv("alpha_vantage") and ls.alpha_vantage_key()),
            "Finnhub": bool(ls.quelle_aktiv("finnhub") and ls.finnhub_key()),
            # FMP hat ZWEI getrennte Faehigkeiten. Im Gratistarif liefert die
            # Symbolsuche Daten, waehrend die News-Endpunkte mit HTTP 402
            # antworten. Ein 402 der News darf die funktionierende
            # Symbolsuche deshalb nicht mitreissen.
            "FMP": bool(ls.quelle_aktiv("fmp") and ls.fmp_key()
                        and __import__("fmp_reference").client().erlaubt("/news/stock")),
            "FMP Symbol Search": bool(ls.quelle_aktiv("fmp") and ls.fmp_key()),
            "MASSIVE": bool(ls.quelle_aktiv("massive") and ls.massive_key()),
        }

    # ------------------------------- Cache --------------------------------
    def _cache_get(self, key, ttl):
        with _GLOBAL_CACHE_LOCK:
            row = self._cache.get(key)
            if row and (time.time() - row[0]) < ttl:
                return row[1]
        return None

    def _cache_put(self, key, value):
        with _GLOBAL_CACHE_LOCK:
            self._cache[key] = (time.time(), value)
        return value

    def _ttl(self, source):
        defaults = {
            "Alpha Vantage": 7200,  # API-Budget schonen; Marktfeed reicht meist
            "Finnhub": 1800,
            "FMP": 1800,
            "FMP Symbol Search": 1800,
            "MASSIVE": 1800,
            "SEC EDGAR": 1800,
            "Nasdaq Halts": 60,
            "Federal Reserve": 900,
            "Yahoo Finance": 900,
            "Google News": 900,
            "GDELT": 600,
        }
        attr = "NEWS_SOURCE_" + source.upper().replace(".", "_").replace(" ", "_") + "_CACHE_SECONDS"
        return int(getattr(config, attr, defaults[source]))

    # ----------------------------- Provider --------------------------------

    def _gdelt(self, symbol=None, hours=48, market=False):
        """GDELT is used only for broad market/crisis context in v5.6.

        Short ticker symbols are too ambiguous for a global media corpus.
        Company-specific research uses SEC/Yahoo/Google instead.
        """
        if symbol:
            return []
        if not self.provider_configuration().get("GDELT"):
            return None
        key=("gdelt","MARKET",int(hours))
        cached=self._cache_get(key,self._ttl("GDELT"))
        if cached is not None:return cached
        query='(stocks OR shares OR earnings OR markets OR recession OR inflation OR crisis) sourcecountry:US'
        params={"query":query,"mode":"ArtList","format":"json","maxrecords":min(50,self.max_each*3),
                "timespan":f"{max(1,int(hours))}h","sort":"HybridRel"}
        r=self._public_get("GDELT", self.GDELT_DOC, params=params);r.raise_for_status();raw=r.json()
        out=[]
        for a in (raw.get("articles",[]) if isinstance(raw,dict) else []):
            title=a.get("title") or "";url=a.get("url") or ""
            if not title:continue
            dt=_safe_dt(a.get("seendate"))
            if not _within_hours(dt,hours):continue
            domain=str(a.get("domain") or "")
            out.append(NewsItem("GDELT Market",title,domain,url,dt,[],0.65,metadata={"domain":domain,"language":a.get("language"),"scope":"market"}))
            if len(out)>=self.max_each:break
        self._mark("GDELT",True,"Markt-/Krisenfeed erreichbar",len(out))
        return self._cache_put(key,out)

    def _company_name(self, symbol: str) -> str:
        try:
            row=self._sec_ticker_map().get(str(symbol).upper()) or {}
            return _clean_text(row.get("title") or symbol)
        except Exception:
            return str(symbol)

    def _company_matches(self, symbol, text):
        name = self._company_name(symbol)
        name = re.sub(r"(?i)[, .]*(?:incorporated|inc|corporation|corp|ltd|plc|limited|co)[. ,]*$", '', name).strip()
        return (len(name) >= 4 and name.upper() != str(symbol).upper()
                and re.search(r"\b" + re.escape(name) + r"\b", text, re.I) is not None)

    def _yahoo(self, symbol=None, hours=48, market=False):
        """Ticker-specific Yahoo news via the already-installed yfinance client.

        The old feeds.finance.yahoo.com RSS URL started returning HTTP 404 in
        our live verification and is therefore no longer queried. This source
        is best-effort/medium weight; SEC/Nasdaq remain the hard signals.
        """
        if not symbol or not self.provider_configuration().get("Yahoo Finance"):
            return None
        key=("yahoo-yfinance",str(symbol).upper(),int(hours))
        cached=self._cache_get(key,self._ttl("Yahoo Finance"))
        if cached is not None:return cached
        try:
            import yfinance as yf
            rows=(yf.Ticker(str(symbol).upper()).news or [])[:self.max_each*3]
            out=[]
            for row in rows:
                if not isinstance(row,dict):continue
                c=row.get("content") if isinstance(row.get("content"),dict) else row
                title=_clean_text(c.get("title") or row.get("title"))
                summary=_clean_text(c.get("summary") or row.get("summary") or "")
                can=c.get("canonicalUrl") if isinstance(c,dict) else None
                link=(can or {}).get("url") if isinstance(can,dict) else (c.get("link") or row.get("link") or "")
                pub=_safe_dt(c.get("pubDate") or row.get("providerPublishTime") or c.get("displayTime"))
                related = row.get('relatedTickers') or c.get('relatedTickers') or []
                finance = c.get('finance') or {}
                related = list(related) + [x.get('symbol', '') for x in finance.get('stockTickers', []) if isinstance(x, dict)]
                explicit = str(symbol).upper() in {str(x).upper() for x in related}
                named = False if explicit else self._company_matches(symbol, title + ' ' + summary)
                if title and _within_hours(pub,hours) and (explicit or named):
                    out.append(NewsItem("Yahoo Finance",title,summary,link,pub,[symbol],0.80,metadata={"feed":"yfinance"}))
                if len(out)>=self.max_each:break
            self._mark("Yahoo Finance",True,"Ticker-News via yfinance erreichbar",len(out))
            return self._cache_put(key,out)
        except Exception as exc:
            raise RuntimeError(f"Yahoo/yfinance News nicht erreichbar: {exc}") from exc

    def _google_news(self, symbol=None, hours=48, market=False):
        if not symbol or not self.provider_configuration().get("Google News"):
            return None
        key=("google-news",str(symbol).upper(),int(hours));cached=self._cache_get(key,self._ttl("Google News"))
        if cached is not None:return cached
        name=self._company_name(symbol)
        if name.upper() == str(symbol).upper() or len(name) < 4:
            return []
        params={"q":f'"{name}" when:{max(1,int(hours/24) or 1)}d',"hl":"en-US","gl":"US","ceid":"US:en"}
        r=self.session.get(self.GOOGLE_NEWS_RSS,params=params,headers={"User-Agent":"Mozilla/5.0 TradingBot/5.6"},timeout=self.timeout);r.raise_for_status();root=ET.fromstring(r.content)
        out=[]
        for node in root.findall(".//item")[:self.max_each*2]:
            title=_clean_text(node.findtext("title"));link=_clean_text(node.findtext("link"));pub=_safe_dt(node.findtext("pubDate"));src=node.find("source");publisher=_clean_text(src.text if src is not None else "")
            if title and _within_hours(pub,hours) and self._company_matches(symbol, title):out.append(NewsItem("Google News",title,publisher,link,pub,[symbol],0.70,metadata={"publisher":publisher,"company_name":name}))
            if len(out)>=self.max_each:break
        self._mark("Google News",True,"Firmennamen-RSS erreichbar",len(out));return self._cache_put(key,out)

    def _source_timeout(self, source):
        # GDELT liefert im freien Betrieb regelmaessig erst nach mehr als
        # 10 Sekunden eine gueltige Antwort (beobachtet 13-14 s bei HTTP 200,
        # 16.09.2026). Der knappe globale Timeout erklaerte die Quelle
        # faelschlich fuer tot; der laengere Wert gilt ausschliesslich fuer
        # GDELT und lockert keine andere Quellen- oder Sicherheitsgrenze.
        if str(source).upper() == "GDELT":
            return max(self.timeout, float(getattr(
                config, "NEWS_SOURCE_GDELT_TIMEOUT_SECONDS", 30)))
        return self.timeout

    def _public_get(self, source, url, params=None):
        from public_news_http import get
        return get(self.session, url, root=STATUS_FILE.parent, ttl=self._ttl(source),
                   timeout=self._source_timeout(source), params=params)

    def _fed(self, symbol=None, hours=48, market=True):
        if symbol or not self.provider_configuration().get("Federal Reserve"):
            return None
        response = self._public_get("Federal Reserve", "https://www.federalreserve.gov/feeds/press_monetary.xml")
        root = ET.fromstring(response.content)
        if root.tag != 'rss' or root.find('channel') is None:
            raise ValueError("Federal-Reserve-Antwort ist kein RSS-Feed")
        rows = []
        for node in root.findall('.//item')[:100]:
            dt = _safe_dt(node.findtext('pubDate'))
            title = _clean_text(node.findtext('title'))
            if title and _within_hours(dt, hours):
                rows.append(NewsItem('Federal Reserve', title, _clean_text(node.findtext('description')),
                    _clean_text(node.findtext('link')), dt, [], .95, official=True,
                    metadata={'scope': 'macro'}))
        self._mark('Federal Reserve', True, 'Geldpolitik-RSS erreichbar; kein Einzelaktien-Signal', len(rows))
        return rows[:self.max_each]

    def _nasdaq_halts(self, symbol=None, hours=48, market=False):
        if not self.provider_configuration().get("Nasdaq Halts"):
            return None
        from nasdaq_halt_feed import trading_evidence
        result = self._cache_get(('nasdaq-halts',), min(60, self._ttl('Nasdaq Halts')))
        if result is None:
            r = self._public_get('Nasdaq Halts', self.NASDAQ_HALTS_RSS)
            root = ET.fromstring(r.content)
            result = trading_evidence(root, _now_utc())
            report = result['diagnostics']
            best_effort_json(STATUS_FILE.with_name("nasdaq_halt_diagnostics.json"), report,
                             label="Nasdaq-Halt-Diagnose")
            self._mark('Nasdaq Halts', report['complete'], report['detail'],
                       len(result['items']), coverage=report)
            self._cache_put(('nasdaq-halts',), result)
        rows = [x for x in result['items'] if self._usable(x, hours) and
                (not symbol or str(symbol).upper() in x.symbols)]
        return ProviderRows(rows, result['diagnostics'])

    def _alpha(self, symbol=None, hours=48, market=False):
        if not self.provider_configuration()["Alpha Vantage"]:
            return None
        key = ("alpha", symbol or "MARKET", int(hours))
        cached = self._cache_get(key, self._ttl("Alpha Vantage"))
        if cached is not None:
            return cached
        params = {"function": "NEWS_SENTIMENT", "limit": self.max_each,
                  "apikey": __import__("live_settings").alpha_vantage_key()}
        if symbol:
            params["tickers"] = symbol
        params["time_from"] = (_now_utc() - timedelta(hours=hours)).strftime("%Y%m%dT%H%M")
        r = self.session.get(self.ALPHA_URL, params=params, timeout=self.timeout)
        r.raise_for_status(); raw = r.json()
        if isinstance(raw, dict) and (raw.get("Information") or raw.get("Note")):
            raise RuntimeError(raw.get("Information") or raw.get("Note"))
        out = []
        for a in (raw.get("feed", []) if isinstance(raw, dict) else []):
            syms = []
            for ts in a.get("ticker_sentiment", []) or []:
                if ts.get("ticker"):
                    syms.append(ts["ticker"])
            out.append(NewsItem("Alpha Vantage", a.get("title", ""), a.get("summary", ""), a.get("url", ""),
                                _safe_dt(a.get("time_published")), syms, 0.86))
        self._mark("Alpha Vantage", True, "NEWS_SENTIMENT erreichbar", len(out))
        return self._cache_put(key, out)

    def _finnhub(self, symbol=None, hours=48, market=False):
        if not self.provider_configuration()["Finnhub"]:
            return None
        key = ("finnhub", symbol or "MARKET", int(hours))
        cached = self._cache_get(key, self._ttl("Finnhub"))
        if cached is not None:
            return cached
        api_key = __import__("live_settings").finnhub_key()
        if symbol:
            start = (_now_utc() - timedelta(hours=hours)).date().isoformat()
            end = _now_utc().date().isoformat()
            url = self.FINNHUB_BASE + "/company-news"
            params = {"symbol": symbol, "from": start, "to": end, "token": api_key}
        else:
            url = self.FINNHUB_BASE + "/news"
            params = {"category": "general", "token": api_key}
        r = self.session.get(url, params=params, timeout=self.timeout)
        r.raise_for_status(); rows = r.json()
        out = []
        for a in (rows or [])[: self.max_each]:
            dt = _safe_dt(a.get("datetime"))
            if not _within_hours(dt, hours):
                continue
            syms = [symbol] if symbol else []
            if a.get("related"):
                syms += [x.strip() for x in str(a.get("related")).split(",") if x.strip()]
            out.append(NewsItem("Finnhub", a.get("headline", ""), a.get("summary", ""), a.get("url", ""), dt, syms, 0.90))
        self._mark("Finnhub", True, "Company/General News erreichbar", len(out))
        return self._cache_put(key, out)

    def _fmp_get(self, path: str, params: dict | None = None, *,
                 purpose: str = "automatic"):
        """One transport and cache for news, NEXUS and PULSAR."""
        from fmp_reference import FMPReferenz
        from fmp_service import request, consumer
        client = FMPReferenz()
        with consumer("nexus_news"):
            return request(client.store, self.session, client._key, path, params,
                           timeout=self.timeout, purpose=purpose)

    def fmp_search_symbol(self, symbol: str, limit: int = 10) -> list[dict]:
        """Aktuelle FMP Stable Symbolsuche (read-only, Diagnose/Validierung)."""
        if not self.provider_configuration().get("FMP Symbol Search"):
            return []
        rows = self._fmp_get(
            "/search-symbol",
            {"query": str(symbol).upper(), "limit": max(1, min(50, int(limit)))},
            purpose="manual")
        out = list(rows or []) if isinstance(rows, list) else []
        self._mark("FMP Symbol Search", True, "Stable Symbolsuche erreichbar", len(out))
        return out

    def fmp_diagnostic(self, symbol: str = "AAPL") -> dict:
        """Expliziter FMP-Key/Stable-Endpoint-Test ohne Handelswirkung.

        Respektiert denselben Provider-Backoff wie der Live-News-Pfad, damit
        ein manueller Test nach HTTP 429/Quota-Fehler nicht sofort erneut
        gegen denselben Dienst feuert.
        """
        symbol=str(symbol or "AAPL").upper().strip()
        if not self.provider_configuration().get("FMP Symbol Search"):
            return {"configured": False, "symbol": symbol, "resolved": False, "news": 0}
        remaining = self._backoff_remaining("FMP")
        if remaining > 0:
            return {
                "configured": True, "symbol": symbol, "resolved": False, "news": 0,
                "backoff_seconds": remaining,
                "detail": f"FMP nach vorherigem Fehler noch ca. {max(1, remaining // 60)} min pausiert",
            }
        rows=self.fmp_search_symbol(symbol, limit=10)
        exact=next((r for r in rows if str(r.get("symbol","")).upper()==symbol), None)
        news=self._fmp(symbol, hours=168, purpose="manual") or []
        return {
            "configured": True, "symbol": symbol, "resolved": bool(exact),
            "company": str((exact or {}).get("name") or (exact or {}).get("companyName") or ""),
            "exchange": str((exact or {}).get("exchange") or (exact or {}).get("exchangeShortName") or ""),
            "news": len(news),
        }

    def _fmp(self, symbol=None, hours=48, market=False, *,
             purpose: str = "automatic"):
        if not self.provider_configuration()["FMP"]:
            return None
        key = ("fmp", symbol or "MARKET", int(hours))
        cached = self._cache_get(key, self._ttl("FMP"))
        if cached is not None:
            return cached
        if symbol:
            rows = self._fmp_get("/news/stock", {
                "symbols": str(symbol).upper(),
                "limit": 20,
            }, purpose=purpose)
        else:
            # Fuer ein Aktienuniversum ist stock-latest zielgerichteter als
            # allgemeine Nachrichten ohne Tickerbezug. Ein Abruf wird gecacht.
            rows = self._fmp_get(
                "/news/stock-latest", {"page": 0, "limit": 20},
                purpose=purpose)
        out = []
        for a in (rows or [])[: self.max_each]:
            dt = _safe_dt(a.get("publishedDate") or a.get("publishedAt") or a.get("date"))
            if not _within_hours(dt, hours):
                continue
            syms = []
            raw_sym = a.get("symbol") or a.get("symbols")
            if isinstance(raw_sym, list): syms = raw_sym
            elif raw_sym: syms = [x.strip() for x in str(raw_sym).split(",") if x.strip()]
            if symbol and not syms: syms = [symbol]
            out.append(NewsItem("FMP", a.get("title", ""), a.get("text") or a.get("snippet") or "",
                                a.get("url") or a.get("link") or "", dt, syms, 0.86))
        self._mark("FMP", True, "Stable Stock-News erreichbar", len(out),
                   endpoint="/stable/news/stock" if symbol else "/stable/news/stock-latest")
        return self._cache_put(key, out)

    def _massive(self, symbol=None, hours=48, market=False, *, cache_only=False):
        if not self.provider_configuration().get("MASSIVE"):
            return None
        key = ("massive-news", symbol or "MARKET", int(hours))
        cached = self._cache_get(key, self._ttl("MASSIVE"))
        if cached is not None:
            return cached
        live_key = __import__("live_settings").massive_key()
        if self._massive_client is None or self._massive_client._key != live_key:
            from massive_api import MassiveClient
            if self._massive_client is not None:
                self._massive_client.session.close()
            self._massive_client = MassiveClient(live_key)
        try:
            rows = self._massive_client.news(str(symbol or ""), limit=self.max_each, cache_only=cache_only)
        except MassivePaused as exc:
            if cache_only and exc.code == "MASSIVE_CACHE_MISS":
                return None
            raise
        out = []
        for row in rows:
            dt = _safe_dt(row.get("published_utc"))
            if not _within_hours(dt, hours):
                continue
            out.append(NewsItem(
                "MASSIVE", row.get("title", ""), row.get("description", ""),
                row.get("article_url", ""), dt, row.get("tickers") or ([symbol] if symbol else []),
                0.90, metadata={"publisher": (row.get("publisher") or {}).get("name", "")},
            ))
        transport = self._massive_client.status().get("transport", {})
        if transport.get("source") == "http":
            self._mark("MASSIVE", True, "Reference News erreichbar", len(out), endpoint="/v2/reference/news")
        return self._cache_put(key, out)

    def _sec_headers(self):
        email = __import__("live_settings").sec_kontakt()
        return {"User-Agent": f"TradingBot/9.8.8-NEXUS {email}", "Accept-Encoding": "gzip, deflate"}

    def _sec_ticker_map(self):
        if self._sec_tickers_cache is not None and time.time()-self._sec_tickers_loaded_at < 86400:
            return self._sec_tickers_cache
        r = self.session.get(self.SEC_TICKERS, headers=self._sec_headers(), timeout=self.timeout)
        r.raise_for_status(); raw = r.json()
        mapping = {}
        rows = raw.values() if isinstance(raw, dict) else raw
        for row in rows or []:
            t = str(row.get("ticker", "")).upper()
            if t:
                mapping[t] = {"cik": int(row.get("cik_str")), "title": row.get("title", "")}
        self._sec_tickers_cache = mapping; self._sec_tickers_loaded_at = time.time()
        return mapping

    def _sec(self, symbol=None, hours=48, market=False):
        if not symbol or not self.provider_configuration()["SEC EDGAR"]:
            return None
        key = ("sec", symbol, int(hours))
        cached = self._cache_get(key, self._ttl("SEC EDGAR"))
        if cached is not None:
            return cached
        row = self._sec_ticker_map().get(symbol.upper())
        if not row:
            return self._cache_put(key, [])
        cik = row["cik"]
        r = self.session.get(self.SEC_SUBMISSIONS.format(cik=cik), headers=self._sec_headers(), timeout=self.timeout)
        r.raise_for_status(); raw = r.json(); rec = ((raw.get("filings") or {}).get("recent") or {})
        forms = rec.get("form", []) or []
        dates = rec.get("filingDate", []) or []
        accessions = rec.get("accessionNumber", []) or []
        docs = rec.get("primaryDocument", []) or []
        items_col = rec.get("items", []) or []
        accepted = {"8-K", "8-K/A", "10-Q", "10-Q/A", "10-K", "10-K/A", "6-K", "20-F", "424B", "S-3", "S-1"}
        cutoff = (_now_utc() - timedelta(hours=hours)).date()
        out = []
        for i, form in enumerate(forms[:80]):
            if form not in accepted:
                continue
            try:
                d = datetime.strptime(dates[i], "%Y-%m-%d").date()
                if d < cutoff: continue
            except Exception:
                d = _now_utc().date()
            acc = str(accessions[i]).replace("-", "") if i < len(accessions) else ""
            doc = docs[i] if i < len(docs) else ""
            filing_items = items_col[i] if i < len(items_col) else ""
            url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{quote(str(doc))}" if acc and doc else ""
            # Richtung wird absichtlich NICHT geraten. SEC ist ein offizieller
            # Event-/Risikobeleg, nicht automatisch bullish/bearish.
            headline = f"SEC filing {form} – {row.get('title') or symbol}"
            summary = f"Offizielle EDGAR-Meldung. Items: {filing_items or 'n/a'}"
            out.append(NewsItem("SEC EDGAR", headline, summary, url,
                                datetime.combine(d, datetime.min.time(), tzinfo=timezone.utc), [symbol], 1.00,
                                official=True, metadata={"form": form, "items": filing_items}))
            if len(out) >= self.max_each:
                break
        self._mark("SEC EDGAR", True, "Company Submissions erreichbar", len(out))
        return self._cache_put(key, out)

    # -------------------------- Aggregation -------------------------------
    def _provider_call(self, name, fn, bundle):
        configured = self.provider_configuration().get(name, False)
        if not configured:
            bundle.sources_skipped.append(name)
            return []

        remaining = self._backoff_remaining(name)
        if remaining > 0:
            detail = f"temporär pausiert nach Fehler; neuer Versuch in ca. {max(1, remaining // 60)} min"
            bundle.sources_backoff[name] = detail
            bundle.sources_failed[name] = detail
            return []

        try:
            rows = fn()
            if rows is None:
                bundle.sources_skipped.append(name)
                return []
            coverage = getattr(rows, 'coverage', None)
            if coverage is not None:
                bundle.source_coverage[name] = coverage
                if not coverage.get('complete'):
                    detail = coverage.get('detail', 'Quellenabdeckung unvollstaendig')
                    bundle.sources_partial[name] = detail
                    # Preserve the existing consumer warning contract too.
                    bundle.sources_failed[name] = detail
                    return rows
            bundle.sources_ok.append(name)
            return rows
        except MassivePaused as exc:
            bundle.sources_backoff[name] = str(exc)
            if exc.code == "MASSIVE_HTTP_429":
                self._mark(name, False, str(exc), backoff_seconds=exc.retry_after,
                           endpoint="/v2/reference/news")
                bundle.sources_failed[name] = str(exc)
            else:
                self._mark_pause(name, exc)
            logger.debug("Newsquelle %s wartet auf Abruffreigabe: %s", name, exc)
            return []
        except Exception as exc:
            bundle.sources_failed[name] = redact(exc)
            backoff = self._failure_backoff(name, exc)
            failures = self._mark(name, False, exc, 0, backoff_seconds=backoff)
            # Nur der erste Fehler einer Serie kommt als WARNING ins Log.
            # Danach verhindert Backoff Log-Spam und unnoetige Requests.
            log = logger.warning if failures <= 1 else logger.debug
            log("Newsquelle %s fehlgeschlagen (Pause %ss): %s", name, backoff, redact(exc))
            return []

    @staticmethod
    def _dedupe(items):
        """Merge exact and near-duplicate syndicated headlines.

        Two publishers repeating the same original press release must not count
        as independent evidence. We therefore merge both canonical URL/exact
        headline duplicates and highly similar normalized headline token sets.
        """
        merged=[]
        def tokens(text):
            return {x for x in re.findall(r"[a-z0-9]{3,}",str(text).lower())
                    if x not in {"the","and","for","with","from","after","says","new","inc","corp"}}
        for item in items:
            if not item.headline:continue
            exact=_headline_key(item.headline,item.url)
            url_key=canonical_news_url(item.url)
            text_key=re.sub(r"\s+", " ", item.text().casefold()).strip()
            itok=tokens(item.headline)
            found=None
            for old,old_exact,otok,old_url,old_text in merged:
                similar=False
                if item.metadata.get('active_halt') or old.metadata.get('active_halt'):
                    # Structured exchange facts must not merge into a headline
                    # whose metadata cannot prove the same instrument/event.
                    if not (item.metadata.get('active_halt') and old.metadata.get('active_halt')
                            and item.symbols == old.symbols and item.published_at == old.published_at
                            and item.metadata.get('resumes_at') == old.metadata.get('resumes_at')
                            and item.metadata.get('observed_at') == old.metadata.get('observed_at')):
                        continue
                if (exact==old_exact or (url_key and url_key==old_url)
                        or (text_key and text_key==old_text)):
                    similar=True
                elif itok and otok:
                    j=len(itok & otok)/max(1,len(itok | otok))
                    similar=(j>=0.78 and len(itok & otok)>=4)
                if similar:
                    found=old;break
            if found is not None:
                lineage = social_news_lineage(found.as_dict()) or social_news_lineage(item.as_dict())
                urls = set(found.metadata.get("deduplicated_urls", [])) | set(item.metadata.get("deduplicated_urls", []))
                urls.update(u for u in (canonical_news_url(found.url), url_key) if u)
                found.metadata["deduplicated_urls"] = sorted(urls)
                if lineage:
                    found.metadata["social_lineage"] = True
                found.providers=sorted(set(found.providers+item.providers))
                found.symbols=sorted(set(found.symbols+item.symbols))
                found.official=found.official or item.official
                if len(item.summary)>len(found.summary):found.summary=item.summary
                if not found.url and item.url:found.url=item.url
                found.provider_weight=max(found.provider_weight,item.provider_weight)
            else:
                merged.append((item,exact,itok,url_key,text_key))
        rows=[x[0] for x in merged]
        rows.sort(key=lambda x:(bool(x.metadata.get('active_halt')),
                  x.published_at or datetime.min.replace(tzinfo=timezone.utc)),reverse=True)
        return rows

    @staticmethod
    def _bounded_items(items, limit):
        # The article display cap must never remove an active exchange halt.
        halts = [x for x in items if x.metadata.get('active_halt')]
        other = [x for x in items if not x.metadata.get('active_halt')]
        return halts + other[:max(0, int(limit)-len(halts))]

    def _attach_symbols(self, items, universe):
        symbols = [str(x).upper() for x in (universe or [])]
        if not symbols:
            return items
        # Erst exakte Ticker-Tokens. Fuer Ueberschriften, die meist den
        # Firmennamen statt AAPL/MSFT nennen, darf
        # optional der offizielle SEC-Ticker->Firmenname-Datensatz helfen.
        patterns = {
            s: re.compile(r"(?:\$|\b(?:NASDAQ|NYSE|AMEX)\s*:\s*)" + re.escape(s) + r"(?![A-Z0-9])")
            for s in symbols
        }
        name_patterns = {}
        if bool(getattr(config, "NEWS_COMPANY_NAME_MATCH_ENABLED", True)):
            try:
                mapping = self._sec_ticker_map() if self.provider_configuration().get("SEC EDGAR") else {}
                suffix = re.compile(
                    r"\s+(incorporated|inc\.?|corp\.?|corporation|company|co\.?|plc|ltd\.?|limited|holdings?)$",
                    re.I,
                )
                for sym in symbols:
                    title = _clean_text((mapping.get(sym) or {}).get("title", ""))
                    title = suffix.sub("", title).strip(" .,-")
                    if len(title) >= 4:
                        name_patterns[sym] = re.compile(r"\b" + re.escape(title) + r"\b", re.I)
            except Exception as exc:
                logger.debug("Firmenname-Zuordnung aus SEC nicht verfuegbar: %s", exc)
        for item in items:
            if item.metadata.get('scope') == 'macro':
                item.symbols = []
                continue
            if item.symbols:
                continue
            text = item.text()
            source=str(getattr(item,"source","") or "").upper()
            # GDELT bleibt bewusst eine breite Markt-/Krisenquelle. Kurze
            # Ticker wie KO, ALL oder ALGO sind dort zu mehrdeutig und duerfen
            # keine Einzelaktie priorisieren. Falls ueberhaupt eine Firma aus
            # GDELT zugeordnet wird, dann nur ueber den offiziellen SEC-Namen.
            if source.startswith("GDELT") or item.metadata.get("scope") == "market":
                found = [sym for sym, pat in name_patterns.items() if pat.search(text)] if name_patterns else []
            else:
                found = [sym for sym, pat in patterns.items() if pat.search(text)]
                if not found and name_patterns:
                    found = [sym for sym, pat in name_patterns.items() if pat.search(text)]
            if found:
                item.symbols = found[:8]
        return items

    def _market_items_for_symbol(self, symbol, hours=48):
        """Bereits gepoolte Markt-News fuer ein Symbol nutzen.

        Das ist der wichtigste API-Schutz der v5.2: breite Feeds werden einmal
        pro Cache-Fenster geladen und fuer das gesamte Aktienuniversum wiederverwendet.
        Erst danach werden – falls erlaubt – gezielte Company-News nachgeladen.
        """
        market = self.fetch_market(hours=hours, universe=[symbol])
        symbol = str(symbol).upper().replace("/", "")
        rows = []
        for item in market.items:
            if symbol in item.symbols and item.metadata.get("scope") != "macro" and self._usable(item, hours):
                rows.append(item)
        return rows, market

    def fetch_symbol(self, symbol, hours=48, focused=True):
        """Nachrichten fuer ein Instrument.

        Stufe 1: kostenlose Kernfeeds (GDELT/SEC) plus optionale Zusatzquellen
        aus dem Prozess-Cache filtern.
        Stufe 2: gezielte Company-Abfragen nur fuer Quellen, bei denen sie
        aktiviert sind. Alpha Vantage focused ist standardmaessig AUS, damit
        ein begrenztes API-Budget nicht beim Durchlauf durch das Aktienuniversum
        verbraucht wird.
        """
        symbol = str(symbol).upper().replace("/", "")
        bundle = NewsBundle(); items = []

        pooled, market_bundle = self._market_items_for_symbol(symbol, hours)
        items.extend(pooled)
        bundle.sources_ok.extend(market_bundle.sources_ok)
        bundle.sources_skipped.extend(market_bundle.sources_skipped)
        bundle.sources_failed.update(market_bundle.sources_failed)
        bundle.sources_backoff.update(market_bundle.sources_backoff)
        bundle.sources_partial.update(market_bundle.sources_partial)
        bundle.source_coverage.update(market_bundle.source_coverage)

        if focused:
            providers = [
                ("SEC EDGAR", lambda: self._sec(symbol, hours), bool(getattr(config, "NEWS_FOCUSED_SEC_ENABLED", True))),
                ("Nasdaq Halts", lambda: self._nasdaq_halts(symbol, hours), bool(getattr(config, "NEWS_FOCUSED_NASDAQ_HALTS_ENABLED", True))),
                ("Yahoo Finance", lambda: self._yahoo(symbol, hours), bool(getattr(config, "NEWS_FOCUSED_YAHOO_ENABLED", True))),
                ("Google News", lambda: self._google_news(symbol, hours), bool(getattr(config, "NEWS_FOCUSED_GOOGLE_NEWS_ENABLED", True))),
                ("Alpha Vantage", lambda: self._alpha(symbol, hours),
                 bool(getattr(config, "NEWS_FOCUSED_ALPHA_VANTAGE_ENABLED", False))),
                ("Finnhub", lambda: self._finnhub(symbol, hours),
                 bool(getattr(config, "NEWS_FOCUSED_FINNHUB_ENABLED", True))),
                ("FMP", lambda: self._fmp(symbol, hours),
                 self.provider_configuration().get("FMP", False)),
                ("MASSIVE", lambda: self._massive(symbol, hours), True),
            ]
            for name, fn, allowed in providers:
                if not allowed:
                    continue
                if name == "MASSIVE" and self._fmp_news_sufficient(items, hours):
                    fn = lambda: self._massive(symbol, hours, cache_only=True)
                rows = self._provider_call(name, fn, bundle)
                items.extend(rows)

        bundle.sources_ok = sorted(set(bundle.sources_ok))
        bundle.sources_skipped = sorted(set(bundle.sources_skipped))
        bundle.items = self._bounded_items(self._dedupe([x for x in items if symbol in x.symbols and self._usable(x, hours)]),
                                          getattr(config, "NEWS_MAX_ARTICLES", 20))
        return bundle

    def fetch_market(self, hours=24, universe=None):
        bundle = NewsBundle(); items = []
        providers = [
            ("GDELT", lambda: self._gdelt(None, hours, market=True)),
            ("Federal Reserve", lambda: self._fed(None, hours, market=True)),
            ("Nasdaq Halts", lambda: self._nasdaq_halts(None, hours, market=True)),
            # Alpha Vantage NEWS_SENTIMENT wird gezielt fuer bereits relevante
            # Ticker genutzt; kein pauschaler Marktabruf, um das API-Budget zu schonen.
            ("Finnhub", lambda: self._finnhub(None, hours, market=True)),
            ("FMP", lambda: self._fmp(None, hours, market=True)),
            ("MASSIVE", lambda: self._massive(None, hours, market=True)),
        ]
        for name, fn in providers:
            if name == "MASSIVE" and self._fmp_news_sufficient(items, hours):
                fn = lambda: self._massive(None, hours, market=True, cache_only=True)
            items.extend(self._provider_call(name, fn, bundle))
        items = self._attach_symbols(items, universe)
        bundle.sources_ok = sorted(set(bundle.sources_ok))
        bundle.sources_skipped = sorted(set(bundle.sources_skipped))
        bundle.items = self._bounded_items(self._dedupe([x for x in items if self._usable(x, hours)]),
                                          getattr(config, "NEWS_MARKET_MAX_ARTICLES", 80))
        return bundle

    def _fmp_news_sufficient(self, items, hours):
        """Reuse complete primary news; three duplicates are not three facts."""
        return sum("FMP" in (x.providers or [x.source]) and bool(x.url and x.summary)
                   and self._usable(x, hours) for x in self._dedupe(items)) >= 3

    @staticmethod
    def _usable(item, hours):
        if item.metadata.get('active_halt'):
            checked = _safe_dt(item.metadata.get('observed_at'))
            resumed = _safe_dt(item.metadata.get('resumes_at'))
            return bool(checked and 0 <= (_now_utc() - checked).total_seconds() <= 90
                        and (not resumed or resumed > _now_utc()))
        return _within_hours(item.published_at, hours)

    def active_health_test(self, source: str, symbol: str = "AAPL") -> dict:
        """Bewusster echter Netzwerktest fuer genau eine Quelle.

        Der normale Dashboard-Status ist passiv und verbraucht kein Budget.
        Diese Methode wird nur ueber den ausdruecklichen Test-Knopf aufgerufen.
        """
        name = str(source or "").strip()
        if name.lower() in {"finanzen.net", "finanzen_net"}:
            return {"ok": False, "source": "finanzen.net", "retired": True,
                    "detail": ("finanzen.net ist in NEXUS 8.2 entfernt, weil der "
                               "automatisierte RSS-Abruf wiederholt blockiert wurde."),
                    "tested_at": _now_utc().isoformat()}
        conf = self.provider_configuration()
        if name not in conf:
            return {"ok": False, "source": name, "detail": "unbekannte Quelle"}
        if not conf[name]:
            return {"ok": False, "source": name, "detail": "nicht aktiviert oder Zugang fehlt"}
        remaining = self._backoff_remaining(name)
        if remaining:
            return {"ok": False, "source": name, "detail": f"Quellenpause noch {remaining}s; kein erneuter Abruf",
                    "backoff_seconds": remaining}
        try:
            if name == "Federal Reserve":
                rows = self._fed(None, 48) or []
            elif name == "FMP Symbol Search":
                # Ueber den budgetgefuehrten Referenzclient testen, damit der
                # Test dieselben 250 Anfragen pro Tag mitzaehlt wie der Betrieb.
                import fmp_reference
                ergebnis = fmp_reference.client().verbindungstest()
                self._mark(name, bool(ergebnis.get("ok")), ergebnis.get("detail", ""),
                           int(ergebnis.get("count", 0) or 0))
                return {"source": name, **ergebnis, "tested_at": _now_utc().isoformat()}
            elif name == "FMP":
                # Nachrichten sind im FMP-Gratistarif nachweislich nicht
                # enthalten (HTTP 402). Das ist eine Tarifaussage und keine
                # Stoerung -- der Test sagt das deshalb im Klartext, statt
                # eine Fehlermeldung zu erzeugen, die nach Defekt aussieht.
                if not __import__("fmp_reference").client().erlaubt("/news/stock"):
                    detail = ("FMP-Nachrichten sind für diesen Endpunkt derzeit nicht freigegeben. "
                              "Der deklarierte Tarif und die übrigen Endpunkte werden getrennt geprüft.")
                    self._mark(name, False, detail, 0)
                    return {"ok": False, "source": name, "tarif": True, "detail": detail,
                            "tested_at": _now_utc().isoformat()}
                self._cache.pop(("fmp", symbol, 48), None)
                rows = self._fmp(symbol, 48) or []
            elif name == "MASSIVE":
                from massive_api import MassiveClient
                result = MassiveClient().verbindungstest()
                news_probe = result.get("faehigkeiten", {}).get("Nachrichten", {})
                if news_probe.get("pending"):
                    self._mark_pause(name, MassivePaused(news_probe.get("detail", "Abrufpause"),
                                                       news_probe.get("retry_after", 60), news_probe.get("code", "MASSIVE_PAUSED")))
                elif news_probe.get("transport", {}).get("source") == "http" or news_probe.get("ok") is False:
                    self._mark(name, news_probe.get("ok") is True, news_probe.get("detail", ""),
                               news_probe.get("count", 0), endpoint="/v2/reference/news")
                return {"source": name, **result, "tested_at": _now_utc().isoformat()}
            elif name == "GDELT":
                self._cache.pop(("gdelt", "MARKET", 24), None)
                rows = self._gdelt(None, 24, market=True) or []
            elif name == "Finnhub":
                self._cache.pop(("finnhub", symbol, 48), None)
                rows = self._finnhub(symbol, 48) or []
            elif name == "Alpha Vantage":
                self._cache.pop(("alpha", symbol, 48), None)
                rows = self._alpha(symbol, 48) or []
            elif name == "SEC EDGAR":
                self._sec_tickers_cache = None
                rows = [self._sec_ticker_map().get(symbol)]
                self._mark(name, True, "Tickerkatalog erreichbar", int(bool(rows[0])))
            elif name == "Nasdaq Halts":
                self._cache.pop(("nasdaq-halts",), None)
                rows = self._nasdaq_halts(None, 48) or []
                coverage = self._status.get(name, {}).get('coverage', {})
                return {"ok": coverage.get('complete', False), "source": name, "count": len(rows),
                        "coverage": coverage, "partial": not coverage.get('complete', False),
                        "detail": coverage.get('detail', ''), "tested_at": _now_utc().isoformat()}
            elif name == "Google News":
                self._cache.pop(("google-news", symbol, 48), None)
                rows = self._google_news(symbol, 48) or []
            elif name == "Yahoo Finance":
                self._cache.pop(("yahoo-yfinance", symbol, 48), None)
                rows = self._yahoo(symbol, 48) or []
            else:
                rows = []
            return {"ok": True, "source": name, "count": len(rows),
                    "detail": "Verbindung erfolgreich", "tested_at": _now_utc().isoformat()}
        except Exception as exc:
            backoff = self._failure_backoff(name, exc)
            self._mark(name, False, str(exc), 0, backoff_seconds=backoff)
            return {"ok": False, "source": name, "detail": redact(exc)[:300],
                    "tested_at": _now_utc().isoformat()}


def configured_sources():
    return MultiSourceNews().provider_configuration()


def source_health():
    """Lokaler letzter Quellenstatus fuer GUI/Diagnose, ohne API-Aufruf."""
    return MultiSourceNews().health_snapshot()
