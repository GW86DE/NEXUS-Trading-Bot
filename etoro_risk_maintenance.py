"""Read-only eToro evidence collection for risk maintenance.

No RiskState.load/save, no trading daemon, no trade/stop modification. The
transport below accepts only the selected environment's documented GET paths.
Current balances and closed trades are NOT a complete daily account statement.
"""
from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from hashlib import sha256
import json
import math
from pathlib import Path
from zoneinfo import ZoneInfo

KIND = "ETORO_RISK_READ_ONLY_EVIDENCE"
BASIS = "broker_kontowert:v2:etoro:USD"
MAX_AGE_SECONDS = 120
GAPS = {
    "RISK_DAY_START_EQUITY_UNPROVEN": "Der Kontowert zum Beginn des lokalen Handelstages ist nicht belegt.",
    "RISK_CASHFLOW_COMPLETENESS_UNPROVEN": "Eine vollstaendige Tagesliste der Ein-/Auszahlungen und Umbuchungen dieses Trading-Kontos fehlt.",
    "RISK_COST_COMPLETENESS_UNPROVEN": "Gebuehren, Finanzierungen und sonstige Tagesbuchungen sind nicht vollstaendig belegt.",
    "RISK_LEGACY_HISTORY_UNASSIGNED": "Alte Ergebnisse sind keinem belegten Konto zugeordnet und bleiben unveraendert erhalten.",
}


def _now():
    return datetime.now(timezone.utc)


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8")


def _digest(value):
    return sha256(_canonical(value)).hexdigest()


def _stamp(value):
    result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("RISK_EVIDENCE_TIMEZONE_MISSING")
    return result.astimezone(timezone.utc)


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
        raise ValueError("RISK_EVIDENCE_NUMBER_UNPROVEN")
    return value


def readonly_broker(*, environment):
    """Local configuration supplies credentials; callers cannot select another URL."""
    from broker.etoro import EtoroBroker
    if environment not in {"DEMO", "LIVE"}:
        raise ValueError("RISK_ENVIRONMENT_REQUIRED")

    class ReadOnlyEtoro(EtoroBroker):
        def _request(self, method, path, **kwargs):
            allowed = {
                "/api/v1/me",
                "/api/v1/trading/info/demo/pnl" if self.paper else "/api/v1/trading/info/real/pnl",
                "/api/v1/trading/info/demo/aggregate-portfolio" if self.paper else "/api/v1/trading/info/aggregate-portfolio",
                "/api/v1/trading/info/trade/demo/history" if self.paper else "/api/v1/trading/info/trade/history",
            }
            if (method != "GET" or path not in allowed or kwargs.get("json") is not None
                    or kwargs.get("payload") is not None):
                raise ValueError("RISK_COLLECTOR_READ_ONLY")
            if self.base_url != "https://public-api.etoro.com":
                raise ValueError("RISK_COLLECTOR_API_ORIGIN_UNPROVEN")
            response = super()._request(method, path, **kwargs)
            if path.endswith("/history"):
                batch = response
                if isinstance(response, dict):
                    if ("items" in response) == ("trades" in response):
                        raise ValueError("RISK_HISTORY_RESPONSE_SCHEMA_UNPROVEN")
                    batch = response.get("items") if "items" in response else response.get("trades")
                # The general adapter drops non-dict rows before its short-page
                # test. Reject them here, before evidence could be lost.
                if not isinstance(batch, list) or any(not isinstance(row, dict) for row in batch):
                    raise ValueError("RISK_HISTORY_RESPONSE_ROWS_UNPROVEN")
            return response

    return ReadOnlyEtoro(paper=environment == "DEMO")


def _validate_rows(rows, *, closed=False):
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError("RISK_POSITION_ROWS_UNPROVEN")
    ids = []
    for row in rows:
        if any(not str(row.get(key, "")).isdigit() or int(row[key]) <= 0
               for key in ("positionId", "instrumentId")):
            raise ValueError("RISK_POSITION_IDENTITY_UNPROVEN")
        if _number(row.get("units")) <= 0 or not isinstance(row.get("isBuy"), bool):
            raise ValueError("RISK_POSITION_SIZE_OR_SIDE_UNPROVEN")
        ids.append(str(row["positionId"]))
        if closed:
            _stamp(row.get("closeTimestamp") or row.get("closeTime"))
            if _number(row.get("closeRate")) <= 0:
                raise ValueError("RISK_HISTORY_CLOSE_PRICE_UNPROVEN")
            if "netProfit" in row and row["netProfit"] is not None:
                _number(row["netProfit"])
    if not closed and len(set(ids)) != len(ids):
        raise ValueError("RISK_POSITION_ROWS_AMBIGUOUS")


def _position_rows(broker, pnl):
    """Keep all positions, including copied/short rows; never silently drop one."""
    broker._validate_pnl_schema(pnl)
    cp = pnl["clientPortfolio"]
    original = list(cp["positions"])
    for mirror in cp.get("mirrors") or []:
        if "positions" not in mirror:
            raise ValueError("RISK_MIRROR_POSITIONS_UNPROVEN")
        original.extend(mirror["positions"])
    if any(not isinstance(row, dict) for row in original):
        raise ValueError("RISK_POSITIONS_SCHEMA_UNPROVEN")
    rows = broker._all_positions(pnl)
    _validate_rows(rows)
    if len(rows) != len(original):
        raise ValueError("RISK_POSITION_ROWS_AMBIGUOUS")
    return rows


def collect(broker, *, local_timezone="Europe/Berlin", max_pages=12, clock=None):
    """Capture fresh API evidence; errors stay explicit and never become zero."""
    clock = clock or _now
    started = clock()
    zone = ZoneInfo(local_timezone)
    local_day = started.astimezone(zone).date()
    beginning = datetime.combine(local_day, time(), zone).astimezone(timezone.utc)
    environment = "DEMO" if broker.paper else "LIVE"
    result = {"schema_version": 1, "kind": KIND, "started_at": started.isoformat(),
        "day": local_day.isoformat(), "local_timezone": local_timezone,
        "day_start_utc": beginning.isoformat(), "environment": environment,
        "observed_basis": BASIS, "observed_scope": None, "observations": {},
        "collection_errors": [], "broker_actions": [], "new_period_allowed": False}
    observations = result["observations"]

    def capture(name, fn):
        stamp = clock().isoformat()
        try:
            data = fn()
            observations[name] = {"status": "OK", "requested_at": stamp,
                "received_at": clock().isoformat(), "data": data, "sha256": _digest(data)}
            return data
        except Exception as exc:
            # Never echo arbitrary HTTP/credential exception text into exports.
            code = str(exc) if isinstance(exc, ValueError) and str(exc).startswith("RISK_") else type(exc).__name__
            observations[name] = {"status": "ERROR", "requested_at": stamp,
                "received_at": clock().isoformat(), "error_code": code}
            result["collection_errors"].append("RISK_COLLECT_" + name.upper() + "_FAILED")
            return None

    def identity():
        profile = broker._request("GET", "/api/v1/me")
        broker._bind_account_identity(profile)
        return {"scope": ["etoro", environment, broker.account_fingerprint()]}

    initial = capture("identity_before", identity)
    if initial:
        result["observed_scope"] = initial["scope"]
        def account():
            raw = broker._get_aggregate(force=True)
            totals = raw.get("accountTotals") or {}
            if raw.get("accountCurrency") != "USD":
                raise ValueError("RISK_CURRENCY_UNPROVEN")
            equity = _number(totals.get("accountTotalValue"))
            cash = _number(totals.get("accountAvailableCash"))
            if equity <= 0:
                raise ValueError("RISK_EQUITY_UNPROVEN")
            return {"currency": "USD", "equity": equity, "available_cash": cash,
                    "account_totals": totals, "source_sha256": _digest(raw)}
        capture("account", account)
        capture("positions", lambda: {"complete": True,
            "rows": _position_rows(broker, broker._pnl(force=True))})
        # API minDate is a date: start one UTC day earlier if local midnight
        # falls on the prior UTC day; preserve rows instead of inventing P&L.
        capture("closed_trade_history", lambda: broker.trade_history_snapshot(
            beginning.date().isoformat(), max_pages=max_pages, force=True))
        final = capture("identity_after", identity)
        if final and initial != final:
            result["collection_errors"].append("RISK_ACCOUNT_CHANGED_DURING_COLLECTION")
    result["completed_at"] = clock().isoformat()
    result["missing_for_new_period"] = [{"code": k, "detail": v} for k, v in GAPS.items()]
    result["assessment"] = review_evidence(result, now=clock())
    return result


def review_evidence(evidence, *, expected_scope=None, today=None, now=None):
    """Current account proof only. Never a self-issued historical checkpoint."""
    from risk_basis_review import scope
    reasons = []
    now = now or _now()
    try:
        if evidence.get("kind") != KIND or evidence.get("schema_version") != 1:
            raise ValueError("RISK_EVIDENCE_SCHEMA_UNPROVEN")
        began, ended = _stamp(evidence["started_at"]), _stamp(evidence["completed_at"])
        if not 0 <= (now - began).total_seconds() <= MAX_AGE_SECONDS or not began <= ended <= now:
            reasons.append("RISK_EVIDENCE_STALE_OR_FUTURE")
        zone = ZoneInfo(evidence["local_timezone"])
        day = str(now.astimezone(zone).date())
        beginning = datetime.combine(now.astimezone(zone).date(), time(), zone).astimezone(timezone.utc)
        if _stamp(evidence["day_start_utc"]) != beginning:
            reasons.append("RISK_EVIDENCE_DAY_BOUNDARY_MISMATCH")
        if evidence.get("day") != day or (today and str(today) != day):
            reasons.append("RISK_EVIDENCE_DAY_MISMATCH")
        observed = scope(evidence.get("observed_scope"))
        if (not observed or observed[0] != "etoro" or observed[1] != evidence.get("environment")
                or (expected_scope and observed != scope(expected_scope))):
            reasons.append("RISK_EVIDENCE_ACCOUNT_MISMATCH")
        if evidence.get("observed_basis") != BASIS:
            reasons.append("RISK_EVIDENCE_BASIS_MISMATCH")
        rows = evidence.get("observations") or {}
        for name in ("identity_before", "account", "positions", "closed_trade_history", "identity_after"):
            record = rows.get(name) or {}
            if record.get("status") != "OK" or record.get("sha256") != _digest(record.get("data")):
                reasons.append("RISK_EVIDENCE_" + name.upper() + "_UNPROVEN")
                continue
            if not began <= _stamp(record["requested_at"]) <= _stamp(record["received_at"]) <= ended:
                reasons.append("RISK_EVIDENCE_OBSERVATION_TIME_INVALID")
        if reasons:
            raise ValueError("RISK_EVIDENCE_INCOMPLETE")
        for name in ("identity_before", "identity_after"):
            if scope(rows[name]["data"].get("scope")) != observed:
                reasons.append("RISK_ACCOUNT_CHANGED_DURING_COLLECTION")
        account = rows["account"]["data"]
        if account.get("currency") != "USD" or _number(account.get("equity")) <= 0:
            reasons.append("RISK_CURRENCY_OR_EQUITY_UNPROVEN")
        _number(account.get("available_cash"))
        for name in ("positions", "closed_trade_history"):
            data = rows[name]["data"]
            if data.get("complete") is not True or not isinstance(data.get("rows"), list):
                reasons.append("RISK_EVIDENCE_" + name.upper() + "_INCOMPLETE")
                continue
            _validate_rows(data["rows"], closed=name == "closed_trade_history")
        history = rows["closed_trade_history"]["data"]
        if history.get("min_date") != str(beginning.date()) or history.get("truncated") not in (None, False):
            reasons.append("RISK_HISTORY_DAY_COVERAGE_UNPROVEN")
        reasons.extend(evidence.get("collection_errors") or [])
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        if not reasons:
            reasons.append(str(exc) if str(exc).startswith("RISK_") else "RISK_EVIDENCE_INVALID")
    return {"status": "CURRENT_ACCOUNT_CONFIRMED" if not reasons else "REVIEW_REQUIRED",
        "reason_codes": list(dict.fromkeys(reasons)), "new_period_allowed": False,
        "detail": ("Aktuelles Konto lesend abgeglichen. Der historische Tagesbeginn bleibt separat zu belegen."
                   if not reasons else "Kontoabgleich unvollstaendig oder veraltet; keine Risikoreparatur freigegeben.")}


def plan_new_period(raw, evidence, *, now=None):
    """Explicit unsupported boundary: no synthetic daily base or zero costs."""
    assessment = review_evidence(evidence, now=now)
    reasons = list(assessment["reason_codes"]) + list(GAPS)
    if raw.get("pending_persistence_operations"):
        reasons.append("RISK_PERSISTENCE_UNCONFIRMED")
    return {"status": "REVIEW_REQUIRED", "method": "NEW_PERIOD_NOT_PROVEN",
        "reason_codes": reasons, "changes": {}, "new_period_allowed": False,
        "detail": "Die aktuelle Equity ersetzt keinen Tagesstartbeleg. Tagesverluste und alte Belege bleiben unveraendert.",
        "required_evidence": [{"code": k, "detail": v} for k, v in GAPS.items()],
        "next_action": "Kontoabgleich exportieren. Fuer einen neuen Risikobeginn wird ein Kontoauszug dieses Trading-Kontos mit Bewertungszeitpunkt/Zeitzone, allen Tagesbuchungen und Kosten benoetigt."}


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description="eToro-Risiko: nur Konto lesen, keine Orders oder Zustandsaenderung")
    parser.add_argument("--collect", action="store_true", help="Frische GET-Abfragen mit lokal gespeicherten Schluesseln")
    parser.add_argument("--environment", choices=("DEMO", "LIVE"), default="DEMO")
    parser.add_argument("--evidence", type=Path, help="Vorhandenen Kontoabgleich offline pruefen")
    parser.add_argument("--state", type=Path, default=Path(__file__).parent / "risk_state_etoro.json")
    parser.add_argument("--output", type=Path, help="Neue JSON-Belegdatei; existierende Dateien werden nicht ueberschrieben")
    args = parser.parse_args(argv)
    if bool(args.collect) == bool(args.evidence):
        parser.error("Genau --collect oder --evidence angeben")
    # Read bytes only: loading RiskState would perform automatic daily migration.
    original = args.state.read_bytes()
    raw = json.loads(original)
    previous_changed = False
    if args.collect:
        import config
        evidence = collect(readonly_broker(environment=args.environment),
            local_timezone=str(getattr(config, "LOCAL_TIMEZONE", "Europe/Berlin")))
    else:
        saved = json.loads(args.evidence.read_text(encoding="utf-8"))
        evidence = saved.get("evidence", saved)
        previous_changed = saved.get("state_changed_during_collection") is True
    changed = args.state.read_bytes() != original or previous_changed
    if changed:
        evidence.setdefault("collection_errors", []).append("RISK_STATE_CHANGED_DURING_COLLECTION")
    assessment = review_evidence(evidence)
    out = {"evidence": evidence, "state_sha256": sha256(original).hexdigest(),
           "state_changed_during_collection": changed,
           "current_account_review": assessment, "new_period_plan": plan_new_period(raw, evidence)}
    text = json.dumps(out, ensure_ascii=False, indent=2, allow_nan=False)
    if args.output:
        # O_EXCL also refuses symlinks and preserves every existing state/evidence.
        import os
        fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text + "\n")
            handle.flush()
            os.fsync(handle.fileno())
    print(text)
    return 0 if assessment["status"] == "CURRENT_ACCOUNT_CONFIRMED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
