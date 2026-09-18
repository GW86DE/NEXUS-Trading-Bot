"""Entscheidungs-Quellenprotokoll (v8.1.1 NEXUS).

WAS v6 KONNTE
=============
v6 hat protokolliert, DASS eine Entscheidung gefallen ist und welcher Filter
sie gegebenenfalls abgelehnt hat.

WAS v7 ERGAENZT
===============
Jede Entscheidung fuehrt jetzt zusaetzlich eine Liste ihrer QUELLEN mit:

    wer hat beigetragen        Technik, News, Luna, Terra, Risk Gate, ...
    in welche Richtung         DAFUER, DAGEGEN, NEUTRAL, BLOCKIEREND
    mit welchem Gewicht        0..1, wobei nur deterministische Quellen
                               ueberhaupt blockieren duerfen
    auf welcher Datenbasis     Quelle, Zeitstempel, Cache ja/nein

Warum das wichtig ist: ohne Quellenangabe laesst sich hinterher nicht
beantworten, ob die KI dem Ergebnis genutzt oder geschadet hat. Und genau
diese Frage entscheidet, ob KI-Kosten sinnvoll sind.

HARTE REGEL
===========
Eine KI-Quelle kann NIEMALS 'BLOCKIEREND' sein und niemals eine Ablehnung
aufheben. Sie ist Kontext, kein Torwaechter. Der Konstruktor erzwingt das.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional
from decision_snapshot import new_decision_id

logger = logging.getLogger(__name__)

# Quellenarten
TECHNIK = "TECHNIK"                 # Indikatoren, Strategie, ML
NEWS = "NEWS"                       # Nachrichtenquellen
LUNA = "LUNA"                       # guenstiges KI-Modell
TERRA = "TERRA"                     # starkes KI-Modell
RISK_GATE = "RISK_GATE"             # Risikomanager / Risikotopf
CANDIDATE_GATE = "CANDIDATE_GATE"   # deterministische Kaufkaskade
UNIVERSE = "UNIVERSE"               # dynamisches Universum
BROKER = "BROKER"                   # Brokerzustand, Spread, Kosten
MARKT = "MARKT"                     # Marktphase, Kalender, Regime
NUTZER = "NUTZER"                   # manuelle Freigabe (Telegram/Web)

KI_QUELLEN = {LUNA, TERRA}

# Richtungen
DAFUER = "DAFUER"
DAGEGEN = "DAGEGEN"
NEUTRAL = "NEUTRAL"
BLOCKIEREND = "BLOCKIEREND"


@dataclass
class Beitrag:
    """Ein einzelner Beitrag zu einer Entscheidung."""
    quelle: str
    richtung: str = NEUTRAL
    gewicht: float = 0.0
    detail: str = ""
    datenquelle: str = ""
    zeit: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    cache: bool = False
    modell: str = ""
    kosten_usd: float = 0.0

    def __post_init__(self):
        self.quelle = str(self.quelle).upper()
        self.richtung = str(self.richtung).upper()
        # Sicherheitsregel: KI darf niemals blockieren.
        if self.quelle in KI_QUELLEN and self.richtung == BLOCKIEREND:
            logger.warning("KI-Quelle %s wollte blockieren -- auf DAGEGEN herabgestuft.",
                           self.quelle)
            self.richtung = DAGEGEN
        self.gewicht = max(0.0, min(1.0, float(self.gewicht or 0.0)))

    def als_dict(self) -> dict:
        return asdict(self)

    def kurz(self) -> str:
        teile = [self.quelle]
        if self.modell:
            teile.append(f"({self.modell})")
        teile.append(self.richtung)
        if self.gewicht:
            teile.append(f"{self.gewicht:.2f}")
        if self.detail:
            teile.append(f"-- {self.detail}")
        return " ".join(teile)


class Entscheidungsprotokoll:
    """Sammelt alle Beitraege zu genau einer Kaufentscheidung."""

    def __init__(self, symbol: str, *, asset_type: str = "crypto", broker: str = "",
                 aktion: str = "KAUF"):
        self.decision_id = new_decision_id()
        self.symbol = str(symbol).upper()
        self.asset_type = str(asset_type)
        self.broker = str(broker)
        self.aktion = str(aktion)
        self.beitraege: list[Beitrag] = []
        self.start = datetime.now(timezone.utc)
        self.ergebnis = ""
        self.ergebnis_grund = ""
        self.daten: dict[str, Any] = {}

    # -- Erfassen -----------------------------------------------------------
    def ergaenze(self, quelle: str, richtung: str, *, gewicht: float = 0.0,
                 detail: str = "", datenquelle: str = "", cache: bool = False,
                 modell: str = "", kosten_usd: float = 0.0) -> "Entscheidungsprotokoll":
        self.beitraege.append(Beitrag(
            quelle=quelle, richtung=richtung, gewicht=gewicht, detail=detail,
            datenquelle=datenquelle, cache=cache, modell=modell, kosten_usd=kosten_usd))
        return self

    def technik(self, richtung: str, gewicht: float, detail: str) -> "Entscheidungsprotokoll":
        return self.ergaenze(TECHNIK, richtung, gewicht=gewicht, detail=detail,
                             datenquelle="Indikatoren/Strategie")

    def news(self, richtung: str, gewicht: float, detail: str,
             quelle_name: str = "") -> "Entscheidungsprotokoll":
        return self.ergaenze(NEWS, richtung, gewicht=gewicht, detail=detail,
                             datenquelle=quelle_name)

    def ki(self, stufe: str, richtung: str, detail: str, *, modell: str = "",
           cache: bool = False, kosten_usd: float = 0.0,
           gewicht: float = 0.2) -> "Entscheidungsprotokoll":
        quelle = TERRA if str(stufe).lower().startswith("terra") else LUNA
        return self.ergaenze(quelle, richtung, gewicht=gewicht, detail=detail,
                             datenquelle="OpenAI Responses", cache=cache,
                             modell=modell, kosten_usd=kosten_usd)

    def blockiert(self, quelle: str, detail: str) -> "Entscheidungsprotokoll":
        """Eine harte Ablehnung. Nur deterministische Quellen duerfen das."""
        return self.ergaenze(quelle, BLOCKIEREND, gewicht=1.0, detail=detail)

    def messwerte(self, **werte: Any) -> "Entscheidungsprotokoll":
        """Bereits gemessene Auditwerte ergaenzen; ``None`` bleibt unbekannt."""
        self.daten.update(werte)
        return self

    # -- Auswerten ----------------------------------------------------------
    @property
    def blockierer(self) -> list[Beitrag]:
        return [b for b in self.beitraege if b.richtung == BLOCKIEREND]

    @property
    def ki_kosten(self) -> float:
        return round(sum(b.kosten_usd for b in self.beitraege if b.quelle in KI_QUELLEN), 6)

    def stimmungsbild(self) -> dict:
        """Gewichtete Summe -- rein informativ, nie entscheidend."""
        dafuer = sum(b.gewicht for b in self.beitraege if b.richtung == DAFUER)
        dagegen = sum(b.gewicht for b in self.beitraege if b.richtung == DAGEGEN)
        gesamt = dafuer + dagegen
        return {
            "dafuer": round(dafuer, 3),
            "dagegen": round(dagegen, 3),
            "tendenz": round((dafuer - dagegen) / gesamt, 3) if gesamt > 0 else 0.0,
            "blockiert": bool(self.blockierer),
        }

    def hauptquelle(self) -> str:
        """Welche Quelle hat das Ergebnis am staerksten getragen?"""
        if self.blockierer:
            return self.blockierer[0].quelle
        relevante = [b for b in self.beitraege if b.richtung in (DAFUER, DAGEGEN)]
        if not relevante:
            return TECHNIK
        return max(relevante, key=lambda b: b.gewicht).quelle

    def abschliessen(self, ergebnis: str, grund: str = "") -> dict:
        """Schliesst das Protokoll ab und liefert die speicherbare Fassung."""
        self.ergebnis = str(ergebnis).upper()
        self.ergebnis_grund = str(grund)
        return self.als_dict()

    def als_dict(self) -> dict:
        return {
            "decision_id": self.decision_id,
            "symbol": self.symbol,
            "asset_type": self.asset_type,
            "broker": self.broker,
            "aktion": self.aktion,
            "zeit": self.start.isoformat(),
            "dauer_sekunden": round((datetime.now(timezone.utc) - self.start).total_seconds(), 3),
            "ergebnis": self.ergebnis,
            "ergebnis_grund": self.ergebnis_grund,
            "hauptquelle": self.hauptquelle(),
            "stimmungsbild": self.stimmungsbild(),
            "ki_kosten_usd": self.ki_kosten,
            "ki_beteiligt": bool([b for b in self.beitraege if b.quelle in KI_QUELLEN]),
            "quellen": [b.als_dict() for b in self.beitraege],
            "messwerte": dict(self.daten),
        }

    def klartext(self) -> str:
        """Menschenlesbare Fassung fuer Telegram und Logbuch."""
        kopf = f"{self.aktion} {self.symbol} ({self.broker or self.asset_type}): {self.ergebnis or 'offen'}"
        if self.ergebnis_grund:
            kopf += f" -- {self.ergebnis_grund}"
        zeilen = [kopf]
        for b in self.beitraege:
            zeilen.append("  " + b.kurz())
        return "\n".join(zeilen)


# ---------------------------------------------------------------------------
# Speicherung
# ---------------------------------------------------------------------------
def _wurzel() -> Path:
    return Path(os.getenv("TRADINGBOT_TEST_STATE_DIR", "").strip() or
                Path(__file__).resolve().parent)


def pfad() -> Path:
    import config
    return _wurzel() / str(getattr(config, "DECISION_SOURCE_FILE", "decision_sources.jsonl"))


def speichere(protokoll: Entscheidungsprotokoll | dict, *,
              decision_id: Optional[int] = None) -> None:
    """Schreibt das Quellenprotokoll -- ohne den Handel je zu gefaehrden."""
    daten = protokoll.als_dict() if isinstance(protokoll, Entscheidungsprotokoll) else dict(protokoll)
    if decision_id is not None:
        daten["decision_id"] = int(decision_id)
    datei = pfad()
    try:
        if datei.exists() and datei.stat().st_size > 5 * 1024 * 1024:
            datei.replace(datei.with_suffix(".1.jsonl"))
        with datei.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(daten, ensure_ascii=False, default=str) + "\n")
    except Exception:
        logger.debug("Quellenprotokoll konnte nicht geschrieben werden", exc_info=True)


def lies(limit: int = 100, *, symbol: str = "", ergebnis: str = "",
         nur_ki: bool = False, quelle: str = "") -> list[dict]:
    """Liest die letzten Quellenprotokolle -- Basis der Logbuch-Filter."""
    datei = pfad()
    if not datei.exists():
        return []
    try:
        zeilen = datei.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
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
        if symbol and str(eintrag.get("symbol", "")).upper() != symbol.upper():
            continue
        if ergebnis and str(eintrag.get("ergebnis", "")).upper() != ergebnis.upper():
            continue
        if nur_ki and not eintrag.get("ki_beteiligt"):
            continue
        if quelle:
            quellen = {str(q.get("quelle", "")).upper() for q in eintrag.get("quellen", [])}
            if quelle.upper() not in quellen:
                continue
        out.append(eintrag)
        if len(out) >= max(1, int(limit)):
            break
    return out


def statistik(stunden: int = 24) -> dict:
    """Wie oft hat welche Quelle den Ausschlag gegeben?"""
    from datetime import timedelta
    grenze = datetime.now(timezone.utc) - timedelta(hours=max(1, int(stunden)))
    haupt: dict[str, int] = {}
    ergebnisse: dict[str, int] = {}
    ki_kosten = 0.0
    ki_anteil = 0
    gesamt = 0
    for eintrag in lies(limit=5000):
        try:
            ts = datetime.fromisoformat(str(eintrag.get("zeit", "")))
        except ValueError:
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if ts < grenze:
            break
        gesamt += 1
        q = str(eintrag.get("hauptquelle", "?"))
        haupt[q] = haupt.get(q, 0) + 1
        e = str(eintrag.get("ergebnis", "?"))
        ergebnisse[e] = ergebnisse.get(e, 0) + 1
        ki_kosten += float(eintrag.get("ki_kosten_usd", 0.0) or 0.0)
        if eintrag.get("ki_beteiligt"):
            ki_anteil += 1
    return {
        "zeitraum_stunden": stunden,
        "entscheidungen": gesamt,
        "hauptquellen": haupt,
        "ergebnisse": ergebnisse,
        "ki_beteiligt": ki_anteil,
        "ki_kosten_usd": round(ki_kosten, 4),
    }


__all__ = [
    "Entscheidungsprotokoll", "Beitrag", "speichere", "lies", "statistik", "pfad",
    "TECHNIK", "NEWS", "LUNA", "TERRA", "RISK_GATE", "CANDIDATE_GATE",
    "UNIVERSE", "BROKER", "MARKT", "NUTZER",
    "DAFUER", "DAGEGEN", "NEUTRAL", "BLOCKIEREND",
]
