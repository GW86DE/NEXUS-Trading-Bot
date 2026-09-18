"""Read-only execution projections; no invented requests or source counts."""
from datetime import datetime, timezone


def not_started(reason, code):
    """The caller skipped this stage before invoking the router."""
    return {"ok": False, "grund": reason, "execution": {
        "schema_version": 1, "phase": "NOT_STARTED", "error_code": code,
        "request_dispatched": False, "response_received": False,
        "local_request_id": "", "provider_request_id": "", "cache_used": False,
        "requested_at": datetime.now(timezone.utc).isoformat(), "started_at": None,
        "finished_at": None, "duration_seconds": None}}


def cached_result(data):
    """Keep the old receipt separately; this cache use made no new request."""
    execution = not_started("Zwischenspeicher verwendet", "")["execution"]
    execution.update(phase="CACHE_HIT", cache_used=True)
    return {**data, "cached": True, "source_execution": data.get("execution") or None,
            "execution": execution}


def failed_result(result, reason, *, code="PULSAR_REVIEW_INVALID", discarded=False):
    """Preserve an actual request receipt even when the PULSAR contract fails."""
    data = dict(result.als_dict()) if callable(getattr(result, "als_dict", None)) else {}
    execution = dict(data.get("execution") or {})
    if execution and (discarded or data.get("ok")):
        execution.update(phase="DISCARDED" if discarded else "FAILED", error_code=code)
    # Legacy adapters without execution evidence stay unknown, not 'not sent'.
    return {**data, "ok": False, "daten": {}, "grund": reason, "execution": execution}


def _source_field_presence(data):
    """Only field-presence metadata; never retain article/post bodies."""
    rows = data if isinstance(data, list) else [data]
    groups = {
        "body": ("text", "description", "summary"),
        "url": ("url", "article_url", "link"),
        "published_at": ("publishedDate", "published_utc", "published_at", "date"),
    }
    return {name: any(isinstance(row, dict) and any(row.get(k) for k in keys)
                      for row in rows) for name, keys in groups.items()}


def _source_ids(value):
    found = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "source_ids" and isinstance(item, list):
                found.update(str(v) for v in item if str(v))
            else:
                found.update(_source_ids(item))
    elif isinstance(value, list):
        for item in value:
            found.update(_source_ids(item))
    return found


def _compact_source(source):
    if not isinstance(source, dict):
        return {}
    return {
        "id": source.get("id"),
        "provider": source.get("provider"),
        "kind": source.get("kind"),
        "observed_at": source.get("observed_at"),
        "diagnostic_fields": _source_field_presence(source.get("data")),
    }


def _compact_stage(stage):
    if not isinstance(stage, dict):
        return {}
    data = stage.get("daten") if isinstance(stage.get("daten"), dict) else {}
    thesis = data.get("thesis") if isinstance(data.get("thesis"), dict) else {}
    return {
        "ok": stage.get("ok"),
        "grund": stage.get("grund"),
        "cached": stage.get("cached"),
        "modell": stage.get("modell"),
        "execution": stage.get("execution") if isinstance(stage.get("execution"), dict) else {},
        "source_execution": stage.get("source_execution") if isinstance(stage.get("source_execution"), dict) else {},
        "input_hash": stage.get("input_hash"),
        "input_bytes": stage.get("input_bytes"),
        "review_revision": stage.get("review_revision"),
        "input_sources": stage.get("input_sources") if isinstance(stage.get("input_sources"), dict) else {},
        "daten": {
            "thesis": {"text": thesis.get("text")},
            "source_ids": sorted(_source_ids(data)),
        },
    }


def compact_cards(cards):
    """Bounded PULSAR projection for diagnostics.

    ``top5`` can legitimately grow beyond the diagnosis cell limit because it
    keeps full research packets. This projection contains only provenance,
    execution receipts and field-presence evidence needed by the diagnostic
    report. It deliberately omits news/post bodies, author identifiers and
    other large raw source payloads.
    """
    out = []
    for card in cards or []:
        if not isinstance(card, dict):
            continue
        packet = card.get("packet") if isinstance(card.get("packet"), dict) else {}
        out.append({
            "symbol": card.get("symbol"),
            "assessment_id": card.get("assessment_id") or card.get("id"),
            "state": card.get("state"),
            "score": card.get("score"),
            "eligible": card.get("eligible"),
            "blocks": card.get("blocks"),
            "missing": card.get("missing"),
            "errors": card.get("errors"),
            "analysis_status": card.get("analysis_status"),
            "text_source": card.get("text_source"),
            "thesis": card.get("thesis"),
            "sources": [_compact_source(r) for r in (card.get("sources") or []) if isinstance(r, dict)],
            "packet": {"sources": [_compact_source(r) for r in (packet.get("sources") or []) if isinstance(r, dict)]},
            "precheck": _compact_stage(card.get("precheck")),
            "analysis": _compact_stage(card.get("analysis")),
            "countercheck": _compact_stage(card.get("countercheck")),
            "source_coordination": card.get("source_coordination") if isinstance(card.get("source_coordination"), dict) else {},
            "attention_coverage": card.get("attention_coverage") if isinstance(card.get("attention_coverage"), dict) else {},
        })
    return out
