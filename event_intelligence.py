"""Strukturierte Event-Intelligence fuer News, Earnings und Kursreaktion.

Kein LLM und keine 'magische KI': Die Entscheidung ist absichtlich
nachvollziehbar. Meldungen werden nach Ereignistyp, Richtung und Staerke
klassifiziert und muessen bei Event-Trades von Preis/Volumen bestaetigt werden.
"""
from __future__ import annotations
from dataclasses import dataclass,field
import math,re
import config

POSITIVE={
 "raises guidance":18,"raised guidance":18,"guidance raised":18,"beats estimates":12,"beat estimates":12,
 "record revenue":10,"record sales":9,"strong demand":7,"contract":6,"award":6,"partnership":4,
 "fda approval":16,"approved":5,"buyback":6,"upgrade":5,"profit rises":6,"revenue growth":5,
 "above expectations":8,"tops estimates":10,"surprise":3,
 # Deutschsprachige Finanznews (z. B. Google News oder Yahoo Finance)
 "prognose angehoben":18,"prognose erhöht":18,"prognose erhoeht":18,
 "übertrifft erwartungen":12,"uebertrifft erwartungen":12,"über den erwartungen":8,
 "rekordumsatz":10,"umsatzrekord":10,"starke nachfrage":7,"großauftrag":8,"grossauftrag":8,
 "auftrag erhalten":6,"partnerschaft":4,"zulassung erhalten":14,"aktienrückkauf":6,"aktienrueckkauf":6,
 "hochgestuft":6,"hochstufung":6,"kursziel angehoben":5,"gewinn steigt":6,"umsatz wächst":5,"umsatz waechst":5,
}
NEGATIVE={
 "cuts guidance":-20,"lowers guidance":-20,"misses estimates":-14,"missed estimates":-14,"profit warning":-20,
 "bankruptcy":-35,"fraud":-30,"investigation":-15,"downgrade":-7,"recall":-10,"data breach":-10,
 "layoffs":-5,"default":-25,"delisting":-30,"trading halt":-25,"below expectations":-10,
 "prognose gesenkt":-20,"gewinnwarnung":-20,"unter den erwartungen":-12,
 "verfehlt erwartungen":-14,"herabgestuft":-7,"herabstufung":-7,"kursziel gesenkt":-6,
 "insolvenz":-35,"betrug":-30,"ermittlung":-15,"ermittlungen":-15,"handelsaussetzung":-25,
}
EARNINGS_WORDS=("earnings","quarter","quarterly","eps","revenue","guidance","results","fiscal",
                "quartal","quartalszahlen","umsatz","gewinn","prognose","geschäftsjahr","geschaeftsjahr","ergebnis")
CRISIS={"war":8,"invasion":12,"missile":8,"airstrike":8,"sanctions":5,"nuclear":15,"terror":8,
        "military escalation":10,"state of emergency":8,"bank failure":10,"sovereign default":12,
        "krieg":8,"raketenangriff":8,"luftangriff":8,"sanktionen":5,"nuklear":15,
        "militärische eskalation":10,"militaerische eskalation":10,"ausnahmezustand":8,
        "bankenkrise":10,"staatspleite":12}
SECTOR_IMPACT={
 "ruestung":1.0,"rüstung":1.0,"energie":0.45,"luftfahrt":-0.65,"transport":-0.35,
 "banken":-0.35,"halbleiter":-0.15,"software":-0.10,"pharma":0.05,"versorger":0.15,
}

@dataclass
class EventAssessment:
    symbol:str; score:int=0; event_type:str="NONE"; positive:int=0; negative:int=0; crisis:int=0
    earnings_score:int=0; price_confirmation:bool=False; volume_ratio:float=0.0; last_return_pct:float=0.0
    buy_blocked:bool=False; event_buy:bool=False; exit_recommended:bool=False; reasons:list=field(default_factory=list)
    source_count:int=0

    def summary(self):
        return (f"{self.symbol}: Event={self.event_type} Score={self.score}/100 | "
                f"Kurs {self.last_return_pct:+.2f}% | Vol {self.volume_ratio:.2f}x | "
                f"{'EVENT-BUY' if self.event_buy else 'BLOCK' if self.buy_blocked else 'WATCH'} | "
                + "; ".join(self.reasons[:3]))


def _text(news_lage):
    return " ".join(getattr(news_lage,"schlagzeilen",[]) or []).lower()

# Shared topics are conservative risk hints, not verified identities of events.
# Technical stops and official exchange-halt checks are outside this path.
RISK_CATEGORIES = {
    "MILITARY_CONFLICT": ("war", "invasion", "missile", "airstrike", "terror", "military escalation", "krieg", "raketenangriff", "luftangriff", "militaerische eskalation", "militärische eskalation"),
    "NUCLEAR_ESCALATION": ("nuclear", "nuklear"),
    "SANCTIONS": ("sanctions", "sanktionen"),
    "STATE_EMERGENCY": ("state of emergency", "ausnahmezustand"),
    "BANKING_FAILURE": ("bank failure", "bank run", "bankenkrise"),
    "SOVEREIGN_DEFAULT": ("sovereign default", "staatspleite"),
    "CORPORATE_INSOLVENCY": ("bankruptcy", "chapter 11", "insolvency", "insolvenz", "going concern"),
    "FRAUD_INVESTIGATION": ("fraud", "betrug", "accounting scandal", "sec investigation", "sec probe", "criminal charges", "bilanzskandal"),
    "EARNINGS_WARNING": ("profit warning", "guidance cut", "cuts guidance", "lowers guidance", "gewinnwarnung", "prognose gesenkt"),
    "CYBER_INCIDENT": ("data breach", "cyberattack", "ransomware", "datenleck"),
    "TRADING_SUSPENSION": ("trading halt", "trading halted", "delisting", "handelsaussetzung"),
}


def _crisis_words_score(text):
    return min(30, sum(w for k, w in CRISIS.items()
                       if re.search(r"\b"+re.escape(k)+r"\b", text.lower())))


def news_risk_evidence(news_lage, *, now=None):
    """Fresh separate publication origins may support a precautionary buy pause.

    Provider names, copied articles, and social reposts cannot supply the second
    origin. Matching a crisis category is not a proof of one common event; this
    result must never be presented as a verified event or sell instruction.
    No new HTTP request is made here.
    """
    from datetime import datetime, timezone, timedelta
    from news_sources import NewsItem, MultiSourceNews, canonical_news_url, social_news_lineage, publication_origin, _safe_dt
    now = datetime.now(timezone.utc) if now is None else now
    if isinstance(now, (int, float)):
        now = datetime.fromtimestamp(now, timezone.utc)
    items, excluded = [], []
    for row in (getattr(news_lage, "quellen_meldungen", None) or []):
        if not isinstance(row, dict):
            continue
        stamp = _safe_dt(row.get("published_at"))
        metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        url = canonical_news_url(row.get("url"))
        reason = ("SOCIAL_LINEAGE" if social_news_lineage(row) else
                  "MISSING_ARTICLE_ORIGIN" if not url else
                  "STALE_OR_UNDATED" if not stamp or not now-timedelta(hours=24) <= stamp <= now else "")
        if reason:
            excluded.append(reason)
            continue
        items.append(NewsItem(str(row.get("source") or ""), str(row.get("text") or ""),
                              url=url, published_at=stamp, metadata=dict(metadata)))
    records, categories = [], {}
    for item in MultiSourceNews._dedupe(items):
        origin = publication_origin(item.as_dict())
        if not origin:
            excluded.append("UNUSABLE_PUBLICATION_ORIGIN")
            continue
        text = item.text().lower()
        observed = [name for name, words in RISK_CATEGORIES.items()
                    if any(re.search(r"\b"+re.escape(word)+r"\b", text) for word in words)]
        record = {"origin": origin, "url": item.url, "text": item.text(), "categories": observed}
        records.append(record)
        for category in observed:
            categories.setdefault(category, set()).add(origin)
    shared = {category: origins for category, origins in categories.items() if len(origins) >= 2}
    contributing = [r for r in records if any(c in shared for c in r["categories"])]
    raw = _crisis_words_score(_text(news_lage))
    supported_text = " ".join(r["text"] for r in contributing).lower()
    supported_words = {word for category in shared for word in RISK_CATEGORIES[category]}
    actionable = min(30, sum(weight for word, weight in CRISIS.items()
        if word in supported_words and re.search(r"\b"+re.escape(word)+r"\b", supported_text)))
    return {"status": "MULTISOURCE_RISK_HINT" if shared else "UNCONFIRMED_RISK_HINT" if raw or categories else "NO_RISK_HINT",
            "observed_at": now.timestamp(), "assessed_at": now.isoformat(),
            "raw_crisis_score": raw, "actionable_crisis_score": actionable,
            "buy_pause_supported": bool(shared), "verified_event": False,
            "origin_count": len({r["origin"] for r in contributing}),
            "categories": [{"category": key, "origins": sorted(value), "multiple_origins": len(value) >= 2}
                           for key, value in sorted(categories.items())],
            "excluded_reasons": dict(__import__("collections").Counter(excluded)),
            "risk_texts": [r["text"] for r in contributing],
            "detail": "Mehrquellen-Risikohinweis fuer eine vorsorgliche Kaufpause; gleiche Themen beweisen kein gemeinsames Ereignis. "
                      "X, Reposts, identische Artikel und reine Anbieternamen bestaetigen keine zweite Quelle."}


def global_crisis_score(news_lage):
    # Keep the public integer contract; expose the unconfirmed/raw score via
    # news_risk_evidence and Nachrichtenlage.risiko_belege instead of discarding it.
    return news_risk_evidence(news_lage)["actionable_crisis_score"]


def price_reaction(df, mode="NORMAL"):
    if df is None or len(df)<21:return False,0.0,0.0,"zu wenig Kursdaten"
    try:
        close=df["close"].astype(float); volume=df["volume"].astype(float)
        ret=(close.iloc[-1]/close.iloc[-2]-1)*100
        avg=volume.iloc[-21:-1].mean(); vr=volume.iloc[-1]/avg if avg and avg>0 else 1.0
        max_gap=float(getattr(config,"EVENT_MAX_CHASE_MOVE_PCT",0.12))*100
        min_ret=float(getattr(config,"EVENT_MIN_LAST_BAR_RETURN_PCT",0.3))
        min_vol=float(getattr(config,"EVENT_MIN_VOLUME_RATIO",1.5))
        mode=str(mode or "NORMAL").upper()
        extra=True
        if mode=="AGGRESSIVE":
            # frueher, aber nur bei starkem Eventscore; Score-/Kostenfilter bleiben erhalten
            min_ret*=0.65; min_vol*=0.85
        elif mode=="DEFENSIVE":
            min_ret*=1.25; min_vol*=1.25
            prev_ret=(close.iloc[-2]/close.iloc[-3]-1)*100 if len(close)>=3 else 0.0
            extra=prev_ret>-0.10 and (close.iloc[-1]/close.iloc[-3]-1)*100 >= min_ret*1.25
        ok=(ret>=min_ret and vr>=min_vol and ret<=max_gap and extra)
        why=(f"bestaetigt ({mode})" if ok else
             f"keine ausreichende Preis/Volumen-Bestaetigung ({mode}: Ret>={min_ret:.2f}%, Vol>={min_vol:.2f}x)")
        return ok,float(vr),float(ret),why
    except Exception as exc:return False,0.0,0.0,str(exc)


def assess(symbol,sector,news_lage=None,earnings=None,df=None,underdog=False,market_crisis_score=0):
    text=_text(news_lage); pos=neg=cr=0; reasons=[]
    for k,w in POSITIVE.items():
        if k in text: pos+=w; reasons.append(f"+{k}")
    for k,w in NEGATIVE.items():
        if k in text: neg+=abs(w); reasons.append(f"-{k}")
    for k,w in CRISIS.items():
        if re.search(r"\b"+re.escape(k)+r"\b",text):cr+=w
    earnings_related=any(w in text for w in EARNINGS_WORDS)
    es=int(getattr(earnings,"score",0) or 0) if earnings and getattr(earnings,"recent",False) else 0
    source_meta = hasattr(news_lage, "quellen") if news_lage is not None else False
    source_count = len(set(getattr(news_lage, "quellen", []) or [])) if source_meta else 0
    # Fuer einen POSITIVEN Event-Kauf zaehlen nur Quellen, deren eigene
    # deduplizierte Meldung auch tatsaechlich ein positives Event-Signal
    # enthaelt. Eine neutrale SEC-Einreichung darf z.B. nicht einfach als
    # zweiter bullish Beleg neben einem positiven Newsartikel gelten.
    source_items = getattr(news_lage, "quellen_meldungen", None) if news_lage is not None else None
    if source_items is not None:
        positive_sources = set()
        for row in source_items or []:
            row_text = str((row or {}).get("text", "")).lower()
            if any(k in row_text for k in POSITIVE):
                positive_sources.add(str((row or {}).get("source", "")))
        positive_source_count = len({x for x in positive_sources if x})
    else:
        # Rueckwaertskompatibel fuer alte Tests/Objekte ohne Quellen-Metadaten.
        positive_source_count = source_count
    earnings_positive = es >= 55
    evidence_count = positive_source_count + (1 if earnings_positive else 0)
    if es:
        pos += max(0,es-50)//2
        if es<45:neg += 45-es
        reasons.append(f"Earnings {es}/100")
        er=str(getattr(earnings,"reason","") or "").strip()
        if er: reasons.append(er[:180])
        earnings_related=True
    market_crisis_score=int(market_crisis_score or 0)
    sector_adj=int(round(market_crisis_score*SECTOR_IMPACT.get((sector or "").lower(),0)))
    # Globale Krisen belasten den Gesamtmarkt, koennen einzelne Branchen
    # (z.B. Ruestung) aber relativ beguenstigen. Theorie allein reicht nie:
    # fuer einen Event-Buy ist zusaetzlich Preis/Volumen-Bestaetigung noetig.
    diversity_bonus = 0
    if source_meta and pos > 0 and positive_source_count >= 2:
        diversity_bonus = min(
            int(getattr(config, "NEWS_DIVERSITY_BONUS_CAP", 6)),
            max(0, positive_source_count - 1) * int(getattr(config, "NEWS_DIVERSITY_BONUS_PER_SOURCE", 2)),
        )
        if diversity_bonus:
            reasons.append(f"{positive_source_count} bestaetigende Newsquellen")
    raw=50+pos-neg-cr-int(round(market_crisis_score*0.60))+sector_adj+diversity_bonus
    score=max(0,min(100,int(raw)))
    reaction_mode=getattr(config,"EARNINGS_ENTRY_MODE","NORMAL") if earnings_related else "NORMAL"
    confirmed,vr,ret,confirm_reason=price_reaction(df,reaction_mode)
    event_type="EARNINGS" if earnings_related else "CRISIS" if cr or market_crisis_score>=8 else "NEWS" if text else "NONE"
    hard_negative=neg>=int(getattr(config,"EVENT_NEGATIVE_BLOCK_SCORE",12))
    block=hard_negative or score<int(getattr(config,"EVENT_MIN_ACCEPTABLE_SCORE",35))
    if underdog and getattr(config,"UNDERDOG_REQUIRE_POSITIVE_EVENT",True):
        if score<int(getattr(config,"UNDERDOG_MIN_EVENT_SCORE",62)):
            block=True; reasons.append("Underdog ohne positive Event-Lage")
        if source_meta and evidence_count < int(getattr(config, "UNDERDOG_MIN_DIVERSE_SOURCES_FOR_EVENT", 2)):
            block=True; reasons.append("Underdog: zu wenig unabhaengige Event-Belege")
    source_ok = (not source_meta) or evidence_count >= int(getattr(config, "NEWS_MIN_DIVERSE_SOURCES_FOR_EVENT", 2))
    if source_meta and not source_ok and pos > 0:
        reasons.append(f"nur {evidence_count} unabhaengige Event-Belege")
    event_buy=(getattr(config,"EVENT_DRIVEN_BUY_ENABLED",True) and event_type in ("EARNINGS","NEWS") and
               score>=int(getattr(config,"EVENT_BUY_SCORE",78)) and confirmed and not block and source_ok)
    exit_rec=score<=int(getattr(config,"EVENT_EXIT_SCORE",20)) or neg>=25
    if confirmed:reasons.append(confirm_reason)
    return EventAssessment(
        symbol=symbol, score=score, event_type=event_type, positive=pos, negative=neg,
        crisis=cr, earnings_score=es, price_confirmation=confirmed, volume_ratio=vr,
        last_return_pct=ret, buy_blocked=block, event_buy=event_buy,
        exit_recommended=exit_rec, reasons=reasons, source_count=source_count,
    )
