"""Read-only Dashboard- und Logbuchdaten fuer die WebUI."""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import config

ROOT = Path(__file__).resolve().parents[1]
logger = logging.getLogger(__name__)


def _json(name: str, default):
    path = ROOT / name
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default
    except Exception:
        return default


def _runtime(name: str) -> dict:
    data = _json(name, {})
    if not isinstance(data, dict):
        data = {"state_error": "RUNTIME_FORMAT_INVALID"}
    # Der Aktienkern nennt das Feld broker_connected, der Kryptokern online.
    broker_online = bool(data.get("broker_connected",
                                  data.get("online", False)))
    try:
        ts = datetime.fromisoformat(str(data.get("last_heartbeat") or "").replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        raw_age = (datetime.now(timezone.utc) - ts).total_seconds()
        age = max(0.0, raw_age) if raw_age >= -5 else None
    except Exception:
        age = None
    data["heartbeat_age_seconds"] = age
    worker_alive = bool(data.get("running") and age is not None and age <= 180)
    data["worker_alive"] = worker_alive
    data["broker_online"] = broker_online
    data["online"] = bool(worker_alive and broker_online)
    data["status_aktuell"] = worker_alive
    # 9.7: Eine alte Bereitschaft darf nach Worker-/Broker-Ausfall niemals
    # weiterhin als "KÄUFE FREI" erscheinen. Die persistierte Detaildiagnose
    # bleibt sichtbar, aber die effektive Freigabe ist an die lebende Domäne
    # gebunden.
    ready = data.get("stock_trading_ready")
    gate = data.get("risk_manager_buy_gate")
    if isinstance(ready, dict) and isinstance(gate, dict) and gate.get("blocked") is True:
        ready = dict(ready)
        reason = str(gate.get("reason") or "Der Risikomanager sperrt neue Käufe.")
        ready.update(kaeufe_erlaubt=False, trading_ready=False, grund=reason,
                     offen=list(dict.fromkeys([reason, *(ready.get("offen") or [])])),
                     risk_manager_forced_block=True)
        data["stock_trading_ready"] = ready
    if isinstance(ready, dict) and ready and not data["online"]:
        ready = dict(ready)
        ready["kaeufe_erlaubt"] = False
        ready["grund"] = ("Aktienworker offline" if not worker_alive
                           else "eToro-Broker offline")
        offen = list(ready.get("offen") or [])
        if ready["grund"] not in offen:
            offen.insert(0, ready["grund"])
        ready["offen"] = offen
        ready["runtime_forced_block"] = True
        data["stock_trading_ready"] = ready
    return data


def core_online() -> bool:
    return bool(_runtime("runtime_status.json").get("online")
                or _runtime("runtime_status_okx.json").get("online"))


def _fmt_market_time(value) -> str:
    if not value:
        return ""
    try:
        zone = ZoneInfo(str(getattr(config, "LOCAL_TIMEZONE", "Europe/Berlin")))
        return value.astimezone(zone).strftime("%d.%m.%Y %H:%M %Z")
    except Exception:
        return str(value)


def _decision_storage() -> dict:
    filename = str(getattr(config, "DECISION_DB_FILE", "decision_history.sqlite"))
    path = ROOT / filename
    rows = 0
    error = None
    if path.exists():
        try:
            con = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=3)
            try:
                rows = int(con.execute("SELECT COUNT(*) FROM decisions").fetchone()[0])
            finally:
                con.close()
        except Exception:
            rows = None
            error = "DECISION_STORAGE_UNREADABLE"
    return {
        "file": filename, "path": str(path), "exists": path.exists(), "rows": rows,
        "error": error, "complete": error is None,
        "size_bytes": path.stat().st_size if path.exists() else 0,
        "mirror": "decision_journal.jsonl",
        "scope": ("Gespeichert werden ernsthafte Kaufkandidaten ab technischem Signal "
                  "einschliesslich Freigaben, Ablehnungen und Begruendungen. Ein normaler "
                  "Scan ohne Signal erzeugt bewusst keinen Entscheidungsdatensatz."),
    }


def dashboard() -> dict:
    from bot_zustand import BotZustand
    from decision_analytics import summary
    from decision_analytics import latest
    from market_calendar import sitzungsstatus
    from news_sources import source_health
    from settings_migration import bericht as migration_report
    market = sitzungsstatus()
    try:
        from pi_system import metrics, health_flags
        pi = metrics(ROOT)
        pi["warnings"] = health_flags(ROOT)
    except Exception:
        pi = {"warnings": ["Pi-/Linux-Telemetrie nicht lesbar"]}
    try:
        from notifier import telegram_status
        telegram = telegram_status(active=False)
        telegram["control"] = _json("telegram_control_status.json", {})
    except Exception:
        telegram = {"configured": False, "control": {}}
    runtimes = {"etoro": _runtime("runtime_status.json"), "okx": _runtime("runtime_status_okx.json")}
    payload = {
        "version": getattr(config, "VERSION_NEXUS", ""),
        "time": datetime.now(timezone.utc).isoformat(),
        "bot": BotZustand(str(ROOT / "bot_zustand.json")).momentaufnahme(),
        "etoro": runtimes["etoro"],
        "okx": runtimes["okx"],
        "stock_market": {
            "open": bool(market.get("offen")), "phase": market.get("phase"),
            "reason": market.get("grund"),
            "open_time": _fmt_market_time(market.get("oeffnung")),
            "close_time": _fmt_market_time(market.get("schluss")),
            "next_open": _fmt_market_time(market.get("naechste_oeffnung")),
            "regular_hours_ny": "09:30–16:00 America/New_York",
            "early_close": bool(market.get("verkuerzt")),
        },
        "decisions": summary(),
        "decisions_by_broker": decision_summary_scoped(),
        "broker_contexts": broker_contexts(runtimes),
        "accounting": accounting_overview(),
        "decision_storage": _decision_storage(),
        "recent_decisions": latest(15),
        # v8.1.4: Die Kryptoseite hatte auf dem Dashboard keine eigene Karte.
        "okx_detail": _okx_detail(),
        "news": source_health(),
        "earnings_calendar": __import__("earnings_calendar_status").status(),
        "universe": _json("universe_state.json", {}),
        "core_volume_20": _core_volume_status(),
        "dynamic_30": _dynamic_30_status(),
        "crypto_strategy": _crypto_strategy_status(),
        "etoro_reconciliation": _etoro_reconciliation_status(),
        "crypto_positions": _json("crypto_positions.json", {"positionen": []}),
        # v8.1.5: Bis 8.1.4 stand die Zahl der OKX-Positionen ZWEIMAL auf der
        # Hauptseite und die der eToro-Positionen kein einziges Mal.
        "stock_positions": _json("stock_positions.json",
                                 {"positionen": [], "kurse_verfuegbar": False}),
        "migration": migration_report(ROOT),
        "pi": pi,
        "telegram": telegram,
    }
    from webui.diagnostics import operations_snapshot
    payload["operations"] = operations_snapshot(payload)
    return payload


def _core_volume_status() -> dict:
    try:
        import core_volume_20
        return core_volume_20.load()
    except Exception as exc:
        return {"status": "STALE", "error": type(exc).__name__, "items": []}


def _crypto_strategy_status() -> dict:
    try:
        import crypto_strategy_mode
        return crypto_strategy_mode.status()
    except Exception as exc:
        return {"active_mode": "CRYPTO_PAUSED", "error": type(exc).__name__}


def _eigentum_status(row: dict) -> str:
    """Eigentum haengt AUSSCHLIESSLICH an brokerseitig vergebenen IDs.

    Kein Wert aus dieser Funktion darf sich durch ein Softwareupdate
    aendern -- weder Strategieversion noch Parameter-Hash gehen ein.
    Kuenstliche ``okx-entry:<orderId>``-Ersatz-IDs zaehlen nicht als Fill.
    """
    import json as _json_mod

    fills: set[str] = set()
    roh = row.get("entry_fill_ids_json") or "[]"
    try:
        werte = _json_mod.loads(roh) if isinstance(roh, str) else roh
        fills.update(str(x) for x in (werte or []) if str(x).strip())
    except (TypeError, ValueError):
        pass
    if str(row.get("entry_fill_id") or "").strip():
        fills.add(str(row.get("entry_fill_id")))
    echte = {x for x in fills if not x.startswith("okx-entry:")}
    if (str(row.get("entry_order_id") or "").strip()
            and str(row.get("client_order_id") or "").strip() and echte):
        return "BEWIESEN"
    if echte or str(row.get("entry_order_id") or "").strip():
        return "UNVOLLSTAENDIG"
    return "KEIN_BEWEIS"


def _etoro_reconciliation_status() -> dict:
    try:
        import etoro_reconciliation
        return etoro_reconciliation.status()
    except Exception as exc:
        return {"active": [], "error": type(exc).__name__}


def _dynamic_30_status() -> dict:
    try:
        import crypto_dynamic_30
        return crypto_dynamic_30.load()
    except Exception as exc:
        return {"status": "STALE", "error": type(exc).__name__, "items": []}


def trade_analysis(*, broker: str = "", tage: int = 90) -> dict:
    """Rein sachliche Ledger-Auswertung fuer die deutsche Trade-Seite."""
    from trade_ledger import trade_liste
    from ledger_result import confirmed_net, fees_confirmed, finite_number, result_status

    broker_name = str(broker or "").strip().lower()
    if broker_name not in ("", "okx", "etoro"):
        broker_name = ""
    lookback = max(1, min(3650, int(tage)))
    rows = trade_liste(broker=broker_name, tage=lookback, limit=500)

    # Interne Parameter-Snapshots sind fuer die Uebersicht unnoetig. Modus,
    # Version und Hash bleiben sichtbar; Geheimnisse werden nie gespeichert.
    erlaubt = {
        "trade_id", "decision_id", "link_status", "enter_tag", "broker",
        "asset_type", "symbol", "waehrung", "strategie_version",
        "entry_strategy_mode", "strategy_parameter_hash", "marktphase", "paper",
        "eingestiegen_am", "einstieg_preis", "einstieg_referenz", "menge",
        "einstieg_gebuehr", "ausgestiegen_am", "ausstieg_preis", "exit_grund",
        "brutto_pnl", "gebuehren", "slippage_geschaetzt", "netto_pnl", "fee_quality",
        "entry_fee_quality", "exit_fee_quality", "accounting_kind", "entry_cost_basis",
        "broker_reported_net_pnl", "broker_reported_fees", "broker_result_currency",
        "broker_result_quality", "broker_result_receipt_hash",
        "haltedauer_minuten", "mfe_pct", "mae_pct", "notiz",
        "broker_position_id", "entry_order_id", "entry_fill_id",
        "entry_fill_ids_json", "client_order_id", "ownership_status",
        "broker_account_fingerprint", "reconciliation_status",
        "reconciliation_updated_at", "protection_algo_id",
        "protection_client_order_id", "protection_status", "protection_detail",
        "exit_order_id", "exit_fill_ids_json",
    }
    hidden = {"DISMISSED", "ACCOUNT_ASSET_CONFIRMED"}
    clean = [{k: v for k, v in row.items() if k in erlaubt} for row in rows
             if str(row.get("reconciliation_status") or "").upper() not in hidden]
    # A proven remainder remains visible as inventory, not a second sale.
    clean = [row for row in clean if row.get("accounting_kind") != "RESIDUAL"]
    residual_positions = []
    try:
        from okx_residual_inventory import public_inventory
        residual_positions = public_inventory(broker=broker_name)
    except Exception:
        logger.warning("Restbestandsnachweise derzeit nicht lesbar", exc_info=True)
    native_by_id = {row["trade_id"]: row.get("exit_native_json") for row in rows}
    reported_by_id = {row["trade_id"]: row.get("broker_result_detail_json") for row in rows}
    for row in clean:
        try:
            assessment = json.loads(reported_by_id.get(row["trade_id"]) or "{}")
            row["broker_result_assessment"] = {k: assessment[k] for k in (
                "quality", "reason", "confirmed_entry_costs", "computed_gross_pnl",
                "arithmetic_delta", "broker_vs_computed_net_delta", "fee_scope_confirmed")
                if isinstance(assessment, dict) and k in assessment}
        except (ValueError, TypeError):
            row["broker_result_assessment"] = {}
        try:
            native = json.loads(native_by_id.get(row["trade_id"]) or "{}").get("receipt", {})
            # Never expose the original row or arbitrary raw broker response.
            row["native_exit"] = {k: native[k] for k in (
                "native_currency", "inst_id", "gross_quantity", "residual",
                "avg_price", "gross_proceeds", "net_proceeds", "fees_quote",
                "closed_at", "order_ids", "fill_ids", "manual_confirmed") if k in native}
        except (ValueError, TypeError, AttributeError):
            row["native_exit"] = {}
    # Display a verified accounting projection, never relabel native broker cash.
    # Raw ledger fields remain available in the audit; cross-currency results get
    # an explicit EUR valuation label, while ordinary USDC/USD trades keep theirs.
    try:
        import trade_ledger as tl
        from okx_reference_valuation import load_on
        original_by_id = {r['trade_id']:r for r in rows}
        with tl._connect() as con:
            for row in clean:
                if row.get('broker') != 'okx' or not row.get('ausgestiegen_am'):
                    continue
                try:
                    value = load_on(con,original_by_id[row['trade_id']])
                    if not value:continue
                    row['eur_valuation'] = {k:value[k] for k in (
                        'method','quality','net_eur','gross_eur','fees_eur','entry_cost_eur',
                        'closed_at','receipt_hash','cross_currency')}
                    row['eur_valuation']['reference_qualities'] = sorted({f['reference']['quality'] for f in value['flows']})
                    if value['cross_currency']:
                        row['native_entry_currency'] = row['waehrung']
                        row['native_entry_price'] = row['einstieg_preis']
                        row.update(waehrung='EUR',netto_pnl=float(value['net_eur']),brutto_pnl=float(value['gross_eur']),
                            gebuehren=float(value['fees_eur']),entry_cost_basis=float(value['entry_cost_eur']),
                            einstieg_preis=float(value['entry_price_eur']),einstieg_gebuehr=float(value['entry_fee_eur']),
                            ausstieg_preis=float(value['exit_price_eur']),fee_quality='KNOWN')
                except (ValueError,KeyError,TypeError,ArithmeticError):
                    logger.warning('EUR-Referenzbewertung fuer Trade %s nicht validierbar',row.get('trade_id'))
    except Exception:
        logger.debug('EUR-Referenzbewertungen noch nicht lesbar',exc_info=True)
    offen = [row for row in clean if not row.get("ausgestiegen_am") and
             str(row.get("reconciliation_status") or "").upper() == "CONFIRMED_OPEN"]
    klaerung = [row for row in clean if not row.get("ausgestiegen_am") and
                str(row.get("reconciliation_status") or "").upper() != "CONFIRMED_OPEN"]
    geschlossen = [row for row in clean if row.get("ausgestiegen_am")]
    try:
        from trade_chart_data import live_metrics_for_trades
        offen = live_metrics_for_trades(offen)
    except Exception:
        logger.debug("Livewerte fuer offene Trades nicht abrufbar", exc_info=True)
    from broker_display_context import match_position, trade_groups, context
    positions = [dict(p, broker="okx") for p in (_json("crypto_positions.json", {"positionen": []}).get("positionen") or [])]
    stock_snapshot = _json("stock_positions.json", {"positionen": []})
    positions += [dict(p, broker="etoro") for p in (stock_snapshot.get("positionen") or [])]
    # v9.2: Eigentum, Abgleich und Verwaltung sind drei getrennte Zustaende.
    # Vorher zeigte die Oberflaeche nur EINEN gemischten Status. Eine
    # pausierte, aber lueckenlos bewiesene Botposition sah deshalb aus wie
    # Fremdbestand -- genau der Eindruck vom 31.08.2026.
    for row in clean:
        position = match_position(row, positions)
        if row.get("broker") == "okx" and (position.get("broker_state") or row.get("reconciliation_status") == "BROKER_STATE_UNKNOWN"):
            row["broker_state"] = "BROKER_STATE_UNKNOWN"
            row["protection_status"] = "UNKNOWN_BROKER_STATE"
            row["protection_detail"] = "Bestand ungeklärt; aktueller Schutz nicht bestätigt"
            row["last_confirmed_protection"] = position.get("last_confirmed_protection") or {}
        row["eigentum_status"] = _eigentum_status(row)
        row["abgleich_status"] = str(row.get("reconciliation_status") or "").upper()
        row["verwaltung_status"] = str(position.get("management_status") or "")
        row["strategie_migriert_am"] = str(position.get("strategy_migrated_at") or "")
        row["strategie_migriert_von"] = str(
            position.get("strategy_migrated_from_version") or "")

    fmp_cache = _json("fmp_reference_cache.json", {})
    etoro_runtime = _runtime("runtime_status.json")
    for row in offen:
        if row.get("broker") == "etoro":
            row["fmp_context"] = fmp_display_context(row.get("symbol"), fmp_cache)
        row["eigentum_status"] = _eigentum_status(row)
        row["abgleich_status"] = str(row.get("reconciliation_status") or "").upper()
        position = match_position(row, positions)
        row["verwaltung_status"] = str(position.get("management_status") or "")
        row["management_mode"] = str(position.get("verwaltung") or "UNKNOWN").upper()
        row["management_note"] = str(position.get("verwaltungsnotiz") or position.get("notiz") or "")
        if row.get("broker") == "etoro" and position:
            from etoro_trade_display import enrich
            enrich(row, stock_snapshot)
            row["protection_status"] = str(position.get("protection_status") or "UNKNOWN") if row.get("protection_snapshot_fresh") else "STALE"
            row["protection_detail"] = str(position.get("protection_detail") or "")
            row["protection_evidence"] = protection_display_evidence(position, etoro_runtime)
            row["protection_plan_history"] = position.get("protection_plan_history") or []
            from webui.display_evidence import position_risk_display
            row["account_risk_display"] = position_risk_display(position, etoro_runtime)
        row["exit_state"] = str(position.get("exit_state") or "IDLE")
        row["exit_retry_after"] = str(position.get("exit_retry_after") or "")
        # v9.1: Nur AUTO-Positionen bekommen ein ROI-Ziel gezeigt. Der Kern
        # ruft _strategy_exit ausschliesslich fuer AUTO auf -- bei BEOBACHTEN
        # wartete der Nutzer auf einen Ausstieg, der nie kommt. Genau diesen
        # Zustand setzt der Kern selbst, wenn ein Strategie-Snapshot nicht
        # reproduzierbar ist.
        if row["management_mode"] != "AUTO":
            row["active_roi_price"] = None
            row["active_roi_pct"] = None
    for row in geschlossen:
        try:
            from ledger_result import entry_capital
            basis = entry_capital(row) or 0.0
            row["netto_pnl_pct"] = (round(
                100.0 * float(row["netto_pnl"]) / basis, 6)
                if row.get("netto_pnl") is not None and basis > 0 else None)
        except (TypeError, ValueError):
            row["netto_pnl_pct"] = None
    # 9.7: Nur Ergebnisse mit belastbarer Gebührenqualität werden als
    # bestätigtes Netto aggregiert. Historische/ungeklärte Gebühren bleiben
    # sichtbar, werden aber nicht als exaktes Netto ausgegeben.
    # Read only pre-existing recovery tables. Opening the UI never calls a
    # broker or starts a repair. The worker records its latest exact reason.
    recovery_checks = {}
    try:
        import trade_ledger
        with trade_ledger._connect() as con:
            tables = {x[0] for x in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "okx_closed_result_checks" in tables:
                recovery_checks = {x["trade_id"]: dict(x) for x in con.execute(
                    "SELECT trade_id,checked_at,status,detail FROM okx_closed_result_checks")}
    except Exception:
        logger.debug("Ergebnis-Abgleichstatus noch nicht lesbar", exc_info=True)
    for row in geschlossen:
        row["ergebnis_recherche"] = recovery_checks.get(row["trade_id"], {})
        from webui.display_evidence import closed_result_display
        row.update(closed_result_display(row))
        row["ergebnis_status"] = result_status(row)
        if not confirmed_net(row):
            row["netto_pnl_pct"] = None
            # A completed sale remains closed even when its accounting is
            # incomplete. Make the exact missing receipts reviewable; never
            # turn the result queue into a second sellable position.
            if row.get("broker_account_fingerprint"):
                gaps = []
                if not finite_number(row.get("ausstieg_preis")):
                    gaps.append("Verkaufskurs und Ausfuehrungsbelege der konkreten Verkaufsorder fehlen")
                if row.get("broker") == "okx" and not row.get("exit_order_id"):
                    gaps.append("Verkaufsorder-ID bzw. ausgefuehrter Kindauftrag der Schutzorder noch nicht zugeordnet")
                if not fees_confirmed(row):
                    if row.get("broker_result_quality") == "BROKER_HISTORY_COST_SCOPE_CONFLICT":
                        gaps.append("Broker-Historienergebnis und bestaetigte Einstiegskosten haben unterschiedliche Kostenumfaenge; Abschlusskostenbeleg fehlt")
                    elif row.get("broker") == "etoro" and row.get("entry_fee_quality") == "CONFIRMED":
                        gaps.append("Einstiegskosten bestaetigt; vollstaendige Abschlusskosten einschliesslich externer Gebuehren fehlen")
                    else:
                        gaps.append("Bestaetigte Gebuehrenbelege fuer Kauf und Verkauf fehlen")
                if not finite_number(row.get("einstieg_preis")):
                    gaps.append("Belegter Einstand fehlt")
                native = row.get("native_exit") or {}
                if native.get("native_currency") and native["native_currency"] != row.get("waehrung"):
                    gaps.append("Historischer Wechselkurs fuer den Verkauf in anderer Waehrung fehlt")
                if not gaps:
                    gaps.append("Vollstaendiger Ergebnisbeleg fehlt")
                row["clarification_kind"] = "RESULT"
                row["missing_receipts"] = gaps
                klaerung.append(row)
    vorlaeufig = [row for row in geschlossen
                  if finite_number(row.get("netto_pnl")) is not None
                  and not confirmed_net(row)]
    bewertbar = [row for row in geschlossen if confirmed_net(row)]
    gebuehren_offen = sum(not fees_confirmed(row) for row in geschlossen)
    gewinne = [row for row in bewertbar if float(row["netto_pnl"]) > 0]
    verluste = [row for row in bewertbar if float(row["netto_pnl"]) < 0]

    netto_nach_waehrung: dict[str, float] = {}
    gebuehren_nach_waehrung: dict[str, float] = {}
    for row in bewertbar:
        ccy = str(row.get("waehrung") or "UNBEKANNT").upper()
        netto_nach_waehrung[ccy] = (
            netto_nach_waehrung.get(ccy, 0.0) + float(row["netto_pnl"]))
        gebuehren_nach_waehrung[ccy] = (
            gebuehren_nach_waehrung.get(ccy, 0.0)
            + float(row.get("gebuehren") or 0.0))
    groups = trade_groups(clean)
    for row in offen:
        row["display_context"] = context(row)
    domains = {context(row)["domain_key"] for row in clean}
    mixed_domains = len(domains) > 1
    if mixed_domains:
        netto_nach_waehrung = {}
        gebuehren_nach_waehrung = {}
    einzige_waehrung = len(netto_nach_waehrung) == 1

    kumuliert: dict[str, float] = {}
    kurven: dict[str, list[dict]] = {}
    for row in sorted(bewertbar, key=lambda x: (
            str(x.get("ausgestiegen_am") or ""), int(x.get("trade_id") or 0))):
        ccy = str(row.get("waehrung") or "UNBEKANNT").upper()
        pnl = float(row["netto_pnl"])
        kumuliert[ccy] = kumuliert.get(ccy, 0.0) + pnl
        kurven.setdefault(ccy, []).append({
            "zeit": row.get("ausgestiegen_am"),
            "wert": round(kumuliert[ccy], 8), "pnl": round(pnl, 8),
            "trade_id": row.get("trade_id"), "symbol": row.get("symbol"),
            "waehrung": ccy})
    # Legacy currency-only series survive only when that currency belongs to
    # exactly one complete domain group. No cross-account merging.
    currency_groups = {}
    for group in groups:
        if group["curve"]:
            currency_groups.setdefault(group["currency"], []).append(group)
    kurven = {ccy: values[0]["curve"] for ccy, values in currency_groups.items() if len(values) == 1}
    currency_contexts = {ccy: values[0]["label"] for ccy, values in currency_groups.items() if len(values) == 1}
    kurve = next(iter(kurven.values())) if len(kurven) == 1 else []

    return {
        "generiert_am": datetime.now(timezone.utc).isoformat(),
        "broker": broker_name or "alle", "zeitraum_tage": lookback, "history_limit": 500,
        "context_groups": groups, "mixed_domains": mixed_domains,
        "currency_contexts": currency_contexts,
        "kennzahlen": {
            "offen": len(offen), "klaerung": len(klaerung),
            "ergebnis_klaerung": sum(r.get("clarification_kind") == "RESULT" for r in klaerung),
            "geschlossen": len(geschlossen),
            "bewertbar": len(bewertbar), "vorlaeufig": len(vorlaeufig),
            "gebuehren_offen": gebuehren_offen,
            "ohne_ergebnis": len(geschlossen) - len(bewertbar), "gewinne": len(gewinne),
            "verluste": len(verluste),
            # Verschiedene Quote-Waehrungen werden niemals ohne beobachteten
            # Kreuzkurs addiert. Die WebUI zeigt sie dann getrennt.
            "summe_netto": (round(next(iter(netto_nach_waehrung.values())), 8)
                             if einzige_waehrung else None),
            "summe_nach_waehrung": {
                key: round(value, 8) for key, value in netto_nach_waehrung.items()},
            "trefferquote_pct": (round(100 * len(gewinne) / len(bewertbar), 2)
                                  if bewertbar else None),
            "gebuehren": (round(next(iter(gebuehren_nach_waehrung.values())), 8)
                           if einzige_waehrung else None),
            "gebuehren_nach_waehrung": {
                key: round(value, 8) for key, value in gebuehren_nach_waehrung.items()},
            "offenes_ergebnis": None,
        },
        "offene_trades": offen,
        "klaerungs_trades": klaerung,
        "geschlossene_trades": geschlossen,
        "restbestaende": residual_positions,
        "kumulierte_ergebnisse": kurve,
        "kumulierte_ergebnisse_nach_waehrung": kurven,
        "hinweis": ("Bis zu 500 Verkäufe nach Abschlussdatum im Zeitraum; offene Positionen ohne Zeitfilter. "
                    "Nur vom aktuellen Broker-/Positionsbuchabgleich bestätigte Trades zählen "
                    "als offen. Unklare Altbestände stehen getrennt unter ‚Klärung nötig‘ und "
                    "werden weder automatisch verkauft noch als 0,00 bewertet. Die "
                    "Kerzenansicht verwendet nur abgeschlossene OKX-Kerzen. Nettoergebnisse "
                    "mit unbekannter Gebührenqualität stehen als vorläufig separat und werden "
                    "nicht in bestätigte Summen eingerechnet."),
    }


def decision_log(*, page: int = 1, per_page: int = 15, broker: str = "",
                 asset_type: str = "", status: str = "", source: str = "",
                 symbol: str = "", day: str = "", search: str = "",
                 strategy: str = "", reason: str = "") -> dict:
    db = ROOT / getattr(config, "DECISION_DB_FILE", "decision_history.sqlite")
    page = max(1, int(page)); per_page = max(10, min(100, int(per_page)))
    if not db.exists():
        return {"rows": [], "total": 0, "page": page, "pages": 0}
    clauses, args = [], []
    for column, value in (("broker", broker), ("asset_type", asset_type), ("status", status),
                          ("local_day", day)):
        if value:
            clauses.append(f"{column}=?"); args.append(str(value))
    if symbol:
        clauses.append("symbol LIKE ?"); args.append(f"%{str(symbol).upper()}%")
    if source:
        clauses.append("payload_json LIKE ?"); args.append(f"%{str(source)}%")
    if search:
        # Bound literal search; SQL metacharacters are not extra permissions.
        needle = "%" + str(search)[:200].replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        columns = ("symbol", "reason", "blocked_by", "order_ids_json", "broker_reference_id", "position_ids_json", "CAST(id AS TEXT)")
        clauses.append("(" + " OR ".join(column+" LIKE ? ESCAPE '\\'" for column in columns) + ")")
        args.extend([needle]*len(columns))
    if strategy:
        clauses.append("(strategy_mode=? OR strategy_version=?)")
        args.extend([str(strategy)[:120]]*2)
    if reason:
        needle = "%" + str(reason)[:200].replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        clauses.append("(reason LIKE ? ESCAPE '\\' OR blocked_by LIKE ? ESCAPE '\\')")
        args.extend([needle]*2)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True, timeout=5)
    con.row_factory = sqlite3.Row
    import time
    deadline = time.monotonic()+2.0
    con.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
    try:
        con.execute("BEGIN")
        total = int(con.execute("SELECT COUNT(*) FROM decisions" + where, args).fetchone()[0])
        rows = con.execute(
            "SELECT * FROM decisions" + where + " ORDER BY created_at_utc DESC, id DESC LIMIT ? OFFSET ?",
            [*args, per_page, (page - 1) * per_page],
        ).fetchall()
        row_ids = [int(x["id"]) for x in rows]
        events_by_id, orders_by_id = {}, {}
        detail_truncated = False
        if row_ids:
            marks = ",".join("?" for _ in row_ids)
            try:
                events = con.execute(
                    f"SELECT * FROM decision_events WHERE decision_id IN ({marks}) ORDER BY created_at_utc DESC, id DESC LIMIT 501", row_ids
                ).fetchall()
                detail_truncated = len(events) > 500
                for event in events[:500]:
                    events_by_id.setdefault(int(event["decision_id"]), []).append(dict(event))
                orders = con.execute(
                    f"SELECT * FROM decision_orders WHERE decision_id IN ({marks}) ORDER BY created_at_utc DESC, id DESC LIMIT 501", row_ids
                ).fetchall()
                detail_truncated = detail_truncated or len(orders) > 500
                for order in orders[:500]:
                    orders_by_id.setdefault(int(order["decision_id"]), []).append(dict(order))
            except sqlite3.OperationalError:
                pass  # read-only compatibility with a not-yet-migrated old DB
    finally:
        con.close()
    out = []
    zone = ZoneInfo(str(getattr(config, "LOCAL_TIMEZONE", "Europe/Berlin")))
    def local_display(value) -> str:
        if not value:
            return ""
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(zone).strftime("%d.%m.%Y %H:%M:%S %Z")
        except (TypeError, ValueError):
            return str(value)
    for row in rows:
        item = dict(row)
        raw = item.pop("payload_json", "{}")
        try:
            payload = json.loads(raw)
        except Exception:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        item["sources"] = payload.get("sources") or payload.get("quellen") or []
        item["hauptquelle"] = payload.get("hauptquelle") or item.get("blocked_by") or ""
        item["signal_reason"] = payload.get("signal_reason") or payload.get("reason") or ""
        item["signal_checks"] = payload.get("signal_checks") or {}
        item["metrics"] = {
            key: payload.get(key) for key in (
                "rsi_5m", "tema_5m", "bb_middle_5m", "volume_5m", "atr",
                "spread_pct", "spread_limit_pct", "required_edge_pct",
                "expected_move_pct", "trade_quote_ccy")
            if payload.get(key) is not None
        }
        item["execution_events"] = events_by_id.get(int(item["id"]), [])
        item["orders"] = orders_by_id.get(int(item["id"]), [])
        item["created_at_local"] = local_display(item.get("created_at_utc"))
        item["updated_at_local"] = local_display(item.get("updated_at_utc"))
        for event in item["execution_events"]:
            event["created_at_local"] = local_display(event.get("created_at_utc"))
        # Die damalige Kontobindung liegt im Decision-Payload. Historische
        # Zeilen ohne Beleg bleiben ungebunden; kein heutiges Konto ergaenzen.
        for key in ("account_fingerprint", "broker_account_fingerprint",
                    "environment", "broker_environment"):
            if payload.get(key) is not None:
                item[key] = payload[key]
        from broker_display_context import context
        item["display_context"] = context(item)
        from decision_explanation import explain_decision
        item["decision_explanation"] = explain_decision(item, payload)
        from webui.display_evidence import startup_repetition
        item["startup_repetition"] = startup_repetition(item, payload)
        out.append(item)
    return {"rows": out, "total": total, "page": page,
            "detail_truncated": detail_truncated,
            "pages": (total + per_page - 1) // per_page, "per_page": per_page}


def system_log(*, search: str = "", level: str = "", limit: int = 300) -> list[str]:
    path = ROOT / getattr(config, "LOG_FILE", "trading_bot.log")
    if not path.exists():
        return []
    with path.open("rb") as handle:
        handle.seek(0, 2); size = handle.tell(); handle.seek(max(0, size - 1_500_000))
        text = handle.read().decode("utf-8", errors="replace")
    rows = text.splitlines()
    if search:
        rows = [x for x in rows if str(search).lower() in x.lower()]
    if level:
        rows = [x for x in rows if str(level).upper() in x.upper()]
    return rows[-max(20, min(1000, int(limit))):]


def _universums_diagnose() -> dict:
    """Die Filterzaehlung des letzten Laufs je Broker."""
    aus = {}
    try:
        import okx_status
        lauf = (okx_status.lies().get("letzter_universumslauf") or {})
        if lauf.get("diagnose"):
            aus["okx"] = lauf["diagnose"]
    except Exception:
        __import__("logging").getLogger(__name__).debug(
            "Universumsdiagnose nicht lesbar", exc_info=True)
    return aus


def _okx_detail() -> dict:
    """Guthaben, Positionen und Handelsbereitschaft der Kryptoseite."""
    try:
        import okx_status
        daten = okx_status.lies()
    except Exception:
        return {}
    if not daten:
        return {"vorhanden": False}
    bereit = daten.get("handelsbereitschaft") or {}
    runtime = _runtime("runtime_status_okx.json")
    alter = okx_status.alter_sekunden(daten)
    max_alter = float(getattr(okx_status, "MAX_ALTER_SEKUNDEN", 180.0))
    heartbeat_aktuell = bool(alter is not None and alter <= max_alter)
    status_aktuell = bool(heartbeat_aktuell and runtime.get("worker_alive"))
    verbunden = bool(status_aktuell and runtime.get("broker_online") and
                     (daten.get("online") or daten.get("verbunden")))
    kaeufe_erlaubt = bool(verbunden and bereit.get("kaeufe_erlaubt"))
    grund = str(bereit.get("grund", "") or "")
    offene = list(bereit.get("offen") or [])
    if not verbunden:
        grund = ("OKX-Worker offline oder Broker nicht authentifiziert; "
                 "angezeigte Kontowerte sind nur der letzte bekannte Stand")
        offene = [grund]
    return {
        "vorhanden": True,
        "modus": daten.get("modus", "?"),
        "account_fingerprint": daten.get("account_fingerprint", ""),
        "kapital_waehrung": daten.get("kapital_waehrung", "UNKNOWN"),
        "online": verbunden,
        "status_aktuell": status_aktuell,
        "werte_veraltet": not verbunden,
        "alter_sekunden": alter,
        "handelbares_kapital": daten.get("handelbares_kapital"),
        "gebuehrensatz_pct": daten.get("gebuehrensatz_pct"),
        "guthaben": daten.get("guthaben") or {},
        "exposure": daten.get("exposure") or {},
        "positionen": daten.get("positionen") or [],
        "universum": daten.get("universum") or {},
        "trading_ready": bool(verbunden and bereit.get("trading_ready")),
        "kaeufe_erlaubt": kaeufe_erlaubt,
        "bereitschaft_grund": grund,
        "offene_bedingungen": offene,
        "signal_timeframe": daten.get("signal_timeframe"),
        "safety_wait": bereit.get("safety_wait") or {},
        "candle_quality": daten.get("candle_quality") or [],
    }


_SYMBOL_CACHE: dict = {"zeit": 0.0, "symbole": []}


def bekannte_symbole() -> list[str]:
    """Alle Symbole, die im Logbuch als Wert hervorgehoben werden sollen.

    9.5.6. Georgs Wunsch: im Logbuch sofort erkennen, um welche Aktie oder
    welchen Coin es geht.

    Bewusst wird NICHT nach Grossbuchstaben gesucht -- dann leuchteten INFO,
    WARNING, OKX, USDC, HANDEL und KRYPTO mit. Hervorgehoben wird nur, was
    wirklich ein gefuehrtes Instrument ist.

    Quellen, alle defensiv: das Universum beider Broker, die offenen
    Kryptopositionen und die festen Kernlisten aus der Konfiguration. Faellt
    eine Quelle aus, fehlen nur ein paar Hervorhebungen -- die Seite bleibt
    benutzbar.
    """
    import time as _time
    if _SYMBOL_CACHE["symbole"] and _time.time() - _SYMBOL_CACHE["zeit"] < 60.0:
        return list(_SYMBOL_CACHE["symbole"])

    symbole: set[str] = set()

    def _sammle(werte) -> None:
        for wert in werte or ():
            name = str(wert or "").strip().upper()
            # Ein- und Zweizeichensymbole waeren im Fliesstext zu unruhig.
            if 2 < len(name) <= 12 and name.replace(".", "").isalnum():
                symbole.add(name)

    try:
        import config
        from universe.manager import UniverseManager
        from universe.modelle import UniverseZustand
        manager = UniverseManager(UniverseZustand(
            getattr(config, "UNIVERSE_STATE_FILE", "universe_state.json")))
        for broker in ("okx", "etoro"):
            _sammle(m.symbol for m in manager.zustand.fuer_broker(broker))
            _sammle(manager.kernwerte(broker))
    except Exception:
        logger.debug("Universumssymbole fuer das Logbuch nicht lesbar",
                     exc_info=True)

    try:
        import config
        _sammle(getattr(config, "STOCK_SYMBOLS", ()) or ())
        _sammle(getattr(config, "CRYPTO_CORE_SYMBOLS", ()) or ())
    except Exception:
        logger.debug("Konfigurationssymbole nicht lesbar", exc_info=True)

    try:
        daten = _json("crypto_positions.json", {}) or {}
        _sammle(str((p or {}).get("symbol") or "")
                for p in (daten.get("positionen") or []))
    except Exception:
        logger.debug("Positionssymbole nicht lesbar", exc_info=True)

    try:
        # Auch schon geschlossene Werte: das Logbuch zeigt Vergangenheit, und
        # ZAMA oder BNB sollen dort genauso hervorgehoben sein wie ein heute
        # gefuehrter Wert.
        import trade_ledger
        _sammle(str(zeile.get("symbol") or "")
                for zeile in (trade_ledger.gehandelte_symbole() or ()))
    except Exception:
        logger.debug("Ledgersymbole nicht lesbar", exc_info=True)

    ergebnis = sorted(symbole)
    _SYMBOL_CACHE.update({"zeit": _time.time(), "symbole": ergebnis})
    return list(ergebnis)


def protection_display_evidence(position, runtime):
    """Match a read-only runtime proof by exact account, environment and IDs."""
    from broker_display_context import context
    pc, rc = context(position, broker="etoro"), context(runtime, broker="etoro")
    if not pc["bound"] or pc["domain_key"] != rc["domain_key"]:
        return {}
    expected = {str(value) for value in position.get("owned_position_ids", [])}
    if not expected:
        return {}
    candidates = []
    proofs = runtime.get("protection_evidence") or {}
    if not isinstance(proofs, dict):
        return {}
    for proof in proofs.values():
        if not isinstance(proof, dict):
            continue
        observed = proof.get("observed") or []
        if not isinstance(observed, list):
            continue
        ids = {str(value.get("position_id") or "") for value in observed if isinstance(value, dict)}
        if ids == expected and len(ids) == len(observed):
            candidates.append(proof)
    return dict(candidates[0], runtime_fresh=runtime.get("worker_alive") is True,
                observed_at=runtime.get("last_heartbeat")) if len(candidates) == 1 else {}


def fmp_display_context(symbol, cache):
    """Saved research fields only; no FMP client, database or quote lookup."""
    value = cache.get(str(symbol or "").upper()) if isinstance(cache, dict) else None
    if not isinstance(value, dict):
        return {}
    from fmp_data import annual_context
    annual = value.get("jahresanalyse")
    annual = annual_context(annual) if isinstance(annual, dict) else {}
    if annual.get("symbol") and str(annual["symbol"]).upper() != str(symbol).upper():
        annual = {"available": False, "reason": "Referenzsymbol widerspricht der Auswahl"}
    return {"role": "OPTIONAL_RESEARCH_ONLY", "observed_at": value.get("zeit"),
        "company_name": value.get("bezeichnung"), "sector": value.get("sektor"),
        "industry": value.get("branche"),
        "instrument_type": "ETF" if value.get("ist_etf") is True else "Fonds" if value.get("ist_fonds") is True else "Unternehmensreferenz" if value.get("ist_etf") is False and value.get("ist_fonds") is False else "Typ nicht belegt",
        "daily": value.get("tagesanalyse") if isinstance(value.get("tagesanalyse"), dict) else {},
        "annual": annual, "source_ids": []}


def universe() -> dict:
    """Das dynamische Universum beider Broker fuer die Universums-Seite.

    Bewusst getrennt nach Anbieter: eToro-Aktien und OKX-Krypto folgen
    unterschiedlichen Regeln (Telegram-Freigabe gegen autonome Aufnahme),
    und genau dieser Unterschied soll auf einen Blick sichtbar sein.
    """
    from datetime import datetime, timezone

    def _alter_stunden(iso_text: str) -> float | None:
        if not iso_text:
            return None
        try:
            ts = datetime.fromisoformat(str(iso_text))
        except (TypeError, ValueError):
            return None
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return round((datetime.now(timezone.utc) - ts).total_seconds() / 3600.0, 1)

    try:
        import config
        from universe.manager import UniverseManager
        from universe.modelle import UniverseZustand
        manager = UniverseManager(
            UniverseZustand(getattr(config, "UNIVERSE_STATE_FILE", "universe_state.json")))
    except Exception as exc:
        return {"fehler": f"Universum nicht ladbar: {exc}", "broker": {}}

    # Der feste Kern kommt ab v8.1.3 vom Manager, nicht mehr direkt aus der
    # config: Aktien haben jetzt ebenfalls einen Kern (bis zu 75 Werte), und
    # nur der Manager kennt die geltende Obergrenze je Broker.
    kern_krypto = sorted(manager.kernwerte("okx"))
    kern_aktien = sorted(manager.kernwerte("etoro"))
    ausgabe: dict[str, dict] = {}
    fmp_cache = _json("fmp_reference_cache.json", {})

    for broker, anzeige, assetklasse in (("okx", "OKX Krypto", "crypto"),
                                         ("etoro", "eToro Aktien", "stock")):
        try:
            zeilen = manager.mitglieder_tabelle(broker)
            uebersicht = manager.uebersicht(broker)
            focus = list(uebersicht.get("focus_set") or [])
        except Exception as exc:
            ausgabe[broker] = {"anzeige": anzeige, "fehler": str(exc), "werte": []}
            continue

        for zeile in zeilen:
            symbol = str(zeile.get("symbol", "")).upper()
            mitglied = manager.zustand.hole(broker, symbol)
            if broker == "etoro":
                zeile["fmp_context"] = fmp_display_context(symbol, fmp_cache)
            zeile["anbieter"] = anzeige
            zeile["asset_type"] = assetklasse
            zeile["kern"] = manager.ist_kern(broker, symbol)
            zeile["focus"] = symbol in focus
            zeile["focus_platz"] = focus.index(symbol) + 1 if symbol in focus else None
            zeile["aufgenommen_am"] = getattr(mitglied, "aufgenommen_am", "") if mitglied else ""
            zeile["alter_stunden"] = _alter_stunden(zeile["aufgenommen_am"])
            zeile["letzte_pruefung"] = getattr(mitglied, "letzte_pruefung", "") if mitglied else ""

        # Catalogue candidates are visible even before admission. This is a
        # read-only projection and cannot add members or bypass entry gates.
        catalog_only = []
        if broker == "etoro":
            known = {str(r["symbol"]).upper() for r in zeilen}
            for candidate in getattr(config, "STOCK_CATALOG_SYMBOLS", []):
                if not isinstance(candidate, dict) or not candidate.get("underdog"):
                    continue
                symbol = str(candidate.get("symbol") or "").upper()
                if symbol and symbol not in known:
                    catalog_only.append({"symbol": symbol, "underdog": True,
                        "zustand": "KATALOG", "score": None, "kern": False,
                        "asset_type": "stock", "anbieter": anzeige,
                        "aufnahmegrund": "Im Underdog-Katalog; noch nicht ins aktive Universum aufgenommen"})
                    known.add(symbol)

        for row in catalog_only:
            row["fmp_context"] = fmp_display_context(row.get("symbol"), fmp_cache)
        ausgabe[broker] = {
            "anzeige": anzeige,
            "asset_type": assetklasse,
            "uebersicht": uebersicht,
            "autonome_aufnahme": bool(uebersicht.get("autonome_aufnahme")),
            "beobachtung_stunden": float(uebersicht.get("beobachtung_stunden", 0.0) or 0.0),
            "max_aenderungen_pro_lauf": int(uebersicht.get("max_aenderungen_pro_lauf", 0) or 0),
            "werte": zeilen,
            "katalog_kandidaten": catalog_only,
        }

    return {
        "zeit": datetime.now(timezone.utc).isoformat(),
        # "kernwerte" bleibt aus Kompatibilitaetsgruenden der Kryptokern --
        # die Aktienliste kommt getrennt dazu, damit die Oberflaeche 75
        # Ticker nicht in eine Kachel zwingen muss.
        # v8.1.4: Warum ist der Pool so klein? Zaehlung je Filter.
        "diagnose": _universums_diagnose(),
        "kernwerte": kern_krypto,
        "kernwerte_krypto": kern_krypto,
        "kernwerte_aktien": kern_aktien,
        "core_volume_20": _core_volume_status(),
        "dynamic_30": _dynamic_30_status(),
        "etoro_reconciliation": _etoro_reconciliation_status(),
        "broker": ausgabe,
    }


def broker_contexts(runtimes: dict | None = None) -> dict:
    """Observed runtime and configured next-start modes are separate facts."""
    from broker_display_context import environment, context
    from webui.settings_store import snapshot
    settings = snapshot()
    out = {}
    for broker, filename in (("etoro", "runtime_status.json"), ("okx", "runtime_status_okx.json")):
        runtime = dict(runtimes.get(broker, {})) if runtimes is not None else _runtime(filename)
        configured = environment(settings.get(broker) or {})
        observed = environment(runtime)
        if broker == "etoro" and not runtime.get("account_fingerprint"):
            parts = str(runtime.get("etoro_reconciliation_domain") or "").split(":")
            if len(parts) == 3:
                runtime["account_fingerprint"] = parts[2]
        c = context(runtime, broker=broker)
        out[broker] = {**c, "configured_environment": configured,
                       "observed_environment": observed, "online": bool(runtime.get("online")),
                       "worker_alive": bool(runtime.get("worker_alive")),
                       "last_heartbeat": runtime.get("last_heartbeat"),
                       "mode_mismatch": configured != observed and observed != "UNKNOWN"}
    return out


def accounting_overview() -> dict:
    import etoro_reconciliation
    # All scopes remain explicitly labelled. This endpoint does not mutate any state.
    return etoro_reconciliation.accounting_status()


def decision_summary_scoped() -> dict:
    """Counts, not sums of money. Decisions segregated by broker and stored mode."""
    from broker_display_context import environment
    zone = ZoneInfo(str(getattr(config, "LOCAL_TIMEZONE", "Europe/Berlin")))
    day = datetime.now(zone).date().isoformat()
    db = ROOT / getattr(config, "DECISION_DB_FILE", "decision_history.sqlite")
    out = {b:{"candidates":0,"approved":0,"blocked":0,"modes":{},"day":day} for b in ("etoro","okx")}
    if not db.is_file():
        return out
    try:
        con = sqlite3.connect(db.resolve().as_uri()+"?mode=ro", uri=True, timeout=3)
        con.row_factory = sqlite3.Row
        try:
            columns = {r[1] for r in con.execute('PRAGMA table_info(decisions)')}
            execution = 'execution_status' if 'execution_status' in columns else "''"
            sql = ('SELECT broker,paper,status,' + execution + ' AS execution_status,COUNT(*) AS n '
                   'FROM decisions WHERE local_day=? GROUP BY broker,paper,status,' + execution)
            for row in con.execute(sql, (day,)):
                b = str(row["broker"]).lower()
                if b not in out:
                    continue
                item, n = out[b], int(row["n"])
                item["candidates"] += n
                from decision_analytics import execution_view
                display = execution_view(dict(row))
                item["approved"] += n if display["status"] == "APPROVED" else 0
                item['execution_failed'] = item.get('execution_failed', 0) + (n if display['status'] in {'FEHLGESCHLAGEN','FAILED','REJECTED','NICHT_AUSGEFUEHRT','CANCELED','CANCELLED'} else 0)
                item["blocked"] += n if row["status"] in {"BLOCKED","AI_BLOCKED","AI_HOLD"} else 0
                mode = environment(dict(row))
                item["modes"][mode] = item["modes"].get(mode,0)+n
        finally:
            con.close()
    except (sqlite3.Error, OSError):
        logger.warning("Brokergetrennte Entscheidungszaehlung nicht lesbar", exc_info=True)
        for value in out.values():
            value.update(error="Zaehlung nicht lesbar",candidates=None,approved=None,blocked=None)
    return out


def system_log_scoped(*, search="", level="", broker="", limit=300) -> list[dict]:
    """Classify by logger or explicit broker word, not ticker/asset guesses."""
    import re
    rows = system_log(limit=1000)
    out, previous = [], "system"
    for text in rows:
        head = re.match(r"^\d{4}-\d\d-\d\d.*?\b(DEBUG|INFO|WARNING|ERROR|CRITICAL)\b\s+([A-Za-z_][\w.]*)\s*:", text)
        if head:
            name = head[2].lower()
            etoro = name.startswith(("etoro", "broker.etoro", "live_trader", "position_manager"))
            okx = name.startswith(("okx", "broker.okx", "crypto_", "exposure_klassifizierung"))
            previous = "etoro" if etoro else "okx" if okx else "system"
            if previous == "system":
                words = text[head.end():].lower()
                e = bool(re.search(r"\betoro\b",words))
                o = bool(re.search(r"\bokx\b",words))
                if e != o:
                    previous = "etoro" if e else "okx"
        # Free continuation lines belong to the preceding timestamped record.
        # A new logger header, not indentation or a ticker, changes the domain.
        elif re.match(r"^\d{4}-\d\d-\d\d", text):
            previous = "system"
        if broker and previous != broker:
            continue
        if search and str(search).lower() not in text.lower():
            continue
        if level and str(level).upper() not in text.upper():
            continue
        out.append({"broker":previous,"text":text})
    return out[-max(20,min(1000,int(limit))):]
