"""Importgraph des Quellbaums, rein aus dem AST (10.8.0, Schritt 0).

WARUM ES DIESES MODUL GIBT
--------------------------
Der Architektur-Audit vom 19.09.2026 (Graphify) fand sechs Import-Zyklen, einen
Ring aus 27 Modulen, Adapter, die den Kern importieren, und eine WebUI mit 47
Kernimporten. Damit das ab jetzt nur noch besser wird, misst der Volltest
dieselben Kennzahlen selbst -- ohne Graphify, ohne Fremdpaket, deterministisch
aus ``ast``. Spaete Importe innerhalb von Funktionen zaehlen mit: Sie sind
echte Kopplung, auch wenn Python sie beim Start nicht aufloest.

Gezaehlt werden nur Module des Quellbaums (Wurzelmodule und die Pakete
``broker``, ``pulsar``, ``universe``, ``webui``, ``market_intelligence``,
``nexus``). Tests, ``offline_test_bootstrap`` und Werkzeuge im Testordner sind
kein Teil des Graphen.
"""
from __future__ import annotations

import ast
from pathlib import Path

PAKETE = ("broker", "pulsar", "universe", "webui", "market_intelligence", "nexus")
AUSGESCHLOSSEN = {"tests", "offline_test_bootstrap", "docs", "validation", "__pycache__", ".venv", "venv",
                  "graphify-out", "build", "dist", "node_modules"}


def modulname(rel: Path) -> str:
    teile = list(rel.with_suffix("").parts)
    if teile and teile[-1] == "__init__":
        teile = teile[:-1]
    return ".".join(teile)


def quellmodule(root: Path) -> dict[str, Path]:
    """Alle Module des Quellbaums: Name -> Datei."""
    module = {}
    for p in sorted(root.rglob("*.py")):
        rel = p.relative_to(root)
        if any(part in AUSGESCHLOSSEN for part in rel.parts):
            continue
        if len(rel.parts) > 1 and rel.parts[0] not in PAKETE:
            continue
        name = modulname(rel)
        if name:
            module[name] = p
    return module


def _aufloesen(name: str, bekannte: set[str]) -> str | None:
    """Importziel auf ein Quellmodul abbilden (auch ``from paket import modul``)."""
    while name:
        if name in bekannte:
            return name
        if "." not in name:
            return None
        name = name.rsplit(".", 1)[0]
    return None


def importe(datei: Path, eigener_name: str, bekannte: set[str]) -> set[str]:
    """Alle Importziele einer Datei, die im Quellbaum liegen (ohne Selbstimport)."""
    try:
        baum = ast.parse(datei.read_text(encoding="utf-8"), filename=str(datei))
    except (SyntaxError, UnicodeDecodeError):
        return set()
    ziele: set[str] = set()
    paket = eigener_name.rsplit(".", 1)[0] if "." in eigener_name else ""
    for knoten in ast.walk(baum):
        if isinstance(knoten, ast.Import):
            for alias in knoten.names:
                ziel = _aufloesen(alias.name, bekannte)
                if ziel:
                    ziele.add(ziel)
        elif isinstance(knoten, ast.ImportFrom):
            basis = knoten.module or ""
            if knoten.level:
                basis = ".".join(filter(None, [paket, basis])) if knoten.level == 1 else basis
            ziel = _aufloesen(basis, bekannte) if basis else None
            if ziel:
                ziele.add(ziel)
            # ``from paket import modul`` -- das Modul selbst ist das Ziel
            for alias in knoten.names:
                kandidat = f"{basis}.{alias.name}" if basis else alias.name
                ziel2 = _aufloesen(kandidat, bekannte)
                if ziel2:
                    ziele.add(ziel2)
    ziele.discard(eigener_name)
    return ziele


def graph(root: Path) -> dict[str, set[str]]:
    module = quellmodule(root)
    bekannte = set(module)
    return {name: importe(pfad, name, bekannte) for name, pfad in module.items()}


def starke_komponenten(g: dict[str, set[str]]) -> list[list[str]]:
    """Tarjan: stark zusammenhaengende Komponenten mit mehr als einem Modul."""
    index = {}
    low = {}
    stapel: list[str] = []
    auf_stapel = set()
    ergebnis: list[list[str]] = []
    zaehler = [0]

    def besuch(v: str) -> None:
        index[v] = low[v] = zaehler[0]
        zaehler[0] += 1
        stapel.append(v)
        auf_stapel.add(v)
        for w in g.get(v, ()):
            if w not in index:
                besuch(w)
                low[v] = min(low[v], low[w])
            elif w in auf_stapel:
                low[v] = min(low[v], index[w])
        if low[v] == index[v]:
            komp = []
            while True:
                w = stapel.pop()
                auf_stapel.discard(w)
                komp.append(w)
                if w == v:
                    break
            if len(komp) > 1:
                ergebnis.append(sorted(komp))

    import sys
    alt = sys.getrecursionlimit()
    sys.setrecursionlimit(max(alt, 10000))
    try:
        for v in sorted(g):
            if v not in index:
                besuch(v)
    finally:
        sys.setrecursionlimit(alt)
    return sorted(ergebnis, key=lambda k: (-len(k), k))


def zyklen(g: dict[str, set[str]]) -> list[list[str]]:
    """Alle Import-Zyklen als Komponenten (jede Komponente enthaelt mindestens einen Kreis)."""
    return starke_komponenten(g)


def kuerzeste_kreise(g: dict[str, set[str]], komponente: list[str], max_laenge: int = 4) -> list[list[str]]:
    """Kurze Kreise innerhalb einer Komponente (fuer lesbare Meldungen)."""
    menge = set(komponente)
    kreise = []
    gesehen = set()
    for start in komponente:
        pfad = [start]

        def lauf(v: str) -> None:
            if len(pfad) > max_laenge:
                return
            for w in sorted(g.get(v, ())):
                if w not in menge:
                    continue
                if w == start:
                    schluessel = frozenset(pfad)
                    if schluessel not in gesehen:
                        gesehen.add(schluessel)
                        kreise.append(list(pfad))
                elif w not in pfad and len(pfad) < max_laenge:
                    pfad.append(w)
                    lauf(w)
                    pfad.pop()

        lauf(start)
    return sorted(kreise, key=lambda k: (len(k), k))


def zeilen(datei: Path) -> int:
    try:
        return sum(1 for _ in datei.open(encoding="utf-8", errors="replace"))
    except OSError:
        return 0


def kennzahlen(root: Path) -> dict:
    module = quellmodule(root)
    g = graph(root)
    komponenten = zyklen(g)
    fan_out = {m: len(z) for m, z in g.items()}
    fan_in: dict[str, int] = {m: 0 for m in g}
    for m, z in g.items():
        for ziel in z:
            fan_in[ziel] = fan_in.get(ziel, 0) + 1
    groesse = {m: zeilen(p) for m, p in module.items()}
    return {
        "module": len(module),
        "kanten": sum(fan_out.values()),
        "zyklen": len(komponenten),
        "zyklen_module": sum(len(k) for k in komponenten),
        "groesste_komponente": len(komponenten[0]) if komponenten else 0,
        "komponenten": komponenten,
        "fan_out": fan_out,
        "fan_in": fan_in,
        "zeilen": groesse,
    }
