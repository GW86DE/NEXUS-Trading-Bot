"""One bounded background worker; no broker access and no order submission."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
import logging
import math
import threading
import time

from . import control, research
from .candidate_selection import select_candidates, profile_type, SELECTION_REVISION, PROFILE_MAX_AGE

LOG = logging.getLogger(__name__)


def scan_schedule(last_started, *, now=None):
    """Describe the existing weekday/weekend rules without triggering work."""
    now = time.time() if now is None else float(now)
    candidate = now
    for _ in range(8):
        local = datetime.fromtimestamp(candidate, ZoneInfo("America/New_York"))
        interval = 900 if local.weekday() < 5 else 12*3600
        due = float(last_started)+interval if last_started else now
        if candidate < due:
            candidate = due
            continue
        if local.weekday() >= 5 or 7 <= local.hour < 19:
            return {"last_scan_started_at": last_started or None, "next_scan_at": candidate,
                "interval_seconds": interval, "timezone": "America/New_York",
                "window": "Mo-Fr 07:00–19:00; Wochenende alle 12 Stunden"}
        if local.hour < 7:
            candidate = local.replace(hour=7, minute=0, second=0, microsecond=0).timestamp()
        else:
            candidate = (local+timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    return {"last_scan_started_at": last_started or None, "next_scan_at": None,
            "timezone": "America/New_York", "reason": "SCHEDULE_UNRESOLVED"}


def _source_id(source):
    # A new fetch of identical facts must not trigger another paid review.
    return control.digest(research.facts(source))


def _cache_cards(cards, ttl=3600):
    """Write full UI cards plus a bounded read-only diagnosis projection.

    Both rows get the exact same revision timestamp, so a later diagnosis can
    prove that the compact projection belongs to the currently exported top5
    revision without reading a multi-megabyte source packet.
    """
    from .diagnostics import compact_cards
    now = time.time()
    research.cache_put("top5", cards, ttl, now=now)
    research.cache_put("diagnostic_top5", {"top5_saved": now, "cards": compact_cards(cards)}, ttl, now=now)


def _request(key, ttl, fn, *, requests_reserved=1):
    old = research.cached(key)
    if old:
        return old["data"]
    failed = research.cached("failed:" + key)
    if failed:
        detail = failed.get("data") or {}
        raise control.Blocked("Quelle in Abrufpause: " + str(detail.get("code") or detail.get("error") or "UNKNOWN"))
    if control.settings()["mode"] == "AUS":
        raise control.Blocked("PULSAR ausgeschaltet; kein weiterer Quellenabruf")
    token = research.reserve("data", requests_reserved)
    try:
        value = fn()
        research.cache_put(key, value, ttl)
        research.settle(token, requests_reserved)
        return value
    except Exception as exc:
        from massive_service import MassivePaused
        if isinstance(exc, MassivePaused):
            if exc.code in {"MASSIVE_RATE_PAUSED", "MASSIVE_INFLIGHT", "MASSIVE_CACHE_MISS", "MASSIVE_CAPABILITY_PAUSED"}:
                research.settle(token, 0)  # No HTTP request was started.
            research.cache_put("failed:" + key, {"error": type(exc).__name__,
                "code": exc.code, "retry_at": time.time()+exc.retry_after}, exc.retry_after)
        else:
            research.cache_put("failed:" + key, {"error": type(exc).__name__}, 3600)
        raise


def _sufficient_fmp_news(rows, *, now=None):
    """Three distinct usable recent articles justify cache-only supplementation."""
    from urllib.parse import urlsplit
    from .news_normalization import normalize_news
    now = time.time() if now is None else now
    distinct = set()
    for row in normalize_news(rows, "FMP"):
        if not row.get("title") or not row.get("text"):
            continue
        try:
            url = urlsplit(row.get("url") or "")
            if url.scheme not in {"http", "https"} or not url.hostname:
                continue
            stamp = datetime.fromisoformat(str(row.get("publishedDate") or "").replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=ZoneInfo("America/New_York"))
            if 0 <= now-stamp.timestamp() <= 72*3600:
                distinct.add((url.hostname.lower(), url.path.rstrip("/")))
        except (ValueError, TypeError):
            continue
    return len(distinct) >= 3


def _massive_supplement(client, symbol, fmp_news):
    """Existing FMP news is the primary source; do not spend Free calls twice."""
    key = "massive:news:" + symbol
    hit = research.cached(key)
    if hit:
        return hit["data"], hit["saved"], "CACHE"
    if _sufficient_fmp_news(fmp_news):
        from massive_service import MassivePaused
        try:
            news = client.news(symbol, limit=5, cache_only=True)
        except MassivePaused as exc:
            if exc.code == "MASSIVE_CACHE_MISS":
                return [], None, "NOT_REQUESTED_FMP_SUFFICIENT"
            raise
        stamp = client.status().get("transport", {}).get("saved_at")
        if not stamp:
            return [], None, "CACHE_FETCH_TIME_UNKNOWN"
        return news, stamp, "CACHE"
    news = _request(key, 7200, lambda: client.news(symbol, limit=5))
    stamp = research.cached(key, stale=True)["saved"]
    return news, stamp, "SUPPLEMENT_FOR_DATA_GAP"


def gather_market(symbol, *, profile_evidence=None):
    """Reuse configured providers and their shared caches/budgets; no subscription changes."""
    import fmp_reference
    fmp = fmp_reference.client()
    packet = {"symbol": symbol, "sources": [], "profile": {}, "bars": [], "news": [], "errors": []}
    if profile_evidence is not None:
        profile = profile_evidence.get("profile") or {}
        source = profile_evidence.get("source") or {}
        try:
            stamp = float(source["observed_at"])
            fresh = math.isfinite(stamp) and 0 <= time.time()-stamp <= PROFILE_MAX_AGE
        except (KeyError, ValueError, TypeError):
            fresh = False
        if (profile_type(symbol, profile)[0] != "SINGLE_STOCK"
                or not fresh or source.get("provider") != "FMP" or source.get("kind") != "profile"
                or source.get("symbol") != symbol or source.get("data") != profile
                or source.get("id") != _source_id(source)):
            raise control.Blocked("Aktienprofil fuer Marktrecherche nicht belegt")
        # Reuse the exact already-dated profile, including its source identity.
        # Classification must not cause a second request for the same profile.
        packet["profile"] = profile
        packet["sources"].append(source)
    if fmp.konfiguriert:
        from fmp_service import consumer
        from fmp_data import daily_metrics
        queries = [
            ("bars", None, None, lambda: fmp.tageskerzen(symbol, 1300 if fmp.starter() else 95)),
            ("quote", "/quote", {"symbol": symbol}, lambda: fmp.quote(symbol)),
        ]
        if profile_evidence is None:
            queries.insert(0, ("profile", "/profile", {"symbol": symbol}, lambda: fmp.profil(symbol)))
        if fmp.erlaubt("/news/stock"):
            queries.append(("news", "/news/stock", {"symbols": symbol, "limit": 20}, lambda: fmp.nachrichten(symbol)))
        if fmp.erlaubt("/income-statement"):
            queries.append(("annual_financials", None, None, lambda: fmp.jahresdaten(symbol)))
        with consumer("pulsar"):
            for name, path, params, fn in queries:
                if control.settings()["mode"] == "AUS":
                    break
                profile = packet.get("profile") or {}
                if (name == "annual_financials" and profile.get("symbol") == symbol
                        and (profile.get("isEtf") is True or profile.get("isFund") is True)):
                    from fmp_data import ANNUAL_SCHEMA
                    packet[name] = {"schema_version": ANNUAL_SCHEMA, "symbol": symbol,
                        "available": False, "status": "NOT_APPLICABLE", "errors": [],
                        "reason": "ETF/Fonds statt Unternehmensjahresabschluesse"}
                    continue
                try:
                    value = fn()
                    packet[name] = value
                    hit = fmp.store.cached("history:" + symbol) if name == "bars" else fmp.cached_source(path, params) if path else None
                    if name == "annual_financials":
                        hits = [fmp.cached_source(p, {"symbol": symbol, "period": "annual", "limit": 5}) for p in
                            ("/income-statement", "/cash-flow-statement", "/balance-sheet-statement")]
                        stamp = min((h["saved"] for h in hits if h), default=0)
                    else:
                        stamp = (hit or {}).get("saved", 0)
                    if not stamp:
                        raise RuntimeError("FMP-Abrufzeit nicht belegt")
                    source = {"provider": "FMP", "kind": name, "symbol": symbol,
                        "observed_at": stamp, "data": value,
                        "url": "https://financialmodelingprep.com/stable" + (path or
                                ("/income-statement" if name == "annual_financials" else "/historical-price-eod/full"))}
                    source["id"] = _source_id(source)
                    packet["sources"].append(source)
                    if name == "bars":
                        packet["daily_metrics"] = daily_metrics(value)
                        # Compatibility for held positions and existing UI. Preserve
                        # the actual fetch timestamp rather than renewing stale data.
                        research.cache_put("fmp:bars:" + symbol, value, 2*86400, now=stamp)
                except Exception as exc:
                    packet["errors"].append(f"FMP {name}: {str(exc)[:180]}")
    else:
        packet["errors"].append("FMP nicht eingerichtet; FMP-Kursdaten fehlen")
    # 10.5.0: zweite Social-Familie und Squeeze-Merkmal gehoeren zum Marktpaket
    # des Symbols; Fehler bleiben UNKNOWN auf der Karte, nie ein Nullwert.
    current = control.settings()
    if current["mode"] != "AUS" and current.get("stocktwits"):
        try:
            from .stocktwits import stream as stocktwits_stream
            packet["stocktwits"] = stocktwits_stream(symbol)
        except Exception as exc:
            packet["errors"].append("StockTwits: " + str(exc)[:180])
    if current["mode"] != "AUS" and current.get("finra"):
        try:
            from .short_interest import fetch as fetch_short_interest
            packet["short_interest"] = fetch_short_interest(symbol)
        except Exception as exc:
            packet["errors"].append("FINRA: " + str(exc)[:180])
    # SEC contact is part of the existing configuration. Reserve the ticker-map
    # request as well as Company Facts; an existing SEC cache may spend less.
    try:
        from sec_fundamentals import SecFundamentals
        sec = SecFundamentals()
        profile = packet.get("profile") or {}
        not_company = profile.get("symbol") == symbol and (profile.get("isEtf") is True or profile.get("isFund") is True)
        if sec.enabled() and not not_company:
            facts = _request(f"sec:{symbol}", 86400, lambda: sec.snapshot(symbol), requests_reserved=2)
            source = {"provider": "SEC", "kind": "companyfacts", "symbol": symbol,
                      "observed_at": research.cached(f"sec:{symbol}", stale=True)["saved"], "data": facts}
            source["url"] = "https://www.sec.gov/edgar/search/"
            source["id"] = _source_id(source)
            packet["sources"].append(source)
    except Exception as exc:
        packet["errors"].append(f"SEC: {str(exc)[:180]}")
    # MASSIVE remains an optional existing subscription, never enabled here.
    try:
        from massive_api import MassiveClient
        import config
        if bool(getattr(config, "MASSIVE_ENABLED", False)):
            client = MassiveClient()
            news, stamp, status = _massive_supplement(client, symbol, packet.get("news") or [])
            packet.setdefault("provider_status", {})["MASSIVE"] = {"status": status, "observed_at": stamp}
            if stamp:
                source = {"provider": "MASSIVE", "kind": "news", "symbol": symbol,
                          "observed_at": stamp, "data": news}
                source["id"] = _source_id(source)
                packet["sources"].append(source)
    except Exception as exc:
        packet["errors"].append(f"MASSIVE: {str(exc)[:180]}")
        packet.setdefault("provider_status", {})["MASSIVE"] = {
            "status": "PAUSED" if getattr(exc, "retry_after", None) else "UNAVAILABLE",
            "code": getattr(exc, "code", type(exc).__name__),
            "retry_after": getattr(exc, "retry_after", None), "reason": str(exc)[:180]}
    return packet


def build_card(attention, market, tradestie=None, *, now=None):
    now = time.time() if now is None else now
    # Old caches can contain a derived growth value despite a missing previous
    # count. Recompute only derived presentation/research fields from the actual
    # counts; never turn that stale number into a measured increase.
    def measured(row):
        if row is None:
            return None
        current, previous = research._count(row.get("mentions")), research._count(row.get("mentions_24h_ago"))
        return {**row, "growth_ratio": current/previous if current is not None and previous else None,
                "newly_observed": previous == 0, "discovery": research.discovery_state(row)}
    attention, tradestie = measured(attention), measured(tradestie)
    symbol = attention["symbol"]
    baseline = research.baseline(symbol, attention.get("observation_source", "apewisdom"), now=now)
    sources = list(market["sources"])
    # One shared X collector supplies bounded, unconfirmed research context.
    # Never add X to market news / crisis keywords or the Reddit mention count.
    x_context = {}
    try:
        from market_intelligence import for_symbol
        x_context = for_symbol(symbol, now=now)
        if x_context:
            x_source = {"provider": "X", "kind": "social_research_hint", "symbol": symbol,
                        "data": x_context}
            x_source["id"] = _source_id(x_source)
            sources.append(x_source)
    except Exception:
        # Optional source failures must not interrupt baseline PULSAR research.
        x_context = {"source_family": "X", "coverage_status": "FEHLER", "trade_effect": False}
    from .source_coordination import coordination
    if attention.get("source") == "X":
        # 10.3.0: Die vollstaendige X-Tageszaehlung (COMPLETE_DAILY_X_COUNTS)
        # ist mit der Score-Maschine entfallen. Eine X-Entdeckung bleibt eine
        # begrenzte Stichprobe; die Hype-Spur verlangt dafuer zusaetzlich die
        # Kurs-/Volumenbestaetigung aus den FMP-Kerzen.
        attention = {**attention, "discovery": {"state": "X_RECHERCHEHINWEIS", "growth_score": 0.,
            "detail": "Aktie in begrenzter X-Stichprobe entdeckt; keine Erwaehnungs-Vollzaehlung, "
                      "Kursanstieg wird separat aus Kursdaten belegt."}}
    enriched = market.get("enrichment") or {}
    sources.extend(enriched.get("documents") or [])
    search = enriched.get("search") or {}
    if search.get("ok"):
        web = {"provider": "GPT-Websuche", "kind": "search_leads", "symbol": symbol,
            "observed_at": search["observed_at"], "data": {
                "summary": search["summary"], "counterevidence": search["counterevidence"],
                "urls": search["urls"], "quality": "SUCHHINWEIS_KEIN_PRIMAERBELEG"}}
        web["id"] = _source_id(web)
        sources.append(web)
    for row in [attention, tradestie]:
        if row and row.get("evidence_id"):
            sources.append({"id": row["evidence_id"], "provider": row["source"],
                "kind": "social_research_hint" if row.get("source") == "X" else "aggregate_attention",
                "observed_at": row["observed_at"], "url": row["url"], "data": row})
        elif row and row.get("source") in {"volume_watch", "stocktwits_trending"}:
            seed = {"provider": row["source"], "kind": "discovery_seed", "symbol": symbol,
                    "observed_at": row.get("observed_at"), "url": row.get("url") or "",
                    "data": {k: row.get(k) for k in ("symbol", "source", "rvol", "gain", "rank", "trending_score", "detail")}}
            seed["id"] = _source_id(seed)
            sources.append(seed)
    # 10.5.0: zweite Social-Familie, relatives Volumen und Squeeze-Merkmal.
    stocktwits_row = market.get("stocktwits") if isinstance(market.get("stocktwits"), dict) else None
    if stocktwits_row:
        st_source = {"provider": "StockTwits", "kind": "social_activity", "symbol": symbol,
                     "observed_at": stocktwits_row.get("observed_at"), "url": stocktwits_row.get("url"),
                     "data": {k: stocktwits_row.get(k) for k in ("symbol", "messages_1h", "authors_1h", "bullish_1h",
                              "bearish_1h", "messages_sampled", "truncated_1h", "watchlist_count", "detail")}}
        st_source["id"] = _source_id(st_source)
        sources.append(st_source)
    short_interest = market.get("short_interest") if isinstance(market.get("short_interest"), dict) else None
    if short_interest and short_interest.get("status") == "OK":
        si_source = {"provider": "FINRA", "kind": "short_interest", "symbol": symbol,
                     "observed_at": short_interest.get("observed_at"), "url": short_interest.get("url"),
                     "data": {k: short_interest.get(k) for k in ("symbol", "settlement_date", "short_shares",
                              "previous_short_shares", "average_daily_volume", "days_to_cover", "market")}}
        si_source["id"] = _source_id(si_source)
        sources.append(si_source)
    from .volume_watch import from_quote
    quote_data = next((s.get("data") for s in sources if isinstance(s, dict) and s.get("provider") == "FMP"
                       and s.get("kind") == "quote" and isinstance(s.get("data"), dict)), None)
    volume = from_quote(quote_data, now=now)
    seed_volume = attention.get("volume_discovery") or (attention if attention.get("source") == "volume_watch" else None)
    if volume.get("status") != "OK" and seed_volume and seed_volume.get("rvol") is not None:
        volume = {"status": "OK", "rvol": seed_volume["rvol"], "gain": seed_volume.get("gain"),
                  "method": "HOURLY_CUMULATIVE_VS_20_SESSIONS", "observed_at": seed_volume.get("observed_at"),
                  "detail": str(seed_volume.get("detail") or "Relatives Volumen aus Stundenkerzen")}
    try:
        stocktwits_baseline = research.baseline(symbol, "stocktwits", now=now) if stocktwits_row else {}
    except Exception:
        stocktwits_baseline = {}
    profile = market.get("profile") or {}
    today = datetime.fromtimestamp(now, ZoneInfo("America/New_York")).date().isoformat()
    bars = sorted([r for r in market.get("bars", []) if isinstance(r, dict) and r.get("date")
                   and str(r["date"])[:10] < today],
                  key=lambda r: r["date"])
    intraday = []
    for row in market.get("intraday") or []:
        if not isinstance(row, dict):
            continue
        try:
            stamp = datetime.fromisoformat(str(row["date"]).replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=ZoneInfo("America/New_York"))
            if stamp.timestamp()+900 <= now and row.get("close") is not None:
                intraday.append({**row, "date": stamp.isoformat()})
        except (TypeError, KeyError, ValueError):
            continue
    intraday = sorted(intraday, key=lambda r: r["date"])[-300:]
    price = profile.get("price")
    cap = profile.get("marketCap")
    def positive(v):
        try:
            return float(v) if math.isfinite(float(v)) and float(v) > 0 else None
        except (ValueError, TypeError):
            return None
    price, cap = positive(price), positive(cap)
    volumes = [positive(r.get("volume")) * positive(r.get("close")) for r in bars[-20:]
               if positive(r.get("volume")) and positive(r.get("close"))]
    turnover = sum(volumes)/len(volumes) if len(volumes) >= 15 else None
    hard = []
    currency = str(profile.get("currency") or "").upper()
    if str(profile.get("symbol") or "").upper() != symbol:
        hard.append("Unternehmensprofil nicht exakt dem Ticker zugeordnet")
    if currency != "USD":
        hard.append("USD-Marktdaten nicht belegt; keine angenommene Waehrungsparitaet")
        turnover = None
    if profile.get("isEtf") or profile.get("isFund"):
        hard.append("ETF/Fonds statt Einzelaktie")
    if price is None or price < 5:
        hard.append("Aktienkurs unter 5 USD oder unbelegt")
    if cap is None or cap < 300_000_000:
        hard.append("Marktkapitalisierung unter 300 Mio. USD oder unbelegt")
    if turnover is None or turnover < 10_000_000:
        hard.append("Tagesumsatz unter 10 Mio. USD oder unbelegt")
    changes = {}
    for label, count in (("1d", 1), ("5d", 5)):
        if len(bars) > count and positive(bars[-count-1].get("close")) and positive(bars[-1].get("close")):
            changes[label] = float(bars[-1]["close"])/float(bars[-count-1]["close"]) - 1
    if changes.get("1d", 0) > .20 or changes.get("5d", 0) > .40:
        hard.append("Starker Kurssprung: nur beobachten")
    compact = []
    for source in sources:
        # Execution quotes/intraday ticks belong to the Core/chart, not to
        # repeated paid thesis analysis. Preserve community + earnings sources.
        if source.get("kind") in {"intraday", "quote"}:
            continue
        data = source.get("data")
        if isinstance(data, list):
            data = (sorted([r for r in data if isinstance(r, dict) and r.get("date")
                            and str(r["date"])[:10] < today], key=lambda r: r["date"])[-20:]
                    if source.get("kind") == "bars" else data[:3])
            if source.get("kind") == "news":
                from .news_normalization import normalize_news
                data = [{k: (str(r.get(k) or "")[:600] if k in {"title", "text"}
                              else r.get(k)) for k in ("title", "text", "publishedDate", "url", "publisher")}
                        for r in normalize_news(data, source.get("provider"))]
        elif source.get("kind") == "profile" and isinstance(data, dict):
            data = {k: data.get(k) for k in ("symbol", "companyName", "price", "marketCap", "sector", "industry", "isEtf", "isFund", "isActivelyTrading", "country", "currency", "cik")}
        elif source.get("kind") == "annual_financials" and isinstance(data, dict):
            from fmp_data import ANNUAL_SCHEMA, annual_context
            if data.get("schema_version") != ANNUAL_SCHEMA:
                data = annual_context(data)
            data = {k: data.get(k) for k in ("schema_version", "symbol", "cik", "available", "currency",
                "symbol_identity_verified", "latest", "years", "trends", "trend_period", "metrics", "reason", "errors", "source_family")}
        elif source.get("kind") == "companyfacts" and isinstance(data, dict):
            data = {k: data.get(k) for k in ("symbol", "available", "cik", "symbol_identity_verified", "schema_version", "revenue", "net_income",
                "operating_cashflow", "eps_diluted", "reason", "source")}
        elif source.get("kind") == "primary_document" and isinstance(data, dict):
            catalyst = enriched.get("catalyst") or {}
            data = {**data, "text": data["text"][:3000],
                    "verified_excerpt": catalyst.get("excerpt", "") if source["id"] in catalyst.get("source_ids", []) else ""}
        if source.get("provider") in {"apewisdom", "tradestie"}:
            data = {k: data.get(k) for k in ("symbol", "mentions", "mentions_24h_ago",
                "growth_ratio", "sentiment", "sentiment_score", "source_family", "unique_authors")}
        elif source.get("provider") in {"volume_watch", "stocktwits_trending"} and isinstance(data, dict):
            data = {k: data.get(k) for k in ("symbol", "source", "rvol", "gain", "rank", "trending_score", "detail")}
        compact.append({k: v for k, v in {**source, "data": data}.items() if k != "observed_at"})
    packet = {"symbol": symbol, "packet_revision": "pulsar-evidence-2", "sources": compact}
    if x_context:
        # X is optional. It must not crowd mandatory prices/financial receipts
        # out of the existing bounded Luna precheck. Keep the UI context and
        # disclose a deliberate omission instead of blocking the research path.
        try:
            from .ai_packet import project
            project(packet, budget=4300)
        except control.Blocked:
            omitted = [s["id"] for s in compact if s.get("kind") == "social_research_hint"]
            packet["sources"] = [s for s in compact if s.get("kind") != "social_research_hint"]
            packet["optional_sources_omitted"] = {"ids": omitted, "reason": "KI-Eingabelimit; X bleibt nur in der Kartenansicht"}
            x_context = {**x_context, "ai_context_status": "OMITTED_INPUT_LIMIT"}
    from fmp_data import annual_context
    fmp_context = {"role": "OPTIONAL_RESEARCH_ONLY",
        "daily": market.get("daily_metrics", {}),
        "annual": annual_context(market.get("annual_financials") or {}),
        "source_ids": [s["id"] for s in sources if s.get("provider") == "FMP"]}
    source_coordination = coordination(symbol, attention, tradestie, sources, enriched.get("catalyst") or {}, now=now)
    return {"symbol": symbol, "name": str(profile.get("companyName") or symbol),
        "attention": attention, "discovery": attention.get("discovery") or research.discovery_state(attention), "tradestie": tradestie, "baseline": baseline,
        "source_coordination": source_coordination, "attention_coverage": {
            k: baseline.get(k) for k in ("source", "method", "days", "censored_days", "unknown_days",
                "source_subset_days", "absent_from_observed_subset_days", "window_dates", "detail")},
        "x_context": x_context,
        "stocktwits": stocktwits_row, "stocktwits_baseline": stocktwits_baseline,
        "volume": volume, "short_interest": short_interest,
        "correlated_sources": bool(tradestie), "market": {"price": price, "market_cap": cap,
            "turnover_usd": turnover, "currency": currency or None, "changes": changes,
            "daily_metrics": market.get("daily_metrics", {})},
        "bars": bars, "intraday": intraday, "sector": str(profile.get("sector") or ""),
        "sources": sources, "evidence_hash": control.digest(packet),
        "fmp_context": fmp_context,
        "provider_status": market.get("provider_status", {}),
        "verified_evidence": {"catalyst": enriched.get("catalyst") or {}}, "web_research": search,
        "enrichment_at": enriched.get("observed_at"),
        "packet": packet, "score": None, "score_reason": "Fuer einen belastbaren Belegscore fehlen Pflichtdaten",
        "eligible": False, "state": "BEOBACHTUNG", "missing": [],
        "blocks": hard, "errors": list(market.get("errors", [])) + list(enriched.get("errors", [])),
        "thesis": (f"{symbol}: Recherchehinweis aus {attention['x_discovery']['sampled_post_count']} "
                   "Beitraegen der begrenzten X-Stichprobe; Kursanstieg nicht belegt."
                   if attention.get("source") == "X" else
                   f"{symbol}: Volumen-Ausloeser ({attention.get('detail') or 'relatives Volumen'}); Social-Belege werden getrennt gepruft."
                   if attention.get("source") == "volume_watch" else
                   f"{symbol}: Platz {attention.get('rank')} der StockTwits-Trending-Liste; Erwaehnungszahl und Kurs werden getrennt gepruft."
                   if attention.get("source") == "stocktwits_trending" else
                   f"{symbol}: {attention.get('mentions')} Erwaehnungen im gelieferten Aufmerksamkeitsfeed."),
        "risks": [], "claims": [], "text_source": "DATENUEBERSICHT",
        "analysis_status": "Individuelle KI-Pruefung steht aus",
        "precheck": {}, "analysis": {}, "countercheck": {}}


def apply_text(card, review, tier):
    from .source_coordination import protect_review
    safe = protect_review(review, card)
    if safe is not review:
        review.clear()
        review.update(safe)
    data = review["daten"]
    card["thesis"] = data["thesis"]["text"]
    card["risks"] = [r["text"] for r in data["risks"]]
    card["claims"] = [data["thesis"], *data["risks"]]
    card["text_source"] = tier.upper()
    card["analysis_status"] = f"{tier.title()}-Pruefung: {data['verdict']}"


def review_priority(cards):
    # Deterministic attention rank stays visible. Luna's actual verdict selects
    # the next deep review; REJECT and unavailable verdicts cannot promote one.
    return sorted([c for c in cards if not c["blocks"] and c.get("precheck", {}).get("ok")
                   and c["precheck"]["daten"]["verdict"] != "REJECT"],
        key=lambda c: (c["precheck"]["daten"]["verdict"] != "REVIEW", c["attention_rank"]))


class Worker:
    def __init__(self):
        self.stop_event = threading.Event()
        self.thread = None
        self.heartbeat_thread = None
        self.session = ""
        self.last_cycle = 0.
        self.last_holdings = 0.
        self.next_market_refresh = 0.

    def _refresh_market_context_if_due(self):
        """Use the cache's due time, independently of the holdings interval."""
        now = time.time()
        if now < self.next_market_refresh:
            return
        # A local/read failure must not create a five-second retry loop.
        self.next_market_refresh = now + 60
        import fmp_reference
        from fmp_data import refresh_market_context
        client = fmp_reference.client()
        if not client.konfiguriert:
            self.next_market_refresh = now + 300
            return
        due = refresh_market_context(client, now=now)
        self.next_market_refresh = max(now+5, float(due)) if due else now+300

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.stop_event.clear()
        self.session = control.start_session()
        self.thread = threading.Thread(target=self._run, name="nexus-pulsar", daemon=True)
        self.thread.start()
        self.heartbeat_thread=threading.Thread(target=self._heartbeat,name='nexus-pulsar-heartbeat',daemon=True)
        self.heartbeat_thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=1)
        if self.heartbeat_thread:
            self.heartbeat_thread.join(timeout=1)

    def _heartbeat(self):
        # Worker liveness is independent of a bounded, slow research request.
        # Evidence timestamps and proposal revisions keep their own freshness gates.
        while not self.stop_event.wait(5):
            if not self.thread or not self.thread.is_alive():return
            try:
                control.heartbeat(self.session)
            except Exception as exc:
                LOG.warning('PULSAR-Heartbeat: %s',type(exc).__name__)

    def _run(self):
        while not self.stop_event.is_set():
            try:
                control.heartbeat(self.session)
                from .research_history import maintain
                maintain()
                if time.time() - self.last_holdings >= 3600:
                    self.last_holdings = time.time()
                    refresh_held_data()
                    update_measurements()
                self._refresh_market_context_if_due()
                state = control.settings()
                local = datetime.now(ZoneInfo("America/New_York"))
                interval = 900 if local.weekday() < 5 else 12*3600
                in_window = (7 <= local.hour < 19) if local.weekday() < 5 else True
                if state["mode"] != "AUS" and in_window and time.time() - self.last_cycle >= interval:
                    self.last_cycle = time.time()
                    self.cycle(state)
                elif state["mode"] != "AUS":
                    self._retry_review(state)
                previous = research.cached("status", stale=True)
                if previous and previous["data"].get("worker_error") is True:
                    research.cache_put("status", {"ok": True, "busy": False,
                        "phase": "WARTEZEIT", "observed_at": time.time(),
                        "detail": "Worker wieder erreichbar; nächste Recherche nach Zeitplan",
                        "last_error": previous["data"].get("detail"),
                        "last_error_at": previous["saved"],
                        "schedule": scan_schedule(self.last_cycle)}, 3600)
            except Exception as exc:
                LOG.warning("PULSAR: %s", exc)
                try:
                    research.cache_put("status", {"ok": False, "worker_error": True, "detail": str(exc)[:300]}, 900)
                except Exception:
                    LOG.exception("PULSAR-Status konnte nicht gespeichert werden")
            self.stop_event.wait(5)

    def _phase(self, name, symbol=""):
        research.cache_put("status", {"ok": True, "busy": True, "phase": name,
            "symbol": symbol, "observed_at": time.time(), "detail": "Recherchephase: " + name,
            "schedule": scan_schedule(self.last_cycle)}, 3600)

    def _retry_review(self, state):
        """Resume failed text work, including weekends; reuse original facts.

        At most two additional attempts within two hours. No repeated discovery
        or market-data fetch. Mode changes and stop requests cancel this work.
        """
        hit = research.cached("ai:precheck:work")
        if not hit:
            return False
        work = hit["data"]
        from .analysis import REVISION as REVIEW_REVISION
        if work and work.get("selection_revision") != SELECTION_REVISION:
            # A pending pre-update batch can contain ETFs. Do not dispatch it
            # before the next bounded, typed selection has replaced it.
            research.cache_put("ai:precheck:work", {}, 0)
            return False
        if (not work or state["mode"] == "AUS" or self.stop_event.is_set()
                or work.get("review_revision") != REVIEW_REVISION
                or work.get("revision") != state["revision"]
                or time.time() >= work["expires_at"]
                or time.time() < work["next_at"] or work["attempts"] >= 2):
            return False
        self._review_cards(state, work["cards"], work["markets"], work["errors"],
            retry_count=work["attempts"]+1, expires_at=work["expires_at"])
        return True

    def _hype_alarm(self, cards):
        """Sofortiger Telegram-Hinweis je Hype-Kandidat, hoechstens einmal je 24h.

        Der Alarm ist reine Information mit Belegen -- er nominiert nichts und
        gibt keine Order frei. Im FREIGABE-Modus folgt die Nominierung separat
        ueber den Core im NY-Handelsfenster mit zweistufiger Bestaetigung.
        """
        from .telegram import notify_hype
        for card in cards:
            if not card.get("eligible"):
                continue
            key = "hype_alarm:" + card["symbol"]
            if research.cached(key):
                continue
            try:
                if notify_hype(card, mode=control.settings()["mode"]):
                    research.cache_put(key, {"at": time.time()}, 86400)
            except Exception as exc:
                LOG.warning("PULSAR-Hype-Alarm %s nicht zustellbar: %s", card["symbol"], exc)

    def cycle(self, state):
        from .research_history import maintain
        maintain()
        # Held positions are serviced before discovery, including weekends
        # and modes where there are currently no candidates.
        refresh_held_data()
        self._phase("QUELLEN")
        rows = research.discover()
        other, errors = {}, []
        try:
            from market_intelligence import discovery_candidates
            x_candidates = discovery_candidates(limit=12)
        except Exception as exc:
            x_candidates = []
            errors.append("X-Kandidatensuche lokal nicht verfuegbar: " + type(exc).__name__)
        if state["tradestie"]:
            try:
                if control.settings()["mode"] == "AUS":
                    return
                other = {r["symbol"]: r for r in research.fetch_social("tradestie")}
            except Exception as exc:
                errors.append("Tradestie: " + str(exc)[:160])
        # 10.5.0: Volumen- und StockTwits-Zusatzkandidaten liefert research.discover()
        # mit (Feld ``source``); select_candidates trennt sie von den Reddit-Zeilen.
        cards, markets = [], {}
        def active():
            current = control.settings()
            return (not self.stop_event.is_set() and current["revision"] == state["revision"]
                    and current["mode"] != "AUS")
        self._phase("INSTRUMENTTYP")
        candidates = select_candidates(rows, active=active, x_candidates=x_candidates)
        for candidate in candidates:
            attention = candidate["attention"]
            current = control.settings()
            if self.stop_event.is_set() or current["revision"] != state["revision"] or current["mode"] == "AUS":
                return
            control.heartbeat(self.session)
            try:
                research.reserve_enrichment(attention["symbol"])
                self._phase("MARKTDATEN", attention["symbol"])
                market = gather_market(attention["symbol"], profile_evidence=candidate["profile_evidence"])
            except control.Blocked as exc:
                market = {"sources": [], "profile": {}, "bars": [], "errors": [str(exc)]}
            hit = research.cached("enrichment:" + attention["symbol"])
            if hit:
                market["enrichment"] = hit["data"]
            markets[attention["symbol"]] = market
            card = build_card(attention, market, other.get(attention["symbol"]))
            card["instrument_type_evidence"] = candidate["selection"]
            card["attention_rank"] = len(cards)+1
            cards.append(card)
            _cache_cards(cards, 3600)
        selection_hit = research.cached("candidate_selection")
        if selection_hit:
            summary = selection_hit["data"]
            summary.setdefault("source_pipeline", {}).update(
                researched=len(cards), x_researched=sum(bool(c["attention"].get("x_discovery")) for c in cards),
                research_status="MARKET_RESEARCH_COMPLETE", research_at=time.time(),
                research_errors=sum(bool(c.get("errors")) for c in cards))
            research.cache_put("candidate_selection", summary, 3600)
        self._review_cards(state, cards, markets, errors)

    def _review_cards(self, state, cards, markets, errors, *, retry_count=0, expires_at=None):
        # 10.3.0: Nur noch die schnelle Luna-Vorpruefung (Warnfilter) laeuft.
        # Terra-Vertiefung und Terra-Gegenpruefung sind mit der Score-Maschine
        # entfallen -- die Hype-Spur entscheidet ueber Social-Spike und
        # Kursbestaetigung, die Orderfreigabe bleibt persoenlich.
        from ai_router import AIRouter
        from .analysis import precheck, REVISION as REVIEW_REVISION
        from .diagnostics import not_started
        from copy import deepcopy
        expires_at = expires_at or time.time()+7200
        # Replace the previous retry job before starting; a crash cannot leave
        # a five-second paid-request loop. Budget reservations remain durable.
        research.cache_put("ai:precheck:work", {}, 0)
        source_errors = list(errors)
        precheck_failed = False
        router = AIRouter()
        preliminary = None
        if router.aktiv and cards and control.settings()["revision"] == state["revision"]:
            try:
                self._phase("LUNA_VORAUSWAHL")
                preliminary = precheck([r["packet"] for r in cards], router)
                if not preliminary.get("ok"):
                    raise control.Blocked(preliminary.get("grund") or "Luna-Vorpruefung fehlt")
                notes = {r["symbol"]: r for r in preliminary["daten"]["notes"]}
                for card in cards:
                    card["precheck"] = {"ok": True, "stufe": "luna", "daten": notes[card["symbol"]],
                        "cached": bool(preliminary.get("cached")),
                        **{k: preliminary.get(k) for k in ("modell", "execution", "source_execution", "input_hash", "input_bytes", "review_revision")},
                        "input_sources": {card["symbol"]: preliminary.get("input_sources", {}).get(card["symbol"])}}
                    apply_text(card, card["precheck"], "luna")
                # One NEW deep review per cycle; cached analyses of the other
                # candidates survive, so the first ticker cannot starve them.
                priority = review_priority(cards)
                # Only one prefiltered candidate causes new source/GPT research.
                selected = next((r for r in priority if not r.get("enrichment_at")), None)
                if selected and control.settings()["revision"] == state["revision"]:
                    from .sources import enrich
                    market = markets[selected["symbol"]]
                    try:
                        self._phase("ORIGINALQUELLEN", selected["symbol"])
                        market["enrichment"] = enrich(selected["symbol"], market, router)
                        updated = build_card(selected["attention"], market, selected.get("tradestie"))
                        updated.update(attention_rank=selected["attention_rank"], precheck=selected["precheck"],
                            instrument_type_evidence=selected.get("instrument_type_evidence", {}))
                        selected.update(updated)
                        apply_text(selected, selected["precheck"], "luna")
                    except Exception as exc:
                        selected["errors"].append("Ereignisrecherche: " + str(exc)[:180])
                if control.settings()["revision"] != state["revision"]:
                    return
            except Exception as exc:
                precheck_failed = any(not c["precheck"].get("ok") for c in cards)
                errors.append("KI: " + str(exc)[:160])
                for card in cards:
                    if not card["precheck"].get("ok"):
                        if preliminary is not None:
                            # All five cards share one batch receipt. Never
                            # imply five separate requests from one failure.
                            card["precheck"] = {k: v for k, v in preliminary.items() if k != "daten"}
                        else:
                            # An unexpected exception may occur after dispatch;
                            # absence of a returned receipt proves nothing.
                            card["precheck"] = {"ok": False, "grund": str(exc)[:160], "execution": {}}
                        for stage in ("analysis", "countercheck"):
                            if not card.get(stage, {}).get("ok"):
                                card[stage] = not_started("Gemeinsame GPT-Vorpruefung nicht erfolgreich; Stufe nicht gestartet",
                                                          "PULSAR_PRECHECK_FAILED")
                        card["analysis_status"] = "KI-Pruefung offen: " + str(exc)[:160]
                if precheck_failed and retry_count < 2 and time.time()+900 < expires_at:
                    research.cache_put("ai:precheck:work", {
                        "review_revision": REVIEW_REVISION,
                        "selection_revision": SELECTION_REVISION,
                        "revision": state["revision"], "cards": deepcopy(cards),
                        "markets": deepcopy(markets), "errors": source_errors,
                        "attempts": retry_count, "next_at": time.time()+900,
                        "expires_at": expires_at}, expires_at-time.time())
        else:
            changed = control.settings()["revision"] != state["revision"]
            reason = "PULSAR-Steuerung geaendert" if changed else "KI-Router nicht aktiv; nur Datenuebersicht"
            code = "PULSAR_MODE_CHANGED" if changed else "AI_DISABLED"
            for card in cards:
                card["analysis_status"] = reason
                for stage in ("precheck", "analysis", "countercheck"):
                    card[stage] = not_started(reason, code)
        from .evidence import evaluate
        from .source_coordination import protect_review
        for card in cards:
            for stage in ("precheck", "analysis", "countercheck"):
                card[stage] = protect_review(card.get(stage) or {}, card)
            card.update(evaluate(card))
        cards = [research.save_assessment(r) for r in cards]
        # 10.5.0 Vorwaertsmessung: jeder Ausloeser wird mit Kurs festgehalten,
        # unabhaengig davon, ob gehandelt wird. Fehler beruehren die Karten nicht.
        for card in cards:
            try:
                from .measurement import record as record_measurement
                record_measurement(card, mode=state["mode"])
            except Exception as exc:
                LOG.warning("PULSAR-Messung %s nicht gespeichert: %s", card.get("symbol"), exc)
        # 10.3.0 Universums-Zubringer: Entdeckungen mit belegter Identitaet
        # (keine harten Kartenbloecke ausser Kurssprung-Beobachtung) wandern in
        # die Gastliste fuer die normale NEXUS-Standard-Pruefung.
        try:
            research.merke_universumsgaeste([
                c["symbol"] for c in cards
                if not any("Profil" in b or "ETF" in b for b in c.get("blocks", []))])
        except Exception as exc:
            LOG.warning("PULSAR-Universumsgaeste nicht gespeichert: %s", exc)
        self._hype_alarm(cards)
        _cache_cards(cards, 3600)
        research.cache_put("status", {"ok": not precheck_failed, "busy": False,
            "phase": "VORPRUEFUNG_OFFEN" if precheck_failed else "ABGESCHLOSSEN",
            "detail": "GPT-Vorpruefung offen" if precheck_failed else "Beobachtung aktualisiert",
            "errors": errors, "observed_at": time.time(), "count": len(cards),
            "schedule": scan_schedule(self.last_cycle)}, 3600)
        control.heartbeat(self.session)


def update_measurements(*, now=None):
    """Faellige Vorwaertsmessungen mit FMP-Tageskerzen nachtragen (hoechstens alle 6 h)."""
    now = time.time() if now is None else now
    if research.cached("measurement:updated", now=now):
        return None
    try:
        from .measurement import update_outcomes
        import fmp_reference
        client = fmp_reference.client()

        def bars_provider(symbol):
            hit = research.cached("fmp:bars:" + symbol, now=now)
            if hit and 0 <= now - hit["saved"] <= 86400:
                return hit["data"]
            if not client.konfiguriert or control.settings()["mode"] == "AUS":
                return hit["data"] if hit else []
            from fmp_service import consumer
            with consumer("pulsar"):
                bars = _request("measurement:bars:" + symbol, 6*3600, lambda: client.tageskerzen(symbol, 40))
            return bars
        result = update_outcomes(bars_provider, now=now)
        research.cache_put("measurement:updated", result, 6*3600, now=now)
        return result
    except Exception as exc:
        LOG.warning("PULSAR-Vorwaertsmessung nicht aktualisiert: %s", exc)
        research.cache_put("measurement:updated", {"error": type(exc).__name__}, 3600, now=now)
        return None


def refresh_held_data():
    """No broker access: refresh registered held PULSAR instruments first."""
    import fmp_reference
    from fmp_service import consumer
    client = fmp_reference.client()
    if not client.konfiguriert:
        return
    symbols=set()
    try:
        import json
        path=client.store.path.parent/"stock_positions.json"
        if path.exists():
            snapshot=json.loads(path.read_text(encoding="utf-8"))
            for row in snapshot.get("positionen", []):
                symbol=str(row.get("symbol") or "").upper()
                if research.TICKER.fullmatch(symbol) and float(row.get("menge") or 0)>0:
                    symbols.add(symbol)
    except (OSError,ValueError,TypeError) as exc:
        LOG.info("FMP: Positionsliste nicht lesbar (%s)", type(exc).__name__)
    with consumer("held_positions"):
        for proposal in control.proposals(active_only=True):
            if proposal.get("status") not in {"FILLED", "PARTIALLY_FILLED"}:
                continue
            symbols.add(proposal["symbol"])
        for symbol in sorted(symbols)[:20]:
            try:
                bars = client.tageskerzen(symbol, 95, purpose="position")
                hit = client.store.cached("history:" + symbol)
                if hit:
                    research.cache_put("fmp:bars:" + symbol, bars, 2*86400, now=hit["saved"])
                if client.erlaubt("/news/stock"):
                    client.nachrichten(symbol, purpose="position")
            except RuntimeError as exc:
                LOG.info("PULSAR-Tagesdaten %s: %s", symbol, exc)
        client.store.maintain()
    from fmp_data import refresh_market_context
    refresh_market_context(client)
