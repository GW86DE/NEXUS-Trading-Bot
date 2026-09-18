"""Display-only broker identities. Never infer account ownership from a symbol.

Trade rows use their stored environment, not today's configuration. Unbound
historical rows receive separate keys; they are not a fictional shared account.
"""
from __future__ import annotations
import re
from ledger_result import confirmed_net, finite_number


def environment(row):
    observed = []
    for key in ("environment", "broker_environment", "modus", "mode"):
        value = str(row.get(key) or "").upper()
        if value in {"DEMO", "PAPER", "LIVE"}:
            observed.append("DEMO" if value in {"DEMO", "PAPER"} else "LIVE")
    if type(row.get("paper")) in (bool, int) and row.get("paper") in (0, 1):
        observed.append("DEMO" if row["paper"] else "LIVE")
    if len(set(observed)) > 1:
        return "CONFLICT"
    return observed[0] if observed else "UNKNOWN"


def context(row, *, broker=""):
    name = str(row.get("broker") or broker or "unknown").lower()
    if name not in {"etoro", "okx"}:
        name = "unknown"
    env = environment(row)
    account = str(row.get("broker_account_fingerprint") or row.get("account_fingerprint") or "")
    # Hash labels are never API secrets. Invalid values are not exposed.
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", account):
        account = ""
    ccy = str(row.get("waehrung") or row.get("trade_quote_ccy") or row.get("currency") or "UNKNOWN").upper()
    if not re.fullmatch(r"[A-Z0-9]{2,12}", ccy):
        ccy = "UNKNOWN"
    anchor = str(row.get("trade_id") or row.get("record_id") or row.get("decision_id") or "unbound")
    domain = f"{name}:{env.lower()}:{account or 'unbound-'+anchor}"
    title = {"etoro": "eToro · Aktien", "okx": "OKX · Spot", "unknown": "Broker unbekannt"}[name]
    env_label = {"DEMO": "DEMO", "LIVE": "LIVE", "CONFLICT": "Umgebung widersprüchlich", "UNKNOWN": "Umgebung unbekannt"}[env]
    return {"broker": name, "environment": env, "account": account,
            "account_label": account[:8] if account else "Kontobindung fehlt",
            "currency": ccy, "domain_key": domain, "group_key": domain+":"+ccy,
            "label": f"{title} · {env_label} · Konto {account[:8] if account else 'unbekannt'}",
            "bound": bool(account and env in {"DEMO", "LIVE"} and name != "unknown")}


def match_position(trade, positions):
    """Exact row linkage for protection overlays; never symbol-only."""
    tc = context(trade)
    if tc["broker"] not in {"okx", "etoro"} or not tc["bound"]:
        return {}
    matches = []
    for position in positions:
        pc = context(position, broker=tc["broker"])
        if pc["domain_key"] != tc["domain_key"] or not pc["bound"]:
            continue
        if tc["broker"] == "etoro":
            pid = str(trade.get("broker_position_id") or "")
            if (pid and pid in {str(x) for x in position.get("owned_position_ids", [])}
                    and str(trade.get("entry_order_id") or "")
                    in {str(x) for x in position.get("order_ids", [])}):
                matches.append(position)
            continue
        if str(position.get("inst_id") or "") != str(trade.get("broker_position_id") or ""):
            continue
        by_id = position.get("trade_id") and str(position["trade_id"]) == str(trade.get("trade_id") or "")
        order = str(position.get("entry_order_id") or position.get("order_id") or "")
        by_order = order and order == str(trade.get("entry_order_id") or "")
        if by_id or by_order:
            matches.append(position)
    return dict(matches[0]) if len(matches) == 1 else {}


def trade_groups(rows):
    """Independent monetary series per broker/environment/account/currency."""
    grouped = {}
    for row in rows:
        if row.get("accounting_kind")=="RESIDUAL":
            continue
        c = context(row)
        row["display_context"] = c
        g = grouped.setdefault(c["group_key"], {**c, "rows": [], "curve": [], "gross_curve": []})
        g["rows"].append(row)
    out = []
    for group in grouped.values():
        data = group.pop("rows")
        closed = [r for r in data if r.get("ausgestiegen_am")]
        valued = [r for r in closed if confirmed_net(r)]
        # Unknown currencies are not numerically comparable.
        evaluable = valued if group["currency"] != "UNKNOWN" and group["environment"] != "CONFLICT" else []
        total = 0.0
        for row in sorted(evaluable, key=lambda r:(str(r.get("ausgestiegen_am") or ""),int(r.get("trade_id") or 0))):
            total += float(row["netto_pnl"])
            group["curve"].append({"zeit": row["ausgestiegen_am"], "wert": round(total,8),
                                   "pnl": float(row["netto_pnl"]), "trade_id": row.get("trade_id"),
                                   "symbol": row.get("symbol"), "waehrung": group["currency"]})
        fees = [finite_number(r.get("gebuehren")) for r in evaluable]
        gross_rows = [r for r in closed if finite_number(r.get("brutto_pnl")) is not None]
        if group["currency"] == "UNKNOWN" or group["environment"] == "CONFLICT":
            gross_rows = []
        gross_total = 0.0
        for row in sorted(gross_rows, key=lambda r:(str(r.get("ausgestiegen_am") or ""),int(r.get("trade_id") or 0))):
            gross_total += float(row["brutto_pnl"])
            group["gross_curve"].append({"zeit": row["ausgestiegen_am"], "wert": round(gross_total, 8),
                "pnl": float(row["brutto_pnl"]), "trade_id": row.get("trade_id"),
                "symbol": row.get("symbol"), "waehrung": group["currency"]})
        group["gross_missing"] = len(closed) - len(gross_rows)
        group.update(offen=sum(not r.get("ausgestiegen_am") and r.get("reconciliation_status")=="CONFIRMED_OPEN" for r in data),
                     klaerung=sum(not r.get("ausgestiegen_am") and r.get("reconciliation_status")!="CONFIRMED_OPEN" for r in data),
                     geschlossen=len(closed), bewertbar=len(evaluable), ohne_bestaetigtes_netto=len(closed)-len(evaluable),
                     summe_netto=round(total,8) if evaluable else None,
                     gebuehren=round(sum(fees),8) if fees and all(v is not None for v in fees) else None)
        out.append(group)
    return sorted(out, key=lambda g:g["group_key"])
