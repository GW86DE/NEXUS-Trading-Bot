"""Schutz vor versehentlich in den Produktionszustand geschriebenen Testdaten.

In v5.2.0 konnte ``tests_integration.py`` einen simulierten Verlust direkt in
``risk_state.json`` schreiben. Beim offensiven Profil war der Testwert exakt
-6.001,00 (100.000 * 6 % + 1). Das sah im Dashboard wie ein echter Verlust aus.

v5.2.1 isoliert Tests generell. Dieses Modul erkennt zusätzlich die sehr enge,
bekannte Alt-Signatur und archiviert sie, damit ein Update die Test-P&L nicht
weitertraegt. Es werden nur exakt passende Testzustaende automatisch bereinigt.
Andere P&L-Werte bleiben unangetastet.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
import json
import math

KNOWN_TEST_LOSSES = (2001.0, 3001.0, 6001.0)


def _f(data, key):
    try:
        return float(data.get(key, 0) or 0)
    except Exception:
        return 0.0


def is_known_v520_test_contamination(data: dict) -> bool:
    if not isinstance(data, dict):
        return False
    loss = abs(_f(data, "realized_pnl_today"))
    if not any(math.isclose(loss, x, abs_tol=1e-6) for x in KNOWN_TEST_LOSSES):
        return False
    # tests_integration erzeugte exakt einen Verlusttrade und keine Gewinne.
    if int(data.get("trades_today", 0) or 0) != 1:
        return False
    if _f(data, "realized_pnl_today") >= 0:
        return False
    if _f(data, "net_profit_today") != 0 or _f(data, "gross_profit_today") != 0:
        return False
    if not math.isclose(_f(data, "net_loss_today"), loss, abs_tol=1e-6):
        return False
    if not math.isclose(_f(data, "gross_loss_today"), loss, abs_tol=1e-6):
        return False
    # Der Integrationstest setzt das Tageslimit und damit trading_halted.
    if not bool(data.get("trading_halted", False)):
        return False
    return True


def read_json(path) -> dict:
    try:
        p = Path(path)
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    except Exception:
        return {}


def quarantine_known_test_state(path) -> Path | None:
    """Archiviert einen eindeutig als v5.2.0-Testzustand erkannten State.

    Die Originaldatei wird *verschoben*, nicht geloescht. Anschliessend kann
    RiskState.load() einen frischen Zustand anlegen. Rueckgabe ist der
    Backup-Pfad oder None, wenn nichts veraendert wurde.
    """
    p = Path(path)
    data = read_json(p)
    if not is_known_v520_test_contamination(data):
        return None
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = p.with_name(f"{p.stem}_TESTDATA_BACKUP_{stamp}{p.suffix}")
    # Kollision bei sehr schnellem mehrfachen Start vermeiden.
    idx = 1
    while backup.exists():
        backup = p.with_name(f"{p.stem}_TESTDATA_BACKUP_{stamp}_{idx}{p.suffix}")
        idx += 1
    p.replace(backup)
    return backup
