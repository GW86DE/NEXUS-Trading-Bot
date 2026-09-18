"""Telegram-Darstellung der Human-in-the-loop-Universumsfreigabe."""
from __future__ import annotations

from approved_universe import approved_stock_rows
from universe_proposals import approve, format_details, get, list_proposals, reject, request_review, status_text


def handle_callback(data: str) -> dict:
    parts=str(data or "").split(":",2)
    if len(parts)!=3 or parts[0] != "univ":
        return {"text":"Unbekannte Aktion."}
    action,pid=parts[1],parts[2]
    if action == "details":
        return {"text":format_details(get(pid))}
    if action == "review":
        ok,detail=request_review(pid,actor="telegram")
        return {"text":(("🔍 " if ok else "⚠️ ")+f"{pid}: {detail}."+(" Ergebnis folgt nach der deterministischen eToro-Prüfung im sicheren Hauptzyklus." if ok else ""))}
    if action == "reject":
        ok,detail=reject(pid,actor="telegram")
        return {"text":(("❌ " if ok else "⚠️ ")+f"{pid}: {detail}.")}
    if action == "approve":
        ok,detail=approve(pid,actor="telegram")
        text=(("✅ " if ok else "⚠️ ")+f"{pid}: {detail}.")
        if ok:
            text += "\n\nWichtig: Der Wert ist NICHT sofort handelbar. Er wird erst beim nächsten Bot-Start geladen und dort erneut normal durch eToro qualifiziert."
        return {"text":text}
    return {"text":"Unbekannte Universumsaktion."}


def technical_result_message(proposal: dict) -> tuple[str,list]:
    review=dict(proposal.get("technical_review") or {})
    pid=str(proposal.get("id") or "")
    if bool(review.get("passed")):
        text=(
            "✅ AUFNAHMEPRÜFUNG BESTANDEN\n"
            f"{proposal.get('symbol','?')} · {proposal.get('company','')}\n"
            f"eToro: {review.get('etoro_symbol_full','?')} · Instrument-ID {review.get('instrument_id','?')}\n"
            f"Historie: {review.get('history_bars',0)} Tageskerzen\n"
            f"Ø Dollar-Volumen: {float(review.get('avg_dollar_volume',0) or 0):,.0f} USD\n"
            f"Median Dollar-Volumen: {float(review.get('median_dollar_volume',0) or 0):,.0f} USD\n"
            f"Spread: {float(review.get('spread_pct',0) or 0)*100:.3f}%\n"
            f"eToro-Kostenklasse: {float(review.get('cost_pct',0) or 0)*100:.3f}% Roundtrip\n\n"
            "Noch NICHT im Universum. Erst deine zweite Freigabe darf den Wert dauerhaft aufnehmen; aktiv wird er erst nach einem Neustart."
        )
        kb=[[{"text":"✅ Ins Universum aufnehmen","callback_data":f"univ:approve:{pid}"}],
            [{"text":"❌ Verwerfen","callback_data":f"univ:reject:{pid}"},{"text":"ℹ️ Details","callback_data":f"univ:details:{pid}"}]]
        return text,kb
    reason=str(review.get("summary") or "technische Aufnahmeprüfung nicht bestanden")
    return (f"❌ AUFNAHMEPRÜFUNG NICHT BESTANDEN\n{proposal.get('symbol','?')} · {proposal.get('company','')}\n\n{reason}\n\nDer Wert wurde nicht aufgenommen und kann aus diesem Vorschlag nicht handelbar werden."),[]


def proposals_command_text() -> str:
    rows=list_proposals(limit=8)
    lines=[status_text()]
    if rows:
        lines += ["", "Letzte Vorschläge:"]
        for p in rows:
            lines.append(f"• {p.get('symbol','?')} · {p.get('status','?')} · {p.get('id','')}")
    return "\n".join(lines)


def _beobachtet_text() -> str:
    """Was der Bot TATSAECHLICH beobachtet -- nicht der statische Katalog.

    Bis v8.1.2 nannte /universum nur die Zahl der konfigurierten Aktien. Das
    ist die Vorauswahl, nicht das Universum, und beantwortete die Frage
    "wie viele sind jetzt drin?" nicht.
    """
    try:
        from universe_overview import dashboard_text
        return dashboard_text()
    except Exception:
        return ""


def universe_command_text(configured_count: int | None = None) -> str:
    approved=approved_stock_rows()
    lines=["📚 HANDELSUNIVERSUM"]
    beobachtet=_beobachtet_text()
    if beobachtet:
        lines += [f"Beobachtet: {beobachtet}",
                  "„Im Universum\u201c heißt beobachtet -- nicht gekauft."]
    lines.append(f"Menschlich freigegebene Research-Erweiterungen: {len(approved)}")
    if configured_count is not None: lines.append(f"Katalog (Vorauswahl) beim Prozessstart: {configured_count} Aktien")
    for row in approved[-15:]: lines.append(f"• {row.get('symbol')} · {row.get('sector','UNCLASSIFIED')} · Research-Freigabe")
    if approved: lines += ["", "Neue Freigaben werden erst nach einem Bot-Neustart aktiv und dann erneut von eToro qualifiziert."]
    return "\n".join(lines)
