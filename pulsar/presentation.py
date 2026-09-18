"""Read-only PULSAR UI projection. Financial results stay in trade_ledger."""
from __future__ import annotations

import json
import time
from . import control, research


def snapshot():
    from .requirements import capabilities
    from .evidence import REVISION
    from .research_history import status as history_status
    state = control.settings()
    proposals = control.proposals()
    # Never disclose approval nonce hashes or Telegram identity to a browser.
    public = [{k: r[k] for k in ("id", "week", "symbol", "account", "environment", "plan",
                                 "plan_hash", "status", "created", "expires", "execution", "reason", "decision_id")}
              for r in proposals]
    cards = [{k: v for k, v in r.items() if k != "packet"} for r in research.latest_cards()]
    for card in cards:
        if not card.get("text_source") or card.get("rules_version") != REVISION:
            # A migrated 9.8.4 cache can contain the old canned summaries.
            # Keep its observed numbers, but do not present those texts as a
            # successful individual review after upgrading.
            card.update(text_source="DATENUEBERSICHT", risks=[], claims=[],
                thesis=f"{card['symbol']}: gespeicherter Aufmerksamkeitsstand vor dem Update.",
                analysis_status="Aktualisierte Individualpruefung steht aus; Beobachten aktivieren",
                precheck={}, analysis={}, countercheck={}, eligible=False, score=None,
                state="BEOBACHTUNG")
    status = research.cached("status", stale=True)
    selection_hit = research.cached("candidate_selection", stale=True)
    selection = ({**selection_hit["data"], "stale": selection_hit["expires"] <= time.time()}
                 if selection_hit else {})
    ids = [r["decision_id"] for r in proposals if r["decision_id"]]
    trades = []
    if ids:
        import trade_ledger
        trade_ledger.init_ledger()
        with trade_ledger._connect() as con:
            trades = [dict(r) for r in con.execute(
                f"SELECT * FROM trades WHERE decision_id IN ({','.join('?' for _ in ids)}) "
                "AND broker='etoro' AND superseded_by IS NULL ORDER BY trade_id DESC", ids)]
        domains = {(r["decision_id"], r["account"], r["environment"]) for r in proposals}
        trades = [r for r in trades if (r["decision_id"], r["broker_account_fingerprint"],
                   "DEMO" if r["paper"] else "LIVE") in domains]
    closed = [t for t in trades if t.get("ausgestiegen_am")]
    # 10.5.0: Vorwaertsmessung, Demo-Handelsbilanz und Quellenstand fuer die Seite.
    try:
        from .measurement import summary as measurement_summary
        measurement = measurement_summary()
    except Exception as exc:
        measurement = {"error": type(exc).__name__, "total": 0, "verdict": {"status": "ZU_WENIG_DATEN", "reason": "Messung nicht lesbar"}}
    confirmed = {"CONFIRMED", "BROKER_CONFIRMED", "KNOWN", "USER_CONFIRMED", "CASH_DELTA_CONFIRMED"}
    netto = {}
    wins = losses = open_net = 0
    for t in closed:
        if t.get("fee_quality") in confirmed and t.get("netto_pnl") is not None:
            try:
                value = float(t["netto_pnl"])
            except (TypeError, ValueError):
                open_net += 1
                continue
            netto[t.get("waehrung") or "?"] = netto.get(t.get("waehrung") or "?", 0.0) + value
            wins += value > 0
            losses += value <= 0
        else:
            open_net += 1
    demo_trades = {"closed": len(closed), "open": len([t for t in trades if not t.get("ausgestiegen_am")]),
                   "wins": wins, "losses": losses, "net_unknown": open_net, "net_by_currency": netto,
                   "detail": "Verbuchte PULSAR-Trades (Demo) mit bestaetigtem Nettoergebnis; UNKNOWN bleibt UNKNOWN."}
    try:
        from .short_interest import status as finra_status
        finra = finra_status()
    except Exception as exc:
        finra = {"state": "unknown", "detail": type(exc).__name__}
    return {"mode": state["mode"], "revision": state["revision"], "tradestie": bool(state["tradestie"]),
        "stocktwits": bool(state.get("stocktwits")), "finra": bool(state.get("finra")),
        "measurement": measurement, "demo_trades": demo_trades, "finra_status": finra,
        "rules_version": REVISION,
        "web_search": bool(state["web_search"]), "usage": research.usage_summary(),
        "history": history_status(),
        "coverage": (research.cached("coverage", stale=True) or {}).get("data", {}),
        "candidate_selection": selection,
        "optional_sources": research.social_source_status(),
        "worker_fresh": bool(state["heartbeat"] and 0 <= time.time()-state["heartbeat"] <= 30),
        "worker_at": state["heartbeat"], "status": status["data"] if status else {},
        "cards": cards, "proposals": public, "rules": control.RULES,
        "capabilities": capabilities(),
        "universe_guests": research.universe_guests(),
        "open_trades": [t for t in trades if not t.get("ausgestiegen_am")],
        "closed_trades": closed[:10], "archive": closed,
        "source_note": "ApeWisdom und Tradestie liefern korrelierte Reddit-Aggregate, StockTwits eine zweite Social-Familie, "
                       "X begrenzte Stichproben. Relatives Volumen aus Quote/Stundenkerzen ist der frueheste Ausloeser. "
                       "Ein Hype-Kandidat braucht Ausloeser, zwei von drei Bestaetigungen und mindestens eine Social-Familie; "
                       "Social-Quellen entscheiden nie allein und geben keine Order frei.",
        "limits_note": "1 Nominierung je Kalendertag (Europe/Berlin), hoechstens 1 offener oder schwebender PULSAR-Trade, "
                       "Zeitstop 10 Handelstage."}


def export_json():
    return json.dumps(snapshot(), ensure_ascii=False, indent=2, allow_nan=False, default=str)
