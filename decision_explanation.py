"""Plain German explanations of recorded decisions, without AI or new data.

Presentation only: this module cannot grant an order or rewrite a historical
decision. Missing evidence stays missing; approval and broker execution differ.
"""
from __future__ import annotations

import math
import re


def _number(value):
    if value is None or isinstance(value, bool) or value == "":
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (ValueError, TypeError):
        return None


def explain_decision(row: dict, payload: dict | None = None) -> dict:
    """Return one highlighted sentence plus evidence keys, never guessed facts."""
    payload = payload if isinstance(payload, dict) else {}
    status = str(row.get("status") or "UNKNOWN").upper()
    execution = str(row.get("execution_status") or "").upper()
    checks = payload.get("signal_checks") or row.get("signal_checks") or {}
    checks = checks if isinstance(checks, dict) else {}
    failed = {key for key, value in checks.items()
              if isinstance(value, dict) and value.get("passed") is False}
    reason = str(row.get("reason") or payload.get("reason") or row.get("signal_reason") or "")
    blocker = str(row.get("blocked_by") or "")
    search = (blocker + " " + reason).casefold()
    side = str(row.get("side") or payload.get("side") or payload.get("action") or "").upper()
    sale = side in {"SELL", "EXIT", "VERKAUF"}
    action = "Verkauf" if sale else "Kauf"
    evidence = []

    def result(text, kind="warning"):
        return {"text": text, "kind": kind, "evidence": evidence, "version": 1}

    if status == "NO_SIGNAL":
        reasons = []
        if "volume_positive" in failed:
            value = _number((checks.get("volume_positive") or {}).get("volume", (checks.get("volume_positive") or {}).get("value")))
            if value is None:
                value = _number((row.get("metrics") or {}).get("volume_5m", payload.get("volume_5m")))
            reasons.append("für die geprüfte Kerze kein Handelsvolumen gemeldet wurde" if value == 0
                           else "kein positives Handelsvolumen belegt ist")
            evidence.append("signal_checks.volume_positive")
        if {"rsi_cross_above_30", "tema_rising"}.issubset(failed):
            reasons.append("die erforderliche Kurserholung noch nicht bestätigt ist")
            evidence.extend(["signal_checks.rsi_cross_above_30", "signal_checks.tema_rising"])
        else:
            for key, sentence in (("rsi_cross_above_30", "das erforderliche Erholungssignal noch fehlt"),
                                  ("tema_rising", "der kurzfristige Durchschnittskurs nicht steigt")):
                if key in failed:
                    reasons.append(sentence); evidence.append("signal_checks." + key)
        if "tema_at_or_below_bb_middle" in failed:
            reasons.append("der kurzfristige Durchschnittskurs über der vorgesehenen Einstiegszone liegt")
            evidence.append("signal_checks.tema_at_or_below_bb_middle")
        if reasons:
            return result(f"Kein {action}, weil " + " und ".join(reasons) + ".", "neutral")
        return result(f"Kein {action}: Die Strategie hat für diesen Zeitpunkt kein Einstiegssignal gespeichert."
                      if not sale else "Kein Verkauf: Die Strategie hat für diesen Zeitpunkt kein Ausstiegssignal gespeichert.", "neutral")

    if execution in {"UNKNOWN", "UNCLEAR", "RECONCILIATION_REQUIRED"} or status in {"UNKNOWN_AFTER_SUBMIT", "RECONCILIATION_REQUIRED"}:
        evidence.append("execution_status")
        return result(f"Der Ausgang des {action.lower()}s ist noch unklar und muss mit den Brokerbelegen abgeglichen werden.")
    if status in {"SYSTEM_ERROR", "ERROR", "FAILED"}:
        evidence.append("status")
        return result(f"Der {action} konnte wegen eines technischen Fehlers nicht zuverlässig beurteilt werden.")

    if status in {"APPROVED", "SELL_APPROVED", "EXIT_APPROVED"}:
        evidence.append("status")
        if status != "APPROVED":
            action = "Verkauf"
        why = []
        if action == "Verkauf":
            exit_reason = str(row.get("exit_reason") or payload.get("exit_reason") or reason).casefold()
            exits = [
                (r"^(?:trailing(?:_stop)?|trailing-stop)(?:$|[ :;,])|nachgezogene.*(?:erreicht|unterschritten)", "der Kurs die nachgezogene Schutzgrenze erreicht hat"),
                (r"^(?:stop_loss|stoploss|native_stop)(?:$|[ :;,])|stop[- ]loss (?:wurde )?(?:erreicht|ausgelöst)|stopp?grenze.*unterschritten", "die festgelegte Verlustgrenze der Position erreicht wurde"),
                (r"^(?:take_profit|takeprofit)(?:$|[ :;,])|take[- ]profit (?:wurde )?erreicht|gewinnziel (?:wurde )?erreicht", "das festgelegte Gewinnziel erreicht wurde"),
                (r"^roi(?:$|[ :;,])", "das für diese Haltedauer festgelegte Gewinnziel erreicht wurde"),
                (r"^(?:exit_signal|sell_signal)(?:$|[ :;,])", "die Strategie ein Ausstiegssignal gespeichert hat"),
                (r"^(?:manual|manual_exit)(?:$|[ :;,])|manueller verkaufsauftrag", "ein manueller Verkaufsauftrag vorliegt"),
            ]
            for pattern, text in exits:
                if re.search(pattern, exit_reason):
                    why.append(text); evidence.append("exit_reason" if row.get("exit_reason") or payload.get("exit_reason") else "reason"); break
        elif not failed:
            passed = {key for key, value in checks.items()
                if isinstance(value, dict) and value.get("passed") is True}
            if {"rsi_cross_above_30", "tema_rising"}.issubset(passed):
                why.append("das Erholungssignal bestätigt ist und der kurzfristige Durchschnittskurs steigt")
                evidence.extend(["signal_checks.rsi_cross_above_30", "signal_checks.tema_rising"])
            else:
                for key, text in (("rsi_cross_above_30", "das erforderliche Erholungssignal bestätigt ist"),
                                  ("tema_rising", "der kurzfristige Durchschnittskurs steigt")):
                    if key in passed: why.append(text); evidence.append("signal_checks." + key)
            if "tema_at_or_below_bb_middle" in passed:
                why.append("der Durchschnittskurs noch in der vorgesehenen Einstiegszone liegt")
                evidence.append("signal_checks.tema_at_or_below_bb_middle")
            if "volume_positive" in passed:
                why.append("Handelsvolumen vorhanden ist"); evidence.append("signal_checks.volume_positive")
            # Old entries may contain the exact strategy sentence, but no
            # structured checks. Recognise that complete known phrase only.
            if not why and "RSI kreuzt 30 aufwaerts; TEMA unter BB-Mitte und steigend" in reason:
                why.append("das Erholungssignal bestätigt ist und der steigende Durchschnittskurs noch in der vorgesehenen Einstiegszone liegt")
                evidence.append("reason")
        if why:
            headline = f"{action} genehmigt, weil " + " und ".join(why)
        elif failed and action == "Kauf":
            headline = "Kaufgenehmigung gespeichert, obwohl gleichzeitig nicht erfüllte Signalprüfungen vorliegen; diese widersprüchlichen Belege müssen geprüft werden"
        else:
            headline = f"{action} genehmigt; der genaue verständliche Freigabegrund ist in den vorhandenen Daten noch nicht belegt"
        if execution in {"REJECTED", "CANCELED_NO_FILL", "CANCELLED_NO_FILL", "EXPIRED"}:
            detail = {"REJECTED": "der Broker hat den Auftrag jedoch abgelehnt",
                      "EXPIRED": "der Auftrag ist beim Broker jedoch verfallen"}.get(execution,
                         "der Auftrag wurde jedoch ohne Ausführung storniert")
            evidence.append("execution_status")
            return result(headline + "; " + detail + ".")
        if execution in {"PARTIAL", "PARTIALLY_FILLED"}:
            evidence.append("execution_status")
            return result(headline + "; bisher wurde nur ein Teil ausgeführt.")
        if execution == "FILLED":
            evidence.append("execution_status")
            return result(headline + "; der Broker meldet die vollständige Ausführung.", "warning" if failed else "approved")
        return result(headline + "; eine vollständige Ausführung ist damit noch nicht bestätigt.")

    # Specific recorded reasons precede broad classes. No quantities, model
    # invocation, account status or market conditions are inferred from a label.
    known = [
        (("risk_equity_basis_review", "risikobasis", "equity_basis"), "die Risikoberechnung noch einem eindeutig bestätigten Brokerkonto zugeordnet werden muss"),
        (("etoro_protection_price_rule_unproven", "etoro_protection_price_mismatch", "schutzabgleich", "schutzwerte stimmen nicht"), "die beim Broker beobachteten Schutzwerte noch nicht mit dem gespeicherten Schutzplan übereinstimmend bestätigt sind"),
        (("anlaufsperre", "sicherheitswartezeit", "startup"), "nach dem Start noch die vorgesehene Sicherheitswartezeit läuft"),
        (("markt geschlossen", "markt ist geschlossen", "börse geschlossen", "boerse geschlossen", "market_closed", "us-markt geschlossen"), "die Börse für dieses Instrument derzeit geschlossen ist"),
        (("reconciliation", "reconcil", "zuordnung", "abgleich", "unbekannter order"), "ein früherer Auftrag oder Bestand noch eindeutig mit dem Broker abgeglichen werden muss"),
        (("liquidität", "liquiditaet", "guthaben", "kapital reicht", "insufficient", "not enough"), "die Kapitalprüfung keine ausreichende verfügbare Summe bestätigt"),
        (("spread",), "der Abstand zwischen Kauf- und Verkaufspreis die vorgeschriebene Prüfung nicht besteht"),
        (("mindestorder", "minimum order", "min_notional", "minsz"), "die Mindestgröße des Brokers für diesen Auftrag nicht bestätigt werden konnte"),
        (("tagesverlust", "daily_loss", "drawdown"), "die Prüfung der festgelegten Verlustgrenzen keine Freigabe ergibt"),
        (("maximale anzahl", "max_open", "positionslimit"), "das erlaubte Positionslimit keine weitere Position zulässt"),
        (("stale", "veraltet", "zu alt"), "die erforderlichen Daten nicht mehr aktuell genug sind"),
        (("timeout", "zeitrahmen", "zeitüberschreitung"), "eine erforderliche Antwort nicht rechtzeitig vorlag"),
        (("precheck", "vorprüfung", "vorpruefung"), "die notwendige Vorprüfung noch keine Freigabe ergeben hat"),
        (("community",), "die benötigte Breite unabhängiger Community-Belege nicht ausreichend nachgewiesen ist"),
        (("katalysator", "catalyst", "originalbeleg"), "ein ausreichend belegter aktueller wirtschaftlicher Anlass fehlt"),
        (("pausiert", "pause", "deaktiviert", "disabled"), "neue Käufe in der zuständigen Steuerung angehalten sind"),
        (("risiko", "risk"), "mindestens eine festgelegte Risikoregel den Auftrag nicht zulässt"),
    ]
    for words, text in known:
        if any(word in search for word in words):
            evidence.append("reason" if reason else "blocked_by")
            return result(f"{action} nicht freigegeben, weil {text}.")
    if status in {"AI_BLOCKED", "AI_HOLD"}:
        return result(f"{action} nicht freigegeben: Die gespeicherte KI-Prüfung hat keine Freigabe ergeben; der technische Eintrag enthält den genauen Prüfgrund.")
    if status in {"BLOCKED", "REJECTED", "DENIED", "HOLD"} or "BLOCK" in status:
        return result(f"{action} nicht freigegeben: Die gespeicherte Begründung lässt sich noch keinem eindeutigen verständlichen Hauptgrund zuordnen; bitte die Details prüfen.")
    return result("Für diesen Eintrag ist keine eindeutige Genehmigung oder Ablehnung mit verständlichem Hauptgrund gespeichert.", "neutral")
