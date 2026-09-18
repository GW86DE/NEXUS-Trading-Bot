"""Kompakte, brokernahe Trade-Meldungen fuer Telegram.

Grundsatz: Keine erfundenen Nullwerte. Ein Feld wird nur angezeigt, wenn die
zugrunde liegende Information tatsaechlich vorhanden ist. Geschaetzte Kosten
werden immer als Schaetzung gekennzeichnet.
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo
from position_manager import fmt_money, fmt_price
import config


def _local_time(value=None):
    zone = ZoneInfo(str(getattr(config, "LOCAL_TIMEZONE", "Europe/Berlin")))
    if value is None:
        return datetime.now(zone)
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=zone)
    return parsed.astimezone(zone)


def _pct(value):
    if value is None:
        return None
    try:
        return f"{float(value) * 100:+.2f} %".replace(".", ",")
    except Exception:
        return None


def _positive(value):
    try:
        return float(value) > 0
    except Exception:
        return False


def buy_message(*, symbol, asset_type, qty, fill_price, currency, position_value,
                account_equity=None, stop=None, take=None, risk_amount=None,
                risk_pct=None, rr=None, reason="", ml_probability=None,
                profile="", entry_time=None, estimated_cost=0.0,
                net_edge_pct=None, event_summary="", edge_model_reason="",
                plausible_move_pct=None):
    ts = entry_time or _local_time().isoformat(timespec="seconds")
    try:
        ts = _local_time(ts).strftime("%d.%m.%Y %H:%M:%S")
    except Exception:
        ts = str(ts)

    lines = [
        f"🟢 KAUF BESTÄTIGT · {symbol}",
        f"Menge: {float(qty):g}",
        f"Kaufkurs: {fmt_price(fill_price)} {currency}",
        f"Kaufwert: {fmt_money(position_value)} {currency}",
        f"Zeit: {ts}",
    ]

    show_details = bool(getattr(config, "NOTIFY_TRADE_DETAILS", True))
    if show_details and _positive(stop):
        lines.append(f"Stop-Loss: {fmt_price(stop)} {currency}")
    if show_details and _positive(take):
        lines.append(f"Take-Profit: {fmt_price(take)} {currency}")
    if show_details and _positive(risk_amount):
        pct = _pct(risk_pct)
        suffix = f" ({pct})" if pct else ""
        lines.append(f"Risiko bis Stop: {fmt_money(risk_amount)} {currency}{suffix}")
    if show_details and _positive(rr):
        lines.append(f"Chance/Risiko: {float(rr):.2f}:1".replace(".", ","))
    if show_details and _positive(account_equity):
        lines.append(f"Kontowert: {fmt_money(account_equity)} {currency}")
    if show_details and _positive(estimated_cost):
        lines.append(f"Gebühren/Kosten geschätzt: {fmt_money(estimated_cost)} {currency}")

    details = []
    if show_details and profile:
        details.append(f"Profil: {str(profile).upper()}")
    if show_details and reason and str(reason).strip().lower() not in {"order ausgefuehrt", "order ausgeführt"}:
        details.append(f"Grund: {reason}")
    if show_details and ml_probability is not None:
        try:
            details.append(f"ML-Wahrscheinlichkeit: {float(ml_probability)*100:.1f} %".replace(".", ","))
        except Exception:
            __import__("logging").getLogger(__name__).debug("Best-effort-Ausnahme unterdrueckt", exc_info=True)
    if show_details and net_edge_pct is not None:
        try:
            details.append(f"Erwartete Netto-Edge: {float(net_edge_pct)*100:+.2f} %".replace(".", ","))
        except Exception:
            __import__("logging").getLogger(__name__).debug("Best-effort-Ausnahme unterdrueckt", exc_info=True)

    if details:
        lines.extend(["", *details])
    return "\n".join(lines)


def sell_message(*, symbol, asset_type, qty, entry_price=None, exit_price=None,
                 currency="", invested_value=None, proceeds=None, pnl=None,
                 pnl_pct=None, reason="", holding_text=None,
                 account_equity=None, daily_realized=None, stop=None, take=None,
                 order_label="VERKAUF", estimated_exit_cost=0.0,
                 remaining=None, pnl_complete=True, tagesbuch=None):
    if pnl is None:
        icon = "🟠"
    else:
        icon = "🟢" if float(pnl) >= 0 else "🔴"

    label = str(order_label or "VERKAUF").replace("BOT-", "").strip()
    lines = [
        f"{icon} {label} BESTÄTIGT · {symbol}",
        f"Menge: {float(qty):g}",
    ]
    if _positive(entry_price):
        lines.append(f"Einstand: {fmt_price(entry_price)} {currency}")
    if _positive(exit_price):
        lines.append(f"Verkaufskurs: {fmt_price(exit_price)} {currency}")
    if _positive(invested_value):
        lines.append(f"Einstandswert: {fmt_money(invested_value)} {currency}")
    if _positive(proceeds):
        lines.append(f"Verkaufserlös: {fmt_money(proceeds)} {currency}")

    lines.append("")
    if pnl is not None:
        pct = _pct(pnl_pct)
        suffix = f" ({pct})" if pct else ""
        lines.append(f"Gewinn/Verlust: {fmt_money(pnl)} {currency}{suffix}")
    else:
        lines.append("Gewinn/Verlust: nicht sicher berechenbar")

    show_details = bool(getattr(config, "NOTIFY_TRADE_DETAILS", True))
    if show_details and holding_text:
        lines.append(f"Haltedauer: {holding_text}")
    if show_details and tagesbuch is not None:
        # v8.1.5: Das Tagesergebnis kommt aus den einzelnen Buchungen des
        # Handelsbuchs, nicht aus einem fortgeschriebenen Summenzaehler.
        # Am 25.08.2026 meldete der Zaehler -226,60 USD, waehrend genau ein
        # Trade geschlossen war: DVLT mit -44,40 USD. Der Zaehler hatte keine
        # Belege -- das Handelsbuch hat sie.
        lines.append("")
        lines.extend(tagesbuch.zeilen(mit_buchungen=False))
    elif show_details and daily_realized is not None and pnl_complete:
        lines.append(f"Heute realisiert: {fmt_money(daily_realized)} {currency}")
    elif show_details and not pnl_complete:
        lines.append("Tages-P&L: unvollständig (mindestens ein Verkauf ohne sichere Einstandsdaten)")
    if show_details and _positive(remaining):
        lines.append(f"Restposition: {float(remaining):g}")
    if show_details and _positive(account_equity):
        lines.append(f"Kontowert: {fmt_money(account_equity)} {currency}")
    if show_details and _positive(estimated_exit_cost):
        lines.append(f"Exit-Gebühren geschätzt: {fmt_money(estimated_exit_cost)} {currency}")
    if show_details and reason:
        lines.extend(["", f"Grund: {reason}"])
    return "\n".join(lines)


def connection_message(title, port=None, extra=""):
    lines = [str(title)]
    if extra:
        lines.append(str(extra))
    return "\n".join(lines)
