"""Woechentlicher KI-Ideengeber fuer neue Aktien -- ohne Schreibrecht aufs Universum."""
from __future__ import annotations

import logging
import copy
from collections import Counter
from datetime import datetime
import config
from ai_router import AIRouter
from decision_analytics import research_context
from instrument_identity import normalize_user_symbol
from universe_proposals import create_proposals, valid_sources

logger=logging.getLogger(__name__)

SCHEMA={
    "type":"object",
    "properties":{
        "candidates":{"type":"array","minItems":5,"maxItems":10,"items":{
            "type":"object",
            "properties":{
                "symbol":{"type":"string"},"company":{"type":"string"},"sector":{"type":"string"},
                "rationale":{"type":"string"},
                "sources":{"type":"array","minItems":2,"maxItems":4,"items":{
                    "type":"object","properties":{"title":{"type":"string"},"url":{"type":"string"}},
                    "required":["title","url"],"additionalProperties":False}},
            },
            "required":["symbol","company","sector","rationale","sources"],"additionalProperties":False,
        }},
        "portfolio_observation":{"type":"string"},
    },
    "required":["candidates","portfolio_observation"],"additionalProperties":False,
}


def _universe_summary(stock_rows: list[dict]) -> dict:
    sectors=Counter(); symbols=[]
    for row in stock_rows or []:
        symbol=str(row.get("symbol") or "").upper().replace(".US","")
        if not symbol: continue
        symbols.append(symbol); sectors[str(row.get("sector") or "UNCLASSIFIED")]+=1
    return {"count":len(symbols),"symbols":symbols,"sector_distribution":dict(sectors)}


class UniverseResearchAssistant:
    def __init__(self, router: AIRouter | None = None):
        self.router = router or AIRouter(config)
        self.enabled=bool(getattr(config,"AI_RESEARCH_ENABLED",False) and self.router.aktiv)
        self.model=""
        self.last_error=""

    def generate(self, stock_rows: list[dict]) -> list[dict]:
        self.last_error=""
        if not self.enabled: return []
        current=_universe_summary(stock_rows)
        catalog=_universe_summary(list(getattr(config,"STOCK_CATALOG_SYMBOLS",[]) or []))
        reserve=[s for s in catalog["symbols"] if s not in set(current["symbols"])]
        internal=research_context(int(getattr(config,"AI_RESEARCH_LOOKBACK_DAYS",90)))
        try:
            minimum = max(1, int(getattr(config, "AI_RESEARCH_MIN_PROPOSALS", 5)))
            maximum = max(minimum, int(getattr(config, "AI_RESEARCH_MAX_PROPOSALS", 10)))
            schema = copy.deepcopy(SCHEMA)
            schema["properties"]["candidates"].update(minItems=minimum, maxItems=maximum)
            answer = self.router.frage(
                "wochen_research",
                {"current_universe": current, "catalog_reserve": reserve,
                 "bot_history": internal},
                schema,
                anweisung=(
                    f"Erzeuge {minimum} bis {maximum} REINE RESEARCH-VORSCHLAEGE fuer moegliche neue "
                    "US-Aktien. Du hast keinerlei Schreibrecht aufs Universum und gibst "
                    "kein Kauf-/Verkaufssignal. Nur liquide boersengelistete Aktien, die "
                    "nicht in current_universe stehen; bevorzuge catalog_reserve. Verboten: "
                    "SPACs, Warrants, Units, Rights sowie IPOs unter etwa neun Monaten. Jeder "
                    "Vorschlag braucht mindestens zwei konkrete HTTPS-Quellen verschiedener "
                    "Domains. Keine belastbaren Quellen bedeutet: Vorschlag weglassen."
                ),
                kontext={"anlass": "wochen_research"},
                cache_erlaubt=True,
            )
            if not answer.ok:
                raise RuntimeError(answer.grund)
            self.model = answer.modell
            parsed=answer.daten
            existing=set(current["symbols"]); clean=[]; seen=set()
            for c in parsed.get("candidates",[]) or []:
                try: symbol=normalize_user_symbol(c.get("symbol",""),"stock")
                except Exception: continue
                if symbol in existing or symbol in seen: continue
                sources=valid_sources(c.get("sources"))
                if not sources: continue
                # URLs muessen unterschiedliche Hosts haben; valid_sources
                # erzwingt dies. Kein Netzwerk-Fetch aus fremden Webseiten im
                # Tradingprozess -- das reduziert Prompt-Injection-Angriffsflaeche.
                c=dict(c); c["symbol"]=symbol; c["sources"]=sources
                clean.append(c); seen.add(symbol)
            min_candidates=int(getattr(config,"AI_RESEARCH_MIN_PROPOSALS",5))
            if len(clean) < min_candidates:
                raise RuntimeError(f"nur {len(clean)} Vorschlaege mit mindestens zwei pruefbaren Quellen")
            batch=f"{datetime.now():%Y-W%W}"
            return create_proposals(clean[:int(getattr(config,"AI_RESEARCH_MAX_PROPOSALS",10))],batch_id=batch)
        except Exception as exc:
            self.last_error=str(exc); logger.warning("Woechentliches KI-Research fehlgeschlagen: %s",exc); return []


def proposal_message(p: dict) -> str:
    sources=p.get("sources",[]) or []
    lines=[
        "🧠 NEUER UNIVERSUM-VORSCHLAG",
        f"{p.get('symbol','?')} · {p.get('company','')}",
        f"Branche: {p.get('sector','UNCLASSIFIED')}",
        "",
        str(p.get("rationale") or "")[:1000],
        "",
        "Quellen:",
    ]
    for src in sources[:3]: lines.append(f"• {src.get('title','Quelle')}: {src.get('url','')}")
    lines += ["", "Status: NUR VORSCHLAG – nicht handelbar.", "„Pruefen lassen“ fordert nur die deterministische eToro-Aufnahmepruefung an."]
    return "\n".join(lines)[:3800]


def proposal_keyboard(proposal_id: str):
    pid=str(proposal_id)
    return [[
        {"text":"🔍 Prüfen lassen","callback_data":f"univ:review:{pid}"},
        {"text":"❌ Ablehnen","callback_data":f"univ:reject:{pid}"},
    ],[
        {"text":"ℹ️ Details","callback_data":f"univ:details:{pid}"},
    ]]
