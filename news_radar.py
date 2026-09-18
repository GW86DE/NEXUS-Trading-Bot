"""Multi-Source-News-Radar fuer priorisierte Scanner-Reihenfolge.

Der Radar handelt nie selbst. Er zieht nur Werte mit frischen wichtigen
Meldungen im Scanner nach vorne. Danach muessen weiterhin Kurs/Volumen,
Earnings, Spread, Kosten, Portfolio- und Risiko-Filter bestehen.
"""
from __future__ import annotations
from dataclasses import dataclass
import time
import config
from event_intelligence import POSITIVE, NEGATIVE, CRISIS, EARNINGS_WORDS
from news_sources import MultiSourceNews, NewsItem, publication_origin, _safe_dt


@dataclass
class RadarHit:
    symbol: str
    score: int
    headline: str
    kind: str
    sources: tuple = ()
    publication_origins: tuple = ()


def _article_text(a):
    return (str(a.get("headline", "")) + " " + str(a.get("summary", ""))).lower()


def priority_from_articles(articles, universe_symbols):
    """Priorisiert wichtige Meldungen und honoriert echte Quellenvielfalt.

    Bestehende Unit-Tests koennen weiterhin einfache Dicts ohne source/providers
    uebergeben.
    """
    universe = {str(s).upper() for s in universe_symbols}
    grouped = {}
    # Transport providers can repeat one syndicated article. Keep their labels
    # for diagnostics, but award diversity only for distinct publication sites.
    items = [NewsItem(str(a.get("source") or ""), str(a.get("headline") or ""),
        str(a.get("summary") or ""), str(a.get("url") or ""), _safe_dt(a.get("published_at")),
        list(a.get("symbols") or []), providers=list(a.get("providers") or []),
        metadata=dict(a.get("metadata") or {})) for a in articles or [] if isinstance(a, dict)]
    for item in MultiSourceNews._dedupe(items):
        a = item.as_dict()
        text = _article_text(a)
        score = 0; kinds = []
        for k, w in POSITIVE.items():
            if k in text: score += abs(int(w)); kinds.append("POS")
        for k, w in NEGATIVE.items():
            if k in text: score += abs(int(w))*2; kinds.append("NEG")
        for k, w in CRISIS.items():
            if k in text: score += abs(int(w)); kinds.append("CRISIS")
        if any(w in text for w in EARNINGS_WORDS):
            score += 8; kinds.append("EARNINGS")
        if score < int(getattr(config, "NEWS_RADAR_MIN_SCORE", 8)):
            continue

        providers = a.get("providers") or ([a.get("source")] if a.get("source") else [])
        providers = {str(x) for x in providers if x}
        origin = publication_origin(a)
        for sym in a.get("symbols", []) or []:
            sym = str(sym).replace("/", "").upper()
            if sym not in universe:
                continue
            row = grouped.setdefault(sym, {
                "max_score": 0, "headline": "", "kinds": set(), "sources": set(), "origins": set()
            })
            if score > row["max_score"]:
                row["max_score"] = score
                row["headline"] = str(a.get("headline", ""))[:180]
            row["kinds"].update(kinds)
            row["sources"].update(providers)
            if origin:
                row["origins"].add(origin)

    hits = []
    for sym, row in grouped.items():
        diversity = max(0, len(row["origins"])-1) * int(getattr(config, "NEWS_DIVERSITY_BONUS_PER_SOURCE", 2))
        diversity = min(diversity, int(getattr(config, "NEWS_DIVERSITY_BONUS_CAP", 6)))
        hits.append(RadarHit(
            sym, int(row["max_score"] + diversity), row["headline"],
            "/".join(sorted(row["kinds"])) or "NEWS", tuple(sorted(row["sources"])), tuple(sorted(row["origins"])),
        ))
    return sorted(hits, key=lambda x: x.score, reverse=True)


class NewsRadar:
    def __init__(self, symbols):
        self.symbols = [str(s).upper() for s in symbols]
        self.last_poll = 0.0
        self.last_hits = []
        self.client = MultiSourceNews()

    def enabled(self):
        return bool(getattr(config, "NEWS_RADAR_ENABLED", True)
                    and getattr(config, "NEWS_MULTI_SOURCE_ENABLED", True))

    def poll(self, force=False):
        if not self.enabled():
            return []
        interval = float(getattr(config, "NEWS_RADAR_POLL_SECONDS", 240))
        if not force and time.time() - self.last_poll < interval:
            return self.last_hits
        lookback = int(getattr(config, "NEWS_RADAR_LOOKBACK_MINUTES", 20))
        try:
            bundle = self.client.fetch_market(hours=max(1, (lookback+59)//60), universe=self.symbols)
            self.last_hits = priority_from_articles(bundle.as_dicts(), self.symbols)
            self.last_poll = time.time()
            return self.last_hits
        except Exception:
            self.last_poll = time.time()
            return self.last_hits
