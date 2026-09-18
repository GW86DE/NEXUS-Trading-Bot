"""Passive presentation of existing evidence; no decisions, providers or writes."""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
from pathlib import Path


def startup_repetition(row, payload):
    """Only a visual group: missing identities stay explicitly unknown."""
    context = row.get("display_context") or {}
    reason = str(row.get("reason") or "")
    if (context.get("broker") not in {"okx", "etoro"} or str(row.get("status")).upper() != "BLOCKED"
            or not re.match(r"(?:anlaufsperre|sicherheitswartezeit)\b", reason, re.I)
            or row.get("execution_status") not in {None, "", "BLOCKED"}
            or row.get("orders") or row.get("execution_events")):
        return {}
    parameters = payload.get("strategy_parameters") or {}
    if not isinstance(parameters, dict):
        parameters = {}
    timeframe = payload.get("signal_timeframe") or payload.get("timeframe") or parameters.get("timeframe")
    instrument = payload.get("signal_instrument") or payload.get("inst_id") or payload.get("instrument_id")
    # An unbound historical refusal is never joined to a known account. Missing
    # route identity is labelled as such, not inferred from a symbol. There is
    # no trade/order/ownership aggregation and no action on a display group.
    if not timeframe or not row.get("symbol") or not row.get("strategy_mode"):
        return {}
    # Replace only an explicitly labelled countdown, never thresholds, IDs or
    # other numeric failure details that might identify different blockers.
    canonical = re.sub(r"(?i)(noch|verbleibend:?)[ ]+\d+(?:[.,]\d+)?[ ]*(s|sekunden|min|minuten)\b",
                       r"\1 <Wartezeit> \2", reason)
    canonical = re.sub(r"^(Anlaufsperre: neue Kaeufe frei in )\d+ (?:min(?: \d+ s)?|s)( \([^)]*\))$",
                       r"\1<Wartezeit>\2", canonical)
    domain = [context.get("broker"), context.get("environment"), context.get("account") or "UNBOUND"]
    key = [domain, row.get("local_day"), row.get("symbol"), str(instrument or "UNPROVEN"), str(timeframe),
           row.get("strategy_mode"), row.get("strategy_version"), row.get("strategy_parameter_hash"),
           row.get("blocked_by"), row.get("side") or payload.get("side") or "BUY", canonical]
    return {"key": hashlib.sha256(json.dumps(key, ensure_ascii=False).encode()).hexdigest(),
            "instrument": str(instrument or "Instrument-ID nicht belegt"), "timeframe": str(timeframe), "reason": canonical,
            "account_bound": context.get("bound") is True, "instrument_proven": bool(instrument),
            "scope": "VISIBLE_PAGE_ONLY"}


def fmp_packet_receipts(cards):
    """Count unique validated FMP input packets, not model impact or HTTP calls."""
    receipts = {}
    for card in cards:
        if not isinstance(card, dict):
            continue
        for phase in ("precheck", "analysis", "countercheck"):
            answer = card.get(phase) or {}
            if not isinstance(answer, dict) or answer.get("ok") is not True:
                continue
            digest = str(answer.get("input_hash") or "")
            if not re.fullmatch(r"[0-9a-fA-F]{64}", digest):
                continue
            sources = answer.get("input_sources") or {}
            if not isinstance(sources, dict):
                continue
            for symbol, packet in sources.items():
                if not isinstance(packet, dict) or packet.get("status") != "INPUT_OF_VALIDATED_RESPONSE":
                    continue
                for source in packet.get("sources") or []:
                    if (not isinstance(source, dict) or source.get("included") is not True
                            or str(source.get("provider") or "").casefold() not in {"fmp", "financialmodelingprep"}
                            or not source.get("source_id")):
                        continue
                    key = (phase, digest, str(symbol), str(source["source_id"]))
                    execution = answer.get("source_execution") or answer.get("execution") or {}
                    receipts[key] = {"phase": phase, "symbol": str(symbol), "input_hash": digest,
                        "source_id": source["source_id"], "kind": source.get("kind"),
                        "as_of": source.get("as_of"), "included_rows": source.get("included_rows"),
                        "truncated": source.get("truncated"),
                        "request_id": execution.get("local_request_id"), "request_at": execution.get("started_at")}
    rows = list(receipts.values())
    return {"state": "OBSERVED" if rows else "NOT_EVIDENCED", "receipts": rows,
            "input_packets": len({(r["phase"], r["input_hash"]) for r in rows}),
            "symbols": sorted({r["symbol"] for r in rows}), "source_count": len({r["source_id"] for r in rows}),
            "claim": "VALIDATED_INPUT_ONLY", "trade_effect": "NOT_ESTABLISHED"}


def saved_fmp_packet_usage(path: Path):
    """Read a bounded existing snapshot without creating/migrating SQLite."""
    result = {**fmp_packet_receipts([]), "complete": False,
              "scope": "Gespeicherte aktuelle Karten und höchstens 40 jüngste Bewertungen; kein 30-Tage-Gesamtzähler"}
    if not path.is_file():
        return dict(result, detail="Noch keine gespeicherten PULSAR-Pakete verfügbar.")
    cards, budget = [], 16_000_000
    try:
        with sqlite3.connect(path.resolve().as_uri()+"?mode=ro", uri=True, timeout=2) as con:
            deadline = time.monotonic()+1.5
            con.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
            con.execute("BEGIN")
            tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            partial = False
            if "cache" in tables:
                meta = con.execute("SELECT length(payload) FROM cache WHERE key='top5'").fetchone()
                if meta and meta[0] <= 8_000_000:
                    raw = con.execute("SELECT payload FROM cache WHERE key='top5'").fetchone()[0]
                    values = json.loads(raw)
                    budget -= len(raw)
                    if isinstance(values, list):
                        cards.extend(values)
                    else:
                        partial = True
                elif meta:
                    partial = True
            if "assessments" in tables:
                metadata = con.execute("SELECT id,length(payload) FROM assessments ORDER BY at DESC LIMIT 40").fetchall()
                for identifier, size in metadata:
                    if size > min(budget, 2_000_000):
                        partial = True
                        continue
                    raw = con.execute("SELECT payload FROM assessments WHERE id=?", (identifier,)).fetchone()[0]
                    budget -= len(raw)
                    values = json.loads(raw)
                    if isinstance(values, dict):
                        cards.append(values)
                    else:
                        partial = True
        return {**result, **fmp_packet_receipts(cards), "complete": not partial,
                "detail": "Übergabe in validierten GPT-Eingaben belegt; Gewichtung und Handelswirkung bleiben unbekannt."}
    except (sqlite3.Error, OSError, ValueError, TypeError):
        return dict(result, detail="Gespeicherte FMP-Eingabebelege nicht vollständig lesbar.")


def position_risk_display(position, runtime):
    from broker_display_context import context
    pc, rc = context(position, broker="etoro"), context(runtime, broker="etoro")
    if not pc["bound"] or pc["domain_key"] != rc["domain_key"]:
        return {"state": "UNKNOWN", "detail": "Für diese Position fehlt ein passender Konto-Risikobeleg."}
    risk = runtime.get("risk_review") or {}
    if not isinstance(risk, dict) or not risk:
        return {"state": "UNKNOWN", "detail": "Keine separate Risikobasis-Prüfung gespeichert."}
    return {"state": "STALE" if not runtime.get("worker_alive") else "BLOCKED" if risk.get("blocks_entries") is True else "REPORTED",
            "detail": risk.get("detail") or risk.get("status"), "blocks_entries": risk.get("blocks_entries"),
            "observed_at": runtime.get("last_heartbeat")}


def closed_result_display(row):
    """A missing fee receipt cannot turn an old net estimate into known money."""
    from ledger_result import fees_confirmed
    result = dict(row)
    if row.get("ausgestiegen_am") and not fees_confirmed(row):
        result["historical_result_scope"] = "CLOSED_TRADE_COSTS_ONLY"
        result["unconfirmed_net_audit"] = row.get("netto_pnl")
        result["netto_pnl"] = None
        result["netto_pnl_pct"] = None
        result["gebuehren"] = None
    return result
