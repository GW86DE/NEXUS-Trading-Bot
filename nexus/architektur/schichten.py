"""Schichtenkarte lesen und Importrichtungen pruefen (10.8.0, Schritt 1).

``schichten.json`` ordnet jedes Modul des Quellbaums genau einer Schicht zu und
nennt je Schicht die Schichten, die sie importieren darf. Dieses Modul rechnet
den Importgraphen (``importgraph.graph``) dagegen. Ein Alias-Modul (Weiche auf
ein umgezogenes Modul, siehe ``nexus/weiche.py``) zaehlt fuer Importeure wie
sein Ziel: Wer ``ledger_result`` importiert, importiert fachlich
``nexus.domain.ledger_result``.
"""
from __future__ import annotations

import json
from pathlib import Path

KARTE = Path(__file__).with_name("schichten.json")


def lade(pfad: Path | None = None) -> dict:
    return json.loads((pfad or KARTE).read_text(encoding="utf-8"))


def zuordnung(karte: dict) -> dict[str, str]:
    """Modulname -> Schicht (Alias-Module tragen die Schicht 'alias')."""
    out: dict[str, str] = {}
    for schicht, module in karte.get("schichten", {}).items():
        for m in module:
            out[m] = schicht
    return out


def fachschicht(modul: str, karte: dict, zu: dict[str, str]) -> str | None:
    """Schicht, die fuer Importeure zaehlt: bei einem Alias die des Ziels."""
    ziel = karte.get("alias", {}).get(modul)
    if ziel:
        return zu.get(ziel)
    return zu.get(modul)


def verstoesse(g: dict[str, set[str]], karte: dict) -> list[list[str]]:
    """Alle Importkanten, die die Richtungsregeln der Karte verletzen.

    Rueckgabe: [[quelle, ziel, "schicht->zielschicht"], ...], sortiert.
    """
    zu = zuordnung(karte)
    regeln = karte.get("regeln", {})
    out: list[list[str]] = []
    for quelle, ziele in g.items():
        s = zu.get(quelle)
        if s is None or s == "alias":
            continue
        erlaubt = regeln.get(s, [])
        if "*" in erlaubt:
            continue
        for ziel in ziele:
            zs = fachschicht(ziel, karte, zu)
            if zs is None or zs in erlaubt:
                continue
            out.append([quelle, ziel, f"{s}->{zs}"])
    return sorted(out)


def ohne_schicht(module: set[str] | dict, karte: dict) -> list[str]:
    zu = zuordnung(karte)
    return sorted(m for m in module if m not in zu)


def verwaist(module: set[str] | dict, karte: dict) -> list[str]:
    """Eintraege der Karte, zu denen kein Modul mehr existiert."""
    return sorted(m for m in zuordnung(karte) if m not in module)
