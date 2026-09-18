"""Telegram-Dateiexporte fuer Decision-History und Tagesberichte."""
from __future__ import annotations
import csv
import os
import json
import sqlite3
from datetime import datetime
from pathlib import Path
import config
from decision_analytics import db_pfad, init_db, latest

ROOT = Path(__file__).resolve().parent
EXPORT_DIR = Path(os.getenv("TRADINGBOT_TEST_STATE_DIR", "").strip() or ROOT) / "exports"

def _ensure_dir():
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    return EXPORT_DIR

def export_decisions_csv(day: str | None = None) -> Path:
    # v9.1: derselbe lokale Handelstag wie beim Schreiben (LOCAL_TIMEZONE).
    from decision_analytics import lokaler_handelstag
    day = day or lokaler_handelstag()
    rows = latest(10000, day=day)
    path = _ensure_dir() / f"decisions_{day}.csv"
    fields = [
        "id","created_at_utc","local_day","symbol","asset_type","broker","paper",
        "status","blocked_by","reason","category","price","ai_decision","ai_confidence",
        "ai_web_searches","ai_source_count","execution_status","order_ids","payload","outcomes",
    ]
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for row in rows:
            x = dict(row)
            for key in ("order_ids","payload","outcomes"):
                x[key] = json.dumps(x.get(key), ensure_ascii=False, default=str)
            w.writerow(x)
    return path


def export_daily_decisions_txt(day: str | None = None) -> Path:
    """Menschenlesbare Tagesanlage mit jeder Kaufentscheidung/Ablehnung."""
    # v9.1: derselbe lokale Handelstag wie beim Schreiben (LOCAL_TIMEZONE).
    from decision_analytics import lokaler_handelstag
    day = day or lokaler_handelstag()
    rows = latest(10000, day=day)
    lines = [
        f"TRADINGBOT {getattr(config, 'VERSION_NEXUS', 'NEXUS')} – KAUFENTSCHEIDUNGEN {day}",
        "=" * 68,
        ("Erfasst werden ernsthafte Kandidaten ab technischem Signal. "
         "Reine Scans ohne Signal stehen bewusst nicht in dieser Datei."),
        "",
    ]
    if not rows:
        lines.append("Keine ernsthaften Kaufkandidaten an diesem Tag.")
    for index, row in enumerate(rows, 1):
        payload = row.get("payload") or {}
        sources = payload.get("sources") or payload.get("quellen") or []
        lines.extend([
            f"{index}. {row.get('created_at_utc','')} · {row.get('symbol','?')}",
            f"   Broker/Anlage: {row.get('broker','?')} / {row.get('asset_type','?')}",
            f"   Modus: {'DEMO/PAPER' if row.get('paper', 1) else 'LIVE'}",
            f"   Ergebnis: {row.get('status','?')}",
            f"   Blockiert durch: {row.get('blocked_by') or '-'}",
            f"   Begründung: {row.get('reason') or '-'}",
        ])
        if sources:
            lines.append("   Quellen/Prüfschritte:")
            for source in sources[:20]:
                if not isinstance(source, dict):
                    continue
                lines.append(
                    f"     - {source.get('quelle') or source.get('source') or '?'} · "
                    f"{source.get('richtung') or source.get('direction') or '-'} · "
                    f"{source.get('detail') or '-'}"
                )
        lines.append("")
    path = _ensure_dir() / f"kaufentscheidungen_{day}.txt"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path

def snapshot_database() -> Path:
    init_db()
    stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    dest = _ensure_dir() / f"decision_history_snapshot_{stamp}.sqlite"
    src = sqlite3.connect(db_pfad(), timeout=10)
    dst = sqlite3.connect(dest)
    try:
        with dst:
            src.backup(dst)
    finally:
        dst.close(); src.close()
    return dest
