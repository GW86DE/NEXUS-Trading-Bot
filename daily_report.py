"""Taeglicher Telegram-Bericht fuer NEXUS."""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import config
from decision_analytics import summary as decision_summary, heartbeat_day_summary, report_sent, mark_report_sent, filter_performance
from notifier import send_telegram, send_document, telegram_status
from telegram_exports import export_daily_decisions_txt

logger=logging.getLogger(__name__)
_last_data_attempt = 0.0

def _local_now():
    try:
        return datetime.now(ZoneInfo(str(getattr(config,"DAILY_REPORT_TIMEZONE","Europe/Berlin"))))
    except Exception:
        return datetime.now().astimezone()


def _money(v):
    try:return f"{float(v):+,.2f}".replace(",","X").replace(".",",").replace("X",".")
    except Exception:return "n/v"


def _fmt_reason(reason: str) -> str:
    names={
        "net_edge":"Netto-Edge/Kosten","nachrichten":"Newsfilter","event":"Event Intelligence",
        "earnings_window":"Earnings-Schutzfenster","underdog_screening":"Underdog-Screening",
        "market_quality":"Spread/Liquiditaet","cost_quote":"Kostenquote nicht verfuegbar",
        "risk_manager":"Risikomanager",
        "sector_guard":"Sektorlimit","correlation_guard":"Korrelationslimit",
        "existing_position":"Position bereits vorhanden","duplicate_open_order":"Offene Kauforder",
        "marktlage":"Marktlage/Krise",
    }
    return names.get(reason, reason or "unbekannt")


def _verified_crypto_positions(payload: dict) -> list[dict]:
    rows = []
    for row in payload.get("positionen") or []:
        fills = [str(x) for x in (row.get("fill_ids") or [])
                 if str(x).strip() and not str(x).startswith("okx-entry:")]
        if (row.get("ownership_verified")
                and str(row.get("verwaltung", "")).upper() == "AUTO"
                and str(row.get("order_id") or "").strip()
                and str(row.get("client_order_id") or row.get("referenz") or "").strip()
                and fills):
            rows.append(row)
    return rows


def build_report(*, broker, risk, equity, positions, cycles: int, ai=None, runtime=None, news_health=None, trades=None) -> str:
    now=_local_now(); day=now.strftime("%Y-%m-%d")
    ds=decision_summary(day)
    hb=heartbeat_day_summary(day, broker=str(getattr(broker,"name","?")))
    cur=getattr(broker,"kontowaehrung",lambda:"USD")()

    def _tagesergebnis_netto() -> float:
        """Der belegte Tageswert aus dem Handelsbuch, ueber beide Broker."""
        try:
            import tagesbuch
            return float(tagesbuch.tagesergebnis(
                offene_positionen=[]).realisiert_netto)
        except Exception:
            logger.warning("Tagesbuch nicht lesbar; Bericht nutzt den Summenzaehler",
                           exc_info=True)
            return float(getattr(risk, "realized_pnl_today", 0.0) or 0.0)

    lines=[
        f"📊 NEXUS {getattr(config, 'VERSION_NEXUS', '')} · TAGESBERICHT",
        now.strftime("%d.%m.%Y · %H:%M"),   # v9.1: die echte Erzeugungszeit
        "",
        "💰 KONTO / ERGEBNIS",
        f"Kontowert: {_money(equity)} {cur}",
        # v9.1: aus dem Handelsbuch ueber BEIDE Broker, nicht aus dem
        # Summenzaehler der Aktienseite. Vorher nannte /bericht eine dritte
        # Zahl fuer denselben Tag -- und anders als /pnl ohne jede Warnung,
        # wenn Zaehler und Buchungen auseinanderliefen.
        f"Heute realisiert: {_money(_tagesergebnis_netto())} {cur}",
        f"Gewinne heute: {_money(getattr(risk,'net_profit_today',getattr(risk,'gross_profit_today',0)))} {cur}",
        f"Verluste heute: {_money(-abs(float(getattr(risk,'net_loss_today',getattr(risk,'gross_loss_today',0)) or 0)))} {cur}",
        f"Geschaetzte explizite Kosten: {_money(getattr(risk,'estimated_costs_today',0))} {cur}",
        f"Offene Positionen: {len(list(positions or []))}",
        "",
        "🧭 KAUFENTSCHEIDUNGEN",
        f"Ernsthafte Kandidaten protokolliert: {ds['candidates']}",
        f"Freigegeben: {ds['approved']} · Order uebermittelt: {ds.get('submitted',0)} · mit Fill bestaetigt: {ds.get('filled',0)}",
        f"Abgelehnt: {ds['blocked']}",
    ]
    trade_rows=list(trades or [])
    buys=sum(1 for x in trade_rows if str(x).startswith("KAUF"))
    sells=sum(1 for x in trade_rows if str(x).startswith("VERK."))
    lines += [f"Ausgefuehrte Fill-Meldungen heute: Kauf {buys} · Verkauf {sells}"]
    if trade_rows:
        lines.append("Letzte Ausfuehrungen:")
        for row in trade_rows[-5:]:
            lines.append(f"• {row}")

    reasons=sorted(ds.get("reasons",{}).items(),key=lambda x:-x[1])[:5]
    if reasons:
        lines.append("Haeufigste Ablehnungsgruende:")
        for i,(reason,n) in enumerate(reasons,1): lines.append(f"{i}. {_fmt_reason(reason)}: {n}")
    else: lines.append("Keine Kaufablehnungen protokolliert.")

    lines += ["", "🤖 KI-AUFMERKSAMKEIT"]
    ai_budget={}
    if ai is not None:
        try:
            ai_budget=ai.budget_status()
        except Exception as exc:
            logger.debug("KI-Aufmerksamkeitsbudget fuer Tagesbericht nicht verfuegbar: %s", exc)
    lines.append("Rolle: nur Scan-Reihenfolge; keine Kauf-/Verkaufsentscheidung oder Freigabe")
    if ai_budget:
        lines.append(f"Priorisierungsaufrufe: {ai_budget.get('calls','?')}/{ai_budget.get('max_calls_per_day','?')} heute")
        lines.append(f"Websuchen fuer Priorisierung: {ai_budget.get('web_searches',0)}")

    # Kurze Nachbewertung bereits gereifter, abgelehnter Qualitaetsfilter.
    # Sicherheitsfilter werden absichtlich NICHT als Optimierungshinweis gewertet.
    try:
        perf=[x for x in filter_performance(40) if x.get('category')=='alpha' and x.get('horizon') in {'1d','5d'} and int(x.get('n',0) or 0)>=3]
        perf=sorted(perf,key=lambda x:(x.get('horizon')!='5d',-int(x.get('n',0) or 0)))[:4]
        if perf:
            lines += ["", "📈 NACHBEWERTUNG ABGELEHNTER KAEUFE"]
            for row in perf:
                lines.append(f"{_fmt_reason(row.get('blocked_by',''))} · {row.get('horizon')}: n={row.get('n')} · Ø danach {float(row.get('avg_return_pct') or 0):+.2f}%")
            lines.append("Nur Qualitaets-/Alpha-Filter; Sicherheitsgrenzen werden hieraus nicht gelockert.")
    except Exception as exc:
        logger.debug("Decision-Nachbewertung fuer Tagesbericht nicht verfuegbar: %s", exc)

    state=(runtime or {}).get("connection_state") if isinstance(runtime,dict) else None
    last=(runtime or {}).get("last_broker_contact") if isinstance(runtime,dict) else None
    lines += ["", "🔌 BROKER / LEBENSBIT",
              f"Broker: {str(getattr(broker,'name','?')).upper()} · {'PAPER/DEMO' if getattr(broker,'ist_paper',lambda:True)() else 'LIVE'}",
              f"Aktueller Zustand: {state or hb.get('last_state','UNKNOWN')}",
              f"Heartbeat-Ereignisse heute: {hb.get('events',0)}",
              f"Letzter Brokerkontakt: {last or 'nicht verfuegbar'}"]
    bad=sum(int(v) for k,v in hb.get("states",{}).items() if k in {"OFFLINE","AUTH_ERROR","DEGRADED","RECONNECTING","RESYNC_FAILED"})
    lines.append(f"Auffaellige Heartbeat-Ereignisse heute: {bad}")
    dt=float(hb.get("observed_downtime_seconds",0) or 0)
    if dt>0:
        lines.append(f"Beobachtete Offline-/Reconnect-Zeit: ~{int(dt//60)} min {int(dt%60)} s")
    if hb.get("avg_latency_ms") is not None:
        lines.append(f"Mittlere gemessene Health-Latenz: {float(hb['avg_latency_ms']):.0f} ms")

    # Zweite Brokerdomaene separat aus ihrem eigenen Heartbeat. Ein OKX-
    # Ausfall darf weder den eToro-Bericht noch den Aktienkern mitreissen.
    try:
        root = Path(__file__).resolve().parent
        okx_path = root / "runtime_status_okx.json"
        okx = json.loads(okx_path.read_text(encoding="utf-8")) if okx_path.exists() else {}
        crypto_path = root / "crypto_positions.json"
        crypto = json.loads(crypto_path.read_text(encoding="utf-8")) if crypto_path.exists() else {}
        lines += [
            "",
            "🪙 OKX-SPOT-DOMÄNE",
            f"Modus: {okx.get('modus') or 'DEMO'} · Zustand: {okx.get('connection_state') or ('ONLINE' if okx.get('online') else 'OFFLINE')}",
            f"Letzter Brokerkontakt: {okx.get('last_broker_contact') or 'nicht verfügbar'}",
            f"Bestätigte Bot-Krypto-Positionen: {len(_verified_crypto_positions(crypto))}",
            f"Health-Test: authentifiziert alle {int(float(okx.get('health_interval_seconds') or getattr(config,'BROKER_HEALTHCHECK_SECONDS',30)))} s",
        ]
        if okx.get("last_connection_error"):
            lines.append("Letzter OKX-Fehler: " + str(okx["last_connection_error"])[:220])
    except Exception as exc:
        logger.debug("OKX-Tagesberichtsstatus nicht lesbar: %s", exc)

    if news_health:
        lines += ["", "📰 DATENQUELLEN"]
        for name,row in list(news_health.items())[:8]:
            state=str((row or {}).get("state") or ("healthy" if (row or {}).get("healthy") else "unknown"))
            icon="🟢" if state in {"healthy","ok"} else "⚪" if state in {"disabled","unknown"} else "🟡"
            lines.append(f"{icon} {name}: {state}")

    lines += ["", "⚙️ BETRIEB", f"Scanner-Zyklen: {cycles}", f"Trades heute laut Risikostatus: {getattr(risk,'trades_today',0)}"]
    if isinstance(runtime, dict) and runtime.get("pi_mode"):
        temp=runtime.get("pi_cpu_temperature_c")
        ram=runtime.get("pi_memory_available_mb")
        disk=runtime.get("pi_disk_free_mb")
        model=runtime.get("pi_model") or "Raspberry Pi"
        lines.append(f"Pi-System: {model}")
        if temp is not None: lines.append(f"CPU-Temperatur: {float(temp):.1f} °C")
        if ram is not None: lines.append(f"Freier RAM: {float(ram):.0f} MB")
        if disk is not None: lines.append(f"Freier Speicher: {float(disk)/1024.0:.1f} GB")
        warnings=list(runtime.get("pi_health_warnings") or [])
        if warnings: lines.append("Pi-Warnung: " + " | ".join(str(x) for x in warnings[:3]))
    if ds['candidates'] and ds['approved']==0:
        top=reasons[0] if reasons else ("unbekannt",0)
        lines += ["", f"ℹ️ Heute wurde kein Kauf freigegeben. Hauptgrund: {_fmt_reason(top[0])} ({top[1]}x)."]
    lines += ["", "Hinweis: Sicherheitsfilter (z.B. Sektor-/Risikogrenzen) werden spaeter nicht anhand entgangener Gewinne 'wegoptimiert'."]
    return "\n".join(lines)


def due(now=None) -> tuple[bool,str]:
    now=now or _local_now()
    key=now.strftime("daily-%Y-%m-%d")
    hour=int(getattr(config,"DAILY_REPORT_HOUR",18)); minute=int(getattr(config,"DAILY_REPORT_MINUTE",0))
    after=(now.hour,now.minute)>=(hour,minute)
    return bool(getattr(config,"DAILY_REPORT_ENABLED",True) and after and not report_sent(key)), key


def maybe_send(**kwargs) -> bool:
    """Sendet Bericht und lesbare Entscheidungsdatei mit getrenntem Status.

    Dadurch kann eine temporaer fehlgeschlagene Dokumentzustellung spaeter
    nachgeholt werden, ohne den Textbericht bei jedem Heartbeat zu duplizieren.
    """
    global _last_data_attempt
    now=_local_now()
    hour=int(getattr(config,"DAILY_REPORT_HOUR",18)); minute=int(getattr(config,"DAILY_REPORT_MINUTE",0))
    after=(now.hour,now.minute)>=(hour,minute)
    if not bool(getattr(config,"DAILY_REPORT_ENABLED",True)) or not after:
        return False
    day=now.strftime("%Y-%m-%d")
    key=f"daily-{day}"
    data_key=f"daily-data-{day}"
    status=telegram_status()
    if not status.get("configured"):
        logger.warning("Tagesbericht ist faellig, Telegram aber nicht konfiguriert.")
        return False
    did=False
    if not report_sent(key):
        text=build_report(**kwargs)
        send_telegram(text,priority="normal",queue_on_fail=True,silent=True)
        mark_report_sent(key,hashlib.sha256(text.encode("utf-8")).hexdigest())
        logger.info("18-Uhr-Tagesbericht an Telegram uebergeben/queued: %s",key)
        did=True
    # Dokumente werden nicht in die Textqueue serialisiert. Bei temporaerem
    # Fehler maximal alle 5 Minuten erneut versuchen.
    import time as _time
    if not report_sent(data_key) and (_time.monotonic()-_last_data_attempt >= 300.0 or _last_data_attempt <= 0):
        _last_data_attempt=_time.monotonic()
        try:
            report_path=export_daily_decisions_txt(day)
            if send_document(report_path,caption="Kaufentscheidungen und Ablehnungen mit Begründung"):
                mark_report_sent(data_key,hashlib.sha256(report_path.read_bytes()).hexdigest())
                logger.info("Entscheidungsdatei zum Tagesbericht gesendet: %s",report_path.name)
                did=True
            else:
                logger.warning("Entscheidungsdatei wird spaeter erneut versucht: %s",report_path)
        except Exception as exc:
            logger.warning("Entscheidungsdatei zum Tagesbericht fehlgeschlagen: %s",exc)
    return did
