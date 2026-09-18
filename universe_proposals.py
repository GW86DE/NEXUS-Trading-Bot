"""Dauerhafte Research-Vorschlaege und Human-in-the-loop-Zustandsmaschine."""
from __future__ import annotations

import json
import logging
import os
import threading
import uuid
import ipaddress
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from instrument_identity import normalize_user_symbol
from safe_persistence import atomic_write_json
from approved_universe import add_approved_stock

ROOT = Path(__file__).resolve().parent
logger = logging.getLogger(__name__)
_LOCK = threading.RLock()
VALID_STATES = {
    "PROPOSED", "REVIEW_REQUESTED", "TECHNICALLY_APPROVED",
    "TECHNICAL_REJECTED", "REJECTED", "HUMAN_APPROVED",
}


def _root() -> Path:
    return Path(os.getenv("TRADINGBOT_TEST_STATE_DIR", "").strip() or ROOT)


def path() -> Path:
    return _root() / "universe_proposals.json"


def _empty() -> dict:
    return {"version": 1, "proposals": [], "updated_at_utc": None}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load() -> dict:
    p = path()
    try:
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("proposals", []), list):
                return data
    except Exception as exc:
        logger.warning("Universums-Vorschlagsdatei konnte nicht gelesen werden: %s",exc)
    return _empty()


def _save(data: dict) -> None:
    data["updated_at_utc"] = _now()
    atomic_write_json(path(), data)


def valid_sources(sources) -> list[dict]:
    """Nur konkrete HTTPS-Quellen von mindestens zwei unterschiedlichen Hosts."""
    out=[]; hosts=set(); seen=set()
    for src in sources or []:
        if not isinstance(src, dict):
            continue
        url=str(src.get("url") or "").strip()
        title=str(src.get("title") or src.get("name") or "Quelle").strip()
        try:
            parsed=urlparse(url)
            host=(parsed.hostname or "").lower().removeprefix("www.")
            # Quellen werden absichtlich nicht aus dem Tradingprozess heraus
            # abgerufen. Trotzdem akzeptieren wir nur plausible oeffentliche
            # Web-Hostnamen, keine localhost-/IP-/Intranet-Ziele.
            if host:
                try:
                    ipaddress.ip_address(host)
                    host_is_ip=True
                except ValueError:
                    host_is_ip=False
            else:
                host_is_ip=False
        except Exception:
            continue
        if (parsed.scheme != "https" or not host or url in seen or host_is_ip
                or "." not in host or host == "localhost"
                or host.endswith((".local", ".internal", ".localhost"))):
            continue
        out.append({"title":title[:180],"url":url[:1000]})
        hosts.add(host); seen.add(url)
    return out if len(out) >= 2 and len(hosts) >= 2 else []


def create_proposals(candidates: list[dict], *, batch_id: str = "") -> list[dict]:
    """Speichert nur vollstaendige Vorschlaege; niemals Universumsfreigaben."""
    created=[]
    with _LOCK:
        data=_load(); rows=list(data.get("proposals",[]) or [])
        active_symbols={
            str(x.get("symbol") or "").upper()
            for x in rows if str(x.get("status") or "") not in {"REJECTED","TECHNICAL_REJECTED"}
        }
        for candidate in candidates or []:
            try:
                symbol=normalize_user_symbol(candidate.get("symbol", ""), "stock")
            except Exception:
                continue
            sources=valid_sources(candidate.get("sources"))
            if not sources or symbol in active_symbols:
                continue
            proposal={
                "id": f"U-{datetime.now():%Y%m%d}-{uuid.uuid4().hex[:8].upper()}",
                "batch_id": str(batch_id or ""),
                "symbol": symbol,
                "company": str(candidate.get("company") or "")[:200],
                "sector": str(candidate.get("sector") or "UNCLASSIFIED")[:120],
                "rationale": str(candidate.get("rationale") or "")[:1500],
                "sources": sources,
                "status": "PROPOSED",
                "created_at_utc": _now(),
                "technical_review": None,
                "audit": [{"at":_now(),"action":"PROPOSED","actor":"ai_research"}],
            }
            rows.append(proposal); created.append(dict(proposal)); active_symbols.add(symbol)
        data["proposals"]=rows; _save(data)
    return created


def list_proposals(*, states: set[str] | None = None, limit: int = 100) -> list[dict]:
    with _LOCK:
        rows=list(_load().get("proposals",[]) or [])
    if states:
        rows=[x for x in rows if str(x.get("status") or "") in states]
    rows.sort(key=lambda x:str(x.get("created_at_utc") or ""), reverse=True)
    return [dict(x) for x in rows[:max(1,int(limit))]]


def get(proposal_id: str) -> dict | None:
    pid=str(proposal_id or "")
    with _LOCK:
        for row in _load().get("proposals",[]) or []:
            if str(row.get("id")) == pid:
                return dict(row)
    return None


def _mutate(proposal_id: str, fn):
    pid=str(proposal_id or "")
    with _LOCK:
        data=_load(); rows=data.get("proposals",[]) or []
        for row in rows:
            if str(row.get("id")) != pid:
                continue
            result=fn(row)
            row.setdefault("audit",[])
            data["proposals"]=rows; _save(data)
            return result, dict(row)
    return (False,"Vorschlag nicht gefunden"), None


def request_review(proposal_id: str, *, actor: str = "telegram") -> tuple[bool,str]:
    def fn(row):
        state=str(row.get("status") or "")
        if state == "HUMAN_APPROVED": return (False,"bereits aufgenommen")
        if state == "REJECTED": return (False,"bereits abgelehnt")
        if state == "REVIEW_REQUESTED": return (True,"Pruefung ist bereits angefordert")
        row["status"]="REVIEW_REQUESTED"
        row["review_requested_at_utc"]=_now()
        row.setdefault("audit",[]).append({"at":_now(),"action":"REVIEW_REQUESTED","actor":actor})
        return (True,"technische Pruefung angefordert")
    result,_=_mutate(proposal_id,fn); return result


def pending_reviews(limit: int = 1) -> list[dict]:
    rows=list_proposals(states={"REVIEW_REQUESTED"},limit=1000)
    rows.sort(key=lambda x:str(x.get("review_requested_at_utc") or x.get("created_at_utc") or ""))
    return rows[:max(1,int(limit))]


def set_technical_result(proposal_id: str, review: dict) -> tuple[bool,str]:
    passed=bool((review or {}).get("passed"))
    def fn(row):
        if str(row.get("status")) != "REVIEW_REQUESTED":
            return (False,f"ungueltiger Zustand {row.get('status')}")
        row["technical_review"]=dict(review or {})
        row["technical_reviewed_at_utc"]=_now()
        row["status"]="TECHNICALLY_APPROVED" if passed else "TECHNICAL_REJECTED"
        row.setdefault("audit",[]).append({"at":_now(),"action":row["status"],"actor":"deterministic_review"})
        return (True,row["status"])
    result,_=_mutate(proposal_id,fn); return result


def reject(proposal_id: str, *, actor: str = "telegram") -> tuple[bool,str]:
    def fn(row):
        if str(row.get("status")) == "HUMAN_APPROVED": return (False,"bereits aufgenommen")
        if str(row.get("status")) == "REJECTED": return (True,"bereits abgelehnt")
        row["status"]="REJECTED"; row["rejected_at_utc"]=_now()
        row.setdefault("audit",[]).append({"at":_now(),"action":"REJECTED","actor":actor})
        return (True,"abgelehnt")
    result,_=_mutate(proposal_id,fn); return result


def approve(proposal_id: str, *, actor: str = "telegram") -> tuple[bool,str]:
    """Zweite Human-Freigabe. Nur TECHNICALLY_APPROVED ist zulaessig."""
    pid=str(proposal_id or "")
    with _LOCK:
        data=_load(); rows=data.get("proposals",[]) or []
        row=next((x for x in rows if str(x.get("id"))==pid),None)
        if not row: return False,"Vorschlag nicht gefunden"
        if str(row.get("status")) == "HUMAN_APPROVED": return True,"bereits aufgenommen"
        if str(row.get("status")) != "TECHNICALLY_APPROVED":
            return False,f"keine Aufnahme aus Zustand {row.get('status')}"
        review=dict(row.get("technical_review") or {})
        if not bool(review.get("passed")):
            return False,"technische Pruefung ist nicht bestanden"
        ok,detail=add_approved_stock(
            proposal_id=pid,symbol=row.get("symbol",""),company=row.get("company",""),
            sector=row.get("sector","UNCLASSIFIED"),etoro_symbol_full=review.get("etoro_symbol_full",""),
            technical_review=review,approved_by=actor,
        )
        if not ok: return False,detail
        row["status"]="HUMAN_APPROVED"; row["approved_at_utc"]=_now()
        row.setdefault("audit",[]).append({"at":_now(),"action":"HUMAN_APPROVED","actor":actor})
        data["proposals"]=rows; _save(data)
        return True,detail


def format_details(row: dict) -> str:
    if not row: return "Vorschlag nicht gefunden."
    lines=[
        f"🧠 UNIVERSUM-VORSCHLAG {row.get('id','')}",
        f"Symbol: {row.get('symbol','?')} · {row.get('company','')}",
        f"Branche: {row.get('sector','UNCLASSIFIED')}",
        f"Status: {row.get('status','?')}",
        "",
        str(row.get("rationale") or "Keine Begruendung."),
        "",
        "Quellen:",
    ]
    for src in row.get("sources",[]) or []:
        lines.append(f"• {src.get('title','Quelle')}: {src.get('url','')}")
    review=row.get("technical_review") or {}
    if review:
        lines += ["", "Technische Pruefung:", str(review.get("summary") or review)]
    return "\n".join(lines)[:3800]


def status_text() -> str:
    rows=list_proposals(limit=1000)
    counts={s:0 for s in VALID_STATES}
    for row in rows: counts[str(row.get("status") or "")]=counts.get(str(row.get("status") or ""),0)+1
    return (
        "🧠 UNIVERSUM-VORSCHLAEGE\n"
        f"Neu: {counts.get('PROPOSED',0)} · Pruefung angefordert: {counts.get('REVIEW_REQUESTED',0)}\n"
        f"Technisch bestanden: {counts.get('TECHNICALLY_APPROVED',0)} · technisch abgelehnt: {counts.get('TECHNICAL_REJECTED',0)}\n"
        f"Menschlich aufgenommen: {counts.get('HUMAN_APPROVED',0)} · verworfen: {counts.get('REJECTED',0)}"
    )
