"""Reine Projektionen des Risikozustands (10.8.0, Schritt 2).

``scope`` und ``status`` sind Regeln ohne Zustand und ohne I/O. Bis 10.7.1 lagen
sie in ``risk_basis_review`` -- das ``risk_manager`` (Anzeige der Basispruefung)
und ``etoro_risk_maintenance`` (Belegpruefung) importierten, waehrend
``risk_basis_review`` selbst ``RiskState`` und die Wartung importierte: zwei
Import-Zyklen. Hier unten kann jedes Modul sie nutzen; ``risk_basis_review``
exportiert beide weiter, damit Aufrufer und Tests unveraendert bleiben.
"""
from __future__ import annotations

import json


def scope(value):
    try:
        value = json.loads(value) if isinstance(value, str) else value
        if (not isinstance(value, list) or len(value) != 3
                or value[0] not in {"etoro", "okx"} or value[1] not in {"DEMO", "LIVE"}
                or not isinstance(value[2], str) or not value[2].strip()):
            return None
        return tuple(value)
    except (TypeError, ValueError):
        return None


def status(raw, *, observed_basis="", observed_scope="", observed_equity=None):
    """Pure, bounded UI projection; this result cannot authorize a migration."""
    stored_scope, current_scope = scope(raw.get("risk_scope_key")), scope(observed_scope)
    stored_basis = str(raw.get("equity_basis_key") or "")
    receipt = raw.get("basis_review_receipt") or {}
    forward = receipt.get("method") == "FORWARD_ONLY_FIRST_OBSERVATION"
    reasons = []
    if raw.get("equity_basis_review_required"):
        reasons.append("RISK_EQUITY_BASIS_REVIEW")
    if observed_basis and stored_basis and stored_basis != observed_basis:
        reasons.append("RISK_BASIS_MISMATCH")
    if current_scope and stored_scope and current_scope != stored_scope:
        reasons.append("RISK_ACCOUNT_SCOPE_MISMATCH")
    if reasons and not stored_scope:
        reasons.append("RISK_HISTORICAL_ACCOUNT_UNPROVEN")
    if raw.get("pending_persistence_operations"):
        reasons.append("RISK_PERSISTENCE_UNCONFIRMED")
    return {"schema_version": 1, "status": "REVIEW_REQUIRED" if reasons else "OK",
        "blocks_entries": bool(reasons), "reason_codes": reasons,
        "stored_basis": stored_basis, "observed_basis": str(observed_basis or ""),
        "stored_scope": list(stored_scope) if stored_scope else None,
        "observed_scope": list(current_scope) if current_scope else None,
        "day": str(raw.get("current_date") or ""),
        "day_start_equity": raw.get("day_start_equity"),
        "observed_equity": observed_equity,
        "period_started_at": receipt.get("started_at") if forward else None,
        "historical_results_assignment": "LEGACY_UNASSIGNED" if forward else raw.get("risk_scope_origin"),
        "automatic_rebase_allowed": False,
        "required_evidence": (["Unabhaengiger Risiko-Checkpoint desselben Tages mit belegtem Konto/Umgebung/Waehrung",
            "Identische Tagesbasis, Zaehler und Finanzbelege; aktuelle Equity allein reicht nicht"] if reasons else []),
        "detail": ("Neue Kaeufe lokal gesperrt: Risikobasis/Kontozuordnung pruefen. "
            "Aktueller Kontostand allein belegt keine historische Tagesbasis. "
            "Verkaeufe und bestehender Schutz bleiben separat geprueft." if reasons else
            ("Kontogebundene Risikoperiode seit " + str(receipt.get("started_at")) +
             ". Fruehere Ergebnisse bleiben unzugeordnet archiviert; weitere Kaufbedingungen gelten separat."
             if forward else "Keine offene Risikobasispruefung; weitere Kaufbedingungen gelten separat."))}
