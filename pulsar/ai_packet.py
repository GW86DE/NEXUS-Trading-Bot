"""Bounded views of evidence. The original receipts remain in research storage.

Never cut a serialized JSON document, drop a source identity, or silently call
an excerpt a complete receipt. Limits are UTF-8 bytes, including metadata.
"""
from __future__ import annotations

import copy
import json
from datetime import datetime
from .control import Blocked, digest

REVISION = "evidence-view-3"
BAR_FIELDS = ("date", "open", "high", "low", "close", "volume")
NEWS_FIELDS = ("title", "text", "publishedDate", "url")
FINANCIAL_QUALITY_FIELDS = ("schema_version", "symbol", "symbol_identity_verified",
    "available", "currency", "reason", "errors", "trend_period")
PRIORITY = ("symbol", "cik", "symbol_identity_verified", "available", "currency",
    "date", "published_at", "period", "start", "end", "fiscalYear", "unit", "value",
    "revenue", "net_income", "operating_cashflow", "eps_diluted", "latest", "trends",
    "metrics", "mentions", "mentions_24h_ago", "growth_ratio", "source_family",
    "granularity", "unique_authors", "communities", "discovery", "argument_clusters", "candidate_research", "reason")


def size(value):
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":"),
                          allow_nan=False).encode("utf-8"))


def _excerpt(value, text, items, depth):
    if isinstance(value, str):
        raw = value.encode("utf-8")
        return value if len(raw) <= text else raw[:max(0, text-3)].decode("utf-8", "ignore") + "…"
    if isinstance(value, (dict, list)) and depth <= 0:
        return {"details_omitted": True}
    if isinstance(value, dict):
        keys = sorted(value, key=lambda k: (PRIORITY.index(k) if k in PRIORITY else len(PRIORITY), str(k)))
        return {k: _excerpt(value[k], text, items, depth-1) for k in keys[:items]}
    if isinstance(value, list):
        return [_excerpt(v, text, items, depth-1) for v in value[:items]]
    return value


def _source_data(source):
    value = copy.deepcopy(source.get("data"))
    if source.get("kind") == "social_research_hint" and isinstance(value, dict):
        # X text stays in its short-lived local store. Give the existing Luna
        # review only derived categories and measured counts, explicitly unproven.
        discovery = value.get("x_discovery") or value.get("discovery") or {}
        measured = value.get("attention") or {}
        if value.get("measurement_kind") == "COMPLETE_DAILY_X_COUNTS":
            measured = {"day": value.get("measurement_day"), "count": value.get("mentions"),
                "query_hash": value.get("measurement_query_hash"), "coverage_status": "VOLLSTAENDIG"}
        candidate_context = value.get('candidate_research') or {}
        value = {"source_family": "X", "role": "UNCONFIRMED_RESEARCH_ONLY",
            "symbol": value.get("symbol"), "trade_effect": False, "primary_source_confirmed": False,
            "attention": {k: measured.get(k) for k in ("day", "count", "query_hash", "coverage_status",
                "baseline_ready", "complete_days", "long_ratio", "normalized_ratio")},
            "discovery": {k: discovery.get(k) for k in ("sampled_post_count", "distinct_accounts_in_sample",
                "topics", "attention_kind", "status", "calibrated_spike")},
            "argument_clusters": [{k: event.get(k) for k in ("event_type", "title", "status", "posts",
                "distinct_accounts_in_sample", "duplicate_texts", "near_duplicate_posts", "linked_domains", "evidence_hashes")}
                for event in (value.get("argument_clusters") or [])[:3]],
            "instruction": "Unbestätigte Social-Hinweise, kein Primärbeleg; unabhängig prüfen."}
        if candidate_context:
            value['candidate_research'] = {k: candidate_context.get(k) for k in
                ('state', 'sentiment', 'usable_posts', 'query_hash', 'processed_at', 'categories', 'assessment', 'method')}
    if source.get("kind") == "bars" and isinstance(value, list):
        # Daily receipts are normally ascending. Generic prefix truncation used
        # to preserve the oldest row and discard the most recent three sessions.
        # Keep complete available OHLCV fields together; missing is never zero.
        rows = [r for r in value if isinstance(r, dict) and _dated(r.get("date"))]
        rows.sort(key=lambda r: r["date"], reverse=True)
        value = [{k: r[k] for k in BAR_FIELDS if k in r} for r in rows]
    elif source.get("kind") == "news" and isinstance(value, list):
        value = [{k: r[k] for k in NEWS_FIELDS if k in r}
                 for r in value if isinstance(r, dict)]
    return value


def _dated(value):
    if not isinstance(value, str):
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        return True
    except ValueError:
        return False


def _whole_bars(rows, allowance):
    """Only remove entire old rows, never fields of a selected candle."""
    selected = []
    for row in rows:
        if size(selected + [row]) > allowance:
            break
        selected.append(row)
    return selected


def _news_excerpt(rows, allowance):
    """A URL and publication timestamp remain intact, even in a short view."""
    if not rows:
        return []
    for limit in (600, 240, 120, 60, 24):
        selected = []
        for row in rows:
            candidate = {k: _excerpt(v, limit, 24, 6) if k in {"title", "text"}
                         else copy.deepcopy(v) for k, v in row.items()}
            if size(selected + [candidate]) > allowance:
                break
            selected.append(candidate)
        if selected:
            return selected
    return {"details_omitted": True}


def _financial_quality(data):
    return {k: copy.deepcopy(data[k]) for k in FINANCIAL_QUALITY_FIELDS if k in data}


def _financial_excerpt(data, allowance):
    # A shortened growth table must not shed its period gap or validation
    # failures. Reserve the complete known quality/period metadata first.
    quality = _financial_quality(data)
    candidate = data
    for text, items, depth in ((500, 24, 6), (240, 16, 5), (120, 10, 4), (80, 6, 3), (40, 3, 2)):
        if size(candidate) <= allowance:
            return candidate
        candidate = {**_excerpt(data, text, items, depth), **quality}
    return candidate if size(candidate) <= allowance else quality


def _social_quality(data):
    quality = {k: data[k] for k in ("source_family", "role", "trade_effect", "primary_source_confirmed") if k in data}
    if data.get('candidate_research'):
        quality['candidate_research'] = {k: data['candidate_research'].get(k) for k in
            ('state', 'sentiment', 'usable_posts', 'query_hash', 'processed_at')}
    return quality


def _social_excerpt(data, allowance):
    quality = _social_quality(data)
    candidate = data
    for text, items, depth in ((200, 12, 5), (100, 8, 4), (50, 4, 3), (20, 2, 2)):
        if size(candidate) <= allowance:
            return candidate
        candidate = {**_excerpt(data, text, items, depth), **quality}
    return candidate if size(candidate) <= allowance else quality


def project(packet, *, budget=4300):
    sources = packet.get("sources") or []
    ids = [s.get("id") for s in sources]
    if any(not isinstance(i, str) or not i for i in ids) or len(ids) != len(set(ids)):
        raise Blocked("Quellenidentitaeten fehlen oder sind doppelt")
    view = {"symbol": packet["symbol"], "view_revision": REVISION,
            "full_packet_sha256": digest(packet), "sources": []}
    prepared = [_source_data(source) for source in sources]
    for source, data in zip(sources, prepared):
        target = {"id": source["id"], "provider": str(source.get("provider") or "")[:80],
            "kind": str(source.get("kind") or "")[:80],
            "full_data_sha256": digest(source.get("data")), "view_truncated": True, "data": None}
        if source.get("kind") == "bars" and isinstance(data, list):
            target.update(data_order="newest_first", available_rows=len(data), included_rows=0,
                          undated_rows=len(source["data"])-len(data),
                          latest_available_date=data[0]["date"] if data else None,
                          as_of=data[0]["date"] if data else None)
        view["sources"].append(target)
    remaining = budget - size(view) - 100
    if remaining < 64 * len(sources):
        raise Blocked("Zu viele Quellenidentitaeten fuer eine sichere KI-Vorpruefung")
    weights = [3 if s.get("kind") in {"companyfacts", "annual_financials", "primary_document"}
               else 2 if s.get("kind") in {"news", "community_sample"} else 1 for s in sources]
    total_weight = sum(weights) or 1
    # Reserve at least the newest complete available candle before sharing the
    # rest. If it cannot fit, do not submit a view that hides current market data.
    minima = []
    for source, data in zip(sources, prepared):
        minimum = 64
        if source.get("kind") == "bars" and isinstance(data, list) and data:
            minimum = max(minimum, size([data[0]]))
        elif source.get("kind") == "annual_financials" and isinstance(data, dict):
            minimum = max(minimum, size(_financial_quality(data)))
        elif source.get("kind") == "social_research_hint" and isinstance(data, dict):
            minimum = max(minimum, size(_social_quality(data)))
        minima.append(minimum)
    distributable = remaining - sum(minima)
    if distributable < 0:
        raise Blocked("Neueste Kurskerze oder Finanz-Qualitaetsbelege passen nicht vollstaendig in das KI-Eingabelimit")
    for source, target, weight, data, minimum in zip(sources, view["sources"], weights, prepared, minima):
        allowance = minimum + distributable * weight // total_weight
        if source.get("kind") == "bars" and isinstance(data, list):
            candidate = _whole_bars(data, allowance)
            target["included_rows"] = len(candidate)
            target["as_of"] = candidate[0]["date"] if candidate else None
        elif source.get("kind") == "news" and isinstance(data, list):
            candidate = _news_excerpt(data, allowance)
        elif source.get("kind") == "annual_financials" and isinstance(data, dict):
            candidate = _financial_excerpt(data, allowance)
        elif source.get("kind") == "social_research_hint" and isinstance(data, dict):
            candidate = _social_excerpt(data, allowance)
        else:
            candidate = data
            for text, items, depth in ((500, 24, 6), (240, 16, 5), (120, 10, 4), (80, 6, 3), (40, 3, 2)):
                if size(candidate) <= allowance:
                    break
                candidate = _excerpt(data, text, items, depth)
            if size(candidate) > allowance:
                candidate = {"details_omitted": True}
        target["data"] = candidate
        target["view_truncated"] = candidate != source.get("data")
    if size(view) > budget:
        raise Blocked("KI-Ansicht ueberschreitet das reservierte Eingabelimit")
    return view
