"""Personal two-step confirmation; callbacks never touch a broker."""
from . import control


def keyboard(pid, nonce, *, second=False):
    return [[{"text": "Kaufplan verbindlich bestätigen" if second else "Plan prüfen und bestätigen",
              "callback_data": f"pulsar:{pid}:{nonce}:c"},
             {"text": "Ablehnen", "callback_data": f"pulsar:{pid}:{nonce}:r"}]]


def text(plan):
    sessions = int(plan.get("max_hold_sessions") or 20)
    return (f"PULSAR · {plan['symbol']} · {plan['environment']}\n"
            f"Konto {plan['account'][:8]} · {plan['quantity']:g} Stück\n"
            f"Referenz {plan['price']:.4f} {plan['currency']} · Band ±0,3 %\n"
            f"Stop {plan['stop']:.4f} · Broker-TP {plan['take']:.4f}\n"
            f"Kursrisiko {plan['quantity']*(plan['price']-plan['stop']):.2f} {plan['currency']}\n" +
            (f"Max. Einstieg {plan['max_entry_price']:.4f} · Kostenreserve {plan['cost_budget']:.2f} {plan['currency']}\n"
             if "cost_budget" in plan else "Kostenrahmen muss vor einer neuen Freigabe ergaenzt werden.\n") +
            (f"Max. {sessions} Handelstage; ein Drittel bei +2R, Rest mit nachgezogenem Stop.\n"
             if plan.get("partial_at_2R") else f"Max. {sessions} Handelstage; keine Teilorder, Stop wird nachgezogen.\n") +
            "Die Freigabe erlaubt genau einen Kaufversuch nach erneuter Core-Prüfung.")


def hype_text(card, *, mode):
    hype = card.get("hype") or {}
    social = hype.get("social") or {}
    price = hype.get("price") or {}
    market = card.get("market") or {}
    earnings = card.get("earnings") or {}
    lines = [f"PULSAR-HYPE · {card['symbol']} · {card.get('name') or ''}".rstrip(" ·")]
    trigger = hype.get("trigger") or {}
    if trigger:
        lines.append(f"Auslöser {trigger.get('kind')}: {trigger.get('detail') or ''}".rstrip(": "))
    confirmations = hype.get("confirmations") or {}
    if confirmations:
        labels = {"volumen": "Volumen", "zweite_social_familie": "zweite Social-Familie", "kurs": "Kurs"}
        lines.append("Bestätigt: " + ", ".join(labels[k] for k, v in confirmations.items() if v.get("ok") and k in labels)
                     + f" ({hype.get('confirmed_count', 0)} von 3)")
    lines += [social.get("detail") or "Social-Beleg fehlt",
              (hype.get("stocktwits") or {}).get("detail") or "StockTwits: kein Spike belegt",
              price.get("detail") or "Kursbeleg fehlt"]
    squeeze = hype.get("squeeze") or {}
    if squeeze.get("detail"):
        lines.append("Squeeze-Merkmal: " + str(squeeze["detail"]))
    if market.get("price"):
        lines.append(f"Kurs {market['price']:.2f} USD · Umsatz {round((market.get('turnover_usd') or 0)/1e6)} Mio. USD/Tag")
    if hype.get("finance_note"):
        lines.append(hype["finance_note"])
    lines.append("Existenzrisiko: " + ("KEIN Befund (Insolvenz/Delisting/Aussetzung/Betrug in 90 Tagen geprüft)"
                                        if not (hype.get("existence_risk") or {}).get("blocked") else "BLOCKIERT"))
    lines.append("Nächste Earnings: " + (earnings.get("date") or "kein Termin geliefert"))
    lines.append("Nur Information — keine Order, keine Kursprognose. Fehlsignale (Pump&Dump) sind möglich.")
    lines.append("Freigabemodus aktiv: Ein Kaufplan folgt im NY-Handelsfenster zur persönlichen Bestätigung."
                 if mode == "FREIGABE" else
                 "Beobachtungsmodus: Es wird nichts nominiert. FREIGABE-Modus aktiviert die Kaufplan-Nominierung.")
    return "\n".join(lines)


def notify_hype(card, *, mode):
    from notifier import send_telegram
    return bool(send_telegram(hype_text(card, mode=mode)))


def notify_nomination(item):
    from notifier import send_telegram_bound
    message_id = send_telegram_bound(text(item["plan"])+"\nErste Bestätigung: 30 Minuten.",
                                     keyboard(item["id"], item["nonce"]))
    if message_id:
        control.bind_message(item["id"], message_id)
    return bool(message_id)


def handle(data, *, chat, user, message_id):
    pieces = data.split(":")
    if len(pieces) != 4 or pieces[0] != "pulsar":
        raise control.Blocked("Ungueltiger PULSAR-Button")
    _, pid, nonce, action = pieces
    result = control.callback(pid, nonce, "confirm" if action == "c" else "reject" if action == "r" else "",
                              chat=chat, user=user, message_id=message_id)
    if result["status"] == "CONFIRMING":
        return {"text": text(result["plan"])+"\nZweite Bestätigung innerhalb von 60 Sekunden.",
                "keyboard": keyboard(pid, result["nonce"], second=True)}
    return {"text": "PULSAR abgelehnt. Heute wird kein weiterer Kandidat nominiert." if result["status"] == "DECLINED"
            else "PULSAR-Plan persönlich bestätigt. Der Core prüft vor einer Übermittlung erneut alle Bedingungen."}
