"""Sorgt dafuer, dass v7-Werkzeuge immer in der richtigen Python-Umgebung laufen.

DAS PROBLEM
===========
Der Pi-Installer legt eine eigene Umgebung unter '.venv' an und installiert
pandas, requests und alles Weitere ausschliesslich dort. Wer dann

    python3 nexus_start.py

eintippt, startet das SYSTEM-Python -- und bekommt

    ModuleNotFoundError: No module named 'pandas'

Das ist kein Fehler des Bots, sondern die falsche Umgebung. Trotzdem sieht
es fuer den Nutzer aus wie ein kaputtes Programm.

DIE LOESUNG
===========
Beim Start wird geprueft, ob die noetigen Pakete vorhanden sind. Fehlen sie
und es gibt eine '.venv' daneben, startet sich das Skript dort selbst neu.
Der Nutzer merkt davon nichts ausser einer kurzen Hinweiszeile.

Ist keine Umgebung vorhanden, kommt eine klare Anweisung statt eines
Tracebacks.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# Kandidaten in der Reihenfolge, in der der Installer sie anlegt.
UMGEBUNGEN = (".venv", "venv", "env")

# Diese Pakete braucht jedes v7-Werkzeug.
PFLICHTPAKETE = ("pandas", "requests")

# Verhindert eine Endlosschleife, falls die Umgebung selbst unvollstaendig ist.
MARKER = "TRADINGBOT_VENV_NEUSTART"


def venv_python() -> Path | None:
    """Pfad zum Python der lokalen Umgebung, falls vorhanden."""
    for name in UMGEBUNGEN:
        kandidat = ROOT / name / ("Scripts" if os.name == "nt" else "bin") / (
            "python.exe" if os.name == "nt" else "python")
        if kandidat.is_file() and os.access(kandidat, os.X_OK):
            return kandidat
    return None


def fehlende_pakete() -> list[str]:
    import importlib.util
    return [name for name in PFLICHTPAKETE if importlib.util.find_spec(name) is None]


def laeuft_in_venv() -> bool:
    ziel = venv_python()
    if ziel is None:
        return False
    try:
        return Path(sys.executable).resolve() == ziel.resolve()
    except OSError:
        return False


def sicherstellen(*, still: bool = False) -> None:
    """Startet das laufende Skript notfalls in der richtigen Umgebung neu.

    Wird ganz oben in nexus_start.py und nexus_setup.py aufgerufen, VOR dem
    ersten Import von config oder pandas.
    """
    fehlt = fehlende_pakete()
    if not fehlt:
        return

    if os.environ.get(MARKER):
        # Wir sind bereits einmal umgezogen und es fehlt immer noch etwas.
        _abbrechen(fehlt, bereits_umgezogen=True)

    ziel = venv_python()
    if ziel is None or laeuft_in_venv():
        _abbrechen(fehlt, bereits_umgezogen=False)

    if not still:
        print(f"Hinweis: {', '.join(fehlt)} fehlt im System-Python.")
        print(f"         Starte neu in {ziel.parent.parent.name}/ ...")
        print()

    umgebung = dict(os.environ)
    umgebung[MARKER] = "1"
    try:
        os.execve(str(ziel), [str(ziel), *sys.argv], umgebung)
    except OSError as exc:
        print(f"FEHLER: Neustart in der Python-Umgebung fehlgeschlagen: {exc}")
        _abbrechen(fehlt, bereits_umgezogen=False)


def _abbrechen(fehlt: list[str], *, bereits_umgezogen: bool) -> None:
    print()
    print("=" * 70)
    print("FALSCHE PYTHON-UMGEBUNG")
    print("=" * 70)
    print(f"Es fehlt: {', '.join(fehlt)}")
    print()
    if bereits_umgezogen:
        print("Die Umgebung unter .venv ist unvollstaendig.")
        print("Bitte einmal neu einrichten:")
        print("    ./Pi_Installieren.sh")
    else:
        print("Der Bot benutzt eine eigene Python-Umgebung. Bitte so starten:")
        print()
        print("    ./Nexus_Starten.sh              (Dauerbetrieb)")
        print("    ./Nexus_Einrichten.sh           (Zugaenge eintragen)")
        print()
        print("Oder direkt mit dem Python der Umgebung:")
        print("    .venv/bin/python nexus_start.py")
        print()
        print("Existiert noch keine Umgebung, zuerst:")
        print("    ./Pi_Installieren.sh")
    print("=" * 70)
    raise SystemExit(78)


__all__ = ["sicherstellen", "venv_python", "fehlende_pakete", "laeuft_in_venv"]
