"""X collection, attention coverage and advisory topic extraction.

No unverified text enters news_filter.marktlage/global_crisis_score. Financial
budgets do not grant trading authority. Counts cover exact complete UTC days;
post search is an explicitly bounded sample, never a global-volume claim.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
import json
import ipaddress
import logging
import math
import re
import secrets
import statistics
import threading
import time

from . import account_registry, store, candidate_research
from .source_registry import search_plan, public_registry

DAY = 86400
COUNTS = "/2/tweets/counts/recent"
SEARCH = "/2/tweets/search/recent"
PRICE_DATE = "2026-09-14"
PRICE_URL = "https://docs.x.com/x-api/getting-started/pricing"
PRICE_MICRO_EUR = 6500  # USD .005 * EUR1/USD * 1.30 reserve buffer.
# 10.3.0: Die taeglichen X-Zaehlungsabrufe existierten nur fuer die
# 14/28-Tage-Baseline der alten Score-Maschine. Mit der Hype-Spur entfallen
# sie; das frei gewordene Budget traegt fuenf Kandidaten-Suchen pro Tag
# (plus zwei Makro-/Accountsuchen) im unveraenderten 15-EUR-Monatslimit:
# 7 Suchen x 10 Posts x 31 Tage x 6500 Mikro-EUR = 14,11 EUR.
MAX_COUNTS_PER_DAY = 0
CANDIDATE_SEARCHES_PER_DAY = 5
GENERAL_SEARCHES_PER_DAY = 2
MAX_MONITORED_SYMBOLS = 12
DEFAULTS = {"enabled": False, "monthly_budget_eur": 15.0, "symbols": [],
    "priority_accounts": [], "pricing_acknowledged": False,
    "counts_interval_hours": 24, "posts_per_request": 10, "searches_per_day": 5}
SYMBOL = re.compile(r"^[A-Z][A-Z0-9.\-]{0,11}$")
ACCOUNT = re.compile(r"^[A-Za-z0-9_]{1,15}$")
TOPICS = {
    "TARIFFS_TRADE": (r"\btariffs?\b|\bexport (?:ban|restriction)|\btrade (?:ban|embargo)", "Zölle / Handelsbeschränkungen"),
    "SANCTIONS": (r"\bsanctions?\b|\bsanktionen\b", "Sanktionen"),
    "CENTRAL_BANK": (r"\b(?:fed|ecb|central bank)\b.*\b(?:rate|policy|inflation)\b|\brate (?:decision|cut|hike)", "Zentralbank / Zinsen"),
    "CYBERATTACK": (r"\bcyberattack\b|\bransomware\b|\bdata breach\b", "Cyberangriff / Datenleck"),
    "EXCHANGE_OUTAGE": (r"\b(?:exchange|broker|trading) outage\b", "Börsen- / Brokerausfall"),
    "MILITARY_ESCALATION": (r"\b(?:military escalation|missile strike|armed conflict|invasion of)\b", "Militärische Eskalation"),
    "ENERGY_SUPPLY": (r"\b(?:oil|gas|energy) (?:supply|disruption|embargo)\b", "Energieversorgung"),
    "COMPANY_EARNINGS": (r"\b(?:earnings|revenue|guidance|quarterly results)\b", "Unternehmensergebnis / Ausblick"),
    "BANKING_STRESS": (r"\b(?:bank run|bank failure|liquidity crisis|sovereign default)\b", "Banken- / Zahlungsausfallrisiko"),
    "REGULATION": (r"\b(?:sec|regulator|antitrust)\b|\bregulatory approval\b", "Regulierung"),
}
MACRO_QUERY = '((tariffs OR sanctions OR "rate decision" OR "export ban" OR "military escalation" OR "cyberattack") (market OR economy OR stocks OR bank)) lang:en -is:retweet'
_thread = None
_stop = threading.Event()
_thread_lock = threading.Lock()


digest = store.digest  # 10.8.0 (Schritt 2): kanonischer Hash liegt in store


def utc(stamp):
    return datetime.fromtimestamp(stamp, timezone.utc)


def iso(stamp):
    return utc(stamp).isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse_time(value):
    result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Zeitbeleg ohne Zeitzone")
    return result.timestamp()


def _validate_settings(raw):
    """Validate every load and reservation, not just the WebUI write path."""
    if not isinstance(raw, dict) or set(raw) - (set(DEFAULTS) | {"pricing_acknowledged_at"}):
        raise ValueError("Unbekannte oder ungültige gespeicherte X-Einstellung")
    data = {**DEFAULTS, **raw}
    for key in ("enabled", "pricing_acknowledged"):
        if type(data[key]) is not bool:
            raise ValueError("Schalter müssen wahr/falsch sein")
    budget = data["monthly_budget_eur"]
    if type(budget) not in (int, float) or not math.isfinite(budget) or not 0 < budget <= 15:
        raise ValueError("X-Monatsbudget muss größer 0 und höchstens 15 EUR sein")
    data["monthly_budget_eur"] = float(budget)
    for key, regex, maximum in (("symbols", SYMBOL, 12), ("priority_accounts", ACCOUNT, 5)):
        if not isinstance(data[key], list) or len(data[key]) > maximum:
            raise ValueError(f"{key}: höchstens {maximum} Einträge")
        if any(not isinstance(s, str) or not regex.fullmatch(s) for s in data[key]):
            raise ValueError(f"Ungültiger Eintrag in {key}")
        data[key] = list(dict.fromkeys(data[key]))
    for key in ("counts_interval_hours", "posts_per_request", "searches_per_day"):
        allowed = {DEFAULTS[key]}
        if key == "searches_per_day":
            # Alte Installationen tragen noch den Wert 3 aus dem 10.2.x-Plan;
            # er wird beim Laden auf den festen 10.3.0-Plan angehoben, nie
            # darueber hinaus.
            allowed.add(3)
        if type(data[key]) is not int or data[key] not in allowed:
            raise ValueError("Der sparsame Abrufplan ist fest auf 10 Posts / 5 Kandidaten-Suchen begrenzt")
        data[key] = DEFAULTS[key]
    stamp = data.get("pricing_acknowledged_at", 0)
    if type(stamp) not in (int, float) or not math.isfinite(stamp) or stamp < 0:
        raise ValueError("Ungültiger Zeitbeleg der X-Preisbestätigung")
    return data


def _settings(con):
    row = con.execute("SELECT payload FROM settings WHERE id=1").fetchone()
    try:
        return _validate_settings(json.loads(row[0]) if row else {})
    except (ValueError, TypeError, OverflowError):
        # Preserve the invalid record for inspection. A local file edit cannot
        # increase the financial cap or turn a string such as 'false' into True.
        return {**_validate_settings({}), "_configuration_error": True}


def _price_current(settings, now):
    # A software estimate cannot certify a future provider invoice. Require a
    # renewed console check after 30 days rather than keep stale prices forever.
    stamp = settings.get("pricing_acknowledged_at", 0)
    return bool(settings["pricing_acknowledged"] and type(stamp) in (int, float)
                and 0 <= now-stamp <= 30*DAY)


def _token():
    try:
        from credential_store import load_credentials
        value = load_credentials(store.ROOT / "market_intelligence_credentials.json", {})
        return str(value.get("bearer_token") or "")
    except (OSError, ValueError, TypeError):
        return ""


def _settings_public(settings):
    invalid = settings.get("_configuration_error") is True
    try:
        clean = _validate_settings({k: v for k, v in settings.items() if k != "_configuration_error"})
    except (ValueError, TypeError, OverflowError):
        clean, invalid = _validate_settings({}), True
    safe = {key: clean[key] for key in DEFAULTS}
    safe["pricing_acknowledged_at"] = clean.get("pricing_acknowledged_at", 0)
    return {**safe, "configuration_error": invalid, "token_configured": bool(_token()),
        "estimated_month_eur": round(
            (MAX_COUNTS_PER_DAY+(CANDIDATE_SEARCHES_PER_DAY+GENERAL_SEARCHES_PER_DAY)*10)*31*PRICE_MICRO_EUR/1e6, 2),
        "price_basis_date": PRICE_DATE, "price_source": PRICE_URL,
        "post_usd": .005, "counts_request_usd": .005, "eur_reserve_multiplier": 1.30,
        "cost_scope": "Nur X-Abrufe dieser Installation; GPT und andere X-Clients separat",
        "raw_retention_days": 7, "mode": "DISCOVERY_RESEARCH", "trade_effect": False,
        "automatic_discovery": True, "manual_symbols_required": False,
        "candidate_searches_per_day": CANDIDATE_SEARCHES_PER_DAY,
        "total_searches_per_day_max": CANDIDATE_SEARCHES_PER_DAY+GENERAL_SEARCHES_PER_DAY,
        "counts_per_day_max": MAX_COUNTS_PER_DAY, "monitored_symbols_max": MAX_MONITORED_SYMBOLS}


def get_settings():
    with store.db(readonly=True) as con:
        return _settings_public(_settings(con))


def save_settings(changes, bearer_token=None):
    if not isinstance(changes, dict):
        raise ValueError("Einstellungen müssen ein Objekt sein")
    changes = dict(changes)
    if "bearer_token" in changes:
        bearer_token = changes.pop("bearer_token")
    if set(changes) - set(DEFAULTS):
        raise ValueError("Unbekannte X-Einstellung")
    with store.db() as con:
        data = {**_settings(con), **changes}
        data.pop("_configuration_error", None)
        if changes.get("pricing_acknowledged") is True:
            data["pricing_acknowledged_at"] = time.time()
        data = _validate_settings(data)
        if bearer_token is not None:
            if not isinstance(bearer_token, str) or len(bearer_token) > 4096 or any(c.isspace() for c in bearer_token):
                raise ValueError("Bearer Token ist ungültig")
        candidate_token = bearer_token if bearer_token is not None else _token()
        if data["enabled"] and (not candidate_token or not _price_current(data, time.time())):
            raise ValueError("Vor Aktivierung Bearer Token und aktuellen Preis / Anbieterlimit bestätigen")
        if bearer_token is not None:
            from credential_store import save_credentials
            save_credentials(store.ROOT / "market_intelligence_credentials.json", {"bearer_token": bearer_token})
        con.execute("INSERT OR REPLACE INTO settings VALUES(1,?)", (store.encode(data),))
        _refresh_control(con, _effective_settings(con, data), time.time())
    result = _settings_public(data)
    store.atomic_json("market_intelligence_settings.json", result)
    _publish()
    return result


def _queries(settings):
    symbols = settings["symbols"]
    if not symbols:
        return []
    control = "(" + " OR ".join("$"+s for s in sorted(symbols)) + ") lang:en -is:retweet"
    return [("__MARKET_CONTROL__", control)] + [(s, "$"+s+" lang:en -is:retweet") for s in symbols]


def _effective_settings(con, settings):
    """Manual monitoring is optional. An automatic cohort remains stable."""
    cohort = store.value(con, "discovery_monitoring_cohort", [])
    symbols = list(settings["symbols"])
    symbols += [r["symbol"] for r in cohort if r["symbol"] not in symbols and r.get("profile_source_id")]
    return {**settings, "symbols": symbols[:MAX_MONITORED_SYMBOLS]}


def _refresh_control(con, settings, now):
    query = dict(_queries(settings)).get("__MARKET_CONTROL__", "")
    qhash = digest({"query": query, "version": 1})
    previous = store.value(con, "normalization", {})
    if previous.get("control_query_hash") != qhash:
        store.put(con, "normalization", {"control_query_hash": qhash,
            "previous_control_query_hash": previous.get("control_query_hash"),
            "changed_at": now, "reason": "INITIAL_COHORT" if not previous else "MONITORING_COHORT_CHANGED",
            "members": sorted(settings["symbols"]),
            "detail": "Normierung benötigt vollständige Vergleichstage mit derselben Symbolgruppe; Einzelreihen bleiben erhalten."})


def _refresh_monitoring(con, settings, now):
    candidates = _discovery_candidates(con, now, limit=100)
    validations = {r["symbol"]: r for r in store.value(con, "candidate_validations", [])}
    observed = {r["symbol"]: r["observed_at"] for r in candidates}
    cohort = store.value(con, "discovery_monitoring_cohort", [])
    retained = []
    for row in cohort:
        row = dict(row)
        if not row.get("profile_source_id") or validations.get(row["symbol"], {}).get("status") == "ETF_OR_FUND":
            continue
        row["last_observed"] = max(row.get("last_observed", 0), observed.get(row["symbol"], 0))
        # A research cohort gets enough time to collect a 28-day baseline.
        if now-row["added_at"] <= 35*DAY or now-row["last_observed"] <= 7*DAY:
            retained.append(row)
    occupied = set(settings["symbols"]) | {r["symbol"] for r in retained}
    for candidate in candidates:
        symbol = candidate["symbol"]
        proof = validations.get(symbol, {})
        if (symbol not in occupied and len(occupied) < MAX_MONITORED_SYMBOLS
                and proof.get("status") == "SINGLE_STOCK" and 0 <= now-proof["observed_at"] <= DAY):
            retained.append({"symbol": symbol, "added_at": now, "last_observed": candidate["observed_at"],
                "profile_source_id": proof["profile_source_id"], "profile_observed_at": proof["observed_at"]})
            occupied.add(symbol)
    store.put(con, "discovery_monitoring_cohort", retained[:MAX_MONITORED_SYMBOLS])
    effective = _effective_settings(con, settings)
    _refresh_control(con, effective, now)
    return effective


def request_confirmation(symbol, profile_source, *, now=None):
    """Punkt 3 (10.8.0): X-Bestaetigungssuche anfordern (Fassade; keine Netzanfrage hier)."""
    now = time.time() if now is None else now
    with store.db(readonly=True) as con:
        if not _settings(con)["enabled"]:
            return {"accepted": False, "reason": "X-Recherche nicht eingerichtet"}
    return candidate_research.request_confirmation(symbol, profile_source, now=now)


def record_candidate_validation(symbol, profile_source, *, now=None):
    """Enroll only a cached, dated FMP single-stock identity. No paid calls.

    This receipt grants a counts observation slot, never trade eligibility. A
    fresh profile is still required by PULSAR for each substantive assessment.
    """
    now = time.time() if now is None else now
    if not isinstance(symbol, str) or not SYMBOL.fullmatch(symbol) or not isinstance(profile_source, dict):
        return {"accepted": False, "status": "UNKNOWN"}
    profile = profile_source.get("data")
    stamp = profile_source.get("observed_at")
    identity = profile_source.get("id")
    if (profile_source.get("provider") != "FMP" or profile_source.get("kind") != "profile"
            or not isinstance(profile, dict) or profile.get("symbol") != symbol
            or type(stamp) not in (int, float) or not math.isfinite(stamp) or not 0 <= now-stamp <= DAY
            or not isinstance(identity, str) or not re.fullmatch(r"[a-f0-9]{64}", identity)):
        return {"accepted": False, "status": "UNKNOWN"}
    status = ("ETF_OR_FUND" if profile.get("isEtf") is True or profile.get("isFund") is True else
              "SINGLE_STOCK" if profile.get("isEtf") is False and profile.get("isFund") is False else "UNKNOWN")
    if status == "UNKNOWN":
        return {"accepted": False, "status": status}
    with store.db() as con:
        settings = _settings(con)
        if not settings["enabled"]:
            return {"accepted": False, "status": "DISABLED"}
        old = [r for r in store.value(con, "candidate_validations", []) if r["symbol"] != symbol]
        old.append({"symbol": symbol, "status": status, "profile_source_id": identity,
            "observed_at": stamp, "recorded_at": now})
        store.put(con, "candidate_validations", sorted(old, key=lambda r: r["recorded_at"])[-100:])
        effective = _refresh_monitoring(con, settings, now)
    return {"accepted": status == "SINGLE_STOCK", "status": status,
            "monitoring": symbol in effective["symbols"], "trade_effect": False}


def _reserve(kind, query, context, window, now):
    if kind not in {"search", "counts"}:
        raise ValueError("X-Anfrageart ohne Preisbeleg")
    query_hash = digest({"query": query, "version": 1})
    request_key = digest({"kind": kind, "query_hash": query_hash, "window": window})
    month, day = iso(now)[:7], iso(now)[:10]
    cost = PRICE_MICRO_EUR * (10 if kind == "search" else 1)
    with store.db() as con:
        settings = _settings(con)
        if not settings["enabled"] or not _price_current(settings, now) or not _token():
            return None
        if store.value(con, "cooldown_until", 0) > now:
            return None
        if con.execute("SELECT 1 FROM requests WHERE request_key=?", (request_key,)).fetchone():
            return None
        if kind == "search":
            targeted = context in (candidate_research.CONTEXT, candidate_research.CONFIRM_CONTEXT)
            if context == candidate_research.CONFIRM_CONTEXT:
                # 10.8.0: Bestaetigungssuchen zaehlen gegen ihr eigenes Tageslimit.
                count = con.execute("SELECT count(*) FROM requests WHERE day=? AND kind='search' AND context=?",
                                    (day, context)).fetchone()[0]
                limit = candidate_research.CONFIRMATIONS_PER_DAY
            elif targeted:
                count = con.execute("SELECT count(*) FROM requests WHERE day=? AND kind='search' AND context=?",
                                    (day, context)).fetchone()[0]
                limit = CANDIDATE_SEARCHES_PER_DAY
            else:
                count = con.execute("SELECT count(*) FROM requests WHERE day=? AND kind='search' AND context NOT IN (?,?)",
                                    (day, candidate_research.CONTEXT, candidate_research.CONFIRM_CONTEXT)).fetchone()[0]
                limit = GENERAL_SEARCHES_PER_DAY
            if count >= limit:
                return None
            if targeted:
                plan = candidate_research.plan(con, now)
                if not plan or query != plan['query'] or plan.get('context', candidate_research.CONTEXT) != context:
                    return None
        else:
            count = con.execute("SELECT count(*) FROM requests WHERE day=? AND kind='counts'", (day,)).fetchone()[0]
            if count >= MAX_COUNTS_PER_DAY:
                return None
        spent = con.execute("SELECT coalesce(sum(charged+reserved),0) FROM requests WHERE month=?", (month,)).fetchone()[0]
        if spent+cost > round(settings["monthly_budget_eur"]*1e6):
            store.put(con, "last_state", {"state": "BUDGET_PAUSED", "detail": "X-Monatsbudget ausgeschöpft; NEXUS läuft ohne X weiter"})
            return None
        request_id = secrets.token_hex(12)
        con.execute("INSERT INTO requests(id,request_key,kind,query_hash,context,started,month,day,reserved,status) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (request_id, request_key, kind, query_hash, context, now, month, day, cost, "RESERVED"))
        request = {"id": request_id, "query_hash": query_hash, "kind": kind, "context": context, "cost": cost}
        if context in (candidate_research.CONTEXT, candidate_research.CONFIRM_CONTEXT):
            candidate_research.reserve_receipt(con, request, query, now)
        return request


def _finish(request, now, *, status, http=None, body_hash="", received=0, processed=0, duplicates=0, coverage="FEHLER", cooldown=0):
    # Never assume that a timeout or error was not billed. Keep the whole
    # reservation as a charged upper estimate; do not discount X deduplication.
    with store.db() as con:
        con.execute("UPDATE requests SET charged=reserved,reserved=0,status=?,http=?,body_hash=?,received=?,processed=?,duplicates=?,coverage=?,finished=? WHERE id=?",
                    (status, http, body_hash, received, processed, duplicates, coverage, now, request["id"]))
        store.put(con, "last_state", {"state": status, "detail": {
            "OK": "X antwortet; Suchergebnisse werden als begrenzte Stichprobe verarbeitet",
            "AUTH_ERROR": "X-Zugang abgelehnt; Bearer Token / Zugriffsrechte prüfen",
            "RATE_LIMITED": "X-Ratengrenze; Abrufe pausieren",
            "HTTP_ERROR": "X-Abruf fehlgeschlagen; NEXUS läuft ohne X weiter",
            "INVALID_RESPONSE": "X-Antwort unvollständig oder ungültig; keine Nullwerte erfunden",
            "NETWORK_ERROR": "X nicht erreichbar; NEXUS läuft ohne X weiter"}.get(status, status)})
        if cooldown:
            store.put(con, "cooldown_until", max(store.value(con, "cooldown_until", 0), now+cooldown))


def _coverage_receipt(con, request, now, expected, observed, *, invalid_rows=0, has_errors=False, has_next_page=False, state):
    receipts = store.value(con, "counts_coverage", [])
    old = next((r for r in receipts if r["query_hash"] == request["query_hash"] and r["symbol"] == request["context"]), {})
    today = iso(now)[:10]
    incomplete = 0 if state == "COMPLETE" else old.get("consecutive_incomplete_days", 0) + int(old.get("checked_day") != today)
    row = {"query_hash": request["query_hash"], "symbol": request["context"], "checked_at": now, "checked_day": today,
        "expected_days": len(expected), "observed_days": len(observed), "missing_days": sorted(set(expected)-set(observed)),
        "invalid_rows": invalid_rows, "has_errors": bool(has_errors), "has_next_page": bool(has_next_page),
        "state": state, "consecutive_incomplete_days": incomplete,
        "detail": "Fehlende Tage bleiben unbekannt; wiederholt unvollständige Tagesdaten sind ein Abdeckungsproblem, kein normales Aufwärmen." if state != "COMPLETE" else "Alle angeforderten UTC-Tageswerte explizit erhalten."}
    receipts = [r for r in receipts if not (r["query_hash"] == row["query_hash"] and r["symbol"] == row["symbol"])]
    store.put(con, "counts_coverage", (receipts + [row])[-52:])


def _counts(request, body, start, end, now):
    expected = [iso(day)[:10] for day in range(int(start), int(end), DAY)]
    rows = body.get("data")
    if not isinstance(rows, list):
        with store.db() as con:
            _coverage_receipt(con, request, now, expected, {}, invalid_rows=1, state="INVALID_RESPONSE")
        return "FEHLER", 0
    observed = {}
    invalid_rows = 0
    duplicate_bucket = False
    for row in rows:
        try:
            left, right = _parse_time(row["start"]), _parse_time(row["end"])
            count = row.get("tweet_count", row.get("post_count"))
            if type(count) is not int or count < 0 or left < start or right > end or right-left != DAY or left % DAY:
                invalid_rows += 1
                continue
            day = iso(left)[:10]
            if day in observed:
                duplicate_bucket = True
                invalid_rows += 1
                continue
            observed[day] = count
        except (KeyError, ValueError, TypeError, OverflowError):
            invalid_rows += 1
            continue
    complete = set(observed) == set(expected) and not invalid_rows and not body.get("errors") and not (body.get("meta") or {}).get("next_token")
    coverage = "VOLLSTAENDIG" if complete else "TEILWEISE"
    with store.db() as con:
        _coverage_receipt(con, request, now, expected, observed, invalid_rows=invalid_rows,
            has_errors=body.get("errors"), has_next_page=(body.get("meta") or {}).get("next_token"),
            state="COMPLETE" if complete else "INVALID_BUCKETS" if invalid_rows else "MISSING_DAYS" if set(observed) != set(expected) else "PARTIAL_RESPONSE")
        if duplicate_bucket:
            return "FEHLER", 0
        for day in expected:
            count = observed.get(day)
            stamp = _parse_time(day+"T00:00:00Z")
            # A partial page never claims the day complete, even for present rows.
            status = coverage if count is not None else "NICHT_ABGEFRAGT"
            old = con.execute("SELECT coverage FROM attention WHERE query_hash=? AND symbol=? AND day=?",
                (request["query_hash"], request["context"], day)).fetchone()
            if old and old[0] == "VOLLSTAENDIG" and status != "VOLLSTAENDIG":
                continue
            con.execute("INSERT OR REPLACE INTO attention VALUES(?,?,?,?,?,?,?,?)", (request["query_hash"], request["context"], day,
                count, status, request["id"], "WEEKEND" if utc(stamp).weekday() >= 5 else "WEEKDAY", now))
    return coverage, len(observed)


def _post_fields(post, now):
    identity, text = str(post.get("id") or ""), post.get("text")
    if not identity.isdigit() or not isinstance(text, str) or not text or len(text) > 25000:
        raise ValueError("Ungültiger Post")
    created = _parse_time(post.get("created_at"))
    if created > now+60 or created < now-7*DAY:
        raise ValueError("Post außerhalb des belegten Zeitfensters")
    author = str(post.get("author_id") or "")
    if author and not author.isdigit():
        raise ValueError("Ungültiger Autor")
    normalized = re.sub(r"https?://\S+", "", text.casefold())
    normalized = re.sub(r"\s+", " ", normalized).strip()
    topic = next((k for k, (pattern, _) in TOPICS.items() if re.search(pattern, normalized)), "OTHER")
    spam = bool(re.search(r"\b(?:join my|free signals|pump group|guaranteed profit|telegram group)\b", normalized))
    urls = []
    for row in (post.get("entities") or {}).get("urls", []):
        raw = str(row.get("expanded_url") or "")
        parsed = urlsplit(raw)
        if parsed.scheme in {"http", "https"} and parsed.hostname and not parsed.username and not parsed.password:
            # Keep only domains in the shared result; never fetch external URLs.
            urls.append(parsed.hostname.lower())
    symbols = sorted({s.upper() for s in re.findall(r"\$([A-Za-z][A-Za-z0-9.\-]{0,11})\b", text)})
    # Cashtag stuffing is promotion, not a credible candidate discovery.
    spam = spam or len(symbols) > 6
    return identity, author, created, text, digest(normalized), topic, int(spam), sorted(set(urls)), symbols


def canonical_url_hash(raw):
    """A bounded link fingerprint, without credentials/query/fragment export."""
    parsed = urlsplit(str(raw))
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        return None
    host = parsed.hostname.lower().removeprefix("www.")
    if host == "localhost" or host.endswith((".localhost", ".local")) or "." not in host:
        return None
    try:
        if not ipaddress.ip_address(host).is_global:
            return None
    except ValueError:
        pass
    canonical = urlunsplit(("https", host, parsed.path.rstrip("/"), "", ""))
    return sha256(canonical.encode()).hexdigest()


def _posts(request, body, now):
    rows = body.get("data", [])
    if not isinstance(rows, list) or len(rows) > 10:
        return 0, 0, 0, "FEHLER"
    processed = duplicates = 0
    seen = set()
    with store.db() as con:
        for post in rows:
            try:
                identity, author, created, text, thash, topic, spam, urls, symbols = _post_fields(post, now)
            except (TypeError, ValueError, AttributeError):
                continue
            if identity in seen:
                duplicates += 1
                continue
            seen.add(identity)
            deleted = any(con.execute("SELECT 1 FROM tombstones WHERE kind=? AND identity_hash=?", (kind, digest(value))).fetchone()
                          for kind, value in (("post", identity), ("author", author)) if value)
            if deleted:
                continue
            old = con.execute("SELECT id,text_hash FROM posts WHERE id=?", (identity,)).fetchone()
            duplicates += int(old is not None)
            # A later result may be an edited post. Replace content, retain all
            # query contexts (UPDATE, not DELETE/REPLACE with cascade).
            con.execute('''INSERT INTO posts VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                author=excluded.author,created=excluded.created,observed=min(posts.observed,excluded.observed),text=excluded.text,
                text_hash=excluded.text_hash,topic=excluded.topic,spam=excluded.spam,urls=excluded.urls,cashtags=excluded.cashtags''',
                (identity, author, created, now, text, thash, topic, spam, store.encode(urls), store.encode(symbols)))
            if old and old["text_hash"] != thash:
                # An edited post must not keep its old inferred symbol links.
                con.execute("DELETE FROM post_context WHERE post_id=?", (identity,))
            con.execute("DELETE FROM post_link_hashes WHERE post_id=?", (identity,))
            for link in (post.get("entities") or {}).get("urls", [])[:10]:
                try:
                    url_hash = canonical_url_hash(link.get("expanded_url") or "")
                except (AttributeError, ValueError):
                    continue
                if url_hash:
                    con.execute("INSERT OR IGNORE INTO post_link_hashes VALUES(?,?)", (identity, url_hash))
            contexts = set(symbols)
            contexts.add(request["context"])
            for symbol in contexts:
                con.execute("INSERT OR IGNORE INTO post_context VALUES(?,?,?,?)", (identity, request["query_hash"], symbol, "search"))
            if request['context'] in (candidate_research.CONTEXT, candidate_research.CONFIRM_CONTEXT):
                for symbol in candidate_research.matched_symbols(con, request, text, symbols):
                    con.execute("INSERT OR IGNORE INTO post_context VALUES(?,?,?,?)",
                                (identity, request['query_hash'], symbol, 'candidate:' + request['id']))
            processed += 1
    coverage = "STICHPROBE_BEGRENZT"
    if body.get("errors") or processed+duplicates < len(rows):
        coverage = "TEILWEISE"
    return len(rows), processed, duplicates, coverage


def _discovery_candidates(con, now, limit=12):
    rows = [dict(r) for r in con.execute("SELECT * FROM posts WHERE created>? AND created<=? AND observed>? AND spam=0 ORDER BY created DESC,id LIMIT 1000", (now-DAY, now+60, now-7*DAY))]
    groups = {}
    for row in rows:
        symbols = json.loads(row["cashtags"])
        for symbol in symbols:
            if SYMBOL.fullmatch(symbol):
                groups.setdefault(symbol, []).append(row)
    has_links = bool(con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='post_link_hashes'").fetchone())
    candidates = []
    cohort_symbols = set(_effective_settings(con, _settings(con))["symbols"])
    validations = {r["symbol"]: r for r in store.value(con, "candidate_validations", [])}
    for symbol, posts in groups.items():
        # Text copied across accounts is one observed claim. Even different texts
        # remain one source family and cannot establish independent confirmation.
        unique = {}
        for post in posts:
            unique.setdefault(post["text_hash"], post)
        items = list(unique.values())
        validation = validations.get(symbol, {})
        valid_profile = validation.get("status") == "SINGLE_STOCK" and 0 <= now-validation["observed_at"] <= DAY
        contexts = {r[0] for post in items for r in con.execute("SELECT symbol FROM post_context WHERE post_id=?", (post["id"],)) if r[0].startswith("__")}
        links = sorted({r[0] for post in items for r in con.execute("SELECT url_hash FROM post_link_hashes WHERE post_id=?", (post["id"],))}) if has_links else []
        candidates.append({"symbol": symbol, "source_family": "X", "status": "UNVERIFIED",
            "observed_at": max(p["observed"] for p in items), "latest_post_at": max(p["created"] for p in items),
            "expires_at": max(p["created"] for p in items)+DAY,
            "evidence_ids": ["x-post:"+p["id"] for p in items[:10]],
            "evidence_hashes": sorted(unique)[:10], "linked_url_hashes": links[:10],
            "sampled_post_count": len(items), "duplicate_text_count": len(posts)-len(items),
            "distinct_accounts_in_sample": len({p["author"] for p in items if p["author"]}),
            "topics": sorted({p["topic"] for p in items if p["topic"] != "OTHER"}),
            "source_roles": sorted(contexts), "independence_confirmed": False,
            "direct_trade_effect": False, "attention_kind": "BOUNDED_SOCIAL_SAMPLE",
            "calibrated_spike": False, "instrument_identity_verified": valid_profile,
            "profile_validation_status": validation.get("status", "UNKNOWN"),
            "monitoring_status": "MONITORED" if symbol in cohort_symbols else "AWAITING_CAPACITY" if valid_profile else "AWAITING_STOCK_PROFILE",
            "detail": "Neue Symbolerwähnung zur Unternehmensprüfung; Stichprobe belegt weder Kursanstieg noch globale Aufmerksamkeit."})
    return sorted(candidates, key=lambda r: (-r["sampled_post_count"], -r["latest_post_at"], r["symbol"]))[:limit]


def discovery_candidates(*, now=None, limit=12):
    """Read cached research seeds only; never makes a provider request."""
    now = time.time() if now is None else now
    limit = max(0, min(100, int(limit)))
    with store.db(readonly=True) as con:
        if not _settings(con)["enabled"]:
            return []
        return _discovery_candidates(con, now, limit)


def _http(session, endpoint, params, token):
    # Caller supplies only the two priced endpoints; no expansions, paginated
    # search, user lookup, stream or full archive can incur unreserved charges.
    if endpoint not in {COUNTS, SEARCH}:
        raise ValueError("X-Endpunkt ohne Preisbeleg")
    response = session.get("https://api.x.com"+endpoint, params=params,
        headers={"Authorization": "Bearer "+token, "User-Agent": "NEXUS-X-Research/1.0"},
        timeout=(4, 10), allow_redirects=False)
    try:
        if len(response.content) > 1_000_000:
            return response.status_code, {}, "", response.headers
        body_hash = sha256(response.content).hexdigest()
        try:
            body = response.json()
        except ValueError:
            body = {}
        return response.status_code, body if isinstance(body, dict) else {}, body_hash, response.headers
    finally:
        response.close()


def _call(kind, query, context, start, end, slot, now, session):
    request = _reserve(kind, query, context, slot, now)
    if not request:
        return False
    params = {"query": query, "start_time": iso(start), "end_time": iso(end)}
    if kind == "counts":
        endpoint = COUNTS
        params["granularity"] = "day"
    else:
        endpoint = SEARCH
        params.update(max_results=10, sort_order="recency", **{"tweet.fields": "id,text,author_id,created_at,entities"})
    try:
        status, body, body_hash, headers = _http(session, endpoint, params, _token())
    except Exception:
        _finish(request, now, status="NETWORK_ERROR", cooldown=1800)
        return True
    if status != 200:
        state = "AUTH_ERROR" if status in {401, 403} else "RATE_LIMITED" if status == 429 else "HTTP_ERROR"
        cooldown = DAY if status in {401, 403} else 1800
        if status == 429:
            try:
                cooldown = max(900, min(DAY, float(headers.get("x-rate-limit-reset", 0))-now))
            except (TypeError, ValueError):
                pass
        _finish(request, now, status=state, http=status, body_hash=body_hash, cooldown=cooldown)
        return True
    try:
        if kind == "counts":
            coverage, processed = _counts(request, body, start, end, now)
            received, duplicates = 0, 0
        else:
            received, processed, duplicates, coverage = _posts(request, body, now)
        valid = coverage != "FEHLER" and bool(body.get("data") is not None or (body.get("meta") or {}).get("result_count") == 0)
    except Exception:
        received = processed = duplicates = 0
        coverage, valid = "FEHLER", False
    _finish(request, now, status="OK" if valid else "INVALID_RESPONSE", http=200,
        body_hash=body_hash, received=received, processed=processed, duplicates=duplicates, coverage=coverage,
        cooldown=0 if valid else 1800)
    return True


def tick(*, now=None, session=None):
    """One bounded request per tick; global reservations coordinate processes."""
    now = time.time() if now is None else now
    with store.db() as con:
        settings = _settings(con)
        store.put(con, "worker_heartbeat", now)
        # Retain raw content for at most seven days even while disabled.
        con.execute("DELETE FROM posts WHERE observed<?", (now-7*DAY,))
        con.execute("DELETE FROM attention WHERE recorded<?", (now-90*DAY,))
        active = settings["enabled"] and _price_current(settings, now) and bool(_token())
        dynamic_ids = []
        if active:
            settings = _refresh_monitoring(con, settings, now)
            dynamic_ids = account_registry.refresh(con, now)
    if not active:
        _publish(now)
        return False
    own = session is None
    if own:
        import requests
        session = requests.Session()
        session.trust_env = False
    try:
        # 10.3.0: Keine taeglichen Zaehlungsabrufe mehr -- die 14/28-Tage-
        # X-Baseline ist mit der PULSAR-Score-Maschine entfallen. Das Budget
        # traegt stattdessen fuenf Kandidaten-Suchen pro Tag (Slots in
        # candidate_research) und zwei Makro-/Accountsuchen (12-Stunden-Slot).
        with store.db(readonly=True) as con:
            candidate_plan = candidate_research.plan(con, now)
        if candidate_plan and _call("search", candidate_plan['query'],
                candidate_plan.get('context', candidate_research.CONTEXT),
                now-DAY, now-30, candidate_plan['slot'], now, session):
            return True
        slot = int(now // (12*3600))
        query, context = search_plan(slot, settings["priority_accounts"], dynamic_ids)
        return _call("search", query, context, now-DAY, now-30, str(slot), now, session)
    finally:
        if own:
            session.close()
        _publish(now)


def _attention(con, settings, now):
    result = []
    settings = _effective_settings(con, settings)
    queries = dict(_queries(settings))
    control_hash = digest({"query": queries.get("__MARKET_CONTROL__", ""), "version": 1})
    normalization = store.value(con, "normalization", {})
    coverage_receipts = store.value(con, "counts_coverage", [])
    for symbol in settings["symbols"]:
        qhash = digest({"query": queries[symbol], "version": 1})
        rows = [dict(r) for r in con.execute("SELECT * FROM attention WHERE symbol=? AND query_hash=? ORDER BY day DESC LIMIT 35", (symbol, qhash))]
        full = [r for r in rows if r["coverage"] == "VOLLSTAENDIG" and r["count"] is not None]
        current = full[0] if full else None
        if current:
            left = _parse_time(current["day"]+"T00:00:00Z")-28*DAY
            previous = [r for r in full[1:] if _parse_time(r["day"]+"T00:00:00Z") >= left and r["day_type"] == current["day_type"]]
            previous_window = {r["day"] for r in full[1:] if _parse_time(r["day"]+"T00:00:00Z") >= left}
            baseline_ready = len(previous_window) == 28
            short = previous[:3]
            median = statistics.median([r["count"] for r in previous]) if previous else None
            control = con.execute("SELECT count,coverage FROM attention WHERE symbol='__MARKET_CONTROL__' AND query_hash=? AND day=?", (control_hash, current["day"])).fetchone()
            share = current["count"]/control[0] if control and control[1] == "VOLLSTAENDIG" and control[0] and current["count"] <= control[0] else None
            past_shares = []
            for prior in previous:
                denominator = con.execute("SELECT count,coverage FROM attention WHERE symbol='__MARKET_CONTROL__' AND query_hash=? AND day=?", (control_hash, prior["day"])).fetchone()
                if denominator and denominator[1] == "VOLLSTAENDIG" and denominator[0] and prior["count"] <= denominator[0]:
                    past_shares.append(prior["count"]/denominator[0])
            normalized_median = statistics.median(past_shares) if past_shares else None
            window_days = [iso(_parse_time(current["day"]+"T00:00:00Z")-d*DAY)[:10] for d in range(1, 29)]
            missing_comparison_days = sorted(set(window_days)-previous_window)
            missing_control_days, inconsistent_control_days, zero_control_days = [], [], []
            symbol_counts = {r["day"]: r["count"] for r in full}
            for day in [current["day"]]+window_days:
                denominator = con.execute("SELECT count,coverage FROM attention WHERE symbol='__MARKET_CONTROL__' AND query_hash=? AND day=?", (control_hash, day)).fetchone()
                if not denominator or denominator[1] != "VOLLSTAENDIG" or denominator[0] is None:
                    missing_control_days.append(day)
                elif day in symbol_counts and symbol_counts[day] > denominator[0]:
                    inconsistent_control_days.append(day)
                elif denominator[0] == 0:
                    zero_control_days.append(day)
            normalization_status = ("INCONSISTENT_CONTROL" if inconsistent_control_days else
                "MISSING_CONTROL_DAYS" if missing_control_days else "BASELINE_INCOMPLETE" if not baseline_ready else
                "ZERO_CONTROL" if not control or not control[0] else "ZERO_CONTROL_COMPARISON" if zero_control_days else "READY")
        else:
            previous, short, median, share = [], [], None, None
            baseline_ready, normalized_median, past_shares = False, None, []
            missing_comparison_days, missing_control_days, inconsistent_control_days, zero_control_days = [], [], [], []
            normalization_status = "NO_COMPLETE_DAYS"
        receipt = next((r for r in coverage_receipts if r["query_hash"] == qhash and r["symbol"] == symbol), {})
        result.append({"symbol": symbol, "source": "X", "query_hash": qhash,
            "coverage_status": rows[0]["coverage"] if rows else "NICHT_ABGEFRAGT",
            "day": current["day"] if current else None, "count": current["count"] if current else None,
            "observed_at": current["recorded"] if current else None,
            "stale": not current or now-_parse_time(current["day"]+"T00:00:00Z") > 3*DAY,
            "comparison_days": len(previous), "complete_days": len(full),
            "baseline_window_days": 28, "baseline_ready": baseline_ready,
            "same_day_type_median": median,
            "long_ratio": current["count"]/median if current and median and baseline_ready else None,
            "short_ratio": current["count"]/statistics.median([r["count"] for r in short]) if current and len(short) == 3 and statistics.median([r["count"] for r in short]) else None,
            "market_query_share": share, "market_control_query_hash": control_hash,
            "normalized_ratio": share/normalized_median if share is not None and normalized_median and normalization_status == "READY" and len(past_shares) == len(previous) else None,
            "normalized_comparison_days": len(past_shares),
            "normalization_status": normalization_status,
            "normalization_epoch": normalization.get("changed_at"),
            "missing_comparison_days": missing_comparison_days,
            "missing_control_days": missing_control_days, "inconsistent_control_days": inconsistent_control_days,
            "zero_control_days": zero_control_days,
            "coverage_issue": receipt if receipt.get("state") != "COMPLETE" else None,
            "daily_samples": [{"day": r["day"], "count": r["count"], "coverage": r["coverage"],
                "query_hash": r["query_hash"], "observed_at": r["recorded"], "day_type": r["day_type"]} for r in rows],
            "short_comparison_method": "Letzte drei gemessene Tage desselben UTC-Wochen-/Wochenendtyps",
            "normalization": "Anteil an derselben beobachteten Symbolgruppe, nicht an ganz X",
            "day_type": current["day_type"] if current else None,
            "day_type_method": "UTC_WEEKDAY_WEEKEND; Feiertage nicht als Börsenkalender abgebildet",
            "absence_inferred": False, "calibrated_spike": None, "trade_effect": False})
    return result


def _events(con, now, symbol=None):
    params = [now-DAY]
    sql = "SELECT DISTINCT p.* FROM posts p"
    if symbol:
        sql += " JOIN post_context c ON c.post_id=p.id WHERE p.created>? AND c.symbol=?"
        params.append(symbol)
    else:
        sql += " WHERE p.created>?"
    rows = [dict(r) for r in con.execute(sql, params)]
    groups = {}
    for row in rows:
        if row["spam"] or row["topic"] == "OTHER":
            continue
        # Same topic on different symbols is not the same event. Daily clusters
        # are candidate narrative groups, never assertions about one occurrence.
        key = (row["topic"], iso(row["created"])[:10])
        groups.setdefault(key, []).append(row)
    result = []
    for (topic, day), entries in groups.items():
        hashes = sorted({r["text_hash"] for r in entries})
        authors = Counter(r["author"] for r in entries if r["author"])
        symbols = sorted({s for r in entries for s in json.loads(r["cashtags"])})
        shingle_sets = []
        near_duplicates = 0
        windows = Counter(int(r["created"]//1800) for r in entries)
        for row in entries:
            words = re.findall(r"\w+", row["text"].casefold())
            shingles = {tuple(words[i:i+4]) for i in range(max(0, len(words)-3))}
            if shingles and any(len(shingles & old)/len(shingles | old) >= .8 for old in shingle_sets if old):
                near_duplicates += 1
            shingle_sets.append(shingles)
        event_id = digest({"topic": topic, "day": day, "hashes": hashes, "symbol": symbol})
        high_priority = any(con.execute("SELECT 1 FROM post_context WHERE post_id=? AND symbol IN ('__PRIORITY_SOURCES__','__POLICY_SOURCES__','__FINANCIAL_SOURCES__')", (r["id"],)).fetchone() for r in entries)
        result.append({"event_id": event_id, "event_type": topic, "title": TOPICS[topic][1],
            "status": "HIGH_PRIORITY_UNCONFIRMED" if high_priority else "SOCIAL_ALERT", "confidence": "UNBELEGT",
            "source_priority": "SELECTED_INFORMATION_ACCOUNT" if high_priority else "NORMAL",
            "source_identity_verified": False,
            "detected_at": min(r["observed"] for r in entries), "event_time": None,
            "expires_at": max(r["created"] for r in entries)+DAY,
            "posts": len(entries), "distinct_accounts_in_sample": len(authors) if len(authors) == len({r["author"] for r in entries}) else None,
            "max_author_share": max(authors.values())/len(entries) if authors else None,
            "duplicate_texts": len(entries)-len(hashes), "evidence_hashes": hashes,
            "near_duplicate_posts": near_duplicates,
            "largest_30m_window_share": max(windows.values())/len(entries),
            "post_ids": sorted(r["id"] for r in entries), "affected_symbols": symbols,
            "linked_domains": sorted({u for r in entries for u in json.loads(r["urls"])}),
            "primary_source_confirmed": False, "trade_effect": False,
            "detail": "Themenhinweis aus begrenzter Stichprobe; kein bestätigtes Ereignis und kein Nachweis unabhängiger Menschen"})
    return sorted(result, key=lambda r: r["detected_at"], reverse=True)[:12]


def _status(now):
    with store.db(readonly=True) as con:
        settings = _settings(con)
        month = iso(now)[:7]
        requests = [dict(r) for r in con.execute("SELECT * FROM requests WHERE month=?", (month,))]
        last = store.value(con, "last_state", {})
        reserved = sum(r["reserved"] for r in requests)
        charged = sum(r["charged"] for r in requests)
        events = _events(con, now)
        candidates = _discovery_candidates(con, now) if settings["enabled"] else []
        effective = _effective_settings(con, settings)
        count = con.execute("SELECT count(*) FROM posts").fetchone()[0]
        consumers = [dict(r) for r in con.execute("SELECT * FROM consumers ORDER BY consumed DESC LIMIT 30")]
        state = ("CONFIGURATION_ERROR" if settings.get("_configuration_error") else "DISABLED" if not settings["enabled"] else "NOT_CONFIGURED" if not _token() else "PRICING_EXPIRED" if not _price_current(settings, now) else
                 last.get("state", "WAITING_FIRST_REQUEST"))
        return {"source": "X", "mode": "DISCOVERY_RESEARCH", "trade_effect": False,
            "settings": _settings_public(settings), "state": state,
            "detail": ("Gespeicherte X-Einstellungen sind ungültig; keine Abrufe. Einstellungen in der WebUI erneut speichern." if state == "CONFIGURATION_ERROR" else "X ist ausgeschaltet" if not settings["enabled"] else
                "X-Preisbeleg fehlt, liegt in der Zukunft oder ist älter als 30 Tage; aktuelle Anbieterpreise erneut prüfen" if state == "PRICING_EXPIRED" else
                last.get("detail", "Noch keine X-Abfrage ausgeführt")),
            "observed_at": now, "worker_heartbeat": store.value(con, "worker_heartbeat"),
            "last_attempt": max([r["started"] for r in requests], default=None),
            "last_success": max([r["finished"] for r in requests if r["status"] == "OK"], default=None),
            "cooldown_until": store.value(con, "cooldown_until", 0),
            "counts": {"requests": len(requests), "successful_requests": sum(r["status"] == "OK" for r in requests),
                "failed_requests": sum(r["status"] not in {"OK", "RESERVED"} for r in requests),
                "posts_received": sum(r["received"] for r in requests if r["kind"] == "search"),
                "unique_posts": count, "duplicates": sum(r["duplicates"] for r in requests),
                "processed_posts": sum(r["processed"] for r in requests if r["kind"] == "search"),
                "events": len(events), "discovery_candidates": len(candidates),
                "consumer_reads": con.execute("SELECT count(*) FROM consumers").fetchone()[0]},
            "budget": {"month": month, "limit_eur": settings["monthly_budget_eur"],
                "reserved_eur": reserved/1e6, "charged_upper_eur": charged/1e6,
                "remaining_eur": max(0, settings["monthly_budget_eur"]-(reserved+charged)/1e6),
                "invoice_reconciled": False, "scope": "UTC-Kalendermonat / nur diese Installation",
                "detail": "Konservative Obergrenze, keine bestätigte Rechnung; Anbieterlimit zusätzlich verwenden"},
            "attention": _attention(con, settings, now), "events": events, "consumers": consumers,
            "discovery": {"automatic": True, "candidates": candidates, "candidate_count": len(candidates),
                "source_accounts": public_registry(),
                "account_registry": account_registry.snapshot(con, now),
                "monitoring_symbols": effective["symbols"], "cohort_limit": MAX_MONITORED_SYMBOLS,
                "plan": {"searches_per_day": GENERAL_SEARCHES_PER_DAY, "posts_per_search": 10,
                    "counts_per_day_max": MAX_COUNTS_PER_DAY,
                    "slots_utc": ["12-Stunden-Slots; politische Konten, Finanzbehörden und offene Unternehmenssuche rotieren"]},
                "detail": "Konten und offene Suche liefern Kandidaten. Kleine Stichprobe; keine vollständige Überwachung und keine alleinige Handels- oder Krisenentscheidung."},
            "candidate_research": candidate_research.snapshot(con, now),
            "counts_coverage": store.value(con, "counts_coverage", []),
            "normalization": store.value(con, "normalization", {}),
            "requests": [{k: r[k] for k in ("id", "kind", "query_hash", "context", "started", "status", "http", "body_hash", "coverage", "finished")} for r in requests[-100:]],
            "content_exported": False, "raw_post_retention_days": 7}


def public_status():
    return _status(time.time())


def _publish(now=None):
    result = _status(time.time() if now is None else now)
    store.atomic_json("market_intelligence_status.json", result)
    return result


def for_symbol(symbol, *, now=None):
    now = time.time() if now is None else now
    symbol = str(symbol).upper()
    if not SYMBOL.fullmatch(symbol):
        return {}
    with store.db(readonly=True) as con:
        if not _settings(con)["enabled"]:
            return {}
    with store.db() as con:
        settings = _settings(con)
        if not settings["enabled"]:
            return {}
        attention = next((a for a in _attention(con, settings, now) if a["symbol"] == symbol), None)
        events = _events(con, now, symbol)
        discovery = next((r for r in _discovery_candidates(con, now, 100) if r["symbol"] == symbol), None)
        candidate_context = candidate_research.for_symbol(con, symbol, now)
        if not attention and not events and not discovery and not candidate_context:
            return {}
        result = {"source_family": "X", "role": "UNCONFIRMED_RESEARCH_ONLY", "symbol": symbol,
            "attention": attention, "argument_clusters": events[:4], "discovery": discovery,
            "candidate_research": candidate_context, "trade_effect": False,
            "instruction": "Unbestätigte Social-Hinweise. Keine Primärquelle, keine Handelsfreigabe; unabhängig prüfen.",
            "cross_platform_status": "GETRENNT_BEOBACHTET_NICHT_KALIBRIERT"}
        con.execute("INSERT OR REPLACE INTO consumers VALUES(?,?,?,?)", ("PULSAR", symbol, digest(result), now))
        return result


def event_hints(*, consumer="NEXUS_CORE", now=None):
    now = time.time() if now is None else now
    with store.db(readonly=True) as con:
        if not _settings(con)["enabled"]:
            return []
    with store.db() as con:
        if not _settings(con)["enabled"]:
            return []
        result = _events(con, now)
        if result:
            con.execute("INSERT OR REPLACE INTO consumers VALUES(?,?,?,?)", (consumer, "MARKET", digest(result), now))
        return result


def export_diagnostics():
    result = public_status()
    with store.db(readonly=True) as con:
        result["requests"] = [dict(r) for r in con.execute("SELECT id,kind,query_hash,context,started,status,http,received,processed,duplicates,body_hash,coverage,finished FROM requests ORDER BY started DESC LIMIT 100")]
    return result


def delete_evidence(post_id=None, author_id=None):
    if bool(post_id) == bool(author_id):
        raise ValueError("Genau eine Post-ID oder Autor-ID angeben")
    kind, identity = ("post", str(post_id)) if post_id else ("author", str(author_id))
    if not identity.isdigit():
        raise ValueError("Ungültige ID")
    with store.db() as con:
        con.execute("INSERT OR IGNORE INTO tombstones VALUES(?,?)", (kind, digest(identity)))
        count = con.execute("DELETE FROM posts WHERE "+("id" if kind == "post" else "author")+"=?", (identity,)).rowcount
        # Clusters are computed from remaining posts, never cached as content.
        # Consumer payload hashes cannot reconstruct removed text.
        con.execute("DELETE FROM consumers")
    _publish()
    return {"deleted_posts": count, "derived_events_rebuilt": True}


def _run():
    while not _stop.is_set():
        try:
            tick()
        except Exception:
            # Never log an exception string containing a secret or response body.
            try:
                with store.db() as con:
                    store.put(con, "last_state", {"state": "LOCAL_ERROR", "detail": "X-Speicherung fehlgeschlagen; Handelsbetrieb bleibt unabhängig"})
            except Exception:
                logging.getLogger(__name__).error(
                    "X LOCAL_ERROR: Fehlerstatus konnte nicht gespeichert werden; "
                    "Handelsbetrieb bleibt unabhängig")
        _stop.wait(30)


def start_worker():
    global _thread
    with _thread_lock:
        if _thread and _thread.is_alive():
            return
        _stop.clear()
        _thread = threading.Thread(target=_run, name="nexus-x-intelligence", daemon=True)
        _thread.start()


def stop_worker():
    _stop.set()
