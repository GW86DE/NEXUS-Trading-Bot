"""Aenderungsprotokoll des Universums (DU-017).

Jede Aufnahme, Freigabe und Entfernung wird zeilenweise als JSON
geschrieben. Das Format ist bewusst JSONL:

    - anhaengen ist atomar genug fuer den Dauerbetrieb,
    - eine kaputte Zeile macht nicht die ganze Datei unlesbar,
    - die Web-UI kann von hinten lesen, ohne alles zu laden.

Gespeichert wird auch, WELCHE Quelle die Aenderung ausgeloest hat --
technischer Score, Luna, Terra oder ein Sicherheitsereignis. Ohne diese
Angabe laesst sich spaeter nicht beurteilen, ob die KI dem Universum
genutzt oder geschadet hat.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)
_LOCK = threading.RLock()

MAX_BYTES = 5 * 1024 * 1024      # ab 5 MB wird rotiert
ROTATIONEN = 3


def _wurzel() -> Path:
    return Path(os.getenv("TRADINGBOT_TEST_STATE_DIR", "").strip() or
                Path(__file__).resolve().parent.parent)


def pfad() -> Path:
    return _wurzel() / "universe_audit.jsonl"


def _rotiere(datei: Path) -> None:
    try:
        if not datei.exists() or datei.stat().st_size < MAX_BYTES:
            return
        for i in range(ROTATIONEN - 1, 0, -1):
            alt = datei.with_suffix(f".{i}.jsonl")
            neu = datei.with_suffix(f".{i + 1}.jsonl")
            if alt.exists():
                alt.replace(neu)
        datei.replace(datei.with_suffix(".1.jsonl"))
    except Exception:
        logger.debug("Audit-Rotation fehlgeschlagen", exc_info=True)


def schreibe(aktion: str, *, symbol: str, broker: str, grund: str = "",
             alter_rang: int = 0, neuer_rang: int = 0,
             alter_score: float = 0.0, neuer_score: float = 0.0,
             quelle: str = "technisch", ai_modell: str = "",
             zusatz: Optional[dict] = None) -> None:
    """Schreibt genau einen Audit-Eintrag."""
    eintrag = {
        "zeit": datetime.now(timezone.utc).isoformat(),
        "aktion": str(aktion),
        "symbol": str(symbol).upper(),
        "broker": str(broker).lower(),
        "grund": str(grund)[:500],
        "alter_rang": int(alter_rang or 0),
        "neuer_rang": int(neuer_rang or 0),
        "alter_score": round(float(alter_score or 0.0), 4),
        "neuer_score": round(float(neuer_score or 0.0), 4),
        "quelle": str(quelle),
        "ai_modell": str(ai_modell or ""),
    }
    if zusatz:
        eintrag["zusatz"] = zusatz
    datei = pfad()
    with _LOCK:
        try:
            _rotiere(datei)
            datei.parent.mkdir(parents=True, exist_ok=True)
            with datei.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(eintrag, ensure_ascii=False) + "\n")
        except Exception:
            logger.warning("Universums-Audit konnte nicht geschrieben werden", exc_info=True)


def lies(limit: int = 200, *, broker: str = "", aktion: str = "",
         symbol: str = "") -> list[dict]:
    """Liest die letzten Eintraege, optional gefiltert."""
    datei = pfad()
    if not datei.exists():
        return []
    try:
        zeilen = datei.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        logger.warning("Universums-Audit nicht lesbar", exc_info=True)
        return []

    out: list[dict] = []
    for zeile in reversed(zeilen):
        zeile = zeile.strip()
        if not zeile:
            continue
        try:
            eintrag = json.loads(zeile)
        except ValueError:
            continue
        if broker and str(eintrag.get("broker", "")).lower() != broker.lower():
            continue
        if aktion and str(eintrag.get("aktion", "")).upper() != aktion.upper():
            continue
        if symbol and str(eintrag.get("symbol", "")).upper() != symbol.upper():
            continue
        out.append(eintrag)
        if len(out) >= max(1, int(limit)):
            break
    return out


def zusammenfassung(stunden: int = 24) -> dict:
    """Kompakte Statistik fuer Dashboard und Telegram-Tagesbericht."""
    from datetime import timedelta
    grenze = datetime.now(timezone.utc) - timedelta(hours=max(1, int(stunden)))
    zaehler: dict[str, int] = {}
    quellen: dict[str, int] = {}
    for eintrag in lies(limit=2000):
        try:
            ts = datetime.fromisoformat(str(eintrag.get("zeit", "")))
        except ValueError:
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if ts < grenze:
            break
        aktion = str(eintrag.get("aktion", "?"))
        zaehler[aktion] = zaehler.get(aktion, 0) + 1
        quelle = str(eintrag.get("quelle", "?"))
        quellen[quelle] = quellen.get(quelle, 0) + 1
    return {"zeitraum_stunden": stunden, "aktionen": zaehler, "quellen": quellen}


__all__ = ["schreibe", "lies", "zusammenfassung", "pfad"]
