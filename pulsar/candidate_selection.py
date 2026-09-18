"""Bounded single-stock selection before market enrichment and paid research.

Only a dated, symbol-matched FMP profile with typed ETF/fund flags qualifies.
The existing scoped FMP cache and request budget remain authoritative. A
symbol-only permanent type table would survive ticker reuse, so none is kept.
"""
from __future__ import annotations

import math
import time

from . import control, research

SELECTION_REVISION = "single-stock-profile-x-research-3"
PROFILE_MAX_AGE = 86400
MAX_INSPECTED = 20
MAX_PROFILE_ATTEMPTS = 10
MAX_CARDS = 5


def profile_type(symbol, profile):
    if not isinstance(profile, dict) or profile.get("symbol") != symbol:
        return "UNKNOWN", "Unternehmensprofil nicht exakt dem Ticker zugeordnet"
    if profile.get("isEtf") is True or profile.get("isFund") is True:
        return "ETF_OR_FUND", "ETF/Fonds gehoert nicht in die fuenf Aktienkandidaten"
    if profile.get("isEtf") is False and profile.get("isFund") is False:
        return "SINGLE_STOCK", "Einzelaktie anhand typisiertem FMP-Profil belegt"
    return "UNKNOWN", "Instrumenttyp unbelegt: isEtf/isFund fehlen oder sind nicht boolesch"


def _profile_hit(client, symbol, now):
    hit = client.cached_source("/profile", {"symbol": symbol})
    if not isinstance(hit, dict):
        return None
    try:
        saved, expires = float(hit["saved"]), float(hit["expires"])
        if (not all(math.isfinite(n) for n in (saved, expires))
                or not 0 <= now-saved <= PROFILE_MAX_AGE or expires <= now):
            return None
    except (KeyError, ValueError, TypeError):
        return None
    rows = hit.get("data")
    profile = rows[0] if isinstance(rows, list) and len(rows) == 1 else rows
    if not isinstance(profile, dict):
        profile = {}
    source = {"provider": "FMP", "kind": "profile", "symbol": symbol,
        "observed_at": saved, "data": profile,
        "url": "https://financialmodelingprep.com/stable/profile"}
    source["id"] = control.digest(research.facts(source))
    return {"profile": profile, "source": source}


def load_profile(client, symbol, *, allow_fetch, now):
    """A missing/invalid flag stays unknown; errors never become ETF evidence."""
    result = {"profile": {}, "source": None, "fetch_attempted": False}
    if not client.konfiguriert:
        return {**result, "status": "UNKNOWN", "reason": "FMP nicht eingerichtet; Instrumenttyp unbelegt"}
    hit = _profile_hit(client, symbol, now)
    if hit:
        kind, reason = profile_type(symbol, hit["profile"])
        return {**result, **hit, "status": kind, "reason": reason}
    if not allow_fetch:
        return {**result, "status": "UNKNOWN", "reason": "Profilgrenze dieses Suchlaufs erreicht"}
    # Shared FMP quota/backoff still applies. A local pause also covers responses
    # without a usable receipt; do not retry ten times every worker heartbeat.
    scope = str(getattr(getattr(client, "store", None), "scope", "unscoped"))
    pause_key = "candidate:profile-failed:" + scope + ":" + symbol
    if research.cached(pause_key, now=now):
        return {**result, "status": "UNKNOWN", "reason": "Instrumenttyp unbelegt; Profilabruf in Pause"}
    try:
        research.reserve_enrichment(symbol, now=now)
        from fmp_service import consumer
        result["fetch_attempted"] = True
        with consumer("pulsar"):
            client.profil(symbol)
        # Never manufacture a timestamp from the wall clock or from a profile's
        # unrelated financial date. Require the transport cache receipt.
        hit = _profile_hit(client, symbol, time.time())
        if hit:
            kind, reason = profile_type(symbol, hit["profile"])
            return {**result, **hit, "status": kind, "reason": reason}
        reason = "Instrumenttyp unbelegt; datierter FMP-Profilbeleg fehlt"
    except Exception as exc:
        reason = "Instrumenttyp unbelegt; Profilabruf fehlgeschlagen (" + type(exc).__name__ + ")"
    research.cache_put(pause_key, {"reason": reason}, 3600, now=now)
    return {**result, "status": "UNKNOWN", "reason": reason}


def select_candidates(rows, *, active=lambda: True, now=None, client=None, x_candidates=None, extra_candidates=None):
    """Keep attention ordering and its cold-start slot; fill at most five stocks.

    Each missing profile is resolved only when needed for an actual free slot.
    Once five stocks are found, no later profile is queried or enriched.
    """
    now = time.time() if now is None else float(now)
    if client is None:
        import fmp_reference
        client = fmp_reference.client()
    selected, inspected, seen = [], [], set()
    attempts = 0
    from .source_coordination import research_order
    reddit_rows, found_extra = research.split_discoveries(rows)
    ranked, pipeline = research_order(reddit_rows, x_candidates, now=now,
                                      extra_candidates=list(found_extra) + list(extra_candidates or []))
    for attention in ranked:
        if len(inspected) >= MAX_INSPECTED or len(selected) >= MAX_CARDS or not active():
            break
        symbol = attention.get("symbol")
        if not isinstance(symbol, str) or not research.TICKER.fullmatch(symbol) or symbol in seen:
            continue
        seen.add(symbol)
        evidence = load_profile(client, symbol, allow_fetch=attempts < MAX_PROFILE_ATTEMPTS, now=now)
        attempts += int(evidence["fetch_attempted"])
        source = evidence.get("source") or {}
        profile = evidence.get("profile") or {}
        monitoring = "NOT_X_DISCOVERY"
        if attention.get("x_discovery"):
            try:
                from market_intelligence import record_candidate_validation
                # A just-fetched profile was observed after the selection's
                # initial timestamp; compare it with the actual receipt time.
                validation_now = time.time() if evidence["fetch_attempted"] else now
                result = record_candidate_validation(symbol, source, now=validation_now)
                monitoring = str((result or {}).get("status") or "RECORDED")
            except Exception as exc:
                # Optional X monitoring cannot stop independent stock research.
                # Its separate persistent quota and enrollment remain closed.
                monitoring = "UNAVAILABLE_" + type(exc).__name__
        receipt = {"symbol": symbol, "status": evidence["status"], "reason": evidence["reason"],
            "discovery_origin": attention.get("discovery_origin", "REDDIT"),
            "x_count_monitoring": monitoring,
            "profile_source_id": source.get("id"), "observed_at": source.get("observed_at"),
            "identity": {k: profile.get(k) for k in ("symbol", "cik", "isin", "cusip")},
            "isEtf": profile.get("isEtf"), "isFund": profile.get("isFund"),
            "profile_fetch_attempted": evidence["fetch_attempted"]}
        inspected.append(receipt)
        if evidence["status"] == "SINGLE_STOCK":
            selected.append({"attention": attention, "profile_evidence": evidence, "selection": receipt})
    try:
        from market_intelligence.candidate_research import enqueue
        queued = enqueue(selected, now=max(now, time.time()) if any(r['profile_evidence']['fetch_attempted'] for r in selected) else now)
        pipeline['x_candidate_research_queued'] = queued
    except Exception as exc:
        pipeline['x_candidate_research_error'] = type(exc).__name__
    pipeline.update(observed_at=now,
        x_profile_validated=sum(bool(r["attention"].get("x_discovery")) for r in selected),
        x_new_selected=sum(r["attention"].get("source") == "X" for r in selected),
        reddit_selected=sum(r["attention"].get("source") not in {"X", "volume_watch", "stocktwits_trending"} for r in selected),
        volume_selected=sum(r["attention"].get("source") == "volume_watch" for r in selected),
        stocktwits_selected=sum(r["attention"].get("source") == "stocktwits_trending" for r in selected),
        x_profile_rejected=sum(r["discovery_origin"] in {"X", "REDDIT_AND_X"}
                               and r["status"] != "SINGLE_STOCK" for r in inspected),
        researched=0, x_researched=0, research_status="SELECTION_COMPLETE")
    summary = {"revision": SELECTION_REVISION, "observed_at": now, "source_pipeline": pipeline,
        "inspected_count": len(inspected), "profile_fetch_attempts": attempts,
        "limits": {"inspected": MAX_INSPECTED, "profile_attempts": MAX_PROFILE_ATTEMPTS, "cards": MAX_CARDS},
        "selected_symbols": [r["attention"]["symbol"] for r in selected],
        "selected": [r["selection"] for r in selected],
        "excluded": [r for r in inspected if r["status"] != "SINGLE_STOCK"],
        "unclassified_discoveries": [r for r in inspected if r["status"] == "UNKNOWN"],
        "remaining_slots": MAX_CARDS-len(selected),
        "uninspected_count": max(0, len({r.get("symbol") for r in ranked})-len(inspected)),
        "detail": "Auswahl fuer Aktienrecherche; keine Handelsfreigabe. Unbekannter Typ bleibt unbekannt."}
    research.cache_put("candidate_selection", summary, 3600, now=now)
    return selected
