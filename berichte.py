"""
Statusberichte und Telegram-Befehlsverarbeitung.

Hier laufen zwei Dinge zusammen:

1. BERICHTE
   Ein Text, der auf einen Blick zeigt, was der Bot getan hat und wie er
   dasteht -- taeglich per Telegram und auf Wunsch jederzeit abrufbar.

2. BEFEHLE
   Die Umsetzung dessen, was per Telegram hereinkommt (STATUS, PAUSE,
   STOPP, START, BERICHT, POSITIONEN).

Bewusst kanalneutral gehalten: Telegram, GUI und Diagnosewerkzeuge nutzen
dieselben Inhalte. Die Transportlogik bleibt davon getrennt.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import config
from position_manager import fmt_money, fmt_price
from bot_zustand import AKTIV, PAUSIERT, GESTOPPT

logger = logging.getLogger(__name__)


def _zone() -> ZoneInfo:
    return ZoneInfo(str(getattr(config, "LOCAL_TIMEZONE", "Europe/Berlin")))


def _lokal(dt: datetime) -> datetime:
    return dt.astimezone(_zone())

def _runtime_broker_status():
    """Liest nur den lokalen Heartbeat; keine Broker-API-Abfrage fuer Berichte."""
    try:
        from runtime_status import read_runtime
        d = read_runtime(Path(__file__).with_name("runtime_status.json"))
        if not d.get("online"):
            return None
        return d
    except Exception:
        return None


def _broker_data_temporarily_unavailable() -> tuple[bool, dict | None]:
    d = _runtime_broker_status()
    if not d:
        return False, d
    state = str(d.get("connection_state", "") or "").upper()
    unavailable = (not bool(d.get("broker_connected"))) or state in {
        "OFFLINE", "OFFLINE_WAIT", "RECONNECTING", "RESYNC",
        "RESYNC_PENDING", "RESYNC_FAILED", "AUTH_ERROR", "FATAL",
    }
    return unavailable, d


# ---------------------------------------------------------------------------
# Berichte
# ---------------------------------------------------------------------------

def _zeit() -> str:
    return datetime.now(_zone()).strftime("%d.%m.%Y %H:%M")


def _position_values(p):
    symbol = str(getattr(p, "symbol", "?"))
    qty = float(getattr(p, "quantity", 0) or 0)
    avg = float(getattr(p, "avg_cost", 0) or 0)
    cur = str(getattr(p, "currency", "") or "")
    market = float(getattr(p, "market_price", 0) or 0)
    market_value = float(getattr(p, "market_value", 0) or 0)
    unreal = float(getattr(p, "unrealized_pnl", 0) or 0)
    # Nur dann selbst berechnen, wenn der Broker einen echten aktuellen Kurs liefert.
    has_market = market > 0 and avg > 0 and qty != 0
    if has_market and not market_value:
        market_value = market * qty
    if has_market and abs(unreal) < 1e-12:
        unreal = (market - avg) * qty
    return symbol, qty, avg, cur, market, market_value, unreal, has_market


def status_text(zustand, broker=None, positionen=None, risiko=None,
                kontowert=None, waehrung="EUR") -> str:
    """Kompakter Status fuer Telegram; nur belegte Werte."""
    positionen = list(positionen or [])
    state = str(getattr(zustand, "zustand", lambda: "?")()).upper()
    icon = {"AKTIV": "🟢", "PAUSIERT": "🟠", "GESTOPPT": "🔴"}.get(state, "⚪")
    zeilen = [
        f"🤖 NEXUS {getattr(config, 'VERSION_NEXUS', '')} · {icon} {state}",
        f"Stand: {_zeit()} · Europe/Berlin",
    ]

    if broker is not None:
        try:
            modus = "PAPER" if broker.ist_paper() else "LIVE"
            # Ausdruecklich als AKTIEN-Domaene benennen -- der Krypto-Block
            # steht weiter unten und hat seinen eigenen Modus.
            zeilen.append(f"AKTIEN · {getattr(broker, 'name', '?')} · {modus}")
        except Exception:
            zeilen.append(f"Broker: {getattr(broker, 'name', '?')}")

    profil = getattr(config, "ACTIVE_PROFILE", None)
    if profil:
        zeilen.append(f"Profil: {str(profil).upper()}")

    broker_data_unavailable, runtime_broker = _broker_data_temporarily_unavailable()
    if runtime_broker:
        cstate = str(runtime_broker.get("connection_state", "") or "").upper()
        if broker_data_unavailable:
            label = "AUTH-FEHLER" if cstate == "AUTH_ERROR" else ("SYNCHRONISIERUNG" if cstate.startswith("RESYNC") else "OFFLINE / AUTO-RECONNECT")
            zeilen.append(f"Broker-Verbindung: 🔴 {label}")
            last = runtime_broker.get("last_broker_contact")
            if last:
                try:
                    dt = _lokal(datetime.fromisoformat(str(last).replace("Z", "+00:00")))
                    zeilen.append(f"Letzter Brokerkontakt: {dt.strftime('%d.%m.%Y %H:%M:%S')}")
                except Exception:
                    __import__("logging").getLogger(__name__).debug("Best-effort-Ausnahme unterdrueckt", exc_info=True)
        else:
            zeilen.append("Broker-Verbindung: 🟢 ONLINE")

    zeilen.append("")
    if kontowert is not None:
        zeilen.append(f"Kontowert: {fmt_money(kontowert)} {waehrung}")

    if risiko is not None:
        try:
            unknown = int(getattr(risiko, "unknown_pnl_trades_today", 0) or 0)
            lifetime_unknown = int(getattr(risiko, "lifetime_unknown_pnl_trades", 0) or 0)
            today_pnl = float(getattr(risiko, "realized_pnl_today", 0) or 0)
            total_pnl = float(getattr(risiko, "lifetime_realized_pnl", 0) or 0)
            if not unknown:
                zeilen.append(f"Heute realisiert: {fmt_money(today_pnl)} {waehrung}")
            elif abs(today_pnl) > 1e-12:
                zeilen.append(f"Heute realisiert (bekannte Trades): {fmt_money(today_pnl)} {waehrung}")
            if not lifetime_unknown:
                zeilen.append(f"Gesamt realisiert seit v5.2: {fmt_money(total_pnl)} {waehrung}")
            elif abs(total_pnl) > 1e-12:
                zeilen.append(f"Gesamt realisiert (nur bekannte Trades): {fmt_money(total_pnl)} {waehrung}")
            if unknown:
                zeilen.append(f"⚠️ P&L heute unvollständig: {unknown} Verkauf/Verkäufe ohne sichere Einstandsdaten")
            costs = float(getattr(risiko, "estimated_costs_today", 0) or 0)
            if costs > 0:
                zeilen.append(f"Kosten heute geschätzt: {fmt_money(costs)} {waehrung}")
        except Exception:
            __import__("logging").getLogger(__name__).debug("Best-effort-Ausnahme unterdrueckt", exc_info=True)

    # Offenes P&L nur aus Positionen mit echten Broker-Marktdaten. Bei
    # bekanntem Brokerausfall wird ein fehlgeschlagener Abruf NICHT als
    # "0 Positionen" oder "0 P&L" ausgegeben.
    if broker_data_unavailable:
        zeilen.extend(["", "Offene Positionen: derzeit nicht zuverlässig abrufbar (Broker offline)"])
    else:
        unreal_sum = 0.0
        unreal_count = 0
        unreal_currencies = set()
        for p in positionen:
            try:
                symbol, qty, avg, cur, market, market_value, unreal, has_market = _position_values(p)
                if has_market:
                    unreal_sum += unreal
                    unreal_count += 1
                    if cur:
                        unreal_currencies.add(cur.upper())
            except Exception:
                __import__("logging").getLogger(__name__).debug("Best-effort-Ausnahme unterdrueckt", exc_info=True)
        if unreal_count and len(unreal_currencies) <= 1:
            cur = next(iter(unreal_currencies), waehrung)
            zeilen.append(f"Offen unrealisiert: {fmt_money(unreal_sum)} {cur}")

        zeilen.extend(["", f"Offene Positionen: {len(positionen)}"])
        for p in positionen[:10]:
            try:
                symbol, qty, avg, cur, market, _, unreal, has_market = _position_values(p)
                line = f"• {symbol} · {qty:g} Stk."
                if avg > 0:
                    line += f" · EK {fmt_price(avg)} {cur}"
                if has_market:
                    pct = ((market / avg) - 1) * 100 if avg else None
                    line += f" · Kurs {fmt_price(market)} · P&L {fmt_money(unreal)} {cur}"
                    if pct is not None:
                        line += f" ({pct:+.2f} %)".replace(".", ",")
                zeilen.append(line)
            except Exception:
                symbol = getattr(p, "symbol", "?")
                zeilen.append(f"• {symbol} · Details derzeit nicht verfügbar")
        if len(positionen) > 10:
            zeilen.append(f"… und {len(positionen)-10} weitere")

    # --- OKX (v8.1.4) ---------------------------------------------------
    # Bis 8.1.3 stand hier ueber die Kryptoseite kein einziges Wort: der
    # Status las nur runtime_status.json, also die Datei des eToro-Prozesses.
    try:
        import okx_status
        zeilen.extend(okx_status.block())
    except Exception:
        __import__("logging").getLogger(__name__).debug(
            "OKX-Statusblock nicht erzeugbar", exc_info=True)

    zeilen.extend(pi_status_lines())
    return "\n".join(zeilen)


def positionen_text(positionen, waehrung="EUR") -> str:
    """Ausfuehrliche, aber kompakte Positionsliste."""
    positionen = list(positionen or [])
    if not positionen:
        return "Keine offenen Positionen."

    zeilen = [f"📊 OFFENE POSITIONEN · {len(positionen)}", f"Stand: {_zeit()}", ""]
    known_market_value = 0.0
    market_value_count = 0
    unreal_sum = 0.0
    unreal_count = 0
    for p in positionen:
        try:
            symbol, qty, avg, cur, market, market_value, unreal, has_market = _position_values(p)
            zeilen.append(f"{symbol} · {qty:g} Stk.")
            if avg > 0:
                zeilen.append(f"  Einstand: {fmt_price(avg)} {cur}")
            if has_market:
                pct = ((market / avg) - 1) * 100 if avg else 0.0
                zeilen.append(f"  Aktuell: {fmt_price(market)} {cur}")
                zeilen.append(f"  P&L offen: {fmt_money(unreal)} {cur} ({pct:+.2f} %)".replace(".", ","))
                unreal_sum += unreal; unreal_count += 1
            if market_value > 0:
                zeilen.append(f"  Marktwert: {fmt_money(market_value)} {cur}")
                known_market_value += market_value; market_value_count += 1
            zeilen.append("")
        except Exception:
            zeilen.append(f"{getattr(p, 'symbol', '?')}: Details derzeit nicht verfügbar")
            zeilen.append("")

    if unreal_count:
        zeilen.append(f"P&L offen gesamt: {fmt_money(unreal_sum)} {waehrung}")
    if market_value_count == len(positionen):
        zeilen.append(f"Marktwert gesamt: {fmt_money(known_market_value)} {waehrung}")
    return "\n".join(zeilen).rstrip()


def tagesbericht_text(zustand, broker=None, positionen=None, risiko=None,
                      kontowert=None, waehrung="EUR", trades_heute=None,
                      zyklen=None, ai_status=None) -> str:
    """
    Der taegliche Bericht. Bewusst nuechtern gehalten: was ist passiert,
    wie steht es, gibt es etwas zu tun.
    """
    zeilen = [
        f"🤖 NEXUS {getattr(config, 'VERSION_NEXUS', 'NEXUS')}",
        "TRADING-BOT TAGESBERICHT",
        "=" * 44,
        datetime.now(_zone()).strftime("%A, %d.%m.%Y %H:%M"),
        "",
    ]

    # --- Betrieb ---
    zeilen.append("BETRIEB")
    zeilen.append(zustand.beschreibung())
    if zyklen is not None:
        zeilen.append(f"Durchlaeufe seit Start: {zyklen}")
    zeilen.append("")

    # --- Konto ---
    zeilen.append("KONTO")
    if kontowert is not None:
        zeilen.append(f"  Kontowert       : {kontowert:,.2f} {waehrung}")
    if risiko is not None:
        try:
            zeilen.append(f"  Realisiert heute: {risiko.realized_pnl_today:+,.2f} {waehrung}")
            zeilen.append(f"  Realisiert gesamt: {getattr(risiko, 'lifetime_realized_pnl', 0.0):+,.2f} {waehrung} (ab v5.2)")
            zeilen.append(f"  Handelskosten~   : {getattr(risiko, 'estimated_costs_today', 0.0):,.2f} {waehrung}")
            if getattr(risiko,'gross_profit_today',0.0)>0:
                zeilen.append(f"  Kostenquote      : {risiko.cost_ratio()*100:.1f} % des positiven Brutto-P&L")
            zeilen.append(f"  Trades heute     : {getattr(risiko, 'trades_today', 0)}")
            zeilen.append(f"  Verlustserie     : {getattr(risiko, 'consecutive_losses', 0)}")
        except Exception:
            __import__("logging").getLogger(__name__).debug("Best-effort-Ausnahme unterdrueckt", exc_info=True)
    zeilen.append(f"  Offene Positionen: {len(positionen) if positionen else 0}")
    zeilen.append("")

    # --- Trades ---
    zeilen.append("TRADES HEUTE")
    if trades_heute:
        for t in trades_heute[:20]:
            zeilen.append(f"  {t}")
        if len(trades_heute) > 20:
            zeilen.append(f"  ... und {len(trades_heute) - 20} weitere")
    else:
        zeilen.append("  keine")
    zeilen.append("")

    # --- Positionen ---
    if positionen:
        zeilen.append("POSITIONEN")
        for p in positionen[:15]:
            try:
                symbol, qty, avg, cur, market, _, unreal, has_market = _position_values(p)
                teil = f"  {symbol:10s} {qty:g} @ {fmt_price(avg)} {cur}" if avg > 0 else f"  {symbol:10s} {qty:g}"
                if has_market:
                    gv_pct = (market / avg - 1) * 100 if avg else 0.0
                    teil += f" | P&L {fmt_money(unreal)} {cur} ({gv_pct:+.2f} %)".replace(".", ",")
                zeilen.append(teil)
            except Exception:
                zeilen.append(f"  {getattr(p, 'symbol', '?')} | Details nicht verfügbar")
        zeilen.append("")

    # --- KI-Aufmerksamkeit (nur Scan-Reihenfolge) ---
    if ai_status:
        zeilen.append("KI-AUFMERKSAMKEIT")
        zeilen.append("  Rolle: nur Scan-Reihenfolge; keine Handelsentscheidung")
        zeilen.append(f"  Priorisierungen heute: {ai_status.get('calls', 0)} von {ai_status.get('max_calls_per_day', '?')}")
        if ai_status.get("letzter_fehler"):
            zeilen.append("  Letzter Fehler: " + str(ai_status.get("letzter_fehler"))[:90])
            zeilen.append("  Folge: deterministische Scanner-Reihenfolge bleibt aktiv")
        zeilen.append("")

    # --- Hinweise, die wirklich relevant sind ---
    hinweise = []
    if zustand.zustand() != AKTIV:
        hinweise.append(f"Der Bot ist {zustand.zustand().upper()} -- es wird nicht "
                        "normal gehandelt.")
    if risiko is not None and getattr(risiko, "trading_halted", False):
        hinweise.append("Das Tages-Verlustlimit wurde erreicht.")
    if risiko is not None and getattr(risiko, "cooldown_active", lambda: False)():
        hinweise.append(f"Verlustserien-Cooldown aktiv bis {getattr(risiko, 'cooldown_until', '')}.")
    if broker is not None:
        try:
            if not broker.unterstuetzt_krypto_stop():
                krypto_offen = [p for p in (positionen or [])
                                if getattr(p, "asset_type", "") == "crypto"]
                if krypto_offen:
                    hinweise.append(
                        f"{len(krypto_offen)} offene Krypto-Position(en) bei einem "
                        "Broker ohne boersenseitigen Stop -- der Schutz wirkt nur, "
                        "solange der Bot laeuft.")
        except Exception:
            __import__("logging").getLogger(__name__).debug("Best-effort-Ausnahme unterdrueckt", exc_info=True)

    if hinweise:
        zeilen.append("HINWEISE")
        for h in hinweise:
            zeilen.append(f"  - {h}")
        zeilen.append("")

    zeilen.append("-" * 44)
    zeilen.append("Befehle per Telegram: STATUS, PAUSE, STOPP, START, BERICHT, POSITIONEN")
    return "\n".join(zeilen)



def _fmt_duration(seconds) -> str:
    try:
        seconds = max(0, int(float(seconds or 0)))
    except Exception:
        return "n/v"
    d, rem = divmod(seconds, 86400); h, rem = divmod(rem, 3600); m, sec = divmod(rem, 60)
    if d: return f"{d}d {h}h {m}m"
    if h: return f"{h}h {m}m"
    return f"{m}m {sec}s"


def pi_status_lines() -> list[str]:
    try:
        from runtime_status import read_runtime
        rt = read_runtime(Path(__file__).with_name("runtime_status.json"))
    except Exception:
        rt = {}
    if not rt.get("pi_mode"):
        return []
    out = ["", "🍓 RASPBERRY PI"]
    temp = rt.get("pi_cpu_temperature_c")
    cpu = rt.get("pi_cpu_usage_pct")
    used = rt.get("pi_memory_used_mb"); total = rt.get("pi_memory_total_mb")
    dfree = rt.get("pi_disk_free_mb"); dtotal = rt.get("pi_disk_total_mb")
    uptime = rt.get("pi_system_uptime_seconds")
    if temp is not None: out.append(f"CPU-Temperatur: {float(temp):.1f} °C")
    if cpu is not None: out.append(f"CPU-Auslastung: {float(cpu):.1f} %")
    if used is not None and total:
        out.append(f"RAM: {float(used)/1024:.2f} / {float(total)/1024:.2f} GB ({float(used)/float(total)*100:.1f} %)")
    if dfree is not None and dtotal:
        out.append(f"Speicher frei: {float(dfree)/1024:.1f} / {float(dtotal)/1024:.1f} GB")
    if uptime is not None: out.append(f"Pi-Uptime: {_fmt_duration(uptime)}")
    power=[]
    if rt.get("pi_undervoltage_now"): power.append("Unterspannung")
    if rt.get("pi_throttled_now"): power.append("Drosselung")
    warnings=list(rt.get("pi_health_warnings") or [])
    if power: out.append("Power/Throttle: 🔴 " + ", ".join(power))
    elif warnings: out.append("Power/Throttle: 🟡 " + " | ".join(str(x) for x in warnings[:2]))
    else: out.append("Power/Throttle: 🟢 unauffällig")
    last = rt.get("last_scan_at") or rt.get("last_cycle_started_at")
    if last:
        try:
            dt=_lokal(datetime.fromisoformat(str(last).replace("Z","+00:00")))
            out.append(f"Letzter Scan: {dt.strftime('%d.%m.%Y %H:%M:%S')}")
        except Exception: __import__("logging").getLogger(__name__).debug("Best-effort-Ausnahme unterdrueckt", exc_info=True)
    return out


def pnl_text(risiko, positionen=None, waehrung="EUR") -> str:
    lines=["💰 P&L", f"Stand: {_zeit()}"]
    if risiko is None:
        return "P&L derzeit nicht verfügbar."
    lifetime=float(getattr(risiko,"lifetime_realized_pnl",0) or 0)

    # v8.1.5: Der Tageswert kommt aus den einzelnen Buchungen des Handelsbuchs.
    # Vorher stand hier risk.realized_pnl_today -- ein Summenzaehler ohne
    # Belege. Am 25.08.2026 meldete er -226,60 USD, waehrend genau ein Trade
    # geschlossen war (DVLT, -44,40 USD).
    # v9.1: BEIDE Broker. Vorher stand hier broker="etoro" fest verdrahtet,
    # waehrend die Ueberschrift "Tagesergebnis gesamt" lautete. An einem Tag
    # mit +98 auf eToro und -252 auf OKX zeigte /pnl "+98,00 gesamt" -- die
    # Kryptoseite fehlte vollstaendig und ohne jeden Hinweis.
    try:
        import tagesbuch
        gesamt = tagesbuch.tagesergebnis(
            waehrung=waehrung,
            offene_positionen=list(positionen) if positionen is not None else None,
            zaehlerwert=None)
        lines += gesamt.zeilen(mit_buchungen=True)
        # Je Broker aufschluesseln, solange beide Seiten laufen.
        for name, anzeige in (("etoro", "eToro Aktien"), ("okx", "OKX Krypto")):
            teil = tagesbuch.tagesergebnis(broker=name, waehrung=waehrung,
                                           offene_positionen=[])
            if teil.geschlossene_trades:
                lines.append(f"  davon {anzeige}: "
                             f"{teil.realisiert_netto:+,.2f} {waehrung} "
                             f"({teil.geschlossene_trades} Trade"
                             f"{'s' if teil.geschlossene_trades != 1 else ''})")
        # Der Summenzaehler der Aktienseite wird weiterhin gegengeprueft --
        # aber gegen die eToro-Buchungen, zu denen er gehoert.
        etoro = tagesbuch.tagesergebnis(broker="etoro", waehrung=waehrung,
                                        offene_positionen=[],
                                        zaehlerwert=getattr(risiko, "realized_pnl_today", None))
        for hinweis in etoro.hinweise:
            if hinweis not in gesamt.hinweise:
                lines.append(f"⚠️ {hinweis}")
    except Exception:
        __import__("logging").getLogger(__name__).warning(
            "Tagesbuch nicht lesbar; Anzeige faellt auf den Summenzaehler zurueck",
            exc_info=True)
        today=float(getattr(risiko,"realized_pnl_today",0) or 0)
        lines.append(f"Heute realisiert: {fmt_money(today)} {waehrung}")
        lines.append("⚠️ Handelsbuch nicht lesbar -- dieser Wert ist nicht belegt.")
        unreal=0.0; n=0
        for p in list(positionen or []):
            try:
                *_, u, has_market = _position_values(p)
                if has_market: unreal += float(u); n += 1
            except Exception:
                __import__("logging").getLogger(__name__).debug("Best-effort-Ausnahme unterdrueckt", exc_info=True)
        if n: lines.append(f"Offen unrealisiert: {fmt_money(unreal)} {waehrung}")

    lines.append(f"Gesamt realisiert seit v5.2: {fmt_money(lifetime)} {waehrung}")
    return "\n".join(lines)


def decisions_text(limit: int = 10) -> str:
    try:
        from decision_analytics import latest
        rows=latest(max(1,int(limit)))
    except Exception as exc:
        return f"Entscheidungen nicht verfuegbar: {exc}"
    if not rows:
        return "Noch keine Kauf-/Ablehnungsentscheidungen protokolliert."
    status_names = {
        "APPROVED": "FREIGEGEBEN", "BLOCKED": "ABGELEHNT",
        "NO_SIGNAL": "KEIN SIGNAL", "SYSTEM_ERROR": "SYSTEMFEHLER",
        "ORDER_SUBMITTED": "ORDER ÜBERMITTELT", "FILLED": "AUSGEFÜHRT",
    }
    lines=[f"🧭 NEXUS {getattr(config, 'VERSION_NEXUS', '')} · LETZTE ENTSCHEIDUNGEN"]
    for r in rows[:limit]:
        stamp=str(r.get("created_at_utc") or "")
        try:
            stamp=_lokal(datetime.fromisoformat(stamp.replace("Z","+00:00"))).strftime("%d.%m %H:%M")
        except Exception:
            stamp=stamp[:16]
        raw_status=str(r.get("status") or "?").upper()
        raw_execution=str(r.get("execution_status") or "").upper()
        status=status_names.get(raw_status, raw_status)
        execution=status_names.get(raw_execution, raw_execution)
        line=f"• {stamp} · {str(r.get('symbol') or '?')} · {status}"
        # Derselbe Zustand wurde bislang doppelt als "BLOCKED · BLOCKED"
        # ausgegeben. Nur eine echte nachgelagerte Ausfuehrungsstufe ist neu.
        if execution and execution not in {raw_status, status}:
            line += f" · {execution}"
        lines.append(line)
        reason=str(r.get("reason") or r.get("blocked_by") or "")
        if reason: lines.append(f"  Grund: {reason[:220]}")
    return "\n".join(lines)


def version_text() -> str:
    import platform
    return "\n".join([
        f"🤖 TRADINGBOT {getattr(config, 'VERSION_NEXUS', 'NEXUS')}",
        f"Python: {platform.python_version()} · {platform.machine()}",
        f"Profil: {str(getattr(config, 'ACTIVE_PROFILE', '?')).upper()}",
        f"eToro: {'DEMO/PAPER' if getattr(config, 'PAPER_TRADING', True) else 'LIVE'}",
        f"OKX: {'LIVE' if getattr(config, 'OKX_LIVE_TRADING', False) else 'DEMO'}",
        "Handelsrollen: eToro nur Aktien/ETF · OKX nur Spot-Krypto",
    ])


def stats_text() -> str:
    try:
        from decision_analytics import summary
        row = summary()
        reasons = sorted((row.get("reasons") or {}).items(), key=lambda x: -x[1])[:5]
        lines = ["📊 HEUTIGE ENTSCHEIDUNGSSTATISTIK",
                 f"Kandidaten: {row.get('candidates',0)}",
                 f"Freigegeben: {row.get('approved',0)} · Abgelehnt: {row.get('blocked',0)}",
                 f"Order übermittelt: {row.get('submitted',0)} · Fill bestätigt: {row.get('filled',0)}"]
        if reasons:
            lines.append("Ablehnungsgründe: " + " · ".join(f"{k} {v}x" for k,v in reasons))
        return "\n".join(lines)
    except Exception as exc:
        return f"Statistik nicht verfügbar: {exc}"


def performance_text() -> str:
    try:
        from decision_analytics import filter_performance
        rows = filter_performance(3)
    except Exception as exc:
        return f"Performance-Nachbewertung nicht verfügbar: {exc}"
    if not rows:
        return "📈 PERFORMANCE\nNoch nicht genug gereifte Entscheidungen (mindestens 3 je Filter/Horizont)."
    lines = ["📈 PERFORMANCE ABGELEHNTER KANDIDATEN"]
    for row in rows[:12]:
        value = row.get("avg_return_pct", row.get("avg_return", 0))
        lines.append(f"• {row.get('blocked_by','?')} · {row.get('horizon','?')} · n={row.get('n',0)} · Ø {float(value or 0):+.2f} %")
    lines.append("Sicherheitsgrenzen werden daraus niemals automatisch gelockert.")
    return "\n".join(lines)


def locks_text(zustand) -> str:
    try:
        from broker_live_arming import status
        etoro = status("etoro"); okx = status("okx")
    except Exception:
        etoro = okx = (False, "nicht lesbar", {})
    return "\n".join([
        "🔒 SPERREN / SICHERHEIT",
        f"Botzustand: {str(zustand.zustand()).upper()}",
        f"eToro LIVE-Arming: {'AKTIV' if etoro[0] else 'AUS'} · {etoro[1]}",
        f"OKX LIVE-Arming: {'AKTIV' if okx[0] else 'AUS'} · {okx[1]}",
        "WebUI kann LIVE auswählen, aber niemals LIVE-Orders armieren.",
        "Ausstiege/Schutz bleiben bei einer Kaufpause aktiv.",
    ])


def telegram_text() -> str:
    try:
        from notifier import telegram_status
        row = telegram_status(active=False)
        import json
        from pathlib import Path
        path = Path(__file__).with_name(getattr(config, "TELEGRAM_CONTROL_STATUS_FILE", "telegram_control_status.json"))
        ctl = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        return "\n".join([
            "📨 TELEGRAM",
            f"Senden eingerichtet: {'ja' if row.get('configured') else 'nein'}",
            f"Queue: {row.get('queued',0)} · Fehlerfolge: {row.get('consecutive_failures',0)}",
            f"Zustellstau: {'JA' if row.get('delivery_stalled') else 'nein'}",
            f"Steuerung: {ctl.get('status','nicht gestartet')} · Sitzung {ctl.get('session_id','-')}",
            f"Letzter Poll: {ctl.get('last_poll_success_at') or ctl.get('updated_at') or '-'}",
        ])
    except Exception as exc:
        return f"Telegram-Status nicht verfügbar: {exc}"

# ---------------------------------------------------------------------------
# Befehlsverarbeitung
# ---------------------------------------------------------------------------

class Befehlsverarbeitung:
    """
    Setzt die per Telegram empfangenen Befehle um.

    Die Datenquellen werden als Funktionen uebergeben, nicht als Werte --
    so liefert jeder Befehl den AKTUELLEN Stand und nicht den vom
    Bot-Start.
    """

    def __init__(self, zustand, hole_broker=None, hole_positionen=None,
                 hole_risiko=None, hole_kontowert=None, hole_trades_heute=None, hole_orders=None,
                 hole_zyklen=None, waehrung="EUR", hole_position_mode=None, hole_bericht=None,
                 health_text=None, request_scan=None, request_shutdown=None, send_database=None,
                 send_decisions_file=None, ai_status=None, ai_set_mode=None, market_status=None):
        self.zustand = zustand
        self.hole_broker = hole_broker or (lambda: None)
        self.hole_positionen = hole_positionen or (lambda: [])
        self.hole_risiko = hole_risiko or (lambda: None)
        self.hole_kontowert = hole_kontowert or (lambda: None)
        self.hole_trades_heute = hole_trades_heute or (lambda: [])
        self.hole_orders = hole_orders or (lambda: [])
        self.hole_zyklen = hole_zyklen or (lambda: None)
        self.hole_position_mode = hole_position_mode or (lambda symbol: "")
        self.hole_bericht = hole_bericht
        self.health_text_cb = health_text
        self.request_scan = request_scan
        self.request_shutdown = request_shutdown
        self.send_database = send_database
        self.send_decisions_file = send_decisions_file
        self.ai_status_cb = ai_status
        self.ai_set_mode_cb = ai_set_mode
        self.market_status_cb = market_status
        self.waehrung = waehrung

    def _sicher(self, funktion, standard=None):
        try:
            return funktion()
        except Exception as exc:
            logger.warning("Datenabruf fehlgeschlagen: %s", exc)
            return standard

    def __call__(self, befehl: str) -> str:
        return self.ausfuehren(befehl, quelle="telegram")

    @staticmethod
    def hilfetext() -> str:
        return (
            "TRADINGBOT TELEGRAM-BEFEHLE\n"
            "/status – NEXUS, eToro, OKX und Pi-Zustand\n"
            "/pnl – Tages- und Gesamt-P&L\n"
            "/positions – nur bestätigte offene Bot-Positionen\n"
            "/decisions – letzte Kauf-/Ablehnungsentscheidungen\n"
            "/health – Broker, Internet, News, OpenAI, Telegram und Pi\n"
            "/pause – nur neue Käufe sperren; Schutzverkäufe bleiben aktiv\n"
            "/resume – neue Käufe wieder freigeben\n"
            "/report – ausführlichen Bericht sofort erzeugen\n"
            "/menu – Sitzungssicheres Schnellmenü\n"
            "/version – Version, Python, Profil und Broker-Modi\n"
            "/stats · /performance · /locks · /telegram – Diagnosen\n"
            "/scan – zusätzlichen normalen Scan anstoßen\n"
            "/database – konsistenten Decision-DB-Snapshot senden\n"
            "/ai status|off|auto|on – KI-Aufmerksamkeitsbudget\n"
            "/risk1 – konservativ (sofort) · /risk2 – ausgewogen (sofort)\n"
            "/risk3 – offensiv; immer mit zweitem Telegram-Button bestätigen\n"
            "/crypto status|standard|freqtrade – OKX-Strategiemodus\n"
            "/cryptopause – sofort keine neuen OKX-Käufe; eToro läuft weiter\n"
            "/shutdownbot – Trader kontrolliert stoppen, Pi bleibt an\n"
            "/wartend – Käufe, die auf deine Freigabe warten\n"
            "/kaufen SYMBOL – kritische KI-Warnung persönlich freigeben\n"
            "/verwerfen SYMBOL – wartenden Kauf ablehnen\n"
            "/help – diese Übersicht"
        )

    @staticmethod
    def _wartende_orders(befehl: str, args) -> str:
        """Human-Gate: Freigabe hebt nur die Warnung auf und kauft nie sofort."""
        try:
            import second_opinion as so
        except Exception as exc:
            return f"Second Opinion nicht verfuegbar: {exc}"

        if befehl == "WARTEND":
            offen = so.offene()
            if not offen:
                return "Keine wartende Kaufentscheidung."
            zeilen = ["WARTENDE KAUFENTSCHEIDUNGEN"]
            for eintrag in offen:
                urteil = eintrag.get("urteil") or {}
                rest = max(0, int((float(eintrag.get("verfaellt_um", 0)) - __import__("time").time()) // 60))
                zeilen.append(
                    f"• {eintrag.get('symbol')} ({eintrag.get('broker')}) · "
                    f"{urteil.get('einschaetzung', '?')} · verfaellt in {rest} min"
                    + (" · FREIGEGEBEN" if eintrag.get("freigegeben") else ""))
            zeilen.append("")
            zeilen.append("/kaufen SYMBOL gibt persönlich frei · /verwerfen SYMBOL lehnt ab")
            return "\n".join(zeilen)

        if not args:
            return f"Bitte ein Symbol angeben: /{befehl.lower()} BTC"
        symbol = args[0]

        if befehl == "VERWERFEN":
            if so.verwerfe("okx", symbol):
                return f"🧹 {symbol}: wartende Kaufentscheidung verworfen."
            return f"ℹ️ {symbol}: keine wartende Kaufentscheidung gefunden."

        eintrag = so.gib_frei("okx", symbol)
        if eintrag is None:
            return (f"ℹ️ {symbol}: keine gültige wartende Kaufentscheidung gefunden "
                    f"(möglicherweise bereits verfallen).")
        return (f"✅ {symbol}: von dir freigegeben. Es wird NICHT sofort gekauft. "
                f"Der nächste reguläre Scan prüft Kurs, Cash, Risiko, Stop und alle "
                f"festen Regeln vollständig neu.")

    def ausfuehren(self, befehl: str, quelle: str = "telegram", args=None) -> str:
        befehl = (befehl or "").strip().upper()
        args = [str(x).upper() for x in (args or [])]

        if befehl == "HILFE":
            return self.hilfetext()

        # Die Freigabe ist ein Human-Gate. Sie hebt keine feste Regel auf und
        # löst keine Sofortorder aus.
        if befehl in ("KAUFEN", "VERWERFEN", "WARTEND"):
            return self._wartende_orders(befehl, args)

        if befehl == "VERSION":
            return version_text()

        if befehl == "STATS":
            return stats_text()

        if befehl == "PERFORMANCE":
            return performance_text()

        if befehl == "LOCKS":
            return locks_text(self.zustand)

        if befehl == "TELEGRAM":
            return telegram_text()

        if befehl in {"CRYPTO", "CRYPTO_PAUSE"}:
            from crypto_strategy_mode import (
                CRYPTO_PAUSED, FREQTRADE_SAMPLE, NEXUS_STANDARD,
                set_mode, status as crypto_mode_status,
            )
            action = (args[0] if args else "STATUS")
            if befehl == "CRYPTO_PAUSE" or action in {"OFF", "PAUSE", "PAUSED", "STOP"}:
                state = set_mode(
                    CRYPTO_PAUSED, source=f"{quelle}:emergency",
                    reason="Krypto-Notaus", notify=False)
                return ("🚨 Krypto-Neueinstiege sind SOFORT pausiert. eToro läuft weiter; "
                        "OKX-Abgleich, Schutz und positionsgebundene Ausstiege bleiben aktiv. "
                        f"Revision {state.get('revision')}.")
            if action in {"STANDARD", "NEXUS", "NEXUS_STANDARD"}:
                state = set_mode(
                    NEXUS_STANDARD, source=quelle,
                    reason="Telegram-Moduswechsel", notify=False)
                return ("✅ NEXUS_STANDARD gilt jetzt für neue OKX-Einstiege. "
                        "Offene Positionen behalten ihre Einstiegsstrategie. "
                        f"Revision {state.get('revision')}.")
            if action in {"FREQTRADE", "FREQTRADE_SAMPLE"}:
                # Defense in depth: activation is performed only by the
                # short-lived session-bound confirmation callback.
                return ("⚠️ FREQTRADE wurde NICHT direkt aktiviert. Sende /crypto freqtrade "
                        "und bestätige anschließend den einmaligen Telegram-Button.")
            state = crypto_mode_status()
            return (f"OKX-STRATEGIEMODUS\nAktiv: {state.get('active_mode')}\n"
                    f"Revision: {state.get('revision', 0)}\n"
                    "Der globale Modus gilt nur für neue Einstiege; offene Positionen "
                    "verwenden ihren gespeicherten Strategie-Snapshot.\n"
                    "Notaus: /cryptopause")

        if befehl == "WEEKLY":
            return "WOCHENÜBERSICHT\n" + performance_text() + "\n\n" + stats_text()

        if befehl == "STATUS":
            text=status_text(
                self.zustand,
                broker=self._sicher(self.hole_broker),
                positionen=self._sicher(self.hole_positionen, []),
                risiko=self._sicher(self.hole_risiko),
                kontowert=self._sicher(self.hole_kontowert),
                waehrung=self.waehrung,
            )
            if self.market_status_cb:
                market=str(self._sicher(self.market_status_cb, "") or "")
                if market:
                    text += "\n" + market
            return text

        if befehl == "PNL":
            return pnl_text(self._sicher(self.hole_risiko), self._sicher(self.hole_positionen, []), self.waehrung)

        if befehl == "DECISIONS":
            return decisions_text(10)

        if befehl == "HEALTH":
            if self.health_text_cb:
                return str(self._sicher(self.health_text_cb, "Health nicht verfügbar."))
            return "Health-Diagnose nicht konfiguriert."

        if befehl == "SCAN":
            if self.request_scan:
                return str(self._sicher(self.request_scan, "Scan konnte nicht angestoßen werden."))
            return "Zusätzlicher Scan ist nicht verfügbar."

        if befehl == "DATABASE":
            if self.send_database:
                return str(self._sicher(self.send_database, "Datenbank konnte nicht gesendet werden."))
            return "Datenbankexport ist nicht verfügbar."

        if befehl == "VORSCHLAEGE":
            try:
                from universe_telegram import proposals_command_text
                return proposals_command_text()
            except Exception as exc:
                return f"Vorschläge nicht verfügbar: {exc}"

        if befehl == "UNIVERSUM":
            try:
                from universe_telegram import universe_command_text
                return universe_command_text(len(getattr(config,"STOCK_SYMBOLS",[]) or []))
            except Exception as exc:
                return f"Universum nicht verfügbar: {exc}"

        if befehl == "AI":
            action=(args[0] if args else "STATUS")
            if action == "STATUS":
                if self.ai_status_cb:
                    return str(self._sicher(self.ai_status_cb, "KI-Status nicht verfügbar."))
                return "KI-Status nicht verfügbar."
            if action in {"OFF","AUTO","ON"}:
                if self.ai_set_mode_cb:
                    return str(self.ai_set_mode_cb(action, quelle))
                return "KI-Fernsteuerung nicht verfügbar."
            return "Verwendung: /ai status | /ai off | /ai auto | /ai on"

        if befehl == "POSITIONEN":
            unavailable, _rt = _broker_data_temporarily_unavailable()
            if unavailable:
                return "Broker-Verbindung derzeit nicht verfügbar. Positionen werden nicht als leer dargestellt; der Bot versucht automatisch die Wiederverbindung und synchronisiert danach neu."
            positions=self._sicher(self.hole_positionen, [])
            text=positionen_text(positions, self.waehrung)
            modes=[]
            for p in positions:
                sym=str(getattr(p,"symbol","?") or "?")
                try: mode=str(self.hole_position_mode(sym) or "").upper()
                except Exception: mode=""
                if mode: modes.append(f"{sym}: {mode}")
            if modes: text += "\n\nVerwaltung: " + " · ".join(modes[:20])
            return text


        if befehl == "ORDERS":
            unavailable, _rt = _broker_data_temporarily_unavailable()
            if unavailable:
                return "Broker-Verbindung derzeit nicht verfügbar. Offene Orders können momentan nicht zuverlässig abgerufen werden. Bereits beim Broker liegende Orders bleiben davon unberührt."
            orders = self._sicher(self.hole_orders, []) or []
            if not orders:
                return "Aktuell keine offenen Orders."
            zeilen=["OFFENE ORDERS", "="*40]
            for o in orders[:40]:
                if isinstance(o, dict):
                    zeilen.append(f"{o.get('symbol','?'):10s} {o.get('side','?'):5s} {o.get('qty','?')} | {o.get('type','?')} | {o.get('status','?')}")
                else: zeilen.append(str(o))
            return "\n".join(zeilen)

        if befehl in ("RISK1","RISK2"):
            name={"RISK1":"konservativ","RISK2":"ausgewogen"}[befehl]
            try:
                from risk_profile_control import activate_risk_profile
                activate_risk_profile(name)
                return f"Risiko-Profil auf {name.upper()} gesetzt. Gilt sofort fuer neue Entscheidungen."
            except Exception as exc:
                return f"Risiko-Profil konnte nicht gesetzt werden: {exc}"

        if befehl == "RISK3":
            # Defense in depth: RISK3 darf niemals ueber diesen generischen
            # Befehlsweg direkt aktiviert werden. TelegramSteuerung erzeugt
            # dafuer einen kurzlebigen Einmal-Button und schaltet erst im
            # autorisierten Callback um.
            return (
                "⚠️ RISK3 / OFFENSIV wurde NICHT aktiviert. "
                "Dieses Profil erfordert die zweistufige Telegram-Bestätigung mit dem Einmal-Button."
            )

        if befehl == "BERICHT" and self.hole_bericht:
            return str(self._sicher(self.hole_bericht, "Bericht derzeit nicht verfügbar."))

        if befehl == "BERICHT":
            return tagesbericht_text(
                self.zustand,
                broker=self._sicher(self.hole_broker),
                positionen=self._sicher(self.hole_positionen, []),
                risiko=self._sicher(self.hole_risiko),
                kontowert=self._sicher(self.hole_kontowert),
                waehrung=self.waehrung,
                trades_heute=self._sicher(self.hole_trades_heute, []),
                zyklen=self._sicher(self.hole_zyklen),
            )

        if befehl == "PAUSE":
            vorher = self.zustand.setze(PAUSIERT, f"per {quelle} pausiert", quelle)
            return (f"PAUSIERT (vorher: {vorher}).\n\n"
                    "Es werden KEINE neuen Kaeufe mehr getaetigt.\n"
                    "Bestehende Positionen werden weiter ueberwacht und bei\n"
                    "Stop, Ziel oder Verkaufssignal geschlossen.\n\n"
                    "Zum Fortsetzen: Telegram-Befehl START JA.\n"
                    "Geheimwort und das Wort JA.")

        if befehl == "RESUME":
            vorher = self.zustand.setze(AKTIV, f"per {quelle} fortgesetzt", quelle)
            return f"NEUE KÄUFE WIEDER FREIGEGEBEN (vorher: {vorher}). Alle normalen Kaufregeln gelten unverändert."

        if befehl == "SHUTDOWNBOT":
            if self.request_shutdown:
                return str(self.request_shutdown())
            return "Kontrolliertes Stoppen ist nicht verfügbar."

        if befehl in ("STOPP", "STOP"):
            vorher = self.zustand.setze(GESTOPPT, f"per {quelle} gestoppt", quelle)
            text = [f"GESTOPPT (vorher: {vorher}).", "",
                    "Der Bot handelt gar nicht mehr -- auch keine Verkäufe."]

            positionen = self._sicher(self.hole_positionen, []) or []
            broker = self._sicher(self.hole_broker)
            if positionen:
                text.append("")
                text.append(f"WICHTIG: {len(positionen)} Position(en) sind noch offen.")
                text.append("Bei Aktien greifen die Stop-Orders weiterhin, weil sie")
                text.append("beim Broker liegen.")
                krypto = [p for p in positionen if getattr(p, "asset_type", "") == "crypto"]
                if krypto:
                    ohne_stop = True
                    try:
                        ohne_stop = broker is not None and not broker.unterstuetzt_krypto_stop()
                    except Exception:
                        __import__("logging").getLogger(__name__).debug("Best-effort-Ausnahme unterdrueckt", exc_info=True)
                    if ohne_stop:
                        text.append("")
                        text.append(f"ACHTUNG: {len(krypto)} Krypto-Position(en) haben bei")
                        text.append("diesem Broker KEINEN boersenseitigen Stop. Nach dem")
                        text.append("STOPP sind sie voellig unbeaufsichtigt.")
                        text.append("Falls du sie absichern willst, ist PAUSE die bessere")
                        text.append("Wahl -- dann verkauft der Bot weiterhin bei Stop.")
            return "\n".join(text)

        if befehl == "START":
            vorher = self.zustand.setze(AKTIV, f"per {quelle} freigegeben", quelle)
            return (f"HANDEL FREIGEGEBEN (vorher: {vorher}).\n\n"
                    "Der Bot kauft und verkauft wieder eigenstaendig nach\n"
                    "seinen Regeln.")

        return (f"Unbekannter Befehl: {befehl}\n\n"
                + self.hilfetext())
