"""Dated provider facts and explicit missing capabilities, without order access."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import math
import time


def next_earnings(card, *, now=None):
    now = time.time() if now is None else now
    today = datetime.fromtimestamp(now, ZoneInfo("America/New_York")).date()
    found = []
    for source in card.get("sources", []):
        if source.get("provider") != "FMP" or source.get("kind") != "earnings":
            continue
        try:
            age = now-float(source["observed_at"])
            if not math.isfinite(age) or not 0 <= age <= 6*3600:
                continue
            for row in source.get("data") or []:
                if not isinstance(row, dict) or row.get("symbol") != card["symbol"]:
                    continue
                day = datetime.fromisoformat(str(row.get("date"))).date()
                if today <= day <= today+timedelta(days=90):
                    found.append({"date": day.isoformat(), "source_id": source["id"],
                        "provider": "FMP", "observed_at": source["observed_at"],
                        "days": (day-today).days, "quality": "PROVIDER_CALENDAR"})
        except (TypeError, ValueError, KeyError, OverflowError):
            continue
    return min(found, key=lambda r: r["date"]) if found else None


def earnings_clear(card, fallback_days=None, *, now=None):
    """Require a known date beyond the next two US exchange sessions.

    A date-only calendar gives no guaranteed announcement time. Include the
    whole second session conservatively; disagreement uses the nearer date.
    """
    from market_calendar import naechster_handelstag
    now = time.time() if now is None else now
    today = datetime.fromtimestamp(now, ZoneInfo("America/New_York")).date()
    candidates = []
    receipt = next_earnings(card, now=now)
    if receipt:
        candidates.append(datetime.fromisoformat(receipt["date"]).date())
    if (isinstance(fallback_days, (int, float)) and not isinstance(fallback_days, bool)
            and math.isfinite(fallback_days) and 0 <= fallback_days <= 90
            and float(fallback_days).is_integer()):
        candidates.append(today+timedelta(days=int(fallback_days)))
    cutoff = naechster_handelstag(naechster_handelstag(today))
    return bool(candidates and min(candidates) > cutoff)


def capabilities():
    return {"attention": True, "individual_community_evidence": False,
        "detail": "PULSAR 2.0 (Hype-Spur): ApeWisdom liefert Aufmerksamkeit, Tradestie optional Stimmung, "
                  "X begrenzte Stichproben. Ein Hype-Kandidat braucht gleichzeitig einen belegten "
                  "Social-Spike, eine Kurs-/Volumenbestaetigung aus den Kursdaten und die unveraenderten "
                  "Identitaets-/Qualitaetsgrenzen; die schnelle Luna-Vorpruefung bleibt als Warnfilter. "
                  "Community-Breite bleibt nicht pruefbar; die Aggregatquellen liefern keine Einzelautoren. "
                  "Eine Nominierung braucht weiterhin zwei zeitlich getrennte Bewertungen und zwei "
                  "persoenliche Telegram-Bestaetigungen; Social-Quellen geben nie eine Order frei. "
                  "Entdeckungen unterhalb der Hype-Schwelle speisen als Gaeste das normale Universum."}
