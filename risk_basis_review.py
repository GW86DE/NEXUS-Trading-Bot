"""Offline risk-basis review. Equal balances alone never prove account identity.

This module publishes ``status``. The optional historical maintenance accepts
an independently retained, correctly scoped checkpoint only if *all* economic
state is identical. It changes metadata, never money, dates or loss counters.
An unbound legacy state without that evidence remains blocked here. Separately,
etoro_risk_period can start a forward period on a previously unstarted day from
fresh account evidence; it archives legacy history without reassigning it.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date
import hashlib
import json
from pathlib import Path

METADATA = frozenset({"equity_basis_key", "equity_basis_changed_at",
    "equity_basis_review_required", "equity_basis_review_reason", "risk_scope_key",
    "risk_scope_origin", "basis_review_receipt", "result_period_scope"})

# 10.1.10: Die native Equity-Reihe (FX-robuste Tagesbremse) besteht aus
# Anzeige-/Bremszustand mit festen Standardwerten. Aeltere, unveraenderlich
# archivierte Checkpoints kennen diese Felder nicht; sie sind deshalb optional
# und werden fuer den Zustandsvergleich mit ihren Defaults normalisiert, damit
# historische Belege gueltig bleiben und nichts rueckwirkend entwertet wird.
OPTIONAL_FIELDS_10_1_10 = frozenset({
    "day_start_native_equity", "last_native_equity", "native_equity_ccy",
    "native_drawdown_pct", "fx_drawdown_pct"})

# 10.2.0: eingefrorene Tagesstartkurse der Mehrlagen-Nativreihe. Gleiche
# Politik: aeltere Checkpoints kennen das Feld nicht, es ist optional und wird
# fuer den Vergleich mit seinem Default normalisiert.
OPTIONAL_FIELDS_10_2_0 = frozenset({"native_fx_day_start"})
OPTIONAL_FIELDS = OPTIONAL_FIELDS_10_1_10 | OPTIONAL_FIELDS_10_2_0


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


def _hash(data):
    return hashlib.sha256(data).hexdigest()


def plan(raw, checkpoint, *, expected_basis, expected_scope, today):
    """A checkpoint must prove the same day/account/basis AND economic state."""
    from risk_manager import RiskState
    try:
        RiskState._decode_payload(raw)
        RiskState._decode_payload(checkpoint)
        # Equal malformed financial fields are still not valid evidence. This
        # maintenance gate covers all scalar counters/costs, not just equity.
        from dataclasses import fields
        import math
        financial_fields = ({field.name for field in fields(RiskState)}
                            - METADATA - OPTIONAL_FIELDS)
        signed = {"realized_pnl_today", "lifetime_realized_pnl", "equity_drawdown_pct",
                  "native_drawdown_pct", "fx_drawdown_pct"}
        for payload in (raw, checkpoint):
            if not financial_fields.issubset(payload):
                raise ValueError("RISK_CHECKPOINT_FINANCIAL_FIELDS_MISSING")
            for field in fields(RiskState):
                if field.name not in payload or field.type not in {int, float}:
                    continue
                value = payload[field.name]
                if (isinstance(value, bool) or not isinstance(value, (int, float))
                        or not math.isfinite(value) or (field.type is int and int(value) != value)
                        or (field.name not in signed and value < 0)):
                    raise ValueError("RISK_CHECKPOINT_FINANCIAL_SCHEMA_UNPROVEN")
        if raw.get("risk_schema_version") != 2 or checkpoint.get("risk_schema_version") != 2:
            raise ValueError("RISK_SCHEMA_REVIEW_REQUIRED")
        target_scope = scope(expected_scope)
        if not target_scope or scope(checkpoint.get("risk_scope_key")) != target_scope:
            raise ValueError("RISK_CHECKPOINT_ACCOUNT_UNPROVEN")
        if scope(raw.get("risk_scope_key")) not in (None, target_scope):
            raise ValueError("RISK_CHECKPOINT_ACCOUNT_MISMATCH")
        if (not expected_basis or checkpoint.get("equity_basis_key") != expected_basis
                or expected_basis.split(":")[-2] != target_scope[0]):
            raise ValueError("RISK_CHECKPOINT_BASIS_UNPROVEN")
        if checkpoint.get("equity_basis_review_required") or checkpoint.get("pending_persistence_operations"):
            raise ValueError("RISK_CHECKPOINT_NOT_CONFIRMED")
        if raw.get("pending_persistence_operations"):
            raise ValueError("RISK_PERSISTENCE_UNCONFIRMED")
        day = str(today)
        date.fromisoformat(day)
        if raw.get("current_date") != day or checkpoint.get("current_date") != day:
            raise ValueError("RISK_CHECKPOINT_DAY_MISMATCH")
        from dataclasses import MISSING
        optional_defaults = {}
        for f in fields(RiskState):
            if f.name not in OPTIONAL_FIELDS:
                continue
            if f.default is not MISSING:
                optional_defaults[f.name] = f.default
            elif f.default_factory is not MISSING:  # 10.2.0: dict-Felder
                optional_defaults[f.name] = f.default_factory()
        def _economic(payload):
            data = {k: v for k, v in payload.items() if k not in METADATA}
            for name, default in optional_defaults.items():
                data.setdefault(name, default)
            return data
        if (not raw.get("day_start_equity") or not raw.get("last_equity")
                or _economic(raw) != _economic(checkpoint)):
            raise ValueError("RISK_CHECKPOINT_ECONOMIC_STATE_MISMATCH")
        return {"status": "ELIGIBLE", "reason_codes": [], "changes": {
            "equity_basis_key": expected_basis,
            "risk_scope_key": json.dumps(list(target_scope), separators=(",", ":")),
            "risk_scope_origin": "VERIFIED_IDENTICAL_CHECKPOINT",
            "equity_basis_review_required": False, "equity_basis_review_reason": ""}}
    except (ValueError, TypeError, KeyError, IndexError) as exc:
        return {"status": "REVIEW_REQUIRED", "reason_codes": [str(exc)], "changes": {}}


def apply_checkpoint(target, checkpoint_path, *, expected_sha256, expected_basis,
                     expected_scope, today, fresh_evidence=None,
                     archive_path=None, archive_member=None):
    """Maintenance-only metadata repair under the same lock as RiskState writes.

    No call in the trading daemon. Missing/stale/different evidence is an error.
    The original byte stream is durably backed up before one atomic replacement.
    """
    from safe_persistence import atomic_write_json, atomic_write_text
    from state_lock import critical_state_lock
    from risk_manager import _handelstag_heute
    from etoro_risk_maintenance import BASIS
    target, checkpoint_path = Path(target), Path(checkpoint_path)
    if (fresh_evidence is None or archive_path is None or not archive_member
            or target.name != "risk_state_etoro.json" or expected_basis != BASIS
            or not scope(expected_scope) or scope(expected_scope)[0] != "etoro"
            or str(today) != str(_handelstag_heute())):
        raise ValueError("RISK_ARCHIVE_AND_FRESH_CURRENT_DAY_EVIDENCE_REQUIRED")
    if (target.resolve() == checkpoint_path.resolve() or target.is_symlink() or checkpoint_path.is_symlink()
            or target.samefile(checkpoint_path)):
        raise ValueError("RISK_INDEPENDENT_CHECKPOINT_REQUIRED")
    proof = checkpoint_path.read_bytes()
    checkpoint = json.loads(proof)
    with critical_state_lock(target):
        retained, provenance = archived_checkpoint(archive_path, archive_member)
        if retained != checkpoint:
            raise ValueError("RISK_CHECKPOINT_DIFFERS_FROM_ORIGINAL_ARCHIVE")
        if fresh_evidence is not None:
            from etoro_risk_maintenance import review_evidence
            fresh = review_evidence(fresh_evidence, expected_scope=expected_scope, today=today)
            if fresh["status"] != "CURRENT_ACCOUNT_CONFIRMED":
                raise ValueError("; ".join(fresh["reason_codes"]))
        original = target.read_bytes()
        raw = json.loads(original)
        from risk_manager import RiskState
        RiskState._decode_payload(raw)
        receipt = raw.get("basis_review_receipt") or {}
        if (receipt.get("before_sha256") == expected_sha256
                and receipt.get("checkpoint_sha256") == _hash(proof)
                and raw.get("equity_basis_key") == expected_basis
                and scope(raw.get("risk_scope_key")) == scope(expected_scope)
                and not raw.get("equity_basis_review_required")):
            # A prior directory-fsync failure can leave the replacement visible.
            # Reconfirm durability before an idempotent success is reported.
            atomic_write_json(target, raw)
            return {"status": "ALREADY_APPLIED", "backup": receipt.get("backup")}
        if _hash(original) != expected_sha256:
            raise ValueError("RISK_STATE_CHANGED_SINCE_REVIEW")
        review = plan(raw, checkpoint, expected_basis=expected_basis,
                      expected_scope=expected_scope, today=today)
        if review["status"] != "ELIGIBLE":
            raise ValueError("; ".join(review["reason_codes"]))
        updated = deepcopy(raw)
        updated.update(review["changes"])
        backup = target.with_name(target.name + ".basis-review-" + expected_sha256[:20] + ".bak")
        if backup.is_symlink():
            raise ValueError("RISK_BACKUP_SYMLINK")
        if backup.exists() and backup.read_bytes() != original:
            raise ValueError("RISK_BACKUP_CONFLICT")
        atomic_write_text(backup, original.decode("utf-8"))
        if backup.read_bytes() != original:
            raise ValueError("RISK_BACKUP_VALIDATION_FAILED")
        updated["basis_review_receipt"] = {"before_sha256": expected_sha256,
            "checkpoint_sha256": _hash(proof), "backup": backup.name,
            "day": str(today), "method": "IDENTICAL_ECONOMIC_CHECKPOINT_V1"}
        if provenance:
            updated["basis_review_receipt"]["source_provenance"] = deepcopy(provenance)
        if fresh_evidence is not None:
            updated["basis_review_receipt"]["current_account_evidence_sha256"] = _hash(
                json.dumps(fresh_evidence, sort_keys=True, allow_nan=False).encode("utf-8"))
        from risk_manager import RiskState
        RiskState._decode_payload(updated)
        atomic_write_json(target, updated)
        if json.loads(target.read_text()) != updated:
            raise RuntimeError("RISK_REPAIR_VALIDATION_FAILED")
        return {"status": "APPLIED", "backup": str(backup)}


def archived_checkpoint(archive_path, member):
    """Read an original, complete eToro state from a separately retained ZIP.

    This verifies byte provenance, not a broker signature. A newly relabelled
    JSON/ZIP is not independent evidence and must never be supplied as such.
    """
    import io
    import zipfile
    from pathlib import PurePosixPath
    path = Path(archive_path)
    if path.is_symlink() or PurePosixPath(member).name != "risk_state_etoro.json":
        raise ValueError("RISK_INDEPENDENT_ETORO_ARCHIVE_REQUIRED")
    archive_bytes = path.read_bytes()
    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
        matches = [info for info in archive.infolist() if info.filename == member]
        if len(matches) != 1 or matches[0].file_size > 8_000_000:
            raise ValueError("RISK_ARCHIVE_MEMBER_AMBIGUOUS_OR_TOO_LARGE")
        source_bytes = archive.read(matches[0])
    checkpoint = json.loads(source_bytes)
    # Diagnostic exports wrap raw files with explicit completeness metadata.
    if "data" in checkpoint:
        if (checkpoint.get("status") != "OK" or checkpoint.get("truncated") is not False
                or checkpoint.get("changed_during_read") is not False):
            raise ValueError("RISK_ARCHIVE_CHECKPOINT_INCOMPLETE")
        checkpoint = checkpoint["data"]
    if (not isinstance(checkpoint, dict) or scope(checkpoint.get("risk_scope_key")) is None
            or checkpoint.get("risk_scope_origin") not in {
                "OBSERVED_V10", "BROKER_OBSERVED", "VERIFIED_IDENTICAL_CHECKPOINT"}):
        raise ValueError("RISK_ARCHIVE_HISTORICAL_SCOPE_UNPROVEN")
    return checkpoint, {"kind": "RETAINED_ARCHIVE_MEMBER", "archive_name": path.name,
        "archive_sha256": _hash(archive_bytes), "member": member,
        "member_sha256": _hash(source_bytes), "broker_signature_verified": False}


def main(argv=None):
    """Defaults to planning. Explicit archive-backed apply holds the writer lock."""
    import argparse
    p = argparse.ArgumentParser(description="Risikobasis lesen und belegen; keine Handelsfreigabe")
    p.add_argument("state", type=Path, nargs="?", default=Path(__file__).parent / "risk_state_etoro.json")
    p.add_argument("--basis")
    p.add_argument("--scope", help='JSON: ["etoro","DEMO","Kontofingerabdruck"]')
    p.add_argument("--runtime", type=Path, help="NEXUS-10.1-Runtime mit beobachteter Risikobasis")
    p.add_argument("--checkpoint", type=Path)
    p.add_argument("--checkpoint-archive", type=Path, help="Unveraendertes, separat aufbewahrtes Diagnose-ZIP")
    p.add_argument("--checkpoint-member", help="Exakter ZIP-Pfad der historischen risk_state_etoro.json")
    p.add_argument("--fresh-evidence", type=Path, help="Frischer Export von etoro_risk_maintenance.py")
    p.add_argument("--apply", action="store_true", help="Nur belegte Metadaten atomar uebernehmen; kein Reset")
    p.add_argument("--expected-sha256", help="Gepruefte aktuelle Zustandspruefsumme, gegen gleichzeitige Aenderungen")
    p.add_argument("--day", help="Handelstag; Standard ist die konfigurierte lokale Zeitzone")
    args = p.parse_args(argv)
    if args.day is None:
        from risk_manager import _handelstag_heute
        args.day = str(_handelstag_heute())
    evidence = None
    if args.fresh_evidence:
        evidence_export = json.loads(args.fresh_evidence.read_text(encoding="utf-8"))
        if evidence_export.get("state_changed_during_collection") is True:
            p.error("RISK_STATE_CHANGED_DURING_COLLECTION: Kontoabgleich erneut ausfuehren")
        evidence = evidence_export.get("evidence", evidence_export)
        args.basis = args.basis or evidence.get("observed_basis")
        if not args.scope and evidence.get("observed_scope"):
            args.scope = json.dumps(evidence["observed_scope"])
    if args.runtime:
        runtime = json.loads(args.runtime.read_text())
        observed = runtime.get("risk_review") or {}
        args.basis = args.basis or observed.get("observed_basis")
        if not args.scope and observed.get("observed_scope"):
            args.scope = json.dumps(observed["observed_scope"])
    if not args.basis or not scope(args.scope):
        p.error("Beobachtete Basis/Scope fehlen: --runtime runtime_status.json oder --basis und --scope angeben")
    original = args.state.read_bytes()
    raw = json.loads(original)
    out = {"source_sha256": _hash(original), "review": status(raw,
        observed_basis=args.basis, observed_scope=args.scope)}
    checkpoint, provenance = None, None
    if args.checkpoint_archive:
        if not args.checkpoint_member or args.checkpoint:
            p.error("Archiv erfordert --checkpoint-member; nicht mit --checkpoint kombinieren")
        checkpoint, provenance = archived_checkpoint(args.checkpoint_archive, args.checkpoint_member)
        out["checkpoint_provenance"] = provenance
        out["checkpoint_plan"] = plan(raw, checkpoint, expected_basis=args.basis,
            expected_scope=args.scope, today=args.day)
    if args.checkpoint:
        out["checkpoint_plan"] = plan(raw, json.loads(args.checkpoint.read_text()),
            expected_basis=args.basis, expected_scope=args.scope, today=args.day)
        out["checkpoint_plan"]["apply_available"] = False
        out["checkpoint_plan"]["provenance_note"] = "Lose JSON-Datei prueft nur Wertgleichheit; unabhaengige Herkunft ist nicht belegt."
    if evidence:
        from etoro_risk_maintenance import review_evidence, plan_new_period
        out["current_account_review"] = review_evidence(evidence, expected_scope=args.scope, today=args.day)
        out["new_period_plan"] = plan_new_period(raw, evidence)
    if args.apply:
        from risk_manager import _handelstag_heute
        from etoro_risk_maintenance import BASIS
        if (checkpoint is None or provenance is None or evidence is None or not args.expected_sha256
                or args.state.name != "risk_state_etoro.json" or scope(args.scope)[0] != "etoro"
                or args.basis != BASIS or args.day != str(_handelstag_heute())):
            p.error("Apply braucht Original-eToro-Archiv, frischen Kontoabgleich, aktuelle Pruefsumme und heutigen Handelstag; lose oder umbenannte Zustandsdateien sind kein Reparaturbeleg")
        import tempfile
        with tempfile.TemporaryDirectory(prefix="nexus-risk-proof-") as directory:
            proof = Path(directory) / "checkpoint.json"
            proof.write_text(json.dumps(checkpoint, ensure_ascii=False), encoding="utf-8")
            out["application"] = apply_checkpoint(args.state, proof,
                expected_sha256=args.expected_sha256, expected_basis=args.basis,
                expected_scope=args.scope, today=args.day, fresh_evidence=evidence,
                archive_path=args.checkpoint_archive, archive_member=args.checkpoint_member)
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
