"""Deterministisches, persistentes OKX-Spot-Kernuniversum nach 30-Tage-Volumen.

GPT bekommt ausschliesslich den bereits berechneten Diff zur Identitaets-/
Risikokommentierung. Zahlen, Filter, Rang und Aktivierung bleiben lokal.
"""
from __future__ import annotations

import json
import logging
import os
import re
import statistics
from calendar import monthrange
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import config
from safe_persistence import atomic_write_json

STABLE = {"USDT", "USDC", "DAI", "TUSD", "USDP", "FDUSD", "PYUSD", "EURT", "EURC", "USD", "EUR"}
# The underlying symbol must have at least two characters. This prevents a
# normal asset such as JUP from being mistaken for the leveraged suffix UP.
LEVERAGED = re.compile(r"^[A-Z0-9]{2,}(?:3L|3S|5L|5S|UP|DOWN|BULL|BEAR)$")
ZONE = ZoneInfo("Europe/Berlin")
logger = logging.getLogger(__name__)


def _root() -> Path:
    return Path(os.getenv("TRADINGBOT_TEST_STATE_DIR", "").strip() or Path(__file__).resolve().parent)


def state_path() -> Path:
    return _root() / getattr(config, "CORE_VOLUME_20_FILE", "core_volume_20.json")


def history_path() -> Path:
    return _root() / getattr(config, "CORE_VOLUME_20_HISTORY_FILE", "core_volume_20_history.json")


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def load() -> dict:
    try:
        data = json.loads(state_path().read_text(encoding="utf-8")) if state_path().exists() else {}
        if not isinstance(data, dict):
            raise ValueError("not an object")
        data.setdefault("items", []); data.setdefault("status", "EMPTY")
        return data
    except Exception as exc:
        return {"schema": 1, "status": "STALE", "items": [], "error": f"state unreadable: {type(exc).__name__}"}


def _history() -> dict:
    try:
        data = json.loads(history_path().read_text(encoding="utf-8")) if history_path().exists() else {}
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        # Never overwrite unreadable 30-day evidence with a fresh empty file.
        return {"samples": [], "storage_error": f"history unreadable: {type(exc).__name__}"}


def _save_history(data: dict) -> None:
    atomic_write_json(history_path(), data)


def next_due(after: datetime) -> datetime:
    local = after.astimezone(ZONE)
    year, month = local.year, local.month
    if month == 12:
        year, month = year + 1, 1
    else:
        month += 1
    # 03:10 avoids midnight/DST edges and is persisted after success.
    return datetime(year, month, 1, 3, 10, tzinfo=ZONE)


def next_collection_due(after: datetime) -> datetime:
    local = after.astimezone(ZONE) + timedelta(days=1)
    return local.replace(hour=3, minute=10, second=0, microsecond=0)


def _eligible(meta, ticker, *, allowed_quotes: tuple[str, ...],
              pair_prevalidated: bool = False) -> tuple[bool, str]:
    inst_type = str(getattr(meta, "inst_type", getattr(meta, "type", "SPOT")) or "SPOT").upper()
    quote = str(getattr(meta, "quote_ccy", "") or "").upper()
    base = str(getattr(meta, "base_ccy", "") or "").upper()
    state = str(getattr(meta, "state", "") or "").lower()
    if inst_type != "SPOT": return False, "not spot"
    if not pair_prevalidated and quote not in allowed_quotes: return False, "quote not allowed"
    if state != "live": return False, "not live/EEA-tradable"
    if not base or base in STABLE: return False, "stablecoin base"
    if quote in STABLE and base in STABLE: return False, "stable-stable"
    if LEVERAGED.fullmatch(base): return False, "leveraged token"
    if ticker is None: return False, "missing ticker"
    if float(getattr(ticker, "vol_24h_quote", 0) or 0) <= 0: return False, "missing quote volume"
    return True, ""


def rank_snapshot(instruments: dict, tickers: dict, *, allowed_quotes=("EUR", "USD", "USDC"),
                  quote_rates: dict | None = None, measured_at: datetime | None = None,
                  limit: int = 20, eligible_pairs: set[str] | None = None) -> list[dict]:
    """Top bases by normalized quote notional, deterministic and deduplicated."""
    measured_at = measured_at or datetime.now(timezone.utc)
    allowed = tuple(dict.fromkeys(str(x).upper() for x in allowed_quotes))
    rates = {"EUR": 1.0, **{str(k).upper(): float(v) for k, v in (quote_rates or {}).items()}}
    choices: dict[str, dict] = {}
    for inst_id, meta in instruments.items():
        if eligible_pairs is not None and str(inst_id).upper() not in eligible_pairs:
            continue
        ticker = tickers.get(inst_id)
        ok, _reason = _eligible(
            meta, ticker, allowed_quotes=allowed,
            pair_prevalidated=eligible_pairs is not None)
        if not ok:
            continue
        base = str(meta.base_ccy).upper(); quote = str(meta.quote_ccy).upper()
        if quote not in rates:
            # No guessed 1:1 conversion: incomparable quotes fail closed.
            continue
        raw = float(ticker.vol_24h_quote)
        normalized = raw * rates[quote]
        item = {"base": base, "pair": str(inst_id).upper(), "quote": quote,
                "volume_24h_quote": raw, "volume_24h_normalized_eur": normalized,
                "volume_field": "volCcy24h/quote-notional", "quote_rate_to_eur": rates[quote],
                "source": "OKX official public SPOT tickers", "origin": "CORE_VOLUME_20",
                "measured_at_utc": _iso(measured_at)}
        old = choices.get(base)
        quote_rank = allowed.index(quote) if quote in allowed else 999
        old_rank = allowed.index(old["quote"]) if old and old["quote"] in allowed else 999
        if old is None or (-normalized, quote_rank, item["pair"]) < (
                -old["volume_24h_normalized_eur"], old_rank, old["pair"]):
            choices[base] = item
    ranked = sorted(choices.values(), key=lambda x: (-x["volume_24h_normalized_eur"], x["base"], x["pair"]))
    for rank, item in enumerate(ranked[:max(0, int(limit))], 1):
        item["rank"] = rank
    return ranked[:max(0, int(limit))]


def collect(instruments: dict, tickers: dict, *, now: datetime | None = None,
            allowed_quotes=("EUR", "USD", "USDC"), quote_rates: dict | None = None,
            eligible_pairs: set[str] | None = None) -> list[dict]:
    now = now or datetime.now(timezone.utc)
    rows = rank_snapshot(instruments, tickers, allowed_quotes=allowed_quotes,
                         quote_rates=quote_rates, measured_at=now, limit=100,
                         eligible_pairs=eligible_pairs)
    history = _history()
    if history.get("storage_error"):
        raise RuntimeError(history["storage_error"])
    samples = list(history.get("samples") or [])
    day = now.astimezone(timezone.utc).date().isoformat()
    if any(str(x.get("measured_at_utc") or "")[:10] == day for x in samples):
        # One bounded observation per UTC day. A 15-minute universe refresh
        # must not rewrite thousands of ticker rows to the Pi's SD card.
        return rows[:20]
    samples.append({"measured_at_utc": _iso(now), "items": rows})
    cutoff = now.astimezone(timezone.utc) - timedelta(days=35)
    samples = [x for x in samples if datetime.fromisoformat(str(x["measured_at_utc"]).replace("Z", "+00:00")) >= cutoff]
    _save_history({"schema": 1, "samples": samples, "updated_at_utc": _iso(now)})
    # A one-day snapshot is diagnostic only. It must never activate a list
    # advertised as a verified 30-day core.
    current = load()
    if current.get("status") == "EMPTY":
        current.update({"schema": 1, "status": "BUILDING", "items": [],
                        "selection_method": "30d median of one daily OKX snapshot",
                        "last_attempt_utc": _iso(now),
                        "next_due_utc": _iso(next_collection_due(now)),
                        "coverage": {"days": 1, "required_days": int(getattr(config, "CORE_VOLUME_20_MIN_COVERAGE_DAYS", 20)),
                                     "complete": False},
                        "source": "OKX official public SPOT tickers",
                        "error": "30-day coverage incomplete"})
        atomic_write_json(state_path(), current)
    return rows[:20]


def rank_30d(*, now: datetime | None = None, min_days: int = 20) -> tuple[list[dict], dict]:
    now = now or datetime.now(timezone.utc); cutoff = now.astimezone(timezone.utc) - timedelta(days=30)
    history = _history()
    if history.get("storage_error"):
        return [], {"days": 0, "required_days": int(min_days), "complete": False,
                    "storage_error": history["storage_error"]}
    samples = [x for x in (history.get("samples") or [])
               if datetime.fromisoformat(str(x.get("measured_at_utc", "")).replace("Z", "+00:00")) >= cutoff]
    days = sorted({str(x.get("measured_at_utc", ""))[:10] for x in samples})
    by_base: dict[str, dict[str, tuple[float, dict]]] = {}
    for sample in samples:
        sample_day = str(sample.get("measured_at_utc", ""))[:10]
        for item in sample.get("items") or []:
            by_base.setdefault(str(item["base"]), {})[sample_day] = (
                float(item["volume_24h_normalized_eur"]), item)
    coverage = {"days": len(days), "required_days": int(min_days),
                "from_utc": _iso(cutoff), "to_utc": _iso(now)}
    qualifying = {base: values for base, values in by_base.items()
                  if len(values) >= int(min_days)}
    coverage["qualified_bases"] = len(qualifying)
    coverage["complete"] = len(days) >= int(min_days) and len(qualifying) >= 20
    if not coverage["complete"]:
        return [], coverage
    rows = []
    for base, daily in qualifying.items():
        values = list(daily.values())
        med = float(statistics.median(x[0] for x in values))
        representative = sorted((x[1] for x in values), key=lambda x: (x["measured_at_utc"], x["pair"]), reverse=True)[0]
        rows.append({**representative, "volume_30d_median_eur": med,
                     "sample_count": len(values), "unique_days": len(daily),
                     "volume_field": "30d median of one daily normalized volCcy24h"})
    rows.sort(key=lambda x: (-x["volume_30d_median_eur"], x["base"], x["pair"]))
    rows = rows[:20]
    for i, row in enumerate(rows, 1): row["rank"] = i
    return rows, coverage


def _diff(old: list[dict], new: list[dict]) -> dict:
    a = {x["base"] for x in old}; b = {x["base"] for x in new}
    return {"added": sorted(b-a), "removed": sorted(a-b), "unchanged": sorted(a & b)}


def monthly_update(*, now: datetime | None = None, gpt_review=None,
                   manual: bool = False, activate: bool = True,
                   min_days: int | None = None) -> dict:
    now = now or datetime.now(timezone.utc); state = load()
    due_raw = state.get("next_due_utc")
    due = datetime.fromisoformat(str(due_raw).replace("Z", "+00:00")) if due_raw else None
    if not manual and due and now.astimezone(timezone.utc) < due.astimezone(timezone.utc):
        return {**state, "ran": False, "reason": "not due"}
    rows, coverage = rank_30d(now=now, min_days=int(min_days if min_days is not None else getattr(config, "CORE_VOLUME_20_MIN_COVERAGE_DAYS", 20)))
    if not rows:
        state.update({"status": "STALE", "error": "30-day coverage incomplete",
                      "coverage": coverage, "last_attempt_utc": _iso(now),
                      "next_due_utc": _iso(next_collection_due(now))})
        atomic_write_json(state_path(), state)
        _monthly_notify(state, success=False)
        return {**state, "ran": True}
    old = list(state.get("items") or []); diff = _diff(old, rows)
    gpt = {"status": "NOT_NEEDED", "warnings": []}
    changed = diff["added"] or diff["removed"]
    if changed and gpt_review is not None:
        try:
            # Exactly one compact call; the callback never receives authority to mutate rows.
            answer = gpt_review({"added": diff["added"], "removed": diff["removed"],
                                 "task": "identity_rebrand_risk_flags_only"})
            if not isinstance(answer, dict) or not isinstance(answer.get("warnings", []), list):
                raise ValueError("invalid structured response")
            gpt = {"status": "OK", "warnings": [str(x)[:300] for x in answer.get("warnings", [])[:20]]}
        except Exception as exc:
            gpt = {"status": "FAILED", "warnings": [type(exc).__name__]}
    # Conservative standard: deterministic activation is allowed only when all
    # fixed tests passed; GPT cannot block or alter it and its failure is visible.
    snapshot = {"schema": 1, "saved_at_utc": _iso(now), "items": old}
    backup = state_path().with_name(f"core_volume_20.backup-{now.strftime('%Y%m%dT%H%M%SZ')}.json")
    atomic_write_json(backup, snapshot)
    if activate:
        state.update({"schema": 1, "status": "CURRENT", "items": rows,
                      "last_success_utc": _iso(now), "last_attempt_utc": _iso(now),
                      "next_due_utc": _iso(next_due(now)), "coverage": coverage,
                      "changes": diff, "gpt_review": gpt, "source": "OKX official public SPOT tickers",
                      "manual_activation": bool(manual), "backup": backup.name, "error": ""})
        atomic_write_json(state_path(), state)
    _monthly_notify(state, success=bool(activate))
    return {**state, "ran": True}


def preview(instruments: dict, tickers: dict, *, now: datetime | None = None,
            allowed_quotes=("EUR", "USD", "USDC"), quote_rates=None) -> dict:
    rows = rank_snapshot(instruments, tickers, allowed_quotes=allowed_quotes,
                         quote_rates=quote_rates, measured_at=now, limit=20)
    return {"read_only": True, "items": rows, "active_unchanged": True, "changes": _diff(load().get("items", []), rows)}


def current_bases(*, require_fresh: bool = False) -> set[str]:
    state = load()
    if require_fresh and state.get("status") != "CURRENT":
        return set()
    return {str(x.get("base", "")).upper() for x in state.get("items") or [] if x.get("base")}


def can_open_new(base: str) -> bool:
    state = load()
    return state.get("status") == "CURRENT" and str(base).upper() in current_bases()


def _monthly_notify(state: dict, *, success: bool) -> None:
    try:
        from notifier import send_telegram
        when = str(state.get("last_success_utc") or state.get("last_attempt_utc") or "")
        changes = state.get("changes") or {}
        if success:
            text = (f"📊 CORE_VOLUME_20 Monatslauf {when}\n"
                    f"Hinzu: {', '.join(changes.get('added', [])) or 'keine'}\n"
                    f"Entfernt: {', '.join(changes.get('removed', [])) or 'keine'}\n"
                    f"Unverändert: {len(changes.get('unchanged', []))}\n"
                    f"GPT-Prüfung: {(state.get('gpt_review') or {}).get('status', 'nicht nötig')}")
            event = f"core-volume-month:{when[:7]}:success"
        else:
            text = f"⚠️ CORE_VOLUME_20 nicht aktualisiert: {state.get('error') or 'Daten stale'}. Alte Liste bleibt erhalten; neue Käufe daraus gesperrt."
            event = f"core-volume-month:{str(state.get('last_attempt_utc', ''))[:7]}:failed"
        send_telegram(text, priority="critical" if not success else "normal", event_id=event)
    except Exception:
        logger.debug("CORE_VOLUME_20 Telegram-Hinweis nicht zustellbar", exc_info=True)
