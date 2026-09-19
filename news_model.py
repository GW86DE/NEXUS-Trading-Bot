"""Nachrichtenmodell (10.8.0, Schritt 2): NewsItem und die beiden Textnormalisierer.

Liegt unterhalb von news_sources und nasdaq_halt_feed. Bis 10.7.1 importierte
nasdaq_halt_feed diese drei Namen aus news_sources, waehrend news_sources fuer
die Handelsstopp-Belege nasdaq_halt_feed importierte: ein Import-Zyklus.
Verhalten unveraendert; news_sources re-exportiert die Namen.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import html
import re


def _safe_dt(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except Exception:
            return None
    s = str(value).strip()
    if not s:
        return None
    try:
        if "," in s and ("GMT" in s.upper() or "+" in s or "-" in s[8:]):
            dt = parsedate_to_datetime(s)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        __import__("logging").getLogger(__name__).debug("Best-effort-Ausnahme unterdrueckt", exc_info=True)
    # Alpha Vantage: 20260817T145900
    for fmt in ("%Y%m%dT%H%M%S", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ",
                "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(s, fmt)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except Exception:
            __import__("logging").getLogger(__name__).debug("Best-effort-Ausnahme unterdrueckt", exc_info=True)
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _clean_text(text):
    s = html.unescape(str(text or ""))
    s = re.sub(r"<[^>]+>", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


@dataclass
class NewsItem:
    source: str
    headline: str
    summary: str = ""
    url: str = ""
    published_at: datetime | None = None
    symbols: list[str] = field(default_factory=list)
    provider_weight: float = 1.0
    providers: list[str] = field(default_factory=list)
    official: bool = False
    metadata: dict = field(default_factory=dict)

    def __post_init__(self):
        self.headline = _clean_text(self.headline)
        self.summary = _clean_text(self.summary)
        self.symbols = sorted({str(x).upper().replace("/", "") for x in (self.symbols or []) if str(x).strip()})
        if not self.providers:
            self.providers = [self.source]

    def text(self):
        return f"{self.headline} {self.summary}".strip()

    def as_dict(self):
        return {
            "source": self.source,
            "providers": list(self.providers),
            "headline": self.headline,
            "summary": self.summary,
            "url": self.url,
            "published_at": self.published_at.isoformat() if self.published_at else "",
            "symbols": list(self.symbols),
            "official": bool(self.official),
            "metadata": dict(self.metadata),
        }
