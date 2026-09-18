"""Research-only source coordination; no votes, trading signals or HTTP calls.

A cashtag and a news topic are leads. Neither proves the named company nor an
event. Provider count is deliberately never used as independent confirmation.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import ipaddress
import hashlib
import math
import re
from urllib.parse import urlsplit, urlunsplit

from . import control, research

X_SAMPLE_MAX_AGE = 86400


def x_attention(seed, *, now):
    """Convert a bounded sample to a research row without fabricating mentions."""
    if not isinstance(seed, dict):
        return None
    symbol = seed.get("symbol")
    stamp = seed.get("observed_at")
    evidence = seed.get("evidence_ids")
    if (not isinstance(symbol, str) or not research.TICKER.fullmatch(symbol)
            or seed.get("source_family") != "X"
            or type(stamp) not in (int, float) or not math.isfinite(stamp)
            or not 0 <= now-stamp <= X_SAMPLE_MAX_AGE
            or not isinstance(evidence, list) or not evidence
            or any(not isinstance(e, str) or not e or len(e) > 160 for e in evidence)):
        return None
    sampled = research._count(seed.get("sampled_post_count"))
    if not sampled:
        return None
    # Only the allowed derived metadata is persisted or sent to the old AI path.
    hint = {k: deepcopy(seed.get(k)) for k in ("evidence_ids", "sampled_post_count",
        "distinct_accounts_in_sample", "topics", "source_roles", "linked_url_hashes")}
    hint.update(source_family="X", symbol=symbol, status="UNVERIFIED",
        direct_trade_effect=False, attention_kind="BOUNDED_SOCIAL_SAMPLE", calibrated_spike=False)
    row = {"symbol": symbol, "source": "X", "source_family": "X",
        "observation_source": "x_discovery_sample", "observed_at": stamp,
        "mentions": None, "mentions_24h_ago": None, "growth_ratio": None,
        "unique_authors": None, "rank": None, "newly_observed": False,
        "url": "https://x.com/", "discovery_origin": "X", "x_discovery": hint}
    row["evidence_id"] = control.digest(research.facts(row))
    return row


def attention_fresh(card, *, now):
    """A recent assessment never refreshes the underlying observation time.

    10.3.0 (Hype-Spur): Reddit-Aufmerksamkeit gilt eine Stunde, eine begrenzte
    X-Stichprobe 24 Stunden. Die fruehere Pflicht einer vollstaendigen
    X-Tageszaehlung mit mindestens 14 Vortagen derselben Abfrage
    (COMPLETE_DAILY_X_COUNTS) ist mit der Score-Maschine entfallen; eine
    Stichprobe bleibt trotzdem nie eine Erwaehnungs-Vollzaehlung.
    """
    attention = card.get("attention") or {}
    observed = attention.get("observed_at")
    if type(observed) not in (int, float) or not math.isfinite(observed):
        return False
    if attention.get("source") != "X":
        return 0 <= now-observed <= 3600
    return bool(attention.get("x_discovery")) and 0 <= now-observed <= X_SAMPLE_MAX_AGE


def research_order(rows, x_candidates, *, now, extra_candidates=None):
    """Preserve Reddit ranking while reserving two of five research slots for X.

The queues are interleaved, so an invalid/ETF candidate cannot monopolise the
other source's profile budget. Empty/exhausted queues lend their slots. A
shared ticker stays one Reddit row with separate X evidence, never summed.

10.5.0: ``extra_candidates`` (Volumen-Ausloeser aus Stundenkerzen, StockTwits-
Trending) reihen sich als weitere Saatliste ein -- frisch (<= 1 h), gueltiger
Ticker, keine Dubletten; ein Reddit-Treffer behaelt seine Zeile und bekommt
den Zusatzbeleg angehaengt.
"""
    reddit = research.attention_order(rows)
    by_symbol = {r.get("symbol"): deepcopy(r) for r in reddit}
    seeds, rejected, duplicates = [], [], 0
    seen = set()
    extra_valid = 0
    for seed in (extra_candidates or [])[:12]:
        if not isinstance(seed, dict):
            continue
        symbol = str(seed.get("symbol") or "").upper()
        observed = seed.get("observed_at")
        if (not research.TICKER.fullmatch(symbol) or type(observed) not in (int, float)
                or not 0 <= now - observed <= 3600):
            rejected.append({"symbol": symbol[:20], "reason": "Zusatzkandidat ohne gueltigen Ticker oder frischen Zeitbeleg"})
            continue
        if symbol in seen:
            duplicates += 1
            continue
        seen.add(symbol)
        extra_valid += 1
        volume = seed.get("source") == "volume_watch"
        key = "volume_discovery" if volume else "stocktwits_discovery"
        if symbol in by_symbol:
            by_symbol[symbol][key] = {k: seed.get(k) for k in ("source", "observed_at", "rvol", "gain", "rank", "trending_score", "detail")}
            by_symbol[symbol]["discovery_origin"] = "REDDIT_AND_" + ("VOLUMEN" if volume else "STOCKTWITS")
            duplicates += 1
            if symbol not in {r['symbol'] for r in reddit[:3]}:
                seeds.append(by_symbol[symbol])
        else:
            seeds.append({**seed, "discovery_origin": "VOLUMEN" if volume else "STOCKTWITS"})
    for seed in (x_candidates or [])[:12]:
        converted = x_attention(seed, now=now)
        if converted is None:
            rejected.append({"symbol": str((seed if isinstance(seed, dict) else {}).get("symbol", ""))[:20],
                "reason": "X-Stichprobe ohne gueltigen Ticker, frischen Zeitbeleg oder Einzelbeleg"})
            continue
        symbol = converted["symbol"]
        if symbol in seen:
            duplicates += 1
            continue
        seen.add(symbol)
        if symbol in by_symbol:
            by_symbol[symbol]["x_discovery"] = converted["x_discovery"]
            by_symbol[symbol]["discovery_origin"] = "REDDIT_AND_X"
            duplicates += 1
            if symbol not in {r['symbol'] for r in reddit[:3]}:
                seeds.append(by_symbol[symbol])
        else:
            seeds.append(converted)
    promoted = {r['symbol'] for r in seeds}
    reddit = [by_symbol[r["symbol"]] for r in reddit if r['symbol'] not in promoted]
    if not seeds:
        ranked = reddit
    else:
        # Preserve the pre-existing cold-start privilege within the smaller
        # Reddit share. It remains research-only, with no score privilege.
        if len(reddit) >= 5 and reddit[4].get("mentions_24h_ago") is None:
            reddit.insert(2, reddit.pop(4))
        ranked = []
        while reddit or seeds:
            if reddit:
                ranked.append(reddit.pop(0))
            if seeds:
                ranked.append(seeds.pop(0))
    return ranked, {"mode": "AUTOMATIC_RESEARCH_DISCOVERY", "trade_effect": False,
        "reddit_received": len(rows), "x_received": len(x_candidates or []),
        "extra_received": len(extra_candidates or []), "extra_valid": extra_valid,
        "x_valid_samples": len(seen) - extra_valid, "x_duplicates_merged": duplicates,
        "x_invalid_samples": rejected, "x_invalid_count": len(rejected),
        "research_slots": {"reddit": 3, "x": 2, "shared_limit": 5},
        "detail": "Gemeinsame begrenzte Aktienrecherche; X-Stichproben sind keine Erwaehnungs-Vollerhebung. "
                  "Keine direkte Handels- oder Krisenentscheidung."}


def canonical_url(value):
    try:
        parsed = urlsplit(str(value or ""))
        host = (parsed.hostname or "").lower().rstrip(".")
        if (parsed.scheme not in {"http", "https"} or not host or parsed.username
                or parsed.password or parsed.port not in {None, 80, 443}
                or host == "localhost" or "." not in host):
            return None
        try:
            if not ipaddress.ip_address(host).is_global:
                return None
        except ValueError:
            pass
        # Removing parameters is conservative: uncertain syndication stays one
        # origin rather than becoming a manufactured independent confirmation.
        return urlunsplit(("https", host.removeprefix("www."), parsed.path.rstrip("/"), "", ""))
    except (ValueError, TypeError):
        return None


def _recent(value, now):
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            from zoneinfo import ZoneInfo
            stamp = stamp.replace(tzinfo=ZoneInfo("America/New_York"))
        return 0 <= now-stamp.timestamp() <= 72*3600
    except (ValueError, TypeError):
        return False


def coordination(symbol, attention, tradestie, sources, catalyst, *, now):
    from .news_normalization import normalize_news
    from .sources import valid_catalyst
    x_seed = attention.get("x_discovery") or {}
    x_present = bool(x_seed or any(s.get("provider") == "X" for s in sources))
    reddit_present = attention.get("source_family") == "reddit_aggregate" or bool(tradestie)
    documents, news_count = {}, 0
    for source in sources:
        if source.get("kind") != "news":
            continue
        for row in normalize_news(source.get("data"), source.get("provider")):
            url = canonical_url(row.get("url"))
            if not url or not _recent(row.get("publishedDate"), now):
                continue
            news_count += 1
            record = documents.setdefault(url, {"url": url, "providers": set(), "source_ids": set()})
            record["providers"].add(str(source.get("provider")))
            record["source_ids"].add(source.get("id"))
    links = {s for s in (x_seed.get("linked_url_hashes") or []) if isinstance(s, str)}
    article_hashes = {hashlib.sha256(url.encode()).hexdigest() for url in documents}
    originals = {s["id"]: s for s in sources}
    primary = valid_catalyst(catalyst, originals, symbol, now=now)
    profile = any(s.get("kind") == "profile" and isinstance(s.get("data"), dict)
                  and s["data"].get("symbol") == symbol for s in sources)
    financial = any(s.get("kind") in {"annual_financials", "companyfacts"}
                    and isinstance(s.get("data"), dict) and s["data"].get("available") is True
                    for s in sources)
    return {"status": "PRIMARY_COMPANY_EVENT_VERIFIED" if primary else "INDEPENDENT_EVENT_CHECK_PENDING",
        "roles": {"X": "DISCOVERY_HINT" if x_present else "NOT_OBSERVED",
            "Reddit": "SEPARATE_AGGREGATE_ATTENTION" if reddit_present else "NOT_OBSERVED",
            "FMP_profile": "IDENTITY_RECEIPT" if profile else "MISSING",
            "financials": "RECEIVED_REQUIRES_QUALITY_CHECK" if financial else "MISSING",
            "news": "RESEARCH_LEADS" if documents else "MISSING",
            "primary_document": "VERIFIED_COMPANY_EVENT" if primary else "MISSING"},
        "news_rows": news_count, "distinct_article_urls": len(documents),
        "duplicate_news_rows": max(0, news_count-len(documents)),
        "shared_x_article_count": len(links & article_hashes),
        "article_groups": [{"url": r["url"], "providers": sorted(r["providers"]),
            "source_ids": sorted(i for i in r["source_ids"] if i)} for r in list(documents.values())[:10]],
        "cross_platform_attention": "SEPARATELY_OBSERVED_NOT_INDEPENDENCE_PROOF" if x_present and reddit_present else "NOT_COMPARABLE",
        "primary_event_source_ids": catalyst.get("source_ids", []) if primary else [],
        "x_claim_confirmed": False, "independent_event_sources": None,
        "crisis_authorized": False, "trade_effect": False,
        "detail": "X liefert Recherchekandidaten. Profile pruefen die Identitaet, Nachrichten liefern Hinweise, "
                  "Originalbelege pruefen den Unternehmensanlass. Mehrere Anbieter desselben Artikels, "
                  "Reposts und gleiche Themen sind keine unabhaengige Bestaetigung. Ein belegter "
                  "Unternehmensanlass bestaetigt nicht automatisch eine X-Behauptung."}


def protect_review(review, card):
    """An AI verdict backed only by X cannot become a rejection/approval vote."""
    if not review.get("ok") or not isinstance(review.get("daten"), dict):
        return review
    data = review["daten"]
    if data.get("verdict") not in {"REJECT", "REVIEW"}:
        return review
    sources = {s.get("id"): s for s in card.get("sources", [])}
    claims = [data.get("thesis") or {}, *(data.get("risks") or [])]
    refs = {ref for claim in claims for ref in claim.get("source_ids", [])}
    x_refs = {ref for ref in refs if (sources.get(ref) or {}).get("provider") == "X"}
    relevant_refs = {ref for claim in (data.get("risks") or []) for ref in claim.get("source_ids", [])}
    if data.get("verdict") != "REJECT":
        relevant_refs = refs
    # A profile citation establishes the company name, not the negative claim.
    # It cannot launder an X-only risk into a rejection. Likewise two Reddit
    # aggregate feeds do not independently substantiate an X event.
    independent_content = {ref for ref in relevant_refs if (sources.get(ref) or {}).get("provider") != "X"
        and (sources.get(ref) or {}).get("kind") in {"primary_document", "news", "annual_financials", "companyfacts", "bars"}}
    if not x_refs or independent_content:
        return review
    result = deepcopy(review)
    result["source_authority"] = {"status": "X_ONLY_VERDICT_NOT_APPLIED", "original_verdict": data["verdict"],
        "detail": "Nur X-Belege zitiert; eigenstaendige Gegenpruefung erforderlich. Kein Kauf-/Ablehnungsentscheid."}
    result["daten"]["verdict"] = "OBSERVE"
    result["daten"]["missing"] = list(dict.fromkeys([*data.get("missing", []),
        "X-Hinweis braucht eine eigenstaendige Pruefung mit Unternehmens- und Marktdaten"]))
    return result
