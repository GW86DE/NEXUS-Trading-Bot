"""Gebuehren-Snapshots offener eToro-Positionen (10.2.0, Vorarbeit).

Hintergrund (Recherche 16.09.2026): Die eToro-API liefert Gebuehrenfelder
zuverlaessig nur fuer OFFENE Positionen. Nach dem Schliessen fehlen sie, und
der Ergebnisabgleich wartet auf manuell bestaetigte Belege ("Ergebnisabgleich
ausstehend"). Dieses Modul sichert deshalb bei jedem ohnehin stattfindenden
P&L-Abruf die Gebuehrenfelder jeder offenen Position dauerhaft weg -- als
spaeteres BELEGMATERIAL, nicht als automatische Freigabe.

Grundsaetze:
- Rein lokal und API-schonend: es wird KEIN zusaetzlicher Netzabruf erzeugt;
  der Hook haengt am bestehenden P&L-Snapshot.
- UNKNOWN bleibt UNKNOWN: fehlen Gebuehrenfelder, wird genau das gespeichert.
  Es wird keine Gebuehr erfunden, geschaetzt oder stillschweigend freigegeben.
- Der Ergebnisabgleich selbst bleibt unveraendert; kuenftige Versionen koennen
  diese Snapshots als zusaetzliche Belegquelle heranziehen.
"""
from __future__ import annotations

from datetime import datetime, timezone
import logging
import os
from pathlib import Path
import threading

from safe_persistence import atomic_write_json

DATEI = "etoro_fee_snapshots.json"
SCHEMA = 1
MAX_POSITIONEN = 400
# Nur diese Felder sind Gebuehren-/Umrechnungsevidenz; alles andere waere
# Ballast und wuerde die Datei unnoetig sensibel machen.
GEBUEHREN_FELDER = ("fees", "totalFees", "accruedFees", "taxes", "spreadFees",
                     "overnightFees", "weekendFees", "openConversionRate",
                     "conversionRate")
IDENTITAETS_FELDER = ("positionId", "instrumentId", "openDateTime", "openRate",
                       "units", "amount", "isBuy", "leverage")
_LOCK = threading.RLock()
logger = logging.getLogger(__name__)


def pfad() -> Path:
    root = Path(os.getenv("TRADINGBOT_TEST_STATE_DIR", "").strip()
                or Path(__file__).resolve().parent)
    return root / DATEI


def _laden() -> dict:
    try:
        import json
        data = json.loads(pfad().read_text(encoding="utf-8"))
        if isinstance(data, dict) and data.get("schema") == SCHEMA:
            return data
    except FileNotFoundError:
        pass
    except Exception as exc:
        logger.warning("Gebuehren-Snapshotdatei unlesbar (%s); sie wird neu "
                       "aufgebaut, vorhandene Belege bleiben in Sicherungen.",
                       type(exc).__name__)
    return {"schema": SCHEMA, "konten": {}}


def _zeile(row: dict) -> dict:
    beleg = {}
    vorhandene = []
    for feld in GEBUEHREN_FELDER:
        if feld in row and row.get(feld) is not None:
            beleg[feld] = row.get(feld)
            vorhandene.append(feld)
    identitaet = {feld: row.get(feld) for feld in IDENTITAETS_FELDER if feld in row}
    return {
        "identitaet": identitaet,
        "gebuehren": beleg,
        "gebuehrenfelder_vorhanden": vorhandene,
        # Ehrlicher Status statt erfundener 0-Gebuehr.
        "status": "FELDER_ERFASST" if vorhandene else "KEINE_GEBUEHRENFELDER_GELIEFERT",
    }


def capture_pnl(broker, pnl: dict) -> int:
    """Sichert die Gebuehrenfelder aller offenen Positionen dieses Snapshots.

    Rueckgabe: Anzahl der aktualisierten Positionsbelege. Fehler werden
    geloggt und niemals in den Handelspfad geworfen.
    """
    try:
        account = str(broker.account_fingerprint() or "")
        if not account:
            return 0
        environment = "DEMO" if bool(getattr(broker, "paper", getattr(broker, "demo", True))) else "LIVE"
        rows = broker._all_positions(pnl) if hasattr(broker, "_all_positions") else []
        if not rows:
            return 0
        stamp = str(pnl.get("_snapshot_at") or datetime.now(timezone.utc).isoformat())
        snapshot_id = str(pnl.get("_snapshot_id") or "")
        with _LOCK:
            data = _laden()
            konto = data["konten"].setdefault(f"{environment}:{account}", {"positionen": {}})
            positionen = konto["positionen"]
            aktualisiert = 0
            for row in rows:
                pid = str(row.get("positionId") or "").strip()
                if not pid:
                    continue
                eintrag = _zeile(row)
                eintrag["zuletzt_gesehen_utc"] = stamp
                eintrag["snapshot_id"] = snapshot_id
                vorher = positionen.get(pid)
                if vorher:
                    eintrag["erstmals_gesehen_utc"] = vorher.get(
                        "erstmals_gesehen_utc", stamp)
                else:
                    eintrag["erstmals_gesehen_utc"] = stamp
                positionen[pid] = eintrag
                aktualisiert += 1
            # Aelteste Belege kappen, damit die Datei nicht unbegrenzt waechst.
            if len(positionen) > MAX_POSITIONEN:
                sortiert = sorted(positionen.items(),
                                  key=lambda kv: str(kv[1].get("zuletzt_gesehen_utc") or ""))
                for pid, _ in sortiert[:len(positionen) - MAX_POSITIONEN]:
                    positionen.pop(pid, None)
            data["aktualisiert_utc"] = stamp
            atomic_write_json(pfad(), data)
        return aktualisiert
    except Exception as exc:
        logger.debug("Gebuehren-Snapshot nicht speicherbar: %s", exc, exc_info=True)
        return 0


def letzter_beleg(account: str, environment: str, position_id: str) -> dict | None:
    """Letzter gesicherter Offen-Beleg einer Position (oder None)."""
    with _LOCK:
        data = _laden()
    konto = data.get("konten", {}).get(f"{str(environment).upper()}:{account}") or {}
    beleg = (konto.get("positionen") or {}).get(str(position_id))
    return dict(beleg) if isinstance(beleg, dict) else None


def status() -> dict:
    with _LOCK:
        data = _laden()
    konten = data.get("konten", {})
    return {
        "datei": str(pfad()),
        "aktualisiert_utc": data.get("aktualisiert_utc", ""),
        "konten": {k: len(v.get("positionen") or {}) for k, v in konten.items()},
    }


__all__ = ["capture_pnl", "letzter_beleg", "pfad", "status"]
