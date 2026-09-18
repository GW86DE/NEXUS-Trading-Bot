"""Versionsschema fuer reproduzierbare Kaufentscheidungen (NEXUS 8.3).

Das Modul bewertet nichts. Es ordnet ausschliesslich bereits gemessene Werte
einem stabilen, maschinenlesbaren Snapshot zu. Fehlende Werte bleiben ``None``;
sie werden niemals aus Freitext rekonstruiert oder geraten.
"""
from __future__ import annotations

import math
import secrets
from datetime import datetime, timezone
from typing import Any, Iterable

SCHEMA_VERSION = 1
GATE_STATUSES = {"PASS", "BLOCKED", "NOT_EVALUATED", "SYSTEM_ERROR"}


def new_decision_id() -> int:
    """Positive 63-bit ID, die vor dem ersten Gate erzeugt werden kann."""
    return secrets.randbits(62) + 1


def number(value: Any) -> float | None:
    """Nur endliche echte Zahlen; leere/NaN/Inf-Werte bleiben unbekannt."""
    if value in (None, ""):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def integer(value: Any) -> int | None:
    n = number(value)
    return int(n) if n is not None else None


def clean_error(exc: BaseException, *, max_length: int = 300) -> dict:
    """Persistierbare Fehlermeldung ohne Traceback, Secrets oder Objekt-Repr."""
    text = " ".join(str(exc).replace("\n", " ").replace("\r", " ").split())
    return {"exception_type": type(exc).__name__, "message": text[:max_length]}


def gate_trace(evaluated: Iterable[dict] | None,
               not_evaluated: Iterable[str] | None) -> list[dict]:
    gates: list[dict] = []
    seen: set[str] = set()
    for item in evaluated or []:
        name = str(item.get("name") or "unknown")
        raw = str(item.get("status") or "PASS").upper()
        status = "PASS" if raw in {"PASS", "PASS_OR_NOT_APPLICABLE"} else raw
        status = status if status in GATE_STATUSES else "PASS"
        row = {"name": name, "status": status}
        for key in ("actual", "limit", "unit", "reason"):
            if key in item:
                row[key] = item[key]
        gates.append(row)
        seen.add(name)
    for name in not_evaluated or []:
        name = str(name)
        if name not in seen:
            gates.append({"name": name, "status": "NOT_EVALUATED"})
            seen.add(name)
    return gates


def _timeseries(payload: dict) -> dict:
    technical = dict(payload.get("technical") or {})
    aliases = {
        "rsi_5m": "rsi_5m", "rsi_15m": "rsi_15m", "rsi_1h": "rsi_1h",
        "atr": "atr", "sma20_1h": "sma20_1h", "sma50_1h": "sma50_1h",
        "ema20": "ema20", "ema50": "ema50", "ml_probability": "ml_probability",
    }
    for source, target in aliases.items():
        if target not in technical:
            technical[target] = number(payload.get(source))
    technical.setdefault("signal", str(payload.get("signal_reason") or ""))
    technical.setdefault("enter_tag", str(payload.get("enter_tag") or payload.get("signal_reason") or "")[:160])
    technical.setdefault("candle_timestamps", dict(payload.get("candle_timestamps") or {}))
    technical.setdefault("completed_candles_only", payload.get("completed_candles_only"))
    return technical


def build_snapshot(payload: dict) -> dict:
    """Normalisiert flache Altwerte in einen strukturierten DecisionSnapshot."""
    p = dict(payload)
    raw_id = p.get('decision_id')
    if raw_id in (None, '', 0):
        did = new_decision_id()
    elif type(raw_id) is int:
        did = raw_id
    elif isinstance(raw_id, str) and raw_id.isascii() and raw_id.isdecimal():
        did = int(raw_id)
    elif type(raw_id) is float and math.isfinite(raw_id) and raw_id.is_integer() and 0 < raw_id <= 2**53:
        did = int(raw_id)
    else:
        raise ValueError('Entscheidungs-ID muss verlustfrei als Ganzzahl vorliegen')
    if not 0 < did < 2**63:
        raise ValueError('Entscheidungs-ID außerhalb des gültigen Bereichs')
    gates = p.get("gates")
    if not isinstance(gates, list):
        gates = gate_trace(p.get("evaluated_filters"), p.get("not_evaluated_filters"))
    market_session = p.get("market_session") if isinstance(p.get("market_session"), dict) else {}
    snapshot = {
        "schema_version": SCHEMA_VERSION,
        "decision_id": did,
        "created_at_utc": str(p.get("time") or p.get("created_at") or datetime.now(timezone.utc).isoformat()),
        "identity": {
            "symbol": str(p.get("symbol") or "?").upper(),
            "asset_type": str(p.get("asset_type") or ""),
            "broker": str(p.get("broker") or "").lower(),
            "paper": bool(p.get("paper", True)),
            "currency": str(p.get("currency") or p.get("waehrung") or "").upper(),
            "profile": str(p.get("profile") or ""),
            "strategy_version": str(p.get("strategy_version") or ""),
        },
        "market": {
            "price": number(p.get("price")), "bid": number(p.get("bid")),
            "ask": number(p.get("ask")), "spread_pct": number(p.get("spread_pct")),
            "spread_limit_pct": number(p.get("spread_limit_pct")),
            "quote_age_seconds": number(p.get("quote_age_seconds", market_session.get("quote_age_seconds"))),
            "quote_source": str(p.get("quote_source") or ""),
            "session": market_session or None,
            "universe_rank": integer(p.get("universe_rank")),
            "universe_score": number(p.get("universe_score")),
        },
        "technical": _timeseries(p),
        "risk_account": {
            "cash": number(p.get("cash_before", p.get("cash"))),
            "equity": number(p.get("equity_before", p.get("equity"))),
            "daily_pnl": number(p.get("daily_pnl", p.get("realized_pnl_today"))),
            "open_positions": integer(p.get("open_positions")),
            "trades_today": integer(p.get("trades_today")),
            "cooldown_active": p.get("cooldown_active"),
            "risk_amount": number(p.get("risk_amount")),
            "risk_pct": number(p.get("risk_pct")),
        },
        "order_plan": {
            "planned_qty": number(p.get("planned_qty", p.get("qty"))),
            "final_qty": number(p.get("final_qty", p.get("qty"))),
            "position_value": number(p.get("position_value")),
            "stop_loss": number(p.get("stop_loss", p.get("stop"))),
            "take_profit": number(p.get("take_profit", p.get("take"))),
            "fees_estimated": number(p.get("fees_estimated")),
            "cost_pct": number(p.get("cost_pct")),
            "required_edge_pct": number(p.get("required_edge_pct")),
            "expected_move_pct": number(p.get("expected_move_pct", p.get("plausible_move_pct"))),
            "net_edge_pct": number(p.get("net_edge_pct")),
        },
        "news_ai": {
            "event_score": number(p.get("event_score")),
            "event_summary": str(p.get("event_summary") or ""),
            "sources": list(p.get("news_sources") or []),
            "ai": dict(p.get("ai") or {}),
        },
        "gates": gates,
        "execution": {
            "status": str(p.get("execution_status") or ""),
            "entry_order_status": str(p.get("entry_order_status") or ""),
            "protective_order_status": str(p.get("protective_order_status") or ""),
            "filled_qty": number(p.get("filled_quantity")),
            "fill_price": number(p.get("avg_fill_price")),
        },
    }
    return snapshot


def apply_snapshot(payload: dict) -> dict:
    result = dict(payload)
    snapshot = build_snapshot(result)
    result["decision_id"] = snapshot["decision_id"]
    result["decision_snapshot"] = snapshot
    return result
