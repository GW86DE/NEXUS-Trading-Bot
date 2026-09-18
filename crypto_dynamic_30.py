"""Persistente Monatsrotation fuer die 30 dynamischen OKX-Kryptowerte.

Der feste Kern lebt in ``config.CRYPTO_CORE_SYMBOLS`` und wird hier immer
ausgeschlossen. Dieses Modul entscheidet ebenfalls nicht ueber Kaeufe: Es
bestimmt nur, welche 30 zusaetzlichen Basiswerte NEXUS beobachten darf.

Erststart
=========
Damit das Universum nicht einen Monat lang leer bleibt, wird aus einem
vollstaendigen aktuellen OKX-SPOT-Snapshot sofort eine reproduzierbare
Startliste gebildet. Die Mitglieder starten im UniverseManager trotzdem in
BEOBACHTUNG; Handelsbereitschaft, abgeschlossene Kerzen, Spread, Orderbuch,
Kosten und Risiko bleiben danach fail-closed.

Monatslauf
==========
Anschliessend wird nur zum Monatswechsel rotiert. Verwendet wird der Median
aus hoechstens einer OKX-24h-Volumenmessung je UTC-Tag. Reicht die Abdeckung
nicht, bleibt die alte Liste sichtbar und der Fehler wird ausgewiesen.
"""
from __future__ import annotations

import json
import logging
import os
import re
import statistics
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import config
from safe_persistence import atomic_write_json

logger = logging.getLogger(__name__)
ZONE = ZoneInfo("Europe/Berlin")
STABLE = {
    "USDT", "USDC", "DAI", "TUSD", "USDP", "FDUSD", "PYUSD", "EURT",
    "EURC", "USDD", "GUSD", "LUSD", "USDE", "SUSD", "BUSD", "USD", "EUR",
}
LEVERAGED = re.compile(r"^[A-Z0-9]{2,}(?:3L|3S|5L|5S|UP|DOWN|BULL|BEAR)$")


def _root() -> Path:
    return Path(os.getenv("TRADINGBOT_TEST_STATE_DIR", "").strip()
                or Path(__file__).resolve().parent)


def state_path() -> Path:
    return _root() / getattr(config, "CRYPTO_DYNAMIC_30_FILE", "crypto_dynamic_30.json")


def history_path() -> Path:
    return _root() / getattr(
        config, "CRYPTO_DYNAMIC_30_HISTORY_FILE", "crypto_dynamic_30_history.json")


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def load() -> dict:
    try:
        data = json.loads(state_path().read_text(encoding="utf-8")) if state_path().exists() else {}
        if not isinstance(data, dict):
            raise ValueError("Status ist kein Objekt")
        data.setdefault("schema", 1)
        data.setdefault("status", "EMPTY")
        data.setdefault("items", [])
        return data
    except Exception as exc:
        return {"schema": 1, "status": "STALE", "items": [],
                "error": f"Status nicht lesbar: {type(exc).__name__}"}


def _history() -> dict:
    try:
        data = json.loads(history_path().read_text(encoding="utf-8")) if history_path().exists() else {}
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        return {"samples": [], "storage_error": f"Historie nicht lesbar: {type(exc).__name__}"}


def next_due(after: datetime) -> datetime:
    local = after.astimezone(ZONE)
    year, month = local.year, local.month
    if month == 12:
        year, month = year + 1, 1
    else:
        month += 1
    return datetime(year, month, 1, 3, 20, tzinfo=ZONE)


def next_collection_due(after: datetime) -> datetime:
    local = after.astimezone(ZONE) + timedelta(days=1)
    return local.replace(hour=3, minute=20, second=0, microsecond=0)


def _eligible(meta, ticker, *, allowed_quotes: tuple[str, ...],
              fixed_bases: set[str], pair_prevalidated: bool = False) -> bool:
    inst_type = str(getattr(meta, "inst_type", getattr(meta, "type", "SPOT")) or "SPOT").upper()
    quote = str(getattr(meta, "quote_ccy", "") or "").upper()
    base = str(getattr(meta, "base_ccy", "") or "").upper()
    state = str(getattr(meta, "state", "") or "").lower()
    if inst_type != "SPOT" or (not pair_prevalidated and quote not in allowed_quotes) or state != "live":
        return False
    if not base or base in fixed_bases or base in STABLE or LEVERAGED.fullmatch(base):
        return False
    if ticker is None or float(getattr(ticker, "vol_24h_quote", 0) or 0) <= 0:
        return False
    return True


def rank_snapshot(instruments: dict, tickers: dict, *, allowed_quotes=("EUR", "USD", "USDC"),
                  quote_rates: dict | None = None, measured_at: datetime | None = None,
                  limit: int = 30, eligible_pairs: set[str] | None = None,
                  fixed_bases: set[str] | None = None) -> list[dict]:
    """Rangfolge nach normalisiertem OKX-Quote-Notional, je Basis ein Paar."""
    measured_at = measured_at or datetime.now(timezone.utc)
    allowed = tuple(dict.fromkeys(str(x).upper() for x in allowed_quotes))
    fixed = {str(x).upper() for x in (fixed_bases if fixed_bases is not None
                                      else getattr(config, "CRYPTO_CORE_SYMBOLS", ())) }
    rates = {"EUR": 1.0, **{
        str(k).upper(): float(v) for k, v in (quote_rates or {}).items()
        if float(v) > 0
    }}
    choices: dict[str, dict] = {}
    for inst_id, meta in instruments.items():
        iid = str(inst_id).upper()
        if eligible_pairs is not None and iid not in eligible_pairs:
            continue
        ticker = tickers.get(inst_id)
        if not _eligible(meta, ticker, allowed_quotes=allowed, fixed_bases=fixed,
                         pair_prevalidated=eligible_pairs is not None):
            continue
        base = str(meta.base_ccy).upper()
        quote = str(meta.quote_ccy).upper()
        if quote not in rates:
            continue  # keine erfundene 1:1-Umrechnung
        raw = float(ticker.vol_24h_quote)
        normalized = raw * rates[quote]
        item = {
            "base": base, "pair": iid, "quote": quote,
            "volume_24h_quote": raw,
            "volume_24h_normalized_eur": normalized,
            "quote_rate_to_eur": rates[quote],
            "volume_field": "volCcy24h/Quote-Notional",
            "source": "OKX official public SPOT tickers",
            "origin": "DYNAMIC_30",
            "measured_at_utc": _iso(measured_at),
        }
        old = choices.get(base)
        quote_rank = allowed.index(quote) if quote in allowed else 999
        old_rank = allowed.index(old["quote"]) if old and old["quote"] in allowed else 999
        if old is None or (-normalized, quote_rank, iid) < (
                -old["volume_24h_normalized_eur"], old_rank, old["pair"]):
            choices[base] = item
    ranked = sorted(choices.values(), key=lambda x: (
        -x["volume_24h_normalized_eur"], x["base"], x["pair"]))
    result = ranked[:max(0, int(limit))]
    for rank, item in enumerate(result, 1):
        item["rank"] = rank
    return result


def _store_daily(rows: list[dict], now: datetime) -> dict:
    history = _history()
    if history.get("storage_error"):
        raise RuntimeError(history["storage_error"])
    samples = list(history.get("samples") or [])
    day = now.astimezone(timezone.utc).date().isoformat()
    if not any(str(x.get("measured_at_utc") or "")[:10] == day for x in samples):
        samples.append({"measured_at_utc": _iso(now), "items": rows[:100]})
        cutoff = now.astimezone(timezone.utc) - timedelta(days=35)
        samples = [x for x in samples if datetime.fromisoformat(
            str(x.get("measured_at_utc", "")).replace("Z", "+00:00")) >= cutoff]
        atomic_write_json(history_path(), {
            "schema": 1, "samples": samples, "updated_at_utc": _iso(now)})
    return {"days": len({str(x.get("measured_at_utc", ""))[:10] for x in samples})}


def _diff(old: list[dict], new: list[dict]) -> dict:
    before = {str(x.get("base", "")).upper() for x in old if x.get("base")}
    after = {str(x.get("base", "")).upper() for x in new if x.get("base")}
    return {"added": sorted(after - before), "removed": sorted(before - after),
            "unchanged": sorted(before & after)}


def collect_and_update(instruments: dict, tickers: dict, *, now: datetime | None = None,
                       allowed_quotes=("EUR", "USD", "USDC"), quote_rates: dict | None = None,
                       eligible_pairs: set[str] | None = None,
                       fixed_bases: set[str] | None = None) -> dict:
    """Taeglich sammeln, sofort initialisieren und danach monatlich rotieren."""
    now = now or datetime.now(timezone.utc)
    rows = rank_snapshot(
        instruments, tickers, allowed_quotes=allowed_quotes, quote_rates=quote_rates,
        measured_at=now, limit=100, eligible_pairs=eligible_pairs,
        fixed_bases=fixed_bases)
    coverage = _store_daily(rows, now)
    state = load()
    limit = int(getattr(config, "CRYPTO_UNIVERSE_DYNAMIC_LIMIT", 30))
    policy_changed = int(state.get("currency_policy_version", 0) or 0) != 3
    if not state.get("items") or policy_changed:
        old_items = list(state.get("items") or [])
        initial = rows[:limit]
        status = "CURRENT" if len(initial) == limit else "PARTIAL"
        state = {
            "schema": 1, "currency_policy_version": 3,
            "status": status, "items": initial,
            "selection_method": "Startliste aus aktuellem normalisiertem OKX-24h-Quote-Notional",
            "source": "OKX official public SPOT tickers",
            "last_success_utc": _iso(now), "last_attempt_utc": _iso(now),
            "next_due_utc": _iso(next_due(now)),
            "coverage": {**coverage, "required_days": int(getattr(
                config, "CRYPTO_DYNAMIC_30_MIN_COVERAGE_DAYS", 20)),
                "complete": False},
            "changes": _diff(old_items, initial),
            "error": "" if len(initial) == limit else (
                f"Nur {len(initial)} von {limit} dynamischen Werten aktuell verifizierbar"),
        }
        atomic_write_json(state_path(), state)
        _notify(state, kind="bootstrap")
        return {**state, "ran": True, "bootstrap": not bool(old_items),
                "policy_migration": policy_changed and bool(old_items)}

    due_raw = state.get("next_due_utc")
    try:
        due = datetime.fromisoformat(str(due_raw).replace("Z", "+00:00")) if due_raw else None
    except ValueError:
        due = None
    if due is None or now.astimezone(timezone.utc) >= due.astimezone(timezone.utc):
        return monthly_update(now=now)
    return {**state, "ran": False, "reason": "not due"}


def rank_30d(*, now: datetime | None = None, min_days: int | None = None,
             limit: int | None = None) -> tuple[list[dict], dict]:
    now = now or datetime.now(timezone.utc)
    min_days = int(min_days if min_days is not None else getattr(
        config, "CRYPTO_DYNAMIC_30_MIN_COVERAGE_DAYS", 20))
    limit = int(limit if limit is not None else getattr(config, "CRYPTO_UNIVERSE_DYNAMIC_LIMIT", 30))
    cutoff = now.astimezone(timezone.utc) - timedelta(days=30)
    history = _history()
    if history.get("storage_error"):
        return [], {"days": 0, "required_days": min_days, "complete": False,
                    "storage_error": history["storage_error"]}
    samples = [x for x in (history.get("samples") or []) if datetime.fromisoformat(
        str(x.get("measured_at_utc", "")).replace("Z", "+00:00")) >= cutoff]
    days = sorted({str(x.get("measured_at_utc", ""))[:10] for x in samples})
    by_base: dict[str, dict[str, tuple[float, dict]]] = {}
    for sample in samples:
        day = str(sample.get("measured_at_utc", ""))[:10]
        for item in sample.get("items") or []:
            base = str(item.get("base", "")).upper()
            if base:
                by_base.setdefault(base, {})[day] = (
                    float(item.get("volume_24h_normalized_eur", 0) or 0), item)
    qualifying = {base: values for base, values in by_base.items()
                  if len(values) >= min_days}
    coverage = {"days": len(days), "required_days": min_days,
                "qualified_bases": len(qualifying), "from_utc": _iso(cutoff),
                "to_utc": _iso(now)}
    coverage["complete"] = len(days) >= min_days and len(qualifying) >= limit
    if not coverage["complete"]:
        return [], coverage
    rows = []
    for base, daily in qualifying.items():
        values = list(daily.values())
        representative = sorted((x[1] for x in values), key=lambda x: (
            x.get("measured_at_utc", ""), x.get("pair", "")), reverse=True)[0]
        rows.append({**representative,
                     "volume_30d_median_eur": float(statistics.median(x[0] for x in values)),
                     "sample_count": len(values), "unique_days": len(daily),
                     "volume_field": "30-Tage-Median taeglicher OKX-Quote-Notionals"})
    rows.sort(key=lambda x: (-x["volume_30d_median_eur"], x["base"], x["pair"]))
    rows = rows[:limit]
    for rank, item in enumerate(rows, 1):
        item["rank"] = rank
    return rows, coverage


def monthly_update(*, now: datetime | None = None, min_days: int | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    state = load()
    rows, coverage = rank_30d(now=now, min_days=min_days)
    if not rows:
        state.update({"status": "STALE", "error": "30-Tage-Abdeckung unvollstaendig; alte Liste bleibt",
                      "coverage": coverage, "last_attempt_utc": _iso(now),
                      "next_due_utc": _iso(next_collection_due(now))})
        atomic_write_json(state_path(), state)
        _notify(state, kind="failed")
        return {**state, "ran": True}
    old = list(state.get("items") or [])
    diff = _diff(old, rows)
    backup = state_path().with_name(
        f"crypto_dynamic_30.backup-{now.strftime('%Y%m%dT%H%M%SZ')}.json")
    atomic_write_json(backup, {"schema": 1, "saved_at_utc": _iso(now), "items": old})
    state.update({"schema": 1, "status": "CURRENT", "items": rows,
                  "selection_method": "30-Tage-Median taeglicher normalisierter OKX-Quote-Notionals",
                  "source": "OKX official public SPOT tickers",
                  "last_success_utc": _iso(now), "last_attempt_utc": _iso(now),
                  "next_due_utc": _iso(next_due(now)), "coverage": coverage,
                  "changes": diff, "backup": backup.name, "error": ""})
    atomic_write_json(state_path(), state)
    _notify(state, kind="monthly")
    return {**state, "ran": True}


def mark_stale(error: str) -> dict:
    state = load()
    state.update({"status": "STALE", "error": str(error)[:300],
                  "last_attempt_utc": _iso(datetime.now(timezone.utc))})
    atomic_write_json(state_path(), state)
    return state


def current_bases() -> set[str]:
    return {str(x.get("base", "")).upper() for x in load().get("items") or []
            if x.get("base")}


def _notify(state: dict, *, kind: str) -> None:
    try:
        from notifier import send_telegram
        changes = state.get("changes") or {}
        if kind == "failed":
            text = ("⚠️ Dynamisches Krypto-Universum nicht neu bewertet: "
                    f"{state.get('error')}. Die vorige Mitgliedschaft bleibt; "
                    "Live-Handelsfilter gelten weiter.")
            priority = "critical"
        else:
            titel = "Startliste" if kind == "bootstrap" else "Monatslauf"
            text = (f"📊 Krypto DYNAMIC_30 {titel}\n"
                    f"Hinzu: {', '.join(changes.get('added', [])) or 'keine'}\n"
                    f"Entfernt: {', '.join(changes.get('removed', [])) or 'keine'}\n"
                    f"Unverändert: {len(changes.get('unchanged', []))}\n"
                    f"Nächste Bewertung: {state.get('next_due_utc', '–')}")
            priority = "normal"
        stamp = str(state.get("last_success_utc") or state.get("last_attempt_utc") or "")
        send_telegram(text, priority=priority,
                      event_id=f"crypto-dynamic-30:{kind}:{stamp[:10]}")
    except Exception:
        logger.debug("DYNAMIC_30 Telegram-Hinweis nicht zustellbar", exc_info=True)


__all__ = [
    "collect_and_update", "current_bases", "history_path", "load", "mark_stale",
    "monthly_update", "next_due", "rank_30d", "rank_snapshot", "state_path",
]
